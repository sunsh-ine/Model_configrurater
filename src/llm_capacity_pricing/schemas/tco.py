"""第四阶段（M26–M28）：成本目录、部署成本方案、完整 TCO 与现金流。

三条口径纪律（任务书 §三）：

1. **不得用 GPU 成本代替 TCO**。第一阶段只算 ``gpu_cost_only``；这里建立真正的
   成本分解 ``TCO = 硬件 + 电力 + 空间 + 网络 + 软件 + 运维 + …``。
2. **买断与租赁互斥**：``cost_basis`` 决定哪些成本项参与核算，不参与的不是"零成本"，
   而是显式的 ``excluded_by_cost_basis``。
3. **未知不等于零**：任何缺价格/缺假设的成本项必须标成 ``unknown_missing_price``，
   并让整个 TCO 结果 ``incomplete=True``，绝不用 0 悄悄补齐。

币种规则沿用第一阶段：每一条价格自带 ``currency`` 与 ``currency_status``，
TCO 只有在包含的成本项币种一致**且**已确认时才允许用于正式口径。
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# ---------------------------------------------------------------------------
# 枚举
# ---------------------------------------------------------------------------

#: 成本类别（TCO 分解的最小可审计粒度）
CostCategory = Literal[
    "gpu_hardware",
    "server_chassis",
    "network",
    "storage",
    "power_energy",
    "facility_space",
    "cooling",
    "software_license",
    "operations",
    "maintenance",
    "other",
]

#: 成本归属方式：买断 / 租赁（互斥）
CostBasis = Literal["owned", "leased"]

#: 计价口径 —— 决定"一次性"还是"按年"，以及数量怎么取
PriceBasis = Literal[
    "one_time_per_gpu",      # 一次性，按卡
    "one_time_per_server",   # 一次性，按服务器
    "per_gpu_year",          # 按卡按年
    "per_server_year",       # 按服务器按年
    "per_kwh",               # 按电量（需功率与 PUE 假设）
    "per_rack_year",         # 按机柜按年（需显式数量）
    "per_kw_year",           # 按电力容量按年（需显式数量）
]

#: 价格确认状态。**synthetic_example 永远不能用于正式报价**。
CostStatus = Literal["confirmed", "pending_business_confirmation", "synthetic_example"]

#: 币种确认状态
CurrencyStatus = Literal["confirmed", "unconfirmed", "assumed"]

#: 成本项的核算结论
CostDetermination = Literal[
    "computed",                 # 已算出
    "unknown_missing_price",    # 缺单价 -> 不得当 0
    "unknown_missing_basis",    # 缺计算所需假设（如功率/PUE）
    "excluded_by_cost_basis",   # 与买断/租赁口径不符，按规则排除
]

#: 单价的来源可信度（沿用第三阶段的 measurement_origin 思路）
CostOrigin = Literal[
    "vendor_published",
    "distributor_quote",
    "internal_actual",
    "public_list_price",
    "assumed",
    "unknown",
]


class FinancingAssumptions(BaseModel):
    """TCO 计算所需的全部假设。缺哪个就写空，不要用行业惯例补齐。"""

    model_config = ConfigDict(extra="forbid")

    horizon_years: int = 3
    #: 买断口径：折旧年限（会计口径，非现金）
    depreciation_years: int | None = None
    #: 残值比例（0..1）
    residual_value_fraction: Decimal | None = None
    #: 年折现率（用于 PV，0..1；None = 不做折现）
    discount_rate_per_year: Decimal | None = None
    #: 年成本上浮率（电费/运维涨价；None = 不上浮）
    escalation_rate_per_year: Decimal | None = None
    #: 机房 PUE
    pue: Decimal | None = None
    pue_basis: CostOrigin = "unknown"
    #: 单卡推理功耗（W）。**不得**从 "Up to 700W (configurable)" 这类上限描述直接取值。
    gpu_power_watts: Decimal | None = None
    gpu_power_basis: CostOrigin = "unknown"
    #: 服务器除 GPU 外的附加功耗（W）
    server_overhead_watts: Decimal | None = None
    #: 平均 IT 负载率（0..1）
    it_power_utilization: Decimal | None = None
    hours_per_year: Decimal = Decimal("8760")

    @field_validator("horizon_years")
    @classmethod
    def _horizon(cls, v: int) -> int:
        if v < 1:
            raise ValueError("horizon_years 至少为 1")
        return v

    @field_validator("residual_value_fraction", "discount_rate_per_year", "it_power_utilization")
    @classmethod
    def _fraction(cls, v: Decimal | None) -> Decimal | None:
        if v is not None and not (Decimal("0") <= v <= Decimal("1")):
            raise ValueError("比例字段必须落在 0..1")
        return v

    @field_validator("pue")
    @classmethod
    def _pue(cls, v: Decimal | None) -> Decimal | None:
        if v is not None and v < Decimal("1"):
            raise ValueError("PUE 不得小于 1")
        return v

    @field_validator("escalation_rate_per_year")
    @classmethod
    def _escalation(cls, v: Decimal | None) -> Decimal | None:
        if v is not None and v <= Decimal("-1"):
            raise ValueError("上浮率不得 <= -100%")
        return v

    @model_validator(mode="after")
    def _guard(self) -> "FinancingAssumptions":
        if self.depreciation_years is not None and self.depreciation_years < 1:
            raise ValueError("depreciation_years 至少为 1")
        return self


class CostCatalogItem(BaseModel):
    """一条成本目录项（M27）：某项成本在某口径下的单价与来源。"""

    model_config = ConfigDict(extra="forbid")

    item_id: str
    category: CostCategory
    name: str
    #: 该成本项适用的归属方式；``None`` = 买断与租赁都适用
    basis: CostBasis | None = None
    price_basis: PriceBasis
    unit_price: Decimal | None = None
    unit: str
    currency: str | None = None
    currency_status: CurrencyStatus = "unconfirmed"
    status: CostStatus
    origin: CostOrigin = "unknown"
    #: 该成本项绑定的硬件 SKU（如某型号 GPU 的采购价）；None = 与型号无关
    applies_to_hardware_id: str | None = None
    region: str | None = None
    #: 显式数量（per_rack_year / per_kw_year 等无法从卡数推导的口径必填）
    default_quantity: Decimal | None = None
    valid_from: date | None = None
    valid_to: date | None = None
    evidence_id: str
    notes: str | None = None

    @field_validator("unit_price", "default_quantity")
    @classmethod
    def _non_negative(cls, v: Decimal | None) -> Decimal | None:
        if v is not None and v < 0:
            raise ValueError("单价/数量不允许为负值")
        return v

    @model_validator(mode="after")
    def _guard(self) -> "CostCatalogItem":
        # 无法从卡数/服务器数推导数量的口径，必须给出显式数量
        if self.price_basis in ("per_rack_year", "per_kw_year") and self.default_quantity is None:
            raise ValueError(
                f"{self.price_basis} 无法从卡数推导数量，必须在条目上给出 default_quantity"
            )
        # 有单价却没说币种，属于口径缺失
        if self.unit_price is not None and not self.currency:
            raise ValueError("给出 unit_price 时必须同时声明 currency")
        # 已确认状态必须真的确认过币种
        if self.status == "confirmed" and self.currency_status != "confirmed":
            raise ValueError("status=confirmed 的成本项必须同时 currency_status=confirmed")
        return self


class CostLineSelection(BaseModel):
    """在某个部署方案中选用某个成本项，并可覆盖数量。"""

    model_config = ConfigDict(extra="forbid")

    item_id: str
    quantity: Decimal | None = None
    note: str | None = None

    @field_validator("quantity")
    @classmethod
    def _non_negative(cls, v: Decimal | None) -> Decimal | None:
        if v is not None and v < 0:
            raise ValueError("quantity 不允许为负值")
        return v


class DeploymentCostPlan(BaseModel):
    """一个部署候选的成本方案（M27/M28 的输入）。"""

    model_config = ConfigDict(extra="forbid")

    plan_id: str
    candidate_id: str
    hardware_id: str
    #: 与第二阶段候选一致：卡数 / 服务器数 / TP / DP
    gpu_count: int
    server_count: int
    tp_size: int | None = None
    dp_size: int | None = None
    #: **买断与租赁互斥**
    cost_basis: CostBasis
    assumptions: FinancingAssumptions = Field(default_factory=FinancingAssumptions)
    selections: list[CostLineSelection] = Field(default_factory=list)
    #: 业务口径说明（区域、楼宇、合同编号等）
    scope_notes: list[str] = Field(default_factory=list)

    @field_validator("gpu_count", "server_count")
    @classmethod
    def _positive(cls, v: int) -> int:
        if v < 1:
            raise ValueError("gpu_count / server_count 必须 >= 1")
        return v

    @model_validator(mode="after")
    def _guard(self) -> "DeploymentCostPlan":
        seen = [s.item_id for s in self.selections]
        if len(seen) != len(set(seen)):
            raise ValueError("同一成本项不得在 selections 中重复出现")
        return self


class CostComponentResult(BaseModel):
    """单个成本项在某个方案下的核算结果。"""

    model_config = ConfigDict(extra="forbid")

    item_id: str
    category: CostCategory
    basis: CostBasis | None
    price_basis: PriceBasis
    unit_price: Decimal | None
    unit: str
    currency: str | None
    currency_status: CurrencyStatus
    status: CostStatus
    origin: CostOrigin
    #: 实际参与计算的数量（按卡/按服务器/按 kWh/显式数量）
    quantity: Decimal | None = None
    annual_cost: Decimal | None = None
    one_time_cost: Decimal | None = None
    determination: CostDetermination
    reason: str | None = None
    evidence_id: str

    @model_validator(mode="after")
    def _guard(self) -> "CostComponentResult":
        if self.determination == "excluded_by_cost_basis":
            if self.annual_cost is not None or self.one_time_cost is not None:
                raise ValueError("被口径排除的成本项不得带金额（排除 ≠ 计 0）")
        if self.determination == "computed" and self.annual_cost is None and self.one_time_cost is None:
            raise ValueError("computed 的成本项必须给出金额")
        if self.determination.startswith("unknown_missing") and (
            self.annual_cost is not None or self.one_time_cost is not None
        ):
            raise ValueError("缺数据的分项不得带金额（未知 ≠ 零）")
        return self

    @property
    def is_unknown(self) -> bool:
        return self.determination in ("unknown_missing_price", "unknown_missing_basis")


class TcoResult(BaseModel):
    """完整 TCO 结果（M28）。金额单位为方案币种；缺数据时保持 ``None`` 并置 ``incomplete``。"""

    model_config = ConfigDict(extra="forbid")

    plan_id: str
    candidate_id: str
    hardware_id: str
    cost_basis: CostBasis
    gpu_count: int
    server_count: int
    horizon_years: int
    currency: str | None
    #: 参与核算的成本项的币种集合（> 1 说明口径不一致，不得用于正式报价）
    currencies_seen: list[str] = Field(default_factory=list)
    #: 折现率（用于现金流折现；None = 不做折现）
    discount_rate_per_year: Decimal | None = None

    components: list[CostComponentResult] = Field(default_factory=list)

    #: 一次性投入（买断口径下的采购；租赁口径通常为 None 或押金）
    capex_one_time: Decimal | None = None
    #: 年现金支出（电力/空间/网络/软件/运维/租赁费）
    annual_cash_opex: Decimal | None = None
    #: 年折旧（**会计口径，非现金**，仅买断有意义）
    annual_depreciation: Decimal | None = None
    #: 年总成本 = 现金支出 + 折旧
    annual_total_cost: Decimal | None = None
    #: 全周期现金总额 = 一次性 + 年现金支出 × 年限
    total_cash_over_horizon: Decimal | None = None
    #: 全周期折现总额（缺折现率时为 None）
    total_pv_over_horizon: Decimal | None = None

    #: 单位成本
    cost_per_gpu_year: Decimal | None = None
    cost_per_server_year: Decimal | None = None

    #: 完整性：任一参与项缺价格/缺假设，或币种不一致，即为 True
    incomplete: bool = False
    completeness_reasons: list[str] = Field(default_factory=list)
    #: 缺数据的成本项（category/item_id）
    unknown_items: list[str] = Field(default_factory=list)
    #: 按买断/租赁口径排除的成本项（**不是零成本**）
    excluded_items: list[str] = Field(default_factory=list)

    #: 币种是否已确认；未确认不得进入正式报价
    currency_confirmed: bool = False
    #: 是否存在非真实价格（合成示例）
    contains_synthetic_prices: bool = False

    notes: list[str] = Field(default_factory=list)


class CashFlowYear(BaseModel):
    """年度现金流一行。"""

    model_config = ConfigDict(extra="forbid")

    year_index: int
    capex: Decimal | None = None
    opex: Decimal | None = None
    revenue: Decimal | None = None
    net_cash: Decimal | None = None
    cumulative_net_cash: Decimal | None = None
    discounted_net_cash: Decimal | None = None
    cumulative_discounted: Decimal | None = None


class CashFlowResult(BaseModel):
    """现金流核算（M28）。缺任何一年的数据就不给 NPV / 回收期。"""

    model_config = ConfigDict(extra="forbid")

    plan_id: str
    horizon_years: int
    currency: str | None
    currency_confirmed: bool = False
    discount_rate_per_year: Decimal | None = None
    years: list[CashFlowYear] = Field(default_factory=list)
    npv: Decimal | None = None
    payback_year: int | None = None
    #: 现金流涉及的收入是否为已验证实测口径
    revenue_basis: Literal["none", "scenario_only", "measured_capacity_verified"] = "none"
    incomplete: bool = False
    completeness_reasons: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
