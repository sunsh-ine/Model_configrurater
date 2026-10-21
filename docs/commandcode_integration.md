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
   **不生成报告**。

headless 默认禁止文件写入/编辑和 shell；不使用 `--yolo` / `--tools-all` /
`--accept-edits`。运行目录是空的 `data/agent/cc-workspace`。

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

同一需求的手动路径与工具路径在候选、静态状态、SLA、成本与模拟 verdict 上逐项一致
（`tests/test_v_agent.py::test_agent_tool_uses_the_same_engine_as_manual_mode`）。

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
