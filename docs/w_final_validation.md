# W 阶段验收：Agent 集成加固与交互闭环

日期：2026-10-21
基线：`6ee95a1` + 本阶段改动
环境：Command Code v1.53.0 / client API / 本地 MCP（mcp 2.3.0）/ 离线归档模拟

## 总结

- **955 passed, 1 skipped**；`scripts/verify_all.sh` **66/66**；
- Command Code 真实链路通过：严格 SLA、宽松 SLA、缺字段 → 同会话补充 → 完成；
- OpenCode 仍作为备选（serve free-tier、`.opencode/tools` 暴露问题未变）；
- Agent 模式仍只在开发环境，未部署到无鉴权的 61 页面。

## W1：任务状态、关联与工具权限

### 1. 任务状态与报告状态分离

`AgentRun` 新增 `agent_status` / `agent_error` / `agent_session_id`：

- 业务工具生成有效报告、但 Command Code 会话随后异常（`subtype != success`）时，
  `completed=True`（报告仍可下载），同时 `agent_status="error"`；
- 页面显示“业务报告已生成，但 Agent 会话未正常结束”，不把整个任务标成完全成功；
- 测试：`test_agent_session_error_is_kept_separate_from_report`。

### 2. task / run / 报告强绑定

- `AgentTaskResult` 新增 `result_sha256`，completed 必须绑定；
- 服务校验：
  - `task.session_id == 本次 task_id`；
  - `task.run_id` 对应 `data/agent_runs/<run_id>`；
  - `artifacts.dir` 必须是该运行目录，`json/proposal/technical` 必须在其内部；
  - `sha256(result.json)` 必须等于任务绑定的值；
  - 运行目录里的 `agent_result.json` 必须与任务文件一致（run_id / session_id / hash）；
  - `result.json.request_id` 必须等于任务 request_id。
- 反向测试：
  - 把任务 A 改成引用任务 B 的 run/artifacts/hash → 拒绝（运行契约 session 不一致）；
  - 篡改 result.json → 拒绝（sha256 不一致）；
  - completed 缺少 hash → 契约校验拒绝。

### 3. 运行时工具门禁

- 解析 Command Code NDJSON 的 `tool_running/tool_completed` 事件；
- 只允许 `mcp__lcp__*` 与 `search_tools/select_tool`；
- 出现 shell/read/edit 等其他工具 → **任务失败，不采用报告**；
- 测试：`test_unauthorized_tool_blocks_the_report`。

Command Code headless 默认已禁止写文件/改文件/shell；本层是额外的运行时硬门禁。
（Command Code 的自定义 Agent `tools` 白名单存在，但 `-p` 没有文档化的 agent 选择参数，
本仓库不依赖它；隔离工作目录 + prompt + 事件门禁是当前边界。）

### 4. MCP 子进程环境收紧

`mcp_server._tool_env()` 只保留 `PATH/LANG/LC_ALL/TZ/TMPDIR/PYTHONIOENCODING`，
加 `PYTHONPATH` 与 `PYTHONHASHSEED`；不再继承父进程的模型凭据等变量。
测试：`test_mcp_tool_env_excludes_parent_secrets`。

## W2：需求确认与多轮补充

### 歧义不猜

真实请求“8 张 H100，部署 Qwen3-32B，输入 8K，并发 32，TTFT 2 秒”的结果：

- `needs_confirmation`；
- 缺失字段：GPU 型号、输出长度 OSL；
- Agent 列出两个候选：`nvidia-h100-sxm-80gb`、`nvidia-h100-nvl-94gb`，不替用户选择。

### 同一任务继续

用户补充“GPU 型号 nvidia-h100-sxm-80gb；OSL 256”后：

- 使用同一 Command Code 会话（`--session`）与同一 `TASK_ID`；
- 结果 `completed`，run_id `20261021T094228Z`；
- 报告、假设、限制完整；不需要重新输入整段需求。

### 三个真实场景

| 场景 | 结果 |
| --- | --- |
| 严格 SLA（TTFT 2000 / TPOT 50） | completed，run_id `20261021T100422Z`，预测未达标 |
| 宽松 SLA（6000 / 40） | completed，run_id `20261021T094315Z`，预测达标并推荐 TP8 |
| 缺字段 → 补充 | 先 needs_confirmation，补充后 completed（`20261021T094228Z`） |

### 默认值与假设

任务契约新增 `assumptions`，明确列出：

- 框架 vllm 0.24.0（示例默认）；
- 模型精度（请求值）；
- 离线归档模拟（非实测）；
- 成本目录 CNY、合成示例、币种未确认。

Prompt 要求：用户给预算但未给币种时不得自行假定，需说明示例目录为 CNY 并请求确认。

框架/版本与预算币种现在会真实传入请求：`framework` / `framework_version` /
`budget_currency` 进入 `PlanRequest`；任务契约的 `assumptions` 区分“请求值”与
“示例默认”。预算币种与成本目录不一致时由既有预算门禁处理，不做跨币种硬比。

## W3：页面最小整合

- Agent 模式文案统一为「AI Agent」，不再出现 OpenCode；
- 缺字段时显示缺失项与 Agent 列出的候选，并提供“补充信息 → 继续分析”；
- Agent 会话异常时显示警告，同时保留可下载的报告；
- 完成后显示本次采用的默认假设；
- 自由文本仍放在“Agent 说明（不是业务结论）”折叠区，结构化结果优先。

## W4：回归与冻结

- 全量 `pytest`：953 passed / 1 skipped；
- `verify_all.sh`：66/66；
- 真实 Command Code 三个场景均执行；
- codex（`gpt-6-luna` + `high`）审核后冻结 Agent Demo。

## 未验证 / 未做

- OpenCode 真实链路（工具暴露问题）；
- Command Code 自定义 Agent 的 headless 选择（文档未提供稳定参数）；
- Agent 模式部署到 61（无鉴权页面，不适合接入带模型凭据的 Agent）；
- N5 真人验收、N6 真机 Benchmark。
