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
from llm_capacity_pricing.engines.quote_gate import evaluate_quote_eligibility
from llm_capacity_pricing.engines.revenue import CapacityEvidence, compute_revenue
from llm_capacity_pricing.engines.tco import build_cash_flow, compute_tco
from llm_capacity_pricing.reporting.tco_report import build_tco_report
from llm_capacity_pricing.schemas.commercial import (
    BillingRule,
    CommercialConfirmation,
    DemandPeriod,
    RevenueScenario,
)
from llm_capacity_pricing.schemas.tco import (
    CostCatalogItem,
    CostLineSelection,
    DeploymentCostPlan,
    FinancingAssumptions,
)
from llm_capacity_pricing.services import (
    ServiceError,
    build_cost_plan_from_template,
    capacity_evidence_for,
    cost_catalog_summary,
    import_cost_catalog,
    import_commercial_confirmations,
    init_db,
    load_cost_plan_templates,
    phase4_decision_for,
    quote_eligibility_for,
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
    tco = compute_tco(_plan(confirmed_items), confirmed_items)

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
    tco = compute_tco(_plan(confirmed_items), confirmed_items)
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
    leased = compute_tco(_plan(items, basis="leased"), items)
    assert leased.capex_one_time is None
    assert leased.annual_depreciation is None
    assert leased.annual_cash_opex == Decimal("30000") * 8 + Decimal("2000")
    excluded = [c.item_id for c in leased.components if c.determination == "excluded_by_cost_basis"]
    assert excluded == ["gpu-buy"]
    # 被排除的项不得带金额（排除 ≠ 零成本）
    excluded_component = next(c for c in leased.components if c.item_id == "gpu-buy")
    assert excluded_component.annual_cost is None
    assert excluded_component.one_time_cost is None

    owned = compute_tco(_plan(items, basis="owned"), items)
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
    tco = compute_tco(_plan(items), items)
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
    tco = compute_tco(plan, confirmed_items)
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
    tco = compute_tco(_plan(items), items)
    assert tco.currency is None
    assert tco.incomplete is True
    assert any("mixed_currencies" in r for r in tco.completeness_reasons)
    assert tco.currency_confirmed is False


def test_m28_synthetic_prices_make_tco_incomplete(confirmed_items):
    items = list(confirmed_items)
    items[0] = items[0].model_copy(
        update={"status": "synthetic_example", "currency_status": "unconfirmed"}
    )
    tco = compute_tco(_plan(items), items)
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
    assert result.scenario_only is True
    assert any("容量未验证" in w for w in result.warnings)


def test_m29_capacity_check_with_verified_evidence():
    ev = CapacityEvidence(
        run_id="run-1",
        is_verified=True,
        configured_concurrency=8,
        output_throughput_tok_s=Decimal("2000"),
        match_level="exact_match",
        data_trust="measured_internal",
    )
    # 峰值需求 = 6.9444 req/s × 256 = 1777.78 tok/s ≤ 2000 -> within
    ok = compute_revenue(_scenario(), _rule(), capacity_evidence=ev)
    assert ok.capacity_check == "within_capacity"
    assert ok.scenario_only is False
    assert ok.capacity_basis

    tight = CapacityEvidence(
        run_id="run-2",
        is_verified=True,
        configured_concurrency=8,
        output_throughput_tok_s=Decimal("100"),
        match_level="exact_match",
        data_trust="measured_internal",
    )
    over = compute_revenue(_scenario(), _rule(), capacity_evidence=tight)
    assert over.capacity_check == "exceeds_capacity"
    assert over.scenario_only is True


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


def test_m29_cache_fraction_takes_conservative_minimum():
    """实际计入缓存价的占比 = min(业务估计, 合同口径)，只会更保守。"""
    # 业务估计 0.6，合同只认 0.2 -> 应按 0.2 计
    result = compute_revenue(
        _scenario(periods=[DemandPeriod(
            label="p", duration_hours=Decimal("1"), request_count=1000,
            avg_input_tokens=Decimal("1000"), avg_output_tokens=Decimal("0"),
            cached_input_fraction=Decimal("0.6"),
        )]),
        _rule(billable_cache_ratio=Decimal("0.2")),
    )
    cached = result.periods[0].billable_input_tokens_cached
    assert cached == Decimal(1000) * 1000 * Decimal("0.2")


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


def test_m30_quote_requires_all_four_dimensions(confirmed_items):
    tco = compute_tco(_plan(confirmed_items), confirmed_items)
    full = evaluate_quote_eligibility(
        candidate_id="c",
        tco=tco,
        sla_status="observed_pass",
        match_level="exact_match",
        confirmation=_confirmation(),
        approval_status="approved",
        approval_ref="APR-001",
    )
    assert full.quote_publishable is True
    assert full.blockers == []


@pytest.mark.parametrize(
    "kwargs,expect_kwarg",
    [
        (dict(sla_status="estimated_only", match_level="exact_match"), "sla_estimated_only"),
        (dict(sla_status="not_evaluable", match_level=None), "sla_not_verified"),
        (dict(sla_status=None, match_level=None), "sla_not_evaluated"),
        (dict(sla_status="observed_pass", match_level=None), "sla_pass_without_exact_match"),
        (dict(sla_status="observed_pass", match_level="comparable_reference"), "sla_pass_without_exact_match"),
    ],
)
def test_m30_sla_dimension_is_strict(confirmed_items, kwargs, expect_kwarg):
    """SLA 维度：只有 observed_pass + exact_match 才算 verified_measured。"""
    tco = compute_tco(_plan(confirmed_items), confirmed_items)
    result = evaluate_quote_eligibility(
        candidate_id="c",
        tco=tco,
        confirmation=_confirmation(),
        approval_status="approved",
        approval_ref="APR-001",
        **kwargs,
    )
    assert result.quote_publishable is False
    assert result.sla_verification_status != "verified_measured"
    assert any(expect_kwarg in b for b in result.blockers), result.blockers


def test_m30_incomplete_cost_blocks_quote(confirmed_items):
    items = list(confirmed_items) + [
        _item("missing", "network", "per_server_year", None, status="pending_business_confirmation")
    ]
    tco = compute_tco(_plan(items), items)
    result = evaluate_quote_eligibility(
        candidate_id="c",
        tco=tco,
        sla_status="observed_pass",
        match_level="exact_match",
        confirmation=_confirmation(),
        approval_status="approved",
        approval_ref="APR-001",
    )
    assert result.cost_estimate_status == "incomplete"
    assert result.quote_publishable is False
    assert any("cost_unknown_items" in b for b in result.blockers)


def test_m30_synthetic_price_blocks_quote_even_when_otherwise_complete(confirmed_items):
    """即便四维看起来齐备，只要成本来自合成示例就不得发布。"""
    items = [
        i.model_copy(update={"status": "synthetic_example", "currency_status": "unconfirmed"})
        for i in confirmed_items
    ]
    tco = compute_tco(_plan(items), items)
    result = evaluate_quote_eligibility(
        candidate_id="c",
        tco=tco,
        sla_status="observed_pass",
        match_level="exact_match",
        confirmation=_confirmation(),
        approval_status="approved",
        approval_ref="APR-001",
    )
    assert result.quote_publishable is False
    assert any("synthetic" in b for b in result.blockers)


def test_m30_missing_confirmation_and_approval_block(confirmed_items):
    tco = compute_tco(_plan(confirmed_items), confirmed_items)
    result = evaluate_quote_eligibility(
        candidate_id="c",
        tco=tco,
        sla_status="observed_pass",
        match_level="exact_match",
        confirmation=None,
        approval_status="missing",
    )
    assert result.quote_publishable is False
    assert result.commercial_confirmation_status == "missing"
    assert any("commercial_confirmation_missing" in b for b in result.blockers)
    assert any("approval_missing" in b for b in result.blockers)


def test_m30_approved_without_reference_is_not_approved(confirmed_items):
    tco = compute_tco(_plan(confirmed_items), confirmed_items)
    result = evaluate_quote_eligibility(
        candidate_id="c",
        tco=tco,
        sla_status="observed_pass",
        match_level="exact_match",
        confirmation=_confirmation(),
        approval_status="approved",
        approval_ref=None,
    )
    assert result.quote_publishable is False
    # 无凭据的"已审批"不作为有效审批：状态降级为 missing 并写明原因
    assert result.approval_status == "missing"
    assert any("approval_ref_missing" in b for b in result.blockers)


def test_m30_expired_confirmation_blocks(confirmed_items):
    tco = compute_tco(_plan(confirmed_items), confirmed_items)
    result = evaluate_quote_eligibility(
        candidate_id="c",
        tco=tco,
        sla_status="observed_pass",
        match_level="exact_match",
        confirmation=_confirmation(valid_until="2020-01-01"),
        approval_status="approved",
        approval_ref="APR-001",
    )
    assert result.quote_publishable is False
    assert any("expired" in b for b in result.blockers)


def test_m30_quote_eligibility_cannot_be_forged():
    """schema 层禁止"未通过却标记可发布"。"""
    from llm_capacity_pricing.schemas.commercial import QuoteEligibility

    with pytest.raises(Exception, match="quote_publishable"):
        QuoteEligibility(
            candidate_id="c",
            cost_estimate_status="incomplete",
            sla_verification_status="verified_measured",
            commercial_confirmation_status="confirmed",
            approval_status="approved",
            approval_ref="A",
            quote_publishable=True,
        )
    with pytest.raises(Exception, match="approval_ref"):
        QuoteEligibility(
            candidate_id="c",
            cost_estimate_status="complete",
            sla_verification_status="verified_measured",
            commercial_confirmation_status="confirmed",
            approval_status="approved",
        )


def test_m30_service_requires_approval_ref(tmp_path):
    db = str(tmp_path / "m30.db")
    init_db(db)
    import_cost_catalog(CATALOG_EXAMPLE, db)
    with pytest.raises(ServiceError, match="approval-ref"):
        quote_eligibility_for(
            db,
            candidate_id="c",
            approval_status="approved",
            approval_ref=None,
        )


# --------------------------------------------------------------------------
# M31/M32：比较、预算、敏感性、盈亏平衡
# --------------------------------------------------------------------------


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
    owned_tco = compute_tco(_plan(confirmed_items, basis="owned"), confirmed_items)
    leased_items = [
        _item("lease", "gpu_hardware", "per_gpu_year", "30000", basis="leased"),
        _item("ops", "operations", "per_server_year", "2000"),
    ]
    leased_tco = compute_tco(_plan(leased_items, basis="leased"), leased_items)
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
    tco = compute_tco(_plan(items), items)
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
    tco = compute_tco(_plan(confirmed_items), confirmed_items)
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
    tco = compute_tco(_plan(confirmed_items), confirmed_items)
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
    tco = compute_tco(_plan(items), items)
    eligibility = evaluate_quote_eligibility(
        candidate_id="c",
        tco=tco,
        sla_status="not_evaluable",
        match_level=None,
        confirmation=None,
        approval_status="missing",
    )
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
    # 阻断时不得输出"报价金额"小节
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


def test_m33_agent_api_does_not_default_to_approved(tmp_path):
    """Agent 调用门禁时必须显式给出审批状态；默认 missing，不会自动通过。"""
    db = str(tmp_path / "agent2.db")
    init_db(db)
    import_cost_catalog(CATALOG_EXAMPLE, db)
    out = call_agent_op("phase4.quote_eligibility", db=db, candidate_id="c")
    assert out["eligibility"]["approval_status"] == "missing"
    assert out["eligibility"]["quote_publishable"] is False


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
