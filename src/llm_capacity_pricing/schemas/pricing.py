"""定价情景与计算结果模型。

单位约定：所有 ``*_10k_*`` 字段单位为**万元**，年份口径为
``calculation_year_seconds``（默认 365 天）。
"""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

PricingMode = Literal["legacy_full_load", "scenario"]
ResultStatus = Literal["ok", "not_evaluable", "partial"]
#: 结果口径：business = 业务数据；test = 人工构造的测试数据（默认不参与业务统计）
ResultScope = Literal["business", "test"]
#: 结果类型：明确这不是"实际营收/客户报价"
ResultType = Literal[
    "theoretical_full_load_potential",
    "scenario_sensitivity_estimate",
]
SlaStatus = Literal["not_evaluable", "evaluable"]

PRICING_ENGINE_VERSION = "0.1.0"

#: 成本边界。本系统只覆盖 GPU 成本，不是完整 TCO（任务书 §4）。
COST_SCOPE = "gpu_cost_only"

#: 币种假设：Excel 未写明币种，必须显式记录悬而未决状态。
CURRENCY_ASSUMPTION = "CNY; pending business confirmation"
#: 币种未确认时的占位符（金额字段的币种一律写这个，不得默认成已确认的人民币）
CURRENCY_UNCONFIRMED = "UNCONFIRMED"
PRICE_UNIT = "currency_unit_per_1M_tokens"
BILLING_PERIOD = "per_gpu_year"


class PricingScenario(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenario_id: str
    benchmark_record_id: str
    token_price_id: str | None = None
    hardware_price_id: str | None = None

    calculation_year_seconds: int = 31_536_000
    mode: PricingMode = "legacy_full_load"

    #: 有效产能变现比例 U ∈ [0, 1]。初版只开放 U。
    effective_monetization_ratio: Decimal | None = None
    #: 可用率 A / 可计费比例 S：初版留空。若启用，必须显式作用于对应的
    #: 有效时段与 Token，**不得与已包含它们的 U 重复相乘**。
    availability_ratio: Decimal | None = None
    billable_ratio: Decimal | None = None

    #: 旧表复算时，H 列命中率被当作**定价代理假设**使用。
    legacy_hit_rate_as_billing_proxy: bool = True

    @property
    def is_scenario(self) -> bool:
        return self.mode == "scenario"


class PricingResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenario_id: str
    benchmark_record_id: str
    mode: PricingMode
    status: ResultStatus = "ok"
    not_evaluable_reason: str | None = None

    # ---- 口径与追溯（任务 1 / 任务 3）------------------------------------
    #: business = 业务数据；test = synthetic_fixture（默认不参与业务统计）
    scope: ResultScope = "business"
    #: 结果类型：理论满载潜力 / 情景敏感性估算。二者都不是实际营收或客户报价。
    result_type: ResultType = "theoretical_full_load_potential"
    #: 本次计算实际引用的输入主键，保证结果可回溯到性能记录 / 硬件成本 / Token 单价
    performance_record_id: str | None = None
    token_price_id: str | None = None
    hardware_price_id: str | None = None
    #: 引用的证据链：性能来源 + 硬件价格来源 + Token 价格来源
    evidence_ids: list[str] = Field(default_factory=list)
    #: 未能取得关联输入时记录原因（不猜测补齐）
    linkage_warnings: list[str] = Field(default_factory=list)

    # ---- 每卡 / 年 ------------------------------------------------------
    input_revenue_10k_per_gpu_year: Decimal | None = None
    output_revenue_10k_per_gpu_year: Decimal | None = None
    total_revenue_10k_per_gpu_year: Decimal | None = None
    gpu_annual_cost_10k: Decimal | None = None
    revenue_cost_ratio: Decimal | None = None
    breakeven_monetization_ratio: Decimal | None = None

    # ---- 集群 -----------------------------------------------------------
    deployment_gpu_count: int | None = None
    total_cluster_cost_10k_year: Decimal | None = None
    total_cluster_revenue_10k_year: Decimal | None = None

    # ---- 情景附加 -------------------------------------------------------
    effective_monetization_ratio: Decimal | None = None
    scenario_revenue_10k_per_gpu_year: Decimal | None = None
    scenario_surplus_before_other_costs_10k_per_gpu_year: Decimal | None = None
    scenario_cluster_revenue_10k_year: Decimal | None = None

    # ---- 溯源 / 边界声明 ------------------------------------------------
    cost_scope: str = COST_SCOPE
    #: 币种。Excel 未写明时为 CURRENCY_UNCONFIRMED，不默认人民币。
    currency: str = CURRENCY_UNCONFIRMED
    currency_assumption: str | None = None
    price_unit: str = PRICE_UNIT
    billing_period: str = BILLING_PERIOD
    calculation_year_seconds: int = 31_536_000
    cache_hit_rate_used: Decimal | None = None
    cache_hit_rate_is_legacy_proxy: bool = False

    # ---- SLA 可评估性（任务 3）------------------------------------------
    #: 缺少实测 P99 TTFT/TPOT 时必须为 not_evaluable，不得由其它指标反推
    sla_status: SlaStatus = "not_evaluable"
    sla_not_evaluable_reasons: list[str] = Field(default_factory=list)

    warnings: list[str] = Field(default_factory=list)
    input_snapshot_hash: str | None = None
    pricing_engine_version: str = PRICING_ENGINE_VERSION
    steps: list[str] = Field(default_factory=list)
