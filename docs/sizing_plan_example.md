# 模型与硬件资源规划报告（第二阶段）

- 请求场景：qwen3-32b @ NVIDIA H100 SXM, bf16 weights / bf16 kv, ctx=8192, concurrency=32
- 模型：`qwen3-32b`　硬件：`nvidia-h100-sxm-80gb`　精度：bf16 权重 / bf16 KV
- 上下文长度：8192 tokens/请求　全局并发：32
- 公式版本：weights-1.0 / kv-1.0 / memory-1.0

### 边界声明（务必先读）

> 本报告是**静态资源规划**：只回答『按给定假设，静态显存是否放得下』。
> **不代表**服务能成功启动：框架内核支持、通信、拓扑、驱动等均未验证。
> **不代表**满足 SLA：本阶段不做 TTFT/TPOT/吞吐预测（第二阶段边界）。
> 可用显存若来自假设而非实测，结论只能算『条件可行』，落地前必须实测复核；报告中会标注来源性质（measured / operator_estimate / synthetic_example）。

## 1. 输入：来源、假设与缺口

| 项 | 值 | 性质 |
|---|---|---|
| 模型 `qwen3-32b` | 结构来自官方 config.json，revision `9216db5781bf21249d130ec9da846c4624c16137`，sha256 `97e295b632839357…` | 厂商官方（已归档） |
| 硬件 `nvidia-h100-sxm-80gb` | NVIDIA H100 SXM，标称 80GB (80.00 GB)，带宽 3.35TB/s | vendor_page_verified |
| 单卡可用显存 | — | 来源性质 **synthetic_example**；演示用构造值：标称 80GB 的 90%（非实测，落地前须用 nvidia-smi 复核） |
| 模型上下文上限 | 40960 tokens | 结构声明 |
| 运行时预留 | 合并预留 可用显存 × 0.1 | 演示用构造值：运行时合并预留 10% |

**待核实字段**（不得默认满足）：

- `nvidia-h100-sxm-80gb`: usable_memory_bytes, supported_weight_precisions, supported_kv_cache_precisions, framework_kernel_support, max_sequence_length_supported

## 2. 部署候选（按状态与卡数排序，不代表性能优劣）

| 候选 | 卡数 | TP | DP | 权重/卡 | KV/卡 | 合计/卡 | 余量 | 状态 |
|---|---|---|---|---|---|---|---|---|
| `qwen3-32b__nvidia-h100-sxm-80gb__tp4_dp1_bf16` | 4 | 4 | 1 | 17.43 GiB | 16.00 GiB | 40.14 GiB | 26.92 GiB | `conditional_feasible` |
| `qwen3-32b__nvidia-h100-sxm-80gb__tp2_dp2_bf16` | 4 | 2 | 2 | 31.96 GiB | 16.00 GiB | 54.67 GiB | 12.39 GiB | `conditional_feasible` |
| `qwen3-32b__nvidia-h100-sxm-80gb__tp8_dp1_bf16` | 8 | 8 | 1 | 10.16 GiB | 8.00 GiB | 24.87 GiB | 42.18 GiB | `conditional_feasible` |
| `qwen3-32b__nvidia-h100-sxm-80gb__tp4_dp2_bf16` | 8 | 4 | 2 | 17.43 GiB | 8.00 GiB | 32.14 GiB | 34.92 GiB | `conditional_feasible` |
| `qwen3-32b__nvidia-h100-sxm-80gb__tp8_dp2_bf16` | 16 | 8 | 2 | 10.16 GiB | 4.00 GiB | 20.87 GiB | 46.18 GiB | `conditional_feasible` |
| `qwen3-32b__nvidia-h100-sxm-80gb__tp1_dp1_bf16` | 1 | 1 | 1 | 61.02 GiB | 64.00 GiB | 131.73 GiB | -64.67 GiB | `infeasible` |
| `qwen3-32b__nvidia-h100-sxm-80gb__tp2_dp1_bf16` | 2 | 2 | 1 | 31.96 GiB | 32.00 GiB | 70.67 GiB | -3.61 GiB | `infeasible` |
| `qwen3-32b__nvidia-h100-sxm-80gb__tp1_dp2_bf16` | 2 | 1 | 2 | 61.02 GiB | 32.00 GiB | 99.73 GiB | -32.67 GiB | `infeasible` |

状态分布：{'conditional_feasible': 5, 'infeasible': 3}

## 3. 每个候选的判定依据

### qwen3-32b__nvidia-h100-sxm-80gb__tp4_dp1_bf16

- 结论：`conditional_feasible`（4 卡 = TP4 × DP1）
- 权重：17.43 GiB / 卡（upper_bound_estimate）
  - 分布：切分 ['attention_k', 'attention_o', 'attention_q', 'attention_v', 'ffn_down', 'ffn_gate', 'ffn_up']；复制 ['attention_k_norm', 'attention_q_norm', 'final_norm', 'layer_norm_in', 'layer_norm_post']；未确认 ['embedding', 'lm_head']
  - 依据：Dense decoder + GQA 结构公式：L=64, h=5120, heads=64, kv_heads=8, head_dim=128, ffn=25600, vocab=151936, tie=False, qk_norm=True；每元素 2 字节
  - 与官方权重总量一致性：结构公式总量与权重索引 metadata.total_size **完全一致**（差 0 字节）
- KV：16.00 GiB / 卡（sharded）；需求 token 262144、预算可容纳 703188、按块分配 262144；依据：每 token 每层 KV = 2 × kv_heads(2/rank) × head_dim(128) × 2 字节；层数 64；每副本并发 32 × 上下文 8192
  - 说明：Paged KV：block_size=16 token/块；每请求对齐到 512 块 → 共 16384 块；对齐放大 0 字节（0.00%）
- 运行时：已知 1 项（合计 6.71 GiB），未知 4 项：['framework_workspace', 'cuda_graph_capture', 'activation_peak', 'framework_overhead']
- 单卡可用：67.06 GiB；合计需求：40.14 GiB；余量：26.92 GiB
- 依赖假设：静态余量 28904316928 字节；结论仅覆盖静态资源，不代表可成功启动或满足 SLA
- 未确认条件：['framework_kernel_support_unverified', 'gpu_usable_memory_not_measured', 'weight_precision_support_unverified:bf16']

### qwen3-32b__nvidia-h100-sxm-80gb__tp2_dp2_bf16

- 结论：`conditional_feasible`（4 卡 = TP2 × DP2）
- 权重：31.96 GiB / 卡（upper_bound_estimate）
  - 分布：切分 ['attention_k', 'attention_o', 'attention_q', 'attention_v', 'ffn_down', 'ffn_gate', 'ffn_up']；复制 ['attention_k_norm', 'attention_q_norm', 'final_norm', 'layer_norm_in', 'layer_norm_post']；未确认 ['embedding', 'lm_head']
  - 依据：Dense decoder + GQA 结构公式：L=64, h=5120, heads=64, kv_heads=8, head_dim=128, ffn=25600, vocab=151936, tie=False, qk_norm=True；每元素 2 字节
  - 与官方权重总量一致性：结构公式总量与权重索引 metadata.total_size **完全一致**（差 0 字节）
- KV：16.00 GiB / 卡（sharded）；需求 token 131072、预算可容纳 232554、按块分配 131072；依据：每 token 每层 KV = 2 × kv_heads(4/rank) × head_dim(128) × 2 字节；层数 64；每副本并发 16 × 上下文 8192
  - 说明：Paged KV：block_size=16 token/块；每请求对齐到 512 块 → 共 8192 块；对齐放大 0 字节（0.00%）
- 运行时：已知 1 项（合计 6.71 GiB），未知 4 项：['framework_workspace', 'cuda_graph_capture', 'activation_peak', 'framework_overhead']
- 单卡可用：67.06 GiB；合计需求：54.67 GiB；余量：12.39 GiB
- 依赖假设：静态余量 13301506048 字节；结论仅覆盖静态资源，不代表可成功启动或满足 SLA
- 未确认条件：['framework_kernel_support_unverified', 'gpu_usable_memory_not_measured', 'weight_precision_support_unverified:bf16']

### qwen3-32b__nvidia-h100-sxm-80gb__tp8_dp1_bf16

- 结论：`conditional_feasible`（8 卡 = TP8 × DP1）
- 权重：10.16 GiB / 卡（upper_bound_estimate）
  - 分布：切分 ['attention_k', 'attention_o', 'attention_q', 'attention_v', 'ffn_down', 'ffn_gate', 'ffn_up']；复制 ['attention_k_norm', 'attention_q_norm', 'final_norm', 'layer_norm_in', 'layer_norm_post']；未确认 ['embedding', 'lm_head']
  - 依据：Dense decoder + GQA 结构公式：L=64, h=5120, heads=64, kv_heads=8, head_dim=128, ffn=25600, vocab=151936, tie=False, qk_norm=True；每元素 2 字节
  - 与官方权重总量一致性：结构公式总量与权重索引 metadata.total_size **完全一致**（差 0 字节）
- KV：8.00 GiB / 卡（sharded）；需求 token 262144、预算可容纳 1644457、按块分配 262144；依据：每 token 每层 KV = 2 × kv_heads(1/rank) × head_dim(128) × 2 字节；层数 64；每副本并发 32 × 上下文 8192
  - 说明：Paged KV：block_size=16 token/块；每请求对齐到 512 块 → 共 16384 块；对齐放大 0 字节（0.00%）
- 运行时：已知 1 项（合计 6.71 GiB），未知 4 项：['framework_workspace', 'cuda_graph_capture', 'activation_peak', 'framework_overhead']
- 单卡可用：67.06 GiB；合计需求：24.87 GiB；余量：42.18 GiB
- 依赖假设：静态余量 45295656960 字节；结论仅覆盖静态资源，不代表可成功启动或满足 SLA
- 未确认条件：['framework_kernel_support_unverified', 'gpu_usable_memory_not_measured', 'weight_precision_support_unverified:bf16']

### qwen3-32b__nvidia-h100-sxm-80gb__tp4_dp2_bf16

- 结论：`conditional_feasible`（8 卡 = TP4 × DP2）
- 权重：17.43 GiB / 卡（upper_bound_estimate）
  - 分布：切分 ['attention_k', 'attention_o', 'attention_q', 'attention_v', 'ffn_down', 'ffn_gate', 'ffn_up']；复制 ['attention_k_norm', 'attention_q_norm', 'final_norm', 'layer_norm_in', 'layer_norm_post']；未确认 ['embedding', 'lm_head']
  - 依据：Dense decoder + GQA 结构公式：L=64, h=5120, heads=64, kv_heads=8, head_dim=128, ffn=25600, vocab=151936, tie=False, qk_norm=True；每元素 2 字节
  - 与官方权重总量一致性：结构公式总量与权重索引 metadata.total_size **完全一致**（差 0 字节）
- KV：8.00 GiB / 卡（sharded）；需求 token 131072、预算可容纳 703188、按块分配 131072；依据：每 token 每层 KV = 2 × kv_heads(2/rank) × head_dim(128) × 2 字节；层数 64；每副本并发 16 × 上下文 8192
  - 说明：Paged KV：block_size=16 token/块；每请求对齐到 512 块 → 共 8192 块；对齐放大 0 字节（0.00%）
- 运行时：已知 1 项（合计 6.71 GiB），未知 4 项：['framework_workspace', 'cuda_graph_capture', 'activation_peak', 'framework_overhead']
- 单卡可用：67.06 GiB；合计需求：32.14 GiB；余量：34.92 GiB
- 依赖假设：静态余量 37494251520 字节；结论仅覆盖静态资源，不代表可成功启动或满足 SLA
- 未确认条件：['framework_kernel_support_unverified', 'gpu_usable_memory_not_measured', 'weight_precision_support_unverified:bf16']

### qwen3-32b__nvidia-h100-sxm-80gb__tp8_dp2_bf16

- 结论：`conditional_feasible`（16 卡 = TP8 × DP2）
- 权重：10.16 GiB / 卡（upper_bound_estimate）
  - 分布：切分 ['attention_k', 'attention_o', 'attention_q', 'attention_v', 'ffn_down', 'ffn_gate', 'ffn_up']；复制 ['attention_k_norm', 'attention_q_norm', 'final_norm', 'layer_norm_in', 'layer_norm_post']；未确认 ['embedding', 'lm_head']
  - 依据：Dense decoder + GQA 结构公式：L=64, h=5120, heads=64, kv_heads=8, head_dim=128, ffn=25600, vocab=151936, tie=False, qk_norm=True；每元素 2 字节
  - 与官方权重总量一致性：结构公式总量与权重索引 metadata.total_size **完全一致**（差 0 字节）
- KV：4.00 GiB / 卡（sharded）；需求 token 131072、预算可容纳 1644457、按块分配 131072；依据：每 token 每层 KV = 2 × kv_heads(1/rank) × head_dim(128) × 2 字节；层数 64；每副本并发 16 × 上下文 8192
  - 说明：Paged KV：block_size=16 token/块；每请求对齐到 512 块 → 共 8192 块；对齐放大 0 字节（0.00%）
- 运行时：已知 1 项（合计 6.71 GiB），未知 4 项：['framework_workspace', 'cuda_graph_capture', 'activation_peak', 'framework_overhead']
- 单卡可用：67.06 GiB；合计需求：20.87 GiB；余量：46.18 GiB
- 依赖假设：静态余量 49590624256 字节；结论仅覆盖静态资源，不代表可成功启动或满足 SLA
- 未确认条件：['dp_replicas_span_multiple_nodes_placement_unverified', 'framework_kernel_support_unverified', 'gpu_usable_memory_not_measured', 'total_gpus_exceed_single_node_capacity', 'weight_precision_support_unverified:bf16']

### qwen3-32b__nvidia-h100-sxm-80gb__tp1_dp1_bf16

- 结论：`infeasible`（1 卡 = TP1 × DP1）
- 权重：61.02 GiB / 卡（upper_bound_estimate）
  - 分布：切分 ['attention_k', 'attention_o', 'attention_q', 'attention_v', 'ffn_down', 'ffn_gate', 'ffn_up']；复制 ['attention_k_norm', 'attention_q_norm', 'final_norm', 'layer_norm_in', 'layer_norm_post']；未确认 ['embedding', 'lm_head']
  - 依据：Dense decoder + GQA 结构公式：L=64, h=5120, heads=64, kv_heads=8, head_dim=128, ffn=25600, vocab=151936, tie=False, qk_norm=True；每元素 2 字节
  - 与官方权重总量一致性：结构公式总量与权重索引 metadata.total_size **完全一致**（差 0 字节）
- KV：64.00 GiB / 卡（sharded）；需求 token 262144、预算可容纳 0、按块分配 262144；依据：每 token 每层 KV = 2 × kv_heads(8/rank) × head_dim(128) × 2 字节；层数 64；每副本并发 32 × 上下文 8192
  - 说明：Paged KV：block_size=16 token/块；每请求对齐到 512 块 → 共 16384 块；对齐放大 0 字节（0.00%）
- 运行时：已知 1 项（合计 6.71 GiB），未知 4 项：['framework_workspace', 'cuda_graph_capture', 'activation_peak', 'framework_overhead']
- 单卡可用：67.06 GiB；合计需求：131.73 GiB；余量：-64.67 GiB
- 原因：每卡需求 141443723264 字节 > 可用 72000000000 字节（超出 69443723264 字节）
- 未确认条件：['framework_kernel_support_unverified', 'gpu_usable_memory_not_measured', 'weight_precision_support_unverified:bf16']

### qwen3-32b__nvidia-h100-sxm-80gb__tp2_dp1_bf16

- 结论：`infeasible`（2 卡 = TP2 × DP1）
- 权重：31.96 GiB / 卡（upper_bound_estimate）
  - 分布：切分 ['attention_k', 'attention_o', 'attention_q', 'attention_v', 'ffn_down', 'ffn_gate', 'ffn_up']；复制 ['attention_k_norm', 'attention_q_norm', 'final_norm', 'layer_norm_in', 'layer_norm_post']；未确认 ['embedding', 'lm_head']
  - 依据：Dense decoder + GQA 结构公式：L=64, h=5120, heads=64, kv_heads=8, head_dim=128, ffn=25600, vocab=151936, tie=False, qk_norm=True；每元素 2 字节
  - 与官方权重总量一致性：结构公式总量与权重索引 metadata.total_size **完全一致**（差 0 字节）
- KV：32.00 GiB / 卡（sharded）；需求 token 262144、预算可容纳 232554、按块分配 262144；依据：每 token 每层 KV = 2 × kv_heads(4/rank) × head_dim(128) × 2 字节；层数 64；每副本并发 32 × 上下文 8192
  - 说明：Paged KV：block_size=16 token/块；每请求对齐到 512 块 → 共 16384 块；对齐放大 0 字节（0.00%）
- 运行时：已知 1 项（合计 6.71 GiB），未知 4 项：['framework_workspace', 'cuda_graph_capture', 'activation_peak', 'framework_overhead']
- 单卡可用：67.06 GiB；合计需求：70.67 GiB；余量：-3.61 GiB
- 原因：每卡需求 75878363136 字节 > 可用 72000000000 字节（超出 3878363136 字节）
- 未确认条件：['framework_kernel_support_unverified', 'gpu_usable_memory_not_measured', 'weight_precision_support_unverified:bf16']

### qwen3-32b__nvidia-h100-sxm-80gb__tp1_dp2_bf16

- 结论：`infeasible`（2 卡 = TP1 × DP2）
- 权重：61.02 GiB / 卡（upper_bound_estimate）
  - 分布：切分 ['attention_k', 'attention_o', 'attention_q', 'attention_v', 'ffn_down', 'ffn_gate', 'ffn_up']；复制 ['attention_k_norm', 'attention_q_norm', 'final_norm', 'layer_norm_in', 'layer_norm_post']；未确认 ['embedding', 'lm_head']
  - 依据：Dense decoder + GQA 结构公式：L=64, h=5120, heads=64, kv_heads=8, head_dim=128, ffn=25600, vocab=151936, tie=False, qk_norm=True；每元素 2 字节
  - 与官方权重总量一致性：结构公式总量与权重索引 metadata.total_size **完全一致**（差 0 字节）
- KV：32.00 GiB / 卡（sharded）；需求 token 131072、预算可容纳 0、按块分配 131072；依据：每 token 每层 KV = 2 × kv_heads(8/rank) × head_dim(128) × 2 字节；层数 64；每副本并发 16 × 上下文 8192
  - 说明：Paged KV：block_size=16 token/块；每请求对齐到 512 块 → 共 8192 块；对齐放大 0 字节（0.00%）
- 运行时：已知 1 项（合计 6.71 GiB），未知 4 项：['framework_workspace', 'cuda_graph_capture', 'activation_peak', 'framework_overhead']
- 单卡可用：67.06 GiB；合计需求：99.73 GiB；余量：-32.67 GiB
- 原因：每卡需求 107083984896 字节 > 可用 72000000000 字节（超出 35083984896 字节）
- 未确认条件：['framework_kernel_support_unverified', 'gpu_usable_memory_not_measured', 'weight_precision_support_unverified:bf16']

## 4. 来源与证据索引

| 来源 | 发布方 | URL | 文件 | sha256 |
|---|---|---|---|---|
| `nvidia_h100_datasheet_page` | nvidia | https://www.nvidia.com/en-us/data-center/h100/ | `data/raw/vendor/nvidia_h100_datacenter_2026-10-15.html` | `aecc301719228f10…` |
| `nvidia_h200_datasheet_page` | nvidia | https://www.nvidia.com/en-us/data-center/h200/ | `data/raw/vendor/nvidia_h200_datacenter_2026-10-15.html` | `73a63384e31550c1…` |

## 5. 明确未做的事（第二阶段边界）

- 不做 TTFT/TPOT/ITL/E2E 的性能预测
- 不根据峰值并发推断所需 GPU 数量
- 不做跨 GPU/TP/DP 的吞吐线性外推
- 不覆盖 PD 分离、MoE EP、MTP/Speculative 的资源优化
- 不做未验证量化实现的精确显存核算
- 不把静态可行方案转换成客户报价
