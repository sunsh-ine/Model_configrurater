"""S1：统一评估入口的数据契约。

**一份配置 + 一条命令 + 一份结果。**

* ``PlanRequest`` 复用既有模型、负载、资源、SLA、预算与成本字段，
  不新造平行概念；必需字段只有"模型 + 候选硬件 + 目标负载"。
* ``PlanResult`` 汇总四个领域（Sizing / Performance / Economics / Governance）
  的结果，并把**缺口**与**阻断原因**显式列出，而不是报错或补默认值。

设计约束（计划 §1/§3）：

* 缺数据时状态是 ``not_configured`` / ``missing_data``，**不使用虚构默认值**；
* 领域状态与既有状态码（`feasibility.status` / `match_level` / `sla_status` /
  `rank_bucket` / `quote_publishable`）保持一致，不建立平行的状态体系；
* 本模块只做"组装"：不重新实现任何计算或门禁。
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from llm_capacity_pricing.schemas.performance import SlaRequirement

#: 领域结论状态（与既有状态码并存，不替代）
DomainStatus = Literal[
    "computed",         # 已算出结论
    "not_configured",   # 用户未提供该领域所需输入
    "missing_data",     # 提供了输入但库里缺数据
    "blocked",          # 有明确阻断原因（如门禁未过）
    "error",            # 计算过程出错（原因见 blockers）
]

#: 整体状态：只表示"这份评估产出了什么"，**不是**报价结论
PlanStatus = Literal["ok", "partial", "blocked"]

PlanDomain = Literal["input", "sizing", "performance", "economics", "governance"]


class PlanWorkload(BaseModel):
    """目标负载：必需项尽量少，专业字段放 advanced。"""

    model_config = ConfigDict(extra="forbid")

    input_len: int
    output_len: int | None = None
    concurrency: int | None = None
    dataset: str | None = None
    arrival_mode: str = "unknown"

    @field_validator("input_len")
    @classmethod
    def _positive(cls, v: int) -> int:
        if v < 1:
            raise ValueError("input_len 必须为正")
        return v


class PlanResourceAssumptions(BaseModel):
    """资源假设。全部可空 —— 缺了就如实标为未验证，不猜。"""

    model_config = ConfigDict(extra="forbid")

    per_gpu_usable_bytes: int | None = None
    usable_fraction: Decimal | None = None
    usable_source: str | None = None
    #: measured / operator_estimate / synthetic_example
    usable_source_kind: str | None = None
    runtime_reserve_fraction: Decimal | None = None
    runtime_reserve_source: str | None = None
    max_context_override: int | None = None
    max_context_override_source: str | None = None


class PlanCostInput(BaseModel):
    """成本侧输入（可选）。不给就走"未配置"，不是错误。"""

    model_config = ConfigDict(extra="forbid")

    plan_file: str
    template_id: str
    region: str | None = None
    as_of: str | None = None


class PlanDemandInput(BaseModel):
    """业务需求情景（可选）。"""

    model_config = ConfigDict(extra="forbid")

    demand_file: str
    scenario_id: str


class PlanGovernanceInput(BaseModel):
    """治理侧引用（可选）。三者齐备才评估报价资格；缺一个都不放行。"""

    model_config = ConfigDict(extra="forbid")

    sla_evaluation_id: str | None = None
    approval_id: str | None = None
    confirmation_id: str | None = None
    billing_rule_id: str | None = None


class PlanBudgetInput(BaseModel):
    """预算（可选）。"""

    model_config = ConfigDict(extra="forbid")

    budget_id: str
    amount: Decimal
    kind: Literal["one_time_capex", "annual_opex", "total_horizon"] = "total_horizon"
    currency: str | None = None

    @field_validator("amount")
    @classmethod
    def _non_negative(cls, v: Decimal) -> Decimal:
        if v < 0:
            raise ValueError("预算金额不允许为负")
        return v


class PlanAdvanced(BaseModel):
    """专业字段：普通用户不需要理解，但专业用户可覆盖。"""

    model_config = ConfigDict(extra="forbid")

    tp_sizes: list[int] = Field(default_factory=lambda: [1, 2, 4, 8])
    dp_sizes: list[int] = Field(default_factory=lambda: [1, 2, 4, 8])
    block_size_tokens: int | None = None
    weight_precision: str | None = None
    kv_precision: str = "bf16"
    #: 目标身份补充项：需要与已签发的 SLA 证据对齐时才填
    model_revision: str | None = None
    framework: str | None = None
    framework_version: str | None = None

    @field_validator("tp_sizes", "dp_sizes")
    @classmethod
    def _non_empty(cls, v: list[int]) -> list[int]:
        if not v:
            raise ValueError("tp_sizes / dp_sizes 不得为空")
        if any(x < 1 for x in v):
            raise ValueError("tp_sizes / dp_sizes 必须为正整数")
        return v


class PlanRequest(BaseModel):
    """统一评估输入。"""

    model_config = ConfigDict(extra="forbid")

    request_id: str = "plan"
    model_id: str
    #: 候选硬件（可多个，逐一出候选方案）
    hardware_ids: list[str]
    server_id: str | None = None
    workload: PlanWorkload
    resources: PlanResourceAssumptions = Field(default_factory=PlanResourceAssumptions)

    # ---- 按场景按需填写（全部可选）----
    sla: SlaRequirement | None = None
    cost: PlanCostInput | None = None
    demand: PlanDemandInput | None = None
    budget: PlanBudgetInput | None = None
    governance: PlanGovernanceInput | None = None

    advanced: PlanAdvanced = Field(default_factory=PlanAdvanced)
    #: 结果中是否包含未验证/未配置领域的说明（默认包含，如实输出）
    include_gaps: bool = True

    @field_validator("hardware_ids")
    @classmethod
    def _hardware_non_empty(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("hardware_ids 至少给出一个候选硬件")
        if len(set(v)) != len(v):
            raise ValueError("hardware_ids 不得重复")
        return v

    @model_validator(mode="after")
    def _guard(self) -> "PlanRequest":
        if self.sla is None and self.governance is not None:
            raise ValueError(
                "给出治理引用（审批/证据）时必须同时给出 sla 需求："
                "没有需求就无法核验性能承诺"
            )
        return self


class PlanCandidate(BaseModel):
    """单个候选在四个领域上的并列视图。"""

    model_config = ConfigDict(extra="forbid")

    candidate_id: str
    hardware_id: str
    tp_size: int
    dp_size: int
    gpu_count: int
    nodes_required: int | None = None

    # ---- Sizing ----
    static_status: str
    headroom_bytes: int | None = None
    static_reasons: list[str] = Field(default_factory=list)

    # ---- Performance（原样取自第三阶段）----
    performance_status: DomainStatus = "not_configured"
    match_level: str | None = None
    sla_status: str | None = None
    rank_bucket: str | None = None
    output_throughput_tok_s: Decimal | None = None
    ttft_p99_ms: Decimal | None = None
    tpot_p99_ms: Decimal | None = None
    evidence_run_ids: list[str] = Field(default_factory=list)

    # ---- Economics ----
    economics_status: DomainStatus = "not_configured"
    plan_id: str | None = None
    cost_basis: str | None = None
    currency: str | None = None
    capex_one_time: Decimal | None = None
    annual_cash_opex: Decimal | None = None
    annual_total_cost: Decimal | None = None
    total_cash_over_horizon: Decimal | None = None
    cost_incomplete: bool = True
    coverage_gaps: list[str] = Field(default_factory=list)
    annual_revenue: Decimal | None = None
    capacity_check: str | None = None
    budget_status: str | None = None

    # ---- Governance ----
    governance_status: DomainStatus = "not_configured"
    quote_publishable: bool = False
    quote_blockers: list[str] = Field(default_factory=list)

    notes: list[str] = Field(default_factory=list)


class PlanBlocker(BaseModel):
    """结构化阻断/错误原因：领域 + 原因码 + 可读说明。"""

    model_config = ConfigDict(extra="forbid")

    domain: PlanDomain
    code: str
    message: str
    candidate_id: str | None = None


class PlanResult(BaseModel):
    """统一评估结果。

    ``status`` 只描述"这次评估产出了什么"：
    ``ok`` = 所有已配置领域都算出结论；``partial`` = 有领域未配置或缺数据；
    ``blocked`` = 输入有错或全部候选被排除。**它不是报价结论。**
    """

    model_config = ConfigDict(extra="forbid")

    request_id: str
    status: PlanStatus
    #: 领域 -> DomainStatus（同时给出四个领域，未配置就写 not_configured）
    domains: dict[str, str] = Field(default_factory=dict)

    request_echo: dict[str, Any] = Field(default_factory=dict)
    candidates: list[PlanCandidate] = Field(default_factory=list)

    blockers: list[PlanBlocker] = Field(default_factory=list)
    #: 还缺哪些输入才能让更多领域出结论
    missing_inputs: list[str] = Field(default_factory=list)
    #: 建议的下一步（补什么数据 / 做什么验证）
    next_data_needed: list[str] = Field(default_factory=list)
    #: 推荐依据（**不做综合评分**，只给可解释的比较结论）
    recommendations: list[str] = Field(default_factory=list)

    summary: dict[str, Any] = Field(default_factory=dict)
    limitations: list[str] = Field(default_factory=list)
    generated_at: datetime

    @model_validator(mode="after")
    def _guard(self) -> "PlanResult":
        if self.status == "ok" and any(
            v in ("not_configured", "missing_data", "error") for v in self.domains.values()
        ):
            raise ValueError("存在未配置/缺数据/出错的领域时，整体状态不得为 ok")
        if self.status == "blocked" and not self.blockers:
            raise ValueError("blocked 状态必须给出阻断原因")
        # 治理结论不允许在这个契约里被"顺带通过"
        for c in self.candidates:
            if c.quote_publishable and c.governance_status != "computed":
                raise ValueError("治理领域未计算时不得标记 quote_publishable")
        return self
