# AF 阶段验收：统一门禁、重复性、可靠性与校准加固

基线：`030b65f`（AE 封板）

本阶段只强化**证据判定与呈现**，不新增真实 GPU 实测；所有模拟结论仍为
`predicted_*`，不会升级为 `observed_*`。

## 已通过

### AF0 统一 P99 门禁

* 新增 `src/llm_capacity_pricing/perf/sla_gate.py`，主推荐、容量扫描、
  到达率扫描共用同一判定：
  - 没有阈值 → `insufficient_prediction`；
  - 缺 P99 → `insufficient_prediction`；
  - 样本数缺失或 `< 30` → `insufficient_prediction`；
  - 阈值比较只对已配置指标生效。
* 容量/到达率扫描把“预测不足”写为 `unknown`，不再当作通过或失败。
* 测试：`tests/test_af_phase.py`（门禁 5 项）。

### AF1 多 seed / 多数据集证据

* 5 个独立 seed × 3 组（H100 r4、H100 r5、H200 r5），每组 500 完成请求。
* 归档：`data/simulation/archives/af-live-*`；报告：
  [`docs/af_reproducibility.md`](af_reproducibility.md) 与 JSON。
* 运行级结果：

| 组 | 通过 | 状态 | TTFT P99 范围 | TPOT P99 范围 |
|---|---|---|---|---|
| 8b_h100_dp2_r4 | 5/5 | all_pass | 725.11–835.19 ms | 30.41–38.83 ms |
| 8b_h100_dp2_r5 | 0/5 | all_fail | 830.91–3639.27 ms | 54.00–62.02 ms |
| 8b_h200_dp2_r5 | 3/5 | mixed | 767.23–930.57 ms | 32.30–44.96 ms |

> 运行均值不等于池化 P99；这组数据说明单次 seed 通过不能代表工作点稳定。
> 报告生成前会逐归档校验 manifest/文件哈希/预测元数据；缺 seed、缺 P99、
> 同 seed 冲突都记为 incomplete，不会用剩余运行冒充完整重复性。

### AF2 可靠性感知的推荐

* 每个候选保留 `repeatability_status/runs/pass_count` 与工作点身份。
* `mixed`（部分 seed 通过）与 `all_fail` 不再进入正式推荐池；
  拒绝原因明确写“多 seed 重复性不足：x/y 通过（mixed）”。
* 缺 P99 的运行或同一 seed 归档结论/样本窗口冲突标记为 `incomplete`：
  不当作失败，也不允许只凭一次通过的 seed 进入正式推荐。
* `all_pass` 与单次运行（无重复性声明）照常参与推荐。
* 容量/到达率扫描同样按完整部署+负载身份分组；多个配置、同一 seed 冲突
  或完成请求数不足 500 时整点保持 `unknown`，不挑一条代表。
* 测试：`tests/test_af_phase.py`（mixed 不可推荐、all_pass 可推荐），
  `tests/test_ae_workload.py`（身份字段与报告）。

### AF3 有限窗口队列与尾延迟

* 两次 `--capture-per-request` 运行各 500 请求，归档于
  `data/simulation/capabilities/per_request/`。
* 报告：[`docs/af_load_stability.md`](af_load_stability.md) / JSON。

| 组 | TTFT P99 | TPOT P99 | 队列等待 P99 | 前半 TTFT P99 | 后半 TTFT P99 |
|---|---|---|---|---|---|
| H100 DP2 r5 seed3 | 830.91 ms | 54.00 ms | 0.00 ms | 705.02 ms | 851.08 ms |
| H200 DP2 r5 seed1 | 930.57 ms | 44.96 ms | 0.00 ms | 660.26 ms | 975.97 ms |

* 能力矩阵：逐请求到达/首 token/末 token、TTFT/E2E、路由队列等待、
  终止状态可用；**全局队列长度/等待请求数时间序列不可用**。
* 明确不据此给出“最大稳定 RPS”；容量仍只认离散验证点。

### AF4 校准证据加固

* `source_file` 必须真实存在；实际内容 SHA256 必须与声明一致。
* 两侧必须声明 `ttft_p99_ms` / `tpot_p99_ms` / `output_throughput_tok_s`
  的标准口径，且与 `metric_semantics.FIELD_DEFINITIONS` 一致。
* 缺文件、哈希不符、口径缺失、口径替换（如 TPOT 用 ITL）、并发工作点不一致
  全部 `calibration_usable=False`，误差保持 unknown。
* 测试：`tests/test_ae_calibration.py`（16 项）。

### AF5 真实 GPU 条件化门禁

* 文档：[`docs/af_real_gpu_validation.md`](af_real_gpu_validation.md)。
* 当前未执行真机验证：无授权窗口，目标 H100/H200 环境不可用。

### AF6 报告统一呈现

* 报告新增/统一：
  - 负载身份（ISL/OSL、到达模式与速率、数据集、seed）；
  - 证据类型（单次模拟 / 多 seed 模拟 x/y 通过 / 预测不足 unknown）；
  - “证据分层与有效范围”（实测、模拟、unknown、搜索/归档覆盖范围）；
  - 多 seed 重复性表带完整工作点身份。
* 实测与模拟仍然分层，模拟值不进入 SLA 实测结论与报价门禁。

### AF7 回归

* 全量 pytest：**1134 passed**（verify 内运行，含 1 skipped 的可选依赖用例）。
* `scripts/verify_all.sh`（live AISimulate 0.12.1）：
  **66/66 通过**，本次自动搜索运行目录
  `data/exports/auto_sim_check/auto_sim/runs/run_20261026T070615Z_a81ff8`。

## 未确认

* 长期队列稳定性：只有 500 请求有限窗口，没有全局队列采样；
  不能证明长期稳定，也不能据此推导最大稳定 RPS。
* 真实 GPU 误差：未执行真机 Benchmark，校准契约尚无可用的真实样本。
* `mixed` 工作点的真实达标概率：5 个 seed 只能说明模拟波动，
  不是统计显著的概率估计。

## 未实现

* 全局队列长度/等待请求数时间序列采集（AISimulate 当前 `--capture-per-request`
  未提供该字段）。
* 真实 GPU 自动执行与审批流：仍需授权窗口与目标硬件。
