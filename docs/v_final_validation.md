# V 阶段验收记录：OpenCode Agent 集成

日期：2026-10-21
环境：OpenCode v2.1.0（本机 `127.0.0.1:4096`，Basic Auth）/ Python 3.12.3 / 离线归档模拟

## 总结

V1–V4 的代码与确定性验证已完成；**V5 的真实模型编排被 provider 权限阻塞**，
未完成。所有“已完成”的结论都附有可复现证据；“未完成”的部分不写成通过。

| 阶段 | 状态 | 说明 |
| --- | --- | --- |
| V1 服务与 HTTP | 部分完成 | 服务启动、鉴权、创建会话、发送消息、等待、取回文本已实测；模型 provider 无法从服务端调用 |
| V2 专用 Agent + 业务工具 | 部分完成 | 三个工具与 Agent 配置已实现；工具由 Python 测试直接验证；模型实际调用工具因 provider 失败未验证 |
| V3 契约与异常 | 完成 | `completed/needs_confirmation/failed`、产物校验、路径白名单、失败不生成报告均有测试 |
| V4 自然语言入口 | 完成（失败路径） | UI 双模式、Agent 输入、服务不可用/失败展示；完整成功路径待在可用 provider 上复验 |
| V5 一致性 | 部分完成 | 手动与工具路径结论逐项一致；OpenCode 真实模型路径未能执行 |

## 证据

### V1：服务与 HTTP

- `bash scripts/start_opencode_server.sh` 启动成功，`/api/model` 鉴权通过；
- 实测 `POST /api/session` → `POST /api/session/{id}/prompt` →
  `POST /api/experimental/session/{id}/wait`（204）→ `GET /api/session/{id}/message`；
- 助手文本可正常取回。

模型调用结果（如实记录）：

| provider/model | 结果 |
| --- | --- |
| `opencode/deepseek-v4.1-flash-free` | 403：free tier 仅限 OpenCode 客户端 |
| `kimi/kimi-for-coding` | 403：订阅无权限 |
| `mimo/mimo-v2.5-pro` | 404：provider 端点 |

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

**未完成**：让真实模型通过 OpenCode 调用工具并返回结果；当前 provider 不可用。
因此不能声称“自然语言 → OpenCode → 工具 → 报告”的完整链路已通过真实模型验证。

## 安全验证

- 服务仅监听 `127.0.0.1`，Basic Auth；
- 密码写入 `data/agent/opencode.env`（0600，git 忽略）；
- 隔离 `XDG_CONFIG_HOME=data/agent/xdg`：启动日志中 MCP 连接数为 **0**，
  全局 skills / instructions 未加载；
- Agent 权限：`shell/edit/read/glob/grep/list/webfetch/websearch/skill/question` 均 deny，
  仅放行三个业务工具；
- 工具只写 `data/agent_runs/`，`run_id` 白名单，路径穿越被拒绝；
- 模型 API Key 只在服务端 provider 配置，浏览器不接触。

## 回归

- `pytest`：**940 passed, 1 skipped**；
- `scripts/verify_all.sh`：**66/66**；
- U 阶段的三场景与 v1.3 容器不受影响。

## 启用实时模型后的复验步骤

1. 在 `agent/opencode/opencode.json` 的 `model` 与 Agent `model` 填入可用
   `provider/model`；
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
