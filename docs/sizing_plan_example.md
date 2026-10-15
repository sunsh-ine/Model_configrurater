# 模型与硬件资源规划报告（第二阶段）

- 请求场景：qwen3-32b @ NVIDIA H100 SXM, bf16 weights / bf16 kv, ctx=8192, concurrency=32
- 模型：`qwen3-32b`　硬件：`nvidia-h100-sxm-80gb`　精度：bf16 权重 / bf16 KV
- 上下文长度：8192 tokens/请求　全局并发：32
- 公式版本：weights-1.0 / kv-1.0 / memory-1.0

### 边界声明（务必先读）

> 本报告是**静态资源规划**：只回答『按给定假设，静态显存是否放得下』。
> **不代表**服务能成功启动：框架内核支持、通信、拓扑、驱动等均未验证。
> **不代表**满足 SLA：本阶段不做 TTFT/TPOT/吞吐预测（第二阶段边界）。
> 可用显存若来自假设而非实测，结论只能算『条件可行』，落地前必须实测复核。

## 1. 输入：来源、假设与缺口

| 项 | 值 | 性质 |
|---|---|---|
| 模型 `qwen3-32b` | 结构来自官方 config.json，revision `9216db5781bf21249d130ec9da846c4624c16137`，sha256 `97e295b632839357…` | 厂商官方（已归档） |
| 硬件 `nvidia-h100-sxm-80gb` | NVIDIA H100 SXM，标称 80GB (80.00 GB)，带宽 3.35TB/s | vendor_page_verified |
| 单卡可用显存 | 76.00 GiB | 示例：H100 80GB 按实测口径 76GiB |
| 运行时预留 | 合并预留 可用显存 × 0.1 | 示例假设 |

**待核实字段**（不得默认满足）：

- `nvidia-h100-sxm-80gb`: usable_memory_bytes, supported_weight_precisions, supported_kv_cache_precisions, framework_kernel_support, max_sequence_length_supported

## 2. 部署候选（按状态与卡数排序，不代表性能优劣）

| 候选 | 卡数 | TP | DP | 权重/卡 | KV/卡 | 合计/卡 | 余量 | 状态 |
|---|---|---|---|---|---|---|---|---|
| `qwen3-32b__nvidia-h100-sxm-80gb__tp2_dp1_bf16` | 2 | 2 | 1 | 31.96 GiB | 32.00 GiB | 71.56 GiB | 4.44 GiB | `conditional_feasible` |
| `qwen3-32b__nvidia-h100-sxm-80gb__tp4_dp1_bf16` | 4 | 4 | 1 | 17.43 GiB | 16.00 GiB | 41.03 GiB | 34.97 GiB | `conditional_feasible` |
| `qwen3-32b__nvidia-h100-sxm-80gb__tp2_dp2_bf16` | 4 | 2 | 2 | 31.96 GiB | 16.00 GiB | 55.56 GiB | 20.44 GiB | `conditional_feasible` |
| `qwen3-32b__nvidia-h100-sxm-80gb__tp8_dp1_bf16` | 8 | 8 | 1 | 10.16 GiB | 8.00 GiB | 25.76 GiB | 50.24 GiB | `conditional_feasible` |
| `qwen3-32b__nvidia-h100-sxm-80gb__tp4_dp2_bf16` | 8 | 4 | 2 | 17.43 GiB | 8.00 GiB | 33.03 GiB | 42.97 GiB | `conditional_feasible` |
| `qwen3-32b__nvidia-h100-sxm-80gb__tp8_dp2_bf16` | 16 | 8 | 2 | 10.16 GiB | 4.00 GiB | 21.76 GiB | 54.24 GiB | `conditional_feasible` |
| `qwen3-32b__nvidia-h100-sxm-80gb__tp1_dp1_bf16` | 1 | 1 | 1 | 61.02 GiB | 64.00 GiB | 132.62 GiB | -56.62 GiB | `infeasible` |
| `qwen3-32b__nvidia-h100-sxm-80gb__tp1_dp2_bf16` | 2 | 1 | 2 | 61.02 GiB | 32.00 GiB | 100.62 GiB | -24.62 GiB | `infeasible` |

状态分布：{'conditional_feasible': 6, 'infeasible': 2}

## 3. 每个候选的判定依据

### qwen3-32b__nvidia-h100-sxm-80gb__tp2_dp1_bf16

- 结论：`conditional_feasible`（2 卡 = TP2 × DP1）
- 权重：31.96 GiB / 卡（upper_bound_estimate）
  - 分布：切分 ['attention_k', 'attention_o', 'attention_q', 'attention_v', 'ffn_down', 'ffn_gate', 'ffn_up']；复制 ['attention_k_norm', 'attention_q_norm', 'final_norm', 'layer_norm_in', 'layer_norm_post']；未确认 ['embedding', 'lm_head']
  - 依据：Dense decoder + GQA 结构公式：L=64, h=5120, heads=64, kv_heads=8, head_dim=128, ffn=25600, vocab=151936, tie=False, qk_norm=True；每元素 2 字节
  - 与官方权重总量一致性：结构公式总量与权重索引 metadata.total_size **完全一致**（差 0 字节）
- KV：32.00 GiB / 卡（sharded）；依据：每 token 每层 KV = 2 × kv_heads(4/rank) × head_dim(128) × 2 字节；层数 64；每副本并发 32 × 上下文 8192
  - 说明：Paged KV：block_size=16 token/块；每请求对齐到 512 块 → 共 16384 块；对齐放大 0 字节（0.00%）
- 运行时：已知 1 项（合计 7.60 GiB），未知 4 项：['framework_workspace', 'cuda_graph_capture', 'activation_peak', 'framework_overhead']
- 单卡可用：76.00 GiB；合计需求：71.56 GiB；余量：4.44 GiB
- 依赖假设：静态余量 4765577626 字节；结论仅覆盖静态资源，不代表可成功启动或满足 SLA
- 原因：存在未确认的前置条件，且未给出任何假设
- 未确认条件：['framework_kernel_support_unverified', 'gpu_usable_memory_not_measured', 'requires_multi_node_interconnect_not_modelled', 'weight_precision_support_unverified:bf16']

### qwen3-32b__nvidia-h100-sxm-80gb__tp4_dp1_bf16

- 结论：`conditional_feasible`（4 卡 = TP4 × DP1）
- 权重：17.43 GiB / 卡（upper_bound_estimate）
  - 分布：切分 ['attention_k', 'attention_o', 'attention_q', 'attention_v', 'ffn_down', 'ffn_gate', 'ffn_up']；复制 ['attention_k_norm', 'attention_q_norm', 'final_norm', 'layer_norm_in', 'layer_norm_post']；未确认 ['embedding', 'lm_head']
  - 依据：Dense decoder + GQA 结构公式：L=64, h=5120, heads=64, kv_heads=8, head_dim=128, ffn=25600, vocab=151936, tie=False, qk_norm=True；每元素 2 字节
  - 与官方权重总量一致性：结构公式总量与权重索引 metadata.total_size **完全一致**（差 0 字节）
- KV：16.00 GiB / 卡（sharded）；依据：每 token 每层 KV = 2 × kv_heads(2/rank) × head_dim(128) × 2 字节；层数 64；每副本并发 32 × 上下文 8192
  - 说明：Paged KV：block_size=16 token/块；每请求对齐到 512 块 → 共 16384 块；对齐放大 0 字节（0.00%）
- 运行时：已知 1 项（合计 7.60 GiB），未知 4 项：['framework_workspace', 'cuda_graph_capture', 'activation_peak', 'framework_overhead']
- 单卡可用：76.00 GiB；合计需求：41.03 GiB；余量：34.97 GiB
- 依赖假设：静态余量 37548257690 字节；结论仅覆盖静态资源，不代表可成功启动或满足 SLA
- 原因：存在未确认的前置条件，且未给出任何假设
- 未确认条件：['framework_kernel_support_unverified', 'gpu_usable_memory_not_measured', 'requires_multi_node_interconnect_not_modelled', 'weight_precision_support_unverified:bf16']

### qwen3-32b__nvidia-h100-sxm-80gb__tp2_dp2_bf16

- 结论：`conditional_feasible`（4 卡 = TP2 × DP2）
- 权重：31.96 GiB / 卡（upper_bound_estimate）
  - 分布：切分 ['attention_k', 'attention_o', 'attention_q', 'attention_v', 'ffn_down', 'ffn_gate', 'ffn_up']；复制 ['attention_k_norm', 'attention_q_norm', 'final_norm', 'layer_norm_in', 'layer_norm_post']；未确认 ['embedding', 'lm_head']
  - 依据：Dense decoder + GQA 结构公式：L=64, h=5120, heads=64, kv_heads=8, head_dim=128, ffn=25600, vocab=151936, tie=False, qk_norm=True；每元素 2 字节
  - 与官方权重总量一致性：结构公式总量与权重索引 metadata.total_size **完全一致**（差 0 字节）
- KV：16.00 GiB / 卡（sharded）；依据：每 token 每层 KV = 2 × kv_heads(4/rank) × head_dim(128) × 2 字节；层数 64；每副本并发 16 × 上下文 8192
  - 说明：Paged KV：block_size=16 token/块；每请求对齐到 512 块 → 共 8192 块；对齐放大 0 字节（0.00%）
- 运行时：已知 1 项（合计 7.60 GiB），未知 4 项：['framework_workspace', 'cuda_graph_capture', 'activation_peak', 'framework_overhead']
- 单卡可用：76.00 GiB；合计需求：55.56 GiB；余量：20.44 GiB
- 依赖假设：静态余量 21945446810 字节；结论仅覆盖静态资源，不代表可成功启动或满足 SLA
- 原因：存在未确认的前置条件，且未给出任何假设
- 未确认条件：['framework_kernel_support_unverified', 'gpu_usable_memory_not_measured', 'requires_multi_node_interconnect_not_modelled', 'weight_precision_support_unverified:bf16']

### qwen3-32b__nvidia-h100-sxm-80gb__tp8_dp1_bf16

- 结论：`conditional_feasible`（8 卡 = TP8 × DP1）
- 权重：10.16 GiB / 卡（upper_bound_estimate）
  - 分布：切分 ['attention_k', 'attention_o', 'attention_q', 'attention_v', 'ffn_down', 'ffn_gate', 'ffn_up']；复制 ['attention_k_norm', 'attention_q_norm', 'final_norm', 'layer_norm_in', 'layer_norm_post']；未确认 ['embedding', 'lm_head']
  - 依据：Dense decoder + GQA 结构公式：L=64, h=5120, heads=64, kv_heads=8, head_dim=128, ffn=25600, vocab=151936, tie=False, qk_norm=True；每元素 2 字节
  - 与官方权重总量一致性：结构公式总量与权重索引 metadata.total_size **完全一致**（差 0 字节）
- KV：8.00 GiB / 卡（sharded）；依据：每 token 每层 KV = 2 × kv_heads(1/rank) × head_dim(128) × 2 字节；层数 64；每副本并发 32 × 上下文 8192
  - 说明：Paged KV：block_size=16 token/块；每请求对齐到 512 块 → 共 16384 块；对齐放大 0 字节（0.00%）
- 运行时：已知 1 项（合计 7.60 GiB），未知 4 项：['framework_workspace', 'cuda_graph_capture', 'activation_peak', 'framework_overhead']
- 单卡可用：76.00 GiB；合计需求：25.76 GiB；余量：50.24 GiB
- 依赖假设：静态余量 53939597722 字节；结论仅覆盖静态资源，不代表可成功启动或满足 SLA
- 原因：存在未确认的前置条件，且未给出任何假设
- 未确认条件：['framework_kernel_support_unverified', 'gpu_usable_memory_not_measured', 'requires_multi_node_interconnect_not_modelled', 'weight_precision_support_unverified:bf16']

### qwen3-32b__nvidia-h100-sxm-80gb__tp4_dp2_bf16

- 结论：`conditional_feasible`（8 卡 = TP4 × DP2）
- 权重：17.43 GiB / 卡（upper_bound_estimate）
  - 分布：切分 ['attention_k', 'attention_o', 'attention_q', 'attention_v', 'ffn_down', 'ffn_gate', 'ffn_up']；复制 ['attention_k_norm', 'attention_q_norm', 'final_norm', 'layer_norm_in', 'layer_norm_post']；未确认 ['embedding', 'lm_head']
  - 依据：Dense decoder + GQA 结构公式：L=64, h=5120, heads=64, kv_heads=8, head_dim=128, ffn=25600, vocab=151936, tie=False, qk_norm=True；每元素 2 字节
  - 与官方权重总量一致性：结构公式总量与权重索引 metadata.total_size **完全一致**（差 0 字节）
- KV：8.00 GiB / 卡（sharded）；依据：每 token 每层 KV = 2 × kv_heads(2/rank) × head_dim(128) × 2 字节；层数 64；每副本并发 16 × 上下文 8192
  - 说明：Paged KV：block_size=16 token/块；每请求对齐到 512 块 → 共 8192 块；对齐放大 0 字节（0.00%）
- 运行时：已知 1 项（合计 7.60 GiB），未知 4 项：['framework_workspace', 'cuda_graph_capture', 'activation_peak', 'framework_overhead']
- 单卡可用：76.00 GiB；合计需求：33.03 GiB；余量：42.97 GiB
- 依赖假设：静态余量 46138192282 字节；结论仅覆盖静态资源，不代表可成功启动或满足 SLA
- 原因：存在未确认的前置条件，且未给出任何假设
- 未确认条件：['framework_kernel_support_unverified', 'gpu_usable_memory_not_measured', 'requires_multi_node_interconnect_not_modelled', 'weight_precision_support_unverified:bf16']

### qwen3-32b__nvidia-h100-sxm-80gb__tp8_dp2_bf16

- 结论：`conditional_feasible`（16 卡 = TP8 × DP2）
- 权重：10.16 GiB / 卡（upper_bound_estimate）
  - 分布：切分 ['attention_k', 'attention_o', 'attention_q', 'attention_v', 'ffn_down', 'ffn_gate', 'ffn_up']；复制 ['attention_k_norm', 'attention_q_norm', 'final_norm', 'layer_norm_in', 'layer_norm_post']；未确认 ['embedding', 'lm_head']
  - 依据：Dense decoder + GQA 结构公式：L=64, h=5120, heads=64, kv_heads=8, head_dim=128, ffn=25600, vocab=151936, tie=False, qk_norm=True；每元素 2 字节
  - 与官方权重总量一致性：结构公式总量与权重索引 metadata.total_size **完全一致**（差 0 字节）
- KV：4.00 GiB / 卡（sharded）；依据：每 token 每层 KV = 2 × kv_heads(1/rank) × head_dim(128) × 2 字节；层数 64；每副本并发 16 × 上下文 8192
  - 说明：Paged KV：block_size=16 token/块；每请求对齐到 512 块 → 共 8192 块；对齐放大 0 字节（0.00%）
- 运行时：已知 1 项（合计 7.60 GiB），未知 4 项：['framework_workspace', 'cuda_graph_capture', 'activation_peak', 'framework_overhead']
- 单卡可用：76.00 GiB；合计需求：21.76 GiB；余量：54.24 GiB
- 依赖假设：静态余量 58234565018 字节；结论仅覆盖静态资源，不代表可成功启动或满足 SLA
- 原因：存在未确认的前置条件，且未给出任何假设
- 未确认条件：['framework_kernel_support_unverified', 'gpu_usable_memory_not_measured', 'requires_multi_node_interconnect_not_modelled', 'weight_precision_support_unverified:bf16']

### qwen3-32b__nvidia-h100-sxm-80gb__tp1_dp1_bf16

- 结论：`infeasible`（1 卡 = TP1 × DP1）
- 权重：61.02 GiB / 卡（upper_bound_estimate）
  - 分布：切分 ['attention_k', 'attention_o', 'attention_q', 'attention_v', 'ffn_down', 'ffn_gate', 'ffn_up']；复制 ['attention_k_norm', 'attention_q_norm', 'final_norm', 'layer_norm_in', 'layer_norm_post']；未确认 ['embedding', 'lm_head']
  - 依据：Dense decoder + GQA 结构公式：L=64, h=5120, heads=64, kv_heads=8, head_dim=128, ffn=25600, vocab=151936, tie=False, qk_norm=True；每元素 2 字节
  - 与官方权重总量一致性：结构公式总量与权重索引 metadata.total_size **完全一致**（差 0 字节）
- KV：64.00 GiB / 卡（sharded）；依据：每 token 每层 KV = 2 × kv_heads(8/rank) × head_dim(128) × 2 字节；层数 64；每副本并发 32 × 上下文 8192
  - 说明：Paged KV：block_size=16 token/块；每请求对齐到 512 块 → 共 16384 块；对齐放大 0 字节（0.00%）
- 运行时：已知 1 项（合计 7.60 GiB），未知 4 项：['framework_workspace', 'cuda_graph_capture', 'activation_peak', 'framework_overhead']
- 单卡可用：76.00 GiB；合计需求：132.62 GiB；余量：-56.62 GiB
- 原因：存在未确认的前置条件，且未给出任何假设
- 原因：每卡需求 142404161126 字节 > 可用 81604378624 字节（超出 60799782502 字节）
- 未确认条件：['framework_kernel_support_unverified', 'gpu_usable_memory_not_measured', 'requires_multi_node_interconnect_not_modelled', 'weight_precision_support_unverified:bf16']

### qwen3-32b__nvidia-h100-sxm-80gb__tp1_dp2_bf16

- 结论：`infeasible`（2 卡 = TP1 × DP2）
- 权重：61.02 GiB / 卡（upper_bound_estimate）
  - 分布：切分 ['attention_k', 'attention_o', 'attention_q', 'attention_v', 'ffn_down', 'ffn_gate', 'ffn_up']；复制 ['attention_k_norm', 'attention_q_norm', 'final_norm', 'layer_norm_in', 'layer_norm_post']；未确认 ['embedding', 'lm_head']
  - 依据：Dense decoder + GQA 结构公式：L=64, h=5120, heads=64, kv_heads=8, head_dim=128, ffn=25600, vocab=151936, tie=False, qk_norm=True；每元素 2 字节
  - 与官方权重总量一致性：结构公式总量与权重索引 metadata.total_size **完全一致**（差 0 字节）
- KV：32.00 GiB / 卡（sharded）；依据：每 token 每层 KV = 2 × kv_heads(8/rank) × head_dim(128) × 2 字节；层数 64；每副本并发 16 × 上下文 8192
  - 说明：Paged KV：block_size=16 token/块；每请求对齐到 512 块 → 共 8192 块；对齐放大 0 字节（0.00%）
- 运行时：已知 1 项（合计 7.60 GiB），未知 4 项：['framework_workspace', 'cuda_graph_capture', 'activation_peak', 'framework_overhead']
- 单卡可用：76.00 GiB；合计需求：100.62 GiB；余量：-24.62 GiB
- 原因：存在未确认的前置条件，且未给出任何假设
- 原因：每卡需求 108044422758 字节 > 可用 81604378624 字节（超出 26440044134 字节）
- 未确认条件：['framework_kernel_support_unverified', 'gpu_usable_memory_not_measured', 'requires_multi_node_interconnect_not_modelled', 'weight_precision_support_unverified:bf16']

## 4. 来源与证据索引

| 来源 | 发布方 | URL | 文件 | sha256 |
|---|---|---|---|---|
| `nvidia_h100_datasheet_page` | nvidia | https://www.nvidia.com/en-us/data-center/h100/ | `data/raw/official/nvidia_h100_datacenter_2026-10-15.html` | `aecc301719228f10…` |
| `nvidia_h200_datasheet_page` | nvidia | https://www.nvidia.com/en-us/data-center/h200/ | `data/raw/official/nvidia_h200_datacenter_2026-10-15.html` | `73a63384e31550c1…` |

## 5. 明确未做的事（第二阶段边界）

- 不做 TTFT/TPOT/ITL/E2E 的性能预测
- 不根据峰值并发推断所需 GPU 数量
- 不做跨 GPU/TP/DP 的吞吐线性外推
- 不覆盖 PD 分离、MoE EP、MTP/Speculative 的资源优化
- 不做未验证量化实现的精确显存核算
- 不把静态可行方案转换成客户报价
