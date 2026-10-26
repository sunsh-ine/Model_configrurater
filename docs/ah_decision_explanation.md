# AH 决策解释结构

基线：`01a1fb3`

## 1. 单一结构

实现：`reporting/decision_evidence.py`

`PlanResult.decision_evidence` 只从已有结果派生，不重新计算 SLA、成本或匹配等级。
CLI JSON、Agent 结果、Markdown 与 UI 使用同一份结构。

顶层字段：

| 字段 | 含义 |
|---|---|
| `schema_version` | 结构版本 |
| `objectives_are_independent` | 最少 GPU / 最高吞吐 / 最低成本是不同目标 |
| `ranking` | 本轮排序目标 |
| `recommended` | 严格推荐（只有完整证据且预测达标） |
| `best_effort` | 未达标参考（不是推荐） |
| `min_gpu_for_sla` | 满足 SLA 的最小 GPU 配置 |
| `throughput_best` | 预测达标候选中的吞吐最高者 |
| `candidates` | 方案候选（Sizing/Performance 视图） |
| `simulation_candidates` | 每个模拟候选项，含 P99、重复性、完整性、校准 |
| `evidence` | 实测/模拟/公开参考/模拟状态 |
| `blockers` | 结构化阻断 |
| `rules` | 展示时必须保留的边界 |

## 2. 候选必须回答

* 为什么符合或不符合 GPU/资源约束；
* 为什么 SLA 通过、失败或无法判断；
* 排序依据（吞吐/成本）是什么；
* 实验完整性、重复性与校准状态；
* 是否有 `mixed` / `incomplete` / `invalid` / 配置不匹配风险；
* 哪些条件变化会改变结论（10%/20% 敏感性）。

## 3. 禁止

* 在报告层另算一套 SLA；
* 把 `best_effort` 写成“推荐”；
* 把模拟重复性当成真实稳定性；
* 缺少真实成本时宣称“成本最优”；
* 隐去 `unknown` 或把缺失写成 0。
