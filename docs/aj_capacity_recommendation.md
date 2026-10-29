# AJ6 真实容量推荐

- 最高输出吞吐：198.550 tok/s
- 最高 SLA Goodput：0.776 req/s
- 最大正式验证固定并发：8（工作点 {'input_len': 2048, 'output_len': 256}）
- 10% 裕量候选：C[]
- 5% 裕量候选：C[]
- 最大窗口稳态到达率：None req/s
- 推荐：fixed_concurrency = 8

## 理由

- 正式固定并发满足 SLA，但无内部裕量

## 需要明确的限制

- ISL2048/C6 的 TPOT P99 35.488 ms 有内部裕量，但正式重复仅 4/5 通过（存在失败请求），不能作为裕量安全容量。
- 历史正式运行未绑定冻结 runtime_config_sha256（ai-e1c8-seed1, ai-e1c8-seed2, ai-e1c8-seed3, ai-e1c8-seed4, ai-e1c8-seed5），只作延迟参考，不参与容量推荐。
- Poisson 到达率只有 1 次 500 请求窗口，未达到 5 次正式重复，只在开放到达报告中作为窗口观察，不作为容量候选。
- 最高吞吐 198.550 tok/s 来自 aj-e1c8-seed3（formal），峰值吞吐不代表 SLA 安全容量。

## 固定并发（正式优先）

| ISL/OSL | C | 证据 | 运行 | 通过 | 最差 TPOT P99 | 10% 裕量 | 5% 裕量 |
|---|---|---|---|---|---|---|---|
| 2048/256 | C10 | exploratory | 1 | 0/1 | 40.572 | False | False |
| 2048/256 | C12 | exploratory | 1 | 0/1 | 39.078 | False | False |
| 2048/256 | C2 | exploratory | 1 | 1/1 | 30.448 | True | True |
| 2048/256 | C4 | exploratory | 1 | 1/1 | 31.180 | True | True |
| 2048/256 | C4 | exploratory | 1 | 1/1 | 32.089 | True | True |
| 2048/256 | C6 | formal | 5 | 4/5 | 35.488 | True | True |
| 2048/256 | C8 | legacy_unbound | 5 | 5/5 | 39.616 | False | False |
| 2048/256 | C8 | formal | 5 | 5/5 | 39.388 | False | False |
| 4096/256 | C2 | exploratory | 1 | 1/1 | 30.746 | True | True |
| 4096/256 | C4 | exploratory | 1 | 1/1 | 38.391 | False | False |
| 4096/256 | C6 | exploratory | 1 | 0/1 | 46.204 | False | False |
| 4096/256 | C8 | exploratory | 1 | 0/1 | 54.232 | False | False |
| 4096/256 | C8 | exploratory | 1 | 0/1 | 54.637 | False | False |
| 8192/256 | C1 | exploratory | 1 | 1/1 | 25.435 | True | True |
| 8192/256 | C2 | exploratory | 1 | 1/1 | 34.957 | True | True |
| 8192/256 | C2 | exploratory | 1 | 1/1 | 35.226 | True | True |

## 限制

- 固定并发容量不等于 Poisson 稳态容量，不能互相换算。
- 最大吞吐不等于 SLA 安全容量；探索点不作为正式推荐。
- 没有全局队列时间序列时，Poisson 稳态最多是窗口内观察。
