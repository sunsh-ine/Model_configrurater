"""M35：统一报告中的成本推荐可比性（P0 修正）。

复审发现：`workflow._recommendations()` 原先直接从"成本完整"的候选中取最小金额，
既没核对币种、买断/租赁口径与计算周期，也没排除全周期金额为 None 的情况 ——
`x or Decimal(0)` 会把**缺失**当成最便宜。

本文件用反向用例把它锁死。
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from llm_capacity_pricing.decision.comparison import (
    CostProfile,
    cost_comparability,
)
from llm_capacity_pricing.reporting.plan_report import build_plan_report
from llm_capacity_pricing.schemas.plan import PlanCandidate
from llm_capacity_pricing.workflow import _cost_recommendations


def _candidate(
    candidate_id: str,
    *,
    total: str | None,
    currency: str | None = "CNY",
    basis: str | None = "owned",
    horizon: int | None = 3,
    incomplete: bool = False,
) -> PlanCandidate:
    return PlanCandidate(
        candidate_id=candidate_id,
        hardware_id="nvidia-h100-sxm-80gb",
        tp_size=4,
        dp_size=1,
        gpu_count=4,
        static_status="conditional_feasible",
        economics_status="computed",
        plan_id=f"plan-{candidate_id}",
        cost_basis=basis,
        currency=currency,
        horizon_years=horizon,
        cost_incomplete=incomplete,
        total_cash_over_horizon=Decimal(total) if total is not None else None,
    )


# --------------------------------------------------------------------------
# 规则层：唯一权威判据
# --------------------------------------------------------------------------


def test_m35_currency_mismatch_is_not_comparable():
    cny = CostProfile("CNY", "owned", 3, False, Decimal("1000000"))
    usd = CostProfile("USD", "owned", 3, False, Decimal("900000"))
    kind, notes = cost_comparability(usd, cny)
    assert kind == "not_comparable_currency"
    assert any("币种不一致" in n for n in notes)
    assert cost_comparability(cny, cny)[0] == "comparable"


def test_m35_basis_mismatch_is_not_comparable():
    owned = CostProfile("CNY", "owned", 3, False, Decimal("1000000"))
    leased = CostProfile("CNY", "leased", 3, False, Decimal("300000"))
    kind, notes = cost_comparability(leased, owned)
    assert kind == "not_comparable_basis"
    assert any("买断" in n or "口径" in n for n in notes)


def test_m35_horizon_mismatch_is_not_comparable():
    three = CostProfile("CNY", "owned", 3, False, Decimal("1000000"))
    five = CostProfile("CNY", "owned", 5, False, Decimal("1500000"))
    kind, notes = cost_comparability(five, three)
    assert kind == "not_comparable_horizon"
    assert any("周期不一致" in n for n in notes)


def test_m35_missing_total_is_not_zero():
    """全周期金额缺失不得按 0 参与排序（这会把它推成"最便宜"）。"""
    has_amount = CostProfile("CNY", "owned", 3, False, Decimal("500000"))
    missing = CostProfile("CNY", "owned", 3, False, None)
    kind, notes = cost_comparability(missing, has_amount)
    assert kind == "incomplete_cost"
    assert any("不得按 0" in n for n in notes)


def test_m35_no_reference_means_no_conclusion():
    """没有参照方案时不给出成本高低结论（而不是默认第一个可比）。"""
    only = CostProfile("CNY", "owned", 3, False, Decimal("100"))
    kind, notes = cost_comparability(only, None)
    assert kind == "incomplete_cost"
    assert any("参照方案" in n for n in notes)


# --------------------------------------------------------------------------
# 推荐层：反向场景
# --------------------------------------------------------------------------


def test_m35_usd_candidate_cannot_win_lowest_cost():
    """**复审指定场景**：100 万 CNY 与 90 万 USD 不得比出"USD 更低"。"""
    cny = _candidate("cheap-cny", total="1000000", currency="CNY")
    usd = _candidate("usd-900k", total="900000", currency="USD")
    lines = _cost_recommendations([cny, usd])
    joined = " ".join(lines)
    # USD 候选只能出现在"不可比原因"里，绝不能出现在"成本最低"结论里
    assert "最低：`usd-900k`" not in joined
    assert "not_comparable_currency" in joined
    # 没有 2 个可比候选 -> 不给最低成本结论
    assert "不给出最低成本候选" in joined


def test_m35_leased_candidate_cannot_win_lowest_cost():
    owned = _candidate("owned", total="1000000", basis="owned")
    leased = _candidate("leased", total="300000", basis="leased")
    joined = " ".join(_cost_recommendations([owned, leased]))
    assert "最低：`leased`" not in joined
    assert "not_comparable_basis" in joined


def test_m35_different_horizon_cannot_win_lowest_cost():
    three = _candidate("h3", total="1000000", horizon=3)
    five = _candidate("h5", total="900000", horizon=5)
    joined = " ".join(_cost_recommendations([three, five]))
    assert "not_comparable_horizon" in joined
    assert "最低：`h5`" not in joined


def test_m35_missing_total_cannot_win_lowest_cost():
    """金额缺失的候选绝不能被选为"成本最低"。"""
    real = _candidate("real", total="1000000")
    ghost = _candidate("ghost", total=None)
    lines = _cost_recommendations([ghost, real])
    joined = " ".join(lines)
    assert "最低：`ghost`" not in joined
    # 参照方案必须是金额已知的那个
    assert "`real`" in joined


def test_m35_incomplete_cost_is_excluded_from_ranking():
    complete = _candidate("complete", total="1000000")
    incomplete = _candidate("incomplete", total="800000", incomplete=True)
    joined = " ".join(_cost_recommendations([complete, incomplete]))
    assert "incomplete_cost" in joined


def test_m35_comparable_candidates_still_get_a_conclusion():
    """对照：同币种/同口径/同周期且金额完整时，正常给出最低成本候选。"""
    a = _candidate("a", total="1000000")
    b = _candidate("b", total="800000")
    lines = _cost_recommendations([a, b])
    joined = " ".join(lines)
    assert "最低：`b`" in joined
    assert "同为 CNY / owned / 3 年" in joined


def test_m35_no_cost_at_all_yields_no_conclusion():
    lines = _cost_recommendations([])
    assert lines and "不给出成本高低结论" in lines[0]


# --------------------------------------------------------------------------
# 报告层：技术资格 vs 生产发布授权
# --------------------------------------------------------------------------


def test_m35_report_separates_technical_eligibility_from_production_authorization(
    tmp_path, root
):
    from llm_capacity_pricing.schemas.plan import (
        PlanAdvanced,
        PlanResourceAssumptions,
        PlanRequest,
        PlanWorkload,
    )
    from llm_capacity_pricing.services import import_hardware, import_models, init_db
    from llm_capacity_pricing.workflow import evaluate_plan

    db = str(tmp_path / "m35.db")
    init_db(db)
    import_models(root / "data/models/model_manifest.yaml", db, root=root)
    import_hardware(root / "data/hardware/hardware_catalog.yaml", db)

    result = evaluate_plan(
        PlanRequest(
            request_id="m35",
            model_id="qwen3-32b",
            hardware_ids=["nvidia-h100-sxm-80gb"],
            server_id="hgx-h100-8gpu",
            workload=PlanWorkload(input_len=8192, output_len=256, concurrency=32),
            resources=PlanResourceAssumptions(
                usable_fraction=Decimal("0.9"),
                usable_source_kind="synthetic_example",
                runtime_reserve_fraction=Decimal("0.1"),
            ),
            advanced=PlanAdvanced(tp_sizes=[4], dp_sizes=[1]),
        ),
        db_path=db,
    )
    md = build_plan_report(result)
    assert "**技术资格（四维门禁）**" in md
    assert "**生产发布授权**：**未授权**" in md
    assert "不得用于生产环境的报价自动发布" in md
    assert "人工在受控流程中签发" in md
