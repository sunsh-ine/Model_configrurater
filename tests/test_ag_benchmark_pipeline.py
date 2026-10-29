"""AG4：Benchmark 导入管线的无 GPU 回归。"""

from __future__ import annotations

import hashlib
import json
import shutil
from decimal import Decimal
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
    # 失败/取消请求没有时序，严格规则下吞吐保持 unknown。
    assert metrics["output_throughput_tok_s"] is None
    assert metrics["window_coverage"] == "unverified"


def test_pipeline_accepts_fixture_and_cross_checks_import():
    result = run_import_pipeline(RAW, METADATA, evidence_root=FIXTURE)
    assert result["accepted"] is True
    assert result["errors"] == []
    assert result["import_consistency"]["consistent"] is True
    assert result["imported"]["success_count"] == 96
    assert result["identity_sha256"] == METADATA["frozen_identity_sha256"]
    assert result["qualification"]["reliability_status"] == "fail"
    assert result["qualification"]["observed_sla_result"] == "unknown"


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
        "output_throughput_tok_s": 1000.0,
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


def test_source_kind_cannot_be_self_upgraded(tmp_path):
    metadata = json.loads(json.dumps(METADATA))
    metadata["performance_source_kind"] = "internal_measurement"
    result = run_import_pipeline(
        RAW, metadata, evidence_root=FIXTURE, registry=tmp_path / "none.json"
    )
    assert result["declared_source_kind"] == "internal_measurement"
    assert result["verified_source_kind"] == "unverified_upload"
    assert result["imported"]["performance_source_kind"] == "unverified_upload"


def test_registered_attestation_can_verify_internal_measurement(tmp_path):
    metadata = json.loads(json.dumps(METADATA))
    metadata["performance_source_kind"] = "internal_measurement"
    metadata["environment"] = {
        "gpu_name": "NVIDIA H200 SXM",
        "driver_version": "570.00",
        "cuda_version": "12.8",
    }
    metadata["measurement_window_ms"] = {"start_ms": 0, "end_ms": 20000}
    identity = metadata["frozen_identity_sha256"]
    raw_sha = metadata["evidence"]["source_sha256"]
    env_sha = hashlib.sha256(
        json.dumps(metadata["environment"], sort_keys=True).encode()
    ).hexdigest()
    run_manifest = tmp_path / "run_manifest.json"
    run_manifest.write_text(
        json.dumps(
            {
                "raw_sha256": raw_sha,
                "identity_sha256": identity,
                "environment_sha256": env_sha,
                "model_name": metadata["model_name"],
                "framework": metadata["framework"],
                "framework_version": metadata["framework_version"],
            }
        ),
        encoding="utf-8",
    )
    manifest_sha = hashlib.sha256(run_manifest.read_bytes()).hexdigest()
    registry = tmp_path / "attestations.json"
    registry.write_text(
        json.dumps(
            {
                "attestations": [
                    {
                        "attestation_id": "att-1",
                        "declared_source_kind": "internal_measurement",
                        "raw_sha256": raw_sha,
                        "identity_sha256": identity,
                        "environment_sha256": env_sha,
                        "verified_by": "fixture-operator",
                        "verified_at": "2026-10-26T00:00:00Z",
                        "environment": {"gpu": "fixture"},
                        "run_manifest_file": "run_manifest.json",
                        "run_manifest_sha256": manifest_sha,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    result = run_import_pipeline(
        RAW, metadata, evidence_root=FIXTURE, registry=registry
    )
    assert result["verified_source_kind"] == "internal_measurement"
    # 该夹具含无时序的失败/取消请求：可以入库，但不能获得 SLA 资格。
    assert result["accepted"] is True
    assert result["imported"]["performance_source_kind"] == "internal_measurement"
    assert result["qualification"]["sla_evaluable"] is False


def test_metric_coverage_exposes_partial_samples():
    rows = [
        {"arrival_ms": 0, "ttft": 100, "latency": 1000, "output_len": 10, "success": True},
        {"arrival_ms": 100, "latency": 1100, "output_len": 10, "success": True},
    ]
    metrics = recompute_metrics(rows)
    assert metrics["metric_completeness"] == "partial"
    assert metrics["metric_samples"]["ttft"] == 1
    assert metrics["metric_samples"]["success"] == 2


def test_declared_measurement_window_must_cover_samples():
    metrics = recompute_metrics(
        [
            {"arrival_ms": 1000, "ttft": 100, "latency": 1000, "output_len": 10, "success": True}
        ],
        measurement_window_ms={"start_ms": 0, "end_ms": 500},
    )
    assert metrics["window_source"] == "declared_window_does_not_cover_samples"
    assert metrics["output_throughput_tok_s"] is None


def test_declared_measurement_window_is_used_when_complete():
    metrics = recompute_metrics(
        [
            {"arrival_ms": 1000, "ttft": 100, "latency": 1000, "output_len": 10, "success": True}
        ],
        measurement_window_ms={"start_ms": 0, "end_ms": 3000},
    )
    assert metrics["window_source"] == "declared_measurement_window"
    assert metrics["output_throughput_tok_s"] == Decimal(10) / Decimal(3)


def test_unsupported_format_is_rejected_explicitly():
    metadata = json.loads(json.dumps(METADATA))
    metadata["format_version"] = "unknown-format/v99"
    result = run_import_pipeline(RAW, metadata, evidence_root=FIXTURE)
    assert result["accepted"] is False
    assert any("适配器导入失败" in item for item in result["errors"])


def test_malformed_attestation_hashes_do_not_verify(tmp_path):
    metadata = json.loads(json.dumps(METADATA))
    metadata["performance_source_kind"] = "internal_measurement"
    registry = tmp_path / "attestations.json"
    registry.write_text(
        json.dumps(
            {
                "attestations": [
                    {
                        "attestation_id": "att-bad",
                        "declared_source_kind": "internal_measurement",
                        "raw_sha256": "",
                        "identity_sha256": "",
                        "verified_by": "x",
                        "verified_at": "2026-10-26T00:00:00Z",
                        "environment": {"gpu": "x"},
                        "run_manifest_sha256": "not-a-hash",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    result = run_import_pipeline(
        RAW, metadata, evidence_root=FIXTURE, registry=registry
    )
    assert result["verified_source_kind"] == "unverified_upload"


def test_three_qualification_layers_are_separate():
    result = run_import_pipeline(RAW, METADATA, evidence_root=FIXTURE)
    qualification = result["qualification"]
    assert qualification["ingest_accepted"] is True
    assert qualification["sla_evaluable"] is False
    assert qualification["calibration_usable"] is False
    assert any("来源未认证" in item for item in qualification["sla_reasons"])
    assert any("显式测量窗口" in item for item in qualification["sla_reasons"])


def test_client_and_server_framework_are_recorded_separately():
    result = run_import_pipeline(RAW, METADATA, evidence_root=FIXTURE)
    assert result["imported"]["server_framework"] == "sglang"
    assert result["imported"]["benchmark_client"] == "openai-compatible-stream-client/v1"
