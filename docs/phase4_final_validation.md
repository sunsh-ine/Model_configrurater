# 第四阶段验证报告（M26–M34）

**基线**：`5a72a71`（第三阶段 V3.1 最终加固第二轮，见 `docs/phase4_baseline.md`）
**范围**：完整 TCO、业务需求经济性、部署方案比较、报价门禁与方案报告
**仓库**：`sunsh-ine/Model_configrurater`（Public）

> ⚠️ 任务书 V4.0 的正文未随执行指令提供（工作区仅有前三阶段的 `task.md` / `v.1.1.md`）。
> 本阶段按指令中的 M26–M34 清单、三条设计原则、四轮执行顺序与 8 条开发要求实施；
> 涉及口径的取舍均在本文与 `docs/phase4_interface_design.md` 中显式记录。

---

## 一、四个独立结论（**不合并成一个"通过"**）

| 结论 | 状态 | 依据 |
|---|---|---|
| **工程功能是否完成** | ✅ **完成** | M26–M34 全部落地：管理层、TCO 引擎、收益引擎、报价门禁、比较与敏感性、报告与受限接口；`50/50` 全链路步骤通过，`616 passed` |
| **商业数据是否确认** | ⛔ **未确认** | 仓库内成本/计费数据全部为 `synthetic_example` 或 `pending_business_confirmation`；币种状态为 `assumed`/`unconfirmed`。尚无任何真实合同价接入 |
| **SLA 是否具备实测依据** | ⛔ **不具备** | 目标配置（Qwen3-32B × H100/H200）无 `exact_match` 的可信实测，第三阶段结论恒为 `not_evaluable` / `requires_benchmark`，本阶段**未做任何放宽** |
| **正式报价是否可发布** | ⛔ **不可发布** | 四维门禁三个维度未过（成本不完整、SLA 未验证、无人工确认与审批）。报告输出**阻断说明**而非报价金额 |

工程完成与数据真实性是两件独立的事。计算链路跑通不等于数据可用；
数据可用也不等于报价可发布。

## 二、交付物对照

| 任务书要求 | 交付物 |
|---|---|
| 源码 | `schemas/tco.py`、`schemas/commercial.py`、`schemas/decision.py`、`engines/tco.py`、`engines/revenue.py`、`engines/quote_gate.py`、`decision/comparison.py`、`decision/sensitivity.py`、`reporting/tco_report.py`、`agent_api.py`、`importers/cost_catalog.py` |
| 测试 | `tests/test_phase4_tco.py`（117 个用例） |
| TCO 手算对照案例 | 见本文 §三；用例 `test_m28_hand_computed_tco_matches_engine` |
| 方案比较示例 | `data/exports/phase4_report.md` §2；用例 `test_m31_*` |
| 报价阻断示例 | `data/exports/phase4_report.md` §7；用例 `test_m30_*`、`test_m33_report_blocks_quote_and_watermarks` |
| JSON/Markdown 报告 | `data/exports/phase4_decision.json`（内部）、`data/exports/phase4_report.md`（内部） |
| 第四阶段验证报告 | 本文 |

## 三、TCO 手算对照案例

用例在测试内用**独立算术路径**重算一遍并逐项比对（不是把引擎结果抄一遍）。

### 3.1 买断口径（8 卡 / 1 台服务器 / 3 年）

输入：GPU 采购 100,000/卡，整机 50,000/台，电价 1/kWh，机柜 1,000/柜年，
运维 2,000/台年；单卡功耗 1,000W，服务器附加 0W，PUE 1.2，IT 负载率 1.0，年 8,760 h。

```
年耗电 kWh   = (1000 × 8 + 0) / 1000 × 8760 × 1.0 × 1.2      = 84,096
电费         = 84,096 × 1                                     = 84,096
年现金支出   = 84,096 + 1,000 + 2,000                         = 87,096
一次性投入   = 100,000 × 8 + 50,000 × 1                       = 850,000
全周期现金   = 850,000 + 87,096 × 3                           = 1,111,288
单位卡年成本 = 1,111,288 / (8 × 3)                            = 46,303.666…
年折旧       = 850,000 × (1 − 0) / 3                          = 283,333.333…
年总成本     = 87,096 + 283,333.333…                          = 370,429.333…
```

引擎输出与上述**逐项相等**（Decimal 精确比较，非近似）。

### 3.2 示例成本目录（合成数据，8 卡 / 1 台 / 买断 / 3 年）

| 项 | 数值 |
|---|---|
| 一次性投入 | 1,680,000 |
| 年现金支出 | 101,747.0720 |
| 年折旧 | 504,000.0 |
| 年总成本 | 605,747.0720 |
| 全周期现金 | 1,985,241.2160 |
| 全周期折现（r=8%） | 1,942,212.072712… |
| 单位卡年成本 | 82,718.3840 |

### 3.3 收益情景手算对照

20 万请求 / 输入 1,024 / 输出 256 / 可计费缓存占比 0.3，
未缓存输入 4、缓存输入 1、输出 12（每百万 token）：

```
未缓存输入 = 200,000 × 1,024 × 0.7 = 143,360,000 → ×4  /1e6 = 573.44
缓存输入   = 200,000 × 1,024 × 0.3 =  61,440,000 → ×1  /1e6 =  61.44
输出       = 200,000 × 256         =  51,200,000 → ×12 /1e6 = 614.40
时段收入   = 1,249.28
年度收入   = 1,249.28 × 365 = 455,987.20        （单时段示例）
示例情景（高峰 8h + 平峰 16h）年度收入 = 1,873.92 × 365 = 683,980.80
峰值请求速率 = 200,000 / (8 × 3600) = 6.9444… req/s
```

## 四、三条设计原则的落实

### 4.1 不得用 GPU 成本代替 TCO

第一阶段 `COST_SCOPE = "gpu_cost_only"` **未被改动**；第四阶段在旁边另建成本目录与
`TcoResult`，两者并列存在、互不覆盖。成本分解覆盖 11 个类别，
`per_kwh` 类的数量由「功率 × PUE × 负载率 × 小时」推导。

**买断与租赁互斥**：不属于当前 `cost_basis` 的成本项标为
`excluded_by_cost_basis`，并写入原因；被排除的项**不带金额**（schema 强制），
即"排除 ≠ 计 0"。租赁口径下 `capex_one_time` 与 `annual_depreciation` 恒为 `None`。

**未知不等于零**：三种"不参与计算"的情形可区分且都使 `TcoResult.incomplete=True`：

| determination | 含义 |
|---|---|
| `unknown_missing_price` | 目录项缺单价 |
| `unknown_missing_basis` | 缺计算假设（如 `per_kwh` 缺功率/PUE） |
| `excluded_by_cost_basis` | 口径不符（**不**使成本不完整，但显式列出） |

**年度与一次性分开**：`capex_one_time` / `annual_cash_opex` / `annual_depreciation`
三个字段分离；折旧为会计口径，**不进入**现金流（用例
`test_m28_cash_flow_separates_capex_and_opex` 直接断言净现金合计 = capex + opex×年限）。

### 4.2 收入必须从业务需求出发

第一阶段的理论满载收入**未参与**第四阶段任何计算。收入由
`RevenueScenario.periods`（请求量、时长、输入输出分布、业务侧缓存估计、是否峰值/批处理）
逐时段推算，再按 `periods_cover` 外推到年度。

**可计费缓存比例是计费口径**：`BillingRule.billable_cache_basis` 的取值域里
**没有** `physical_cache_hit_rate`（schema 层拒绝，用例
`test_m29_physical_cache_hit_rate_is_not_a_billable_basis`），
且给出 `billable_cache_ratio` 时必须声明依据。实际计入缓存价的占比取
`min(业务侧估计, 合同口径)` —— 只会更保守。

**与可信容量比对**：只有第三阶段存在 `exact_match` 且可信等级为测量类的实测时，
才可能给出 `within_capacity` / `exceeds_capacity`；否则恒为
`unknown_no_measurement`、`scenario_only=True`，并在报告中写明"仅作情景分析"。
不可信证据会被**显式列出**在 `capacity_basis` 里说明为何被排除。

### 4.3 三种结论必须独立

第四阶段的独立性由**第四个维度**补齐，门禁四维并列、互不替代：

| 维度 | 通过条件 |
|---|---|
| `cost_estimate_status` | `TcoResult.incomplete == False`、币种已确认、不含合成价格、**无未确认条目** |
| `sla_verification_status` | 存在持久化证据记录，服务端**重新推导**得到 `observed_pass` + `exact_match`，且与记录一致 |
| `commercial_confirmation_status` | 币种 / 价格 / 条款三项**人工**确认（且齐备确认人、时间、有效期、凭据） |
| `approval_status` | 存在**持久化审批记录**、作用域匹配、**成本快照指纹一致**、且在有效期内 |

`quote_publishable = AND(四维通过)`，且 schema 层禁止"未通过却标记可发布"。
任一维度未过，报告输出**阻断说明**并逐条列出原因，**不输出任何金额**
（水印："⛔ 草稿已阻断（不具备发布资格）"）。

> **V4.1 F4.1 修正**：本表最初版本只检查"状态字符串是否自洽"，
> 调用方可以声明 `observed_pass` / `approved` 就得到可发布资格。
> 现在门禁只接受**证据引用**（`sla_evaluation_id` / `approval_id`），
> 服务端从持久化记录读取并重新推导结论；详见附录 A.1。

## 五、验证命令与结果

```
bash scripts/verify_all.sh
```

| 项 | 结果 |
|---|---|
| 全链路步骤 | **47 / 47 通过**（第四阶段 11 步，其中 4 步为**断言式**） |
| 全量测试 | **616 passed**（第三阶段 499 → 第四阶段新增 117） |
| 第一阶段回归 | 默认情景 **160**（32×5）、`pricing_result` 总数 **192**、超容差不匹配 **0** —— 不变 |
| 敏感数据扫描 | 已跟踪文件中未发现内部定价数值；忽略规则校验通过 |

第四阶段的断言式步骤（关键行未命中即失败，不接受静默通过）：

| 步骤 | 断言 |
|---|---|
| 36 | 租赁口径：`capex_one_time` 与 `annual_depreciation` 必须为 `null`，买断项必须被显式列出 |
| 37 | 缺价项：`incomplete` 必须为 `true`，且缺价项必须被列出 |
| 39 | 合成成本 + 无确认 + 未审批 → 必须阻断 |
| 41 | 商务已确认但 SLA 非实测 → **仍然**必须阻断，且给出 `sla_estimated_only` |

## 六、Agent 受限接口（M33）

`agent_api.py` 只暴露白名单只读算子：

```
phase4.cost_catalog / phase4.tco / phase4.revenue
phase4.quote_eligibility / phase4.decision / phase3.performance_inventory
```

* 未知算子直接报错，不做猜测式调用（用例 `test_m33_agent_api_is_whitelisted_and_read_only`）；
* 接口内**没有**任何生成商务确认或审批的算子
  （用例 `test_m33_no_commercial_confirmation_generator_in_agent_api`）；
* `phase4.quote_eligibility` 的 `approval_status` 默认 `missing`，
  不存在"默认已审批"的便利路径（用例 `test_m33_agent_api_does_not_default_to_approved`）；
* CLI 侧同样把关：声明 `--approval-status approved` 却未给 `--approval-ref`
  直接被拒（`ServiceError`）。

## 七、禁止事项逐条核对

| 禁止事项 | 落实情况 |
|---|---|
| 不得用 GPU 成本代替 TCO | ✅ 独立成本目录 + 11 类分解；第一阶段口径未改 |
| 不得把理论满载收入当真实营业收入 | ✅ 收入只由需求情景推算 |
| 不得把物理 Prefix Cache 命中率当计费口径 | ✅ 取值域直接排除 + schema 校验 + 专项用例 |
| 不得在缺数据时用 0 补齐 | ✅ 三种 unknown/excluded 情形均可区分且不带金额 |
| 不得把估算性能说成已验证 SLA | ✅ 门禁只认 `observed_pass` + `exact_match` |
| 不得在币种/合同价未确认时声称成本已确认 | ✅ 合成/未确认一律 `incomplete`，并阻断报价 |
| 不得让报价门禁被任何参数放宽 | ✅ 纯函数、无 bypass 参数；schema 层双重校验 |
| 不得改变第一阶段历史计算口径 | ✅ `PricingResult` / `calculate_all` 未改动，回归基线不变 |
| 不开发 Web 前端与自治商务 Agent | ✅ 仅提供只读受限接口 |

## 八、本轮开发中发现并修掉的问题

诚实记录（都是自己写出来又在测试/断言中被抓到的）：

| 问题 | 现象 | 处理 |
|---|---|---|
| `Repository` 被重复定义 | 追加的 mixin 让后一个空类覆盖了原类，`Repository()` 直接报错 | 把 mixin 移到原类定义之前并让 `Repository` 继承它 |
| 缺价项不进 `incomplete` | `unknown_items` 有值，但 `TcoResult.incomplete` 仍为 `False` —— 缺项会被当成 0 悄悄吃掉 | 在引擎里把 `unknown_items` 接入完整性判定（用例 37 抓到） |
| 单个不完整候选被判"可比" | `_comparability` 先判"无参照方案"再判完整性，导致唯一候选（不完整）被标成 `comparable` | 调整判断顺序：成本不完整**永远**不可比 |
| "已审批但无凭据"导致门禁抛异常 | schema 禁止该状态，而门禁想把它作为 blocker 报告 | 门禁将该状态降级为 `missing` 并写明 `approval_ref_missing` |
| 租赁模板未体现互斥规则 | 模板只选租赁项，排除规则无从触发 | 模板显式包含买断项，让"排除 ≠ 零成本"在链路上可验证（步骤 36 抓到） |
| 报告缺少决策数据 | `phase4-report` 独立运行时第 2–6 节被静默省略，"工程功能"被误判为未完成 | 增加 `--decision-json`；缺数据时报告显式声明省略，不再静默 |

## 九、未确认 / 未实现（如实列出）

**未确认（数据缺口，不是代码缺口）**

1. 无任何真实成本/合同价：全部为合成示例或待确认；
2. 币种未经业务确认（第一阶段起就悬而未决，本阶段沿用）；
3. 目标配置（Qwen3-32B × H100/H200）无现场实测 → SLA 与容量恒为不可评估；
4. 可计费缓存比例、批处理折扣、最小计费量均无合同依据。

**未实现（本阶段边界内不做）**

1. 需求预测与排队模型（需求量由业务侧给定，不做预测）；
2. 跨模型/跨硬件的通用性能预测器；
3. 经济性评分与跨维度加权综合评分（币种与合同价确认前不启用）；
4. Web 前端与自治商务 Agent；
5. 历史 git 重写（见下节，属独立安全事项）。

## 十、安全事项（**独立、未闭环、未授权执行**）

仓库为 Public，历史提交中曾出现内部定价数据。该风险**不因 616 项测试通过而消除**，
也不能由本阶段的功能开发替代处理。方案与影响评估见
`docs/security_remediation_report.md` §4。

**本轮未执行任何历史重写或强制推送。** 第四阶段新增的示例数据一律标注
`synthetic_example`，且名称必须以「【合成」开头（导入器强制校验），
真实商务数据继续留在被 git 忽略的 `data/private/`。

## 十一、封板状态

| 维度 | 结论 |
|---|---|
| 第四阶段工程交付 | ✅ 完成，可验收 |
| 商业数据 | ⛔ 未确认（需业务方提供真实价格与条款） |
| SLA 实测依据 | ⛔ 不具备（需目标配置现场实测） |
| 正式报价 | ⛔ 不可发布（四维门禁未过） |

第四阶段的计算与决策框架已完整可用：接入真实成本、计费规则与实测数据后，
门禁会自动放行 —— 不需要改动任何代码，也不需要放宽任何口径。

---

# 附：V4.1 定向整改（F4.1–F4.7）

复审指出第四阶段的核心正确性缺陷：**门禁信任调用方字符串、TCO 靠"不选"规避完整性、
价格适用范围未校验**。本节记录整改结果。

## A.1 F4.1（P0）报价门禁改为读取真实证据

**问题**：`quote_eligibility_for` 直接接受 `sla_status="observed_pass"`、
`match_level="exact_match"`、`approval_status="approved"`、`approval_ref="随便一个字符串"`，
并据此可能给出 `quote_publishable=True`。门禁检查的是**状态字符串之间是否自洽**，
而不是这些状态背后的证据是否有效。

**整改**：

1. **只接受引用**。`quote_eligibility_for(db, candidate_id, requirement_id,
   sla_evaluation_id, approval_id, ...)`：缺少证据 ID 直接 `ServiceError`，
   没有任何"声明即通过"的入口。
2. **持久化证据表**。新增 `sla_evidence` 与 `quote_approval` 两张表；
   `record-sla-evidence` 跑一次评估并落库，`import-quote-approvals` 导入人工审批。
3. **服务端重新推导**。报价时用证据记录里的**需求快照 + 目标身份**重新匹配、
   重新做 SLA 判定，并把结果与记录中的结论比对：
   不一致 → `sla_evidence_stale`；引用的实测已不存在 → `matched_run_not_found`；
   等级或状态不达标 → `sla_recheck_not_pass`。
4. **成本快照绑定**。`tco_snapshot_hash()` 对决定金额的字段取指纹，审批记录必须绑定
   同一个指纹；成本一变，旧审批自动失效（`approval_cost_snapshot_stale`）。
5. **审批必须是记录**。`QuoteApproval` 要求审批人、时间、有效期、凭据与作用域
   （候选 / 需求 / 方案），`approval_ref` 由记录提供，调用方无法自填。
6. **诊断入口隔离**。`quote_eligibility_from_claims()` 保留原始字符串入口，
   但恒返回 `quote_publishable=False`、`claims_only=True`，且**不会**把声明升级成
   `verified_measured`。
7. **Agent 接口参数白名单**。`AGENT_OP_PARAMS` 为每个算子声明允许的参数；
   传 `sla_status` 等状态字符串属于未声明参数，直接拒绝；
   `phase4.quote_eligibility` 强制要求四个引用。
8. **Schema 层双保险**。`QuoteEligibility` 的校验器禁止
   "`verified_measured` 却没有 `sla_evaluation_id`"、
   "`approved` 却没有 `approval_id`"、"诊断路径标记可发布"。

**复现**（评审指定的反向场景）：`scripts/check_forged_quote_blocked.py`
—— 全部成本项标记 `confirmed`、调用方声称 `observed_pass`、审批凭据是任意字符串，
10 项检查全部阻断（`50/50` 全链路第 44 步）。

## A.2 F4.2（P0）成本范围完整性

**问题**：`compute_tco()` 只遍历 `plan.selections`，未校验"该方案应该覆盖哪些类别"。
只选 GPU 采购 + 完整折旧假设，就能得到 `incomplete=False` 的"完整 TCO"，
而电费、网络、运维根本没进计算。

**整改**：新增**版本化** `CostCoveragePolicy`，每个适用类别必须处于
`covered` / `excluded_by_scope`（必须给理由）/ `not_applicable` 之一；
既未选入又未显式排除的类别记为 `coverage_gaps`，使 TCO `incomplete=True`。
**没有覆盖策略本身也算不完整**（`coverage_policy_missing`），
否则"少选几类"就能蒙过检查。另加**疑似重复计费**检查：同一类别、同一计价口径、
同一适用范围出现多个参与成本项时报警并使结果不完整。

**复现**：`test_f42_omitting_required_categories_makes_tco_incomplete`、
`test_f42_missing_coverage_policy_is_incomplete`、`test_f42_duplicate_billing_is_flagged`、
`test_f42_explicit_scope_exclusion_is_accepted`、`test_f42_policy_forbidding_exclusion_still_flags_gap`。

## A.3 F4.3（P0）价格适用范围与有效期

**问题**：`applies_to_hardware_id` / `region` / `valid_from` / `valid_to` 保存了却从不参与计算。
H200 的价格可以用来算 H100 方案，过期单价照用。

**整改**：在算金额**之前**逐项校验，不通过则标 `not_applicable`、不给金额并写入
`applicability_errors`（使 TCO 不完整）：

| 情形 | 原因码 |
|---|---|
| 价格绑定的是别的 GPU SKU | `hardware_sku_mismatch` |
| 价格区域与方案区域冲突 | `region_mismatch` |
| 条目声明了区域但方案未声明区域 | `region_unspecified` |
| 价格在基准日之前已失效 | `price_expired` |
| 价格在基准日尚未生效 | `price_not_yet_effective` |

基准日来自 `DeploymentCostPlan.as_of_date`（缺省取当天，并写进结果与备注）。
收入侧同样收紧：`BillingRule.model_name` 必须与情景模型一致，
计费规则的有效期窗口必须覆盖基准日，否则直接报错。

**复现**：`test_f43_price_for_other_gpu_sku_is_not_applicable`、
`test_f43_expired_and_not_yet_effective_prices_are_blocked`、
`test_f43_region_conflict_and_unspecified_region`、
`test_f43_billing_rule_must_match_scenario_model`、
`test_f43_billing_rule_validity_window_is_enforced`。

## A.4 F4.4（P1）缓存计费语义与失败请求

**问题（缓存）**：`min(业务估计, 合同上限)` 被描述为"更保守"，但缓存价低于未缓存价时，
**取更小的缓存比例反而提高收入预测**。术语与语义都不成立。

**整改**：

* 明确 `billable_cache_ratio` 是**合同上限**，不是保守估计；
* 给出**收入区间** `[annual_revenue_lower_bound, annual_revenue_upper_bound]`
  = [最大可辩护折扣, 完全不打折]，基准值落在区间内；
* 删除全部"保守"措辞，并在 notes 里写明"基准值不是保守下界"。

**问题（失败请求）**：收入模型没有区分需求请求量与可计费请求量。

**整改**：`DemandPeriod.success_rate` + `BillingRule.billable_on_failure`；
结果按 `demand / served / failed / billable` **三段**记录，
默认 `billable_on_failure=False`（不假设失败也收费），并用 warning 明确提示。

**复现**：`test_m29_cache_fraction_is_contract_cap_not_conservative`、
`test_m29_revenue_bounds_widen_with_unconfirmed_cache_rule`、
`test_f44_demand_served_billable_are_separated`、
`test_f44_billable_on_failure_follows_contract`、`test_f44_different_cache_prices_compute_correctly`。

## A.5 F4.5（P1）跨币种与完整性检查

**问题**：`annual_margin = annual_revenue - annual_total` 未先检查币种，
USD 收入减 CNY 成本也能得出"利润 200"。

**整改**：新增 `_margin_allowed()`，要求成本与收入都存在、**成本结论完整**、
双方币种一致且已知；否则**不给数字**，只写 `margin_not_evaluable:<原因>`。
盈亏平衡的收入成本比同样加币种校验；预算检查新增 `compared_currency`，
币种不一致直接 `unknown`。

**复现**：`test_f45_usd_revenue_minus_cny_cost_is_not_a_margin`、
`test_f45_incomplete_cost_hides_margin`、`test_f45_same_currency_still_yields_margin`（对照）、
`test_f45_breakeven_ratio_blocked_on_currency_mismatch`、`test_f45_budget_blocked_on_currency_mismatch`。

## A.6 F4.6（P1）综合容量证据

**问题**：只要 `Peak Output TPS ≤ Measured Output TPS` 就判 `within_capacity`，
忽略了请求吞吐、并发、负载一致性、SLA 与可持续性。

**整改**：新增 `CapacityCheckDetail`，**五个子项**分别判定：

| 子项 | 判据 |
|---|---|
| `output_throughput` | 峰值输出 tok/s ≤ 实测输出吞吐 |
| `request_throughput` | 峰值 req/s ≤ 实测请求吞吐 |
| `concurrency_and_load` | Little 定律推算所需并发 ≤ 实测配置并发；且需求负载长度与实测一致 |
| `sla_evidence` | 实测 TTFT/TPOT 满足需求约束 |
| `sustainability` | 同组曲线中该并发被认定为可持续容量 |

结论规则：任一 `not_satisfied` → `exceeds_capacity`；全部 `satisfied` → `within_capacity`；
存在 `unknown` → **`partially_verified`**（明确不得当作容量可行）。

**复现**：`test_m29_output_only_is_not_enough_for_within_capacity`、
`test_m29_all_five_subchecks_needed_for_within_capacity`、
`test_m29_any_failing_subcheck_yields_exceeds_capacity`、`test_f46_*`。

## A.7 F4.7 端到端与任务书对照

* 全链路 `50/50` 通过，其中**断言式**步骤 4 个（租赁口径、缺价不完整、
  证据未核验必须阻断、不接受状态字符串）+ **反向场景**步骤 1 个；
* 全量测试 **616 passed**，第一阶段回归 **160 / 192 / 0 超容差** 不变；
* 任务书对照：V4.0 正文未随指令提供，本节按复审列出的差异项
  （成本类别覆盖策略、审批登记与证据绑定、价格有效期、报价作用域、
  需求成功率、币种可比性、可复算输入快照）逐项落实；
  **仍需在拿到 V4.0 正文后做一次逐条差异核对**。

## A.8 修正后的门禁契约（摘要）

```
evaluate_quote_eligibility(
    candidate_id, requirement_id,
    tco,                      # 成本结果（含覆盖策略、适用性校验）
    sla_evidence,             # 持久化记录（SlaEvidenceRecord）
    sla_recheck,              # 服务端用记录快照重新推导的结果
    approval,                 # 持久化审批记录（QuoteApproval）
    confirmation,             # 人工商务确认
    cost_snapshot_hash,       # 现算快照指纹，审批必须绑定同一个
) -> QuoteEligibility
```

四个维度独立，**没有任何参数可以放宽门禁**；调用方无法只靠传字符串影响结论。

---

# 附：V4.2 定向加固（E1–E7）

复审在 V4.1 基础上指出三处遗留：算法验证结果可能进入正式报价、聚合证据无法重放、
快照与审批作用域未闭合。另有两处模型口径问题（收入区间、Little 定律）。本轮逐项修复。

## B.1 E1（P0）正式报价禁止 Synthetic / 算法验证

**问题**：`_sla_dimension()` 只看重放后的 `status == observed_pass` 与
`match_level == exact_match`，没有检查数据可信等级、匹配证明的 `sla_proof` 禁令、
`algorithm_validation_only` 标记，以及当次 SLA 评估是否启用了 `allow_synthetic`。
第三阶段允许显式 `allow_synthetic=True` 产出仅用于算法验证的 `observed_pass` ——
这是合理的测试设计，但**不能**被第四阶段当作生产依据。

**整改**：第四阶段自建一道**不可被任何策略放宽**的生产可信度门禁
（`PRODUCTION_TRUST_LEVELS`），在门禁里逐条检查：

| 检查 | 阻断原因码 |
|---|---|
| 记录的数据可信等级必须属于测量类 | `sla_data_trust_not_production` |
| 匹配证明不得仅限算法验证 | `sla_algorithm_validation_only` |
| 当次评估不得启用 `allow_synthetic` | `sla_allow_synthetic_used` |
| 匹配证明不得声明禁止 `sla_proof` | `sla_proof_blocked_by_match` |
| **重放**得到的可信等级与质量标记同样要过门禁 | `sla_recheck_data_trust_not_production` / `sla_recheck_algorithm_validation_only` |

证据记录新增 `algorithm_validation_only` / `allow_synthetic_used` / `sla_proof_blocked` /
`policy_snapshot` 字段，把"这是算法验证口径"永久留在记录里。
`record-sla-evidence` 同时输出 `production_grade` 布尔值，便于人工一眼判断。

**复现**：`test_e1_synthetic_evidence_never_reaches_formal_quote`（用
`allow_synthetic=True` 真的跑出 `observed_pass`，再确认门禁拒绝）、
`test_e1_quality_flags_block_formal_quote`、
`test_e1_production_trust_levels_are_enforced_by_recheck_too`；
反向场景脚本路径 5（12/12 全阻断）。

## B.2 E2（P0）聚合 SLA 证据可重放

**问题**：多条实测保守聚合后，`record_sla_evidence_for()` 把
`matched_run_id` 存成 `aggregate:run-a,run-b`；而 `_recheck_sla_evidence()`
拿这个字符串去原始 `benchmark_run` 表里查 —— 聚合记录只是运行时产物，
表里没有这一行，于是**必然** `matched_run_not_found`。这是确定的流程断裂。

**整改**：

1. 证据记录保存 **`supporting_run_ids`**（全部参与实测），并有 schema 校验
   —— 聚合证据的 `matched_run_id` 必须精确等于 `aggregate:<排序后的成员>`；
2. 复核**不再**用 `matched_run_id` 去表里查。改为：用记录里的**目标身份快照**
   重新匹配整个证据池，比较重放得到的代表运行与记录是否一致；
3. `_aggregate_runs()` 的聚合 ID 改为**对成员排序**，保证同一组实测得到同一个 ID
   （否则重放时无法比对）。

**复现**：`test_e2_aggregate_evidence_replays_without_lookup_failure`（两条同配置实测
→ 聚合 → 重放成功）、`test_e2_aggregate_id_is_deterministic`、
`test_e2_supporting_runs_must_still_exist`。

## B.3 E3（P0）完整快照与审批作用域

**问题**（三处）：

| 位置 | 问题 |
|---|---|
| `_recheck_sla_evidence()` | 用调用方传入的 facts 计算，而不是记录里的 `target_identity`，也未比对两者 |
| `run_pool_hash` | 创建时保存了，复核时不比较；且指纹只含 run_id/哈希/可信等级，未覆盖指标数值 |
| `_approval_dimension()` | 未检查 `scenario_id` / `billing_rule_id` 与审批作用域的一致性 |

**整改**：

1. **目标身份以记录为准**：重放用 `record.target_identity` 构造目标；
   调用方传的 facts 只用于**比对**，不一致即 `sla_target_identity_changed`；
2. **run 池指纹比较**：复核时重算并比对，不一致即 `sla_run_pool_changed`；
   指纹扩展到**归一化指标快照**（并发、负载长度、到达方式、吞吐、TTFT/TPOT、
   mean/p99 E2E、失败率、样本量、时长）—— 只改数值不改 ID 也会被检出
   （`test_e3_run_pool_hash_covers_metric_values` 专门验证这一点）；
3. **规范化输入指纹** `input_snapshot_hash`：覆盖目标身份 + SLA Requirement +
   匹配证明等级 + 参与 Run + 评估策略快照 + 商务方案；
4. **策略快照比对**：策略被换过（例如把 `allow_synthetic` 打开）即
   `policy_changed`，旧证据不可复现；
5. **审批作用域补齐** `scenario_id` / `billing_rule_id`：任一不符即
   `approval_scope_mismatch`。

**复现**：`test_e3_run_pool_change_invalidates_evidence`、
`test_e3_target_identity_change_invalidates_evidence`、`test_e3_policy_change_is_detected`、
`test_e3_run_pool_hash_covers_metric_values`、
`test_e3_approval_scope_covers_scenario_and_billing_rule`。

## B.4 E4（P1）收入区间遵守合同计费范围

**问题**：`high = min(1, max(业务估计, 合同上限))` 用 `max()` **扩大了合同上限**
—— 合同只允许 20%、业务估计 60% 时，下界仍按 60% 计折扣，超出合同允许范围。

**整改**：合同允许的可计费占比区间是 `[0, min(业务估计, 合同上限)]`。
收入区间由该区间**两端**算出，并按**实际金额大小排序**
（不预设"缓存价一定更低"；缓存价更高时区间方向自然反转）。

**复现**：`test_e4_lower_bound_never_exceeds_contract_cap`（显式断言下界不等于按 60%
计算的值）、`test_e4_bounds_follow_actual_price_ordering`。

## B.5 E5（P1）Little 定律统计口径

**问题**：`residence = P99 TTFT + P99 TPOT × 输出长度` 是**两个 P99 拼出来的上界**，
不是 Little 定律要求的**平均**驻留时间。用它反推会显著高估平均并发需求，
从而错误拒绝实际可行的配置。

**整改**：并发子项改用实测 `e2e_mean_ms`；**没有 mean E2E 就把该子项标 `unknown`**，
绝不用 P99 代替。缺失时容量结论只能是 `partially_verified`。

**复现**：`test_e5_little_law_requires_mean_e2e`、`test_e5_mean_e2e_enables_concurrency_judgement`。

## B.6 E6（P1）覆盖策略可信度

**问题**：覆盖策略本身没有授权概念 —— 谁都可以写一份"只要求 1 个类别"的策略，
把大量成本合法地排除在"完整性检查"之外。

**整改**：`CostCoveragePolicy` 新增 `authority`（`confirmed` / `draft` / `unauthorized`）
与批准人、批准日期、凭据编号（`confirmed` 时必填）。非 `confirmed` 的策略：

* 使 `TcoResult.incomplete = True`（`coverage_policy_not_authorized`）；
* 被报价门禁阻断（`cost_coverage_policy_not_authorized`）。

示例策略**故意标为 `draft`**：它是合成示例，不能声称获得任何真实批准。

**复现**：`test_e6_draft_coverage_policy_blocks_production_quote`、
`test_e6_confirmed_policy_requires_approval_audit_fields`、
`test_e6_example_policy_is_draft_not_claimed_confirmed`。

## B.7 E7 端到端与回归

| 项 | 结果 |
|---|---|
| 全链路 | **50 / 50 通过**（断言式步骤 4 个 + 反向场景步骤 1 个，反向场景 12 项检查） |
| 全量测试 | **616 passed**（第四阶段 88 → 108 用例） |
| 第一阶段回归 | 默认情景 **160**、`pricing_result` **192**、超容差不匹配 **0** —— 不变 |

## B.8 仍未闭环的事项（如实列出）

1. **审批人身份与权限未认证**。`import-quote-approvals` 只做持久化，
   没有独立的身份认证与权限校验（离线算法测试可接受，生产级需要受信任的
   审批输入通道与 RBAC）。本轮**未实现**，需要上游系统提供。
2. **任务书 V4.0 正文仍未提供**。B 节按复审列出的差异项逐条落实，
   但尚未与 V4.0 原文做逐条对照。
3. **真实商务数据与目标配置实测缺失**，与前几轮一致。
4. **仓库为 Public，历史内部定价数据风险未闭环**，未重写历史。

---

# 附：V4.3 局部闭合与接口冻结（三项修正）

V4.2 复审确认 E1–E7 主要落实，同时指出三处「代码写了但没接上」的断点。
本轮**只改现有函数**，不新增业务模块与 Schema 层级。

## C.1 P0 计费规则作用域真正生效

**问题**：`_approval_dimension()` 里加了计费规则比对，但
`quote_eligibility_for()` 把它写死成 `billing_rule_id = None`（注释还写着
"此处只做记录，不参与门禁判定"）—— 于是审批的是 `rule-A`、当前用 `rule-B`
也不会被拦住。

**整改**：

1. `quote_eligibility_for()` 真实解析计费规则：优先用显式传入的 `billing_rule_id`，
   否则从 `--demand-file` + `--scenario` 对应的业务情景里取；
   两者都有且不一致时**直接报错**（不静默取其一）；
2. 规则必须真实存在于库中，否则 `ServiceError`；
3. 计费规则并入**商务维度**（报价必然含 Token 单价或计费条款）：
   规则缺失 → `billing_rule_missing`；未确认 / 币种未确认 / 未生效 / 已失效
   各有独立原因码；
4. 作用域比对的基线**只来自实际使用的规则对象**（`billing_rule.rule_id`），
   删除了冗余的 `billing_rule_id` 入参 —— 两个来源正是产生断点的原因。

**复现**：`test_v43_billing_rule_scope_is_really_checked`（评审指定用例：
审批 rule-A / 当前 rule-B 必须阻断；rule-A 对 rule-A 则只剩其它维度的原因）、
`test_v43_billing_rule_resolved_from_scenario`、`test_v43_missing_or_unconfirmed_billing_rule_blocks`、
`test_v43_service_requires_existing_billing_rule`；反向场景脚本路径 6/7（14/14）。

## C.2 P1 统一证据指纹并真正用于复核

**问题（两处）**：`input_snapshot_hash` 生成了却从不在复核时重算比较，
只是审计元数据；`_run_pool_hash()` 手写字段，未覆盖 `itl_p99_ms`、
`metric_definition_ids` 等参与 SLA 判断的字段 —— 只改这些字段且 `raw_sha256`
不变时，指纹不会变化。

**整改**：删除手写字段的做法，改为**规范化快照 + 确定性序列化**：

```
_canonical_run_snapshot(run)  →  单条实测的证据快照
_evidence_pool_hash(runs)     →  对全部快照排序后 JSON(sort_keys) 取 SHA256
```

* 快照覆盖签名身份、负载、指标口径声明、**全部标准化指标数值**、样本量与时长、
  失败计数、质量标记与来源哈希；逐请求明细改为取指纹（避免体积与噪声）；
  仅排除人工批注 `notes`；
* **生成与复核共用同一个函数**，不再有两套手写字段；
* `_recheck_sla_evidence()` 现在**重算并比对** `input_snapshot_hash`：
  记录内容被改动即 `evidence_snapshot_tampered`。

**复现**：`test_v43_pool_hash_covers_itl_and_metric_definitions`、
`test_v43_evidence_pool_change_invalidates_old_evidence`（只改 ITL、保留 run_id 与
raw_sha256，旧证据失效）、`test_v43_tampered_evidence_record_is_rejected`。

## C.3 P1 成本快照纳入计算依据

**问题**：`tco_snapshot_hash()` 只哈希金额与成本项数值。两份**要求不同成本类别**的
覆盖策略可能算出相同金额，此时审批指纹完全一致 ——
"哪些成本该计入"的业务规则已经变了，旧审批却仍然匹配。

**整改**：快照从"数值结果"扩展为"**数值 + 计算依据**"，新增：

* 覆盖策略身份：`coverage_policy_id` / `version` / `authority`；
* 覆盖结论与排除范围：`coverage_decisions`、`coverage_gaps`、`scope_exclusions`；
* 完整性口径：`incomplete`、`completeness_reasons`、`unknown_items`、
  `excluded_items`、`applicability_errors`、`unconfirmed_items`、
  `currency_confirmed`、`contains_synthetic_prices`；
* 逐项依据：`category` / `basis` / `unit` / `currency` / `currency_status` /
  `status` / `origin` / `determination` / `applicability_issue` / `evidence_id`；
* 方案区域 `region`（价格适用性依据之一，同时补进 `TcoResult`）。

**复现**：`test_v43_cost_snapshot_includes_coverage_policy_identity`
（金额相同、策略版本或排除范围不同 → 快照不同；同参数重算 → 快照相同）、
`test_v43_coverage_change_invalidates_approval`（策略版本变化 → 旧审批失效）。

## C.4 接口冻结与部署级限制

**本轮之后冻结第四阶段核心接口**，不再新增业务模块、Schema 层级或验收阶段。

**部署级限制（不通过引入 RBAC 解决）**：

> 本系统**未接入可信审批系统**，`import-quote-approvals` 只做持久化，
> 不校验审批人身份与权限。因此：
> **不得用于生产环境的报价自动发布。**
> 正式报价必须由人工在受控流程中签发。

这条限制同时写进 `docs/phase4_interface_design.md`，作为接口冻结点的一部分。

## C.5 本轮结果

| 项 | 结果 |
|---|---|
| 全链路 | **50 / 50 通过**（反向场景脚本 12 → 14 项检查） |
| 全量测试 | **616 passed**（第四阶段 108 → 117 用例） |
| 第一阶段回归 | 默认情景 **160**、`pricing_result` **192**、超容差不匹配 **0** —— 不变 |
| 新增模块 | **无**（本轮只修改现有函数） |

## C.6 下一阶段方向（按复审建议，尚未执行）

复审建议停止"每轮审核后继续扩充报价系统"，转向架构收敛。建议的三件事：

1. **统一用户评估入口** —— 目前 `services.py` 有 40+ 个 `*_for()` 函数，
   调用方需要自己拼装链路；
2. **收敛 `services.py` 的业务编排** —— 该文件已超过 3000 行，
   跨阶段编排与纯查询混在一起；
3. **建立简单稳定的数据与算法扩展边界** —— 明确"新增一个成本类别 /
   一个指标口径 / 一个证据来源"分别要动哪些地方。

**本轮未执行这三项**：它们会改动大量现有文件，属于范围变更而非局部修正。
真实数据接入与压测验证此时仍缺输入，先做大规模重构缺乏验证手段。

## C.7 未闭环事项（与前几轮一致）

1. **审批人身份与权限未认证** —— 见 C.4 的部署级限制；
2. **任务书 V4.0 正文仍未提供** —— 未做逐条对照；
3. **真实商务数据与目标配置实测缺失**；
4. **仓库为 Public，历史内部定价数据风险未闭环** —— 未重写历史。
