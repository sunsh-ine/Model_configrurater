> AH4 C-open-arrival-risk

# 方案评估报告

- 请求：`ah-scenario-c`
- 模型：`qwen3-8b`
- 候选硬件：['nvidia-h200-sxm-141gb']
- 目标负载：输入 8192 / 输出 256 / 并发 None
- 生成时间：2026-10-26T12:00:08+00:00

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
| `qwen3-8b__nvidia-h200-sxm-141gb__tp1_dp1_bf16` | 1 | 1×1 | 信息不足 | 未评估 | 证据不足 | — | — CNY | 不完整（含未确认/缺失项） | — | ⛔ 不可发布 |

- `qwen3-8b__nvidia-h200-sxm-141gb__tp1_dp1_bf16`：成本覆盖缺口：['gpu_hardware'] / 成本结论不完整，不得据此报价

## 2.1 公开数据参考（**不参与**本需求的性能判定）

库里共有 **14 条**官方/第三方**公开发布**的实测。它们用来看同类配置在业界大概什么水平，**不是**你要部署的配置的实测依据：不参与 SLA 判定，也不进报价门禁。

> 排序：匹配等级 → 与目标相同的条件数 → 输入长度接近程度。比较基准取你的候选方案中**与该条数据差异最小**的一个，因此下面列的是「最有利情况下」仍然存在的差异。完整 14 条见第 7 节附录，结构化数据见 JSON 结果。

**所有 14 条都有、因此下面不再逐条重复的缺口：**

- 请求到达方式不同：你的目标是 poisson，公开数据是 unbounded
- 公开来源未给出模型版本（revision）：无法确认这条公开数据就是你要部署的配置
- 公开来源未给出DP（数据并行）：无法确认这条公开数据就是你要部署的配置
- 你的目标需求也未给出模型版本（revision）：因此这条公开数据最多只能作参考，不能当作该配置下的实测
- 你的目标需求也未给出配置并发：因此这条公开数据最多只能作参考，不能当作该配置下的实测

### 最相关的 3 条

| # | 来源 | 模型 | GPU | 卡数 | TP/DP | 框架 | ISL/OSL | 并发 | TTFT P99 | TPOT P99 | 输出吞吐 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | MG-Cafe（GitHub 社区实验报告） | Qwen/Qwen3-32B | NVIDIA H100 80GB HBM3 | 4 | 4/— | vllm 0.17.0 | 31400/128 | — | 359749.43 | 154.03 | 69.87 |
| 2 | GPUStack (gpustack.ai) | Qwen/Qwen3-32B | NVIDIA H100 SXM | 1 | —/— | vllm 0.11.0 | —/— | — | 153161.51 | 378.18 | 1131.55 |
| 3 | sgl-project/sglang | Qwen/Qwen3-235B-A22B-Instruct-2507 | NVIDIA B200 | 8 | 8/— | sglang 0.5.6 | 8000/1000 | 1 | 225.17 | 10.51 | 93.94 |

#### 1. `public::community_qwen3_32b_h100_4gpu_report:h100-4gpu-random-32k`

- **与目标相同的条件**：权重精度=bf16；推理框架=vllm；数据集=random
- **不能直接比较的条件**：
  - 模型不同：你的目标是 Qwen/Qwen3-8B，公开数据是 Qwen/Qwen3-32B
  - GPU 型号不同：你的目标是 NVIDIA H200 SXM，公开数据是 NVIDIA H100 80GB HBM3
  - 卡数不同：你的目标是 1，公开数据是 4
  - TP（张量并行）不同：你的目标是 1，公开数据是 4
  - 框架版本不同：你的目标是 0.24.0，公开数据是 0.17.0
  - 负载长度不同：你的目标是 8192/256，公开数据是 31400/128（时延与吞吐随长度变化，不能直接套用）
  - 公开来源未给出配置并发：无法确认这条公开数据就是你要部署的配置

- 匹配等级：`rejected`
- 来源：MG-Cafe（GitHub 社区实验报告）｜https://github.com/MG-Cafe/TPU_V6e_H100_Benchmarking_Qwen/blob/main/results/GPU_BENCHMARK_REPORT.md
- 存档证据：`data/raw/official/community_qwen3_32b_h100_4gpu_report_2026-10-15.md`（sha256 `422eec419cd8fd542aa8b969519b4bf86a67e56b8a28bc3333ab61a844eb96f6`）
- 数据可信等级：`measured_third_party`

#### 2. `public::gpustack_perf_lab_qwen3_32b_h100:vllm-sharegpt`

- **与目标相同的条件**：卡数=1；推理框架=vllm
- **不能直接比较的条件**：
  - 模型不同：你的目标是 Qwen/Qwen3-8B，公开数据是 Qwen/Qwen3-32B
  - GPU 型号不同：你的目标是 NVIDIA H200 SXM，公开数据是 NVIDIA H100 SXM
  - 框架版本不同：你的目标是 0.24.0，公开数据是 0.11.0
  - 负载长度不同：你的目标是 8192/256，公开数据是 None/None（时延与吞吐随长度变化，不能直接套用）
  - 数据集不同：你的目标是 random，公开数据是 sharegpt
  - 公开来源未给出权重精度：无法确认这条公开数据就是你要部署的配置
  - 公开来源未给出TP（张量并行）：无法确认这条公开数据就是你要部署的配置
  - 公开来源未给出输入长度：无法确认这条公开数据就是你要部署的配置
  - 公开来源未给出输出长度：无法确认这条公开数据就是你要部署的配置
  - 公开来源未给出配置并发：无法确认这条公开数据就是你要部署的配置

- 匹配等级：`rejected`
- 来源：GPUStack (gpustack.ai)｜https://docs.gpustack.ai/2.0/performance-lab/qwen3-32b/h100/
- 存档证据：`data/raw/official/gpustack_perf_lab_qwen3_32b_h100_2026-10-15.html`（sha256 `7abb84172e27dc90f1b2e668c495292909c15e991fcec1167b87e534037ae008`）
- 数据可信等级：`measured_vendor_published`

#### 3. `public::sglang_qwen3_cookbook:5.1.3.1`

- **与目标相同的条件**：数据集=random
- **不能直接比较的条件**：
  - 模型不同：你的目标是 Qwen/Qwen3-8B，公开数据是 Qwen/Qwen3-235B-A22B-Instruct-2507
  - GPU 型号不同：你的目标是 NVIDIA H200 SXM，公开数据是 NVIDIA B200
  - 卡数不同：你的目标是 1，公开数据是 8
  - TP（张量并行）不同：你的目标是 1，公开数据是 8
  - 推理框架不同：你的目标是 vllm，公开数据是 sglang
  - 框架版本不同：你的目标是 0.24.0，公开数据是 0.5.6
  - 负载长度不同：你的目标是 8192/256，公开数据是 8000/1000（时延与吞吐随长度变化，不能直接套用）
  - 公开来源未给出权重精度：无法确认这条公开数据就是你要部署的配置

- 匹配等级：`rejected`
- 来源：sgl-project/sglang｜https://github.com/sgl-project/sglang/blob/main/docs/cookbook/autoregressive/Qwen/Qwen3.mdx
- 存档证据：`data/raw/official/sglang_cookbook_qwen3_2026-10-15.mdx`（sha256 `23d8e61ce899920e5606ca80c30c292a88cab02321c1bd3c610a19dba0b27425`）
- 数据可信等级：`measured_vendor_published`

## 2.2 模拟部署配置推荐（**非实测**）

模拟状态：**已计算**

- 工具：`aisimulate 0.12.1`（原生回放），后端 `vllm 0.24.0`，硬件库 `h200_sxm`，数据库模式 `SILICON`
- 搜索空间存档：`data/simulation/archives/af-live-qwen3-8b-h200-tp1dp2-r5-seed1/exp_config.yaml`（sha256 `30c8534ab5992adec805fdc7067f6baf15e82f5d35dd33f69066294f7bf1d006`）

- 排序目标：**吞吐优先**（多候选都满足时按此目标取一个，不提供含糊的「全局最优」）

**无符合条件的模拟候选**（身份/资源/P99/预算任一不满足即不进推荐池），因此不给出模拟推荐。

- `sim-native:Qwen/Qwen3-8B@h200_sxm-tp1pp1dp2-cNone-n500-r5-s1`：静态结论 insufficient_information（Sizing 口径 2 卡）；权重或 KV 的每卡字节数不可得（结构/负载信息不全）；多 seed 重复性不足：3/5 通过（mixed），不能作为正式推荐
- `sim-native:Qwen/Qwen3-8B@h200_sxm-tp1pp1dp2-cNone-n500-r5-s2`：静态结论 insufficient_information（Sizing 口径 2 卡）；权重或 KV 的每卡字节数不可得（结构/负载信息不全）；多 seed 重复性不足：3/5 通过（mixed），不能作为正式推荐
- `sim-native:Qwen/Qwen3-8B@h200_sxm-tp1pp1dp2-cNone-n500-r5-s3`：静态结论 insufficient_information（Sizing 口径 2 卡）；权重或 KV 的每卡字节数不可得（结构/负载信息不全）；多 seed 重复性不足：3/5 通过（mixed），不能作为正式推荐
- `sim-native:Qwen/Qwen3-8B@h200_sxm-tp1pp1dp2-cNone-n500-r5-s4`：静态结论 insufficient_information（Sizing 口径 2 卡）；权重或 KV 的每卡字节数不可得（结构/负载信息不全）；多 seed 重复性不足：3/5 通过（mixed），不能作为正式推荐
- `sim-native:Qwen/Qwen3-8B@h200_sxm-tp1pp1dp2-cNone-n500-r5-s5`：静态结论 insufficient_information（Sizing 口径 2 卡）；权重或 KV 的每卡字节数不可得（结构/负载信息不全）；多 seed 重复性不足：3/5 通过（mixed），不能作为正式推荐

**并行身份与工作点说明**：

- `sim-native:Qwen/Qwen3-8B@h200_sxm-tp1pp1dp2-cNone-n500-r5-s1`：DP2 由 workers.aggregated.replicas=2 推导（单一 aggregated worker 组；原生 schema 无独立 dp 维度）
- `sim-native:Qwen/Qwen3-8B@h200_sxm-tp1pp1dp2-cNone-n500-r5-s1`：GPU 总数 2 由 decode_gpus_per_worker=1 × replicas=2 推导
- `sim-native:Qwen/Qwen3-8B@h200_sxm-tp1pp1dp2-cNone-n500-r5-s1`：模型精度 bf16 由**输入身份**给出（原生回放输出不回显精度）
- `sim-native:Qwen/Qwen3-8B@h200_sxm-tp1pp1dp2-cNone-n500-r5-s1`：归档：af-live-qwen3-8b-h200-tp1dp2-r5-seed1:predict_1
- `sim-native:Qwen/Qwen3-8B@h200_sxm-tp1pp1dp2-cNone-n500-r5-s1`：负载类型 poisson，到达率 5 req/s，seed=1
- `sim-native:Qwen/Qwen3-8B@h200_sxm-tp1pp1dp2-cNone-n500-r5-s1`：证据工具：aisimulate 0.12.1，数据库模式 SILICON
- `sim-native:Qwen/Qwen3-8B@h200_sxm-tp1pp1dp2-cNone-n500-r5-s1`：多 seed 重复性：3/5 通过（mixed）
- `sim-native:Qwen/Qwen3-8B@h200_sxm-tp1pp1dp2-cNone-n500-r5-s2`：DP2 由 workers.aggregated.replicas=2 推导（单一 aggregated worker 组；原生 schema 无独立 dp 维度）
- `sim-native:Qwen/Qwen3-8B@h200_sxm-tp1pp1dp2-cNone-n500-r5-s2`：GPU 总数 2 由 decode_gpus_per_worker=1 × replicas=2 推导
- `sim-native:Qwen/Qwen3-8B@h200_sxm-tp1pp1dp2-cNone-n500-r5-s2`：模型精度 bf16 由**输入身份**给出（原生回放输出不回显精度）
- `sim-native:Qwen/Qwen3-8B@h200_sxm-tp1pp1dp2-cNone-n500-r5-s2`：归档：af-live-qwen3-8b-h200-tp1dp2-r5-seed2:predict_1
- `sim-native:Qwen/Qwen3-8B@h200_sxm-tp1pp1dp2-cNone-n500-r5-s2`：负载类型 poisson，到达率 5 req/s，seed=2
- `sim-native:Qwen/Qwen3-8B@h200_sxm-tp1pp1dp2-cNone-n500-r5-s2`：证据工具：aisimulate 0.12.1，数据库模式 SILICON
- `sim-native:Qwen/Qwen3-8B@h200_sxm-tp1pp1dp2-cNone-n500-r5-s2`：多 seed 重复性：3/5 通过（mixed）
- `sim-native:Qwen/Qwen3-8B@h200_sxm-tp1pp1dp2-cNone-n500-r5-s3`：DP2 由 workers.aggregated.replicas=2 推导（单一 aggregated worker 组；原生 schema 无独立 dp 维度）
- `sim-native:Qwen/Qwen3-8B@h200_sxm-tp1pp1dp2-cNone-n500-r5-s3`：GPU 总数 2 由 decode_gpus_per_worker=1 × replicas=2 推导
- `sim-native:Qwen/Qwen3-8B@h200_sxm-tp1pp1dp2-cNone-n500-r5-s3`：模型精度 bf16 由**输入身份**给出（原生回放输出不回显精度）
- `sim-native:Qwen/Qwen3-8B@h200_sxm-tp1pp1dp2-cNone-n500-r5-s3`：归档：af-live-qwen3-8b-h200-tp1dp2-r5-seed3:predict_1
- `sim-native:Qwen/Qwen3-8B@h200_sxm-tp1pp1dp2-cNone-n500-r5-s3`：负载类型 poisson，到达率 5 req/s，seed=3
- `sim-native:Qwen/Qwen3-8B@h200_sxm-tp1pp1dp2-cNone-n500-r5-s3`：证据工具：aisimulate 0.12.1，数据库模式 SILICON
- `sim-native:Qwen/Qwen3-8B@h200_sxm-tp1pp1dp2-cNone-n500-r5-s3`：多 seed 重复性：3/5 通过（mixed）
- `sim-native:Qwen/Qwen3-8B@h200_sxm-tp1pp1dp2-cNone-n500-r5-s4`：DP2 由 workers.aggregated.replicas=2 推导（单一 aggregated worker 组；原生 schema 无独立 dp 维度）
- `sim-native:Qwen/Qwen3-8B@h200_sxm-tp1pp1dp2-cNone-n500-r5-s4`：GPU 总数 2 由 decode_gpus_per_worker=1 × replicas=2 推导
- `sim-native:Qwen/Qwen3-8B@h200_sxm-tp1pp1dp2-cNone-n500-r5-s4`：模型精度 bf16 由**输入身份**给出（原生回放输出不回显精度）
- `sim-native:Qwen/Qwen3-8B@h200_sxm-tp1pp1dp2-cNone-n500-r5-s4`：归档：af-live-qwen3-8b-h200-tp1dp2-r5-seed4:predict_1
- `sim-native:Qwen/Qwen3-8B@h200_sxm-tp1pp1dp2-cNone-n500-r5-s4`：负载类型 poisson，到达率 5 req/s，seed=4
- `sim-native:Qwen/Qwen3-8B@h200_sxm-tp1pp1dp2-cNone-n500-r5-s4`：证据工具：aisimulate 0.12.1，数据库模式 SILICON
- `sim-native:Qwen/Qwen3-8B@h200_sxm-tp1pp1dp2-cNone-n500-r5-s4`：多 seed 重复性：3/5 通过（mixed）
- `sim-native:Qwen/Qwen3-8B@h200_sxm-tp1pp1dp2-cNone-n500-r5-s5`：DP2 由 workers.aggregated.replicas=2 推导（单一 aggregated worker 组；原生 schema 无独立 dp 维度）
- `sim-native:Qwen/Qwen3-8B@h200_sxm-tp1pp1dp2-cNone-n500-r5-s5`：GPU 总数 2 由 decode_gpus_per_worker=1 × replicas=2 推导
- `sim-native:Qwen/Qwen3-8B@h200_sxm-tp1pp1dp2-cNone-n500-r5-s5`：模型精度 bf16 由**输入身份**给出（原生回放输出不回显精度）
- `sim-native:Qwen/Qwen3-8B@h200_sxm-tp1pp1dp2-cNone-n500-r5-s5`：归档：af-live-qwen3-8b-h200-tp1dp2-r5-seed5:predict_1
- `sim-native:Qwen/Qwen3-8B@h200_sxm-tp1pp1dp2-cNone-n500-r5-s5`：负载类型 poisson，到达率 5 req/s，seed=5
- `sim-native:Qwen/Qwen3-8B@h200_sxm-tp1pp1dp2-cNone-n500-r5-s5`：证据工具：aisimulate 0.12.1，数据库模式 SILICON
- `sim-native:Qwen/Qwen3-8B@h200_sxm-tp1pp1dp2-cNone-n500-r5-s5`：多 seed 重复性：3/5 通过（mixed）

**候选搜索与推荐有效范围**：

- 发现部署：5；通过身份/资源筛选：5；完成原生回放：5；回放失败：0；因回放上限截断：0
- 有效范围：离线归档选择（仅包含已归档的部署）

**多 seed 重复性（运行级）**：

| 工作点 | 实验完整性 | 预期/发现/有效 | 结论 | 通过数 | 校准 |
|---|---|---|---|---|---|
| Qwen/Qwen3-8B（bf16） / h200_sxm / vllm 0.24.0 / TP1×PP1×DP2 / 8192/256 / 5 req/s（poisson） / 数据集 synthetic / 500 请求 | 完整 | 5/5/5 | 部分通过（mixed） | 3 | 未知（unknown） |

> 运行均值不等于池化 P99；这是模拟波动，不是实测误差。

**证据分层与有效范围**：

- 实测证据：无（本轮没有任何候选具备目标配置的匹配实测）
- 模拟证据：5 个候选有 P99 预测结论；其中多 seed 工作点 1 个
- 预测不足（unknown）：无
- 实验完整性：完整 5 个、不完整/无效 0 个
- 匹配实测校准：无（误差保持 unknown）
- 容量/搜索有效范围：离线归档选择（仅包含已归档的部署）

**本节结论的限制**：

- 多部署归档：各候选独立解析；搜索阶段的修正系数不跨归档合并，原生 P99 不额外乘系数
- 本次只评估同一工作点；跨并发/到达率的容量边界需单独扫描，未验证区间保持 unknown，不插值
- 扫描器候选共 0 行，报告只展示其中的 0 行（优先展示能与目标直接比较的工作点）
- 模拟结论**不是实测**：不得写入 BenchmarkRun / CapacityPoint，不得产生 observed_pass / observed_fail
- 未被任何候选使用的实验清单：af-8b-h100-dp2-r4、af-8b-h100-dp2-r5
- 与第 2 节**实测证据**的区别：实测只认目标配置下、可信来源的测量结果；
  本节全是预测，`predicted_*` 与 `observed_*` 是两套取值，不会互相转换。

## 2.3 决策证据（统一结构）

| 目标 | 候选 | GPU | 结论 | 实验完整性 | 校准 |
|---|---|---|---|---|---|
| 推荐（严格） | 无 | — | — | — | — |
| 未达标参考 | 无 | — | — | — | — |
| 最少 GPU 达标 | 无 | — | — | — | — |
| 吞吐最优达标 | 无 | — | — | — | — |

> 排序目标：吞吐优先；最少 GPU、最高吞吐、最低成本是不同目标，不提供综合评分。
- 模拟预测不是实测；predicted_* 不会变成 observed_*
- 成本、性能与报价资格三者独立，不提供综合评分
- min_gpu / max_throughput / min_cost 是不同目标，不能混称‘最优’
- 实验不完整或校准 unknown 时必须显式展示，不得省略

## 3. 推荐依据

- 资源占用最小的静态可行候选：`qwen3-8b__nvidia-h200-sxm-141gb__tp1_dp1_bf16`（1 卡，TP1×DP1）—— 这是**资源**结论，不含性能与经济性判断
- 没有候选具备匹配实测（SLA 全部不可评估）：这属于**证据缺口**，需要用目标配置补测，不能由估算替代
- 没有任何候选具备**完整且金额已知**的成本结论：本结果不给出成本高低结论
- 性能证据分布：需要补测 1 个
- 本结果**不包含**跨维度综合评分：成本、性能与报价资格三者独立

## 3.1 成本可比性

参照方案：`无`
**可比较候选**：无

**不可比较候选**（不参与成本高低比较）：

- `qwen3-8b__nvidia-h200-sxm-141gb__tp1_dp1_bf16`：成本不完整或金额缺失（不得按 0 排序）

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

### qwen3-8b__nvidia-h200-sxm-141gb__tp1_dp1_bf16

- 资源：1 卡 / TP1×DP1 / 服务器 1 台 / 静态余量 — B
- 静态结论：insufficient_information
  - 权重或 KV 的每卡字节数不可得（结构/负载信息不全）
- 性能：匹配等级 —，SLA —，分桶 requires_benchmark
  - 输出吞吐 — tok/s，TTFT p99 — ms，TPOT p99 — ms
  - qwen3-8b × NVIDIA H200 SXM：TP1 × DP1 = 1 卡 → 信息不足
- 成本：方案 `syn-owned-3y::qwen3-8b__nvidia-h200-sxm-141gb__tp1_dp1_bf16`（owned） 一次性 80000 / 年现金 31034.3040 / 年总成本 55034.3040

- 候选数：1
- 库中计费规则：['syn-billing-qwen3-32b']

## 7. 附录：其余公开参考数据

以下 11 条与目标的差距不小于第 2.1 节展示的 3 条，保留供复核；判定口径与正文完全一致（同样**不参与**本需求的 SLA 判定）。完整原因清单见 JSON 结果里的 `external_references`。

| 来源 | 模型 | GPU | 卡数 | TP/DP | 框架 | ISL/OSL | 并发 | TTFT P99 | TPOT P99 | 输出吞吐 | 匹配等级 | 链接 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `public::sglang_qwen3_cookbook:5.1.3.2` | Qwen/Qwen3-235B-A22B-Instruct-2507 | NVIDIA B200 | 8 | 8/— | sglang 0.5.6 | 8000/1000 | 16 | 1027.21 | 27.18 | 820.69 | `rejected` | https://github.com/sgl-project/sglang/blob/main/docs/cookbook/autoregressive/Qwen/Qwen3.mdx |
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

- `public::sglang_qwen3_cookbook:5.1.3.2`：模型不同：你的目标是 Qwen/Qwen3-8B，公开数据是 Qwen/Qwen3-235B-A22B-Instruct-2507；GPU 型号不同：你的目标是 NVIDIA H200 SXM，公开数据是 NVIDIA B200；卡数不同：你的目标是 1，公开数据是 8；TP（张量并行）不同：你的目标是 1，公开数据是 8；推理框架不同：你的目标是 vllm，公开数据是 sglang；框架版本不同：你的目标是 0.24.0，公开数据是 0.5.6；负载长度不同：你的目标是 8192/256，公开数据是 8000/1000（时延与吞吐随长度变化，不能直接套用）；公开来源未给出权重精度：无法确认这条公开数据就是你要部署的配置
- `public::sglang_qwen3_cookbook:5.1.3.3`：模型不同：你的目标是 Qwen/Qwen3-8B，公开数据是 Qwen/Qwen3-235B-A22B-Instruct-2507；GPU 型号不同：你的目标是 NVIDIA H200 SXM，公开数据是 NVIDIA B200；卡数不同：你的目标是 1，公开数据是 8；TP（张量并行）不同：你的目标是 1，公开数据是 8；推理框架不同：你的目标是 vllm，公开数据是 sglang；框架版本不同：你的目标是 0.24.0，公开数据是 0.5.6；负载长度不同：你的目标是 8192/256，公开数据是 8000/1000（时延与吞吐随长度变化，不能直接套用）；公开来源未给出权重精度：无法确认这条公开数据就是你要部署的配置
- `public::sglang_qwen3_coder_cookbook:5.1.1.1`：模型不同：你的目标是 Qwen/Qwen3-8B，公开数据是 Qwen/Qwen3-Coder-480B-A35B-Instruct-FP8；权重精度不同：你的目标是 bf16，公开数据是 fp8；GPU 型号不同：你的目标是 NVIDIA H200 SXM，公开数据是 AMD MI300X；卡数不同：你的目标是 1，公开数据是 8；TP（张量并行）不同：你的目标是 1，公开数据是 8；推理框架不同：你的目标是 vllm，公开数据是 sglang；框架版本不同：你的目标是 0.24.0，公开数据是 0.5.7；负载长度不同：你的目标是 8192/256，公开数据是 1000/1000（时延与吞吐随长度变化，不能直接套用）；公开数据使用了本阶段不建性能模型的部署维度（ep_size）：已按规则阻断，不参与结论
- `public::sglang_qwen3_coder_cookbook:5.1.1.2`：模型不同：你的目标是 Qwen/Qwen3-8B，公开数据是 Qwen/Qwen3-Coder-480B-A35B-Instruct-FP8；权重精度不同：你的目标是 bf16，公开数据是 fp8；GPU 型号不同：你的目标是 NVIDIA H200 SXM，公开数据是 AMD MI300X；卡数不同：你的目标是 1，公开数据是 8；TP（张量并行）不同：你的目标是 1，公开数据是 8；推理框架不同：你的目标是 vllm，公开数据是 sglang；框架版本不同：你的目标是 0.24.0，公开数据是 0.5.7；负载长度不同：你的目标是 8192/256，公开数据是 1000/1000（时延与吞吐随长度变化，不能直接套用）；公开数据使用了本阶段不建性能模型的部署维度（ep_size）：已按规则阻断，不参与结论
- `public::sglang_qwen3_coder_cookbook:5.1.1.3`：模型不同：你的目标是 Qwen/Qwen3-8B，公开数据是 Qwen/Qwen3-Coder-480B-A35B-Instruct-FP8；权重精度不同：你的目标是 bf16，公开数据是 fp8；GPU 型号不同：你的目标是 NVIDIA H200 SXM，公开数据是 AMD MI300X；卡数不同：你的目标是 1，公开数据是 8；TP（张量并行）不同：你的目标是 1，公开数据是 8；推理框架不同：你的目标是 vllm，公开数据是 sglang；框架版本不同：你的目标是 0.24.0，公开数据是 0.5.7；负载长度不同：你的目标是 8192/256，公开数据是 1000/1000（时延与吞吐随长度变化，不能直接套用）；公开数据使用了本阶段不建性能模型的部署维度（ep_size）：已按规则阻断，不参与结论
- `public::sglang_qwen3_cookbook:5.1.1.1`：模型不同：你的目标是 Qwen/Qwen3-8B，公开数据是 Qwen/Qwen3-235B-A22B-Instruct-2507；GPU 型号不同：你的目标是 NVIDIA H200 SXM，公开数据是 NVIDIA B200；卡数不同：你的目标是 1，公开数据是 8；TP（张量并行）不同：你的目标是 1，公开数据是 8；推理框架不同：你的目标是 vllm，公开数据是 sglang；框架版本不同：你的目标是 0.24.0，公开数据是 0.5.6；负载长度不同：你的目标是 8192/256，公开数据是 1000/1000（时延与吞吐随长度变化，不能直接套用）；公开来源未给出权重精度：无法确认这条公开数据就是你要部署的配置
- `public::sglang_qwen3_cookbook:5.1.1.2`：模型不同：你的目标是 Qwen/Qwen3-8B，公开数据是 Qwen/Qwen3-235B-A22B-Instruct-2507；GPU 型号不同：你的目标是 NVIDIA H200 SXM，公开数据是 NVIDIA B200；卡数不同：你的目标是 1，公开数据是 8；TP（张量并行）不同：你的目标是 1，公开数据是 8；推理框架不同：你的目标是 vllm，公开数据是 sglang；框架版本不同：你的目标是 0.24.0，公开数据是 0.5.6；负载长度不同：你的目标是 8192/256，公开数据是 1000/1000（时延与吞吐随长度变化，不能直接套用）；公开来源未给出权重精度：无法确认这条公开数据就是你要部署的配置
- `public::sglang_qwen3_cookbook:5.1.1.3`：模型不同：你的目标是 Qwen/Qwen3-8B，公开数据是 Qwen/Qwen3-235B-A22B-Instruct-2507；GPU 型号不同：你的目标是 NVIDIA H200 SXM，公开数据是 NVIDIA B200；卡数不同：你的目标是 1，公开数据是 8；TP（张量并行）不同：你的目标是 1，公开数据是 8；推理框架不同：你的目标是 vllm，公开数据是 sglang；框架版本不同：你的目标是 0.24.0，公开数据是 0.5.6；负载长度不同：你的目标是 8192/256，公开数据是 1000/1000（时延与吞吐随长度变化，不能直接套用）；公开来源未给出权重精度：无法确认这条公开数据就是你要部署的配置
- `public::sglang_qwen3_cookbook:5.1.2.1`：模型不同：你的目标是 Qwen/Qwen3-8B，公开数据是 Qwen/Qwen3-235B-A22B-Instruct-2507；GPU 型号不同：你的目标是 NVIDIA H200 SXM，公开数据是 NVIDIA B200；卡数不同：你的目标是 1，公开数据是 8；TP（张量并行）不同：你的目标是 1，公开数据是 8；推理框架不同：你的目标是 vllm，公开数据是 sglang；框架版本不同：你的目标是 0.24.0，公开数据是 0.5.6；负载长度不同：你的目标是 8192/256，公开数据是 1000/8000（时延与吞吐随长度变化，不能直接套用）；公开来源未给出权重精度：无法确认这条公开数据就是你要部署的配置
- `public::sglang_qwen3_cookbook:5.1.2.2`：模型不同：你的目标是 Qwen/Qwen3-8B，公开数据是 Qwen/Qwen3-235B-A22B-Instruct-2507；GPU 型号不同：你的目标是 NVIDIA H200 SXM，公开数据是 NVIDIA B200；卡数不同：你的目标是 1，公开数据是 8；TP（张量并行）不同：你的目标是 1，公开数据是 8；推理框架不同：你的目标是 vllm，公开数据是 sglang；框架版本不同：你的目标是 0.24.0，公开数据是 0.5.6；负载长度不同：你的目标是 8192/256，公开数据是 1000/8000（时延与吞吐随长度变化，不能直接套用）；公开来源未给出权重精度：无法确认这条公开数据就是你要部署的配置
- `public::sglang_qwen3_cookbook:5.1.2.3`：模型不同：你的目标是 Qwen/Qwen3-8B，公开数据是 Qwen/Qwen3-235B-A22B-Instruct-2507；GPU 型号不同：你的目标是 NVIDIA H200 SXM，公开数据是 NVIDIA B200；卡数不同：你的目标是 1，公开数据是 8；TP（张量并行）不同：你的目标是 1，公开数据是 8；推理框架不同：你的目标是 vllm，公开数据是 sglang；框架版本不同：你的目标是 0.24.0，公开数据是 0.5.6；负载长度不同：你的目标是 8192/256，公开数据是 1000/8000（时延与吞吐随长度变化，不能直接套用）；公开来源未给出权重精度：无法确认这条公开数据就是你要部署的配置
