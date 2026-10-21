# V 阶段验收记录：OpenCode Agent 集成

日期：2026-10-21
环境：OpenCode v2.1.0（本机 `127.0.0.1:4096`，Basic Auth）/ Python 3.12.3 / 离线归档模拟

## 总结

**Command Code + MCP 已真实跑通完整链路**（自然语言 → 模型 → 三个业务工具 → 报告）。
OpenCode 作为备选仍有阻塞：serve 的 free tier、v2.1.2 未暴露 `.opencode/tools`。所有“已完成”的结论都附有可复现证据；“未完成”的部分不写成通过。

| 阶段 | 状态 | 说明 |
| --- | --- | --- |
| V1 Agent 入口 | 完成 | Command Code client API 已实测；OpenCode 服务/鉴权/会话/消息也已实测（serve 的 free tier 仍受限） |
| V2 业务工具 | 完成 | MCP server 暴露三个工具；Command Code 实际调用成功（catalog → evaluate → report）；OpenCode 自定义工具仍未暴露 |
| V3 契约与异常 | 完成 | `completed/needs_confirmation/failed`、产物校验、路径白名单、失败不生成报告均有测试 |
| V4 自然语言入口 | 完成（失败路径） | UI 双模式、Agent 输入、服务不可用/失败展示；完整成功路径待在可用 provider 上复验 |
| V5 一致性 | 完成（Command Code） | 手动与工具路径逐项一致；Command Code 真实模型链路跑通，run_id 20261021T085956Z |

## 证据

### V1：服务与 HTTP

- `bash scripts/start_opencode_server.sh` 启动成功，`/api/model` 鉴权通过；
- 实测 `POST /api/session` → `POST /api/session/{id}/prompt` →
  `POST /api/experimental/session/{id}/wait`（204）→ `GET /api/session/{id}/message`；
- 助手文本可正常取回。

模型与工具现状（如实记录，2026-10-21 复测）：

| 项 | 结果 |
| --- | --- |
| v2.1.2 CLI + `glm-5.3-flash-free` | 可用（客户端 `run`） |
| v2.1.2 CLI + `big-pickle` / `mimo-v2.6-flash-free` | 可用 |
| `deepseek-v4.1-flash-free` | 上游 503 |
| `opencode serve` + free 模型 | 403：仅限 OpenCode 客户端内使用 |
| `.opencode/tools` 自定义工具 | 未加载；模型调用返回 `Unknown tool 'get_platform_catalog'` |

### Command Code（当前默认 transport）

- 本机 `command-code` v1.53.0，client API 已登录；
- `mcp add --transport stdio --scope user lcp -- .venv/bin/python -m llm_capacity_pricing.agent.mcp_server`；
- headless 默认禁止写文件/改文件/shell；不使用 `--yolo/--tools-all`；
- 实测模型依次调用 `mcp__lcp__get_platform_catalog` →
  `mcp__lcp__evaluate_deployment_plan` → `mcp__lcp__get_plan_report`；
- 任务文件按 `TASK_ID` 写入，服务只读产物；最终报告与手动模式同一引擎。

### OpenCode（备选）

服务端会话中模型已能正常回复；失败点是自定义工具未暴露。free-tier 鉴权需要
`read`，已收窄为“仅 `.env` 类”，并把凭据文件改名为 `opencode.credentials`。

### V2/V3：工具与契约

`tests/test_v_agent.py` 12 项全部通过：

- catalog 列出 `qwen3-32b` / `nvidia-h100-sxm-80gb`；
- 缺字段 → `needs_confirmation`，列出“GPU 型号、GPU 上限…”且无 `run_id`；
- 完整需求 → `completed`，生成 `result.json` / 方案 / 技术报告；
- `report` 可按 `run_id` 取回方案；`../outside` 被拒绝；
- 契约校验拒绝：completed 缺产物、needs_confirmation 缺字段、failed 缺原因；
- 模型文本里编造的“16 卡 / P99 1ms”没有进入结构化结果；
- 未调用工具 → `failed`，`result=None`；
- 产物缺失 → `failed`，不生成报告。

### V4：双模式入口

- 手动模式保持原有表单与结论；
- Agent 模式显示自然语言输入、服务可用性与“让 Agent 生成方案”按钮；
- Agent 失败时页面显示失败原因，不显示方案；无工具产物时明确“未生成方案”；
- Agent 成功时会复用同一套结果渲染（`as_demo_outcome()`）。

### V5：一致性

`test_agent_tool_uses_the_same_engine_as_manual_mode`：同一需求分别走
`run_demo()` 与 Agent 工具，候选 ID、静态状态、SLA、成本与模拟 verdict 逐项一致。

Command Code 路径已完成；OpenCode 路径仍未完成（工具未暴露）。
因此“OpenCode 真实模型链路”不写成通过，“Command Code 真实模型链路”通过。

## 安全验证

- 服务仅监听 `127.0.0.1`，Basic Auth；
- 密码写入 `data/agent/opencode.credentials`（0600，git 忽略）；
- 隔离 `XDG_CONFIG_HOME=data/agent/xdg`：启动日志中 MCP 连接数为 **0**，
  全局 skills / instructions 未加载；
- Agent 权限：`shell/edit/read/glob/grep/list/webfetch/websearch/skill/question` 均 deny，
  仅放行三个业务工具；
- 工具只写 `data/agent_runs/`，`run_id` 白名单，路径穿越被拒绝；
- 模型 API Key 只在服务端 provider 配置，浏览器不接触。

## 回归

- `pytest`：**945 passed, 1 skipped**；
- `scripts/verify_all.sh`：**66/66**；
- U 阶段的三场景与 v1.3 容器不受影响。

## 启用实时模型后的复验步骤

1. 解决工具暴露：改用 MCP，或换到支持 `.opencode/tools` 的 OpenCode 版本；
   若走 HTTP serve，还需一个非 free-tier 的 provider；
2. `bash scripts/start_opencode_server.sh` 重启；
3. 运行：
   ```bash
   .venv/bin/python - <<'PY'
   from llm_capacity_pricing.agent.service import AgentService
   run = AgentService().run("我有 8 张 H100，部署 Qwen3-32B，输入 8K、输出 256，并发 32，TTFT P99 2 秒，TPOT P99 50ms。")
   print(run.completed, run.task.status, run.task.error)
   PY
   ```
4. 用同一需求走手动模式，按 V5 的快照比较候选、SLA、成本与模拟 verdict。
