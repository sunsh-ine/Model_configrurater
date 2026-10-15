# 第四阶段基线冻结（M26）

**冻结时点**：2026-10-15
**基线提交**：`5a72a711362be73036e9f34064a86ac6c46139c1`（第三阶段 V3.1 最终加固第二轮）
**仓库**：`sunsh-ine/Model_configrurater`（Public）

---

## 一、为什么需要显式冻结

第四阶段要新增"完整 TCO、业务需求经济性、报价门禁"，这些功能会**读取**前三阶段的对象
（成本、性能、容量、SLA）。如果第三阶段的接口在开发过程中继续变动，
第四阶段的成本对比与报价门禁就会建立在不稳定的基础上，历史结论也无法复现。

因此 M26 的第一件事不是写代码，而是把第三阶段的**对外接口**冻结成基线：
第四阶段只允许**读取**与**复用**，不允许改写其结论与计算口径。

## 二、被冻结的接口（第四阶段只读）

### 2.1 第一阶段：历史计算口径（**不得改变**）

| 对象 | 位置 | 冻结内容 |
|---|---|---|
| `HardwarePrice` | `schemas/costs.py` | `per_gpu_annual_cost_10k`、`cost_scope` |
| `TokenPrice` | `schemas/costs.py` | 三个单价字段 |
| `PricingResult` | `schemas/pricing.py` | 全部金额字段与 `COST_SCOPE = "gpu_cost_only"` |
| `calculate_all` | `services.py` | 32 满载 + 160 情景 = 192 条结果的算法 |

**硬约束**：第一阶段只算 GPU 成本，这是**已知边界**而非缺陷。
第四阶段的 TCO **不替换**它，而是在其旁边另建一套完整成本口径；
两者可以并列展示，但不得混用、不得把 TCO 结果写回 `PricingResult`。

### 2.2 第二阶段：资源规划接口

| 对象 | 位置 | 冻结内容 |
|---|---|---|
| `DeploymentCandidate` | `schemas/sizing.py` | `candidate_id` / `tp_size` / `dp_size` / `gpu_count` / `headroom_bytes` / `feasibility` |
| `ResourceFeasibilityResult` | `schemas/sizing.py` | `status` ∈ {infeasible, conditional_feasible, insufficient_information, unsupported} |
| `HardwareSpec` / `ModelSpec` / `ServerProfile` | `schemas/sizing.py` | 字段名与语义 |

第四阶段通过 `candidate_id` 关联成本方案，**不复制**候选对象，也不重新做显存计算。

### 2.3 第三阶段：性能与 SLA 接口（证据链）

| 对象 | 位置 | 冻结内容 |
|---|---|---|
| `BenchmarkRun` | `schemas/performance.py` | `run_id` / `signature` / `profile` / `data_trust` |
| `BenchmarkMatchResult` | `schemas/performance.py` | `level` / `usable_for` / `blocked_from` / `supporting_run_ids` |
| `SlaEvaluation` | `schemas/performance.py` | `status` ∈ {observed_pass, observed_fail, estimated_only, not_evaluable} |
| `DeploymentRanking` / `RankedDeployment` | `schemas/performance.py` | `rank_bucket` / `evidence_run_ids` |
| `CapacityProfile` | `schemas/performance.py` | `maximum_sustainable_capacity` 语义（需重复观测） |

**硬约束**：第四阶段**只能消费** SLA 结论，不得自行降低门禁。
`observed_pass` 仍只接受 `exact_match` + 可信实测，这一点在 M30 的报价门禁中被再次引用。

## 三、三层语义独立性（第四阶段必须继承）

| 层级 | 类型 | 第四阶段的用法 |
|---|---|---|
| 匹配级别 | `MatchLevel` | 只读，用于判断"性能结论是否可信" |
| 数据信任级别 | `DataTrust` | 只读，`synthetic_fixture` 永远不支撑生产结论 |
| SLA 状态 | `SlaStatus` | 只读，`observed_pass` 是 `verified_measured` 的唯一来源 |

第四阶段新增**第四个独立维度**：**商业确认状态**（M30）。
它和前三个维度并列，任何一个都不能替代另一个。

## 四、基线回归基线值（第四阶段不得回退）

| 项 | 基线值 |
|---|---|
| 全量测试 | **499 passed** |
| 全链路步骤 | **36 / 36 通过** |
| 第一阶段回归 | 默认情景 160（32×5）、`pricing_result` 总数 192、超容差不匹配 **0** |
| 第三阶段测试 | 95 passed |

第四阶段每完成一个里程碑都要跑一次全量回归，确认上表数值不变。

## 五、仓库安全状态（**独立事项，未闭环**）

仓库为 Public，历史提交中曾出现内部定价数据。该风险**不因测试通过而消除**，
需要用户明确授权后重写 git 历史（方案与影响评估见 `docs/security_remediation_report.md` §4）。
**本轮未执行任何历史重写或强制推送。**

第四阶段的合成测试数据一律使用明确标记的 `synthetic_example`，
真实商务数据继续留在本地被 git 忽略的目录中（`data/private/`）。
