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

## 五、S3–S6：待执行

| 里程碑 | 目标 | 状态 |
|---|---|---|
| S3 | 统一公共规则与错误表达：匹配/SLA 可信度、synthetic 区分、成本完整性、报价门禁、证据追溯各只有一处权威实现 | ⬜ 未开始 |
| S3 | 统一公共规则与错误表达：匹配/SLA 可信度、synthetic 区分、成本完整性、报价门禁、证据追溯各只有一处权威实现 | ⬜ 未开始 |
| S4 | 最小可扩展边界：数据 / Benchmark 适配 / 计算规则三个方向 + 开发指南与契约测试 | ⬜ 未开始 |
| S5 | 简化配置、CLI 与报告：`plan` 为普通入口、历史命令作为高级入口；报告区分实测/估算/假设/缺失 | ⬜ 未开始 |
| S6 | 四场景端到端验证、新旧结果对照、文档与封板 | ⬜ 未开始 |

### S6 四个端到端场景（待建立）

| 场景 | 输入 | 期望 |
|---|---|---|
| A | 只有模型与 GPU 信息 | 正常输出静态规划，性能与经济性保留缺口 |
| B | 有匹配性能实测但无已确认价格 | 能评价性能，**不得**生成正式报价 |
| C | 有完整合成样本 | 四领域贯通，但 synthetic **绝不**升级为生产 SLA |
| D | 输入错误或证据不匹配 | 返回清楚的错误/阻断原因，无静默降级 |

---

## 五、每轮回归结果记录

| 轮次 | 全量测试 | 全链路步骤 | 第一阶段回归 | 反向场景 |
|---|---|---|---|---|
| S0 基线 | 620 passed | 50 / 50 | 160 / 192 / 0 | 17 / 17 |
| S0 + S1 | 641 passed | 52 / 52 | 160 / 192 / 0 | 17 / 17 |
| S0 + S1 + S2 | **647 passed** | **52 / 52** | **160 / 192 / 0** | **17 / 17** |

---

## 六、封板检查清单（人工验收用，当前 3/7）

- [x] 一份 YAML、一条 `plan` 命令能完成主评估流程
- [x] CLI、Agent 和报告复用相同的核心业务判断（S2；业务规则单一权威在 S3 收口）
- [x] 原有测试与全链路验证没有回退（641 passed / 52 步）
- [x] 160 / 192 / 0 历史基线保持不变
- [ ] 新模型/GPU/Benchmark 的扩展路径已通过契约测试（S4）
- [ ] 缺失数据、Synthetic 和未授权报价不会被错误放行（S3/S6 全场景覆盖）
- [ ] 新旧入口结果一致，Simple 架构文档完成（S6）

**S6 未通过之前不进入第五阶段的新功能开发。**

---

## 七、独立跟踪的安全事项

仓库为 Public，历史提交中的内部定价数据风险**未闭环**。
S 阶段不重写 Git 历史，该事项单独跟踪，需用户明确授权后另行处理。
