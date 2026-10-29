# AJ4 Poisson 开放到达稳定性

| 计划到达率 | 实际到达 | 完成 | 成功 | 最差 TTFT P99 | 最差 TPOT P99 | 输出 tok/s | 稳态 | 队列观测 |
|---|---|---|---|---|---|---|---|---|
| 0.3 | 0.303 | 0.303 | 1/1 | 2858.094 | 39.623 | 77.616 | unstable | unobserved |
| 0.5 | 0.506 | 0.506 | 1/1 | 1364.805 | 37.948 | 129.653 | unknown | unobserved |
| 0.5 | 0.506 | 0.506 | 0/1 | 10469.943 | 40.146 | 129.663 | unknown | unobserved |
| 0.65 | 0.646 | 0.646 | 1/1 | 1396.479 | 39.120 | 165.259 | observed_stable_in_window | unobserved |
| 0.8 | 0.752 | 0.745 | 0/1 | 1670.138 | 39.071 | 190.632 | unstable | unobserved |
| 1.0 | 0.756 | 0.756 | 1/1 | 1376.685 | 39.518 | 193.637 | unknown | unobserved |

## 结论

- 计划 0.3 req/s（exploratory，1 次运行）：实际到达 0.303、完成 0.303 req/s，完成/计划=1.010，成功 1/1，稳态 `unstable`；
- 计划 0.5 req/s（exploratory，1 次运行）：实际到达 0.506、完成 0.506 req/s，完成/计划=1.012，成功 1/1，稳态 `unknown`；
- 计划 0.5 req/s（exploratory，1 次运行）：实际到达 0.506、完成 0.506 req/s，完成/计划=1.012，成功 0/1，稳态 `unknown`；
- 计划 0.65 req/s（insufficient_repeats，1 次运行）：实际到达 0.646、完成 0.646 req/s，完成/计划=0.994，成功 1/1，稳态 `observed_stable_in_window`；
- 计划 0.8 req/s（exploratory，1 次运行）：实际到达 0.752、完成 0.745 req/s，完成/计划=0.931，成功 0/1，稳态 `unstable`；
- 计划 1.0 req/s（exploratory，1 次运行）：实际到达 0.756、完成 0.756 req/s，完成/计划=0.756，成功 1/1，稳态 `unknown`；

- 服务端队列时间序列不可得，所有点 `queue_status=unobserved`。
- 正式重复要求至少 5 个独立 seed；`insufficient_repeats` 的到达率即使窗口内稳定，也只作为观察，不作为容量候选。

> 只有 `observed_stable_in_window`、无失败且达到正式重复要求的到达率才可作为稳态候选；固定并发容量不能直接换算为 Poisson 稳态容量。
