# 官方 Benchmark 覆盖缺口报告（M5 + M7）

- 最近更新：2026-10-15（M7 批次）
- 采集方式：人工触发的 HTTP 抓取（**无自动爬取、不绕过访问限制**）。线索检索借助
  codex CLI 的联网检索能力定位候选页面，**每个 URL 都由本机 curl 独立复核 HTTP 状态后**
  才抓取存档，未直接采信检索结果中的任何数值。
- 清单文件：`data/sources/official_manifest.yaml`（**18 条**；M7 数值型 2 条 + D7 公开参考 2 条）
- 存档证据：`data/raw/official/`（12 个 HTML + **2 个 .mdx**，每个都记录 sha256）
- 入库命令：`import-official`（页面级候选）与 `import-sglang-candidates`（M7 数值型候选）
  → 同一张表 `official_benchmark_candidate`，用 `candidate_family` 区分
- 覆盖查询：`official-coverage`

> **本文件在 M5 之后由 M7 续写**：§0 是本批次（M7）的结论与逐条核对，§1 起保留 M5 的
> 页面级核查记录。两批数据都在候选表里，互不覆盖。

## 0. M7 批次：SGLang Cookbook 官方数值型候选

### 0.1 结论

**完整可复现的官方案例仍为 verified = 0 条**（`official-coverage` 输出
`verified_count: 0`），但本次取得了 12 条"有完整测试命令 + 原始控制台输出、
且已逐字段机器核对"的官方**数值**记录。

```json
{
  "candidate_count": 18,
  "by_family": {"manifest_partial_evidence": 6, "sglang_numeric_cookbook": 12},
  "manifest_candidates": 6,
  "numeric_candidates": 12,
  "verified_count": 0,
  "by_publisher": {"lmsys-sglang": 4, "vllm": 2, "sgl-project/sglang": 12},
  "with_parsed_metrics": 13,
  "throughput_benchmarks": 12,
  "single_request_smoke_tests": 1,
  "numeric_verified_against_source": 12,
  "numeric_missing_model_revision": 12,
  "numeric_source_count": 2,
  "gates": {
    "can_reference_performance": 12,
    "can_evaluate_sla": 0,
    "can_calculate_pricing": 0,
    "commercial_quote_allowed": 0
  }
}
```

两个来源、12 条记录：

| 来源 | 条数 | 模型 | 硬件 / 拓扑 | 文档版本 |
|---|---|---|---|---|
| SGLang Qwen3 Cookbook | 9 | Qwen/Qwen3-235B-A22B-Instruct-2507 | 8×B200、TP8 | SGLang 0.5.6 |
| SGLang Qwen3-Coder Cookbook | 3 | Qwen/Qwen3-Coder-480B-A35B-Instruct-FP8 | 8×MI300X、TP8、EP2 | SGLang 0.5.7 |

每条记录都具备：完整 `python3 -m sglang.bench_serving` 命令、`Serving Benchmark Result`
控制台输出（26 项指标）、场景标签、目标 ISL/OSL、并发与请求数。**缺的是配置侧的
可复现要素**：`model_revision`、完整环境 pin、以及 Qwen3 的权重精度。

### 0.2 为什么这 12 条仍然不是 `official_benchmark`

任务书 §5.2 要求"官方实验配置 + 原始指标 + 可追溯来源"三者齐备才可升级。本批次的
12 条满足后两项（指标来自原文、来源已存档且 Blob SHA 与 SHA256 双校验通过），
但**实验配置缺少 model revision 与环境 pin**——按"不编造缺失配置"的原则，
不能宣称可复现，因此：

- `quality_status` 恒为 `partial_evidence`；
- `can_reference_performance = True`（可作**官方参考数值**展示）；
- `can_evaluate_sla = False`、`can_calculate_pricing = False`（不得用于 SLA 结论与经济性计算）；
- 模型 revision 保持 `null`，并登记在 `missing_fields`。

### 0.3 逐条核对方式与结果

核对不是人工比对，而是**机器解析**：按标题锚点（`5.1.1.1` 等）定位章节 →
取该节的 benchmark 命令与输出块 → 与候选 JSON 逐字段比较。

| 比对类别 | 字段 |
|---|---|
| 命令参数 | `--model`、`--dataset-name`、`--random-input-len`、`--random-output-len`、`--num-prompts`、`--max-concurrency` |
| 控制台指标 | 成功请求数、时长、累计输入/输出 token、集群输入/输出吞吐、TTFT P99、TPOT Mean/P99、观测并发 |
| 交叉校验 | 命令里的 `--max-concurrency` 与输出块 "Max request concurrency" 是否一致 |
| 环境字段 | 硬件型号（`NVIDIA B200` / `AMD MI300X`）、卡数（`(8x)`）、`--tp 8`、`--ep 2` 与原文文字/命令对照 |

结果：**12/12 通过，0 处不一致**（每条比对 13–14 个字段）。六条参考断言全部命中：

| 记录 | 配置并发 | 集群 Input tok/s | 集群 Output tok/s | TTFT P99 ms | TPOT P99 ms |
|---|---:|---:|---:|---:|---:|
| Qwen3 5.1.1.1 | 1 | 140.07 | 96.65 | 166.05 | 10.15 |
| Qwen3 5.1.1.2 | 16 | 810.44 | 832.04 | 727.08 | 24.09 |
| Qwen3 5.1.1.3 | 100 | 2713.46 | 2738.78 | 863.11 | 41.19 |
| Coder 5.1.1.1 | 1 | 82.68 | 57.19 | 662.15 | 17.08 |
| Coder 5.1.1.2 | 16 | 455.77 | 468.83 | 1318.63 | 43.48 |
| Coder 5.1.1.3 | 64 | 893.84 | 956.80 | 529.01 | 204.24 |

### 0.4 本批次发现的三个坑（已修）

1. **四反引号围栏**：Coder 原文用 ````text Output 包裹并内嵌 ```python，
   按固定三反引号匹配会整体错位，导致 6 个真实结果块被漏掉。已按 CommonMark 规则
   重写围栏扫描（闭合反引号数 ≥ 开启数）。
2. **命令归属错位**：同一段落窗口里同时有 "Model Deployment Command" 与
   "Benchmark Command" 时取错，把下一节的命令配给了上一节。已改为取最近标记 +
   限定在本节范围内匹配。
3. **`(retokenized)` 覆盖真值**：`Total generated tokens (retokenized)` 会命中
   `Total generated tokens` 的映射，把 4210 覆盖成 4206。已让括号限定词参与查表。

### 0.5 原文中存在但本批次未接入的结果块

Qwen3-Coder 原文的 `5.1.2.1`（FP8）与 `5.1.2.2`（NVFP4）两节还有 **6 个结果块**
（NVIDIA B200/GB200 配置）。它们**不在本批次提供的候选文件内**，因此：

- 不生成候选记录（避免自行扩大数据集）；
- 在 `import-sglang-candidates` 输出中登记为 `unlisted_sections_with_results: ['5.1.2', '5.1.2.1', '5.1.2.2']`、
  `unlisted_result_block_count: 6`，供后续批次按需接入。

### 0.6 正文与命令冲突的处理

两个来源的正文都提到使用 ShareGPT_Vicuna_unfiltered 数据集，而实际测试命令是
`--dataset-name random`。按任务书 §3.4，**以命令为测试参数依据**，并把冲突写进
`verification.command_conflicts`，同时打 `narrative_conflicts_with_benchmark_command` 标记。
候选中的 `dataset_name` 因此是 `random`，不是 `sharegpt`。

---

## 0bis. D7 批次：两条更贴近目标的**公开参考**来源

Demo V1.1 补入两条公开来源（manifest 因此从 16 条增至 18 条）。
它们**不是**官方候选证据，也**不是**目标配置的实测 —— 状态记为
`public_reference_partial_environment`，由
`scripts/import_public_benchmarks.py` 接入为"公开参考"，仅供对照。

| 条目 ID | 发布方 | 模型 / 硬件 | 规模与负载 | 可信等级 | 为什么仍不能当本配置实测 |
|---|---|---|---|---|---|
| `gpustack_perf_lab_qwen3_32b_h100` | GPUStack 性能实验室 | Qwen3-32B / 单张 H100 SXM | 1 卡，ShareGPT，vLLM 0.11.0 | `measured_vendor_published` | 只有 1 卡；负载是 ShareGPT（长度可变）；TP/DP 与权重精度未声明 → 匹配等级 `modeling_candidate`，只能建模/校准 |
| `community_qwen3_32b_h100_4gpu_report` | MG-Cafe（社区报告） | Qwen3-32B bf16 / 4×H100 80GB HBM3 | 4 卡 TP4，random 31400/128，vLLM 0.17.0 | `measured_third_party` | 原文 GPU 型号写的是「NVIDIA H100 80GB HBM3」而非 SXM，本工具**不做产品别名推断**，故与目录中的「NVIDIA H100 SXM」按不同型号处理；且非厂商实测，报告自述存在版本差异与 CUDA Graphs 不可比因素 |

### 接入方式：逐字段字面量核对（claims）

这两条的原文不是 SGLang 的控制台输出块，没有章节锚点可依，因此改用
**逐字段字面量核对**：`data/incoming/public_benchmark_references_20261015.json`
为每个入库取值声明一段词序列（token），导入时必须能在归档原文里**按顺序**找到
（token 之间只允许空白）。规则与对应的反例测试：

- **字面量**：claim 的 tokens 在归档原文里找不到 → **拒绝整批入库**；
- **取值绑定（N1 补）**：入库字段值必须**等于** `claim.value`（数值按 Decimal 精确比较）。
  只校验字面量是不够的 —— 原文里写着 1131.55、而入库字段被改成 9999.99 时，
  字面量检查照样通过。`derived_fields` 同样受绑定约束；
- **覆盖率**：有值却既无 claim 也无 derived 说明 → **拒绝整批入库**（不得静默采信）；
- 同一字段不允许同时声明 claim 与 derived（口径会打架）；
- 归档文件 sha256 与声明不一致 → 拒绝；
- 无法用字面量核对的取值（例如由 `Request Rate ∞` 推出的 `arrival_mode=unbounded`、
  由 `Failed Requests 0` 推出的失败率）必须写进 `derived_fields` 并给出理由，
  导入后会带上 `derived:<字段>:<理由>` 质量标记 ——
  **写了理由不等于升级成原文实测**。

核验全部发生在数据库写入之前：任一条不过即整批中止，不产出"部分可信"的记录。
`scripts/check_reference_tamper_blocked.sh` 在验证链里跑三类篡改
（只改入库值 / 只改声明值 / 两者都改），每一种都必须 0 条入库。

**已知限制（不假装更强）**：核对的是"该取值与标签确实在原文中出现且相邻"，
并不证明它出现在表格里的正确位置。因此所有 claims 都连标签一起声明
（例如 `["P99","TTFT","(ms):","153161.51"]`）。导入输出说的是"与归档原文核对通过"，
**不是**"该数字是目标配置的性能"。

### 两条来源都没能填上的字段

原文明说的缺口一律留空，不做推算：`model_revision`、GPUStack 的 `tp_size`/`dp_size`、
两条的 `configured_max_concurrency`。GPUStack 页面的「Peak concurrent requests 1000.00」
按 `observed_concurrency` 存，**不**写进 `configured_concurrency` ——
观测值不能冒充配置值。

---

## 1. M5 批次的结论（保留）

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

## 2. 逐来源核查记录（M5 批次的 14 条；manifest 现共 18 条：M7 新增 2 条见 §0，D7 新增 2 条见 §0bis）

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
