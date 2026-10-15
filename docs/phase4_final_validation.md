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
| **工程功能是否完成** | ✅ **完成** | M26–M34 全部落地：管理层、TCO 引擎、收益引擎、报价门禁、比较与敏感性、报告与受限接口；`47/47` 全链路步骤通过，`549 passed` |
| **商业数据是否确认** | ⛔ **未确认** | 仓库内成本/计费数据全部为 `synthetic_example` 或 `pending_business_confirmation`；币种状态为 `assumed`/`unconfirmed`。尚无任何真实合同价接入 |
| **SLA 是否具备实测依据** | ⛔ **不具备** | 目标配置（Qwen3-32B × H100/H200）无 `exact_match` 的可信实测，第三阶段结论恒为 `not_evaluable` / `requires_benchmark`，本阶段**未做任何放宽** |
| **正式报价是否可发布** | ⛔ **不可发布** | 四维门禁三个维度未过（成本不完整、SLA 未验证、无人工确认与审批）。报告输出**阻断说明**而非报价金额 |

工程完成与数据真实性是两件独立的事。计算链路跑通不等于数据可用；
数据可用也不等于报价可发布。

## 二、交付物对照

| 任务书要求 | 交付物 |
|---|---|
| 源码 | `schemas/tco.py`、`schemas/commercial.py`、`schemas/decision.py`、`engines/tco.py`、`engines/revenue.py`、`engines/quote_gate.py`、`decision/comparison.py`、`decision/sensitivity.py`、`reporting/tco_report.py`、`agent_api.py`、`importers/cost_catalog.py` |
| 测试 | `tests/test_phase4_tco.py`（50 个用例） |
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
| `cost_estimate_status` | `TcoResult.incomplete == False` 且币种已确认 且不含合成价格 |
| `sla_verification_status` | 第三阶段 `observed_pass` **且**匹配等级 `exact_match` |
| `commercial_confirmation_status` | 币种 / 价格 / 条款三项**人工**确认（且齐备确认人、时间、有效期、凭据） |
| `approval_status` | 人工审批 **且**提供审批凭据 `approval_ref` |

`quote_publishable = AND(四维通过)`，且 schema 层禁止"未通过却标记可发布"。
任一维度未过，报告输出**阻断说明**并逐条列出原因，**不输出任何金额**
（水印："⛔ 草稿已阻断（不具备发布资格）"）。

## 五、验证命令与结果

```
bash scripts/verify_all.sh
```

| 项 | 结果 |
|---|---|
| 全链路步骤 | **47 / 47 通过**（第四阶段 11 步，其中 4 步为**断言式**） |
| 全量测试 | **549 passed**（第三阶段 499 → 第四阶段新增 50） |
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

仓库为 Public，历史提交中曾出现内部定价数据。该风险**不因 549 项测试通过而消除**，
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
