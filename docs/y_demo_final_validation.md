# Y 阶段验收：完整系统 Demo 发布

日期：2026-10-22
基线：`d18779f` + 本阶段改动
环境：Command Code（client API）/ MCP 2.3.0 / bwrap 0.9.0 / 离线归档模拟

## 总结

- **981 passed, 1 skipped**；`verify_all.sh` **66/66**；
- 手动、预设、Agent 三条链路全部跑通，并在相同输入下得到一致的业务结论；
- 本地单用户 Demo 冻结；多用户隔离与受控部署仍列为正式部署前置条件。

## Y1：本地 Agent 环境收尾

### 信任边界

- 本地单用户模式：`data/agent_runs` / `data/agent_tasks` 对沙箱可读写，
  不同任务之间**共享**这些目录；这是已知边界，不是多用户隔离。
- 多用户任务隔离、用户身份认证、每用户报告目录：正式部署前置条件（X5 设计）。
- bwrap 限制文件系统访问；`--share-net` 保留网络用于模型调用，
  **不是**网络隔离，也不阻止白名单外工具被发现；越权调用由事件检查拒绝结果。

### 凭据与 MCP 最小化

- 每任务 HOME 只复制 `auth.json`、`config.json` 与**仅含 lcp 的** `mcp.json`；
  不复制其他 MCP server、历史、缓存。
- 任务完成后自动删除该任务 HOME；`needs_confirmation` 保留以支持同会话续跑；
  超过 24 小时的残留目录在下次准备时清理；真实 HOME 从不删除。
- 实测：`needs_confirmation` 时 HOME 存在；补充完成（run_id
  `20261022T022220Z_debdb4`）后 HOME 已删除，报告仍在。

### 路径修正

- 文档统一为 `data/agent/cc-homes/<task_id>/`；
- PATH 不再硬编码 `/home/ssa/...`，改为从实际 `command-code` / `node` 路径推导。

## Y2：三种业务流程

| 模式 | 验收内容 | 结果 |
| --- | --- | --- |
| 手动 A | Qwen3-32B / H100 / 8192-256 / 并发 32 / 严格 SLA | 3 候选，TP2 不可行，预测未达标，成本不完整，不可报价 |
| 预设 B | strict / relaxed / cost | strict 无推荐；relaxed 预测达标且 P99 不变；cost 拒绝成本最优、预算未知 |
| Agent C | 自然语言 → H100 歧义 + 缺 OSL → 同会话补充 | 先 needs_confirmation，补充后 completed（`20261022T022220Z_debdb4`） |

一致性：

- `tests/test_y_demo.py` 比较手动、预设 strict、Agent 工具、Agent 服务四种路径的
  候选、静态状态、SLA、成本完整性、报价资格与模拟 P99/verdict；
- run_id 不要求相同；业务结论逐项一致。

## Y3：结果展示

- 首页主状态「方案已生成」；`partial` 只作为分析覆盖说明；
- 预测与实测分开：预测推荐明确标注“不是实测”，无实测证据时写“证据不足”；
- 预算未知显示「未知」，不写成满足预算；
- 无推荐时给出未达标参考与主要限制；
- Agent 文本在“Agent 说明”中，结构化结果优先；
- 客户方案中的模型、GPU、TP/PP/DP、P99 与成本状态来自同一 `PlanResult`。

## Y4：交付与冻结

### 修复的缺陷

Y4 截图验收发现：Agent 完成态调用了不存在的 `_render_result`
（手动模式是内联渲染），导致 Agent 完成页报错。已把手动结果渲染抽成
`_render_result()`，手动与 Agent 共用；手动与 Agent 完成页均实测通过。
这是本阶段唯一发现的展示层缺陷，已修复并回归。

### 截图

| 文件 | 内容 |
| --- | --- |
| `docs/ui/y-phase/manual_strict.png` | 手动模式严格 SLA |
| `docs/ui/y-phase/predicted_pass.png` | 宽松 SLA 预测达标 |
| `docs/ui/y-phase/agent_confirmation.png` | Agent 缺字段确认（H100 候选、补充输入） |
| `docs/ui/y-phase/agent_completed.png` | Agent 完成报告（假设、核心对比、下载） |

### 回归

- 全量 `pytest`：981 passed / 1 skipped；
- `verify_all.sh`：66/66；
- codex（`gpt-6-luna` + `high`）审核后提交。

### 版本与回滚

- 本阶段提交为本文件所在提交；
- 回滚到上一稳定点：`git revert` 本提交，或检出 `d18779f`；
- `compute01` 的 v1.3 手动模式容器不随本阶段变化；
- Agent 模式不回滚到无沙箱运行。

## 未做

- 多用户隔离、身份认证、受控部署（X5 设计，未实施）；
- 公网访问、Agent 部署到 61；
- OpenCode 真实链路；
- N5 真人验收、N6 真机 Benchmark、实时 AISimulate；
- 新的性能算法、报价发布、多 Agent。
