# 第四阶段接口设计（M26）

基线：`5a72a71`（见 `docs/phase4_baseline.md`）
范围：M26–M34 —— 完整 TCO、业务需求经济性、部署方案比较、报价门禁与方案报告。

---

## 一、模块划分

```
schemas/
  tco.py          M26/M27/M28  成本目录、部署成本方案、TCO 结果、现金流
  commercial.py   M26/M29/M30  计费规则、需求情景、收益结果、商务确认、报价资格
  decision.py     M26/M31/M32  候选比较、预算检查、敏感性、盈亏平衡

engines/
  tco.py          M28  纯计算：成本分解 → 年度化 → 折旧 → 折现 → 现金流
  revenue.py      M29  纯计算：需求情景 + 计费规则 → 收入；与可信容量比对
  quote_gate.py   M30  纯判定：四维门禁 → 报价资格（fail-closed）

decision/
  comparison.py   M31  阶段一/二/三/四结果的并列视图 + 可比性判定
  sensitivity.py  M32  预算检查、单因子敏感性、盈亏平衡

reporting/
  tco_report.py   M33  Markdown 报告 + 报价草稿（**草稿永远带水印式阻断说明**）

agent_api.py      M33  受限只读接口（白名单函数 + JSON 输出，无写操作）
```

## 二、核心数据流

```
第二阶段 DeploymentCandidate ─┐
                              ├─→ M27 成本目录 + DeploymentCostPlan ─→ M28 TcoResult ─┐
成本目录（GPU/电力/空间/网络/软件/运维）┘                                              │
                                                                                      ├─→ M31 比较
第三阶段 RankedDeployment（性能与 SLA，只读）──────────────────────────────────────────┤
                                                                                      │
业务需求（请求量/时长/输入输出分布）─→ M29 RevenueResult（含容量比对）──────────────────┘
                                                                                      │
                                                                                      ↓
                                            M30 报价门禁（四维独立）──────────────→ M32 预算/敏感性/盈亏平衡
                                                                                      │
                                                                                      ↓
                                                                             M33 报告 + 报价草稿
```

## 三、关键接口签名

### 3.1 M28 TCO 引擎

```python
def compute_tco(
    plan: DeploymentCostPlan,
    items: Sequence[CostCatalogItem],
) -> TcoResult: ...

def build_cash_flow(
    tco: TcoResult,
    *,
    annual_revenue: Decimal | None = None,
    revenue_basis: str = "none",
) -> CashFlowResult: ...
```

**成本分解口径**（每年 vs 一次性严格分开）：

| price_basis | 数量来源 | 计入 |
|---|---|---|
| `one_time_per_gpu` | `gpu_count` | `capex_one_time` |
| `one_time_per_server` | `server_count` | `capex_one_time` |
| `per_gpu_year` | `gpu_count` | `annual_cash_opex` |
| `per_server_year` | `server_count` | `annual_cash_opex` |
| `per_kwh` | 功率 × PUE × 负载率 × 小时 | `annual_cash_opex` |
| `per_rack_year` / `per_kw_year` | 条目显式 `default_quantity` | `annual_cash_opex` |

**三种不参与计算的情形必须可区分**：

1. `unknown_missing_price` —— 目录项缺单价（**不得当 0**）；
2. `unknown_missing_basis` —— 缺计算所需假设（如 `per_kwh` 缺功率或 PUE）；
3. `excluded_by_cost_basis` —— 与买断/租赁口径不符（**排除 ≠ 零成本**）。

任一出现即 `TcoResult.incomplete = True`，并写入 `completeness_reasons`。
`incomplete=True` 时下游**不得**发布报价（M30 会再次校验）。

### 3.2 M29 收入引擎

```python
def compute_revenue(
    scenario: RevenueScenario,
    rule: BillingRule,
    *,
    capacity_evidence: CapacityEvidence | None = None,
) -> RevenueResult: ...
```

- 逐时段计算计费 token：`可计费缓存输入 = 输入 × cached_input_fraction`，
  缓存与未缓存分别套用各自单价；批处理时段乘 `(1 - batch_discount_fraction)`。
- `BillingRule.billable_cache_ratio` 是**计费口径**，其来源由
  `billable_cache_basis` 声明；`physical_cache_hit_rate` **不是**合法取值。
- 容量比对：仅当存在第三阶段的**可信实测产能**（`maximum_sustainable_capacity`
  或带 `exact_match` 的实测吞吐）时才可能给出 `within_capacity` / `exceeds_capacity`，
  否则恒为 `unknown_no_measurement`，且 `scenario_only = True`。

### 3.3 M30 报价门禁（V4.1 F4.1 修正后）

```python
def evaluate_quote_eligibility(
    *, candidate_id, requirement_id,
    tco: TcoResult | None,
    sla_evidence: SlaEvidenceRecord | None,   # 持久化证据记录
    sla_recheck: SlaRecheck | None,           # 服务端用记录快照重新推导的结果
    approval: QuoteApproval | None,           # 持久化审批记录
    confirmation: CommercialConfirmation | None,
    cost_snapshot_hash: str | None,           # 现算指纹，审批必须绑定同一个
    plan_id=None, scenario_id=None, billing_rule_id=None, as_of=None,
) -> QuoteEligibility: ...

def record_sla_evidence_for(db, *, candidate_id, requirement, facts, ...) -> dict
def import_quote_approvals(file, db) -> dict
```

**只接受引用，不接受状态字符串。** 四个维度独立判定，
`quote_publishable = AND(四维通过)`：

| 维度 | 通过条件 |
|---|---|
| `cost_estimate_status` | `TcoResult.incomplete == False`、币种已确认、无合成价格、无未确认条目、**覆盖策略已授权** |
| `sla_verification_status` | 存在证据记录，且**重新推导**得到 `observed_pass` + `exact_match`，且与记录结论一致 |
| `commercial_confirmation_status` | 币种 / 价格 / 条款三项人工确认，且在有效期内 |
| `approval_status` | 存在审批记录、`decision=approved`、作用域匹配、**成本快照指纹一致**、未过期 |

**服务端重新核验**（不接受"记录里写了就算"）：

1. 按证据记录里的**需求快照 + 目标身份**重新跑匹配与 SLA 判定；
2. 引用的实测必须仍存在，且重新匹配到的仍是同一条（否则 `evidence_run_superseded`）；
3. 重新推导的等级/状态必须与记录一致（否则 `sla_evidence_stale`）；
4. 成本快照指纹必须与审批一致（否则 `approval_cost_snapshot_stale`）。

**V4.2 追加的生产可信度门禁**（不可被任何策略放宽）：

* 证据的数据可信等级必须属于测量类（`synthetic_fixture` 一律拒绝）；
* 匹配证明不得仅限算法验证（`algorithm_validation_only`）；
* 当次 SLA 评估不得启用 `allow_synthetic`；
* 匹配证明不得声明禁止 `sla_proof`；
* **重放**得到的可信等级与质量标记同样要过这套门禁。

**V4.2 追加的快照绑定**：

* 重放使用记录里的 `target_identity`，调用方 facts 只用于比对；
* `run_pool_hash` 在复核时重算并比对（指纹含归一化指标快照）；
* 策略快照不一致即 `policy_changed`；
* 审批作用域还覆盖 `scenario_id` 与 `billing_rule_id`。

`quote_eligibility_from_claims()` 是**内部诊断**入口：恒返回
`quote_publishable=False` 且 `claims_only=True`，且不会把声明升级为 `verified_measured`。
Agent 接口另有**参数白名单**：传 `sla_status` 之类状态字符串会被直接拒绝。

### 3.4 M31 比较与 M32 敏感性

```python
def compare_deployments(
    *, requirement_id: str, ranking: DeploymentRanking,
    plans: Mapping[str, DeploymentCostPlan], tco_by_plan: Mapping[str, TcoResult],
    revenue_by_plan: Mapping[str, RevenueResult],
    eligibility_by_plan: Mapping[str, QuoteEligibility],
) -> DeploymentComparison: ...

def analyze_sensitivity(*, plan, items, drivers, metric) -> SensitivityAnalysis: ...
def check_budget(*, budget, value, currency_confirmed) -> BudgetCheck: ...
def analyze_breakeven(*, tco, revenue, rule) -> BreakevenResult: ...
```

- 比较**不产生单一总分**：`composite_score_used=False`、`economic_scoring_used=False`。
- 跨口径（不同币种 / 买断 vs 租赁 / 不同年限）的候选标为
  `not_comparable_*`，**不进入** `lowest_cost_candidate_id` 的评选范围。
- 币种未确认时预算不给 `within_budget` 结论。

### 3.5 M33 受限 Agent 接口

`agent_api.py` 只暴露**白名单只读函数**，每个都返回可 JSON 序列化的结构化结果，
并且**不接受**任何写库参数：

```python
AGENT_OPS = {
  "phase4.cost_catalog",        # 成本目录快照
  "phase4.tco",                 # 单方案 TCO
  "phase4.revenue",             # 收入情景
  "phase4.quote_eligibility",   # 报价资格
  "phase4.compare",             # 多候选比较
  "phase4.report",              # 报告（只读生成，不落库）
}
def call_agent_op(op: str, **kwargs) -> dict: ...
```

未知 `op` 一律抛错，不做"猜测式调用"。接口内**禁止**修改数据库与文件系统。

## 四、禁止事项（贯穿第四阶段）

1. 不得用 GPU 成本代替 TCO；
2. 不得把理论满载 Token 收入当作真实营业收入；
3. 不得把 SGLang 物理 Prefix Cache 命中率当作可计费缓存比例；
4. 不得在缺数据时用 0 补齐成本项或收益项；
5. 不得把估算性能说成已验证 SLA；
6. 不得在币种/合同价未确认时宣称成本结论"已确认"；
7. 不得让报价门禁被任何参数放宽；
8. 不得改变第一阶段的历史计算口径；
9. 本阶段不开发 Web 前端与自治商务 Agent。


---

## 五、接口冻结与部署级限制（V4.3）

第四阶段核心接口自 V4.3 起**冻结**：不再新增业务模块、Schema 层级或验收阶段，
后续只做修正与收敛。

### 5.1 冻结的调用契约

```python
# 报价资格：只接受引用，服务端重放核验
quote_eligibility_for(
    db, *, candidate_id, requirement_id, facts,
    sla_evaluation_id, approval_id, confirmation_id=None,
    plan_file=None, template_id=None, hardware_id=None,
    gpu_count=None, server_count=None,
    scenario_id=None, demand_file=None, billing_rule_id=None,
    region=None, as_of=None, policy=None,
) -> dict
```

* **计费规则必填且必须有效**：报价必然含计费条款，规则缺失/未确认/失效一律阻断；
  作用域比对基线只来自实际使用的规则对象。
* **四维门禁不可放宽**：成本完整+已授权覆盖策略 / SLA 生产级实测 /
  人工商务确认+有效计费规则 / 人工审批+成本快照绑定。
* **回退路径恒不可发布**：`quote_eligibility_from_claims()` 仅供诊断。

### 5.2 部署级限制（**不通过 RBAC 解决**）

> 本系统**未接入可信审批系统**：`import-quote-approvals` 只做持久化，
> 不校验审批人身份与权限。
>
> **因此本系统不得用于生产环境的报价自动发布。**
> 正式报价必须由人工在受控流程中签发。

改为引入完整 RBAC 属于部署侧能力，超出本仓库范围；
在审批基础设施到位前，这条限制是**唯一**的合规边界。

### 5.3 已知的收敛方向（下一阶段，尚未执行）

统一用户评估入口 · 收敛 `services.py` 编排 · 明确数据与算法扩展边界。
