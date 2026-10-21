> 演示案例（成本与预算）：使用仓库内合成成本示例（非真实报价）。成本项覆盖完整，但价格为合成、币种未确认，因此不给出正式“成本最优”结论；成本排序降级为吞吐优先，预算结论保持未知。

# 方案评估报告

- 请求：`demo-cost`
- 模型：`qwen3-32b`
- 候选硬件：['nvidia-h100-sxm-80gb']
- 目标负载：输入 8192 / 输出 256 / 并发 32
- 生成时间：2026-10-21T04:30:35+00:00

## 1. 结论摘要

整体状态：**部分结论**（只表示这次评估产出了什么，不是报价结论）

| 领域 | 状态 | 说明 |
|---|---|---|
| 输入与库存 | 已评估 | |
| 资源规划 | 已评估 | |
| 性能与 SLA | 已评估 | |
| 成本与收益 | 已评估 | |
| 报价资格 | 未配置（缺少输入） | |

- **技术资格（四维门禁）**：无候选通过。这是成本/SLA/商务确认/审批四项证据计算的**计算结果**。
- **生产发布授权**：**未授权**。本系统未接入可信审批系统，审批记录只做持久化、不校验审批人身份与权限，因此**不得用于生产环境的报价自动发布**（正式报价须由人工在受控流程中签发）。

**本报告里的数字分五层，请勿跨层混用：**

| 层 | 含义 | 怎么读 |
|---|---|---|
| 静态资源可行 | 按模型参数与显存算出的可行性 | 只说明「装不装得下」，不含性能 |
| 公开性能参考 | 官方/第三方**公开发布**的实测（第 2.1 节） | 了解业界水平，**不用于**本需求判定 |
| 已验证 SLA | 只有**目标配置下**、可信来源的实测才算 | 没有就是「证据不足」，不猜、不外推 |
| 假设成本 | 示例成本目录算出的金额 | 不是报价；本 Demo 中不完整 |
| 正式报价资格 | 成本 / SLA / 商务确认 / 审批四维门禁 | 当前恒为「未授权发布」 |

## 2. 候选方案

| 候选 | 卡数 | TP×DP | 静态 | 性能/SLA | 性能数据性质 | 证据匹配等级 | 成本(全周期) | 成本口径 | 预算 | 报价 |
|---|---|---|---|---|---|---|---|---|---|---|
| `qwen3-32b__nvidia-h100-sxm-80gb__tp4_dp1_bf16` | 4 | 4×1 | 条件可行（需按假设复核） | 证据不足 | 证据不足 | 仅可建模（非本配置） | — CNY | 不完整（含未确认/缺失项） | unknown | ⛔ 不可发布 |
| `qwen3-32b__nvidia-h100-sxm-80gb__tp8_dp1_bf16` | 8 | 8×1 | 条件可行（需按假设复核） | 证据不足 | 证据不足 | 仅可建模（非本配置） | — CNY | 不完整（含未确认/缺失项） | unknown | ⛔ 不可发布 |
| `qwen3-32b__nvidia-h100-sxm-80gb__tp2_dp1_bf16` | 2 | 2×1 | 不可行 | 未评估 | 证据不足 | — | — CNY | 不完整（含未确认/缺失项） | unknown | ⛔ 不可发布 |

- `qwen3-32b__nvidia-h100-sxm-80gb__tp4_dp1_bf16`：成本结论不完整，不得据此报价
- `qwen3-32b__nvidia-h100-sxm-80gb__tp8_dp1_bf16`：成本结论不完整，不得据此报价
- `qwen3-32b__nvidia-h100-sxm-80gb__tp2_dp1_bf16`：成本结论不完整，不得据此报价

## 2.1 公开数据参考（**不参与**本需求的性能判定）

库里共有 **13 条**官方/第三方**公开发布**的实测。它们用来看同类配置在业界大概什么水平，**不是**你要部署的配置的实测依据：不参与 SLA 判定，也不进报价门禁。

> 排序：匹配等级 → 与目标相同的条件数 → 输入长度接近程度。比较基准取你的候选方案中**与该条数据差异最小**的一个，因此下面列的是「最有利情况下」仍然存在的差异。完整 13 条见第 7 节附录，结构化数据见 JSON 结果。

**所有 13 条都有、因此下面不再逐条重复的缺口：**

- 公开来源未给出模型版本（revision）：无法确认这条公开数据就是你要部署的配置
- 公开来源未给出DP（数据并行）：无法确认这条公开数据就是你要部署的配置
- 你的目标需求也未给出模型版本（revision）：因此这条公开数据最多只能作参考，不能当作该配置下的实测

### 最相关的 3 条

| # | 来源 | 模型 | GPU | 卡数 | TP/DP | 框架 | ISL/OSL | 并发 | TTFT P99 | TPOT P99 | 输出吞吐 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | MG-Cafe（GitHub 社区实验报告） | Qwen/Qwen3-32B | NVIDIA H100 80GB HBM3 | 4 | 4/— | vllm 0.17.0 | 31400/128 | — | 359749.43 | 154.03 | 69.87 |
| 2 | sgl-project/sglang | Qwen/Qwen3-235B-A22B-Instruct-2507 | NVIDIA B200 | 8 | 8/— | sglang 0.5.6 | 8000/1000 | 1 | 225.17 | 10.51 | 93.94 |
| 3 | sgl-project/sglang | Qwen/Qwen3-235B-A22B-Instruct-2507 | NVIDIA B200 | 8 | 8/— | sglang 0.5.6 | 8000/1000 | 16 | 1027.21 | 27.18 | 820.69 |

#### 1. `public::community_qwen3_32b_h100_4gpu_report:h100-4gpu-random-32k`

- **与目标相同的条件**：模型=Qwen/Qwen3-32B；权重精度=bf16；卡数=4；TP（张量并行）=4；推理框架=vllm；数据集=random；请求到达方式=unbounded
- **不能直接比较的条件**：
  - GPU 型号不同：你的目标是 NVIDIA H100 SXM，公开数据是 NVIDIA H100 80GB HBM3
  - 框架版本不同：你的目标是 0.24.0，公开数据是 0.17.0
  - 负载长度不同：你的目标是 8192/256，公开数据是 31400/128（时延与吞吐随长度变化，不能直接套用）
  - 公开来源未给出配置并发：无法确认这条公开数据就是你要部署的配置

- 匹配等级：`rejected`
- 来源：MG-Cafe（GitHub 社区实验报告）｜https://github.com/MG-Cafe/TPU_V6e_H100_Benchmarking_Qwen/blob/main/results/GPU_BENCHMARK_REPORT.md
- 存档证据：`data/raw/official/community_qwen3_32b_h100_4gpu_report_2026-10-15.md`（sha256 `422eec419cd8fd542aa8b969519b4bf86a67e56b8a28bc3333ab61a844eb96f6`）
- 数据可信等级：`measured_third_party`

#### 2. `public::sglang_qwen3_cookbook:5.1.3.1`

- **与目标相同的条件**：卡数=8；TP（张量并行）=8；数据集=random；请求到达方式=unbounded
- **不能直接比较的条件**：
  - 模型不同：你的目标是 Qwen/Qwen3-32B，公开数据是 Qwen/Qwen3-235B-A22B-Instruct-2507
  - GPU 型号不同：你的目标是 NVIDIA H100 SXM，公开数据是 NVIDIA B200
  - 推理框架不同：你的目标是 vllm，公开数据是 sglang
  - 框架版本不同：你的目标是 0.24.0，公开数据是 0.5.6
  - 负载长度不同：你的目标是 8192/256，公开数据是 8000/1000（时延与吞吐随长度变化，不能直接套用）
  - 配置并发不同：你的目标是 32，公开数据是 1
  - 公开来源未给出权重精度：无法确认这条公开数据就是你要部署的配置

- 匹配等级：`rejected`
- 来源：sgl-project/sglang｜https://github.com/sgl-project/sglang/blob/main/docs/cookbook/autoregressive/Qwen/Qwen3.mdx
- 存档证据：`data/raw/official/sglang_cookbook_qwen3_2026-10-15.mdx`（sha256 `23d8e61ce899920e5606ca80c30c292a88cab02321c1bd3c610a19dba0b27425`）
- 数据可信等级：`measured_vendor_published`

#### 3. `public::sglang_qwen3_cookbook:5.1.3.2`

- **与目标相同的条件**：卡数=8；TP（张量并行）=8；数据集=random；请求到达方式=unbounded
- **不能直接比较的条件**：
  - 模型不同：你的目标是 Qwen/Qwen3-32B，公开数据是 Qwen/Qwen3-235B-A22B-Instruct-2507
  - GPU 型号不同：你的目标是 NVIDIA H100 SXM，公开数据是 NVIDIA B200
  - 推理框架不同：你的目标是 vllm，公开数据是 sglang
  - 框架版本不同：你的目标是 0.24.0，公开数据是 0.5.6
  - 负载长度不同：你的目标是 8192/256，公开数据是 8000/1000（时延与吞吐随长度变化，不能直接套用）
  - 配置并发不同：你的目标是 32，公开数据是 16
  - 公开来源未给出权重精度：无法确认这条公开数据就是你要部署的配置

- 匹配等级：`rejected`
- 来源：sgl-project/sglang｜https://github.com/sgl-project/sglang/blob/main/docs/cookbook/autoregressive/Qwen/Qwen3.mdx
- 存档证据：`data/raw/official/sglang_cookbook_qwen3_2026-10-15.mdx`（sha256 `23d8e61ce899920e5606ca80c30c292a88cab02321c1bd3c610a19dba0b27425`）
- 数据可信等级：`measured_vendor_published`

## 2.2 模拟部署配置推荐（**非实测**）

模拟状态：**已计算**

- 工具：`aisimulate 0.12.1`（配置扫描器 + 原生回放），后端 `vllm 0.24.0`，硬件库 `h100_sxm`，数据库模式 `SILICON`
- 搜索空间存档：`docs/poc/aisimulate/artifacts/matched_exp_config_vllm_bf16.yaml`（sha256 `c1afd887bf591f518abf7ebb19c5c61aad20185d003539ed32af845cfe53a54a`）
- 叠加的经验修正系数（**非实测**）：`autoscale_ttft_correction_factor=1.8`、`decode_latency_correction=1.08`、`encoder_latency_correction=1.0`、`prefill_latency_correction=1.1`、`rate_match_decode_degradation=0.92`、`rate_match_encoder_degradation=0.9`、`rate_match_prefill_degradation=0.9`

- 排序目标：**成本优先**（多候选都满足时按此目标取一个，不提供含糊的「全局最优」）
- 成本排序**不可用**：1 个候选的成本不完整或金额缺失（如 sim-native:Qwen/Qwen3-32B@h100_sxm-tp8-c32-n500）：无法与其他候选比较成本。**已降级为吞吐优先**（仍然只从预测达标的候选里选），且**不得**称为成本最优

**推荐配置（模拟）**：`sim-native:Qwen/Qwen3-32B@h100_sxm-tp8-c32-n500`

| 项 | 值 |
|---|---|
| 模型 | Qwen/Qwen3-32B（bf16） |
| GPU | h100_sxm × 8 |
| 并行 | TP8 × PP— × DP—，副本 1 |
| 部署模式 | 聚合部署 |
| 负载 | 输入 8192 / 输出 256 / 并发 32 |
| 模拟输出吞吐 | 990.888548064229 tok/s（123.861 tok/s/GPU） |
| 模拟 TTFT P99 | 5201.827252000001 ms（均值 548.0465038100006） |
| 模拟 TPOT P99 | 31.45861707450993 ms（均值 30.065770863803973） |
| 预测结论 | 预测满足阈值（模拟） |
| 静态可行性（Sizing） | 可行（8 卡） |
| GPU 上限 | 在范围内 |
| 预算 | 无法判定（成本不完整或币种未确认） |

> 这里的 TTFT / TPOT 是**性能模拟**的预测值，不是实测，也不与第 2 节的实测证据合并。

- 资源校验：静态结论 conditional_feasible（Sizing 口径 8 卡）
- 预算：cost_incomplete: 成本不完整，不能据此给预算结论
- 记录说明：模型精度 bf16 由**输入身份**给出（原生回放输出不回显精度，它按 HF config 自行推断）

- **成本（本仓库 TCO 计算，不是模拟值）**：**不完整，不给总额**
  - 成本结论不完整：含未确认/缺失项

**其它候选（截断展示）**：

| 候选 | GPU | 并行 | 并发 | 吞吐 tok/s | TTFT P99 | TPOT P99 | 结论 |
|---|---|---|---|---|---|---|---|
| `sim-sweeper:Qwen/Qwen3-32B@h100_sxm-tp8pp1dp1etp1ep1-c32-ctx8192` | 8 | TP8×PP1×DP1 | 32 | 997.419 | — | — | 预测不足以下结论 |
| `sim-sweeper:Qwen/Qwen3-32B@h100_sxm-tp8pp1dp1etp1ep1-c32-ctx16384` | 8 | TP8×PP1×DP1 | 32 | 966.671 | — | — | 预测不足以下结论 |

**本节结论的限制**：

- 扫描器输出**不含 P99**（只有稳态单点估计，实测约等于原生回放的中位数）：因此扫描器候选一律无法给出 P99 判定，需要原生回放补测
- 扫描器候选共 51 行，报告只展示其中的 2 行（优先展示能与目标直接比较的工作点）
- 同时使用了配置扫描器与原生回放两种输出：两者口径不同（扫描器=稳态单点，原生=含尾部的分布），只**分别列示**，不合并为同一结论
- 即使 SILICON 模式也含经验算子 8 个（例如 genonly_step.generation_act_gate, genonly_step.generation_add_norm_1, genonly_step.generation_add_norm_2 ...）：不是全部来自实测
- 搜索默认叠加了经验修正系数（非实测）：autoscale_ttft_correction_factor=1.8, decode_latency_correction=1.08, encoder_latency_correction=1.0, prefill_latency_correction=1.1, rate_match_decode_degradation=0.92, rate_match_encoder_degradation=0.9, rate_match_prefill_degradation=0.9
- 模拟结论**不是实测**：不得写入 BenchmarkRun / CapacityPoint，不得产生 observed_pass / observed_fail
- 与第 2 节**实测证据**的区别：实测只认目标配置下、可信来源的测量结果；
  本节全是预测，`predicted_*` 与 `observed_*` 是两套取值，不会互相转换。

## 3. 推荐依据

- 资源占用最小的静态可行候选：`qwen3-32b__nvidia-h100-sxm-80gb__tp4_dp1_bf16`（4 卡，TP4×DP1）—— 这是**资源**结论，不含性能与经济性判断
- 没有候选具备匹配实测（SLA 全部不可评估）：这属于**证据缺口**，需要用目标配置补测，不能由估算替代
- 没有任何候选具备**完整且金额已知**的成本结论：本结果不给出成本高低结论
- 性能证据分布：静态不可行 1 个、需要补测 2 个
- 本结果**不包含**跨维度综合评分：成本、性能与报价资格三者独立

## 3.1 成本可比性

参照方案：`无`
**可比较候选**：无

**不可比较候选**（不参与成本高低比较）：

- `qwen3-32b__nvidia-h100-sxm-80gb__tp2_dp1_bf16`：成本不完整或金额缺失（不得按 0 排序）
- `qwen3-32b__nvidia-h100-sxm-80gb__tp4_dp1_bf16`：成本不完整或金额缺失（不得按 0 排序）
- `qwen3-32b__nvidia-h100-sxm-80gb__tp8_dp1_bf16`：成本不完整或金额缺失（不得按 0 排序）

## 4. 缺口与下一步

**缺少的输入：**

- governance: 未给出 SLA 证据与审批引用，因此不生成报价资格结论

**建议补充：**

- 如需报价资格：先 record-sla-evidence 生成证据、import-quote-approvals 导入审批，再在 governance 中引用其 ID

## 5. 结论边界

- 结论按领域独立给出：静态可行 ≠ 性能达标 ≠ 报价可发布
- 未提供的数据一律标为未配置/缺失，不使用默认值补齐
- 治理领域未产出结论：正式报价资格未被评估（默认不可发布）

## 6. 附录：领域明细

> 详细指标、证据哈希、匹配等级与逐候选阻断原因用于复核，普通使用可以忽略本节。

### qwen3-32b__nvidia-h100-sxm-80gb__tp4_dp1_bf16

- 资源：4 卡 / TP4×DP1 / 服务器 1 台 / 静态余量 28904316928 B
- 静态结论：conditional_feasible
- 性能：匹配等级 仅可建模（非本配置），SLA not_evaluable，分桶 requires_benchmark
  - 输出吞吐 1131.55 tok/s，TTFT p99 153161.51 ms，TPOT p99 378.18 ms（**非本配置实测**，见下）
  - 依据实测：['public::gpustack_perf_lab_qwen3_32b_h100:vllm-sharegpt']
  - qwen3-32b × NVIDIA H100 SXM：TP4 × DP1 = 4 卡 → 条件可行
  - 性能数字来自公开数据 `public::gpustack_perf_lab_qwen3_32b_h100:vllm-sharegpt`（Qwen/Qwen3-32B @ NVIDIA H100 SXM ×1 / TP 未声明 / vllm 0.11.0 / sharegpt 长度未声明，匹配等级 **modeling_candidate**）：该数据**不是**本配置的实测，只能作参考，不得用于 SLA 判定或容量结论；来源：https://docs.gpustack.ai/2.0/performance-lab/qwen3-32b/h100/
- 成本：方案 `syn-owned-3y::qwen3-32b__nvidia-h100-sxm-80gb__tp4_dp1_bf16`（owned） 一次性 880000 / 年现金 61339.7760 / 年总成本 325339.7760

### qwen3-32b__nvidia-h100-sxm-80gb__tp8_dp1_bf16

- 资源：8 卡 / TP8×DP1 / 服务器 1 台 / 静态余量 45295656960 B
- 静态结论：conditional_feasible
- 性能：匹配等级 仅可建模（非本配置），SLA not_evaluable，分桶 requires_benchmark
  - 输出吞吐 1131.55 tok/s，TTFT p99 153161.51 ms，TPOT p99 378.18 ms（**非本配置实测**，见下）
  - 依据实测：['public::gpustack_perf_lab_qwen3_32b_h100:vllm-sharegpt']
  - qwen3-32b × NVIDIA H100 SXM：TP8 × DP1 = 8 卡 → 条件可行
  - 性能数字来自公开数据 `public::gpustack_perf_lab_qwen3_32b_h100:vllm-sharegpt`（Qwen/Qwen3-32B @ NVIDIA H100 SXM ×1 / TP 未声明 / vllm 0.11.0 / sharegpt 长度未声明，匹配等级 **modeling_candidate**）：该数据**不是**本配置的实测，只能作参考，不得用于 SLA 判定或容量结论；来源：https://docs.gpustack.ai/2.0/performance-lab/qwen3-32b/h100/
- 成本：方案 `syn-owned-3y::qwen3-32b__nvidia-h100-sxm-80gb__tp8_dp1_bf16`（owned） 一次性 1680000 / 年现金 101747.0720 / 年总成本 605747.0720

### qwen3-32b__nvidia-h100-sxm-80gb__tp2_dp1_bf16

- 资源：2 卡 / TP2×DP1 / 服务器 1 台 / 静态余量 -3878363136 B
- 静态结论：infeasible
  - 每卡需求 75878363136 字节 > 可用 72000000000 字节（超出 3878363136 字节）
- 性能：匹配等级 —，SLA —，分桶 excluded
  - 输出吞吐 — tok/s，TTFT p99 — ms，TPOT p99 — ms
  - qwen3-32b × NVIDIA H100 SXM：TP2 × DP1 = 2 卡 → 不可行
- 成本：方案 `syn-owned-3y::qwen3-32b__nvidia-h100-sxm-80gb__tp2_dp1_bf16`（owned） 一次性 480000 / 年现金 41136.1280 / 年总成本 185136.1280

- 候选数：3
- 库中计费规则：['syn-billing-qwen3-32b']

## 7. 附录：其余公开参考数据

以下 10 条与目标的差距不小于第 2.1 节展示的 3 条，保留供复核；判定口径与正文完全一致（同样**不参与**本需求的 SLA 判定）。完整原因清单见 JSON 结果里的 `external_references`。

| 来源 | 模型 | GPU | 卡数 | TP/DP | 框架 | ISL/OSL | 并发 | TTFT P99 | TPOT P99 | 输出吞吐 | 匹配等级 | 链接 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `public::sglang_qwen3_cookbook:5.1.3.3` | Qwen/Qwen3-235B-A22B-Instruct-2507 | NVIDIA B200 | 8 | 8/— | sglang 0.5.6 | 8000/1000 | 64 | 510.48 | 40.59 | 1790.43 | `rejected` | https://github.com/sgl-project/sglang/blob/main/docs/cookbook/autoregressive/Qwen/Qwen3.mdx |
| `public::sglang_qwen3_coder_cookbook:5.1.1.1` | Qwen/Qwen3-Coder-480B-A35B-Instruct-FP8 | AMD MI300X | 8 | 8/— | sglang 0.5.7 | 1000/1000 | 1 | 662.15 | 17.08 | 57.19 | `rejected` | https://github.com/sgl-project/sglang/blob/main/docs/cookbook/autoregressive/Qwen/Qwen3-Coder.mdx |
| `public::sglang_qwen3_coder_cookbook:5.1.1.2` | Qwen/Qwen3-Coder-480B-A35B-Instruct-FP8 | AMD MI300X | 8 | 8/— | sglang 0.5.7 | 1000/1000 | 16 | 1318.63 | 43.48 | 468.83 | `rejected` | https://github.com/sgl-project/sglang/blob/main/docs/cookbook/autoregressive/Qwen/Qwen3-Coder.mdx |
| `public::sglang_qwen3_coder_cookbook:5.1.1.3` | Qwen/Qwen3-Coder-480B-A35B-Instruct-FP8 | AMD MI300X | 8 | 8/— | sglang 0.5.7 | 1000/1000 | 64 | 529.01 | 204.24 | 956.80 | `rejected` | https://github.com/sgl-project/sglang/blob/main/docs/cookbook/autoregressive/Qwen/Qwen3-Coder.mdx |
| `public::sglang_qwen3_cookbook:5.1.1.1` | Qwen/Qwen3-235B-A22B-Instruct-2507 | NVIDIA B200 | 8 | 8/— | sglang 0.5.6 | 1000/1000 | 1 | 166.05 | 10.15 | 96.65 | `rejected` | https://github.com/sgl-project/sglang/blob/main/docs/cookbook/autoregressive/Qwen/Qwen3.mdx |
| `public::sglang_qwen3_cookbook:5.1.1.2` | Qwen/Qwen3-235B-A22B-Instruct-2507 | NVIDIA B200 | 8 | 8/— | sglang 0.5.6 | 1000/1000 | 16 | 727.08 | 24.09 | 832.04 | `rejected` | https://github.com/sgl-project/sglang/blob/main/docs/cookbook/autoregressive/Qwen/Qwen3.mdx |
| `public::sglang_qwen3_cookbook:5.1.1.3` | Qwen/Qwen3-235B-A22B-Instruct-2507 | NVIDIA B200 | 8 | 8/— | sglang 0.5.6 | 1000/1000 | 100 | 863.11 | 41.19 | 2738.78 | `rejected` | https://github.com/sgl-project/sglang/blob/main/docs/cookbook/autoregressive/Qwen/Qwen3.mdx |
| `public::sglang_qwen3_cookbook:5.1.2.1` | Qwen/Qwen3-235B-A22B-Instruct-2507 | NVIDIA B200 | 8 | 8/— | sglang 0.5.6 | 1000/8000 | 1 | 167.43 | 10.32 | 97.17 | `rejected` | https://github.com/sgl-project/sglang/blob/main/docs/cookbook/autoregressive/Qwen/Qwen3.mdx |
| `public::sglang_qwen3_cookbook:5.1.2.2` | Qwen/Qwen3-235B-A22B-Instruct-2507 | NVIDIA B200 | 8 | 8/— | sglang 0.5.6 | 1000/8000 | 16 | 455.71 | 15.20 | 935.49 | `rejected` | https://github.com/sgl-project/sglang/blob/main/docs/cookbook/autoregressive/Qwen/Qwen3.mdx |
| `public::sglang_qwen3_cookbook:5.1.2.3` | Qwen/Qwen3-235B-A22B-Instruct-2507 | NVIDIA B200 | 8 | 8/— | sglang 0.5.6 | 1000/8000 | 64 | 823.85 | 24.97 | 2387.34 | `rejected` | https://github.com/sgl-project/sglang/blob/main/docs/cookbook/autoregressive/Qwen/Qwen3.mdx |

- `public::sglang_qwen3_cookbook:5.1.3.3`：模型不同：你的目标是 Qwen/Qwen3-32B，公开数据是 Qwen/Qwen3-235B-A22B-Instruct-2507；GPU 型号不同：你的目标是 NVIDIA H100 SXM，公开数据是 NVIDIA B200；推理框架不同：你的目标是 vllm，公开数据是 sglang；框架版本不同：你的目标是 0.24.0，公开数据是 0.5.6；负载长度不同：你的目标是 8192/256，公开数据是 8000/1000（时延与吞吐随长度变化，不能直接套用）；配置并发不同：你的目标是 32，公开数据是 64；公开来源未给出权重精度：无法确认这条公开数据就是你要部署的配置
- `public::sglang_qwen3_coder_cookbook:5.1.1.1`：模型不同：你的目标是 Qwen/Qwen3-32B，公开数据是 Qwen/Qwen3-Coder-480B-A35B-Instruct-FP8；权重精度不同：你的目标是 bf16，公开数据是 fp8；GPU 型号不同：你的目标是 NVIDIA H100 SXM，公开数据是 AMD MI300X；推理框架不同：你的目标是 vllm，公开数据是 sglang；框架版本不同：你的目标是 0.24.0，公开数据是 0.5.7；负载长度不同：你的目标是 8192/256，公开数据是 1000/1000（时延与吞吐随长度变化，不能直接套用）；配置并发不同：你的目标是 32，公开数据是 1；公开数据使用了本阶段不建性能模型的部署维度（ep_size）：已按规则阻断，不参与结论
- `public::sglang_qwen3_coder_cookbook:5.1.1.2`：模型不同：你的目标是 Qwen/Qwen3-32B，公开数据是 Qwen/Qwen3-Coder-480B-A35B-Instruct-FP8；权重精度不同：你的目标是 bf16，公开数据是 fp8；GPU 型号不同：你的目标是 NVIDIA H100 SXM，公开数据是 AMD MI300X；推理框架不同：你的目标是 vllm，公开数据是 sglang；框架版本不同：你的目标是 0.24.0，公开数据是 0.5.7；负载长度不同：你的目标是 8192/256，公开数据是 1000/1000（时延与吞吐随长度变化，不能直接套用）；配置并发不同：你的目标是 32，公开数据是 16；公开数据使用了本阶段不建性能模型的部署维度（ep_size）：已按规则阻断，不参与结论
- `public::sglang_qwen3_coder_cookbook:5.1.1.3`：模型不同：你的目标是 Qwen/Qwen3-32B，公开数据是 Qwen/Qwen3-Coder-480B-A35B-Instruct-FP8；权重精度不同：你的目标是 bf16，公开数据是 fp8；GPU 型号不同：你的目标是 NVIDIA H100 SXM，公开数据是 AMD MI300X；推理框架不同：你的目标是 vllm，公开数据是 sglang；框架版本不同：你的目标是 0.24.0，公开数据是 0.5.7；负载长度不同：你的目标是 8192/256，公开数据是 1000/1000（时延与吞吐随长度变化，不能直接套用）；配置并发不同：你的目标是 32，公开数据是 64；公开数据使用了本阶段不建性能模型的部署维度（ep_size）：已按规则阻断，不参与结论
- `public::sglang_qwen3_cookbook:5.1.1.1`：模型不同：你的目标是 Qwen/Qwen3-32B，公开数据是 Qwen/Qwen3-235B-A22B-Instruct-2507；GPU 型号不同：你的目标是 NVIDIA H100 SXM，公开数据是 NVIDIA B200；推理框架不同：你的目标是 vllm，公开数据是 sglang；框架版本不同：你的目标是 0.24.0，公开数据是 0.5.6；负载长度不同：你的目标是 8192/256，公开数据是 1000/1000（时延与吞吐随长度变化，不能直接套用）；配置并发不同：你的目标是 32，公开数据是 1；公开来源未给出权重精度：无法确认这条公开数据就是你要部署的配置
- `public::sglang_qwen3_cookbook:5.1.1.2`：模型不同：你的目标是 Qwen/Qwen3-32B，公开数据是 Qwen/Qwen3-235B-A22B-Instruct-2507；GPU 型号不同：你的目标是 NVIDIA H100 SXM，公开数据是 NVIDIA B200；推理框架不同：你的目标是 vllm，公开数据是 sglang；框架版本不同：你的目标是 0.24.0，公开数据是 0.5.6；负载长度不同：你的目标是 8192/256，公开数据是 1000/1000（时延与吞吐随长度变化，不能直接套用）；配置并发不同：你的目标是 32，公开数据是 16；公开来源未给出权重精度：无法确认这条公开数据就是你要部署的配置
- `public::sglang_qwen3_cookbook:5.1.1.3`：模型不同：你的目标是 Qwen/Qwen3-32B，公开数据是 Qwen/Qwen3-235B-A22B-Instruct-2507；GPU 型号不同：你的目标是 NVIDIA H100 SXM，公开数据是 NVIDIA B200；推理框架不同：你的目标是 vllm，公开数据是 sglang；框架版本不同：你的目标是 0.24.0，公开数据是 0.5.6；负载长度不同：你的目标是 8192/256，公开数据是 1000/1000（时延与吞吐随长度变化，不能直接套用）；配置并发不同：你的目标是 32，公开数据是 100；公开来源未给出权重精度：无法确认这条公开数据就是你要部署的配置
- `public::sglang_qwen3_cookbook:5.1.2.1`：模型不同：你的目标是 Qwen/Qwen3-32B，公开数据是 Qwen/Qwen3-235B-A22B-Instruct-2507；GPU 型号不同：你的目标是 NVIDIA H100 SXM，公开数据是 NVIDIA B200；推理框架不同：你的目标是 vllm，公开数据是 sglang；框架版本不同：你的目标是 0.24.0，公开数据是 0.5.6；负载长度不同：你的目标是 8192/256，公开数据是 1000/8000（时延与吞吐随长度变化，不能直接套用）；配置并发不同：你的目标是 32，公开数据是 1；公开来源未给出权重精度：无法确认这条公开数据就是你要部署的配置
- `public::sglang_qwen3_cookbook:5.1.2.2`：模型不同：你的目标是 Qwen/Qwen3-32B，公开数据是 Qwen/Qwen3-235B-A22B-Instruct-2507；GPU 型号不同：你的目标是 NVIDIA H100 SXM，公开数据是 NVIDIA B200；推理框架不同：你的目标是 vllm，公开数据是 sglang；框架版本不同：你的目标是 0.24.0，公开数据是 0.5.6；负载长度不同：你的目标是 8192/256，公开数据是 1000/8000（时延与吞吐随长度变化，不能直接套用）；配置并发不同：你的目标是 32，公开数据是 16；公开来源未给出权重精度：无法确认这条公开数据就是你要部署的配置
- `public::sglang_qwen3_cookbook:5.1.2.3`：模型不同：你的目标是 Qwen/Qwen3-32B，公开数据是 Qwen/Qwen3-235B-A22B-Instruct-2507；GPU 型号不同：你的目标是 NVIDIA H100 SXM，公开数据是 NVIDIA B200；推理框架不同：你的目标是 vllm，公开数据是 sglang；框架版本不同：你的目标是 0.24.0，公开数据是 0.5.6；负载长度不同：你的目标是 8192/256，公开数据是 1000/8000（时延与吞吐随长度变化，不能直接套用）；配置并发不同：你的目标是 32，公开数据是 64；公开来源未给出权重精度：无法确认这条公开数据就是你要部署的配置
