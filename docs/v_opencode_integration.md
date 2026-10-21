# V 阶段：OpenCode 作为可替换的 Agent 编排组件

## 架构

```
Streamlit（手动表单 / 自然语言）
        │
        ├── 手动：DemoInputs → run_demo()
        └── Agent：AgentService → OpenCode Server（deployment-planner）
                                   │  只调用三个业务工具
                                   ▼
                         tool_cli（catalog / evaluate / report）
                                   │
                                   ▼
                    evaluate_plan() → PlanResult → Reporting
```

- OpenCode 负责理解需求、选择工具、组织步骤；
- **所有资源、性能、SLA、成本与推荐资格的数字只来自现有引擎**；
- Agent 的最终文本只作解释；业务系统按 `run_id` 读取工具产物生成报告；
- 没有自研 Agent 框架、没有新的计算模块。

## 实际验证过的 OpenCode 接口

本机 OpenCode `v2.1.0`，服务端使用 Basic Auth（用户名 `opencode`）：

| 操作 | 方法与路径 |
| --- | --- |
| 鉴权探测 | `GET /api/model` |
| 创建会话 | `POST /api/session` `{title, agent}` |
| 提交消息 | `POST /api/session/{id}/prompt` `{text}` |
| 等待完成 | `POST /api/experimental/session/{id}/wait`（204） |
| 读取消息 | `GET /api/session/{id}/message` |
| Agent 列表 | `GET /api/agent` |

已实测：创建会话 → 发送消息 → 等待 → 取回 assistant 文本。
（文档中常见的 `/session/:id/message` 在本版本并不存在，以 OpenAPI 为准。）

## 业务工具

`agent/opencode/.opencode/tools/` 下三个 TypeScript 工具，调用
`python -m llm_capacity_pricing.agent.tool_cli`：

| 工具 | 作用 | 底层 |
| --- | --- | --- |
| `get_platform_catalog` | 列出支持的模型/GPU/机箱 | 目录 YAML |
| `evaluate_deployment_plan` | 结构化需求 → 完整评估与报告 | `run_demo()` → `evaluate_plan()` |
| `get_plan_report` | 按 `run_id` 取回结果 | 已保存的 `result.json` / 报告 |

`evaluate` 的输入缺字段时返回 `needs_confirmation`，并列出缺失字段；不会猜默认值。
执行失败返回 `failed`；**不会生成任何报告**。

### 任务契约

工具把结果写入 `data/agent_tasks/<session_id>.json` 并返回 stdout：

```json
{
  "status": "completed",
  "session_id": "ses_...",
  "run_id": "20261021T...",
  "request_id": "agent-...",
  "plan_result_available": true,
  "needs_confirmation": false,
  "summary": "整体状态：partial；模拟状态：computed；未达标参考：...",
  "missing_fields": [],
  "error": null,
  "artifacts": {
    "dir": "/.../data/agent_runs/...",
    "proposal": ".../deployment_proposal.md",
    "technical": ".../technical_report.md",
    "json": ".../result.json"
  },
  "conclusions": {"candidate_count": 3, "observed_pass_count": 0, "...": "..."}
}
```

Python 侧用 Pydantic 校验；`completed` 必须带 `run_id`、`result.json` 引用；
任何不一致都降级为 `failed`，不生成报告。**模型文本不参与数字或结论。**

## 双模式

- **手动模式**：原有结构化表单，直接调用 `run_demo()`；不依赖 OpenCode。
- **Agent 模式**：左侧输入自然语言，OpenCode 调用工具；结果仍写回
  `data/agent_runs/`，页面复用同一套结果渲染与下载。

两条路径最终都是 `PlanRequest → evaluate_plan() → PlanResult → Reporting`。
测试 `test_agent_tool_uses_the_same_engine_as_manual_mode` 对同一需求比较
候选、静态状态、SLA、成本与模拟 verdict，结果逐项一致。

## 启动

```bash
# 1) 启动专用 OpenCode 服务（127.0.0.1，Basic Auth）
bash scripts/start_opencode_server.sh

# 2) 手动模式：直接打开 Streamlit
.venv/bin/streamlit run app/streamlit_app.py

# 3) 工具直连（不经模型，用于排查）
PYTHONPATH=src .venv/bin/python -m llm_capacity_pricing.agent.tool_cli catalog
PYTHONPATH=src .venv/bin/python -m llm_capacity_pricing.agent.tool_cli evaluate \
  --session-id ses_manual --input-json '{"model_id":"qwen3-32b","hardware_id":"nvidia-h100-sxm-80gb","gpu_count":8,"input_len":8192,"output_len":256,"concurrency":32,"ttft_p99_ms_max":400,"tpot_p99_ms_max":30}'
```

`scripts/start_opencode_server.sh` 会：

1. 在 `agent/opencode/` 启动 `opencode serve`；
2. 用 `XDG_CONFIG_HOME=data/agent/xdg` 隔离全局 MCP / skills / instructions；
3. 只复制模型 provider（含服务端密钥）与本项目 Agent；
4. 把 server password 写入 `data/agent/opencode.env`（0600，git 忽略）。

## 安全边界

| 项 | 做法 |
| --- | --- |
| 监听 | `127.0.0.1` only |
| 认证 | HTTP Basic Auth；密码只写本地 0600 文件 |
| Agent 工具权限 | `shell/edit/read/glob/grep/list/webfetch/websearch/skill/question` 全部 deny；只放行三个业务工具 |
| 全局能力 | 隔离配置，不加载全局 MCP / skills / instructions |
| 浏览器 | 不接触模型 API Key |
| 工具输出 | 只写 `data/agent_runs/`；`run_id` 白名单校验，阻止路径穿越 |
| 失败 | 无工具产物即 `failed`，不生成报告 |

## 当前外部阻塞：模型 provider

本机 OpenCode 已启动、会话与 HTTP API 已验证，但现有三组 provider 都无法从
服务端完成一次模型调用：

| provider/model | 实际结果 |
| --- | --- |
| `opencode/deepseek-v4.1-flash-free` | 403：`OpenCode's free tier can only be used from within OpenCode` |
| `kimi/kimi-for-coding` | 403：当前订阅没有 Kimi Code 权限 |
| `mimo/mimo-v2.5-pro` | 404（provider 端点） |

因此 **V5 的“真实模型编排”尚未验证**；这不是代码缺陷，也不是可以靠改提示词
绕过的问题。启用方式：在 OpenCode 配置里换成一个可用的 provider/model
（`agent/opencode/opencode.json` 的 `model` 与 Agent 的 `model`），重启服务后
重新执行 `AgentService().run(...)`。

在此之前，以下部分已验证：

- 服务启动、Basic Auth、会话/消息/等待接口；
- 三个业务工具的 Python 实现与契约；
- 缺字段、失败、超时、产物不一致时不生成报告；
- 手动模式与工具路径的结论逐项一致；
- Agent 模式的 UI、失败展示与无报告边界。

## 测试

```bash
.venv/bin/python -m pytest tests/test_v_agent.py -q
```

覆盖：catalog、缺字段、完整评估、report、路径穿越、契约校验、
“模型文本不影响结果”、无工具产物失败、产物不一致失败、手动/工具一致性。
