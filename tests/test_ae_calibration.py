"""AE5/AF4：预测与实测的精确匹配/误差/证据契约。"""

from __future__ import annotations

import hashlib
import json

from llm_capacity_pricing.perf.calibration import compare_metrics, match_identity

_METRICS = ("ttft_p99_ms", "tpot_p99_ms", "output_throughput_tok_s")
_DEFINITIONS = {
    "ttft_p99_ms": "m.latency.ttft.p99",
    "tpot_p99_ms": "m.latency.tpot.p99",
    "output_throughput_tok_s": "m.throughput.cluster.output",
}


def _prediction(**overrides):
    data = {
        "model_id": "qwen3-8b",
        "model_revision": "rev1",
        "weight_precision": "bf16",
        "kv_precision": "bf16",
        "hardware_id": "nvidia-h100-sxm-80gb",
        "gpu_count": 1,
        "framework": "vllm",
        "framework_version": "0.24.0",
        "tp_size": 1,
        "dp_size": 1,
        "pp_size": 1,
        "input_len": 8192,
        "output_len": 256,
        "arrival_mode": "fixed_concurrency",
        "concurrency": 32,
        "dataset": "synthetic",
        "metric_definitions": dict(_DEFINITIONS),
        "p99_ttft_ms": 1000.0,
        "p99_tpot_ms": 20.0,
        "output_throughput_tok_s": 500.0,
        "evidence": {
            "source_file": "fixtures/bench.json",
            "source_sha256": "a" * 64,
        },
    }
    data.update(overrides)
    return data


def _write_evidence(tmp_path, payload=None):
    path = tmp_path / "bench.json"
    path.write_text(
        json.dumps(payload or {"source": "measured"}, ensure_ascii=False),
        encoding="utf-8",
    )
    return path, hashlib.sha256(path.read_bytes()).hexdigest()


def _benchmark_with_evidence(tmp_path, **overrides):
    path, digest = _write_evidence(tmp_path)
    return _prediction(
        evidence={"source_file": str(path), "source_sha256": digest},
        **overrides,
    )


def test_exact_identity_computes_errors(tmp_path):
    benchmark = _benchmark_with_evidence(
        tmp_path,
        p99_ttft_ms=1100.0,
        p99_tpot_ms=18.0,
        output_throughput_tok_s=550.0,
    )
    result = compare_metrics(_prediction(), benchmark, evidence_root=tmp_path)
    assert result["matched"] is True
    assert result["calibration_usable"] is True
    assert result["evidence"]["verified"] is True
    assert result["metric_definition_errors"] == []
    assert result["errors"]["ttft_p99_ms"]["absolute"] == "-100.0"
    assert result["errors"]["tpot_p99_ms"]["relative"] == "0.1111111111111111111111111111"


def test_mismatch_is_not_calibration_usable():
    benchmark = _prediction(tp_size=2)
    result = compare_metrics(_prediction(), benchmark)
    assert result["matched"] is False
    assert result["calibration_usable"] is False
    assert any("TP" in item for item in result["identity_differences"])
    assert result["errors"] == {}


def test_missing_measured_metric_stays_unknown(tmp_path):
    benchmark = _benchmark_with_evidence(tmp_path, p99_ttft_ms=None)
    result = compare_metrics(_prediction(), benchmark, evidence_root=tmp_path)
    assert result["matched"] is True
    assert result["calibration_usable"] is False
    assert result["errors"]["ttft_p99_ms"]["absolute"] is None


def test_arrival_mode_and_rate_must_match():
    pred = _prediction(arrival_mode="poisson", concurrency=None, request_rate_rps="3")
    benchmark = _prediction(
        arrival_mode="fixed_concurrency", concurrency=32, request_rate_rps=None
    )
    matched, reasons = match_identity(pred, benchmark)
    assert matched is False
    assert any("到达模式" in item for item in reasons)

    poisson_bench = _prediction(
        arrival_mode="poisson", concurrency=None, request_rate_rps="4"
    )
    matched, reasons = match_identity(pred, poisson_bench)
    assert matched is False
    assert any("到达率" in item for item in reasons)


def test_trace_requires_trace_id():
    pred = _prediction(arrival_mode="trace", concurrency=None, trace_id=None)
    benchmark = _prediction(arrival_mode="trace", concurrency=None, trace_id=None)
    matched, reasons = match_identity(pred, benchmark)
    assert matched is False
    assert any("trace_id" in item for item in reasons)

    pred["trace_id"] = "trace-a"
    benchmark["trace_id"] = "trace-b"
    matched, reasons = match_identity(pred, benchmark)
    assert matched is False
    assert any("trace" in item for item in reasons)


def test_numeric_rate_equivalence():
    pred = _prediction(arrival_mode="poisson", concurrency=None, request_rate_rps="3")
    bench = _prediction(arrival_mode="poisson", concurrency=None, request_rate_rps="3.0")
    matched, reasons = match_identity(pred, bench)
    assert matched is True, reasons


def test_zero_measured_metric_stays_unknown(tmp_path):
    benchmark = _benchmark_with_evidence(tmp_path, p99_ttft_ms="0")
    result = compare_metrics(_prediction(), benchmark, evidence_root=tmp_path)
    assert result["matched"] is True
    assert result["errors"]["ttft_p99_ms"]["absolute"] is None
    assert result["calibration_usable"] is False


def test_unknown_arrival_mode_is_not_calibration_usable():
    pred = _prediction(arrival_mode="unknown", concurrency=None)
    benchmark = _prediction(arrival_mode="unknown", concurrency=None)
    matched, reasons = match_identity(pred, benchmark)
    assert matched is False
    assert any("到达模式" in item for item in reasons)


def test_calibration_requires_benchmark_provenance():
    benchmark = _prediction()
    benchmark["evidence"] = {}
    result = compare_metrics(_prediction(), benchmark)
    assert result["calibration_usable"] is False
    assert "缺少原始文件" in result["note"]
    assert result["evidence"]["verified"] is False


def test_missing_source_file_is_not_calibration_usable(tmp_path):
    benchmark = _prediction(
        evidence={
            "source_file": str(tmp_path / "missing.json"),
            "source_sha256": "a" * 64,
        }
    )
    result = compare_metrics(_prediction(), benchmark, evidence_root=tmp_path)
    assert result["matched"] is True
    assert result["calibration_usable"] is False
    assert "不存在" in result["note"]


def test_source_hash_mismatch_is_not_calibration_usable(tmp_path):
    path, _ = _write_evidence(tmp_path)
    benchmark = _prediction(
        evidence={"source_file": str(path), "source_sha256": "b" * 64}
    )
    result = compare_metrics(_prediction(), benchmark, evidence_root=tmp_path)
    assert result["calibration_usable"] is False
    assert result["evidence"]["actual_sha256"] == hashlib.sha256(
        path.read_bytes()
    ).hexdigest()
    assert "哈希不一致" in result["note"]


def test_relative_source_is_resolved_against_evidence_root(tmp_path):
    path, digest = _write_evidence(tmp_path)
    benchmark = _prediction(
        evidence={
            "source_file": path.name,
            "source_sha256": digest,
        }
    )
    result = compare_metrics(_prediction(), benchmark, evidence_root=tmp_path)
    assert result["evidence"]["verified"] is True
    assert result["calibration_usable"] is True


def test_missing_metric_definitions_is_not_calibration_usable(tmp_path):
    benchmark = _benchmark_with_evidence(tmp_path)
    prediction = _prediction()
    prediction["metric_definitions"] = {}
    result = compare_metrics(prediction, benchmark, evidence_root=tmp_path)
    assert result["calibration_usable"] is False
    assert any("未声明" in item for item in result["metric_definition_errors"])


def test_metric_definition_substitution_is_rejected(tmp_path):
    benchmark = _benchmark_with_evidence(tmp_path)
    benchmark["metric_definitions"] = dict(benchmark["metric_definitions"])
    benchmark["metric_definitions"]["tpot_p99_ms"] = "m.latency.itl.p99"
    result = compare_metrics(_prediction(), benchmark, evidence_root=tmp_path)
    assert result["calibration_usable"] is False
    assert any("标准定义" in item for item in result["metric_definition_errors"])


def test_metric_definition_mismatch_between_sides_is_rejected(tmp_path):
    benchmark = _benchmark_with_evidence(tmp_path)
    prediction = _prediction()
    prediction["metric_definitions"] = dict(prediction["metric_definitions"])
    prediction["metric_definitions"]["ttft_p99_ms"] = "m.latency.ttft.mean"
    result = compare_metrics(prediction, benchmark, evidence_root=tmp_path)
    assert result["calibration_usable"] is False
    assert any("口径不同" in item for item in result["metric_definition_errors"])


def test_concurrency_workpoint_mismatch_is_not_calibration_usable(tmp_path):
    benchmark = _benchmark_with_evidence(tmp_path, concurrency=64)
    result = compare_metrics(_prediction(), benchmark, evidence_root=tmp_path)
    assert result["matched"] is False
    assert any("并发不同" in item for item in result["identity_differences"])
    assert result["calibration_usable"] is False


def test_unverified_evidence_does_not_publish_numeric_errors(tmp_path):
    path, _ = _write_evidence(tmp_path)
    benchmark = _prediction(
        evidence={"source_file": str(path), "source_sha256": "b" * 64}
    )
    result = compare_metrics(_prediction(), benchmark, evidence_root=tmp_path)
    assert result["calibration_usable"] is False
    for error in result["errors"].values():
        assert error["absolute"] is None
        assert error["relative"] is None


def test_metric_definition_mismatch_does_not_publish_numeric_errors(tmp_path):
    benchmark = _benchmark_with_evidence(tmp_path)
    benchmark["metric_definitions"] = dict(benchmark["metric_definitions"])
    benchmark["metric_definitions"]["tpot_p99_ms"] = "m.latency.itl.p99"
    result = compare_metrics(_prediction(), benchmark, evidence_root=tmp_path)
    assert result["calibration_usable"] is False
    for error in result["errors"].values():
        assert error["absolute"] is None
        assert error["relative"] is None
