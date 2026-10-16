"""第四阶段（M29/M30）：Token 计费、业务需求收益情景、商务确认与报价门禁。

两条纪律：

* **收入必须从业务需求出发**。第一阶段的理论满载 Token 收入继续保留，
  但这里不能把它当真实营业收入；收入要按请求量、输入输出分布、计费规则与
  使用时段算出来，再与可信容量对比。
* **不得把 SGLang 的物理 Prefix Cache 命中率当作可计费缓存 Token 比例**。
  可计费缓存比例是**合同/计费口径**，必须单独给出并声明依据。
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

#: 可计费缓存比例的来源依据。 ``physical_cache_hit_rate`` 被**显式禁止**：
#: 物理命中率是引擎内部行为，不构成计费依据。
BillableCacheBasis = Literal[
    "contract_defined",
    "vendor_published",
    "assumed",
    "unknown",
]

#: 收益情景与可信容量的比对结论（F4.6：不只看输出吞吐）
CapacityCheck = Literal[
    "within_capacity",          # 全部子项满足
    "partially_verified",       # 部分子项证据不足（**不得**当作容量可行）
    "exceeds_capacity",         # 至少一个子项不满足
    "unknown_no_measurement",   # 没有可信实测，无法判定
]

#: 单个容量子项的判定
CapacityCheckStatus = Literal["satisfied", "not_satisfied", "unknown"]


class CapacityCheckDetail(BaseModel):
    """综合容量校验明细（V4.1 F4.6）。

    仅"Output TPS 达标"不足以得出容量可行：还要看请求吞吐、并发与负载一致性、
    SLA 证据与可持续性。任一子项不满足即整体不满足；有子项无法判定则只能是
    ``partially_verified``。
    """

    model_config = ConfigDict(extra="forbid")

    output_throughput: CapacityCheckStatus
    request_throughput: CapacityCheckStatus
    concurrency_and_load: CapacityCheckStatus
    sla_evidence: CapacityCheckStatus
    sustainability: CapacityCheckStatus
    overall: CapacityCheck
    reasons: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _guard(self) -> "CapacityCheckDetail":
        subs = (
            self.output_throughput,
            self.request_throughput,
            self.concurrency_and_load,
            self.sla_evidence,
            self.sustainability,
        )
        if self.overall == "within_capacity" and any(s != "satisfied" for s in subs):
            raise ValueError("有子项未满足时不得判 within_capacity")
        if self.overall == "exceeds_capacity" and not any(
            s == "not_satisfied" for s in subs
        ):
            raise ValueError("判 exceeds_capacity 必须至少有一个子项 not_satisfied")
        if not self.reasons:
            raise ValueError("容量判定必须给出逐子项理由")
        return self

#: 商务确认状态
ConfirmationStatus = Literal["confirmed", "pending", "missing"]

#: 第三阶段 SLA 状态（第四阶段只读沿用）
SlaStatusLiteral = Literal[
    "observed_pass", "observed_fail", "estimated_only", "not_evaluable"
]
#: 第三阶段匹配等级（第四阶段只读沿用）
MatchLevelLiteral = Literal[
    "exact_match", "comparable_reference", "modeling_candidate", "rejected"
]

#: 报价发布门禁的四个独立维度
CostEstimateStatus = Literal["complete", "incomplete", "unknown"]
SlaVerificationStatus = Literal[
    "verified_measured",   # 有匹配实测且指标完整
    "estimated_only",      # 只有估算，不构成性能承诺
    "not_evaluable",       # 匹配/指标不足，无法评估
    "unverified",          # 未做评估
]
ApprovalStatus = Literal["approved", "pending", "missing"]


class BillingRule(BaseModel):
    """计费规则（M29）：客户合同/公开价目下的 Token 计费口径。"""

    model_config = ConfigDict(extra="forbid")

    rule_id: str
    model_name: str
    currency: str | None = None
    currency_status: Literal["confirmed", "unconfirmed", "assumed"] = "unconfirmed"

    price_uncached_input_per_1m: Decimal | None = None
    price_cached_input_per_1m: Decimal | None = None
    price_output_per_1m: Decimal | None = None

    #: 可计费缓存输入占比上限（0..1）。**不是**物理 prefix cache 命中率。
    #: 语义是"合同最多允许多大比例的输入按缓存价计费"——**合同上限**，
    #: 不是"保守估计"：缓存价低于未缓存价时，取更小的比例会**提高**收入预测。
    billable_cache_ratio: Decimal | None = None
    billable_cache_basis: BillableCacheBasis = "unknown"
    #: 失败请求是否计费（合同口径）。默认 False —— 未确认前不得假设失败也收费。
    billable_on_failure: bool = False

    #: 批处理/离线折扣（0..1）
    batch_discount_fraction: Decimal | None = None
    #: 单请求最小计费输入 token（不足按此计）
    min_billable_input_tokens_per_request: int | None = None
    #: 计费周期说明（如"按自然月结算"）
    settlement_note: str | None = None

    status: Literal["confirmed", "pending_business_confirmation", "synthetic_example"] = (
        "pending_business_confirmation"
    )
    valid_from: date | None = None
    valid_to: date | None = None
    evidence_id: str
    notes: str | None = None

    @field_validator(
        "price_uncached_input_per_1m", "price_cached_input_per_1m", "price_output_per_1m"
    )
    @classmethod
    def _non_negative(cls, v: Decimal | None) -> Decimal | None:
        if v is not None and v < 0:
            raise ValueError("Token 单价不允许为负值")
        return v

    @field_validator("billable_cache_ratio", "batch_discount_fraction")
    @classmethod
    def _fraction(cls, v: Decimal | None) -> Decimal | None:
        if v is not None and not (Decimal("0") <= v <= Decimal("1")):
            raise ValueError("比例字段必须落在 0..1")
        return v

    @model_validator(mode="after")
    def _guard(self) -> "BillingRule":
        if self.billable_cache_ratio is not None and self.billable_cache_basis == "unknown":
            raise ValueError(
                "给出 billable_cache_ratio 时必须声明 billable_cache_basis"
                "（可计费缓存比例是计费口径，不能默认来自引擎物理命中率）"
            )
        if self.status == "confirmed" and self.currency_status != "confirmed":
            raise ValueError("status=confirmed 的计费规则必须同时 currency_status=confirmed")
        return self


class DemandPeriod(BaseModel):
    """一个业务时段的需求量（M29）。时段是收入计算的最小单位。"""

    model_config = ConfigDict(extra="forbid")

    label: str
    duration_hours: Decimal
    request_count: int
    avg_input_tokens: Decimal
    avg_output_tokens: Decimal
    #: 业务侧估计的"可复用前缀"占比（用于选择缓存价），**不是**引擎命中率
    cached_input_fraction: Decimal = Decimal("0")
    #: 是否需要按峰值容量校验（峰值时段必须有可信容量证据）
    is_peak: bool = False
    #: 该时段是否走批处理折扣
    batch: bool = False
    #: 成功提供服务的比例（0..1]。需求请求量 ≠ 成功服务量 ≠ 可计费量，
    #: 三者必须分开记录，否则收入会被按"全部成功"高估。
    success_rate: Decimal = Decimal("1")

    @field_validator("duration_hours")
    @classmethod
    def _positive_hours(cls, v: Decimal) -> Decimal:
        if v <= 0:
            raise ValueError("duration_hours 必须为正")
        return v

    @field_validator("request_count")
    @classmethod
    def _non_negative_int(cls, v: int) -> int:
        if v < 0:
            raise ValueError("request_count 不允许为负")
        return v

    @field_validator("avg_input_tokens", "avg_output_tokens")
    @classmethod
    def _non_negative_dec(cls, v: Decimal) -> Decimal:
        if v < 0:
            raise ValueError("token 数不允许为负")
        return v

    @field_validator("cached_input_fraction")
    @classmethod
    def _fraction(cls, v: Decimal) -> Decimal:
        if not (Decimal("0") <= v <= Decimal("1")):
            raise ValueError("cached_input_fraction 必须落在 0..1")
        return v

    @field_validator("success_rate")
    @classmethod
    def _success_rate(cls, v: Decimal) -> Decimal:
        if not (Decimal("0") < v <= Decimal("1")):
            raise ValueError("success_rate 必须落在 (0, 1]")
        return v


class RevenueScenario(BaseModel):
    """业务需求情景（M29 的输入）：按请求量而不是理论满载推算收入。"""

    model_config = ConfigDict(extra="forbid")

    scenario_id: str
    model_name: str
    billing_rule_id: str
    #: 情景口径说明（客户类型、业务线、假设来源）
    scope_notes: list[str] = Field(default_factory=list)
    periods: list[DemandPeriod] = Field(default_factory=list)
    #: 一年覆盖多少天（用于把"一天"的时段表外推到年度）
    days_per_year: int = 365
    #: 时段表覆盖的周期；``one_day`` 时年度收入 = 时段合计 × days_per_year
    periods_cover: Literal["one_day", "one_year"] = "one_day"

    @field_validator("days_per_year")
    @classmethod
    def _days(cls, v: int) -> int:
        if v < 1:
            raise ValueError("days_per_year 至少为 1")
        return v

    @model_validator(mode="after")
    def _guard(self) -> "RevenueScenario":
        if not self.periods:
            raise ValueError("RevenueScenario 至少要有一个需求时段")
        labels = [p.label for p in self.periods]
        if len(labels) != len(set(labels)):
            raise ValueError("时段 label 不得重复（用于可追溯的逐时段结果）")
        return self


class PeriodRevenue(BaseModel):
    """单个时段的计费与收入结果。"""

    model_config = ConfigDict(extra="forbid")

    label: str
    #: 需求 / 成功服务 / 可计费 三段请求量（三者不一定相等）
    demand_requests: Decimal
    served_requests: Decimal
    failed_requests: Decimal
    billable_requests: Decimal

    billable_input_tokens_uncached: Decimal
    billable_input_tokens_cached: Decimal
    billable_output_tokens: Decimal
    input_revenue: Decimal
    output_revenue: Decimal
    gross_revenue: Decimal
    batch_discount: Decimal = Decimal("0")
    #: 按**当前计费口径**算出的收入（基准值，不等于保守下界）
    revenue: Decimal
    #: 收入区间：最大折扣（lower）与完全不打折（upper）。基准值落在区间内。
    revenue_lower_bound: Decimal | None = None
    revenue_upper_bound: Decimal | None = None
    #: 实际采用的缓存计费规则说明
    cache_rule: str
    #: 该时段的平均请求速率（req/s），峰值时段用于容量校验
    request_rate_rps: Decimal
    is_peak: bool = False


class RevenueResult(BaseModel):
    """收入情景结果（M29）。"""

    model_config = ConfigDict(extra="forbid")

    scenario_id: str
    model_name: str
    billing_rule_id: str
    currency: str | None
    currency_status: Literal["confirmed", "unconfirmed", "assumed"] = "unconfirmed"
    billing_rule_status: Literal["confirmed", "pending_business_confirmation", "synthetic_example"]

    #: 时段合计覆盖的周期：一天（外推 × days_per_year）还是一年
    periods_cover: Literal["one_day", "one_year"] = "one_day"

    periods: list[PeriodRevenue] = Field(default_factory=list)
    #: 表内时段合计（未外推）
    period_revenue_total: Decimal
    #: 外推到年度后的收入
    annual_revenue: Decimal
    #: 年度总 token（用于单位经济性）
    annual_billable_input_tokens: Decimal
    annual_billable_output_tokens: Decimal
    annual_requests: int
    #: 年度三段请求量（需求 / 成功服务 / 可计费）
    annual_demand_requests: Decimal
    annual_served_requests: Decimal
    annual_billable_requests: Decimal
    annual_failed_requests: Decimal
    #: 收入区间（缓存计费不确定性的边界）；基准值 annual_revenue 落在区间内，
    #: **不构成**"保守下界"——缓存价低于未缓存价时，取更小的缓存比例会提高收入。
    annual_revenue_lower_bound: Decimal | None = None
    annual_revenue_upper_bound: Decimal | None = None
    #: 缓存计费口径说明（合同上限 / 业务估计未受约束）
    cache_rule: str = "unknown"

    #: 峰值时段请求速率（容量校验用）
    peak_request_rate_rps: Decimal | None = None
    #: 与第三阶段可信容量的比对结论（= capacity_detail.overall）
    capacity_check: CapacityCheck = "unknown_no_measurement"
    #: 逐子项判定明细（输出吞吐 / 请求吞吐 / 并发负载 / SLA / 可持续性）
    capacity_detail: CapacityCheckDetail | None = None
    #: 比对依据说明（哪条实测、什么并发、什么产能）
    capacity_basis: list[str] = Field(default_factory=list)

    #: 该收入口径是否为"仅情景"（未与可信容量对齐时为 True）
    scenario_only: bool = True
    warnings: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _guard(self) -> "RevenueResult":
        if self.capacity_check == "within_capacity" and not self.capacity_basis:
            raise ValueError("判定 within_capacity 必须给出容量依据（哪条实测/什么产能）")
        if self.capacity_detail is not None and self.capacity_detail.overall != self.capacity_check:
            raise ValueError("capacity_check 必须等于 capacity_detail.overall")
        lo, hi, base = (
            self.annual_revenue_lower_bound,
            self.annual_revenue_upper_bound,
            self.annual_revenue,
        )
        if lo is not None and hi is not None and not (lo <= base <= hi):
            raise ValueError(
                "基准收入必须落在 [下界, 上界] 区间内（区间是缓存计费不确定性的边界）"
            )
        return self


class ConfirmationScope(BaseModel):
    """商务确认的**结构化**作用域（S0）。

    自由文本的 ``scope`` 无法与当前报价比对，因此确认必须声明它到底确认了
    哪些候选、需求、模型与计费规则。任一清单为空即视为"未限定"，
    不能支撑正式报价。
    """

    model_config = ConfigDict(extra="forbid")

    candidate_ids: list[str] = Field(default_factory=list)
    requirement_ids: list[str] = Field(default_factory=list)
    model_names: list[str] = Field(default_factory=list)
    billing_rule_ids: list[str] = Field(default_factory=list)

    @property
    def is_scoped(self) -> bool:
        return bool(self.candidate_ids and self.requirement_ids)


class CommercialConfirmation(BaseModel):
    """商务确认记录（M30）。**人工**确认，Agent 不得自行生成确认。"""

    model_config = ConfigDict(extra="forbid")

    confirmation_id: str
    scope: str
    #: 结构化作用域（S0）：正式报价必须能把确认与当前候选/需求对上
    scope_refs: ConfirmationScope | None = None
    #: 币种是否已确认
    currency_confirmed: bool = False
    #: 合同/目录价格是否已确认
    price_confirmed: bool = False
    #: 条款（计费规则、有效期、SLA 承诺口径）是否已确认
    terms_confirmed: bool = False
    confirmed_by: str | None = None
    confirmed_at: datetime | None = None
    #: 价格有效期
    valid_until: date | None = None
    document_ref: str | None = None
    notes: str | None = None

    @model_validator(mode="after")
    def _guard(self) -> "CommercialConfirmation":
        if self.currency_confirmed and not self.confirmed_by:
            raise ValueError("币种已确认时必须记录确认人")
        if self.status == "confirmed":
            missing = [
                name
                for name in ("confirmed_by", "confirmed_at", "valid_until", "document_ref")
                if getattr(self, name) is None
            ]
            if missing:
                raise ValueError(
                    f"status=confirmed 的商务确认必须齐备审计字段，缺少 {missing}"
                    "（人工确认需要可追溯的确认人、时间、有效期与凭据）"
                )
        return self

    @property
    def status(self) -> ConfirmationStatus:
        if self.currency_confirmed and self.price_confirmed and self.terms_confirmed:
            return "confirmed"
        if self.currency_confirmed or self.price_confirmed or self.terms_confirmed:
            return "pending"
        return "missing"


class QuoteEligibility(BaseModel):
    """正式报价资格（M30）。**四个维度相互独立**，任一不通过即不可发布。"""

    model_config = ConfigDict(extra="forbid")

    candidate_id: str
    plan_id: str | None = None
    scenario_id: str | None = None
    billing_rule_id: str | None = None

    cost_estimate_status: CostEstimateStatus
    sla_verification_status: SlaVerificationStatus
    commercial_confirmation_status: ConfirmationStatus
    approval_status: ApprovalStatus
    #: 本次判定实际引用的**持久化**证据 ID（V4.1 F4.1）
    sla_evaluation_id: str | None = None
    approval_id: str | None = None
    confirmation_id: str | None = None
    #: 判定时的成本快照指纹（审批必须绑定同一个）
    cost_snapshot_hash: str | None = None
    #: 人工审批凭据编号（来自审批记录，不接受调用方自填）
    approval_ref: str | None = None
    #: 是否走了"仅凭调用方声明"的诊断路径（该路径**永不可发布**）
    claims_only: bool = False

    #: 四个维度全部通过才可能为 True（且必须满足有效期等附加条件）
    quote_publishable: bool = False
    blockers: list[str] = Field(default_factory=list)
    #: 通过但仍需注意的事项（如"仅算法验证数据支撑"）
    cautions: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _guard(self) -> "QuoteEligibility":
        expected = (
            self.cost_estimate_status == "complete"
            and self.sla_verification_status == "verified_measured"
            and self.commercial_confirmation_status == "confirmed"
            and self.approval_status == "approved"
        )
        if self.quote_publishable and not expected:
            raise ValueError("四维门禁未全通过时不得标记 quote_publishable")
        if self.quote_publishable and self.blockers:
            raise ValueError("quote_publishable=True 时不得存在 blockers")
        if self.claims_only and self.quote_publishable:
            raise ValueError("仅凭调用方声明的诊断路径永不可发布")
        if self.approval_status == "approved" and not self.approval_id:
            raise ValueError(
                "approval_status=approved 必须引用真实的审批记录 approval_id"
                "（非空字符串不算授权证明）"
            )
        if self.sla_verification_status == "verified_measured" and not self.sla_evaluation_id:
            raise ValueError("verified_measured 必须引用真实的 SLA 证据记录 sla_evaluation_id")
        return self


# ---------------------------------------------------------------------------
# V4.1 F4.1：证据链记录 —— 报价资格只能来自**持久化**的 SLA 与审批记录
# ---------------------------------------------------------------------------


class SlaEvidenceRecord(BaseModel):
    """一条持久化的 SLA 证据。

    报价门禁**只认这个记录**，不认调用方传来的状态字符串。
    记录里保存了评估时使用的需求快照与目标身份，报价时服务端会用它们
    **重新推导**匹配等级与 SLA 状态：结论变了（数据被替换、需求被改动）
    就视为证据失效。
    """

    model_config = ConfigDict(extra="forbid")

    sla_evaluation_id: str
    candidate_id: str
    requirement_id: str
    #: 评估时使用的需求快照（重新核验时用这份，而不是调用方现填的）
    requirement: dict[str, Any]
    #: 评估时使用的目标身份快照
    target_identity: dict[str, Any]
    status: SlaStatusLiteral
    match_level: MatchLevelLiteral | None = None
    #: 代表运行 ID：单条实测是 run_id，多条聚合是 ``aggregate:<成员 run_id 列表>``
    matched_run_id: str | None = None
    #: **参与该结论的全部实测 run_id**（V4.2 E2）：聚合证据必须保存成员列表，
    #: 复核时据此重放，而不是拿 ``aggregate:...`` 字符串去原始表里查。
    supporting_run_ids: list[str] = Field(default_factory=list)
    data_trust: str | None = None

    # ---- 生产可信度（V4.2 E1）------------------------------------------
    #: 匹配结论是否**仅用于算法验证**（第三阶段 allow_synthetic 路径）
    algorithm_validation_only: bool = False
    #: 当次 SLA 评估是否启用了 allow_synthetic
    allow_synthetic_used: bool = False
    #: 匹配证明是否声明禁止用于 sla_proof
    sla_proof_blocked: bool = False
    #: 评估策略快照（关键字段），用于复核时判断策略是否被更换
    policy_snapshot: dict[str, Any] = Field(default_factory=dict)

    policy_id: str
    evaluated_at: datetime
    #: 评估时 run 池的指纹（含归一化指标快照；数据变化即视为证据不可复现）
    run_pool_hash: str
    #: 覆盖"目标身份 + 需求 + 匹配证明 + 参与 Run 指标 + 策略 + 商务方案"的规范化指纹
    input_snapshot_hash: str

    @model_validator(mode="after")
    def _guard(self) -> "SlaEvidenceRecord":
        if self.status == "observed_pass" and self.match_level != "exact_match":
            raise ValueError("observed_pass 的 SLA 证据必须来自 exact_match")
        if self.status in ("observed_pass", "observed_fail") and not self.matched_run_id:
            raise ValueError("实测类 SLA 结论必须绑定 matched_run_id")
        if self.matched_run_id and not self.supporting_run_ids:
            raise ValueError(
                "必须记录 supporting_run_ids：聚合证据（aggregate:...）不是真实 run_id，"
                "没有成员列表就无法重放"
            )
        if self.matched_run_id and self.matched_run_id.startswith("aggregate:"):
            members = ",".join(sorted(self.supporting_run_ids))
            if self.matched_run_id != f"aggregate:{members}":
                raise ValueError(
                    "聚合证据的 matched_run_id 必须等于 aggregate:<排序后的成员 run_id>"
                )
        if self.algorithm_validation_only and self.status == "observed_pass":
            # 允许记录，但调用方（门禁）必须能看出它不能用于生产
            pass
        return self


class ApprovalScope(BaseModel):
    """审批的作用域：这份审批到底批的是**哪一个**方案。"""

    model_config = ConfigDict(extra="forbid")

    candidate_id: str
    requirement_id: str
    plan_id: str
    scenario_id: str | None = None
    billing_rule_id: str | None = None
    #: 审批时看到的**计费规则内容**指纹（S0）：同一个 rule_id 改了单价或条款，
    #: 旧审批也必须失效 —— 只绑 ID 是不够的
    billing_rule_hash: str | None = None
    #: 审批时看到的成本快照指纹；成本变了旧审批自动失效
    cost_snapshot_hash: str


class QuoteApproval(BaseModel):
    """人工审批记录（V4.1 F4.1）。

    **审批是一条可核验的记录，不是调用方给的一个非空字符串。**
    ``decision=approved`` 必须齐备审批人、时间、有效期、凭据与作用域；
    Agent 无法通过任何接口生成它。
    """

    model_config = ConfigDict(extra="forbid")

    approval_id: str
    decision: Literal["approved", "rejected", "pending"]
    approved_by: str | None = None
    approved_at: datetime | None = None
    valid_until: date | None = None
    document_ref: str | None = None
    scope: ApprovalScope
    notes: str | None = None

    @model_validator(mode="after")
    def _guard(self) -> "QuoteApproval":
        if self.decision == "approved":
            missing = [
                name
                for name in ("approved_by", "approved_at", "valid_until", "document_ref")
                if getattr(self, name) is None
            ]
            if missing:
                raise ValueError(
                    f"decision=approved 的审批必须齐备 {missing}"
                    "（人工审批需要可追溯的审批人、时间、有效期与凭据）"
                )
        return self
