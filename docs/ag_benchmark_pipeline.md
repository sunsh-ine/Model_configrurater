# AG4 Benchmark 导入与校验闭环

基线：`24ac68f`

> 本阶段**不执行真实 GPU Benchmark**。夹具只验证导入与误差计算链路；
> `measured_benchmark_runs` 仍为 0。

## 1. 流程

实现：`src/llm_capacity_pricing/perf/benchmark_ingest.py`

```
冻结配置 identity
  -> 原始逐请求日志（OpenAI 兼容流式客户端）
  -> 独立重算 P99 / 吞吐 / 成功失败取消计数
  -> 校验身份哈希 + 原始文件 SHA256 + 指标口径
  -> 适配器导入（BenchmarkRecord / Evidence）
  -> 与导入摘要交叉核验
  -> 可选入库
  -> 与预测对比（夹具只标 fixture_only）
```

## 2. 客户端统计口径

夹具元数据显式记录：

| 事件 | 定义 |
|---|---|
| 开始 | `request_sent` |
| 首 token | 首个带内容的流式 chunk |
| 末 token | stream end |
| TPOT | `(last_token - first_token)/(output_tokens-1)`；不是 ITL 池 |

失败与取消请求单独计数；只有成功且有输出长度的请求参与时延统计。

## 3. 强制校验

导入被拒绝（`accepted=False`）的条件：

* 冻结身份哈希与当前配置不一致（运行后改配置）；
* 原始文件不存在；
* 缺少原始文件 SHA256 声明；
* 实际 SHA256 与声明不一致；
* 三个指标口径未声明标准 ID（`m.latency.ttft.p99`、
  `m.latency.tpot.p99`、`m.throughput.cluster.output`）；
* 独立重算的 P99/吞吐与适配器导入摘要不一致；
* 成功请求数不一致；
* 适配器导入失败或产出记录数不为 1。

`synthetic_fixture` 可以走通管线，但校准状态只能是 `fixture_only`，
`calibration_usable=False`。

## 4. 夹具

* `data/examples/benchmark_pipeline/requests.jsonl`：96 成功 + 3 失败 + 1 取消；
* `data/examples/benchmark_pipeline/metadata.yaml`：冻结身份、原始哈希、
  客户端事件定义与指标口径。

回归：`tests/test_ag_benchmark_pipeline.py`（8 项，含篡改、缺哈希、身份变更、
口径错误与 DB 入库）。

## 5. 与真实执行的关系

真实执行仍需 AG5 的授权窗口。没有真实日志时：

* 不生成 `observed_*`；
* 不计算真实预测误差；
* 不训练全局修正系数；
* `calibration_status=unknown`。
