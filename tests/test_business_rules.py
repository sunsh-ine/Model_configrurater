"""M3 / M6：商业边界与失败行为。

任务书 §9「商业边界」「失败行为」「年度时间」与 §4 公式约束对应本文件。
"""

from __future__ import annotations

import json
from decimal import Decimal

import pytest
from typer.testing import CliRunner

from llm_capacity_pricing.cli import app
from llm_capacity_pricing.engines.calculator import (
    BOUNDARY_WARNINGS,
    calculate,
    make_legacy_scenario,
    make_scenario,
)
from llm_capacity_pricing.engines.pricing import (
    InvalidUtilizationError,
    NotComputableError,
    derived_billable_ratio,
    revenue_cost_ratio,
    validate_utilization,
    breakeven_monetization_ratio as be,
)
from llm_capacity_pricing.reporting.json_export import dumps_exact, loads_tolerant
from llm_capacity_pricing.schemas.benchmark import BenchmarkRecord
from llm_capacity_pricing.schemas.costs import HardwarePrice, TokenPrice
from llm_capacity_pricing.services import (
    calculate_all,
    build_sensitivity,
    export_legacy_results,
    export_report,
    open_db,
)

#: 绝不允许出现在任何输出中的"越界承诺"措辞
FORBIDDEN_CLAIM_TOKENS = (
    "guaranteed_sla",
    "sla_guarantee",
    "official_quote",
    "total_cost_of_ownership",
)


def _rec(**kw) -> BenchmarkRecord:
    base = dict(
        record_id="r",
        model_name="m",
        hardware_model="g",
        deployment_gpu_count=8,
        evidence_id="e",
        performance_source_kind="internal_excel",
        input_tps_per_gpu=Decimal("1000"),
        output_tps_per_gpu=Decimal("50"),
        cache_hit_rate_raw=Decimal("0.5"),
    )
    base.update(kw)
    return BenchmarkRecord(**base)


def _tp(**kw) -> TokenPrice:
    base = dict(
        token_price_id="t",
        model_name="m",
        price_cached_input_per_1m=Decimal("2"),
        price_uncached_input_per_1m=Decimal("20"),
        price_output_per_1m=Decimal("100"),
        currency=None,
        currency_assumption="CNY; pending business confirmation",
        evidence_id="e",
    )
    base.update(kw)
    return TokenPrice(**base)


def _hp(**kw) -> HardwarePrice:
    base = dict(
        hardware_price_id="h",
        gpu_model="g",
        per_gpu_annual_cost_10k=Decimal("52.5"),
        currency=None,
        currency_assumption="CNY; pending business confirmation",
        evidence_id="e",
    )
    base.update(kw)
    return HardwarePrice(**base)


# --------------------------------------------------------------------------
# 变现比例 U 的边界
# --------------------------------------------------------------------------


def test_utilization_validation():
    assert validate_utilization(Decimal("0")) == 0
    assert validate_utilization(Decimal("1")) == 1
    assert validate_utilization(Decimal("0.30")) == Decimal("0.30")
    for bad in ("-0.01", "1.01", "2", "-1"):
        with pytest.raises(InvalidUtilizationError):
            validate_utilization(Decimal(bad))


def test_scenario_requires_utilization():
    with pytest.raises(InvalidUtilizationError):
        make_scenario("r", Decimal("1.5"))


def test_utilization_zero_gives_zero_revenue():
    res = calculate(_rec(), _tp(), _hp(), make_scenario("r", Decimal("0")))
    assert res.status == "ok"
    assert res.scenario_revenue_10k_per_gpu_year == Decimal("0")
    assert res.scenario_surplus_before_other_costs_10k_per_gpu_year == Decimal("-52.5")
    assert res.total_revenue_10k_per_gpu_year is not None, "满载收入仍应保留"


def test_utilization_one_equals_full_load():
    full = calculate(_rec(), _tp(), _hp(), make_legacy_scenario("r"))
    scen = calculate(_rec(), _tp(), _hp(), make_scenario("r", Decimal("1")))
    assert scen.scenario_revenue_10k_per_gpu_year == full.total_revenue_10k_per_gpu_year


def test_utilization_scales_linearly():
    half = calculate(_rec(), _tp(), _hp(), make_scenario("r", Decimal("0.5")))
    full = calculate(_rec(), _tp(), _hp(), make_legacy_scenario("r"))
    assert half.scenario_revenue_10k_per_gpu_year * 2 == full.total_revenue_10k_per_gpu_year


def test_scenario_marked_as_sensitivity_only():
    res = calculate(_rec(), _tp(), _hp(), make_scenario("r", Decimal("0.3")))
    assert any("scenario_is_sensitivity_only" in w for w in res.warnings)


def test_availability_and_billable_not_double_multiplied():
    """A/S 若启用，必须与 U 相乘一次；重复相乘会被显式警告。"""
    ratio, warnings = derived_billable_ratio(
        utilization=Decimal("0.5"), availability=Decimal("0.8"), billable=Decimal("0.5")
    )
    assert ratio == Decimal("0.5") * Decimal("0.8") * Decimal("0.5")
    assert sum("不得重复相乘" in w for w in warnings) == 2


# --------------------------------------------------------------------------
# 除零 / 零成本 / 零收入
# --------------------------------------------------------------------------


def test_zero_cost_ratio_is_named_error_not_inf():
    with pytest.raises(NotComputableError):
        revenue_cost_ratio(Decimal("100"), Decimal("0"))
    with pytest.raises(NotComputableError):
        be(Decimal("0"), Decimal("0"))


def test_zero_cost_record_is_not_evaluable():
    res = calculate(
        _rec(), _tp(), _hp(per_gpu_annual_cost_10k=Decimal("0")), make_legacy_scenario("r")
    )
    assert res.status == "not_evaluable"
    assert res.revenue_cost_ratio is None
    assert "不可计算" in res.not_evaluable_reason


def test_zero_revenue_record_has_no_breakeven():
    res = calculate(
        _rec(),
        _tp(
            price_cached_input_per_1m=Decimal("0"),
            price_uncached_input_per_1m=Decimal("0"),
            price_output_per_1m=Decimal("0"),
        ),
        _hp(),
        make_legacy_scenario("r"),
    )
    assert res.total_revenue_10k_per_gpu_year == Decimal("0")
    assert res.breakeven_monetization_ratio is None
    assert any("breakeven_not_evaluable" in w for w in res.warnings)


def test_breakeven_above_one_is_flagged():
    """成本高于满载收入时必须给出明确提示。"""
    res = calculate(
        _rec(input_tps_per_gpu=Decimal("1"), output_tps_per_gpu=Decimal("1")),
        _tp(),
        _hp(),
        make_legacy_scenario("r"),
    )
    assert res.breakeven_monetization_ratio > 1
    assert any("breakeven_utilization_above_1" in w for w in res.warnings)


# --------------------------------------------------------------------------
# 币种 / 成本边界命名 / 无越界承诺
# --------------------------------------------------------------------------


def test_unconfirmed_currency_is_warned_not_assumed():
    res = calculate(_rec(), _tp(), _hp(), make_legacy_scenario("r"))
    # 币种不得默认成已确认的人民币：使用显式占位符
    assert res.currency == "UNCONFIRMED"
    assert res.currency_assumption == "CNY; pending business confirmation"
    assert any("currency_unconfirmed" in w for w in res.warnings)
    assert any("hardware_currency_unconfirmed" in w for w in res.warnings)


def test_result_carries_explicit_cost_scope_and_result_type():
    """任务 2/3：币种、成本范围、结果类型都必须显式。"""
    full = calculate(_rec(), _tp(), _hp(), make_legacy_scenario("r"))
    scen = calculate(_rec(), _tp(), _hp(), make_scenario("r", Decimal("0.3")))
    assert full.cost_scope == "gpu_cost_only"
    assert full.result_type == "theoretical_full_load_potential"
    assert scen.result_type == "scenario_sensitivity_estimate"
    for res in (full, scen):
        assert res.price_unit == "currency_unit_per_1M_tokens"
        assert res.billing_period == "per_gpu_year"
        assert res.currency == "UNCONFIRMED"


def test_cost_scope_is_gpu_cost_only():
    res = calculate(_rec(), _tp(), _hp(), make_legacy_scenario("r"))
    assert res.cost_scope == "gpu_cost_only"
    assert res.price_unit == "currency_unit_per_1M_tokens"
    assert res.billing_period == "per_gpu_year"


def test_boundary_warnings_always_present():
    res = calculate(_rec(), _tp(), _hp(), make_legacy_scenario("r"))
    for w in BOUNDARY_WARNINGS:
        assert w in res.warnings


def test_no_forbidden_claim_tokens_in_engine_output():
    res = calculate(_rec(), _tp(), _hp(), make_scenario("r", Decimal("0.3")))
    blob = dumps_exact(res.model_dump(mode="python"))
    for token in FORBIDDEN_CLAIM_TOKENS:
        assert token not in blob


def test_report_has_no_forbidden_claims_and_states_boundaries(full_db, tmp_path):
    out = tmp_path / "overview.md"
    export_report(full_db, out)
    text = out.read_text(encoding="utf-8")
    for token in FORBIDDEN_CLAIM_TOKENS:
        assert token not in text
    assert "不是对外报价" in text
    assert "gpu_cost_only" in text
    for section in (
        "## 1. 数据概览",
        "## 2. 可用场景数",
        "## 3. 数据缺口",
        "## 4. 最大理论满载收入案例",
        "## 5. 成本敏感性",
        "## 6. 逐条来源说明",
        "## 7. 边界与免责声明",
    ):
        assert section in text, section


def test_export_has_boundary_notices(full_db, tmp_path):
    # 默认（业务）口径：排除 synthetic_fixture -> 32 条
    info = export_legacy_results(full_db, tmp_path / "legacy.json")
    payload = json.loads((tmp_path / "legacy.json").read_text(encoding="utf-8"))
    assert payload["result_count"] == 32
    assert payload["meta"]["include_test_data"] is False
    assert payload["meta"]["business_record_count"] == 32
    assert payload["meta"]["test_record_count"] == 0
    assert len(payload["meta"]["excluded_test_records"]) == 4
    assert any("不是对外报价" in n for n in payload["boundary_notices"])
    assert payload["meta"]["cost_scope"] == "gpu_cost_only"
    assert info["result_count"] == 32

    # 显式测试口径 -> 36 条
    info36 = export_legacy_results(
        full_db, tmp_path / "legacy36.json", include_test_data=True
    )
    payload36 = json.loads((tmp_path / "legacy36.json").read_text(encoding="utf-8"))
    assert payload36["result_count"] == 36
    assert info36["meta"]["include_test_data"] is True
    assert info36["meta"]["test_record_count"] == 4


# --------------------------------------------------------------------------
# JSON 金额精度
# --------------------------------------------------------------------------


def test_json_export_uses_exactly_recoverable_decimal_strings():
    """任务 2：金额以十进制字符串导出，可逐位精确恢复。"""
    payload = {
        "input_revenue_10k_per_gpu_year": Decimal("123.45678901234567"),
        "total_revenue_10k_per_gpu_year": [Decimal("0.30"), Decimal("0")],
    }
    text = dumps_exact(payload)
    assert '"123.45678901234567"' in text
    assert '"0.30"' in text
    assert json.loads(text) == {
        "input_revenue_10k_per_gpu_year": "123.45678901234567",
        "total_revenue_10k_per_gpu_year": ["0.30", "0"],
    }
    # 读回（宽容读取）后与原 Decimal 逐位一致
    back = loads_tolerant(text)
    assert back["input_revenue_10k_per_gpu_year"] == Decimal("123.45678901234567")
    assert str(back["input_revenue_10k_per_gpu_year"]) == "123.45678901234567"
    assert back["total_revenue_10k_per_gpu_year"][0] == Decimal("0.30")


def test_json_export_survives_roundtrip_exactly():
    val = Decimal("234.56789012345678")
    text = dumps_exact({"v": val})
    assert Decimal(loads_tolerant(text)["v"]) == val
    assert str(loads_tolerant(text)["v"]) == str(val)


# --------------------------------------------------------------------------
# 情景批量与持久化
# --------------------------------------------------------------------------


def test_sensitivity_levels_and_persistence(excel_db):
    results = build_sensitivity(excel_db, levels=[Decimal("0.1"), Decimal("0.3")])
    assert len(results) == 32 * 2
    assert all(r.scope == "business" for r in results)
    db, repo = open_db(excel_db)
    try:
        with db.session() as session:
            persisted = {(r.benchmark_record_id, str(r.effective_monetization_ratio))
                         for r in repo.list_pricing_results(session)}
    finally:
        db.dispose()
    assert len(persisted) == 64


# --------------------------------------------------------------------------
# CLI 失败行为（任务书 §9）
# --------------------------------------------------------------------------


runner = CliRunner()


def test_cli_rejects_invalid_utilization(excel_db):
    for bad in ("1.5", "-0.2", "abc"):
        r = runner.invoke(
            app,
            ["scenario", "--record", "excel_部署效能_2", "--utilization", bad, "--db", excel_db],
        )
        assert r.exit_code != 0, bad
        assert "错误" in r.output or "错误" in (r.stderr or "")


def test_cli_rejects_missing_excel(tmp_path):
    r = runner.invoke(
        app, ["import-excel", "--file", str(tmp_path / "nope.xlsx"), "--db", str(tmp_path / "d.db")]
    )
    assert r.exit_code != 0


def test_cli_rejects_unknown_record(excel_db):
    r = runner.invoke(
        app,
        ["scenario", "--record", "no_such_record", "--utilization", "0.5", "--db", excel_db],
    )
    assert r.exit_code != 0
    assert "找不到 record_id" in r.output


def test_cli_rejects_bad_metadata(tmp_path, root):
    r = runner.invoke(
        app,
        [
            "import-vllm",
            "--file",
            str(root / "data" / "fixtures" / "vllm_synthetic.json"),
            "--metadata",
            str(tmp_path / "missing.yaml"),
            "--db",
            str(tmp_path / "d.db"),
        ],
    )
    assert r.exit_code != 0


def test_cli_happy_path_exit_zero(tmp_path, excel_path):
    db = tmp_path / "cli.db"
    for args in (
        ["init-db", "--db", str(db)],
        ["import-excel", "--file", str(excel_path), "--db", str(db)],
        ["validate-import", "--db", str(db)],
        ["calculate-all", "--mode", "legacy_full_load", "--db", str(db), "--out", str(tmp_path / "l.json")],
        ["scenario", "--record", "excel_部署效能_2", "--utilization", "0.30", "--db", str(db), "--out", str(tmp_path / "s.json")],
        ["report", "--db", str(db), "--out", str(tmp_path / "o.md")],
    ):
        r = runner.invoke(app, args)
        assert r.exit_code == 0, (args, r.output, r.stderr)
    assert (tmp_path / "l.json").exists()
    assert (tmp_path / "s.json").exists()
    assert (tmp_path / "o.md").exists()


def test_cli_scenario_output_content(excel_db, tmp_path):
    out = tmp_path / "s.json"
    r = runner.invoke(
        app,
        ["scenario", "--record", "excel_部署效能_2", "--utilization", "0.30",
         "--db", excel_db, "--out", str(out)],
    )
    assert r.exit_code == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    res = payload["results"][0]
    # 金额为十进制字符串；按 Decimal 精确比较（不受 float 影响）。
    # 期望值**从数据库里的满载结果推导**（该记录 Excel 第 2 行的真实金额属内部信息，
    # 不写成仓库内的字面量，见 V1.2 F1）。
    legacy_results, _meta = calculate_all(excel_db, mode="legacy_full_load", persist=True)
    legacy = next(
        r for r in legacy_results if r.benchmark_record_id == res["benchmark_record_id"]
    )
    assert Decimal(res["scenario_revenue_10k_per_gpu_year"]) == (
        Decimal("0.30") * legacy.total_revenue_10k_per_gpu_year
    )
    assert res["cost_scope"] == "gpu_cost_only"
    assert res["mode"] == "scenario"
    assert res["result_type"] == "scenario_sensitivity_estimate"
    assert res["currency"] == "UNCONFIRMED"
    assert payload["result_type"] == "scenario_sensitivity_estimate"
