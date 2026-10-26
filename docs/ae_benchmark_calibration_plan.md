# AE5：真实 Benchmark 校准准备

基线：`b4dd888`

> 本阶段**不运行真机**。没有实测时误差必须为 unknown，不能写 0。
> 真实 GPU Benchmark 仍为 0，N6 继续暂停。

## 1. 元数据模板

模板：`data/examples/benchmark_calibration_metadata.example.json`。

必须记录：

* 模型 `model_id`、`model_revision`、`weight_precision`、`kv_precision`；
* `hardware_id`、`gpu_count`，以及实际 SKU/节点信息（在 `server_args` 或扩展字段）；
* `framework`、`framework_version`、`tp_size`/`dp_size`/`pp_size`；
* `arrival_mode`（`fixed_concurrency` / `poisson` / `constant_rate`）、
  `concurrency` 或 `request_rate_rps`；
* `input_len`、`output_len`、`dataset`、请求数、成功/失败数、时长；
* 服务端启动参数、客户端工具与指标定义；
* `p99_ttft_ms`、`p99_tpot_ms`、`output_throughput_tok_s`；
* 原始产物路径、SHA256、采集时间与操作人。

缺失字段保持 `null`，不得推断。

## 2. 匹配与误差规则

实现：`src/llm_capacity_pricing/perf/calibration.py`。

1. 身份完全匹配才计算误差：模型/revision、精度、GPU SKU、卡数、
   框架/版本、TP/DP/PP、ISL/OSL、到达模式；固定并发比较并发，
   Poisson/constant-rate 比较到达率。
2. 不匹配：`matched=False`，只展示差异，`errors={}`，不参与校准训练。
3. 匹配但缺预测/实测值：`calibration_usable=False`，对应误差 `None`。
4. 误差输出绝对值和相对值；相对值分母为实测值，实测为 0 时保持 unknown。
5. 模拟预测、不同 seed 的预测波动、硬件实测波动三者分开记录，
   不合并成一个“误差”。

测试：`tests/test_ae_calibration.py`。

## 3. 未来优先基准（需授权）

| 组 | 配置 | 目的 |
|---|---|---|
| 1 | Qwen3-8B / H100 / TP1×DP1、TP1×DP2 | 验证副本扩展与预测误差 |
| 2 | Qwen3-32B / H100 / TP8×DP1、TP4×DP2 | 同 8 卡 TP/DP 对照校准 |
| 3 | Qwen3-32B / H200 / TP8×DP1、TP4×DP2 | 跨硬件与 DP 校准 |

每组至少固定 ISL8192/OSL256、C32（或指定到达率）、500 请求；
记录 P99、吞吐、错误率与环境元数据。

## 4. 执行门禁

只有同时具备以下条件才可执行真实 Benchmark：

* 对应 GPU 与框架环境可独占或明确共享边界；
* 有明确的测试授权与时间窗口；
* 原始产物有 SHA256、运行目录与操作人；
* 不在生产流量上测试，不影响现有 61 服务器服务。

否则只更新模板、导入器与比较测试，真实 Benchmark 保持 0。
