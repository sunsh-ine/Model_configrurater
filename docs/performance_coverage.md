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
| qwen3-8b | NVIDIA H100 SXM | static_only | 0 | 是 |
| qwen3-8b | NVIDIA H200 SXM | static_only | 0 | 否 |
| qwen3-32b | NVIDIA H100 SXM | simulated_native_p99 | 176 | 是 |
| qwen3-32b | NVIDIA H200 SXM | static_only | 0 | 否 |

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

- 证据总数：226
- 按级别：static_only=0；simulated_scan_only=159；simulated_native_p99=17；measured_benchmark=0
- 工作点总数：80
- SLA 可用工作点：12
- 仅参考记录：50
- 阻塞的归档运行：0
- 阻塞的候选回放：0（同一运行内其它成功候选仍可用）

已知缺口：
- qwen3-8b × NVIDIA H100 SXM 只有静态规划，没有该工作点的性能证据
- qwen3-8b × NVIDIA H200 SXM 只有静态规划，没有该工作点的性能证据
- qwen3-32b × NVIDIA H200 SXM 只有静态规划，没有该工作点的性能证据
- 没有任何证据达到 4 级（目标配置的真实 Benchmark）
- 没有目标配置的真实 Benchmark：所有实测型记录都缺身份或属于参考数据

## 6. 结论边界

1. 当前有 12 个可比较的原生 P99 工作点：
   - Qwen/Qwen3-32B / nvidia-h100-sxm-80gb / bf16 / vllm 0.24.0 / TP4×PP1×DP1 / C8 / ISL1024 / OSL1024 → TTFT P99 260.4617910000088 ms、TPOT P99 8.564802355816468 ms、吞吐 900.8776991343514 tok/s
   - Qwen/Qwen3-32B / nvidia-h100-sxm-80gb / bf16 / vllm 0.24.0 / TP4×PP1×DP1 / C8 / ISL8192 / OSL256 → TTFT P99 1159.756941 ms、TPOT P99 17.848578929412408 ms、吞吐 420.4521057972523 tok/s
   - Qwen/Qwen3-32B / nvidia-h100-sxm-80gb / bf16 / vllm 0.24.0 / TP8×PP1×DP1 / C8 / ISL1024 / OSL1024 → TTFT P99 169.22123400000055 ms、TPOT P99 5.983962341153699 ms、吞吐 1292.8510420687026 tok/s
   - Qwen/Qwen3-32B / nvidia-h100-sxm-80gb / bf16 / vllm 0.24.0 / TP8×PP1×DP1 / C8 / ISL8192 / OSL256 → TTFT P99 727.2969240000001 ms、TPOT P99 11.230072717647314 ms、吞吐 667.5295614199107 tok/s
   - Qwen/Qwen3-32B / nvidia-h100-sxm-80gb / bf16 / vllm 0.24.0 / TP8×PP1×DP1 / C32 / ISL8192 / OSL256 → TTFT P99 5201.827252000001 ms、TPOT P99 31.45861707450993 ms、吞吐 990.888548064229 tok/s
   - Qwen/Qwen3-32B / nvidia-h100-sxm-80gb / bf16 / vllm 0.24.0 / TP4×PP1×DP1 / C32 / ISL8192 / OSL256 → TTFT P99 8359.441161 ms、TPOT P99 50.170601298039216 ms、吞吐 621.8971855351141 tok/s
   - Qwen/Qwen3-32B / nvidia-h100-sxm-80gb / bf16 / vllm 0.24.0 / TP4×PP1×DP1 / C32 / ISL1024 / OSL1024 → TTFT P99 1044.013729 ms、TPOT P99 11.304065113391985 ms、吞吐 2759.5234009630535 tok/s
   - Qwen/Qwen3-32B / nvidia-h100-sxm-80gb / bf16 / vllm 0.24.0 / TP8×PP1×DP1 / C32 / ISL1024 / OSL1024 → TTFT P99 678.928668 ms、TPOT P99 7.646308508308902 ms、吞吐 4071.268859775253 tok/s
   - Qwen/Qwen3-32B / nvidia-h100-sxm-80gb / bf16 / vllm 0.24.0 / TP4×PP1×DP1 / C64 / ISL1024 / OSL1024 → TTFT P99 2091.052369 ms、TPOT P99 14.00218116324539 ms、吞吐 4476.380464100694 tok/s
   - Qwen/Qwen3-32B / nvidia-h100-sxm-80gb / bf16 / vllm 0.24.0 / TP4×PP1×DP1 / C64 / ISL8192 / OSL256 → TTFT P99 18030.104925999993 ms、TPOT P99 91.11349433333312 ms、吞吐 691.290490942291 tok/s
   - Qwen/Qwen3-32B / nvidia-h100-sxm-80gb / bf16 / vllm 0.24.0 / TP8×PP1×DP1 / C64 / ISL1024 / OSL1024 → TTFT P99 1358.8681519999998 ms、TPOT P99 9.276825615835756 ms、吞吐 6747.2858397137925 tok/s
   - Qwen/Qwen3-32B / nvidia-h100-sxm-80gb / bf16 / vllm 0.24.0 / TP8×PP1×DP1 / C64 / ISL8192 / OSL256 → TTFT P99 11211.532603000005 ms、TPOT P99 56.43859718823514 ms、吞吐 1115.0950375870086 tok/s
2. 其余模型、硬件与工作点最多只能做静态规划；没有 P99 时报告必须
   给出缺口，不能推荐“达标”。
3. 扫描器数据只用于候选枚举与相对排序，不能拿来判 SLA。
4. 公开参考与历史 Excel 记录不参与 SLA、不参与报价门禁。
5. 成本分析只在本仓库有对应成本项时可用，且示例价格为合成值；
   “可算成本”不等于“可发布报价”。
