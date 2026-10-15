# 大模型部署基准数据与 Token 定价 —— 计算报告

> 本报告由离线计算引擎自动生成。**不是对外报价，不是 SLA 承诺，
> 不是实际营收**；所有金额为**理论满载潜力**（或指定变现比例下的情景估算），
> 单位万元，`cost_scope = gpu_cost_only`（仅表内 GPU 成本，不是完整 TCO）。

### 口径声明（务必先读）

| 项 | 值 |
|---|---|
| 结果类型 | 理论满载潜力 / 情景敏感性估算 —— **不是实际收入，不是客户报价，不是可收取金额** |
| 币种 | `UNCONFIRMED`（假设 `CNY; pending business confirmation`，待业务确认） |
| 成本范围 | `gpu_cost_only` —— 仅表内 GPU 摊销/租金成本，**不是**完整 TCO |
| 价格单位 / 计费周期 | 货币单位/百万 Token；万元/卡/年 |
| SLA | 不提供任何 SLA 承诺；缺 TTFT/TPOT 的 P99 实测数据时恒为 `not_evaluable` |
| 金额编码 | 导出 JSON 中金额为可精确恢复的十进制字符串 |
| 成本范围未确认项 | `datacenter_space`、`electricity`、`network`、`labor_and_operations`、`software_license`、`depreciation_or_rental`（确认状态 `unconfirmed_pending_business_review`）—— 上述各项**是否已包含在表内成本中尚未确认**，因此本报告的成本与收入比不能当作完整 TCO 结论 |
| 官方数值候选 | 本次接入 12 条（sglang_qwen3_coder_cookbook 3 条，sglang_qwen3_cookbook 9 条）；全部为 `partial_evidence`，**完整可复现的 verified=0**，不参与本报告任何经济性计算 |
| 官方页面候选 | 6 条（页面级证据，同上不参与计算） |

## 1. 数据概览

- 数据库: `data/local.db`
- 证据条目: 49
- GPU 硬件价格: 2 条
- 模型 Token 价格: 3 条
- 部署效能记录: 36 条（业务 32 + 人工测试 4）
- 满载计算结果: 32 条（业务 32 + 测试 0）
- 情景结果: 160 条（业务 160 + 测试 0）
- 本次报告是否纳入测试数据: **否**

### 1.1 测试数据隔离口径

`synthetic_fixture` 是人工构造的解析器测试样例，**默认不参与任何业务统计**。

本次被排除在业务计算之外的测试记录（4 条）：

- `fixture_sglang_renamed_rec`
- `fixture_sglang_synthetic_rec`
- `fixture_vllm_synthetic_rec`
- `fixture_vllm_zero_success_rec`

> 如需包含测试数据的完整口径（36 条 × 5 档 = 180 条情景），必须显式启用：`--include-test-data`。

证据来源构成：

| source_type | 条数 |
|---|---|
| assumption | 4 |
| internal_excel | 37 |
| official_benchmark | 8 |

效能数据来源类型（`performance_source_kind`）：

| 类型 | 条数 | 业务口径 |
|---|---|---|
| internal_excel | 32 | 纳入 |
| synthetic_fixture | 4 | 不纳入 |

导入批次：

| import_run_id | 文件 | sha256(前 12) | 时间 | 效能行数 |
|---|---|---|---|---|
| excel::ed25cf5493b469ae | Token产能计算器-0910.xlsx | ed25cf5493b4 | 2026-10-15 14:49:03 | 32 |

## 2. 可用场景数

**真实数据**（32 条）：

- 可完整计算（status=ok）: **32** / 32
- 部分可算（status=partial）: 0
- 不可计算（status=not_evaluable）: 0

> `not_evaluable` 表示缺少必需输入（价格/吞吐/成本）；引擎不返回 inf，也不静默填 0。

## 3. 数据缺口

以下字段在 32 条真实效能记录中全部或部分缺失（原始文件未提供，未做任何补齐）：

| 字段 | 缺失条数 | 缺失比例 |
|---|---|---|
| `model_revision` | 32 | 100% |
| `model_precision` | 32 | 100% |
| `framework` | 32 | 100% |
| `framework_version` | 32 | 100% |
| `driver_version` | 32 | 100% |
| `hardware_sku` | 32 | 100% |
| `node_count` | 32 | 100% |
| `gpus_per_node` | 32 | 100% |
| `tp_size` | 32 | 100% |
| `dp_size` | 32 | 100% |
| `ep_size` | 32 | 100% |
| `pp_size` | 32 | 100% |
| `prefill_gpu_count` | 32 | 100% |
| `decode_gpu_count` | 32 | 100% |
| `mean_observed_concurrency` | 32 | 100% |
| `request_rate_rps` | 32 | 100% |
| `num_requests` | 32 | 100% |
| `input_tokens_target` | 32 | 100% |
| `output_tokens_target` | 32 | 100% |
| `input_length_distribution` | 32 | 100% |
| `output_length_distribution` | 32 | 100% |
| `dataset_name` | 32 | 100% |
| `dataset_digest` | 32 | 100% |
| `ttft_mean_ms` | 32 | 100% |
| `ttft_p99_ms` | 32 | 100% |
| `tpot_mean_ms` | 32 | 100% |
| `tpot_p99_ms` | 32 | 100% |
| `itl_p99_ms` | 32 | 100% |
| `e2e_p99_ms` | 32 | 100% |
| `failure_rate` | 32 | 100% |
| `success_count` | 32 | 100% |
| `duration_s` | 32 | 100% |
| `physical_cache_hit_rate` | 32 | 100% |
| `billable_cached_input_ratio` | 32 | 100% |

质量标记统计（真实记录）：

| quality_flag | 条数 |
|---|---|
| `historical_external_source_not_internal_measurement` | 32 |
| `currency_pending_business_confirmation` | 32 |
| `cost_coverage_scope_pending_business_confirmation` | 32 |
| `underlying_source_inferencex_not_official_framework_benchmark` | 32 |
| `legacy_hit_rate_used_as_billing_proxy` | 32 |
| `cache_metric_semantics_unknown` | 32 |
| `deployment_strategy_parse_unverified` | 32 |
| `deployment_gpu_count_differs_from_server_gpu_count` | 17 |

另有 4 条 `synthetic_fixture` 解析器测试记录（人工构造，**不进入性能数据库、不参与经济性结论**）：

| record_id | 框架 | 缺失字段数 |
|---|---|---|
| `fixture_sglang_renamed_rec` | sglang | 12 |
| `fixture_sglang_synthetic_rec` | sglang | 12 |
| `fixture_vllm_synthetic_rec` | vllm | 12 |
| `fixture_vllm_zero_success_rec` | vllm | 15 |

需业务确认的关键问题：

1. Token 价格币种未写明，当前假设 `CNY; pending business confirmation`。
2. `GPU价格!C` 的“摊销或租金价格/卡/年”覆盖范围（是否含机房、网络、运维）未确认。
3. H 列“总缓存命中率”的计费语义未确认：物理 KV 命中率 ≠ 可计费缓存命中 Token 比例。
4. 全部 32 条效能数据来源为 `InferenceX`（第三方），**不是** SGLang/vLLM 官方 benchmark 数据。
5. 部署策略（TP/EP/PD）仅有字符串描述，没有启动配置或日志证据。

## 4. 最大理论满载收入案例

> 本节数字是**理论满载潜力**（theoretical_full_load_potential），**不是实际收入、不是客户报价、不是可收取金额**。

按**每卡**理论满载年收入排序，最高为 `excel_部署效能_22`：

| 项目 | 值 |
|---|---|
| 模型 | DeepSeek-V4-Pro（峰值） |
| GPU 型号 | B300 |
| 部署卡数 | 64 |
| 配置并发 | 512 |
| 输入收入(万元/卡/年) | 324.6126 |
| 输出收入(万元/卡/年) | 69.2798 |
| 合计收入(万元/卡/年) | 393.8923 |
| GPU 年成本(万元/卡/年) | 52.5000 |
| 营收/成本比 | 7.5027 |
| 盈亏平衡变现比例 | 13.33% |
| 集群总成本(万元/年) | 3,360.0000 |
| 集群满载收入(万元/年) | 25,209.1100 |

> 这是**理论满载**结果：假设全年 31536000 秒持续跑满该 Benchmark 配置的吞吐，并把全部 Token 变现。它不是实际营收，也不是客户可收取金额。

营收/成本比 > 1 的记录（在表内 GPU 成本口径下）：

| record_id | 模型 | 营收/成本 | 盈亏平衡变现比例 |
|---|---|---|---|
| excel_部署效能_22 | DeepSeek-V4-Pro（峰值） | 7.5027 | 13.33% |
| excel_部署效能_23 | DeepSeek-V4-Pro（峰值） | 5.2403 | 19.08% |
| excel_部署效能_2 | KIMI-K3 | 3.3745 | 29.63% |
| excel_部署效能_25 | DeepSeek-V4-Pro（峰值） | 3.2820 | 30.47% |
| excel_部署效能_24 | DeepSeek-V4-Pro（峰值） | 3.1450 | 31.80% |
| excel_部署效能_3 | KIMI-K3 | 2.3983 | 41.70% |
| excel_部署效能_4 | KIMI-K3 | 2.3958 | 41.74% |
| excel_部署效能_26 | DeepSeek-V4-Pro（峰值） | 2.3898 | 41.84% |
| excel_部署效能_15 | GLM-5.2/5.3 | 2.2864 | 43.74% |
| excel_部署效能_16 | GLM-5.2/5.3 | 1.4806 | 67.54% |
| excel_部署效能_5 | KIMI-K3 | 1.4340 | 69.74% |
| excel_部署效能_17 | GLM-5.2/5.3 | 1.2531 | 79.80% |
| excel_部署效能_27 | DeepSeek-V4-Pro（峰值） | 1.1375 | 87.91% |

## 5. 成本敏感性（变现比例情景）

业务口径：对 32 条真实记录生成 160 条情景（U = 10/30/50/70/100%，即 32 × 5）。

测试口径：人工 `synthetic_fixture` 记录产生 0 条情景，**已排除**。默认口径下应为 32 × 5 = 160 条；36 × 5 = 180 条仅在显式 `--include-test-data` 时出现。

> 本节所有数字都是**情景敏感性估算**（scenario_sensitivity_estimate），**不是实际收入、不是客户报价**；「结余」为负表示该变现比例下收入低于 GPU 成本，且**未包含**机房、网络、人力、存储、运维等成本，不能作为利润结论。


| record_id | U | 情景收入(万元/卡/年) | 减 GPU 成本后结余(万元/卡/年) |
|---|---|---|---|
| excel_部署效能_10 | 10.00% | 0.9755 | -14.0245 |
| excel_部署效能_10 | 30.00% | 2.9264 | -12.0736 |
| excel_部署效能_10 | 50.00% | 4.8773 | -10.1227 |
| excel_部署效能_10 | 70.00% | 6.8282 | -8.1718 |
| excel_部署效能_10 | 100.00% | 9.7546 | -5.2454 |
| excel_部署效能_11 | 10.00% | 0.4436 | -14.5564 |
| excel_部署效能_11 | 30.00% | 1.3308 | -13.6692 |
| excel_部署效能_11 | 50.00% | 2.2180 | -12.7820 |
| excel_部署效能_11 | 70.00% | 3.1052 | -11.8948 |
| excel_部署效能_11 | 100.00% | 4.4360 | -10.5640 |
| excel_部署效能_12 | 10.00% | 0.3700 | -14.6300 |
| excel_部署效能_12 | 30.00% | 1.1100 | -13.8900 |
| excel_部署效能_12 | 50.00% | 1.8500 | -13.1500 |
| excel_部署效能_12 | 70.00% | 2.5899 | -12.4101 |
| excel_部署效能_12 | 100.00% | 3.6999 | -11.3001 |
| excel_部署效能_13 | 10.00% | 0.2508 | -14.7492 |
| excel_部署效能_13 | 30.00% | 0.7524 | -14.2476 |
| excel_部署效能_13 | 50.00% | 1.2540 | -13.7460 |
| excel_部署效能_13 | 70.00% | 1.7556 | -13.2444 |
| excel_部署效能_13 | 100.00% | 2.5080 | -12.4920 |
| excel_部署效能_14 | 10.00% | 0.2258 | -14.7742 |
| excel_部署效能_14 | 30.00% | 0.6774 | -14.3226 |
| excel_部署效能_14 | 50.00% | 1.1289 | -13.8711 |
| excel_部署效能_14 | 70.00% | 1.5805 | -13.4195 |
| excel_部署效能_14 | 100.00% | 2.2579 | -12.7421 |
| excel_部署效能_15 | 10.00% | 12.0034 | -40.4966 |
| excel_部署效能_15 | 30.00% | 36.0102 | -16.4898 |
| excel_部署效能_15 | 50.00% | 60.0170 | 7.5170 |
| excel_部署效能_15 | 70.00% | 84.0238 | 31.5238 |
| excel_部署效能_15 | 100.00% | 120.0340 | 67.5340 |
| excel_部署效能_16 | 10.00% | 7.7733 | -44.7267 |
| excel_部署效能_16 | 30.00% | 23.3200 | -29.1800 |
| excel_部署效能_16 | 50.00% | 38.8667 | -13.6333 |
| excel_部署效能_16 | 70.00% | 54.4133 | 1.9133 |
| excel_部署效能_16 | 100.00% | 77.7334 | 25.2334 |
| excel_部署效能_17 | 10.00% | 6.5789 | -45.9211 |
| excel_部署效能_17 | 30.00% | 19.7366 | -32.7634 |
| excel_部署效能_17 | 50.00% | 32.8943 | -19.6057 |
| excel_部署效能_17 | 70.00% | 46.0520 | -6.4480 |
| excel_部署效能_17 | 100.00% | 65.7886 | 13.2886 |
| excel_部署效能_18 | 10.00% | 3.5913 | -48.9087 |
| excel_部署效能_18 | 30.00% | 10.7738 | -41.7262 |
| excel_部署效能_18 | 50.00% | 17.9563 | -34.5437 |
| excel_部署效能_18 | 70.00% | 25.1388 | -27.3612 |
| excel_部署效能_18 | 100.00% | 35.9125 | -16.5875 |
| excel_部署效能_19 | 10.00% | 2.0894 | -50.4106 |
| excel_部署效能_19 | 30.00% | 6.2681 | -46.2319 |
| excel_部署效能_19 | 50.00% | 10.4468 | -42.0532 |
| excel_部署效能_19 | 70.00% | 14.6256 | -37.8744 |
| excel_部署效能_19 | 100.00% | 20.8937 | -31.6063 |
| excel_部署效能_2 | 10.00% | 17.7160 | -34.7840 |
| excel_部署效能_2 | 30.00% | 53.1480 | 0.6480 |
| excel_部署效能_2 | 50.00% | 88.5801 | 36.0801 |
| excel_部署效能_2 | 70.00% | 124.0121 | 71.5121 |
| excel_部署效能_2 | 100.00% | 177.1601 | 124.6601 |
| excel_部署效能_20 | 10.00% | 1.0574 | -13.9426 |
| excel_部署效能_20 | 30.00% | 3.1723 | -11.8277 |
| excel_部署效能_20 | 50.00% | 5.2871 | -9.7129 |
| excel_部署效能_20 | 70.00% | 7.4020 | -7.5980 |
| excel_部署效能_20 | 100.00% | 10.5742 | -4.4258 |
| excel_部署效能_21 | 10.00% | 0.5869 | -14.4131 |
| excel_部署效能_21 | 30.00% | 1.7608 | -13.2392 |
| excel_部署效能_21 | 50.00% | 2.9347 | -12.0653 |
| excel_部署效能_21 | 70.00% | 4.1086 | -10.8914 |
| excel_部署效能_21 | 100.00% | 5.8695 | -9.1305 |
| excel_部署效能_22 | 10.00% | 39.3892 | -13.1108 |
| excel_部署效能_22 | 30.00% | 118.1677 | 65.6677 |
| excel_部署效能_22 | 50.00% | 196.9462 | 144.4462 |
| excel_部署效能_22 | 70.00% | 275.7246 | 223.2246 |
| excel_部署效能_22 | 100.00% | 393.8923 | 341.3923 |
| excel_部署效能_23 | 10.00% | 27.5113 | -24.9887 |
| excel_部署效能_23 | 30.00% | 82.5340 | 30.0340 |
| excel_部署效能_23 | 50.00% | 137.5567 | 85.0567 |
| excel_部署效能_23 | 70.00% | 192.5793 | 140.0793 |
| excel_部署效能_23 | 100.00% | 275.1133 | 222.6133 |
| excel_部署效能_24 | 10.00% | 16.5111 | -35.9889 |
| excel_部署效能_24 | 30.00% | 49.5334 | -2.9666 |
| excel_部署效能_24 | 50.00% | 82.5557 | 30.0557 |
| excel_部署效能_24 | 70.00% | 115.5780 | 63.0780 |
| excel_部署效能_24 | 100.00% | 165.1115 | 112.6115 |
| excel_部署效能_25 | 10.00% | 17.2305 | -35.2695 |
| excel_部署效能_25 | 30.00% | 51.6914 | -0.8086 |
| excel_部署效能_25 | 50.00% | 86.1523 | 33.6523 |
| excel_部署效能_25 | 70.00% | 120.6132 | 68.1132 |
| excel_部署效能_25 | 100.00% | 172.3045 | 119.8045 |
| excel_部署效能_26 | 10.00% | 12.5465 | -39.9535 |
| excel_部署效能_26 | 30.00% | 37.6395 | -14.8605 |
| excel_部署效能_26 | 50.00% | 62.7324 | 10.2324 |
| excel_部署效能_26 | 70.00% | 87.8254 | 35.3254 |
| excel_部署效能_26 | 100.00% | 125.4649 | 72.9649 |
| excel_部署效能_27 | 10.00% | 5.9719 | -46.5281 |
| excel_部署效能_27 | 30.00% | 17.9158 | -34.5842 |
| excel_部署效能_27 | 50.00% | 29.8597 | -22.6403 |
| excel_部署效能_27 | 70.00% | 41.8036 | -10.6964 |
| excel_部署效能_27 | 100.00% | 59.7194 | 7.2194 |
| excel_部署效能_28 | 10.00% | 2.9764 | -49.5236 |
| excel_部署效能_28 | 30.00% | 8.9293 | -43.5707 |
| excel_部署效能_28 | 50.00% | 14.8821 | -37.6179 |
| excel_部署效能_28 | 70.00% | 20.8349 | -31.6651 |
| excel_部署效能_28 | 100.00% | 29.7642 | -22.7358 |
| excel_部署效能_29 | 10.00% | 1.5245 | -50.9755 |
| excel_部署效能_29 | 30.00% | 4.5736 | -47.9264 |
| excel_部署效能_29 | 50.00% | 7.6227 | -44.8773 |
| excel_部署效能_29 | 70.00% | 10.6717 | -41.8283 |
| excel_部署效能_29 | 100.00% | 15.2453 | -37.2547 |
| excel_部署效能_3 | 10.00% | 12.5910 | -39.9090 |
| excel_部署效能_3 | 30.00% | 37.7730 | -14.7270 |
| excel_部署效能_3 | 50.00% | 62.9549 | 10.4549 |
| excel_部署效能_3 | 70.00% | 88.1369 | 35.6369 |
| excel_部署效能_3 | 100.00% | 125.9099 | 73.4099 |
| excel_部署效能_30 | 10.00% | 0.8349 | -51.6651 |
| excel_部署效能_30 | 30.00% | 2.5047 | -49.9953 |
| excel_部署效能_30 | 50.00% | 4.1745 | -48.3255 |
| excel_部署效能_30 | 70.00% | 5.8443 | -46.6557 |
| excel_部署效能_30 | 100.00% | 8.3490 | -44.1510 |
| excel_部署效能_31 | 10.00% | 1.2501 | -13.7499 |
| excel_部署效能_31 | 30.00% | 3.7503 | -11.2497 |
| excel_部署效能_31 | 50.00% | 6.2506 | -8.7494 |
| excel_部署效能_31 | 70.00% | 8.7508 | -6.2492 |
| excel_部署效能_31 | 100.00% | 12.5011 | -2.4989 |
| excel_部署效能_32 | 10.00% | 0.7964 | -14.2036 |
| excel_部署效能_32 | 30.00% | 2.3892 | -12.6108 |
| excel_部署效能_32 | 50.00% | 3.9820 | -11.0180 |
| excel_部署效能_32 | 70.00% | 5.5748 | -9.4252 |
| excel_部署效能_32 | 100.00% | 7.9640 | -7.0360 |
| excel_部署效能_33 | 10.00% | 0.4338 | -14.5662 |
| excel_部署效能_33 | 30.00% | 1.3014 | -13.6986 |
| excel_部署效能_33 | 50.00% | 2.1690 | -12.8310 |
| excel_部署效能_33 | 70.00% | 3.0366 | -11.9634 |
| excel_部署效能_33 | 100.00% | 4.3380 | -10.6620 |
| excel_部署效能_4 | 10.00% | 12.5782 | -39.9218 |
| excel_部署效能_4 | 30.00% | 37.7346 | -14.7654 |
| excel_部署效能_4 | 50.00% | 62.8910 | 10.3910 |
| excel_部署效能_4 | 70.00% | 88.0474 | 35.5474 |
| excel_部署效能_4 | 100.00% | 125.7820 | 73.2820 |
| excel_部署效能_5 | 10.00% | 7.5283 | -44.9717 |
| excel_部署效能_5 | 30.00% | 22.5849 | -29.9151 |
| excel_部署效能_5 | 50.00% | 37.6415 | -14.8585 |
| excel_部署效能_5 | 70.00% | 52.6982 | 0.1982 |
| excel_部署效能_5 | 100.00% | 75.2831 | 22.7831 |
| excel_部署效能_6 | 10.00% | 4.3798 | -48.1202 |
| excel_部署效能_6 | 30.00% | 13.1393 | -39.3607 |
| excel_部署效能_6 | 50.00% | 21.8988 | -30.6012 |
| excel_部署效能_6 | 70.00% | 30.6583 | -21.8417 |
| excel_部署效能_6 | 100.00% | 43.7976 | -8.7024 |
| excel_部署效能_7 | 10.00% | 2.9976 | -49.5024 |
| excel_部署效能_7 | 30.00% | 8.9927 | -43.5073 |
| excel_部署效能_7 | 50.00% | 14.9878 | -37.5122 |
| excel_部署效能_7 | 70.00% | 20.9829 | -31.5171 |
| excel_部署效能_7 | 100.00% | 29.9756 | -22.5244 |
| excel_部署效能_8 | 10.00% | 2.4297 | -50.0703 |
| excel_部署效能_8 | 30.00% | 7.2891 | -45.2109 |
| excel_部署效能_8 | 50.00% | 12.1485 | -40.3515 |
| excel_部署效能_8 | 70.00% | 17.0079 | -35.4921 |
| excel_部署效能_8 | 100.00% | 24.2970 | -28.2030 |
| excel_部署效能_9 | 10.00% | 0.7986 | -14.2014 |
| excel_部署效能_9 | 30.00% | 2.3957 | -12.6043 |
| excel_部署效能_9 | 50.00% | 3.9928 | -11.0072 |
| excel_部署效能_9 | 70.00% | 5.5900 | -9.4100 |
| excel_部署效能_9 | 100.00% | 7.9856 | -7.0144 |

> 结余为负表示该变现比例下收入低于 GPU 成本；该结余**未包含**机房、网络、人力、存储、运维等成本，不能作为利润结论。

## 6. 逐条来源说明

### 6.1 结果 → 输入 追溯链

每条计算结果都能回溯到：性能记录 + 硬件价格 + Token 单价 + 证据链。缺失的价格关联不会被猜测补齐，而是留空并在该结果的 `linkage_warnings` 中记录。

| record_id | 硬件价格 ID | Token 价格 ID | 证据链 | SLA | 关联告警 |
|---|---|---|---|---|---|
| `excel_部署效能_10` | excel_GPU价格_3 | excel_模型价格_2 | `ev_excel_部署效能_10`, `ev_excel_模型价格_2`, `ev_excel_GPU价格_3` | `not_evaluable` | — |
| `excel_部署效能_11` | excel_GPU价格_3 | excel_模型价格_2 | `ev_excel_部署效能_11`, `ev_excel_模型价格_2`, `ev_excel_GPU价格_3` | `not_evaluable` | — |
| `excel_部署效能_12` | excel_GPU价格_3 | excel_模型价格_2 | `ev_excel_部署效能_12`, `ev_excel_模型价格_2`, `ev_excel_GPU价格_3` | `not_evaluable` | — |
| `excel_部署效能_13` | excel_GPU价格_3 | excel_模型价格_2 | `ev_excel_部署效能_13`, `ev_excel_模型价格_2`, `ev_excel_GPU价格_3` | `not_evaluable` | — |
| `excel_部署效能_14` | excel_GPU价格_3 | excel_模型价格_2 | `ev_excel_部署效能_14`, `ev_excel_模型价格_2`, `ev_excel_GPU价格_3` | `not_evaluable` | — |
| `excel_部署效能_15` | excel_GPU价格_2 | excel_模型价格_3 | `ev_excel_部署效能_15`, `ev_excel_模型价格_3`, `ev_excel_GPU价格_2` | `not_evaluable` | — |
| `excel_部署效能_16` | excel_GPU价格_2 | excel_模型价格_3 | `ev_excel_部署效能_16`, `ev_excel_模型价格_3`, `ev_excel_GPU价格_2` | `not_evaluable` | — |
| `excel_部署效能_17` | excel_GPU价格_2 | excel_模型价格_3 | `ev_excel_部署效能_17`, `ev_excel_模型价格_3`, `ev_excel_GPU价格_2` | `not_evaluable` | — |
| `excel_部署效能_18` | excel_GPU价格_2 | excel_模型价格_3 | `ev_excel_部署效能_18`, `ev_excel_模型价格_3`, `ev_excel_GPU价格_2` | `not_evaluable` | — |
| `excel_部署效能_19` | excel_GPU价格_2 | excel_模型价格_3 | `ev_excel_部署效能_19`, `ev_excel_模型价格_3`, `ev_excel_GPU价格_2` | `not_evaluable` | — |
| `excel_部署效能_2` | excel_GPU价格_2 | excel_模型价格_2 | `ev_excel_部署效能_2`, `ev_excel_模型价格_2`, `ev_excel_GPU价格_2` | `not_evaluable` | — |
| `excel_部署效能_20` | excel_GPU价格_3 | excel_模型价格_3 | `ev_excel_部署效能_20`, `ev_excel_模型价格_3`, `ev_excel_GPU价格_3` | `not_evaluable` | — |
| `excel_部署效能_21` | excel_GPU价格_3 | excel_模型价格_3 | `ev_excel_部署效能_21`, `ev_excel_模型价格_3`, `ev_excel_GPU价格_3` | `not_evaluable` | — |
| `excel_部署效能_22` | excel_GPU价格_2 | excel_模型价格_4 | `ev_excel_部署效能_22`, `ev_excel_模型价格_4`, `ev_excel_GPU价格_2` | `not_evaluable` | — |
| `excel_部署效能_23` | excel_GPU价格_2 | excel_模型价格_4 | `ev_excel_部署效能_23`, `ev_excel_模型价格_4`, `ev_excel_GPU价格_2` | `not_evaluable` | — |
| `excel_部署效能_24` | excel_GPU价格_2 | excel_模型价格_4 | `ev_excel_部署效能_24`, `ev_excel_模型价格_4`, `ev_excel_GPU价格_2` | `not_evaluable` | — |
| `excel_部署效能_25` | excel_GPU价格_2 | excel_模型价格_4 | `ev_excel_部署效能_25`, `ev_excel_模型价格_4`, `ev_excel_GPU价格_2` | `not_evaluable` | — |
| `excel_部署效能_26` | excel_GPU价格_2 | excel_模型价格_4 | `ev_excel_部署效能_26`, `ev_excel_模型价格_4`, `ev_excel_GPU价格_2` | `not_evaluable` | — |
| `excel_部署效能_27` | excel_GPU价格_2 | excel_模型价格_4 | `ev_excel_部署效能_27`, `ev_excel_模型价格_4`, `ev_excel_GPU价格_2` | `not_evaluable` | — |
| `excel_部署效能_28` | excel_GPU价格_2 | excel_模型价格_4 | `ev_excel_部署效能_28`, `ev_excel_模型价格_4`, `ev_excel_GPU价格_2` | `not_evaluable` | — |
| `excel_部署效能_29` | excel_GPU价格_2 | excel_模型价格_4 | `ev_excel_部署效能_29`, `ev_excel_模型价格_4`, `ev_excel_GPU价格_2` | `not_evaluable` | — |
| `excel_部署效能_3` | excel_GPU价格_2 | excel_模型价格_2 | `ev_excel_部署效能_3`, `ev_excel_模型价格_2`, `ev_excel_GPU价格_2` | `not_evaluable` | — |
| `excel_部署效能_30` | excel_GPU价格_2 | excel_模型价格_4 | `ev_excel_部署效能_30`, `ev_excel_模型价格_4`, `ev_excel_GPU价格_2` | `not_evaluable` | — |
| `excel_部署效能_31` | excel_GPU价格_3 | excel_模型价格_4 | `ev_excel_部署效能_31`, `ev_excel_模型价格_4`, `ev_excel_GPU价格_3` | `not_evaluable` | — |
| `excel_部署效能_32` | excel_GPU价格_3 | excel_模型价格_4 | `ev_excel_部署效能_32`, `ev_excel_模型价格_4`, `ev_excel_GPU价格_3` | `not_evaluable` | — |
| `excel_部署效能_33` | excel_GPU价格_3 | excel_模型价格_4 | `ev_excel_部署效能_33`, `ev_excel_模型价格_4`, `ev_excel_GPU价格_3` | `not_evaluable` | — |
| `excel_部署效能_4` | excel_GPU价格_2 | excel_模型价格_2 | `ev_excel_部署效能_4`, `ev_excel_模型价格_2`, `ev_excel_GPU价格_2` | `not_evaluable` | — |
| `excel_部署效能_5` | excel_GPU价格_2 | excel_模型价格_2 | `ev_excel_部署效能_5`, `ev_excel_模型价格_2`, `ev_excel_GPU价格_2` | `not_evaluable` | — |
| `excel_部署效能_6` | excel_GPU价格_2 | excel_模型价格_2 | `ev_excel_部署效能_6`, `ev_excel_模型价格_2`, `ev_excel_GPU价格_2` | `not_evaluable` | — |
| `excel_部署效能_7` | excel_GPU价格_2 | excel_模型价格_2 | `ev_excel_部署效能_7`, `ev_excel_模型价格_2`, `ev_excel_GPU价格_2` | `not_evaluable` | — |
| `excel_部署效能_8` | excel_GPU价格_2 | excel_模型价格_2 | `ev_excel_部署效能_8`, `ev_excel_模型价格_2`, `ev_excel_GPU价格_2` | `not_evaluable` | — |
| `excel_部署效能_9` | excel_GPU价格_3 | excel_模型价格_2 | `ev_excel_部署效能_9`, `ev_excel_模型价格_2`, `ev_excel_GPU价格_3` | `not_evaluable` | — |

其中 `sla_status = not_evaluable` 共 **32** / 32 条，原因：无 TTFT/TPOT 的 P99 实测数据，且引擎不以其它指标反推 P99。

### 6.2 证据明细

共 49 条证据，按 `source_type` 分组列出。

### 6.3 官方 Benchmark 候选证据（partial_evidence）

共 6 条官方候选。它们**不进入性能数据库、不参与任何经济性计算**；原因是缺少完整比对必需的配置或指标。

| candidate_id | 发布方 | 框架 | 指标条数 | 吞吐基准? | 硬件绑定 | 缺失字段 |
|---|---|---|---|---|---|---|
| `official_lmsys_epd_elastic_encoder` | lmsys-sglang | sglang | 0 | 未知 | `unbound` | `model_name`, `model_revision`, `framework_version`, `hardware_model`, `deployment_gpu_count`, `dataset_name`, `ttft_p99_ms`, `tpot_p99_ms` |
| `official_lmsys_hetero_cpu_gpu_epd` | lmsys-sglang | sglang | 0 | 未知 | `unbound` | `model_name`, `model_revision`, `framework_version`, `hardware_model`, `deployment_gpu_count`, `dataset_name`, `ttft_p99_ms`, `tpot_p99_ms` |
| `official_lmsys_hpc_ops_sglang` | lmsys-sglang | sglang | 0 | 未知 | `unbound` | `model_name`, `model_revision`, `framework_version`, `hardware_model`, `deployment_gpu_count`, `dataset_name`, `ttft_p99_ms`, `tpot_p99_ms` |
| `official_lmsys_sglang_llama3` | lmsys-sglang | sglang | 0 | 未知 | `unbound` | `model_revision`, `framework_version`, `hardware_model`, `deployment_gpu_count`, `dataset_name`, `ttft_p99_ms`, `tpot_p99_ms` |
| `official_vllm_ascend_kimi_k2_thinking` | vllm | vllm-ascend | 0 | 未知 | `unbound` | `model_revision`, `hardware_model`, `deployment_gpu_count`, `dataset_name`, `ttft_p99_ms`, `tpot_p99_ms` |
| `official_vllm_recipes_deepseek_v3_console_output` | vllm | vllm | 16 | 否（单请求冒烟） | `unbound` | `model_revision`, `framework_version`, `hardware_model`, `deployment_gpu_count`, `dataset_name` |

可由证据文件机器解析出指标的候选：

| candidate_id | 指标 | 值 | 原始标签 |
|---|---|---|---|
| `official_vllm_recipes_deepseek_v3_console_output` | `success_count` | 1 | Successful requests |
| `official_vllm_recipes_deepseek_v3_console_output` | `duration_s` | 16.39 | Benchmark duration (s) |
| `official_vllm_recipes_deepseek_v3_console_output` | `total_input_tokens` | 7902 | Total input tokens |
| `official_vllm_recipes_deepseek_v3_console_output` | `total_output_tokens` | 1000 | Total generated tokens |
| `official_vllm_recipes_deepseek_v3_console_output` | `request_throughput_rps` | 0.06 | Request throughput (req/s) |
| `official_vllm_recipes_deepseek_v3_console_output` | `output_throughput_tok_s` | 61.00 | Output token throughput (tok/s) |
| `official_vllm_recipes_deepseek_v3_console_output` | `total_token_throughput_tok_s` | 543.06 | Total Token throughput (tok/s) |
| `official_vllm_recipes_deepseek_v3_console_output` | `ttft_mean_ms` | 560.00 | Mean TTFT (ms) |
| `official_vllm_recipes_deepseek_v3_console_output` | `ttft_median_ms` | 560.00 | Median TTFT (ms) |
| `official_vllm_recipes_deepseek_v3_console_output` | `ttft_p99_ms` | 560.00 | P99 TTFT (ms) |
| `official_vllm_recipes_deepseek_v3_console_output` | `tpot_mean_ms` | 15.85 | Mean TPOT (ms) |
| `official_vllm_recipes_deepseek_v3_console_output` | `tpot_median_ms` | 15.85 | Median TPOT (ms) |
| `official_vllm_recipes_deepseek_v3_console_output` | `tpot_p99_ms` | 15.85 | P99 TPOT (ms) |
| `official_vllm_recipes_deepseek_v3_console_output` | `itl_mean_ms` | 15.85 | Mean ITL (ms) |
| `official_vllm_recipes_deepseek_v3_console_output` | `itl_median_ms` | 15.85 | Median ITL (ms) |
| `official_vllm_recipes_deepseek_v3_console_output` | `itl_p99_ms` | 16.15 | P99 ITL (ms) |

> 上表数值由 `importers/console_summary.py` 从**存档的原始 HTML** 中解析，文件 sha256 记录在候选记录中，非人工抄录。

> **已核实的完整官方案例仍然为 0 条**：没有任何候选同时具备完整部署配置（模型 revision、框架版本、硬件型号与卡数、数据集）与全部必需指标。详见 `docs/official_coverage_gaps.md`。

### 6.4 官方数值型候选（SGLang Cookbook，M7 接入）

共 12 条，其中 **12 条**已与本地存档的官方原文逐字段机器核对一致（命令参数 + 控制台输出）。全部保持 `partial_evidence`：官方原文未给出 model revision 等配置项。

| record_id | 来源 | 场景 | 配置并发 | 观测并发 | 集群 in/out tok/s | TTFT P99 | TPOT P99 | 原文核对 | 参考可用 | SLA 可用 | 经济性可用 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `sglang_qwen3_coder_cookbook:5.1.1.1` | sglang_qwen3_coder_cookbook | standard | 1 | 1.00 | 82.68 / 57.19 | 662.15 | 17.08 | ✅ | 是 | 否 | 否 |
| `sglang_qwen3_coder_cookbook:5.1.1.2` | sglang_qwen3_coder_cookbook | standard | 16 | 13.76 | 455.77 / 468.83 | 1318.63 | 43.48 | ✅ | 是 | 否 | 否 |
| `sglang_qwen3_coder_cookbook:5.1.1.3` | sglang_qwen3_coder_cookbook | standard | 64 | 58.88 | 893.84 / 956.80 | 529.01 | 204.24 | ✅ | 是 | 否 | 否 |
| `sglang_qwen3_cookbook:5.1.1.1` | sglang_qwen3_cookbook | standard | 1 | 1.00 | 140.07 / 96.65 | 166.05 | 10.15 | ✅ | 是 | 否 | 否 |
| `sglang_qwen3_cookbook:5.1.1.2` | sglang_qwen3_cookbook | standard | 16 | 13.61 | 810.44 / 832.04 | 727.08 | 24.09 | ✅ | 是 | 否 | 否 |
| `sglang_qwen3_cookbook:5.1.1.3` | sglang_qwen3_cookbook | standard | 100 | 90.50 | 2713.46 / 2738.78 | 863.11 | 41.19 | ✅ | 是 | 否 | 否 |
| `sglang_qwen3_cookbook:5.1.2.1` | sglang_qwen3_cookbook | reasoning | 1 | 1.00 | 13.34 / 97.17 | 167.43 | 10.32 | ✅ | 是 | 否 | 否 |
| `sglang_qwen3_cookbook:5.1.2.2` | sglang_qwen3_cookbook | reasoning | 16 | 13.85 | 116.61 / 935.49 | 455.71 | 15.20 | ✅ | 是 | 否 | 否 |
| `sglang_qwen3_cookbook:5.1.2.3` | sglang_qwen3_cookbook | reasoning | 64 | 56.35 | 291.72 / 2387.34 | 823.85 | 24.97 | ✅ | 是 | 否 | 否 |
| `sglang_qwen3_cookbook:5.1.3.1` | sglang_qwen3_cookbook | summarization | 1 | 1.00 | 935.86 / 93.94 | 225.17 | 10.51 | ✅ | 是 | 否 | 否 |
| `sglang_qwen3_cookbook:5.1.3.2` | sglang_qwen3_cookbook | summarization | 16 | 13.90 | 5920.41 / 820.69 | 1027.21 | 27.18 | ✅ | 是 | 否 | 否 |
| `sglang_qwen3_cookbook:5.1.3.3` | sglang_qwen3_cookbook | summarization | 64 | 58.63 | 13441.86 / 1790.43 | 510.48 | 40.59 | ✅ | 是 | 否 | 否 |

> **集群吞吐是整套 8 卡一起跑出来的**，不是单卡能力；`配置并发` 与 `观测并发`是两个不同口径（前者来自测试命令 `--max-concurrency`，后者来自控制台的 `Concurrency` 行）。指标口径见 `docs/benchmark_metric_dictionary.md`。

> 这些候选**不参与**本报告第 4、5 节的经济性计算：'经济性可用' 一列全为否，因为币种与成本范围尚未业务确认，且候选缺 model revision。

### assumption（4 条）

| evidence_id | 发布方 | 位置 | 版本 | 抓取时间 | 原始 sha256 | 质量标记 |
|---|---|---|---|---|---|---|
| `fixture_sglang_renamed` | synthetic_fixture | sglang_renamed_fields.jsonl | synthetic/v1-renamed | 2026-10-15 14:49:04.977627 | d47637aeb663 | sglang_bench_serving_adapter, synthetic_fixture_not_official_performance_data, missing_source_url |
| `fixture_sglang_synthetic` | synthetic_fixture | sglang_synthetic.jsonl | synthetic/v1 | 2026-10-15 14:49:04.439067 | 68de8899ad28 | aggregated_from_per_request_samples, tpot_partially_computable, itl_present_kept_separate_from_tpot, sglang_bench_serving_adapter, synthetic_fixture_not_official_performance_data, missing_source_url |
| `fixture_vllm_synthetic` | synthetic_fixture | vllm_synthetic.json | synthetic/v1 | 2026-10-15 14:49:05.571838 | 1b1b2b10cb41 | vllm_bench_serve_adapter, synthetic_fixture_not_official_performance_data, missing_source_url |
| `fixture_vllm_zero_success` | synthetic_fixture | vllm_zero_success.json | synthetic/v1-zero-success | 2026-10-15 14:49:06.113820 | fb19fed26465 | vllm_bench_serve_adapter, synthetic_fixture_not_official_performance_data, missing_source_url |

备注（原始值留档）：

- `fixture_sglang_renamed`: 人工构造：模拟上游字段改名，验证版本化字段映射。不是真实测量结果。
- `fixture_sglang_synthetic`: 人工构造的合法小型 SGLang bench_serving 逐请求样例，用于验证适配器对 缺失字段（无 latency 或无 output_len）与失败请求的处理。不是真实测量结果。
- `fixture_vllm_synthetic`: 人工构造的合法小型 vLLM bench serve 结果样例。故意**只提供 mean_itl_ms 与 p99_itl_ms，不提供 mean_tpot_ms / p99_tpot_ms**，用于验证适配器拒绝用 ITL 顶替 TPOT。不是真实测量结果。
- `fixture_vllm_zero_success`: 人工构造：全部请求失败。不是真实测量结果。

### internal_excel（37 条）

| evidence_id | 发布方 | 位置 | 版本 | 抓取时间 | 原始 sha256 | 质量标记 |
|---|---|---|---|---|---|---|
| `ev_excel_GPU价格_2` | internal | Token产能计算器-0910.xlsx!GPU价格!row 2 | — | 2026-10-15 14:49:03.754882 | ed25cf5493b4 | historical_external_source_not_internal_measurement, currency_pending_business_confirmation, cost_coverage_scope_pending_business_confirmation |
| `ev_excel_GPU价格_3` | internal | Token产能计算器-0910.xlsx!GPU价格!row 3 | — | 2026-10-15 14:49:03.754882 | ed25cf5493b4 | historical_external_source_not_internal_measurement, currency_pending_business_confirmation, cost_coverage_scope_pending_business_confirmation |
| `ev_excel_模型价格_2` | internal | Token产能计算器-0910.xlsx!模型价格!row 2 | — | 2026-10-15 14:49:03.754882 | ed25cf5493b4 | historical_external_source_not_internal_measurement, currency_pending_business_confirmation, cost_coverage_scope_pending_business_confirmation |
| `ev_excel_模型价格_3` | internal | Token产能计算器-0910.xlsx!模型价格!row 3 | — | 2026-10-15 14:49:03.754882 | ed25cf5493b4 | historical_external_source_not_internal_measurement, currency_pending_business_confirmation, cost_coverage_scope_pending_business_confirmation |
| `ev_excel_模型价格_4` | internal | Token产能计算器-0910.xlsx!模型价格!row 4 | — | 2026-10-15 14:49:03.754882 | ed25cf5493b4 | historical_external_source_not_internal_measurement, currency_pending_business_confirmation, cost_coverage_scope_pending_business_confirmation |
| `ev_excel_部署效能_10` | InferenceX | Token产能计算器-0910.xlsx!部署效能!row 10 | — | 2026-10-15 14:49:03.754882 | ed25cf5493b4 | historical_external_source_not_internal_measurement, currency_pending_business_confirmation, cost_coverage_scope_pending_business_confirmation, underlying_source_inferencex_not_official_framework_benchmark, legacy_hit_rate_used_as_billing_proxy, cache_metric_semantics_unknown, deployment_strategy_parse_unverified, deployment_gpu_count_differs_from_server_gpu_count |
| `ev_excel_部署效能_11` | InferenceX | Token产能计算器-0910.xlsx!部署效能!row 11 | — | 2026-10-15 14:49:03.754882 | ed25cf5493b4 | historical_external_source_not_internal_measurement, currency_pending_business_confirmation, cost_coverage_scope_pending_business_confirmation, underlying_source_inferencex_not_official_framework_benchmark, legacy_hit_rate_used_as_billing_proxy, cache_metric_semantics_unknown, deployment_strategy_parse_unverified, deployment_gpu_count_differs_from_server_gpu_count |
| `ev_excel_部署效能_12` | InferenceX | Token产能计算器-0910.xlsx!部署效能!row 12 | — | 2026-10-15 14:49:03.754882 | ed25cf5493b4 | historical_external_source_not_internal_measurement, currency_pending_business_confirmation, cost_coverage_scope_pending_business_confirmation, underlying_source_inferencex_not_official_framework_benchmark, legacy_hit_rate_used_as_billing_proxy, cache_metric_semantics_unknown, deployment_strategy_parse_unverified, deployment_gpu_count_differs_from_server_gpu_count |
| `ev_excel_部署效能_13` | InferenceX | Token产能计算器-0910.xlsx!部署效能!row 13 | — | 2026-10-15 14:49:03.754882 | ed25cf5493b4 | historical_external_source_not_internal_measurement, currency_pending_business_confirmation, cost_coverage_scope_pending_business_confirmation, underlying_source_inferencex_not_official_framework_benchmark, legacy_hit_rate_used_as_billing_proxy, cache_metric_semantics_unknown, deployment_strategy_parse_unverified, deployment_gpu_count_differs_from_server_gpu_count |
| `ev_excel_部署效能_14` | InferenceX | Token产能计算器-0910.xlsx!部署效能!row 14 | — | 2026-10-15 14:49:03.754882 | ed25cf5493b4 | historical_external_source_not_internal_measurement, currency_pending_business_confirmation, cost_coverage_scope_pending_business_confirmation, underlying_source_inferencex_not_official_framework_benchmark, legacy_hit_rate_used_as_billing_proxy, cache_metric_semantics_unknown, deployment_strategy_parse_unverified, deployment_gpu_count_differs_from_server_gpu_count |
| `ev_excel_部署效能_15` | InferenceX | Token产能计算器-0910.xlsx!部署效能!row 15 | — | 2026-10-15 14:49:03.754882 | ed25cf5493b4 | historical_external_source_not_internal_measurement, currency_pending_business_confirmation, cost_coverage_scope_pending_business_confirmation, underlying_source_inferencex_not_official_framework_benchmark, legacy_hit_rate_used_as_billing_proxy, cache_metric_semantics_unknown, deployment_strategy_parse_unverified |
| `ev_excel_部署效能_16` | InferenceX | Token产能计算器-0910.xlsx!部署效能!row 16 | — | 2026-10-15 14:49:03.754882 | ed25cf5493b4 | historical_external_source_not_internal_measurement, currency_pending_business_confirmation, cost_coverage_scope_pending_business_confirmation, underlying_source_inferencex_not_official_framework_benchmark, legacy_hit_rate_used_as_billing_proxy, cache_metric_semantics_unknown, deployment_strategy_parse_unverified |
| `ev_excel_部署效能_17` | InferenceX | Token产能计算器-0910.xlsx!部署效能!row 17 | — | 2026-10-15 14:49:03.754882 | ed25cf5493b4 | historical_external_source_not_internal_measurement, currency_pending_business_confirmation, cost_coverage_scope_pending_business_confirmation, underlying_source_inferencex_not_official_framework_benchmark, legacy_hit_rate_used_as_billing_proxy, cache_metric_semantics_unknown, deployment_strategy_parse_unverified |
| `ev_excel_部署效能_18` | InferenceX | Token产能计算器-0910.xlsx!部署效能!row 18 | — | 2026-10-15 14:49:03.754882 | ed25cf5493b4 | historical_external_source_not_internal_measurement, currency_pending_business_confirmation, cost_coverage_scope_pending_business_confirmation, underlying_source_inferencex_not_official_framework_benchmark, legacy_hit_rate_used_as_billing_proxy, cache_metric_semantics_unknown, deployment_strategy_parse_unverified |
| `ev_excel_部署效能_19` | InferenceX | Token产能计算器-0910.xlsx!部署效能!row 19 | — | 2026-10-15 14:49:03.754882 | ed25cf5493b4 | historical_external_source_not_internal_measurement, currency_pending_business_confirmation, cost_coverage_scope_pending_business_confirmation, underlying_source_inferencex_not_official_framework_benchmark, legacy_hit_rate_used_as_billing_proxy, cache_metric_semantics_unknown, deployment_strategy_parse_unverified |
| `ev_excel_部署效能_2` | InferenceX | Token产能计算器-0910.xlsx!部署效能!row 2 | — | 2026-10-15 14:49:03.754882 | ed25cf5493b4 | historical_external_source_not_internal_measurement, currency_pending_business_confirmation, cost_coverage_scope_pending_business_confirmation, underlying_source_inferencex_not_official_framework_benchmark, legacy_hit_rate_used_as_billing_proxy, cache_metric_semantics_unknown, deployment_strategy_parse_unverified |
| `ev_excel_部署效能_20` | InferenceX | Token产能计算器-0910.xlsx!部署效能!row 20 | — | 2026-10-15 14:49:03.754882 | ed25cf5493b4 | historical_external_source_not_internal_measurement, currency_pending_business_confirmation, cost_coverage_scope_pending_business_confirmation, underlying_source_inferencex_not_official_framework_benchmark, legacy_hit_rate_used_as_billing_proxy, cache_metric_semantics_unknown, deployment_strategy_parse_unverified, deployment_gpu_count_differs_from_server_gpu_count |
| `ev_excel_部署效能_21` | InferenceX | Token产能计算器-0910.xlsx!部署效能!row 21 | — | 2026-10-15 14:49:03.754882 | ed25cf5493b4 | historical_external_source_not_internal_measurement, currency_pending_business_confirmation, cost_coverage_scope_pending_business_confirmation, underlying_source_inferencex_not_official_framework_benchmark, legacy_hit_rate_used_as_billing_proxy, cache_metric_semantics_unknown, deployment_strategy_parse_unverified, deployment_gpu_count_differs_from_server_gpu_count |
| `ev_excel_部署效能_22` | InferenceX | Token产能计算器-0910.xlsx!部署效能!row 22 | — | 2026-10-15 14:49:03.754882 | ed25cf5493b4 | historical_external_source_not_internal_measurement, currency_pending_business_confirmation, cost_coverage_scope_pending_business_confirmation, underlying_source_inferencex_not_official_framework_benchmark, legacy_hit_rate_used_as_billing_proxy, cache_metric_semantics_unknown, deployment_strategy_parse_unverified, deployment_gpu_count_differs_from_server_gpu_count |
| `ev_excel_部署效能_23` | InferenceX | Token产能计算器-0910.xlsx!部署效能!row 23 | — | 2026-10-15 14:49:03.754882 | ed25cf5493b4 | historical_external_source_not_internal_measurement, currency_pending_business_confirmation, cost_coverage_scope_pending_business_confirmation, underlying_source_inferencex_not_official_framework_benchmark, legacy_hit_rate_used_as_billing_proxy, cache_metric_semantics_unknown, deployment_strategy_parse_unverified, deployment_gpu_count_differs_from_server_gpu_count |
| `ev_excel_部署效能_24` | InferenceX | Token产能计算器-0910.xlsx!部署效能!row 24 | — | 2026-10-15 14:49:03.754882 | ed25cf5493b4 | historical_external_source_not_internal_measurement, currency_pending_business_confirmation, cost_coverage_scope_pending_business_confirmation, underlying_source_inferencex_not_official_framework_benchmark, legacy_hit_rate_used_as_billing_proxy, cache_metric_semantics_unknown, deployment_strategy_parse_unverified, deployment_gpu_count_differs_from_server_gpu_count |
| `ev_excel_部署效能_25` | InferenceX | Token产能计算器-0910.xlsx!部署效能!row 25 | — | 2026-10-15 14:49:03.754882 | ed25cf5493b4 | historical_external_source_not_internal_measurement, currency_pending_business_confirmation, cost_coverage_scope_pending_business_confirmation, underlying_source_inferencex_not_official_framework_benchmark, legacy_hit_rate_used_as_billing_proxy, cache_metric_semantics_unknown, deployment_strategy_parse_unverified, deployment_gpu_count_differs_from_server_gpu_count |
| `ev_excel_部署效能_26` | InferenceX | Token产能计算器-0910.xlsx!部署效能!row 26 | — | 2026-10-15 14:49:03.754882 | ed25cf5493b4 | historical_external_source_not_internal_measurement, currency_pending_business_confirmation, cost_coverage_scope_pending_business_confirmation, underlying_source_inferencex_not_official_framework_benchmark, legacy_hit_rate_used_as_billing_proxy, cache_metric_semantics_unknown, deployment_strategy_parse_unverified, deployment_gpu_count_differs_from_server_gpu_count |
| `ev_excel_部署效能_27` | InferenceX | Token产能计算器-0910.xlsx!部署效能!row 27 | — | 2026-10-15 14:49:03.754882 | ed25cf5493b4 | historical_external_source_not_internal_measurement, currency_pending_business_confirmation, cost_coverage_scope_pending_business_confirmation, underlying_source_inferencex_not_official_framework_benchmark, legacy_hit_rate_used_as_billing_proxy, cache_metric_semantics_unknown, deployment_strategy_parse_unverified, deployment_gpu_count_differs_from_server_gpu_count |
| `ev_excel_部署效能_28` | InferenceX | Token产能计算器-0910.xlsx!部署效能!row 28 | — | 2026-10-15 14:49:03.754882 | ed25cf5493b4 | historical_external_source_not_internal_measurement, currency_pending_business_confirmation, cost_coverage_scope_pending_business_confirmation, underlying_source_inferencex_not_official_framework_benchmark, legacy_hit_rate_used_as_billing_proxy, cache_metric_semantics_unknown, deployment_strategy_parse_unverified, deployment_gpu_count_differs_from_server_gpu_count |
| `ev_excel_部署效能_29` | InferenceX | Token产能计算器-0910.xlsx!部署效能!row 29 | — | 2026-10-15 14:49:03.754882 | ed25cf5493b4 | historical_external_source_not_internal_measurement, currency_pending_business_confirmation, cost_coverage_scope_pending_business_confirmation, underlying_source_inferencex_not_official_framework_benchmark, legacy_hit_rate_used_as_billing_proxy, cache_metric_semantics_unknown, deployment_strategy_parse_unverified, deployment_gpu_count_differs_from_server_gpu_count |
| `ev_excel_部署效能_3` | InferenceX | Token产能计算器-0910.xlsx!部署效能!row 3 | — | 2026-10-15 14:49:03.754882 | ed25cf5493b4 | historical_external_source_not_internal_measurement, currency_pending_business_confirmation, cost_coverage_scope_pending_business_confirmation, underlying_source_inferencex_not_official_framework_benchmark, legacy_hit_rate_used_as_billing_proxy, cache_metric_semantics_unknown, deployment_strategy_parse_unverified |
| `ev_excel_部署效能_30` | InferenceX | Token产能计算器-0910.xlsx!部署效能!row 30 | — | 2026-10-15 14:49:03.754882 | ed25cf5493b4 | historical_external_source_not_internal_measurement, currency_pending_business_confirmation, cost_coverage_scope_pending_business_confirmation, underlying_source_inferencex_not_official_framework_benchmark, legacy_hit_rate_used_as_billing_proxy, cache_metric_semantics_unknown, deployment_strategy_parse_unverified, deployment_gpu_count_differs_from_server_gpu_count |
| `ev_excel_部署效能_31` | InferenceX | Token产能计算器-0910.xlsx!部署效能!row 31 | — | 2026-10-15 14:49:03.754882 | ed25cf5493b4 | historical_external_source_not_internal_measurement, currency_pending_business_confirmation, cost_coverage_scope_pending_business_confirmation, underlying_source_inferencex_not_official_framework_benchmark, legacy_hit_rate_used_as_billing_proxy, cache_metric_semantics_unknown, deployment_strategy_parse_unverified |
| `ev_excel_部署效能_32` | InferenceX | Token产能计算器-0910.xlsx!部署效能!row 32 | — | 2026-10-15 14:49:03.754882 | ed25cf5493b4 | historical_external_source_not_internal_measurement, currency_pending_business_confirmation, cost_coverage_scope_pending_business_confirmation, underlying_source_inferencex_not_official_framework_benchmark, legacy_hit_rate_used_as_billing_proxy, cache_metric_semantics_unknown, deployment_strategy_parse_unverified |
| `ev_excel_部署效能_33` | InferenceX | Token产能计算器-0910.xlsx!部署效能!row 33 | — | 2026-10-15 14:49:03.754882 | ed25cf5493b4 | historical_external_source_not_internal_measurement, currency_pending_business_confirmation, cost_coverage_scope_pending_business_confirmation, underlying_source_inferencex_not_official_framework_benchmark, legacy_hit_rate_used_as_billing_proxy, cache_metric_semantics_unknown, deployment_strategy_parse_unverified |
| `ev_excel_部署效能_4` | InferenceX | Token产能计算器-0910.xlsx!部署效能!row 4 | — | 2026-10-15 14:49:03.754882 | ed25cf5493b4 | historical_external_source_not_internal_measurement, currency_pending_business_confirmation, cost_coverage_scope_pending_business_confirmation, underlying_source_inferencex_not_official_framework_benchmark, legacy_hit_rate_used_as_billing_proxy, cache_metric_semantics_unknown, deployment_strategy_parse_unverified |
| `ev_excel_部署效能_5` | InferenceX | Token产能计算器-0910.xlsx!部署效能!row 5 | — | 2026-10-15 14:49:03.754882 | ed25cf5493b4 | historical_external_source_not_internal_measurement, currency_pending_business_confirmation, cost_coverage_scope_pending_business_confirmation, underlying_source_inferencex_not_official_framework_benchmark, legacy_hit_rate_used_as_billing_proxy, cache_metric_semantics_unknown, deployment_strategy_parse_unverified |
| `ev_excel_部署效能_6` | InferenceX | Token产能计算器-0910.xlsx!部署效能!row 6 | — | 2026-10-15 14:49:03.754882 | ed25cf5493b4 | historical_external_source_not_internal_measurement, currency_pending_business_confirmation, cost_coverage_scope_pending_business_confirmation, underlying_source_inferencex_not_official_framework_benchmark, legacy_hit_rate_used_as_billing_proxy, cache_metric_semantics_unknown, deployment_strategy_parse_unverified |
| `ev_excel_部署效能_7` | InferenceX | Token产能计算器-0910.xlsx!部署效能!row 7 | — | 2026-10-15 14:49:03.754882 | ed25cf5493b4 | historical_external_source_not_internal_measurement, currency_pending_business_confirmation, cost_coverage_scope_pending_business_confirmation, underlying_source_inferencex_not_official_framework_benchmark, legacy_hit_rate_used_as_billing_proxy, cache_metric_semantics_unknown, deployment_strategy_parse_unverified |
| `ev_excel_部署效能_8` | InferenceX | Token产能计算器-0910.xlsx!部署效能!row 8 | — | 2026-10-15 14:49:03.754882 | ed25cf5493b4 | historical_external_source_not_internal_measurement, currency_pending_business_confirmation, cost_coverage_scope_pending_business_confirmation, underlying_source_inferencex_not_official_framework_benchmark, legacy_hit_rate_used_as_billing_proxy, cache_metric_semantics_unknown, deployment_strategy_parse_unverified |
| `ev_excel_部署效能_9` | InferenceX | Token产能计算器-0910.xlsx!部署效能!row 9 | — | 2026-10-15 14:49:03.754882 | ed25cf5493b4 | historical_external_source_not_internal_measurement, currency_pending_business_confirmation, cost_coverage_scope_pending_business_confirmation, underlying_source_inferencex_not_official_framework_benchmark, legacy_hit_rate_used_as_billing_proxy, cache_metric_semantics_unknown, deployment_strategy_parse_unverified, deployment_gpu_count_differs_from_server_gpu_count |

备注（原始值留档）：

- `ev_excel_GPU价格_2`: 原始值: A='B300' B=8 C=Decimal('52.5')；单位按表头语境理解为万元/卡/年
- `ev_excel_GPU价格_3`: 原始值: A='H200' B=8 C=Decimal('15')；单位按表头语境理解为万元/卡/年
- `ev_excel_模型价格_2`: 原始值: 模型='KIMI-K3' 命中=Decimal('2') 未命中=Decimal('20') 输出=Decimal('100')；单位为 货币单位/百万Token，币种待确认
- `ev_excel_模型价格_3`: 原始值: 模型='GLM-5.2/5.3' 命中=Decimal('2') 未命中=Decimal('8') 输出=Decimal('28')；单位为 货币单位/百万Token，币种待确认
- `ev_excel_模型价格_4`: 原始值: 模型='DeepSeek-V4-Pro（峰值）' 命中=Decimal('0.3') 未命中=Decimal('9') 输出=Decimal('27')；单位为 货币单位/百万Token，币种待确认
- `ev_excel_部署效能_10`: 原始行: GPU='H200' 模型='KIMI-K3' 部署卡数=32 并发=12 单用户输出P90=1.684 每卡输入=220.593 每卡输出=0.75 命中率=Decimal('0.351') 策略='TP8EP32 w/ DP Attn w/ Dspark' 数据来源='InferenceX'。该行 I/J/K/L/M 为旧表计算结果，仅作历史留档，不作为对外营收或利润承诺
- `ev_excel_部署效能_11`: 原始行: GPU='H200' 模型='KIMI-K3' 部署卡数=32 并发=10 单用户输出P90=2.845 每卡输入=163.032 每卡输出=0.887 命中率=Decimal('0.662') 策略='TP8EP32 w/ DP Attn w/ Dspark' 数据来源='InferenceX'。该行 I/J/K/L/M 为旧表计算结果，仅作历史留档，不作为对外营收或利润承诺
- `ev_excel_部署效能_12`: 原始行: GPU='H200' 模型='KIMI-K3' 部署卡数=32 并发=8 单用户输出P90=2.858 每卡输入=137.318 每卡输出=0.681 命中率=Decimal('0.664') 策略='TP8EP32 w/ DP Attn w/ Dspark' 数据来源='InferenceX'。该行 I/J/K/L/M 为旧表计算结果，仅作历史留档，不作为对外营收或利润承诺
- `ev_excel_部署效能_13`: 原始行: GPU='H200' 模型='KIMI-K3' 部署卡数=32 并发=4 单用户输出P90=4.487 每卡输入=87.122 每卡输出=0.502 命中率=Decimal('0.636') 策略='TP8EP32 w/ DP Attn w/ Dspark' 数据来源='InferenceX'。该行 I/J/K/L/M 为旧表计算结果，仅作历史留档，不作为对外营收或利润承诺
- `ev_excel_部署效能_14`: 原始行: GPU='H200' 模型='KIMI-K3' 部署卡数=32 并发=1 单用户输出P90=5.453 每卡输入=69.716 每卡输出=0.156 命中率=Decimal('0.553') 策略='TP8EP32 w/ DP Attn w/ Dspark' 数据来源='InferenceX'。该行 I/J/K/L/M 为旧表计算结果，仅作历史留档，不作为对外营收或利润承诺
- `ev_excel_部署效能_15`: 原始行: GPU='B300' 模型='GLM-5.2/5.3' 部署卡数=8 并发=16 单用户输出P90=125.313 每卡输入=13063.587 每卡输出=84.743 命中率=Decimal('0.878') 策略='TP8EP1 w/ MTP' 数据来源='InferenceX'。该行 I/J/K/L/M 为旧表计算结果，仅作历史留档，不作为对外营收或利润承诺
- `ev_excel_部署效能_16`: 原始行: GPU='B300' 模型='GLM-5.2/5.3' 部署卡数=8 并发=12 单用户输出P90=161.031 每卡输入=8024.704 每卡输出=62.951 命中率=Decimal('0.858') 策略='TP8EP1 w/ MTP' 数据来源='InferenceX'。该行 I/J/K/L/M 为旧表计算结果，仅作历史留档，不作为对外营收或利润承诺
- `ev_excel_部署效能_17`: 原始行: GPU='B300' 模型='GLM-5.2/5.3' 部署卡数=8 并发=8 单用户输出P90=174.52 每卡输入=7015.766 每卡输出=51.492 命中率=Decimal('0.872') 策略='TP8EP1 w/ MTP' 数据来源='InferenceX'。该行 I/J/K/L/M 为旧表计算结果，仅作历史留档，不作为对外营收或利润承诺
- `ev_excel_部署效能_18`: 原始行: GPU='B300' 模型='GLM-5.2/5.3' 部署卡数=8 并发=4 单用户输出P90=259.067 每卡输入=4066.755 每卡输出=28.208 命中率=Decimal('0.899') 策略='TP8EP1 w/ MTP' 数据来源='InferenceX'。该行 I/J/K/L/M 为旧表计算结果，仅作历史留档，不作为对外营收或利润承诺
- `ev_excel_部署效能_19`: 原始行: GPU='B300' 模型='GLM-5.2/5.3' 部署卡数=8 并发=1 单用户输出P90=307.692 每卡输入=2624.944 每卡输出=18.749 命中率=Decimal('0.946') 策略='TP8EP1 w/ MTP' 数据来源='InferenceX'。该行 I/J/K/L/M 为旧表计算结果，仅作历史留档，不作为对外营收或利润承诺
- `ev_excel_部署效能_2`: 原始行: GPU='B300' 模型='KIMI-K3' 部署卡数=8 并发=70 单用户输出P90=7.888 每卡输入=12478.922 每卡输出=87.572 命中率=Decimal('0.9') 策略='TP8DCP8EP1 w/ DP Attn w/ Dspark' 数据来源='InferenceX'。该行 I/J/K/L/M 为旧表计算结果，仅作历史留档，不作为对外营收或利润承诺
- `ev_excel_部署效能_20`: 原始行: GPU='H200' 模型='GLM-5.2/5.3' 部署卡数=32 并发=12 单用户输出P90=84.459 每卡输入=835.96 每卡输出=7.913 命中率=Decimal('0.709') 策略='P16D16分离' 数据来源='InferenceX'。该行 I/J/K/L/M 为旧表计算结果，仅作历史留档，不作为对外营收或利润承诺
- `ev_excel_部署效能_21`: 原始行: GPU='H200' 模型='GLM-5.2/5.3' 部署卡数=32 并发=8 单用户输出P90=84.746 每卡输入=662.098 每卡输出=6.268 命中率=Decimal('0.909') 策略='P16D16分离' 数据来源='InferenceX'。该行 I/J/K/L/M 为旧表计算结果，仅作历史留档，不作为对外营收或利润承诺
- `ev_excel_部署效能_22`: 原始行: GPU='B300' 模型='DeepSeek-V4-Pro（峰值）' 部署卡数=64 并发=512 单用户输出P90=11.523 每卡输入=108067.164 每卡输出=813.647 命中率=Decimal('0.925') 策略='TP8EP8 w/ MTP' 数据来源='InferenceX'。该行 I/J/K/L/M 为旧表计算结果，仅作历史留档，不作为对外营收或利润承诺
- `ev_excel_部署效能_23`: 原始行: GPU='B300' 模型='DeepSeek-V4-Pro（峰值）' 部署卡数=64 并发=256 单用户输出P90=25.233 每卡输入=81074.101 每卡输出=606.033 命中率=Decimal('0.934') 策略='TP8EP8 w/ MTP' 数据来源='InferenceX'。该行 I/J/K/L/M 为旧表计算结果，仅作历史留档，不作为对外营收或利润承诺
- `ev_excel_部署效能_24`: 原始行: GPU='B300' 模型='DeepSeek-V4-Pro（峰值）' 部署卡数=64 并发=128 单用户输出P90=33.693 每卡输入=46980.576 每卡输出=402.865 命中率=Decimal('0.933') 策略='TP8EP8 w/ MTP' 数据来源='InferenceX'。该行 I/J/K/L/M 为旧表计算结果，仅作历史留档，不作为对外营收或利润承诺
- `ev_excel_部署效能_25`: 原始行: GPU='B300' 模型='DeepSeek-V4-Pro（峰值）' 部署卡数=16 并发=64 单用户输出P90=31.211 每卡输入=48218.536 每卡输出=384.713 命中率=Decimal('0.929') 策略='TP4EP4 w/ MTP' 数据来源='InferenceX'。该行 I/J/K/L/M 为旧表计算结果，仅作历史留档，不作为对外营收或利润承诺
- `ev_excel_部署效能_26`: 原始行: GPU='B300' 模型='DeepSeek-V4-Pro（峰值）' 部署卡数=16 并发=48 单用户输出P90=41.789 每卡输入=42945.446 每卡输出=290.597 命中率=Decimal('0.949') 策略='TP4EP4 w/ MTP' 数据来源='InferenceX'。该行 I/J/K/L/M 为旧表计算结果，仅作历史留档，不作为对外营收或利润承诺
- `ev_excel_部署效能_27`: 原始行: GPU='B300' 模型='DeepSeek-V4-Pro（峰值）' 部署卡数=4 并发=16 单用户输出P90=59.277 每卡输入=18569.965 每卡输出=136.014 命中率=Decimal('0.94') 策略='TP4EP1 w/ MTP' 数据来源='InferenceX'。该行 I/J/K/L/M 为旧表计算结果，仅作历史留档，不作为对外营收或利润承诺
- `ev_excel_部署效能_28`: 原始行: GPU='B300' 模型='DeepSeek-V4-Pro（峰值）' 部署卡数=4 并发=8 单用户输出P90=97.752 每卡输入=10880.696 每卡输出=84.919 命中率=Decimal('0.959') 策略='TP4EP1 w/ MTP' 数据来源='InferenceX'。该行 I/J/K/L/M 为旧表计算结果，仅作历史留档，不作为对外营收或利润承诺
- `ev_excel_部署效能_29`: 原始行: GPU='B300' 模型='DeepSeek-V4-Pro（峰值）' 部署卡数=4 并发=4 单用户输出P90=138.313 每卡输入=5919.994 每卡输出=44.597 命中率=Decimal('0.964') 策略='TP4EP1 w/ MTP' 数据来源='InferenceX'。该行 I/J/K/L/M 为旧表计算结果，仅作历史留档，不作为对外营收或利润承诺
- `ev_excel_部署效能_3`: 原始行: GPU='B300' 模型='KIMI-K3' 部署卡数=8 并发=32 单用户输出P90=17.615 每卡输入=9972.287 每卡输出=70.571 命中率=Decimal('0.928') 策略='TP8DCP8EP1 w/ DP Attn w/ Dspark' 数据来源='InferenceX'。该行 I/J/K/L/M 为旧表计算结果，仅作历史留档，不作为对外营收或利润承诺
- `ev_excel_部署效能_30`: 原始行: GPU='B300' 模型='DeepSeek-V4-Pro（峰值）' 部署卡数=4 并发=1 单用户输出P90=166.945 每卡输入=3425.741 每卡输出=27.978 命中率=Decimal('0.971') 策略='TP4EP1 w/ MTP' 数据来源='InferenceX'。该行 I/J/K/L/M 为旧表计算结果，仅作历史留档，不作为对外营收或利润承诺
- `ev_excel_部署效能_31`: 原始行: GPU='H200' 模型='DeepSeek-V4-Pro（峰值）' 部署卡数=8 并发=8 单用户输出P90=57.405 每卡输入=4325.492 每卡输出=36.037 命中率=Decimal('0.955') 策略='TP8EP1 w/ MTP' 数据来源='InferenceX'。该行 I/J/K/L/M 为旧表计算结果，仅作历史留档，不作为对外营收或利润承诺
- `ev_excel_部署效能_32`: 原始行: GPU='H200' 模型='DeepSeek-V4-Pro（峰值）' 部署卡数=8 并发=4 单用户输出P90=104.384 每卡输入=2816.119 每卡输出=20.501 命中率=Decimal('0.954') 策略='TP8EP1 w/ MTP' 数据来源='InferenceX'。该行 I/J/K/L/M 为旧表计算结果，仅作历史留档，不作为对外营收或利润承诺
- `ev_excel_部署效能_33`: 原始行: GPU='H200' 模型='DeepSeek-V4-Pro（峰值）' 部署卡数=8 并发=1 单用户输出P90=117.096 每卡输入=1553.597 每卡输出=12.659 命中率=Decimal('0.958') 策略='TP8EP1 w/ MTP' 数据来源='InferenceX'。该行 I/J/K/L/M 为旧表计算结果，仅作历史留档，不作为对外营收或利润承诺
- `ev_excel_部署效能_4`: 原始行: GPU='B300' 模型='KIMI-K3' 部署卡数=8 并发=16 单用户输出P90=32.123 每卡输入=7592.283 每卡输出=58.414 命中率=Decimal('0.862') 策略='TP8DCP8EP1 w/ DP Attn w/ Dspark' 数据来源='InferenceX'。该行 I/J/K/L/M 为旧表计算结果，仅作历史留档，不作为对外营收或利润承诺
- `ev_excel_部署效能_5`: 原始行: GPU='B300' 模型='KIMI-K3' 部署卡数=8 并发=8 单用户输出P90=63.898 每卡输入=5139.806 每卡输出=40.633 命中率=Decimal('0.897') 策略='TP8DCP8EP1 w/ DP Attn w/ Dspark' 数据来源='InferenceX'。该行 I/J/K/L/M 为旧表计算结果，仅作历史留档，不作为对外营收或利润承诺
- `ev_excel_部署效能_6`: 原始行: GPU='B300' 模型='KIMI-K3' 部署卡数=8 并发=4 单用户输出P90=121.065 每卡输入=3096.442 每卡输出=23.446 命中率=Decimal('0.904') 策略='TP8DCP8EP1 w/ DP Attn w/ Dspark' 数据来源='InferenceX'。该行 I/J/K/L/M 为旧表计算结果，仅作历史留档，不作为对外营收或利润承诺
- `ev_excel_部署效能_7`: 原始行: GPU='B300' 模型='KIMI-K3' 部署卡数=8 并发=2 单用户输出P90=132.45 每卡输入=2091.381 每卡输出=18.591 命中率=Decimal('0.908') 策略='TP8DCP8EP1 w/ DP Attn w/ Dspark' 数据来源='InferenceX'。该行 I/J/K/L/M 为旧表计算结果，仅作历史留档，不作为对外营收或利润承诺
- `ev_excel_部署效能_8`: 原始行: GPU='B300' 模型='KIMI-K3' 部署卡数=8 并发=1 单用户输出P90=202.84 每卡输入=2010.199 每卡输出=15.131 命中率=Decimal('0.94') 策略='TP8DCP8EP1 w/ DP Attn w/ Dspark' 数据来源='InferenceX'。该行 I/J/K/L/M 为旧表计算结果，仅作历史留档，不作为对外营收或利润承诺
- `ev_excel_部署效能_9`: 原始行: GPU='H200' 模型='KIMI-K3' 部署卡数=32 并发=26 单用户输出P90=1.46 每卡输入=262.758 每卡输出=1.054 命中率=Decimal('0.598') 策略='TP8EP32 w/ DP Attn w/ Dspark' 数据来源='InferenceX'。该行 I/J/K/L/M 为旧表计算结果，仅作历史留档，不作为对外营收或利润承诺

### official_benchmark（8 条）

| evidence_id | 发布方 | 位置 | 版本 | 抓取时间 | 原始 sha256 | 质量标记 |
|---|---|---|---|---|---|---|
| `ev_official_lmsys_epd_elastic_encoder` | lmsys-sglang | https://www.lmsys.org/blog/2026-01-12-epd/ | — | 2026-10-15 10:26:00+08:00 | eae2ea69ad16 | official_source_page_archived, partial_evidence_candidate_not_in_performance_table, metrics_not_machine_parsed_present_in_document_only, manifest_status:partial_evidence |
| `ev_official_lmsys_hetero_cpu_gpu_epd` | lmsys-sglang | https://www.lmsys.org/blog/2026-06-01-hetero-epd/ | — | 2026-10-15 10:26:00+08:00 | eef667e2baa8 | official_source_page_archived, partial_evidence_candidate_not_in_performance_table, metrics_not_machine_parsed_present_in_document_only, manifest_status:partial_evidence |
| `ev_official_lmsys_hpc_ops_sglang` | lmsys-sglang | https://www.lmsys.org/blog/2026-08-07-hpc-ops-sglang/ | — | 2026-10-15 10:26:00+08:00 | 4cdb9daf5409 | official_source_page_archived, partial_evidence_candidate_not_in_performance_table, metrics_not_machine_parsed_present_in_document_only, manifest_status:partial_evidence |
| `ev_official_lmsys_sglang_llama3` | lmsys-sglang | https://www.lmsys.org/blog/2024-07-25-sglang-llama3/ | — | 2026-10-15 10:26:00+08:00 | 1dad2e26b2ef | official_source_page_archived, partial_evidence_candidate_not_in_performance_table, metrics_not_machine_parsed_present_in_document_only, manifest_status:partial_evidence |
| `ev_official_sglang_qwen3_coder_cookbook` | sgl-project/sglang | https://github.com/sgl-project/sglang/blob/main/docs/cookbook/autoregressive/Qwen/Qwen3-Coder.mdx | git-blob:a743f7ba3eb3a204bf9f69ffc7d21d3311fa7703 | — | 67ca6704360e | official_project_published_source, source_snapshot_archived, git_blob_sha_verified |
| `ev_official_sglang_qwen3_cookbook` | sgl-project/sglang | https://github.com/sgl-project/sglang/blob/main/docs/cookbook/autoregressive/Qwen/Qwen3.mdx | git-blob:580640bd1b9ab74fb5ea0be6120050a99fa93f6d | — | 23d8e61ce899 | official_project_published_source, source_snapshot_archived, git_blob_sha_verified |
| `ev_official_vllm_ascend_kimi_k2_thinking` | vllm | https://docs.vllm.ai/projects/ascend/en/v0.23.0rc1/tutorials/models/Kimi-K2-Thinking.html | v0.20.2rc1 | 2026-10-15 10:26:00+08:00 | 486bfd63d0ca | official_source_page_archived, partial_evidence_candidate_not_in_performance_table, metrics_not_machine_parsed_present_in_document_only, manifest_status:partial_evidence |
| `ev_official_vllm_recipes_deepseek_v3_console_output` | vllm | https://docs.vllm.ai/projects/recipes/en/latest/DeepSeek/DeepSeek-V3.html | — | 2026-10-15 10:20:33+08:00 | 6ff76f16c0a7 | official_source_page_archived, partial_evidence_candidate_not_in_performance_table, metrics_machine_parsed_from_evidence_file, single_request_smoke_test, manifest_status:partial_evidence |

备注（原始值留档）：

- `ev_official_lmsys_epd_elastic_encoder`: EPD（Encoder-Prefill-Decode）弹性扩展文章，配置侧列出了 8x H20 96GB、 P/D 部署变体与 TP 规模、输入输出长度与 QPS 区间，但时延/吞吐结果以图片呈现， 正文只有相对提升，故不转写为 metrics。
- `ev_official_lmsys_hetero_cpu_gpu_epd`: 异构 CPU+GPU EPD 分离文章。正文给出模型名、输入输出长度、请求数与 QPS 区间， 但硬件型号/卡数与拓扑缺失，且确切时延吞吐值在图片中（正文仅相对提升）， 因此 configuration 与 metrics 两侧都不完整。
- `ev_official_lmsys_hpc_ops_sglang`: HPC-Ops x SGLang 算子与推理性能文章，含 H200/H20 型号、TP/EP 与算子时延、 跨 batch size 的服务端结果。存在多处实测数值，但散落在正文与图表中， 且服务端结果的并发/到达率口径未统一给出，故记录为 partial_evidence 不转写数值。
- `ev_official_lmsys_sglang_llama3`: Llama3 服务性能对比文章，含 GPU 型号与卡数、模型名、部分精度设置、 vLLM/TensorRT-LLM 版本、负载长度区间与请求率，以及吞吐/时延对比。 但缺失 SGLang 版本与 model revision，且各案例的 TP/EP/DP 拓扑未完整给出， 故为 partial_evidence。数值未做机器解析，不转写。
- `ev_official_sglang_qwen3_coder_cookbook`: SGLang Cookbook 官方原文快照（docs/cookbook/autoregressive/Qwen/Qwen3-Coder.mdx）。Git Blob SHA-1 与 SHA256 语义不同，二者均已校验。
- `ev_official_sglang_qwen3_cookbook`: SGLang Cookbook 官方原文快照（docs/cookbook/autoregressive/Qwen/Qwen3.mdx）。Git Blob SHA-1 与 SHA256 语义不同，二者均已校验。
- `ev_official_vllm_ascend_kimi_k2_thinking`: 含 TPOT / P99 TTFT / 吞吐等实测数值与较长配置说明，是本次最接近完整的一条。 但未给出 TP/EP/DP 拓扑，也未给出 model revision，故仍为 partial_evidence。 数值以文档正文/表格形式呈现，未做机器解析，因此不转写为 metrics。
- `ev_official_vllm_recipes_deepseek_v3_console_output`: 页面 "Expected Output" 段给出了 vllm bench serve 的完整控制台汇总（含 P99 TTFT 560.00ms、 P99 TPOT 15.85ms、P99 ITL 16.15ms、Output token throughput 61.00 tok/s 等）， 数值由 scripts 从存档 HTML 机器解析得到（见 importers/console_summary.py），非人工抄录。 **关键限制**：Successful requests = 1，说明这是单请求冒烟校验而非吞吐基准， 因此 P99 = Median = Mean 不具统计意义，不能作为 SLA 证据。 页面本身标注 "kept as a historical reference"，指向 recipes.vllm.ai 为现行版本。 页面在 "8xH200 / 8xMI300X (FP8)" 与 "4xB200" 两个小节下分别给出启动命令， 但 Expected Output 未声明对应哪一套配置，故 hardware_binding = unbound。

## 7. 边界与免责声明

- 本引擎不提供任何 SLA 承诺；缺少实测 P99 TTFT/TPOT 时一律标记 `not_evaluable`。
- `input_tps_cluster` 仅按原 Benchmark 配置的卡数归一化还原，禁止跨 GPU/TP/PD 外推。
- 引擎不预测扩卡、改 TP、改 P/D 后的吞吐；不自动选择满足 SLA 的 GPU 数量。
- 情景模式下的 `effective_monetization_ratio` 是敏感性参数，不是实际变现率。
- 结果哈希 `input_snapshot_hash` 可用于复算校验。
