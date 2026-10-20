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
from llm_capacity_pricing.schemas.simulation import SimulationResult

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


class PlanSimulationInput(BaseModel):
    """可选：用 NVIDIA AISimulate 的**模拟**结果补充"该配什么、预测性能如何"。

    **默认关闭**：不配置这段时，``plan`` 的行为与输出逐字节不变，也不会调用任何外部工具。

    默认走**离线归档**路线（读仓库里已保存的输出），因此在没有 GPU、没有安装
    AISimulate 的机器上也能复现；只有显式 ``enable_external: true`` 才会去执行外部命令。
    """

    model_config = ConfigDict(extra="forbid")

    # ---- 离线归档（默认路线）----
    sweeper_csv: str | None = None
    native_prediction: str | None = None
    native_input: str | None = None
    exp_config: str | None = None
    per_ops_source: str | None = None
    #: 原生回放不回显精度；模型名无精度后缀时需在这里给出"我们要求模拟的精度"
    native_model_precision: str | None = None
    tool_version: str | None = None
    default_backend: str | None = None
    #: 扫描器候选的短名单上限（避免无界展示；原生候选不受限）
    top_n: int = 3
    #: 后端版本槽位别名 -> 实际版本（例如 ``{"current": "0.24.0"}``）。
    #: 槽位别名本身不是版本号：解析不出来就**不能**认为身份匹配（R1.1）。
    backend_version_aliases: dict[str, str] = Field(default_factory=dict)

    # ---- 外部执行（第二阶段，默认关闭）----
    enable_external: bool = False
    #: 自动模式：由本系统按需求生成搜索输入（不再要求用户手写第二份模拟 YAML）。
    #: 需要 ``enable_external: true``。
    auto_search: bool = False
    #: 自动搜索的工作目录（输入/输出/清单都写在这里）
    work_dir: str | None = None
    #: 多候选都满足条件时的排序目标。不提供"全局最优"的含糊说法。
    ranking: Literal["throughput", "cost"] = "throughput"
    #: 每个候选做原生回放时的请求数（决定 P99 的样本窗口）
    predict_requests: int = 500
    external_python: str | None = None
    external_surface: Literal["sweeper", "native_replay"] = "sweeper"
    external_config: str | None = None
    external_out_dir: str | None = None
    external_timeout_s: int = 1800

    @model_validator(mode="after")
    def _guard(self) -> "PlanSimulationInput":
        if self.auto_search and not self.enable_external:
            raise ValueError("auto_search 需要同时设置 enable_external: true")
        if self.auto_search and not self.work_dir:
            raise ValueError("auto_search 需要给出 work_dir（存放自动生成的输入与输出）")
        if self.enable_external and not self.auto_search:
            if not self.external_config:
                raise ValueError(
                    "enable_external 需要同时给出 external_config（要运行的输入 YAML）"
                )
            if not self.external_out_dir:
                raise ValueError(
                    "enable_external 需要同时给出 external_out_dir（外部输出写到哪）"
                )
        if (
            not self.enable_external
            and not (self.sweeper_csv or self.native_prediction)
        ):
            raise ValueError(
                "simulation 至少要给出 sweeper_csv 或 native_prediction（离线归档）；"
                "如果要现场调用外部工具，请显式设置 enable_external: true"
            )
        if self.top_n < 1:
            raise ValueError("top_n 至少为 1")
        return self


class PlanRequest(BaseModel):
    """统一评估输入。"""

    model_config = ConfigDict(extra="forbid")

    request_id: str = "plan"
    model_id: str
    #: 候选硬件（可多个，逐一出候选方案）
    hardware_ids: list[str]
    #: 用户实际可用的 GPU 总数上限（可选）。**未给出时不假设等于单机卡数** ——
    #: 拿不到上限就不做"卡数是否超限"的判断，也不据此推荐。
    max_total_gpus: int | None = None
    server_id: str | None = None
    workload: PlanWorkload
    resources: PlanResourceAssumptions = Field(default_factory=PlanResourceAssumptions)

    # ---- 按场景按需填写（全部可选）----
    sla: SlaRequirement | None = None
    cost: PlanCostInput | None = None
    demand: PlanDemandInput | None = None
    budget: PlanBudgetInput | None = None
    governance: PlanGovernanceInput | None = None
    #: 可选：模拟预测（默认关闭；不影响其它四个领域）
    simulation: PlanSimulationInput | None = None

    advanced: PlanAdvanced = Field(default_factory=PlanAdvanced)
    #: 结果中是否包含未验证/未配置领域的说明（默认包含，如实输出）
    include_gaps: bool = True

    @field_validator("max_total_gpus")
    @classmethod
    def _max_gpus_positive(cls, v: int | None) -> int | None:
        if v is not None and v < 1:
            raise ValueError("max_total_gpus 至少为 1")
        return v

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
    #: 成本计算周期（年）：跨周期不可直接比较全周期总额
    horizon_years: int | None = None
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


class PlanExternalReference(BaseModel):
    """**外部公开实测参考**（Demo：公开数据驱动）。

    这类数据来自官方/第三方公开发布，可以用来展示"业界在这类配置上大概什么水平"，
    但**不是**本需求的实测依据。它与候选性能结论**分开列示**，并写明为什么不能用：

    * 身份不匹配（不同模型 / 不同硬件 / 不同并行配置）；
    * 记录侧身份不完整（缺模型版本、精度、并发等）→ 只能作参考；
    * 这些数值**不参与** SLA 判定，也不进入报价门禁。
    """

    model_config = ConfigDict(extra="forbid")

    run_id: str
    publisher: str | None = None
    source_url: str | None = None
    source_file: str | None = None
    source_sha256: str | None = None
    data_trust: str
    #: 部署身份
    model_name: str | None = None
    model_revision: str | None = None
    weight_precision: str | None = None
    hardware_model: str | None = None
    gpu_count: int | None = None
    tp_size: int | None = None
    dp_size: int | None = None
    framework: str | None = None
    framework_version: str | None = None
    #: 负载条件
    dataset_name: str | None = None
    input_len_target: int | None = None
    output_len_target: int | None = None
    configured_concurrency: int | None = None
    #: 公开指标（缺失保持 None，不补造）
    ttft_p99_ms: Decimal | None = None
    tpot_p99_ms: Decimal | None = None
    output_throughput_tok_s: Decimal | None = None
    #: 匹配结论与本需求的口径（均由 Benchmark Matcher 给出，Demo 不另立一套规则）
    match_level: str | None = None
    #: 与目标配置**相同**、可以直接对照的条件
    same_conditions: list[str] = Field(default_factory=list)
    #: **不能**直接比较的原因（身份差异 / 记录侧缺失 / 目标侧缺失）
    not_usable_reasons: list[str] = Field(default_factory=list)
    quality_flags: list[str] = Field(default_factory=list)


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
    #: 外部公开实测参考（**不参与**候选评价与 SLA 判定）
    external_references: list[PlanExternalReference] = Field(default_factory=list)
    #: 模拟预测结果（可选领域；状态在 SimulationResult 自己身上，
    #: **不参与** PlanResult.domains 与整体 status 汇总 ——
    #: 否则"没开模拟"会把每次评估都降级成 partial）
    simulation: SimulationResult | None = None

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
