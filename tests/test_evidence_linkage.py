"""任务 3 回归测试：证据与结果的关联。

固定以下契约：

* 每条 Benchmark 都有关联来源与至少一个质量标记；
* 每条 PricingResult 能追溯到性能记录、硬件成本、Token 单价与证据链；
* 原始 Excel 数据统一标记为**历史外部来源**，不得标为内部实测；
* 缺 TTFT/TPOT P99 时 SLA 恒为 `not_evaluable`，且不由其它指标反推；
* 报告不会把理论年收入描述成实际收入或客户报价。
"""

from __future__ import annotations

import re
from decimal import Decimal

import pytest

from llm_capacity_pricing.engines.calculator import (
    calculate,
    make_legacy_scenario,
    sla_assessment,
)
from llm_capacity_pricing.importers.common import ORIGIN_BY_SOURCE_KIND
from llm_capacity_pricing.reporting.json_export import read_json
from llm_capacity_pricing.schemas.benchmark import BenchmarkRecord
from llm_capacity_pricing.schemas.evidence import ALLOWED_ORIGINS, Evidence
from llm_capacity_pricing.services import (
    calculate_all,
    export_report,
    export_legacy_results,
    open_db,
    validate_import,
    validate_results,
)

#: 只会在"把理论收入说成真实收入/报价"时出现的表述（正向断言用语）
FORBIDDEN_POSITIVE_CLAIMS = (
    "guaranteed_sla",
    "sla_guarantee",
    "official_quote",
    "total_cost_of_ownership",
    "承诺 SLA",
    "保证 SLA",
    "实际营收为",
    "实际收入为",
    "客户报价为",
    "可对外报价",
)

#: 报告必须出现的反声明
REQUIRED_DISCLAIMERS = (
    "不是对外报价",
    "不是实际营收",
    "不是实际收入，不是客户报价",
    "理论满载",
    "cost_scope = gpu_cost_only",
    "不提供任何 SLA 承诺",
)


@pytest.fixture
def full_data(full_db):
    db, repo = open_db(full_db)
    try:
        with db.session() as session:
            return {
                "records": repo.list_benchmarks(session),
                "evidences": {e.evidence_id: e for e in repo.list_evidence(session)},
                "hardware": {h.hardware_price_id: h for h in repo.list_hardware_prices(session)},
                "tokens": {t.token_price_id: t for t in repo.list_token_prices(session)},
            }
    finally:
        db.dispose()


# --------------------------------------------------------------------------
# 1. 每条 Benchmark 都有来源与质量标记
# --------------------------------------------------------------------------


def test_every_benchmark_has_source_and_quality_flags(full_data):
    for rec in full_data["records"]:
        ev = full_data["evidences"].get(rec.evidence_id)
        assert ev is not None, rec.record_id
        assert ev.publisher or ev.source_url or ev.source_file, rec.record_id
        assert rec.quality_flags, rec.record_id
        assert rec.performance_source_kind, rec.record_id


def test_validate_import_reports_new_linkage_checks(excel_db):
    report = validate_import(excel_db)
    names = {c["name"] for c in report["checks"]}
    for expected in (
        "every_record_has_source",
        "every_record_has_quality_flags",
        "excel_marked_as_historical_external",
        "historical_external_flag_present",
        "no_internal_measurement_declared",
    ):
        assert expected in names, expected
    assert report["ok"], [c for c in report["checks"] if not c["ok"]]


def test_excel_records_have_row_level_traceability(full_data):
    excel = [r for r in full_data["records"] if r.performance_source_kind == "internal_excel"]
    assert len(excel) == 32
    for rec in excel:
        ev = full_data["evidences"][rec.evidence_id]
        assert ev.source_sheet == "部署效能"
        assert isinstance(ev.source_row, int)
        assert ev.raw_sha256 and len(ev.raw_sha256) == 64


# --------------------------------------------------------------------------
# 2. 每条 PricingResult 可追溯
# --------------------------------------------------------------------------


def test_validate_results_passes_for_business_scope(full_db):
    report = validate_results(full_db)
    assert report["ok"], [c for c in report["checks"] if not c["ok"]]
    assert report["result_count"] == 32
    names = {c["name"] for c in report["checks"]}
    for expected in (
        "result_linked_to_performance_record",
        "result_linked_to_hardware_price",
        "result_linked_to_token_price",
        "result_has_evidence_chain",
        "result_scope_matches_source_kind",
        "currency_declared",
    ):
        assert expected in names, expected


def test_validate_results_passes_with_test_data(full_db):
    report = validate_results(full_db, include_test_data=True)
    assert report["result_count"] == 36
    linkage = {c["name"]: c for c in report["checks"]}
    assert linkage["result_linked_to_performance_record"]["ok"]
    assert linkage["result_scope_matches_source_kind"]["ok"]


def test_result_carries_all_three_input_ids(full_db, full_data):
    results, _ = calculate_all(full_db, mode="legacy_full_load", persist=False)
    for r in results:
        assert r.performance_record_id == r.benchmark_record_id
        assert r.hardware_price_id in full_data["hardware"]
        assert r.token_price_id in full_data["tokens"]
        rec = next(x for x in full_data["records"] if x.record_id == r.benchmark_record_id)
        assert full_data["hardware"][r.hardware_price_id].gpu_model == rec.hardware_model
        assert full_data["tokens"][r.token_price_id].model_name == rec.model_name
        # 证据链 = 性能来源 + 硬件价格来源 + Token 价格来源
        assert rec.evidence_id in r.evidence_ids
        assert full_data["hardware"][r.hardware_price_id].evidence_id in r.evidence_ids
        assert full_data["tokens"][r.token_price_id].evidence_id in r.evidence_ids
        assert len(r.evidence_ids) == 3


def test_result_ids_persisted_in_db(full_db):
    from sqlalchemy import select

    from llm_capacity_pricing.storage.database import PricingResultRow

    calculate_all(full_db, mode="legacy_full_load", persist=True)
    db, _ = open_db(full_db)
    try:
        with db.session() as session:
            rows = session.scalars(select(PricingResultRow)).all()
    finally:
        db.dispose()
    assert rows
    for row in rows:
        assert row.performance_record_id
        assert row.token_price_id
        assert row.hardware_price_id
        assert row.evidence_ids_json and row.evidence_ids_json.count("ev_") >= 1
        assert row.scope in ("business", "test")
        assert row.sla_status in ("not_evaluable", "evaluable")


def test_missing_price_linkage_is_explicit_not_guessed(full_db):
    """只导入 Excel、不含 fixture 价格时，fixture 记录应给出 linkage_warnings。"""
    results, _ = calculate_all(
        full_db, mode="legacy_full_load", persist=False, include_test_data=True
    )
    fixtures = [r for r in results if r.scope == "test"]
    assert fixtures
    for r in fixtures:
        assert r.status == "not_evaluable"
        assert r.linkage_warnings, r.scenario_id
        assert r.token_price_id is None
        assert any("token_price_not_linked" in w for w in r.linkage_warnings)


# --------------------------------------------------------------------------
# 3. 原始 Excel = 历史外部来源，不是内部实测
# --------------------------------------------------------------------------


def test_excel_evidence_marked_historical_external(full_data):
    excel_evs = [
        e
        for e in full_data["evidences"].values()
        if e.source_type == "internal_excel"
    ]
    assert len(excel_evs) == 37  # 2 GPU + 3 价格 + 32 效能
    for ev in excel_evs:
        assert ev.measurement_origin == "historical_external_third_party"
        assert ev.is_internal_measurement is False
        assert "historical_external_source_not_internal_measurement" in ev.quality_flags


def test_no_evidence_claims_internal_measurement(full_data):
    assert not any(e.is_internal_measurement for e in full_data["evidences"].values())
    assert not any(
        e.measurement_origin == "internal_measurement"
        for e in full_data["evidences"].values()
    )


def test_evidence_rejects_inconsistent_origin():
    """模型层禁止把外部数据标成内部实测。"""
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="measurement_origin"):
        Evidence(
            evidence_id="e",
            source_type="internal_excel",
            measurement_origin="internal_measurement",
            is_internal_measurement=True,
        )
    with pytest.raises(ValidationError, match="is_internal_measurement"):
        Evidence(
            evidence_id="e",
            source_type="internal_excel",
            measurement_origin="historical_external_third_party",
            is_internal_measurement=True,
        )


def test_allowed_origins_cover_adapter_mapping():
    for kind, (source_type, origin) in ORIGIN_BY_SOURCE_KIND.items():
        assert origin in ALLOWED_ORIGINS[source_type], (kind, source_type, origin)


def test_fixture_evidence_is_assumption_not_official(full_data):
    for ev in full_data["evidences"].values():
        if "synthetic_fixture" in ev.evidence_id:
            assert ev.source_type == "assumption"
            assert ev.measurement_origin == "synthetic_fixture"
            assert ev.is_internal_measurement is False


# --------------------------------------------------------------------------
# 4. 缺 P99 -> SLA 恒为 not_evaluable
# --------------------------------------------------------------------------


def test_all_business_results_are_sla_not_evaluable(full_db, full_data):
    results, _ = calculate_all(full_db, mode="legacy_full_load", persist=False)
    assert len(results) == 32
    for r in results:
        rec = next(x for x in full_data["records"] if x.record_id == r.benchmark_record_id)
        assert rec.ttft_p99_ms is None and rec.tpot_p99_ms is None
        assert r.sla_status == "not_evaluable"
        assert "missing_ttft_p99_ms" in r.sla_not_evaluable_reasons
        assert "missing_tpot_p99_ms" in r.sla_not_evaluable_reasons
        assert any("不以其它指标反推" in x for x in r.sla_not_evaluable_reasons)


def test_sla_function_requires_both_p99():
    base = dict(
        record_id="r",
        model_name="m",
        hardware_model="g",
        deployment_gpu_count=1,
        evidence_id="e",
        performance_source_kind="internal_excel",
    )
    status, reasons = sla_assessment(BenchmarkRecord(**base))
    assert status == "not_evaluable"
    assert reasons[:2] == ["missing_ttft_p99_ms", "missing_tpot_p99_ms"]

    only_ttft = BenchmarkRecord(**base, ttft_p99_ms=Decimal("500"))
    assert sla_assessment(only_ttft)[0] == "not_evaluable"

    only_tpot = BenchmarkRecord(**base, tpot_p99_ms=Decimal("30"))
    assert sla_assessment(only_tpot)[0] == "not_evaluable"

    both = BenchmarkRecord(**base, ttft_p99_ms=Decimal("500"), tpot_p99_ms=Decimal("30"))
    assert sla_assessment(both) == ("evaluable", [])


def test_single_user_p90_never_makes_sla_evaluable():
    rec = BenchmarkRecord(
        record_id="r",
        model_name="m",
        hardware_model="g",
        deployment_gpu_count=1,
        evidence_id="e",
        performance_source_kind="internal_excel",
        single_user_output_tps_p90=Decimal("200"),
        input_tps_per_gpu=Decimal("1000"),
        output_tps_per_gpu=Decimal("50"),
        cache_hit_rate_raw=Decimal("0.5"),
    )
    assert sla_assessment(rec)[0] == "not_evaluable"


def test_fixture_with_p99_is_evaluable_showing_rule_is_not_hardcoded(full_db):
    """vLLM fixture 提供 p99_ttft，但无 tpot_p99 -> 仍 not_evaluable；
    说明该判定来自数据而非写死。"""
    results, _ = calculate_all(
        full_db, mode="legacy_full_load", persist=False, include_test_data=True
    )
    by_id = {r.benchmark_record_id: r for r in results}
    vllm = by_id["fixture_vllm_synthetic_rec"]
    assert vllm.sla_status == "not_evaluable"
    assert "missing_tpot_p99_ms" in vllm.sla_not_evaluable_reasons
    assert "missing_ttft_p99_ms" not in vllm.sla_not_evaluable_reasons
    # 但该记录有 ttft_p99 实测值，只是 SLA 需要同时具备两项
    db, repo = open_db(full_db)
    try:
        with db.session() as session:
            rec = repo.get_benchmark(session, "fixture_vllm_synthetic_rec")
    finally:
        db.dispose()
    assert rec.ttft_p99_ms == Decimal("452.7")
    assert rec.tpot_p99_ms is None


def test_validate_results_catches_sla_mislabel(full_db, monkeypatch):
    """若人为把 sla_status 改错，校验必须报错。"""
    import llm_capacity_pricing.services as svc

    original = svc.calculate

    def patched(record, tp, hp, scenario):
        res = original(record, tp, hp, scenario)
        res.sla_status = "evaluable"
        return res

    monkeypatch.setattr(svc, "calculate", patched)
    report = svc.validate_results(full_db)
    failed = {c["name"] for c in report["checks"] if not c["ok"]}
    assert "sla_not_evaluable_without_p99" in failed
    assert not report["ok"]


# --------------------------------------------------------------------------
# 5. 报告措辞：不得把理论收入说成实际收入/客户报价
# --------------------------------------------------------------------------


def _assert_no_positive_claims(text: str, label: str) -> None:
    for token in FORBIDDEN_POSITIVE_CLAIMS:
        assert token not in text, f"{label} 出现越界表述: {token}"


def test_report_contains_required_disclaimers(full_db, tmp_path):
    out = tmp_path / "o.md"
    export_report(full_db, out)
    text = out.read_text(encoding="utf-8")
    for phrase in REQUIRED_DISCLAIMERS:
        assert phrase in text, phrase
    _assert_no_positive_claims(text, "report")


def test_json_exports_contain_no_positive_claims(full_db, tmp_path):
    legacy = tmp_path / "l.json"
    export_legacy_results(full_db, legacy)
    _assert_no_positive_claims(legacy.read_text(encoding="utf-8"), "legacy.json")
    payload = read_json(legacy)
    joined = " ".join(payload["boundary_notices"])
    assert "不是对外报价" in joined
    assert "gpu_cost_only" in joined
    assert "非实际营收" in joined
    assert payload["results"][0]["result_type"] == "theoretical_full_load_potential"


def test_revenue_section_has_adjacent_disclaimer(full_db, tmp_path):
    """收入表格附近的文本里必须有反声明（防止被单独摘引）。"""
    out = tmp_path / "o.md"
    export_report(full_db, out)
    lines = out.read_text(encoding="utf-8").splitlines()
    idx = next(i for i, l in enumerate(lines) if l.startswith("## 4. 最大理论满载收入案例"))
    window = "\n".join(lines[idx : idx + 45])
    assert "不是实际收入、不是客户报价" in window
    assert "理论满载" in window

    idx5 = next(i for i, l in enumerate(lines) if l.startswith("## 5. 成本敏感性"))
    window5 = "\n".join(lines[idx5 : idx5 + 30])
    assert "不能作为利润结论" in window5 or "未包含" in window5


def test_report_declares_currency_and_cost_scope_table(full_db, tmp_path):
    out = tmp_path / "o.md"
    export_report(full_db, out)
    text = out.read_text(encoding="utf-8")
    assert "### 口径声明（务必先读）" in text
    assert "不是可收取金额" in text
    assert "`UNCONFIRMED`" in text
    assert "`gpu_cost_only`" in text


def test_report_includes_result_input_traceability_table(full_db, tmp_path):
    out = tmp_path / "o.md"
    export_report(full_db, out)
    text = out.read_text(encoding="utf-8")
    assert "### 6.1 结果 → 输入 追溯链" in text
    assert "excel_部署效能_2" in text
    assert "excel_GPU价格_2" in text
    assert "excel_模型价格_2" in text
    assert "`not_evaluable`" in text


def test_report_money_figures_are_labelled_theoretical(full_db, tmp_path):
    """收入表格的表头必须带"理论"字样，避免被误读为实际收入。"""
    out = tmp_path / "o.md"
    export_report(full_db, out)
    text = out.read_text(encoding="utf-8")
    assert "理论满载年收入" in text or "理论满载" in text
    # 表格里的金额行不得出现"实际"
    for line in text.splitlines():
        if re.search(r"\|\s*[\d,]+\.\d{4}\s*\|", line):
            assert "实际" not in line, line
