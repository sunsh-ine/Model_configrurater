# X 阶段验收：Agent Demo 稳定化与交付

日期：2026-10-22
基线：`ba27fb9` + 本阶段改动
环境：Command Code（client API）/ 本地 MCP（mcp 2.3.0）/ bwrap 0.9.0 / 离线归档模拟

## 总结

- **974 passed, 1 skipped**；`verify_all.sh` **66/66**；
- 沙箱内真实跑通 Command Code → MCP → `evaluate_plan()` → 报告
  （run_id `20261022T012312Z_e59f43`）；
- 四个 P1/P2 边界已处理：工具边界、报告归属、退出码、并发目录；
- Agent 模式仍只在开发环境，未部署到 61。

## X1：关键边界加固

### X1.1 工具权限

- Command Code 没有文档化的 headless agent 选择/工具白名单参数；
  `config list` 也没有 agent/tool 项；
- 因此采用 **bwrap 文件系统沙箱** 作为执行边界：
  - 只读挂载代码、venv、模型/硬件/示例数据、`docs/poc`；
  - 只写挂载 `data/agent_runs`、`data/agent_tasks`、`data/agent/cc-workspace` 与沙箱 HOME；
  - **不挂载** `data/private`、`data/local.db`、`data/raw/*.xlsx`、真实 HOME；
  - 沙箱 HOME 只复制 `auth.json/config.json` 与**仅含 lcp 的** `mcp.json`（0600）；完成/失败后清理，不复制历史/缓存；
- 运行时事件门禁收紧为**精确三个工具名**：
  `mcp__lcp__get_platform_catalog`、`mcp__lcp__evaluate_deployment_plan`、
  `mcp__lcp__get_plan_report`（另允许 `search_tools/select_tool` 元工具）；
  其他调用判任务失败、不采用报告；
- **不声称“执行前拦截了工具调用”**：事件检查是事后审计；执行边界是 bwrap。
- **fail-closed**：默认要求 bwrap + node；不可用则拒绝运行，不会静默降级到宿主机；
  仅显式 `COMMAND_CODE_SANDBOX=0` 允许本地调试。
- 测试：`test_sandbox_binds_exclude_private_data`、`test_exact_mcp_tool_allowlist`、
  `test_unauthorized_tool_blocks_the_report`、`test_sandbox_is_required_by_default`。

### X1.2 报告任务归属

- `get_plan_report` 现在必须同时提供 `task_id` 与 `run_id`；
- 工具层校验：任务文件存在、`task.session_id == task_id`、
  `task.run_id == run_id`、运行目录的 `agent_result.json` 同属该任务、
  `result.json` sha256 一致、产物在运行目录内；
- 跨任务读取、猜历史 run_id、路径穿越均被拒绝；
- 测试：`test_report_requires_matching_task_and_run`、`test_cross_task_report_reference_is_rejected`。

### X1.3 状态与并发

- Command Code 退出码非零时，即使 stdout 有 `subtype=success`，也记录为
  Agent 会话异常（报告可保留，但 `agent_status=error`）；
- `new_run_dir()` 改为随机后缀 + 原子 `mkdir(exist_ok=False)`，并发任务不会拿到同一目录；
- 运行目录不可变；任务文件原子写入；
- 测试：`test_nonzero_exit_is_an_error_even_with_success_event`、
  `test_run_dirs_are_atomic_and_unique`、`test_new_run_dir_is_unique`。

## X2：场景矩阵

`docs/agent_test_cases.md` 列出 12 类自然语言样例；确定性覆盖 1–11，12 由
`test_continuation_reuses_task_and_session` 与真实续跑覆盖。

结构化矩阵（`tests/test_x_agent_scenarios.py`，12 项）：

- 严格/宽松完成；
- 缺 GPU 型号 / OSL / 并发；
- H100 多 SKU 不猜；
- 秒/毫秒与 8K 映射（prompt）；
- 预算无币种、币种不一致；
- 门禁绕过字段被拒；
- 不支持模型/硬件 → blocked 计划，0 候选；
- 工具路径与手动引擎逐项一致。

真实 Command Code 代表场景：

| 场景 | run_id | 结果 |
| --- | --- | --- |
| 严格 SLA | `20261021T100422Z` | completed，预测未达标 |
| 宽松 SLA | `20261021T094315Z` | completed，预测达标并推荐 |
| 缺字段 → 同会话补充 | `20261021T094228Z` | 先 needs_confirmation，补充后 completed |
| 沙箱复验 | `20261022T012312Z_e59f43` | completed |
| 每任务沙箱 HOME | `20261022T014913Z_a8a0bf` | completed，预测达标并推荐 |

指标口径：单次真实调用在分钟级；本轮不做稳定性统计，不把一次成功外推为成功率。
字段抽取正确率依赖模型，未做大规模统计；结构化矩阵验证的是业务边界与门禁。

## X3：页面收尾

- AI Agent 模式保留自然语言输入、补充信息、继续分析、结果与下载；
- 缺字段时**直接展示** Agent 给出的候选/缺失项，而不是只放进折叠区；
- 有效报告与 Agent 会话状态分开显示；
- 完成页显示 `assumptions`，并标注模拟/成本边界；
- 模型自由文本不进入结构化结果；
- API 不可用时手动模式不受影响。

## X4：可重复交付

- `docs/agent_demo_start.md`：安装、MCP 注册、可用性检查、启动、示例请求、
  下载与故障排查；
- 敏感凭据只在 `data/agent/cc-homes/<task_id>/`（0600，git 忽略）与 Command Code 自身配置；
- 全量 pytest 与 `verify_all.sh` 见总结。

## X5：未实施

仅设计受控部署方案，见 `docs/x5_deployment_plan.md`；未向 61 加入 Command Code
登录态，也未开放公网入口。

## 未验证 / 限制

- 自定义 Command Code Agent 的 headless 选择（官方未提供稳定参数）；
- 模型凭据仍位于沙箱 HOME（模型服务所需），事件门禁会拒绝非 MCP 工具产出的报告；
- OpenCode 真实链路仍未跑通；
- 实时 AISimulate、N5/N6 不在本阶段。
