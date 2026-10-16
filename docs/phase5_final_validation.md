# 第五阶段验证报告（M35–M42）

**基线**：`92944e3`（S 阶段封板）
**阶段定位**：真实数据接入、性能验证与方案交付 MVP ——
把**真实数据流**接到现有工作流上，而不是再造一套系统。
**文档状态**：M35–M42 全部完成（封板），并含封板前的三项证据加固。

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

## 八、M41：降低用户操作成本 ✅

* **配置报错定位**：校验失败时输出"文件 + 字段路径 + 修正建议"，
  并对拼错的字段名给出"是不是想写 X"的提示；不再抛 Pydantic 原始堆栈。
  例：

  ```
  配置 scenario.yaml 校验失败（3 处）：
    - cost.template_id: 该字段必填，请补上
    - cost.template: 不认识这个字段名，请改用正确字段名（是不是想写 `template_id`？）
    - sla_requirement: 不认识这个字段名，请改用正确字段名（是不是想写 `sla`？）
  ```

* **补测提示**：只要库里没有实测，无论是否配置了 SLA，报告都会给出可执行指引
  （`plan-benchmarks` 生成清单 → 采集 → `import-performance` 导入）。
* 报告层次（结论 → 候选 → 缺口 → 附录）与"不要求用户理解表结构/Run ID"
  在 S5 已完成，本轮未改动。

**明确不做**：本阶段**没有**增加本地只读单页界面 —— 计划里它是"CLI 交互成本仍然较高时"
的可选项，而当前 `plan --config` 已是单命令、单配置、单报告，交互成本已足够低；
引入任何前端都会与"不新增独立前后端架构"的约束冲突。

| 计划 §M41 验收 | 结果 |
|---|---|
| 配置字段错误指出位置与修正建议 | ✅ `test_m42_scenario_d_bad_config_gives_field_path_and_hint` |
| 自动提示需要补测的配置与缺失数据 | ✅ `test_m42_scenario_a_resource_only` |
| 不要求理解表结构/Run ID | ✅ S5 已验收，本轮未回退 |
| 保留专业用户的高级命令 | ✅ 历史命令全部保留（S5 有逐一检查的用例） |

---

## 九、M42：端到端验收与封板 ✅

### 9.1 四个端到端场景

| 场景 | 断言 | 用例 |
|---|---|---|
| **A** 只有资源数据 | 静态可用；性能与成本明确留缺口；缺口给出可执行的补数据指引 | `test_m42_scenario_a_resource_only` |
| **B** 导入实测 | 导入一致性干净；匹配到 `exact_match`；容量曲线/峰值/可持续性/SLA 逐子项齐备；统一入口同样得到性能结论 | `test_m42_scenario_b_imported_measurement_drives_match_and_sla` |
| **C** Synthetic 数据 | 计算与报告可运行；但 `observed_pass` 为零、不可发布；数据质量报告标注 `synthetic`；现场验证给出"不可替代真实依据" | `test_m42_scenario_c_synthetic_runs_but_never_impersonates` |
| **D** 不匹配/过期/变更/缺失 | 负载身份变更使旧证据失效；未知成本模板给明确阻断；非法配置在计算前被拒并指出字段 | `test_m42_scenario_d_*`（3 个用例） |

> **场景 B 的证据强度**：用的是**真实格式**（vLLM `bench serve` JSON）的机制夹具，
> 证明的是**工程链路**可用。**真实性能验收仍需目标模型/GPU 的现场记录**，
> 当前状态为"未验证"（见 §十）。

### 9.2 封板硬性要求复核

| 要求 | 结果 |
|---|---|
| S 阶段 683 项测试无回退 | ✅ 全量 **749 passed**（含 S 阶段全部用例） |
| 原全链路验证全部通过 | ✅ **58 / 58**（S 阶段 53 步 → 新增 data-quality / check-import / plan-benchmarks / validate-site / M42 场景共 5 步） |
| 第一阶段 160 / 192 / 0 不变 | ✅ 不变 |
| CLI / Agent / 应用层核心结果一致 | ✅ `test_m42_phase5_entry_points_are_consistent` |
| 新增模型/GPU/Benchmark 只改数据或局部适配 | ✅ `test_m42_phase5_pipeline_needs_no_code_change_for_new_data`（+ S4 契约测试） |
| 没有引入平行计算链路 | ✅ `test_m42_no_parallel_computation_chain_in_phase5_modules`（规划器与一致性校验不触碰 TCO/SLA；现场验证只**委托**既有实现） |
| 安全事项仍独立跟踪 | ✅ 见 §十 |

### 9.3 交付物清单

| 计划 §六 交付物 | 位置 |
|---|---|
| 真实数据准备规范 | `docs/real_data_preparation.md` |
| Benchmark 实验任务与元数据模板 | `plan-benchmarks`（`perf/experiment_planner.py`） |
| 真实结果导入与一致性校验 | `check-import`（`perf/import_consistency.py`） |
| 性能曲线与 SLA 评价 | `validate-site`（`perf/site_validation.py`） |
| 资源/性能/TCO/预算综合报告 | `plan`（`workflow.evaluate_plan` + `reporting/plan_report.py`） |
| 新旧入口回归与扩展契约验证 | `tests/test_s_phase_*` + `tests/test_phase5_m42_end_to_end.py` |
| 第五阶段验证报告 | 本文 |

---

## 十、封板前的证据加固（评审三项反向用例）✅

复审在封板前发现三处"新输出可能被当成生产级证据"的漏洞，已全部修复并配反向用例。

### 10.1 P0-1 重复压测会被同一个 Run ID 覆盖

**问题**：`_metadata_template()` 用 `record_id = point_id`，同一实验点的多次重复运行
共用同一个 ID；而 `upsert_benchmark_runs()` 按 run_id `merge`，
于是第二次、第三次独立执行会**覆盖**第一次 —— 最终只剩一条 Run，
拿不到"重复观测"的证据。

**修复**：

* **区分实验点与执行**：metadata 模板改为**每次独立执行一份**（`metadata_templates`），
  各自带 `experiment_point_id`（实验配置）、`repeat_index`（第几次）与独立 `record_id`；
* **Run 身份纳入配置指纹**：`record_id = <point_id>-<config_fingerprint>-r<i>`，
  指纹覆盖模型版本 / 框架 / 框架版本 / 权重精度 / KV 精度 / **前缀缓存策略** ——
  不同实验配置不会复用同一个 ID；
* schema 校验"模板数量必须等于 repeats 且 record_id 互不相同"。

**反向用例**：`test_hardening_independent_runs_are_kept_and_retry_is_idempotent`
（两次独立执行 → 2 条 Run；重试同一执行 → 不新增）、
`test_hardening_different_experiment_configs_do_not_share_run_ids`。

### 10.2 P0-2 实验模板默认声明"已验证实测"

**问题**：模板在生成时就写死 `performance_source_kind: internal_measurement` +
`verification_status: verified`；`run_from_adapter()` 据此把可信等级映射为
`measured_internal` —— 一份**计划**模板只要配上格式完整的数据，
就能不经验证获得生产级可信度。

**修复**（复用既有质量与信任机制，未新建审批系统）：

1. 模板默认 `verification_status: unverified_parse`；
2. `run_from_adapter()` 新增降级规则：**自我声明的内部实测**
   （`internal_measurement`）在 `verification_status != "verified"` 时，
   可信等级降为 `unknown`，并写入 `source_verification_required:<kind>/<status>` 标记；
   刻意**不**波及 `official_benchmark` —— 厂商发布是另一条溯源路径，
   其字段完整性由 M9 适用性门禁与 SLA 门禁分别把关；
3. 新增显式核验步骤 `attest-run --run-id ... --confirmed-by ... --raw-sha256 ...`：
   只升级**这一条** Run，核验人/时间/哈希写入 `quality_flags`；
   哈希与导入时记录不一致则**拒绝**核验；
   synthetic 等不可核验来源直接拒绝。
4. `data-quality` 对未核验记录给出可执行理由（点名 `attest-run` 命令）。

**反向用例**：`test_hardening_unverified_template_import_is_not_production_evidence`、
`test_hardening_attest_requires_matching_raw_hash_and_upgrades_once`、
`test_hardening_attest_rejects_non_attestable_sources`；
全链路第 55 步 `scripts/check_attest_flow.py`（9 项检查）。

### 10.3 P1 现场验证的 `can_claim_sla` 不够严格

**问题**：只判断 `sla_status in ("observed_pass","observed_fail")`。
第三阶段允许显式 `allow_synthetic=True` 的算法验证路径，此时 synthetic 数据也能产出
`observed_pass` —— 报告里会给出一个误导性的"可对外声称"布尔值。

**修复**：`can_claim_sla` 现在同时要求：`exact_match` 严格匹配、
可信等级属于 `PRODUCTION_TRUST_LEVELS`（复用同一份权威定义）、且不含 synthetic 数据。
`data_trust` 也改为：混合可信等级时保持 `None`（不再取"最乐观的那个"）。

**反向用例**：`test_hardening_algorithm_validation_observed_pass_cannot_claim_sla`
（算法验证确实拿到 `observed_pass`，但 `can_claim_sla` 为 False）、
`test_hardening_can_claim_sla_requires_all_three_conditions`（三条件逐项对照）。

### 10.4 两项非阻塞改进

* **数据质量复用一致性校验**：一条 Run 被声明为 `production_grade` 之前，
  会先跑 `check_run_consistency()`；有 blocker 即降为"仅参考"，并逐条列出原因码
  （不再有第二套质量规则）。用例：
  `test_hardening_data_quality_declines_production_grade_on_consistency_blocker`。
* **实测时长与计划要求比对**：现场验证的每个并发点新增
  `required_duration_s`（与实验清单同源）与 `duration_meets_requirement`；
  有实测低于门槛时，结论里明确写出"不满足可持续容量的证据门槛"。
  清单里的 `duration_s` 是**计划值**，比对的是**实测值**。

### 10.5 加固后的回归

| 项 | 结果 |
|---|---|
| 全量测试 | **757 passed**（新增 8 个加固用例） |
| 全链路 | **59 / 59**（新增 attest-run 流程断言，9 项检查） |
| 第一阶段回归 | 160 / 192 / 0 超容差不变 |
| 报价反向场景 | 17 / 17 |

---

## 十一、封板结论

M35–M42 全部完成。**工程验收通过**：接口、数据导入、质量检查、报告与回归
都有明确标记的测试数据支撑。

三条独立验收线的最终状态：

| 验收线 | 状态 | 依据 |
|---|---|---|
| **工程验收** | ✅ 通过 | 749 passed / 58 步全链路 / 第一阶段 160-192-0 不变 |
| **真实性能验收** | ⛔ **未验证** | 无目标模型 / GPU / 配置的现场运行记录；机制夹具只证明链路可用 |
| **商业报价** | ⛔ **不可发布** | 未接入可信审批系统；无已确认真实价格 |

**第五阶段之后**：真实 Benchmark 采集仍是唯一能把"未验证"变成"已验证"的路径；
拿到现场数据后按 `docs/real_data_preparation.md` 与 `plan-benchmarks` 的清单导入即可，
**不需要改代码**。
| M39 | 现场性能与 SLA 可信度验证（容量曲线、稳定容量、逐子项判定） | ⬜ 未开始 |
| M40 | 真实成本与方案比较闭环（可比/不可比候选并列） | ⬜ 未开始 |
| M41 | 降低用户操作成本（配置报错定位、报告层次、补测提示） | ⬜ 未开始 |
| M42 | 端到端验收与第五阶段封板 | ⬜ 未开始 |

---

## 十二、回归记录

| 轮次 | 全量测试 | 全链路步骤 | 第一阶段回归 | 反向场景 |
|---|---|---|---|---|
| S 阶段封板（92944e3） | 683 passed | 53 / 53 | 160 / 192 / 0 | 17 / 17 |
| M35 + M36 | 709 passed | 54 / 54 | 160 / 192 / 0 | 17 / 17 |
| M35–M38 | 727 passed | 56 / 56 | 160 / 192 / 0 | 17 / 17 |
| M35–M40 | 740 passed | 57 / 57 | 160 / 192 / 0 | 17 / 17 |
| M35–M42（封板） | 749 passed | 58 / 58 | 160 / 192 / 0 | 17 / 17 |
| 封板前证据加固 | **757 passed** | **59 / 59** | **160 / 192 / 0** | **17 / 17** |

---

## 十三、未闭环事项（与前几阶段一致）

1. **真实性能验收未验证** —— 缺少目标模型 / GPU / 配置的真实压测记录；
2. **商业报价不可发布** —— 未接入可信审批系统，且无已确认真实价格；
3. **任务书 V4.0 正文未提供**，因此始终未做原文逐条对照；
4. **仓库为 Public，历史提交中的内部定价数据风险未闭环** —— 不重写 Git 历史。
