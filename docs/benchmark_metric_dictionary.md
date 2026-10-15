# Benchmark 指标字典（M8）

- 版本：`metric_semantics_version = 1.0`（2026-10-15）
- 代码侧真值：`src/llm_capacity_pricing/engines/metric_semantics.py`
  （本文件中的表格由该模块的 `render_dictionary_markdown()` 生成，测试比对二者一致，
  防止文档与实现口径漂移）
- 适用来源：SGLang `bench_serving`、vLLM `bench serve`（两套工具的输出字段名不同，
  但**规范字段名与口径统一**；字段映射见 `importers/console_summary.py` 的 `LABEL_MAP`）

## 1. 为什么要单独维护一份口径字典

任务书 §4 列出的三组混淆，在真实数据里都会出现：

1. **配置值当实测值**：SGLang 命令写 `--max-concurrency 100`，输出 "Concurrency: 90.50"。
   这是两个数（一个是上限，一个是"全请求累计耗时 / 墙钟时间"的观测均值），必须并存。
2. **目标长度当实际长度**：命令写 `--random-input-len 1000`，而某次 80 请求的实测
   `total_input_tokens = 39668`（平均 495.85/请求）——目标 1000 并不等于实际分布。
3. **整组 GPU 吞吐当单卡能力**：`Input token throughput (tok/s) = 13441.86` 是
   8 张卡一起跑出来的集群吞吐；除以 8 得到的每卡值只能在**同一部署配置**内比较。

## 2. 字段定义（由代码生成）

| 标准字段 | 口径 ID | 单位 | 定义 | 禁止替代 |
|---|---|---|---|---|
| `configured_max_concurrency` | `m.concurrency.configured` | requests | 测试命令 --max-concurrency 给出的**最大允许并发**，是配置值。（不等于实际平均在途请求数；SGLang 输出中对应 'Max request concurrency'。） | — |
| `observed_concurrency` | `m.concurrency.observed` | requests | 文档报告的**观测**平均并发；SGLang 口径为『全请求累计耗时 / 墙钟时间』（对应输出 'Concurrency'）。（必须注明计算窗口与统计方法；与配置并发是两个数，必须并存。） | `configured_max_concurrency` |
| `traffic_request_rate_raw` | `m.request_rate.raw` | req/s 或符号 | 请求到达速率；``inf`` 表示负载生成器不限速（符号值，不是数值）。（符号值不得转成 Decimal 参与任何财务计算（禁止 Decimal(Infinity)）。） | — |
| `input_len_target` | `m.len.target.input` | tokens | Benchmark 目标输入长度（如 --random-input-len）。（是目标参数，不等于逐请求实际输入长度分布。） | — |
| `output_len_target` | `m.len.target.output` | tokens | Benchmark 目标输出长度（如 --random-output-len）。（是目标参数，不等于逐请求实际生成长度分布。） | — |
| `total_input_tokens` | `m.tokens.total.input` | tokens | 同一测量窗口内实际累计的输入 token 数（来源原文定义）。（SGLang 另行给出 text/vision 拆分，二者相加与总数不一定严格相等（retokenize 影响）。） | — |
| `total_output_tokens` | `m.tokens.total.output` | tokens | 同一测量窗口内实际累计的生成 token 数。（控制台另有 '(retokenized)' 计数，是**另一个**指标，不得覆盖本字段。） | — |
| `input_throughput_tok_s` | `m.throughput.cluster.input` | tok/s | **整套参与测试 GPU** 的输入吞吐（集群口径）。（不能当作单卡独立可实现能力；跨 TP/DP/PD/GPU 数外推被禁止。） | — |
| `output_throughput_tok_s` | `m.throughput.cluster.output` | tok/s | **整套参与测试 GPU** 的输出吞吐（集群口径）。（同上；与 peak output throughput 也不是同一个量。） | — |
| `input_tps_per_gpu_normalized` | `m.throughput.per_gpu.input` | tok/s/gpu | 在同一原始部署配置下，用集群吞吐 ÷ GPU 数得到的**归一化**资源效率。（仅用于同配置内的资源效率比较，禁止跨配置外推。） | — |
| `output_tps_per_gpu_normalized` | `m.throughput.per_gpu.output` | tok/s/gpu | 同一原始部署配置下，输出集群吞吐 ÷ GPU 数。（同上。） | — |
| `ttft_p99_ms` | `m.latency.ttft.p99` | ms | 该 Bench 版本规定的首响应/首有效 token 计时口径下的 P99。（保存 percentile 方法说明；不得用均值或中位数冒充 P99。） | `ttft_mean_ms`、`ttft_median_ms` |
| `tpot_mean_ms` | `m.latency.tpot.mean` | ms | 请求级 TPOT（首个输出 token 之后）的均值。（≠ 全 token 间隔池的 ITL 均值。） | `itl_mean_ms` |
| `tpot_p99_ms` | `m.latency.tpot.p99` | ms | 请求级 TPOT 的 P99。（**不得**用 ITL P99 冒名顶替（FORBIDDEN_SUBSTITUTIONS 已强制）。） | `itl_p99_ms` |
| `itl_mean_ms` | `m.latency.itl.mean` | ms | 逐 token/chunk 间隔的均值；受版本与流式协议影响。 | — |
| `itl_p99_ms` | `m.latency.itl.p99` | ms | 逐 token/chunk 间隔的 P99。（与 TPOT P99 并列存在，不互相替代。） | — |
| `physical_cache_hit_rate` | `m.cache.physical` | ratio | KV Prefix 物理复用 / 框架监控到的命中率。 | — |
| `billable_cached_input_ratio` | `m.cache.billable` | ratio | **可计费**缓存命中 token 占比，按合同计费规则定义。（无合同规则不得推导；Excel H 列只是旧表定价代理假设。） | `physical_cache_hit_rate`、`cache_hit_rate_raw` |

**其它已定义指标**（同表，见代码）：`input_tps_per_gpu_normalized`、
`output_tps_per_gpu_normalized`、`ttft_p99_ms`、`tpot_mean_ms`、`tpot_p99_ms`、
`itl_mean_ms`、`itl_p99_ms`、`physical_cache_hit_rate`、`billable_cached_input_ratio`。

## 3. 指标口径编号（`metric_definition_id`）

每条候选/记录在 `metric_definition_ids` 里携带"字段 → 口径 ID"的映射，
便于跨来源核对时确认两边说的是同一件事：

| 口径 ID | 含义 |
|---|---|
| `m.concurrency.configured` / `m.concurrency.observed` | 配置并发 / 观测并发，二者独立 |
| `m.request_rate.raw` | 到达速率（含 `inf` 符号值） |
| `m.len.target.input` / `m.len.target.output` | 目标长度（非实际分布） |
| `m.tokens.total.input` / `m.tokens.total.output` | 实际累计 token（含 retokenized 另计） |
| `m.throughput.cluster.input` / `m.throughput.cluster.output` | 集群吞吐 |
| `m.throughput.per_gpu.input` / `m.throughput.per_gpu.output` | 同配置归一化每卡吞吐 |
| `m.latency.ttft.p99` | 首 token P99 |
| `m.latency.tpot.mean` / `m.latency.tpot.p99` | 请求级 TPOT 的均值 / P99 |
| `m.latency.itl.mean` / `m.latency.itl.p99` | 逐 token 间隔的均值 / P99 |
| `m.cache.physical` / `m.cache.billable` | 物理命中率 / 可计费命中比例 |

## 4. 一致性校验：吞吐 ≈ 累计 token / 时长

`engines/metric_semantics.py` 的 `throughput_consistency()` 对每条记录校验：

```
input_throughput_tok_s  ≈ total_input_tokens  / duration_s
output_throughput_tok_s ≈ total_output_tokens / duration_s
```

容差取 **1e-3（0.1%）**，理由是可量化的：公开发布值只保留 2 位小数，整数 token 数
除以 2 位小数的时长，相对舍入误差上限约 `1e-4`。12 条 SGLang 候选实测最大相对误差
为 **2.27e-4**（输入侧），全部落在容差内；而真正的单位/量纲错误（例如把 tok/s 写成
tok/min）会造成 10x、1000x 级别的偏差，容差完全拦得住。

校验结果写入候选的 `throughput_consistency` 字段（逐项记录 published / computed /
relative_error / within_tolerance），并在 `official-coverage` 中汇总。

## 5. 拒绝案例（口径被混用时必须拦下）

**案例 A：用 ITL P99 冒名 TPOT P99。**
SGLang 输出里同时有 `P99 TPOT (ms): 15.85` 与 `P99 ITL (ms): 16.15`（vLLM Recipes 的
DeepSeek-V3 样例）。二者接近但**不是同一个统计量**：TPOT 按请求级 TPOT 聚合，ITL 是
逐 token/chunk 间隔池。适配器侧由 `FORBIDDEN_SUBSTITUTIONS` 硬拦（`tpot_p99_ms` 禁止被
`p99_itl_ms` 填充），入库后再由 `semantic_violations()` 做二次体检：若两个字段取值
完全相同，会写进 `semantic_warnings` 要求人工确认。

**案例 B：用 `--max-concurrency` 顶替观测并发。**
某条候选 `--max-concurrency 100`、输出 `Concurrency: 90.50`。若代码里只存一个
`concurrency` 字段，就会在"配置 100 = 实际 90.5"之间丢失信息，进而把 SLA 结论建立在
错误前提上。本项目的做法是**两个字段都存**（`configured_max_concurrency` 来自命令、
`observed_concurrency` 来自输出），并有测试
`test_configured_and_observed_concurrency_coexist` 固定该行为。

**案例 C：物理命中率当可计费命中比例。**
Excel H 列 `cache_hit_rate_raw` 语义未确认，`cache_metric_semantics="unknown"`，
只作旧表定价代理假设（`legacy_proxy_for_billable_hit_rate=True`）。
`billable_cached_input_ratio` 在字典里明确禁止由 `physical_cache_hit_rate` 或
`cache_hit_rate_raw` 填充——没有合同计费规则不得推导。

## 6. 跨来源可比性

`cross_backend_comparable(version_a, version_b)` 只在**后端版本字符串完全相同**时返回
True。不同框架（SGLang vs vLLM）或不同版本之间，指标口径未核实一致，
**不允许合并统计或直接排序性能优劣**——这一点在报告与校验中都被显式声明。

## 7. 缺失值的处理

- 缺字段一律 `None`，禁止填 0；来源明确给出的 0（如零成功请求时的吞吐 0）作为
  **实测 0** 保留，并带 `zero_successful_requests` 标记。
- 缺少逐请求原始样本时，**不允许**由总吞吐反推 P99、E2E 或真实长度分布
  （任务书 §4 第 2 条），代码中没有任何这样的推算路径。
- `traffic_request_rate="inf"` 是负载生成模式符号，存在
  `traffic_request_rate_raw`，**永不**进入 `metrics` 或任何 `Decimal` 财务字段
  （Schema 校验器拒绝非有限 Decimal）。
