"""M28：完整 TCO、成本分解与现金流核算（纯计算，不落库）。

口径（任务书 §三.1）：

* ``TCO = 硬件 + 服务器 + 网络 + 存储 + 电力 + 空间 + 制冷 + 软件 + 运维 + 维保``；
* **买断与租赁互斥**：不属于当前 ``cost_basis`` 的成本项标为
  ``excluded_by_cost_basis`` —— 排除**不是**零成本；
* **未知不等于零**：缺单价或缺计算假设的成本项一律不给金额；
* **年度费用与一次性采购费用分开**：``annual_cash_opex`` / ``capex_one_time``；
* 折旧是**会计口径、非现金**，单列 ``annual_depreciation``，不混进现金支出。

全部金额用 ``Decimal``；缺任一参与项即 ``incomplete=True``。
"""

from __future__ import annotations

from decimal import Decimal
from typing import Sequence

from llm_capacity_pricing.schemas.tco import (
    CashFlowResult,
    CashFlowYear,
    CostCatalogItem,
    CostComponentResult,
    DeploymentCostPlan,
    TcoResult,
)

#: 一次性口径
_ONE_TIME = ("one_time_per_gpu", "one_time_per_server")
#: 按卡/按服务器数量推导的年度口径
_PER_UNIT_YEAR = ("per_gpu_year", "per_server_year")
#: 需要显式数量的年度口径
_EXPLICIT_QUANTITY = ("per_rack_year", "per_kw_year")


def _is_synthetic(item: CostCatalogItem) -> bool:
    return item.status == "synthetic_example"


def _base_quantity(
    item: CostCatalogItem, plan: DeploymentCostPlan
) -> tuple[Decimal | None, str | None]:
    """按 price_basis 推导数量。返回 ``(数量, 缺失原因)``。"""
    if item.price_basis in ("one_time_per_gpu", "per_gpu_year"):
        return Decimal(plan.gpu_count), None
    if item.price_basis in ("one_time_per_server", "per_server_year"):
        return Decimal(plan.server_count), None
    if item.price_basis in _EXPLICIT_QUANTITY:
        if item.default_quantity is None:
            return None, f"{item.price_basis} 缺少条目级 default_quantity"
        return item.default_quantity, None
    if item.price_basis == "per_kwh":
        return None, "per_kwh 需要功率与 PUE 假设，见 _energy_kwh"
    raise ValueError(f"未处理的 price_basis: {item.price_basis}")


def _energy_kwh(
    plan: DeploymentCostPlan,
) -> tuple[Decimal | None, str | None]:
    """年耗电量（kWh）。缺任一假设即不可算 —— 不用 1.0/0 之类的默认值顶替。"""
    a = plan.assumptions
    missing: list[str] = []
    if a.gpu_power_watts is None:
        missing.append("gpu_power_watts")
    if a.server_overhead_watts is None:
        missing.append("server_overhead_watts")
    if a.pue is None:
        missing.append("pue")
    if a.it_power_utilization is None:
        missing.append("it_power_utilization")
    if missing:
        return None, "缺少电力假设: " + ",".join(missing)

    it_watts = (
        a.gpu_power_watts * Decimal(plan.gpu_count)
        + a.server_overhead_watts * Decimal(plan.server_count)
    )
    facility_watts = it_watts * a.pue
    kwh = (
        facility_watts / Decimal(1000) * a.hours_per_year * a.it_power_utilization
    )
    return kwh, None


def compute_tco(
    plan: DeploymentCostPlan,
    items: Sequence[CostCatalogItem],
) -> TcoResult:
    """把一个部署成本方案与成本目录合成完整 TCO。"""
    by_id = {item.item_id: item for item in items}
    components: list[CostComponentResult] = []
    completeness: list[str] = []
    unknown_items: list[str] = []
    excluded_items: list[str] = []
    currencies: set[str] = set()
    statuses: set[str] = set()

    capex = Decimal(0)
    opex = Decimal(0)
    have_capex = False
    have_opex = False

    for selection in plan.selections:
        item = by_id.get(selection.item_id)
        if item is None:
            completeness.append(f"cost_item_not_found:{selection.item_id}")
            unknown_items.append(selection.item_id)
            continue

        # ---- 口径互斥：买断 / 租赁 ----
        if item.basis is not None and item.basis != plan.cost_basis:
            excluded_items.append(f"{item.category}/{item.item_id}")
            components.append(
                CostComponentResult(
                    item_id=item.item_id,
                    category=item.category,
                    basis=item.basis,
                    price_basis=item.price_basis,
                    unit_price=item.unit_price,
                    unit=item.unit,
                    currency=item.currency,
                    currency_status=item.currency_status,
                    status=item.status,
                    origin=item.origin,
                    quantity=None,
                    annual_cost=None,
                    one_time_cost=None,
                    determination="excluded_by_cost_basis",
                    reason=(
                        f"条目口径为 {item.basis}，本方案为 {plan.cost_basis}；"
                        "按规则排除（排除不等于零成本）"
                    ),
                    evidence_id=item.evidence_id,
                )
            )
            continue

        # ---- 缺单价：未知，不得当 0 ----
        if item.unit_price is None:
            unknown_items.append(f"{item.category}/{item.item_id}")
            components.append(
                CostComponentResult(
                    item_id=item.item_id,
                    category=item.category,
                    basis=item.basis,
                    price_basis=item.price_basis,
                    unit_price=None,
                    unit=item.unit,
                    currency=item.currency,
                    currency_status=item.currency_status,
                    status=item.status,
                    origin=item.origin,
                    quantity=None,
                    determination="unknown_missing_price",
                    reason="成本目录项缺少 unit_price（未知不等于零）",
                    evidence_id=item.evidence_id,
                )
            )
            continue

        # ---- 数量 ----
        quantity = selection.quantity
        if item.price_basis == "per_kwh":
            if quantity is None:
                quantity, reason = _energy_kwh(plan)
                if quantity is None:
                    unknown_items.append(f"{item.category}/{item.item_id}")
                    components.append(
                        CostComponentResult(
                            item_id=item.item_id,
                            category=item.category,
                            basis=item.basis,
                            price_basis=item.price_basis,
                            unit_price=item.unit_price,
                            unit=item.unit,
                            currency=item.currency,
                            currency_status=item.currency_status,
                            status=item.status,
                            origin=item.origin,
                            quantity=None,
                            determination="unknown_missing_basis",
                            reason=reason,
                            evidence_id=item.evidence_id,
                        )
                    )
                    continue
        elif quantity is None:
            quantity, reason = _base_quantity(item, plan)
            if quantity is None:
                unknown_items.append(f"{item.category}/{item.item_id}")
                components.append(
                    CostComponentResult(
                        item_id=item.item_id,
                        category=item.category,
                        basis=item.basis,
                        price_basis=item.price_basis,
                        unit_price=item.unit_price,
                        unit=item.unit,
                        currency=item.currency,
                        currency_status=item.currency_status,
                        status=item.status,
                        origin=item.origin,
                        quantity=None,
                        determination="unknown_missing_basis",
                        reason=reason,
                        evidence_id=item.evidence_id,
                    )
                )
                continue

        amount = item.unit_price * quantity
        is_one_time = item.price_basis in _ONE_TIME
        annual = None if is_one_time else amount
        one_time = amount if is_one_time else None
        if is_one_time:
            capex += amount
            have_capex = True
        else:
            opex += amount
            have_opex = True
        if item.currency:
            currencies.add(item.currency)
        statuses.add(item.status)

        components.append(
            CostComponentResult(
                item_id=item.item_id,
                category=item.category,
                basis=item.basis,
                price_basis=item.price_basis,
                unit_price=item.unit_price,
                unit=item.unit,
                currency=item.currency,
                currency_status=item.currency_status,
                status=item.status,
                origin=item.origin,
                quantity=quantity,
                annual_cost=annual,
                one_time_cost=one_time,
                determination="computed",
                evidence_id=item.evidence_id,
            )
        )

    # ---- 折旧（会计口径，仅买断）----
    annual_depreciation: Decimal | None = None
    if plan.cost_basis == "owned":
        a = plan.assumptions
        if not have_capex:
            completeness.append("missing_capex_for_owned_basis: 买断口径未提供任何一次性采购成本")
        elif a.depreciation_years is None or a.residual_value_fraction is None:
            completeness.append(
                "missing_depreciation_assumptions: 需要 depreciation_years 与 residual_value_fraction"
            )
        else:
            depreciable = capex * (Decimal(1) - a.residual_value_fraction)
            annual_depreciation = depreciable / Decimal(a.depreciation_years)

    # ---- 币种一致性 ----
    # 注意：先固定"见过的币种"再判定，避免 pop 破坏 currencies_seen
    currencies_seen = sorted(currencies)
    currency = currencies.pop() if len(currencies) == 1 else None
    if len(currencies) > 1:
        completeness.append("mixed_currencies: 参与核算的成本项币种不一致")
    # 缺数据的分项必须让整个 TCO 变成"不完整"——否则缺项会被当成 0 悄悄吃掉
    if unknown_items:
        completeness.append(
            "unknown_cost_items:" + ",".join(sorted(set(unknown_items)))
            + "（缺价格或缺假设：未知不等于零，总额不完整）"
        )
    currency_confirmed = bool(currency) and all(
        c.currency_status == "confirmed"
        for c in components
        if c.determination == "computed"
    )
    if currency and not currency_confirmed:
        completeness.append("currency_not_confirmed: 存在未确认币种的成本项")

    contains_synthetic = any(s == "synthetic_example" for s in statuses)
    if contains_synthetic:
        completeness.append("contains_synthetic_prices: 使用了合成示例价格，不得用于正式口径")

    incomplete = bool(completeness)

    horizon = plan.assumptions.horizon_years
    total_cash = None
    total_pv = None
    cost_per_gpu_year = None
    cost_per_server_year = None
    annual_total = None
    if have_capex or have_opex:
        total_cash = (capex if have_capex else Decimal(0)) + (
            opex * Decimal(horizon) if have_opex else Decimal(0)
        )
        gpu_years = Decimal(plan.gpu_count) * Decimal(horizon)
        if gpu_years:
            cost_per_gpu_year = total_cash / gpu_years
        server_years = Decimal(plan.server_count) * Decimal(horizon)
        if server_years:
            cost_per_server_year = total_cash / server_years
        if annual_depreciation is not None and have_opex:
            annual_total = opex + annual_depreciation

    rate = plan.assumptions.discount_rate_per_year
    if total_cash is not None and rate is not None:
        pv = capex if have_capex else Decimal(0)
        for year in range(1, horizon + 1):
            pv += (opex if have_opex else Decimal(0)) / ((Decimal(1) + rate) ** year)
        total_pv = pv

    notes: list[str] = []
    if excluded_items:
        notes.append(
            f"有 {len(excluded_items)} 项因买断/租赁口径被排除：{excluded_items}；"
            "排除项不代表零成本"
        )
    if unknown_items:
        notes.append(
            f"有 {len(unknown_items)} 项缺价格或缺少假设，成本总额因此**不完整**：{unknown_items}"
        )
    notes.append(
        "折旧为会计口径、非现金支出；现金流核算请使用 capex_one_time 与 annual_cash_opex"
    )

    return TcoResult(
        plan_id=plan.plan_id,
        candidate_id=plan.candidate_id,
        hardware_id=plan.hardware_id,
        cost_basis=plan.cost_basis,
        gpu_count=plan.gpu_count,
        server_count=plan.server_count,
        horizon_years=horizon,
        currency=currency,
        currencies_seen=currencies_seen,
        discount_rate_per_year=plan.assumptions.discount_rate_per_year,
        components=components,
        capex_one_time=capex if have_capex else None,
        annual_cash_opex=opex if have_opex else None,
        annual_depreciation=annual_depreciation,
        annual_total_cost=annual_total,
        total_cash_over_horizon=total_cash,
        total_pv_over_horizon=total_pv,
        cost_per_gpu_year=cost_per_gpu_year,
        cost_per_server_year=cost_per_server_year,
        incomplete=incomplete,
        completeness_reasons=sorted(set(completeness)),
        unknown_items=unknown_items,
        excluded_items=excluded_items,
        currency_confirmed=currency_confirmed,
        contains_synthetic_prices=contains_synthetic,
        notes=notes,
    )


def build_cash_flow(
    tco: TcoResult,
    *,
    annual_revenue: Decimal | None = None,
    revenue_basis: str = "none",
) -> CashFlowResult:
    """按 TCO 生成现金流；``annual_revenue`` 为 None 时只算成本侧。

    第 0 年 = 一次性投入（买断采购）；第 1..N 年 = 年现金支出（租赁/电力/运维…）。
    折旧**不进入**现金流，只影响会计利润，因此不出现在本函数里。
    """
    horizon = tco.horizon_years
    rate = tco.discount_rate_per_year
    notes: list[str] = [
        "一次性投入计入第 0 年（现金流口径）；折旧为会计口径，不进入现金流"
    ]
    completeness: list[str] = []

    opex = tco.annual_cash_opex
    capex = tco.capex_one_time
    if tco.incomplete:
        completeness.extend(tco.completeness_reasons)
    if opex is None and capex is None:
        completeness.append("no_cost_component: 没有任何可核算的成本项")
    if annual_revenue is not None and revenue_basis == "none":
        completeness.append(
            "revenue_basis_missing: 给出了收入却未声明口径（不得默认当成已验证收入）"
        )
    if annual_revenue is not None and revenue_basis != "measured_capacity_verified":
        notes.append(
            "收入口径为情景值（未与可信容量对齐）：现金流与回收期**仅供情景分析**，"
            "不构成商业承诺"
        )

    years: list[CashFlowYear] = []
    cumulative = Decimal(0)
    payback: int | None = None
    npv: Decimal | None = None

    for year in range(0, horizon + 1):
        if year == 0:
            c: Decimal | None = capex
            o: Decimal | None = None
        else:
            c = Decimal(0) if capex is not None else None
            o = opex
        r: Decimal | None = None
        if annual_revenue is not None:
            r = annual_revenue if year > 0 else Decimal(0)

        parts = [v for v in (c, o) if v is not None]
        net = None
        if parts or r is not None:
            net = -sum(parts, Decimal(0))
            if r is not None:
                net = net + r
        row = CashFlowYear(year_index=year, capex=c, opex=o, revenue=r, net_cash=net)
        if net is not None:
            cumulative += net
            row.cumulative_net_cash = cumulative
            if payback is None and cumulative >= 0 and year > 0:
                payback = year
        years.append(row)

    if rate is None:
        notes.append("未提供折现率：不给出 NPV 与折现累计")
    else:
        cumulative_disc = Decimal(0)
        for row in years:
            if row.net_cash is None:
                continue
            disc = row.net_cash / ((Decimal(1) + rate) ** row.year_index)
            row.discounted_net_cash = disc
            cumulative_disc += disc
            row.cumulative_discounted = cumulative_disc
        npv = cumulative_disc

    return CashFlowResult(
        plan_id=tco.plan_id,
        horizon_years=horizon,
        currency=tco.currency,
        currency_confirmed=tco.currency_confirmed,
        discount_rate_per_year=rate,
        years=years,
        npv=npv,
        payback_year=payback,
        revenue_basis=revenue_basis,  # type: ignore[arg-type]
        incomplete=bool(completeness),
        completeness_reasons=sorted(set(completeness)),
        notes=notes,
    )
