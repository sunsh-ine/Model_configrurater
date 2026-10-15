"""任务 1 回归测试：业务数据与 synthetic fixture 的隔离。

固定以下契约：

* 默认业务计算 **排除** `synthetic_fixture`（32 条 Excel 历史数据）；
* 默认敏感性分析输出 **32 × 5 = 160** 条；
* 只有显式 `include_test_data=True` / `--include-test-data` 才得到 36 × 5 = **180** 条；
* 默认口径落库后，库中不得残留测试态计算结果；
* 报告的业务统计口径默认排除测试数据，并明确标注被排除的记录。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from llm_capacity_pricing.cli import app
from llm_capacity_pricing.reporting.markdown import SENSITIVITY_LEVELS
from llm_capacity_pricing.services import (
    TEST_DATA_KINDS,
    build_sensitivity,
    calculate_all,
    export_legacy_results,
    export_report,
    export_scenario_results,
    export_single_scenario,
    is_test_record,
    open_db,
    split_by_scope,
)

runner = CliRunner()

EXPECTED_BUSINESS_RECORDS = 32
EXPECTED_TEST_RECORDS = 4
LEVELS = 5  # 10/30/50/70/100%


def _fixture_ids(db_path: str) -> list[str]:
    db, repo = open_db(db_path)
    try:
        with db.session() as session:
            return sorted(repo.benchmark_ids_by_kind(session, *TEST_DATA_KINDS))
    finally:
        db.dispose()


def _stored_results(db_path: str):
    db, repo = open_db(db_path)
    try:
        with db.session() as session:
            return repo.list_pricing_results(session)
    finally:
        db.dispose()


# --------------------------------------------------------------------------
# 数据切分
# --------------------------------------------------------------------------


def test_split_by_scope_excludes_test_by_default(full_db):
    db, repo = open_db(full_db)
    try:
        with db.session() as session:
            records = repo.list_benchmarks(session)
    finally:
        db.dispose()

    assert len(records) == EXPECTED_BUSINESS_RECORDS + EXPECTED_TEST_RECORDS

    kept, excluded = split_by_scope(records, include_test_data=False)
    assert len(kept) == EXPECTED_BUSINESS_RECORDS
    assert len(excluded) == EXPECTED_TEST_RECORDS
    assert all(not is_test_record(r) for r in kept)
    assert all(is_test_record(r) for r in excluded)

    kept_all, excluded_all = split_by_scope(records, include_test_data=True)
    assert len(kept_all) == 36
    assert excluded_all == []


def test_fixture_records_are_exactly_the_four_synthetic_ones(full_db):
    ids = _fixture_ids(full_db)
    assert len(ids) == EXPECTED_TEST_RECORDS
    assert all("fixture" in rid for rid in ids)


# --------------------------------------------------------------------------
# 默认计算口径：32 / 160
# --------------------------------------------------------------------------


def test_default_calculate_all_excludes_test_data(full_db):
    results, meta = calculate_all(full_db, mode="legacy_full_load", persist=False)
    assert meta["include_test_data"] is False
    assert len(results) == EXPECTED_BUSINESS_RECORDS
    assert meta["business_record_count"] == EXPECTED_BUSINESS_RECORDS
    assert meta["test_record_count"] == 0
    assert len(meta["excluded_test_records"]) == EXPECTED_TEST_RECORDS
    assert all(r.scope == "business" for r in results)
    assert all(not r.benchmark_record_id.startswith("fixture") for r in results)
    # 32 条业务记录全部可算（fixture 才是不可算的那 4 条）
    assert meta["status_counts"]["not_evaluable"] == 0


def test_default_sensitivity_is_160(full_db):
    results = build_sensitivity(full_db, persist=False)
    assert len(results) == EXPECTED_BUSINESS_RECORDS * LEVELS == 160
    assert len(SENSITIVITY_LEVELS) == LEVELS
    assert all(r.scope == "business" for r in results)
    # 每条记录恰好 5 档
    per_record: dict[str, int] = {}
    for r in results:
        per_record[r.benchmark_record_id] = per_record.get(r.benchmark_record_id, 0) + 1
    assert len(per_record) == EXPECTED_BUSINESS_RECORDS
    assert set(per_record.values()) == {LEVELS}


# --------------------------------------------------------------------------
# 显式测试口径：36 / 180
# --------------------------------------------------------------------------


def test_explicit_include_test_data_gives_36_and_180(full_db):
    results, meta = calculate_all(
        full_db, mode="legacy_full_load", persist=False, include_test_data=True
    )
    assert len(results) == 36
    assert meta["include_test_data"] is True
    assert meta["business_record_count"] == EXPECTED_BUSINESS_RECORDS
    assert meta["test_record_count"] == EXPECTED_TEST_RECORDS
    assert meta["excluded_test_records"] == []
    assert {r.scope for r in results} == {"business", "test"}

    scenarios = build_sensitivity(full_db, persist=False, include_test_data=True)
    assert len(scenarios) == 36 * LEVELS == 180
    assert sum(1 for r in scenarios if r.scope == "test") == EXPECTED_TEST_RECORDS * LEVELS


# --------------------------------------------------------------------------
# 落库隔离：默认口径不得残留测试结果
# --------------------------------------------------------------------------


def test_default_persist_removes_stale_test_results(full_db):
    # 先按测试口径落库 180 条情景 + 36 条满载
    build_sensitivity(full_db, include_test_data=True)
    calculate_all(full_db, mode="legacy_full_load", persist=True, include_test_data=True)
    with_tests = _stored_results(full_db)
    assert len([r for r in with_tests if r.scope == "test"]) > 0

    # 再按默认口径重跑：测试态结果应被清理
    build_sensitivity(full_db)
    calculate_all(full_db, mode="legacy_full_load", persist=True)
    after = _stored_results(full_db)
    assert after, "默认口径应留下业务结果"
    assert all(r.scope == "business" for r in after), [
        r.scenario_id for r in after if r.scope != "business"
    ]
    assert len([r for r in after if r.mode == "legacy_full_load"]) == EXPECTED_BUSINESS_RECORDS
    assert len([r for r in after if r.mode == "scenario"]) == 160


def test_stored_scope_column_matches_record_kind(full_db):
    calculate_all(full_db, mode="legacy_full_load", persist=True, include_test_data=True)
    ids = set(_fixture_ids(full_db))
    for r in _stored_results(full_db):
        expected = "test" if r.benchmark_record_id in ids else "business"
        assert r.scope == expected, r.scenario_id


# --------------------------------------------------------------------------
# 导出文件口径
# --------------------------------------------------------------------------


def test_default_exports_are_business_only(full_db, tmp_path):
    legacy = export_legacy_results(full_db, tmp_path / "legacy.json")
    scenario = export_scenario_results(full_db, tmp_path / "scenario.json")

    assert legacy["result_count"] == 32
    assert scenario["result_count"] == 160
    assert scenario["business_record_count"] == 160
    assert scenario["test_record_count"] == 0

    legacy_payload = json.loads((tmp_path / "legacy.json").read_text(encoding="utf-8"))
    scenario_payload = json.loads((tmp_path / "scenario.json").read_text(encoding="utf-8"))
    for payload in (legacy_payload, scenario_payload):
        assert payload["meta"]["include_test_data"] is False
        assert payload["result_count"] == len(payload["results"])
        assert all(not r["benchmark_record_id"].startswith("fixture") for r in payload["results"])
        assert all(r["scope"] == "business" for r in payload["results"])
        assert all(r["result_type"] for r in payload["results"])


def test_test_mode_exports_include_fixtures(full_db, tmp_path):
    legacy = export_legacy_results(full_db, tmp_path / "l36.json", include_test_data=True)
    scenario = export_scenario_results(full_db, tmp_path / "s180.json", include_test_data=True)
    assert legacy["result_count"] == 36
    assert scenario["result_count"] == 180
    assert scenario["test_record_count"] == 4 * LEVELS


def test_single_scenario_on_fixture_requires_explicit_flag(full_db, tmp_path):
    from llm_capacity_pricing.services import ServiceError

    with pytest.raises(ServiceError, match="synthetic_fixture"):
        export_single_scenario(
            full_db,
            tmp_path / "x.json",
            record_id="fixture_vllm_synthetic_rec",
            utilization=__import__("decimal").Decimal("0.3"),
        )

    info = export_single_scenario(
        full_db,
        tmp_path / "y.json",
        record_id="fixture_vllm_synthetic_rec",
        utilization=__import__("decimal").Decimal("0.3"),
        include_test_data=True,
    )
    assert info["scope"] == "test"
    assert info["result_type"] == "scenario_sensitivity_estimate"
    assert info["status"] == "not_evaluable", "fixture 无匹配 Token 价格，应不可计算"


# --------------------------------------------------------------------------
# 报告统计
# --------------------------------------------------------------------------


def test_report_business_stats_exclude_test_data(full_db, tmp_path):
    out = tmp_path / "overview.md"
    info = export_report(full_db, out)
    text = out.read_text(encoding="utf-8")

    assert info["include_test_data"] is False
    assert info["business_records"] == EXPECTED_BUSINESS_RECORDS
    assert len(info["excluded_test_records"]) == EXPECTED_TEST_RECORDS
    assert info["scenario_results"] == 160
    assert info["legacy_results"] == EXPECTED_BUSINESS_RECORDS

    assert "本次报告是否纳入测试数据: **否**" in text
    assert "32 × 5 = 160 条" in text
    assert "36 × 5 = 180 条" in text
    assert "--include-test-data" in text
    assert "被排除在业务计算之外的测试记录" in text
    for rid in info["excluded_test_records"]:
        assert rid in text

    # 报告里的业务统计不得把 fixture 计入业务数
    assert "可完整计算（status=ok）: **32** / 32" in text


def test_report_with_test_data_is_explicitly_flagged(full_db, tmp_path):
    out = tmp_path / "overview_tests.md"
    info = export_report(full_db, out, include_test_data=True)
    text = out.read_text(encoding="utf-8")
    assert info["include_test_data"] is True
    assert info["scenario_results"] == 180
    assert "本次报告是否纳入测试数据: **是**" in text
    assert "不得用于任何对外结论" in text


def test_report_source_kind_table_marks_test_data_excluded(full_db, tmp_path):
    out = tmp_path / "o.md"
    export_report(full_db, out)
    text = out.read_text(encoding="utf-8")
    assert "| synthetic_fixture | 4 | 不纳入 |" in text
    assert "| internal_excel | 32 | 纳入 |" in text
    # 缺口统计只针对 32 条真实记录
    assert "以下字段在 32 条真实效能记录中" in text


def test_evidence_and_import_counts_unaffected_by_scope(full_db, tmp_path):
    """切换口径不得改变"导入结果"类统计（证据/记录总数），只影响"计算结果"统计。"""
    out_a = tmp_path / "a.md"
    out_b = tmp_path / "b.md"
    info_a = export_report(full_db, out_a)
    info_b = export_report(full_db, out_b, include_test_data=True)

    assert info_a["records"] == info_b["records"] == 36
    assert info_a["business_records"] == info_b["business_records"] == 32
    text_a = out_a.read_text(encoding="utf-8")
    text_b = out_b.read_text(encoding="utf-8")
    for needle in (
        "- 部署效能记录: 36 条（业务 32 + 人工测试 4）",
        "- GPU 硬件价格: 2 条",
        "- 模型 Token 价格: 3 条",
    ):
        assert needle in text_a, needle
        assert needle in text_b, needle


# --------------------------------------------------------------------------
# CLI 开关
# --------------------------------------------------------------------------


def test_cli_default_excludes_test_data(full_db, tmp_path):
    out = tmp_path / "l.json"
    r = runner.invoke(
        app,
        ["calculate-all", "--mode", "legacy_full_load", "--db", full_db, "--out", str(out)],
    )
    assert r.exit_code == 0, r.output
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["result_count"] == 32
    assert payload["meta"]["include_test_data"] is False
    assert "结果条数: 32（业务 32 + 测试 0）" in r.output


def test_cli_include_test_data_flag(full_db, tmp_path):
    out = tmp_path / "l36.json"
    r = runner.invoke(
        app,
        [
            "calculate-all", "--mode", "legacy_full_load", "--db", full_db,
            "--out", str(out), "--include-test-data",
        ],
    )
    assert r.exit_code == 0, r.output
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["result_count"] == 36
    assert payload["meta"]["include_test_data"] is True
    assert "结果条数: 36（业务 32 + 测试 4）" in r.output
    assert "人工测试数据" in r.output


def test_cli_scenario_mode_default_160(full_db, tmp_path):
    """`--mode scenario --out` 导出完整默认敏感性：32 × 5 = 160 条。"""
    out = tmp_path / "s.json"
    r = runner.invoke(
        app,
        ["calculate-all", "--mode", "scenario", "--utilization", "0.30",
         "--db", full_db, "--out", str(out)],
    )
    assert r.exit_code == 0, r.output
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["result_count"] == 160
    assert payload["meta"]["include_test_data"] is False
    assert payload["meta"]["levels"] == ["0.10", "0.30", "0.50", "0.70", "1.00"]
    assert payload["meta"]["test_record_count"] == 0


def test_cli_report_default_excludes_test_data(full_db, tmp_path):
    out = tmp_path / "o.md"
    r = runner.invoke(app, ["report", "--db", full_db, "--out", str(out)])
    assert r.exit_code == 0, r.output
    assert "本次报告是否纳入测试数据: **否**" in out.read_text(encoding="utf-8")

    out2 = tmp_path / "o2.md"
    r2 = runner.invoke(
        app, ["report", "--db", full_db, "--out", str(out2), "--include-test-data"]
    )
    assert r2.exit_code == 0, r2.output
    assert "本次报告是否纳入测试数据: **是**" in out2.read_text(encoding="utf-8")


def test_cli_scenario_on_fixture_needs_flag(full_db, tmp_path):
    r = runner.invoke(
        app,
        ["scenario", "--record", "fixture_vllm_synthetic_rec", "--utilization", "0.3",
         "--db", full_db, "--out", str(tmp_path / "x.json")],
    )
    assert r.exit_code != 0
    assert "synthetic_fixture" in r.output

    r2 = runner.invoke(
        app,
        ["scenario", "--record", "fixture_vllm_synthetic_rec", "--utilization", "0.3",
         "--db", full_db, "--out", str(tmp_path / "y.json"), "--include-test-data"],
    )
    assert r2.exit_code == 0, r2.output
