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
| qwen3-8b | NVIDIA H100 SXM | simulated_native_p99 | 15 | 是 |
| qwen3-8b | NVIDIA H200 SXM | simulated_native_p99 | 12 | 否 |
| qwen3-32b | NVIDIA H100 SXM | simulated_native_p99 | 182 | 是 |
| qwen3-32b | NVIDIA H200 SXM | simulated_native_p99 | 16 | 否 |

`static_only` 是矩阵级别，不是一条独立证据；`evidence_by_level` 只统计有实际证据的级别（2/3/4）。
未支持的模型（解析器会拒绝，不做硬算）：
- `qwen3-30b-a3b`：MoE 结构（专家数与专家路由影响权重显存），本轮公式只覆盖 Dense； 如需支持需先明确 expert 权重布局与激活专家的显存口径。
- `deepseek-v3`：MoE + MLA（Multi-head Latent Attention），KV 结构与 Dense GQA 不同，本轮不覆盖。

## 3. 工作点覆盖

按“模型 + 精度 + 硬件 + 框架/版本 + TP/PP/DP + ISL/OSL/并发/ctx”分组；样本窗口（请求数）与数据集记录在每条证据上，不参与分组；不同工作点之间**不做插值**。

| 工作点 | 模型/精度 | 硬件/并行 | 负载 | 最高级别 | SLA 可用 | TTFT P99 (ms) | TPOT P99 (ms) | 吞吐 (tok/s) | 阻塞原因 |
|---|---|---|---|---|---|---|---|---|---|
| `b6b576a31561709a` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP8/PP1/DP1 | ISL8192/OSL256/C1/ctx8192 | simulated_scan_only | 否 | — | — | 147.4 | 扫描器输出是稳态单点（均值口径），不含 P99；不能用于 SLA 判定 |
| `c9fe58e923100deb` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP8/PP1/DP1 | ISL8192/OSL256/C2/ctx8192 | simulated_scan_only | 否 | — | — | 242.482 | 扫描器输出是稳态单点（均值口径），不含 P99；不能用于 SLA 判定 |
| `98cd3b7722aa53d6` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP8/PP1/DP1 | ISL8192/OSL256/C3/ctx8192 | simulated_scan_only | 否 | — | — | 331.277 | 扫描器输出是稳态单点（均值口径），不含 P99；不能用于 SLA 判定 |
| `fd243683a59cda65` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP8/PP1/DP1 | ISL8192/OSL256/C4/ctx8192 | simulated_scan_only | 否 | — | — | 412.012 | 扫描器输出是稳态单点（均值口径），不含 P99；不能用于 SLA 判定 |
| `6dc6b50169049193` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP8/PP1/DP1 | ISL8192/OSL256/C4/ctx16384 | simulated_scan_only | 否 | — | — | 380.379 | 扫描器输出是稳态单点（均值口径），不含 P99；不能用于 SLA 判定 |
| `5974b0405c7356f1` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP8/PP1/DP1 | ISL8192/OSL256/C5/ctx8192 | simulated_scan_only | 否 | — | — | 471.729 | 扫描器输出是稳态单点（均值口径），不含 P99；不能用于 SLA 判定 |
| `c8ee6f30e3f2aa74` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP8/PP1/DP1 | ISL8192/OSL256/C6/ctx8192 | simulated_scan_only | 否 | — | — | 533.154 | 扫描器输出是稳态单点（均值口径），不含 P99；不能用于 SLA 判定 |
| `b781f81fe11e2e9e` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP8/PP1/DP1 | ISL8192/OSL256/C6/ctx16384 | simulated_scan_only | 否 | — | — | 495.673 | 扫描器输出是稳态单点（均值口径），不含 P99；不能用于 SLA 判定 |
| `3d373f771de9f860` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP8/PP1/DP1 | ISL8192/OSL256/C7/ctx8192 | simulated_scan_only | 否 | — | — | 581.916 | 扫描器输出是稳态单点（均值口径），不含 P99；不能用于 SLA 判定 |
| `2f9854254e25f870` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP4/PP1/DP1 | ISL1024/OSL1024/C8 | simulated_native_p99 | 是 | 260.4617910000088 | 8.564802355816468 | 900.8776991343514 | — |
| `24a56f94a54c85c6` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP4/PP1/DP1 | ISL8192/OSL256/C8 | simulated_native_p99 | 是 | 1159.756941 | 17.848578929412408 | 420.4521057972523 | — |
| `d17c99dcfb4c9b1d` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP8/PP1/DP1 | ISL1024/OSL1024/C8 | simulated_native_p99 | 是 | 169.22123400000055 | 5.983962341153699 | 1292.8510420687026 | — |
| `ef488d65060821fa` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP4/PP1/DP2 | ISL1024/OSL1024/C8 | simulated_native_p99 | 是 | 137.71360100002494 | 8.618903230694222 | 908.1847846247342 | — |
| `a650c47ef339dcf8` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP4/PP1/DP2 | ISL8192/OSL256/C8 | simulated_native_p99 | 是 | 861.7461110000002 | 12.326389607843286 | 591.6756451215094 | — |
| `58c7e4998c87a93b` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP8/PP1/DP1 | ISL8192/OSL256/C8 | simulated_native_p99 | 是 | 727.2969240000001 | 11.230072717647314 | 667.5295614199107 | — |
| `f306fb335f4b7838` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP8/PP1/DP1 | ISL8192/OSL256/C8/ctx8192 | simulated_scan_only | 否 | — | — | 627.163 | 扫描器输出是稳态单点（均值口径），不含 P99；不能用于 SLA 判定 |
| `fe650110b0b47466` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP8/PP1/DP1 | ISL8192/OSL256/C8/ctx16384 | simulated_scan_only | 否 | — | — | 586.651 | 扫描器输出是稳态单点（均值口径），不含 P99；不能用于 SLA 判定 |
| `1aed7324c31ae52b` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP8/PP1/DP1 | ISL8192/OSL256/C9/ctx8192 | simulated_scan_only | 否 | — | — | 659.523 | 扫描器输出是稳态单点（均值口径），不含 P99；不能用于 SLA 判定 |
| `da9d90dfc7ae718a` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP4/PP1/DP1 | ISL8192/OSL256/C9/ctx8192 | simulated_scan_only | 否 | — | — | 421.836 | 扫描器输出是稳态单点（均值口径），不含 P99；不能用于 SLA 判定 |
| `02cda410deb90012` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP8/PP1/DP1 | ISL8192/OSL256/C10/ctx8192 | simulated_scan_only | 否 | — | — | 682.636 | 扫描器输出是稳态单点（均值口径），不含 P99；不能用于 SLA 判定 |
| `bc1c11daaff136ef` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP4/PP1/DP1 | ISL8192/OSL256/C10/ctx8192 | simulated_scan_only | 否 | — | — | 441.502 | 扫描器输出是稳态单点（均值口径），不含 P99；不能用于 SLA 判定 |
| `adb70b2e07d40785` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP8/PP1/DP1 | ISL8192/OSL256/C11/ctx8192 | simulated_scan_only | 否 | — | — | 719.531 | 扫描器输出是稳态单点（均值口径），不含 P99；不能用于 SLA 判定 |
| `8c5dff36d307fd47` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP4/PP1/DP1 | ISL8192/OSL256/C11/ctx8192 | simulated_scan_only | 否 | — | — | 460.396 | 扫描器输出是稳态单点（均值口径），不含 P99；不能用于 SLA 判定 |
| `a4f356f5c5f3c194` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP8/PP1/DP1 | ISL8192/OSL256/C12/ctx8192 | simulated_scan_only | 否 | — | — | 742.676 | 扫描器输出是稳态单点（均值口径），不含 P99；不能用于 SLA 判定 |
| `39830267bc17e849` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP4/PP1/DP1 | ISL8192/OSL256/C12/ctx8192 | simulated_scan_only | 否 | — | — | 477.672 | 扫描器输出是稳态单点（均值口径），不含 P99；不能用于 SLA 判定 |
| `fe30b9cb306c4330` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP8/PP1/DP1 | ISL8192/OSL256/C13/ctx8192 | simulated_scan_only | 否 | — | — | 766.425 | 扫描器输出是稳态单点（均值口径），不含 P99；不能用于 SLA 判定 |
| `c694db08eab09e21` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP4/PP1/DP1 | ISL8192/OSL256/C13/ctx8192 | simulated_scan_only | 否 | — | — | 487.888 | 扫描器输出是稳态单点（均值口径），不含 P99；不能用于 SLA 判定 |
| `de93b4c20718b1ba` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP8/PP1/DP1 | ISL8192/OSL256/C14/ctx8192 | simulated_scan_only | 否 | — | — | 788.07 | 扫描器输出是稳态单点（均值口径），不含 P99；不能用于 SLA 判定 |
| `24c215545098d01c` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP4/PP1/DP1 | ISL8192/OSL256/C14/ctx8192 | simulated_scan_only | 否 | — | — | 508.159 | 扫描器输出是稳态单点（均值口径），不含 P99；不能用于 SLA 判定 |
| `114a77c66f1b8168` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP8/PP1/DP1 | ISL8192/OSL256/C15/ctx8192 | simulated_scan_only | 否 | — | — | 807.007 | 扫描器输出是稳态单点（均值口径），不含 P99；不能用于 SLA 判定 |
| `574a224c6a0916c5` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP4/PP1/DP1 | ISL8192/OSL256/C15/ctx8192 | simulated_scan_only | 否 | — | — | 521.185 | 扫描器输出是稳态单点（均值口径），不含 P99；不能用于 SLA 判定 |
| `bc3735b658495a76` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP8/PP1/DP1 | ISL8192/OSL256/C16/ctx8192 | simulated_scan_only | 否 | — | — | 821.667 | 扫描器输出是稳态单点（均值口径），不含 P99；不能用于 SLA 判定 |
| `9e97059c64eb5b9f` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP4/PP1/DP1 | ISL8192/OSL256/C16/ctx8192 | simulated_scan_only | 否 | — | — | 525.592 | 扫描器输出是稳态单点（均值口径），不含 P99；不能用于 SLA 判定 |
| `57cf91493ee08660` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP8/PP1/DP1 | ISL8192/OSL256/C20/ctx8192 | simulated_scan_only | 否 | — | — | 875.575 | 扫描器输出是稳态单点（均值口径），不含 P99；不能用于 SLA 判定 |
| `78c1276e2cd071da` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP4/PP1/DP1 | ISL8192/OSL256/C20/ctx8192 | simulated_scan_only | 否 | — | — | 562.9 | 扫描器输出是稳态单点（均值口径），不含 P99；不能用于 SLA 判定 |
| `7708ea6a6644d47c` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP8/PP1/DP1 | ISL8192/OSL256/C20/ctx16384 | simulated_scan_only | 否 | — | — | 839.538 | 扫描器输出是稳态单点（均值口径），不含 P99；不能用于 SLA 判定 |
| `1c0177726f0686d3` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP8/PP1/DP1 | ISL8192/OSL256/C24/ctx8192 | simulated_scan_only | 否 | — | — | 924.067 | 扫描器输出是稳态单点（均值口径），不含 P99；不能用于 SLA 判定 |
| `e042bcbd9de5312a` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP2/PP1/DP1 | ISL8192/OSL256/C24/ctx8192 | simulated_scan_only | 否 | — | — | 343.701 | 扫描器输出是稳态单点（均值口径），不含 P99；不能用于 SLA 判定 |
| `c94c1e65892fab5b` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP4/PP1/DP1 | ISL8192/OSL256/C24/ctx8192 | simulated_scan_only | 否 | — | — | 592.153 | 扫描器输出是稳态单点（均值口径），不含 P99；不能用于 SLA 判定 |
| `8a5ed0f9ba76bbc6` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP8/PP1/DP1 | ISL8192/OSL256/C24/ctx16384 | simulated_scan_only | 否 | — | — | 889.832 | 扫描器输出是稳态单点（均值口径），不含 P99；不能用于 SLA 判定 |
| `24bccdb213d8041b` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP8/PP1/DP1 | ISL8192/OSL256/C28/ctx8192 | simulated_scan_only | 否 | — | — | 964.001 | 扫描器输出是稳态单点（均值口径），不含 P99；不能用于 SLA 判定 |
| `6b4d5886a8599601` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP2/PP1/DP1 | ISL8192/OSL256/C28/ctx8192 | simulated_scan_only | 否 | — | — | 356.703 | 扫描器输出是稳态单点（均值口径），不含 P99；不能用于 SLA 判定 |
| `8c617066a36e4891` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP4/PP1/DP1 | ISL8192/OSL256/C28/ctx8192 | simulated_scan_only | 否 | — | — | 614.857 | 扫描器输出是稳态单点（均值口径），不含 P99；不能用于 SLA 判定 |
| `ab9281daff448830` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP8/PP1/DP1 | ISL8192/OSL256/C28/ctx16384 | simulated_scan_only | 否 | — | — | 931.534 | 扫描器输出是稳态单点（均值口径），不含 P99；不能用于 SLA 判定 |
| `6d8f3ebbef51e978` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP8/PP1/DP1 | ISL8192/OSL256/C32 | simulated_native_p99 | 是 | 5201.827252000001 | 31.45861707450993 | 990.888548064229 | — |
| `4c1922f823a0406b` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP4/PP1/DP1 | ISL8192/OSL256/C32 | simulated_native_p99 | 是 | 8359.441161 | 50.170601298039216 | 621.8971855351141 | — |
| `1f0871ab7dba07ce` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP4/PP1/DP1 | ISL1024/OSL1024/C32 | simulated_native_p99 | 是 | 1044.013729 | 11.304065113391985 | 2759.5234009630535 | — |
| `45e085982a2014ae` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP8/PP1/DP1 | ISL1024/OSL1024/C32 | simulated_native_p99 | 是 | 678.928668 | 7.646308508308902 | 4071.268859775253 | — |
| `b42c80c72f3eec21` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP4/PP1/DP2 | ISL1024/OSL1024/C32 | simulated_native_p99 | 是 | 520.8245929999999 | 9.653310475073386 | 3229.7293058361915 | — |
| `150644f1c548c1d4` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP4/PP1/DP2 | ISL8192/OSL256/C32 | simulated_native_p99 | 是 | 4448.276797 | 28.824056776470616 | 1062.4468369728693 | — |
| `a41bd8327c5cdeed` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP8/PP1/DP1 | ISL8192/OSL256/C32/ctx8192 | simulated_scan_only | 否 | — | — | 997.419 | 扫描器输出是稳态单点（均值口径），不含 P99；不能用于 SLA 判定 |
| `5c6c6ef903c6db1a` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP2/PP1/DP1 | ISL8192/OSL256/C32/ctx8192 | simulated_scan_only | 否 | — | — | 367.138 | 扫描器输出是稳态单点（均值口径），不含 P99；不能用于 SLA 判定 |
| `4fb088d3fd0fde6f` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP4/PP1/DP1 | ISL8192/OSL256/C32/ctx8192 | simulated_scan_only | 否 | — | — | 633.0 | 扫描器输出是稳态单点（均值口径），不含 P99；不能用于 SLA 判定 |
| `0aa2b662878f3358` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP8/PP1/DP1 | ISL8192/OSL256/C32/ctx16384 | simulated_scan_only | 否 | — | — | 966.671 | 扫描器输出是稳态单点（均值口径），不含 P99；不能用于 SLA 判定 |
| `413ee146379983db` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP8/PP1/DP1 | ISL8192/OSL256/C40/ctx8192 | simulated_scan_only | 否 | — | — | 1053.705 | 扫描器输出是稳态单点（均值口径），不含 P99；不能用于 SLA 判定 |
| `6778239cf75fb5ed` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP4/PP1/DP1 | ISL8192/OSL256/C40/ctx8192 | simulated_scan_only | 否 | — | — | 666.567 | 扫描器输出是稳态单点（均值口径），不含 P99；不能用于 SLA 判定 |
| `5bf23a5924dd6959` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP8/PP1/DP1 | ISL8192/OSL256/C40/ctx16384 | simulated_scan_only | 否 | — | — | 1026.269 | 扫描器输出是稳态单点（均值口径），不含 P99；不能用于 SLA 判定 |
| `de7c7acde89340ad` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP8/PP1/DP1 | ISL8192/OSL256/C48/ctx8192 | simulated_scan_only | 否 | — | — | 1089.946 | 扫描器输出是稳态单点（均值口径），不含 P99；不能用于 SLA 判定 |
| `e61545940b3286d6` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP8/PP1/DP1 | ISL8192/OSL256/C48/ctx16384 | simulated_scan_only | 否 | — | — | 1065.332 | 扫描器输出是稳态单点（均值口径），不含 P99；不能用于 SLA 判定 |
| `5cddfa6f352aad07` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP8/PP1/DP1 | ISL8192/OSL256/C56/ctx8192 | simulated_scan_only | 否 | — | — | 1116.201 | 扫描器输出是稳态单点（均值口径），不含 P99；不能用于 SLA 判定 |
| `9d7e08070fb63898` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP8/PP1/DP1 | ISL8192/OSL256/C56/ctx16384 | simulated_scan_only | 否 | — | — | 1093.891 | 扫描器输出是稳态单点（均值口径），不含 P99；不能用于 SLA 判定 |
| `2ada06c5f145bcbb` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP4/PP1/DP1 | ISL1024/OSL1024/C64 | simulated_native_p99 | 是 | 2091.052369 | 14.00218116324539 | 4476.380464100694 | — |
| `79cf0c54e6bdbf2c` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP4/PP1/DP1 | ISL8192/OSL256/C64 | simulated_native_p99 | 是 | 18030.104925999993 | 91.11349433333312 | 691.290490942291 | — |
| `a5e138bf13fed18b` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP8/PP1/DP1 | ISL1024/OSL1024/C64 | simulated_native_p99 | 是 | 1358.8681519999998 | 9.276825615835756 | 6747.2858397137925 | — |
| `91f609aa669b8617` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP4/PP1/DP2 | ISL1024/OSL1024/C64 | simulated_native_p99 | 是 | 1044.013729 | 11.332671839687185 | 5519.110887783805 | — |
| `cd4709e0526134b2` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP4/PP1/DP2 | ISL8192/OSL256/C64 | simulated_native_p99 | 是 | 9264.708154999998 | 50.170601298039216 | 1242.203008622216 | — |
| `017cccee52093417` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP8/PP1/DP1 | ISL8192/OSL256/C64 | simulated_native_p99 | 是 | 11211.532603000005 | 56.43859718823514 | 1115.0950375870086 | — |
| `d562eb3ede1fe23b` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP8/PP1/DP1 | ISL8192/OSL256/C64/ctx8192 | simulated_scan_only | 否 | — | — | 1137.693 | 扫描器输出是稳态单点（均值口径），不含 P99；不能用于 SLA 判定 |
| `2d5ba5a0e349a7d0` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP8/PP1/DP1 | ISL8192/OSL256/C64/ctx16384 | simulated_scan_only | 否 | — | — | 1117.425 | 扫描器输出是稳态单点（均值口径），不含 P99；不能用于 SLA 判定 |
| `a8fa1a8abc8dc340` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP8/PP1/DP1 | ISL8192/OSL256/C80/ctx8192 | simulated_scan_only | 否 | — | — | 1172.987 | 扫描器输出是稳态单点（均值口径），不含 P99；不能用于 SLA 判定 |
| `45681845ae81c4a6` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP8/PP1/DP1 | ISL8192/OSL256/C80/ctx16384 | simulated_scan_only | 否 | — | — | 1156.658 | 扫描器输出是稳态单点（均值口径），不含 P99；不能用于 SLA 判定 |
| `1cd6cd5977f8d57f` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP8/PP1/DP1 | ISL8192/OSL256/C96/ctx8192 | simulated_scan_only | 否 | — | — | 1195.097 | 扫描器输出是稳态单点（均值口径），不含 P99；不能用于 SLA 判定 |
| `1364527f7db4880a` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP4/PP1/DP1 | ISL8192/OSL256/C96/ctx8192 | simulated_scan_only | 否 | — | — | 741.622 | 扫描器输出是稳态单点（均值口径），不含 P99；不能用于 SLA 判定 |
| `e3449a790fb7b65a` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP8/PP1/DP1 | ISL8192/OSL256/C96/ctx16384 | simulated_scan_only | 否 | — | — | 1181.522 | 扫描器输出是稳态单点（均值口径），不含 P99；不能用于 SLA 判定 |
| `453a0d7bccba308b` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP8/PP1/DP1 | ISL8192/OSL256/C112/ctx8192 | simulated_scan_only | 否 | — | — | 1211.899 | 扫描器输出是稳态单点（均值口径），不含 P99；不能用于 SLA 判定 |
| `1f516def1e47106c` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP8/PP1/DP1 | ISL8192/OSL256/C112/ctx16384 | simulated_scan_only | 否 | — | — | 1200.777 | 扫描器输出是稳态单点（均值口径），不含 P99；不能用于 SLA 判定 |
| `c9f8ffd6fba5d127` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP8/PP1/DP1 | ISL8192/OSL256/C128/ctx8192 | simulated_scan_only | 否 | — | — | 1223.312 | 扫描器输出是稳态单点（均值口径），不含 P99；不能用于 SLA 判定 |
| `ea24fbbcdcbe4044` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP8/PP1/DP1 | ISL8192/OSL256/C128/ctx16384 | simulated_scan_only | 否 | — | — | 1213.677 | 扫描器输出是稳态单点（均值口径），不含 P99；不能用于 SLA 判定 |
| `0ba4e25473196278` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP8/PP1/DP1 | ISL8192/OSL256/C144/ctx8192 | simulated_scan_only | 否 | — | — | 1231.57 | 扫描器输出是稳态单点（均值口径），不含 P99；不能用于 SLA 判定 |
| `a7141a51713b8e3a` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP8/PP1/DP1 | ISL8192/OSL256/C144/ctx16384 | simulated_scan_only | 否 | — | — | 1223.498 | 扫描器输出是稳态单点（均值口径），不含 P99；不能用于 SLA 判定 |
| `4aa43b412185c81d` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP8/PP1/DP1 | ISL8192/OSL256/C160/ctx8192 | simulated_scan_only | 否 | — | — | 1238.218 | 扫描器输出是稳态单点（均值口径），不含 P99；不能用于 SLA 判定 |
| `47e9255b5bd07e81` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP8/PP1/DP1 | ISL8192/OSL256/C176/ctx8192 | simulated_scan_only | 否 | — | — | 1243.126 | 扫描器输出是稳态单点（均值口径），不含 P99；不能用于 SLA 判定 |
| `557e3994c4c1f523` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP8/PP1/DP1 | ISL8192/OSL256/C192/ctx8192 | simulated_scan_only | 否 | — | — | 1248.132 | 扫描器输出是稳态单点（均值口径），不含 P99；不能用于 SLA 判定 |
| `76a177c2a4523192` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP8/PP1/DP1 | ISL8192/OSL256/C192/ctx16384 | simulated_scan_only | 否 | — | — | 1243.8 | 扫描器输出是稳态单点（均值口径），不含 P99；不能用于 SLA 判定 |
| `7aba4c8285946e4f` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP8/PP1/DP1 | ISL8192/OSL256/C208/ctx8192 | simulated_scan_only | 否 | — | — | 1251.559 | 扫描器输出是稳态单点（均值口径），不含 P99；不能用于 SLA 判定 |
| `69f9e2b75d50f496` | Qwen/Qwen3-32B / bf16 | nvidia-h100-sxm-80gb / TP8/PP1/DP1 | ISL8192/OSL256/C224/ctx8192 | simulated_scan_only | 否 | — | — | 1254.757 | 扫描器输出是稳态单点（均值口径），不含 P99；不能用于 SLA 判定 |
| `7d835ec64ce26cae` | Qwen/Qwen3-32B / bf16 | nvidia-h200-sxm-141gb / TP2/PP1/DP1 | ISL1024/OSL1024/C8 | simulated_native_p99 | 是 | 440.7024270000402 | 11.05583574389083 | 691.7932401874034 | — |
| `6533026651696ef8` | Qwen/Qwen3-32B / bf16 | nvidia-h200-sxm-141gb / TP2/PP1/DP1 | ISL8192/OSL256/C8 | simulated_native_p99 | 是 | 1995.562819 | 26.646441819607944 | 279.3985178058005 | — |
| `7dc6cec4f1edfbba` | Qwen/Qwen3-32B / bf16 | nvidia-h200-sxm-141gb / TP4/PP1/DP2 | ISL1024/OSL1024/C8 | simulated_native_p99 | 是 | 132.70885400001134 | 7.0779355053764395 | 1102.6959193454597 | — |
| `7ea882c140b35cc6` | Qwen/Qwen3-32B / bf16 | nvidia-h200-sxm-141gb / TP4/PP1/DP2 | ISL8192/OSL256/C8 | simulated_native_p99 | 是 | 878.576915 | 10.833958360784477 | 664.772924145956 | — |
| `8ed4e697c7d2ccc6` | Qwen/Qwen3-32B / bf16 | nvidia-h200-sxm-141gb / TP2/PP1/DP1 | ISL1024/OSL1024/C32 | simulated_native_p99 | 是 | 1765.553723 | 14.477458347995988 | 2149.7672006351536 | — |
| `5947c75deb4c06e5` | Qwen/Qwen3-32B / bf16 | nvidia-h200-sxm-141gb / TP2/PP1/DP1 | ISL8192/OSL256/C32 | simulated_native_p99 | 是 | 14356.537065000002 | 79.91176538039238 | 390.35883497438374 | — |
| `905688e90287fb92` | Qwen/Qwen3-32B / bf16 | nvidia-h200-sxm-141gb / TP4/PP1/DP1 | ISL8192/OSL256/C32 | simulated_native_p99 | 是 | 8481.037019000001 | 47.49475550196087 | 656.4300876984471 | — |
| `8a2e000d4352cf97` | Qwen/Qwen3-32B / bf16 | nvidia-h200-sxm-141gb / TP8/PP1/DP1 | ISL8192/OSL256/C32 | simulated_native_p99 | 是 | 4955.538856 | 28.770253968627422 | 1082.997232236789 | — |
| `bcf8719445835a07` | Qwen/Qwen3-32B / bf16 | nvidia-h200-sxm-141gb / TP4/PP1/DP2 | ISL1024/OSL1024/C32 | simulated_native_p99 | 是 | 532.0884139999998 | 8.038959994134894 | 3873.420667483136 | — |
| `68144bc7d13cdcb3` | Qwen/Qwen3-32B / bf16 | nvidia-h200-sxm-141gb / TP4/PP1/DP2 | ISL8192/OSL256/C32 | simulated_native_p99 | 是 | 4521.6287919999995 | 26.8434794 | 1138.1864434887902 | — |
| `d1981ddbd0b0dd8a` | Qwen/Qwen3-32B / bf16 | nvidia-h200-sxm-141gb / TP2/PP1/DP1 | ISL1024/OSL1024/C64 | simulated_native_p99 | 是 | 3533.6141140000004 | 18.36405357673509 | 3407.386670638768 | — |
| `b8da731719768978` | Qwen/Qwen3-32B / bf16 | nvidia-h200-sxm-141gb / TP2/PP1/DP1 | ISL8192/OSL256/C64 | simulated_native_p99 | 是 | 30866.989649000003 | 148.80633225882306 | 423.32901395617455 | — |
| `08f96a3d63b1d5a3` | Qwen/Qwen3-32B / bf16 | nvidia-h200-sxm-141gb / TP4/PP1/DP2 | ISL1024/OSL1024/C64 | simulated_native_p99 | 是 | 1065.5455469999997 | 9.39976863734117 | 6648.3700709741215 | — |
| `c9743f6dfc7569de` | Qwen/Qwen3-32B / bf16 | nvidia-h200-sxm-141gb / TP4/PP1/DP2 | ISL8192/OSL256/C64 | simulated_native_p99 | 是 | 9395.242326 | 47.49475550196081 | 1311.2457649568023 | — |
| `a5caa5feb133f150` | Qwen/Qwen3-32B / bf16 | nvidia-h200-sxm-141gb / TP4/PP1/DP2 | ISL8192/OSL256/C96 | simulated_native_p99 | 是 | 14265.872607999992 | 67.54073382745081 | 1376.624854092987 | — |
| `5661ebbfd82dc9b7` | Qwen/Qwen3-32B / bf16 | nvidia-h200-sxm-141gb / TP4/PP1/DP2 | ISL8192/OSL256/C128 | simulated_native_p99 | 是 | 19121.870338999986 | 87.29699354117625 | 1436.3384727706505 | — |
| `45a1bc47d3995835` | Qwen/Qwen3-8B / bf16 | nvidia-h100-sxm-80gb / TP1/PP1/DP1 | ISL1024/OSL1024/C8 | simulated_native_p99 | 是 | 185.48182300000917 | 7.230641140762756 | 1073.1434253976 | — |
| `ca2f988b290c90f3` | Qwen/Qwen3-8B / bf16 | nvidia-h100-sxm-80gb / TP1/PP1/DP1 | ISL8192/OSL256/C8 | simulated_native_p99 | 是 | 887.919842 | 16.005433647058844 | 473.4381502095157 | — |
| `4bb61202840da82b` | Qwen/Qwen3-8B / bf16 | nvidia-h100-sxm-80gb / TP1/PP1/DP2 | ISL1024/OSL1024/C8 | simulated_native_p99 | 是 | 93.09983800002374 | 6.744311719452742 | 1162.361629272693 | — |
| `7e76434283f7492d` | Qwen/Qwen3-8B / bf16 | nvidia-h100-sxm-80gb / TP1/PP1/DP2 | ISL8192/OSL256/C8 | simulated_native_p99 | 是 | 653.9557169999999 | 11.053877941176625 | 669.3956053210023 | — |
| `7bdceae665347a52` | Qwen/Qwen3-8B / bf16 | nvidia-h100-sxm-80gb / TP1/PP1/DP1 | ISL1024/OSL1024/C32 | simulated_native_p99 | 是 | 746.227258 | 10.10754084652979 | 3096.7872683153632 | — |
| `2392acc25dd22d55` | Qwen/Qwen3-8B / bf16 | nvidia-h100-sxm-80gb / TP1/PP1/DP1 | ISL8192/OSL256/C32 | simulated_native_p99 | 是 | 6609.393869999999 | 46.599841372549015 | 671.6197848398688 | — |
| `ed48a483e5f940b5` | Qwen/Qwen3-8B / bf16 | nvidia-h100-sxm-80gb / TP1/PP1/DP2 | ISL1024/OSL1024/C32 | simulated_native_p99 | 是 | 371.39593 | 8.608299143695115 | 3643.8625365988637 | — |
| `a6b5a728299c49c5` | Qwen/Qwen3-8B / bf16 | nvidia-h100-sxm-80gb / TP1/PP1/DP2 | ISL8192/OSL256/C32 | simulated_native_p99 | 是 | 3486.4293139999995 | 27.420675007843208 | 1127.7751784411603 | — |
| `1425b6eb8a6d8a6b` | Qwen/Qwen3-8B / bf16 | nvidia-h100-sxm-80gb / TP1/PP1/DP4 | ISL8192/OSL256/C32 | simulated_native_p99 | 是 | 1827.7921460000002 | 16.0054336470589 | 1880.7211353088285 | — |
| `c66f2830b97c8b44` | Qwen/Qwen3-8B / bf16 | nvidia-h100-sxm-80gb / TP1/PP1/DP1 | ISL1024/OSL1024/C64 | simulated_native_p99 | 是 | 1499.3555680000002 | 13.52109428739 | 4657.798733662274 | — |
| `f7875715d5db22e2` | Qwen/Qwen3-8B / bf16 | nvidia-h100-sxm-80gb / TP1/PP1/DP1 | ISL8192/OSL256/C64 | simulated_native_p99 | 是 | 19798.39472699997 | 76.13251087843177 | 700.192047763515 | — |
| `623225fc49fecf77` | Qwen/Qwen3-8B / bf16 | nvidia-h100-sxm-80gb / TP1/PP1/DP2 | ISL1024/OSL1024/C64 | simulated_native_p99 | 是 | 746.227258 | 10.129031437927686 | 6191.125539745652 | — |
| `b1b538641ff63bc0` | Qwen/Qwen3-8B / bf16 | nvidia-h100-sxm-80gb / TP1/PP1/DP2 | ISL8192/OSL256/C64 | simulated_native_p99 | 是 | 7334.551824999999 | 46.599841372549015 | 1341.269828068544 | — |
| `889c47dcdda72972` | Qwen/Qwen3-8B / bf16 | nvidia-h100-sxm-80gb / TP1/PP1/DP2 | ISL8192/OSL256/C96 | simulated_native_p99 | 是 | 11034.099922 | 76.13560087843159 | 1394.4902625469197 | — |
| `9ff303ae72a73e83` | Qwen/Qwen3-8B / bf16 | nvidia-h100-sxm-80gb / TP1/PP1/DP2 | ISL8192/OSL256/C128 | simulated_native_p99 | 是 | 20535.195289999967 | 76.13560087843159 | 1394.4902625469197 | — |
| `1540d30716199781` | Qwen/Qwen3-8B / bf16 | nvidia-h200-sxm-141gb / TP1/PP1/DP1 | ISL1024/OSL1024/C8 | simulated_native_p99 | 是 | 181.30924300000333 | 5.540693851417527 | 1390.5781678719216 | — |
| `6acf9efafd77e298` | Qwen/Qwen3-8B / bf16 | nvidia-h200-sxm-141gb / TP1/PP1/DP1 | ISL8192/OSL256/C8 | simulated_native_p99 | 是 | 859.7394019999999 | 13.592190360784526 | 553.5143888489443 | — |
| `d482b70c175c1a49` | Qwen/Qwen3-8B / bf16 | nvidia-h200-sxm-141gb / TP1/PP1/DP2 | ISL1024/OSL1024/C8 | simulated_native_p99 | 是 | 91.21148900000844 | 5.313530915933562 | 1471.2269209774552 | — |
| `14813f1269cffd22` | Qwen/Qwen3-8B / bf16 | nvidia-h200-sxm-141gb / TP1/PP1/DP2 | ISL8192/OSL256/C8 | simulated_native_p99 | 是 | 636.6716029999999 | 9.002842921569062 | 810.6443773058669 | — |
| `96cdf13ad16f59ab` | Qwen/Qwen3-8B / bf16 | nvidia-h200-sxm-141gb / TP1/PP1/DP1 | ISL1024/OSL1024/C32 | simulated_native_p99 | 是 | 728.1516899999998 | 7.712977738025394 | 4049.0165794668155 | — |
| `4d5c405ac871085a` | Qwen/Qwen3-8B / bf16 | nvidia-h200-sxm-141gb / TP1/PP1/DP1 | ISL8192/OSL256/C32 | simulated_native_p99 | 是 | 6286.380569000001 | 40.78204896862738 | 767.0359516128434 | — |
| `e527b4173a9e4888` | Qwen/Qwen3-8B / bf16 | nvidia-h200-sxm-141gb / TP1/PP1/DP2 | ISL1024/OSL1024/C32 | simulated_native_p99 | 是 | 362.8524709999999 | 6.498827639296478 | 4811.783329426065 | — |
| `8d97b78854c4454b` | Qwen/Qwen3-8B / bf16 | nvidia-h200-sxm-141gb / TP1/PP1/DP2 | ISL8192/OSL256/C32 | simulated_native_p99 | 是 | 3330.3272680000005 | 22.918945984313847 | 1341.9938095420605 | — |
| `c112e00d57a57c5c` | Qwen/Qwen3-8B / bf16 | nvidia-h200-sxm-141gb / TP1/PP1/DP1 | ISL1024/OSL1024/C64 | simulated_native_p99 | 是 | 1460.2410519999996 | 10.414255296187664 | 6039.994042071066 | — |
| `9663566f83694d47` | Qwen/Qwen3-8B / bf16 | nvidia-h200-sxm-141gb / TP1/PP1/DP1 | ISL8192/OSL256/C64 | simulated_native_p99 | 是 | 13681.380651000005 | 75.51767865882333 | 834.6444646073313 | — |
| `66489ae15c6ed5b1` | Qwen/Qwen3-8B / bf16 | nvidia-h200-sxm-141gb / TP1/PP1/DP2 | ISL1024/OSL1024/C64 | simulated_native_p99 | 是 | 728.1516899999998 | 7.735301954056702 | 8094.894604956275 | — |
| `922de28d3e5d88b5` | Qwen/Qwen3-8B / bf16 | nvidia-h200-sxm-141gb / TP1/PP1/DP2 | ISL8192/OSL256/C64 | simulated_native_p99 | 是 | 6972.955788000001 | 40.78204896862752 | 1532.2982314387943 | — |

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

- 证据总数：275
- 按级别：static_only=0；simulated_scan_only=159；simulated_native_p99=66；measured_benchmark=0
- 工作点总数：129
- SLA 可用工作点：61
- 仅参考记录：50
- 阻塞的归档运行：0
- 阻塞的候选回放：0（同一运行内其它成功候选仍可用）

已知缺口：
- 没有任何证据达到 4 级（目标配置的真实 Benchmark）
- 没有目标配置的真实 Benchmark：所有实测型记录都缺身份或属于参考数据

## 6. 结论边界

1. 当前有 61 个可比较的原生 P99 工作点：
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
   - Qwen/Qwen3-32B / nvidia-h200-sxm-141gb / bf16 / vllm 0.24.0 / TP2×PP1×DP1 / C64 / ISL1024 / OSL1024 → TTFT P99 3533.6141140000004 ms、TPOT P99 18.36405357673509 ms、吞吐 3407.386670638768 tok/s
   - Qwen/Qwen3-32B / nvidia-h200-sxm-141gb / bf16 / vllm 0.24.0 / TP2×PP1×DP1 / C64 / ISL8192 / OSL256 → TTFT P99 30866.989649000003 ms、TPOT P99 148.80633225882306 ms、吞吐 423.32901395617455 tok/s
   - Qwen/Qwen3-32B / nvidia-h200-sxm-141gb / bf16 / vllm 0.24.0 / TP4×PP1×DP2 / C64 / ISL1024 / OSL1024 → TTFT P99 1065.5455469999997 ms、TPOT P99 9.39976863734117 ms、吞吐 6648.3700709741215 tok/s
   - Qwen/Qwen3-32B / nvidia-h200-sxm-141gb / bf16 / vllm 0.24.0 / TP4×PP1×DP2 / C64 / ISL8192 / OSL256 → TTFT P99 9395.242326 ms、TPOT P99 47.49475550196081 ms、吞吐 1311.2457649568023 tok/s
   - Qwen/Qwen3-32B / nvidia-h200-sxm-141gb / bf16 / vllm 0.24.0 / TP4×PP1×DP2 / C96 / ISL8192 / OSL256 → TTFT P99 14265.872607999992 ms、TPOT P99 67.54073382745081 ms、吞吐 1376.624854092987 tok/s
   - Qwen/Qwen3-32B / nvidia-h200-sxm-141gb / bf16 / vllm 0.24.0 / TP4×PP1×DP2 / C128 / ISL8192 / OSL256 → TTFT P99 19121.870338999986 ms、TPOT P99 87.29699354117625 ms、吞吐 1436.3384727706505 tok/s
   - Qwen/Qwen3-8B / nvidia-h100-sxm-80gb / bf16 / vllm 0.24.0 / TP1×PP1×DP1 / C8 / ISL1024 / OSL1024 → TTFT P99 185.48182300000917 ms、TPOT P99 7.230641140762756 ms、吞吐 1073.1434253976 tok/s
   - Qwen/Qwen3-8B / nvidia-h100-sxm-80gb / bf16 / vllm 0.24.0 / TP1×PP1×DP1 / C8 / ISL8192 / OSL256 → TTFT P99 887.919842 ms、TPOT P99 16.005433647058844 ms、吞吐 473.4381502095157 tok/s
   - Qwen/Qwen3-8B / nvidia-h100-sxm-80gb / bf16 / vllm 0.24.0 / TP1×PP1×DP2 / C8 / ISL1024 / OSL1024 → TTFT P99 93.09983800002374 ms、TPOT P99 6.744311719452742 ms、吞吐 1162.361629272693 tok/s
   - Qwen/Qwen3-8B / nvidia-h100-sxm-80gb / bf16 / vllm 0.24.0 / TP1×PP1×DP2 / C8 / ISL8192 / OSL256 → TTFT P99 653.9557169999999 ms、TPOT P99 11.053877941176625 ms、吞吐 669.3956053210023 tok/s
   - Qwen/Qwen3-8B / nvidia-h100-sxm-80gb / bf16 / vllm 0.24.0 / TP1×PP1×DP1 / C32 / ISL1024 / OSL1024 → TTFT P99 746.227258 ms、TPOT P99 10.10754084652979 ms、吞吐 3096.7872683153632 tok/s
   - Qwen/Qwen3-8B / nvidia-h100-sxm-80gb / bf16 / vllm 0.24.0 / TP1×PP1×DP1 / C32 / ISL8192 / OSL256 → TTFT P99 6609.393869999999 ms、TPOT P99 46.599841372549015 ms、吞吐 671.6197848398688 tok/s
   - Qwen/Qwen3-8B / nvidia-h100-sxm-80gb / bf16 / vllm 0.24.0 / TP1×PP1×DP2 / C32 / ISL1024 / OSL1024 → TTFT P99 371.39593 ms、TPOT P99 8.608299143695115 ms、吞吐 3643.8625365988637 tok/s
   - Qwen/Qwen3-8B / nvidia-h100-sxm-80gb / bf16 / vllm 0.24.0 / TP1×PP1×DP2 / C32 / ISL8192 / OSL256 → TTFT P99 3486.4293139999995 ms、TPOT P99 27.420675007843208 ms、吞吐 1127.7751784411603 tok/s
   - Qwen/Qwen3-8B / nvidia-h100-sxm-80gb / bf16 / vllm 0.24.0 / TP1×PP1×DP4 / C32 / ISL8192 / OSL256 → TTFT P99 1827.7921460000002 ms、TPOT P99 16.0054336470589 ms、吞吐 1880.7211353088285 tok/s
   - Qwen/Qwen3-8B / nvidia-h100-sxm-80gb / bf16 / vllm 0.24.0 / TP1×PP1×DP1 / C64 / ISL1024 / OSL1024 → TTFT P99 1499.3555680000002 ms、TPOT P99 13.52109428739 ms、吞吐 4657.798733662274 tok/s
   - Qwen/Qwen3-8B / nvidia-h100-sxm-80gb / bf16 / vllm 0.24.0 / TP1×PP1×DP1 / C64 / ISL8192 / OSL256 → TTFT P99 19798.39472699997 ms、TPOT P99 76.13251087843177 ms、吞吐 700.192047763515 tok/s
   - Qwen/Qwen3-8B / nvidia-h100-sxm-80gb / bf16 / vllm 0.24.0 / TP1×PP1×DP2 / C64 / ISL1024 / OSL1024 → TTFT P99 746.227258 ms、TPOT P99 10.129031437927686 ms、吞吐 6191.125539745652 tok/s
   - Qwen/Qwen3-8B / nvidia-h100-sxm-80gb / bf16 / vllm 0.24.0 / TP1×PP1×DP2 / C64 / ISL8192 / OSL256 → TTFT P99 7334.551824999999 ms、TPOT P99 46.599841372549015 ms、吞吐 1341.269828068544 tok/s
   - Qwen/Qwen3-8B / nvidia-h100-sxm-80gb / bf16 / vllm 0.24.0 / TP1×PP1×DP2 / C96 / ISL8192 / OSL256 → TTFT P99 11034.099922 ms、TPOT P99 76.13560087843159 ms、吞吐 1394.4902625469197 tok/s
   - Qwen/Qwen3-8B / nvidia-h100-sxm-80gb / bf16 / vllm 0.24.0 / TP1×PP1×DP2 / C128 / ISL8192 / OSL256 → TTFT P99 20535.195289999967 ms、TPOT P99 76.13560087843159 ms、吞吐 1394.4902625469197 tok/s
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
