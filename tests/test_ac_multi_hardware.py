"""AC2/AC4：多硬件模拟分流与跨硬件比较。"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from llm_capacity_pricing.app.demo_service import prepare_demo_db
from llm_capacity_pricing.app.offline_archives import find_archives
from llm_capacity_pricing.schemas.performance import SlaRequirement
from llm_capacity_pricing.schemas.plan import (
    PlanRequest,
    PlanResourceAssumptions,
    PlanSimulationInput,
    PlanWorkload,
    SimulationArchiveSpec,
)
from llm_capacity_pricing.workflow import evaluate_plan


def _archive(hardware_id: str, tp: int) -> SimulationArchiveSpec:
    entries = find_archives(
        model_id="qwen3-32b",
        hardware_id=hardware_id,
        input_len=8192,
        output_len=256,
        concurrency=32,
        precision="bf16",
        framework="vllm",
        framework_version="0.24.0",
        gpu_limit=8,
        tp_sizes=(tp,),
    )
    assert entries
    entry = entries[0]
    return SimulationArchiveSpec(
        native_prediction=entry.native_prediction,
        native_input=entry.native_input,
        hardware_id=hardware_id,
        native_model_precision=entry.precision,
        tool_version=entry.tool_version,
        exp_config=entry.exp_config,
        label=entry.name,
    )


def _request(*, archives, hardware_ids) -> PlanRequest:
    return PlanRequest(
        request_id="ac-multi",
        model_id="qwen3-32b",
        hardware_ids=hardware_ids,
        max_total_gpus=8,
        resources=PlanResourceAssumptions(
            usable_fraction=Decimal("0.9"),
            usable_source_kind="synthetic_example",
            runtime_reserve_fraction=Decimal("0.1"),
        ),
        workload=PlanWorkload(
            input_len=8192,
            output_len=256,
            concurrency=32,
            dataset="random",
            arrival_mode="unbounded",
        ),
        sla=SlaRequirement(
            requirement_id="ac-sla",
            ttft_p99_ms_max=Decimal("6000"),
            tpot_p99_ms_max=Decimal("40"),
        ),
        advanced={
            "weight_precision": "bf16",
            "framework": "vllm",
            "framework_version": "0.24.0",
            "tp_sizes": [1, 2, 4, 8],
            "dp_sizes": [1],
        },
        simulation=PlanSimulationInput(archives=archives),
    )


def _natives(result):
    return [
        c
        for c in result.simulation.candidates
        if c.candidate_id.startswith("sim-native")
    ]


def test_multi_hardware_simulates_each_gpu_independently(tmp_path):
    db = prepare_demo_db(tmp_path / "demo.db")
    archives = [
        _archive("nvidia-h100-sxm-80gb", 8),
        _archive("nvidia-h200-sxm-141gb", 2),
    ]
    result = evaluate_plan(
        _request(
            archives=archives,
            hardware_ids=["nvidia-h100-sxm-80gb", "nvidia-h200-sxm-141gb"],
        ),
        db_path=str(db),
    )
    natives = _natives(result)
    systems = {c.deployment.system for c in natives}
    assert systems == {"h100_sxm", "h200_sxm"}
    h100 = next(c for c in natives if c.deployment.system == "h100_sxm")
    h200 = next(c for c in natives if c.deployment.system == "h200_sxm")
    assert h100.metrics.sim_p99_ttft_ms == Decimal("5201.827252000001")
    assert h200.metrics.sim_p99_ttft_ms == Decimal("14356.537065000002")
    # 跨硬件不得复用 P99
    assert h100.metrics.sim_p99_ttft_ms != h200.metrics.sim_p99_ttft_ms
    recommended = result.simulation.recommended("throughput")
    assert recommended is not None and recommended.deployment.system == "h100_sxm"
    assert result.simulation.search_audit is not None
    assert result.simulation.search_audit.replayed_deployments == 2
    assert result.simulation.search_audit.effective_scope == "offline_archives"


def test_multi_hardware_without_matching_archive_is_static_only(tmp_path):
    db = prepare_demo_db(tmp_path / "demo.db")
    result = evaluate_plan(
        _request(
            archives=[_archive("nvidia-h100-sxm-80gb", 8)],
            hardware_ids=["nvidia-h100-sxm-80gb", "nvidia-h200-sxm-141gb"],
        ),
        db_path=str(db),
    )
    natives = _natives(result)
    assert {c.deployment.system for c in natives} == {"h100_sxm"}
    # H200 的静态候选仍在主结果里，但没有预测排名资格
    assert any(
        candidate.hardware_id == "nvidia-h200-sxm-141gb"
        for candidate in result.candidates
    )
    assert any("h200" in item.lower() or "nvidia-h200" in item.lower() for item in result.simulation.limitations)
    assert all(
        c.deployment.system != "h200_sxm"
        for c in natives
    )


def test_single_hardware_path_still_works(tmp_path):
    db = prepare_demo_db(tmp_path / "demo.db")
    result = evaluate_plan(
        _request(
            archives=[_archive("nvidia-h100-sxm-80gb", 8)],
            hardware_ids=["nvidia-h100-sxm-80gb"],
        ),
        db_path=str(db),
    )
    assert [c.deployment.system for c in _natives(result)] == ["h100_sxm"]
    assert result.simulation.search_audit is not None
    assert result.simulation.search_audit.effective_scope == "offline_archives"


def test_multi_hardware_report_distinguishes_gpus(tmp_path):
    from llm_capacity_pricing.reporting.plan_report import build_plan_report

    db = prepare_demo_db(tmp_path / "demo.db")
    result = evaluate_plan(
        _request(
            archives=[
                _archive("nvidia-h100-sxm-80gb", 8),
                _archive("nvidia-h200-sxm-141gb", 2),
            ],
            hardware_ids=["nvidia-h100-sxm-80gb", "nvidia-h200-sxm-141gb"],
        ),
        db_path=str(db),
    )
    text = build_plan_report(result)
    assert "h100_sxm" in text and "h200_sxm" in text
    assert "多硬件请求" in text
    min_gpu = result.simulation.min_gpu_for_sla()
    assert min_gpu is not None and min_gpu.deployment.system == "h100_sxm"


def test_cross_hardware_recommends_better_independent_evidence(tmp_path):
    db = prepare_demo_db(tmp_path / "demo.db")
    result = evaluate_plan(
        _request(
            archives=[
                _archive("nvidia-h100-sxm-80gb", 8),
                _archive("nvidia-h200-sxm-141gb", 8),
            ],
            hardware_ids=["nvidia-h100-sxm-80gb", "nvidia-h200-sxm-141gb"],
        ),
        db_path=str(db),
    )
    natives = {c.deployment.system: c for c in _natives(result)}
    assert natives["h200_sxm"].metrics.sim_p99_ttft_ms == Decimal(
        "4955.538856"
    )
    assert natives["h200_sxm"].metrics.sim_output_throughput_tok_s == Decimal(
        "1082.997232236789"
    )
    recommended = result.simulation.recommended("throughput")
    assert recommended is not None
    assert recommended.deployment.system == "h200_sxm"


def test_noncomputed_hardware_candidates_are_not_merged(monkeypatch):
    from types import SimpleNamespace

    from llm_capacity_pricing import workflow
    from llm_capacity_pricing.schemas.simulation import (
        SimulationCandidate,
        SimulationDeployment,
        SimulationMetrics,
        SimulationResult,
    )

    def candidate(system: str) -> SimulationCandidate:
        return SimulationCandidate(
            candidate_id=f"sim-native:{system}-tp8",
            deployment=SimulationDeployment(
                model_name="Qwen/Qwen3-32B",
                system=system,
                gpu_count=8,
                tp_size=8,
                pp_size=1,
                dp_size=1,
                serving_mode="agg",
            ),
            metrics=SimulationMetrics(
                sim_p99_ttft_ms=Decimal("100"),
                sim_p99_tpot_ms=Decimal("10"),
                sim_output_throughput_tok_s=Decimal("1000"),
            ),
            verdict="predicted_pass",
            comparable=True,
            evidence_status="predicted_pass",
        )

    def fake_simulate(request, *, model_repo_id, hardware_id, db_path, cost_items):
        if hardware_id == "nvidia-h200-sxm-141gb":
            # 非 computed 状态即使带候选，也不得进入跨硬件推荐池
            return SimulationResult(
                status="error",
                message="h200 failed",
                candidates=[candidate("h200_sxm")],
            )
        return SimulationResult(
            status="computed", candidates=[candidate("h100_sxm")]
        )

    monkeypatch.setattr(workflow, "_simulate", fake_simulate)
    request = SimpleNamespace(simulation=SimpleNamespace(archives=[]))
    merged = workflow._simulate_all(
        request,
        model_repo_id="Qwen/Qwen3-32B",
        hardware_ids=["nvidia-h100-sxm-80gb", "nvidia-h200-sxm-141gb"],
        db_path=None,
        cost_items=None,
    )
    assert [c.deployment.system for c in merged.candidates] == ["h100_sxm"]
    assert any("h200" in item for item in merged.limitations)


def test_archive_without_hardware_id_derives_from_native_input(tmp_path):
    db = prepare_demo_db(tmp_path / "demo.db")
    h200 = _archive("nvidia-h200-sxm-141gb", 8)
    h200 = h200.model_copy(update={"hardware_id": None})
    result = evaluate_plan(
        _request(
            archives=[_archive("nvidia-h100-sxm-80gb", 8), h200],
            hardware_ids=["nvidia-h100-sxm-80gb", "nvidia-h200-sxm-141gb"],
        ),
        db_path=str(db),
    )
    assert {c.deployment.system for c in _natives(result)} == {"h100_sxm", "h200_sxm"}


def test_legacy_unscoped_input_is_not_reused_across_hardware(tmp_path):
    db = prepare_demo_db(tmp_path / "demo.db")
    from llm_capacity_pricing.schemas.plan import PlanSimulationInput

    legacy = PlanSimulationInput(
        native_prediction="data/simulation/archives/poc-qwen3-32b-h100-tp8-c32-bf16/predict/out_1/prediction.json",
        native_input="data/simulation/archives/poc-qwen3-32b-h100-tp8-c32-bf16/predict/predict_1.yaml",
        exp_config="data/simulation/archives/poc-qwen3-32b-h100-tp8-c32-bf16/exp_config.yaml",
        native_model_precision="bf16",
        tool_version="0.12.1",
    )
    request = _request(
        archives=[_archive("nvidia-h100-sxm-80gb", 8)],
        hardware_ids=["nvidia-h100-sxm-80gb", "nvidia-h200-sxm-141gb"],
    ).model_copy(update={"simulation": legacy})
    result = evaluate_plan(request, db_path=str(db))
    assert [c.deployment.system for c in _natives(result)] == ["h100_sxm"]
    assert any("h200" in item.lower() for item in result.simulation.limitations)


def test_archives_path_clears_unscoped_top_level_evidence(tmp_path):
    db = prepare_demo_db(tmp_path / "demo.db")
    base = _request(
        archives=[_archive("nvidia-h100-sxm-80gb", 8)],
        hardware_ids=["nvidia-h100-sxm-80gb", "nvidia-h200-sxm-141gb"],
    )
    sim = base.simulation.model_copy(
        update={
            "native_prediction": "data/simulation/archives/poc-qwen3-32b-h100-tp8-c32-bf16/predict/out_1/prediction.json",
            "native_input": "data/simulation/archives/poc-qwen3-32b-h100-tp8-c32-bf16/predict/predict_1.yaml",
            "exp_config": "data/simulation/archives/poc-qwen3-32b-h100-tp8-c32-bf16/exp_config.yaml",
            "native_model_precision": "bf16",
        }
    )
    result = evaluate_plan(base.model_copy(update={"simulation": sim}), db_path=str(db))
    natives = _natives(result)
    assert [c.deployment.system for c in natives] == ["h100_sxm"]
    assert len({c.candidate_id for c in natives}) == 1


def test_auto_search_is_not_blocked_by_archives_for_another_hardware(monkeypatch):
    from types import SimpleNamespace

    from llm_capacity_pricing import workflow
    from llm_capacity_pricing.schemas.plan import PlanSimulationInput
    from llm_capacity_pricing.schemas.simulation import (
        SimulationCandidate,
        SimulationDeployment,
        SimulationResult,
    )

    def candidate(system: str):
        return SimulationCandidate(
            candidate_id=f"sim-native:{system}-tp8",
            deployment=SimulationDeployment(
                model_name="Qwen/Qwen3-32B",
                system=system,
                gpu_count=8,
                tp_size=8,
                pp_size=1,
                dp_size=1,
                serving_mode="agg",
            ),
            verdict="predicted_pass",
            comparable=True,
            evidence_status="predicted_pass",
        )

    calls = []

    def fake_simulate(request, *, model_repo_id, hardware_id, db_path, cost_items):
        calls.append(hardware_id)
        system = "h100_sxm" if hardware_id.startswith("nvidia-h100") else "h200_sxm"
        return SimulationResult(status="computed", candidates=[candidate(system)])

    monkeypatch.setattr(workflow, "_simulate", fake_simulate)
    config = PlanSimulationInput(
        enable_external=True,
        auto_search=True,
        work_dir="data/exports/unused",
        archives=[_archive("nvidia-h100-sxm-80gb", 8)],
    )
    merged = workflow._simulate_all(
        SimpleNamespace(simulation=config),
        model_repo_id="Qwen/Qwen3-32B",
        hardware_ids=["nvidia-h100-sxm-80gb", "nvidia-h200-sxm-141gb"],
        db_path=None,
        cost_items=None,
    )
    assert calls == ["nvidia-h100-sxm-80gb", "nvidia-h200-sxm-141gb"]
    assert {c.deployment.system for c in merged.candidates} == {"h100_sxm", "h200_sxm"}
