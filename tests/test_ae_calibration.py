"""AE5：预测与实测的精确匹配/误差契约。"""

from __future__ import annotations

from llm_capacity_pricing.perf.calibration import compare_metrics, match_identity


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


def test_exact_identity_computes_errors():
    benchmark = _prediction(
        p99_ttft_ms=1100.0,
        p99_tpot_ms=18.0,
        output_throughput_tok_s=550.0,
    )
    result = compare_metrics(_prediction(), benchmark)
    assert result["matched"] is True
    assert result["calibration_usable"] is True
    assert result["errors"]["ttft_p99_ms"]["absolute"] == "-100.0"
    assert result["errors"]["tpot_p99_ms"]["relative"] == "0.1111111111111111111111111111"


def test_mismatch_is_not_calibration_usable():
    benchmark = _prediction(tp_size=2)
    result = compare_metrics(_prediction(), benchmark)
    assert result["matched"] is False
    assert result["calibration_usable"] is False
    assert any("TP" in item for item in result["identity_differences"])
    assert result["errors"] == {}


def test_missing_measured_metric_stays_unknown():
    benchmark = _prediction(p99_ttft_ms=None)
    result = compare_metrics(_prediction(), benchmark)
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


def test_zero_measured_metric_stays_unknown():
    benchmark = _prediction(p99_ttft_ms="0")
    result = compare_metrics(_prediction(), benchmark)
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
