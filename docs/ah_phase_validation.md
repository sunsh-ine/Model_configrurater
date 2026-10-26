# AH 阶段验收：可信证据闭环与客户方案交付

基线：`01a1fb3`

## 已通过

### AH0 AG 遗留整改

* `declared_source_kind` 与 `verified_source_kind` 分离；自报
  `internal_measurement` 无登记记录时降级为 `unverified_upload`。
* 现有 AF 清单全部标记 `manifest_status=reconstructed`；新增预注册路径。
* 每个 run 同时登记 `exp_config.yaml` 与 `predict.yaml` 的实际 SHA256。
* Benchmark 重算输出每指标样本数、覆盖率与完整性；测量窗口需覆盖全部成功请求。
* 新增正向/反向测试：来源篡改、缺失冻结哈希、配置变化、指标缺失、窗口越界。

### AH1 预注册与执行契约

* `ExperimentManifest` 增加 `manifest_status`、`execution_status`、
  `preregistered_at`、`base_config`/哈希、`allowed_variation_fields`。
* 运行前冻结基准配置；执行后补全每个 seed 的配置/结果/哈希。
* 去掉允许变化字段后的标准化配置与基准不一致 → run `invalid`。
* CLI：`scripts/preregister_experiment.py`、`scripts/finalize_experiment.py`。

### AH2 来源认证与导入

* 受控执行登记表 `data/benchmark/source_attestations.json`（当前为空）。
* 原始/身份/配置/环境/指标口径摘要分别记录。
* 成功/失败/取消计数与指标覆盖；显式窗口与观测窗口区分。
* 不支持格式、DB 未写入、哈希/身份/口径不符均拒绝或保持 unknown。
* 合成夹具仍只产生 `fixture_only`。

### AH3/AH5 决策证据

* `PlanResult.decision_evidence` 统一结构，CLI/JSON/Markdown/UI 共用。
* 区分严格推荐、未达标参考、最少 GPU、吞吐最优。
* 候选展示 P99、SLA、实验完整性、重复性、校准、成本与治理状态。
* `tests/test_ah_scenarios.py` 验证三场景结构一致、模拟达标不解锁报价。

### AH4 三组客户场景

输出 `docs/ah_scenarios/`：

| 场景 | 结论 |
|---|---|
| A 同 8 卡 TP8×DP1 vs TP4×DP2 | 推荐 TP4×DP2（模拟） |
| B 8B H100 C32 DP1/2/4 | 最少 GPU = DP2；吞吐最优 = DP4 |
| C H200 Poisson 5 req/s | 5 seed 3/5 通过，mixed，无正式推荐 |

每个场景含客户版方案、内部技术报告、统一 `summary.json`。

### AH6

真实 GPU 接口与门禁文档完成；**未执行**。

### AH7

成本/报价门禁未放宽：三场景所有候选 `quote_publishable=False`；
模拟达标不解锁生产 SLA 或正式报价。

## 回归

* 全量 pytest：**1173 passed**（verify 内运行）。
* live `verify_all.sh`：**66/66 通过**。
* 自动搜索：`run_20261026T120236Z_0d8d8e`。
* 系统 Demo：`20261026T120255Z_eeacbc`。
* 机器可读摘要：`docs/ah_verification_summary.json`。

## 已修复

OMP DeepSeek 定向审核与自检发现并修复：

* 来源类型可由 metadata 自行升级；
* 历史清单可能被误认为预注册；
* 只冻结 `exp_config.yaml`、缺少逐次 `predict.yaml` 哈希；
* 成功请求指标缺失时无覆盖率；
* 测量窗口与成功请求窗口混淆；
* 决策证据只在报告层重复计算，未形成统一结构。

### 审核范围

OMP `deepseek/deepseek-v4.1-flash` 只读定向审核了 Benchmark 导入、来源认证、
预注册、实验审计和决策证据文件；未完成一次性全量大 diff 审核。发现的
来源自证、seedless、哈希/配置一致、窗口/样本口径、成本降级未显式等问题已修复。
登记表不是数字签名/远程证明，仍是残余限制。

## 未确认

* 真实 GPU 性能、真实预测误差、真实 SLA 达标概率：无实测。
* 来源登记表当前为空，任何 `internal_measurement` 都只能 `unverified_upload`。
* 三场景结论均为模拟预测，不是生产承诺。

## 未实现

* AH6 真机执行（无授权窗口）。
* 生产级报价自动发布（仍禁止）。
