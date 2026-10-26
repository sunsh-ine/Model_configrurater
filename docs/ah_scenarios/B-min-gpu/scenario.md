# AH 客户场景 B-min-gpu

> 由 `scripts/ah_customer_scenarios.py` 生成；数值来自同一次 `evaluate_plan()`。

- 运行目录：`docs/ah_scenarios/B-min-gpu`
- 排序目标：throughput

| 目标 | 候选 | GPU | 结论 | 实验完整性 | 校准 |
|---|---|---|---|---|---|
| 推荐（严格） | `sim-native:Qwen/Qwen3-8B@h100_sxm-tp1pp1dp4-c32-n500` | 4 | predicted_pass | — | unknown |
| 未达标参考 | `sim-native:Qwen/Qwen3-8B@h100_sxm-tp1pp1dp4-c32-n500` | 4 | predicted_pass | — | unknown |
| 最少 GPU 达标 | `sim-native:Qwen/Qwen3-8B@h100_sxm-tp1pp1dp2-c32-n500` | 2 | predicted_pass | — | unknown |
| 吞吐最优达标 | `sim-native:Qwen/Qwen3-8B@h100_sxm-tp1pp1dp4-c32-n500` | 4 | predicted_pass | — | unknown |

## 候选证据

| 候选 | GPU | TP/DP | P99 TTFT/TPOT | SLA | 证据 | 实验 | 校准 |
|---|---|---|---|---|---|---|---|
| `qwen3-8b__nvidia-h100-sxm-80gb__tp1_dp1_bf16` | 1 | 1/1 | None/None | None | unknown | — | unknown |

## 模拟候选

| 候选 | GPU | TP/DP | P99 TTFT/TPOT | 结论 | 实验完整性 | 重复性 | 可推荐 |
|---|---|---|---|---|---|---|---|
| `sim-native:Qwen/Qwen3-8B@h100_sxm-tp1pp1dp1-c32-n500` | 1 | 1/1 | 6609.393869999999/46.599841372549015 | predicted_fail | — | — | 是 |
| `sim-native:Qwen/Qwen3-8B@h100_sxm-tp1pp1dp2-c32-n500` | 2 | 1/2 | 3486.4293139999995/27.420675007843208 | predicted_pass | — | — | 是 |
| `sim-native:Qwen/Qwen3-8B@h100_sxm-tp1pp1dp4-c32-n500` | 4 | 1/4 | 1827.7921460000002/16.0054336470589 | predicted_pass | — | — | 是 |

> 模拟预测不是实测；没有真实 GPU Benchmark 时校准状态保持 unknown。
