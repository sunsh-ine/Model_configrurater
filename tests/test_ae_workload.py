"""AE0/AE1：数据库模式来源与负载身份。"""

from __future__ import annotations

from decimal import Decimal

from llm_capacity_pricing.app.offline_archives import archive_entries
from llm_capacity_pricing.perf.aisimulate_adapter import (
    SimulationTarget,
    check_comparability,
)
from llm_capacity_pricing.schemas.simulation import (
    SimulationCandidate,
    SimulationDeployment,
    SimulationWorkload,
)


def test_archive_entries_expose_database_mode_source():
    entries = [
        entry
        for entry in archive_entries()
        if entry.name.startswith("ae-live-qwen3-8b-h100-tp1dp2-c40")
    ]
    assert entries
    assert entries[0].database_mode == "SILICON"
    assert entries[0].database_mode_source == "archive_declared"


def _candidate(arrival_mode: str, **workload):
    return SimulationCandidate(
        candidate_id="test",
        deployment=SimulationDeployment(
            model_name="Qwen/Qwen3-8B",
            model_precision="bf16",
            system="h100_sxm",
            backend="vllm",
            backend_version="0.24.0",
            gpu_count=2,
            tp_size=1,
            pp_size=1,
            dp_size=2,
            serving_mode="agg",
        ),
        workload=SimulationWorkload(
            input_len=8192,
            output_len=256,
            arrival_mode=arrival_mode,
            **workload,
        ),
    )


def _target(arrival_mode: str, **kwargs):
    return SimulationTarget(
        model_name="Qwen/Qwen3-8B",
        model_precision="bf16",
        system="h100_sxm",
        backend="vllm",
        backend_version="0.24.0",
        input_len=8192,
        output_len=256,
        arrival_mode=arrival_mode,
        **kwargs,
    )


def test_fixed_concurrency_does_not_match_poisson():
    candidate = _candidate(
        "poisson", request_rate_rps=Decimal("3"), concurrency=None, seed=42
    )
    check_comparability(candidate, _target("fixed_concurrency", concurrency=32))
    assert candidate.comparable is False
    assert any("到达模式不同" in reason for reason in candidate.comparability_reasons)


def test_poisson_requires_same_rate():
    candidate = _candidate(
        "poisson", request_rate_rps=Decimal("3"), concurrency=None, seed=42
    )
    check_comparability(
        candidate, _target("poisson", request_rate_rps=Decimal("4"))
    )
    assert candidate.comparable is False
    assert any("请求到达率不同" in reason for reason in candidate.comparability_reasons)

    candidate = _candidate(
        "poisson", request_rate_rps=Decimal("3"), concurrency=None, seed=7
    )
    check_comparability(
        candidate, _target("poisson", request_rate_rps=Decimal("3"))
    )
    assert candidate.comparable is True


def test_fixed_concurrency_same_workpoint_is_comparable():
    candidate = _candidate("fixed_concurrency", concurrency=32)
    check_comparability(candidate, _target("fixed_concurrency", concurrency=32))
    assert candidate.comparable is True


def test_poisson_archive_parses_arrival_identity():
    from llm_capacity_pricing.perf.aisimulate_adapter import parse_native_input_yaml

    entry = next(
        entry
        for entry in archive_entries()
        if entry.name.startswith("ae-live-qwen3-8b-h100-tp1dp2-poisson-r3-seed42")
    )
    _, workload = parse_native_input_yaml(
        entry.native_input, model_precision="bf16"
    )
    assert workload.arrival_mode == "poisson"
    assert workload.request_rate_rps == Decimal("3")
    assert workload.seed == 42
    assert workload.concurrency is None
    assert workload.num_requests == 500


def test_plan_workload_carries_request_rate():
    from llm_capacity_pricing.schemas.plan import PlanWorkload

    workload = PlanWorkload(
        input_len=8192,
        output_len=256,
        concurrency=None,
        arrival_mode="poisson",
        request_rate_rps=Decimal("3"),
    )
    assert workload.arrival_mode == "poisson"
    assert workload.request_rate_rps == Decimal("3")
    # 默认仍是显式 unbounded，不是隐式 unknown
    default = PlanWorkload(input_len=8192)
    assert default.arrival_mode == "unbounded"


def test_offline_poisson_selection_pins_the_requested_rate():
    from llm_capacity_pricing.app.offline_archives import offline_simulation_paths

    paths = offline_simulation_paths(
        model_id="qwen3-8b",
        hardware_id="nvidia-h100-sxm-80gb",
        input_len=8192,
        output_len=256,
        concurrency=None,
        precision="bf16",
        framework="vllm",
        framework_version="0.24.0",
        gpu_limit=2,
        tp_sizes=(1,),
        arrival_mode="poisson",
        request_rate_rps="3",
    )
    assert paths is not None
    labels = [item["label"] for item in paths["archives"]]
    assert labels
    assert all("r3" in label for label in labels)


def test_demo_poisson_request_selects_rate_and_ignores_concurrency():
    from llm_capacity_pricing.app.demo_service import DemoInputs, build_plan_request

    request = build_plan_request(
        DemoInputs(
            model_id="qwen3-8b",
            hardware_id="nvidia-h100-sxm-80gb",
            gpu_count=2,
            concurrency=32,
            arrival_mode="poisson",
            request_rate_rps=Decimal("3"),
        )
    )
    assert request.workload.arrival_mode == "poisson"
    assert request.workload.request_rate_rps == Decimal("3")
    assert request.simulation is not None
    labels = [item.label for item in request.simulation.archives]
    assert labels and all("r3" in label for label in labels)


def test_live_open_arrival_is_rejected_explicitly():
    import pytest

    from llm_capacity_pricing.app.demo_service import DemoInputs, build_plan_request

    with pytest.raises(ValueError, match="live 自动搜索目前只支持固定并发"):
        build_plan_request(
            DemoInputs(
                mode="live",
                arrival_mode="poisson",
                request_rate_rps=Decimal("3"),
            )
        )


def test_trace_identity_must_match():
    from llm_capacity_pricing.perf.aisimulate_adapter import (
        SimulationTarget,
        check_comparability,
    )

    candidate = _candidate("trace", concurrency=None, trace_id="trace-a")
    target = SimulationTarget(
        model_name="Qwen/Qwen3-8B",
        model_precision="bf16",
        system="h100_sxm",
        backend="vllm",
        backend_version="0.24.0",
        input_len=8192,
        output_len=256,
        arrival_mode="trace",
        trace_id="trace-b",
    )
    check_comparability(candidate, target)
    assert candidate.comparable is False
    assert any("trace 标识不同" in item for item in candidate.comparability_reasons)


def test_multi_seed_reliability_is_exposed(tmp_path):
    from decimal import Decimal

    from llm_capacity_pricing.app.demo_service import DemoInputs, run_demo

    outcome = run_demo(
        DemoInputs(
            model_id="qwen3-8b",
            hardware_id="nvidia-h200-sxm-141gb",
            gpu_count=2,
            tp_sizes=(1,),
            concurrency=32,
            arrival_mode="poisson",
            request_rate_rps=Decimal("5"),
            ttft_p99_ms_max=Decimal("6000"),
            tpot_p99_ms_max=Decimal("40"),
        ),
        out_dir=tmp_path / "multi-seed",
        write_files=False,
    )
    sim = outcome.result.simulation
    assert sim is not None
    reliability = sim.reliability()
    assert len(reliability) == 1
    # AF 归档中的 seed42 被清单明确排除，不参与本实验聚合。
    assert reliability[0]["status"] == "mixed"
    assert reliability[0]["experiment_completeness"] == "complete"
    assert reliability[0]["experiment_expected_runs"] == 5
    assert reliability[0]["experiment_found_runs"] == 5
    assert reliability[0]["experiment_unexpected_runs"] == 0
    assert reliability[0]["runs"] >= 5
    assert reliability[0]["pass_count"] >= 1
    assert reliability[0]["pass_count"] < reliability[0]["runs"]
    assert reliability[0]["system"] == "h200_sxm"
    assert reliability[0]["arrival_mode"] == "poisson"
    assert reliability[0]["request_rate_rps"] == Decimal("5")
    assert reliability[0]["dataset"]
    assert "多 seed 重复性" in outcome.technical_md
    assert "工作点" in outcome.technical_md
    assert "证据分层" in outcome.technical_md
