# 证据等级策略（evidence policy）

## 1. 为什么需要这份策略

任务书 §0 的最高优先级指令里有三条互相牵连的要求：

1. **不编造数据** —— 原始文件未提供的字段留空。
2. **来源不可混淆** —— Excel 的 32 条效能数据来源是 `InferenceX`（第三方），
   **不是** SGLang/vLLM 官方数据。
3. **指标不可混淆** —— 同名指标在不同 Benchmark 版本口径不同。

这三条只有在"每条数据都能说清它从哪来、有多可信"时才能落地，因此引入了
`Evidence` 与 `quality_flags`。

## 2. 证据等级

| `source_type` | 强度 | 含义 | 本项目实例 |
|---|---|---|---|
| `assumption` | 0 | 人工假设，**不是**测量结果 | 4 条 synthetic fixture 的 evidence |
| `derived` | 1 | 由其它数据算出 | 集群吞吐归一化、派生失败率 |
| `internal_excel` | 2 | 内部 Excel 中的既有数据 | 37 条（2 GPU + 3 价格 + 32 效能） |
| `third_party` | 2 | 第三方发布的数据 | 本项目通过 `publisher=InferenceX` 标注底层来源 |
| `official_benchmark` | 3 | 框架官方发布的实测数据 | **当前 0 条**（见 `official_coverage_gaps.md`） |
| `internal_measurement` | 4 | 自有实测 | 当前 0 条 |

规则：

- **不得跨级使用。** `assumption` / `derived` 级别的数据不能用来支撑 SLA 或对外结论。
- **不得升格。** 不能因为"看起来合理"就把第三方数据标成官方数据。
- **fixture 隔离。** `synthetic_fixture` 记录以 `assumption` 级证据入库，带
  `performance_source_kind = "synthetic_fixture"` 与 `verification_status = "synthetic_fixture"`，
  并且**永远不进入**业务口径的统计与计算：`calculate-all` / `scenario` / `report` 默认
  只算业务数据，要显式加 `--include-test-data` 才会带上 fixture，结果上还会打
  `scope = "test"`（详见 README §4）。

## 3. 载体 ≠ 归属：`measurement_origin`

`source_type` 只回答"这份数据是通过什么**载体**进到系统里的"，它**不等于**"数据是谁测的"。
一张放在我们内部目录里的 Excel，内容仍可能是第三方汇总的历史数据。为把这两件事彻底分开，
`Evidence` 增加了两个字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| `measurement_origin` | enum | `historical_external_third_party` / `official_benchmark` / `internal_measurement` / `synthetic_fixture` / `derived_or_assumption` |
| `is_internal_measurement` | bool | 是否自有实测；必须与 `measurement_origin == "internal_measurement"` 严格一致 |

一致性与允许组合由 Schema 校验器强制（`schemas/evidence.py` 的 `ALLOWED_ORIGINS`）：

| `source_type` | 允许的 `measurement_origin` |
|---|---|
| `internal_excel` | `historical_external_third_party`、`derived_or_assumption` |
| `third_party` | `historical_external_third_party` |
| `official_benchmark` | `official_benchmark` |
| `internal_measurement` | `internal_measurement` |
| `derived` | `derived_or_assumption` |
| `assumption` | `derived_or_assumption`、`synthetic_fixture` |

默认值是**最保守**的 `historical_external_third_party` / `is_internal_measurement = False`：
不显式声明就绝不会被当成自有实测。

Excel 导入的证据全部满足：

- `measurement_origin = "historical_external_third_party"`
- `is_internal_measurement = False`
- `quality_flags` 含 `historical_external_source_not_internal_measurement`

并在 `validate-import` 中以检查项固定下来：

| 检查 | 判定 |
|---|---|
| `excel_marked_as_historical_external` | 32 条效能记录不得被标成内部实测 |
| `historical_external_flag_present` | 每条都必须带 `historical_external_source_not_internal_measurement` 标记 |
| `no_internal_measurement_declared` | 全库不得出现任何 `internal_measurement` 声明（本阶段无自有实测） |
| `every_record_has_source` / `every_record_has_quality_flags` | 每条记录都要有可追溯来源与至少一个质量标记 |

## 4. 每条数据的溯源要求

一条数据要被认为是"可追溯的"，必须能回答：

| 问题 | 字段 |
|---|---|
| 从哪个文件/网页来？ | `source_file` 或 `source_url` |
| 文件有没有被改过？ | `raw_sha256` |
| 在文件的哪个位置？ | `source_sheet` + `source_row`，或 `source_version` |
| 什么时候取的？ | `retrieved_at` |
| 发布方是谁？ | `publisher` |
| 有哪些已知问题？ | `quality_flags` |

Excel 导入的每条记录都带 `source_sheet=部署效能`、`source_row=N`、
`raw_sha256=<xlsx 的 sha256>`，因此 `record_id` ↔ 原始行号 ↔ 文件哈希形成闭环。
官方页面证据则把原始 HTML 存档在 `data/raw/official/` 并记录 sha256
（`tests/test_official_manifest.py` 会校验文件与记录一致，并反向确认**不存在**
没有 manifest 条目的存档文件）。

## 5. 不完整案例的处理

| 情况 | 处理 |
|---|---|
| 来源可定位，但只说明工具用法、无实测数值 | `status: not_a_result_dataset`，**不入库** |
| 来源可定位，但缺少实验配置或原始指标 | `status: pending_verification`，**不入库** |
| 有部分字段，缺口已逐项记录 | `partial_evidence`，入候选表 `official_benchmark_candidate`，**不可**作为完整对比案例，**不可**参与 SLA / 产能结论 |
| 配置与指标齐备且有原始证据 | `verified`，可入库为 `official_benchmark` |

`partial_evidence` 的落地方式（`import-official`）：

- 每条候选写一张候选表记录 + 一条 `official_benchmark` 类型的证据；
- 候选**不进** `benchmark_record`，因此不会被 `calculate-all` 或 `report` 当成可算数据；
- 缺失字段不是手工声明的，而是从实际模型状态算出来的（`absent_required_fields()`），
  清单里写的 `fields_missing` 与实际不符会直接报错；
- 每条候选带 `hardware_binding`（`bound` / `unbound`）：没有可核对的 GPU 型号与卡数时
  一律 `unbound`，并且**不允许**填 `hardware_model` / `deployment_gpu_count`；
- 已有归档证据的条目，`sha256` 必须与 manifest 记录一致才允许入库；
- 单请求冒烟测试会被识别出来：`success_count == 1` 时强制标记
  `single_request_smoke_test` 且 `is_throughput_benchmark = False`。

查询现状：`official-coverage` 输出 `candidate_count` / `verified_count` /
`with_parsed_metrics` / `throughput_benchmarks` / `single_request_smoke_tests`。
当前 manifest 中 `verified = 0`。任务书 §8 M5 明确："若无法获得 2 条完整来源，
**不得捏造**"，因此以缺口报告形式交付，而不是凑数。

## 6. 指标口径隔离规则

以下替代关系被代码显式禁止（`importers/common.py` 的 `FORBIDDEN_SUBSTITUTIONS`），
触发时会留下 `refused_substitution:*` 标记：

| 规范字段 | 禁止被以下字段填充 | 原因 |
|---|---|---|
| `tpot_mean_ms` | `mean_itl_ms` | ITL 是逐 token 间隔，TPOT 是每输出 token 平均耗时 |
| `tpot_p99_ms` | `p99_itl_ms` | 同上 |
| `ttft_p99_ms` | `mean_ttft_ms` / `median_ttft_ms` | 不同统计量，不可互推 |

其它已落地的口径隔离：

- `configured_concurrency`（配置值）与 `mean_observed_concurrency`（实测均值）分开存；
  官方文档确认 SGLang 的 Concurrency 口径为"全请求累计耗时 / 墙钟时间"，二者不同。
- `cache_hit_rate_raw` 与 `physical_cache_hit_rate` / `billable_cached_input_ratio`
  分开存；Excel 的 H 列语义未确认，一律 `cache_metric_semantics = "unknown"`。
- 旧表 I/J/K/L/M 保留在 `legacy_*`，复算结果写在 `PricingResult`，互不覆盖。
- 缺失值一律 `None`，禁止填 0；来源明确给出的 0（如零成功请求时的吞吐 0）作为
  **实测 0** 保留，并附带 `zero_successful_requests` 标记以示区分。

## 7. 结果侧的证据链

证据链不只挂在输入侧，每条 `PricingResult` 也必须能回答"这个数字是怎么来的"：

| 字段 | 作用 |
|---|---|
| `performance_record_id` | 指向 `BenchmarkRecord`（吞吐与时延的来源） |
| `hardware_price_id` | 指向 `HardwarePrice`（成本来源），缺失时为 `None` 并写 `linkage_warnings` |
| `token_price_id` | 指向 `TokenPrice`（单价来源），缺失时同上 |
| `evidence_ids` | 引用到的全部 `Evidence` |
| `scope` | `business` / `test`，标记该结果是否来自人工测试数据 |
| `result_type` | `theoretical_full_load_potential` / `scenario_sensitivity_estimate`，说明这是理论上限还是情景估算 |
| `sla_status` + `sla_not_evaluable_reasons` | SLA 是否可评估；缺 P99 时恒为 `not_evaluable` 并写明原因 |
| `currency` / `cost_scope` / `price_unit` / `billing_period` | 金额口径四件套，缺失币种时为 `UNCONFIRMED` |

`validate-results` 用 9 项检查固定这些约束（`result_linked_to_performance_record`、
`result_linked_to_hardware_price`、`result_linked_to_token_price`、
`result_has_evidence_chain`、`result_scope_matches_source_kind`、
`sla_not_evaluable_without_p99`、`sla_reasons_recorded`、`unlinked_inputs_warned`、
`currency_declared`）。缺价格关联时**不会静默**：结果上会留下 `linkage_warnings`。

## 8. 联网策略

- 代码**默认不联网**。`config/settings.example.yaml` 中 `network.allow_fetch: false`。
- 官方资料采集是**人工触发**动作，不做自动爬取，不绕过访问限制。本次检索线索借助
  codex CLI 的联网检索能力，但每个 URL 都由本机 curl 独立复核后存档（见
  `official_coverage_gaps.md`），检索结果中的数值一律不直接采信。
- 解析器测试使用本地 fixture，因此无网络环境下测试全绿；`pytest` 与
  `scripts/verify_all.sh` 全程离线可复现。

## 9. 业务假设清单（需确认项）

| 假设 | 当前值 | 影响 |
|---|---|---|
| 价格币种 | `CNY; pending business confirmation`（结果对象中为 `UNCONFIRMED`） | 所有金额口径 |
| `GPU价格!C` 覆盖范围 | 万元/卡/年，覆盖范围待确认 | 成本是否含机房/网络/运维 |
| H 列语义 | 旧表定价代理假设 | 输入侧收入计算 |
| 年度秒数 | 31536000（365 天） | 全部年化金额 |
| 成本边界 | `gpu_cost_only` | 不得称 TCO |
| 结果性质 | `theoretical_full_load_potential` | 不得称为实际营收或对外报价 |

这些假设在结果对象中都有对应字段与 `warnings`，导出 JSON 与 Markdown 报告都会带上。
