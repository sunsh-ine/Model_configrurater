# AH 客户场景 A-same-gpu-parallel

> 由 `scripts/ah_customer_scenarios.py` 生成；数值来自同一次 `evaluate_plan()`。

- 运行目录：`docs/ah_scenarios/A-same-gpu-parallel`
- 排序目标：throughput

| 目标 | 候选 | GPU | 结论 | 实验完整性 | 校准 |
|---|---|---|---|---|---|
| 推荐（严格） | `sim-native:Qwen/Qwen3-32B@h100_sxm-tp4pp1dp2-c32-n500` | 8 | predicted_pass | — | unknown |
| 未达标参考 | `sim-native:Qwen/Qwen3-32B@h100_sxm-tp4pp1dp2-c32-n500` | 8 | predicted_pass | — | unknown |
| 最少 GPU 达标 | `sim-native:Qwen/Qwen3-32B@h100_sxm-tp4pp1dp2-c32-n500` | 8 | predicted_pass | — | unknown |
| 吞吐最优达标 | `sim-native:Qwen/Qwen3-32B@h100_sxm-tp4pp1dp2-c32-n500` | 8 | predicted_pass | — | unknown |

## 候选证据

| 候选 | GPU | TP/DP | P99 TTFT/TPOT | SLA | 证据 | 实验 | 校准 |
|---|---|---|---|---|---|---|---|
| `qwen3-32b__nvidia-h100-sxm-80gb__tp4_dp1_bf16` | 4 | 4/1 | 153161.51/378.18 | not_evaluable | benchmark_reference | — | unknown |
| `qwen3-32b__nvidia-h100-sxm-80gb__tp8_dp1_bf16` | 8 | 8/1 | 153161.51/378.18 | not_evaluable | benchmark_reference | — | unknown |

## 模拟候选

| 候选 | GPU | TP/DP | P99 TTFT/TPOT | 结论 | 实验完整性 | 重复性 | 可推荐 |
|---|---|---|---|---|---|---|---|
| `sim-sweeper:Qwen/Qwen3-32B@h100_sxm-tp8pp1dp1etp1ep1-c32-ctx8192` | 8 | 8/1 | None/None | insufficient_prediction | — | — | 否 |
| `sim-sweeper:Qwen/Qwen3-32B@h100_sxm-tp8pp1dp1etp1ep1-c32-ctx16384` | 8 | 8/1 | None/None | insufficient_prediction | — | — | 否 |
| `sim-sweeper:Qwen/Qwen3-32B@h100_sxm-tp8pp1dp1etp1ep1-c224-ctx8192` | 8 | 8/1 | None/None | insufficient_prediction | — | — | 否 |
| `sim-sweeper:Qwen/Qwen3-32B@h100_sxm-tp8pp1dp1etp1ep1-c208-ctx8192` | 8 | 8/1 | None/None | insufficient_prediction | — | — | 否 |
| `sim-native:Qwen/Qwen3-32B@h100_sxm-tp4pp1dp1-c32-n500` | 4 | 4/1 | 8359.441161/50.170601298039216 | predicted_fail | — | — | 是 |
| `sim-native:Qwen/Qwen3-32B@h100_sxm-tp4pp1dp2-c32-n500` | 8 | 4/2 | 4448.276797/28.824056776470616 | predicted_pass | — | — | 是 |
| `sim-native:Qwen/Qwen3-32B@h100_sxm-tp8pp1dp1-c32-n500` | 8 | 8/1 | 5201.827252000001/31.45861707450993 | predicted_pass | — | — | 是 |

> 模拟预测不是实测；没有真实 GPU Benchmark 时校准状态保持 unknown。
