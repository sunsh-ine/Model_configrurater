"""AG4：Benchmark 导入管线的无 GPU 回归。"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
import yaml

from llm_capacity_pricing.perf.benchmark_ingest import (
    load_requests,
    recompute_metrics,
    run_import_pipeline,
)

FIXTURE = Path("data/examples/benchmark_pipeline")
RAW = FIXTURE / "requests.jsonl"
METADATA = yaml.safe_load((FIXTURE / "metadata.yaml").read_text(encoding="utf-8"))


def test_fixture_recomputes_counts_and_metrics():
    metrics = recompute_metrics(load_requests(RAW))
    assert metrics["num_requests"] == 100
    assert metrics["success_count"] == 96
    assert metrics["failure_count"] == 3
    assert metrics["cancelled_count"] == 1
    assert metrics["ttft_p99_ms"] is not None
    assert metrics["tpot_p99_ms"] is not None
    assert metrics["output_throughput_tok_s"] is not None


def test_pipeline_accepts_fixture_and_cross_checks_import():
    result = run_import_pipeline(RAW, METADATA, evidence_root=FIXTURE)
    assert result["accepted"] is True
    assert result["errors"] == []
    assert result["import_consistency"]["consistent"] is True
    assert result["imported"]["success_count"] == 96
    assert result["identity_sha256"] == METADATA["frozen_identity_sha256"]


def test_tampered_raw_file_is_rejected(tmp_path):
    raw = tmp_path / "requests.jsonl"
    shutil.copy(RAW, raw)
    raw.write_text(raw.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    result = run_import_pipeline(raw, METADATA, evidence_root=tmp_path)
    assert result["accepted"] is False
    assert any("SHA256" in item for item in result["errors"])


def test_missing_declared_hash_is_rejected(tmp_path):
    raw = tmp_path / "requests.jsonl"
    shutil.copy(RAW, raw)
    metadata = json.loads(json.dumps(METADATA))
    metadata["evidence"]["source_sha256"] = None
    result = run_import_pipeline(raw, metadata, evidence_root=tmp_path)
    assert result["accepted"] is False
    assert any("缺少原始文件 SHA256" in item for item in result["errors"])


def test_changed_identity_after_freeze_is_rejected():
    metadata = json.loads(json.dumps(METADATA))
    metadata["model_revision"] = "changed-after-freeze"
    result = run_import_pipeline(RAW, metadata, evidence_root=FIXTURE)
    assert result["accepted"] is False
    assert any("冻结配置身份哈希" in item for item in result["errors"])


def test_import_rejects_unknown_throughput_definition(tmp_path):
    metadata = json.loads(json.dumps(METADATA))
    metadata["metric_definitions"]["output_throughput_tok_s"] = "m.throughput.unknown"
    result = run_import_pipeline(RAW, metadata, evidence_root=FIXTURE)
    assert result["accepted"] is False
    assert any("指标口径" in item for item in result["errors"])
    prediction = {
        "model_id": "qwen3-8b",
        "model_revision": "fixture-rev-001",
        "weight_precision": "bf16",
        "kv_precision": "bf16",
        "hardware_id": "nvidia-h200-sxm-141gb",
        "gpu_count": 2,
        "framework": "sglang",
        "framework_version": "0.5.6",
        "tp_size": 1,
        "dp_size": 2,
        "pp_size": 1,
        "input_len": 8192,
        "output_len": 256,
        "arrival_mode": "fixed_concurrency",
        "concurrency": 32,
        "dataset": "synthetic",
        "p99_ttft_ms": 237.0,
        "p99_tpot_ms": 12.0,
        "output_throughput_tok_s": 1000.0,
        "metric_definitions": {
            "ttft_p99_ms": "m.latency.ttft.p99",
            "tpot_p99_ms": "m.latency.tpot.p99",
            "output_throughput_tok_s": "m.throughput.unknown",
        },
    }
    result = run_import_pipeline(
        RAW, metadata, evidence_root=FIXTURE, prediction=prediction
    )
    assert result["accepted"] is False


def test_fixture_calibration_is_never_usable():
    metrics = recompute_metrics(load_requests(RAW))
    prediction = {
        "model_id": "qwen3-8b",
        "model_revision": "fixture-rev-001",
        "weight_precision": "bf16",
        "kv_precision": "bf16",
        "hardware_id": "nvidia-h200-sxm-141gb",
        "gpu_count": 2,
        "framework": "sglang",
        "framework_version": "0.5.6",
        "tp_size": 1,
        "dp_size": 2,
        "pp_size": 1,
        "input_len": 8192,
        "output_len": 256,
        "arrival_mode": "fixed_concurrency",
        "concurrency": 32,
        "dataset": "synthetic",
        "p99_ttft_ms": float(metrics["ttft_p99_ms"]),
        "p99_tpot_ms": float(metrics["tpot_p99_ms"]),
        "output_throughput_tok_s": float(metrics["output_throughput_tok_s"]),
        "metric_definitions": metrics["metric_definitions"],
    }
    result = run_import_pipeline(
        RAW, METADATA, evidence_root=FIXTURE, prediction=prediction
    )
    assert result["calibration"]["matched"] is True
    assert result["calibration"]["calibration_usable"] is False
    assert result["calibration"]["calibration_status"] == "fixture_only"
    assert result["calibration"]["errors"]["ttft_p99_ms"]["absolute"] == "0.0"


def test_pipeline_can_ingest_into_database(tmp_path):
    result = run_import_pipeline(
        RAW,
        METADATA,
        evidence_root=FIXTURE,
        metadata_file=FIXTURE / "metadata.yaml",
        db_path=tmp_path / "bench.db",
    )
    assert result["accepted"] is True
    assert result["database"]["records"]
    assert result["database"]["evidence_ids"]


def test_missing_frozen_identity_hash_is_rejected():
    metadata = json.loads(json.dumps(METADATA))
    metadata.pop("frozen_identity_sha256")
    result = run_import_pipeline(RAW, metadata, evidence_root=FIXTURE)
    assert result["accepted"] is False
    assert any("未冻结" in item for item in result["errors"])


def test_malformed_request_log_is_rejected_without_crash(tmp_path):
    raw = tmp_path / "requests.jsonl"
    raw.write_text('{"ttft": 1}\nnot-json\n', encoding="utf-8")
    metadata = json.loads(json.dumps(METADATA))
    import hashlib

    metadata["evidence"]["source_sha256"] = hashlib.sha256(
        raw.read_bytes()
    ).hexdigest()
    result = run_import_pipeline(raw, metadata, evidence_root=tmp_path)
    assert result["accepted"] is False
    assert any("无法解析" in item for item in result["errors"])
