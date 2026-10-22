# Command Code + MCP：自然语言生成部署方案

## 结论

这条链路已经真实跑通：

```
Streamlit（自然语言）
  → AgentService（transport=commandcode）
  → command-code -p（headless；默认禁止写文件/改文件/shell）
  → MCP server（lcp）
  → tool_cli（catalog / evaluate / report）
  → evaluate_plan() → PlanResult → Reporting
```

OpenCode 的两条路径仍有阻塞（`serve` 的 free tier、v2.1.2 未加载
`.opencode/tools`）；当前默认 transport 是 **Command Code**，因为它在本机
有可用的 client API，且 MCP 工具能被模型自动发现。

## 安装与配置

```bash
# 0) 需要 bwrap（文件系统沙箱）与已登录的 command-code
bwrap --version

# 1) MCP Python SDK（只给 Agent 用，不并入主依赖）
.venv/bin/pip install -r requirements-agent.txt

# 2) 注册本地 MCP server（用户级，任何工作目录都能用）
command-code mcp add --transport stdio --scope user \
  -e "PYTHONPATH=$PWD/src" lcp -- \
  "$PWD/.venv/bin/python" -m llm_capacity_pricing.agent.mcp_server

# 3) 确认
command-code mcp list          # lcp / stdio / user / enabled
```

`command-code` 的 client API 登录状态由它自己的 `~/.commandcode/auth.json`
管理；本仓库不含任何密钥。默认模型：

```bash
export COMMAND_CODE_MODEL=deepseek/deepseek-v4-flash   # 可覆盖
```

## MCP 工具

`src/llm_capacity_pricing/agent/mcp_server.py` 暴露三个工具：

| 工具 | 作用 |
| --- | --- |
| `get_platform_catalog` | 支持的模型 / GPU / 机箱 |
| `evaluate_deployment_plan` | 结构化需求 → 完整评估与报告 |
| `get_plan_report` | 按 run_id 取回结果 |

MCP 的 stdout 是协议通道，因此 server 通过**子进程**调用 `tool_cli` 并捕获输出，
不会把业务日志混进协议流。

## 任务契约（模型文本不是结论）

1. `AgentService` 为每次任务生成 `TASK_ID=cc-<16 hex>`；
2. prompt 要求模型调用 `evaluate_deployment_plan(task_id=...)`；
3. MCP 工具把结果写入 `data/agent_tasks/<task_id>.json`；
4. 服务只读取该文件，校验 `status/run_id/artifacts`，再从 `result.json` 加载
   `PlanResult`；
5. 模型的 `finalText` 只用于页面解释；缺失任务文件、状态失败或产物不一致时
   **不生成报告**；
6. 服务强制校验 task_id / run_id / artifacts / result.json 的 sha256 与运行目录
   `agent_result.json` 一致，禁止任务文件改指向其他历史报告；
7. Agent 会话状态（`agent_status`）与报告状态分开：报告有效但会话异常时保留报告，
   并在页面提示“不要把本次会话视为完全成功”。

headless 默认禁止文件写入/编辑和 shell；不使用 `--yolo` / `--tools-all` /
`--accept-edits`。运行目录是空的 `data/agent/cc-workspace`。

额外的运行时门禁：解析 NDJSON 的 `tool_running/tool_completed` 事件，
只允许 `mcp__lcp__*` 与 `search_tools/select_tool`；出现其他工具就判定任务失败、
不采用报告。MCP 子进程只接收最小环境变量（不继承模型凭据）。

## 实测记录（2026-10-21）

请求：

> 我有 8 张 H100，希望部署 Qwen3-32B，输入 8K、输出 256，并发 32，
> TTFT P99 不超过 2 秒，TPOT P99 不超过 50ms，请给出部署配置和成本分析。

结果：

- `completed`，run_id `20261021T085956Z`；
- 3 个候选；`observed_pass=0`；`quote_publishable=0`；
- 模拟推荐为空；未达标参考 `sim-native:…tp8pp1dp1-c32-n500`；
- 模型调用链：`get_platform_catalog` → `evaluate_deployment_plan` → `get_plan_report`；
- 报告与手动模式同一套 `evaluate_plan()` 产物。

W 阶段复验（同一最终代码）：

| 场景 | run_id | 结果 |
| --- | --- | --- |
| 严格 SLA（2000/50） | `20261021T100422Z` | completed，预测未达标 |
| 宽松 SLA（6000/40） | `20261021T094315Z` | completed，预测达标并推荐 TP8 |
| 缺字段 → 同会话补充 | `20261021T094228Z` | 先 needs_confirmation，补充后 completed |

同一需求的手动路径与工具路径在候选、静态状态、SLA、成本与模拟 verdict 上逐项一致
（`tests/test_v_agent.py::test_agent_tool_uses_the_same_engine_as_manual_mode`）。

## 多轮补充与歧义确认

- 用户只说“H100”时，Agent 不猜型号：先返回 `needs_confirmation`，列出
  `nvidia-h100-sxm-80gb` 与 `nvidia-h100-nvl-94gb`，并追问 OSL/ISL 等缺失项；
- 页面提供“补充信息 → 继续分析”，使用同一 Command Code 会话（`--session`）
  与同一 `TASK_ID`，不需要重新输入整段需求；
- 实测：首次缺 GPU 型号/OSL → 补充后 completed，run_id `20261021T094228Z`；
- 预算未给币种时，Agent 说明示例成本目录为 CNY 并请求确认；
  框架/精度/成本口径作为 `assumptions` 写进契约与页面。

## X 阶段加固

- **执行边界**：Command Code 子进程默认在 `bwrap` 沙箱中运行；只读挂载代码/venv/
  公开数据，只写挂载任务/运行目录与**每任务独立的 HOME**；不挂载
  `data/private`、`data/local.db`、raw xlsx 与真实 HOME；bwrap/node 不可用时默认拒绝运行。
- **工具门禁**：只允许精确的三个 MCP 工具名（外加 `search_tools/select_tool`）；
  其他调用判失败、不采用报告。事件检查是**事后审计**，执行边界由沙箱提供；
  不声称已做到执行前拦截。
- **报告归属**：`get_plan_report(task_id, run_id)` 必须同时给出两者；任务文件、
  运行目录契约、sha256、request_id 全部一致才返回，猜历史 run_id 无效。
- **状态**：Command Code 退出码非零时，即使结果事件是 success 也标记 Agent 会话异常；
  报告可保留，但不显示为整体成功。
- **并发**：运行目录随机后缀 + 原子 `mkdir`；每任务独立沙箱 HOME，避免 Session 串用。
- 调试：`COMMAND_CODE_SANDBOX=0` 仅用于本地排查，不用于开放访问。

## 双模式

- **手动**：结构化表单，直接 `run_demo()`；
- **Agent**：自然语言 → Command Code → MCP → 同一引擎；
- 两条路径都落到 `data/agent_runs/`，页面复用同一套结果渲染与下载。

## 安全边界

| 项 | 做法 |
| --- | --- |
| headless 权限 | 默认禁止写文件/改文件/shell；不加 `--yolo`/`--tools-all` |
| 工具范围 | prompt 只允许三个 lcp MCP 工具；MCP server 也只暴露这三个 |
| 工作目录 | 空的 `data/agent/cc-workspace` |
| 结果可信来源 | 只读任务文件与已保存的 `result.json` |
| 路径校验 | `run_id` 白名单，阻止路径穿越 |
| 密钥 | Command Code 登录态在其自身配置；仓库不含密钥 |
| 失败 | 无工具产物 / 契约不一致 / 超时 → 不生成报告 |

headless 仍允许读/搜索类工具（Command Code 的默认），因此 prompt 明确禁止
用它们处理业务数据；若要更强隔离，可在 `~/.commandcode/agents/` 定义
`tools` 仅含三个 MCP 工具的专用 Agent（当前 `-p` 没有稳定的 agent 选择参数，
本仓库不依赖它）。

## 测试

```bash
.venv/bin/python -m pytest tests/test_v_agent.py -q
```

覆盖：工具契约、缺字段、路径穿越、Command Code 流解析、任务文件缺失、
模型编造数字不进入结果、手动/工具一致性、OpenCode transport 回归。
实时 Command Code 调用依赖本机 client API，不在 CI 中跑。
