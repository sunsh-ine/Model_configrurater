"""AF0/AF6：统一 P99 门禁与报告证据分层。"""

from __future__ import annotations

from types import SimpleNamespace

from llm_capacity_pricing.perf.sla_gate import (
    MIN_SAMPLES_FOR_P99,
    evaluate_p99_gate,
)
from llm_capacity_pricing.reporting.plan_report import (
    _candidate_evidence_text,
    _evidence_summary,
    _reliability_workload_text,
    _workload_identity_text,
)


def _gate(**overrides):
    data = {
        "ttft_p99_ms": 1000,
        "tpot_p99_ms": 20,
        "ttft_samples": MIN_SAMPLES_FOR_P99,
        "tpot_samples": MIN_SAMPLES_FOR_P99,
        "ttft_limit_ms": 2000,
        "tpot_limit_ms": 30,
    }
    data.update(overrides)
    return evaluate_p99_gate(**data)


def test_no_threshold_means_no_verdict():
    result = _gate(ttft_limit_ms=None, tpot_limit_ms=None)
    assert result.verdict == "insufficient_prediction"
    assert any("阈值" in item for item in result.reasons)


def test_missing_p99_means_no_verdict():
    result = _gate(ttft_p99_ms=None)
    assert result.verdict == "insufficient_prediction"
    assert any("缺少预测值" in item for item in result.reasons)


def test_insufficient_samples_means_no_verdict():
    result = _gate(ttft_samples=MIN_SAMPLES_FOR_P99 - 1)
    assert result.verdict == "insufficient_prediction"
    assert any("样本不足" in item for item in result.reasons)


def test_unconfigured_metric_does_not_require_samples():
    result = _gate(tpot_limit_ms=None, tpot_samples=None)
    assert result.verdict == "predicted_pass"


def test_threshold_comparison_is_shared():
    assert _gate(ttft_p99_ms=2000).verdict == "predicted_pass"
    assert _gate(ttft_p99_ms=2000.1).verdict == "predicted_fail"
    assert _gate(tpot_p99_ms=30).verdict == "predicted_pass"
    assert _gate(tpot_p99_ms=30.1).verdict == "predicted_fail"


def test_workload_identity_text_includes_dataset_and_seed():
    workload = SimpleNamespace(
        arrival_mode="poisson",
        request_rate_rps=5,
        concurrency=None,
        input_len=8192,
        output_len=256,
        dataset="synthetic",
        seed=3,
    )
    text = _workload_identity_text(workload)
    assert "5 req/s" in text
    assert "poisson" in text
    assert "synthetic" in text
    assert "seed 3" in text


def test_candidate_evidence_type_is_explicit():
    multi = SimpleNamespace(
        repeatability_status="mixed",
        repeatability_pass_count=3,
        repeatability_runs=5,
        verdict="predicted_pass",
    )
    assert "多 seed" in _candidate_evidence_text(multi)
    unknown = SimpleNamespace(
        repeatability_status=None,
        verdict="insufficient_prediction",
    )
    assert "unknown" in _candidate_evidence_text(unknown)
    single = SimpleNamespace(repeatability_status=None, verdict="predicted_pass")
    assert _candidate_evidence_text(single) == "单次模拟"
    incomplete = SimpleNamespace(
        repeatability_status=None,
        repeatability_runs=2,
        repeatability_pass_count=1,
        verdict="predicted_pass",
    )
    assert "重复性 unknown" in _candidate_evidence_text(incomplete)


def test_reliability_workload_text_is_identity_not_just_status():
    item = {
        "model_name": "qwen3-8b",
        "system": "h200_sxm",
        "tp_size": 1,
        "pp_size": 1,
        "dp_size": 2,
        "input_len": 8192,
        "output_len": 256,
        "concurrency": None,
        "arrival_mode": "poisson",
        "request_rate_rps": 5,
        "dataset": "synthetic",
    }
    text = _reliability_workload_text(item)
    assert "qwen3-8b" in text
    assert "h200_sxm" in text
    assert "TP1×PP1×DP2" in text
    assert "5 req/s" in text


def test_evidence_summary_separates_measured_simulation_and_unknown():
    result = SimpleNamespace(
        candidates=[
            SimpleNamespace(candidate_id="m1", sla_status="observed_pass"),
            SimpleNamespace(candidate_id="m2", sla_status="not_evaluable"),
        ]
    )
    simulation = SimpleNamespace(
        candidates=[
            SimpleNamespace(candidate_id="s1", verdict="predicted_pass"),
            SimpleNamespace(candidate_id="s2", verdict="insufficient_prediction"),
        ],
        reliability=lambda: [{"status": "mixed"}],
        search_audit=SimpleNamespace(effective_scope="partial"),
    )
    lines: list[str] = []
    _evidence_summary(result, simulation, lines.append)
    text = "\n".join(lines)
    assert "实测证据：1 个候选" in text
    assert "模拟证据：1 个候选" in text
    assert "预测不足（unknown）：1 个" in text
    assert "不完整" in text


def _sim_candidate(candidate_id: str, verdict: str, *, status: str, pass_count: int):
    from decimal import Decimal

    from llm_capacity_pricing.schemas.simulation import (
        SimulationCandidate,
        SimulationDeployment,
        SimulationMetrics,
        SimulationResult,
        SimulationWorkload,
    )

    candidate = SimulationCandidate(
        candidate_id=candidate_id,
        deployment=SimulationDeployment(
            model_name="qwen3-8b",
            model_precision="bf16",
            system="h200_sxm",
            gpu_count=2,
            tp_size=1,
            pp_size=1,
            dp_size=2,
        ),
        workload=SimulationWorkload(
            input_len=8192,
            output_len=256,
            arrival_mode="poisson",
            request_rate_rps=Decimal("5"),
            dataset="synthetic",
            seed=1,
        ),
        metrics=SimulationMetrics(
            sim_p99_ttft_ms=Decimal("1000"),
            sim_p99_tpot_ms=Decimal("20"),
            sim_ttft_samples=100,
            sim_tpot_samples=100,
            sim_output_throughput_tok_s=Decimal("100"),
        ),
        verdict=verdict,
        comparable=True,
        resource_feasible=True,
        within_gpu_budget=True,
        repeatability_status=status,
        repeatability_runs=5,
        repeatability_pass_count=pass_count,
    )
    return SimulationResult(status="computed", candidates=[candidate])


def test_mixed_seed_group_cannot_be_a_formal_recommendation():
    result = _sim_candidate("s1", "predicted_pass", status="mixed", pass_count=3)
    candidate = result.candidates[0]
    assert result.recommended() is None
    assert any("多 seed 重复性不足" in item for item in result.rejection_reasons(candidate))


def test_all_pass_seed_group_can_be_recommended():
    result = _sim_candidate("s1", "predicted_pass", status="all_pass", pass_count=5)
    assert result.recommended() is not None
    assert result.rejection_reasons(result.candidates[0]) == []


def _af_archive_spec(seed: int):
    from llm_capacity_pricing.app.offline_archives import find_archives
    from llm_capacity_pricing.schemas.plan import SimulationArchiveSpec

    entries = find_archives(
        model_id="qwen3-8b",
        hardware_id="nvidia-h200-sxm-141gb",
        input_len=8192,
        output_len=256,
        concurrency=None,
        precision="bf16",
        framework="vllm",
        framework_version="0.24.0",
        gpu_limit=None,
        tp_sizes=(1,),
        arrival_mode="poisson",
        request_rate_rps="5",
        seed=seed,
        min_requests=500,
    )
    entry = next(item for item in entries if item.dp_size == 2)
    return SimulationArchiveSpec(
        native_prediction=entry.native_prediction,
        native_input=entry.native_input,
        hardware_id="nvidia-h200-sxm-141gb",
        database_mode=entry.database_mode,
        database_mode_source=entry.database_mode_source,
        exp_config=entry.exp_config,
        native_model_precision=entry.precision,
        tool_version=entry.tool_version,
        label=entry.name,
    )


def test_single_seed_is_not_labelled_repeatable():
    from decimal import Decimal

    from llm_capacity_pricing.perf.aisimulate_adapter import (
        SimulationTarget,
        load_archived,
    )

    result = load_archived(
        archives=[_af_archive_spec(1)],
        target=SimulationTarget(
            ttft_p99_ms_max=Decimal("6000"),
            tpot_p99_ms_max=Decimal("40"),
        ),
    )
    assert result.candidates
    assert all(c.repeatability_status is None for c in result.candidates)
    assert result.reliability() == []


def test_two_distinct_seeds_create_repeatability_evidence():
    from decimal import Decimal

    from llm_capacity_pricing.perf.aisimulate_adapter import (
        SimulationTarget,
        load_archived,
    )

    result = load_archived(
        archives=[_af_archive_spec(1), _af_archive_spec(2)],
        target=SimulationTarget(
            ttft_p99_ms_max=Decimal("6000"),
            tpot_p99_ms_max=Decimal("40"),
        ),
    )
    # 没有实验清单时，多 seed 只能证明“找到的两次”，不能声称完整实验。
    assert all(c.repeatability_status == "incomplete" for c in result.candidates)
    assert all(c.experiment_completeness == "incomplete" for c in result.candidates)


def test_complete_two_seed_manifest_yields_a_complete_classification():
    from decimal import Decimal

    from llm_capacity_pricing.perf.aisimulate_adapter import (
        SimulationTarget,
        load_archived,
    )
    from llm_capacity_pricing.perf.experiment_audit import load_manifests

    base = next(
        item
        for item in load_manifests()
        if item.experiment_id == "af-8b-h200-dp2-r5"
    )
    manifest = base.model_copy(
        update={
            "expected_seeds": [1, 2],
            "runs": [run for run in base.runs if run.seed in (1, 2)],
        }
    )
    result = load_archived(
        archives=[_af_archive_spec(1), _af_archive_spec(2)],
        target=SimulationTarget(
            ttft_p99_ms_max=Decimal("6000"),
            tpot_p99_ms_max=Decimal("40"),
        ),
        experiment_manifests=[manifest],
    )
    assert all(c.experiment_completeness == "complete" for c in result.candidates)
    assert {c.repeatability_status for c in result.candidates} <= {
        "all_pass",
        "all_fail",
        "mixed",
    }


def test_reliability_groups_different_request_counts_separately():
    result = _sim_candidate("s1", "predicted_pass", status="all_pass", pass_count=5)
    first = result.candidates[0]
    second = first.model_copy(deep=True)
    second.candidate_id = "s2"
    second.workload.num_requests = 100
    first.workload.num_requests = 500
    result.candidates.append(second)
    assert len(result.reliability()) == 2


def test_reliability_groups_different_backend_versions_separately():
    result = _sim_candidate("s1", "predicted_pass", status="all_pass", pass_count=5)
    first = result.candidates[0]
    first.deployment.backend = "vllm"
    first.deployment.backend_version = "0.24.0"
    second = first.model_copy(deep=True)
    second.candidate_id = "s2"
    second.deployment.backend_version = "0.25.0"
    result.candidates.append(second)
    assert len(result.reliability()) == 2


def test_incomplete_seed_group_cannot_be_a_formal_recommendation():
    result = _sim_candidate("s1", "predicted_pass", status="incomplete", pass_count=1)
    candidate = result.candidates[0]
    assert result.recommended() is None
    assert any("重复性未判定" in item for item in result.rejection_reasons(candidate))


def test_run_verdict_aggregation_never_selects_a_passing_seed():
    from llm_capacity_pricing.perf.sla_gate import aggregate_run_verdicts

    assert aggregate_run_verdicts(["predicted_pass", "predicted_pass"]) == (
        "predicted_pass",
        2,
    )
    assert aggregate_run_verdicts(["predicted_pass", "predicted_fail"]) == (
        "mixed",
        1,
    )
    assert aggregate_run_verdicts(["predicted_pass", "insufficient_prediction"]) == (
        "unknown",
        1,
    )
    assert aggregate_run_verdicts([]) == ("unknown", 0)


def test_non_finite_p99_is_not_a_verdict():
    assert _gate(ttft_p99_ms="NaN").verdict == "insufficient_prediction"
    assert _gate(tpot_p99_ms="Infinity").verdict == "insufficient_prediction"
    assert _gate(ttft_p99_ms="-Infinity").verdict == "insufficient_prediction"
