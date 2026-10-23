"""AD2/AD3/AD4：TP/DP 并行接入、同资源比较与容量边界。"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from llm_capacity_pricing.app.demo_service import DemoInputs, run_demo
from llm_capacity_pricing.app.offline_archives import find_archives
from llm_capacity_pricing.perf.aisimulate_adapter import (
    SimulationTarget,
    deployment_identity_reasons,
)
from llm_capacity_pricing.schemas.simulation import (
    SimulationCandidate,
    SimulationDeployment,
)


def _run(tmp_path: Path, name: str, **overrides):
    values = {
        "concurrency": 32,
        "ttft_p99_ms_max": Decimal("6000"),
        "tpot_p99_ms_max": Decimal("40"),
        "request_id": f"ad-{name}",
    }
    values.update(overrides)
    return run_demo(DemoInputs(**values), out_dir=tmp_path / name, write_files=False)


def _natives(result):
    return [
        c
        for c in result.simulation.candidates
        if c.candidate_id.startswith("sim-native")
    ]


def test_dp_archives_are_selected_with_full_gpu_count():
    entries = find_archives(
        model_id="qwen3-32b",
        hardware_id="nvidia-h100-sxm-80gb",
        input_len=8192,
        output_len=256,
        concurrency=32,
        precision="bf16",
        framework="vllm",
        framework_version="0.24.0",
        gpu_limit=8,
        tp_sizes=(4,),
    )
    by_dp = {entry.dp_size: entry for entry in entries}
    assert set(by_dp) >= {1, 2}
    assert by_dp[2].gpu_count == 8


def test_dp_is_allowed_but_pp_is_not_for_recommendation():
    target = SimulationTarget(
        model_name="Qwen/Qwen3-32B",
        model_precision="bf16",
        system="h100_sxm",
        backend="vllm",
        backend_version="0.24.0",
        input_len=8192,
        output_len=256,
        concurrency=32,
    )

    def candidate(dp: int, pp: int) -> SimulationCandidate:
        return SimulationCandidate(
            candidate_id=f"test-dp{dp}-pp{pp}",
            deployment=SimulationDeployment(
                model_name="Qwen/Qwen3-32B",
                model_precision="bf16",
                system="h100_sxm",
                backend="vllm",
                backend_version="0.24.0",
                gpu_count=8,
                tp_size=4,
                pp_size=pp,
                dp_size=dp,
                serving_mode="agg",
            ),
        )

    assert deployment_identity_reasons(candidate(2, 1), target) == []
    assert any("PP=2" in reason for reason in deployment_identity_reasons(candidate(1, 2), target))


def test_c32_same_8gpu_compares_tp8dp1_and_tp4dp2(tmp_path):
    result = _run(tmp_path, "c32").result
    by_shape = {
        (c.deployment.tp_size, c.deployment.dp_size): c for c in _natives(result)
    }
    assert (8, 1) in by_shape and (4, 2) in by_shape
    assert by_shape[(8, 1)].verdict == "predicted_pass"
    assert by_shape[(4, 2)].verdict == "predicted_pass"
    assert by_shape[(4, 2)].metrics.sim_p99_ttft_ms == Decimal("4448.276797")
    assert by_shape[(4, 2)].metrics.sim_output_throughput_tok_s == Decimal(
        "1062.4468369728693"
    )
    recommended = result.simulation.recommended("throughput")
    assert recommended is not None
    assert (recommended.deployment.tp_size, recommended.deployment.dp_size) == (4, 2)


def test_8b_dp2_makes_c32_pass_and_keeps_smallest_sla_gpu(tmp_path):
    result = _run(
        tmp_path,
        "8b-c32",
        model_id="qwen3-8b",
        hardware_id="nvidia-h100-sxm-80gb",
        gpu_count=4,
    ).result
    by_dp = {
        c.deployment.dp_size: c
        for c in _natives(result)
        if c.deployment.tp_size == 1
    }
    assert by_dp[1].verdict == "predicted_fail"
    assert by_dp[2].verdict == "predicted_pass"
    assert by_dp[4].verdict == "predicted_pass"
    # 吞吐优先选 DP4；最小达标卡数是 DP2（2 卡）
    assert result.simulation.recommended("throughput").deployment.dp_size == 4
    assert result.simulation.min_gpu_for_sla().deployment.dp_size == 2


def test_verified_capacity_scan_does_not_interpolate(tmp_path):
    import json
    import subprocess
    import sys

    out_json = tmp_path / "cap.json"
    out_md = tmp_path / "cap.md"
    subprocess.run(
        [
            sys.executable,
            "scripts/capacity_scan.py",
            "--model-id",
            "qwen3-8b",
            "--hardware-id",
            "nvidia-h100-sxm-80gb",
            "--tp",
            "1",
            "--dp",
            "2",
            "--input-len",
            "8192",
            "--output-len",
            "256",
            "--ttft",
            "6000",
            "--tpot",
            "40",
            "--concurrency",
            "8",
            "32",
            "64",
            "96",
            "128",
            "--json-out",
            str(out_json),
            "--md-out",
            str(out_md),
        ],
        check=True,
    )
    doc = json.loads(out_json.read_text(encoding="utf-8"))
    assert doc["highest_verified_pass"] == 32
    statuses = {p["concurrency"]: p["status"] for p in doc["points"]}
    assert statuses[8] == "predicted_pass"
    assert statuses[32] == "predicted_pass"
    assert statuses[64] == "predicted_fail"
    assert statuses[96] == "predicted_fail"
    assert statuses[128] == "predicted_fail"
    assert "不做插值" in doc["note"]


def test_candidates_carry_their_own_tool_provenance(tmp_path):
    result = _run(tmp_path, "provenance").result
    natives = _natives(result)
    assert natives
    for candidate in natives:
        assert candidate.source_tool_version == "0.12.1"
        assert candidate.source_database_mode == "SILICON"
        assert any("证据工具" in note for note in candidate.notes)


def test_capacity_scan_marks_missing_p99_unknown(tmp_path, monkeypatch):
    import json
    import sys

    from scripts import capacity_scan as cap
    from llm_capacity_pricing.app.offline_archives import ArchiveEntry

    pred = tmp_path / "prediction.json"
    pred.write_text(json.dumps({"completed_requests": 500}), encoding="utf-8")
    entry = ArchiveEntry(
        name="fake",
        model_name="Qwen/Qwen3-8B",
        system="h100_sxm",
        precision="bf16",
        backend="vllm",
        backend_version="0.24.0",
        input_len=8192,
        output_len=256,
        concurrency=32,
        gpu_count=2,
        tp_size=1,
        requests=500,
        native_prediction=str(pred),
        native_input="unused",
        exp_config=None,
        per_ops_source=None,
        tool_version="0.12.1",
        dp_size=2,
    )
    monkeypatch.setattr(cap, "find_archives", lambda **kwargs: [entry])
    out_json = tmp_path / "cap.json"
    out_md = tmp_path / "cap.md"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "capacity_scan.py",
            "--model-id", "qwen3-8b",
            "--hardware-id", "nvidia-h100-sxm-80gb",
            "--tp", "1", "--dp", "2",
            "--input-len", "8192", "--output-len", "256",
            "--ttft", "6000", "--tpot", "40",
            "--concurrency", "32",
            "--json-out", str(out_json), "--md-out", str(out_md),
        ],
    )
    assert cap.main() == 0
    doc = json.loads(out_json.read_text(encoding="utf-8"))
    assert doc["points"][0]["status"] == "unknown"
    assert doc["highest_verified_pass"] is None
