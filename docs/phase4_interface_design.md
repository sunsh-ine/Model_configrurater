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

### 3.3 M30 报价门禁

```python
def evaluate_quote_eligibility(
    *, candidate_id: str, tco: TcoResult | None, sla_status: str | None,
    confirmation: CommercialConfirmation | None, approval_status: str,
    as_of: date | None = None,
) -> QuoteEligibility: ...
```

四个维度**独立**判定，`quote_publishable = AND(四维通过 AND 有效期未过)`：

| 维度 | 通过条件 |
|---|---|
| `cost_estimate_status` | `TcoResult.incomplete == False` 且币种已确认 且无合成价格 |
| `sla_verification_status` | `SlaEvaluation.status == observed_pass` 且匹配等级为 `exact_match` |
| `commercial_confirmation_status` | 币种 / 价格 / 条款三项全部人工确认 |
| `approval_status` | 人工审批记录为 `approved` |

任意一项未过 → `quote_publishable = False`，并把**每一项**不通过的原因写进 `blockers`。
门禁是纯函数：不知道、没确认、缺数据一律**不通过**（fail-closed）。

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
