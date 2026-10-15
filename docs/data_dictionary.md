# 字段字典（data dictionary）

本文件描述标准化数据模型。**核心原则：原始文件未提供的字段一律留空（`null`），
不允许用 0 代替"未知"，不允许从策略字符串或模型名补齐后宣称已核实。**

单位约定：

| 记号 | 含义 |
|---|---|
| `10k` | 万元 |
| `_per_gpu` | 每卡（单卡）口径 |
| `_cluster` | 集群口径（同一 Benchmark 配置的 N 卡总和） |
| `_tps` | tokens/second |
| `_per_1m` | 每百万 Token |
| `_ms` | 毫秒 |

---

## 1. `Evidence`（公共溯源）

| 字段 | 类型 | 可空 | 说明 |
|---|---|---|---|
| `evidence_id` | str | 否 | 主键，如 `ev_excel_部署效能_2` |
| `source_type` | enum | 否 | `internal_excel` / `official_benchmark` / `third_party` / `internal_measurement` / `derived` / `assumption` |
| `publisher` | str | 是 | 发布方。Excel 效能数据的发布方记为 `InferenceX`（第三方） |
| `source_url` | str | 是 | 官方来源 URL |
| `source_file` | str | 是 | 文件名 |
| `source_sheet` | str | 是 | 工作表名 |
| `source_row` | int | 是 | 原始行号（1 基，与 Excel 一致） |
| `source_version` | str | 是 | 版本标识 |
| `retrieved_at` | datetime | 是 | 抓取/导入时间 |
| `raw_sha256` | str | 是 | 原始文件或原始页面的 sha256 |
| `notes` | str | 是 | 原始值留档 |
| `quality_flags` | list[str] | 否 | 质量标记，见 §8 |
| `measurement_origin` | enum | 否 | **载体≠归属**：`historical_external_third_party` / `official_benchmark` / `internal_measurement` / `synthetic_fixture` / `derived_or_assumption`。默认 `historical_external_third_party` |
| `is_internal_measurement` | bool | 否 | 是否自有实测。必须与 `measurement_origin == "internal_measurement"` 一致，否则 Schema 直接报错 |

证据强度（`EVIDENCE_STRENGTH`）：`assumption` 0 < `derived` 1 < `internal_excel`/`third_party` 2
< `official_benchmark` 3 < `internal_measurement` 4。

`source_type` 与 `measurement_origin` 的允许组合由 `ALLOWED_ORIGINS` 强制：
`internal_excel` 只允许 `historical_external_third_party` / `derived_or_assumption`，
`third_party` 只允许 `historical_external_third_party`，`official_benchmark` 只允许
`official_benchmark`，`internal_measurement` 只允许 `internal_measurement`。
因此**不可能**把 Excel 里的外部数据标成"内部实测"。

## 2. `HardwarePrice`

| 字段 | 类型 | 可空 | 来源 / 说明 |
|---|---|---|---|
| `hardware_price_id` | str | 否 | `excel_GPU价格_{row}` |
| `gpu_model` | str | 否 | `GPU价格!A` |
| `gpus_per_server` | int | 是 | `GPU价格!B`。**每服务器卡数**，与 `deployment_gpu_count` 含义不同 |
| `per_gpu_annual_cost_10k` | Decimal | 是 | `GPU价格!C`，单位万元/卡/年（按表头语境推断） |
| `currency` | str | 是 | **Excel 未写明币种，保持 `None`** |
| `currency_assumption` | str | 是 | 固定为 `CNY; pending business confirmation` |
| `cost_scope` | str | 是 | 成本覆盖范围假设，标注"待业务确认" |
| `valid_from` / `valid_to` | date | 是 | 未提供 |
| `evidence_id` | str | 否 | 关联证据 |

当前 2 条：B300（8 卡/服务器，52.5）、H200（8 卡/服务器，15）。

## 3. `TokenPrice`

| 字段 | 类型 | 可空 | 来源 / 说明 |
|---|---|---|---|
| `token_price_id` | str | 否 | `excel_模型价格_{row}` |
| `model_name` | str | 否 | `模型价格!A`，**原样保留**（如 `DeepSeek-V4-Pro（峰值）` 含全角括号） |
| `price_cached_input_per_1m` | Decimal | 是 | `模型价格!B`，货币单位/百万 Token |
| `price_uncached_input_per_1m` | Decimal | 是 | `模型价格!C` |
| `price_output_per_1m` | Decimal | 是 | `模型价格!D` |
| `currency` | str | 是 | 保持 `None`，币种待确认 |
| `currency_assumption` | str | 是 | `CNY; pending business confirmation` |
| `effective_date` | date | 是 | 未提供 |
| `evidence_id` | str | 否 | 关联证据 |

当前 3 条：`KIMI-K3`(2/20/100)、`GLM-5.2/5.3`(2/8/28)、`DeepSeek-V4-Pro（峰值）`(0.3/9/27)。
模型名不与任何"别名/后代模型"做等价推断。

## 4. `BenchmarkRecord`

### 4.1 必需字段

| 字段 | 类型 | 说明 |
|---|---|---|
| `record_id` | str | `excel_部署效能_{row}`，可反解工作表与行号 |
| `model_name` | str | 原样保留 |
| `hardware_model` | str | GPU 型号 |
| `deployment_gpu_count` | int | `部署效能!C`。**部署卡数**，不是 `GPU价格!B` 每服务器卡数 |
| `evidence_id` | str | 关联证据 |
| `performance_source_kind` | enum | `internal_excel` / `official_benchmark` / `third_party` / `internal_measurement` / `synthetic_fixture` |

### 4.2 模型 / 框架 / 硬件

`model_revision`、`model_precision`、`framework`、`framework_version`、`driver_version`、
`hardware_sku`、`node_count`、`gpus_per_node` —— **Excel 全部未提供，均为 `None`**。

### 4.3 并行与拓扑

`tp_size`、`dp_size`、`ep_size`、`pp_size`、`prefill_gpu_count`、`decode_gpu_count`、
`deployment_mode`、`deployment_strategy_raw`、`deployment_strategy_parsed`。

- `deployment_strategy_raw` = `部署效能!N` 原文，逐字保留。
- `deployment_strategy_parsed` 只做**未核实解析**：始终带 `verified: False` 与
  说明文字，可读出 `tp`/`ep`/`dp`/`dcp`/`prefill_p`/`decode_d`/`mtp` 等 token，
  但**不得**写入 `tp_size` 等已核实字段。
- `deployment_mode` 仅在策略串出现 `P16D16` 形态时置为
  `prefill_decode_disaggregated`，否则为 `unknown`。

### 4.4 负载

| 字段 | 来源 | 说明 |
|---|---|---|
| `configured_concurrency` | `部署效能!D` | **配置的**并发数。不等于实际平均并发，也不等于请求到达率 |
| `mean_observed_concurrency` | — | 未提供，`None` |
| `request_rate_rps` | — | 未提供，`None` |
| `num_requests` / `success_count` | — | 未提供，`None` |
| `input_tokens_target` / `output_tokens_target` | — | 未提供，`None` |
| `input_length_distribution` / `output_length_distribution` | — | 未提供，`None` |
| `dataset_name` / `dataset_digest` | — | 未提供，`None` |

> 官方文档确认 SGLang 的 `Concurrency` 口径是 "aggregate time of all requests divided by
> wall time"（全请求累计耗时 / 墙钟时间），与 `--max-concurrency` 配置值不同。
> 这是 D 列必须叫 `configured_concurrency` 的直接依据。

### 4.5 吞吐

| 字段 | 来源 | 说明 |
|---|---|---|
| `input_tps_per_gpu` | `部署效能!F` | 每卡输入吞吐（tok/s/卡） |
| `output_tps_per_gpu` | `部署效能!G` | 每卡输出吞吐（tok/s/卡） |
| `input_tps_cluster` / `output_tps_cluster` | 派生 | 仅按**同一配置**的 N 卡归一化还原 |
| `input_tps_cluster_basis` | 派生 | 文字说明派生依据，含"禁止跨 GPU/TP 外推" |
| `single_user_output_tps_p90` | `部署效能!E` | 单用户输出 P90 吞吐。**不得换算为 TPOT P99** |

Excel 记录中 `input_tps_cluster`/`output_tps_cluster` 保持 `None`（Excel 未给出集群吞吐，
也没有可归一化的集群口径数值）；适配器导入的记录会在已知集群吞吐与卡数时归一化，
并打 `per_gpu_throughput_derived_from_cluster_and_gpu_count` 标记。

### 4.6 时延与可靠性

`ttft_mean_ms`、`ttft_p99_ms`、`tpot_mean_ms`、`tpot_p99_ms`、`itl_p99_ms`、
`e2e_p99_ms`、`failure_rate`、`metric_definitions`、`success_count`、`duration_s`。

Excel 记录中全部为 `None` → 任何 SLA 结论均为 `not_evaluable`。

`metric_definitions` 记录同名指标的**来源口径**（例如说明 P99 的 percentile 方法、
说明 `mean_tpot_ms` 是来源直出还是适配器派生）。

### 4.7 缓存口径

| 字段 | 来源 | 说明 |
|---|---|---|
| `cache_hit_rate_raw` | `部署效能!H` | 原始命中率数值，语义未确认 |
| `physical_cache_hit_rate` | — | 物理 KV 命中率；Excel 未提供，`None` |
| `billable_cached_input_ratio` | — | **可计费**缓存命中 Token 比例；Excel 未提供，`None` |
| `cache_metric_semantics` | 默认 `unknown` | 未核实前不得宣称是计费命中 |
| `legacy_proxy_for_billable_hit_rate` | 默认 `True`（Excel 记录） | 显式声明 H 列被当作旧表**定价代理假设** |

> **物理 KV 命中率 ≠ 可计费缓存命中 Token 比例。** 本阶段两者都留空，只在旧表复算中
> 使用 H 作为代理，并强制打标记。

### 4.8 旧表原始值（只读留档）

`legacy_input_revenue_10k_per_gpu_year`(I)、`legacy_output_revenue_10k_per_gpu_year`(J)、
`legacy_total_revenue_10k_per_gpu_year`(K)、`legacy_cost_10k_per_gpu_year`(L)、
`legacy_revenue_cost_ratio`(M)。新计算结果另存于 `PricingResult`，**不覆盖**这些字段。

### 4.9 Goodness 与溯源字段

`format_version`、`raw_metrics`（原始列值/原始 JSON 全量）、`missing_fields`、
`quality_flags`、`verification_status`。

`verification_status` 取值：`unverified_parse` / `partial_evidence` /
`verified` / `synthetic_fixture`。

## 5. `PricingScenario` / `PricingResult`

### 5.1 场景

`scenario_id`、`benchmark_record_id`、`token_price_id`、`hardware_price_id`、
`calculation_year_seconds`（默认 31536000）、`mode`（`legacy_full_load` | `scenario`）、
`effective_monetization_ratio`（U，仅 scenario 模式）、`availability_ratio`、
`billable_ratio`、`legacy_hit_rate_as_billing_proxy`。

### 5.2 结果

| 字段 | 单位 | 说明 |
|---|---|---|
| `input_revenue_10k_per_gpu_year` | 万元/卡/年 | 输入侧理论年收入 |
| `output_revenue_10k_per_gpu_year` | 万元/卡/年 | 输出侧理论年收入 |
| `total_revenue_10k_per_gpu_year` | 万元/卡/年 | **满载**输入+输出合计 |
| `gpu_annual_cost_10k` | 万元/卡/年 | 表内 GPU 成本 |
| `revenue_cost_ratio` | 无量纲 | 满载收入 / GPU 成本 |
| `breakeven_monetization_ratio` | 比例 | 成本 / 满载收入；> 1 表示满载也不覆盖成本 |
| `deployment_gpu_count` | 卡 | 原配置卡数 |
| `total_cluster_cost_10k_year` | 万元/年 | 成本 × 卡数 |
| `total_cluster_revenue_10k_year` | 万元/年 | 满载收入 × 卡数 |
| `scenario_revenue_10k_per_gpu_year` | 万元/卡/年 | 仅 scenario：满载收入 × U |
| `scenario_surplus_before_other_costs_10k_per_gpu_year` | 万元/卡/年 | 情景收入 − GPU 成本 |
| `scenario_cluster_revenue_10k_year` | 万元/年 | 情景收入 × 卡数 |
| `status` | enum | `ok` / `partial` / `not_evaluable` |
| `not_evaluable_reason` | str | 不可计算原因 |
| `scope` | enum | `business` / `test`。标出该结果是否来自人工测试数据（fixture） |
| `result_type` | enum | `theoretical_full_load_potential` / `scenario_estimate`。**说明这是理论上限或情景估算，不是实际营收、不是报价** |
| `performance_record_id` | str | 指向 `BenchmarkRecord`（吞吐/时延来源），构成结果→性能记录的证据链 |
| `hardware_price_id` | str? | 指向 `HardwarePrice`（成本来源）；找不到时 `None` 并写 `linkage_warnings` |
| `token_price_id` | str? | 指向 `TokenPrice`（单价来源）；找不到时同上 |
| `evidence_ids` | list[str] | 该结果引用到的全部证据 ID |
| `linkage_warnings` | list[str] | 缺少价格关联等情况的显式提示（**不静默**） |
| `sla_status` | enum | `not_evaluable` / `evaluable`。缺 P99 TTFT/TPOT 时恒为 `not_evaluable` |
| `sla_not_evaluable_reasons` | list[str] | 为什么不可评估（逐项列出缺失的 P99 字段） |
| `cost_scope` | str | 固定 `gpu_cost_only`，**不是** `total_cost_of_ownership` |
| `currency` | str | 未确认时为 `UNCONFIRMED`（不再用 `None`，避免与"没这个字段"混淆） |
| `currency_assumption` | str | `CNY; pending business confirmation` |
| `price_unit` | str | `currency_unit_per_1M_tokens` |
| `billing_period` | str | `per_gpu_year` |
| `cache_hit_rate_used` / `cache_hit_rate_is_legacy_proxy` | Decimal/bool | 本次计算使用的命中率与口径标记 |
| `warnings` | list[str] | 业务边界与口径告警 |
| `input_snapshot_hash` | str | 参与计算的输入规范化后的 sha256，用于复算校验 |
| `pricing_engine_version` | str | 引擎版本 |
| `steps` | list[str] | 逐步计算过程（可溯源） |

## 6. `OfficialBenchmarkCandidate`（官方候选，`partial_evidence`）

官方来源中"有部分字段、缺口已逐项记录"的案例不会丢弃，也不会伪装成完整记录，
而是存为候选对象（表 `official_benchmark_candidate`，命令 `import-official`）。

| 字段 | 类型 | 说明 |
|---|---|---|
| `candidate_id` | str | `official_<manifest_id>` |
| `manifest_id` | str | 对应 `data/sources/official_manifest.yaml` 的条目 ID |
| `publisher` | str | `sglang` / `vllm` / `lmsys-sglang` 等 |
| `source_url` | str? | 来源 URL；未能复核时为 `None` 并带 `missing_source_url` |
| `evidence_id` / `evidence_file` / `evidence_sha256` | str | 归档证据与哈希（入库时逐一比对，不一致即报错） |
| `model_name` / `hardware_model` / `deployment_gpu_count` / `model_revision` / `framework_version` / `dataset_name` | 各类型? | 缺则为 `None`，**不允许补造** |
| `hardware_binding` | enum | `bound` / `unbound`。`unbound` 时硬件字段必须为空（Schema 强制） |
| `is_throughput_benchmark` | bool? | `True` / `False` / `None`（无法机器判定时为 `None`，不猜） |
| `metrics` | dict[str, Decimal] | 已机器解析的指标（当前仅 1 条候选有） |
| `raw_labels` | dict[str, str] | 原始标签文本，用于复核映射（如 `ttft_p99_ms -> "P99 TTFT (ms)"`） |
| `present_fields` / `missing_fields` | list[str] | 由实际模型状态算出，**不是手工声明**；与清单声明不符会报错 |
| `verification_status` | str | 恒为 `partial_evidence` |
| `quality_flags` | list[str] | 见 §8 |

关键约束：候选**不进入** `benchmark_record`，因此不会被 `calculate-all` / `report`
当成可算数据。`success_count == 1` 的候选会被强制标记 `single_request_smoke_test`
且 `is_throughput_benchmark = False`。

## 7. SQLite 表结构

| 表 | 主键 | 说明 |
|---|---|---|
| `evidence` | `evidence_id` | 全部证据（含 `measurement_origin` / `is_internal_measurement`） |
| `hardware_price` | `hardware_price_id` | GPU 价格 |
| `token_price` | `token_price_id` | 模型价格 |
| `benchmark_record` | `record_id` | 效能记录（含 `performance_source_kind`、`verification_status` 便于分级查询） |
| `official_benchmark_candidate` | `candidate_id` | 官方**候选**记录（`partial_evidence`）：字段级缺口台账，**不是**性能记录，不参与任何计算 |
| `pricing_result` | `scenario_id` | 计算结果（含 `scope` / `result_type` / 三个输入 ID / `sla_status`） |
| `import_run` | `import_run_id` | 导入批次（文件、sha256、时间、条数） |

**精度决策**：所有金额/比率列用 `TEXT` 存放十进制字面量（如 `"149.54340799296"`），
不用 `NUMERIC`——SQLite 的 NUMERIC 实际以 REAL(float) 存储，会破坏 Decimal 精度。
每行同时保存 `payload_json`（`model_dump(mode="json")`）作为权威记录，读取时由
Pydantic 把字符串还原为 `Decimal`，保证"落库—读回—复算"完全一致。

**JSON 导出编码**：导出文件里金额/比率字段写成**可精确恢复的十进制字符串**
（定点字面量，不用科学计数法），并在文件头的 `decimals_encoding` 块声明编码方式与
兼容读取入口 `llm_capacity_pricing.reporting.json_export.loads_tolerant`。
理由：JSON number 会被多数读取器落成 double，28 位有效数字不保真。计算路径不受影响，
公式层仍全程 `Decimal`（float 入参会被 `_reject_float` 直接拒绝）。

## 8. 质量标记（`quality_flags`）一览

| 标记 | 含义 |
|---|---|
| `currency_pending_business_confirmation` | 币种未确认 |
| `cost_coverage_scope_pending_business_confirmation` | 成本覆盖范围未确认 |
| `underlying_source_inferencex_not_official_framework_benchmark` | 底层来源为第三方 InferenceX，非框架官方 benchmark |
| `legacy_hit_rate_used_as_billing_proxy` | H 列命中率被当作旧表定价代理假设 |
| `cache_metric_semantics_unknown` | 缓存命中语义未确认 |
| `deployment_strategy_parse_unverified` | 策略字符串解析未核实 |
| `deployment_gpu_count_differs_from_server_gpu_count` | 部署卡数 ≠ 每服务器卡数 |
| `no_matching_hardware_price` / `no_matching_token_price` | 找不到对应价格行（只记录不猜测） |
| `aggregated_from_per_request_samples` | 由逐请求样本聚合 |
| `tpot_not_computable_from_samples` / `tpot_partially_computable` | 样本不足以计算 TPOT |
| `itl_present_kept_separate_from_tpot` | ITL 存在且与 TPOT 分开存储 |
| `refused_substitution:<字段>_left_null_despite_<来源字段>` | 拒绝用不同口径字段顶替 |
| `zero_successful_requests` | 零成功请求 |
| `non_numeric_request_rate_left_null` | 来源写了非数值（如 `"inf"`），留空 |
| `per_gpu_throughput_derived_from_cluster_and_gpu_count` | 每卡吞吐由集群吞吐 ÷ 卡数派生 |
| `failure_rate_derived_from_counts` | 失败率由 (num - success)/num 派生 |
| `synthetic_fixture_not_official_performance_data` | 人工构造，不得进入性能数据库 |
| `downgraded_to_partial_evidence_incomplete_fields` | 因字段不全降级为部分证据 |
| `historical_external_source_not_internal_measurement` | **Excel 导入的每条记录都带**：数据来自历史外部来源，不是内部实测 |
| `metrics_machine_parsed_from_evidence_file` | 候选记录的指标由存档证据文件机器解析得到（可复核） |
| `metrics_not_machine_parsed_present_in_document_only` | 文档中有数值但**未**机器解析，不做人工转写 |
| `missing_source_url` | 来源 URL 未能复核（如 HTTP 429 限流），只有本地存档可追溯 |
| `missing_ttft_p99_ms` / `missing_tpot_p99_ms` | 候选缺对应 P99 指标（据此 SLA 恒 `not_evaluable`） |
| `single_request_smoke_test` | `success_count == 1`，属单请求冒烟测试，不是吞吐基准 |
