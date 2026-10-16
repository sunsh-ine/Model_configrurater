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
#: 估算器在"仅算法验证"场景下允许的可信等级
ALGO_TRUST = ("measured_internal", "measured_vendor_published", "synthetic_fixture")
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


def test_exact_match_needs_full_identity(candidate_db):
    """V3.1 R2：官方候选缺 model_revision / 精度 -> 不得评为 exact_match。"""
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
    result = match(target, sig)
    assert result.level == "comparable_reference"
    assert any(
        "record_identity_incomplete" in r and "model_revision" in r for r in result.reasons
    ), result.reasons


def test_exact_match_requires_full_identity_on_record(curve_runs):
    """身份齐备（revision/精度/框架版本/数据集/长度/并发）时才可能 exact_match。"""
    run = next(r for r in curve_runs if r.profile.configured_concurrency == 8)
    sig = signature_from_run(run)
    base = dict(
        model_name=sig.model_name,
        model_revision=sig.model_revision,
        weight_precision=sig.weight_precision,
        hardware_model=sig.hardware_model,
        gpu_count=sig.gpu_count,
        tp_size=sig.tp_size,
        dp_size=sig.dp_size,
        framework=sig.framework,
        framework_version=sig.framework_version,
        allow_synthetic=True,
        workload=WorkloadSignature(
            input_len_target=sig.workload.input_len_target,
            output_len_target=sig.workload.output_len_target,
            dataset_name=sig.workload.dataset_name,
            arrival_mode=sig.workload.arrival_mode,
            configured_concurrency=sig.workload.configured_concurrency,
        ),
    )
    assert match(TargetRequirement(**base), sig).level == "exact_match"

    # 抽掉任一身份字段 -> 立即降级（缺失不等于匹配成功）
    for dropped in ("model_revision", "framework_version", "weight_precision"):
        degraded_sig = sig.model_copy(update={dropped: None})
        result = match(TargetRequirement(**base), degraded_sig)
        assert result.level == "comparable_reference", dropped
        assert any("record_identity_incomplete" in r for r in result.reasons), dropped


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
    base = _full_identity_target(run).model_dump(
        exclude={"allow_synthetic", "require_measured"}
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
    """V3.1 R5：单次观测只给 observed_stable_candidate，不给可持续容量。"""
    once = analyze_group(curve_runs, criteria=SustainabilityCriteria(min_duration_s=Decimal("60")))
    assert once.maximum_sustainable_capacity is None
    assert once.observed_stable_candidate == 16
    assert "sustainable_capacity_needs_repeats" in once.quality_flags
    # **同一次执行**换个 run_id 重新导入，不构成独立观测（P0-1）
    same_bytes = list(curve_runs) + [
        r.model_copy(update={"run_id": r.run_id + "-repeat"})
        for r in curve_runs
        if r.profile.configured_concurrency == 16
    ]
    cloned = analyze_group(same_bytes, criteria=SustainabilityCriteria(min_duration_s=Decimal("60")))
    assert cloned.maximum_sustainable_capacity is None
    assert any(f.startswith("duplicate_observation:c16") for f in cloned.quality_flags)
    assert any("原始文件哈希相同" in n for n in cloned.notes)

    # **另一次独立执行**（不同原始文件哈希）后，才给出可持续容量
    doubled = list(curve_runs) + [
        _independent_repeat(r)
        for r in curve_runs
        if r.profile.configured_concurrency == 16
    ]
    again = analyze_group(doubled, criteria=SustainabilityCriteria(min_duration_s=Decimal("60")))
    assert again.maximum_sustainable_capacity == 16
    assert again.sustainability_evidence
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
        metric="output_throughput_tok_s",
        target_concurrency=8,
        runs=curve_runs,
        target_run=target,
        allowed_trust=ALGO_TRUST,
    )
    assert result.estimate_kind == "measured_reference"
    assert result.value == target.profile.output_throughput_tok_s
    assert result.source_run_id == target.run_id


def test_calibrated_estimate_within_range_has_holdout_error(curve_runs):
    result = estimate(
        metric="output_throughput_tok_s", target_concurrency=6, runs=curve_runs,
        allowed_trust=ALGO_TRUST,
    )
    assert result.estimate_kind == "calibrated_estimate"
    assert result.value is not None and result.covered_range == [Decimal(1), Decimal(16)]
    assert result.holdout_mae is not None and result.holdout_relative_error is not None
    assert result.method_id == "linear_interpolation_in_covered_range"


def test_no_extrapolation_outside_range(curve_runs):
    result = estimate(
        metric="output_throughput_tok_s", target_concurrency=64, runs=curve_runs,
        allowed_trust=ALGO_TRUST,
    )
    assert result.estimate_kind == "not_predictable"
    assert any("out_of_covered_range" in r for r in result.reasons)


@pytest.mark.parametrize("metric", ["ttft_p99_ms", "tpot_p99_ms", "itl_p99_ms", "e2e_p99_ms"])
def test_p99_metrics_are_never_interpolated(curve_runs, metric):
    result = estimate(metric=metric, target_concurrency=6, runs=curve_runs, allowed_trust=ALGO_TRUST)
    assert result.estimate_kind == "not_predictable"
    assert any("p99_not_interpolatable" in r for r in result.reasons)


def test_insufficient_points_not_predictable(curve_runs):
    result = estimate(
        metric="output_throughput_tok_s", target_concurrency=2, runs=curve_runs[:2],
        allowed_trust=ALGO_TRUST,
    )
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
    """构造与该 run 身份完全对齐的目标需求。

    V3.1 复审 P0-1 之后，**目标侧**也必须齐备身份字段（revision/精度/框架版本/
    数据集/长度/到达方式/并发），否则不得 exact_match。
    """
    sig = signature_from_run(run)
    base = dict(
        model_name=sig.model_name,
        model_revision=sig.model_revision,
        weight_precision=sig.weight_precision,
        hardware_model=sig.hardware_model,
        gpu_count=sig.gpu_count,
        tp_size=sig.tp_size,
        dp_size=sig.dp_size,
        framework=sig.framework,
        framework_version=sig.framework_version,
        workload=WorkloadSignature(
            input_len_target=sig.workload.input_len_target,
            output_len_target=sig.workload.output_len_target,
            dataset_name=sig.workload.dataset_name,
            arrival_mode=sig.workload.arrival_mode,
            configured_concurrency=sig.workload.configured_concurrency,
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
        match_result=match(
            _target_for(run, allow_synthetic=True), signature_from_run(run)
        ),
    )
    assert result.status == "not_evaluable"
    # 生产默认策略下，synthetic 会同时触发"不得用于 SLA 证明"与"可信等级不接受"两道门禁
    assert any(
        "data_trust_not_accepted" in r or "sla_proof_blocked_by_match" in r
        for r in result.reasons
    ), result.reasons
    assert result.algorithm_validation_only is False


def test_synthetic_observed_pass_is_flagged_algorithm_only(curve_runs):
    run = next(r for r in curve_runs if r.profile.configured_concurrency == 4)
    result = evaluate_sla(
        SlaRequirement(requirement_id="r", ttft_p99_ms_max=Decimal("400")),
        policy=ALGO_POLICY,
        run=run,
        match_result=match(
            _target_for(run, allow_synthetic=True), signature_from_run(run)
        ),
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
    assert any(
        "sla_requires_exact_match" in r or "match_level_below_policy" in r
        for r in result.reasons
    ), result.reasons


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


def _aligned_runs(measured_runs):
    """把实测曲线对齐到候选所需配置（模型/硬件/TP/DP/卡数），用于验证排序分支。"""
    aligned = []
    for run in measured_runs:
        sig = run.signature.model_copy(
            update={
                "model_name": "qwen3-32b",   # 与候选 model_id 对齐
                "hardware_model": "NVIDIA H100 SXM",
                "tp_size": 4,
                "dp_size": 1,
                "gpu_count": 4,
            }
        )
        aligned.append(run.model_copy(update={"signature": sig}))
    return aligned


def _candidate_x():
    from llm_capacity_pricing.schemas.sizing import (
        DeploymentCandidate,
        ResourceFeasibilityResult,
    )

    return DeploymentCandidate(
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


def _independent_repeat(run, suffix: str = "-r2"):
    """把一条 run 复制成**另一次独立执行**。

    P0-1 要求：不同 run_id 不等于独立观测。要作为独立重复观测，
    必须来自不同的原始执行 —— 这里给一个不同的 ``raw_sha256`` 表示这一点。
    """
    digest = (run.raw_sha256 or "0" * 64)
    flipped = ("1" if digest[0] != "1" else "2") + digest[1:]
    return run.model_copy(
        update={"run_id": run.run_id + suffix, "raw_sha256": flipped}
    )


def _with_repeats(measured_runs, concurrency: int = 8, worse: bool = True):
    """给指定并发点造一次**同身份**的重复实测。

    ``worse=True`` 时让重复观测明显更差（TTFT/TPOT 更高），用于验证
    "同一配置多次实测时取保守值、不取最优值"（V3.1 复审 P1-2）。
    """
    target = next(r for r in measured_runs if r.profile.configured_concurrency == concurrency)
    profile = target.profile
    if worse:
        profile = profile.model_copy(
            update={
                "ttft_p99_ms": Decimal("900"),
                "tpot_p99_ms": Decimal("60"),
                "output_throughput_tok_s": profile.output_throughput_tok_s / 2,
            }
        )
    repeat = target.model_copy(update={"run_id": target.run_id + "-repeat", "profile": profile})
    return list(measured_runs) + [repeat]


def _rank(measured_runs, ttft_max: str):
    """排序用目标：负载身份齐备（长度/数据集/到达方式/并发）。"""
    return rank_deployments(
        requirement_id="r",
        candidates=[_candidate_x()],
        runs=_aligned_runs(measured_runs),
        sla_requirement=SlaRequirement(requirement_id="r", ttft_p99_ms_max=Decimal(ttft_max)),
        policy=ALGO_POLICY,
        workload=WorkloadSignature(
            input_len_target=1024,
            output_len_target=256,
            dataset_name="random",
            arrival_mode="unbounded",
            configured_concurrency=8,
        ),
        # V3.1 复审 P0-1：目标侧必须声明 revision / 框架 / 框架版本，否则不得 exact_match
        target_identity={
            "model_revision": "synthetic-revision-1",
            "framework": "vllm",
            "framework_version": "synthetic-fw-1.0",
        },
    )


def test_ranking_marks_verified_when_aggregate_meets_sla(measured_runs):
    ranking = _rank(_with_repeats(measured_runs), "1000")   # 宽松阈值：保守聚合仍达标
    entry = ranking.entries[0]
    assert entry.rank_bucket == "verified_sla_met"
    assert entry.sla_status == "observed_pass"
    assert entry.matched_run_id.startswith("aggregate:")   # 多次实测 -> 保守聚合（R6）
    assert any("保守聚合" in n for n in ranking.comparability_notes)


def test_ranking_uses_conservative_aggregate_not_best_run(measured_runs):
    """V3.1 R6：同一配置多次实测时，不取最优值。

    严格阈值下，单看 c=1（TTFT 120ms）会通过，但保守聚合取最大 P99（720ms）会不通过。
    """
    ranking = _rank(_with_repeats(measured_runs), "400")
    entry = ranking.entries[0]
    assert entry.rank_bucket == "measured_but_sla_unmet"
    assert entry.matched_run_id.startswith("aggregate:")
    assert any("constraint_failed:ttft_p99_ms" in r for r in entry.reasons)


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
        app,
        ["calibrate-performance", "--metric", "output_throughput_tok_s", "--concurrency", "6",
         "--allow-synthetic", "--db", db],
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
         "--model-revision", "synthetic-revision-1", "--framework", "vllm",
         "--framework-version", "synthetic-fw-1.0", "--precision", "bf16",
         "--hardware", "SYNTHETIC-GPU-C", "--gpu-count", "4", "--tp", "4", "--dp", "1",
         "--input-len", "1024", "--output-len", "256", "--dataset", "random",
         "--arrival-mode", "unbounded", "--concurrency", "4", "--db", db],
    )
    assert r.exit_code == 0
    assert "not_evaluable" in r.output

    r2 = runner.invoke(
        app,
        ["evaluate-sla", "--requirement", '{"requirement_id":"r","ttft_p99_ms_max":400}',
         "--run-id", "fixture_perf_curve_c4", "--model", "SYNTHETIC-MODEL-C",
         "--model-revision", "synthetic-revision-1", "--framework", "vllm",
         "--framework-version", "synthetic-fw-1.0", "--precision", "bf16",
         "--hardware", "SYNTHETIC-GPU-C", "--gpu-count", "4", "--tp", "4", "--dp", "1",
         "--input-len", "1024", "--output-len", "256", "--dataset", "random",
         "--arrival-mode", "unbounded", "--concurrency", "4",
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


# ==========================================================================
# V3.1 整改专项（R1–R7）
# ==========================================================================


def _full_identity_target(run: BenchmarkRun, **workload_overrides) -> TargetRequirement:
    """构造与某条实测**身份完全对齐**的目标需求（用于 R1/R7 场景）。"""
    sig = signature_from_run(run)
    workload = dict(
        input_len_target=sig.workload.input_len_target,
        output_len_target=sig.workload.output_len_target,
        dataset_name=sig.workload.dataset_name,
        arrival_mode=sig.workload.arrival_mode,
        configured_concurrency=sig.workload.configured_concurrency,
    )
    workload.update(workload_overrides)
    return TargetRequirement(
        model_name=sig.model_name,
        model_revision=sig.model_revision,
        weight_precision=sig.weight_precision,
        hardware_model=sig.hardware_model,
        gpu_count=sig.gpu_count,
        tp_size=sig.tp_size,
        dp_size=sig.dp_size,
        framework=sig.framework,
        framework_version=sig.framework_version,
        workload=WorkloadSignature(**workload),
        require_measured=False,
    )


def test_r1_long_context_cannot_use_short_context_benchmark(measured_runs):
    """R1（P0）：32K/并发 32 的需求**不得**用 1K/并发 8 的实测得出 observed_pass。"""
    run = next(r for r in measured_runs if r.profile.configured_concurrency == 8)
    sig = signature_from_run(run)
    assert (sig.workload.input_len_target, sig.workload.configured_concurrency) == (1024, 8)

    # 客户需求：32K 输入、并发 32；其余身份与实测完全一致
    target = _full_identity_target(run, input_len_target=32768, configured_concurrency=32)
    match_result = match(target, sig)
    assert match_result.level == "comparable_reference"
    assert any("workload_length_mismatch" in r for r in match_result.reasons)
    assert any("concurrency_mismatch" in r for r in match_result.reasons)

    # 默认策略（要求 exact_match）必须拒绝
    evaluation = evaluate_sla(
        SlaRequirement(requirement_id="r1", ttft_p99_ms_max=Decimal("2000")),  # 实测 340ms 看似通过
        policy=SlaPolicy(),  # 生产默认策略
        run=run,
        match_result=match_result,
    )
    assert evaluation.status == "not_evaluable"
    # 现在由"生产级结论强制 exact_match"这道不可放宽的门禁拦下
    assert any(
        "sla_requires_exact_match" in r or "match_level_below_policy" in r
        for r in evaluation.reasons
    ), evaluation.reasons


def test_r1_ranking_never_verifies_mismatched_load(measured_runs):
    """R1+R7：负载/并发不匹配时，排序不得进入 verified_sla_met。"""
    candidate = _candidate_x()
    ranking = rank_deployments(
        requirement_id="r1",
        candidates=[candidate],
        runs=_aligned_runs(measured_runs),
        sla_requirement=SlaRequirement(requirement_id="r1", ttft_p99_ms_max=Decimal("2000")),
        policy=ALGO_POLICY,
        workload=WorkloadSignature(
            input_len_target=32768, output_len_target=256, configured_concurrency=32
        ),
        target_identity={
            "model_revision": "synthetic-revision-1",
            "framework": "vllm",
            "framework_version": "synthetic-fw-1.0",
        },
    )
    entry = ranking.entries[0]
    assert entry.rank_bucket != "verified_sla_met"
    assert entry.rank_bucket == "requires_benchmark"


def test_r2_concurrency_mismatch_downgrades(measured_runs):
    """R2（P0）：并发不一致必须降级，不得 exact_match。"""
    run = next(r for r in measured_runs if r.profile.configured_concurrency == 8)
    sig = signature_from_run(run)
    target = _full_identity_target(run, configured_concurrency=32)
    result = match(target, sig)
    assert result.level == "comparable_reference"
    assert any("concurrency_mismatch:32!=8" in r for r in result.reasons)


def test_r2_strict_identity_fields_required(measured_runs):
    """R2：revision / 框架版本 / 精度 / 数据集 缺失时不得 exact_match。"""
    run = next(r for r in measured_runs if r.profile.configured_concurrency == 4)
    sig = signature_from_run(run)
    target = _full_identity_target(run)
    assert match(target, sig).level == "exact_match"
    for field_name in ("model_revision", "framework_version", "weight_precision"):
        degraded = sig.model_copy(update={field_name: None})
        result = match(target, degraded)
        assert result.level != "exact_match", field_name
        assert any("record_identity_incomplete" in r for r in result.reasons), field_name
    # 数据集缺失同样降级
    degraded_wl = sig.model_copy(
        update={"workload": sig.workload.model_copy(update={"dataset_name": None})}
    )
    result = match(target, degraded_wl)
    assert result.level != "exact_match"
    assert any("workload.dataset_name" in r for r in result.reasons)


def test_r3_source_kind_trust_mapping():
    """R3（P0）：来源必须显式映射，外部数据不得被标成内部实测。"""
    from llm_capacity_pricing.perf.performance_profile import trust_for_source_kind

    assert trust_for_source_kind("internal_measurement")[0] == "measured_internal"
    assert trust_for_source_kind("official_benchmark")[0] == "measured_vendor_published"
    assert trust_for_source_kind("third_party")[0] == "measured_third_party"
    assert trust_for_source_kind("internal_excel")[0] == "historical_external_no_latency"
    assert trust_for_source_kind("synthetic_fixture")[0] == "synthetic_fixture"
    for unknown in (None, "", "something_new"):
        trust, warning = trust_for_source_kind(unknown)
        assert trust == "unknown", unknown
        assert warning


def test_r3_official_benchmark_not_marked_internal(tmp_path, root: Path):
    """用适配器导入的 official_benchmark 不得被标为 measured_internal。"""
    import shutil

    from llm_capacity_pricing.importers.vllm_adapter import parse_vllm_bench_serve_json_full
    from llm_capacity_pricing.perf.performance_profile import run_from_adapter

    work = tmp_path / "proj"
    shutil.copytree(root / "data" / "fixtures" / "perf_curve", work / "perf_curve")
    folder = work / "perf_curve"
    data = folder / "perf_curve_c4.json"
    meta = yaml.safe_load((folder / "perf_curve_c4.yaml").read_text(encoding="utf-8"))
    meta["performance_source_kind"] = "official_benchmark"
    records, _ = parse_vllm_bench_serve_json_full(data, meta)
    run = run_from_adapter(records[0], metadata=meta, raw_file=data, raw_format="test")
    assert run.data_trust == "measured_vendor_published"
    assert run.data_trust != "measured_internal"


def test_r4_workload_lengths_split_capacity_groups(curve_runs):
    """R4（P1）：1K/1K 与 32K/256 不得进入同一条曲线。"""
    long_ctx = curve_runs[0].model_copy(
        update={
            "run_id": "long-ctx",
            "signature": curve_runs[0].signature.model_copy(
                update={
                    "workload": curve_runs[0].signature.workload.model_copy(
                        update={"input_len_target": 32768}
                    )
                }
            ),
        }
    )
    assert grouping_key(curve_runs[0]) != grouping_key(long_ctx)
    profiles = analyze_runs([curve_runs[0], long_ctx])
    assert len(profiles) == 2
    # 混合进同一组会直接报错
    with pytest.raises(ValueError, match="不允许混合"):
        analyze_group([curve_runs[0], long_ctx])


def test_r4_dataset_and_arrival_mode_split_groups(curve_runs):
    base = curve_runs[0]
    for update in ({"dataset_name": "sharegpt"}, {"arrival_mode": "request_rate"}):
        other = base.model_copy(
            update={
                "run_id": f"other-{list(update)[0]}",
                "signature": base.signature.model_copy(
                    update={"workload": base.signature.workload.model_copy(update=update)}
                ),
            }
        )
        assert grouping_key(base) != grouping_key(other)


def test_r5_unknown_error_rate_blocks_sustainable_capacity(curve_runs):
    """R5（P1）：失败率未知时不得输出可持续容量。"""
    stripped = [
        r.model_copy(update={"profile": r.profile.model_copy(update={"error_rate": None})})
        for r in curve_runs
    ]
    profile = analyze_group(
        stripped,
        criteria=SustainabilityCriteria(min_duration_s=Decimal("60"), min_repeats_per_point=1),
    )
    assert profile.maximum_sustainable_capacity is None
    assert profile.observed_stable_candidate is None
    assert any("失败率未知" in f for f in profile.quality_flags)


def test_r5_latency_slo_included_in_sustainability(curve_runs):
    # 独立重复观测 = 不同原始执行（不同 raw_sha256）
    doubled = list(curve_runs) + [_independent_repeat(r) for r in curve_runs]
    strict = analyze_group(
        doubled,
        criteria=SustainabilityCriteria(
            min_duration_s=Decimal("60"), min_repeats_per_point=2, ttft_p99_ms_max=Decimal("400")
        ),
    )
    # 并发 16 的 TTFT P99 = 720ms，超过 400ms -> 不能算可持续
    assert strict.maximum_sustainable_capacity == 8


def test_r6_estimator_rejects_mixed_configurations(curve_runs):
    from llm_capacity_pricing.perf.capacity_analyzer import grouping_key as gk

    other_cfg = curve_runs[0].model_copy(
        update={
            "run_id": "other-tp",
            "signature": curve_runs[0].signature.model_copy(update={"tp_size": 8}),
        }
    )
    result = estimate(
        metric="output_throughput_tok_s",
        target_concurrency=4,
        runs=[curve_runs[0], other_cfg],
        allowed_trust=ALGO_TRUST,
    )
    assert result.estimate_kind == "not_predictable"
    assert any("mixed_configurations_in_input" in r for r in result.reasons)
    assert gk(curve_runs[0]) != gk(other_cfg)


def test_r6_estimator_rejects_mixed_trust(measured_runs, curve_runs):
    mixed = [measured_runs[0], curve_runs[1], curve_runs[2]]
    result = estimate(
        metric="output_throughput_tok_s",
        target_concurrency=4,
        runs=mixed,
        allowed_trust=ALGO_TRUST,
    )
    assert result.estimate_kind == "not_predictable"
    assert any("mixed_trust_levels" in r for r in result.reasons)


def test_r6_estimator_default_refuses_synthetic(curve_runs):
    result = estimate(metric="output_throughput_tok_s", target_concurrency=6, runs=curve_runs)
    assert result.estimate_kind == "not_predictable"
    assert any("trust_level_not_allowed_for_estimation" in r for r in result.reasons)


def test_r7_end_to_end_no_false_verified_path(measured_runs):
    """R7：审核要求的端到端场景。

    模型、GPU、TP/DP **相同**，但输入长度与配置并发**不同**的两类 Benchmark，
    走完"匹配 → SLA → 排序"后不得错误进入 verified_sla_met。
    """
    candidate = _candidate_x()
    aligned = _aligned_runs(measured_runs)
    short_ctx = [r for r in aligned if r.signature.workload.input_len_target == 1024]
    assert short_ctx, "夹具应包含 1K 输入的实测"

    # 客户需求：32K 输入、并发 32
    target = TargetRequirement(
        model_name="qwen3-32b",
        hardware_model="NVIDIA H100 SXM",
        gpu_count=4,
        tp_size=4,
        dp_size=1,
        weight_precision="bf16",
        workload=WorkloadSignature(
            input_len_target=32768,
            output_len_target=256,
            dataset_name="random",
            configured_concurrency=32,
            arrival_mode="unbounded",
        ),
        require_measured=True,
    )
    for run in short_ctx:
        assert match(target, signature_from_run(run)).level != "exact_match"

    ranking = rank_deployments(
        requirement_id="r7",
        candidates=[candidate],
        runs=aligned,
        sla_requirement=SlaRequirement(requirement_id="r7", ttft_p99_ms_max=Decimal("2000")),
        policy=ALGO_POLICY,
        workload=WorkloadSignature(
            input_len_target=32768,
            output_len_target=256,
            dataset_name="random",
            configured_concurrency=32,
        ),
        target_identity={
            "model_revision": "synthetic-revision-1",
            "framework": "vllm",
            "framework_version": "synthetic-fw-1.0",
        },
    )
    entry = ranking.entries[0]
    assert entry.rank_bucket == "requires_benchmark"
    assert entry.sla_status in (None, "not_evaluable")
    assert entry.rank_bucket != "verified_sla_met"


def test_r7_same_load_still_reaches_verified(measured_runs):
    """对照组：负载与并发都匹配时，仍能正常进入 verified_sla_met（规则不是无脑拦死）。"""
    ranking = _rank(measured_runs, "1000")
    assert ranking.entries[0].rank_bucket == "verified_sla_met"


# ==========================================================================
# V3.1 复审加固专项（本轮四类边界问题）
# ==========================================================================


def test_review_target_identity_incomplete_blocks_exact_match(curve_runs):
    """复审 P0-1：记录侧完整但**目标侧**缺 revision -> 不得 exact_match。"""
    run = next(r for r in curve_runs if r.profile.configured_concurrency == 4)
    sig = signature_from_run(run)
    full = _target_for(run, allow_synthetic=True)
    assert match(full, sig).level == "exact_match"

    for dropped in ("model_revision", "framework_version", "weight_precision"):
        partial = full.model_copy(update={dropped: None})
        result = match(partial, sig)
        assert result.level != "exact_match", dropped
        assert any("target_identity_incomplete" in r for r in result.reasons), (dropped, result.reasons)


def test_review_target_workload_incomplete_blocks_sla(curve_runs):
    """复审 P0-1：目标未填数据集/到达方式/长度/并发 -> 不得证明 SLA。"""
    run = next(r for r in curve_runs if r.profile.configured_concurrency == 4)
    sig = signature_from_run(run)
    full = _target_for(run, allow_synthetic=True)
    partial_wl = full.model_copy(
        update={
            "workload": WorkloadSignature(input_len_target=1024, output_len_target=256)
        }
    )
    result = match(partial_wl, sig)
    assert result.level != "exact_match"
    assert any("target_identity_incomplete" in r for r in result.reasons)

    evaluation = evaluate_sla(
        SlaRequirement(requirement_id="r", ttft_p99_ms_max=Decimal("400")),
        policy=ALGO_POLICY,
        run=run,
        match_result=result,
    )
    assert evaluation.status == "not_evaluable"


def test_review_relaxed_policy_cannot_produce_observed_pass(measured_runs):
    """复审 P0-2：自定义策略放宽 minimum_match_level 也不能得到生产级 observed_pass。"""
    run = next(r for r in measured_runs if r.profile.configured_concurrency == 4)
    sig = signature_from_run(run)
    # 负载不同 -> comparable_reference（长度属于 workload，不能当顶层字段传）
    target = _full_identity_target(run, input_len_target=32768)
    match_result = match(target, sig)
    assert match_result.level == "comparable_reference"

    relaxed = SlaPolicy(policy_id="relaxed", minimum_match_level="comparable_reference")
    evaluation = evaluate_sla(
        SlaRequirement(requirement_id="r", ttft_p99_ms_max=Decimal("9999")),
        policy=relaxed,
        run=run,
        match_result=match_result,
    )
    assert evaluation.status == "not_evaluable"
    assert any("sla_requires_exact_match" in r for r in evaluation.reasons)


def test_review_match_result_must_reference_this_run(measured_runs):
    """复审 P0-2：匹配结论必须针对被评价的那条 run，否则阻断。"""
    run = next(r for r in measured_runs if r.profile.configured_concurrency == 4)
    other = next(r for r in measured_runs if r.profile.configured_concurrency == 8)
    wrong_match = match(_target_for(other), signature_from_run(other))
    evaluation = evaluate_sla(
        SlaRequirement(requirement_id="r", ttft_p99_ms_max=Decimal("9999")),
        policy=ALGO_POLICY,
        run=run,
        match_result=wrong_match,
    )
    assert evaluation.status == "not_evaluable"
    assert any("match_result_run_mismatch" in r for r in evaluation.reasons)


def test_review_partial_repeats_do_not_become_sustainable(curve_runs):
    """复审 P1-1：同并发两次测试，一次通过一次失败 -> 不得认定为稳定。"""
    base = next(r for r in curve_runs if r.profile.configured_concurrency == 16)
    failing = base.model_copy(
        update={
            "run_id": base.run_id + "-failing",
            "profile": base.profile.model_copy(update={"error_rate": Decimal("0.20")}),
        }
    )
    profile = analyze_group(
        [base, failing],
        criteria=SustainabilityCriteria(
            min_duration_s=Decimal("60"), min_repeats_per_point=2
        ),
    )
    assert profile.maximum_sustainable_capacity is None
    assert profile.observed_stable_candidate is None
    assert any("concurrency_has_failing_observation" in f for f in profile.quality_flags)
    assert any("失败观测" in n for n in profile.notes)


def test_review_estimator_exact_query_respects_trust(curve_runs):
    """复审 P1-2：精确查询不得绕开可信度校验。"""
    target = next(r for r in curve_runs if r.profile.configured_concurrency == 8)
    result = estimate(
        metric="output_throughput_tok_s",
        target_concurrency=8,
        runs=curve_runs,
        target_run=target,  # synthetic，且未显式允许
    )
    assert result.estimate_kind == "not_predictable"
    assert any(
        "trust_level_not_allowed_for_estimation" in r
        or "target_run_trust_not_allowed" in r
        for r in result.reasons
    ), result.reasons


def test_review_estimator_target_run_must_be_in_scope(curve_runs, measured_runs):
    """复审 P1-2：target_run 必须与输入 run 同组。"""
    target = next(r for r in curve_runs if r.profile.configured_concurrency == 8)
    other_scope = [
        r.model_copy(
            update={
                "signature": r.signature.model_copy(update={"input_len_target": 32768}),
            }
        )
        for r in measured_runs[:3]
    ]
    # 修正 workload 里的长度（signature 与 workload 都改）
    other_scope = [
        r.model_copy(
            update={
                "signature": r.signature.model_copy(
                    update={
                        "workload": r.signature.workload.model_copy(
                            update={"input_len_target": 32768}
                        )
                    }
                )
            }
        )
        for r in other_scope
    ]
    result = estimate(
        metric="output_throughput_tok_s",
        target_concurrency=8,
        runs=other_scope,
        target_run=target,
        allowed_trust=ALGO_TRUST,
    )
    assert result.estimate_kind == "not_predictable"
    assert any(
        "target_run_not_in_scope" in r or "mixed_configurations_in_input" in r
        for r in result.reasons
    ), result.reasons


def test_review_aggregate_does_not_hide_missing_metric(measured_runs):
    """复审 P1-2：部分记录缺 TTFT P99 时，聚合不得给出该指标。"""
    from llm_capacity_pricing.perf.ranker import _aggregate_runs

    run_a = measured_runs[0]
    run_b = measured_runs[1].model_copy(
        update={
            "run_id": measured_runs[1].run_id + "-no-ttft",
            "profile": measured_runs[1].profile.model_copy(update={"ttft_p99_ms": None}),
        }
    )
    aggregated = _aggregate_runs([run_a, run_b], "exact_match")
    assert aggregated.profile.ttft_p99_ms is None          # 不得用剩余记录凑出值
    assert any(
        "aggregate_metric_missing_in_some_runs:ttft_p99_ms" in f
        for f in aggregated.profile.quality_flags
    )
    assert any("不得据此声称全部观测支持对应 SLA" in n for n in aggregated.profile.consistency_notes)


def test_review_aggregate_takes_conservative_values(measured_runs):
    """复审 P1-2：聚合取 P99 最大、吞吐最小（不取最优）。"""
    from llm_capacity_pricing.perf.ranker import _aggregate_runs

    good = measured_runs[0]
    bad = measured_runs[0].model_copy(
        update={
            "run_id": good.run_id + "-bad",
            "profile": good.profile.model_copy(
                update={
                    "ttft_p99_ms": Decimal("900"),
                    "output_throughput_tok_s": good.profile.output_throughput_tok_s / 4,
                }
            ),
        }
    )
    aggregated = _aggregate_runs([good, bad], "exact_match")
    assert aggregated.profile.ttft_p99_ms == Decimal("900")
    assert aggregated.profile.output_throughput_tok_s == bad.profile.output_throughput_tok_s
    assert "conservative_worst_case" in aggregated.profile.quality_flags
    assert aggregated.data_trust == "measured_internal"


def test_review_conservative_aggregate_fails_tight_sla(measured_runs):
    """结合体：严格阈值下，保守聚合让'较好那次'无法单独通过。"""
    ranking = _rank(_with_repeats(measured_runs, worse=True), "400")
    entry = ranking.entries[0]
    assert entry.matched_run_id.startswith("aggregate:")
    assert entry.rank_bucket == "measured_but_sla_unmet"


# ==========================================================================
# V3.1 复审第二轮专项（三条边界）
# ==========================================================================


def test_review2_strict_target_requires_deployment_identity(measured_runs):
    """边界 1：目标缺 GPU / 卡数 / TP / DP / 框架 / 并发时不得证明 SLA。"""
    run = next(r for r in measured_runs if r.profile.configured_concurrency == 4)
    sig = signature_from_run(run)
    full = _full_identity_target(run)
    assert match(full, sig).level == "exact_match"

    # 逐个抽掉部署身份字段 -> 一律不得 exact_match
    for dropped in ("hardware_model", "gpu_count", "tp_size", "dp_size", "framework"):
        partial = full.model_copy(update={dropped: None})
        result = match(partial, sig)
        assert result.level != "exact_match", dropped
        assert any("target_identity_incomplete" in r for r in result.reasons), (dropped, result.reasons)

    # 负载侧：并发缺失同样不行
    partial_wl = full.model_copy(
        update={
            "workload": full.workload.model_copy(update={"configured_concurrency": None})
        }
    )
    result = match(partial_wl, sig)
    assert result.level != "exact_match"
    assert any("workload.configured_concurrency" in r for r in result.reasons)


def test_review2_strict_target_blocks_sla_conclusion(measured_runs):
    """目标身份不全 -> 即便数值看起来达标，SLA 也不能通过。"""
    run = next(r for r in measured_runs if r.profile.configured_concurrency == 4)
    sig = signature_from_run(run)
    partial = _full_identity_target(run).model_copy(update={"hardware_model": None})
    match_result = match(partial, sig)
    assert match_result.level != "exact_match"
    evaluation = evaluate_sla(
        SlaRequirement(requirement_id="r", ttft_p99_ms_max=Decimal("9999")),
        policy=ALGO_POLICY,
        run=run,
        match_result=match_result,
    )
    assert evaluation.status == "not_evaluable"


def test_review2_full_chain_metric_gap_blocks_verified(measured_runs):
    """边界 2：同配置两条实测、其中一条缺 TTFT P99 -> 不得进入 verified_sla_met。

    完整链路：匹配（按身份收集全集）→ 聚合 → SLA → 排序。
    """
    run_a = next(r for r in measured_runs if r.profile.configured_concurrency == 8)
    run_a = run_a.model_copy(
        update={"profile": run_a.profile.model_copy(update={"ttft_p99_ms": Decimal("500")})}
    )
    run_b = run_a.model_copy(
        update={
            "run_id": run_a.run_id + "-no-ttft",
            "profile": run_a.profile.model_copy(update={"ttft_p99_ms": None}),
        }
    )
    aligned = _aligned_runs([run_a, run_b])
    # 两条都必须被匹配器接受（不能被"缺指标"提前筛掉）
    for run in aligned:
        result = match(_full_identity_target(run), signature_from_run(run))
        assert result.level == "exact_match", result.reasons

    ranking = rank_deployments(
        requirement_id="r2",
        candidates=[_candidate_x()],
        runs=aligned,
        sla_requirement=SlaRequirement(requirement_id="r2", ttft_p99_ms_max=Decimal("2000")),
        policy=ALGO_POLICY,
        workload=WorkloadSignature(
            input_len_target=1024,
            output_len_target=256,
            dataset_name="random",
            arrival_mode="unbounded",
            configured_concurrency=8,
        ),
        target_identity={
            "model_revision": "synthetic-revision-1",
            "framework": "vllm",
            "framework_version": "synthetic-fw-1.0",
        },
    )
    entry = ranking.entries[0]
    assert entry.rank_bucket != "verified_sla_met"
    assert entry.sla_status in (None, "not_evaluable")


def test_review2_aggregate_keeps_gap_visible_in_full_chain(measured_runs):
    """边界 2 的机制层面：聚合结果保留空值，SLA 因此不可评估。"""
    run_a = next(r for r in measured_runs if r.profile.configured_concurrency == 8)
    run_b = run_a.model_copy(
        update={
            "run_id": run_a.run_id + "-no-ttft",
            "profile": run_a.profile.model_copy(update={"ttft_p99_ms": None}),
        }
    )
    from llm_capacity_pricing.perf.ranker import _aggregate_runs

    aggregated = _aggregate_runs([run_a, run_b], "exact_match")
    evaluation = evaluate_sla(
        SlaRequirement(requirement_id="r", ttft_p99_ms_max=Decimal("2000")),
        policy=ALGO_POLICY,
        run=aggregated,
        match_result=match(_full_identity_target(aggregated), signature_from_run(aggregated)),
    )
    assert evaluation.status == "not_evaluable"
    assert any(
        c.constraint == "ttft_p99_ms" and c.status == "not_evaluable" for c in evaluation.constraints
    )


def test_review2_estimator_rejects_unauthorized_target_run_alone(curve_runs):
    """边界 3：runs=[] 且 target_run 可信等级未授权 -> 必须 not_predictable。"""
    synthetic = next(r for r in curve_runs if r.profile.configured_concurrency == 8)
    result = estimate(
        metric="output_throughput_tok_s",
        target_concurrency=8,
        runs=[],
        target_run=synthetic,  # 未授权（默认不允许 synthetic）
    )
    assert result.estimate_kind == "not_predictable"
    assert result.value is None
    assert any("target_run_trust_not_allowed" in r for r in result.reasons)


def test_review2_estimator_allows_target_run_when_explicitly_permitted(curve_runs):
    """对照组：显式允许 synthetic 时，精确查询仍可返回 measured_reference。"""
    synthetic = next(r for r in curve_runs if r.profile.configured_concurrency == 8)
    result = estimate(
        metric="output_throughput_tok_s",
        target_concurrency=8,
        runs=[],
        target_run=synthetic,
        allowed_trust=ALGO_TRUST,
    )
    assert result.estimate_kind == "measured_reference"
    assert result.value == synthetic.profile.output_throughput_tok_s


# ==========================================================================
# V3.1 最终复审第三轮（记录侧并发 + 聚合证据归属）
# ==========================================================================


def _strip_record_concurrency(run: BenchmarkRun) -> BenchmarkRun:
    """抹掉记录侧的配置并发（模拟"记了长度却没记并发"的实测记录）。"""
    return run.model_copy(
        update={
            "signature": run.signature.model_copy(
                update={
                    "workload": run.signature.workload.model_copy(
                        update={"configured_concurrency": None}
                    )
                }
            )
        }
    )


def test_review3_record_missing_concurrency_blocks_exact_match(measured_runs):
    """P0 反向用例：**需求有并发、记录缺并发** -> 不得 exact_match。

    此前并发只在"双方都有值"时才比较，于是"需求并发 8 / 记录并发 None"会因
    `_compare` 把缺失当相等而判 exact_match —— 这是最后一处严格匹配缺口。
    """
    run = next(r for r in measured_runs if r.profile.configured_concurrency == 8)
    sig = signature_from_run(run)
    assert sig.workload.configured_concurrency == 8
    # 对照：两侧并发齐备且一致 -> exact_match
    assert match(_full_identity_target(run), sig).level == "exact_match"

    result = match(_full_identity_target(run), _strip_record_concurrency(run).signature)
    assert result.level == "comparable_reference", result.reasons
    assert any(
        "record_identity_incomplete" in r and "workload.configured_concurrency" in r
        for r in result.reasons
    ), result.reasons
    # 降级理由必须显式写出"未确认"而不是悄悄放过
    assert any("downgraded:unverified_details_present" in r for r in result.reasons)


def test_review3_record_missing_concurrency_blocks_sla_and_ranking(measured_runs):
    """全链路：记录缺并发 -> 等级降级 -> SLA not_evaluable -> 不进 verified_sla_met。"""
    stripped = _strip_record_concurrency(
        next(r for r in measured_runs if r.profile.configured_concurrency == 8)
    )
    evaluation = evaluate_sla(
        # 阈值极其宽松：数值上一定"达标"，唯一的阻断理由是身份不完整
        SlaRequirement(requirement_id="r", ttft_p99_ms_max=Decimal("99999")),
        policy=ALGO_POLICY,
        run=stripped,
        match_result=match(_full_identity_target(stripped), signature_from_run(stripped)),
    )
    assert evaluation.status == "not_evaluable"
    assert any("sla_requires_exact_match" in r for r in evaluation.reasons)

    ranking = rank_deployments(
        requirement_id="r3-concurrency",
        candidates=[_candidate_x()],
        runs=_aligned_runs([stripped]),
        sla_requirement=SlaRequirement(requirement_id="r", ttft_p99_ms_max=Decimal("99999")),
        policy=ALGO_POLICY,
        workload=WorkloadSignature(
            input_len_target=1024,
            output_len_target=256,
            dataset_name="random",
            arrival_mode="unbounded",
            configured_concurrency=8,
        ),
        target_identity={
            "model_revision": "synthetic-revision-1",
            "framework": "vllm",
            "framework_version": "synthetic-fw-1.0",
        },
    )
    assert ranking.entries[0].rank_bucket != "verified_sla_met"
    assert ranking.entries[0].sla_status == "not_evaluable"


def test_review3_aggregate_proof_has_own_identity_and_full_attribution(measured_runs):
    """P1：聚合结果的匹配证明必须独立，并逐条记录参与 Run 的等级与来源。"""
    from llm_capacity_pricing.perf.ranker import _best_match

    runs = _aligned_runs(_with_repeats(measured_runs, concurrency=8))
    target = _full_identity_target(next(r for r in runs if r.profile.configured_concurrency == 8))
    best_run, proof = _best_match(target, runs)
    assert best_run is not None and proof is not None
    assert best_run.run_id.startswith("aggregate:")

    participants = sorted(r.run_id for r in runs if r.profile.configured_concurrency == 8)
    assert len(participants) == 2                      # 一次原始观测 + 一次重复观测
    assert sorted(proof.supporting_run_ids) == participants
    assert set(proof.per_run_levels) == set(participants)
    assert all(v == "exact_match" for v in proof.per_run_levels.values())
    assert set(proof.per_run_source_refs) == set(participants)
    # 每条参与 Run 各自的来源可追溯（不是共用首条的身份）
    assert all(proof.per_run_source_refs[rid] == rid for rid in participants)

    # 聚合证据有自己的签名身份，不再是首条 Run 的签名
    assert proof.signature_id == best_run.run_id
    assert proof.source_ref == best_run.run_id
    assert best_run.signature.signature_id == best_run.run_id
    first = next(r for r in runs if r.profile.configured_concurrency == 8)
    assert proof.signature_id != first.signature.signature_id
    assert any("evidence_aggregate" in r for r in proof.reasons), proof.reasons


def test_review3_aggregate_proof_is_never_more_permissive_than_members(measured_runs):
    """聚合证明的用途取交集、禁用项取并集、可信取最弱：不得比单条更宽松。"""
    from llm_capacity_pricing.perf.ranker import _aggregate_match_proof, _aggregate_runs

    measured = measured_runs[0]
    synthetic = measured.model_copy(
        update={
            "run_id": measured.run_id + "-synthetic",
            "data_trust": "synthetic_fixture",
            "signature": measured.signature.model_copy(
                update={"data_trust": "synthetic_fixture"}
            ),
        }
    )
    target = _full_identity_target(measured).model_copy(update={"allow_synthetic": True})
    results = [
        (measured, match(target, signature_from_run(measured))),
        (synthetic, match(target, signature_from_run(synthetic))),
    ]
    assert all(r.level == "exact_match" for _run, r in results)
    assert "sla_proof" in results[1][1].blocked_from        # synthetic 单条已阻断 sla_proof

    proof = _aggregate_match_proof(
        _aggregate_runs([measured, synthetic], "exact_match"), results, "exact_match"
    )
    assert "sla_proof" in proof.blocked_from                # 并集：聚合同样被阻断
    assert proof.data_trust == "synthetic_fixture"          # 最弱可信等级
    assert proof.algorithm_validation_only is True          # 保守：仍只限算法验证
    assert len(proof.supporting_run_ids) == 2


def test_review3_ranking_entry_exposes_all_evidence_runs(measured_runs):
    """排序条目必须列出**全部**支撑 Run，而不只是代表 Run。"""
    ranking = _rank(_with_repeats(measured_runs, concurrency=8), "1000")
    entry = ranking.entries[0]
    assert entry.matched_run_id.startswith("aggregate:")
    participants = sorted(
        r.run_id
        for r in _aligned_runs(_with_repeats(measured_runs, concurrency=8))
        if r.profile.configured_concurrency == 8
    )
    assert sorted(entry.evidence_run_ids) == participants
    assert set(entry.evidence_per_run_levels) == set(participants)


def test_review3_single_run_evidence_is_attributed_too(measured_runs):
    """单条匹配也要带证据构成（对照）：supporting_run_ids 恰好是自己。"""
    from llm_capacity_pricing.perf.ranker import _best_match

    run = next(r for r in _aligned_runs(measured_runs) if r.profile.configured_concurrency == 8)
    best_run, proof = _best_match(_full_identity_target(run), [run])
    assert best_run is not None and proof is not None and not best_run.run_id.startswith("aggregate:")
    assert proof.supporting_run_ids == [run.run_id]
    assert proof.per_run_levels == {run.run_id: "exact_match"}
