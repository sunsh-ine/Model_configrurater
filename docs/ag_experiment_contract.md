# AG1/AG2 实验清单与完整性契约

基线：`24ac68f`

## 1. 为什么需要清单

AF 的重复性聚合只统计“实际找到的 seed”。如果预期 5 个 seed 中失败或损坏的归档
被跳过，剩余通过运行仍可能被误写成 `all_pass`。AG 用**运行前冻结的实验清单**
解决这个问题：清单是预期，归档是实际，两者必须对齐。

## 2. 契约

实现：

* `src/llm_capacity_pricing/schemas/experiment_manifest.py`
* `src/llm_capacity_pricing/perf/experiment_audit.py`
* 生成脚本：`scripts/build_experiment_manifest.py`

清单字段：

| 类别 | 字段 |
|---|---|
| 实验身份 | `experiment_id`、`created_at`、`tool`、`tool_version` |
| 部署 | 模型/精度、system、后端/版本、GPU 数、TP/PP/DP、replicas |
| 负载 | ISL/OSL、并发或到达率、dataset、trace、请求数、停止条件 |
| 重复实验 | `expected_seeds`、`required_completed_requests` |
| 参考 | `config_file`、`config_sha256` |
| 运行记录 | 每个 seed 的 `status`、归档路径、归档 SHA256、配置 SHA256、完成请求数 |

`runs` 中的 seed 必须在 `expected_seeds` 中且不能重复；固定并发必须声明并发，
Poisson/constant-rate 必须声明到达率，trace 必须声明 trace_id。

当前 AF 清单：

* `data/simulation/experiments/af-8b-h100-dp2-r4.json`
* `data/simulation/experiments/af-8b-h100-dp2-r5.json`
* `data/simulation/experiments/af-8b-h200-dp2-r5.json`

每组 5 个预期 seed，`required_completed_requests=500`。

## 3. 两层状态

| 层 | 取值 | 含义 |
|---|---|---|
| 实验完整性 | `complete` / `incomplete` / `invalid` | 预期运行是否齐全、有效 |
| 模拟 SLA 结论 | `all_pass` / `all_fail` / `mixed` / `unknown` | 只有完整性为 `complete` 才给完整分类 |

有效运行必须同时满足：

1. seed 在预期集合内；
2. 归档存在且文件哈希/预测元数据校验通过；
3. 实际完成请求数 ≥ 清单要求；
4. 有可用 P99 结论（≥30 样本且非 NaN/Inf）；
5. 同一 seed 的多个归档结论/样本窗口一致。

缺少任一条件：

* 缺预期 seed → `incomplete`；
* 同 seed 结论冲突 → `invalid`；
* 清单之外的额外 seed → 该候选标记 `invalid`，不参与本实验聚合；
* 没有任何清单但存在多个 seed → `incomplete`（不能声称完整）。

## 4. 推荐与扫描

`SimulationResult.rejection_reasons()` 对 `incomplete` / `invalid` 候选给出
“实验清单不完整”原因，正式推荐池不接收。

主推荐、容量扫描、到达率扫描共用同一套规则：

* 主推荐：`load_archived()` 应用清单；无清单的多 seed 保持 `incomplete`；
* 容量/到达率扫描：按完整部署+负载身份分组，应用清单预期 seed；
  完成请求数不足、seed 缺失、同 seed 冲突都返回 `unknown`。

## 5. 当前结果

| 实验 | 预期/发现/有效 | 结论 |
|---|---|---|
| H100 DP2 4 req/s | 5/5/5 | all_pass |
| H100 DP2 5 req/s | 5/5/5 | all_fail |
| H200 DP2 5 req/s | 5/5/5 | mixed（3/5 通过） |

AF 归档中额外的 `seed42` 不属于实验清单，在清单生效的工作点中被排除并标记
`invalid`，不会稀释或改变 5 seed 结论。
