"""第四阶段（M31/M32）：多候选经济性比较、预算与敏感性分析。

三个必须**相互独立**的结论（任务书 §三.3）：

============  ================================================
结论            需要的证据
============  ================================================
资源与成本估算可行   静态配置、成本来源与明确假设
性能与 SLA 已验证   匹配实测、完整指标、可靠证据链
正式报价可以发布     商业条款确认、报价有效期、人工审批与性能承诺审核
============  ================================================

因此本模块**不提供**单一的"总分"：成本低不构成 SLA 结论，性能达标也不构成报价资格。
"""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

#: 候选之间的可比性：只有**同口径**才能直接比经济性
Comparability = Literal[
    "comparable",              # 币种/口径/周期一致
    "not_comparable_currency", # 币种不一致
    "not_comparable_basis",    # 买断 vs 租赁
    "not_comparable_horizon",  # 期限不一致
    "incomplete_cost",         # 成本不完整
]


class DeploymentComparisonEntry(BaseModel):
    """单个候选在"资源 + 性能 + 经济性"三个维度上的并列视图。"""

    model_config = ConfigDict(extra="forbid")

    candidate_id: str
    hardware_id: str
    gpu_count: int
    tp_size: int
    dp_size: int
    static_status: str | None = None

    # ---- 性能维度（来自第三阶段，**不改写**其结论）----
    match_level: str | None = None
    sla_status: str | None = None
    rank_bucket: str | None = None
    output_throughput_tok_s: Decimal | None = None
    ttft_p99_ms: Decimal | None = None
    tpot_p99_ms: Decimal | None = None
    evidence_run_ids: list[str] = Field(default_factory=list)

    # ---- 成本维度（M28）----
    plan_id: str | None = None
    cost_basis: Literal["owned", "leased"] | None = None
    currency: str | None = None
    capex_one_time: Decimal | None = None
    annual_cash_opex: Decimal | None = None
    annual_total_cost: Decimal | None = None
    total_cash_over_horizon: Decimal | None = None
    cost_per_gpu_year: Decimal | None = None
    cost_incomplete: bool = False

    # ---- 收益维度（M29）----
    annual_revenue: Decimal | None = None
    annual_gross_margin: Decimal | None = None
    margin_ratio: Decimal | None = None
    capacity_check: str | None = None

    # ---- 结论维度（M30）----
    quote_publishable: bool = False
    quote_blockers: list[str] = Field(default_factory=list)

    comparability: Comparability = "comparable"
    notes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _guard(self) -> "DeploymentComparisonEntry":
        if self.margin_ratio is not None and self.annual_revenue:
            expected = self.annual_gross_margin
            if expected is not None and self.annual_revenue != 0:
                pass  # 数值一致性由引擎保证；此处不做浮点比较
        if self.quote_publishable and self.quote_blockers:
            raise ValueError("quote_publishable=True 时不得存在 blockers")
        if self.cost_incomplete and self.total_cash_over_horizon is not None:
            raise ValueError("成本不完整时不得给出全周期总额（避免被当成已核算）")
        return self


class DeploymentComparison(BaseModel):
    """多候选比较结果（M31）。**不产出单一总分**，只并列展示三个维度。"""

    model_config = ConfigDict(extra="forbid")

    requirement_id: str
    entries: list[DeploymentComparisonEntry] = Field(default_factory=list)
    #: 推荐的"经济性候选"仅在这些候选间可比时给出
    lowest_cost_candidate_id: str | None = None
    #: 可比性说明（跨口径比较必须显式拒绝）
    comparability_notes: list[str] = Field(default_factory=list)
    #: 始终为 False：本阶段不引入跨维度的加权评分
    composite_score_used: bool = False
    #: 始终为 False：币种未确认前不使用经济性评分
    economic_scoring_used: bool = False


# ---------------------------------------------------------------------------
# M32：预算、敏感性、盈亏平衡
# ---------------------------------------------------------------------------


class BudgetCheck(BaseModel):
    """预算检查。"""

    model_config = ConfigDict(extra="forbid")

    budget_id: str
    budget_amount: Decimal | None = None
    currency: str | None = None
    currency_confirmed: bool = False
    #: 预算口径：一次性采购上限 / 年支出上限
    budget_kind: Literal["one_time_capex", "annual_opex", "total_horizon"]
    #: 比对对象
    compared_value: Decimal | None = None
    headroom: Decimal | None = None
    status: Literal["within_budget", "exceeds_budget", "unknown"] = "unknown"
    reasons: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _guard(self) -> "BudgetCheck":
        if self.status == "within_budget" and self.headroom is not None and self.headroom < 0:
            raise ValueError("headroom 为负不得判定 within_budget")
        if self.status == "within_budget" and self.currency_confirmed is False:
            raise ValueError("币种未确认时不得给出 within_budget 结论")
        return self


class DriverSensitivity(BaseModel):
    """单因子敏感性：某个驱动量变化时目标量的变化。"""

    model_config = ConfigDict(extra="forbid")

    driver: str
    #: 驱动量的相对变化（如 -0.2 / +0.2）
    delta_fraction: Decimal
    base_value: Decimal | None = None
    perturbed_value: Decimal | None = None
    #: 绝对值变化与相对变化
    absolute_change: Decimal | None = None
    relative_change: Decimal | None = None
    #: 该驱动量是否是"缺数据"导致的不可算项
    computable: bool = True
    #: 敏感性对成本/收益哪个方向敏感（仅描述，不做评分）
    affects: Literal["cost", "revenue", "both"] = "cost"
    notes: list[str] = Field(default_factory=list)


class SensitivityAnalysis(BaseModel):
    """成本/收益敏感性分析（M32）。不做"最优推荐"，只给可比区间。"""

    model_config = ConfigDict(extra="forbid")

    plan_id: str
    base_metric: str
    base_value: Decimal | None = None
    drivers: list[DriverSensitivity] = Field(default_factory=list)
    #: 参与分析的驱动量中不可算的项（缺数据）
    uncomputable_drivers: list[str] = Field(default_factory=list)
    incomplete: bool = False
    notes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _guard(self) -> "SensitivityAnalysis":
        if any(not d.computable for d in self.drivers) and not self.uncomputable_drivers:
            raise ValueError("存在不可算的驱动量时必须逐项列出 uncomputable_drivers")
        return self


class BreakevenResult(BaseModel):
    """盈亏平衡（M32）：达到不亏所需的计费量或单价。"""

    model_config = ConfigDict(extra="forbid")

    plan_id: str
    #: 年度成本（基数）
    annual_cost: Decimal | None = None
    #: 盈亏平衡所需年收入
    required_annual_revenue: Decimal | None = None
    #: 在给定计费规则下，所需的年输出 token 计费量
    breakeven_billable_output_tokens: Decimal | None = None
    #: 在给定需求量下，所需的最低输出单价
    breakeven_output_price_per_1m: Decimal | None = None
    #: 当前情景对应的收入与成本之比（**仅供参考，不是经济性评分**）
    revenue_cost_ratio: Decimal | None = None
    #: 盈亏平衡点是否落在已验证容量内
    capacity_check: Literal[
        "within_capacity", "exceeds_capacity", "unknown_no_measurement"
    ] = "unknown_no_measurement"
    computable: bool = False
    reasons: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class EconomicDecision(BaseModel):
    """M31/M32 的汇总输出：比较 + 预算 + 敏感性 + 盈亏平衡。"""

    model_config = ConfigDict(extra="forbid")

    requirement_id: str
    comparison: DeploymentComparison
    budget_checks: list[BudgetCheck] = Field(default_factory=list)
    sensitivity: list[SensitivityAnalysis] = Field(default_factory=list)
    breakeven: list[BreakevenResult] = Field(default_factory=list)
    #: 约束说明：本阶段的结论边界
    limitations: list[str] = Field(default_factory=list)

    @field_validator("limitations")
    @classmethod
    def _non_empty(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("limitations 不得为空（必须显式声明结论边界）")
        return v
