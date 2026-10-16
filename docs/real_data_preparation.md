# 真实数据准备规范（M35 契约冻结 + M36 数据准备与质量检查）

**基线**：`92944e3`（S 阶段封板）
**用途**：把真实模型 / GPU / Benchmark / 成本数据接进现有系统时，**照着本文准备即可**，
不需要改代码、也不需要新增数据格式。

> 数据只走既有通道：`import-models` / `import-hardware` / `import-performance`
> / `import-cost-catalog` / `record-sla-evidence` / `import-quote-approvals`。
> 本轮**没有**引入任何平行的数据 Schema。

---

## 一、核心数据契约冻结（M35）

以下契约自第五阶段起冻结。变更需提供兼容方案与测试，不得直接替换。

### 1.1 `BenchmarkRun`（真实性能数据的最小载体）

| 部分 | 冻结内容 |
|---|---|
| 身份 | `run_id`、`signature.{model_name, model_revision, weight_precision, hardware_model, gpu_count, tp_size, dp_size, framework, framework_version}` |
| 负载身份 | `signature.workload.{input_len_target, output_len_target, dataset_name, arrival_mode, configured_concurrency}` |
| 指标 | `profile.{ttft_p99_ms, tpot_p99_ms, itl_p99_ms, e2e_p99_ms, e2e_mean_ms, output_throughput_tok_s, request_throughput_rps, error_rate}` |
| 溯源 | `raw_file`、`raw_sha256`、`raw_format`、`collected_at`、`data_trust`、`quality_flags` |

**部署身份与负载身份必须齐备**才可能达到 `exact_match`；
缺任一字段只会得到"证据不足/参考级"结论 —— 这是刻意设计。

### 1.2 `PlanRequest` / `PlanResult`（统一入口契约）

* `PlanRequest` 必需字段：`model_id`、`hardware_ids`、`workload.input_len`；
  建议：`server_id`、`workload.output_len/concurrency`、`resources.*`；
  可选段落：`sla` / `cost` / `demand` / `budget` / `governance`；专业字段在 `advanced`。
* `PlanResult` 四个领域状态与四个候选字段组（资源 / 性能 / 经济 / 治理）冻结；
  领域状态取值域 `computed` / `not_configured` / `missing_data` / `blocked` / `error` 冻结。
* 候选上的业务取值**沿用**既有 literal（`MatchLevel` / `SlaStatus` / `rank_bucket`），
  不另立平行状态体系。

---

## 二、数据准备清单（M36）

逐域列出"要准备什么、从哪来、什么算合格"。**不合格不会报错，但会被标为不可用于生产结论。**

### 2.1 模型结构（`import-models`）

| 项 | 要求 |
|---|---|
| 来源 | 官方 `config.json` + 权重索引（归档到 `data/models/<id>/`，记录 sha256） |
| 必备字段 | 层数、hidden size、注意力头数、KV 头数、head_dim、词表大小 |
| 精度 | 本次部署请求的权重精度（bf16 / fp8 …） |
| 溯源 | `revision` 必须是**具体版本**，不能写 `main` 之类的浮动引用 |

缺失后果：`missing_fields`；结构字段缺失会让显存估算不可用。

### 2.2 硬件规格（`import-hardware`）

| 项 | 要求 |
|---|---|
| 标称容量 | 来自厂商公开页面（含 sha256 存档） |
| **可用显存** | **必须实测**（工作机上 `nvidia-smi`），不能用标称值代替 |
| 服务器形态 | 每节点卡数、节点内互联（NVLink/PCIe） |

缺失后果：`usable_memory_bytes` 为空时规划只给 `conditional_feasible` / `insufficient_information`。

### 2.3 性能实测（`import-performance`）

| 项 | 要求 |
|---|---|
| 原始文件 | 全量保留（vLLM `bench serve` JSON / SGLang `bench_serving` JSONL），并记录 sha256 |
| 部署身份 | 模型 + revision + 精度 + GPU 型号 + 卡数 + TP/DP + 框架 + 框架版本 |
| 负载身份 | 输入/输出长度 + 数据集 + 到达方式 + **配置并发** |
| 指标 | TTFT/TPOT（或 ITL/E2E）的 P99 + 输出吞吐 + 错误率 + 测试时长 + 样本量 |
| 可信等级 | 自有环境实测 `measured_internal`；厂商公开 `measured_vendor_published` |

**红线**：

* 客户端与服务端指标不得混用（`metric_side` 必须一致）；
* 无 P99 时**不得**从 Mean 估算 P99；
* 配置并发与实际并发分开记录；
* Prefix Cache 的**物理命中率**不等于可计费缓存比例；
* 同一配置的多次 Run 会被保守聚合（P99 取最大、吞吐取最小）。

### 2.4 成本与计费规则（`import-cost-catalog`）

| 项 | 要求 |
|---|---|
| 价格 | `unit_price` 或明确留空（**未知不等于零**） |
| 币种 | `currency` + `currency_status`；两者齐备才可能标 `confirmed` |
| 适用范围 | `applies_to_hardware_id`（GPU SKU）、`region`、`valid_from/valid_to` |
| 状态 | `confirmed` / `pending_business_confirmation` / `synthetic_example` |
| 计费规则 | 单价 + 可计费缓存比例**及其依据** + 批处理折扣 + 失败是否计费 |

### 2.5 治理证据（`record-sla-evidence` / `import-quote-approvals`）

| 项 | 要求 |
|---|---|
| SLA 证据 | 由 `record-sla-evidence` 生成并落库，含需求快照、参与 Run、策略快照与两个指纹 |
| 审批记录 | 由业务方提供，含审批人、时间、有效期、凭据、作用域与成本快照指纹 |

**部署级限制**：本系统未接入可信审批系统，审批记录只做持久化、
**不校验审批人身份与权限**，因此**不得用于生产环境的报价自动发布**。

---

## 三、数据质量分级（M36）

```bash
python -m llm_capacity_pricing.cli data-quality --db data/local.db --out quality.json
```

报告只读：**不修改任何记录的信任等级**，也不会把 synthetic / 未验证数据升级为生产级。

| 状态 | 含义 | 能否支撑生产结论 |
|---|---|---|
| `complete` | 字段齐备、来源属于测量类可信等级 | ✅ 可以 |
| `unverified` | 来源真实但尚未核实（页面级证据、状态待确认） | ❌ 只能参考/建模 |
| `missing_fields` | 字段缺失（报告会**逐项列出缺什么**） | ❌ |
| `not_applicable` | 当前配置/口径下不适用（如 SKU 本轮不支持） | ❌ |
| `synthetic` | 人工构造，仅用于验证算法 | ❌ 绝不可 |

**默认不乐观**：`production_grade` 只在 `status == complete` 时为真；
审批记录**恒为 False**（因为身份与权限未经校验）。

报告末尾的 `gaps` 是可执行的补数据指引，例如：

* 没有任何可直接用于生产结论的实测 → 去补目标配置的压测（见 §四）并补齐身份字段；
* 有 SKU 未实测可用显存 → 在工作机执行 `nvidia-smi`；
* 没有已确认价格 → 整理真实价格并置 `status: confirmed`。

---

## 四、压测实验清单（M37 将补齐元数据模板）

以 Qwen3-32B BF16 + H100 SXM 为首轮验证对象；资源允许时比较 TP4/DP1 与 TP2/DP2。

| 工作负载 | 并发 | 目的 |
|---|---|---|
| 1K / 256 | 1、8 | 建立低负载参考 |
| 8K / 256 | 1、8、16、32 | 常用上下文下的并发性能 |
| 32K / 256 | 1、8、16 | 长上下文影响 |

每个实验点需记录：模型 revision、GPU SKU 与卡数、TP/DP、框架版本与精度、
运行参数、输入/输出长度、并发与到达方式、warmup、缓存策略、测试时长与重复次数、
必需指标（TTFT/TPOT/ITL/E2E/吞吐/错误率）。

**代表点应至少进行多次独立重复测试**；能否验证最大可持续容量还需满足预定义的
时长、错误率与稳定性门槛（默认：时长 ≥ 60s、错误率 ≤ 1%、每点重复 ≥ 2 次）。

> 上述矩阵是**测试任务**，不是已获得的性能结果。
> 没有对应硬件时，只能完成实验清单与导入机制的工程验收 —— 真实性能验收保持"未验证"。

---

## 五、常见拒绝原因速查

| 现象 | 原因 | 处理 |
|---|---|---|
| 匹配等级停在 `comparable_reference` | 目标或记录缺部署/负载身份 | 补 `model_revision`、`framework_version`、`arrival_mode`、`configured_concurrency` |
| SLA 恒为 `not_evaluable` | 没有 `exact_match` 的实测，或指标缺失 | 按 §四 补测并导入 |
| 成本标为不完整 | 缺单价/缺假设/覆盖缺口/价格不适用 | 看 `TcoResult.completeness_reasons` 逐条补 |
| 报价被阻断 | 合成价格 / 币种未确认 / SLA 非实测 / 规则或审批不匹配 | 看 `QuoteEligibility.blockers` 逐条补 |
| 显存规划只给条件可行 | 可用显存未实测 | `nvidia-smi` 记录后填入硬件目录 |
