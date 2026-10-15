"""M7/M8/M9：SGLang Cookbook 官方数值候选、指标口径与可用性门禁。

覆盖任务书 §7.3 的测试类别：
官方数值正确性 / 幂等 / 来源完整性 / 缺失字段 / 指标语义 / 边界数值 /
业务数据隔离 / SLA 门禁 / 报价门禁 / 精度复算 / 仓库可复现（离线）。
"""

from __future__ import annotations

import json
import shutil
from decimal import Decimal
from pathlib import Path

import pytest
from typer.testing import CliRunner

from llm_capacity_pricing.cli import app
from llm_capacity_pricing.engines.applicability import (
    CURRENCY_UNCONFIRMED,
    PriceContext,
    SlaRequirement,
    assess_applicability,
)
from llm_capacity_pricing.engines.metric_semantics import (
    FIELD_DEFINITIONS,
    THROUGHPUT_TOLERANCE,
    cross_backend_comparable,
    render_dictionary_markdown,
    label_provenance_issues,
    throughput_consistency,
)
from llm_capacity_pricing.importers.sglang_cookbook import (
    METRIC_MAP,
    SOURCE_SPECS,
    SglangCookbookError,
    build_numeric_candidates,
    git_blob_sha1,
    load_incoming,
    sha256_file,
)
from llm_capacity_pricing.services import (
    build_sensitivity,
    calculate_all,
    import_sglang_candidates,
    open_db,
    official_coverage_summary,
)

INCOMING = "data/incoming/sglang_official_benchmark_candidates_20261015.json"
QWEN3 = "sglang_qwen3_cookbook"
CODER = "sglang_qwen3_coder_cookbook"
runner = CliRunner()

#: 任务书 §3.3 给出的参考断言（并发, 集群输入吞吐, 集群输出吞吐, TTFT P99, TPOT P99）
TASK_BOOK_EXPECTATIONS: dict[str, tuple[int, str, str, str, str]] = {
    f"{QWEN3}:5.1.1.1": (1, "140.07", "96.65", "166.05", "10.15"),
    f"{QWEN3}:5.1.1.2": (16, "810.44", "832.04", "727.08", "24.09"),
    f"{QWEN3}:5.1.1.3": (100, "2713.46", "2738.78", "863.11", "41.19"),
    f"{CODER}:5.1.1.1": (1, "82.68", "57.19", "662.15", "17.08"),
    f"{CODER}:5.1.1.2": (16, "455.77", "468.83", "1318.63", "43.48"),
    f"{CODER}:5.1.1.3": (64, "893.84", "956.80", "529.01", "204.24"),
}


@pytest.fixture(scope="module")
def built(root: Path):
    return build_numeric_candidates(root / INCOMING, root=root)


@pytest.fixture
def candidate_db(tmp_path: Path, root: Path) -> str:
    from llm_capacity_pricing.services import init_db

    db = str(tmp_path / "cand.db")
    init_db(db)
    import_sglang_candidates(root / INCOMING, db, root=root)
    return db


# --------------------------------------------------------------------------
# 官方数值正确性
# --------------------------------------------------------------------------


def test_twelve_records_across_two_sources(built):
    candidates, evidences, summary = built
    assert len(candidates) == 12
    assert len(evidences) == 2
    assert summary["by_source"] == {QWEN3: 9, CODER: 3}
    assert summary["record_count"] == 12


def test_all_records_verified_against_archived_source(built):
    """12 条全部通过与存档原文的逐字段机器核对，且比对项数非空。"""
    candidates, _, summary = built
    assert summary["verified_against_source"] == 12
    for c in candidates:
        v = c.verification
        assert v.source_block_found, c.record_id
        assert v.command_found, c.record_id
        assert v.compared_fields, c.record_id
        assert not v.mismatches, (c.record_id, v.mismatches)
        assert v.verified_against_source, c.record_id


def test_task_book_reference_values_match_source(built):
    """任务书 §3.3 的 6 条参考断言逐项核对（Decimal 精确比较）。"""
    candidates, _, _ = built
    by_id = {c.record_id: c for c in candidates}
    for record_id, (conc, in_tps, out_tps, ttft, tpot) in TASK_BOOK_EXPECTATIONS.items():
        c = by_id[record_id]
        assert c.configured_max_concurrency == conc, record_id
        assert c.metrics["input_throughput_tok_s"] == Decimal(in_tps), record_id
        assert c.metrics["output_throughput_tok_s"] == Decimal(out_tps), record_id
        assert c.metrics["ttft_p99_ms"] == Decimal(ttft), record_id
        assert c.metrics["tpot_p99_ms"] == Decimal(tpot), record_id


def test_remaining_six_records_also_checked(built):
    """其余 6 条同样逐字段核对过，不存在"只核对示例"的情况。"""
    candidates, _, _ = built
    others = [c for c in candidates if c.record_id not in TASK_BOOK_EXPECTATIONS]
    assert len(others) == 6
    for c in others:
        assert c.verification.verified_against_source, c.record_id
        for json_key, console_key in METRIC_MAP.items():
            assert console_key in c.metrics or c.record_id, (c.record_id, json_key)


def test_candidate_metrics_match_candidate_file(built):
    """原文核对值与候选文件值一致（差异会被 verification.mismatches 抓住）。"""
    candidates, _, _ = built
    for c in candidates:
        for key, value in c.metrics_from_candidate_file.items():
            assert c.metrics.get(key) == value, (c.record_id, key)


def test_archived_snapshot_matches_declared_hashes(built, root: Path):
    _, _, summary = built
    for source_id, info in summary["sources"].items():
        assert info["sha256_matches_spec"], source_id
        assert info["git_blob_sha_matches_declared"], source_id
        blob = git_blob_sha1(root / info["evidence_file"])
        assert len(blob) == 40 and blob == info["snapshot_git_blob_sha"]


def test_coder_source_has_unlisted_result_blocks(built):
    """原文还有 6 个结果块（NVIDIA FP8/NVFP4）不在本批次输入内 —— 只登记不建候选。"""
    _, _, summary = built
    coder = summary["sources"][CODER]
    assert coder["unlisted_result_block_count"] == 6
    assert set(coder["unlisted_sections_with_results"]) >= {"5.1.2.1", "5.1.2.2"}


# --------------------------------------------------------------------------
# 幂等
# --------------------------------------------------------------------------


def test_import_is_idempotent(candidate_db, root: Path):
    for _ in range(2):
        import_sglang_candidates(root / INCOMING, candidate_db, root=root)
    db, repo = open_db(candidate_db)
    try:
        with db.session() as session:
            rows = repo.list_numeric_candidates(session)
            assert len(rows) == 12
            assert len({r.candidate_id for r in rows}) == 12
            assert len({r.record_id for r in rows}) == 12
    finally:
        db.dispose()


def test_cli_import_candidates_reports_counts(candidate_db):
    r = runner.invoke(
        app,
        [
            "import-sglang-candidates",
            "--file",
            INCOMING,
            "--db",
            candidate_db,
            "--root",
            ".",
        ],
    )
    assert r.exit_code == 0, r.output
    assert "已导入 12 条官方数值候选" in r.output
    assert "原文逐字段核对通过: 12/12" in r.output


def test_duplicate_record_ids_in_input_rejected(tmp_path: Path, root: Path):
    payload = json.loads((root / INCOMING).read_text(encoding="utf-8"))
    payload["records"].append(dict(payload["records"][0]))
    payload["records_count"] = len(payload["records"])
    bad = tmp_path / "dup.json"
    bad.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(SglangCookbookError, match="record_id 不唯一"):
        load_incoming(bad)


def test_unknown_source_id_rejected(tmp_path: Path, root: Path):
    payload = json.loads((root / INCOMING).read_text(encoding="utf-8"))
    payload["records"][0]["source_id"] = "not_declared"
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(SglangCookbookError, match="source_id"):
        load_incoming(bad)


# --------------------------------------------------------------------------
# 来源完整性 / 缺失时不得伪装成已核实
# --------------------------------------------------------------------------


def test_missing_snapshot_means_not_verified(tmp_path: Path, root: Path):
    """快照缺失时：不报错、但候选不得声称已核对，并写明原因。"""
    work = tmp_path / "proj"
    (work / "data" / "incoming").mkdir(parents=True)
    shutil.copy(root / INCOMING, work / "data" / "incoming" / Path(INCOMING).name)
    (work / "data" / "raw" / "official").mkdir(parents=True)
    candidates, evidences, summary = build_numeric_candidates(
        work / "data" / "incoming" / Path(INCOMING).name, root=work
    )
    assert candidates == []
    assert evidences == []
    for info in summary["sources"].values():
        assert info["snapshot_present"] is False
        assert info["sha256_matches_spec"] is False
        assert "未复核原文" in info["reason"]


def test_tampered_snapshot_is_detected(tmp_path: Path, root: Path):
    work = tmp_path / "proj"
    shutil.copytree(root / "data", work / "data")
    target = work / "data" / "raw" / "official" / "sglang_cookbook_qwen3_2026-10-15.mdx"
    target.write_text(target.read_text(encoding="utf-8") + "\n<!-- tampered -->\n", encoding="utf-8")
    _, _, summary = build_numeric_candidates(
        work / "data" / "incoming" / Path(INCOMING).name, root=work
    )
    info = summary["sources"][QWEN3]
    assert info["sha256_matches_spec"] is False
    assert info["git_blob_sha_matches_declared"] is False
    assert "不一致" in info["reason"]


def test_git_blob_sha_differs_from_file_sha256(root: Path):
    """Blob SHA（Git 对象 SHA-1）与 SHA256（文件哈希）不是同一个东西，必须分开记录。"""
    path = root / SOURCE_SPECS[QWEN3]["evidence_file"]
    blob = git_blob_sha1(path)
    digest = sha256_file(path)
    assert blob != digest
    assert len(blob) == 40 and len(digest) == 64


def test_blob_sha_helper_matches_git_object_format(root: Path, tmp_path: Path):
    """``git hash-object`` 的结果必须与本实现的算法一致（同一文件）。"""
    import subprocess

    path = root / "README.md"
    expected = subprocess.run(
        ["git", "hash-object", str(path)], capture_output=True, text=True, check=True
    ).stdout.strip()
    assert git_blob_sha1(path) == expected


# --------------------------------------------------------------------------
# 缺失字段：不得补 0 / 不得猜测
# --------------------------------------------------------------------------


def test_model_revision_stays_null_and_is_registered(built):
    candidates, _, _ = built
    for c in candidates:
        assert c.model_revision is None
        assert "model_revision" in c.missing_fields
        assert "model_revision_not_published" in c.quality_flags


def test_missing_metrics_are_absent_not_zero(built):
    """来源没给的指标一律不出现在 metrics 里，绝不以 0 占位。"""
    candidates, _, _ = built
    for c in candidates:
        assert "physical_cache_hit_rate" not in c.metrics
        assert "billable_cached_input_ratio" not in c.metrics
        assert "e2e_p99_ms" not in c.metrics or c.source_id == CODER
        for key, value in c.metrics.items():
            assert value != 0 or key in (
                "total_input_vision_tokens",
                "peak_concurrent_requests",
            ), (c.record_id, key)


def test_coder_precision_only_completed_where_documented(built):
    """精度来自来源声明：Coder 源码文件写明 FP8，Qwen3 未写 -> 保持空并登记。"""
    candidates, _, _ = built
    coder = [c for c in candidates if c.source_id == CODER]
    qwen3 = [c for c in candidates if c.source_id == QWEN3]
    assert all(c.model_precision == "FP8" for c in coder)
    assert all(c.model_precision is None for c in qwen3)
    assert all("weight_precision" in c.missing_fields for c in qwen3)


# --------------------------------------------------------------------------
# 指标语义（M8）
# --------------------------------------------------------------------------


def test_configured_and_observed_concurrency_coexist(built):
    """配置并发 100 与观测并发 90.5 必须并存，不互相覆盖。"""
    candidates, _, _ = built
    c = next(x for x in candidates if x.record_id == f"{QWEN3}:5.1.1.3")
    assert c.configured_max_concurrency == 100
    assert c.metrics["observed_concurrency"] == Decimal("90.50")
    assert c.metrics["configured_max_concurrency"] == Decimal("100")


def test_target_length_not_equal_to_actual_average(built):
    """目标 ISL/OSL 是参数，不是实际平均长度。"""
    candidates, _, _ = built
    c = next(x for x in candidates if x.record_id == f"{QWEN3}:5.1.1.2")
    assert (c.input_len_target, c.output_len_target) == (1000, 1000)
    actual_in = c.metrics["total_input_tokens"] / c.metrics["success_count"]
    assert actual_in != Decimal(c.input_len_target)
    assert actual_in == Decimal("39668") / Decimal("80")


def test_tpot_p99_is_not_itl_p99(built):
    """TPOT 与 ITL 是两组独立字段，映射各自来自自己的原始标签。

    注意：二者取值**允许相等**（单请求场景下 Mean TPOT 与 Mean ITL 实测都是 17.06），
    因此这里校验的是"来源标签溯源"而不是"取值不相等"——后者会产生误报。
    """
    candidates, _, _ = built
    for c in candidates:
        assert "tpot_p99_ms" in c.metrics
        assert "itl_p99_ms" in c.metrics
        # 原始标签带单位后缀（如 "P99 TPOT (ms)"），以前缀判定来源行
        assert c.raw_labels["tpot_p99_ms"].lower().startswith("p99 tpot")
        assert c.raw_labels["itl_p99_ms"].lower().startswith("p99 itl")
        assert "m.latency.tpot.p99" in c.metric_definition_ids.values()
        # 溯源检查必须干净（含"允许相等"的情形）
        assert not c.semantic_warnings, (c.record_id, c.semantic_warnings)


def test_label_provenance_detects_wrong_source_line():
    """把某个值的原始标签标错 -> 溯源检查必须报出来。"""
    from llm_capacity_pricing.engines.metric_semantics import label_provenance_issues

    issues = label_provenance_issues(
        {"tpot_p99_ms": Decimal("15.85")}, {"tpot_p99_ms": "P99 ITL"}
    )
    assert issues and "P99 ITL" in issues[0]
    missing = label_provenance_issues({"ttft_p99_ms": Decimal("1")}, {})
    assert missing and "缺少原始标签" in missing[0]


def test_retokenized_count_does_not_overwrite_total(built):
    """控制台的 '(retokenized)' 计数不得覆盖 total_output_tokens。"""
    candidates, _, _ = built
    c = next(x for x in candidates if x.record_id == f"{QWEN3}:5.1.1.1")
    assert c.metrics["total_output_tokens"] == Decimal("4210")
    assert c.metrics["total_output_tokens_retokenized"] == Decimal("4206")


def test_metric_definition_ids_attached(built):
    candidates, _, _ = built
    c = candidates[0]
    assert c.metric_definition_ids["input_throughput_tok_s"] == "m.throughput.cluster.input"
    assert c.metric_definition_ids["configured_max_concurrency"] == "m.concurrency.configured"
    assert c.metric_semantics_version == "1.0"


def test_throughput_consistency_within_tolerance(built):
    """吞吐 ≈ 累计 token / 时长，12 条全部落在 1e-3 容差内。"""
    candidates, _, _ = built
    for c in candidates:
        checks = c.throughput_consistency
        assert checks, c.record_id
        for check in checks:
            assert check["within_tolerance"] is True, (c.record_id, check)
            assert Decimal(check["relative_error"]) <= THROUGHPUT_TOLERANCE


def test_throughput_consistency_detects_unit_error():
    """量纲错误（少乘 60）必须被抓出来。"""
    metrics = {
        "duration_s": Decimal("43.56"),
        "total_input_tokens": Decimal("6101"),
        "input_throughput_tok_s": Decimal("140.07") / Decimal(60),
    }
    result = throughput_consistency(metrics)
    assert result.ok is False
    assert "input_throughput_vs_tokens_over_duration" in result.violations


def test_label_provenance_allows_equal_tpot_and_itl_values():
    """取值相等不算问题（单请求下本就相等），只要来源标签各自正确。"""
    from llm_capacity_pricing.engines.metric_semantics import label_provenance_issues

    metrics = {"tpot_p99_ms": Decimal("10.15"), "itl_p99_ms": Decimal("10.15")}
    assert label_provenance_issues(
        metrics, {"tpot_p99_ms": "P99 TPOT", "itl_p99_ms": "P99 ITL"}
    ) == []


def test_cross_backend_comparability_requires_same_version():
    assert cross_backend_comparable("0.5.6", "0.5.6")
    assert not cross_backend_comparable("0.5.6", "0.5.7")
    assert not cross_backend_comparable(None, None)


def test_metric_dictionary_doc_matches_code(root: Path):
    """文档里的口径表必须与代码生成的一致，防止文档漂移。"""
    doc = (root / "docs" / "benchmark_metric_dictionary.md").read_text(encoding="utf-8")
    for line in render_dictionary_markdown().splitlines():
        assert line in doc, line
    assert len(FIELD_DEFINITIONS) >= 18


# --------------------------------------------------------------------------
# 边界数值：inf 不进入 Decimal
# --------------------------------------------------------------------------


def test_inf_never_enters_decimal_metrics(built):
    candidates, _, _ = built
    for c in candidates:
        assert c.traffic_request_rate_raw == "inf"
        assert c.symbols["traffic_request_rate"] == "inf"
        # 不得以任何形式混进指标字典或财务字段
        assert "traffic_request_rate" not in c.metrics
        assert "request_rate_rps" not in c.metrics
        for value in c.metrics.values():
            assert value.is_finite()


def test_schema_rejects_non_finite_metrics(built):
    """``inf``/``NaN`` 不得进入 metrics（Schema 层拦截，另有 ``_guard`` 兜底）。"""
    from pydantic import ValidationError

    from llm_capacity_pricing.schemas.official import SglangNumericCandidate

    c = built[0][0]
    payload = c.model_dump(mode="python")
    payload["metrics"] = {**payload["metrics"], "request_rate_rps": Decimal("Infinity")}
    with pytest.raises(ValidationError) as exc:
        SglangNumericCandidate.model_validate(payload)
    assert "finite" in str(exc.value)


def test_tampered_source_values_are_reported_as_mismatch(tmp_path: Path, root: Path):
    """人为改掉候选文件里的数值 -> 必须报 mismatch，而不是静默接受。"""
    payload = json.loads((root / INCOMING).read_text(encoding="utf-8"))
    payload["records"][0]["metrics"]["input_token_throughput_tps"] = 999.99
    work = tmp_path / "proj"
    shutil.copytree(root / "data", work / "data")
    bad = work / "data" / "incoming" / Path(INCOMING).name
    bad.chmod(0o644)  # 只读输入副本权限为 444，测试里需先放开
    bad.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    candidates, _, summary = build_numeric_candidates(bad, root=work)
    assert summary["verified_against_source"] == 11
    tampered = next(c for c in candidates if c.record_id == f"{QWEN3}:5.1.1.1")
    assert not tampered.verification.verified_against_source
    assert any("input_token_throughput_tps" in m for m in tampered.verification.mismatches)
    # 入库时以**原文**为准：被篡改的候选值不会进入 metrics
    assert tampered.metrics["input_throughput_tok_s"] == Decimal("140.07")
    assert tampered.metrics_from_candidate_file["input_throughput_tok_s"] == Decimal("999.99")


def test_command_conflict_is_recorded(built):
    """正文写 ShareGPT、命令为 --dataset-name random —— 冲突必须留痕。"""
    candidates, _, _ = built
    conflicts = [c for c in candidates if c.verification.command_conflicts]
    assert conflicts, "应当记录正文叙述与命令的冲突"
    assert any(
        "narrative_conflicts_with_benchmark_command" in c.quality_flags for c in conflicts
    )


# --------------------------------------------------------------------------
# 业务数据隔离
# --------------------------------------------------------------------------


def test_candidates_do_not_enter_benchmark_records(candidate_db):
    db, repo = open_db(candidate_db)
    try:
        with db.session() as session:
            assert repo.list_benchmarks(session) == []
            assert repo.counts(session).benchmark_record == 0
    finally:
        db.dispose()


def test_business_calculation_unaffected_by_candidates(full_db, root: Path):
    before_results, before_meta = calculate_all(full_db, mode="legacy_full_load", persist=False)
    import_sglang_candidates(root / INCOMING, full_db, root=root)
    after_results, after_meta = calculate_all(full_db, mode="legacy_full_load", persist=False)
    assert len(before_results) == len(after_results) == 32
    assert after_meta["business_record_count"] == 32
    # 默认口径下纳入的测试记录为 0，被排除的 fixture 记录仍为 4 条
    assert after_meta["test_record_count"] == 0
    assert len(after_meta["excluded_test_records"]) == 4
    assert {r.benchmark_record_id for r in before_results} == {
        r.benchmark_record_id for r in after_results
    }


def test_scenario_counts_unchanged_after_candidate_import(full_db, root: Path):
    """敏感性仍为 32×5=160；只有显式 include_test_data 才是 36×5=180。"""
    import_sglang_candidates(root / INCOMING, full_db, root=root)
    assert len(build_sensitivity(full_db, persist=False)) == 160
    assert len(build_sensitivity(full_db, persist=False, include_test_data=True)) == 180


# --------------------------------------------------------------------------
# SLA 门禁（M9）
# --------------------------------------------------------------------------


def test_no_candidate_can_evaluate_sla(built):
    candidates, _, _ = built
    assert all(c.can_evaluate_sla is False for c in candidates)
    assert all(c.eligible_for_sla_guarantee is False for c in candidates)
    assert all("sla_conclusion_blocked" in c.quality_flags for c in candidates)
    for c in candidates:
        assert any("sla_not_evaluable" in n for n in c.applicability_notes)


def test_candidates_can_be_referenced_as_official_numbers(built):
    candidates, _, _ = built
    assert all(c.can_reference_performance for c in candidates)


def test_sla_blocked_even_with_matching_target_missing_revision(built):
    """关键项（model revision）不符 -> 即使其它都匹配也必须阻断。"""
    c = built[0][0]
    app = assess_applicability(
        source_locatable=True,
        model_name=c.model_name,
        model_revision=None,  # 记录缺 revision
        model_precision=c.model_precision,
        hardware_model=c.hardware_model,
        deployment_gpu_count=c.deployment_gpu_count,
        framework_version=c.framework_version,
        workload_defined=True,
        metrics=c.metrics,
        sla_requirement=SlaRequirement(
            model_name=c.model_name,
            model_revision="rev-123",  # 目标有 revision，记录没有
            hardware_model=c.hardware_model,
            deployment_gpu_count=c.deployment_gpu_count,
            framework_version=c.framework_version,
            parallel_config="TP8",
            workload_profile="random-1000-1000",
        ),
        price=PriceContext(currency=CURRENCY_UNCONFIRMED, currency_confirmed=False),
    )
    assert app.can_evaluate_sla is False
    assert any("record_missing:model_revision" in n for n in app.applicability_notes)


def test_sla_evaluable_when_everything_matches_shows_rule_is_not_hardcoded():
    """把全部条件凑齐（含 revision/精度/拓扑/负载）后可以评估 —— 证明是条件判断。"""
    app = assess_applicability(
        source_locatable=True,
        model_name="Qwen/Qwen3-235B-A22B-Instruct-2507",
        model_revision="rev-abc",
        model_precision="BF16",
        hardware_model="NVIDIA B200",
        deployment_gpu_count=8,
        framework_version="0.5.6",
        workload_defined=True,
        metrics={"ttft_p99_ms": Decimal("100"), "tpot_p99_ms": Decimal("10")},
        sla_requirement=SlaRequirement(
            model_name="Qwen/Qwen3-235B-A22B-Instruct-2507",
            model_revision="rev-abc",
            model_precision="BF16",
            hardware_model="NVIDIA B200",
            deployment_gpu_count=8,
            framework_version="0.5.6",
            parallel_config="TP8",
            workload_profile="random-1000-1000",
        ),
        price=PriceContext(currency="CNY", currency_confirmed=True, hardware_cost_bound=True),
    )
    assert app.can_evaluate_sla is True


def test_sla_blocked_without_concrete_target_requirement():
    app = assess_applicability(
        source_locatable=True,
        model_name="m",
        model_revision="r",
        model_precision="p",
        hardware_model="hw",
        deployment_gpu_count=8,
        framework_version="v",
        workload_defined=True,
        metrics={"ttft_p99_ms": Decimal("1"), "tpot_p99_ms": Decimal("1")},
        sla_requirement=None,
        price=None,
    )
    assert app.can_evaluate_sla is False
    assert any("no_concrete_target_requirement" in n for n in app.applicability_notes)


def test_synthetic_fixture_never_passes_any_gate():
    """synthetic fixture 不得被当成业务证据（三条门禁全 False）。"""
    app = assess_applicability(
        source_locatable=True,
        model_name="m",
        model_revision="r",
        model_precision="p",
        hardware_model="hw",
        deployment_gpu_count=8,
        framework_version="v",
        workload_defined=True,
        metrics={"ttft_p99_ms": Decimal("1"), "tpot_p99_ms": Decimal("1")},
        sla_requirement=None,
        price=PriceContext(currency="CNY", currency_confirmed=True, hardware_cost_bound=True),
        measurement_origin_is_official=False,
    )
    assert not app.can_reference_performance
    assert not app.can_evaluate_sla
    assert not app.can_calculate_pricing


# --------------------------------------------------------------------------
# 报价门禁（M9 / M10）
# --------------------------------------------------------------------------


def test_pricing_gate_blocked_without_confirmed_commercial_context(built):
    candidates, _, _ = built
    assert all(c.can_calculate_pricing is False for c in candidates)
    assert all(c.commercial_quote_allowed is False for c in candidates)
    assert all("not_a_commercial_quote" in c.quality_flags for c in candidates)


def test_price_context_blockers_enumerated():
    ctx = PriceContext(currency=CURRENCY_UNCONFIRMED, currency_confirmed=False)
    blockers = ctx.blockers()
    assert "currency_not_confirmed" in blockers
    assert "hardware_cost_bound" in blockers or "hardware_cost_not_bound" in blockers
    assert "billing_rules_not_declared" in blockers


def test_schema_rejects_commercial_quote_flag(built):
    from pydantic import ValidationError

    from llm_capacity_pricing.schemas.official import SglangNumericCandidate

    payload = built[0][0].model_dump(mode="python")
    payload["commercial_quote_allowed"] = True
    with pytest.raises(ValidationError, match="commercial_quote_allowed"):
        SglangNumericCandidate.model_validate(payload)


def test_schema_rejects_sla_flag_without_revision(built):
    from pydantic import ValidationError

    from llm_capacity_pricing.schemas.official import SglangNumericCandidate

    payload = built[0][0].model_dump(mode="python")
    payload["can_evaluate_sla"] = True
    with pytest.raises(ValidationError, match="model_revision"):
        SglangNumericCandidate.model_validate(payload)


# --------------------------------------------------------------------------
# 覆盖摘要与 CLI
# --------------------------------------------------------------------------


def test_coverage_summary_reports_both_families(candidate_db, root: Path):
    from llm_capacity_pricing.services import import_official_candidates

    import_official_candidates(root / "data" / "sources" / "official_manifest.yaml",
                               candidate_db, root=root)
    info = official_coverage_summary(candidate_db)
    assert info["manifest_candidates"] == 6
    assert info["numeric_candidates"] == 12
    assert info["candidate_count"] == 18
    assert info["by_family"] == {"manifest_partial_evidence": 6, "sglang_numeric_cookbook": 12}
    assert info["verified_count"] == 0
    assert info["numeric_verified_against_source"] == 12
    assert info["numeric_missing_model_revision"] == 12
    assert info["gates"]["can_reference_performance"] == 12
    assert info["gates"]["can_evaluate_sla"] == 0
    assert info["gates"]["can_calculate_pricing"] == 0


def test_cli_official_coverage_shows_gates(candidate_db):
    r = runner.invoke(app, ["official-coverage", "--db", candidate_db])
    assert r.exit_code == 0, r.output
    assert '"numeric_candidates": 12' in r.output
    assert '"can_evaluate_sla": 0' in r.output


def test_cli_missing_file_fails_cleanly(empty_db):
    r = runner.invoke(
        app,
        [
            "import-sglang-candidates",
            "--file",
            "data/incoming/does_not_exist.json",
            "--db",
            empty_db,
            "--root",
            ".",
        ],
    )
    assert r.exit_code != 0
    assert "候选文件不存在" in r.output
