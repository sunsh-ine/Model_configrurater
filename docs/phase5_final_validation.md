# 第五阶段验证报告（M35–M42）

**基线**：`92944e3`（S 阶段封板）
**阶段定位**：真实数据接入、性能验证与方案交付 MVP ——
把**真实数据流**接到现有工作流上，而不是再造一套系统。
**文档状态**：第一轮（M35 + M36）已完成；M37–M42 待执行。

---

## 一、总体目标与约束

依托现有 `PlanRequest → evaluate_plan() → PlanResult` 主工作流，
让系统可以接收真实模型、GPU、Benchmark 与成本数据，生成可解释、可复核的部署评估结论。

继续坚持 Keep It Simple：不重写四阶段计算引擎；复用现有 SQLite / Importer /
BenchmarkRun / SLA / TCO；数据、适配器与业务判断各自保持单一权威；
不新增插件框架、微服务或分布式任务系统；用户仍以一份 YAML + 一条主命令完成评估。

**两条独立验收线**（不可混为一谈）：

| 验收线 | 依据 | 当前状态 |
|---|---|---|
| 工程验收 | 接口、导入、质量检查、报告与回归，可用明确标记的测试数据完成 | 本轮已完成 M35/M36 部分 |
| 真实性能验收 | 必须来自目标模型 / GPU / 配置 / Workload 的真实运行记录 | **未验证**（无真实压测数据） |
| 商业报价 | 独立门禁 | **不可发布**（未接入可信审批系统） |

---

## 二、M35：基线修正与交付口径冻结 ✅

### 2.1 修复的问题

复审发现统一报告的"全周期成本最低"推荐**没有复用**第四阶段已有的成本可比性规则：
它直接从"成本完整"的候选中取最小金额，既没核对币种、买断/租赁口径与计算周期，
也没排除全周期金额为 `None` 的情况 —— 而 `x or Decimal(0)` 会把**缺失**当成最便宜。

### 2.2 修复方式

把可比性判据抽成**唯一权威实现** `decision/comparison.cost_comparability()`，
输入是 `CostProfile`（币种 / 口径 / 周期 / 完整性 / 全周期金额）：
第四阶段比较器由 `TcoResult` 归约，统一入口由 `PlanCandidate` 归约 ——
**同一规则不再有第二份实现**。

判定顺序（刻意如此）：

1. 没有成本结果 / 成本不完整 —— 永远不可比（先于"没有参照"判断）；
2. 没有参照方案 —— 不给任何"最低成本"结论；
3. 币种 / 买断租赁口径 / 计算年限任一不同 —— 不可比，并给出原因码；
4. **全周期金额缺失 —— 不可比**（不得按 0 排序）。

### 2.3 交付口径：技术资格 vs 生产发布授权

报告与 CLI 现在分开陈述两件不同的事：

* **技术资格（四维门禁）**：成本/SLA/商务确认/审批四项证据的**计算结果**；
* **生产发布授权**：**未授权** —— 未接入可信审批系统，
  审批记录只做持久化、不校验审批人身份与权限，**不得用于生产环境的报价自动发布**。

### 2.4 验收对照

| 计划 §M35 验收 | 结果 |
|---|---|
| 复用第四阶段成本可比性判断 | ✅ 唯一权威 `cost_comparability()`，两处调用 |
| 不跨币种/口径/周期比较最低成本 | ✅ `test_m35_usd_candidate_cannot_win_lowest_cost`、`test_m35_leased_candidate_cannot_win_lowest_cost`、`test_m35_different_horizon_cannot_win_lowest_cost` |
| 全周期成本缺失不按 0 排序 | ✅ `test_m35_missing_total_is_not_zero`、`test_m35_missing_total_cannot_win_lowest_cost` |
| 报告区分技术资格与生产发布授权 | ✅ `test_m35_report_separates_technical_eligibility_from_production_authorization` |
| 冻结核心数据契约 | ✅ `docs/real_data_preparation.md` §一 |
| 原有回归不变 | ✅ 709 passed / 54 步 / 第一阶段 160-192-0 不变 |

---

## 三、M36：真实数据准备与质量检查 ✅

### 3.1 数据准备规范

`docs/real_data_preparation.md`：逐域列出"要准备什么、从哪来、什么算合格"，
覆盖 `ModelSpec` / `HardwareSpec` / `BenchmarkRun` / `CostCatalogItem` + `BillingRule` /
治理证据，并附**常见拒绝原因速查表**（现象 → 原因 → 处理）。

**不新建平行数据格式**：数据只走既有 `import-*` 通道。

### 3.2 数据质量分级

新增只读能力 `services.data_quality_report()` 与 CLI：

```bash
python -m llm_capacity_pricing.cli data-quality --db data/local.db --out quality.json
```

五类状态：`complete` / `unverified` / `missing_fields` / `not_applicable` / `synthetic`。

**默认不乐观**：

* `production_grade` 只在 `status == complete` 时为真（schema 层强制）；
* 只有 `complete` 的性能实测才标 `usable_for = [sla_proof, capacity_analysis]`；
* 合成数据一律 `usable_for = [algorithm_validation]`；
* **审批记录恒为 `production_grade = False`**，并写明"审批人身份与权限未经校验"；
* 报告**只读**：不修改任何记录的信任等级，也不做升级。

报告末尾的 `gaps` 是可执行的补数据指引（含 `import-performance`、
`import-cost-catalog`、`nvidia-smi` 等具体动作）。

### 3.3 验收对照

| 计划 §M36 验收 | 结果 |
|---|---|
| 真实数据可沿现有 Adapter 导入 | ✅ `test_m36_real_adapter_path_still_works` |
| 不可信/不完整记录不会自动获得生产级状态 | ✅ `test_m36_unverified_source_is_not_production_grade`、`test_m36_incomplete_identity_is_missing_fields`、`test_m36_cost_and_billing_rules_are_not_silently_upgraded` |
| 区分五种质量状态 | ✅ `test_m36_report_covers_five_statuses` 等 |
| 不新建平行数据格式 | ✅ 复用既有 Schema；质量报告只是只读输出 |
| 报告不改动信任等级 | ✅ `test_m36_report_is_read_only` |
| 指引可执行 | ✅ `test_m36_gaps_are_actionable` |

---

## 四、M37：标准化压测实验清单 ✅

### 4.1 交付

`perf/experiment_planner.py` + `schemas/experiment.py` + CLI：

```bash
python -m llm_capacity_pricing.cli plan-benchmarks \
  --model qwen3-32b --hardware nvidia-h100-sxm-80gb --server hgx-h100-8gpu \
  --framework-version <版本> --model-revision <版本> --precision bf16 \
  --out data/exports/benchmark_experiments.json \
  --metadata-dir data/exports/metadata --db data/local.db
```

每个实验点包含：部署身份（模型版本/精度/GPU SKU/卡数/TP/DP/框架版本）、负载条件
（输入输出长度/并发/到达方式/数据集/**显式的前缀缓存策略**）、证据强度要求
（warmup、时长、重复次数、必需指标）、**可导入的 metadata 模板**与压测命令骨架。

首轮矩阵沿用计划 §三（1K/256 @1,8；8K/256 @1,8,16,32；32K/256 @1,8,16），
并自动继承容量分析的可持续性门槛（重复次数 ≥ 2、时长 ≥ 60s）。

### 4.2 一处刻意的"不给"

复审要求"提供可执行命令示例"。实现时把**服务端启动命令**去掉了 ——
本仓库归档的官方文档是**压测客户端**文档，服务端启动参数（`--tensor-parallel-size`
等）在其中查不到；既然是靠"参数必须可在归档文档中查到"来防编造，就不该为服务端破例。

现在只给出 `server_args`（必须对齐的参数：TP/DP/max_model_len/前缀缓存策略）
并在 notes 里说明"参数名以所用框架版本文档为准"。**测试会逐个校验压测命令中的
每个 `--flag` 都真实存在于归档文档**，所以这条约束不会随时间漂移。

### 4.3 验收对照

| 计划 §M37 验收 | 结果 |
|---|---|
| 同一实验记录可从采集结果追溯到具体部署配置 | ✅ `test_m37_metadata_template_reaches_exact_match`（照模板造的数据 → 与该部署配置匹配到 `exact_match`） |
| 缺失关键条件的测试不会被判为 exact_match | ✅ `test_m37_missing_identity_conditions_are_declared` |
| 清单含参数、不含性能数值 | ✅ `test_m37_plan_contains_no_performance_numbers` |
| 命令可执行且不编造 | ✅ `test_m37_command_flags_come_from_archived_docs` |
| 覆盖矩阵与重复要求 | ✅ `test_m37_plan_covers_grid_and_feasible_candidates`、`test_m37_repeats_and_duration_meet_sustainability_thresholds` |

---

## 五、M38：真实 Benchmark 导入与一致性验证 ✅

### 5.1 交付

`perf/import_consistency.py` + CLI：

```bash
python -m llm_capacity_pricing.cli check-import --db data/local.db --out consistency.json
```

逐条检查（只报告，不改数据）：客户端/服务端指标混用、分位口径是否声明、
配置并发与实际并发是否自洽、到达方式与限速是否冲突、请求成功/失败/超时账目、
错误率与计数是否一致、**可计费缓存比例是否被照抄物理命中率**、
同分组身份是否一致、批次内是否有重复 run_id、重复观测会如何聚合。

**严重度按关键性分级**（避免"一刀切全是 blocker"）：SLA 关键分位
（TTFT/TPOT P99）缺口径是 `blocker`；ITL/E2E 缺口径只 `warning`；
只有 Mean、没有任何 P99 是 `warning` + 一条明确的"不得据此推算 P99"提示。

### 5.2 导入幂等

`services.verify_reimport_is_stable()`：先导入一次作为基线，再重试导入，
比较 run 数、run_id 集合与吞吐指标 —— 三者必须完全一致。

### 5.3 验收对照

| 计划 §M38 验收 | 结果 |
|---|---|
| 相同数据重复导入结果稳定 | ✅ `test_m38_reimport_is_stable` |
| 字段/指标缺失时输出可解释原因 | ✅ `test_m38_request_accounting_and_error_rate`、`test_m38_p99_without_definition_is_blocking` |
| 不伪造性能值 | ✅ `test_m38_mean_only_never_infers_p99`（断言 P99 仍为 `None`） |
| 客户端/服务端不混用 | ✅ `test_m38_server_only_metrics_is_blocking` |
| 配置并发与实际并发分开 | ✅ `test_m38_concurrency_semantics` |
| 缓存口径不混用 | ✅ `test_m38_cache_rate_must_not_be_copied_from_physical` |
| 重复 Run 保守聚合 | ✅ `test_m38_batch_checks`（`repeat_runs_will_be_conservatively_aggregated`） |

---

## 六、M39：现场性能与 SLA 可信度验证 ✅

### 6.1 交付

`perf/site_validation.py` + CLI `validate-site`。把三个既有能力串成一次可解释验证：
容量分析、配置/负载匹配、SLA 逐子项判定；**不新增判定规则**，
所有结论都来自既有实现，这里只组织输入、汇总结论，并把"为什么不能评价"写成清单。

输出包含：各并发点证据（含时长/失败率/样本是否满足可持续门槛）、已观测峰值吞吐与并发、
可持续容量（未认定时给出原因）、匹配等级与原因、**负载与统计口径逐项对齐情况**、
SLA 逐子项状态、以及不可评价原因。

### 6.2 收紧的一处门禁

SLA 评估新增 `metric_side_not_client` 门禁：服务端时延与客户端时延不可互换，
SLA 是面向用户体验的承诺，只能用客户端口径支撑。这是**追加**的收紧
（`metric_side` 默认即 `client`），既有数据与既有测试全部不受影响。

### 6.3 验收对照

| 计划 §M39 验收 | 结果 |
|---|---|
| 同模型同配置、不同长度/并发不能证明目标 SLA | ✅ `test_m39_different_length_cannot_prove_target_sla`、`test_m39_different_concurrency_cannot_prove_target_sla` |
| 真实达标与真实不达标都正确报告 | ✅ `test_m39_clean_measured_group_reports_curve_and_subchecks`（`observed_pass`）、`test_m39_real_fail_is_reported_correctly`（`observed_fail`） |
| Synthetic 仍不可替代真实依据 | ✅ `test_m39_synthetic_data_cannot_substitute_real_evidence` |
| 输出曲线/峰值/可持续容量/逐子项/对齐情况 | ✅ `test_m39_clean_measured_group_reports_curve_and_subchecks` |
| 可持续容量需重复观测 | ✅ `test_m39_sustainability_requires_repeats_and_duration` |

---

## 七、M40：真实成本与方案比较闭环 ✅

### 7.1 交付

* `PlanResult.summary.cost_comparability`：结构化输出**参照方案、可比较候选、
  不可比较候选及其原因码**（币种/口径/周期/金额缺失），与报告文字共用同一套判据；
* 报告的 **3.1 成本可比性**小节把不可比较原因翻译成用户语言；
* **一条硬性提醒**：当"成本最低"的候选没有可用性能实测时，报告明确写出
  "**不得**把它称为最优部署 —— 成本低与性能达标是两个独立维度，缺性能证据的方案需要先补测"。

### 7.2 验收对照

| 计划 §M40 验收 | 结果 |
|---|---|
| 输出可比较与不可比较候选及原因 | ✅ `test_m40_summary_exposes_comparable_and_not_comparable_candidates` |
| 缺性能数据的低成本方案不得称为最优部署 | ✅ `test_m40_cheapest_without_performance_evidence_is_not_called_optimal`（含对照：有实测时不加该提醒） |
| SLA 与成本是独立维度 | ✅ `test_m40_measured_cheapest_has_no_warning` |
| 允许情景估算，未确认价格不得宣称正式价格 | ✅ `test_m40_unconfirmed_price_is_never_a_formal_price` |
| 报告呈现可比性 | ✅ `test_m40_report_renders_comparability_section` |

---

## 八、M41–M42：待执行

| 里程碑 | 目标 | 状态 |
|---|---|---|
| M41 | 降低用户操作成本（配置报错定位、报告层次、补测提示） | ⬜ 未开始 |
| M39 | 现场性能与 SLA 可信度验证（容量曲线、稳定容量、逐子项判定） | ⬜ 未开始 |
| M40 | 真实成本与方案比较闭环（可比/不可比候选并列） | ⬜ 未开始 |
| M41 | 降低用户操作成本（配置报错定位、报告层次、补测提示） | ⬜ 未开始 |
| M42 | 端到端验收与第五阶段封板 | ⬜ 未开始 |

---

## 九、回归记录

| 轮次 | 全量测试 | 全链路步骤 | 第一阶段回归 | 反向场景 |
|---|---|---|---|---|
| S 阶段封板（92944e3） | 683 passed | 53 / 53 | 160 / 192 / 0 | 17 / 17 |
| M35 + M36 | 709 passed | 54 / 54 | 160 / 192 / 0 | 17 / 17 |
| M35–M38 | 727 passed | 56 / 56 | 160 / 192 / 0 | 17 / 17 |
| M35–M40 | **740 passed** | **57 / 57** | **160 / 192 / 0** | **17 / 17** |

---

## 十、未闭环事项（与前几阶段一致）

1. **真实性能验收未验证** —— 缺少目标模型 / GPU / 配置的真实压测记录；
2. **商业报价不可发布** —— 未接入可信审批系统，且无已确认真实价格；
3. **任务书 V4.0 正文未提供**，因此始终未做原文逐条对照；
4. **仓库为 Public，历史提交中的内部定价数据风险未闭环** —— 不重写 Git 历史。
