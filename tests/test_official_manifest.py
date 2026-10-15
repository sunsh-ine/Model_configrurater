"""M5：官方来源清单与覆盖缺口。

不联网：只校验本地 manifest 与已存档证据的一致性，以及"不完整案例不得升级为
official_benchmark"这条规则。
"""

from __future__ import annotations

import hashlib

import yaml

VALID_STATUS = {
    "not_a_result_dataset",
    "pending_verification",
    "partial_evidence",
    # M7：官方 Cookbook 数值型来源（有实测数值但环境描述不完整）
    "numeric_source_partial_environment",
    "verified",
}


def _manifest(root):
    p = root / "data" / "sources" / "official_manifest.yaml"
    return yaml.safe_load(p.read_text(encoding="utf-8"))


def test_manifest_parses_and_ids_unique(root):
    entries = _manifest(root)
    assert entries
    ids = [e["id"] for e in entries]
    assert len(ids) == len(set(ids))


def test_manifest_statuses_are_valid(root):
    for e in _manifest(root):
        assert e["status"] in VALID_STATUS, e["id"]


def test_no_entry_claims_verified_without_full_config(root):
    """只有同时给出配置与指标字段的来源才允许标 verified；当前必须为 0 条。"""
    for e in _manifest(root):
        if e["status"] == "verified":
            assert e.get("model_revision") and e.get("framework_version")
            assert e.get("contains_measured_values") is True
            assert e.get("evidence_file") and e.get("evidence_sha256")


def test_currently_zero_verified_entries(root):
    entries = _manifest(root)
    assert sum(1 for e in entries if e["status"] == "verified") == 0
    # 未取得完整来源时，占位条目必须停在 pending_verification（不捏造）
    pending = [e for e in entries if e["status"] == "pending_verification"]
    assert pending, "应保留待补齐的候选来源占位条目"
    for e in pending:
        assert e.get("source_url") is None
        assert e.get("contains_measured_values") in (None, False)


def test_archived_evidence_files_match_recorded_sha256(root):
    """证据文件与 manifest 记录的 sha256 必须一致（可追溯性）。"""
    checked = 0
    for e in _manifest(root):
        f = e.get("evidence_file")
        if not f:
            continue
        p = root / f
        assert p.exists(), f"证据文件缺失: {f}"
        actual = hashlib.sha256(p.read_bytes()).hexdigest()
        assert actual == e["evidence_sha256"], f
        checked += 1
    assert checked >= 4, f"应校验至少 4 个已存档证据文件，实际 {checked}"


def test_every_archived_evidence_file_has_a_manifest_entry(root):
    """反向校验：data/raw/official/ 下不允许存在没有任何 manifest 条目的存档文件。

    否则会出现"文件在库里、但没人知道它从哪来"的溯源断点。
    """
    listed = {
        e.get("evidence_file")
        for e in _manifest(root)
        if e.get("evidence_file")
    }
    archived = {
        str(p.relative_to(root)).replace("\\", "/")
        for p in (root / "data" / "raw" / "official").glob("*.html")
    }
    assert archived, "应存在已存档的官方页面证据文件"
    assert not (archived - listed), f"以下存档文件没有 manifest 条目: {sorted(archived - listed)}"


def test_doc_pages_are_not_result_datasets(root):
    """抓取到的官方页面均不含实测数值，不能作为 official_benchmark。"""
    doc_entries = [e for e in _manifest(root) if e["status"] == "not_a_result_dataset"]
    assert doc_entries
    for e in doc_entries:
        assert e["contains_measured_values"] is False
        assert e["evidence_file"]


def test_coverage_gap_doc_delivered(root):
    p = root / "docs" / "official_coverage_gaps.md"
    assert p.exists()
    text = p.read_text(encoding="utf-8")
    assert "verified = 0" in text or "verified = 0 条" in text
    assert "不得捏造" in text


def test_no_official_benchmark_records_in_db(full_db):
    """当前应为 0 条 official_benchmark 记录；若未来导入，必须来自 verified 来源。"""
    from llm_capacity_pricing.services import open_db

    db, repo = open_db(full_db)
    try:
        with db.session() as session:
            kinds = [r.performance_source_kind for r in repo.list_benchmarks(session)]
    finally:
        db.dispose()
    assert "official_benchmark" not in kinds
    assert kinds.count("internal_excel") == 32
    assert kinds.count("synthetic_fixture") == 4
