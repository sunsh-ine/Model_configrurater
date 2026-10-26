# AH6 真实 GPU 验证接口（条件任务）

基线：`01a1fb3`

> **未执行。** 无 H100/H200 授权窗口。AH6 只定义接口与门禁，不产生实测结论。

## 1. 接口

真实执行应写入 AH2 的受控记录，再走 `perf/benchmark_ingest.py`：

1. 冻结 `predict` 配置与实验清单（AH1 预注册）。
2. 受控执行器生成原始逐请求 JSONL，记录显式测量窗口。
3. 生成 `source_attestation`（原始/身份/run manifest/环境哈希）。
4. 调用 `run_import_pipeline(..., registry=...)`。
5. 只有 `verified_source_kind=internal_measurement` 且 `calibration_usable=True`
   才形成真实误差；否则保持 unknown。

## 2. 最低首轮对照

* Qwen3-8B / H100，TP1×DP1 与 TP1×DP2；
* 相同 ISL/OSL、到达方式、客户端采样口径；
* 每点至少 5 次独立运行；
* 保存预热、正式窗口、失败/取消请求、驱动/CUDA、服务端启动参数。

## 3. 门禁

无授权时不得启动真机；不得用其他 GPU 结果替代 H100/H200；
不得把合成夹具或未登记上传升级为实测；不得恢复生产 SLA 与正式报价。
