"""AA：多候选部署比较与证据完整性。"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from llm_capacity_pricing.app import offline_archives as oa
from llm_capacity_pricing.app.archive_integrity import verify_manifest_hashes
from llm_capacity_pricing.app.demo_service import DemoInputs, run_demo
from scripts.build_performance_coverage import build, render_markdown


def _natives(result):
    sim = result.simulation
    if sim is None:
        return []
    return [c for c in sim.candidates if c.candidate_id.startswith("sim-native")]


def _run(tmp_path: Path, name: str, **overrides):
    values = {
        "concurrency": 32,
        "ttft_p99_ms_max": Decimal("6000"),
        "tpot_p99_ms_max": Decimal("40"),
        "request_id": f"aa-{name}",
    }
    values.update(overrides)
    return run_demo(DemoInputs(**values), out_dir=tmp_path / name)


def test_c32_compares_tp4_and_tp8_and_recommends_tp8(tmp_path):
    outcome = _run(tmp_path, "c32-relaxed")
    natives = {c.deployment.tp_size: c for c in _natives(outcome.result)}
    assert set(natives) == {4, 8}
    assert natives[4].verdict == "predicted_fail"
    assert natives[8].verdict == "predicted_pass"
    assert natives[4].metrics.sim_p99_ttft_ms == Decimal("8359.441161")
    assert natives[8].metrics.sim_p99_ttft_ms == Decimal("5201.827252000001")

    recommended = outcome.result.simulation.recommended("throughput")
    assert recommended is not None
    assert recommended.deployment.tp_size == 8
    assert recommended.metrics.sim_output_throughput_tok_s == Decimal(
        "990.888548064229"
    )


def test_strict_sla_has_no_recommendation_but_keeps_best_effort(tmp_path):
    outcome = _run(
        tmp_path,
        "c32-strict",
        ttft_p99_ms_max=Decimal("400"),
        tpot_p99_ms_max=Decimal("30"),
    )
    sim = outcome.result.simulation
    natives = {c.deployment.tp_size: c for c in _natives(outcome.result)}
    assert {c.verdict for c in natives.values()} == {"predicted_fail"}
    assert sim.recommended("throughput") is None
    best = sim.best_effort()
    assert best is not None and best.deployment.tp_size == 8


def test_gpu_cap_excludes_tp8(tmp_path):
    outcome = _run(tmp_path, "c32-gpu4", gpu_count=4)
    natives = _natives(outcome.result)
    assert {c.deployment.tp_size for c in natives} == {4}
    assert outcome.result.simulation.recommended("throughput") is None


def test_tp_whitelist_excludes_tp8(tmp_path):
    outcome = _run(tmp_path, "c32-tp4-only", tp_sizes=(4,))
    natives = _natives(outcome.result)
    assert {c.deployment.tp_size for c in natives} == {4}
    assert outcome.result.simulation.recommended("throughput") is None


def test_no_native_p99_cannot_claim_sla_pass(tmp_path):
    outcome = _run(tmp_path, "c16", concurrency=16)
    assert _natives(outcome.result) == []
    assert all(
        candidate.verdict != "predicted_pass"
        for candidate in outcome.result.simulation.candidates
    )
    assert outcome.result.simulation.recommended("throughput") is None


def test_incomplete_cost_does_not_claim_cost_optimum(tmp_path):
    outcome = _run(
        tmp_path,
        "cost",
        ranking="cost",
        budget_amount=Decimal("1000000"),
    )
    sim = outcome.result.simulation
    available, reasons = sim.cost_ranking_available()
    assert available is False
    assert reasons
    # 成本不可比时降级为吞吐优先；仍然只在预测达标候选里选
    recommended = sim.recommended("cost")
    assert recommended is not None and recommended.verdict == "predicted_pass"
    assert recommended.deployment.tp_size == 8


def test_c8_and_c64_predictions_are_unchanged(tmp_path):
    c8 = {c.deployment.tp_size: c for c in _natives(_run(tmp_path, "c8", concurrency=8).result)}
    c64 = {c.deployment.tp_size: c for c in _natives(_run(tmp_path, "c64", concurrency=64).result)}
    assert 8 in c8 and 8 in c64
    # AB2 新增 TP4 归档；TP8 的原始预测值必须保持不变
    assert 4 in c8 and 4 in c64
    assert c8[8].metrics.sim_p99_ttft_ms == Decimal("727.2969240000001")
    assert c8[8].metrics.sim_p99_tpot_ms == Decimal("11.230072717647314")
    assert c8[8].verdict == "predicted_pass"
    assert c64[8].metrics.sim_p99_ttft_ms == Decimal("11211.532603000005")
    assert c64[8].metrics.sim_p99_tpot_ms == Decimal("56.43859718823514")
    assert c64[8].verdict == "predicted_fail"


def test_manifest_tamper_is_rejected(tmp_path, monkeypatch):
    run = tmp_path / "archives" / "run"
    (run / "predict" / "out_1").mkdir(parents=True)
    input_path = run / "predict" / "predict_1.yaml"
    pred_path = run / "predict" / "out_1" / "prediction.json"
    config = {
        "engine": {
            "backend": "vllm",
            "backend_version": "0.24.0",
            "hardware": "h100_sxm",
            "mode": "aggregated",
            "model": "Qwen/Qwen3-32B",
            "workers": {"aggregated": {"parallelism": {"pipeline": 1, "replicas": 1, "tensor": 8}}},
        },
        "traffic": {
            "load": {"concurrency": 32, "type": "concurrency"},
            "source": {"input_tokens": 8192, "output_tokens": 256, "type": "synthetic"},
            "stop": {"requests": 500},
        },
    }
    input_path.write_text(yaml.safe_dump(config), encoding="utf-8")
    pred_path.write_text(
        json.dumps({"p99_ttft_ms": 1.0, "p99_tpot_ms": 1.0, "completed_requests": 500}),
        encoding="utf-8",
    )
    import hashlib

    manifest = {
        "run_id": "run",
        "tool_version": "0.12.1",
        "spec": {"precision": "bf16", "database_mode": "SILICON"},
        "errors": [],
        "inputs": {
            "predict/predict_1.yaml": hashlib.sha256(input_path.read_bytes()).hexdigest()
        },
        "outputs": {
            "predict/out_1/prediction.json": hashlib.sha256(pred_path.read_bytes()).hexdigest()
        },
    }
    (run / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setattr(oa, "ARCHIVE_ROOT", tmp_path / "archives")
    monkeypatch.setattr(oa, "ROOT", tmp_path)
    assert len(oa.archive_entries()) == 1

    pred_path.write_text(
        json.dumps({"p99_ttft_ms": 999999.0, "p99_tpot_ms": 1.0, "completed_requests": 500}),
        encoding="utf-8",
    )
    assert verify_manifest_hashes(run, manifest)
    assert oa.archive_entries() == []


def test_coverage_summary_and_evidence_ids_are_dynamic():
    doc = build()
    native_ids = [
        item["evidence_id"]
        for item in doc["evidence"]
        if item["source_kind"] == "simulated_native"
    ]
    assert len(native_ids) == len(set(native_ids))
    assert any("predict_1" in evidence_id for evidence_id in native_ids)

    md = render_markdown(doc)
    assert "当前只有 Qwen3-32B" not in md
    for concurrency in (8, 32, 64):
        assert f"C{concurrency}" in md
    assert "TP4" in md and "TP8" in md


def test_manifest_requires_both_inputs_and_outputs(tmp_path):
    from llm_capacity_pricing.app.archive_integrity import verify_manifest_hashes

    pred = tmp_path / "prediction.json"
    pred.write_text("{}", encoding="utf-8")
    import hashlib

    digest = hashlib.sha256(pred.read_bytes()).hexdigest()
    assert verify_manifest_hashes(
        tmp_path, {"inputs": {}, "outputs": {"prediction.json": digest}}
    )
    assert verify_manifest_hashes(
        tmp_path, {"inputs": {"prediction.json": digest}, "outputs": {}}
    )


def test_required_evidence_file_must_be_registered(tmp_path):
    from llm_capacity_pricing.app.archive_integrity import verify_manifest_hashes

    other = tmp_path / "other.txt"
    other.write_text("other", encoding="utf-8")
    pred = tmp_path / "prediction.json"
    pred.write_text("{}", encoding="utf-8")
    import hashlib

    manifest = {
        "inputs": {"other.txt": hashlib.sha256(other.read_bytes()).hexdigest()},
        "outputs": {"prediction.json": hashlib.sha256(pred.read_bytes()).hexdigest()},
    }
    assert verify_manifest_hashes(tmp_path, manifest, required=["prediction.json"]) == []
    reasons = verify_manifest_hashes(
        tmp_path, manifest, required=["predict/predict_1.yaml", "prediction.json"]
    )
    assert any("未登记实际使用的文件" in reason for reason in reasons)
