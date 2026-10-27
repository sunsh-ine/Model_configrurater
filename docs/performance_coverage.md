# 性能预测证据覆盖矩阵

> 本文件与 `docs/performance_coverage.json` 由 `python scripts/build_performance_coverage.py` 生成；请勿手改。

> **预测不等于实测。** 本矩阵只回答“现有数据能支持哪一层结论”，不把扫描器均值、公开参考数据或历史 Excel 记录升级为 SLA 证据。

## 1. 分级口径

| 级别 | 含义 | 能回答什么 | 不能回答什么 |
|---|---|---|---|
| 1 static_only | 只有 Catalog 资源规格 | 能否装下、卡数是否够 | 性能、SLA |
| 2 simulated_scan_only | 扫描器稳态单点（均值） | 候选枚举与相对排序 | P99 SLA |
| 3 simulated_native_p99 | 原生回放 P99（500 请求窗口） | 预测达标/未达标 | 真实实测 |
| 4 measured_benchmark | 目标配置的真实 Benchmark | 可作为实测证据 | 仍需审批口径 |

公开参考数据与历史 Excel 记录单列为 `public_reference` / `internal_reference`：它们不是上述四级，不参与 SLA 判定。

## 2. Catalog 静态能力

| 模型 | 硬件 | 最高覆盖级别 | 已有证据数 | 可做成本分析 |
|---|---|---|---|---|
| qwen3-8b | NVIDIA GeForce RTX 3090 | static_only | 0 | 否 |
| qwen3-8b | NVIDIA H100 SXM | simulated_native_p99 | 37 | 是 |
| qwen3-8b | NVIDIA H200 SXM | simulated_native_p99 | 21 | 否 |
| qwen3-32b | NVIDIA GeForce RTX 3090 | static_only | 0 | 否 |
| qwen3-32b | NVIDIA H100 SXM | simulated_native_p99 | 182 | 是 |
| qwen3-32b | NVIDIA H200 SXM | simulated_native_p99 | 19 | 否 |

`static_only` 是矩阵级别，不是一条独立证据；`evidence_by_level` 只统计有实际证据的级别（2/3/4）。
未支持的模型（解析器会拒绝，不做硬算）：
- `qwen3-30b-a3b`：MoE 结构（专家数与专家路由影响权重显存），本轮公式只覆盖 Dense； 如需支持需先明确 expert 权重布局与激活专家的显存口径。
- `deepseek-v3`：MoE + MLA（Multi-head Latent Attention），KV 结构与 Dense GQA 不同，本轮不覆盖。

## 3. 工作点覆盖

按“模型 + 精度 + 硬件 + 框架/版本 + TP/PP/DP + ISL/OSL/并发/ctx”分组；样本窗口（请求数）与数据集记录在每条证据上，不参与分组；不同工作点之间**不做插值**。

| 工作点 | 模型/精度 | 硬件/并行 | 负载 | 最高级别 | SLA 可用 | TTFT P99 (ms) | TPOT P99 (ms) | 吞吐 (tok/s) | 阻塞原因 |
|---|---|---|---|---|---|---|---|---|---|
| `e114fa18647bdf13` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP8/PP1/DP1 | ISL8192/OSL256/C1/ctx8192 | simulated_scan_only | 否 | — | — | 147.4 | 扫描器输出是稳态单点（均值口径），不含 P99；不能用于 SLA 判定 |
| `67a111ed3efbbeeb` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP8/PP1/DP1 | ISL8192/OSL256/C2/ctx8192 | simulated_scan_only | 否 | — | — | 242.482 | 扫描器输出是稳态单点（均值口径），不含 P99；不能用于 SLA 判定 |
| `95d102b09d1c1edf` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP8/PP1/DP1 | ISL8192/OSL256/C3/ctx8192 | simulated_scan_only | 否 | — | — | 331.277 | 扫描器输出是稳态单点（均值口径），不含 P99；不能用于 SLA 判定 |
| `11732d5ace5c724c` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP8/PP1/DP1 | ISL8192/OSL256/C4/ctx8192 | simulated_scan_only | 否 | — | — | 412.012 | 扫描器输出是稳态单点（均值口径），不含 P99；不能用于 SLA 判定 |
| `ec2a115380769b06` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP8/PP1/DP1 | ISL8192/OSL256/C4/ctx16384 | simulated_scan_only | 否 | — | — | 380.379 | 扫描器输出是稳态单点（均值口径），不含 P99；不能用于 SLA 判定 |
| `0696e87f2c79c4ce` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP8/PP1/DP1 | ISL8192/OSL256/C5/ctx8192 | simulated_scan_only | 否 | — | — | 471.729 | 扫描器输出是稳态单点（均值口径），不含 P99；不能用于 SLA 判定 |
| `8dea04dbd8fdf509` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP8/PP1/DP1 | ISL8192/OSL256/C6/ctx8192 | simulated_scan_only | 否 | — | — | 533.154 | 扫描器输出是稳态单点（均值口径），不含 P99；不能用于 SLA 判定 |
| `0325f3e8928d1f58` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP8/PP1/DP1 | ISL8192/OSL256/C6/ctx16384 | simulated_scan_only | 否 | — | — | 495.673 | 扫描器输出是稳态单点（均值口径），不含 P99；不能用于 SLA 判定 |
| `125b3f6d92292221` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP8/PP1/DP1 | ISL8192/OSL256/C7/ctx8192 | simulated_scan_only | 否 | — | — | 581.916 | 扫描器输出是稳态单点（均值口径），不含 P99；不能用于 SLA 判定 |
| `2128f73ff1d3dd5e` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP4/PP1/DP1 | ISL1024/OSL1024/C8 | simulated_native_p99 | 是 | 260.4617910000088 | 8.564802355816468 | 900.8776991343514 | — |
| `b8fd930f3de54ff5` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP4/PP1/DP1 | ISL8192/OSL256/C8 | simulated_native_p99 | 是 | 1159.756941 | 17.848578929412408 | 420.4521057972523 | — |
| `15382965162aed3d` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP8/PP1/DP1 | ISL1024/OSL1024/C8 | simulated_native_p99 | 是 | 169.22123400000055 | 5.983962341153699 | 1292.8510420687026 | — |
| `3f18c2342b047824` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP4/PP1/DP2 | ISL1024/OSL1024/C8 | simulated_native_p99 | 是 | 137.71360100002494 | 8.618903230694222 | 908.1847846247342 | — |
| `bc12c27dd8b4ca66` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP4/PP1/DP2 | ISL8192/OSL256/C8 | simulated_native_p99 | 是 | 861.7461110000002 | 12.326389607843286 | 591.6756451215094 | — |
| `a80c47d9677b4de0` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP8/PP1/DP1 | ISL8192/OSL256/C8 | simulated_native_p99 | 是 | 727.2969240000001 | 11.230072717647314 | 667.5295614199107 | — |
| `ff5a0f9536af5aae` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP8/PP1/DP1 | ISL8192/OSL256/C8/ctx8192 | simulated_scan_only | 否 | — | — | 627.163 | 扫描器输出是稳态单点（均值口径），不含 P99；不能用于 SLA 判定 |
| `beca4c364ed5e408` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP8/PP1/DP1 | ISL8192/OSL256/C8/ctx16384 | simulated_scan_only | 否 | — | — | 586.651 | 扫描器输出是稳态单点（均值口径），不含 P99；不能用于 SLA 判定 |
| `771f7b2bcd5a82e1` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP8/PP1/DP1 | ISL8192/OSL256/C9/ctx8192 | simulated_scan_only | 否 | — | — | 659.523 | 扫描器输出是稳态单点（均值口径），不含 P99；不能用于 SLA 判定 |
| `a2488d3e969a6617` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP4/PP1/DP1 | ISL8192/OSL256/C9/ctx8192 | simulated_scan_only | 否 | — | — | 421.836 | 扫描器输出是稳态单点（均值口径），不含 P99；不能用于 SLA 判定 |
| `6ade9df2baa68705` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP8/PP1/DP1 | ISL8192/OSL256/C10/ctx8192 | simulated_scan_only | 否 | — | — | 682.636 | 扫描器输出是稳态单点（均值口径），不含 P99；不能用于 SLA 判定 |
| `543fdfee4f992652` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP4/PP1/DP1 | ISL8192/OSL256/C10/ctx8192 | simulated_scan_only | 否 | — | — | 441.502 | 扫描器输出是稳态单点（均值口径），不含 P99；不能用于 SLA 判定 |
| `e8917d3a44214826` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP8/PP1/DP1 | ISL8192/OSL256/C11/ctx8192 | simulated_scan_only | 否 | — | — | 719.531 | 扫描器输出是稳态单点（均值口径），不含 P99；不能用于 SLA 判定 |
| `113744dae9b0247f` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP4/PP1/DP1 | ISL8192/OSL256/C11/ctx8192 | simulated_scan_only | 否 | — | — | 460.396 | 扫描器输出是稳态单点（均值口径），不含 P99；不能用于 SLA 判定 |
| `cd2416ca1515fffe` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP8/PP1/DP1 | ISL8192/OSL256/C12/ctx8192 | simulated_scan_only | 否 | — | — | 742.676 | 扫描器输出是稳态单点（均值口径），不含 P99；不能用于 SLA 判定 |
| `3660011c8131a2a2` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP4/PP1/DP1 | ISL8192/OSL256/C12/ctx8192 | simulated_scan_only | 否 | — | — | 477.672 | 扫描器输出是稳态单点（均值口径），不含 P99；不能用于 SLA 判定 |
| `f86c150de451d156` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP8/PP1/DP1 | ISL8192/OSL256/C13/ctx8192 | simulated_scan_only | 否 | — | — | 766.425 | 扫描器输出是稳态单点（均值口径），不含 P99；不能用于 SLA 判定 |
| `be6a8c0f4f347941` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP4/PP1/DP1 | ISL8192/OSL256/C13/ctx8192 | simulated_scan_only | 否 | — | — | 487.888 | 扫描器输出是稳态单点（均值口径），不含 P99；不能用于 SLA 判定 |
| `e1a23326a4946979` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP8/PP1/DP1 | ISL8192/OSL256/C14/ctx8192 | simulated_scan_only | 否 | — | — | 788.07 | 扫描器输出是稳态单点（均值口径），不含 P99；不能用于 SLA 判定 |
| `91b7a00751776b58` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP4/PP1/DP1 | ISL8192/OSL256/C14/ctx8192 | simulated_scan_only | 否 | — | — | 508.159 | 扫描器输出是稳态单点（均值口径），不含 P99；不能用于 SLA 判定 |
| `b7c854bacdfafcdb` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP8/PP1/DP1 | ISL8192/OSL256/C15/ctx8192 | simulated_scan_only | 否 | — | — | 807.007 | 扫描器输出是稳态单点（均值口径），不含 P99；不能用于 SLA 判定 |
| `70aaf2555e98ea58` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP4/PP1/DP1 | ISL8192/OSL256/C15/ctx8192 | simulated_scan_only | 否 | — | — | 521.185 | 扫描器输出是稳态单点（均值口径），不含 P99；不能用于 SLA 判定 |
| `091e9007facb07f0` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP8/PP1/DP1 | ISL8192/OSL256/C16/ctx8192 | simulated_scan_only | 否 | — | — | 821.667 | 扫描器输出是稳态单点（均值口径），不含 P99；不能用于 SLA 判定 |
| `766e857c7f460806` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP4/PP1/DP1 | ISL8192/OSL256/C16/ctx8192 | simulated_scan_only | 否 | — | — | 525.592 | 扫描器输出是稳态单点（均值口径），不含 P99；不能用于 SLA 判定 |
| `dd57b60bf81b7bf8` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP8/PP1/DP1 | ISL8192/OSL256/C20/ctx8192 | simulated_scan_only | 否 | — | — | 875.575 | 扫描器输出是稳态单点（均值口径），不含 P99；不能用于 SLA 判定 |
| `7fd8638131434e0e` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP4/PP1/DP1 | ISL8192/OSL256/C20/ctx8192 | simulated_scan_only | 否 | — | — | 562.9 | 扫描器输出是稳态单点（均值口径），不含 P99；不能用于 SLA 判定 |
| `b64956a68d2a30c3` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP8/PP1/DP1 | ISL8192/OSL256/C20/ctx16384 | simulated_scan_only | 否 | — | — | 839.538 | 扫描器输出是稳态单点（均值口径），不含 P99；不能用于 SLA 判定 |
| `8751e8f7eb137085` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP8/PP1/DP1 | ISL8192/OSL256/C24/ctx8192 | simulated_scan_only | 否 | — | — | 924.067 | 扫描器输出是稳态单点（均值口径），不含 P99；不能用于 SLA 判定 |
| `4c86eaf27e96a7d0` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP2/PP1/DP1 | ISL8192/OSL256/C24/ctx8192 | simulated_scan_only | 否 | — | — | 343.701 | 扫描器输出是稳态单点（均值口径），不含 P99；不能用于 SLA 判定 |
| `af34676b78448a35` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP4/PP1/DP1 | ISL8192/OSL256/C24/ctx8192 | simulated_scan_only | 否 | — | — | 592.153 | 扫描器输出是稳态单点（均值口径），不含 P99；不能用于 SLA 判定 |
| `7fe271526a73c947` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP8/PP1/DP1 | ISL8192/OSL256/C24/ctx16384 | simulated_scan_only | 否 | — | — | 889.832 | 扫描器输出是稳态单点（均值口径），不含 P99；不能用于 SLA 判定 |
| `5adc9d4a4816962c` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP8/PP1/DP1 | ISL8192/OSL256/C28/ctx8192 | simulated_scan_only | 否 | — | — | 964.001 | 扫描器输出是稳态单点（均值口径），不含 P99；不能用于 SLA 判定 |
| `b19b226c076d9219` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP2/PP1/DP1 | ISL8192/OSL256/C28/ctx8192 | simulated_scan_only | 否 | — | — | 356.703 | 扫描器输出是稳态单点（均值口径），不含 P99；不能用于 SLA 判定 |
| `fae3bbaea084ba77` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP4/PP1/DP1 | ISL8192/OSL256/C28/ctx8192 | simulated_scan_only | 否 | — | — | 614.857 | 扫描器输出是稳态单点（均值口径），不含 P99；不能用于 SLA 判定 |
| `57ca6dfac1fc5611` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP8/PP1/DP1 | ISL8192/OSL256/C28/ctx16384 | simulated_scan_only | 否 | — | — | 931.534 | 扫描器输出是稳态单点（均值口径），不含 P99；不能用于 SLA 判定 |
| `b24ed775ea26de7f` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP8/PP1/DP1 | ISL8192/OSL256/C32 | simulated_native_p99 | 是 | 5201.827252000001 | 31.45861707450993 | 990.888548064229 | — |
| `2babda0ef1d52fc1` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP4/PP1/DP1 | ISL8192/OSL256/C32 | simulated_native_p99 | 是 | 8359.441161 | 50.170601298039216 | 621.8971855351141 | — |
| `97ad1b8356c3a7f5` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP4/PP1/DP1 | ISL1024/OSL1024/C32 | simulated_native_p99 | 是 | 1044.013729 | 11.304065113391985 | 2759.5234009630535 | — |
| `434f35dff4d1ed9c` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP8/PP1/DP1 | ISL1024/OSL1024/C32 | simulated_native_p99 | 是 | 678.928668 | 7.646308508308902 | 4071.268859775253 | — |
| `1c5a3c55b2a4532d` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP4/PP1/DP2 | ISL1024/OSL1024/C32 | simulated_native_p99 | 是 | 520.8245929999999 | 9.653310475073386 | 3229.7293058361915 | — |
| `0b208674de8acc0d` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP4/PP1/DP2 | ISL8192/OSL256/C32 | simulated_native_p99 | 是 | 4448.276797 | 28.824056776470616 | 1062.4468369728693 | — |
| `3408a030ce23afc5` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP8/PP1/DP1 | ISL8192/OSL256/C32/ctx8192 | simulated_scan_only | 否 | — | — | 997.419 | 扫描器输出是稳态单点（均值口径），不含 P99；不能用于 SLA 判定 |
| `19184bfd8cf9befb` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP2/PP1/DP1 | ISL8192/OSL256/C32/ctx8192 | simulated_scan_only | 否 | — | — | 367.138 | 扫描器输出是稳态单点（均值口径），不含 P99；不能用于 SLA 判定 |
| `bf5ec0607a75d169` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP4/PP1/DP1 | ISL8192/OSL256/C32/ctx8192 | simulated_scan_only | 否 | — | — | 633.0 | 扫描器输出是稳态单点（均值口径），不含 P99；不能用于 SLA 判定 |
| `e7cd78903fb4871f` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP8/PP1/DP1 | ISL8192/OSL256/C32/ctx16384 | simulated_scan_only | 否 | — | — | 966.671 | 扫描器输出是稳态单点（均值口径），不含 P99；不能用于 SLA 判定 |
| `ee5910df4355eb72` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP8/PP1/DP1 | ISL8192/OSL256/C40/ctx8192 | simulated_scan_only | 否 | — | — | 1053.705 | 扫描器输出是稳态单点（均值口径），不含 P99；不能用于 SLA 判定 |
| `2a01a8acd4703a92` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP4/PP1/DP1 | ISL8192/OSL256/C40/ctx8192 | simulated_scan_only | 否 | — | — | 666.567 | 扫描器输出是稳态单点（均值口径），不含 P99；不能用于 SLA 判定 |
| `f5cf515046f7370a` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP8/PP1/DP1 | ISL8192/OSL256/C40/ctx16384 | simulated_scan_only | 否 | — | — | 1026.269 | 扫描器输出是稳态单点（均值口径），不含 P99；不能用于 SLA 判定 |
| `53cc555f96eac2b5` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP8/PP1/DP1 | ISL8192/OSL256/C48/ctx8192 | simulated_scan_only | 否 | — | — | 1089.946 | 扫描器输出是稳态单点（均值口径），不含 P99；不能用于 SLA 判定 |
| `2173c44507a85dd2` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP8/PP1/DP1 | ISL8192/OSL256/C48/ctx16384 | simulated_scan_only | 否 | — | — | 1065.332 | 扫描器输出是稳态单点（均值口径），不含 P99；不能用于 SLA 判定 |
| `99c53113ba92dcf1` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP8/PP1/DP1 | ISL8192/OSL256/C56/ctx8192 | simulated_scan_only | 否 | — | — | 1116.201 | 扫描器输出是稳态单点（均值口径），不含 P99；不能用于 SLA 判定 |
| `9d7130ff46b49635` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP8/PP1/DP1 | ISL8192/OSL256/C56/ctx16384 | simulated_scan_only | 否 | — | — | 1093.891 | 扫描器输出是稳态单点（均值口径），不含 P99；不能用于 SLA 判定 |
| `766f3b2faa70b2ee` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP4/PP1/DP1 | ISL1024/OSL1024/C64 | simulated_native_p99 | 是 | 2091.052369 | 14.00218116324539 | 4476.380464100694 | — |
| `c5149bdfb887f5a6` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP4/PP1/DP1 | ISL8192/OSL256/C64 | simulated_native_p99 | 是 | 18030.104925999993 | 91.11349433333312 | 691.290490942291 | — |
| `650c421362fc9332` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP8/PP1/DP1 | ISL1024/OSL1024/C64 | simulated_native_p99 | 是 | 1358.8681519999998 | 9.276825615835756 | 6747.2858397137925 | — |
| `d18f3057e1574ad0` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP4/PP1/DP2 | ISL1024/OSL1024/C64 | simulated_native_p99 | 是 | 1044.013729 | 11.332671839687185 | 5519.110887783805 | — |
| `954748fe3540339f` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP4/PP1/DP2 | ISL8192/OSL256/C64 | simulated_native_p99 | 是 | 9264.708154999998 | 50.170601298039216 | 1242.203008622216 | — |
| `b8e342f34f21f1c8` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP8/PP1/DP1 | ISL8192/OSL256/C64 | simulated_native_p99 | 是 | 11211.532603000005 | 56.43859718823514 | 1115.0950375870086 | — |
| `fbfb90726b5ec92c` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP8/PP1/DP1 | ISL8192/OSL256/C64/ctx8192 | simulated_scan_only | 否 | — | — | 1137.693 | 扫描器输出是稳态单点（均值口径），不含 P99；不能用于 SLA 判定 |
| `efc869a83af43960` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP8/PP1/DP1 | ISL8192/OSL256/C64/ctx16384 | simulated_scan_only | 否 | — | — | 1117.425 | 扫描器输出是稳态单点（均值口径），不含 P99；不能用于 SLA 判定 |
| `9848028666ab027f` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP8/PP1/DP1 | ISL8192/OSL256/C80/ctx8192 | simulated_scan_only | 否 | — | — | 1172.987 | 扫描器输出是稳态单点（均值口径），不含 P99；不能用于 SLA 判定 |
| `d0527cb85af64531` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP8/PP1/DP1 | ISL8192/OSL256/C80/ctx16384 | simulated_scan_only | 否 | — | — | 1156.658 | 扫描器输出是稳态单点（均值口径），不含 P99；不能用于 SLA 判定 |
| `29171a2b3c6ece1c` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP8/PP1/DP1 | ISL8192/OSL256/C96/ctx8192 | simulated_scan_only | 否 | — | — | 1195.097 | 扫描器输出是稳态单点（均值口径），不含 P99；不能用于 SLA 判定 |
| `835deb2bc1878a91` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP4/PP1/DP1 | ISL8192/OSL256/C96/ctx8192 | simulated_scan_only | 否 | — | — | 741.622 | 扫描器输出是稳态单点（均值口径），不含 P99；不能用于 SLA 判定 |
| `fe9a174d880e3b91` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP8/PP1/DP1 | ISL8192/OSL256/C96/ctx16384 | simulated_scan_only | 否 | — | — | 1181.522 | 扫描器输出是稳态单点（均值口径），不含 P99；不能用于 SLA 判定 |
| `c2a4b5146cfadde5` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP8/PP1/DP1 | ISL8192/OSL256/C112/ctx8192 | simulated_scan_only | 否 | — | — | 1211.899 | 扫描器输出是稳态单点（均值口径），不含 P99；不能用于 SLA 判定 |
| `2361e0912df0523a` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP8/PP1/DP1 | ISL8192/OSL256/C112/ctx16384 | simulated_scan_only | 否 | — | — | 1200.777 | 扫描器输出是稳态单点（均值口径），不含 P99；不能用于 SLA 判定 |
| `e9870336a49f5a5e` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP8/PP1/DP1 | ISL8192/OSL256/C128/ctx8192 | simulated_scan_only | 否 | — | — | 1223.312 | 扫描器输出是稳态单点（均值口径），不含 P99；不能用于 SLA 判定 |
| `106843122408ee0e` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP8/PP1/DP1 | ISL8192/OSL256/C128/ctx16384 | simulated_scan_only | 否 | — | — | 1213.677 | 扫描器输出是稳态单点（均值口径），不含 P99；不能用于 SLA 判定 |
| `8f6f74fe2365ef1f` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP8/PP1/DP1 | ISL8192/OSL256/C144/ctx8192 | simulated_scan_only | 否 | — | — | 1231.57 | 扫描器输出是稳态单点（均值口径），不含 P99；不能用于 SLA 判定 |
| `bc31341817f19f7e` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP8/PP1/DP1 | ISL8192/OSL256/C144/ctx16384 | simulated_scan_only | 否 | — | — | 1223.498 | 扫描器输出是稳态单点（均值口径），不含 P99；不能用于 SLA 判定 |
| `078885fc93ff64e7` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP8/PP1/DP1 | ISL8192/OSL256/C160/ctx8192 | simulated_scan_only | 否 | — | — | 1238.218 | 扫描器输出是稳态单点（均值口径），不含 P99；不能用于 SLA 判定 |
| `5b7278250af37c15` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP8/PP1/DP1 | ISL8192/OSL256/C176/ctx8192 | simulated_scan_only | 否 | — | — | 1243.126 | 扫描器输出是稳态单点（均值口径），不含 P99；不能用于 SLA 判定 |
| `13f766057c49e1e1` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP8/PP1/DP1 | ISL8192/OSL256/C192/ctx8192 | simulated_scan_only | 否 | — | — | 1248.132 | 扫描器输出是稳态单点（均值口径），不含 P99；不能用于 SLA 判定 |
| `3462f720bb1cc3b8` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP8/PP1/DP1 | ISL8192/OSL256/C192/ctx16384 | simulated_scan_only | 否 | — | — | 1243.8 | 扫描器输出是稳态单点（均值口径），不含 P99；不能用于 SLA 判定 |
| `e80c2130d0b03d52` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP8/PP1/DP1 | ISL8192/OSL256/C208/ctx8192 | simulated_scan_only | 否 | — | — | 1251.559 | 扫描器输出是稳态单点（均值口径），不含 P99；不能用于 SLA 判定 |
| `42e8b86554aff15b` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP8/PP1/DP1 | ISL8192/OSL256/C224/ctx8192 | simulated_scan_only | 否 | — | — | 1254.757 | 扫描器输出是稳态单点（均值口径），不含 P99；不能用于 SLA 判定 |
| `a540f3e1b695faaf` | Qwen/Qwen3-32B / bf16 | nvidia-h200-sxm-141gb / TP2/PP1/DP1 | ISL1024/OSL1024/C8 | simulated_native_p99 | 是 | 440.7024270000402 | 11.05583574389083 | 691.7932401874034 | — |
| `e7b86d7552f911c0` | Qwen/Qwen3-32B / bf16 | nvidia-h200-sxm-141gb / TP2/PP1/DP1 | ISL8192/OSL256/C8 | simulated_native_p99 | 是 | 1995.562819 | 26.646441819607944 | 279.3985178058005 | — |
| `953e29f4b6478a25` | Qwen/Qwen3-32B / bf16 | nvidia-h200-sxm-141gb / TP4/PP1/DP2 | ISL1024/OSL1024/C8 | simulated_native_p99 | 是 | 132.70885400001134 | 7.0779355053764395 | 1102.6959193454597 | — |
| `77bcba528269adbe` | Qwen/Qwen3-32B / bf16 | nvidia-h200-sxm-141gb / TP4/PP1/DP2 | ISL8192/OSL256/C8 | simulated_native_p99 | 是 | 878.576915 | 10.833958360784477 | 664.772924145956 | — |
| `ae43a35c2eb1a4ac` | Qwen/Qwen3-32B / bf16 | nvidia-h200-sxm-141gb / TP2/PP1/DP1 | ISL1024/OSL1024/C32 | simulated_native_p99 | 是 | 1765.553723 | 14.477458347995988 | 2149.7672006351536 | — |
| `d4f373b8de069b36` | Qwen/Qwen3-32B / bf16 | nvidia-h200-sxm-141gb / TP2/PP1/DP1 | ISL8192/OSL256/C32 | simulated_native_p99 | 是 | 14356.537065000002 | 79.91176538039238 | 390.35883497438374 | — |
| `53e3dd905f99efd5` | Qwen/Qwen3-32B / bf16 | nvidia-h200-sxm-141gb / TP4/PP1/DP1 | ISL8192/OSL256/C32 | simulated_native_p99 | 是 | 8481.037019000001 | 47.49475550196087 | 656.4300876984471 | — |
| `cbca112b9137d1f9` | Qwen/Qwen3-32B / bf16 | nvidia-h200-sxm-141gb / TP8/PP1/DP1 | ISL8192/OSL256/C32 | simulated_native_p99 | 是 | 4955.538856 | 28.770253968627422 | 1082.997232236789 | — |
| `c38774b3ac6cd0fc` | Qwen/Qwen3-32B / bf16 | nvidia-h200-sxm-141gb / TP4/PP1/DP2 | ISL1024/OSL1024/C32 | simulated_native_p99 | 是 | 532.0884139999998 | 8.038959994134894 | 3873.420667483136 | — |
| `7c4e9c47b1c733f1` | Qwen/Qwen3-32B / bf16 | nvidia-h200-sxm-141gb / TP4/PP1/DP2 | ISL8192/OSL256/C32 | simulated_native_p99 | 是 | 4521.6287919999995 | 26.8434794 | 1138.1864434887902 | — |
| `7baa5c593112752a` | Qwen/Qwen3-32B / bf16 | nvidia-h200-sxm-141gb / TP4/PP1/DP2 | ISL8192/OSL256/C40 | simulated_native_p99 | 是 | 5739.230234000001 | 32.08379045882372 | 1195.654714987972 | — |
| `0bbcebd6e1440360` | Qwen/Qwen3-32B / bf16 | nvidia-h200-sxm-141gb / TP4/PP1/DP2 | ISL8192/OSL256/C48 | simulated_native_p99 | 是 | 6957.594815000001 | 37.223840470588264 | 1240.3436830209462 | — |
| `db872ce2603dcea9` | Qwen/Qwen3-32B / bf16 | nvidia-h200-sxm-141gb / TP4/PP1/DP2 | ISL8192/OSL256/C56 | simulated_native_p99 | 是 | 8176.317045000002 | 42.36271450980398 | 1285.7037039824977 | — |
| `c4ac186039d9e334` | Qwen/Qwen3-32B / bf16 | nvidia-h200-sxm-141gb / TP2/PP1/DP1 | ISL1024/OSL1024/C64 | simulated_native_p99 | 是 | 3533.6141140000004 | 18.36405357673509 | 3407.386670638768 | — |
| `876fa4b03bdf6726` | Qwen/Qwen3-32B / bf16 | nvidia-h200-sxm-141gb / TP2/PP1/DP1 | ISL8192/OSL256/C64 | simulated_native_p99 | 是 | 30866.989649000003 | 148.80633225882306 | 423.32901395617455 | — |
| `9bb0d724266ba606` | Qwen/Qwen3-32B / bf16 | nvidia-h200-sxm-141gb / TP4/PP1/DP2 | ISL1024/OSL1024/C64 | simulated_native_p99 | 是 | 1065.5455469999997 | 9.39976863734117 | 6648.3700709741215 | — |
| `902d3c8fd61feaea` | Qwen/Qwen3-32B / bf16 | nvidia-h200-sxm-141gb / TP4/PP1/DP2 | ISL8192/OSL256/C64 | simulated_native_p99 | 是 | 9395.242326 | 47.49475550196081 | 1311.2457649568023 | — |
| `28e3b8df932eaee7` | Qwen/Qwen3-32B / bf16 | nvidia-h200-sxm-141gb / TP4/PP1/DP2 | ISL8192/OSL256/C96 | simulated_native_p99 | 是 | 14265.872607999992 | 67.54073382745081 | 1376.624854092987 | — |
| `25d3ea5e8ced0ec0` | Qwen/Qwen3-32B / bf16 | nvidia-h200-sxm-141gb / TP4/PP1/DP2 | ISL8192/OSL256/C128 | simulated_native_p99 | 是 | 19121.870338999986 | 87.29699354117625 | 1436.3384727706505 | — |
| `41685b114e2deed1` | Qwen/Qwen3-8B / bf16 | nvidia-h100-sxm-80gb / TP1/PP1/DP1 | ISL8192/OSL256/C— | simulated_native_p99 | 是 | 654.54886086646 | 18.03786428235403 | 274.27803481996517 | — |
| `4a322de9460079a4` | Qwen/Qwen3-8B / bf16 | nvidia-h100-sxm-80gb / TP1/PP1/DP1 | ISL8192/OSL256/C— | simulated_native_p99 | 是 | 1004.0468315048784 | 53.25004861960804 | 544.5986460914221 | — |
| `5ebeb0d9254632dc` | Qwen/Qwen3-8B / bf16 | nvidia-h100-sxm-80gb / TP1/PP1/DP1 | ISL8192/OSL256/C— | simulated_native_p99 | 是 | 23321.40930932734 | 63.998291674510156 | 703.4322981123667 | — |
| `8d69c8668e195630` | Qwen/Qwen3-8B / bf16 | nvidia-h100-sxm-80gb / TP1/PP1/DP1 | ISL8192/OSL256/C— | simulated_native_p99 | 是 | 60174.8116980795 | 76.13251087843177 | 700.192047763515 | — |
| `1d4a00f59961261c` | Qwen/Qwen3-8B / bf16 | nvidia-h100-sxm-80gb / TP1/PP1/DP2 | ISL8192/OSL256/C— | simulated_native_p99 | 是 | 452.2775124297391 | 14.658638909803908 | 546.1767611366565 | — |
| `7a13b9303c37343d` | Qwen/Qwen3-8B / bf16 | nvidia-h100-sxm-80gb / TP1/PP1/DP2 | ISL8192/OSL256/C— | simulated_native_p99 | 是 | 593.5181920715222 | 20.913063670588187 | 773.1155588940723 | — |
| `d89ce89d67b9b30f` | Qwen/Qwen3-8B / bf16 | nvidia-h100-sxm-80gb / TP1/PP1/DP2 | ISL8192/OSL256/C— | simulated_native_p99 | 是 | 809.5613405105687 | 34.5578661843134 | 1018.9233272202802 | — |
| `821a7a5880ba024d` | Qwen/Qwen3-8B / bf16 | nvidia-h100-sxm-80gb / TP1/PP1/DP2 | ISL8192/OSL256/C— | simulated_native_p99 | 是 | 999.3026506029273 | 58.93375709019664 | 1261.3523526622848 | — |
| `2b1e1f7207ea1425` | Qwen/Qwen3-8B / bf16 | nvidia-h100-sxm-80gb / TP1/PP1/DP1 | ISL1024/OSL1024/C8 | simulated_native_p99 | 是 | 185.48182300000917 | 7.230641140762756 | 1073.1434253976 | — |
| `b08f334e3072e8b1` | Qwen/Qwen3-8B / bf16 | nvidia-h100-sxm-80gb / TP1/PP1/DP1 | ISL8192/OSL256/C8 | simulated_native_p99 | 是 | 887.919842 | 16.005433647058844 | 473.4381502095157 | — |
| `5ccb6fb0a56bfa3b` | Qwen/Qwen3-8B / bf16 | nvidia-h100-sxm-80gb / TP1/PP1/DP2 | ISL1024/OSL1024/C8 | simulated_native_p99 | 是 | 93.09983800002374 | 6.744311719452742 | 1162.361629272693 | — |
| `9ea1af29e079b439` | Qwen/Qwen3-8B / bf16 | nvidia-h100-sxm-80gb / TP1/PP1/DP2 | ISL8192/OSL256/C8 | simulated_native_p99 | 是 | 653.9557169999999 | 11.053877941176625 | 669.3956053210023 | — |
| `44f4b7961df3d28e` | Qwen/Qwen3-8B / bf16 | nvidia-h100-sxm-80gb / TP1/PP1/DP1 | ISL1024/OSL1024/C32 | simulated_native_p99 | 是 | 746.227258 | 10.10754084652979 | 3096.7872683153632 | — |
| `bde125e390236398` | Qwen/Qwen3-8B / bf16 | nvidia-h100-sxm-80gb / TP1/PP1/DP1 | ISL8192/OSL256/C32 | simulated_native_p99 | 是 | 6609.393869999999 | 46.599841372549015 | 671.6197848398688 | — |
| `caf2093055ab9b6d` | Qwen/Qwen3-8B / bf16 | nvidia-h100-sxm-80gb / TP1/PP1/DP2 | ISL1024/OSL1024/C32 | simulated_native_p99 | 是 | 371.39593 | 8.608299143695115 | 3643.8625365988637 | — |
| `d88c829a86a9c910` | Qwen/Qwen3-8B / bf16 | nvidia-h100-sxm-80gb / TP1/PP1/DP2 | ISL8192/OSL256/C32 | simulated_native_p99 | 是 | 3486.4293139999995 | 27.420675007843208 | 1127.7751784411603 | — |
| `07009602846747ec` | Qwen/Qwen3-8B / bf16 | nvidia-h100-sxm-80gb / TP1/PP1/DP4 | ISL8192/OSL256/C32 | simulated_native_p99 | 是 | 1827.7921460000002 | 16.0054336470589 | 1880.7211353088285 | — |
| `8402c3b9745355ad` | Qwen/Qwen3-8B / bf16 | nvidia-h100-sxm-80gb / TP1/PP1/DP2 | ISL8192/OSL256/C40 | simulated_native_p99 | 是 | 4443.510729 | 32.13858656470586 | 1204.2801594556427 | — |
| `4dace62a4f283837` | Qwen/Qwen3-8B / bf16 | nvidia-h100-sxm-80gb / TP1/PP1/DP2 | ISL8192/OSL256/C48 | simulated_native_p99 | 是 | 5404.200543 | 36.983700949019656 | 1257.2509562704433 | — |
| `fae7f97a03e7c27f` | Qwen/Qwen3-8B / bf16 | nvidia-h100-sxm-80gb / TP1/PP1/DP2 | ISL8192/OSL256/C56 | simulated_native_p99 | 是 | 6367.9972099999995 | 41.80868124705882 | 1309.3396396196968 | — |
| `2fc95812a75bc5d3` | Qwen/Qwen3-8B / bf16 | nvidia-h100-sxm-80gb / TP1/PP1/DP1 | ISL1024/OSL1024/C64 | simulated_native_p99 | 是 | 1499.3555680000002 | 13.52109428739 | 4657.798733662274 | — |
| `db1f7193bc7d8482` | Qwen/Qwen3-8B / bf16 | nvidia-h100-sxm-80gb / TP1/PP1/DP1 | ISL8192/OSL256/C64 | simulated_native_p99 | 是 | 19798.39472699997 | 76.13251087843177 | 700.192047763515 | — |
| `44a521c25c6d173a` | Qwen/Qwen3-8B / bf16 | nvidia-h100-sxm-80gb / TP1/PP1/DP2 | ISL1024/OSL1024/C64 | simulated_native_p99 | 是 | 746.227258 | 10.129031437927686 | 6191.125539745652 | — |
| `0c1bbfdd6316d18b` | Qwen/Qwen3-8B / bf16 | nvidia-h100-sxm-80gb / TP1/PP1/DP2 | ISL8192/OSL256/C64 | simulated_native_p99 | 是 | 7334.551824999999 | 46.599841372549015 | 1341.269828068544 | — |
| `bbb1b69c61d53d48` | Qwen/Qwen3-8B / bf16 | nvidia-h100-sxm-80gb / TP1/PP1/DP2 | ISL8192/OSL256/C96 | simulated_native_p99 | 是 | 11034.099922 | 76.13560087843159 | 1394.4902625469197 | — |
| `e0d89b785420b4c8` | Qwen/Qwen3-8B / bf16 | nvidia-h100-sxm-80gb / TP1/PP1/DP2 | ISL8192/OSL256/C128 | simulated_native_p99 | 是 | 20535.195289999967 | 76.13560087843159 | 1394.4902625469197 | — |
| `0f7b1e720657bcb8` | Qwen/Qwen3-8B / bf16 | nvidia-h200-sxm-141gb / TP1/PP1/DP2 | ISL8192/OSL256/C— | simulated_native_p99 | 是 | 435.37229772889987 | 10.99272484313748 | 547.3118701294937 | — |
| `e11c2722dbe61001` | Qwen/Qwen3-8B / bf16 | nvidia-h200-sxm-141gb / TP1/PP1/DP2 | ISL8192/OSL256/C— | simulated_native_p99 | 是 | 554.438000886541 | 16.477727670588457 | 817.4543040497814 | — |
| `6ec213bb65b7cefe` | Qwen/Qwen3-8B / bf16 | nvidia-h200-sxm-141gb / TP1/PP1/DP2 | ISL8192/OSL256/C— | simulated_native_p99 | 是 | 625.4226347604636 | 26.289728533333324 | 1084.3566101091678 | — |
| `6d7f6f06ad176a70` | Qwen/Qwen3-8B / bf16 | nvidia-h200-sxm-141gb / TP1/PP1/DP2 | ISL8192/OSL256/C— | simulated_native_p99 | 是 | 854.2590297111892 | 35.675425713725375 | 1272.9612253381513 | — |
| `6f60822ce6c7c63f` | Qwen/Qwen3-8B / bf16 | nvidia-h200-sxm-141gb / TP1/PP1/DP1 | ISL1024/OSL1024/C8 | simulated_native_p99 | 是 | 181.30924300000333 | 5.540693851417527 | 1390.5781678719216 | — |
| `c07032fbe948ab4f` | Qwen/Qwen3-8B / bf16 | nvidia-h200-sxm-141gb / TP1/PP1/DP1 | ISL8192/OSL256/C8 | simulated_native_p99 | 是 | 859.7394019999999 | 13.592190360784526 | 553.5143888489443 | — |
| `8e4d95d6e076f458` | Qwen/Qwen3-8B / bf16 | nvidia-h200-sxm-141gb / TP1/PP1/DP2 | ISL1024/OSL1024/C8 | simulated_native_p99 | 是 | 91.21148900000844 | 5.313530915933562 | 1471.2269209774552 | — |
| `d7e6f6725ec05dd2` | Qwen/Qwen3-8B / bf16 | nvidia-h200-sxm-141gb / TP1/PP1/DP2 | ISL8192/OSL256/C8 | simulated_native_p99 | 是 | 636.6716029999999 | 9.002842921569062 | 810.6443773058669 | — |
| `c32fd5df5ee94b8c` | Qwen/Qwen3-8B / bf16 | nvidia-h200-sxm-141gb / TP1/PP1/DP1 | ISL1024/OSL1024/C32 | simulated_native_p99 | 是 | 728.1516899999998 | 7.712977738025394 | 4049.0165794668155 | — |
| `c32dce538c0e9d97` | Qwen/Qwen3-8B / bf16 | nvidia-h200-sxm-141gb / TP1/PP1/DP1 | ISL8192/OSL256/C32 | simulated_native_p99 | 是 | 6286.380569000001 | 40.78204896862738 | 767.0359516128434 | — |
| `930868cc98af809c` | Qwen/Qwen3-8B / bf16 | nvidia-h200-sxm-141gb / TP1/PP1/DP2 | ISL1024/OSL1024/C32 | simulated_native_p99 | 是 | 362.8524709999999 | 6.498827639296478 | 4811.783329426065 | — |
| `2fa2bfe0884a3878` | Qwen/Qwen3-8B / bf16 | nvidia-h200-sxm-141gb / TP1/PP1/DP2 | ISL8192/OSL256/C32 | simulated_native_p99 | 是 | 3330.3272680000005 | 22.918945984313847 | 1341.9938095420605 | — |
| `949ddf3a926838d7` | Qwen/Qwen3-8B / bf16 | nvidia-h200-sxm-141gb / TP1/PP1/DP1 | ISL1024/OSL1024/C64 | simulated_native_p99 | 是 | 1460.2410519999996 | 10.414255296187664 | 6039.994042071066 | — |
| `77fb4c9d0120c509` | Qwen/Qwen3-8B / bf16 | nvidia-h200-sxm-141gb / TP1/PP1/DP1 | ISL8192/OSL256/C64 | simulated_native_p99 | 是 | 13681.380651000005 | 75.51767865882333 | 834.6444646073313 | — |
| `6844ee5b26d88cfe` | Qwen/Qwen3-8B / bf16 | nvidia-h200-sxm-141gb / TP1/PP1/DP2 | ISL1024/OSL1024/C64 | simulated_native_p99 | 是 | 728.1516899999998 | 7.735301954056702 | 8094.894604956275 | — |
| `285db9f32b2187d9` | Qwen/Qwen3-8B / bf16 | nvidia-h200-sxm-141gb / TP1/PP1/DP2 | ISL8192/OSL256/C64 | simulated_native_p99 | 是 | 6972.955788000001 | 40.78204896862752 | 1532.2982314387943 | — |

## 4. 参考证据（不参与 SLA）

| 记录 | 来源类型 | 模型 | 硬件 | 负载 | TTFT P99 | TPOT P99 | 为什么不能用于 SLA |
|---|---|---|---|---|---|---|---|
| `gpustack_perf_lab_qwen3_32b_h100:vllm-sharegpt` | measured_public | Qwen/Qwen3-32B | NVIDIA H100 SXM | ISL—/OSL—/C— | 153161.51 | 378.18 | 公开参考数据，不是目标配置实测；原文未声明：model_revision；原文未声明：weight_precision；原文未声明：tp_size；原文未声明：dp_size；原文未声明：input_len_target；原文未声明：output_len_target；原文未声明：configured_max_concurrency；单卡 H100 SXM 的 ShareGPT 基准；原文未声明权重精度与 TP/DP。TTFT P99 高达 153161.51 ms（约 153 秒）—— 这是 1000 条请求无速率限制压满后的排队时延，不是稳态服务时延。 |
| `community_qwen3_32b_h100_4gpu_report:h100-4gpu-random-32k` | measured_public | Qwen/Qwen3-32B | NVIDIA H100 80GB HBM3 | ISL31400/OSL128/C— | 359749.43 | 154.03 | 公开参考数据，不是目标配置实测；原文未声明：model_revision；原文未声明：dp_size；原文未声明：configured_max_concurrency；4×H100 80GB HBM3、TP4、bfloat16、vLLM 0.17.0（原文另注明该侧禁用 CUDA Graphs）。输入 31400 / 输出 128。原文未写 SXM，故与本目录的『NVIDIA H100 SXM』按不同型号处理。 |
| `sglang_qwen3_cookbook:5.1.1.1` | measured_public | Qwen/Qwen3-235B-A22B-Instruct-2507 | NVIDIA B200 | ISL1000/OSL1000/C1 | 166.05 | 10.15 | 公开参考数据，不是目标配置实测；eligible_for_sla_guarantee=False；原文未声明：model_revision；原文未声明：full_environment_pin；原文未声明：weight_precision |
| `sglang_qwen3_cookbook:5.1.1.2` | measured_public | Qwen/Qwen3-235B-A22B-Instruct-2507 | NVIDIA B200 | ISL1000/OSL1000/C16 | 727.08 | 24.09 | 公开参考数据，不是目标配置实测；eligible_for_sla_guarantee=False；原文未声明：model_revision；原文未声明：full_environment_pin；原文未声明：weight_precision |
| `sglang_qwen3_cookbook:5.1.1.3` | measured_public | Qwen/Qwen3-235B-A22B-Instruct-2507 | NVIDIA B200 | ISL1000/OSL1000/C100 | 863.11 | 41.19 | 公开参考数据，不是目标配置实测；eligible_for_sla_guarantee=False；原文未声明：model_revision；原文未声明：full_environment_pin；原文未声明：weight_precision |
| `sglang_qwen3_cookbook:5.1.2.1` | measured_public | Qwen/Qwen3-235B-A22B-Instruct-2507 | NVIDIA B200 | ISL1000/OSL8000/C1 | 167.43 | 10.32 | 公开参考数据，不是目标配置实测；eligible_for_sla_guarantee=False；原文未声明：model_revision；原文未声明：full_environment_pin；原文未声明：weight_precision |
| `sglang_qwen3_cookbook:5.1.2.2` | measured_public | Qwen/Qwen3-235B-A22B-Instruct-2507 | NVIDIA B200 | ISL1000/OSL8000/C16 | 455.71 | 15.2 | 公开参考数据，不是目标配置实测；eligible_for_sla_guarantee=False；原文未声明：model_revision；原文未声明：full_environment_pin；原文未声明：weight_precision |
| `sglang_qwen3_cookbook:5.1.2.3` | measured_public | Qwen/Qwen3-235B-A22B-Instruct-2507 | NVIDIA B200 | ISL1000/OSL8000/C64 | 823.85 | 24.97 | 公开参考数据，不是目标配置实测；eligible_for_sla_guarantee=False；原文未声明：model_revision；原文未声明：full_environment_pin；原文未声明：weight_precision |
| `sglang_qwen3_cookbook:5.1.3.1` | measured_public | Qwen/Qwen3-235B-A22B-Instruct-2507 | NVIDIA B200 | ISL8000/OSL1000/C1 | 225.17 | 10.51 | 公开参考数据，不是目标配置实测；eligible_for_sla_guarantee=False；原文未声明：model_revision；原文未声明：full_environment_pin；原文未声明：weight_precision |
| `sglang_qwen3_cookbook:5.1.3.2` | measured_public | Qwen/Qwen3-235B-A22B-Instruct-2507 | NVIDIA B200 | ISL8000/OSL1000/C16 | 1027.21 | 27.18 | 公开参考数据，不是目标配置实测；eligible_for_sla_guarantee=False；原文未声明：model_revision；原文未声明：full_environment_pin；原文未声明：weight_precision |
| `sglang_qwen3_cookbook:5.1.3.3` | measured_public | Qwen/Qwen3-235B-A22B-Instruct-2507 | NVIDIA B200 | ISL8000/OSL1000/C64 | 510.48 | 40.59 | 公开参考数据，不是目标配置实测；eligible_for_sla_guarantee=False；原文未声明：model_revision；原文未声明：full_environment_pin；原文未声明：weight_precision |
| `sglang_qwen3_coder_cookbook:5.1.1.1` | measured_public | Qwen/Qwen3-Coder-480B-A35B-Instruct-FP8 | AMD MI300X | ISL1000/OSL1000/C1 | 662.15 | 17.08 | 公开参考数据，不是目标配置实测；eligible_for_sla_guarantee=False；原文未声明：model_revision；原文未声明：full_environment_pin |
| `sglang_qwen3_coder_cookbook:5.1.1.2` | measured_public | Qwen/Qwen3-Coder-480B-A35B-Instruct-FP8 | AMD MI300X | ISL1000/OSL1000/C16 | 1318.63 | 43.48 | 公开参考数据，不是目标配置实测；eligible_for_sla_guarantee=False；原文未声明：model_revision；原文未声明：full_environment_pin |
| `sglang_qwen3_coder_cookbook:5.1.1.3` | measured_public | Qwen/Qwen3-Coder-480B-A35B-Instruct-FP8 | AMD MI300X | ISL1000/OSL1000/C64 | 529.01 | 204.24 | 公开参考数据，不是目标配置实测；eligible_for_sla_guarantee=False；原文未声明：model_revision；原文未声明：full_environment_pin |
| `local-db:excel_部署效能_2` | measured_internal | KIMI-K3 | B300 | ISL—/OSL—/C70 | — | — | 历史 Excel 记录，verification_status=partial_evidence；performance_source_kind=internal_excel；缺字段：model_revision；缺字段：model_precision；缺字段：framework；缺字段：framework_version；缺字段：driver_version；缺字段：hardware_sku；缺字段：node_count；缺字段：gpus_per_node |
| `local-db:excel_部署效能_3` | measured_internal | KIMI-K3 | B300 | ISL—/OSL—/C32 | — | — | 历史 Excel 记录，verification_status=partial_evidence；performance_source_kind=internal_excel；缺字段：model_revision；缺字段：model_precision；缺字段：framework；缺字段：framework_version；缺字段：driver_version；缺字段：hardware_sku；缺字段：node_count；缺字段：gpus_per_node |
| `local-db:excel_部署效能_4` | measured_internal | KIMI-K3 | B300 | ISL—/OSL—/C16 | — | — | 历史 Excel 记录，verification_status=partial_evidence；performance_source_kind=internal_excel；缺字段：model_revision；缺字段：model_precision；缺字段：framework；缺字段：framework_version；缺字段：driver_version；缺字段：hardware_sku；缺字段：node_count；缺字段：gpus_per_node |
| `local-db:excel_部署效能_5` | measured_internal | KIMI-K3 | B300 | ISL—/OSL—/C8 | — | — | 历史 Excel 记录，verification_status=partial_evidence；performance_source_kind=internal_excel；缺字段：model_revision；缺字段：model_precision；缺字段：framework；缺字段：framework_version；缺字段：driver_version；缺字段：hardware_sku；缺字段：node_count；缺字段：gpus_per_node |
| `local-db:excel_部署效能_6` | measured_internal | KIMI-K3 | B300 | ISL—/OSL—/C4 | — | — | 历史 Excel 记录，verification_status=partial_evidence；performance_source_kind=internal_excel；缺字段：model_revision；缺字段：model_precision；缺字段：framework；缺字段：framework_version；缺字段：driver_version；缺字段：hardware_sku；缺字段：node_count；缺字段：gpus_per_node |
| `local-db:excel_部署效能_7` | measured_internal | KIMI-K3 | B300 | ISL—/OSL—/C2 | — | — | 历史 Excel 记录，verification_status=partial_evidence；performance_source_kind=internal_excel；缺字段：model_revision；缺字段：model_precision；缺字段：framework；缺字段：framework_version；缺字段：driver_version；缺字段：hardware_sku；缺字段：node_count；缺字段：gpus_per_node |
| `local-db:excel_部署效能_8` | measured_internal | KIMI-K3 | B300 | ISL—/OSL—/C1 | — | — | 历史 Excel 记录，verification_status=partial_evidence；performance_source_kind=internal_excel；缺字段：model_revision；缺字段：model_precision；缺字段：framework；缺字段：framework_version；缺字段：driver_version；缺字段：hardware_sku；缺字段：node_count；缺字段：gpus_per_node |
| `local-db:excel_部署效能_9` | measured_internal | KIMI-K3 | H200 | ISL—/OSL—/C26 | — | — | 历史 Excel 记录，verification_status=partial_evidence；performance_source_kind=internal_excel；缺字段：model_revision；缺字段：model_precision；缺字段：framework；缺字段：framework_version；缺字段：driver_version；缺字段：hardware_sku；缺字段：node_count；缺字段：gpus_per_node |
| `local-db:excel_部署效能_10` | measured_internal | KIMI-K3 | H200 | ISL—/OSL—/C12 | — | — | 历史 Excel 记录，verification_status=partial_evidence；performance_source_kind=internal_excel；缺字段：model_revision；缺字段：model_precision；缺字段：framework；缺字段：framework_version；缺字段：driver_version；缺字段：hardware_sku；缺字段：node_count；缺字段：gpus_per_node |
| `local-db:excel_部署效能_11` | measured_internal | KIMI-K3 | H200 | ISL—/OSL—/C10 | — | — | 历史 Excel 记录，verification_status=partial_evidence；performance_source_kind=internal_excel；缺字段：model_revision；缺字段：model_precision；缺字段：framework；缺字段：framework_version；缺字段：driver_version；缺字段：hardware_sku；缺字段：node_count；缺字段：gpus_per_node |
| `local-db:excel_部署效能_12` | measured_internal | KIMI-K3 | H200 | ISL—/OSL—/C8 | — | — | 历史 Excel 记录，verification_status=partial_evidence；performance_source_kind=internal_excel；缺字段：model_revision；缺字段：model_precision；缺字段：framework；缺字段：framework_version；缺字段：driver_version；缺字段：hardware_sku；缺字段：node_count；缺字段：gpus_per_node |
| `local-db:excel_部署效能_13` | measured_internal | KIMI-K3 | H200 | ISL—/OSL—/C4 | — | — | 历史 Excel 记录，verification_status=partial_evidence；performance_source_kind=internal_excel；缺字段：model_revision；缺字段：model_precision；缺字段：framework；缺字段：framework_version；缺字段：driver_version；缺字段：hardware_sku；缺字段：node_count；缺字段：gpus_per_node |
| `local-db:excel_部署效能_14` | measured_internal | KIMI-K3 | H200 | ISL—/OSL—/C1 | — | — | 历史 Excel 记录，verification_status=partial_evidence；performance_source_kind=internal_excel；缺字段：model_revision；缺字段：model_precision；缺字段：framework；缺字段：framework_version；缺字段：driver_version；缺字段：hardware_sku；缺字段：node_count；缺字段：gpus_per_node |
| `local-db:excel_部署效能_15` | measured_internal | GLM-5.2/5.3 | B300 | ISL—/OSL—/C16 | — | — | 历史 Excel 记录，verification_status=partial_evidence；performance_source_kind=internal_excel；缺字段：model_revision；缺字段：model_precision；缺字段：framework；缺字段：framework_version；缺字段：driver_version；缺字段：hardware_sku；缺字段：node_count；缺字段：gpus_per_node |
| `local-db:excel_部署效能_16` | measured_internal | GLM-5.2/5.3 | B300 | ISL—/OSL—/C12 | — | — | 历史 Excel 记录，verification_status=partial_evidence；performance_source_kind=internal_excel；缺字段：model_revision；缺字段：model_precision；缺字段：framework；缺字段：framework_version；缺字段：driver_version；缺字段：hardware_sku；缺字段：node_count；缺字段：gpus_per_node |
| `local-db:excel_部署效能_17` | measured_internal | GLM-5.2/5.3 | B300 | ISL—/OSL—/C8 | — | — | 历史 Excel 记录，verification_status=partial_evidence；performance_source_kind=internal_excel；缺字段：model_revision；缺字段：model_precision；缺字段：framework；缺字段：framework_version；缺字段：driver_version；缺字段：hardware_sku；缺字段：node_count；缺字段：gpus_per_node |
| `local-db:excel_部署效能_18` | measured_internal | GLM-5.2/5.3 | B300 | ISL—/OSL—/C4 | — | — | 历史 Excel 记录，verification_status=partial_evidence；performance_source_kind=internal_excel；缺字段：model_revision；缺字段：model_precision；缺字段：framework；缺字段：framework_version；缺字段：driver_version；缺字段：hardware_sku；缺字段：node_count；缺字段：gpus_per_node |
| `local-db:excel_部署效能_19` | measured_internal | GLM-5.2/5.3 | B300 | ISL—/OSL—/C1 | — | — | 历史 Excel 记录，verification_status=partial_evidence；performance_source_kind=internal_excel；缺字段：model_revision；缺字段：model_precision；缺字段：framework；缺字段：framework_version；缺字段：driver_version；缺字段：hardware_sku；缺字段：node_count；缺字段：gpus_per_node |
| `local-db:excel_部署效能_20` | measured_internal | GLM-5.2/5.3 | H200 | ISL—/OSL—/C12 | — | — | 历史 Excel 记录，verification_status=partial_evidence；performance_source_kind=internal_excel；缺字段：model_revision；缺字段：model_precision；缺字段：framework；缺字段：framework_version；缺字段：driver_version；缺字段：hardware_sku；缺字段：node_count；缺字段：gpus_per_node |
| `local-db:excel_部署效能_21` | measured_internal | GLM-5.2/5.3 | H200 | ISL—/OSL—/C8 | — | — | 历史 Excel 记录，verification_status=partial_evidence；performance_source_kind=internal_excel；缺字段：model_revision；缺字段：model_precision；缺字段：framework；缺字段：framework_version；缺字段：driver_version；缺字段：hardware_sku；缺字段：node_count；缺字段：gpus_per_node |
| `local-db:excel_部署效能_22` | measured_internal | DeepSeek-V4-Pro（峰值） | B300 | ISL—/OSL—/C512 | — | — | 历史 Excel 记录，verification_status=partial_evidence；performance_source_kind=internal_excel；缺字段：model_revision；缺字段：model_precision；缺字段：framework；缺字段：framework_version；缺字段：driver_version；缺字段：hardware_sku；缺字段：node_count；缺字段：gpus_per_node |
| `local-db:excel_部署效能_23` | measured_internal | DeepSeek-V4-Pro（峰值） | B300 | ISL—/OSL—/C256 | — | — | 历史 Excel 记录，verification_status=partial_evidence；performance_source_kind=internal_excel；缺字段：model_revision；缺字段：model_precision；缺字段：framework；缺字段：framework_version；缺字段：driver_version；缺字段：hardware_sku；缺字段：node_count；缺字段：gpus_per_node |
| `local-db:excel_部署效能_24` | measured_internal | DeepSeek-V4-Pro（峰值） | B300 | ISL—/OSL—/C128 | — | — | 历史 Excel 记录，verification_status=partial_evidence；performance_source_kind=internal_excel；缺字段：model_revision；缺字段：model_precision；缺字段：framework；缺字段：framework_version；缺字段：driver_version；缺字段：hardware_sku；缺字段：node_count；缺字段：gpus_per_node |
| `local-db:excel_部署效能_25` | measured_internal | DeepSeek-V4-Pro（峰值） | B300 | ISL—/OSL—/C64 | — | — | 历史 Excel 记录，verification_status=partial_evidence；performance_source_kind=internal_excel；缺字段：model_revision；缺字段：model_precision；缺字段：framework；缺字段：framework_version；缺字段：driver_version；缺字段：hardware_sku；缺字段：node_count；缺字段：gpus_per_node |
| `local-db:excel_部署效能_26` | measured_internal | DeepSeek-V4-Pro（峰值） | B300 | ISL—/OSL—/C48 | — | — | 历史 Excel 记录，verification_status=partial_evidence；performance_source_kind=internal_excel；缺字段：model_revision；缺字段：model_precision；缺字段：framework；缺字段：framework_version；缺字段：driver_version；缺字段：hardware_sku；缺字段：node_count；缺字段：gpus_per_node |
| `local-db:excel_部署效能_27` | measured_internal | DeepSeek-V4-Pro（峰值） | B300 | ISL—/OSL—/C16 | — | — | 历史 Excel 记录，verification_status=partial_evidence；performance_source_kind=internal_excel；缺字段：model_revision；缺字段：model_precision；缺字段：framework；缺字段：framework_version；缺字段：driver_version；缺字段：hardware_sku；缺字段：node_count；缺字段：gpus_per_node |
| `local-db:excel_部署效能_28` | measured_internal | DeepSeek-V4-Pro（峰值） | B300 | ISL—/OSL—/C8 | — | — | 历史 Excel 记录，verification_status=partial_evidence；performance_source_kind=internal_excel；缺字段：model_revision；缺字段：model_precision；缺字段：framework；缺字段：framework_version；缺字段：driver_version；缺字段：hardware_sku；缺字段：node_count；缺字段：gpus_per_node |
| `local-db:excel_部署效能_29` | measured_internal | DeepSeek-V4-Pro（峰值） | B300 | ISL—/OSL—/C4 | — | — | 历史 Excel 记录，verification_status=partial_evidence；performance_source_kind=internal_excel；缺字段：model_revision；缺字段：model_precision；缺字段：framework；缺字段：framework_version；缺字段：driver_version；缺字段：hardware_sku；缺字段：node_count；缺字段：gpus_per_node |
| `local-db:excel_部署效能_30` | measured_internal | DeepSeek-V4-Pro（峰值） | B300 | ISL—/OSL—/C1 | — | — | 历史 Excel 记录，verification_status=partial_evidence；performance_source_kind=internal_excel；缺字段：model_revision；缺字段：model_precision；缺字段：framework；缺字段：framework_version；缺字段：driver_version；缺字段：hardware_sku；缺字段：node_count；缺字段：gpus_per_node |
| `local-db:excel_部署效能_31` | measured_internal | DeepSeek-V4-Pro（峰值） | H200 | ISL—/OSL—/C8 | — | — | 历史 Excel 记录，verification_status=partial_evidence；performance_source_kind=internal_excel；缺字段：model_revision；缺字段：model_precision；缺字段：framework；缺字段：framework_version；缺字段：driver_version；缺字段：hardware_sku；缺字段：node_count；缺字段：gpus_per_node |
| `local-db:excel_部署效能_32` | measured_internal | DeepSeek-V4-Pro（峰值） | H200 | ISL—/OSL—/C4 | — | — | 历史 Excel 记录，verification_status=partial_evidence；performance_source_kind=internal_excel；缺字段：model_revision；缺字段：model_precision；缺字段：framework；缺字段：framework_version；缺字段：driver_version；缺字段：hardware_sku；缺字段：node_count；缺字段：gpus_per_node |
| `local-db:excel_部署效能_33` | measured_internal | DeepSeek-V4-Pro（峰值） | H200 | ISL—/OSL—/C1 | — | — | 历史 Excel 记录，verification_status=partial_evidence；performance_source_kind=internal_excel；缺字段：model_revision；缺字段：model_precision；缺字段：framework；缺字段：framework_version；缺字段：driver_version；缺字段：hardware_sku；缺字段：node_count；缺字段：gpus_per_node |
| `local-db:fixture_sglang_synthetic_rec` | measured_internal | SYNTHETIC-MODEL-A | SYNTHETIC-GPU-A | ISL—/OSL—/C— | 210.5 | 8.185039370078740157480314961 | 历史 Excel 记录，verification_status=synthetic_fixture；performance_source_kind=synthetic_fixture；缺字段：model_revision；缺字段：framework_version；缺字段：driver_version；缺字段：hardware_sku；缺字段：tp_size；缺字段：dp_size；缺字段：ep_size；缺字段：dataset_name |
| `local-db:fixture_sglang_renamed_rec` | measured_internal | SYNTHETIC-MODEL-A | SYNTHETIC-GPU-A | ISL—/OSL—/C32 | 612.3 | 47.82 | 历史 Excel 记录，verification_status=synthetic_fixture；performance_source_kind=synthetic_fixture；缺字段：model_revision；缺字段：framework_version；缺字段：driver_version；缺字段：hardware_sku；缺字段：tp_size；缺字段：dp_size；缺字段：ep_size；缺字段：dataset_name |
| `local-db:fixture_vllm_synthetic_rec` | measured_internal | SYNTHETIC-MODEL-B | SYNTHETIC-GPU-B | ISL2048/OSL256/C16 | 452.7 | — | 历史 Excel 记录，verification_status=synthetic_fixture；performance_source_kind=synthetic_fixture；缺字段：model_revision；缺字段：framework_version；缺字段：driver_version；缺字段：hardware_sku；缺字段：tp_size；缺字段：dp_size；缺字段：ep_size；缺字段：dataset_name |
| `local-db:fixture_vllm_zero_success_rec` | measured_internal | SYNTHETIC-MODEL-B | SYNTHETIC-GPU-B | ISL—/OSL—/C16 | — | — | 历史 Excel 记录，verification_status=synthetic_fixture；performance_source_kind=synthetic_fixture；缺字段：model_revision；缺字段：framework_version；缺字段：driver_version；缺字段：hardware_sku；缺字段：tp_size；缺字段：dp_size；缺字段：ep_size；缺字段：dataset_name |

## 5. 统计与缺口

- 证据总数：309
- 原生模拟运行数：100；唯一原生工作点数：79
- 按级别：static_only=0；simulated_scan_only=159；simulated_native_p99=100；measured_benchmark=0
- 工作点总数：147
- SLA 可用工作点：79
- 真实 GPU Benchmark 运行数：5；真实工作点数：1
- 仅参考记录：50
- 阻塞的归档运行：0
- 阻塞的候选回放：0（同一运行内其它成功候选仍可用）

已知缺口：
- qwen3-8b × NVIDIA GeForce RTX 3090 只有静态规划，没有该工作点的性能证据
- qwen3-32b × NVIDIA GeForce RTX 3090 只有静态规划，没有该工作点的性能证据
- 没有任何证据达到 4 级（目标配置的真实 Benchmark）
- 没有目标配置的真实 Benchmark：所有实测型记录都缺身份或属于参考数据

## 6. 结论边界

1. 当前有 79 个可比较的原生 P99 工作点：
   - Qwen/Qwen3-32B / nvidia-h100-sxm-80gb / bf16 / vllm 0.24.0 / TP4×PP1×DP1 / C8 / ISL1024 / OSL1024 → TTFT P99 260.4617910000088 ms、TPOT P99 8.564802355816468 ms、吞吐 900.8776991343514 tok/s
   - Qwen/Qwen3-32B / nvidia-h100-sxm-80gb / bf16 / vllm 0.24.0 / TP4×PP1×DP1 / C8 / ISL8192 / OSL256 → TTFT P99 1159.756941 ms、TPOT P99 17.848578929412408 ms、吞吐 420.4521057972523 tok/s
   - Qwen/Qwen3-32B / nvidia-h100-sxm-80gb / bf16 / vllm 0.24.0 / TP8×PP1×DP1 / C8 / ISL1024 / OSL1024 → TTFT P99 169.22123400000055 ms、TPOT P99 5.983962341153699 ms、吞吐 1292.8510420687026 tok/s
   - Qwen/Qwen3-32B / nvidia-h100-sxm-80gb / bf16 / vllm 0.24.0 / TP4×PP1×DP2 / C8 / ISL1024 / OSL1024 → TTFT P99 137.71360100002494 ms、TPOT P99 8.618903230694222 ms、吞吐 908.1847846247342 tok/s
   - Qwen/Qwen3-32B / nvidia-h100-sxm-80gb / bf16 / vllm 0.24.0 / TP4×PP1×DP2 / C8 / ISL8192 / OSL256 → TTFT P99 861.7461110000002 ms、TPOT P99 12.326389607843286 ms、吞吐 591.6756451215094 tok/s
   - Qwen/Qwen3-32B / nvidia-h100-sxm-80gb / bf16 / vllm 0.24.0 / TP8×PP1×DP1 / C8 / ISL8192 / OSL256 → TTFT P99 727.2969240000001 ms、TPOT P99 11.230072717647314 ms、吞吐 667.5295614199107 tok/s
   - Qwen/Qwen3-32B / nvidia-h100-sxm-80gb / bf16 / vllm 0.24.0 / TP8×PP1×DP1 / C32 / ISL8192 / OSL256 → TTFT P99 5201.827252000001 ms、TPOT P99 31.45861707450993 ms、吞吐 990.888548064229 tok/s
   - Qwen/Qwen3-32B / nvidia-h100-sxm-80gb / bf16 / vllm 0.24.0 / TP4×PP1×DP1 / C32 / ISL8192 / OSL256 → TTFT P99 8359.441161 ms、TPOT P99 50.170601298039216 ms、吞吐 621.8971855351141 tok/s
   - Qwen/Qwen3-32B / nvidia-h100-sxm-80gb / bf16 / vllm 0.24.0 / TP4×PP1×DP1 / C32 / ISL1024 / OSL1024 → TTFT P99 1044.013729 ms、TPOT P99 11.304065113391985 ms、吞吐 2759.5234009630535 tok/s
   - Qwen/Qwen3-32B / nvidia-h100-sxm-80gb / bf16 / vllm 0.24.0 / TP8×PP1×DP1 / C32 / ISL1024 / OSL1024 → TTFT P99 678.928668 ms、TPOT P99 7.646308508308902 ms、吞吐 4071.268859775253 tok/s
   - Qwen/Qwen3-32B / nvidia-h100-sxm-80gb / bf16 / vllm 0.24.0 / TP4×PP1×DP2 / C32 / ISL1024 / OSL1024 → TTFT P99 520.8245929999999 ms、TPOT P99 9.653310475073386 ms、吞吐 3229.7293058361915 tok/s
   - Qwen/Qwen3-32B / nvidia-h100-sxm-80gb / bf16 / vllm 0.24.0 / TP4×PP1×DP2 / C32 / ISL8192 / OSL256 → TTFT P99 4448.276797 ms、TPOT P99 28.824056776470616 ms、吞吐 1062.4468369728693 tok/s
   - Qwen/Qwen3-32B / nvidia-h100-sxm-80gb / bf16 / vllm 0.24.0 / TP4×PP1×DP1 / C64 / ISL1024 / OSL1024 → TTFT P99 2091.052369 ms、TPOT P99 14.00218116324539 ms、吞吐 4476.380464100694 tok/s
   - Qwen/Qwen3-32B / nvidia-h100-sxm-80gb / bf16 / vllm 0.24.0 / TP4×PP1×DP1 / C64 / ISL8192 / OSL256 → TTFT P99 18030.104925999993 ms、TPOT P99 91.11349433333312 ms、吞吐 691.290490942291 tok/s
   - Qwen/Qwen3-32B / nvidia-h100-sxm-80gb / bf16 / vllm 0.24.0 / TP8×PP1×DP1 / C64 / ISL1024 / OSL1024 → TTFT P99 1358.8681519999998 ms、TPOT P99 9.276825615835756 ms、吞吐 6747.2858397137925 tok/s
   - Qwen/Qwen3-32B / nvidia-h100-sxm-80gb / bf16 / vllm 0.24.0 / TP4×PP1×DP2 / C64 / ISL1024 / OSL1024 → TTFT P99 1044.013729 ms、TPOT P99 11.332671839687185 ms、吞吐 5519.110887783805 tok/s
   - Qwen/Qwen3-32B / nvidia-h100-sxm-80gb / bf16 / vllm 0.24.0 / TP4×PP1×DP2 / C64 / ISL8192 / OSL256 → TTFT P99 9264.708154999998 ms、TPOT P99 50.170601298039216 ms、吞吐 1242.203008622216 tok/s
   - Qwen/Qwen3-32B / nvidia-h100-sxm-80gb / bf16 / vllm 0.24.0 / TP8×PP1×DP1 / C64 / ISL8192 / OSL256 → TTFT P99 11211.532603000005 ms、TPOT P99 56.43859718823514 ms、吞吐 1115.0950375870086 tok/s
   - Qwen/Qwen3-32B / nvidia-h200-sxm-141gb / bf16 / vllm 0.24.0 / TP2×PP1×DP1 / C8 / ISL1024 / OSL1024 → TTFT P99 440.7024270000402 ms、TPOT P99 11.05583574389083 ms、吞吐 691.7932401874034 tok/s
   - Qwen/Qwen3-32B / nvidia-h200-sxm-141gb / bf16 / vllm 0.24.0 / TP2×PP1×DP1 / C8 / ISL8192 / OSL256 → TTFT P99 1995.562819 ms、TPOT P99 26.646441819607944 ms、吞吐 279.3985178058005 tok/s
   - Qwen/Qwen3-32B / nvidia-h200-sxm-141gb / bf16 / vllm 0.24.0 / TP4×PP1×DP2 / C8 / ISL1024 / OSL1024 → TTFT P99 132.70885400001134 ms、TPOT P99 7.0779355053764395 ms、吞吐 1102.6959193454597 tok/s
   - Qwen/Qwen3-32B / nvidia-h200-sxm-141gb / bf16 / vllm 0.24.0 / TP4×PP1×DP2 / C8 / ISL8192 / OSL256 → TTFT P99 878.576915 ms、TPOT P99 10.833958360784477 ms、吞吐 664.772924145956 tok/s
   - Qwen/Qwen3-32B / nvidia-h200-sxm-141gb / bf16 / vllm 0.24.0 / TP2×PP1×DP1 / C32 / ISL1024 / OSL1024 → TTFT P99 1765.553723 ms、TPOT P99 14.477458347995988 ms、吞吐 2149.7672006351536 tok/s
   - Qwen/Qwen3-32B / nvidia-h200-sxm-141gb / bf16 / vllm 0.24.0 / TP2×PP1×DP1 / C32 / ISL8192 / OSL256 → TTFT P99 14356.537065000002 ms、TPOT P99 79.91176538039238 ms、吞吐 390.35883497438374 tok/s
   - Qwen/Qwen3-32B / nvidia-h200-sxm-141gb / bf16 / vllm 0.24.0 / TP4×PP1×DP1 / C32 / ISL8192 / OSL256 → TTFT P99 8481.037019000001 ms、TPOT P99 47.49475550196087 ms、吞吐 656.4300876984471 tok/s
   - Qwen/Qwen3-32B / nvidia-h200-sxm-141gb / bf16 / vllm 0.24.0 / TP8×PP1×DP1 / C32 / ISL8192 / OSL256 → TTFT P99 4955.538856 ms、TPOT P99 28.770253968627422 ms、吞吐 1082.997232236789 tok/s
   - Qwen/Qwen3-32B / nvidia-h200-sxm-141gb / bf16 / vllm 0.24.0 / TP4×PP1×DP2 / C32 / ISL1024 / OSL1024 → TTFT P99 532.0884139999998 ms、TPOT P99 8.038959994134894 ms、吞吐 3873.420667483136 tok/s
   - Qwen/Qwen3-32B / nvidia-h200-sxm-141gb / bf16 / vllm 0.24.0 / TP4×PP1×DP2 / C32 / ISL8192 / OSL256 → TTFT P99 4521.6287919999995 ms、TPOT P99 26.8434794 ms、吞吐 1138.1864434887902 tok/s
   - Qwen/Qwen3-32B / nvidia-h200-sxm-141gb / bf16 / vllm 0.24.0 / TP4×PP1×DP2 / C40 / ISL8192 / OSL256 → TTFT P99 5739.230234000001 ms、TPOT P99 32.08379045882372 ms、吞吐 1195.654714987972 tok/s
   - Qwen/Qwen3-32B / nvidia-h200-sxm-141gb / bf16 / vllm 0.24.0 / TP4×PP1×DP2 / C48 / ISL8192 / OSL256 → TTFT P99 6957.594815000001 ms、TPOT P99 37.223840470588264 ms、吞吐 1240.3436830209462 tok/s
   - Qwen/Qwen3-32B / nvidia-h200-sxm-141gb / bf16 / vllm 0.24.0 / TP4×PP1×DP2 / C56 / ISL8192 / OSL256 → TTFT P99 8176.317045000002 ms、TPOT P99 42.36271450980398 ms、吞吐 1285.7037039824977 tok/s
   - Qwen/Qwen3-32B / nvidia-h200-sxm-141gb / bf16 / vllm 0.24.0 / TP2×PP1×DP1 / C64 / ISL1024 / OSL1024 → TTFT P99 3533.6141140000004 ms、TPOT P99 18.36405357673509 ms、吞吐 3407.386670638768 tok/s
   - Qwen/Qwen3-32B / nvidia-h200-sxm-141gb / bf16 / vllm 0.24.0 / TP2×PP1×DP1 / C64 / ISL8192 / OSL256 → TTFT P99 30866.989649000003 ms、TPOT P99 148.80633225882306 ms、吞吐 423.32901395617455 tok/s
   - Qwen/Qwen3-32B / nvidia-h200-sxm-141gb / bf16 / vllm 0.24.0 / TP4×PP1×DP2 / C64 / ISL1024 / OSL1024 → TTFT P99 1065.5455469999997 ms、TPOT P99 9.39976863734117 ms、吞吐 6648.3700709741215 tok/s
   - Qwen/Qwen3-32B / nvidia-h200-sxm-141gb / bf16 / vllm 0.24.0 / TP4×PP1×DP2 / C64 / ISL8192 / OSL256 → TTFT P99 9395.242326 ms、TPOT P99 47.49475550196081 ms、吞吐 1311.2457649568023 tok/s
   - Qwen/Qwen3-32B / nvidia-h200-sxm-141gb / bf16 / vllm 0.24.0 / TP4×PP1×DP2 / C96 / ISL8192 / OSL256 → TTFT P99 14265.872607999992 ms、TPOT P99 67.54073382745081 ms、吞吐 1376.624854092987 tok/s
   - Qwen/Qwen3-32B / nvidia-h200-sxm-141gb / bf16 / vllm 0.24.0 / TP4×PP1×DP2 / C128 / ISL8192 / OSL256 → TTFT P99 19121.870338999986 ms、TPOT P99 87.29699354117625 ms、吞吐 1436.3384727706505 tok/s
   - Qwen/Qwen3-8B / nvidia-h100-sxm-80gb / bf16 / vllm 0.24.0 / TP1×PP1×DP1 / C— / ISL8192 / OSL256 → TTFT P99 654.54886086646 ms、TPOT P99 18.03786428235403 ms、吞吐 274.27803481996517 tok/s
   - Qwen/Qwen3-8B / nvidia-h100-sxm-80gb / bf16 / vllm 0.24.0 / TP1×PP1×DP1 / C— / ISL8192 / OSL256 → TTFT P99 1004.0468315048784 ms、TPOT P99 53.25004861960804 ms、吞吐 544.5986460914221 tok/s
   - Qwen/Qwen3-8B / nvidia-h100-sxm-80gb / bf16 / vllm 0.24.0 / TP1×PP1×DP1 / C— / ISL8192 / OSL256 → TTFT P99 23321.40930932734 ms、TPOT P99 63.998291674510156 ms、吞吐 703.4322981123667 tok/s
   - Qwen/Qwen3-8B / nvidia-h100-sxm-80gb / bf16 / vllm 0.24.0 / TP1×PP1×DP1 / C— / ISL8192 / OSL256 → TTFT P99 60174.8116980795 ms、TPOT P99 76.13251087843177 ms、吞吐 700.192047763515 tok/s
   - Qwen/Qwen3-8B / nvidia-h100-sxm-80gb / bf16 / vllm 0.24.0 / TP1×PP1×DP2 / C— / ISL8192 / OSL256 → TTFT P99 452.2775124297391 ms、TPOT P99 14.658638909803908 ms、吞吐 546.1767611366565 tok/s
   - Qwen/Qwen3-8B / nvidia-h100-sxm-80gb / bf16 / vllm 0.24.0 / TP1×PP1×DP2 / C— / ISL8192 / OSL256 → TTFT P99 593.5181920715222 ms、TPOT P99 20.913063670588187 ms、吞吐 773.1155588940723 tok/s
   - Qwen/Qwen3-8B / nvidia-h100-sxm-80gb / bf16 / vllm 0.24.0 / TP1×PP1×DP2 / C— / ISL8192 / OSL256 → TTFT P99 809.5613405105687 ms、TPOT P99 34.5578661843134 ms、吞吐 1018.9233272202802 tok/s
   - Qwen/Qwen3-8B / nvidia-h100-sxm-80gb / bf16 / vllm 0.24.0 / TP1×PP1×DP2 / C— / ISL8192 / OSL256 → TTFT P99 999.3026506029273 ms、TPOT P99 58.93375709019664 ms、吞吐 1261.3523526622848 tok/s
   - Qwen/Qwen3-8B / nvidia-h100-sxm-80gb / bf16 / vllm 0.24.0 / TP1×PP1×DP1 / C8 / ISL1024 / OSL1024 → TTFT P99 185.48182300000917 ms、TPOT P99 7.230641140762756 ms、吞吐 1073.1434253976 tok/s
   - Qwen/Qwen3-8B / nvidia-h100-sxm-80gb / bf16 / vllm 0.24.0 / TP1×PP1×DP1 / C8 / ISL8192 / OSL256 → TTFT P99 887.919842 ms、TPOT P99 16.005433647058844 ms、吞吐 473.4381502095157 tok/s
   - Qwen/Qwen3-8B / nvidia-h100-sxm-80gb / bf16 / vllm 0.24.0 / TP1×PP1×DP2 / C8 / ISL1024 / OSL1024 → TTFT P99 93.09983800002374 ms、TPOT P99 6.744311719452742 ms、吞吐 1162.361629272693 tok/s
   - Qwen/Qwen3-8B / nvidia-h100-sxm-80gb / bf16 / vllm 0.24.0 / TP1×PP1×DP2 / C8 / ISL8192 / OSL256 → TTFT P99 653.9557169999999 ms、TPOT P99 11.053877941176625 ms、吞吐 669.3956053210023 tok/s
   - Qwen/Qwen3-8B / nvidia-h100-sxm-80gb / bf16 / vllm 0.24.0 / TP1×PP1×DP1 / C32 / ISL1024 / OSL1024 → TTFT P99 746.227258 ms、TPOT P99 10.10754084652979 ms、吞吐 3096.7872683153632 tok/s
   - Qwen/Qwen3-8B / nvidia-h100-sxm-80gb / bf16 / vllm 0.24.0 / TP1×PP1×DP1 / C32 / ISL8192 / OSL256 → TTFT P99 6609.393869999999 ms、TPOT P99 46.599841372549015 ms、吞吐 671.6197848398688 tok/s
   - Qwen/Qwen3-8B / nvidia-h100-sxm-80gb / bf16 / vllm 0.24.0 / TP1×PP1×DP2 / C32 / ISL1024 / OSL1024 → TTFT P99 371.39593 ms、TPOT P99 8.608299143695115 ms、吞吐 3643.8625365988637 tok/s
   - Qwen/Qwen3-8B / nvidia-h100-sxm-80gb / bf16 / vllm 0.24.0 / TP1×PP1×DP2 / C32 / ISL8192 / OSL256 → TTFT P99 3486.4293139999995 ms、TPOT P99 27.420675007843208 ms、吞吐 1127.7751784411603 tok/s
   - Qwen/Qwen3-8B / nvidia-h100-sxm-80gb / bf16 / vllm 0.24.0 / TP1×PP1×DP4 / C32 / ISL8192 / OSL256 → TTFT P99 1827.7921460000002 ms、TPOT P99 16.0054336470589 ms、吞吐 1880.7211353088285 tok/s
   - Qwen/Qwen3-8B / nvidia-h100-sxm-80gb / bf16 / vllm 0.24.0 / TP1×PP1×DP2 / C40 / ISL8192 / OSL256 → TTFT P99 4443.510729 ms、TPOT P99 32.13858656470586 ms、吞吐 1204.2801594556427 tok/s
   - Qwen/Qwen3-8B / nvidia-h100-sxm-80gb / bf16 / vllm 0.24.0 / TP1×PP1×DP2 / C48 / ISL8192 / OSL256 → TTFT P99 5404.200543 ms、TPOT P99 36.983700949019656 ms、吞吐 1257.2509562704433 tok/s
   - Qwen/Qwen3-8B / nvidia-h100-sxm-80gb / bf16 / vllm 0.24.0 / TP1×PP1×DP2 / C56 / ISL8192 / OSL256 → TTFT P99 6367.9972099999995 ms、TPOT P99 41.80868124705882 ms、吞吐 1309.3396396196968 tok/s
   - Qwen/Qwen3-8B / nvidia-h100-sxm-80gb / bf16 / vllm 0.24.0 / TP1×PP1×DP1 / C64 / ISL1024 / OSL1024 → TTFT P99 1499.3555680000002 ms、TPOT P99 13.52109428739 ms、吞吐 4657.798733662274 tok/s
   - Qwen/Qwen3-8B / nvidia-h100-sxm-80gb / bf16 / vllm 0.24.0 / TP1×PP1×DP1 / C64 / ISL8192 / OSL256 → TTFT P99 19798.39472699997 ms、TPOT P99 76.13251087843177 ms、吞吐 700.192047763515 tok/s
   - Qwen/Qwen3-8B / nvidia-h100-sxm-80gb / bf16 / vllm 0.24.0 / TP1×PP1×DP2 / C64 / ISL1024 / OSL1024 → TTFT P99 746.227258 ms、TPOT P99 10.129031437927686 ms、吞吐 6191.125539745652 tok/s
   - Qwen/Qwen3-8B / nvidia-h100-sxm-80gb / bf16 / vllm 0.24.0 / TP1×PP1×DP2 / C64 / ISL8192 / OSL256 → TTFT P99 7334.551824999999 ms、TPOT P99 46.599841372549015 ms、吞吐 1341.269828068544 tok/s
   - Qwen/Qwen3-8B / nvidia-h100-sxm-80gb / bf16 / vllm 0.24.0 / TP1×PP1×DP2 / C96 / ISL8192 / OSL256 → TTFT P99 11034.099922 ms、TPOT P99 76.13560087843159 ms、吞吐 1394.4902625469197 tok/s
   - Qwen/Qwen3-8B / nvidia-h100-sxm-80gb / bf16 / vllm 0.24.0 / TP1×PP1×DP2 / C128 / ISL8192 / OSL256 → TTFT P99 20535.195289999967 ms、TPOT P99 76.13560087843159 ms、吞吐 1394.4902625469197 tok/s
   - Qwen/Qwen3-8B / nvidia-h200-sxm-141gb / bf16 / vllm 0.24.0 / TP1×PP1×DP2 / C— / ISL8192 / OSL256 → TTFT P99 435.37229772889987 ms、TPOT P99 10.99272484313748 ms、吞吐 547.3118701294937 tok/s
   - Qwen/Qwen3-8B / nvidia-h200-sxm-141gb / bf16 / vllm 0.24.0 / TP1×PP1×DP2 / C— / ISL8192 / OSL256 → TTFT P99 554.438000886541 ms、TPOT P99 16.477727670588457 ms、吞吐 817.4543040497814 tok/s
   - Qwen/Qwen3-8B / nvidia-h200-sxm-141gb / bf16 / vllm 0.24.0 / TP1×PP1×DP2 / C— / ISL8192 / OSL256 → TTFT P99 625.4226347604636 ms、TPOT P99 26.289728533333324 ms、吞吐 1084.3566101091678 tok/s
   - Qwen/Qwen3-8B / nvidia-h200-sxm-141gb / bf16 / vllm 0.24.0 / TP1×PP1×DP2 / C— / ISL8192 / OSL256 → TTFT P99 854.2590297111892 ms、TPOT P99 35.675425713725375 ms、吞吐 1272.9612253381513 tok/s
   - Qwen/Qwen3-8B / nvidia-h200-sxm-141gb / bf16 / vllm 0.24.0 / TP1×PP1×DP1 / C8 / ISL1024 / OSL1024 → TTFT P99 181.30924300000333 ms、TPOT P99 5.540693851417527 ms、吞吐 1390.5781678719216 tok/s
   - Qwen/Qwen3-8B / nvidia-h200-sxm-141gb / bf16 / vllm 0.24.0 / TP1×PP1×DP1 / C8 / ISL8192 / OSL256 → TTFT P99 859.7394019999999 ms、TPOT P99 13.592190360784526 ms、吞吐 553.5143888489443 tok/s
   - Qwen/Qwen3-8B / nvidia-h200-sxm-141gb / bf16 / vllm 0.24.0 / TP1×PP1×DP2 / C8 / ISL1024 / OSL1024 → TTFT P99 91.21148900000844 ms、TPOT P99 5.313530915933562 ms、吞吐 1471.2269209774552 tok/s
   - Qwen/Qwen3-8B / nvidia-h200-sxm-141gb / bf16 / vllm 0.24.0 / TP1×PP1×DP2 / C8 / ISL8192 / OSL256 → TTFT P99 636.6716029999999 ms、TPOT P99 9.002842921569062 ms、吞吐 810.6443773058669 tok/s
   - Qwen/Qwen3-8B / nvidia-h200-sxm-141gb / bf16 / vllm 0.24.0 / TP1×PP1×DP1 / C32 / ISL1024 / OSL1024 → TTFT P99 728.1516899999998 ms、TPOT P99 7.712977738025394 ms、吞吐 4049.0165794668155 tok/s
   - Qwen/Qwen3-8B / nvidia-h200-sxm-141gb / bf16 / vllm 0.24.0 / TP1×PP1×DP1 / C32 / ISL8192 / OSL256 → TTFT P99 6286.380569000001 ms、TPOT P99 40.78204896862738 ms、吞吐 767.0359516128434 tok/s
   - Qwen/Qwen3-8B / nvidia-h200-sxm-141gb / bf16 / vllm 0.24.0 / TP1×PP1×DP2 / C32 / ISL1024 / OSL1024 → TTFT P99 362.8524709999999 ms、TPOT P99 6.498827639296478 ms、吞吐 4811.783329426065 tok/s
   - Qwen/Qwen3-8B / nvidia-h200-sxm-141gb / bf16 / vllm 0.24.0 / TP1×PP1×DP2 / C32 / ISL8192 / OSL256 → TTFT P99 3330.3272680000005 ms、TPOT P99 22.918945984313847 ms、吞吐 1341.9938095420605 tok/s
   - Qwen/Qwen3-8B / nvidia-h200-sxm-141gb / bf16 / vllm 0.24.0 / TP1×PP1×DP1 / C64 / ISL1024 / OSL1024 → TTFT P99 1460.2410519999996 ms、TPOT P99 10.414255296187664 ms、吞吐 6039.994042071066 tok/s
   - Qwen/Qwen3-8B / nvidia-h200-sxm-141gb / bf16 / vllm 0.24.0 / TP1×PP1×DP1 / C64 / ISL8192 / OSL256 → TTFT P99 13681.380651000005 ms、TPOT P99 75.51767865882333 ms、吞吐 834.6444646073313 tok/s
   - Qwen/Qwen3-8B / nvidia-h200-sxm-141gb / bf16 / vllm 0.24.0 / TP1×PP1×DP2 / C64 / ISL1024 / OSL1024 → TTFT P99 728.1516899999998 ms、TPOT P99 7.735301954056702 ms、吞吐 8094.894604956275 tok/s
   - Qwen/Qwen3-8B / nvidia-h200-sxm-141gb / bf16 / vllm 0.24.0 / TP1×PP1×DP2 / C64 / ISL8192 / OSL256 → TTFT P99 6972.955788000001 ms、TPOT P99 40.78204896862752 ms、吞吐 1532.2982314387943 tok/s
2. 其余模型、硬件与工作点最多只能做静态规划；没有 P99 时报告必须
   给出缺口，不能推荐“达标”。
3. 扫描器数据只用于候选枚举与相对排序，不能拿来判 SLA。
4. 公开参考与历史 Excel 记录不参与 SLA、不参与报价门禁。
5. 成本分析只在本仓库有对应成本项时可用，且示例价格为合成值；
   “可算成本”不等于“可发布报价”。
