# 模拟预测接入（P1–P4）最终验证报告

**基线**：`eddf948`（Demo V1.1 封板）
**本轮范围**：P1 指标口径与数据契约 → P2 可选适配器 → P3 与 Sizing/TCO 比较 → P4 统一报告与验收
**结论**：**P1–P4 全部完成，通过验收。** 模拟预测以**可选、离线优先、结构性隔离**的方式接入，
未开启时原有行为与输出不变。

> 本报告所有数字来自本轮实际执行，日志见 `docs/test_run_log.txt`
> （sha256 `53f6ad6ae2b6c385511cccd684312d2d0939c09f93680ff9b5a3c941454e41bc`，
> 完成时间 `2026-10-16T17:00:00+08:00`，**64 步全部通过**）。

---

## 1. P1：口径与数据契约

### 1.1 先纠正了上一版错误对照（P1.1）

上一版把扫描器的 `default` 运行（默认后端 **trtllm**、自选**并发 13**）与原生回放的
**vllm / 并发 32** 相比 —— 后端与并发两个维度都不同，属跨口径比较，**结论已作废**。

### 1.2 严格对齐后重做（P1.3）

把扫描器搜索空间收缩到**一个点**（同 vllm 0.24.0 / 同 tp8 / 同 8 卡 / 同量化 /
同 ISL 8192 / OSL 256 / 并发 32），再用原生回放跑同一身份（500 请求）。

| 指标（bf16，与目标精度一致） | 扫描器 | 原生均值 | 原生中位数 | 原生 P99 |
|---|---|---|---|---|
| TTFT (ms) | **370.45** | 548.05 | **375.35** | **5201.83** |
| TPOT (ms) | 30.63 | 30.07 | 30.77 | 31.46 |
| 输出吞吐 (tok/s) | 997.42 | 990.89 | — | — |
| 请求速率 (req/s) | 3.911 | 3.871 | — | — |

**结论修正为**：两套接口在吞吐、TPOT 与 TTFT **中位数**上高度一致（1%–3%）；
扫描器单点 ≈ 原生中位数（差 1.3%），既不是均值也不是 P99；
**差异几乎全在尾部 —— P99 是中位数的 13.9 倍**。
对我们的 SLA（TTFT P99 ≤ 400ms）：扫描器 370.45ms 看着达标，**P99 超标 13 倍**。

### 1.3 顺带发现的身份缺口（P1.4/P1.5）

`pareto.csv` 里 bf16 / 并发 32 有**两行身份列完全相同**、只差
`ctx_tokens`（8192 vs 16384）的记录，**TTFT 差 73%**（370.45 vs 640.81）。
`ctx_tokens` 已并入工作点身份（`SimulationWorkload.ctx_tokens`），候选 ID 也包含它；
另有测试断言"同身份不同工作点必须保持为两个不同候选"。

### 1.4 数据契约（P1.4–P1.6）

新增 `src/llm_capacity_pricing/schemas/simulation.py`：

| 对象 | 作用 |
|---|---|
| `SimulationToolIdentity` | 谁算的：工具版本、surface、后端+版本、system、数据库模式、搜索空间哈希、**逐算子来源**、**经验修正系数** |
| `SimulationDeployment` | 部署身份：模型、**精度**、system、卡数、TP/PP/DP/MoE-TP/MoE-EP/CP、副本、并行串、量化列、内存、balance |
| `SimulationWorkload` | 负载身份：ISL/OSL/并发/前缀/**ctx_tokens**/停止条件/请求数 |
| `SimulationMetrics` | **全部 `sim_` 前缀**：均值/中位数/P99 分开存，吞吐、样本数 |
| `SimulationCandidate` | 身份+负载+指标+结论+可比性+来源文件/sha256/行号+成本 |
| `SimulationResult` | 状态、工具身份、候选、限制、`recommended()` / `best_effort()` |

**隔离是结构性的，不靠约定**：

* 不使用 `BenchmarkRun` / `CapacityPoint` / `PerformanceProfile`；
* 不往 `ttft_p99_ms` / `output_throughput_tok_s` 等实测字段写数；
* **不新增实测 `DataTrust` 等级** —— `simulated_prediction` 只是本对象内部的标签，
  实测可信等级枚举保持不动；
* 判定只有 `predicted_pass` / `predicted_fail` / `insufficient_prediction`，
  与 `observed_pass` / `observed_fail` 是两套取值。

判定规则（**全部由我们的阈值说了算**，不采纳工具的均值型 SLA 过滤结论）：

* 缺 P99 或样本数 < 30 → `insufficient_prediction`（**不得**用均值顶替 P99）；
* 未给任何 P99 阈值 → `insufficient_prediction`（没有判据就不给结论）；
* 任一项 P99 超阈 → `predicted_fail`；都不超 → `predicted_pass`。

---

## 2. P2：可选适配器

`src/llm_capacity_pricing/perf/aisimulate_adapter.py`（只做映射 / 取数 / 转换 / 归档）：

* **第一阶段（默认）只读本地归档**：`load_archived()` 解析
  `best_config_topn.csv` / `pareto.csv` / `prediction.json` / `exp_config.yaml` /
  `per_ops_source.json`，**不需要安装 AISimulate、不需要 GPU**；
* **第二阶段显式开启**：`enable_external: true` 才执行外部命令，
  命令用 **argv 列表 + `shell=False` + 超时**构造，含 shell 元字符的路径只当普通参数；
* 独立解释器经 `AISIMULATE_PYTHON` 或显式配置解析，找不到就**明确报错**；
* 原生结果里没有身份，**必须**配它的输入 YAML，否则报错而不是编一个身份；
* 短名单**在判完可比性之后**裁剪（优先保留可比工作点），避免"吞吐最高的 3 条恰好全不可比"。

**验收**：在没有 GPU、没有安装 AISimulate 的机器上，凭归档数据即可复现模拟展示
（`tests/test_p_phase_simulation.py` + `scripts/check_simulation_report.sh`）。

---

## 3. P3：与 Sizing / TCO 比较形成推荐

处理流程按任务书实现：

1. 静态资源规划照旧生成候选（未改动）；
2. 适配器返回模拟候选；
3. 检查模型、精度、硬件、负载、并发、后端版本的身份一致性 —— 缺一项即**不可比较**；
4. 只对**可比**候选比较预测吞吐 / P99 / 卡数；不可比的不参与排名；
5. 对可比候选调用**现有 TCO**（`build_cost_plan_from_template` + `compute_tco`）形成成本；
6. 报告输出模拟推荐理由与数据限制。

`recommended()` 只在 `predicted_pass` 里取吞吐最高者；全不达标时返回 `None`，
另由 `best_effort()` 给出"最好能到哪一步"（其结论多半是 `predicted_fail`，报告如实展示）。

---

## 4. P4：统一报告与验收

* `PlanRequest.simulation`（可选，默认关闭）；`PlanResult.simulation`；
  **不参与** `PlanResult.domains` 与整体 `status` 汇总 —— 否则"没开模拟"会把每次评估降级成 `partial`；
* 报告新增 **「## 2.2 模拟部署配置推荐（非实测）」**，含：模型与 GPU、推荐 TP/PP/DP/卡数/部署模式、
  模拟吞吐与 TTFT/TPOT P99、预测是否满足阈值、成本（本仓库 TCO）、工具/后端版本与数据来源、
  经验修正系数、逐算子来源、与实测证据的区别；
* 示例配置 `data/examples/scenario_simulation.example.yaml`（离线路线已配好，外部执行注释说明）；
* 示例报告归档：`docs/poc/aisimulate/20_sample_simulation_report.md`。

### 4.1 八条反向测试（全部通过）

| # | 要求 | 用例 |
|---|---|---|
| 1 | AISimulate 未安装时原有 `plan` 正常运行 | `test_plan_without_simulation_is_unchanged`、`test_plan_works_when_the_tool_is_not_installed` |
| 2 | 模拟结果不写入 `BenchmarkRun` / `CapacityPoint` | `test_simulation_never_writes_benchmark_runs_or_capacity_points` |
| 3 | 模拟结果不生成 `observed_pass` | `test_simulation_never_produces_observed_status` |
| 4 | 均值达标而 P99 超限必须 `predicted_fail` | `test_mean_pass_but_p99_fail_is_reported_as_predicted_fail` |
| 5 | 模型/量化/GPU/后端版本失配不得直接比较 | `test_precision_mismatch_makes_the_candidate_incomparable`、`test_hardware_mismatch_...`、`test_backend_version_mismatch_is_reported` |
| 6 | 命令失败或字段缺失返回可解释错误 | `test_missing_archive_file_yields_missing_data_not_a_crash`、`test_incomplete_native_output_has_no_verdict` |
| 7 | 不完整成本不得当作零成本 | `test_incomplete_cost_is_not_treated_as_zero` |
| 8 | 既有正式报价门禁保持禁用 | `test_quote_gate_stays_disabled_with_simulation_enabled` |

---

## 5. 验证结果

| 项 | 上轮 | 本轮实测 |
|---|---|---|
| `pytest -q` | 807 | **822 passed**（P 阶段新增 23 + 15 条） |
| `bash scripts/verify_all.sh` | 63 步 | **64 步，通过 64，失败 0** |
| 第一阶段历史基线 | 32 / 160 / 192 | **32 / 160 / 192 不变** |
| 复算超差 | 0 | **0** |
| 官方候选逐字段核对 | 12/12 | **12/12（不一致 0 条）** |
| 敏感数据扫描 | 未发现 | **未发现**（含本轮新增的归档报告，已单独复查） |

新增链路步骤：`60. 模拟预测接入（P4：离线可复现的模拟报告 + 与实测隔离）`。

---

## 6. 模拟配置搜索与预测准确性边界（交付物）

**能做**：给定模型 + GPU + 负载 + SLA，搜索并推荐部署配置（TP/PP/DP/MoE-TP/MoE-EP/CP、
副本、agg/disagg、量化、chunked prefill、MTP），并在原生回放下给出**含 P99 的完整分位数**。

**边界（必须随任何结论一起出现）**：

1. **口径**：扫描器的 SLA 过滤是**聚合均值**，官方原文写明 "It is not a p99 constraint"；
   我们只取它的候选枚举与吞吐排序，**达标与否由我们用 P99 自己判**。
2. **单点 ≈ 中位数**：扫描器的 TTFT 单点与原生回放中位数一致（差 1.3%），
   **不是**均值、更不是 P99；P99 可达其中位数的 13.9 倍。
3. **精度不可混用**：fp8 与 bf16 的 TTFT/TPOT 差异明显（TPOT 44.82 vs 30.63）；
   目标精度与模拟精度不一致时判为**不可比较**。
4. **SILICON 不等于全实测**：`per_ops_source.json` 显示仍有 8 个 `empirical` 算子，
   且搜索默认叠加经验修正系数（`autoscale_ttft_correction_factor=1.8`、
   `prefill_latency_correction=1.1`、`decode_latency_correction=1.08` 等）。
5. **官方精度证据不覆盖我们的模型**：发布的 E2E 精度快照里**没有 `Qwen3-32B`**；
   全量 MAPE 为 TTFT **45.27%** / TPOT **22.39%**（且明确"只适用于快照中的那些实测工作点"）。
   因此模拟值**只能用于排序与发现明显不可行**，不能当作可承诺的时延。
6. **样本**：P99 需要样本支撑；样本缺失或 < 30 一律 `insufficient_prediction`。
7. **身份**：模型 + 精度 + system + 后端 + 后端版本 + 并行 + 负载 + **每步上下文预算**
   必须齐备，缺一项就只能给"不足以结论"。

---

## 7. 未闭环事项

1. **Public 仓库历史提交中的内部定价数据风险** —— 仍需单独闭环；
   重写历史或强推必须获得明确授权，本轮未做。
2. **生产审批与正式报价发布** —— 仍依赖可信审批系统，保持部署级禁用。
3. **目标配置（Qwen3-32B @ H100）的真实性能** —— 仍无实测；模拟预测**不能**替代它。
4. **RTX 3090 实测管线验证** —— 你已同意开展，但当前 `compute01` 根分区仅剩约 17GB，
   建议先完成本阶段，再独立安排（选装得下的小模型、先确认安装位置与清理策略）。
   它验证的是"真实 Benchmark 能否进入我们的系统"，**不**为 H100 的模拟结果提供校准点
   （硬件不同，AISimulate 也不支持 3090）。
5. **Web 前端 / FastAPI / Agent** —— 本阶段范围内未开发。
