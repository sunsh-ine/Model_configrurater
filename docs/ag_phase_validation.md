# AG 阶段验收：实验完整性与 Benchmark 验证闭环

基线：`24ac68f`

本阶段没有执行真实 GPU Benchmark；真实性能与真实校准仍为 `unknown`。
所有模拟结论仍为 `predicted_*`，不产生 `observed_*`。

## 已通过

### AG0 AF 遗留问题

* **P99 样本门槛与重复实验门槛分离**：
  - `MIN_SAMPLES_FOR_P99 = 30` 只回答“P99 是否有最低样本支撑”；
  - 实验清单 `required_completed_requests = 500` 回答“重复实验的一次运行是否完整”。
* 多 seed 主推荐、容量扫描、到达率扫描都要求每个有效运行有 ≥500 完成请求；
  不足或未知保持 `unknown` / `incomplete`。
* `queue_wait_ms` 缺失不再默认 0：`queue_stability_report.py` 与
  `load_analysis.py` 都输出 unknown，并记录可用/缺失请求数。
* 覆盖矩阵已重新生成：**309 条证据**，其中 **100 次原生模拟运行**、
  **79 个唯一 SLA 可用工作点**、0 条真实实测。多 seed 增加运行数，不增加独立工作点数。
* 最终验证日志：`docs/test_run_log.txt` 与 `docs/ag_verification_summary.json`。

### AG1 实验清单

* 新增 `schemas/experiment_manifest.py`、`perf/experiment_audit.py`、
  `scripts/build_experiment_manifest.py`。
* 每组冻结：部署身份、负载身份、预期 seed、每 run 完成请求数、
  配置/归档 SHA256、运行状态。
* AF 三组清单在 `data/simulation/experiments/`；`seed42` 作为已有归档被
  **显式**列入 `out_of_scope_seeds`，不是静默丢弃。
* 清单校验：seed 唯一且非空、runs 必须属于预期 seed、固定并发/开放到达/
  trace 必填字段、各 run 配置哈希一致。

### AG2 两层状态

* 实验完整性：`complete` / `incomplete` / `invalid` / `excluded`。
* 模拟 SLA 结论：`all_pass` / `all_fail` / `mixed` / `unknown`。
* 只有完整性 `complete` 才给完整 SLA 分类；缺失预期 seed、失败/取消、
  哈希或配置不一致、同 seed 冲突、无 seed 运行都阻止 complete。
* `mixed`、`incomplete`、`invalid`、`excluded` 不进入正式推荐池。
* 主推荐、容量扫描、到达率扫描共用同一清单与聚合规则。
* 当前结果：

| 实验 | 预期/发现/有效 | 结论 |
|---|---|---|
| H100 DP2 4 req/s | 5/5/5 | all_pass |
| H100 DP2 5 req/s | 5/5/5 | all_fail |
| H200 DP2 5 req/s | 5/5/5 | mixed（3/5） |

### AG3 有限窗口负载分析

* `scripts/load_analysis.py` 生成 `docs/ag_load_analysis.md/json`。
* 指标来源表：TTFT、TPOT、E2E、路由 queue_wait、scheduler 等待、全局队列；
  后两者明确 unavailable。
* 全窗口、前半/后半、首/尾 100 请求分段；只有全部计数请求都有到达/末 token
  时才计算吞吐与 RPS，否则 unknown。
* 结论仍限定为有限窗口，不声称长期稳定或最大稳定 RPS。

### AG4 Benchmark 导入闭环

* 新增 `perf/benchmark_ingest.py`，流程：
  冻结身份 → 原始 JSONL → 独立重算 → 身份/哈希/口径校验 →
  适配器导入 → 重算与导入交叉核验 → 可选 DB 入库 → 可选预测对比。
* 夹具：96 成功 + 3 失败 + 1 取消，显式 `synthetic_fixture`。
* 拒绝：身份哈希缺失/不符、原始哈希缺失/不符、口径非标准、重算与导入不一致、
  成功数/请求总数不一致、日志无法解析、DB 未写入记录。
* 回归：`tests/test_ag_benchmark_pipeline.py`（10 项）。
* 文档：`docs/ag_benchmark_pipeline.md`。

### AG6 误差与校准状态

* `docs/ag_calibration_report.md/json`：
  - 真实 Benchmark：0，`calibration_status=unknown`；
  - 夹具：身份匹配、误差可算，但 `fixture_only`，不可用于校准；
  - 模拟不确定度与实测误差分开呈现。
* 无匹配实测时不计算真实误差、不训练全局修正系数。

### AG7 报告集成

* 候选主表与重复性表新增：证据类型、实验完整性（预期/发现/有效）、
  校准状态（usable / fixture_only / unknown）。
* 证据分层新增完整/不完整计数、匹配实测校准数量。
* UI 模拟候选表同步展示实验完整性与校准状态。

### AG8 回归

* 全量 pytest：**1157 passed**。
* `scripts/verify_all.sh`（live AISimulate 0.12.1）：**66/66 通过**。
* 自动搜索运行目录：`data/exports/auto_sim_check/auto_sim/runs/run_20261026T094125Z_d5f734`。
* 系统 Demo 运行目录：`data/exports/system_demo/20261026T094134Z_fa3e9f`。
* 最终机器可读摘要：`docs/ag_verification_summary.json`。

## 已修复

审核（codex + OMP DeepSeek v4.1）发现并已修复：

* 扫描器未用 manifest 校验归档/配置哈希；
* 清单外 seed 被静默丢弃；
* 同一 seed 的归档/配置哈希与清单记录不一致仍可 complete；
* 无 seed 运行继承工作点完整性；
* 同一清单匹配多个工作点；
* 实验完整性未校验工具版本；
* 提交流程中 p99、失败/取消、缺失时序口径不一致；
* 吞吐窗口混用到达/非到达请求导致高估；
* 入库失败仍标记 accepted；
* 缺失 queue_wait 渲染崩溃与默认 0。

## 未确认

* 真实 GPU 性能、真实预测误差和真实 SLA 达标概率：无实测样本。
* 长期队列稳定性：仅有限请求窗口，无全局队列采样。
* `mixed` 工作点的真实达标概率：5 次模拟运行不是统计显著估计。

## 未实现

* AG5 真实 GPU 执行：无授权窗口，仅计划文档。
* 全局队列长度/等待请求数时间序列采集。
* 生产级 SLA 承诺与正式报价自动发布仍被门禁禁止。
