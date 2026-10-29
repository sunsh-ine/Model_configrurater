# AJ2/AJ3 固定并发容量扫描

> Goodput = 逐请求 success 且 TTFT≤6000ms、TPOT≤40ms 的完成率；与工作点 P99 达标分开。10%/5% 裕量只比较 TPOT P99。

| ISL/OSL | 并发 | 证据 | 运行 | 通过 | 最差 TTFT P99 | 最差 TPOT P99 | 最高吞吐 | 最高 Goodput | 10%裕量 | 5%裕量 | 稳态 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 2048/256 | C10 | exploratory | 1 | 0/1 | 11488.867 | 40.572 | 181.530 | 0.511 | False | False | unknown |
| 2048/256 | C12 | exploratory | 1 | 0/1 | 12466.443 | 39.078 | 186.819 | 0.409 | False | False | unknown |
| 2048/256 | C2 | exploratory | 1 | 1/1 | 1508.113 | 30.448 | 71.784 | 0.280 | True | True | observed_stable_in_window |
| 2048/256 | C4 | exploratory | 1 | 1/1 | 1825.981 | 31.180 | 126.367 | 0.494 | True | True | unknown |
| 2048/256 | C4 | exploratory | 1 | 1/1 | 1816.030 | 32.089 | 126.353 | 0.494 | True | True | unknown |
| 2048/256 | C6 | formal | 5 | 4/5 | 1817.786 | 35.488 | 167.304 | 0.654 | True | True | unstable |
| 2048/256 | C8 | legacy_unbound | 5 | 5/5 | 2220.335 | 39.616 | 198.777 | 0.776 | False | False | observed_stable_in_window |
| 2048/256 | C8 | formal | 5 | 5/5 | 1831.801 | 39.388 | 198.550 | 0.776 | False | False | observed_stable_in_window |
| 4096/256 | C2 | exploratory | 1 | 1/1 | 1914.827 | 30.746 | 62.866 | 0.246 | True | True | unknown |
| 4096/256 | C4 | exploratory | 1 | 1/1 | 3806.928 | 38.391 | 96.683 | 0.378 | False | False | unknown |
| 4096/256 | C6 | exploratory | 1 | 0/1 | 5730.526 | 46.204 | 117.384 | 0.037 | False | False | unknown |
| 4096/256 | C8 | exploratory | 1 | 0/1 | 7088.301 | 54.232 | 135.798 | 0.021 | False | False | unknown |
| 4096/256 | C8 | exploratory | 1 | 0/1 | 7664.234 | 54.637 | 130.381 | 0.041 | False | False | unknown |
| 8192/256 | C1 | exploratory | 1 | 1/1 | 2061.836 | 25.435 | 30.980 | 0.121 | True | True | unknown |
| 8192/256 | C2 | exploratory | 1 | 1/1 | 4138.742 | 34.957 | 47.598 | 0.186 | True | True | unknown |
| 8192/256 | C2 | exploratory | 1 | 1/1 | 4133.404 | 35.226 | 47.824 | 0.187 | True | True | unknown |

## 结论分类

- 最高输出吞吐：198.550 tok/s，来自 aj-e1c8-seed3（ISL2048/OSL256，C8，formal）；
- 最高 SLA Goodput：0.776 req/s，来自 aj-e1c8-seed3（ISL2048/OSL256，C8，formal）；
- 最大可验证固定并发：8（工作点 {'input_len': 2048, 'output_len': 256}）；
- 10%/5% 裕量候选数：0；

> 峰值吞吐通常出现在过载探索点，不等于安全容量；跨 ISL 的吞吐数字不可直接比较。探索点不进入正式推荐。
