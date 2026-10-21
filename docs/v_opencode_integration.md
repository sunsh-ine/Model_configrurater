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
4. 把 server password 写入 `data/agent/opencode.credentials`（0600，git 忽略）。

## 安全边界

| 项 | 做法 |
| --- | --- |
| 监听 | `127.0.0.1` only |
| 认证 | HTTP Basic Auth；密码只写 `data/agent/opencode.credentials`（0600，避开 `*.env` 通配） |
| Agent 工具权限 | shell/edit/glob/grep/list/bash/task/webfetch/websearch/lsp/skill/question 全部 deny；read 仅允许 `.env` 类（free-tier 鉴权需要）；只放行三个业务工具 |
| 全局能力 | 隔离配置，不加载全局 MCP / skills / instructions |
| 浏览器 | 不接触模型 API Key |
| 工具输出 | 只写 `data/agent_runs/`；`run_id` 白名单校验，阻止路径穿越 |
| 失败 | 无工具产物即 `failed`，不生成报告 |

## 当前阻塞：模型可用，但工具未暴露

61 上的 OpenCode v2.1.2 CLI 可以直接调用 free 模型，实测可用：

- `opencode/glm-5.3-flash-free`、`opencode/big-pickle`、`opencode/mimo-v2.6-flash-free`；
- `opencode/deepseek-v4.1-flash-free` 当前上游返回 503。

但还有两个限制：

1. **`opencode serve` 的 free tier 仍被限制**（即使 v2.1.2 也返回 403
   “only be used from within OpenCode”）；只有客户端 `opencode run` 能用 free 模型。
   走 HTTP 服务端需要付费 provider。
2. **v2.1.2 没有加载 `.opencode/tools/` 下的自定义工具**：模型实际只看到 7 个
   内置 opencode 工具，调用 `get_platform_catalog` 返回 `Unknown tool`。
   已尝试项目目录、仓库根目录与隔离的全局 tools 目录，均未暴露。

因此“自然语言 → 模型 → 业务工具 → 报告”仍未完整跑通，V5 不写成通过。
两条可行路径：

- 用 **MCP**（官方支持）暴露同样三个工具（当前配置已禁用全局 MCP，
  可新增一个本地 MCP server）；
- 或换到确实支持 `.opencode/tools` 的版本，或使用付费 provider 走 serve。

已验证的部分：

- 服务启动、Basic Auth、会话/消息/等待接口；
- v2.1.2 模型（glm-5.3-flash-free）能在客户端与会话中正常回复；
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
