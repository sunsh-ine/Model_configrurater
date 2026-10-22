# 本地 Agent Demo 启动说明

## 前置

- Python 3.12 + 项目 venv；
- 已登录的 `command-code`（client API）；
- `bwrap` 与 `node`：默认必须可用；否则 Agent 拒绝运行（fail-closed）；
- 无需 GPU；模拟走仓库内归档输出。

## 安装与配置

```bash
cd llm-capacity-pricing

# 1) 主依赖 + Agent 依赖（mcp）
.venv/bin/pip install -r requirements.lock
.venv/bin/pip install -e .
.venv/bin/pip install -r requirements-agent.txt

# 2) 注册本地 MCP server（用户级；不修改项目其他配置）
command-code mcp add --transport stdio --scope user \
  -e "PYTHONPATH=$PWD/src" lcp -- \
  "$PWD/.venv/bin/python" -m llm_capacity_pricing.agent.mcp_server
command-code mcp list          # lcp / stdio / enabled

# 3) 可用性检查
.venv/bin/python - <<'PY'
from llm_capacity_pricing.agent.service import AgentService
print(AgentService(transport="commandcode").available())
PY
```

`AgentService` 会：

- 首次运行前把 `~/.commandcode/{auth.json,config.json,mcp.json}` 复制到
  `data/agent/cc-home/.commandcode/`（0600）；
- 用 `bwrap` 以只读方式挂载代码、venv 与必要数据目录，只对
  `data/agent_runs`、`data/agent_tasks`、`data/agent/cc-workspace` 与沙箱 HOME 开放写；
- **不挂载** `data/private`、`data/local.db`、`data/raw/*.xlsx`、真实 HOME。

调试时可临时关闭沙箱（仅本地）：

```bash
COMMAND_CODE_SANDBOX=0 .venv/bin/streamlit run app/streamlit_app.py
```

关闭后事件检查仍会拒绝越权工具，但不再有文件系统边界；只用于本地排查，不要用于开放访问。
默认情况下若 `bwrap` 或 `node` 不可用，`AgentService.available()` 返回不可用，任务不会降级到宿主机执行。

## 启动页面

```bash
.venv/bin/streamlit run app/streamlit_app.py
```

左侧切换「自然语言（AI Agent）」，输入例如：

> 我有 8 张 H100，想部署 Qwen3-32B，输入 8K，并发 32，TTFT P99 不超过 2 秒。

预期：

1. AI Agent 返回 `needs_confirmation`，列出 H100 SXM/NVL 候选与缺失 OSL；
2. 在“补充信息”里填写型号与 OSL，点“继续分析”；
3. 同一任务完成，显示报告、假设与限制；
4. 下载方案 Markdown、技术报告、JSON；
5. 模拟预测标注为“不是实测”，成本标注为示例目录。

## 常见问题

| 现象 | 处理 |
| --- | --- |
| AI Agent 不可用 | 检查 `command-code --version` 与 `command-code mcp list` |
| MCP 工具未出现 | 确认注册带 `-e PYTHONPATH=$PWD/src`；重开 Streamlit |
| 沙箱启动失败 | 确认 `bwrap --version`；本地调试可 `COMMAND_CODE_SANDBOX=0` |
| 任务 `failed` 且无报告 | 查看页面错误；不会用模型文本生成报告 |
| 模型登录过期 | `command-code` 交互登录后重试；凭据不进入 Git/日志 |
| 报告下载后想核对 | `data/agent_runs/<run_id>/` 内的 `result.json` 是权威来源 |

## 边界

- 只支持离线归档模拟；实时 AISimulate 需要独立环境，不在本 Demo 范围；
- 成本为合成示例，不是报价；生产报价与审批发布保持禁用；
- Agent 模式尚未部署到 `compute01`，也不应接入无鉴权页面。
