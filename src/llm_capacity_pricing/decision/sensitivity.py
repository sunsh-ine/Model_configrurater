"""M32：预算检查、成本敏感性与盈亏平衡（纯计算）。

三条纪律：

* 预算结论在**币种未确认**时一律为 ``unknown``，不给"在预算内"；
* 敏感性只做**单因子扰动**并如实报告可算/不可算，不给"最优推荐"；
* 盈亏平衡必须说明它用的是**现金成本**还是**含折旧的全成本**，
  并且在缺少可信容量时不得声称"该盈亏平衡点可达"。
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Sequence

from llm_capacity_pricing.engines.tco import compute_tco
from llm_capacity_pricing.schemas.commercial import BillingRule, RevenueResult
from llm_capacity_pricing.schemas.decision import (
    BreakevenResult,
    BudgetCheck,
    DriverSensitivity,
    SensitivityAnalysis,
)
from llm_capacity_pricing.schemas.tco import (
    CostCatalogItem,
    DeploymentCostPlan,
    TcoResult,
)

#: 默认扰动的成本驱动（假设类）
DEFAULT_COST_DRIVERS: tuple[tuple[str, str], ...] = (
    ("assumption:gpu_power_watts", "cost"),
    ("assumption:pue", "cost"),
    ("assumption:it_power_utilization", "cost"),
)
#: 默认相对扰动量
DEFAULT_DELTAS: tuple[Decimal, ...] = (Decimal("-0.2"), Decimal("0.2"))

PER_MILLION = Decimal("1000000")


def check_budget(
    *,
    budget_id: str,
    budget_amount: Decimal | None,
    budget_kind: str,
    compared_value: Decimal | None,
    currency: str | None,
    currency_confirmed: bool,
    compared_currency: str | None = None,
    reasons_extra: Sequence[str] = (),
) -> BudgetCheck:
    """预算检查。缺金额 / 缺对比值 / 币种未确认或不一致一律 ``unknown``。"""
    reasons: list[str] = list(reasons_extra)
    if budget_amount is None:
        reasons.append("budget_amount_missing: 未给出预算金额")
    if compared_value is None:
        reasons.append("compared_value_missing: 没有可对比的成本结果")
    if not currency_confirmed:
        reasons.append(
            "currency_not_confirmed: 币种未确认，不得给出预算结论"
        )
    if compared_currency is not None and currency is not None and (
        compared_currency != currency
    ):
        reasons.append(
            f"currency_mismatch:{compared_currency}!= {currency}: "
            "预算与成本币种不一致，不得直接相减"
        )
    if (
        budget_amount is None
        or compared_value is None
        or not currency_confirmed
        or any(r.startswith("currency_mismatch") for r in reasons)
    ):
        return BudgetCheck(
            budget_id=budget_id,
            budget_amount=budget_amount,
            currency=currency,
            currency_confirmed=currency_confirmed,
            budget_kind=budget_kind,  # type: ignore[arg-type]
            compared_value=compared_value,
            status="unknown",
            reasons=reasons,
        )

    headroom = budget_amount - compared_value
    return BudgetCheck(
        budget_id=budget_id,
        budget_amount=budget_amount,
        currency=currency,
        currency_confirmed=currency_confirmed,
        budget_kind=budget_kind,  # type: ignore[arg-type]
        compared_value=compared_value,
        headroom=headroom,
        status="within_budget" if headroom >= 0 else "exceeds_budget",
        reasons=reasons,
    )


def _perturb(
    plan: DeploymentCostPlan,
    items: Sequence[CostCatalogItem],
    driver: str,
    delta: Decimal,
) -> tuple[TcoResult | None, str | None]:
    """对单个驱动量施加相对扰动后重算 TCO。返回 ``(结果, 不可算原因)``。"""
    if driver.startswith("assumption:"):
        field = driver.split(":", 1)[1]
        current = getattr(plan.assumptions, field, None)
        if current is None:
            return None, f"assumption_missing:{field}"
        updated = plan.assumptions.model_copy(
            update={field: current * (Decimal(1) + delta)}
        )
        return compute_tco(plan.model_copy(update={"assumptions": updated}), items), None

    if driver.startswith("price:"):
        item_id = driver.split(":", 1)[1]
        target = next((i for i in items if i.item_id == item_id), None)
        if target is None:
            return None, f"cost_item_not_found:{item_id}"
        if target.unit_price is None:
            return None, f"unit_price_missing:{item_id}"
        updated = target.model_copy(
            update={"unit_price": target.unit_price * (Decimal(1) + delta)}
        )
        new_items = [updated if i.item_id == item_id else i for i in items]
        return compute_tco(plan, new_items), None

    return None, f"unsupported_driver:{driver}"


def analyze_sensitivity(
    *,
    plan: DeploymentCostPlan,
    items: Sequence[CostCatalogItem],
    drivers: Sequence[tuple[str, str]] | None = None,
    deltas: Sequence[Decimal] = DEFAULT_DELTAS,
) -> SensitivityAnalysis:
    """成本侧单因子敏感性。缺数据的驱动量单独列出，不猜、不跳过。"""
    driver_specs: list[tuple[str, str]] = list(drivers or DEFAULT_COST_DRIVERS)
    # 把方案中实际选用的成本项价格也作为驱动（数量已由引擎按口径推导）
    for sel in plan.selections:
        driver_specs.append((f"price:{sel.item_id}", "cost"))

    base = compute_tco(plan, items)
    base_value = base.total_cash_over_horizon

    results: list[DriverSensitivity] = []
    uncomputable: list[str] = []
    for driver, affects in driver_specs:
        for delta in deltas:
            perturbed, reason = _perturb(plan, items, driver, delta)
            if perturbed is None:
                uncomputable.append(f"{driver}:{reason}")
                results.append(
                    DriverSensitivity(
                        driver=driver,
                        delta_fraction=delta,
                        base_value=base_value,
                        computable=False,
                        affects=affects,  # type: ignore[arg-type]
                        notes=[reason or "unknown"],
                    )
                )
                continue
            value = perturbed.total_cash_over_horizon
            absolute = None
            relative = None
            if base_value is not None and value is not None:
                absolute = value - base_value
                if base_value != 0:
                    relative = absolute / base_value
            results.append(
                DriverSensitivity(
                    driver=driver,
                    delta_fraction=delta,
                    base_value=base_value,
                    perturbed_value=value,
                    absolute_change=absolute,
                    relative_change=relative,
                    computable=value is not None,
                    affects=affects,  # type: ignore[arg-type]
                    notes=[] if value is not None else ["扰动后成本不可算（存在未知项）"],
                )
            )

    notes = [
        "敏感性为**单因子**扰动，不含交互项",
        "驱动量按相对比例变动，成本按同一 TCO 口径重算",
    ]
    if base.incomplete:
        notes.append("基准 TCO 本身不完整：敏感性结果同样不完整，仅供结构参考")

    return SensitivityAnalysis(
        plan_id=plan.plan_id,
        base_metric="total_cash_over_horizon",
        base_value=base_value,
        drivers=results,
        uncomputable_drivers=sorted(set(uncomputable)),
        incomplete=base.incomplete or bool(uncomputable),
        notes=notes,
    )


def analyze_breakeven(
    *,
    tco: TcoResult,
    revenue: RevenueResult | None,
    rule: BillingRule | None,
) -> BreakevenResult:
    """盈亏平衡：达到不亏所需的收入、输出计费量与最低输出单价。"""
    reasons: list[str] = []
    if tco.incomplete:
        reasons.append("cost_incomplete: 成本不完整，盈亏平衡点不可靠")
    if tco.currency_confirmed is False:
        reasons.append("currency_not_confirmed: 币种未确认")

    annual_cost = tco.annual_total_cost
    cost_scope = "annual_total_cost(含折旧)"
    if annual_cost is None:
        annual_cost = tco.annual_cash_opex
        cost_scope = "annual_cash_opex(仅现金)"
    if annual_cost is None:
        reasons.append("no_annual_cost: 没有年度成本，无法计算盈亏平衡")
        return BreakevenResult(
            plan_id=tco.plan_id,
            computable=False,
            reasons=reasons,
            capacity_check="unknown_no_measurement",
        )

    out_tokens = revenue.annual_billable_output_tokens if revenue else None
    breakeven_tokens = None
    breakeven_price = None
    if rule and rule.price_output_per_1m and rule.price_output_per_1m > 0:
        breakeven_tokens = annual_cost / rule.price_output_per_1m * PER_MILLION
    if out_tokens and out_tokens > 0:
        breakeven_price = annual_cost / out_tokens * PER_MILLION
    if breakeven_tokens is None:
        reasons.append("missing_output_price: 缺少输出单价，无法反推所需计费量")

    ratio = None
    if revenue is not None:
        if not tco.currency or not revenue.currency:
            reasons.append("currency_unknown: 成本或收入的币种未知，不得计算收入成本比")
        elif tco.currency != revenue.currency:
            reasons.append(
                f"currency_mismatch:{revenue.currency}!= {tco.currency}: "
                "跨币种不得计算收入成本比"
            )
        elif annual_cost:
            ratio = revenue.annual_revenue / annual_cost

    capacity_check = revenue.capacity_check if revenue else "unknown_no_measurement"
    notes = [
        f"年度成本口径：{cost_scope}",
        "盈亏平衡只说明'不亏'，不构成产能或 SLA 承诺",
    ]
    if capacity_check != "within_capacity":
        notes.append(
            "盈亏平衡点对应的产能**未经验证**：不得据此声称该收入水平可达"
        )

    return BreakevenResult(
        plan_id=tco.plan_id,
        annual_cost=annual_cost,
        required_annual_revenue=annual_cost,
        breakeven_billable_output_tokens=breakeven_tokens,
        breakeven_output_price_per_1m=breakeven_price,
        revenue_cost_ratio=ratio,
        capacity_check=capacity_check,  # type: ignore[arg-type]
        computable=not tco.incomplete and breakeven_tokens is not None,
        reasons=reasons,
        notes=notes,
    )


def summarize_plan_tco(plan: DeploymentCostPlan, items: Sequence[CostCatalogItem]) -> dict[str, Any]:
    """便于 CLI/报告复用的紧凑摘要。"""
    tco = compute_tco(plan, items)
    return {
        "plan_id": tco.plan_id,
        "cost_basis": tco.cost_basis,
        "capex_one_time": tco.capex_one_time,
        "annual_cash_opex": tco.annual_cash_opex,
        "annual_depreciation": tco.annual_depreciation,
        "annual_total_cost": tco.annual_total_cost,
        "total_cash_over_horizon": tco.total_cash_over_horizon,
        "incomplete": tco.incomplete,
    }
