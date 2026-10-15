"""V1.2 加固任务的测试（F1–F8）。

覆盖：
* F1 内部数据不外泄（敏感数值扫描 + 脱敏样本可用性）
* F2 来源校验 Fail-Closed（见 test_sglang_numeric_candidates.py 的 F2 段落）
* F3 SLA 门禁：结构化部署配置 + 工作负载逐维匹配
* F4 经济性门禁：吞吐/单位/成本绑定/单价绑定/商业条件
* F5 参数类型化比较 + 权威命令溯源
* F6 数据库字段与 Schema 一致（含 True/False/None）
* F7 跨框架可比性
* F8 脱敏 Golden Fixture（公开克隆无需真实数据即可验证核心计算）
"""

from __future__ import annotations

import json
import subprocess
from decimal import Decimal
from pathlib import Path

import pytest

from llm_capacity_pricing.engines.applicability import (
    ALLOWED_THROUGHPUT_UNITS,
    LEGACY_ASSUMPTIONS,
    DeploymentConfig,
    PriceContext,
    SlaRequirement,
    WorkloadProfile,
    assess_applicability,
    assess_pricing_readiness,
)
from llm_capacity_pricing.engines.metric_semantics import (
    BackendProfile,
    assess_comparability,
    cross_backend_comparable,
)
from llm_capacity_pricing.importers.sglang_cookbook import (
    PARAM_SPECS,
    ParamSpec,
    build_numeric_candidates,
    compare_param,
    parse_cli_args,
)
from llm_capacity_pricing.services import (
    calculate_all,
    import_excel_into_db,
    import_sglang_candidates,
    init_db,
    open_db,
)

INCOMING = "data/incoming/sglang_official_benchmark_candidates_20261015.json"

#: 内部特征串放在**不入库**的本地文件里（把真实数值写进仓库等于重新公开它们）。
#: 公开克隆没有该文件 -> 只做忽略规则检查（本来也没有内部值可保护）。
SENSITIVE_PATTERN_FILE = "data/private/sensitive_patterns.txt"


def _sensitive_patterns(root: Path) -> list[str]:
    path = root / SENSITIVE_PATTERN_FILE
    if not path.exists():
        return []
    return [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


# --------------------------------------------------------------------------
# F1：内部数据不外泄
# --------------------------------------------------------------------------


def test_no_internal_values_in_tracked_files(root: Path):
    """F1/F8：已跟踪文件中不得出现真实业务数值（可作 CI 提交检查）。

    特征表来自本地 ``data/private/sensitive_patterns.txt``（不入库）；
    公开克隆没有它，本用例退化为"记录跳过原因"的报告（用 assert 保护规则仍生效）。
    """
    patterns = _sensitive_patterns(root)
    tracked = subprocess.run(
        ["git", "ls-files"], cwd=root, capture_output=True, text=True, check=True
    ).stdout.split()
    offenders: dict[str, list[str]] = {}
    if patterns:
        for rel in tracked:
            path = root / rel
            if not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:  # pragma: no cover
                continue
            hits = [p for p in patterns if p in text]
            if hits:
                offenders[rel] = hits
    assert not offenders, f"以下已跟踪文件含内部数值: {offenders}"
    # 无论如何：内部产物目录不得被跟踪
    tracked_set = set(tracked)
    assert not any(p.startswith("data/exports/") for p in tracked_set)
    assert not any(p.startswith("data/private/") for p in tracked_set)


def test_internal_artifacts_are_gitignored(root: Path):
    """/data/exports 与 data/private 必须被忽略；脱敏样本必须可提交。"""
    def ignored(rel: str) -> bool:
        return (
            subprocess.run(
                ["git", "check-ignore", "-q", rel], cwd=root
            ).returncode
            == 0
        )

    assert ignored("data/exports/legacy_results.json")
    assert ignored("data/private/row2_oracle.json")
    assert not ignored("data/examples/public_sample_expected.json")
    assert not ignored("data/raw/official/NOTICE.md")


def test_public_sample_is_marked_synthetic(root: Path):
    doc = json.loads(
        (root / "data" / "examples" / "public_sample_expected.json").read_text(encoding="utf-8")
    )
    assert doc["data_kind"] == "synthetic_public_sample"
    assert "不是真实业务数据" in doc["notice"]
    for row in doc["rows"]:
        assert row["model_name"].startswith("EXAMPLE-")
        assert row["gpu_model"].startswith("EXAMPLE-")


# --------------------------------------------------------------------------
# F8：脱敏 Golden Fixture
# --------------------------------------------------------------------------


def test_sample_workbook_imports_and_matches_expected(tmp_path: Path, sample_excel_path):
    """公开样本走完整导入路径，并对**手算**期望值逐项校验。"""
    db = str(tmp_path / "sample.db")
    init_db(db)
    report = import_excel_into_db(sample_excel_path, db)
    counts = report["sheet_rows"]
    assert counts == {"GPU价格": 2, "模型价格": 2, "部署效能": 3}

    results, meta = calculate_all(db, mode="legacy_full_load", persist=False)
    assert len(results) == 3 and meta["business_record_count"] == 3
    by_id = {r.benchmark_record_id: r for r in results}
    expected = json.loads(
        (sample_excel_path.parent / "public_sample_expected.json").read_text(encoding="utf-8")
    )
    for index, row in enumerate(expected["rows"]):
        res = by_id[f"excel_部署效能_{index + 2}"]
        assert res.input_revenue_10k_per_gpu_year == Decimal(row["expected_input_revenue_10k_per_gpu_year"])
        assert res.output_revenue_10k_per_gpu_year == Decimal(row["expected_output_revenue_10k_per_gpu_year"])
        assert res.total_revenue_10k_per_gpu_year == Decimal(row["expected_total_revenue_10k_per_gpu_year"])
        assert res.revenue_cost_ratio == Decimal(row["expected_revenue_cost_ratio"])
        assert res.breakeven_monetization_ratio == Decimal(
            row["expected_breakeven_monetization_ratio"]
        )


def test_sample_sensitivity_counts(tmp_path: Path, sample_excel_path):
    from llm_capacity_pricing.services import build_sensitivity

    db = str(tmp_path / "sample.db")
    init_db(db)
    import_excel_into_db(sample_excel_path, db)
    assert len(build_sensitivity(db, persist=False)) == 3 * 5


# --------------------------------------------------------------------------
# F3：SLA 门禁的结构化匹配
# --------------------------------------------------------------------------


def _req(**overrides) -> SlaRequirement:
    base = dict(
        model_name="M",
        model_revision="rev-1",
        model_precision="BF16",
        hardware_model="HW-A",
        deployment_gpu_count=8,
        framework_version="0.5.6",
        deployment=DeploymentConfig(tp_size=8, dp_size=1, ep_size=1, gpu_count=8, mode="colocated"),
        workload=WorkloadProfile(
            input_len=1000,
            output_len=1000,
            arrival_mode="unbounded",
            max_concurrency=64,
            dataset_name="random",
            statistics_method="bench-0.5.6",
        ),
    )
    base.update(overrides)
    return SlaRequirement(**base)


def _assess(record_deploy: DeploymentConfig, record_workload: WorkloadProfile, **req_overrides):
    return assess_applicability(
        source_locatable=True,
        model_name="M",
        model_revision="rev-1",
        model_precision="BF16",
        hardware_model="HW-A",
        deployment_gpu_count=record_deploy.gpu_count,
        framework_version="0.5.6",
        workload_defined=True,
        metrics={"ttft_p99_ms": Decimal("100"), "tpot_p99_ms": Decimal("10")},
        sla_requirement=_req(**req_overrides),
        record_deployment=record_deploy,
        record_workload=record_workload,
        price=PriceContext(currency="CNY", currency_confirmed=True, cost_scope_confirmed=True, billing_rules_declared=True, price_valid_period_declared=True),
    )


MATCHING_DEPLOY = DeploymentConfig(tp_size=8, dp_size=1, ep_size=1, gpu_count=8, mode="colocated")
MATCHING_WORKLOAD = WorkloadProfile(
    input_len=1000,
    output_len=1000,
    arrival_mode="unbounded",
    max_concurrency=64,
    dataset_name="random",
    statistics_method="bench-0.5.6",
)


def test_f3_exact_match_is_evaluable():
    app = _assess(MATCHING_DEPLOY, MATCHING_WORKLOAD)
    assert app.can_evaluate_sla is True
    assert any("不代表 SLA 已达标" in n for n in app.applicability_notes)


@pytest.mark.parametrize(
    "field,value,reason",
    [
        ("tp_size", 4, "mismatch:tp_size"),
        ("dp_size", 2, "mismatch:dp_size"),
        ("ep_size", 2, "mismatch:ep_size"),
        ("gpu_count", 16, "mismatch:gpu_count"),
        ("mode", "prefill_decode_disaggregated", "mismatch:mode"),
    ],
)
def test_f3_deployment_dimension_mismatch_blocks(field, value, reason):
    record = DeploymentConfig(**{**MATCHING_DEPLOY.__dict__, field: value})
    app = _assess(record, MATCHING_WORKLOAD)
    assert app.can_evaluate_sla is False
    assert any(reason in n for n in app.applicability_notes), app.applicability_notes


def test_f3_record_missing_ep_blocks():
    """记录侧缺字段必须给出 record_missing 原因，而不是"需求侧非空"就放行。"""
    record = DeploymentConfig(tp_size=8, dp_size=1, ep_size=None, gpu_count=8, mode="colocated")
    app = _assess(record, MATCHING_WORKLOAD)
    assert app.can_evaluate_sla is False
    assert any("record_missing:ep_size" in n for n in app.applicability_notes)


@pytest.mark.parametrize(
    "field,value,reason",
    [
        ("input_len", 2000, "mismatch:input_len"),
        ("output_len", 2000, "mismatch:output_len"),
        ("arrival_mode", "request_rate", "mismatch:arrival_mode"),
        ("max_concurrency", 16, "mismatch:max_concurrency"),
        ("dataset_name", "sharegpt", "mismatch:dataset_name"),
        ("statistics_method", "bench-0.5.7", "mismatch:statistics_method"),
    ],
)
def test_f3_workload_dimension_mismatch_blocks(field, value, reason):
    record = WorkloadProfile(**{**MATCHING_WORKLOAD.__dict__, field: value})
    app = _assess(MATCHING_DEPLOY, record)
    assert app.can_evaluate_sla is False
    assert any(reason in n for n in app.applicability_notes), app.applicability_notes


def test_f3_record_missing_statistics_method_blocks():
    record = WorkloadProfile(**{**MATCHING_WORKLOAD.__dict__, "statistics_method": None})
    app = _assess(MATCHING_DEPLOY, record)
    assert app.can_evaluate_sla is False
    assert any("record_missing:statistics_method" in n for n in app.applicability_notes)


def test_f3_requirement_without_structured_profiles_blocks():
    app = assess_applicability(
        source_locatable=True,
        model_name="M",
        model_revision="rev-1",
        model_precision="BF16",
        hardware_model="HW-A",
        deployment_gpu_count=8,
        framework_version="0.5.6",
        workload_defined=True,
        metrics={"ttft_p99_ms": Decimal("1"), "tpot_p99_ms": Decimal("1")},
        sla_requirement=SlaRequirement(model_name="M"),  # 只有模型名
        price=None,
    )
    assert app.can_evaluate_sla is False
    assert any("target_requirement_missing:deployment_config" in n for n in app.applicability_notes)
    assert any("target_requirement_missing:workload_profile" in n for n in app.applicability_notes)


def test_f3_official_candidates_stay_blocked(root: Path):
    """F3 第 6 条：12 条 partial_evidence 官方候选保持保守处理。"""
    candidates, _, _ = build_numeric_candidates(root / INCOMING, root=root)
    assert len(candidates) == 12
    assert all(c.can_evaluate_sla is False for c in candidates)
    assert all("sla_conclusion_blocked" in c.quality_flags for c in candidates)


# --------------------------------------------------------------------------
# F4：经济性门禁
# --------------------------------------------------------------------------


def _readiness(**overrides):
    base = dict(
        metrics={"input_tps_per_gpu": Decimal("1000"), "output_tps_per_gpu": Decimal("40")},
        throughput_unit="tok/s/gpu",
        token_unit_prices_present=True,
        cost_present=True,
        hardware_price_bound=True,
        hardware_model_matches=True,
        gpu_count_matches=True,
        token_price_bound=True,
        token_price_model_matches=True,
        price=PriceContext(currency="CNY", currency_confirmed=True, cost_scope_confirmed=True, billing_rules_declared=True, price_valid_period_declared=True),
    )
    base.update(overrides)
    return assess_pricing_readiness(**base)


def test_f4_complete_context_computes_but_never_quotes():
    r = _readiness()
    assert r.can_compute_theoretical is True
    assert r.can_issue_formal_quote is False
    assert r.blockers == []


def test_f4_missing_throughput_blocks_computation():
    r = _readiness(metrics={"input_tps_per_gpu": Decimal("1000"), "output_tps_per_gpu": None})
    assert r.can_compute_theoretical is False
    assert "throughput_missing:output_tps_per_gpu" in r.blockers


@pytest.mark.parametrize("unit,expected", [(None, "throughput_unit_not_declared"), ("tok/min", "throughput_unit_unrecognized:tok/min")])
def test_f4_throughput_unit_must_be_declared_and_recognized(unit, expected):
    r = _readiness(throughput_unit=unit)
    assert expected in r.blockers
    assert "tok/s/gpu" in ALLOWED_THROUGHPUT_UNITS


@pytest.mark.parametrize(
    "overrides,expected",
    [
        ({"hardware_price_bound": False}, "hardware_price_not_bound"),
        ({"hardware_model_matches": False}, "hardware_model_mismatch_with_benchmark"),
        ({"gpu_count_matches": False}, "deployment_gpu_count_mismatch_with_cost_row"),
        ({"token_price_bound": False}, "token_price_not_bound"),
        ({"token_price_model_matches": False}, "token_price_model_mismatch_with_benchmark"),
        ({"token_unit_prices_present": False}, "token_prices_missing"),
        ({"cost_present": False}, "gpu_cost_missing"),
    ],
)
def test_f4_binding_mismatches_block(overrides, expected):
    r = _readiness(**overrides)
    assert expected in r.blockers


def test_f4_business_conditions_blockers_listed():
    r = _readiness(price=PriceContext(currency="UNCONFIRMED", currency_confirmed=False))
    for expected in (
        "currency_not_confirmed",
        "cost_scope_unconfirmed",
        "price_valid_period_not_declared",
        "billing_rules_not_declared",
    ):
        assert expected in r.blockers


def test_f4_cache_hit_not_auto_converted(full_db):
    """F4 第 6 条：物理/原始命中率不得自动变成可计费缓存命中比例。"""
    results, _ = calculate_all(full_db, mode="legacy_full_load", persist=False)
    for r in results:
        assert r.cache_hit_rate_is_legacy_proxy is True
    db, repo = open_db(full_db)
    try:
        with db.session() as session:
            records = repo.list_benchmarks(session)
    finally:
        db.dispose()
    for rec in records:
        assert rec.billable_cached_input_ratio is None
        assert rec.physical_cache_hit_rate is None
        assert rec.cache_metric_semantics == "unknown"


def test_f4_legacy_results_carry_explicit_assumptions(full_db):
    results, _ = calculate_all(full_db, mode="legacy_full_load", persist=False)
    assert results
    for r in results:
        assert list(r.legacy_assumptions) == list(LEGACY_ASSUMPTIONS)
        assert r.commercial_quote_allowed is False
    scenario, _ = calculate_all(full_db, mode="scenario", utilization=Decimal("0.3"), persist=False)
    assert all(r.legacy_assumptions == [] for r in scenario)


def test_f4_gate_blockers_recorded_on_every_result(full_db):
    results, _ = calculate_all(full_db, mode="legacy_full_load", persist=False)
    assert all(r.pricing_gate_blockers for r in results)
    assert all("currency_not_confirmed" in r.pricing_gate_blockers for r in results)


# --------------------------------------------------------------------------
# F5：参数类型化比较与命令溯源
# --------------------------------------------------------------------------


def test_f5_string_param_is_actually_compared():
    """dataset_name 是字符串参数：以前会被静默跳过，现在必须参与比较。"""
    spec = next(s for s in PARAM_SPECS if s.field == "dataset_name")
    involved, ok, why = compare_param(spec, "random", {"--dataset-name": "sharegpt"})
    assert involved is True and ok is False and "dataset_name" in why
    involved, ok, _ = compare_param(spec, "random", {"--dataset-name": "RANDOM"})
    assert involved is True and ok is True  # 枚举大小写不敏感


def test_f5_numeric_and_symbol_params():
    conc = next(s for s in PARAM_SPECS if s.field == "configured_max_concurrency")
    assert compare_param(conc, 100, {"--max-concurrency": "100"})[1] is True
    assert compare_param(conc, 100, {"--max-concurrency": "16"})[1] is False
    rate = next(s for s in PARAM_SPECS if s.field == "traffic_request_rate")
    assert compare_param(rate, "inf", {"--request-rate": "inf"})[1] is True
    assert compare_param(rate, "inf", {"--request-rate": "10"})[1] is False


def test_f5_candidate_command_compared_against_source_command():
    """候选文件自带命令与原文命令参数不一致时必须报出来（F5 第 4 条）。"""
    spec = ParamSpec("num_prompts", "--num-prompts", "int")
    involved, ok, why = compare_param(
        spec, 10, {"--num-prompts": "10"}, secondary_args={"--num-prompts": "80"}
    )
    assert involved and not ok
    assert "候选文件命令" in why and "原文命令" in why


def test_f5_tampered_dataset_name_detected(tmp_path: Path, root: Path):
    import shutil

    payload = json.loads((root / INCOMING).read_text(encoding="utf-8"))
    payload["records"][0]["workload"]["dataset_name"] = "sharegpt"
    work = tmp_path / "proj"
    shutil.copytree(root / "data", work / "data")
    bad = work / "data" / "incoming" / Path(INCOMING).name
    bad.chmod(0o644)
    bad.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    candidates, _, summary = build_numeric_candidates(bad, root=work)
    assert summary["verified_against_source"] == 11
    tampered = next(c for c in candidates if c.record_id.endswith("5.1.1.1"))
    assert any("dataset_name" in m for m in tampered.verification.mismatches)


def test_f5_tampered_command_detected(tmp_path: Path, root: Path):
    import shutil

    payload = json.loads((root / INCOMING).read_text(encoding="utf-8"))
    payload["records"][0]["benchmark_command"] = payload["records"][0]["benchmark_command"].replace(
        "--num-prompts 10", "--num-prompts 999"
    )
    work = tmp_path / "proj"
    shutil.copytree(root / "data", work / "data")
    bad = work / "data" / "incoming" / Path(INCOMING).name
    bad.chmod(0o644)
    bad.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    candidates, _, summary = build_numeric_candidates(bad, root=work)
    assert summary["verified_against_source"] == 11
    tampered = next(c for c in candidates if c.record_id.endswith("5.1.1.1"))
    assert any("num_prompts" in m for m in tampered.verification.mismatches)


def test_f5_authoritative_command_comes_from_source(root: Path):
    """benchmark_command 取自官方原文；候选文件命令单列保存用于差异检查。"""
    candidates, _, _ = build_numeric_candidates(root / INCOMING, root=root)
    c = candidates[0]
    assert c.benchmark_command is not None
    assert "\\\n" in c.benchmark_command  # 原文里的多行续行写法
    assert "--model Qwen/Qwen3-235B-A22B-Instruct-2507" in c.benchmark_command
    assert c.benchmark_command_from_candidate_file is not None
    # 候选文件那份是压缩成单行的
    assert "\\\n" not in c.benchmark_command_from_candidate_file
    assert parse_cli_args(c.benchmark_command)["--num-prompts"] == "10"


# --------------------------------------------------------------------------
# F6：数据库字段与 Schema 一致
# --------------------------------------------------------------------------


@pytest.mark.parametrize("value", [True, False, None])
def test_f6_is_throughput_benchmark_roundtrip(tmp_path: Path, root: Path, value):
    """True/False/None 三种取值都要原样落库并读回（F6）。"""
    candidates, evidences, _ = build_numeric_candidates(root / INCOMING, root=root)
    patched = candidates[0].model_copy(update={"is_throughput_benchmark": value})
    db_path = str(tmp_path / "f6.db")
    db, repo = open_db(db_path)
    try:
        with db.session() as session:
            repo.upsert_evidence(session, evidences)
            repo.upsert_numeric_candidates(session, [patched])
            session.commit()
        with db.session() as session:
            raw = session.execute(
                __import__("sqlalchemy").text(
                    "select is_throughput_benchmark from official_benchmark_candidate "
                    "where candidate_id = :cid"
                ),
                {"cid": patched.candidate_id},
            ).scalar_one_or_none()
            stored = repo.list_numeric_candidates(session)
    finally:
        db.dispose()
    assert stored[0].is_throughput_benchmark is value
    # SQLite 把布尔存为 0/1，None 才是真正的 NULL
    if value is None:
        assert raw is None
    else:
        assert bool(raw) is value


def test_f6_stored_row_matches_schema_values(tmp_path: Path, root: Path):
    """逐字段核对：DB 行不得对 Schema 原始值做任何硬编码覆盖。"""
    candidates, evidences, _ = build_numeric_candidates(root / INCOMING, root=root)
    db_path = str(tmp_path / "f6b.db")
    db, repo = open_db(db_path)
    try:
        with db.session() as session:
            repo.upsert_evidence(session, evidences)
            repo.upsert_numeric_candidates(session, candidates)
            session.commit()
        with db.session() as session:
            from sqlalchemy import select

            from llm_capacity_pricing.storage.database import OfficialCandidateRow

            rows = {
                r.candidate_id: r
                for r in session.scalars(select(OfficialCandidateRow)).all()
            }
    finally:
        db.dispose()
    for c in candidates:
        row = rows[c.candidate_id]
        assert row.record_id == c.record_id
        assert row.manifest_id == c.source_id
        assert row.framework == c.framework
        assert row.hardware_binding == c.hardware_binding
        assert row.hardware_model == c.hardware_model
        assert row.deployment_gpu_count == c.deployment_gpu_count
        assert row.is_throughput_benchmark == c.is_throughput_benchmark
        assert row.quality_status == c.quality_status
        assert row.source_git_blob_sha == c.source_git_blob_sha
        assert json.loads(row.metrics_json) == {k: str(v) for k, v in c.metrics.items()}
        assert row.benchmark_tool == "sglang.bench_serving"


def test_f6_reimport_is_idempotent_and_keeps_values(tmp_path: Path, root: Path):
    db_path = str(tmp_path / "f6c.db")
    init_db(db_path)
    first = import_sglang_candidates(root / INCOMING, db_path, root=root)
    second = import_sglang_candidates(root / INCOMING, db_path, root=root)
    assert first["candidate_ids"] == second["candidate_ids"]
    handle, repo = open_db(db_path)
    try:
        with handle.session() as session:
            rows = repo.list_numeric_candidates(session)
    finally:
        handle.dispose()
    assert len(rows) == 12
    assert all(r.is_throughput_benchmark is True for r in rows)


# --------------------------------------------------------------------------
# F7：跨框架可比性
# --------------------------------------------------------------------------


def _profile(**overrides) -> BackendProfile:
    base = dict(
        framework="sglang",
        backend_version="0.5.6",
        metric_definition_ids=("m.throughput.cluster.input", "m.throughput.cluster.output"),
        statistics_method="bench-0.5.6",
        arrival_mode="unbounded",
        max_concurrency=1,
        input_len=1000,
        output_len=1000,
        dataset_name="random",
    )
    base.update(overrides)
    return BackendProfile(**base)


def test_f7_same_version_different_framework_not_comparable():
    result = assess_comparability(_profile(), _profile(framework="vllm"))
    assert result.comparable is False
    assert any("framework_differs" in r for r in result.reasons)
    assert "backend_version_equal_but_not_sufficient" in result.notes


def test_f7_different_metric_definitions_not_comparable():
    result = assess_comparability(
        _profile(), _profile(metric_definition_ids=("m.latency.tpot.p99",))
    )
    assert result.comparable is False
    assert any("metric_definitions_differ" in r for r in result.reasons)


@pytest.mark.parametrize(
    "overrides,reason",
    [
        ({"statistics_method": None}, "statistics_method_absent"),
        ({"statistics_method": "bench-0.5.7"}, "statistics_method_differs"),
        ({"arrival_mode": "request_rate"}, "arrival_mode_differs"),
        ({"max_concurrency": 16}, "max_concurrency_differs"),
        ({"input_len": 2000}, "input_len_differs"),
        ({"output_len": 100}, "output_len_differs"),
        ({"dataset_name": "sharegpt"}, "dataset_name_differs"),
    ],
)
def test_f7_workload_differences_block_comparability(overrides, reason):
    result = assess_comparability(_profile(), _profile(**overrides))
    assert result.comparable is False
    assert any(reason in r for r in result.reasons)


def test_f7_full_match_is_comparable():
    result = assess_comparability(_profile(), _profile(framework="SGLANG"))
    assert result.comparable is True and result.reasons == []
    assert cross_backend_comparable(_profile(), _profile())


def test_f7_version_only_profile_is_not_comparable():
    bare = BackendProfile(framework="sglang", backend_version="0.5.6")
    result = assess_comparability(bare, bare)
    assert result.comparable is False
    assert "metric_definitions_absent" in result.reasons
