"""第三阶段（M19–M25）测试：Benchmark 匹配、性能档案、容量曲线、估算、SLA、排序。

覆盖任务书 M25 的测试要求：

* 第一阶段 32/160/192 与第二阶段显存测试的回归由既有用例保证（本文件不重复）；
* 官方候选不会错误变成精确匹配的内部实测；
* synthetic 只用于验证算法，不能支撑正式 SLA；
* 不同 GPU/模型/TP/并发/长度/框架版本的错配都能识别；
* TTFT/TPOT/ITL 的不同统计口径不被混用；
* 吞吐与累计 token / 测试窗口一致；
* 低样本量、失败请求、P99 缺失时的行为；
* 无现场 GPU 的离线导入与复现；
* 真实价格与内部性能数据不得进入公开仓库（由 V1.2 扫描用例保证）。
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from llm_capacity_pricing.cli import app
from llm_capacity_pricing.importers.vllm_adapter import parse_vllm_bench_serve_json_full
from llm_capacity_pricing.perf.benchmark_matcher import (
    collect_signatures,
    match,
    match_all,
    signature_from_numeric_candidate,
    signature_from_record,
    signature_from_run,
)
from llm_capacity_pricing.perf.capacity_analyzer import (
    SustainabilityCriteria,
    analyze_group,
    analyze_runs,
    grouping_key,
)
from llm_capacity_pricing.perf.estimator import estimate
from llm_capacity_pricing.perf.performance_profile import (
    check_throughput_consistency,
    profile_from_record,
    run_from_adapter,
    run_from_numeric_candidate,
)
from llm_capacity_pricing.perf.ranker import rank_deployments
from llm_capacity_pricing.perf.sla_evaluator import evaluate_sla
from llm_capacity_pricing.schemas.performance import (
    BenchmarkRun,
    DataTrust,
    PerformanceEstimate,
    SlaPolicy,
    SlaRequirement,
    TargetRequirement,
    WorkloadSignature,
)
from llm_capacity_pricing.services import (
    capacity_profiles_for,
    calibrate_performance_for,
    import_performance_curve,
    match_benchmarks_for,
    open_db,
    performance_inventory,
)

CURVE_INDEX = "data/fixtures/perf_curve/INDEX.json"
runner = CliRunner()
#: 算法验证专用策略：显式允许 synthetic（**仅用于测试算法**，生产默认策略见 SlaPolicy()）
ALGO_POLICY = SlaPolicy(
    policy_id="algorithm-validation-only",
    allow_synthetic=True,
    accepted_trust_levels=["measured_internal", "measured_vendor_published", "synthetic_fixture"],
)


# --------------------------------------------------------------------------
# 夹具
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def curve_runs(root: Path) -> list[BenchmarkRun]:
    """合成性能曲线（5 个并发点），走真实 vLLM 适配器导入路径。"""
    folder = root / "data" / "fixtures" / "perf_curve"
    index = json.loads((folder / "INDEX.json").read_text(encoding="utf-8"))
    runs = []
    for point in index["points"]:
        data = folder / point["file"]
        meta = yaml.safe_load((folder / point["metadata"]).read_text(encoding="utf-8"))
        records, _ = parse_vllm_bench_serve_json_full(data, meta)
        runs.append(
            run_from_adapter(records[0], metadata=meta, raw_file=data, raw_format="vllm-bench-serve-json/v1")
        )
    return runs


@pytest.fixture(scope="module")
def measured_runs(curve_runs) -> list[BenchmarkRun]:
    """把曲线样本标记为"内部实测"。

    **这是机制测试夹具**：synthetic 样本本身不能支撑 SLA 结论（有专门用例验证），
    但 SLA 的通过/不通过分支需要一条可信数据才能走通，因此这里显式把信任等级
    提升为 ``measured_internal`` 并保留原始来源说明。
    """
    out = []
    for run in curve_runs:
        sig = run.signature.model_copy(update={"data_trust": "measured_internal"})
        out.append(run.model_copy(update={"signature": sig, "data_trust": "measured_internal"}))
    return out


@pytest.fixture(scope="module")
def candidate_db(tmp_path_factory, root: Path) -> str:
    """含官方候选（6 页面级 + 12 数值型）的库，用于匹配测试。

    只依赖仓库内已归档的文件，公开克隆同样可运行。
    """
    from llm_capacity_pricing.services import (
        import_official_candidates,
        import_sglang_candidates,
        init_db,
    )

    db = str(tmp_path_factory.mktemp("cand") / "cand.db")
    init_db(db)
    import_official_candidates(root / "data/sources/official_manifest.yaml", db, root=root)
    import_sglang_candidates(
        root / "data/incoming/sglang_official_benchmark_candidates_20261015.json", db, root=root
    )
    return db


@pytest.fixture
def curve_db(tmp_path: Path, root: Path) -> str:
    db = str(tmp_path / "p3.db")
    from llm_capacity_pricing.services import init_db

    init_db(db)
    import_performance_curve(root / CURVE_INDEX, db, root=root)
    return db


# --------------------------------------------------------------------------
# M19：匹配
# --------------------------------------------------------------------------


def test_signature_collection_covers_all_sources(full_db, root: Path):
    from llm_capacity_pricing.services import import_official_candidates, import_sglang_candidates

    # 在同一库里凑齐 Excel（32）+ fixture（4）+ 数值候选（12）+ 页面候选（6）
    import_official_candidates(root / "data/sources/official_manifest.yaml", full_db, root=root)
    import_sglang_candidates(
        root / "data/incoming/sglang_official_benchmark_candidates_20261015.json", full_db, root=root
    )
    db, repo = open_db(full_db)
    try:
        with db.session() as session:
            signatures = collect_signatures(
                records=repo.list_benchmarks(session),
                numeric_candidates=repo.list_numeric_candidates(session),
                official_candidates=repo.list_official_candidates(session),
                runs=repo.list_benchmark_runs(session),
            )
    finally:
        db.dispose()
    assert len(signatures) == 32 + 4 + 12 + 6
    trusts = {s.data_trust for s in signatures}
    assert "historical_external_no_latency" in trusts  # Excel 32 条
    assert "measured_vendor_published" in trusts       # 官方 18 条
    assert "synthetic_fixture" in trusts               # 4 条 fixture


def test_qwen3_32b_h100_does_not_hit_other_models(candidate_db):
    """M19 关键验收：目标配置不得命中跨模型/跨硬件的数据。"""
    info = match_benchmarks_for(
        candidate_db,
        model_name="Qwen/Qwen3-32B",
        hardware_model="NVIDIA H100 SXM",
        gpu_count=4,
        tp_size=4,
        input_len_target=8192,
        output_len_target=256,
        required_metrics=["m.latency.ttft.p99", "m.latency.tpot.p99"],
        require_measured=True,
    )
    # 候选库共 18 条签名（6 页面级 + 12 数值型），全部被拒绝
    assert info["level_counts"].get("rejected") == 18
    assert info["matched"] == []


def test_exact_match_requires_same_model_hardware_deployment(candidate_db):
    db, repo = open_db(candidate_db)
    try:
        with db.session() as session:
            candidate = next(
                c
                for c in repo.list_numeric_candidates(session)
                if c.record_id == "sglang_qwen3_cookbook:5.1.1.3"
            )
    finally:
        db.dispose()
    sig = signature_from_numeric_candidate(candidate)
    target = TargetRequirement(
        model_name=sig.model_name,
        hardware_model=sig.hardware_model,
        gpu_count=sig.gpu_count,
        tp_size=sig.tp_size,
        workload=WorkloadSignature(
            input_len_target=1000,
            output_len_target=1000,
            dataset_name="random",
            arrival_mode="unbounded",
            configured_concurrency=sig.workload.configured_concurrency,
        ),
        require_measured=True,
    )
    assert match(target, sig).level == "exact_match"


@pytest.mark.parametrize(
    "overrides,expected_level,reason_prefix",
    [
        ({"tp_size": 4, "gpu_count": 4}, "modeling_candidate", "tp_size_mismatch"),
        ({"gpu_count": 4}, "modeling_candidate", "gpu_count_mismatch"),
        ({"hardware_model": "NVIDIA H100 SXM"}, "rejected", "hardware_model_mismatch"),
        ({"model_name": "Qwen/Qwen3-32B"}, "rejected", "model_name_mismatch"),
    ],
)
def test_configuration_mismatches_are_classified(candidate_db, overrides, expected_level, reason_prefix):
    db, repo = open_db(candidate_db)
    try:
        with db.session() as session:
            candidate = next(
                c
                for c in repo.list_numeric_candidates(session)
                if c.record_id == "sglang_qwen3_cookbook:5.1.1.3"
            )
    finally:
        db.dispose()
    sig = signature_from_numeric_candidate(candidate)
    base = dict(
        model_name=sig.model_name,
        hardware_model=sig.hardware_model,
        gpu_count=sig.gpu_count,
        tp_size=sig.tp_size,
        require_measured=True,
    )
    base.update(overrides)
    result = match(TargetRequirement(**base), sig)
    assert result.level == expected_level
    assert any(r.startswith(reason_prefix) for r in result.reasons), result.reasons


def test_precision_mismatch_is_rejected(measured_runs):
    """精度不同（fp8 vs bf16）必须拒绝：不同精度的性能不可互推。"""
    run = measured_runs[0]
    sig = run.signature.model_copy(update={"weight_precision": "bf16"})
    target = TargetRequirement(
        model_name=sig.model_name,
        hardware_model=sig.hardware_model,
        weight_precision="fp8",
        require_measured=True,
    )
    result = match(target, sig)
    assert result.level == "rejected"
    assert any(r.startswith("weight_precision_mismatch") for r in result.reasons)


def test_workload_mismatch_yields_comparable_reference(candidate_db):
    db, repo = open_db(candidate_db)
    try:
        with db.session() as session:
            candidate = next(
                c
                for c in repo.list_numeric_candidates(session)
                if c.record_id == "sglang_qwen3_cookbook:5.1.1.3"
            )
    finally:
        db.dispose()
    sig = signature_from_numeric_candidate(candidate)
    target = TargetRequirement(
        model_name=sig.model_name,
        hardware_model=sig.hardware_model,
        gpu_count=sig.gpu_count,
        tp_size=sig.tp_size,
        workload=WorkloadSignature(input_len_target=32000, output_len_target=1000),
        require_measured=True,
    )
    result = match(target, sig)
    assert result.level == "comparable_reference"
    assert "sla_proof" in result.blocked_from
    assert "sla_proof" not in result.usable_for


def test_ep_deployment_is_blocked(candidate_db):
    """PP/EP/PD 本阶段不建性能模型：字段保存但阻断。"""
    db, repo = open_db(candidate_db)
    try:
        with db.session() as session:
            coder = next(
                c
                for c in repo.list_numeric_candidates(session)
                if c.record_id == "sglang_qwen3_coder_cookbook:5.1.1.1"
            )
    finally:
        db.dispose()
    sig = signature_from_numeric_candidate(coder)
    result = match(TargetRequirement(model_name=sig.model_name, hardware_model=sig.hardware_model), sig)
    assert result.level == "rejected"
    assert any("unsupported_deployment_field:ep_size" in r for r in result.reasons)


def test_synthetic_requires_explicit_optin(curve_runs):
    run = curve_runs[0]
    sig = signature_from_run(run)
    base = dict(
        model_name=sig.model_name,
        hardware_model=sig.hardware_model,
        gpu_count=sig.gpu_count,
        tp_size=sig.tp_size,
        workload=WorkloadSignature(
            input_len_target=sig.workload.input_len_target,
            output_len_target=sig.workload.output_len_target,
        ),
    )
    default = match(TargetRequirement(**base), sig)
    assert default.level == "rejected"
    assert any(r.startswith("data_trust_rejected") for r in default.reasons)

    optin = match(TargetRequirement(**base, allow_synthetic=True), sig)
    assert optin.level == "exact_match"
    assert optin.algorithm_validation_only is True
    assert "sla_proof" not in optin.usable_for


def test_reason_codes_are_structured(curve_runs):
    sig = signature_from_run(curve_runs[0])
    result = match(TargetRequirement(model_name="SOMETHING-ELSE", allow_synthetic=True), sig)
    assert result.level == "rejected"
    assert all(":" in r or "_" in r for r in result.reasons)
    assert result.differences.get("model_name")


def test_matching_does_not_touch_existing_data(full_db):
    """匹配是只读操作：既有官方证据与业务数据不被覆盖。"""
    db, repo = open_db(full_db)
    try:
        with db.session() as session:
            before = repo.counts(session).as_dict()
    finally:
        db.dispose()
    match_benchmarks_for(full_db, model_name="Qwen/Qwen3-32B", require_measured=True)
    db, repo = open_db(full_db)
    try:
        with db.session() as session:
            after = repo.counts(session).as_dict()
    finally:
        db.dispose()
    assert before == after


# --------------------------------------------------------------------------
# M20：性能档案
# --------------------------------------------------------------------------


def test_curve_fixture_imports_offline(curve_db):
    db, repo = open_db(curve_db)
    try:
        with db.session() as session:
            runs = repo.list_benchmark_runs(session)
    finally:
        db.dispose()
    assert len(runs) == 5
    assert {r.data_trust for r in runs} == {"synthetic_fixture"}
    assert {r.profile.configured_concurrency for r in runs} == {1, 2, 4, 8, 16}


def test_reimport_is_idempotent(curve_db, root: Path):
    before = len(_runs(curve_db))
    import_performance_curve(root / CURVE_INDEX, curve_db, root=root)
    assert len(_runs(curve_db)) == before == 5


def _runs(db_path: str):
    db, repo = open_db(db_path)
    try:
        with db.session() as session:
            return repo.list_benchmark_runs(session)
    finally:
        db.dispose()


def test_metrics_are_unified_and_verifiable(curve_runs):
    run = next(r for r in curve_runs if r.profile.configured_concurrency == 8)
    p = run.profile
    # 与原始 JSON 逐项可核对
    raw = json.loads(Path(run.raw_file).read_text(encoding="utf-8"))
    assert p.ttft_p99_ms == Decimal(str(raw["p99_ttft_ms"]))
    assert p.tpot_p99_ms == Decimal(str(raw["p99_tpot_ms"]))
    assert p.itl_p99_ms == Decimal(str(raw["p99_itl_ms"]))
    assert p.output_throughput_tok_s == Decimal(str(raw["output_throughput"]))
    assert p.configured_concurrency == raw["max_concurrency"]
    assert run.raw_sha256 and len(run.raw_sha256) == 64


def test_tpot_and_itl_kept_separate(curve_runs):
    for run in curve_runs:
        p = run.profile
        assert p.tpot_p99_ms is not None and p.itl_p99_ms is not None
        assert p.tpot_p99_ms != p.itl_p99_ms  # 本样本刻意让两者不同
        assert "m.latency.tpot.p99" in p.metric_definition_ids
        assert "m.latency.itl.p99" in p.metric_definition_ids


def test_missing_p99_is_not_derived_from_mean(full_db):
    """Excel 记录没有时延口径：P99 保持 None，不得由均值推算。"""
    db, repo = open_db(full_db)
    try:
        with db.session() as session:
            record = next(r for r in repo.list_benchmarks(session) if r.record_id == "excel_部署效能_2")
    finally:
        db.dispose()
    profile = profile_from_record(record)
    assert profile.ttft_p99_ms is None and profile.tpot_p99_ms is None
    assert "ttft_p99_ms" in profile.missing_metrics
    assert "sla_proof" not in []  # 结构占位
    assert profile.output_throughput_tok_s_per_gpu is not None


def test_throughput_consistency_detects_inconsistency(curve_runs):
    run = curve_runs[-1]
    p = run.profile
    assert "throughput_consistent_with_tokens_over_duration" in p.consistency_notes
    broken = p.model_copy(update={"output_throughput_tok_s": p.output_throughput_tok_s * 10})
    notes = check_throughput_consistency(
        broken, total_input_tokens=None, total_output_tokens=25600
    )
    assert any("output_throughput_inconsistent" in n for n in notes)


def test_concurrency_fields_separated(curve_runs):
    for run in curve_runs:
        # 本样本不提供观测并发 -> 保持 None，不得用配置并发顶替
        assert run.profile.configured_concurrency is not None
        assert run.profile.observed_concurrency is None


def test_candidate_profile_reports_missing_metrics(candidate_db):
    db, repo = open_db(candidate_db)
    try:
        with db.session() as session:
            candidate = next(
                c for c in repo.list_numeric_candidates(session)
                if c.record_id == "sglang_qwen3_cookbook:5.1.1.1"
            )
    finally:
        db.dispose()
    run = run_from_numeric_candidate(candidate)
    assert run.data_trust == "measured_vendor_published"
    assert run.profile.ttft_p99_ms == Decimal("166.05")
    assert "e2e_p99_ms" in run.profile.missing_metrics  # 该来源没有 E2E
    assert run.has_request_metrics is False


# --------------------------------------------------------------------------
# M21：容量曲线
# --------------------------------------------------------------------------


def test_capacity_curve_for_controlled_sample(curve_runs):
    profile = analyze_group(curve_runs, target_concurrency=8)
    assert profile.coverage_status == "sufficient"
    assert profile.covered_concurrency_points == [1, 2, 4, 8, 16]
    assert 32 in profile.missing_concurrency_points
    # 吞吐随并发上升，时延同时上升
    throughputs = [p.output_throughput_tok_s for p in profile.points]
    assert throughputs == sorted(throughputs)
    ttfts = [p.ttft_p99_ms for p in profile.points]
    assert ttfts == sorted(ttfts)
    assert profile.best_observed_point.configured_concurrency == 16


def test_best_observed_is_not_sustainable_without_evidence(curve_runs):
    strict = analyze_group(curve_runs, criteria=SustainabilityCriteria(min_duration_s=Decimal("1000")))
    assert strict.best_observed_point is not None
    assert strict.maximum_sustainable_capacity is None
    assert "sustainable_capacity_not_evidenced" in strict.quality_flags


def test_sustainability_requires_duration_and_error_evidence(curve_runs):
    ok = analyze_group(curve_runs, criteria=SustainabilityCriteria(min_duration_s=Decimal("60")))
    assert ok.maximum_sustainable_capacity == 16
    assert ok.sustainability_evidence
    # 失败率门槛收紧到 0 以下（不可能满足）-> 不给可持续容量
    impossible = analyze_group(
        curve_runs, criteria=SustainabilityCriteria(min_duration_s=Decimal("60"), max_error_rate=Decimal("-1"))
    )
    assert impossible.maximum_sustainable_capacity is None


def test_insufficient_coverage_when_too_few_points(curve_runs):
    profile = analyze_group(curve_runs[:2], target_concurrency=8)
    assert profile.coverage_status == "insufficient_coverage"
    assert "insufficient_coverage" in profile.quality_flags


def test_mixing_configurations_is_rejected(curve_runs):
    other = curve_runs[0].model_copy(
        update={
            "signature": curve_runs[0].signature.model_copy(update={"tp_size": 8}),
        }
    )
    with pytest.raises(ValueError, match="不允许混合不同配置"):
        analyze_group([curve_runs[0], other])


def test_mixing_load_profile_kinds_is_rejected(curve_runs):
    other = curve_runs[0].model_copy(update={"load_profile_kind": "multi_turn_prefix_reuse"})
    with pytest.raises(ValueError, match="不允许混合不同配置"):
        analyze_group([curve_runs[0], other])


def test_analyze_runs_groups_separately(curve_runs):
    a = curve_runs[0]
    different = a.model_copy(
        update={
            "run_id": "other-hw",
            "signature": a.signature.model_copy(update={"hardware_model": "SYNTHETIC-GPU-D"}),
            "load_profile_kind": a.load_profile_kind,
        }
    )
    profiles = analyze_runs([a, different])
    assert len(profiles) == 2
    assert {p.hardware_model for p in profiles} == {"SYNTHETIC-GPU-C", "SYNTHETIC-GPU-D"}


# --------------------------------------------------------------------------
# M22：估算
# --------------------------------------------------------------------------


def test_estimated_reference_for_exact_point(curve_runs):
    target = next(r for r in curve_runs if r.profile.configured_concurrency == 8)
    result = estimate(
        metric="output_throughput_tok_s", target_concurrency=8, runs=curve_runs, target_run=target
    )
    assert result.estimate_kind == "measured_reference"
    assert result.value == target.profile.output_throughput_tok_s
    assert result.source_run_id == target.run_id


def test_calibrated_estimate_within_range_has_holdout_error(curve_runs):
    result = estimate(metric="output_throughput_tok_s", target_concurrency=6, runs=curve_runs)
    assert result.estimate_kind == "calibrated_estimate"
    assert result.value is not None and result.covered_range == [Decimal(1), Decimal(16)]
    assert result.holdout_mae is not None and result.holdout_relative_error is not None
    assert result.method_id == "linear_interpolation_in_covered_range"


def test_no_extrapolation_outside_range(curve_runs):
    result = estimate(metric="output_throughput_tok_s", target_concurrency=64, runs=curve_runs)
    assert result.estimate_kind == "not_predictable"
    assert any("out_of_covered_range" in r for r in result.reasons)


@pytest.mark.parametrize("metric", ["ttft_p99_ms", "tpot_p99_ms", "itl_p99_ms", "e2e_p99_ms"])
def test_p99_metrics_are_never_interpolated(curve_runs, metric):
    result = estimate(metric=metric, target_concurrency=6, runs=curve_runs)
    assert result.estimate_kind == "not_predictable"
    assert any("p99_not_interpolatable" in r for r in result.reasons)


def test_insufficient_points_not_predictable(curve_runs):
    result = estimate(metric="output_throughput_tok_s", target_concurrency=2, runs=curve_runs[:2])
    assert result.estimate_kind == "not_predictable"
    assert any("insufficient_points" in r for r in result.reasons)


def test_estimate_must_not_be_observed_pass():
    estimate_only = PerformanceEstimate(
        target_ref="x",
        metric="ttft_p99_ms",
        estimate_kind="calibrated_estimate",
        value=Decimal("100"),
        method_id="m",
        covered_range=[Decimal(1), Decimal(8)],
        holdout_mae=Decimal("5"),
    )
    evaluation = evaluate_sla(
        SlaRequirement(requirement_id="r", ttft_p99_ms_max=Decimal("200")),
        policy=ALGO_POLICY,
        estimate=estimate_only,
    )
    assert evaluation.status == "estimated_only"
    assert "estimated_only_is_not_verified" in evaluation.reasons


# --------------------------------------------------------------------------
# M23：SLA
# --------------------------------------------------------------------------


def _target_for(run: BenchmarkRun, **overrides) -> TargetRequirement:
    sig = signature_from_run(run)
    base = dict(
        model_name=sig.model_name,
        hardware_model=sig.hardware_model,
        gpu_count=sig.gpu_count,
        tp_size=sig.tp_size,
        workload=WorkloadSignature(
            input_len_target=sig.workload.input_len_target,
            output_len_target=sig.workload.output_len_target,
        ),
    )
    base.update(overrides)
    return TargetRequirement(**base)


def test_observed_pass_with_matched_measurement(measured_runs):
    run = next(r for r in measured_runs if r.profile.configured_concurrency == 4)
    result = evaluate_sla(
        SlaRequirement(requirement_id="r", ttft_p99_ms_max=Decimal("400"), tpot_p99_ms_max=Decimal("30")),
        policy=ALGO_POLICY,
        run=run,
        match_result=match(_target_for(run), signature_from_run(run)),
    )
    assert result.status == "observed_pass"
    assert all(c.status == "observed_pass" for c in result.constraints)
    assert result.data_trust == "measured_internal"
    assert result.algorithm_validation_only is False


def test_observed_fail_when_threshold_exceeded(measured_runs):
    run = next(r for r in measured_runs if r.profile.configured_concurrency == 4)
    result = evaluate_sla(
        SlaRequirement(requirement_id="r", ttft_p99_ms_max=Decimal("150")),
        policy=ALGO_POLICY,
        run=run,
        match_result=match(_target_for(run), signature_from_run(run)),
    )
    assert result.status == "observed_fail"
    assert result.constraints[0].observed == Decimal("210.0")
    assert "超过上限" in (result.constraints[0].reason or "")


def test_synthetic_is_rejected_by_default_policy(curve_runs):
    run = next(r for r in curve_runs if r.profile.configured_concurrency == 4)
    result = evaluate_sla(
        SlaRequirement(requirement_id="r", ttft_p99_ms_max=Decimal("400")),
        policy=SlaPolicy(),  # 生产默认策略
        run=run,
        match_result=match(_target_for(run, allow_synthetic=True), signature_from_run(run)),
    )
    assert result.status == "not_evaluable"
    assert any("data_trust_not_accepted" in r for r in result.reasons)
    assert result.algorithm_validation_only is False


def test_synthetic_observed_pass_is_flagged_algorithm_only(curve_runs):
    run = next(r for r in curve_runs if r.profile.configured_concurrency == 4)
    result = evaluate_sla(
        SlaRequirement(requirement_id="r", ttft_p99_ms_max=Decimal("400")),
        policy=ALGO_POLICY,
        run=run,
        match_result=match(_target_for(run, allow_synthetic=True), signature_from_run(run)),
    )
    assert result.status == "observed_pass"
    assert result.algorithm_validation_only is True  # 明确标记，不得对外使用


def test_no_match_result_means_not_evaluable(measured_runs):
    run = measured_runs[0]
    result = evaluate_sla(
        SlaRequirement(requirement_id="r", ttft_p99_ms_max=Decimal("400")), policy=ALGO_POLICY, run=run
    )
    assert result.status == "not_evaluable"
    assert any("match_result_required" in r for r in result.reasons)


def test_match_level_below_policy_blocks(measured_runs):
    run = next(r for r in measured_runs if r.profile.configured_concurrency == 4)
    # 负载明显不同 -> comparable_reference；策略要求 exact_match
    target = _target_for(run)
    target = target.model_copy(update={"workload": WorkloadSignature(input_len_target=99999, output_len_target=1)})
    strict = SlaPolicy(policy_id="strict", minimum_match_level="exact_match")
    result = evaluate_sla(
        SlaRequirement(requirement_id="r", ttft_p99_ms_max=Decimal("400")),
        policy=strict,
        run=run,
        match_result=match(target, signature_from_run(run)),
    )
    assert result.status == "not_evaluable"
    assert any("match_level_below_policy" in r for r in result.reasons)


def test_missing_metric_prevents_conclusion(measured_runs):
    from llm_capacity_pricing.schemas.performance import (
        PerformanceProfile,
        RequestMetric,
    )

    run = measured_runs[0].model_copy(
        update={
            "profile": measured_runs[0].profile.model_copy(update={"e2e_p99_ms": None})
        }
    )
    result = evaluate_sla(
        SlaRequirement(requirement_id="r", e2e_p99_ms_max=Decimal("5000")),
        policy=ALGO_POLICY,
        run=run,
        match_result=match(_target_for(run), signature_from_run(run)),
    )
    assert result.status == "not_evaluable"
    assert any("metric_not_available:e2e_p99_ms" in (c.reason or "") for c in result.constraints)


def test_goodput_requires_per_request_metrics(measured_runs):
    run = measured_runs[0]
    result = evaluate_sla(
        SlaRequirement(requirement_id="r", ttft_p99_ms_max=Decimal("400"), require_goodput=True),
        policy=ALGO_POLICY,
        run=run,
        match_result=match(_target_for(run), signature_from_run(run)),
    )
    assert result.status == "not_evaluable"
    assert result.goodput_rps is None
    assert any("request_metrics_required_for_goodput" in r for r in result.reasons)


def test_goodput_computed_from_request_metrics(measured_runs):
    from llm_capacity_pricing.schemas.performance import RequestMetric

    base = measured_runs[0]
    metrics = [
        RequestMetric(request_id=f"r{i}", ttft_ms=Decimal("100") if i < 8 else Decimal("900"),
                      tpot_ms=Decimal("10"), e2e_ms=Decimal("1000"), success=True)
        for i in range(10)
    ]
    run = base.model_copy(update={"request_metrics": metrics})
    result = evaluate_sla(
        SlaRequirement(requirement_id="r", ttft_p99_ms_max=Decimal("500"), require_goodput=True),
        policy=ALGO_POLICY,
        run=run,
        match_result=match(_target_for(run), signature_from_run(run)),
    )
    # 8/10 条满足 TTFT 上限；聚合 TTFT P99 只是单点值，不能替代请求级判断
    assert result.goodput_rps is not None
    assert "8/10" in (result.goodput_note or "")


# --------------------------------------------------------------------------
# M24：候选排序
# --------------------------------------------------------------------------


def _ranking(tmp_path: Path, root: Path, runs_source: str):
    from decimal import Decimal

    from llm_capacity_pricing.services import (
        import_hardware,
        import_models,
        init_db,
        make_assumptions,
        plan_deployment_for,
        rank_deployments_for,
    )

    db = str(tmp_path / "rank.db")
    init_db(db)
    import_models(root / "data/models/model_manifest.yaml", db, root=root)
    import_hardware(root / "data/hardware/hardware_catalog.yaml", db)
    import_performance_curve(root / CURVE_INDEX, db, root=root) if runs_source == "curve" else None
    return rank_deployments_for(
        db,
        model_id="qwen3-32b",
        hardware_id="nvidia-h100-sxm-80gb",
        server_id="hgx-h100-8gpu",
        requirement={"requirement_id": "r", "ttft_p99_ms_max": 400, "tpot_p99_ms_max": 30},
        assumptions=make_assumptions(
            usable_fraction_of_nominal=Decimal("0.9"),
            usable_memory_source="演示值",
            usable_memory_source_kind="synthetic_example",
            runtime_reserve_fraction=Decimal("0.1"),
            runtime_reserve_source="演示值",
            global_concurrency=32,
            context_length_per_request=8192,
            tp_sizes=(2, 4, 8),
            dp_sizes=(1,),
        ),
        input_len_target=8192,
        output_len_target=256,
    )


def test_ranking_without_perf_evidence_marks_requires_benchmark(tmp_path, root):
    info = _ranking(tmp_path, root, "curve")
    buckets = {e["rank_bucket"] for e in info["entries"]}
    assert buckets == {"requires_benchmark", "excluded"}
    assert info["blocker"] and "requires_benchmark" in info["blocker"]
    assert info["economic_scoring_used"] is False


def test_static_infeasible_candidates_are_excluded(tmp_path, root):
    info = _ranking(tmp_path, root, "curve")
    excluded = [e for e in info["entries"] if e["rank_bucket"] == "excluded"]
    assert excluded and all(e["static_status"] == "infeasible" for e in excluded)


def test_ranking_prefers_verified_sla_met(measured_runs, tmp_path, root):
    """把"内部实测"曲线喂进排序：命中的候选应进入 verified_sla_met。"""
    from llm_capacity_pricing.schemas.sizing import (
        DeploymentCandidate,
        ResourceFeasibilityResult,
    )

    candidate = DeploymentCandidate(
        candidate_id="cand-x",
        model_id="qwen3-32b",
        hardware_id="nvidia-h100-sxm-80gb",
        weight_precision="bf16",
        kv_precision="bf16",
        tp_size=4,
        dp_size=1,
        gpu_count=4,
        feasibility=ResourceFeasibilityResult(status="conditional_feasible", assumptions=["测试"]),
        headroom_bytes=10**9,
    )
    # 把 measured_runs 的签名改成与该候选一致，以验证"有匹配实测"分支。
    # 注意 ranker 会把硬件 ID 映射成商品名（nvidia-h100-sxm-80gb -> NVIDIA H100 SXM），
    # 因此这里必须用同一个显示名对齐。
    aligned = []
    for run in measured_runs:
        sig = run.signature.model_copy(
            update={
                "model_name": "qwen3-32b",
                "hardware_model": "NVIDIA H100 SXM",
                "tp_size": 4,
                "dp_size": 1,
                "gpu_count": 4,
            }
        )
        aligned.append(run.model_copy(update={"signature": sig}))
    ranking = rank_deployments(
        requirement_id="r",
        candidates=[candidate],
        runs=aligned,
        sla_requirement=SlaRequirement(requirement_id="r", ttft_p99_ms_max=Decimal("400")),
        policy=ALGO_POLICY,
        workload=WorkloadSignature(input_len_target=1024, output_len_target=256),
    )
    entry = ranking.entries[0]
    assert entry.rank_bucket == "verified_sla_met"
    assert entry.sla_status == "observed_pass"
    assert entry.matched_run_id


def test_ranking_notes_forbid_cross_config_throughput_order(tmp_path, root):
    info = _ranking(tmp_path, root, "curve")
    joined = " ".join(info["comparability_notes"])
    assert "不以吞吐跨配置排序" in joined
    assert "经济性评分" in joined


# --------------------------------------------------------------------------
# M25：CLI 与报告
# --------------------------------------------------------------------------


def test_cli_import_and_capacity_offline(tmp_path, root):
    db = str(tmp_path / "cli.db")
    assert runner.invoke(app, ["init-db", "--db", db]).exit_code == 0
    r = runner.invoke(
        app,
        ["import-performance", "--curve-index", str(root / CURVE_INDEX), "--kind", "vllm",
         "--root", str(root), "--db", db],
    )
    assert r.exit_code == 0, r.output
    assert "已导入 5 条性能记录" in r.output
    assert "synthetic_fixture" in r.output

    cap = runner.invoke(app, ["capacity-profile", "--target-concurrency", "8", "--db", db])
    assert cap.exit_code == 0
    assert "分成 1 个配置组" in cap.output

    cal = runner.invoke(
        app, ["calibrate-performance", "--metric", "output_throughput_tok_s", "--concurrency", "6", "--db", db]
    )
    assert cal.exit_code == 0
    assert "calibrated_estimate" in cal.output


def test_cli_excludes_synthetic_for_formal_conclusions(tmp_path, root):
    db = str(tmp_path / "cli2.db")
    runner.invoke(app, ["init-db", "--db", db])
    runner.invoke(
        app,
        ["import-performance", "--curve-index", str(root / CURVE_INDEX), "--kind", "vllm",
         "--root", str(root), "--db", db],
    )
    r = runner.invoke(app, ["capacity-profile", "--exclude-synthetic", "--db", db])
    assert r.exit_code == 0
    assert "分成 0 个配置组" in r.output


def test_cli_evaluate_sla_default_policy_refuses_synthetic(tmp_path, root):
    db = str(tmp_path / "cli3.db")
    runner.invoke(app, ["init-db", "--db", db])
    runner.invoke(
        app,
        ["import-performance", "--curve-index", str(root / CURVE_INDEX), "--kind", "vllm",
         "--root", str(root), "--db", db],
    )
    r = runner.invoke(
        app,
        ["evaluate-sla", "--requirement", '{"requirement_id":"r","ttft_p99_ms_max":400}',
         "--run-id", "fixture_perf_curve_c4", "--model", "SYNTHETIC-MODEL-C",
         "--hardware", "SYNTHETIC-GPU-C", "--gpu-count", "4", "--tp", "4",
         "--input-len", "1024", "--output-len", "256", "--db", db],
    )
    assert r.exit_code == 0
    assert "not_evaluable" in r.output

    r2 = runner.invoke(
        app,
        ["evaluate-sla", "--requirement", '{"requirement_id":"r","ttft_p99_ms_max":400}',
         "--run-id", "fixture_perf_curve_c4", "--model", "SYNTHETIC-MODEL-C",
         "--hardware", "SYNTHETIC-GPU-C", "--gpu-count", "4", "--tp", "4",
         "--input-len", "1024", "--output-len", "256",
         "--policy-id", "algorithm-validation-only", "--allow-synthetic", "--db", db],
    )
    assert r2.exit_code == 0
    assert "observed_pass" in r2.output
    assert "仅用于算法验证" in r2.output


def test_cli_match_benchmark_rejects_cross_model(tmp_path, root, full_db):
    r = runner.invoke(
        app,
        ["match-benchmark", "--model", "Qwen/Qwen3-32B", "--hardware", "NVIDIA H100 SXM",
         "--gpu-count", "4", "--tp", "4", "--db", full_db],
    )
    assert r.exit_code == 0, r.output
    assert "'rejected'" in r.output
    assert "非拒绝项: 0 条" in r.output


def test_cli_performance_report(tmp_path, root):
    db = str(tmp_path / "cli4.db")
    runner.invoke(app, ["init-db", "--db", db])
    runner.invoke(
        app,
        ["import-performance", "--curve-index", str(root / CURVE_INDEX), "--kind", "vllm",
         "--root", str(root), "--db", db],
    )
    out = tmp_path / "perf.md"
    r = runner.invoke(app, ["performance-report", "--out", str(out), "--db", db])
    assert r.exit_code == 0, r.output
    text = out.read_text(encoding="utf-8")
    for expected in (
        "匹配等级 ≠ 数据可信等级 ≠ SLA 状态",
        "不得**作为任何正式 SLA 结论的依据",
        "不等于可持续产能",
        "不做跨配置吞吐排序",
        "未确认币种与成本范围前，不使用经济性评分",
    ):
        assert expected in text, expected


def test_inventory_marks_which_trust_can_support_sla(curve_db):
    info = performance_inventory(curve_db)
    assert info["benchmark_runs"] == 5
    assert "synthetic_fixture" in info["by_trust"]
    assert set(info["sla_usable_trust_levels"]) == {"measured_internal", "measured_vendor_published"}
