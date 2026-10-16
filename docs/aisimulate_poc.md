# NVIDIA AISimulate 独立 PoC（N3）与集成决策（N4）

**状态**：调研 + 独立实验**已完成**；本轮**未**改动主系统任何计算模块。
**执行环境**：本机无 GPU（`nvidia-smi` 不存在），全程**离线模拟**在无目标 GPU 的机器上完成。
**原始输出**：`docs/poc/aisimulate/`（每个文件都带 sha256，见 §9）

---

## 0. 结论（先看这里）

1. **工具就是它**：`aisimulate`（PyPI 包名 `aisimulate`，0.12.1），源自 `aiconfigurator`，
   仓库 `github.com/ai-dynamo/aisimulate`，SPDX 头声明 Copyright (c) NVIDIA CORPORATION，
   属于 Dynamo 生态。定位自述：**"GPU-free inference simulation and deployment configuration"**。
2. **支持我们的目标组合**：`Qwen/Qwen3-32B`（bf16）与 `Qwen/Qwen3-32B-FP8` 在 **h100_sxm** 上，
   trtllm / vllm / sglang 三个后端、aggregated 与 disaggregated **都返回 YES**。
3. **它能填上我们最核心的缺口**：给定模型 + GPU + 负载 + SLA，**自动搜索并推荐部署配置**
   （TP/PP/DP/MoE-TP/MoE-EP/CP、replicas、agg/disagg、量化、chunked prefill、MTP），
   并生成可部署的配置工件。这正是我们目前"给定硬件、自动找更优部署"的空白。
4. **但它有两套口径完全不同的接口，混用会得出错误结论**（本轮最重要的发现）：
   * **扫描器**（`cli default/recommend`）：op-level 解析模型，输出**单点均值型**估计，
     SLA 过滤用的是**聚合均值**——官方文档明说 **"It is not a p99 constraint"**；
   * **原生回放**（`aisimulate predict`）：给定确切部署 + 流量，输出**完整分位数**，
     **含 p99**，与我们的 SLA 口径（TTFT P99 / TPOT P99）可比。
5. **同一配置上两者的差**：8×H100 / TP8 / vLLM，ISL 8192 / OSL 256 / 并发 32 ——
   扫描器给 TTFT **397.92ms**、TPOT **11.51ms**（看着"刚好达标"）；
   原生回放给 **p99 TTFT 5680ms、p99 TPOT 48.2ms**（远超我们的 400ms / 30ms）。
   **均值口径会让人误判"达标"，这正是本项目一直在防的那类错误。**
6. **精度证据不覆盖我们的模型**：官方发布的 E2E 精度快照里
   **完全没有 `Qwen3-32B`**（只有 `Qwen3.5-397B-A17B` 系列）；
   全量 MAPE 为 **TTFT 45.27% / TPOT 22.39%**，且原文强调
   "Accuracy applies only to the exact measured operating points in this snapshot"。
7. **决策（N4）**：**值得集成，但只集成"候选枚举 + 分位数预测"，且必须分层**。
   本轮不实现，下一步按 §8 的最小方案做。**禁止**把模拟结果写成 `BenchmarkRun`，
   **禁止**让它获得 `observed_pass`。

---

## 1. 安装方式与版本（要求记录第 1 项）

| 项 | 值 |
|---|---|
| 安装命令 | `python3 -m venv /tmp/aisim-venv && /tmp/aisim-venv/bin/pip install aisimulate` |
| 是否需要私有源 | **不需要**（公开 PyPI：`https://pypi.org/pypi/aisimulate/json`） |
| 包版本 | `aisimulate 0.12.1` |
| 附带兼容包 | 同 wheel 提供 `aiconfigurator`（0.12.0 兼容入口）与 `aiconfigurator_core` / `aisimulate_core` 导入名 |
| wheel 大小 | 83,554,922 字节（自带模型与性能数据） |
| venv 占用 | 约 **1.5 GB** |
| Python | 3.12.3（包要求 `>=3.11,<3.14`） |
| 是否需要编译 | 是（wheel 存在源码构建路径，本机 gcc 可用，构建成功） |
| 是否需要 GPU | **不需要**（本轮全程在无 GPU 机器上运行） |
| 执行入口 | `aisimulate {predict,recommend}`（新）与 `aiconfigurator cli {default,estimate,recommend,exp,generate,support}`（兼容） |

> 兼容入口会打印弃用警告：`aiconfigurator cli` 计划在 0.13.0 移除，
> 官方指引是继续用同样命令但装 `aisimulate`。因此**适配器应优先用 `aisimulate predict/recommend`**，
> 兼容入口只作为过渡。

---

## 2. 支持矩阵（实测，不是抄文档）

查询命令：`aiconfigurator cli support --model-path <模型> --system h100_sxm [--backend <后端>]`

| 模型 | 后端 | 解析出的版本 | Aggregated | Disaggregated |
|---|---|---|---|---|
| `Qwen/Qwen3-32B-FP8` | trtllm | 1.3.0rc23 | **YES** | **YES** |
| `Qwen/Qwen3-32B-FP8` | vllm | 0.24.0 | **YES** | **YES** |
| `Qwen/Qwen3-32B-FP8` | sglang | 0.5.16 | **YES** | **YES** |
| `Qwen/Qwen3-32B`（bf16，`quant_algo=None`） | trtllm | 1.3.0rc23 | **YES** | **YES** |

模型侧解析出的结构（与我们的目录一致）：`Qwen3ForCausalLM`、64 层、64 头、8 KV 头、
head_dim 128、hidden 5120、inter 25600、vocab 151936、context 40960、非 MoE。

**必须记录的两点**：

* 支持是**按后端版本**判定的，不是按后端。官方 README 明确给了一个当前返回 `NO` 的单元格
  （`Qwen3-32B-FP8 @ h200_sxm @ vllm 0.14.0`）。因此任何集成都要把
  **模型 + system + 后端 + 后端版本**四元组当成证据身份，少一个都不算"支持"。
* `support` 报的版本（trtllm 1.3.0rc23）与扫描器实际加载的版本
  （`exp_config.yaml` 里 `backend_version: 1.3.0rc20`）**不一致**。
  落库时必须记录**实际用于计算的那个版本**，不能记 support 的返回值。

---

## 3. 最小实验（要求记录第 2、3 项）

统一负载：**ISL 8192 / OSL 256 / 并发 32**（= 我们 Demo 的目标负载），
系统 `h100_sxm`，模型 `Qwen/Qwen3-32B-FP8`。

### 3.1 扫描器：找最小 GPU 数（`recommend`）

```bash
aiconfigurator cli recommend --model-path Qwen/Qwen3-32B-FP8 --system h100_sxm \
  --target-concurrency 32 --isl 8192 --osl 256 --ttft 400 --tpot 30 \
  --database-mode SILICON --strict-sla --no-color
```

```
Target Concurrency: 32.0 / agg GPUs needed: 24 (replicas: 3)
Best Throughput: 2,934.67 tokens/s / Per-GPU: 122.28 tokens/s/gpu / Per-User: 86.90
Request Rate: 11.51 req/s / TTFT: 397.92ms / TPOT: 11.51ms / Request Latency: 3332.24ms
Rank 1 | trtllm | concurrency 39 (=13x3) | total_gpus 24 (=3x8) | replicas 3
       | gpus/worker 8 (=8x1) | parallel tp8pp1 | bs 13
```

* 耗时 **8.23 秒**（官方称"thousands of configurations"）。
* `disagg` 在 SILICON + strict SLA 下**返回 0 条**，并给出三条可能原因
  （约束太紧 / 装不下 / 该配置无硅数据）——失败信息可用，不是静默空结果。

### 3.2 扫描器：固定 8 卡找最优部署（`default`）

```bash
aiconfigurator cli default --model-path Qwen/Qwen3-32B-FP8 --system h100_sxm \
  --total-gpus 8 --isl 8192 --osl 256 --ttft 400 --tpot 30 \
  --database-mode SILICON --save-dir /tmp/aic_save --no-color
```

```
8 GPUs → Best Throughput 978.22 tokens/s / 122.28 per-GPU / 86.90 per-user
Request Rate 3.84 req/s / TTFT 397.92ms / TPOT 11.51ms / Request Latency 3332.24ms
parallel tp8pp1 / bs 13 / memory 10.179 / balance_score 0.051
```

产物（`--save-dir`）：`best_config_topn.csv`、`pareto.csv`、`exp_config.yaml`、
`per_ops_source.json`、`pareto_frontier.png`，以及每个候选的
`agg_config.yaml` / `sflow.yaml` / `run_0.sh` / `bench_run.sh` /
`k8s_deploy.yaml` / `k8s_bench.yaml` / `generator_config.yaml`。

### 3.3 跨后端对比（`--backend auto`）

同一 8 卡负载下，扫描器逐后端给出结果（`tokens/s/gpu`，均满足 TTFT≤400）：

| 排名 | 后端 | tokens/s/gpu | tokens/s/user | req/s | TTFT | 并发 | parallel | bs |
|---|---|---|---|---|---|---|---|---|
| 1 | sglang | **137.10** | 76.80 | 4.30 | 375.86 | 16 | tp8pp1 | 16 |
| 2 | trtllm | 122.28 | 86.90 | 3.84 | 397.92 | 13 | tp8pp1 | 13 |
| 4 | vllm | 86.65 | 36.63 | 2.72 | 395.23 | 20 | tp4pp1 | 10 |

> 注意：三个候选的 `bs` 与 `parallel` 各不相同，这是扫描器在给定卡数下同时选拓扑与批大小，
> 不能只把吞吐列拿出来横向比。

### 3.4 可复现性（`--database-mode SILICON`）

同一命令连跑两次，结果表 `diff` **完全一致**。官方也声明只有 SILICON 模式可复现，
其余（HYBRID / EMPIRICAL / SOL）"are for research purpose"。
本实验中 HYBRID 的 agg 第一名与 SILICON 相同。

### 3.5 原生回放：给定确切部署，拿分位数（`aisimulate predict`）

```yaml
engine:
  mode: aggregated
  model: Qwen/Qwen3-32B-FP8
  hardware: h100_sxm
  backend: vllm
  backend_version: current          # 必须显式给版本槽位
  workers:
    aggregated:
      parallelism: {tensor: 8, replicas: 1}
traffic:
  source: {type: synthetic, input_tokens: 8192, output_tokens: 256}
  load: {type: concurrency, concurrency: 32}
  stop: {requests: 200}
```

| 指标 | TP1（1 卡） | **TP8（8 卡）** |
|---|---|---|
| `mean_ttft_ms` | 2893.55 | **818.48** |
| `p90_ttft_ms` | 8403.07 | 2345.72 |
| **`p99_ttft_ms`** | 20296.51 | **5680.19** |
| `mean_tpot_ms` | 108.70 | **45.66** |
| **`p99_tpot_ms`** | 117.71 | **48.21** |
| `output_throughput_tok_s` | 261.09 | 630.59 |
| `request_throughput_rps` | 1.020 | 2.463 |
| `p99_e2e_latency_ms` | — | 17831.02 |

输出共 **77 个字段**，`ttft_ms` / `tpot_ms` / `itl_ms` / `e2e_latency_ms` /
`ttst_ms` / `output_token_throughput_per_user` 各有
`min / median / mean / max / std / p75 / p90 / p95 / p99` 九种统计量。

---

## 4. 口径警告：均值 ≠ P99（本轮最重要的一条）

官方 `docs/getting-started/understand-results.md` 原文：

> **`optimization.strict_sla: true`** additionally rejects a candidate when a configured
> **aggregate mean** exceeds its bound, or required completed-request counts, latency metrics,
> or sample counts are absent/invalid. **It is not a p99 constraint** and does not require
> every request to pass.

> Aggregate SLA filtering uses **mean TPOT** for the `itl_ms` bound.

> Use the saved report's **explicit field names** when comparing with a benchmark;
> **do not match columns solely by a similar label.**

对一个模型族的对照很直白（同一 8 卡配置，同一负载）：

| 来源 | 口径 | TTFT | TPOT | 对照我们的 SLA（P99 400ms / 30ms） |
|---|---|---|---|---|
| 扫描器 `best_config_topn.csv` | 单点均值型 | 397.92 ms | 11.51 ms | **看起来刚好达标** |
| 原生回放 `prediction.json` | **p99** | **5680.19 ms** | **48.21 ms** | **明确不达标** |

**结论**：如果把我们的 P99 SLA 直接传给扫描器的 `--ttft/--tpot`，再把它的输出当成
"满足 SLA 的部署建议"，就会得出与 P99 事实相反的建议。这与第四阶段
"Little 定律不得用 P99 TTFT/TPOT"是同一类错误 —— **口径必须先对齐，再比较**。

另有两个同类陷阱（官方帮助原文）：

* `--inclusive-tpot`：把 TPOT 报成 `(ttft + tpot*(osl-1))/osl`，**改变 TPOT 定义**；
* `--tpot` 只把超限配置从 topN 里过滤，**pareto 里仍可能留有超限点**。

---

## 5. 需要什么数据、要什么输入 / 出什么（要求记录第 4、5 项）

### 5.1 数据依赖：已内置，无需微基准或校准文件

* 性能库随 wheel 装在 `aiconfigurator_core/systems/`，共 **101 MB**，
  含 `h100_sxm` / `h200_sxm` / `b200_sxm` / `gb200` / `a100_sxm` 等的 **Parquet** 操作级性能表
  与 system YAML；**当前 profile 是 checked-in 的 Parquet 文件，无需 Git LFS、无需自行采集**。
* 采集器（collector）只在要新增模型/硬件时才需要，属于独立流程。
* **但"SILICON 模式"不等于 100% 实测**：随结果保存的 `per_ops_source.json` 显示，
  即便在 SILICON 模式，仍有算子被标为 `empirical`（embedding、add_norm、act_gate）
  或 `mixed`（context_attention）。
* 搜索默认还叠了**经验修正系数**（`exp_config.yaml` 原文）：
  `prefill_latency_correction: 1.1`、`decode_latency_correction: 1.08`、
  `autoscale_ttft_correction_factor: 1.8`、
  `rate_match_prefill_degradation: 0.9` / `decode: 0.92` / `encoder: 0.9`。
  **这些是启发式，不是实测**，必须随证据一起记录。

### 5.2 输入（能表达我们的需求）

| 我们需要的 | AISimulate 的字段 | 结论 |
|---|---|---|
| 模型 | `--model-path` / `engine.model` | ✅ 用 HF repo_id |
| GPU 型号 | `--system` / `engine.hardware`（`h100_sxm`） | ✅ 与我们目录的 `nvidia-h100-sxm-80gb` 对得上 |
| GPU 数量 | `--total-gpus`（或 `recommend` 反求最小卡数） | ✅ 比我们更强：能反求最小卡数 |
| ISL / OSL | `--isl` / `--osl`；`traffic.source.input_tokens/output_tokens` | ✅ |
| 并发 | `--target-concurrency`；`traffic.load.concurrency` | ✅ 与 `configured_concurrency` 同义 |
| 请求速率 | `--target-request-rate`；`traffic.load.requests_per_second` | ✅（与并发互斥，二者取一） |
| SLA | `--ttft` / `--tpot` / `--request-latency`；`evaluation.sla.{ttft_ms,itl_ms}` | ⚠️ **聚合均值口径**；原生路径另给 p99 |
| 后端框架 | `--backend {trtllm,vllm,sglang,auto}` | ✅ |
| 预算 / 成本 | — | ❌ 完全不涉及，仍由我们的 TCO 负责 |
| 精度/量化 | 由 HF `config.json` 推断，可用 `--gemm-quant-mode` 等覆盖 | ✅ |

### 5.3 输出（能产出我们要的结论）

```csv
model,isl,osl,prefix,concurrency,request_rate,bs,global_bs,
ttft,tpot,request_latency,encoder_latency,encoder_memory,
seq/s,seq/s/gpu,tokens/s,tokens/s/gpu,tokens/s/user,
num_total_gpus,tp,pp,dp,moe_tp,moe_ep,cp,parallel,
gemm,kvcache,fmha,moe,comm,memory,balance_score,
num_ctx_reqs,num_gen_reqs,num_tokens,ctx_tokens,gen_tokens,
backend,version,system,power_w,tokens/s/gpu_cluster
```

原生路径另有 `prediction.json`（77 字段，含 p99）与可选 `requests.jsonl` 逐请求明细。

---

## 6. 预测误差：官方发布的证据（要求记录第 6 项）

来源：官方发布的 E2E 精度快照 `https://ai-dynamo.org/aisimulate/e2e-accuracy/summary.json`
（`schema_version: 1`，`latency_scope: client_observed`，`measurement_scope: end_to_end`）。

| 指标 | AISimulate | AIC（旧 CLI） |
|---|---|---|
| 成功点数 | 1584（另有 487 失败） | 1298（另有 773 失败） |
| 覆盖率 | 76.48% | 62.68% |
| **TTFT MAPE** | **45.27%** | 48.02% |
| **TPOT MAPE** | **22.39%** | 22.25% |
| TTFT 曲线形状误差 | 33.20% | 38.41% |
| TPOT 曲线形状误差 | 11.83% | 12.05% |

**必须记录的三条限定**：

1. 快照里的模型名单（21 个）**不含 `Qwen3-32B`** —— 搜遍快照原文，
   `Qwen3-32B` 一次都没出现（只有 `Qwen/Qwen3.5-397B-A17B[-FP8]` 与 NVFP4 变体）。
   也就是说：**对我们的目标模型，没有任何公开精度证据**。
2. 覆盖的 GPU SKU 为 `b200/b300/gb200/gb300/h100/h200`，框架为 `dynamo-* / sglang / trt / vllm`，
   但"支持"与"有精度证据"是**两件事**（官方原文：
   "A supported cell means a specific path can execute with the required data;
   it does not establish that the resulting end-to-end prediction is accurate."）。
3. 快照自带声明："Accuracy applies only to the exact measured operating points in this
   snapshot; it is not universal model, hardware, or deployment support."
   按配置质量分组看，"verified"子集反而更差（TTFT 43.97% / TPOT 57.6%，样本仅 32 点）。

**TTFT MAPE 约 45% 意味着**：把模拟值当成"能保证的时延"是不可接受的；
它只适合用来**排序候选**与**发现明显不可行的配置**。

---

## 7. 已知限制（要求记录第 6 项）

1. **两套接口口径不同**（§4），且官方明确 is not a p99 constraint。
2. **vLLM / SGLang 支持仍在评估中**（官方 Known Issues 第 3 条）：
   "we are still completing comprehensive performance evaluations and alignment testing.
   We recommend validating results with real benchmarks for production use."
3. **内存估算待完善**（Known Issues 第 1 条）；**高吞吐低速区结果可能偏乐观**（第 2 条）。
4. **版本槽位限制**：某些后端必须显式给版本槽（`current`=0.24.0 / `next`=0.27.0）
   或设 `AIC_ALLOW_UNLISTED_VERSIONS=1`，否则**直接失败**（本轮实测错误见
   `10_native_predict_version_slot_error.txt`）。
5. **原生路径的 `parallelism` 与推荐路径的 schema 不同**：预测路径用
   `{replicas,tensor,pipeline,moe_tensor,moe_expert}`（不接受 `preset`），
   推荐路径用 `{preset|独立旋钮}`。跨路径复用配置必须做映射
   （实测错误见 `11_native_predict_parallelism_error.txt`）。
6. **回放结果依赖流量窗口**：官方原文明确"a cutoff is not a measurement warmup period
   or proof that the requested load was sustainable"，p99 会受预热与截断影响。
7. **`disagg` 在 SILICON + 严格 SLA 下可能返回空**（本轮即是），需要放宽约束或换 HYBRID。
8. 观察到的告警：本机加载时出现 "Legacy perf-data layout" 与
   "low-fidelity fallback rows for mla_bmm_perf.parquet ... from sglang/0.5.14"
   —— **跨后端借用了低保真数据行**。这类告警必须被采集并当作证据质量标记，
   不能因为"命令成功"就当无副作用。
9. 不涉及成本、不涉及商务确认、不涉及报价 —— 我们的 TCO / 门禁层完全保留。

---

## 8. 字段映射表（要求记录第 4 项）

### 8.1 输入侧：`PlanRequest` → AISimulate

| 我们的字段 | AISimulate（原生 `predict`） | AISimulate（扫描器 CLI） | 备注 |
|---|---|---|---|
| `model_id` / repo_id | `engine.model`（HF repo_id） | `--model-path` | 需把 `qwen3-32b` 解析成 `Qwen/Qwen3-32B`（复用 `_model_repo_id`） |
| `hardware_ids[i]` | `engine.hardware` | `--system` | 我们目录 `nvidia-h100-sxm-80gb` → `h100_sxm` 需一张映射表 |
| `advanced.tp_sizes/dp_sizes` | `workers.aggregated.parallelism.tensor/pipeline` | `--total-gpus` + 扫描空间 | 扫描器自己枚举，不需要我们给 |
| `workload.input_len` | `traffic.source.input_tokens` | `--isl` | 直接对应 |
| `workload.output_len` | `traffic.source.output_tokens` | `--osl` | 直接对应 |
| `workload.concurrency` | `traffic.load.concurrency` | `--target-concurrency` | 同义 |
| `workload.num_requests` | `traffic.stop.requests` | — | 扫描器不接请求数 |
| `sla.ttft_p99_ms_max` | `evaluation.sla.ttft_ms` ⚠️ | `--ttft` ⚠️ | **两边都是均值口径**；P99 需从 `prediction.json` 的 `p99_ttft_ms` 事后判定 |
| `sla.tpot_p99_ms_max` | `evaluation.sla.itl_ms` ⚠️ | `--tpot` ⚠️ | 同上；`p99_tpot_ms` 事后判定 |
| `resources.usable_fraction` | — | `--free-gpu-memory-fraction` | 语义不同，不可直接映射 |
| `cost` / `demand` / `budget` / `governance` | — | — | AISimulate 不涉及 |

### 8.2 输出侧：AISimulate → `PlanCandidate`

| AISimulate | 我们的字段 | 备注 |
|---|---|---|
| `num_total_gpus` / `total_gpus_needed` | `gpu_count` | 直接对应 |
| `tp` / `pp` / `dp` | `tp_size` / `dp_size`（+ 新增 `pp_size`） | 我们需要补 `pp_size` |
| `parallel`（`tp8pp1dp1etp1ep1`） | 同上（解析串） | 便于原样存档 |
| `bs` / `global_bs` | 新增 `sim_batch_size` | 我们没有这个概念 |
| `tokens/s` | 新增 `sim_output_throughput_tok_s` | 集群级（不是单卡） |
| `tokens/s/gpu` | 新增 `sim_tokens_per_gpu` | 用于跨卡数比较 |
| `tokens/s/user` | 新增 `sim_tokens_per_user` | 交互质量指标 |
| `ttft`（均值型） | 新增 `sim_mean_ttft_ms` | **不得**写进 `ttft_p99_ms` |
| `tpot`（均值型） | 新增 `sim_mean_tpot_ms` | 同上 |
| `p99_ttft_ms` / `p99_tpot_ms`（原生） | 新增 `sim_p99_ttft_ms` / `sim_p99_tpot_ms` | 唯一可与 SLA 对照的量 |
| `backend` / `version` | 新增 `sim_backend` / `sim_backend_version` | **证据身份的一部分** |
| `system` | `hardware_id`（需反查映射） | |
| `gemm` / `kvcache` / `fmha` / `moe` / `comm` | 新增 `sim_quant_modes` | 我们目前不建性能模型，但要存档 |
| `memory` / `balance_score` / `power_w` | 新增（可选） | 内存与功耗是它独有的信号 |
| `per_ops_source.json` | 新增 `sim_data_provenance` | silicon / mixed / empirical 逐算子来源 |
| `exp_config.yaml` | 新增 `sim_search_space` + 修正系数 | 可复现性所必需 |
| `prediction.json` 的 `num_*/completed_requests` | 新增 `sim_sample_counts` | 样本不足时 p99 无意义，必须一起存 |
| — | `data_trust` = **`simulated_prediction`**（新等级） | 与 `measured_*` 严格分离 |

> **映射原则**：模拟量**只进 `sim_*` 前缀字段**，**永不**写进
> `ttft_p99_ms` / `output_throughput_tok_s` / `BenchmarkRun` / `CapacityPoint`，
> 因此不可能被 SLA 判定器或报价门禁当成实测采纳。

---

## 9. 原始输出清单（要求记录第 3 项）

目录 `docs/poc/aisimulate/`：

| 文件 | 内容 | sha256（前 16 位） |
|---|---|---|
| `01_recommend_qwen3_32b_h100_sxm_silicon.txt` | `recommend` 完整输出（最小卡数 24） | `83372962dc81d1c8` |
| `02_default_gpus8_silicon.txt` | `default` 8 卡完整输出 | `9607d15f03914bb5` |
| `03_backend_auto_gpus8.txt` | 三后端对比 | `e174b8889ac2a2fb` |
| `04_hybrid_gpus8.txt` | HYBRID 模式对照 | `b476f65e886a45a0` |
| `06_native_predict_input_single_gpu.yaml` | 原生预测输入（单卡） | `dbcf5c544fc3f71c` |
| `07_native_predict_input_tp8.yaml` | 原生预测输入（TP8） | `b01c86a7f112b362` |
| `08_native_predict_result_single_gpu.json` | 原生预测结果（单卡，77 字段） | `140e46b1f4b4e5d2` |
| `09_native_predict_result_tp8.json` | 原生预测结果（TP8，含 p99） | `a45bb4f4fab4d061` |
| `10_native_predict_version_slot_error.txt` | 版本槽位缺失导致的失败原文 | `ae9016021f253f7b` |
| `11_native_predict_parallelism_error.txt` | parallelism schema 不匹配的失败原文 | `3c8df2418ce808aa` |
| `artifacts/best_config_topn.csv` | 结构化 topN 结果 | `d5db7882a6bb912e` |
| `artifacts/pareto.csv` | Pareto 前沿点 | `ed35384dd5c27c06` |
| `artifacts/exp_config.yaml` | **完整搜索空间 + 实际后端版本 + 修正系数** | `da61659831f6d210` |
| `artifacts/per_ops_source.json` | 逐算子数据来源（silicon/mixed/empirical） | `a6c7ccc5ccd8e930` |

环境记录：`aisimulate 0.12.1`、Python 3.12.3、venv `/tmp/aisim-venv`（约 1.5 GB）、
执行机器**无 GPU**、`nvidia-smi` 不存在。

---

## 10. N4：是否集成的技术决策

### 10.1 决策：**值得集成，条件是"只做候选枚举 + 分位数预测，且严格分层"**

**支持集成的理由**

1. 它正好补上我们唯一的重大能力缺口：**给定模型+硬件+负载+SLA，自动搜索部署配置**。
   我们目前只能对**给定的** TP/DP 组合做静态可行性，不能搜索也不能预测性能。
2. 目标组合（Qwen3-32B / H100 SXM）**原生支持**，三个后端都能算。
3. **无 GPU 可跑、纯离线**，与本仓库"离线可复现"的交付纪律一致；
   SILICON 模式实测**可复现**（两次结果逐字相同）。
4. 原生回放给出 **p99**，与我们的 SLA 口径**可对齐** —— 这是它相对其他方案最有价值的一点。
5. 接口干净：`--format json` + `--output-dir`，产物是文件而不是需要我们解析终端。

**不集成的部分（必须明确划掉）**

1. **不采纳它的 SLA 结论**。它的严格 SLA 过滤是**聚合均值**口径，
   官方原文写明 "It is not a p99 constraint"；我们的 SLA 是 P99。
   因此我们只取它的**候选枚举与分位数**，达标与否**由我们自己的规则判**。
2. **不把模拟结果当实测**。`data_trust` 新增 `simulated_prediction`，
   与 `measured_internal` / `measured_vendor_published` / `measured_third_party` 并列但**不可互相升级**；
   模拟结果**永不**写入 `BenchmarkRun` / `CapacityPoint`，**永不**获得 `observed_pass`。
3. **不因它重写现有模块**。Sizing / TCO / Revenue / Governance / Reporting 全部保留；
   它是**性能预测领域的可选外部适配器**，缺省不启用。
4. **不在本轮实现**（N4 与总约束共同要求）。

### 10.2 为什么"候选枚举"和"分位数预测"要分开用

| 用途 | 用哪个接口 | 为什么 |
|---|---|---|
| 枚举 / 排序候选配置 | 扫描器（`cli default/recommend`，SILICON） | 它就是这个用途：搜索 thousands of configs，按吞吐/Pareto 排序 |
| 判定 P99 是否达标 | 原生回放（`aisimulate predict`） | 只有它给 p99；扫描器给的是均值型单点 |
| 成本 / 报价 / 商务 | **我们的 TCO + 门禁** | AISimulate 完全不涉及 |

也就是说，集成形态是一个**两段式管线**：
`扫描器给候选 → 我们的 TCO 算成本 → 原生回放给短名单的 p99 → 我们的 SLA 规则判定`。
其中**只有最后一步能说"达标"**。

---

## 11. 下一阶段最小实施方案（供你确认后再执行）

**目标**：让 `evaluate_plan()` 在**显式开启**时，能引用 AISimulate 的候选枚举与 p99 预测，
并与现有四领域结论**并列展示**（不混合、不覆盖）。

**范围（刻意小）**

1. **一个可选适配器**：`src/llm_capacity_pricing/perf/aisimulate_adapter.py`
   * 只做两件事：调外部命令、把输出映射到一批 `sim_*` 字段；
   * 外部命令**不存在或失败时返回 `not_configured`/`error`**，绝不影响其他领域；
   * 通过 `PERFORMANCE_ADAPTERS` 查找表注册（S4 已有的扩展边界，不新建架构）。
2. **一个新数据分级**：`DataTrust` 增加 `simulated_prediction`
   （**只在 `schemas/performance.py` 那一处权威定义里加**，不散落字面量）。
3. **一个新领域状态**：`PlanResult.domains` 增加 `simulation`
   （`not_configured` / `computed` / `missing_data` / `error`）；
   报告新增一节"## 2.2 模拟预测（**不是实测**）"，与公开参考并列、与候选分开。
4. **配置开关**：`PlanRequest.simulation`（可选）；
   不给就完全不调用外部工具，现有行为与输出**逐字节不变**。
5. **证据纪律**（沿用本仓库既有做法）
   * 把外部工具的**版本 + 后端版本 + 数据库模式 + 搜索空间 + 修正系数 + 逐算子来源**
     一起存档（`exp_config.yaml` / `per_ops_source.json` 已经天然提供）；
   * 记录**p99 与均值的差**，并在报告里显式提示"均值口径会高估达标情况"；
   * 反向用例：模拟结果**不得**进入 `BenchmarkRun`；**不得**产生 `observed_pass`；
     外部工具失败时**不得**影响其它领域结论。

**验收标准**

* 未配置 `simulation` 时：现有 784 项测试与 63 步链路**完全不变**；
* 配置后：报告能并列给出"实测证据 / 公开参考 / 模拟预测"三类，且三类互不污染；
* 任何模拟数值都能追溯到：工具版本、后端版本、数据库模式、搜索空间、p99 样本数；
* 至少一条反例测试证明"模拟结果不会变成 SLA 证据"。

**工作量估计**：适配器 + 分级 + 报告 + 反向用例，约等于此前一个 M 编号任务的规模。
**前置条件**：`aisimulate` 需在目标环境可安装（1.5 GB venv）——建议做成
**可选依赖**，而不是写进主 `requirements.lock`。

---

## 12. 本轮明确不做的事

* 不改动 Day-1 的任何计算模块（Sizing / TCO / Revenue / Governance / Reporting 全部原样）；
* 不把模拟结果写成 `BenchmarkRun`，不产生 `observed_pass`；
* 不据此重写现有计算模块；
* 不开发 FastAPI / Web 前端 / 完整 AI Agent；
* 不因为"工具能跑"就声称 Qwen3-32B @ H100 的性能已经得到验证 ——
  **它没有**：官方精度快照里根本没有这个模型，全量 TTFT MAPE 约 45%。

---

## 13. 附：可用的实测硬件（RTX 3090）能补上什么

你提供了一个可 SSH 的机器（`10.0.102.61` / `compute01`）。已做**只读**盘点：

| 项 | 实况 |
|---|---|
| GPU | 1× NVIDIA GeForce RTX 3090，24 GB（空闲，占用 445 MiB） |
| 驱动 / CUDA | 12.8 |
| Python | 3.10.12 |
| 已装 | `torch 2.11.0+cu128`、`triton 3.6.0` |
| **未装** | **vLLM、SGLang 都没有** |
| 资源 | 16 核 / 15 GB 内存（可用约 12 GB）/ 根分区剩余 **17 GB**（已用 83%） |

### 它能做和不能做的（如实划清）

**不能做**：

* **不能验证 AISimulate 的预测**：3090 不在 AISimulate 的支持矩阵里
  （只有 `h100_sxm / h200_sxm / b200_sxm / gb200 / a100_sxm / h100_pcie / a100_pcie /
  l4 / a30 / l40s / b300_sxm / gb300`），没有它的性能表，算不了它。
* **不能为我们的目标配置提供 SLA 证据**：我们的目标是 H100 SXM，
  3090 是另一个硬件身份，匹配器会（也应该）判为不同型号；
  我们目录里也没有这个 SKU。
* **跑不动 Qwen3-32B**：bf16 权重约 64 GB、FP8 约 32 GB，都超过 24 GB。

**能做、而且是目前最有价值的一件事**：

> 用**真实测量**跑通我们那条至今只靠 synthetic fixture 验证过的实测管线：
> `plan-benchmarks → import-performance → check-import → attest-run → validate-site`。

这条链路上的所有环节（客户端 JSONL 解析、P99 口径一致性、成功数/失败数核算、
并发语义、重复观测去重、attest 只比哈希不认证身份）目前都只有构造数据在验证。
在一台真机、真框架、真请求上跑一遍，能暴露构造数据永远暴露不了的问题
（例如真实 `vllm bench serve` 输出的字段名与百分位定义、warmup 行为、
`--enforce-eager` 对 P99 的影响）。**它是管线验证，不是目标模型验证**，
两者必须在报告里分开写。

### 启动它需要你确认的三件事（所以我先不动手）

1. **在 compute01 上安装 vLLM**（约 5–10 GB；该机根分区只剩 17 GB，偏紧）——
   这是对一台非本机的外部写操作，需要你明确授权；
2. **是否允许在目录里新增一个非目标 SKU**（如 `rtx-3090`），并明确标注
   `not_a_production_sku` / 不可用于生产 SLA 结论；
3. **用哪个小模型**（3090 装得下且公开可下载，例如 Qwen3-8B 级别的 FP8/AWQ）。

如果你同意，我建议把它作为 **N6（可选）实测管线验证**，
排在 N4 的适配器方案之后 —— 它能给"模拟预测"提供第一个真实校准点，
也能第一次验证我们自己的实测链路。
