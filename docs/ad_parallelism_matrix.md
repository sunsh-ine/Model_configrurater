# AISimulate 并行策略能力矩阵

- 工具：aisimulate 0.12.1
- 工作负载：ISL8192 / OSL256 / C32 / 500 请求 / BF16 / vLLM 0.24.0

| 组合 | TP×DP×PP | 状态 | 说明 |
|---|---|---|---|
| Qwen/Qwen3-8B @ h100_sxm | 1×1×1 | supported | 已生成完整 P99 |
| Qwen/Qwen3-8B @ h100_sxm | 1×2×1 | supported | 已生成完整 P99 |
| Qwen/Qwen3-8B @ h100_sxm | 1×4×1 | supported | 已生成完整 P99 |
| Qwen/Qwen3-8B @ h100_sxm | 1×1×2 | supported | 已生成完整 P99 |
| Qwen/Qwen3-8B @ h200_sxm | 1×1×1 | supported | 已生成完整 P99 |
| Qwen/Qwen3-8B @ h200_sxm | 1×2×1 | supported | 已生成完整 P99 |
| Qwen/Qwen3-32B @ h100_sxm | 8×1×1 | supported | 已生成完整 P99 |
| Qwen/Qwen3-32B @ h100_sxm | 4×2×1 | supported | 已生成完整 P99 |
| Qwen/Qwen3-32B @ h200_sxm | 8×1×1 | supported | 已生成完整 P99 |
| Qwen/Qwen3-32B @ h200_sxm | 4×2×1 | supported | 已生成完整 P99 |
| Qwen/Qwen3-32B @ h200_sxm | 4×1×2 | supported | 已生成完整 P99 |
