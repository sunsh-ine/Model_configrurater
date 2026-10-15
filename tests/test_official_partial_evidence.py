"""任务 4 回归测试：官方资料覆盖（partial_evidence）。

固定以下契约：

* manifest 中只有 ``partial_evidence`` 条目入库，且必须带存档证据与 sha256；
* 证据文件哈希必须与 manifest 记录一致（防止抄错/被篡改）；
* 指标只从证据文件**机器解析**，不采用人工抄录值；
* 硬件绑定不明时 ``hardware_binding=unbound`` 且硬件字段必须为空（不补造配置）；
* 候选记录**不进入**性能数据库、不参与经济性计算；
* 单请求冒烟测试必须显式标记，不得当作吞吐基准。
"""

from __future__ import annotations

import hashlib
import shutil
from decimal import Decimal
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from llm_capacity_pricing.cli import app
from llm_capacity_pricing.importers.console_summary import (
    extract_summary_blocks,
    html_to_text,
)
from llm_capacity_pricing.importers.official_manifest import (
    OfficialManifestError,
    build_all_candidates,
    build_candidate,
    load_manifest,
)
from llm_capacity_pricing.schemas.official import OfficialBenchmarkCandidate
from llm_capacity_pricing.services import (
    calculate_all,
    export_report,
    import_official_candidates,
    official_coverage_summary,
    open_db,
    validate_results,
)

runner = CliRunner()
MANIFEST = "data/sources/official_manifest.yaml"
RECIPES = "vllm_recipes_deepseek_v3_console_output"

#: 依据 vLLM Recipes 页面 Expected Output 块中**文档明确写出**的标签
EXPECTED_RECIPES_METRICS = {
    "success_count": Decimal("1"),
    "duration_s": Decimal("16.39"),
    "ttft_p99_ms": Decimal("560.00"),
    "tpot_p99_ms": Decimal("15.85"),
    "itl_p99_ms": Decimal("16.15"),
    "output_throughput_tok_s": Decimal("61.00"),
}


@pytest.fixture
def official_db(excel_db: str) -> str:
    import_official_candidates(MANIFEST, excel_db, root=".")
    return excel_db


# --------------------------------------------------------------------------
# manifest 结构
# --------------------------------------------------------------------------


def test_manifest_loads_and_statuses_known(root):
    entries = load_manifest(root / MANIFEST)
    statuses = {e["status"] for e in entries}
    assert statuses <= {"partial_evidence", "pending_verification", "not_a_result_dataset", "verified"}
    assert sum(1 for e in entries if e["status"] == "verified") == 0


def test_only_partial_evidence_entries_are_imported(root):
    candidates, evidences, skipped = build_all_candidates(root / MANIFEST, root=root)
    assert len(candidates) == 6
    assert len(evidences) == 6
    # 14 条 manifest = 6 条 partial_evidence 入库 + 6 条 not_a_result_dataset
    # + 2 条 pending_verification 占位，后两类不进候选表。
    assert len(skipped) == 8
    assert all(c.verification_status == "partial_evidence" for c in candidates)
    assert all(s["status"] != "partial_evidence" for s in skipped)


def test_candidate_requires_evidence_file(root, tmp_path):
    entry = {
        "id": "x",
        "status": "partial_evidence",
        "publisher": "p",
        "evidence_file": None,
    }
    with pytest.raises(OfficialManifestError, match="evidence_file"):
        build_candidate(entry, root=tmp_path)


def test_candidate_rejects_sha256_mismatch(root, tmp_path):
    src = root / "data/raw/official/vllm_cli_guide_2026-10-15.html"
    shutil.copy(src, tmp_path / "f.html")
    entry = {
        "id": "x",
        "status": "partial_evidence",
        "publisher": "p",
        "evidence_file": "f.html",
        "evidence_sha256": "0" * 64,
    }
    with pytest.raises(OfficialManifestError, match="sha256 不一致"):
        build_candidate(entry, root=tmp_path)


def test_missing_evidence_file_raises(root, tmp_path):
    entry = {
        "id": "x",
        "status": "partial_evidence",
        "publisher": "p",
        "evidence_file": "nope.html",
    }
    with pytest.raises(OfficialManifestError, match="证据文件缺失"):
        build_candidate(entry, root=tmp_path)


# --------------------------------------------------------------------------
# 指标来自证据文件解析
# --------------------------------------------------------------------------


def test_recipes_metrics_match_documented_labels(root):
    candidates, _, _ = build_all_candidates(root / MANIFEST, root=root)
    c = next(x for x in candidates if x.manifest_id == RECIPES)
    for field, want in EXPECTED_RECIPES_METRICS.items():
        assert c.metrics[field] == want, (field, c.metrics.get(field), want)
    # 原始标签保留，便于回查文档
    assert c.raw_labels["ttft_p99_ms"] == "P99 TTFT (ms)"
    assert c.raw_labels["tpot_p99_ms"] == "P99 TPOT (ms)"
    assert "metrics_machine_parsed_from_evidence_file" in c.quality_flags


def test_metrics_traceable_to_archived_file(root):
    entry = next(e for e in load_manifest(root / MANIFEST) if e["id"] == RECIPES)
    path = root / entry["evidence_file"]
    assert hashlib.sha256(path.read_bytes()).hexdigest() == entry["evidence_sha256"]
    blocks = extract_summary_blocks(html_to_text(path.read_text(encoding="utf-8", errors="replace")))
    assert len(blocks) == 1
    assert blocks[0]["metrics"]["tpot_p99_ms"] == Decimal("15.85")


def test_single_request_page_is_marked_smoke_test(root):
    candidates, _, _ = build_all_candidates(root / MANIFEST, root=root)
    c = next(x for x in candidates if x.manifest_id == RECIPES)
    assert c.metrics["success_count"] == 1
    assert c.is_throughput_benchmark is False
    assert "single_request_smoke_test" in c.quality_flags


def test_unparsed_pages_have_no_invented_metrics(root):
    candidates, _, _ = build_all_candidates(root / MANIFEST, root=root)
    for c in candidates:
        if c.manifest_id == RECIPES:
            continue
        assert c.metrics == {}, c.candidate_id
        assert c.is_throughput_benchmark is None, c.candidate_id
        assert "metrics_not_machine_parsed_present_in_document_only" in c.quality_flags


# --------------------------------------------------------------------------
# 不补造配置
# --------------------------------------------------------------------------


def test_hardware_binding_unbound_keeps_fields_empty(root):
    candidates, _, _ = build_all_candidates(root / MANIFEST, root=root)
    with_candidates = 0
    for c in candidates:
        assert c.hardware_binding == "unbound"
        assert c.hardware_model is None
        assert c.deployment_gpu_count is None
        if c.hardware_candidates:
            with_candidates += 1
    # 只有一个来源完全没有列出硬件候选（allowed），其余应列出候选但不绑定
    assert with_candidates == len(candidates) - 1
    empty = [c.candidate_id for c in candidates if not c.hardware_candidates]
    assert empty == ["official_lmsys_hetero_cpu_gpu_epd"]


def test_candidate_model_rejects_bound_without_hardware():
    with pytest.raises(Exception, match="hardware_binding=unbound"):
        OfficialBenchmarkCandidate(
            candidate_id="c",
            manifest_id="m",
            evidence_id="e",
            publisher="p",
            hardware_binding="unbound",
            hardware_model="H200",
            missing_fields=["hardware_model"],
            quality_flags=["synthetic_fixture"],
        )


def test_candidate_model_rejects_verified_with_gaps():
    with pytest.raises(Exception, match="不能为 'verified'"):
        OfficialBenchmarkCandidate(
            candidate_id="c",
            manifest_id="m",
            evidence_id="e",
            publisher="p",
            verification_status="verified",
            model_name="m",
            metrics={"ttft_p99_ms": Decimal("1")},
            missing_fields=["model_revision"],
        )


def test_missing_ledger_is_complete_and_consistent(root):
    candidates, _, _ = build_all_candidates(root / MANIFEST, root=root)
    for c in candidates:
        absent = c.absent_required_fields()
        assert set(c.missing_fields) == set(absent), c.candidate_id
        # 台账里的配置字段确实为空
        for f in c.missing_fields:
            if f in OfficialBenchmarkCandidate.model_fields:
                assert getattr(c, f) is None, (c.candidate_id, f)


def test_recipes_candidate_declares_missing_binding(root):
    candidates, _, _ = build_all_candidates(root / MANIFEST, root=root)
    c = next(x for x in candidates if x.manifest_id == RECIPES)
    for f in ("hardware_model", "deployment_gpu_count", "model_revision",
              "framework_version", "dataset_name"):
        assert f in c.missing_fields
    # 指标侧齐备，因此不应出现在缺口里
    assert "ttft_p99_ms" not in c.missing_fields
    assert "tpot_p99_ms" not in c.missing_fields


# --------------------------------------------------------------------------
# 隔离：候选不进入性能数据库
# --------------------------------------------------------------------------


def test_candidates_do_not_enter_performance_table(official_db):
    db, repo = open_db(official_db)
    try:
        with db.session() as session:
            records = repo.list_benchmarks(session)
            candidates = repo.list_official_candidates(session)
    finally:
        db.dispose()
    assert len(candidates) == 6
    assert len(records) == 32, "候选不得进入 benchmark_record（此库仅含 Excel 32 条）"
    assert all(r.performance_source_kind == "internal_excel" for r in records)
    assert not any(r.performance_source_kind == "official_benchmark" for r in records)
    assert not any(r.record_id.startswith("official_") for r in records)


def test_candidates_do_not_affect_calculations(official_db):
    results, meta = calculate_all(official_db, mode="legacy_full_load", persist=False)
    assert len(results) == 32
    assert meta["business_record_count"] == 32
    assert not any(r.benchmark_record_id.startswith("official_") for r in results)
    report = validate_results(official_db)
    assert report["ok"], [c for c in report["checks"] if not c["ok"]]


def test_candidate_evidence_is_official_and_traceable(official_db):
    db, repo = open_db(official_db)
    try:
        with db.session() as session:
            candidates = repo.list_official_candidates(session)
            evs = {e.evidence_id: e for e in repo.list_evidence(session)}
    finally:
        db.dispose()
    for c in candidates:
        ev = evs[c.evidence_id]
        assert ev.source_type == "official_benchmark"
        assert ev.measurement_origin == "official_benchmark"
        assert ev.is_internal_measurement is False
        assert ev.source_url == c.source_url
        assert ev.raw_sha256 == c.evidence_sha256
        assert c.evidence_file and c.evidence_sha256


def test_coverage_summary_reports_zero_verified(official_db):
    summary = official_coverage_summary(official_db)
    assert summary["candidate_count"] == 6
    assert summary["verified_count"] == 0
    assert summary["with_parsed_metrics"] == 1
    assert summary["single_request_smoke_tests"] == 1
    assert summary["throughput_benchmarks"] == 0


def test_report_lists_candidates_and_keeps_verified_zero(official_db, tmp_path):
    out = tmp_path / "o.md"
    export_report(official_db, out)
    text = out.read_text(encoding="utf-8")
    assert "### 6.3 官方 Benchmark 候选证据（partial_evidence）" in text
    assert "official_vllm_recipes_deepseek_v3_console_output" in text
    assert "已核实的完整官方案例仍然为 0 条" in text
    assert "`unbound`" in text
    # 机器解析数值出现在报告中
    assert "560.00" in text and "15.85" in text


def test_import_is_idempotent(official_db):
    before = official_coverage_summary(official_db)
    import_official_candidates(MANIFEST, official_db, root=".")
    after = official_coverage_summary(official_db)
    assert before == after
    db, repo = open_db(official_db)
    try:
        with db.session() as session:
            assert len(repo.list_official_candidates(session)) == 6
    finally:
        db.dispose()


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def test_cli_import_official(excel_db, tmp_path):
    r = runner.invoke(
        app, ["import-official", "--manifest", MANIFEST, "--db", excel_db, "--root", "."]
    )
    assert r.exit_code == 0, r.output
    assert "6 条官方候选证据" in r.output
    assert "跳过 8 条未入库条目" in r.output


def test_cli_official_coverage(excel_db):
    runner.invoke(app, ["import-official", "--manifest", MANIFEST, "--db", excel_db, "--root", "."])
    r = runner.invoke(app, ["official-coverage", "--db", excel_db])
    assert r.exit_code == 0, r.output
    assert '"verified_count": 0' in r.output
    assert '"candidate_count": 6' in r.output


def test_cli_import_official_rejects_bad_manifest(excel_db, tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("- id: x\n  status: partial_evidence\n  evidence_file: nope.html\n", encoding="utf-8")
    r = runner.invoke(app, ["import-official", "--manifest", str(bad), "--db", excel_db])
    assert r.exit_code != 0
