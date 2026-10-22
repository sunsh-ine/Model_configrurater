# AB 阶段验收：部署决策可信度与预测覆盖

基线：`3718da2`（AA 阶段封板后的审核规则提交）
执行日期：2026-10-22

> 本阶段仍然只有 AISimulate 的**预测**，没有真实 GPU Benchmark。
> `predicted_*` 与 `observed_*` 是两套取值；模拟结果不会写入
> `BenchmarkRun` / `CapacityPoint`，也不会产生 `observed_pass`。

## 0. 结论

| 项目 | 结论 |
|---|---|
| AB0 失败隔离 / PoC 完整性 / 路径安全 | 通过 |
| AB1 证据标准化 | 通过；新归档带 `archive_version`、`workpoint`、逐预测指标 |
| AB2 工作负载覆盖 | 通过；12 个原生 P99 工作点（2 负载 × C8/C32/C64 × TP4/TP8） |
| AB3 决策稳健性 | 通过；SLA 裕量、10%/20% 敏感性、资源效率、最小卡数、证据状态 |
| AB4 候选搜索审计 | 通过；发现/通过/回放/失败/截断与有效范围均输出 |
| AB5 回归与封板 | 全量测试、verify_all、三入口一致性、Codex 审核见 §6 |
| 真实 Benchmark（第 4 级） | 仍为 0 |

## 1. AB0：失败隔离、PoC 完整性与路径安全

### 1.1 运行级 vs 候选级错误

`run_two_stage` 的 manifest 现在分开记录：

* `run_errors`：搜索未执行、搜索无输出等**运行级**错误；
* `candidate_errors`：某个候选超时/解析失败/资格异常；键包含
  `predict/predict_N.yaml`，只影响该预测；
* `errors`：兼容旧的汇总字段。

`offline_archives.archive_entries()` 和覆盖矩阵只跳过 `run_errors`，
并只跳过对应的失败 `predict_N`；同一运行内成功的预测仍可离线复用。
失败候选已登记但后来缺失/损坏的产物会被过滤掉，不会让整次运行失效。
旧 manifest 没有 `run_errors` 时保守按运行级错误处理。

反向测试：`test_failed_candidate_does_not_block_successful_archive`
（TP4 回放失败、TP8 成功，归档只读 TP8；覆盖矩阵记录 1 条阻塞候选）。

### 1.2 PoC 标准化

历史 PoC 已迁移到
`data/simulation/archives/poc-qwen3-32b-h100-tp8-c32-bf16/`：

* `predict/predict_1.yaml`、`predict/out_1/prediction.json`、
  `exp_config.yaml`、`per_ops_source.json`；
* manifest 登记全部实际使用文件的 SHA256；
* 原始预测值不变（TTFT P99 5201.827252 ms、TPOT P99 31.458617 ms）；
* 迁移脚本：`scripts/migrate_poc_archive.py`。

`_poc_entry()` 特殊加载路径已删除，PoC 与其它归档走同一套
`verify_manifest_hashes`。

### 1.3 路径安全

`verify_manifest_hashes` 拒绝绝对路径和包含 `..` 的归档路径；
实际读取的 prediction/input 必须被 manifest 登记并哈希一致。

## 2. AB1：证据标准化

新归档（`archive_version: ab-1`）包含：

* `spec`：模型、精度、system、ISL/OSL/并发、后端/版本、数据库模式；
* `workpoint`：同一身份 + TP/PP/DP/请求数；
* `predictions`：每条 `predict_N.yaml` → `out_N/prediction.json` 及
  P99/吞吐/完成请求数；
* `inputs` / `outputs`：所有实际使用文件的 SHA256；
* `run_errors` / `candidate_errors` / `not_aligned` / `not_replayed`。

迁移后的 PoC 也遵循同一格式；旧 Z/AA 归档保留原格式，仍通过哈希校验。

## 3. AB2：12 个工作点的原生 P99

固定 Qwen3-32B / H100 SXM / BF16 / vLLM 0.24.0 / 500 请求：

| 负载 | 并发 | TP4 TTFT/TPOT (ms) | TP8 TTFT/TPOT (ms) |
|---|---|---|---|
| ISL8192 / OSL256 | C8 | 1159.76 / 17.85 | 727.30 / 11.23 |
| ISL8192 / OSL256 | C32 | 8359.44 / 50.17 | 5201.83 / 31.46 |
| ISL8192 / OSL256 | C64 | 18030.10 / 91.11 | 11211.53 / 56.44 |
| ISL1024 / OSL1024 | C8 | 260.46 / 8.56 | 169.22 / 5.98 |
| ISL1024 / OSL1024 | C32 | 1044.01 / 11.30 | 678.93 / 7.65 |
| ISL1024 / OSL1024 | C64 | 2091.05 / 14.00 | 1358.87 / 9.28 |

覆盖校验：`tests/test_ab_decision_quality.py::test_ab2_covers_twelve_measured_workpoints`
对每个 (负载, TP, 并发) 组合要求存在归档；没有插值，也没有把不同工作点合并。
所有 8 个新增点均为真实 AISimulate 0.12.1 运行（SILICON，无 GPU），
原始 prediction 与 SHA256 在 `data/simulation/archives/ab-live-*`。

观察：

* 8192/256 下 TP8 的 P99 全面优于 TP4，但 C64 两者都明显恶化；
* 1024/1024 下 TP4/TP8 的绝对延迟大幅下降，C32 两者都可满足 6000/40；
* 12 个点各自独立，不能由相邻点插值推断其他并发/长度。

## 4. AB3：决策质量

`SimulationCandidate` 新增（不改变 `verdict`）：

* `sla_margin_ttft_ms` / `sla_margin_tpot_ms`：阈值 − 预测值；
* `sla_margin_*_ratio`：余量 / 阈值；
* `sensitive_10pct` / `sensitive_20pct`：预测值上调 10%/20% 是否越阈；
* `evidence_status`：`predicted_pass` / `boundary_sensitive` /
  `predicted_fail` / `insufficient_prediction`；
* `metrics.sim_tokens_per_gpu` 已有的每 GPU 吞吐用于资源效率；
* `SimulationResult.min_gpu_for_sla()`：预测达标候选中 GPU 数最少的一个。

C32 / 8192 / relaxed 示例：

| 部署 | TTFT 裕量 | TPOT 裕量 | +10% | +20% | 证据状态 |
|---|---|---|---|---|---|
| TP4 | -2359.44 ms（-39.3%） | -10.17 ms（-25.4%） | 已越阈 | 已越阈 | 预测未达标 |
| TP8 | +798.17 ms（13.3%） | +8.54 ms（21.4%） | 未越阈 | **TTFT 越阈** | 边界敏感 |

TP8 仍是 `predicted_pass`，但报告会明确写“预测达标（边界敏感）”，
不会把 20% 的敏感性假设当成已观测误差。

C8 / 8192 / relaxed 示例：TP4 与 TP8 都达标；吞吐优先推荐 TP8，
但“满足 SLA 的最小配置”是 TP4（4 卡），资源效率分别为
105.11 / 83.44 tok/s/GPU。两者回答的是不同问题，不合并成一个分数。

## 5. AB4：候选搜索审计

`SimulationResult.search_audit` 记录：

* 发现部署数、通过身份/资源筛选数、完成回放数、失败数、截断数；
* 未参与比较的候选及原因（Sizing 不可行、GPU 超限、DP/PP 不可回放等）；
* 回放成功的候选 ID、失败候选的错误；
* 有效范围：`complete` / `partial` / `partial_with_failures` /
  `offline_archives`。

真实运行 `run_20261022T084124Z_44d383`（归档
`ab-live-audit-c32-tp4-tp8-bf16`）：

* 发现 3 个部署，通过 2 个，回放 2 个，失败 0，截断 0；
* TP2 被 Sizing 排除（75.88 GB > 72 GB/卡），原因保留在
  `filtered` / `not_replayed`；
* 有效范围 `complete`；TP4 fail、TP8 pass（边界敏感）。

技术报告和 Streamlit 都会显示“候选搜索与有效范围”，
不再把局部搜索静默当成全局最优。

## 6. 验收执行

| 项目 | 结果 |
|---|---|
| 全量测试 | `1062 passed, 1 skipped` |
| 全链路校验（含第 61 步真实执行） | `verify_all.sh` 66 / 66 通过；第 61 步运行 `run_20261022T103240Z_326e4e`（最终一轮），不是跳过 |
| Codex 审核 | `gpt-6-sol` + `low`（按当前 AGENTS.md）七轮：P1 5 项、P2 4 项，均已修复；末轮无新增可执行缺陷 |

## 7. 仍未完成

* 真实 GPU Benchmark 与预测误差校准（第 4 级证据）仍为 0；
* 12 个工作点只覆盖 Qwen3-32B / H100 / bf16 / vLLM 0.24.0；
* DP/PP 等复杂拓扑仍只记录“无法回放”，未做预测；
* 成本仍是合成示例，正式报价发布保持禁用；
* N5/N6 保持暂停。
