"""AJ0：来源认证必须绑定冻结运行配置（不得事后补哈希）。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from llm_capacity_pricing.perf.source_verification import resolve_source_kind

RAW = "1" * 64
IDENTITY = "2" * 64
ENVIRONMENT = {"gpu_name": "NVIDIA GeForce RTX 3090"}
ENV_SHA = hashlib.sha256(
    json.dumps(ENVIRONMENT, ensure_ascii=False, sort_keys=True).encode("utf-8")
).hexdigest()
RUNTIME_CONFIG = {"server": {"max_num_seqs": 8, "enforce_eager": True}}
SHA_A = hashlib.sha256(
    json.dumps(RUNTIME_CONFIG, ensure_ascii=False, sort_keys=True).encode("utf-8")
).hexdigest()
SHA_B = "b" * 64


def _sha_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _resolve(
    tmp_path: Path,
    *,
    attested_runtime: str | None,
    metadata_runtime: str | None,
    manifest_runtime: str | None,
    manifest_identity: str = IDENTITY,
    metadata_runtime_config: dict | None = RUNTIME_CONFIG,
):
    manifest_dir = tmp_path / "run_manifests"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = manifest_dir / "run-1.json"
    manifest = {
        "raw_sha256": RAW,
        "identity_sha256": manifest_identity,
        "environment_sha256": ENV_SHA,
        "model_name": "Qwen/Qwen3-8B",
        "framework": "vllm",
        "framework_version": "0.11.0",
    }
    if manifest_runtime is not None:
        manifest["runtime_config_sha256"] = manifest_runtime
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    registry = tmp_path / "source_attestations.json"
    attestation = {
        "attestation_id": "att-run-1",
        "declared_source_kind": "internal_measurement",
        "raw_sha256": RAW,
        "identity_sha256": IDENTITY,
        "environment_sha256": ENV_SHA,
        "verified_by": "tester",
        "verified_at": "2026-10-28T00:00:00+00:00",
        "environment": ENVIRONMENT,
        "run_manifest_file": "run_manifests/run-1.json",
        "run_manifest_sha256": _sha_file(manifest_path),
    }
    if attested_runtime is not None:
        attestation["runtime_config_sha256"] = attested_runtime
    registry.write_text(json.dumps({"attestations": [attestation]}), encoding="utf-8")

    metadata = {
        "performance_source_kind": "internal_measurement",
        "model_name": "Qwen/Qwen3-8B",
        "framework": "vllm",
        "framework_version": "0.11.0",
        "environment": ENVIRONMENT,
    }
    if metadata_runtime is not None:
        metadata["runtime_config_sha256"] = metadata_runtime
        if metadata_runtime_config is not None:
            metadata["runtime_config"] = metadata_runtime_config
    return resolve_source_kind(
        metadata,
        raw_sha256=RAW,
        identity_sha256=IDENTITY,
        registry=registry,
    )


def test_legacy_attestation_verifies_when_no_runtime_config_declared(tmp_path):
    result = _resolve(
        tmp_path,
        attested_runtime=None,
        metadata_runtime=None,
        manifest_runtime=None,
    )
    assert result["verified_source_kind"] == "internal_measurement"


def test_runtime_config_cannot_be_added_after_attestation(tmp_path):
    result = _resolve(
        tmp_path,
        attested_runtime=None,
        metadata_runtime=SHA_A,
        manifest_runtime=None,
    )
    assert result["verified_source_kind"] == "unverified_upload"


def test_manifest_runtime_config_must_match_metadata(tmp_path):
    result = _resolve(
        tmp_path,
        attested_runtime=SHA_A,
        metadata_runtime=SHA_A,
        manifest_runtime=SHA_B,
    )
    assert result["verified_source_kind"] == "unverified_upload"


def test_matching_runtime_config_stays_verified(tmp_path):
    result = _resolve(
        tmp_path,
        attested_runtime=SHA_A,
        metadata_runtime=SHA_A,
        manifest_runtime=SHA_A,
    )
    assert result["verified_source_kind"] == "internal_measurement"


def test_tampered_runtime_config_payload_is_rejected(tmp_path):
    result = _resolve(
        tmp_path,
        attested_runtime=SHA_A,
        metadata_runtime=SHA_A,
        manifest_runtime=SHA_A,
        metadata_runtime_config={"server": {"max_num_seqs": 4}},
    )
    assert result["verified_source_kind"] == "unverified_upload"


def test_runtime_config_without_payload_is_rejected(tmp_path):
    result = _resolve(
        tmp_path,
        attested_runtime=SHA_A,
        metadata_runtime=SHA_A,
        manifest_runtime=SHA_A,
        metadata_runtime_config=None,
    )
    assert result["verified_source_kind"] == "unverified_upload"
