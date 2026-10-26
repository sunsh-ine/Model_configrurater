# AH 客户场景 C-open-arrival-risk

> 由 `scripts/ah_customer_scenarios.py` 生成；数值来自同一次 `evaluate_plan()`。

- 运行目录：`docs/ah_scenarios/C-open-arrival-risk`
- 排序目标：throughput

| 目标 | 候选 | GPU | 结论 | 实验完整性 | 校准 |
|---|---|---|---|---|---|
| 推荐（严格） | 无 | — | — | — | — |
| 未达标参考 | 无 | — | — | — | — |
| 最少 GPU 达标 | 无 | — | — | — | — |
| 吞吐最优达标 | 无 | — | — | — | — |

## 候选证据

| 候选 | GPU | TP/DP | P99 TTFT/TPOT | SLA | 证据 | 实验 | 校准 |
|---|---|---|---|---|---|---|---|
| `qwen3-8b__nvidia-h200-sxm-141gb__tp1_dp1_bf16` | 1 | 1/1 | None/None | None | unknown | — | unknown |

## 模拟候选

| 候选 | GPU | TP/DP | P99 TTFT/TPOT | 结论 | 实验完整性 | 重复性 | 可推荐 |
|---|---|---|---|---|---|---|---|
| `sim-native:Qwen/Qwen3-8B@h200_sxm-tp1pp1dp2-cNone-n500-r5-s1` | 2 | 1/2 | 930.5687098523485/44.95807737647059 | predicted_fail | complete | mixed | 否 |
| `sim-native:Qwen/Qwen3-8B@h200_sxm-tp1pp1dp2-cNone-n500-r5-s2` | 2 | 1/2 | 869.4920468822675/43.209455133333414 | predicted_fail | complete | mixed | 否 |
| `sim-native:Qwen/Qwen3-8B@h200_sxm-tp1pp1dp2-cNone-n500-r5-s3` | 2 | 1/2 | 767.225608350811/32.30179785098082 | predicted_pass | complete | mixed | 否 |
| `sim-native:Qwen/Qwen3-8B@h200_sxm-tp1pp1dp2-cNone-n500-r5-s4` | 2 | 1/2 | 866.3116121074054/35.86148418823529 | predicted_pass | complete | mixed | 否 |
| `sim-native:Qwen/Qwen3-8B@h200_sxm-tp1pp1dp2-cNone-n500-r5-s5` | 2 | 1/2 | 854.2590297111892/35.675425713725375 | predicted_pass | complete | mixed | 否 |

> 模拟预测不是实测；没有真实 GPU Benchmark 时校准状态保持 unknown。
