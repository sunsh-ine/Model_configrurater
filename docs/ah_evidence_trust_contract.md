# AH 证据可信契约

基线：`01a1fb3`

## 1. 来源声明 ≠ 已验证来源

Benchmark metadata 只能声明 `performance_source_kind`。AH 将其拆成：

| 字段 | 含义 |
|---|---|
| `declared_source_kind` | 上传者/运行者声明 |
| `verified_source_kind` | 系统依据受控记录确认的来源 |

规则（`perf/source_verification.py`）：

* `synthetic_fixture`：可自证为夹具，但永远 `calibration_status=fixture_only`；
* `internal_measurement`：必须匹配 `data/benchmark/source_attestations.json`
  中登记的受控执行记录（绑定原始 SHA256、身份 SHA256、环境、操作人、
  run manifest SHA256），否则降级为 `unverified_upload`；
* 其他声明：一律 `unverified_upload`，不能凭 metadata 字符串获得实测身份。

`unverified_upload` 在 `BenchmarkRecord` / `Evidence` 中按外部参考处理，
`data_trust=unknown`，不得进入生产 SLA 或校准。

**能力边界**：`source_attestations.json` 是仓库内维护的登记表，不是数字签名或
远程证明。能修改该文件的人可以登记新的受控记录；因此它只能证明“与登记记录一致”，
不能单独证明执行环境真实可信。生产级仍需人工审批与受控签发。

## 2. 实验清单：预注册与重建

`ExperimentManifest.manifest_status`：

* `preregistered`：运行前创建，`execution_status=planned`，冻结
  `base_config`/`base_config_sha256`、预期 seed、允许变化字段（默认仅 `seed`）；
* `reconstructed`：从历史归档反向整理，不能宣称运行前已冻结。

现有 AF 三组清单均为 `reconstructed`。新实验使用：

```bash
python scripts/preregister_experiment.py \
  --experiment-id <id> --predict-yaml <predict.yaml> --seeds 1 2 3 4 5

python scripts/finalize_experiment.py --manifest data/simulation/experiments/<id>.json
```

执行后补全：

* 每个 seed 的 `predict.yaml` 实际 SHA256；
* `exp_config.yaml` SHA256；
* 归档预测 SHA256；
* 去掉允许变化字段后的标准化配置 SHA256；
* 完成请求数与运行状态。

标准化配置与预注册基准不一致 → run `invalid`，实验不能 `complete`。

## 3. 每指标样本与测量窗口

`recompute_metrics()` 输出：

* `metric_samples`：TTFT / TPOT / E2E / success 各自样本数；
* `metric_coverage`：相对成功请求的覆盖率；
* `metric_completeness`：全部成功请求都具备三项指标才为 `complete`。

量窗口：

* 默认 `observed_success_requests`（成功请求最早到达至最晚完成）；
* 若 metadata 声明 `measurement_window_ms`，必须覆盖全部成功请求；
  否则 `window_source=declared_window_does_not_cover_samples`，吞吐保持 unknown；
* 显式窗口与适配器窗口不同，导入交叉核验会跳过吞吐比较并记录原因。

## 4. 强制边界

* 自报 `internal_measurement` 不能升级为实测；
* 历史清单不能改写成预注册；
* 缺少归档/配置/predict.yaml 哈希的 run 不算有效；
* 实验未认证或窗口不完整时，校准状态保持 unknown；
* 任何来源/配置/窗口改动都必须使可信等级下降，而不是上升。
