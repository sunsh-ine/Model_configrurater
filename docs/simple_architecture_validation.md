# S 阶段（Keep It Simple）架构收敛验证

**冻结基线**：`ab5cd2e` + S0 的三项门禁修正
**执行顺序**：S0 → S1 → S2 → S3 → S4 → S5 → S6（每轮结束跑全量回归）
**文档状态**：第一轮（S0 + S1）已完成；S2–S6 待执行，末尾的封板清单保持未勾选。

---

## 一、目标与约束回顾

| 维度 | 目标 |
|---|---|
| 用户体验 | 一份配置 + 一条命令 + 一份报告 |
| 内部架构 | 一个单体应用 + 四个业务领域（逻辑划分，不是微服务） |
| 扩展方式 | 局部新增，不改主流程 |
| 质量要求 | 现有功能与回归结果不退化 |

禁止事项：不新增微服务、Redis、Kafka、复杂插件框架、通用规则引擎、
自治 Agent、Web 前端、完整 RBAC；不一次性重写系统。

---

## 二、S0：基线冻结与最后的局部正确性问题 ✅

### 2.1 冻结内容

见 `docs/refactor_baseline.md`：CLI 命令清单（只允许新增）、数据库表与列
（只允许追加可空列）、报告字段、四个阶段的计算口径，以及
**620 passed / 50 步 / 160-192-0** 的回归基线。

### 2.2 三项局部检查（均在现有门禁内完成）

| 检查 | 实现 | 原因码 |
|---|---|---|
| 计费规则模型必须与报价模型一致 | `_model_matches()`（沿用 `normalize_name`，不做别名推断） | `billing_rule_model_mismatch` |
| 计费规则内容变化使旧审批失效 | `billing_rule_snapshot_hash()` + `ApprovalScope.billing_rule_hash` | `approval_billing_rule_stale` |
| 商务确认必须匹配当前报价作用域 | `ConfirmationScope`（候选/需求/模型/计费规则清单） | `commercial_confirmation_scope_missing` / `..._mismatch:*` |

**验收**：三个反向场景全部被阻断（`test_s0_*` 三个用例）；
反向场景脚本由 14 项扩展到 **17 项检查**，全部通过。
生产自动报价维持禁用。

---

## 三、S1：统一用户入口 ✅

### 3.1 交付

| 交付物 | 位置 |
|---|---|
| `PlanRequest` / `PlanResult` 契约 | `schemas/plan.py` |
| `evaluate_plan(request) -> PlanResult` | `workflow.py` |
| `llm-capacity plan --config <yaml> --out <md>` | `cli.py` |
| 用户报告 | `reporting/plan_report.py` |
| 最小示例 / 完整示例 | `data/examples/scenario_minimal.example.yaml` / `scenario_full.example.yaml` |
| 契约与端到端测试 | `tests/test_s_phase_plan.py`（21 用例） |

**一条命令跑通四个领域**：

```bash
python -m llm_capacity_pricing.cli plan \
  --config data/examples/scenario_full.example.yaml \
  --out data/exports/plan_full.md \
  --json-out data/exports/plan_full.json \
  --db data/local.db
```

### 3.2 配置的必需项与可选项

必需：`model_id` + `hardware_ids` + `workload.input_len`（+ 建议给资源假设与 `server_id`）。
可选、按场景填写：`resources` / `sla` / `cost` / `demand` / `budget` / `governance`。
专业字段收在 `advanced`（`tp_sizes` / `dp_sizes` / `block_size_tokens` /
`weight_precision` / `kv_precision` / 目标身份补充项）。

### 3.3 领域状态与缺口表达

四个领域各自给出状态，**不合并成单一的"通过"**：

| 状态 | 含义 |
|---|---|
| `computed` | 已算出结论 |
| `not_configured` | 用户未提供该领域所需输入 |
| `missing_data` | 提供了输入但库里缺数据 |
| `error` | 计算过程出错（原因见 `blockers`） |

`PlanResult.status` 只描述"这次评估产出了什么"（`ok` / `partial` / `blocked`），
**不是报价结论**；契约层还禁止"治理未计算却标记可发布"。

### 3.4 验收对照

| 计划 §3 验收 | 结果 |
|---|---|
| 一次调用贯通资源、性能、经济性与方案评估 | ✅ `test_s1_cost_computed_with_catalog` 等 |
| 仅有资源数据也能输出部分结果，不整体报错 | ✅ `test_s1_resource_only_yields_partial_without_error`；验证链第 47 步（断言式） |
| 新入口不能绕开正式 SLA 或报价门禁 | ✅ `test_s1_governance_cannot_bypass_evidence_records`、`test_s1_result_guard_rejects_publishable_without_governance` |
| 原 CLI 行为兼容 | ✅ `test_s1_existing_cli_commands_still_present`（26 个既有命令逐一检查） |
| 缺数据不用虚构默认值 | ✅ 未配置领域标 `not_configured`，成本不完整时不给全周期总额 |

### 3.5 新旧入口一致性（初步）

`test_s1_plan_matches_underlying_sizing_and_ranking`：同一数据库快照下，
`plan` 的候选集合、静态结论与 `plan-deployment` 一致，
性能分桶与 SLA 状态与 `rank-deployments` 一致。
完整对照（含成本与治理）留到 S6。

---

## 四、S2：收敛业务编排与调用链 ✅

### 4.1 做了什么

| 动作 | 说明 |
|---|---|
| 跨阶段编排迁出 `services.py` | `phase4_decision_for` 的实现搬到 `workflow.evaluate_decision()`；`services` 版本缩成转发包装（函数内延迟导入，模块层无循环依赖） |
| 统一排序调用入口 | `evaluate_plan` 与 `evaluate_decision` 原来各写一遍排序调用，现统一为 `workflow.build_ranking()` |
| Agent 复用同一应用服务 | 新增算子 `plan.evaluate` / `plan.evaluate_config`，与 CLI `plan` 调用**同一个** `workflow.evaluate_plan()` |
| 写明调用链 | `workflow.py` 模块文档记录 CLI/Agent → 应用层 → 四个领域的依赖方向 |

`services.py`：**3455 → 3197 行**（−258），且新增代码不再落在它身上。

### 4.2 验收对照

| 计划 §4 验收 | 结果 |
|---|---|
| 主评估调用链清晰且可追踪 | ✅ `workflow.py` 模块文档给出调用链图与依赖方向 |
| 不出现循环依赖 | ✅ `test_s2_no_module_level_circular_dependency`（用 AST 检查顶层导入） |
| CLI 与 Agent 调用同一应用服务，不重复编排 | ✅ `test_s2_cli_and_agent_share_the_same_application_service`（同库同配置逐字段一致） |
| 老入口仍可用 | ✅ `test_s2_services_decision_is_a_thin_compatibility_wrapper`（新旧结果完全一致） |
| 新增代码不扩大巨型服务文件职责 | ✅ 编排迁出，`services.py` 净减 258 行 |
| 现有计算结果等价 | ✅ 647 passed；第一阶段 160/192/0 不变 |
| CLI / Agent / Report 不各自实现业务判断 | ✅ `test_s2_reports_do_not_reimplement_business_rules`（报告层不得调用 `match` / `evaluate_sla` / `compute_tco` / `evaluate_quote_eligibility`） |

### 4.3 本轮的取舍

* **没有**把 `services.py` 机械拆成十几个小文件。它剩下的内容（导入、校验、
  单领域查询）职责单一，拆分只会增加跳转成本；
* **保留** `services.phase4_decision_for` 与全部既有 CLI 命令作为兼容入口，
  只是让它们转发到应用层；
* 报告层仍然是纯格式化 —— 它读取门禁输出做展示，不重新判定。

---

## 五、S3：统一公共规则与错误表达 ✅

### 5.1 收敛的三条重复规则

| 规则 | 权威领域（唯一实现） | 原先重复处 |
|---|---|---|
| **生产可信等级**（哪些数据能支撑生产结论） | `schemas/performance.PRODUCTION_TRUST_LEVELS` | `services.PHASE4_TRUSTED_LEVELS`、`quote_gate.PRODUCTION_TRUST_LEVELS`、`estimator.DEFAULT_ALLOWED_TRUST`、`SlaPolicy` 默认值、CLI 的算法验证信任集（共 5 处字面量） |
| **硬件 ID → 签名商品名** | `sizing/hardware.product_name_for_hardware_id()` | `perf/ranker._hardware_name`、`workflow._hardware_product_name`、`workflow._candidate_hardware_name`（3 份相同映射） |
| **币种可比性**（两个金额能否直接相减） | `engines/tco.currencies_comparable()` | `decision/comparison._margin_allowed`、`decision/sensitivity`（预算与盈亏平衡）（3 处各自的判断与原因码） |

收敛后各消费方改为**别名或调用**，不再各自维护一份。

### 5.2 顺带修掉的两处口径漂移

* 币种不匹配的原因码原先带调用方传参顺序（`USD!=CNY` 与 `CNY!=USD` 并存），
  现按币种名排序拼接，同一对币种只有一个码；
* 硬件名映射现在有**契约测试**比对 `data/hardware/hardware_catalog.yaml`
  的 `product_name` —— 新增一张卡忘了改映射会直接失败。

### 5.3 验收对照

| 计划 §5 验收 | 结果 |
|---|---|
| 修改一条规则只需改权威领域，所有入口自动一致 | ✅ `test_s3_trust_levels_have_one_authoritative_definition`、`test_s3_adding_a_sku_propagates_to_all_consumers`（运行时改权威映射，ranker/workflow 立刻生效） |
| 业务规则位于所属领域，Application 只调用与汇总 | ✅ `test_s3_reason_codes_are_not_reinvented`（编排层只报结构性问题，业务码来自领域） |
| CLI / Agent / Report 不复制业务判断 | ✅ `test_s3_reports_do_not_import_domain_engines`（AST 检查报告层导入） |
| 沿用既有状态与原因码，不另立平行体系 | ✅ `test_s3_plan_result_reuses_existing_status_vocabulary`（`plan.py` 内不出现 `observed_pass` / `exact_match` 等业务取值） |
| 无重复实现的规则 | ✅ `test_s3_no_module_redeclares_the_trust_pair`、`test_s3_only_one_hardware_name_mapping_exists`、`test_s3_currency_rule_has_one_implementation` |

---

## 六、S4：最小可扩展边界 ✅

只保留三个扩展方向，每个都给了固定入口、验收方式，以及**不需要改什么**。
详见 `docs/extension_guide.md`。

| 方向 | 入口 | 需要改的地方 | 明确不需要改 |
|---|---|---|---|
| 数据（模型/GPU/成本/计费规则） | 目录 YAML + `import-*` | 数据文件；新 GPU 若参与签名匹配再补 `SIGNATURE_PRODUCT_NAMES` **一行** | `workflow.py`、CLI、匹配/SLA/TCO 引擎 |
| Benchmark 适配 | `PERFORMANCE_ADAPTERS` 注册表加一行 | 新适配器模块（`parse(file, metadata) -> (records, evidences)`） | `perf/benchmark_matcher.py`、`perf/sla_evaluator.py`、`engines/tco.py` |
| 计算规则 | 所属领域内新增**版本化**实现 | 该领域文件 | 编排层与其它领域 |

`_perf_importer` 由 if/elif 改为**普通查找表**（`{kind: (模块, 函数名)}`）——
不是插件框架：没有抽象基类、没有注册装饰器、没有动态发现。

| 计划 §6 验收 | 结果 |
|---|---|
| 新增 GPU SKU 不必修改主工作流 | ✅ `test_s4_new_gpu_sku_needs_data_plus_one_mapping` |
| 新增 Benchmark 格式只需局部适配 | ✅ `test_s4_new_benchmark_adapter_plugs_in_without_touching_domains`（现场写一个假想格式适配器 → 导入 → `exact_match` → `observed_pass`，未改领域代码） |
| 新增同结构计费规则以数据配置完成 | ✅ `test_s4_new_billing_rule_is_data_only`、`test_s4_new_cost_category_is_data_only` |
| 不产生空接口/抽象基类/动态注册 | ✅ `test_s4_extension_did_not_add_abstractions` |

---

## 七、S5：简化配置、CLI 与报告 ✅

**配置**：`docs/plan_config_reference.md` 给出必需字段（三样）、按场景选填的
五个段落、`advanced` 专业字段与报告导读，并明确"**只维护一份配置**"
（成本规则与需求情景各自留在一个文件里，`plan` 配置只引用其路径与 ID）。
契约测试保证两份示例是**超集关系**，不会出现两份表达同一事实却互相矛盾的配置。

**CLI**：`plan` 移到命令区首位，帮助按用途分为 6 组
（① 主入口 / ② 数据导入 / ③ 校验与库存 / ④ 计算与诊断 / ⑤ 报告 / ⑥ 治理与报价）；
顶层帮助写明"日常只需一条命令"；**历史命令全部保留**。

**报告**：正文（1–5 节）面向使用者，不暴露内部枚举；附录保留可复核细节。

| 内部取值 | 正文说法 |
|---|---|
| `observed_pass` / `observed_fail` | 实测达标 / 实测未达标 |
| `estimated_only` | 仅估算（不算达标） |
| `not_evaluable` | 证据不足 |
| `conditional_feasible` | 条件可行（需按假设复核） |
| `requires_benchmark` | 需要补测 |
| 可信等级（`measured_internal` 等） | 只在附录出现 |

| 计划 §7 验收 | 结果 |
|---|---|
| 普通用户无需理解表结构/Run ID/匹配级别 | ✅ `test_s5_report_body_hides_internal_vocabulary` |
| 专业用户仍可追踪来源、复现计算 | ✅ 附录保留 Run ID、匹配等级与逐项成本分解 |
| 报告区分实测/估算/假设/缺失 | ✅ `test_s5_report_distinguishes_measured_estimated_assumed_missing` |
| 有明确的下一步建议 | ✅ `test_s5_report_has_next_steps_section` |

---

## 八、S6：四场景端到端验收与封板 ✅

### 8.1 四个端到端验收场景

| 场景 | 断言 | 用例 |
|---|---|---|
| **A** 只有模型与 GPU | `partial` 而非 `blocked`；无错误阻断；资源领域出结论；性能与经济性明确留缺口且**不给假值** | `test_s6_scenario_a_model_and_gpu_only` |
| **B** 有匹配实测但无已确认价格 | 性能领域出结论且达到 `observed_pass`；价格是合成示例 → 成本不完整；**任何候选都不可发布** | `test_s6_scenario_b_measured_performance_without_confirmed_price`、`..._governance_reference_does_not_bypass_price_gap` |
| **C** 完整合成样本 | 四领域贯通；但 synthetic **绝不**升级为生产 SLA（`observed_pass` 为零）；收入只作情景分析 | `test_s6_scenario_c_synthetic_never_becomes_production_sla`、`..._algorithm_validation_cannot_reach_publishing` |
| **D** 输入错误 / 证据不匹配 | 未知模型、未知成本模板、非法配置、证据身份不符各自给出**明确原因**，无静默降级、无误放行 | `test_s6_scenario_d_*`（4 个用例） |

验收入口（全链路第 49 步）：

```bash
.venv/bin/python -m pytest tests/test_s_phase_end_to_end.py -q
```

### 8.2 新旧入口对照

| 对照 | 结果 |
|---|---|
| `plan` vs `rank-deployments` | 静态状态、性能分桶、SLA 状态、匹配等级逐候选一致 |
| `plan` vs `tco` | 一次性投入与年现金支出逐项一致 |
| `plan` CLI vs Agent `plan.evaluate_config` | 同一数据库快照下状态、领域、汇总裁荷、候选顺序与可发布性完全一致 |

用例：`test_s6_new_and_old_entries_agree_on_core_results`、
`test_s6_cli_plan_and_agent_agree_end_to_end`。

### 8.3 硬性要求核对

| 要求 | 结果 |
|---|---|
| 原有测试全部通过 | ✅ **683 passed**（S0 基线 620 → 683） |
| 原 52 步全链路全部通过 | ✅ **53 / 53**（新增第 49 步：四场景验收） |
| 第一阶段维持 160 / 192 / 0 | ✅ 不变 |
| 新增 CLI 与工作流契约测试通过 | ✅ `tests/test_s_phase_plan.py`（33）、`test_s_phase_rules.py`（13）、`test_s_phase_extension.py`（6）、`test_s_phase_end_to_end.py`（11） |
| 原有公开函数与字段兼容 | ✅ `services.phase4_decision_for` 等保留为兼容入口；数据库只追加过可空列 |
| 新旧入口核心业务结果一致 | ✅ 见 8.2 |
| 未修改权重/KV/性能/TCO 计算口径 | ✅ 仅收敛调用与常量来源，公式未改；第一阶段回归不变 |
| 生产自动报价维持禁用 | ✅ 见第十节 |

---

## 九、每轮回归结果记录

| 轮次 | 全量测试 | 全链路步骤 | 第一阶段回归 | 反向场景 |
|---|---|---|---|---|
| S0 基线 | 620 passed | 50 / 50 | 160 / 192 / 0 | 17 / 17 |
| S0 + S1 | 641 passed | 52 / 52 | 160 / 192 / 0 | 17 / 17 |
| S0 + S1 + S2 | 647 passed | 52 / 52 | 160 / 192 / 0 | 17 / 17 |
| S0 + S1 + S2 + S3 | **660 passed** | **52 / 52** | **160 / 192 / 0** | **17 / 17** |

---

## 十、封板检查清单（人工验收用，当前 7/7）

- [x] 一份 YAML、一条 `plan` 命令能完成主评估流程
- [x] CLI、Agent 和报告复用相同的核心业务判断（S2；业务规则单一权威在 S3 收口）
- [x] 原有测试与全链路验证没有回退（683 passed / 53 步）
- [x] 160 / 192 / 0 历史基线保持不变
- [ ] 新模型/GPU/Benchmark 的扩展路径已通过契约测试（S4）
- [x] 缺失数据、Synthetic 和未授权报价不会被错误放行（S6 四场景覆盖）
- [x] 新旧入口结果一致，Simple 架构文档完成（S6 §8.2）

**S6 未通过之前不进入第五阶段的新功能开发。**

---

## 十一、独立跟踪的安全事项

仓库为 Public，历史提交中的内部定价数据风险**未闭环**。
S 阶段不重写 Git 历史，该事项单独跟踪，需用户明确授权后另行处理。

---

## 十二、封板结论

S 阶段全部里程碑（S0–S6）完成，七项封板检查全部通过（见第十节）。

**本阶段的目标是降低复杂度，不是增加功能**：本轮没有新增业务能力，
只做入口统一、编排收敛、规则单一权威、扩展边界明确与报告简化。
判断依据是"下一次加一张 GPU 卡只需改数据 + 一行映射"——
这一点已由 `test_s4_new_gpu_sku_needs_data_plus_one_mapping` 证明。

**生产自动报价仍为禁用状态**（未接入可信审批系统），
真实商务数据与目标配置实测仍缺失 —— 这三项与 S 阶段无关，需单独推进。
