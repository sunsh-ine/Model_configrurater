"""第四阶段（M26–M33）测试：TCO、业务收益、报价门禁与部署决策。

覆盖要点：

* 成本分解正确、**买断与租赁互斥**、**未知不等于零**、年度与一次性分开；
* 折旧是会计口径、不进入现金流；
* 收入从业务请求量推算，**不得**用第一阶段的理论满载收入；
* 可计费缓存比例是**计费口径**，引擎物理命中率不是合法来源；
* 报价门禁四维独立、fail-closed、不可绕过；
* 跨口径候选不可比较经济性；
* Agent 接口只读且白名单化。
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from llm_capacity_pricing.agent_api import AGENT_OPS, call_agent_op
from llm_capacity_pricing.decision.comparison import CandidateEconomics, compare_deployments
from llm_capacity_pricing.decision.sensitivity import (
    analyze_breakeven,
    analyze_sensitivity,
    check_budget,
)
from llm_capacity_pricing.engines.quote_gate import (
    SlaRecheck,
    evaluate_quote_eligibility,
    quote_eligibility_from_claims,
)
from llm_capacity_pricing.engines.revenue import (
    CapacityEvidence,
    SlaLimits,
    compute_revenue,
)
from llm_capacity_pricing.engines.tco import (
    build_cash_flow,
    compute_tco,
    tco_snapshot_hash,
)
from llm_capacity_pricing.reporting.tco_report import build_tco_report
from llm_capacity_pricing.schemas.commercial import (
    ApprovalScope,
    BillingRule,
    CommercialConfirmation,
    DemandPeriod,
    QuoteApproval,
    RevenueScenario,
    SlaEvidenceRecord,
)
from llm_capacity_pricing.schemas.tco import (
    CostCatalogItem,
    CostCoveragePolicy,
    CostCoverageRequirement,
    CostLineSelection,
    DeploymentCostPlan,
    FinancingAssumptions,
    ScopeExclusion,
)
from llm_capacity_pricing.services import (
    ServiceError,
    build_cost_plan_from_template,
    capacity_evidence_for,
    cost_catalog_summary,
    import_cost_catalog,
    import_commercial_confirmations,
    import_quote_approvals,
    init_db,
    load_cost_plan_templates,
    phase4_decision_for,
    quote_eligibility_for,
    record_sla_evidence_for,
    revenue_for,
    tco_for,
)

CATALOG_EXAMPLE = "data/examples/phase4_cost_inputs.example.yaml"
PLAN_EXAMPLE = "data/examples/phase4_cost_plan.example.yaml"
DEMAND_EXAMPLE = "data/examples/phase4_demand.example.yaml"


# --------------------------------------------------------------------------
# 机制夹具
# --------------------------------------------------------------------------


def _item(
    item_id: str,
    category: str,
    price_basis: str,
    unit_price: str | None,
    *,
    basis: str | None = None,
    unit: str = "currency",
    currency: str | None = "CNY",
    currency_status: str = "confirmed",
    status: str = "confirmed",
    default_quantity: str | None = None,
    origin: str = "internal_actual",
) -> CostCatalogItem:
    return CostCatalogItem(
        item_id=item_id,
        category=category,
        name=f"mechanism-fixture {item_id}",
        basis=basis,
        price_basis=price_basis,
        unit_price=Decimal(unit_price) if unit_price is not None else None,
        unit=unit,
        currency=currency,
        currency_status=currency_status,
        status=status,
        origin=origin,
        default_quantity=Decimal(default_quantity) if default_quantity else None,
        evidence_id=f"ev_{item_id}",
    )


@pytest.fixture
def confirmed_items() -> list[CostCatalogItem]:
    """**机制夹具**：为验证"成本完整时门禁可通过"而构造的全确认成本目录。

    数值是人工取的圆整数，不对应任何真实报价；只是为了让
    ``TcoResult.incomplete`` 能取到 False，从而走到门禁的"通过"分支。
    """
    return [
        _item("gpu", "gpu_hardware", "one_time_per_gpu", "100000", basis="owned"),
        _item("chassis", "server_chassis", "one_time_per_server", "50000", basis="owned"),
        _item("power", "power_energy", "per_kwh", "1"),
        _item("space", "facility_space", "per_rack_year", "1000", default_quantity="1"),
        _item("ops", "operations", "per_server_year", "2000"),
    ]


def _assumptions(**overrides) -> FinancingAssumptions:
    base = dict(
        horizon_years=3,
        depreciation_years=3,
        residual_value_fraction=Decimal("0"),
        discount_rate_per_year=Decimal("0.1"),
        pue=Decimal("1.2"),
        pue_basis="assumed",
        gpu_power_watts=Decimal("1000"),
        gpu_power_basis="assumed",
        server_overhead_watts=Decimal("0"),
        it_power_utilization=Decimal("1"),
    )
    base.update(overrides)
    return FinancingAssumptions(**base)


#: 机制夹具的默认覆盖策略：只要求夹具中真实出现的 5 个类别
COVERAGE_CATEGORIES = (
    "gpu_hardware",
    "server_chassis",
    "power_energy",
    "facility_space",
    "operations",
)


def _coverage(*categories: str, policy_id: str = "mech") -> CostCoveragePolicy:
    return CostCoveragePolicy(
        policy_id=policy_id,
        version="1.0",
        requirements=[
            CostCoverageRequirement(category=c)  # type: ignore[arg-type]
            for c in (categories or COVERAGE_CATEGORIES)
        ],
    )


DEFAULT_COVERAGE = _coverage()


def _tco(plan, items, policy=None):
    """默认带覆盖策略调用引擎（V4.1 F4.2：没有策略就不算完整）。"""
    return compute_tco(plan, items, coverage_policy=DEFAULT_COVERAGE if policy is None else policy)


def _plan(items, *, basis="owned", gpu_count=8, server_count=1, **overrides):
    return DeploymentCostPlan(
        plan_id="p-mech",
        candidate_id="cand-mech",
        hardware_id="nvidia-h100-sxm-80gb",
        gpu_count=gpu_count,
        server_count=server_count,
        cost_basis=basis,
        assumptions=_assumptions(**overrides),
        selections=[CostLineSelection(item_id=i.item_id) for i in items],
    )


# --------------------------------------------------------------------------
# M28：TCO 手算对照
# --------------------------------------------------------------------------


def test_m28_hand_computed_tco_matches_engine(confirmed_items):
    """手算对照：8 卡 / 1 台服务器 / 买断 / 3 年。

    手算过程（测试内用字面量重算一遍，作为独立算术路径）：

        年耗电 kWh = (1000W × 8 + 0W × 1) / 1000 × 8760 h × 1.0 × PUE 1.2 = 84,096 kWh
        电费      = 84,096 × 1 = 84,096
        机柜 1 个 = 1,000
        运维 1 台 = 2,000
        年现金支出 = 84,096 + 1,000 + 2,000 = 87,096
        一次性     = 100,000 × 8 + 50,000 × 1 = 850,000
        全周期现金 = 850,000 + 87,096 × 3 = 1,111,288
        单位卡年   = 1,111,288 / (8 × 3) = 46,303.666...
        折旧       = 850,000 × (1 - 0) / 3 = 283,333.333...
        年总成本   = 87,096 + 283,333.333... = 370,429.333...
    """
    tco = _tco(_plan(confirmed_items), confirmed_items)

    kwh = (Decimal(1000) * 8) / Decimal(1000) * Decimal(8760) * Decimal(1) * Decimal("1.2")
    assert kwh == Decimal("84096.000")
    power = kwh * Decimal(1)
    opex = power + Decimal(1000) + Decimal(2000)
    capex = Decimal(100000) * 8 + Decimal(50000)
    total = capex + opex * 3

    assert tco.capex_one_time == capex == Decimal("850000")
    assert tco.annual_cash_opex == opex == Decimal("87096.000")
    assert tco.total_cash_over_horizon == total == Decimal("1111288.000")
    assert tco.cost_per_gpu_year == total / 24
    assert tco.annual_depreciation == Decimal("850000") / 3
    assert tco.annual_total_cost == opex + Decimal("850000") / 3
    assert tco.incomplete is False
    assert tco.currency_confirmed is True


def test_m28_cash_flow_separates_capex_and_opex(confirmed_items):
    """现金流：一次性在第 0 年，年支出在第 1..N 年；折旧**不进入**现金流。"""
    tco = _tco(_plan(confirmed_items), confirmed_items)
    cf = build_cash_flow(tco)

    assert len(cf.years) == tco.horizon_years + 1
    y0 = cf.years[0]
    assert y0.capex == tco.capex_one_time
    assert y0.opex is None
    assert y0.net_cash == -tco.capex_one_time
    for row in cf.years[1:]:
        assert row.capex == Decimal(0)
        assert row.opex == tco.annual_cash_opex
    # 折旧不进现金流：净现金合计等于 capex + opex×年限
    total_net = sum(r.net_cash for r in cf.years)
    assert total_net == -tco.total_cash_over_horizon
    # NPV 用折现率算出，且绝对值小于未折现总额
    assert cf.npv is not None
    assert abs(cf.npv) < tco.total_cash_over_horizon


def test_m28_leased_basis_has_no_capex_and_no_depreciation():
    """租赁口径：不产生一次性投入、不计折旧；买断专属成本项被显式排除。"""
    items = [
        _item("gpu-buy", "gpu_hardware", "one_time_per_gpu", "100000", basis="owned"),
        _item("gpu-lease", "gpu_hardware", "per_gpu_year", "30000", basis="leased"),
        _item("ops", "operations", "per_server_year", "2000"),
    ]
    leased = _tco(_plan(items, basis="leased"), items, _coverage("gpu_hardware", "operations"))
    assert leased.capex_one_time is None
    assert leased.annual_depreciation is None
    assert leased.annual_cash_opex == Decimal("30000") * 8 + Decimal("2000")
    excluded = [c.item_id for c in leased.components if c.determination == "excluded_by_cost_basis"]
    assert excluded == ["gpu-buy"]
    # 被排除的项不得带金额（排除 ≠ 零成本）
    excluded_component = next(c for c in leased.components if c.item_id == "gpu-buy")
    assert excluded_component.annual_cost is None
    assert excluded_component.one_time_cost is None

    owned = _tco(_plan(items, basis="owned"), items, _coverage("gpu_hardware", "operations"))
    assert owned.capex_one_time == Decimal("800000")
    assert owned.annual_depreciation is not None
    assert [c.item_id for c in owned.components if c.determination == "excluded_by_cost_basis"] == [
        "gpu-lease"
    ]


def test_m28_unknown_price_is_not_zero():
    """缺单价的成本项必须标 unknown_missing_price，且**不得**按 0 计入总额。"""
    items = [
        _item("gpu", "gpu_hardware", "one_time_per_gpu", "100000", basis="owned"),
        _item("unknown-net", "network", "per_server_year", None, status="pending_business_confirmation"),
    ]
    tco = _tco(_plan(items), items)
    unknown = next(c for c in tco.components if c.item_id == "unknown-net")
    assert unknown.determination == "unknown_missing_price"
    assert unknown.annual_cost is None
    assert "network/unknown-net" in tco.unknown_items
    assert tco.incomplete is True
    assert any("未知不等于零" in n or "缺价格" in n for n in tco.notes)


def test_m28_missing_energy_assumptions_is_unknown_not_zero(confirmed_items):
    """缺功率/PUE 假设时，电费项标 unknown_missing_basis，而不是按 0 或 1.0 顶替。"""
    plan = _plan(
        confirmed_items,
        pue=None,
        gpu_power_watts=None,
    )
    tco = _tco(plan, confirmed_items)
    power = next(c for c in tco.components if c.item_id == "power")
    assert power.determination == "unknown_missing_basis"
    assert "gpu_power_watts" in (power.reason or "")
    assert "pue" in (power.reason or "")
    assert power.annual_cost is None
    assert tco.incomplete is True


def test_m28_mixed_currencies_block_formal_use(confirmed_items):
    items = list(confirmed_items) + [
        _item("usd-net", "network", "per_server_year", "100", currency="USD")
    ]
    tco = _tco(_plan(items), items)
    assert tco.currency is None
    assert tco.incomplete is True
    assert any("mixed_currencies" in r for r in tco.completeness_reasons)
    assert tco.currency_confirmed is False


def test_m28_synthetic_prices_make_tco_incomplete(confirmed_items):
    items = list(confirmed_items)
    items[0] = items[0].model_copy(
        update={"status": "synthetic_example", "currency_status": "unconfirmed"}
    )
    tco = _tco(_plan(items), items)
    assert tco.contains_synthetic_prices is True
    assert tco.incomplete is True
    assert any("synthetic" in r for r in tco.completeness_reasons)


# --------------------------------------------------------------------------
# M27：成本目录数据管理
# --------------------------------------------------------------------------


def test_m27_example_catalog_is_importable_and_marks_synthetic(tmp_path):
    db = str(tmp_path / "m27.db")
    init_db(db)
    info = import_cost_catalog(CATALOG_EXAMPLE, db)
    assert info["cost_item_count"] > 0
    assert info["synthetic_item_count"] > 0

    summary = cost_catalog_summary(db)
    assert summary["contains_synthetic"] is True
    assert "gpu_hardware" in summary["by_category"]
    # 故意留空的条目必须被列出来，而不是被当成免费
    assert "syn-infiniband-fabric" in summary["items_missing_price"]


def test_m27_import_is_idempotent(tmp_path):
    db = str(tmp_path / "m27b.db")
    init_db(db)
    first = import_cost_catalog(CATALOG_EXAMPLE, db)
    second = import_cost_catalog(CATALOG_EXAMPLE, db)
    assert first["cost_item_count"] == second["cost_item_count"]
    assert cost_catalog_summary(db)["item_count"] == first["cost_item_count"]


def test_m27_plan_templates_loaded_by_id(root: Path):
    templates = load_cost_plan_templates(PLAN_EXAMPLE)
    assert "syn-owned-3y" in templates
    assert "syn-leased-3y" in templates
    assert templates["syn-owned-3y"]["cost_basis"] == "owned"
    assert templates["syn-leased-3y"]["cost_basis"] == "leased"


def test_m27_synthetic_items_must_be_labelled(tmp_path):
    """合成条目名称必须带「【合成」标记，避免合成数字被误读为真实报价。"""
    from llm_capacity_pricing.importers.cost_catalog import CostCatalogError, load_cost_catalog

    bad = tmp_path / "bad.yaml"
    bad.write_text(
        """
cost_items:
  - item_id: sneaky
    category: power_energy
    name: "看起来很真实的价格"
    price_basis: per_kwh
    unit_price: "0.5"
    unit: currency_per_kwh
    status: synthetic_example
    origin: assumed
""",
        encoding="utf-8",
    )
    with pytest.raises(CostCatalogError, match="【合成"):
        load_cost_catalog(bad)


# --------------------------------------------------------------------------
# M29：业务收益情景
# --------------------------------------------------------------------------


def _rule(**overrides) -> BillingRule:
    base = dict(
        rule_id="r1",
        model_name="qwen3-32b",
        currency="CNY",
        currency_status="confirmed",
        price_uncached_input_per_1m=Decimal("4"),
        price_cached_input_per_1m=Decimal("1"),
        price_output_per_1m=Decimal("12"),
        billable_cache_ratio=Decimal("0.3"),
        billable_cache_basis="contract_defined",
        status="confirmed",
        evidence_id="ev_r1",
    )
    base.update(overrides)
    return BillingRule(**base)


def _scenario(**overrides) -> RevenueScenario:
    base = dict(
        scenario_id="s1",
        model_name="qwen3-32b",
        billing_rule_id="r1",
        periods_cover="one_day",
        days_per_year=365,
        periods=[
            DemandPeriod(
                label="peak",
                duration_hours=Decimal("8"),
                request_count=200000,
                avg_input_tokens=Decimal("1024"),
                avg_output_tokens=Decimal("256"),
                cached_input_fraction=Decimal("0.3"),
                is_peak=True,
            )
        ],
    )
    base.update(overrides)
    return RevenueScenario(**base)


def test_m29_hand_computed_revenue_matches_engine():
    """手算对照：20 万请求 / 输入 1024 / 输出 256 / 缓存占比 0.3。

        未缓存输入 = 200000 × 1024 × 0.7 = 143,360,000 → ×4/1e6 = 573.44
        缓存输入   = 200000 × 1024 × 0.3 =  61,440,000 → ×1/1e6 =  61.44
        输出       = 200000 × 256        =  51,200,000 → ×12/1e6 = 614.40
        时段合计 = 1249.28；年度 = 1249.28 × 365 = 455,987.20
    """
    result = compute_revenue(_scenario(), _rule())
    period = result.periods[0]
    assert period.billable_input_tokens_uncached == Decimal("143360000.000")
    assert period.billable_input_tokens_cached == Decimal("61440000.000")
    assert period.billable_output_tokens == Decimal("51200000.000")
    assert period.revenue == Decimal("1249.28")
    assert result.period_revenue_total == Decimal("1249.28")
    assert result.annual_revenue == Decimal("1249.28") * 365


def test_m29_peak_rate_and_capacity_check_unknown_without_evidence():
    result = compute_revenue(_scenario(), _rule())
    assert result.peak_request_rate_rps == Decimal(200000) / (Decimal(8) * 3600)
    assert result.capacity_check == "unknown_no_measurement"
    assert result.capacity_detail is not None
    assert result.capacity_detail.overall == "unknown_no_measurement"
    assert result.scenario_only is True
    assert any("容量未完整验证" in w for w in result.warnings)


def _full_evidence(**overrides) -> CapacityEvidence:
    """五子项齐备的证据：用于验证 only-all-satisfied 才给 within_capacity。"""
    base = dict(
        run_id="run-1",
        is_verified=True,
        # Little 定律：6.944 req/s × (100 + 10×256)/1000 s ≈ 18.5 并发，取 32 留余量
        configured_concurrency=32,
        request_throughput_rps=Decimal("10"),
        output_throughput_tok_s=Decimal("2000"),
        match_level="exact_match",
        data_trust="measured_internal",
        input_len_target=1024,
        output_len_target=256,
        ttft_p99_ms=Decimal("100"),
        tpot_p99_ms=Decimal("10"),
        error_rate=Decimal("0"),
        sla_status="observed_pass",
        sustainability_verified=True,
    )
    base.update(overrides)
    return CapacityEvidence(**base)


def test_m29_output_only_is_not_enough_for_within_capacity():
    """V4.1 F4.6：只有 Output TPS 达标**不足**以判定容量可行。"""
    partial = CapacityEvidence(
        run_id="run-partial",
        is_verified=True,
        output_throughput_tok_s=Decimal("999999"),  # 输出吞吐非常充裕
        match_level="exact_match",
        data_trust="measured_internal",
    )
    result = compute_revenue(_scenario(), _rule(), capacity_evidence=partial)
    assert result.capacity_check == "partially_verified"
    assert result.capacity_detail.output_throughput == "satisfied"
    assert result.capacity_detail.request_throughput == "unknown"
    assert result.capacity_detail.concurrency_and_load == "unknown"
    assert result.capacity_detail.sustainability == "unknown"
    assert result.scenario_only is True


def test_m29_all_five_subchecks_needed_for_within_capacity():
    limits = SlaLimits(
        ttft_p99_ms_max=Decimal("200"), tpot_p99_ms_max=Decimal("20")
    )
    result = compute_revenue(
        _scenario(), _rule(), capacity_evidence=_full_evidence(), sla_limits=limits
    )
    d = result.capacity_detail
    assert (d.output_throughput, d.request_throughput, d.concurrency_and_load) == (
        "satisfied",
        "satisfied",
        "satisfied",
    )
    assert (d.sla_evidence, d.sustainability) == ("satisfied", "satisfied")
    assert result.capacity_check == "within_capacity"
    assert result.scenario_only is False


def test_m29_any_failing_subcheck_yields_exceeds_capacity():
    limits = SlaLimits(ttft_p99_ms_max=Decimal("200"), tpot_p99_ms_max=Decimal("20"))
    # 请求吞吐不足
    tight_rps = _full_evidence(request_throughput_rps=Decimal("1"))
    r1 = compute_revenue(_scenario(), _rule(), capacity_evidence=tight_rps, sla_limits=limits)
    assert r1.capacity_check == "exceeds_capacity"
    assert r1.capacity_detail.request_throughput == "not_satisfied"

    # 并发不足（Little 定律推算需求并发 > 实测配置并发）
    low_conc = _full_evidence(configured_concurrency=1)
    r2 = compute_revenue(_scenario(), _rule(), capacity_evidence=low_conc, sla_limits=limits)
    assert r2.capacity_check == "exceeds_capacity"
    assert r2.capacity_detail.concurrency_and_load == "not_satisfied"

    # SLA 不满足
    r3 = compute_revenue(
        _scenario(),
        _rule(),
        capacity_evidence=_full_evidence(),
        sla_limits=SlaLimits(ttft_p99_ms_max=Decimal("10")),
    )
    assert r3.capacity_detail.sla_evidence == "not_satisfied"
    assert r3.capacity_check == "exceeds_capacity"

    # 负载长度不一致（实测是别的负载）
    r4 = compute_revenue(
        _scenario(),
        _rule(),
        capacity_evidence=_full_evidence(input_len_target=8192),
        sla_limits=limits,
    )
    assert r4.capacity_detail.concurrency_and_load == "not_satisfied"


def test_m29_unverified_evidence_cannot_yield_within_capacity():
    ev = CapacityEvidence(
        run_id="run-syn",
        is_verified=False,
        output_throughput_tok_s=Decimal("999999"),
        match_level="comparable_reference",
        data_trust="synthetic_fixture",
    )
    result = compute_revenue(_scenario(), _rule(), capacity_evidence=ev)
    assert result.capacity_check == "unknown_no_measurement"
    assert result.scenario_only is True
    assert any("已排除不可信证据" in b for b in result.capacity_basis)


def test_m29_cache_fraction_is_contract_cap_not_conservative():
    """V4.1 F4.4：min() 是**合同上限**，不是"保守估计"——缓存价更低时它提高收入。"""
    # 业务估计 0.6，合同只认 0.2
    scenario = _scenario(periods=[DemandPeriod(
        label="p", duration_hours=Decimal("1"), request_count=1000,
        avg_input_tokens=Decimal("1000"), avg_output_tokens=Decimal("0"),
        cached_input_fraction=Decimal("0.6"),
    )])
    rule = _rule(billable_cache_ratio=Decimal("0.2"))
    result = compute_revenue(scenario, rule)
    period = result.periods[0]
    assert period.billable_input_tokens_cached == Decimal(1000) * 1000 * Decimal("0.2")
    assert "min(业务估计 0.6, 合同上限 0.2) = 0.2" in period.cache_rule
    # 缓存价更低 -> 少用缓存价 => 收入更高；区间上界是"完全不打折"
    assert period.revenue > period.revenue_lower_bound - Decimal("1e-9")
    assert period.revenue_upper_bound > period.revenue
    assert period.revenue <= period.revenue_upper_bound
    assert any("不是保守下界" in n for n in result.notes)


def test_m29_revenue_bounds_widen_with_unconfirmed_cache_rule():
    """用 60% 业务估计对比 30% 合同上限：折扣越少收入越高（方向性验证）。"""
    base_period = DemandPeriod(
        label="p", duration_hours=Decimal("1"), request_count=1000,
        avg_input_tokens=Decimal("1000"), avg_output_tokens=Decimal("0"),
        cached_input_fraction=Decimal("0.6"),
    )
    high_discount = compute_revenue(
        _scenario(periods=[base_period]),
        _rule(billable_cache_ratio=Decimal("0.6")),
    )
    low_discount = compute_revenue(
        _scenario(periods=[base_period]),
        _rule(billable_cache_ratio=Decimal("0.3")),
    )
    assert low_discount.annual_revenue > high_discount.annual_revenue


def test_m29_batch_discount_applied_only_to_batch_periods():
    base = _scenario()
    batch = _scenario(
        periods=[
            base.periods[0].model_copy(update={"batch": True}),
        ]
    )
    plain = compute_revenue(base, _rule(batch_discount_fraction=Decimal("0.5")))
    discounted = compute_revenue(batch, _rule(batch_discount_fraction=Decimal("0.5")))
    assert plain.periods[0].batch_discount == Decimal(0)
    assert discounted.periods[0].batch_discount == discounted.periods[0].gross_revenue / 2
    assert discounted.periods[0].revenue == plain.periods[0].revenue / 2


def test_m29_missing_price_raises_instead_of_zero():
    rule = _rule(price_output_per_1m=None)
    with pytest.raises(ValueError, match="缺少单价"):
        compute_revenue(_scenario(), rule)


def test_m29_physical_cache_hit_rate_is_not_a_billable_basis():
    """引擎物理命中率不是合法的计费依据（schema 层面直接拒绝）。"""
    with pytest.raises(Exception, match="billable_cache_basis"):
        BillingRule(
            rule_id="bad",
            model_name="m",
            price_output_per_1m=Decimal("1"),
            billable_cache_ratio=Decimal("0.5"),
            billable_cache_basis="physical_cache_hit_rate",  # type: ignore[arg-type]
            evidence_id="e",
        )


def test_m29_ratio_requires_declared_basis():
    with pytest.raises(Exception, match="billable_cache_basis"):
        BillingRule(
            rule_id="bad",
            model_name="m",
            price_output_per_1m=Decimal("1"),
            billable_cache_ratio=Decimal("0.5"),
            evidence_id="e",
        )


# --------------------------------------------------------------------------
# M30：报价门禁
# --------------------------------------------------------------------------


def _confirmation(**overrides) -> CommercialConfirmation:
    base = dict(
        confirmation_id="c1",
        scope="qwen3-32b 报价",
        currency_confirmed=True,
        price_confirmed=True,
        terms_confirmed=True,
        confirmed_by="业务负责人（示例）",
        confirmed_at="2026-10-15T10:00:00+08:00",
        valid_until="2026-12-31",
        document_ref="DOC-EXAMPLE-001",
    )
    base.update(overrides)
    return CommercialConfirmation(**base)


def _sla_evidence(**overrides) -> SlaEvidenceRecord:
    base = dict(
        sla_evaluation_id="sla-1",
        candidate_id="cand-mech",
        requirement_id="req-1",
        requirement={"requirement_id": "req-1", "ttft_p99_ms_max": 400},
        target_identity={"model_name": "m", "hardware_model": "h"},
        status="observed_pass",
        match_level="exact_match",
        matched_run_id="run-1",
        data_trust="measured_internal",
        policy_id="default",
        evaluated_at="2026-10-15T10:00:00+08:00",
        run_pool_hash="0" * 64,
    )
    base.update(overrides)
    return SlaEvidenceRecord(**base)


def _approval(**overrides) -> QuoteApproval:
    scope = ApprovalScope(
        candidate_id="cand-mech",
        requirement_id="req-1",
        plan_id="p-mech",
        cost_snapshot_hash=overrides.pop("cost_snapshot_hash", "SNAP"),
    )
    base = dict(
        approval_id="apr-1",
        decision="approved",
        approved_by="评审人（示例）",
        approved_at="2026-10-15T10:00:00+08:00",
        valid_until="2026-12-31",
        document_ref="APR-EXAMPLE-001",
        scope=scope,
    )
    base.update(overrides)
    return QuoteApproval(**base)


def _recheck(**overrides) -> SlaRecheck:
    base = dict(
        ok=True,
        match_level="exact_match",
        status="observed_pass",
        run_id="run-1",
        reasons=["重新推导：match_level=exact_match status=observed_pass"],
    )
    base.update(overrides)
    return SlaRecheck(**base)


def _gate(tco, **overrides):
    """默认全通过的调用参数；各用例只覆盖它关心的那一维。"""
    kwargs = dict(
        candidate_id="cand-mech",
        requirement_id="req-1",
        tco=tco,
        sla_evidence=_sla_evidence(),
        sla_recheck=_recheck(),
        approval=_approval(),
        confirmation=_confirmation(),
        cost_snapshot_hash="SNAP",
        plan_id="p-mech",
    )
    kwargs.update(overrides)
    return evaluate_quote_eligibility(**kwargs)


def test_m30_quote_requires_all_four_dimensions(confirmed_items):
    tco = _tco(_plan(confirmed_items), confirmed_items)
    full = _gate(tco)
    assert full.quote_publishable is True
    assert full.blockers == []
    assert full.sla_evaluation_id == "sla-1"
    assert full.approval_id == "apr-1"


# --------------------------------------------------------------------------
# V4.1 F4.1：门禁只认持久化证据 —— 反向场景
# --------------------------------------------------------------------------


def test_f41_claims_only_entry_never_publishes(confirmed_items):
    """**评审指定的反向场景**：全部成本已确认 + 调用方声称 SLA 通过 +
    审批凭据只是任意字符串 -> 必须阻断。"""
    items = confirmed_items  # 全部 status=confirmed、币种已确认
    tco = _tco(_plan(items), items)
    assert tco.incomplete is False  # 成本侧"看起来"完整

    forged = quote_eligibility_from_claims(
        candidate_id="cand-mech",
        tco=tco,
        sla_status="observed_pass",
        match_level="exact_match",
        approval_status="approved",
        approval_ref="随便一个字符串",
    )
    assert forged.quote_publishable is False
    assert forged.claims_only is True
    assert any("claims_only_never_publishable" in b for b in forged.blockers)


def test_f41_service_rejects_missing_evidence_refs(tmp_path, root):
    """服务层：不提供证据 ID 直接拒绝（不给"声明即通过"的机会）。"""
    db = _phase4_db(tmp_path, root)
    with pytest.raises(ServiceError, match="sla-evaluation-id"):
        quote_eligibility_for(
            db, candidate_id="c", requirement_id="r", facts={}
        )
    with pytest.raises(ServiceError, match="approval-id"):
        quote_eligibility_for(
            db, candidate_id="c", requirement_id="r", facts={},
            sla_evaluation_id="sla-x",
        )


def test_f41_agent_api_rejects_status_string_params(tmp_path):
    """Agent 接口：传状态字符串属于未声明参数，直接拒绝。"""
    db = str(tmp_path / "agent41.db")
    init_db(db)
    import_cost_catalog(CATALOG_EXAMPLE, db)
    with pytest.raises(ServiceError, match="不接受参数"):
        call_agent_op(
            "phase4.quote_eligibility",
            db=db,
            candidate_id="c",
            sla_status="observed_pass",
            match_level="exact_match",
        )
    with pytest.raises(ServiceError, match="缺少证据引用"):
        call_agent_op("phase4.quote_eligibility", db=db, candidate_id="c")


def test_f41_random_approval_string_is_not_authorization(confirmed_items):
    """approval_ref 字符串不再是授权证明；必须引用审批记录。"""
    tco = _tco(_plan(confirmed_items), confirmed_items)
    result = evaluate_quote_eligibility(
        candidate_id="cand-mech",
        requirement_id="req-1",
        tco=tco,
        sla_evidence=_sla_evidence(),
        sla_recheck=_recheck(),
        approval=None,  # 只有字符串没有记录
        confirmation=_confirmation(),
        cost_snapshot_hash="SNAP",
    )
    assert result.quote_publishable is False
    assert any("approval_record_missing" in b for b in result.blockers)
    assert any("非空字符串不构成授权证明" in b for b in result.blockers)


def test_f41_sla_evidence_must_be_reverified(confirmed_items):
    """SLA 维度：没有重新推导、或重新推导不是 exact_match+observed_pass -> 阻断。"""
    tco = _tco(_plan(confirmed_items), confirmed_items)

    no_recheck = _gate(tco, sla_recheck=None)
    assert no_recheck.quote_publishable is False
    assert any("sla_evidence_not_reverified" in b for b in no_recheck.blockers)

    weak = _gate(tco, sla_recheck=_recheck(match_level="comparable_reference"))
    assert weak.quote_publishable is False
    assert any("sla_recheck_not_pass" in b for b in weak.blockers)

    failed = _gate(tco, sla_recheck=_recheck(ok=False, reasons=["matched_run_not_found"]))
    assert failed.quote_publishable is False
    assert any("sla_recheck_failed" in b for b in failed.blockers)


def test_f41_stale_sla_evidence_is_detected(confirmed_items):
    """记录里的结论与重新推导不一致 -> 证据已失效。"""
    tco = _tco(_plan(confirmed_items), confirmed_items)
    stale = _gate(
        tco,
        sla_evidence=_sla_evidence(status="observed_fail", matched_run_id="run-old"),
        sla_recheck=_recheck(),
    )
    assert stale.quote_publishable is False
    assert any("sla_evidence_stale" in b for b in stale.blockers)


def test_f41_evidence_scope_must_match_candidate_and_requirement(confirmed_items):
    tco = _tco(_plan(confirmed_items), confirmed_items)
    other_candidate = _gate(tco, sla_evidence=_sla_evidence(candidate_id="someone-else"))
    assert other_candidate.quote_publishable is False
    assert any("sla_evidence_scope_mismatch:candidate" in b for b in other_candidate.blockers)

    other_req = _gate(tco, sla_evidence=_sla_evidence(requirement_id="other-req"))
    assert any("sla_evidence_scope_mismatch:requirement" in b for b in other_req.blockers)


def test_f41_approval_is_bound_to_cost_snapshot(confirmed_items):
    """成本变了，旧审批自动失效。"""
    tco = _tco(_plan(confirmed_items), confirmed_items)
    snapshot = tco_snapshot_hash(tco)
    ok = _gate(tco, cost_snapshot_hash=snapshot, approval=_approval(cost_snapshot_hash=snapshot))
    assert ok.quote_publishable is True

    changed = tco.model_copy(update={"capex_one_time": tco.capex_one_time + Decimal(1)})
    stale = _gate(
        changed,
        cost_snapshot_hash=tco_snapshot_hash(changed),
        approval=_approval(cost_snapshot_hash=snapshot),
    )
    assert stale.quote_publishable is False
    assert any("approval_cost_snapshot_stale" in b for b in stale.blockers)


def test_f41_approval_scope_and_validity(confirmed_items):
    tco = _tco(_plan(confirmed_items), confirmed_items)
    wrong_scope = _gate(
        tco, approval=_approval(scope=ApprovalScope(
            candidate_id="other", requirement_id="req-1", plan_id="p-mech",
            cost_snapshot_hash="SNAP"))
    )
    assert wrong_scope.quote_publishable is False
    assert any("approval_scope_mismatch" in b for b in wrong_scope.blockers)

    expired = _gate(tco, approval=_approval(valid_until="2020-01-01"))
    assert expired.quote_publishable is False
    assert any("approval_expired" in b for b in expired.blockers)

    not_approved = _gate(tco, approval=_approval(decision="pending"))
    assert not_approved.quote_publishable is False
    assert any("approval_not_approved" in b for b in not_approved.blockers)


def test_f41_approved_approval_requires_audit_fields():
    """审批记录本身：decision=approved 必须齐备审批人/时间/有效期/凭据。"""
    with pytest.raises(Exception, match="approved_by"):
        QuoteApproval(
            approval_id="a", decision="approved",
            scope=ApprovalScope(candidate_id="c", requirement_id="r", plan_id="p",
                                cost_snapshot_hash="h"),
        )


def test_f41_report_does_not_output_amounts_when_blocked(confirmed_items):
    """报告层：门禁未过时只给阻断说明与水印。"""
    items = confirmed_items
    tco = _tco(_plan(items), items)
    blocked = _gate(tco, approval=None)
    md = build_tco_report(
        catalog={"item_count": 5, "billing_rule_count": 1, "currencies": ["CNY"],
                 "currency_confirmed_count": 5, "by_category": {}, "by_basis": {},
                 "by_status": {}, "items_missing_price": []},
        eligibilities=[blocked.model_dump(mode="json")],
        tco_details=[{"tco": tco.model_dump(mode="json")}],
        synthetic_notice="合成示例数据",
    )
    assert "草稿已阻断" in md
    assert "approval_record_missing" in md


# --------------------------------------------------------------------------
# 其余四维判定
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "recheck_kwargs",
    [
        dict(status="estimated_only", match_level="exact_match"),
        dict(status="not_evaluable", match_level=None),
        dict(status="observed_pass", match_level=None),
        dict(status="observed_pass", match_level="comparable_reference"),
    ],
)
def test_m30_sla_dimension_is_strict(confirmed_items, recheck_kwargs):
    """SLA 维度：只有 observed_pass + exact_match 才算 verified_measured。"""
    tco = _tco(_plan(confirmed_items), confirmed_items)
    result = _gate(tco, sla_recheck=_recheck(**recheck_kwargs))
    assert result.quote_publishable is False
    assert result.sla_verification_status != "verified_measured"


def test_m30_incomplete_cost_blocks_quote(confirmed_items):
    items = list(confirmed_items) + [
        _item("missing", "network", "per_server_year", None, status="pending_business_confirmation")
    ]
    tco = _tco(_plan(items), items)
    result = _gate(tco)
    assert result.cost_estimate_status == "incomplete"
    assert result.quote_publishable is False
    assert any("cost_unknown_items" in b for b in result.blockers)


def test_m30_unconfirmed_prices_block_quote(confirmed_items):
    """V4.1 F4.1：单价"填了但没确认"（pending）同样不得报价。"""
    items = [
        i.model_copy(update={"status": "pending_business_confirmation"})
        for i in confirmed_items
    ]
    tco = _tco(_plan(items), items)
    assert tco.incomplete is False          # 数据齐备
    assert tco.unconfirmed_items            # 但未确认
    result = _gate(tco)
    assert result.quote_publishable is False
    assert any("cost_items_unconfirmed" in b for b in result.blockers)


def test_m30_synthetic_price_blocks_quote_even_when_otherwise_complete(confirmed_items):
    items = [
        i.model_copy(update={"status": "synthetic_example", "currency_status": "unconfirmed"})
        for i in confirmed_items
    ]
    tco = _tco(_plan(items), items)
    result = _gate(tco)
    assert result.quote_publishable is False
    assert any("synthetic" in b for b in result.blockers)


def test_m30_missing_confirmation_blocks(confirmed_items):
    tco = _tco(_plan(confirmed_items), confirmed_items)
    result = _gate(tco, confirmation=None)
    assert result.quote_publishable is False
    assert result.commercial_confirmation_status == "missing"
    assert any("commercial_confirmation_missing" in b for b in result.blockers)


def test_m30_expired_confirmation_blocks(confirmed_items):
    tco = _tco(_plan(confirmed_items), confirmed_items)
    result = _gate(tco, confirmation=_confirmation(valid_until="2020-01-01"))
    assert result.quote_publishable is False
    assert any("expired" in b for b in result.blockers)


def test_m30_quote_eligibility_cannot_be_forged():
    """schema 层禁止"未通过却标记可发布"，也禁止没有证据 ID 的"已验证"。"""
    from llm_capacity_pricing.schemas.commercial import QuoteEligibility

    with pytest.raises(Exception, match="quote_publishable"):
        QuoteEligibility(
            candidate_id="c",
            cost_estimate_status="incomplete",
            sla_verification_status="verified_measured",
            commercial_confirmation_status="confirmed",
            approval_status="approved",
            approval_id="a",
            sla_evaluation_id="s",
            quote_publishable=True,
        )
    with pytest.raises(Exception, match="approval_id"):
        QuoteEligibility(
            candidate_id="c",
            cost_estimate_status="complete",
            sla_verification_status="verified_measured",
            commercial_confirmation_status="confirmed",
            approval_status="approved",
            sla_evaluation_id="s",
        )
    with pytest.raises(Exception, match="sla_evaluation_id"):
        QuoteEligibility(
            candidate_id="c",
            cost_estimate_status="complete",
            sla_verification_status="verified_measured",
            commercial_confirmation_status="confirmed",
            approval_status="missing",
        )
    with pytest.raises(Exception, match="诊断路径"):
        QuoteEligibility(
            candidate_id="c",
            cost_estimate_status="complete",
            sla_verification_status="verified_measured",
            commercial_confirmation_status="confirmed",
            approval_status="approved",
            approval_id="a",
            sla_evaluation_id="s",
            claims_only=True,
            quote_publishable=True,
        )


def test_f41_record_sla_evidence_persists_and_is_reused(tmp_path, root):
    """端到端：生成证据 -> 导入审批 -> 门禁从记录读取并重新核验。"""
    from llm_capacity_pricing.services import (
        import_hardware,
        import_models,
        import_performance_curve,
        open_db,
    )

    db = str(tmp_path / "f41e2e.db")
    init_db(db)
    import_models(root / "data/models/model_manifest.yaml", db, root=root)
    import_hardware(root / "data/hardware/hardware_catalog.yaml", db)
    import_cost_catalog(CATALOG_EXAMPLE, db)
    import_performance_curve(
        root / "data/fixtures/perf_curve/INDEX.json", db, root=root
    )

    facts = {
        "model_name": "SYNTHETIC-MODEL-C",
        "model_revision": "synthetic-revision-1",
        "weight_precision": "bf16",
        "hardware_model": "SYNTHETIC-GPU-C",
        "gpu_count": 8,
        "tp_size": 8,
        "dp_size": 1,
        "framework": "vllm",
        "framework_version": "synthetic-fw-1.0",
        "configured_concurrency": 8,
        "input_len_target": 1024,
        "output_len_target": 256,
        "dataset_name": "random",
        "arrival_mode": "unbounded",
    }
    evidence = record_sla_evidence_for(
        db,
        candidate_id="cand-mech",
        requirement={"requirement_id": "req-1", "ttft_p99_ms_max": 99999},
        facts=facts,
    )
    # synthetic 曲线默认不支撑生产结论 -> 结论不会是 observed_pass
    assert evidence["status"] != "observed_pass"

    d, repo = open_db(db)
    try:
        with d.session() as session:
            stored = repo.get_sla_evidence(session, evidence["sla_evaluation_id"])
    finally:
        d.dispose()
    assert stored is not None
    assert stored.run_pool_hash == evidence["run_pool_hash"]

    # 审批记录导入（人工事项）
    approvals_file = tmp_path / "approvals.yaml"
    approvals_file.write_text(
        f"""
approvals:
  - approval_id: apr-e2e
    decision: approved
    approved_by: "示例审批人"
    approved_at: "2026-10-15T10:00:00+08:00"
    valid_until: "2099-12-31"
    document_ref: "APR-E2E"
    scope:
      candidate_id: cand-mech
      requirement_id: req-1
      plan_id: "syn-owned-3y::cand-mech"
      cost_snapshot_hash: "{"0" * 64}"
""",
        encoding="utf-8",
    )
    info = import_quote_approvals(str(approvals_file), db)
    assert info["decisions"] == {"apr-e2e": "approved"}

    result = quote_eligibility_for(
        db,
        candidate_id="cand-mech",
        requirement_id="req-1",
        facts=facts,
        sla_evaluation_id=evidence["sla_evaluation_id"],
        approval_id="apr-e2e",
        plan_file=PLAN_EXAMPLE,
        template_id="syn-owned-3y",
        hardware_id="nvidia-h100-sxm-80gb",
        gpu_count=8,
        server_count=1,
    )
    elig = result["eligibility"]
    assert elig["quote_publishable"] is False
    # 证据明确、可追溯：SLA 证据 ID 与审批 ID 都被引用
    assert elig["sla_evaluation_id"] == evidence["sla_evaluation_id"]
    assert elig["approval_id"] == "apr-e2e"
    assert elig["cost_snapshot_hash"] == result["cost_snapshot_hash"]
    # 阻断原因必须点名到具体维度，而不是笼统的"未通过"
    joined = " ".join(elig["blockers"])
    assert "sla_recheck" in joined or "sla_" in joined


class _DummyRanked:
    def __init__(self, candidate_id: str, **kw):
        self.candidate_id = candidate_id
        self.hardware_id = "nvidia-h100-sxm-80gb"
        self.gpu_count = 8
        self.tp_size = 8
        self.dp_size = 1
        self.static_status = "conditional_feasible"
        self.match_level = kw.get("match_level", "exact_match")
        self.sla_status = kw.get("sla_status", "observed_pass")
        self.rank_bucket = kw.get("rank_bucket", "verified_sla_met")
        self.output_throughput_tok_s = Decimal("1000")
        self.ttft_p99_ms = Decimal("100")
        self.tpot_p99_ms = Decimal("20")
        self.evidence_run_ids = ["run-1"]


class _DummyRanking:
    def __init__(self, entries):
        self.entries = entries


def test_m31_incomparable_candidates_are_not_ranked_by_cost(confirmed_items):
    owned_tco = _tco(_plan(confirmed_items, basis="owned"), confirmed_items)
    leased_items = [
        _item("lease", "gpu_hardware", "per_gpu_year", "30000", basis="leased"),
        _item("ops", "operations", "per_server_year", "2000"),
    ]
    leased_tco = _tco(_plan(leased_items, basis="leased"), leased_items, _coverage("gpu_hardware", "operations"))
    ranking = _DummyRanking([_DummyRanked("owned"), _DummyRanked("leased")])
    comparison = compare_deployments(
        requirement_id="r",
        ranking=ranking,
        economics=[
            CandidateEconomics("owned", tco=owned_tco),
            CandidateEconomics("leased", tco=leased_tco),
        ],
    )
    kinds = {e.candidate_id: e.comparability for e in comparison.entries}
    assert kinds["leased"] == "not_comparable_basis"
    assert comparison.lowest_cost_candidate_id is None  # 可比者不足 2 个
    assert comparison.composite_score_used is False
    assert comparison.economic_scoring_used is False


def test_m31_incomplete_cost_never_yields_total(confirmed_items):
    items = list(confirmed_items) + [
        _item("missing", "network", "per_server_year", None, status="pending_business_confirmation")
    ]
    tco = _tco(_plan(items), items)
    ranking = _DummyRanking([_DummyRanked("c")])
    comparison = compare_deployments(
        requirement_id="r",
        ranking=ranking,
        economics=[CandidateEconomics("c", tco=tco)],
    )
    entry = comparison.entries[0]
    assert entry.cost_incomplete is True
    assert entry.total_cash_over_horizon is None
    assert entry.comparability == "incomplete_cost"


def test_m31_performance_dimension_is_read_only(confirmed_items):
    """比较结果原样沿用第三阶段结论，不做改写。"""
    tco = _tco(_plan(confirmed_items), confirmed_items)
    ranking = _DummyRanking(
        [_DummyRanked("c", sla_status="not_evaluable", match_level="comparable_reference",
                      rank_bucket="requires_benchmark")]
    )
    comparison = compare_deployments(
        requirement_id="r", ranking=ranking, economics=[CandidateEconomics("c", tco=tco)]
    )
    entry = comparison.entries[0]
    assert entry.sla_status == "not_evaluable"
    assert entry.match_level == "comparable_reference"
    assert entry.evidence_run_ids == ["run-1"]


def test_m32_budget_unknown_without_confirmed_currency():
    unknown = check_budget(
        budget_id="b",
        budget_amount=Decimal("100"),
        budget_kind="total_horizon",
        compared_value=Decimal("50"),
        currency="CNY",
        currency_confirmed=False,
    )
    assert unknown.status == "unknown"
    assert any("currency_not_confirmed" in r for r in unknown.reasons)

    within = check_budget(
        budget_id="b",
        budget_amount=Decimal("100"),
        budget_kind="total_horizon",
        compared_value=Decimal("50"),
        currency="CNY",
        currency_confirmed=True,
    )
    assert within.status == "within_budget"
    assert within.headroom == Decimal("50")

    over = check_budget(
        budget_id="b",
        budget_amount=Decimal("10"),
        budget_kind="total_horizon",
        compared_value=Decimal("50"),
        currency="CNY",
        currency_confirmed=True,
    )
    assert over.status == "exceeds_budget"


def test_m32_sensitivity_reports_uncomputable_drivers(confirmed_items):
    plan = _plan(confirmed_items, pue=None)
    analysis = analyze_sensitivity(plan=plan, items=confirmed_items)
    assert any(not d.computable for d in analysis.drivers)
    assert any(d.driver == "assumption:pue" for d in analysis.drivers)
    assert analysis.uncomputable_drivers
    assert analysis.incomplete is True


def test_m32_sensitivity_is_single_factor_and_directional(confirmed_items):
    plan = _plan(confirmed_items)
    analysis = analyze_sensitivity(plan=plan, items=confirmed_items)
    base = analysis.base_value
    assert base is not None
    up = next(
        d
        for d in analysis.drivers
        if d.driver == "assumption:gpu_power_watts" and d.delta_fraction > 0
    )
    down = next(
        d
        for d in analysis.drivers
        if d.driver == "assumption:gpu_power_watts" and d.delta_fraction < 0
    )
    assert up.perturbed_value > base > down.perturbed_value
    # 单因子：+20% 与 -20% 的绝对变化不要求对称，但符号必须相反
    assert up.absolute_change > 0 > down.absolute_change


def test_m32_breakeven_reports_scope_and_capacity(confirmed_items):
    tco = _tco(_plan(confirmed_items), confirmed_items)
    revenue = compute_revenue(_scenario(), _rule())
    be = analyze_breakeven(tco=tco, revenue=revenue, rule=_rule())
    assert be.annual_cost == tco.annual_total_cost
    assert any("annual_total_cost" in n for n in be.notes)
    assert be.breakeven_billable_output_tokens is not None
    assert be.capacity_check == "unknown_no_measurement"
    assert any("未经验证" in n for n in be.notes)


# --------------------------------------------------------------------------
# M33：报告与 Agent 接口
# --------------------------------------------------------------------------


def test_m33_report_blocks_quote_and_watermarks(confirmed_items):
    """门禁未过时：报告不给金额，且打出阻断水印。"""
    items = [
        i.model_copy(update={"status": "synthetic_example", "currency_status": "unconfirmed"})
        for i in confirmed_items
    ]
    tco = _tco(_plan(items), items)
    eligibility = _gate(tco, approval=None, confirmation=None, sla_recheck=_recheck(
        ok=False, match_level=None, status=None, reasons=["matched_run_not_found"]
    ))
    md = build_tco_report(
        catalog={"item_count": 5, "billing_rule_count": 1, "currencies": ["CNY"],
                 "currency_confirmed_count": 0, "by_category": {}, "by_basis": {},
                 "by_status": {}, "items_missing_price": []},
        eligibilities=[eligibility.model_dump(mode="json")],
        tco_details=[{"tco": tco.model_dump(mode="json")}],
        synthetic_notice="合成示例数据",
    )
    assert "草稿已阻断" in md
    assert "四个独立结论" in md
    # 四个结论必须分开写，不得合并
    assert "工程功能是否完成" in md
    assert "商业数据是否确认" in md
    assert "SLA 是否具备实测依据" in md
    assert "正式报价是否可发布" in md
    # 阻断时不得输出"报价草稿含金额"的形态
    assert "以下为**草稿**" not in md


def test_m33_agent_api_is_whitelisted_and_read_only(tmp_path):
    db = str(tmp_path / "agent.db")
    init_db(db)
    import_cost_catalog(CATALOG_EXAMPLE, db)

    assert "phase4.cost_catalog" in AGENT_OPS
    out = call_agent_op("phase4.cost_catalog", db=db)
    assert out["item_count"] > 0

    with pytest.raises(ServiceError, match="未知 Agent 算子"):
        call_agent_op("phase4.delete_everything", db=db)


def test_m33_agent_api_requires_evidence_refs_not_status_strings(tmp_path):
    """Agent 调用门禁：必须给证据引用，且状态字符串属于非法参数。"""
    db = str(tmp_path / "agent2.db")
    init_db(db)
    import_cost_catalog(CATALOG_EXAMPLE, db)
    # 状态字符串属于未声明参数 -> 直接拒绝
    with pytest.raises(ServiceError, match="不接受参数"):
        call_agent_op(
            "phase4.quote_eligibility", db=db, candidate_id="c",
            approval_status="approved", approval_ref="x",
        )
    # 缺证据引用 -> 拒绝，不提供默认通过
    with pytest.raises(ServiceError, match="缺少证据引用"):
        call_agent_op("phase4.quote_eligibility", db=db, candidate_id="c")


def test_m33_no_commercial_confirmation_generator_in_agent_api():
    """Agent 接口**不存在**生成商务确认的算子（人工事项不得自动化）。"""
    assert not any("confirm" in op or "approve" in op for op in AGENT_OPS)


# --------------------------------------------------------------------------
# 端到端：服务层链路
# --------------------------------------------------------------------------


def _phase4_db(tmp_path: Path, root: Path) -> str:
    from llm_capacity_pricing.services import import_hardware, import_models

    db = str(tmp_path / "p4e2e.db")
    init_db(db)
    import_models(root / "data/models/model_manifest.yaml", db, root=root)
    import_hardware(root / "data/hardware/hardware_catalog.yaml", db)
    import_cost_catalog(CATALOG_EXAMPLE, db)
    return db


def test_e2e_tco_and_revenue_link(tmp_path, root):
    db = _phase4_db(tmp_path, root)
    info = tco_for(
        db,
        plan_file=PLAN_EXAMPLE,
        template_id="syn-owned-3y",
        candidate_id="cand-x",
        hardware_id="nvidia-h100-sxm-80gb",
        gpu_count=8,
        server_count=1,
    )
    assert Decimal(info["tco"]["capex_one_time"]) == Decimal("1680000")
    assert info["tco"]["incomplete"] is True  # 合成价格

    rev = revenue_for(
        db, demand_file=DEMAND_EXAMPLE, scenario_id="syn-demand-base"
    )
    assert Decimal(rev["revenue"]["annual_revenue"]) == Decimal("683980.80")
    assert rev["revenue"]["capacity_check"] == "unknown_no_measurement"


def test_e2e_incomplete_template_flags_unknown_item(tmp_path, root):
    db = _phase4_db(tmp_path, root)
    info = tco_for(
        db,
        plan_file=PLAN_EXAMPLE,
        template_id="syn-owned-incomplete",
        candidate_id="cand-x",
        hardware_id="nvidia-h100-sxm-80gb",
        gpu_count=8,
        server_count=1,
    )
    tco = info["tco"]
    assert "network/syn-infiniband-fabric" in tco["unknown_items"]
    assert any("unknown_missing_price" == c["determination"] for c in tco["components"])


def test_e2e_decision_chain_blocks_quote(tmp_path, root):
    from decimal import Decimal as D

    from llm_capacity_pricing.services import make_assumptions

    db = _phase4_db(tmp_path, root)
    info = phase4_decision_for(
        db,
        requirement={"requirement_id": "r", "ttft_p99_ms_max": 400},
        model_id="qwen3-32b",
        hardware_id="nvidia-h100-sxm-80gb",
        server_id="hgx-h100-8gpu",
        plan_file=PLAN_EXAMPLE,
        template_id="syn-owned-3y",
        assumptions=make_assumptions(
            usable_fraction_of_nominal=D("0.9"),
            usable_memory_source_kind="synthetic_example",
            runtime_reserve_fraction=D("0.1"),
            global_concurrency=32,
            context_length_per_request=8192,
            tp_sizes=(2, 4, 8),
            dp_sizes=(1,),
        ),
        demand_file=DEMAND_EXAMPLE,
        scenario_id="syn-demand-base",
    )
    assert info["comparison"]["entries"]
    assert all(not e["quote_publishable"] for e in info["comparison"]["entries"])
    assert info["limitations"]
    assert info["assumptions_notice"]
    # 每个候选都必须给出四维状态
    for e in info["eligibilities"]:
        assert e["cost_estimate_status"] in ("complete", "incomplete", "unknown")
        assert e["sla_verification_status"] in (
            "verified_measured", "estimated_only", "not_evaluable", "unverified"
        )


def test_m27_confirmation_import_records_audit_fields(tmp_path):
    """人工确认记录导入后必须带齐审计字段，且状态为 confirmed。"""
    db = str(tmp_path / "conf.db")
    init_db(db)
    info = import_commercial_confirmations(
        "data/examples/phase4_commercial_confirmation.example.yaml", db
    )
    assert info["confirmation_count"] == 1
    assert list(info["statuses"].values()) == ["confirmed"]

    from llm_capacity_pricing.services import open_db

    d, repo = open_db(db)
    try:
        with d.session() as session:
            conf = repo.get_commercial_confirmation(session, "syn-confirmation-demo")
    finally:
        d.dispose()
    assert conf is not None
    assert conf.confirmed_by and conf.confirmed_at and conf.valid_until and conf.document_ref
    assert conf.status == "confirmed"


def test_m29_capacity_evidence_absent_without_runs(tmp_path, root):
    """证据池为空时必须返回 None，而不是编造一条容量。"""
    db = _phase4_db(tmp_path, root)
    evidence = capacity_evidence_for(
        db,
        model_name="qwen3-32b",
        hardware_model="NVIDIA H100 SXM",
        gpu_count=8,
        tp_size=8,
        dp_size=1,
        weight_precision="bf16",
        configured_concurrency=32,
        input_len_target=8192,
        output_len_target=256,
    )
    assert evidence is None


def test_m28_build_plan_from_template_binds_candidate_facts(root):
    """模板只声明成本口径，卡数/服务器数由第二阶段候选事实绑定。"""
    templates = load_cost_plan_templates(PLAN_EXAMPLE)
    plan = build_cost_plan_from_template(
        templates["syn-owned-3y"],
        candidate_id="cand-9",
        hardware_id="nvidia-h100-sxm-80gb",
        gpu_count=4,
        server_count=1,
        tp_size=4,
        dp_size=1,
    )
    assert plan.gpu_count == 4
    assert plan.cost_basis == "owned"
    assert plan.candidate_id == "cand-9"
    assert "syn-gpu-purchase" in [s.item_id for s in plan.selections]


# --------------------------------------------------------------------------
# V4.1 F4.2：成本范围完整性 —— 不能靠"不选"来规避
# --------------------------------------------------------------------------


def test_f42_omitting_required_categories_makes_tco_incomplete(confirmed_items):
    """故意只选 GPU 采购 + 机箱：电力/网络/运维等未选且未排除 -> 必须不完整。"""
    partial = [i for i in confirmed_items if i.category in ("gpu_hardware", "server_chassis")]
    policy = _coverage(
        "gpu_hardware",
        "server_chassis",
        "power_energy",
        "network",
        "storage",
        "facility_space",
        "cooling",
        "software_license",
        "operations",
        "maintenance",
    )
    tco = _tco(_plan(partial), partial, policy)
    assert tco.incomplete is True
    assert set(tco.coverage_gaps) >= {"power_energy", "network", "operations"}
    assert any("coverage_gap" in r for r in tco.completeness_reasons)
    assert tco.coverage_decisions["power_energy"] == "missing"
    # 关键：金额仍然算得出来，但结论被明确标为不完整
    assert tco.capex_one_time is not None


def test_f42_explicit_scope_exclusion_is_accepted(confirmed_items):
    """显式排除（带理由）不算缺口，且理由必须原样保留。"""
    partial = [i for i in confirmed_items if i.category in ("gpu_hardware", "server_chassis")]
    policy = _coverage("gpu_hardware", "server_chassis", "power_energy")
    plan = _plan(partial).model_copy(
        update={
            "scope_exclusions": [
                ScopeExclusion(category="power_energy", reason="合成示例：电费由园区统一承担")
            ]
        }
    )
    tco = _tco(plan, partial, policy)
    assert tco.coverage_gaps == []
    assert tco.coverage_decisions["power_energy"] == "excluded_by_scope"
    assert tco.scope_exclusions["power_energy"] == "合成示例：电费由园区统一承担"
    assert tco.incomplete is False


def test_f42_scope_exclusion_without_reason_is_rejected():
    """排除必须给理由 —— 不给理由的"排除"就是缺数据。"""
    with pytest.raises(Exception, match="reason"):
        ScopeExclusion(category="power_energy")


def test_f42_policy_forbidding_exclusion_still_flags_gap(confirmed_items):
    """策略不允许排除的类别即便写了排除理由，也仍然算缺口。"""
    partial = [i for i in confirmed_items if i.category == "gpu_hardware"]
    policy = CostCoveragePolicy(
        policy_id="strict",
        version="1.0",
        requirements=[
            CostCoverageRequirement(category="gpu_hardware"),
            CostCoverageRequirement(category="power_energy", allow_scope_exclusion=False),
        ],
    )
    plan = _plan(partial).model_copy(
        update={
            "scope_exclusions": [
                ScopeExclusion(category="power_energy", reason="想排除但策略不允许")
            ]
        }
    )
    tco = _tco(plan, partial, policy)
    assert any("policy_forbids_scope_exclusion" in g for g in tco.coverage_gaps)
    assert tco.incomplete is True


def test_f42_missing_coverage_policy_is_incomplete(confirmed_items):
    """没有覆盖策略就不算完整 —— 否则"少选几类"就能蒙过检查。"""
    tco = compute_tco(_plan(confirmed_items), confirmed_items, coverage_policy=None)
    assert tco.incomplete is True
    assert any("coverage_policy_missing" in r for r in tco.completeness_reasons)
    assert tco.coverage_policy_id is None


def test_f42_duplicate_billing_is_flagged(confirmed_items):
    """同一类别、同一计价口径、同一适用范围出现两个成本项 -> 疑似重复计费。"""
    dup = _item("gpu-extra", "gpu_hardware", "one_time_per_gpu", "100000", basis="owned")
    items = list(confirmed_items) + [dup]
    tco = _tco(_plan(items), items)
    assert tco.duplicate_billing
    assert any("duplicate_billing" in r for r in tco.completeness_reasons)
    assert tco.incomplete is True


def test_f42_example_templates_have_no_coverage_gaps(tmp_path, root):
    """示例的"标准"模板必须无覆盖缺口；"不完整"模板必须有缺口。"""
    db = _phase4_db(tmp_path, root)
    good = tco_for(
        db, plan_file=PLAN_EXAMPLE, template_id="syn-owned-3y",
        candidate_id="c", hardware_id="nvidia-h100-sxm-80gb",
        gpu_count=8, server_count=1,
    )
    assert good["tco"]["coverage_gaps"] == []
    leased = tco_for(
        db, plan_file=PLAN_EXAMPLE, template_id="syn-leased-3y",
        candidate_id="c", hardware_id="nvidia-h100-sxm-80gb",
        gpu_count=8, server_count=1,
    )
    assert leased["tco"]["coverage_gaps"] == []
    assert leased["tco"]["coverage_decisions"]["server_chassis"] == "excluded_by_scope"

    bad = tco_for(
        db, plan_file=PLAN_EXAMPLE, template_id="syn-owned-incomplete",
        candidate_id="c", hardware_id="nvidia-h100-sxm-80gb",
        gpu_count=8, server_count=1,
    )
    assert set(bad["tco"]["coverage_gaps"]) >= {
        "network", "software_license", "operations", "maintenance"
    }


# --------------------------------------------------------------------------
# V4.1 F4.3：价格适用范围与有效期
# --------------------------------------------------------------------------


def test_f43_price_for_other_gpu_sku_is_not_applicable(confirmed_items):
    """H200 的价格不能用在 H100 方案上。"""
    h200_price = _item(
        "gpu-h200", "gpu_hardware", "one_time_per_gpu", "300000", basis="owned"
    ).model_copy(update={"applies_to_hardware_id": "nvidia-h200-sxm-141gb"})
    others = [i for i in confirmed_items if i.category != "gpu_hardware"]
    items = [h200_price] + others
    tco = _tco(_plan(items), items)
    comp = next(c for c in tco.components if c.item_id == "gpu-h200")
    assert comp.determination == "not_applicable"
    assert comp.applicability_issue == "hardware_sku_mismatch"
    assert comp.one_time_cost is None          # 不得因为"能相乘"就算进去
    # 只剩机箱的 50,000 一次性投入；GPU 采购价被剔除
    assert tco.capex_one_time == Decimal("50000")
    assert any("hardware_sku_mismatch" in e for e in tco.applicability_errors)
    assert tco.incomplete is True


def test_f43_matching_sku_is_applicable(confirmed_items):
    """同 SKU 的价格正常参与计算（对照）。"""
    items = [
        i.model_copy(update={"applies_to_hardware_id": "nvidia-h100-sxm-80gb"})
        if i.category == "gpu_hardware"
        else i
        for i in confirmed_items
    ]
    tco = _tco(_plan(items), items)
    assert tco.applicability_errors == []
    assert tco.capex_one_time == Decimal("850000")   # GPU 800,000 + 机箱 50,000


def test_f43_expired_and_not_yet_effective_prices_are_blocked(confirmed_items):
    from datetime import date

    expired = [
        i.model_copy(update={"valid_to": date(2020, 1, 1)})
        if i.category == "gpu_hardware"
        else i
        for i in confirmed_items
    ]
    tco = _tco(_plan(expired), expired)
    comp = next(c for c in tco.components if c.category == "gpu_hardware")
    assert comp.applicability_issue == "price_expired"
    assert tco.incomplete is True

    future = [
        i.model_copy(update={"valid_from": date(2099, 1, 1)})
        if i.category == "gpu_hardware"
        else i
        for i in confirmed_items
    ]
    tco2 = _tco(_plan(future), future)
    comp2 = next(c for c in tco2.components if c.category == "gpu_hardware")
    assert comp2.applicability_issue == "price_not_yet_effective"
    assert tco2.incomplete is True


def test_f43_price_validity_is_judged_against_plan_as_of_date(confirmed_items):
    """基准日由方案给出：同一份目录在不同基准日下结论不同。"""
    from datetime import date

    items = [
        i.model_copy(update={"valid_to": date(2026, 6, 30)})
        if i.category == "gpu_hardware"
        else i
        for i in confirmed_items
    ]
    inside = _plan(items).model_copy(update={"as_of_date": date(2026, 1, 1)})
    outside = _plan(items).model_copy(update={"as_of_date": date(2026, 7, 1)})
    assert _tco(inside, items).applicability_errors == []
    assert _tco(outside, items).applicability_errors != []


def test_f43_region_conflict_and_unspecified_region(confirmed_items):
    items = [
        i.model_copy(update={"region": "cn-north"})
        if i.category == "gpu_hardware"
        else i
        for i in confirmed_items
    ]
    # 方案未声明区域 -> 无法断言区域价格适用
    unspecified = _tco(_plan(items), items)
    assert any("region_unspecified" in e for e in unspecified.applicability_errors)

    # 区域冲突
    conflict = _plan(items).model_copy(update={"region": "cn-south"})
    tco = _tco(conflict, items)
    comp = next(c for c in tco.components if c.category == "gpu_hardware")
    assert comp.applicability_issue == "region_mismatch"

    # 区域一致 -> 正常
    matched = _plan(items).model_copy(update={"region": "cn-north"})
    assert _tco(matched, items).applicability_errors == []


def test_f43_billing_rule_must_match_scenario_model():
    with pytest.raises(ValueError, match="不能用于情景的模型"):
        compute_revenue(_scenario(model_name="other-model"), _rule())


def test_f43_billing_rule_validity_window_is_enforced():
    from datetime import date

    with pytest.raises(ValueError, match="失效"):
        compute_revenue(
            _scenario(), _rule(valid_to=date(2020, 1, 1)), as_of=date(2026, 1, 1)
        )
    with pytest.raises(ValueError, match="才生效"):
        compute_revenue(
            _scenario(), _rule(valid_from=date(2099, 1, 1)), as_of=date(2026, 1, 1)
        )


# --------------------------------------------------------------------------
# V4.1 F4.4：失败请求计费与三段请求量
# --------------------------------------------------------------------------


def test_f44_demand_served_billable_are_separated():
    """成功率 0.9、合同规定失败不计费 -> 只按成功服务量计费。"""
    scenario = _scenario(periods=[DemandPeriod(
        label="p", duration_hours=Decimal("1"), request_count=1000,
        avg_input_tokens=Decimal("1000"), avg_output_tokens=Decimal("100"),
        success_rate=Decimal("0.9"),
    )])
    result = compute_revenue(scenario, _rule(billable_on_failure=False))
    p = result.periods[0]
    assert p.demand_requests == Decimal(1000)
    assert p.served_requests == Decimal(900)
    assert p.failed_requests == Decimal(100)
    assert p.billable_requests == Decimal(900)
    assert p.billable_output_tokens == Decimal(900) * 100
    assert any("period_has_failures" in w for w in result.warnings)


def test_f44_billable_on_failure_follows_contract():
    """合同规定失败也计费时，按需求请求量计费 —— 但这是**合同口径**，不是默认。"""
    scenario = _scenario(periods=[DemandPeriod(
        label="p", duration_hours=Decimal("1"), request_count=1000,
        avg_input_tokens=Decimal("1000"), avg_output_tokens=Decimal("100"),
        success_rate=Decimal("0.9"),
    )])
    not_billed = compute_revenue(scenario, _rule(billable_on_failure=False))
    billed = compute_revenue(scenario, _rule(billable_on_failure=True))
    assert billed.periods[0].billable_requests == Decimal(1000)
    assert billed.annual_revenue > not_billed.annual_revenue


def test_f44_success_rate_must_be_in_range():
    with pytest.raises(Exception, match="success_rate"):
        DemandPeriod(
            label="p", duration_hours=Decimal("1"), request_count=1,
            avg_input_tokens=Decimal("1"), avg_output_tokens=Decimal("1"),
            success_rate=Decimal("0"),
        )


def test_f44_different_cache_prices_compute_correctly():
    """缓存价与未缓存价不同单价下的精确计算（10% vs 90% 缓存占比）。"""
    period = DemandPeriod(
        label="p", duration_hours=Decimal("1"), request_count=1000,
        avg_input_tokens=Decimal("1000"), avg_output_tokens=Decimal("0"),
        cached_input_fraction=Decimal("0.1"),
    )
    rule = _rule(
        billable_cache_ratio=Decimal("0.9"),
        price_uncached_input_per_1m=Decimal("10"),
        price_cached_input_per_1m=Decimal("1"),
    )
    # 合同上限 0.9 > 业务估计 0.1 -> 按 0.1 计
    result = compute_revenue(_scenario(periods=[period]), rule)
    p = result.periods[0]
    assert p.billable_input_tokens_cached == Decimal("100000")     # 1e6 × 0.1
    assert p.billable_input_tokens_uncached == Decimal("900000")
    expected = Decimal("900000") / Decimal("1000000") * 10 + Decimal("100000") / Decimal("1000000") * 1
    assert p.revenue == expected


# --------------------------------------------------------------------------
# V4.1 F4.5：跨币种 / 完整性
# --------------------------------------------------------------------------


def _entry(candidate_id: str, **kw):
    base = dict(
        candidate_id=candidate_id,
        hardware_id="nvidia-h100-sxm-80gb",
        gpu_count=8,
        tp_size=8,
        dp_size=1,
        static_status="conditional_feasible",
        match_level="exact_match",
        sla_status="observed_pass",
        rank_bucket="verified_sla_met",
        output_throughput_tok_s=Decimal("1000"),
        ttft_p99_ms=Decimal("100"),
        tpot_p99_ms=Decimal("20"),
        evidence_run_ids=["run-1"],
    )
    base.update(kw)
    return _DummyRanked(**base)


def test_f45_usd_revenue_minus_cny_cost_is_not_a_margin(confirmed_items):
    """USD 收入 - CNY 成本 不得产出"利润"数字。"""
    tco = _tco(_plan(confirmed_items), confirmed_items)
    assert tco.currency == "CNY"

    revenue = compute_revenue(_scenario(), _rule(currency="USD"))
    assert revenue.currency == "USD"

    comparison = compare_deployments(
        requirement_id="r",
        ranking=_DummyRanking([_entry("cand-mech")]),
        economics=[CandidateEconomics("cand-mech", tco=tco, revenue=revenue)],
    )
    entry = comparison.entries[0]
    assert entry.annual_gross_margin is None
    assert entry.margin_ratio is None
    assert any("margin_not_evaluable" in n for n in entry.notes)
    assert any("currency_mismatch" in n for n in entry.notes)


def test_f45_incomplete_cost_hides_margin(confirmed_items):
    """成本不完整时不得继续展示"年度利润"。"""
    items = list(confirmed_items) + [
        _item("missing", "network", "per_server_year", None, status="pending_business_confirmation")
    ]
    tco = _tco(_plan(items), items)
    revenue = compute_revenue(_scenario(), _rule())
    comparison = compare_deployments(
        requirement_id="r",
        ranking=_DummyRanking([_entry("cand-mech")]),
        economics=[CandidateEconomics("cand-mech", tco=tco, revenue=revenue)],
    )
    entry = comparison.entries[0]
    assert entry.annual_gross_margin is None
    assert any("cost_incomplete" in n for n in entry.notes)


def test_f45_same_currency_still_yields_margin(confirmed_items):
    """同币种且成本完整时正常给出利润（对照，避免"一刀切不显示"）。"""
    tco = _tco(_plan(confirmed_items), confirmed_items)
    revenue = compute_revenue(_scenario(), _rule(currency=tco.currency))
    comparison = compare_deployments(
        requirement_id="r",
        ranking=_DummyRanking([_entry("cand-mech")]),
        economics=[CandidateEconomics("cand-mech", tco=tco, revenue=revenue)],
    )
    entry = comparison.entries[0]
    assert entry.annual_gross_margin is not None
    assert entry.annual_gross_margin == revenue.annual_revenue - tco.annual_total_cost


def test_f45_breakeven_ratio_blocked_on_currency_mismatch(confirmed_items):
    tco = _tco(_plan(confirmed_items), confirmed_items)
    revenue = compute_revenue(_scenario(), _rule(currency="USD"))
    be = analyze_breakeven(tco=tco, revenue=revenue, rule=_rule(currency="USD"))
    assert be.revenue_cost_ratio is None
    assert any("currency_mismatch" in r for r in be.reasons)


def test_f45_budget_blocked_on_currency_mismatch():
    result = check_budget(
        budget_id="b",
        budget_amount=Decimal("100"),
        budget_kind="total_horizon",
        compared_value=Decimal("50"),
        currency="CNY",
        currency_confirmed=True,
        compared_currency="USD",
    )
    assert result.status == "unknown"
    assert any("currency_mismatch" in r for r in result.reasons)


# --------------------------------------------------------------------------
# V4.1 F4.6：综合容量证据（命名用例）
# --------------------------------------------------------------------------


def test_f46_output_tps_alone_never_yields_within_capacity():
    ev = CapacityEvidence(
        run_id="run-out-only",
        is_verified=True,
        output_throughput_tok_s=Decimal("1000000"),
        match_level="exact_match",
        data_trust="measured_internal",
    )
    result = compute_revenue(_scenario(), _rule(), capacity_evidence=ev)
    assert result.capacity_check != "within_capacity"
    assert result.capacity_check == "partially_verified"
    assert result.scenario_only is True
    assert any("部分子项无法判定" in r for r in result.capacity_detail.reasons)


def test_f46_partially_verified_cannot_be_reported_as_feasible():
    """partial 结论不得被写成"容量可行"。"""
    detail_reasons = " ".join(
        CapacityEvidence(
            run_id="r", is_verified=True, output_throughput_tok_s=Decimal("1e9"),
            match_level="exact_match", data_trust="measured_internal",
        ).run_id
        for _ in range(1)
    )
    result = compute_revenue(
        _scenario(),
        _rule(),
        capacity_evidence=CapacityEvidence(
            run_id="r", is_verified=True, output_throughput_tok_s=Decimal("1000000"),
            match_level="exact_match", data_trust="measured_internal",
        ),
    )
    assert result.capacity_check == "partially_verified"
    assert any("不得当作容量可行" in w for w in result.warnings)
