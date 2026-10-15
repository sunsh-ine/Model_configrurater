# 官方 Benchmark 覆盖缺口报告（M5 / 验收整改任务 4）

- 采集日期：2026-10-15
- 采集方式：人工触发的 HTTP 抓取（**无自动爬取、不绕过访问限制**）。线索检索借助
  codex CLI 的联网检索能力定位候选页面，**每个 URL 都由本机 curl 独立复核 HTTP 状态后**
  才抓取存档，未直接采信检索结果中的任何数值。
- 清单文件：`data/sources/official_manifest.yaml`（14 条）
- 存档证据：`data/raw/official/*.html`（12 个文件，每个都记录 sha256）
- 入库命令：`import-official` → 表 `official_benchmark_candidate`
- 覆盖查询：`official-coverage`（当前输出见 §1）

## 1. 结论

**本次仍未取得任何可入库的 `official_benchmark` 实测记录：verified = 0 条
（`official-coverage` 输出 `verified_count: 0`）。**

但本阶段把"不完整但不是零证据"的来源变成了**结构化缺口台账**，而不是丢掉：

```json
{
  "candidate_count": 6,
  "verified_count": 0,
  "by_publisher": {"lmsys-sglang": 4, "vllm": 2},
  "with_parsed_metrics": 1,
  "throughput_benchmarks": 0,
  "single_request_smoke_tests": 1
}
```

要点：

- 6 条候选全部标记 `verification_status = "partial_evidence"`，**不进**
  `benchmark_record`，更不会参与任何 SLA / 产能结论；
- 其中 1 条（`official_vllm_recipes_deepseek_v3_console_output`）的指标是从存档 HTML
  里**机器解析**出来的原始控制台汇总，其余 5 条只登记"存在哪些字段、缺哪些字段"，
  **数值不做人工转写**（转写即等于二次录入，容易失真且不可复核）；
- 没有任何一条同时具备"完整实验配置 + 实测指标"，所以 `throughput_benchmarks = 0`；
- SQLite 中 `performance_source_kind = "official_benchmark"` 的记录数仍为 **0**；
  32 条效能记录来源是 `internal_excel`（底层为第三方 `InferenceX`，**不是**
  SGLang/vLLM 官方数据）。

按照任务书 §8 M5："若无法获得 2 条完整来源，**不得捏造**"，此缺口以本文件如实记录。

## 2. 逐来源核查记录（manifest 全部 14 条）

| # | 条目 ID | 发布方 | status | 请求 URL | HTTP | 含实测数值 | 判定 |
|---|---|---|---|---|---|---|---|
| 1 | `sglang_bench_serving_guide` | sglang | `not_a_result_dataset` | `docs.sglang.ai/developer_guide/bench_serving`（旧路径） | 404 → 重定向到 `docs.sglang.io` 后 200 | 否 | 工具文档，仅口径说明 |
| 2 | `sglang_benchmark_and_profiling` | sglang | `not_a_result_dataset` | `docs.sglang.ai/developer_guide/benchmark_and_profiling.html` | 200 | 否 | 工具文档 |
| 3 | `vllm_bench_serve_cli` | vllm | `not_a_result_dataset` | `docs.vllm.ai/en/stable/cli/bench/serve/` | 200 | 否 | CLI 参考 |
| 4 | `vllm_cli_guide` | vllm | `not_a_result_dataset` | `docs.vllm.ai/en/latest/cli/` | 200 | 否 | CLI 索引 |
| 5 | `vllm_performance_dashboard` | vllm | `not_a_result_dataset` | `docs.vllm.ai/en/latest/benchmarking/dashboard/` | 200 | 否 | 描述"性能面板"这一工具 |
| 6 | `vllm_recipes_index` | vllm | `not_a_result_dataset` | `docs.vllm.ai/projects/recipes/en/latest/` | 采集时 200，复核时 **429** | 否 | 导航索引页；`source_url` 记 `null`（见 §5 注） |
| 7 | `candidate_sglang_public_benchmark_result` | sglang | `pending_verification` | 未定位 | — | — | 占位条目，等待补齐 |
| 8 | `candidate_vllm_public_benchmark_result` | vllm | `pending_verification` | 未定位 | — | — | 占位条目，等待补齐 |
| 9 | `vllm_recipes_deepseek_v3_console_output` | vllm | `partial_evidence` | `docs.vllm.ai/projects/recipes/en/latest/DeepSeek/DeepSeek-V3.html` | 200 | **是**（已机器解析） | 候选入库，缺硬件与版本 |
| 10 | `vllm_ascend_kimi_k2_thinking` | vllm | `partial_evidence` | vLLM Ascend 文档（Kimi-K2-Thinking） | 200 | 是（未转写） | 候选入库，缺硬件与拓扑 |
| 11 | `lmsys_hpc_ops_sglang` | lmsys-sglang | `partial_evidence` | LMSYS 博客（HPC-Ops × SGLang） | 200 | 是（未转写） | 候选入库，缺模型/框架版本 |
| 12 | `lmsys_epd_elastic_encoder` | lmsys-sglang | `partial_evidence` | LMSYS 博客（EPD 弹性扩展） | 200 | 是（未转写） | 候选入库 |
| 13 | `lmsys_hetero_cpu_gpu_epd` | lmsys-sglang | `partial_evidence` | LMSYS 博客（异构 CPU+GPU EPD） | 200 | 是（未转写） | 候选入库，缺口最多 |
| 14 | `lmsys_sglang_llama3` | lmsys-sglang | `partial_evidence` | LMSYS 博客（Llama3 服务性能） | 200 | 是（未转写） | 候选入库 |

前 6 条的抓取证据留档方式：原始 HTML 存档 → 计算 sha256 写入 manifest →
`tests/test_official_manifest.py` 每次运行都重新计算并比对哈希；另有一条反向测试
确认 `data/raw/official/` 下**不存在**没有 manifest 条目的存档文件。

## 3. 为什么这些官方页面不能当作性能数据

任务书 §5.1 已明确："这些资料用于确认测试命令、输入输出和指标口径。**官方工具文档
不是性能结果数据集**。" 本次核查与之一致。要新增 `official_benchmark` 记录，必须同时具备：

1. 明确的官方实验配置（模型与 revision、精度、硬件型号与卡数、TP/DP/EP 或 P/D 拓扑、节点数）；
2. 原始指标（TTFT / TPOT / 吞吐，含统计口径与 percentile 定义）；
3. 可追溯来源链接与版本、抓取日期、原始证据文件（sha256）。

第 1～6 条只满足第 3 条的形式要求；第 9～14 条**部分**满足第 2 条但都缺第 1 条的关键项，
因此只能停在 `partial_evidence`。

## 4. `partial_evidence` 候选的字段缺口台账

`import-official` 会把每条候选的"已具备字段 / 缺失字段"逐项算出来入库（不接受手工填写的
`missing_fields`：`build_candidate` 会拿实际模型状态与清单声明比对，不一致就报错）。
当前 6 条的缺口如下（缺口数由 `absent_required_fields()` 计算，测试逐条固定）：

| 候选 ID | 模型名 | 硬件绑定 | 已具备字段 | 缺失字段数 | 缺失字段 |
|---|---|---|---|---|---|
| `official_vllm_recipes_deepseek_v3_console_output` | DeepSeek-R1 / DeepSeek-V3 | `unbound` | 28 | 5 | `model_revision`,`framework_version`,`hardware_model`,`deployment_gpu_count`,`dataset_name` |
| `official_vllm_ascend_kimi_k2_thinking` | Kimi-K2-Thinking | `unbound` | 13 | 6 | `model_revision`,`hardware_model`,`deployment_gpu_count`,`dataset_name`,`ttft_p99_ms`,`tpot_p99_ms` |
| `official_lmsys_sglang_llama3` | Llama-3 | `unbound` | 11 | 7 | 上列 6 项 + `framework_version` |
| `official_lmsys_hpc_ops_sglang` | —（未声明） | `unbound` | 10 | 8 | 上列 7 项 + `model_name` |
| `official_lmsys_epd_elastic_encoder` | —（未声明） | `unbound` | 10 | 8 | 上列 7 项 + `model_name` |
| `official_lmsys_hetero_cpu_gpu_epd` | —（未声明） | `unbound` | 9 | 8 | 上列 7 项 + `model_name` |

（`上列 N 项` 指该行上一行的缺失字段集合；仅 3 条 LMSYS 条目使用这种省略写法，
vLLM 两条均为全量列出。）

**硬件绑定一律为 `unbound`**：所有候选页面都没有给出可核对的 GPU 型号与卡数，
因此严格按照"不允许补造缺失配置"的要求，`hardware_model` 与 `deployment_gpu_count`
保持 `None`，候选记录**不得**与任何价格行或成本行做关联。

### 唯一一条带机器解析指标的候选

`official_vllm_recipes_deepseek_v3_console_output` 的 "Expected Output" 段给出了一段
完整的 `vllm bench serve` 控制台汇总。解析方式：HTML → 纯文本 → 按
`Serving Benchmark Result` 块切分 → 标签映射到规范字段（原始标签一并存入
`raw_labels` 以便复核）。解析结果（Decimal，原样保留精度）：

| 字段 | 值 | 字段 | 值 |
|---|---|---|---|
| `success_count` | 1 | `output_throughput_tok_s` | 61.00 |
| `duration_s` | 16.39 | `total_token_throughput_tok_s` | 543.06 |
| `total_input_tokens` | 7902 | `ttft_mean_ms` / `ttft_median_ms` / `ttft_p99_ms` | 560.00 / 560.00 / 560.00 |
| `total_output_tokens` | 1000 | `tpot_mean_ms` / `tpot_median_ms` / `tpot_p99_ms` | 15.85 / 15.85 / 15.85 |
| `request_throughput_rps` | 0.06 | `itl_mean_ms` / `itl_median_ms` / `itl_p99_ms` | 15.85 / 15.85 / 16.15 |

**这条数据为什么不能当产能依据（重要）：**

- `success_count = 1`，即**单次请求的冒烟测试**（页面本身就是用它演示输出格式的）。
  引擎据此自动打了 `single_request_smoke_test` 标记并把
  `is_throughput_benchmark` 判为 `False`；
- 因此 `output_throughput = 61.00 tok/s` 是**单流**数值，不是单卡或集群吞吐，
  不能乘 31536000 变成年产能，也不能与 Excel 的 `output_tps_per_gpu` 相比；
- `ttft_p99 = 560.00 ms` 与 `tpot_p99 = 15.85 ms` 在这里与均值相等，是单样本的必然结果，
  **不构成**任何 P99 分布证据，不能用于 SLA 结论；
- 另外还缺 `model_revision`、`framework_version`、`hardware_model`、`deployment_gpu_count`、
  `dataset_name` 五项配置字段。

## 5. 有价值的副产物（可作为字段口径依据）

虽然不能作为性能记录，抓取到的文档确认了若干**口径**，已写入适配器
`metric_definitions` 与 `docs/data_dictionary.md`：

- SGLang `bench_serving` 的 **Concurrency** 定义是
  > "aggregate time of all requests divided by wall time"

  这正是任务书 §0.3 强调的口径差异来源：**它不等于 `--max-concurrency` 配置值**。
  本引擎据此把 Excel 的 D 列命名为 `configured_concurrency`，并单独保留
  `mean_observed_concurrency`（本阶段为空）。原文出处见
  `data/raw/official/sglang_bench_serving_guide_2026-10-15.html`。
- SGLang 输出的 Input/Output/Total token throughput 已把**文本与视觉 token 合并计入**，
  与任务书 §0.3 "input/output throughput 是同一次实验、同一种部署拓扑下的总吞吐
  或归一化吞吐"的口径要求一致。
- vLLM `bench serve` 结果 JSON 的字段名（`mean_ttft_ms` / `p99_ttft_ms` /
  `mean_tpot_ms` / `p99_tpot_ms` / `mean_itl_ms` / `p99_itl_ms` / `mean_e2el_ms` /
  `p99_e2el_ms` / `request_throughput` / `input_throughput` / `output_throughput` /
  `duration` / `completed` / `max_concurrency` / `request_rate`）已用于
  `vllm_adapter.py` 的映射表，并明确 `mean_tpot_ms ≠ mean_itl_ms`。
- vLLM Recipes 控制台的原始标签（`P99 TTFT (ms)`、`P99 TPOT (ms)`、`Mean ITL (ms)` 等）
  确认了 `bench serve` 输出里 **ITL 与 TPOT 是两个并列字段**，佐证了
  `FORBIDDEN_SUBSTITUTIONS` 中"不许用 ITL 顶替 TPOT"的规则。

> 注：`vllm_recipes_index` 条目在重新校验 URL 时收到 HTTP 429（限流），
> 因此 `source_url` 记为 `null`，只保留本地存档文件与 sha256；
> 不把"没复核成功的 URL"写成已核实来源。

## 6. 下一步补齐建议（按优先级）

1. **确定可引用的官方实测来源**：优先查找框架官方发布的、带有完整配置表的 benchmark
   报告/发布说明（例如带 TP/EP 拓扑与硬件清单的结果页），而不是 CLI 文档与配方页。
   当前 6 条候选的缺口集中在同一处：`hardware_model` + `deployment_gpu_count` +
   `model_revision`。**只要有一份来源同时补上这三项**，即可在候选表基础上直接升级为
   `verified` 并导入 `official_benchmark`。
2. **若获得来源**：下载原始证据 → 计算 sha256 → 在 manifest 中把 `status` 从
   `partial_evidence` 改为 `verified`（同时填齐 §3 的三项要求）→ 用适配器导入
   `official_benchmark` 记录 → 重跑 `import-official` 与 `official-coverage`，
   确认 `verified_count` 从 0 变正数。
3. **补齐内部证据**：与业务方确认 Token 价格币种与 `GPU价格!C` 的成本覆盖范围，
   并补充 TTFT/TPOT 的 P99 实测口径，否则 SLA 相关字段将持续为 `not_evaluable`。

> 说明：本节 1～3 项均**尚未执行**，属于待办而非已完成能力。
