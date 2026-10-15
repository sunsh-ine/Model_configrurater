# 第三阶段验证报告（M19–M25）

- 日期：2026-10-15
- 开发基线：`924dc80`（第二阶段 V2.1 整改后）
- 范围：Benchmark 匹配、性能实测数据管理、并发容量分析、性能估算、SLA 评估、部署候选比较
- 完整执行日志：`docs/test_run_log.txt`（`bash scripts/verify_all.sh`，**36 步全绿，退出码 0**）
- 配套：`docs/phase2_final_validation.md`（M12–M18）、`docs/security_remediation_report.md`（F1）

---

## 一、两个独立验收维度（任务书 §5 要求）

任务书明确要求把"工程功能"与"性能可信度"**分开验收**。本报告据此分节。

### 1.1 工程功能验收：通过

| # | 验收项 | 实测证据 |
|---|---|---|
| 1 | 全链路离线可运行 | `步骤 36 项：通过 36，失败 0`，退出码 0（无 GPU、无联网） |
| 2 | 自动化测试 | **460 passed**（第二阶段的 404 + 第三阶段 56） |
| 3 | 第一阶段回归 | `结果条数: 32`、`默认敏感性情景条数: 160`、`最终 pricing_result 总数: 192`、`超容差不匹配数: 0`、`残留的 synthetic_fixture 结果行: 0`——全部不变 |
| 4 | 七个 CLI 命令接通 | `import-performance` / `match-benchmark` / `capacity-profile` / `calibrate-performance` / `evaluate-sla` / `rank-deployments` / `performance-report` 在链路中全部通过 |
| 5 | JSON + Markdown 报告 | `data/exports/performance_report.md`（2482 字节，含证据概览、容量曲线、候选比较、未确认清单） |
| 6 | 官方候选可检索可解释 | 18 条候选全部进签名池；匹配结果带结构化的 `reason_codes` 与 `differences` |
| 7 | 官方候选不会变成"内部实测" | 候选的 `data_trust` 恒为 `measured_vendor_published`；任何 synthetic 结论必须显式打标 |

### 1.2 性能可信度验收：**未验证（保持未验证）**

| 验收项 | 状态 | 原因 |
|---|---|---|
| 目标部署（Qwen3-32B + H100/H200）的真实实测数据 | **缺失** | 本项目**没有**该组合的任何实测记录；现有材料只有 Qwen3-235B/B200 与 Qwen3-Coder/MI300X 的官方公开数据 |
| 样本量与覆盖范围满足既定政策 | **未满足** | 目标配置没有任何并发点，谈不上覆盖 |
| 预测误差验证 | **未进行** | 没有可校准的实测数据；估算器对目标配置一律返回 `not_predictable` |

因此第三阶段**只能交付"评估引擎"，不能交付"性能结论"**。
按任务书要求，这种情况下的正确输出就是 `not_evaluable` / `requires_benchmark`——
链路中已实测得到该结果（见 §3.4、§3.5），**这不是开发失败**。

---

## 二、四分类结论

### 2.1 已通过

| # | 项 | 实测输出 |
|---|---|---|
| 1 | M19 跨模型/跨硬件不误命中 | `匹配等级分布: {'rejected': 55}`，`非拒绝项: 0 条`（目标 Qwen3-32B + H100 SXM） |
| 2 | M19 四种匹配等级齐备 | `exact_match`（配置+负载全等）、`comparable_reference`（负载不同）、`modeling_candidate`（同模型同硬件但 TP/卡数不同）、`rejected`（模型/硬件/精度不符、EP/PD、缺指标、synthetic 未开 opt-in） |
| 3 | M19 有明确 reason codes | 如 `hardware_model_mismatch:NVIDIA H100 SXM!=NVIDIA B200`、`unsupported_deployment_field:ep_size`、`data_trust_rejected:synthetic_fixture_not_allowed` |
| 4 | M20 两套框架统一导入 | vLLM `bench serve` JSON 与 SGLang 逐请求 JSONL 均落到 `BenchmarkRun` + `PerformanceProfile` |
| 5 | M20 原始值可逐项核对 | 测试对同一原始 JSON 的 `p99_ttft_ms`/`p99_tpot_ms`/`p99_itl_ms`/`output_throughput` 逐项比对通过 |
| 6 | M20 幂等 | 同一曲线重复导入 2 次仍为 5 条 |
| 7 | M20 P99 缺失不填零 | Excel 记录（无时延口径）的 P99 保持 `None`，并登记 `missing_metrics` |
| 8 | M20 吞吐自洽校验 | `throughput_consistent_with_tokens_over_duration`；人为放大 10 倍后被判 `output_throughput_inconsistent` |
| 9 | M20 配置并发与观测并发分离 | 合成样本只给配置并发 → 观测并发保持 `None`，不被顶替 |
| 10 | M21 容量曲线 | 5 个并发点：吞吐与 TTFT 均随并发单调上升；覆盖 `[1,2,4,8,16]`，标出缺失点 32；`coverage_status=sufficient` |
| 11 | M21 单点最优 ≠ 可持续 | 默认判据下 `maximum_sustainable_capacity=16`（附持续时间与失败率证据）；把时长门限提到 1000s 后变回 `None` 并打 `sustainable_capacity_not_evidenced` |
| 12 | M21 不混配置/负载 | 混合不同 TP 或不同负载类型直接抛错（两条测试） |
| 13 | M22 精确查询优先 | 命中已观测并发点 → `measured_reference`，不使用模型 |
| 14 | M22 范围内插值带留出误差 | 并发 6（区间 1–16 内）→ `calibrated_estimate`，值 222.5，`holdout_mae=19.78`、相对误差 10.3%（**留一验证**，不是拟合残差） |
| 15 | M22 不外推 | 并发 64 → `not_predictable: out_of_covered_range` |
| 16 | M22 P99 不插值 | `ttft/tpot/itl/e2e` 四个 P99 全部 `not_predictable: p99_not_interpolatable` |
| 17 | M23 四类状态齐全 | `observed_pass` / `observed_fail` / `estimated_only` / `not_evaluable` 均有测试覆盖 |
| 18 | M23 默认策略拒绝 synthetic | 链路中 `SLA 状态: not_evaluable`（`data_trust_not_accepted:synthetic_fixture`） |
| 19 | M23 实测超标 → observed_fail | 阈值 150ms 对实测 210ms，逐项给出"超过上限" |
| 20 | M23 估算不得当实测 | 仅估算 → `estimated_only`，明示"未经实测验证" |
| 21 | M23 Goodput 需逐请求明细 | 无明细 → `not_evaluable` 且拒绝用聚合 P99 反推；有明细 → 8/10 条满足并除以测试时长 |
| 22 | M23 SLA 必须挂匹配判定 | 不传匹配结果 → `not_evaluable: match_result_required_for_sla_proof`（无后门） |
| 23 | M24 静态不可行先排除 | 分桶 `{'requires_benchmark': 2, 'excluded': 1}`，`excluded` 全为 `infeasible` |
| 24 | M24 无性能证据 → requires_benchmark | 链路输出 `阻塞: requires_benchmark: 所有静态可行候选都缺少匹配的目标硬件实测数据` |
| 25 | M24 有匹配实测 → verified_sla_met | 测试中喂入对齐的实测记录后进入 `verified_sla_met` 并附 `matched_run_id` |
| 26 | M24 不做跨配置吞吐排序 / 不做经济性评分 | `comparability_notes` 显式声明；`economic_scoring_used=False` |
| 27 | M25 报告含全部边界声明 | 6 条边界声明逐一断言通过 |

### 2.2 已修复（开发中发现并修掉的问题）

| # | 问题 | 根因 | 修复 |
|---|---|---|---|
| 1 | **ranker 把 SLA 字段名当指标口径 ID**：`required_metrics=["ttft_p99_ms"]` 而签名里是 `m.latency.ttft.p99` → 所有记录都被判"缺指标"，候选全部落到 `requires_benchmark` | 匹配需要的是**口径 ID**，不是字段名 | 新增 `required_metric_ids()` 做映射；ranker 改用它。若未修，`verified_sla_met` 分支永远不可达（被测试抓出） |
| 2 | **TP/DP 差异被当成硬阻断**：同模型同硬件、仅 TP 不同也被判 `rejected`，`modeling_candidate` 形同虚设 | `gpu_count_mismatch` 被列入硬阻断原因 | 拆分为"数据层不一致"（模型/硬件/精度/指标 → rejected）与"部署层不一致"（tp/dp/卡数 → modeling_candidate） |
| 3 | **`deployment_mode="unknown"` 被当成"有值但未确认"**，导致配置完全一致的合成样本只能降级 | 未区分"真实取值"与"未指定占位值" | 引入 `is_absent()`，把 `unknown/none/空` 视为缺失 |
| 4 | **SLA 可以绕过匹配判定**：不传 `match_result` 也能给出 `observed_pass` | 缺省即放行 | 改为强制：无匹配判定 → `not_evaluable`（`match_result_required_for_sla_proof`） |
| 5 | **降级原因不可解释**：只写 `downgraded:unverified_details_present`，不说是什么未确认 | 未把未确认项写入原因 | 未确认项以 `unverified:<item>` 形式进入 `reasons`（例：`unverified:deployment_mode_unverified:unknown`） |
| 6 | `RankedDeployment` 构造时漏传必填的 `rank_bucket` | 先构造后赋值的写法与 Pydantic 校验冲突 | 改为先定桶与理由再构造 |
| 7 | 服务层缺少 `SlaPolicy` 导入，`rank-deployments` 直接报 `NameError` | 类型注解之外的运行时引用 | 补齐导入 |

### 2.3 未确认（保留待确认，未自行假设）

| # | 事项 | 当前处理 |
|---|---|---|
| 1 | **目标部署（Qwen3-32B + H100/H200）的实测性能** | 无数据 → 匹配全 `rejected`、SLA `not_evaluable`、排序 `requires_benchmark` |
| 2 | 目标硬件的实测可用显存（第二阶段的遗留缺口） | `usable_memory_status=pending_measurement`，规划只能到 `conditional_feasible` |
| 3 | 官方候选的 `model_revision` / 环境 pin | 保持 `null`；因此候选之间即使配置相近也不能当作同一部署 |
| 4 | 观测并发 | 官方候选与合成样本都只给配置并发；观测并发保持 `None` |
| 5 | 统计学口径（P99 的算法、计时窗口、预热策略） | 记录为 `metric_definition_ids` + `statistics_window`，未提供即留空 |
| 6 | 价格币种与成本覆盖范围 | 仍未确认；因此**不使用经济性评分**（M24 硬约束） |
| 7 | Git 历史中的定价数据 | 仓库仍为 public，方案见安全报告 §4，未授权不重写历史 |

### 2.4 未实现（本阶段边界内不做）

| # | 项 | 说明 |
|---|---|---|
| 1 | 按 GPU FLOPS 推算 TTFT/TPOT | 任务书禁止项；代码中无此路径 |
| 2 | PP / EP / PD 分离的性能模型 | 字段保存并**显式阻断**（`unsupported_deployment_field`） |
| 3 | 跨模型/跨硬件的通用性能预测器 | 只保留 `PerformanceModel` 协议接口；未实现 |
| 4 | 跨 TP/DP 的每卡吞吐外推 | 禁止；`modeling_candidate` 明确标注 `cross_config_extrapolation` 禁止使用 |
| 5 | Goodput 的聚合反推 | 只允许由逐请求明细计算 |
| 6 | P99 的插值与统计保证 | P99 一律不可插值 |
| 7 | 把静态可行当性能达标 | 两种结论在数据结构上就是不同的字段（`feasibility` vs `sla_status`） |

---

## 三、关键实测输出

### 3.1 匹配：目标需求 vs 全源数据

```text
$ match-benchmark --model "Qwen/Qwen3-32B" --hardware "NVIDIA H100 SXM" --gpu-count 4 --tp 4 \
    --input-len 8192 --output-len 256
匹配等级分布: {'rejected': 55}
非拒绝项: 0 条
```

55 = 32 条 Excel 历史记录 + 4 条 synthetic fixture + 12 条数值候选 + 6 条页面候选 + 1 条导入的性能记录。
**全部被拒绝是正确行为**：没有任何一条数据是在 Qwen3-32B + H100 上测出来的。

### 3.2 容量曲线（合成样本，仅算法验证）

```text
$ capacity-profile --target-concurrency 8
记录 5 条，分成 1 个配置组
  点数=5 覆盖=sufficient 峰值并发=16 输出=380.0 可持续容量=16
```

| 配置并发 | 输出 tok/s | TTFT p99 | TPOT p99 | ITL p99 |
|---|---|---|---|---|
| 1 | 45.0 | 120.0 | 12.0 | 12.6 |
| 2 | 88.0 | 150.0 | 14.0 | 14.8 |
| 4 | 165.0 | 210.0 | 18.0 | 19.2 |
| 8 | 280.0 | 340.0 | 26.0 | 27.5 |
| 16 | 380.0 | 720.0 | 45.0 | 47.0 |

`--exclude-synthetic` 时输出 `分成 0 个配置组`——**正式结论不会碰到 synthetic 数据**。

### 3.3 估算（M22）

```text
$ calibrate-performance --metric output_throughput_tok_s --concurrency 6
estimate_kind = calibrated_estimate
value = 222.5        （介于 c=4 的 165 与 c=8 的 280 之间）
holdout_mae = 19.77777777777777777777777777
holdout_relative_error = 0.103224
covered_range = [1, 16]
```

并发 64（范围外）→ `not_predictable`；四个 P99 → `not_predictable`。

### 3.4 SLA

```text
$ evaluate-sla --requirement '{"requirement_id":"req-default","ttft_p99_ms_max":400}' \
    --run-id fixture_perf_curve_c4 …（默认策略）
SLA 状态: not_evaluable       # synthetic 被生产策略拒绝

$ evaluate-sla … --policy-id algorithm-validation-only --allow-synthetic
SLA 状态: observed_pass
  注意：该结论仅用于算法验证（synthetic 数据），不得对外使用
```

同一份数据在两种策略下给出不同结论，且后者被显式打标——这是"算法可验证、结论不可对外"的实现方式。

### 3.5 候选排序（M24，目标配置的真实情况）

```text
$ rank-deployments --model qwen3-32b --hardware nvidia-h100-sxm-80gb --tp-sizes 2,4,8 --dp-sizes 1 …
分桶: {'requires_benchmark': 2, 'excluded': 1}
阻塞: requires_benchmark: 所有静态可行候选都缺少匹配的目标硬件实测数据
```

| 候选 | 静态状态 | 分桶 |
|---|---|---|
| TP4×DP1 | conditional_feasible | `requires_benchmark` |
| TP8×DP1 | conditional_feasible | `requires_benchmark` |
| TP2×DP1 | infeasible | `excluded` |

---

## 四、验证命令与结果

```bash
bash scripts/verify_all.sh      # 退出码 0；步骤 36 项：通过 36，失败 0
.venv/bin/python -m pytest -q   # 460 passed
```

| 步骤 | 实测输出 |
|---|---|
| 24 | `已导入 5 条性能记录`，`可信等级: ['synthetic_fixture']`，附"仅用于算法验证"说明 |
| 25 | `分成 0 个配置组`（排除 synthetic 后无正式曲线） |
| 26 | `点数=5 覆盖=sufficient 峰值并发=16 输出=380.0 可持续容量=16` |
| 27 | `estimate_kind: calibrated_estimate` + 留出误差 |
| 28 | `匹配等级分布: {'rejected': 55}` |
| 29 | `SLA 状态: not_evaluable` |
| 30 | `SLA 状态: observed_pass` + `注意：该结论仅用于算法验证` |
| 31 | `分桶: {'requires_benchmark': 2, 'excluded': 1}` + 阻塞说明 |
| 32 | `报告已写入 data/exports/performance_report.md` |
| 33 | `已跟踪文件中未发现内部定价数值`（V1.2 门禁仍生效） |
| 34 | `460 passed` |

---

## 五、下一步：如何把"未验证"变成"已验证"

任务书 §4 已给出建议实验矩阵，落地时**按此补测即可接入**（无需再改代码）：

| 维度 | 建议取值 | 备注 |
|---|---|---|
| 模型 | Qwen3-32B BF16（记录 revision） | 与 `data/models/` 归档的 revision 对齐 |
| GPU | H100 SXM / H200 SXM | 同时记录 `nvidia-smi` 实测可用显存 |
| 部署候选 | TP4×DP1、TP2×DP2 | 正是第二阶段判定为"条件可行"的两组 |
| 输入/输出 | 1K/1K、8K/256、32K/256 | 32K 需确认模型上下文上限（声明 40960，可用） |
| 并发 | 1、2、4、8、16、32 | ≥3 点才能做范围内插值，≥5 点曲线才有意义 |
| 必采指标 | TTFT P99、TPOT P99、ITL P99、E2E P99、Input/Output TPS、失败率 | 缺 P99 的项会被判 `not_evaluable` |
| 必存元数据 | 模型 revision、框架版本、完整启动与测试命令、实际 token 长度、卡数与测试持续时间 | 持续时间决定能否给出"可持续容量" |
| 两类负载 | 固定长度标准测试 + 多轮/前缀复用测试 | **分文件导入**，不得混入同一条曲线 |

导入方式（无需 GPU、无需联网）：

```bash
python -m llm_capacity_pricing.cli import-performance \
  --file <bench_result.json> --metadata <meta.yaml> --kind vllm --db data/local.db
```

补测后的预期变化：`match-benchmark` 对目标配置给出 `exact_match`；
`evaluate-sla`（默认策略）给出 `observed_pass`/`observed_fail`；
`rank-deployments` 的 `verified_sla_met` 桶被填充。

---

## 六、是否达到第三阶段完成判定

任务书要求：*能完成一次完整流程——业务场景输入 → 资源筛选 → Benchmark 匹配 → 性能分析 →
SLA 评估 → 候选排序或返回证据缺口*。

| 判定项 | 结论 |
|---|---|
| 工程功能验收 | **通过**（36 步全绿、460 passed、7 个 CLI、JSON/Markdown 报告） |
| 性能可信度验收 | **未验证**（缺目标硬件实测，属预期；已如实输出 `not_evaluable` / `requires_benchmark`） |
| 是否越界 | **无**（未按 FLOPS 推算、未跨配置外推、未用均值代 P99、未用 synthetic 出正式结论） |
| 第一阶段回归 | **不变**（32 / 160 / 192 / 0 超容差） |

**结论：第三阶段工程功能完成、可交付；性能可信度维度保持未验证，等待现场实测数据接入。**


---

# 附：V3.1 验收整改（按代码验收意见 R1–R7）

验收意见指出 3 个 P0 与 4 个 P1 缺陷，其中 P0-1（SLA 默认允许负载不匹配）会直接导致
"用 1K 的实测证明 32K 的 SLA"。本节记录**根因 → 修复 → 复现证据**，以及被推翻的旧结论。

## A.1 R1（P0）SLA 默认只接受严格匹配

**根因**：`SlaPolicy.minimum_match_level` 默认值是 `comparable_reference`，
而 `comparable_reference` 恰恰包含"负载长度不同"的情形 —— 于是 1K/1K 的实测可以
为 32K/256 的需求给出 `observed_pass`。

**修复**：默认改为 `exact_match`；语义写进字段注释：
`comparable_reference` 仅用于展示/研究/对比，`modeling_candidate` 仅用于建模，
二者都**不得**直接用于 SLA 证明；即便 `exact_match`，仍需通过来源可信度与样本检查。

**复现（修复后）**：`tests/test_phase3_performance.py::test_r1_long_context_cannot_use_short_context_benchmark`
构造"模型/GPU/TP/DP 完全相同、仅输入长度 32K vs 1K、并发 32 vs 8"的一对数据，
断言匹配等级为 `comparable_reference`、SLA 为 `not_evaluable`
（`match_level_below_policy:comparable_reference<exact_match`）。

**被推翻的旧结论**：第三阶段首版报告中"负载不同的实测可作可比参考并参与 SLA 判定"
的描述**不成立**；现在这类数据只能用于展示与建模。

## A.2 R2（P0）严格匹配必需字段

**根因**：`_compare()` 把"任一侧缺失"视为相等，且 `concurrency_mismatch` 只记录不参与定级，
`request_rate_rps` / `streaming` / `prefix_reuse` / `warmup` / `eos_policy` 完全没有参与匹配。

**修复**：

| 机制 | 规则 |
|---|---|
| `RECORD_IDENTITY_FIELDS` | model_name / model_revision / weight_precision / hardware_model / gpu_count / tp / dp / framework / framework_version —— 记录侧**缺失任一项**即登记 `record_identity_incomplete:*`，不得 `exact_match` |
| `RECORD_WORKLOAD_IDENTITY_FIELDS` | dataset_name / input_len_target / output_len_target / arrival_mode —— 同样要求记录侧齐备 |
| 并发不一致 | 降级为 `comparable_reference`（并发直接决定吞吐与时延） |
| 请求速率不一致 | 降级 |
| `LOAD_SEMANTIC_FIELDS` | streaming / prefix_reuse / warmup / eos_policy 两侧都有值且不一致时降级 |

**连带发现**：适配器此前**没有**从元数据读取 `weight_precision` / `tp_size` / `dp_size` /
`dataset_name`，导致严格匹配永远无法达成。已在 `vllm_adapter.py` 与 `sglang_adapter.py`
补齐这四个字段的映射。

**复现**：`test_exact_match_requires_full_identity_on_record`、
`test_r2_concurrency_mismatch_downgrades`、`test_r2_strict_identity_fields_required`、
`test_exact_match_needs_full_identity`（官方候选缺 revision/精度 → 只能 `comparable_reference`）。

## A.3 R3（P0）来源可信等级显式映射

**根因**：`run_from_adapter()` 写的是

```python
"synthetic_fixture" if record.performance_source_kind == "synthetic_fixture" else "measured_internal"
```

——**除 synthetic 外的一切来源都被标为"内部实测"**，包括官方公开发布与第三方数据。
这是一次真实的"来源升级"，会让外部数据直接具备支撑 SLA 的资格。

**修复**：新增显式映射表 `SOURCE_KIND_TO_TRUST` 与 `trust_for_source_kind()`：

| `performance_source_kind` | 可信等级 |
|---|---|
| `internal_measurement` | `measured_internal` |
| `official_benchmark` | `measured_vendor_published` |
| `third_party` | `measured_third_party`（**新增等级**，默认策略不自动接受） |
| `internal_excel` | `historical_external_no_latency` |
| `synthetic_fixture` | `synthetic_fixture` |
| 其它 / 缺失 | `unknown` + 质量标记 `unmapped_performance_source_kind:*` |

**复现**：`test_r3_source_kind_trust_mapping`、`test_r3_official_benchmark_not_marked_internal`。

## A.4 R4（P1）容量曲线分组纳入 Workload

**根因**：`grouping_key()` 只含部署条件与负载类型，没有长度/数据集/到达方式，
于是 1K/1K 与 32K/256 可能被算进同一条并发曲线（M22 也复用了这个键）。

**修复**：分组键增加 `isl / osl / dataset_name / arrival_mode / prefix_reuse / streaming`。

**复现**：`test_r4_workload_lengths_split_capacity_groups`、
`test_r4_dataset_and_arrival_mode_split_groups`（并验证混合成组会直接报错）。

## A.5 R5（P1）可持续容量证据收紧

**根因**：筛选条件 `p.error_rate is None or p.error_rate <= criteria.max_error_rate`
—— **失败率未知反而能通过**；且单次 60 秒测试就被当作"可持续"。

**修复**：

* 失败率**必须已知**且 ≤ 阈值，否则该点直接判不通过（并记 `point_not_stable:cN:失败率未知`）；
* 新增重复观测门槛 `min_repeats_per_point`（默认 2）与可选时延 SLO 约束；
* 单次达标只记入新字段 **`observed_stable_candidate`**；
  `maximum_sustainable_capacity` 必须同时满足"失败率已知 + 时长达标 + 重复观测达标"。

**复现**：`test_r5_unknown_error_rate_blocks_sustainable_capacity`、
`test_r5_latency_slo_included_in_sustainability`（TTFT SLO 400ms 时，可持续容量从 16 降到 8）、
`test_sustainability_requires_duration_and_error_evidence`。

## A.6 R6（P1）估算与排序的多样本处理

**根因（三处）**：① `estimate()` 不校验输入 run 的配置一致性与可信等级，完全依赖调用方；
② 留出验证可能只有 1～2 个有效点却照常给出误差；③ `_best_match()` 同等级取第一条，
可能选中波动中的"较好那一次"。

**修复**：

| 项 | 修复 |
|---|---|
| 输入校验 | 估算器内部再查：配置/负载必须同组（否则 `mixed_configurations_in_input`）、可信等级必须一致（`mixed_trust_levels`）且被允许（`trust_level_not_allowed_for_estimation`，默认拒绝 synthetic） |
| 留出门槛 | 有效留出点 < 3（`MIN_HOLDOUT_POINTS`）→ `not_predictable: insufficient_holdout_points` |
| 多次实测 | 新增 `_aggregate_runs()`：同等级多条实测按**保守口径**聚合（P99 取最大、吞吐取最小、可信等级取最弱），生成 `run_id="aggregate:..."`，并在排序说明中标注 |

**复现**：`test_r6_estimator_rejects_mixed_configurations`、`test_r6_estimator_rejects_mixed_trust`、
`test_r6_estimator_default_refuses_synthetic`、
`test_ranking_uses_conservative_aggregate_not_best_run`
（严格阈值下，取 c=1 的 120ms 会通过，但保守聚合取最大 P99 720ms → `measured_but_sla_unmet`）。

## A.7 R7（P1）专项测试与重新封板

新增 15 个反向用例，其中审核特别要求的端到端场景：

`test_r7_end_to_end_no_false_verified_path` ——
模型、GPU、TP/DP 相同，输入长度与配置并发不同的两条 Benchmark，
依次执行**匹配 → SLA → 排序**，断言：

* 匹配等级 ≠ `exact_match`；
* 排序结果落在 `requires_benchmark`（**绝不进入 `verified_sla_met`**）；
* 对照组 `test_r7_same_load_still_reaches_verified` 证明规则不是"无脑拦死"。

| 项 | 结果 |
|---|---|
| 全链路 | `步骤 36 项：通过 36，失败 0` |
| 测试 | **477 passed**（第三阶段 58 → 73） |
| 第一阶段回归 | 32 / 160 / 192 / 0 超容差 —— 不变 |

## A.8 开发过程中额外修掉的两个问题

1. **`analyze_group` 变量初始化顺序错误**：R5 的新逻辑在 `flags` 初始化之前就使用它，
   导致 `capacity-profile` / `performance-report` 直接崩溃（被链路步骤抓到）。
2. **CLI 无法表达严格匹配所需的身份字段**：`--model-revision` / `--framework` /
   `--framework-version` / `--precision` / `--dataset` / `--arrival-mode` 全部补齐
   （否则修复 R1/R2 后，SLA 判定永远无法达到 `exact_match`，功能形同虚设）。

## A.9 修复前后行为对照

| 场景 | 修复前 | 修复后 |
|---|---|---|
| 32K 需求 vs 1K 实测（其余相同） | 可能 `observed_pass` ❌ | `not_evaluable`（`match_level_below_policy`）✅ |
| 并发 32 需求 vs 并发 8 实测 | 可能 `exact_match` ❌ | `comparable_reference` + `concurrency_mismatch` ✅ |
| 记录缺 model_revision | 视为"相等" ❌ | `record_identity_incomplete` → 不得 exact_match ✅ |
| 导入 `official_benchmark` | 标为 `measured_internal` ❌ | `measured_vendor_published` ✅ |
| 1K/1K 与 32K/256 | 可能同组算曲线 ❌ | 分组键不同，混组直接报错 ✅ |
| 失败率缺失的并发点 | 计入可持续容量 ❌ | 判不通过；单次达标只记 `observed_stable_candidate` ✅ |
| 同配置多次实测 | 取第一条 ❌ | 保守聚合（P99 最大 / 吞吐最小 / 可信最弱）✅ |
| 混合不同配置做插值 | 不检查 ❌ | `mixed_configurations_in_input` ✅ |


---

# 附：V3.1 复审加固（四类边界问题）

复审确认 R1–R7 的核心修复已落地，同时指出四类**边界问题**。已全部加固，
本节记录根因、修复与复现证据。

## B.1 P0 目标侧身份完整性

**根因**：`RECORD_IDENTITY_FIELDS` 只检查**记录侧**是否完整；目标侧缺
`model_revision` / `framework_version` / 负载条件时，`_compare()` 仍把"缺失"视为相等，
于是"记录完整 + 目标什么都没说"也能得到 `exact_match`。

**修复**：新增 `TARGET_IDENTITY_FIELDS`（model_revision / weight_precision / framework_version）
与 `TARGET_WORKLOAD_IDENTITY_FIELDS`（dataset_name / input_len_target / output_len_target /
arrival_mode），目标侧缺任一即登记 `target_identity_incomplete:*`，不得 `exact_match`。
**严格匹配现在要求"目标说清了要什么"且"记录说清了测了什么"。**

**复现**：`test_review_target_identity_incomplete_blocks_exact_match`、
`test_review_target_workload_incomplete_blocks_sla`。

## B.2 P0 SLA 门禁不可放宽 + 匹配来源校验

**根因（两处）**：① `evaluate_sla()` 只比较等级顺序，调用方传
`SlaPolicy(minimum_match_level="comparable_reference")` 就能让负载不同的数据进入
`observed_pass`；② 没有校验传入的匹配结论是否针对**被评价的那条 run**。

**修复**：

* 新增不可放宽的门禁：`observed_pass` / `observed_fail` **只接受 `exact_match`**，
  与 `minimum_match_level` 无关；`comparable_reference` / `modeling_candidate` 一律
  `not_evaluable`（`sla_requires_exact_match`）。
* 匹配结论若声明 `blocked_from` 含 `sla_proof`，除非策略**同时**显式允许 synthetic
  且该结论带 `algorithm_validation_only` 标记，否则阻断（`sla_proof_blocked_by_match`）。
* 新增 `match_result_run_mismatch`：匹配结论的 `signature_id` / `source_ref` 必须与
  被评价的 run 对应，否则阻断。

**复现**：`test_review_relaxed_policy_cannot_produce_observed_pass`、
`test_review_match_result_must_reference_this_run`。

**实现过程中发现并修掉的相关 bug**：门禁 2b 最初把"等级不足"与"`sla_proof` 被阻断"
混在一个判断里，导致**连合法的算法验证路径也被拦死**，且报错信息写成
"当前匹配等级为 exact_match"（自相矛盾）。现已拆成两个独立原因码。

## B.3 P1 重复观测必须"达标"才算，失败观测不得被忽略

**根因**：`repeats` 统计的是同一并发点的**全部**测试次数；只要其中一次达标、
总次数够门槛，就会写出 `maximum_sustainable_capacity`。例如两次观测中一次 0%、
一次 20% 失败率，仍可能被认定为稳定。

**修复**：按并发点归组，逐点统计"达标 / 不达标"观测；
**任一次观测不达标即该点整体不计入稳定容量**，并把失败观测的 `run_id` 与原因
写入 `notes` 与 `quality_flags`（`concurrency_has_failing_observation:cN`）。
只有"全部观测达标且达标次数 ≥ 门槛"才给 `maximum_sustainable_capacity`。

**复现**：`test_review_partial_repeats_do_not_become_sustainable`。

## B.4 P1 估算器校验顺序与聚合的缺失指标

**根因（两处）**：① `estimate()` 的精确查询发生在可信度/一致性校验**之前**，
`target_run` 可以绕过后面的校验直接返回 `measured_reference`；
② `_aggregate_runs()` 聚合时对 `None` 取 max/min 会**忽略缺失**，用剩余记录凑出
一个看似完整的值（例如某条观测没有 TTFT P99，聚合结果却给出 TTFT）。

**修复**：

* 估算器顺序改为：**可信度/一致性/target_run 作用域校验 → 精确查询 → P99 红线 → 点数与覆盖**；
  新增 `target_run_not_in_scope`（target_run 必须与输入 run 同组同负载）。
* 聚合前先检查每条记录是否具备所需指标：**任一记录缺某项，该指标在聚合结果中保持为空**，
  并打 `aggregate_metric_missing_in_some_runs:<field>` 标记 + 说明
  "不得据此声称全部观测支持对应 SLA"。

**复现**：`test_review_estimator_exact_query_respects_trust`、
`test_review_estimator_target_run_must_be_in_scope`、
`test_review_aggregate_does_not_hide_missing_metric`、
`test_review_aggregate_takes_conservative_values`、
`test_review_conservative_aggregate_fails_tight_sla`。

## B.5 验证脚本改进：SLA 步骤改为**断言式**

复审期间发现一个**验证脚本本身的缺陷**：`run_step` 的"关键行"只用于展示，
命中与否不影响该步成败 —— 于是"算法验证路径失效"这种回归可以**静默通过**。

已把两个 SLA 步骤改为断言式 shell 函数：

* `step_sla_default_policy`：输出必须含 `SLA 状态: not_evaluable`，否则该步失败；
* `step_sla_algorithm_validation`：必须含 `SLA 状态: observed_pass` **且**含
  `仅用于算法验证`，否则失败。

这个改动当场就抓出了"verify 脚本漏传 `--concurrency`、导致目标负载身份不全"的问题
（该问题此前被展示式关键行掩盖）。

## B.6 本轮结果

| 项 | 结果 |
|---|---|
| 全链路 | `步骤 36 项：通过 36，失败 0`（其中 2 步为断言式） |
| 测试 | **487 passed**（第三阶段 73 → 83；新增 10 个复审专项用例） |
| 第一阶段回归 | 32 / 160 / 192 / 0 超容差 / fixture 残留 0 —— 不变 |
| 复审要求的 7 个验收用例 | 全部实现并通过（见 B.1–B.4 的复现列表） |

## B.7 修复前后对照（本轮）

| 场景 | 修复前 | 修复后 |
|---|---|---|
| 记录完整、目标未填 revision | 可能 `exact_match` ❌ | `target_identity_incomplete` → 不得 exact_match ✅ |
| 自定义策略放宽到 comparable_reference | 可能 `observed_pass` ❌ | 一律 `not_evaluable`（门禁不可放宽）✅ |
| 匹配结论指向另一条 run | 不校验 ❌ | `match_result_run_mismatch` ✅ |
| 同并发两次观测，一次失败 | 总次数够即认定稳定 ❌ | 该点整体不计入；失败观测写入 notes ✅ |
| 精确查询 target_run 可信度不足 | 绕过校验直接返回 ❌ | 先校验，`trust_level_not_allowed` ✅ |
| 聚合时某条缺 TTFT P99 | 用剩余记录凑出值 ❌ | 该指标置空 + 明确标记 ✅ |
| verify 脚本关键行未命中 | 静默通过 ❌ | 断言式，直接失败 ✅ |
