"""M28：完整 TCO、成本分解与现金流核算（纯计算，不落库）。

口径（任务书 §三.1 与 V4.1 F4.2/F4.3）：

* ``TCO = 硬件 + 服务器 + 网络 + 存储 + 电力 + 空间 + 制冷 + 软件 + 运维 + 维保``；
* **买断与租赁互斥**：不属于当前 ``cost_basis`` 的成本项标为
  ``excluded_by_cost_basis`` —— 排除**不是**零成本；
* **未知不等于零**：缺单价或缺计算假设的成本项一律不给金额；
* **覆盖不能靠"不选"**：每个适用成本类别必须由覆盖策略（``CostCoveragePolicy``）
  显式处理 —— 未选入又未声明排除的类别记为**覆盖缺口**，使 TCO 不完整（F4.2）；
* **价格必须适用**：在算金额之前校验 GPU SKU、区域与有效期，
  不适用一律不给金额并记明原因（F4.3）；
* **年度费用与一次性采购费用分开**：``annual_cash_opex`` / ``capex_one_time``；
* 折旧是**会计口径、非现金**，单列 ``annual_depreciation``，不混进现金支出。

全部金额用 ``Decimal``；缺任一参与项即 ``incomplete=True``。
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Sequence

from llm_capacity_pricing.schemas.tco import (
    ApplicabilityIssue,
    CashFlowResult,
    CashFlowYear,
    CostCatalogItem,
    CostComponentResult,
    CostCoveragePolicy,
    DeploymentCostPlan,
    TcoResult,
)

#: 一次性口径
_ONE_TIME = ("one_time_per_gpu", "one_time_per_server")
#: 按卡/按服务器数量推导的年度口径
_PER_UNIT_YEAR = ("per_gpu_year", "per_server_year")
#: 需要显式数量的年度口径
_EXPLICIT_QUANTITY = ("per_rack_year", "per_kw_year")


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


def _energy_kwh(plan: DeploymentCostPlan) -> tuple[Decimal | None, str | None]:
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
    kwh = facility_watts / Decimal(1000) * a.hours_per_year * a.it_power_utilization
    return kwh, None


def _applicability_issue(
    item: CostCatalogItem, plan: DeploymentCostPlan, as_of: date
) -> ApplicabilityIssue | str | None:
    """价格适用范围校验（F4.3）。返回原因码，或 ``None`` 表示适用。"""
    if item.applies_to_hardware_id and item.applies_to_hardware_id != plan.hardware_id:
        return "hardware_sku_mismatch"
    if item.region:
        if plan.region is None:
            # 条目标了区域而方案没声明区域：无法断言"该区域价格适用于本方案"
            return "region_unspecified"
        if item.region != plan.region:
            return "region_mismatch"
    if item.valid_to is not None and item.valid_to < as_of:
        return "price_expired"
    if item.valid_from is not None and item.valid_from > as_of:
        return "price_not_yet_effective"
    return None


def _coverage(
    plan: DeploymentCostPlan,
    policy: CostCoveragePolicy | None,
    components: Sequence[CostComponentResult],
) -> tuple[dict[str, str], list[str], dict[str, str]]:
    """成本范围覆盖判定（F4.2）。

    返回 ``(decisions, gaps, exclusions)``。**没有策略就不算完整** ——
    否则"少选几个类别"就能得到看似完整的 TCO。
    """
    exclusions = {e.category: e.reason for e in plan.scope_exclusions}
    covered = {c.category for c in components if c.determination == "computed"}
    decisions: dict[str, str] = {}
    gaps: list[str] = []
    if policy is None:
        return decisions, gaps, exclusions

    for req in policy.requirements:
        if req.applies_to_basis is not None and req.applies_to_basis != plan.cost_basis:
            decisions[req.category] = "not_applicable"
            continue
        if req.category in covered:
            decisions[req.category] = "covered"
            continue
        if req.category in exclusions:
            if not req.allow_scope_exclusion:
                # 策略不允许排除该类别，却给了排除理由 -> 仍然算缺口
                decisions[req.category] = "missing"
                gaps.append(f"{req.category}:policy_forbids_scope_exclusion")
                continue
            decisions[req.category] = "excluded_by_scope"
            continue
        decisions[req.category] = "missing"
        gaps.append(req.category)
    return decisions, gaps, exclusions


def currencies_comparable(
    left: str | None, right: str | None
) -> tuple[bool, str | None]:
    """两个金额能否**直接相减/相除**（S3：唯一权威实现）。

    利润、利润率、盈亏平衡与预算比对都必须先过这一关：
    币种未知或不同时不得产出数字 —— USD 收入减 CNY 成本没有经济含义。

    返回 ``(可比, 不可比原因码)``；原因码可直接作为 reason/blocker 前缀使用。
    """
    if not left or not right:
        return False, "currency_unknown"
    if left != right:
        # 排序后拼接：与调用方传参顺序无关，同一对币种只产生同一个原因码
        a, b = sorted((left, right))
        return False, f"currency_mismatch:{a}!={b}"
    return True, None


def compute_tco(
    plan: DeploymentCostPlan,
    items: Sequence[CostCatalogItem],
    coverage_policy: CostCoveragePolicy | None = None,
) -> TcoResult:
    """把一个部署成本方案与成本目录合成完整 TCO。

    ``coverage_policy`` 为 None 时结果**一定**是不完整的：
    没有覆盖策略就无法证明"该算的成本都算了"。
    """
    by_id = {item.item_id: item for item in items}
    as_of = plan.as_of_date or date.today()
    components: list[CostComponentResult] = []
    completeness: list[str] = []
    unknown_items: list[str] = []
    excluded_items: list[str] = []
    applicability_errors: list[str] = []
    validity_unknown: list[str] = []
    unconfirmed_items: list[str] = []
    #: 参与核算的 (成本项, 结果) —— 用于覆盖/重复计费/币种判定
    participating: list[tuple[CostCatalogItem, CostComponentResult]] = []
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

        # ---- 1. 口径互斥：买断 / 租赁 ----
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

        # ---- 2. 价格适用范围（F4.3）：不适用就不得算金额 ----
        issue = _applicability_issue(item, plan, as_of)
        if issue is not None:
            label = f"{item.category}/{item.item_id}:{issue}"
            applicability_errors.append(label)
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
                    determination="not_applicable",
                    reason=f"价格适用范围不通过（{issue}，基准日 {as_of.isoformat()}）",
                    applicability_issue=issue,  # type: ignore[arg-type]
                    evidence_id=item.evidence_id,
                )
            )
            continue

        # ---- 3. 缺单价：未知，不得当 0 ----
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

        # ---- 4. 数量 ----
        quantity = selection.quantity
        if item.price_basis == "per_kwh" and quantity is None:
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

        # ---- 5. 计算 ----
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
        if item.valid_from is None and item.valid_to is None:
            validity_unknown.append(f"{item.category}/{item.item_id}")

        component = CostComponentResult(
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
        components.append(component)
        participating.append((item, component))
        if item.status != "confirmed":
            unconfirmed_items.append(f"{item.category}/{item.item_id}")

    # ---- 6. 折旧（会计口径，仅买断）----
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

    # ---- 7. 成本范围覆盖（F4.2）----
    coverage_policy_id = None
    coverage_policy_version = None
    coverage_policy_authority = None
    coverage_decisions: dict[str, str] = {}
    coverage_gaps: list[str] = []
    scope_exclusions: dict[str, str] = {}
    if coverage_policy is None:
        completeness.append(
            "coverage_policy_missing: 未提供成本覆盖策略，无法证明该算的成本都已覆盖"
        )
    else:
        coverage_policy_id = coverage_policy.policy_id
        coverage_policy_version = coverage_policy.version
        coverage_policy_authority = coverage_policy.authority
        if coverage_policy.authority != "confirmed":
            # 未授权的覆盖策略不能支撑生产级"完整 TCO"（V4.2 E6）
            completeness.append(
                f"coverage_policy_not_authorized:{coverage_policy.authority}"
                "（策略未经批准：可以计算，但不得声称完整口径）"
            )
        coverage_decisions, coverage_gaps, scope_exclusions = _coverage(
            plan, coverage_policy, components
        )
        if coverage_gaps:
            completeness.append(
                "coverage_gap:" + ",".join(sorted(coverage_gaps))
                + "（类别未选入且未显式排除：不选它不等于它不存在）"
            )

    # ---- 8. 疑似重复计费 ----
    groups: dict[tuple[str, str, str | None, str | None], list[str]] = {}
    for item, _component in participating:
        key = (item.category, item.price_basis, item.applies_to_hardware_id, item.region)
        groups.setdefault(key, []).append(item.item_id)
    duplicate_billing = sorted(
        f"{cat}/" + ",".join(sorted(ids))
        for (cat, _pb, _hw, _rg), ids in groups.items()
        if len(ids) > 1
    )
    if duplicate_billing:
        completeness.append(
            "duplicate_billing:" + ";".join(duplicate_billing)
            + "（同一类别、同一计价口径、同一适用范围出现多个成本项）"
        )

    # ---- 9. 币种与状态 ----
    currencies_seen = sorted(currencies)
    currency = currencies.pop() if len(currencies) == 1 else None
    if len(currencies) > 1:
        completeness.append("mixed_currencies: 参与核算的成本项币种不一致")
    if unknown_items:
        completeness.append(
            "unknown_cost_items:" + ",".join(sorted(set(unknown_items)))
            + "（缺价格或缺假设：未知不等于零，总额不完整）"
        )
    if applicability_errors:
        completeness.append(
            "price_not_applicable:" + ",".join(sorted(set(applicability_errors)))
            + "（价格适用范围不通过：不得跨 SKU/区域/失效期使用）"
        )
    currency_confirmed = bool(currency) and all(
        c.currency_status == "confirmed" for c in components if c.determination == "computed"
    )
    if currency and not currency_confirmed:
        completeness.append("currency_not_confirmed: 存在未确认币种的成本项")

    contains_synthetic = any(s == "synthetic_example" for s in statuses)
    if contains_synthetic:
        completeness.append("contains_synthetic_prices: 使用了合成示例价格，不得用于正式口径")

    incomplete = bool(completeness)

    # ---- 10. 汇总 ----
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
    if scope_exclusions:
        notes.append(
            "按范围显式排除的成本类别：" + "; ".join(
                f"{k}（{v}）" for k, v in sorted(scope_exclusions.items())
            )
        )
    if unknown_items:
        notes.append(
            f"有 {len(unknown_items)} 项缺价格或缺少假设，成本总额因此**不完整**：{unknown_items}"
        )
    if applicability_errors:
        notes.append(
            f"有 {len(applicability_errors)} 项价格适用范围不通过，已从总额中剔除："
            f"{applicability_errors}"
        )
    if validity_unknown:
        notes.append(
            f"有 {len(validity_unknown)} 项价格未声明有效期：计算可用，"
            f"但正式报价前需确认：{validity_unknown}"
        )
    if coverage_gaps:
        notes.append(
            f"成本范围覆盖缺口：{coverage_gaps} —— 这些类别既未选入也未显式排除，"
            "因此这份 TCO **不完整**"
        )
    if duplicate_billing:
        notes.append(f"疑似重复计费：{duplicate_billing}")
    if unconfirmed_items:
        notes.append(
            f"有 {len(unconfirmed_items)} 项价格状态未确认（非 confirmed）："
            f"{unconfirmed_items}；正式报价前必须确认"
        )
    notes.append(
        "折旧为会计口径、非现金支出；现金流核算请使用 capex_one_time 与 annual_cash_opex"
    )
    notes.append(f"价格适用性判定基准日：{as_of.isoformat()}")

    return TcoResult(
        plan_id=plan.plan_id,
        candidate_id=plan.candidate_id,
        hardware_id=plan.hardware_id,
        cost_basis=plan.cost_basis,
        gpu_count=plan.gpu_count,
        server_count=plan.server_count,
        horizon_years=horizon,
        region=plan.region,
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
        applicability_errors=applicability_errors,
        validity_unknown_items=validity_unknown,
        coverage_policy_id=coverage_policy_id,
        coverage_policy_version=coverage_policy_version,
        coverage_policy_authority=coverage_policy_authority,
        coverage_decisions=coverage_decisions,
        coverage_gaps=coverage_gaps,
        scope_exclusions=scope_exclusions,
        duplicate_billing=duplicate_billing,
        currency_confirmed=currency_confirmed,
        contains_synthetic_prices=contains_synthetic,
        unconfirmed_items=unconfirmed_items,
        notes=notes,
    )


def tco_snapshot_hash(tco: TcoResult) -> str:
    """成本快照指纹（V4.1 F4.1）：用于把人工审批绑定到**具体这一版成本**。

    覆盖决定金额的字段（方案身份、口径、各项金额与币种）。审批记录保存该指纹，
    报价时重新计算并比对 —— 成本变了，旧审批自动失效。
    """
    import hashlib
    import json

    payload = {
        # ---- 方案与金额 ----
        "plan_id": tco.plan_id,
        "candidate_id": tco.candidate_id,
        "hardware_id": tco.hardware_id,
        "cost_basis": tco.cost_basis,
        "region": tco.region,
        "gpu_count": tco.gpu_count,
        "server_count": tco.server_count,
        "horizon_years": tco.horizon_years,
        "currency": tco.currency,
        "capex": str(tco.capex_one_time),
        "opex": str(tco.annual_cash_opex),
        "depreciation": str(tco.annual_depreciation),
        # ---- 计算依据（V4.3 P1-2）----
        # 只对金额取哈希是不够的：两份"要求不同成本类别"的覆盖策略
        # 可能算出同样的金额，但"哪些成本该计入"的业务规则已经变了。
        "coverage_policy_id": tco.coverage_policy_id,
        "coverage_policy_version": tco.coverage_policy_version,
        "coverage_policy_authority": tco.coverage_policy_authority,
        "coverage_decisions": dict(sorted(tco.coverage_decisions.items())),
        "coverage_gaps": sorted(tco.coverage_gaps),
        "scope_exclusions": dict(sorted(tco.scope_exclusions.items())),
        "incomplete": tco.incomplete,
        "completeness_reasons": sorted(tco.completeness_reasons),
        "unknown_items": sorted(tco.unknown_items),
        "excluded_items": sorted(tco.excluded_items),
        "applicability_errors": sorted(tco.applicability_errors),
        "currency_confirmed": tco.currency_confirmed,
        "contains_synthetic_prices": tco.contains_synthetic_prices,
        "unconfirmed_items": sorted(tco.unconfirmed_items),
        "components": [
            {
                "item_id": c.item_id,
                "category": c.category,
                "basis": c.basis,
                "price_basis": c.price_basis,
                "unit_price": str(c.unit_price),
                "unit": c.unit,
                "currency": c.currency,
                "currency_status": c.currency_status,
                "status": c.status,
                "origin": c.origin,
                "quantity": str(c.quantity),
                "annual": str(c.annual_cost),
                "one_time": str(c.one_time_cost),
                "determination": c.determination,
                "applicability_issue": c.applicability_issue,
                "evidence_id": c.evidence_id,
            }
            for c in sorted(tco.components, key=lambda c: c.item_id)
        ],
    }
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


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
