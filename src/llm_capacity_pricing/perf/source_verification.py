"""AH2：Benchmark 来源声明与可信来源分离。

数据提供者只能**声明** ``declared_source_kind``。只有受控执行记录（登记在
``data/benchmark/source_attestations.json``）才能验证为 ``internal_measurement``；
其他声明一律降级为 ``unverified_upload``，不得产生生产级实测身份。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_REGISTRY = ROOT / "data/benchmark/source_attestations.json"

VERIFIABLE_KINDS = ("synthetic_fixture", "internal_measurement")


def load_attestations(
    path: str | Path | None = None,
) -> list[dict[str, Any]]:
    registry = Path(path) if path is not None else DEFAULT_REGISTRY
    if not registry.exists():
        return []
    payload = json.loads(registry.read_text(encoding="utf-8"))
    items = payload.get("attestations") if isinstance(payload, dict) else payload
    if not isinstance(items, list):
        raise ValueError("source_attestations.json 必须是列表或 {'attestations': [...]}")
    return [item for item in items if isinstance(item, dict)]


def _canonical_sha(payload: Any) -> str | None:
    if payload is None:
        return None
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_sha256(value: Any) -> bool:
    if not isinstance(value, str) or len(value) != 64:
        return False
    return all(ch in "0123456789abcdef" for ch in value.lower())


def _attestation_matches(
    attestation: Mapping[str, Any],
    *,
    raw_sha256: str,
    identity_sha256: str,
    declared: str,
    environment_sha256: str | None,
    registry_dir: Path,
    metadata: Mapping[str, Any],
) -> bool:
    attested_raw = attestation.get("raw_sha256")
    attested_identity = attestation.get("identity_sha256")
    if not _is_sha256(raw_sha256) or not _is_sha256(identity_sha256):
        return False
    if not _is_sha256(attested_raw) or not _is_sha256(attested_identity):
        return False
    if not environment_sha256:
        return False
    if attestation.get("environment_sha256") != environment_sha256:
        return False
    attested_runtime = attestation.get("runtime_config_sha256")
    current_runtime = metadata.get("runtime_config_sha256")
    # 配置哈希必须与登记时完全一致；旧登记没有该字段时，元数据也不得事后补一个
    if attested_runtime != current_runtime:
        return False
    config_payload = metadata.get("runtime_config")
    if current_runtime is not None:
        if config_payload is None:
            return False
        if _canonical_sha(config_payload) != current_runtime:
            return False
    attested_summary = attestation.get("client_summary_sha256")
    current_summary = metadata.get("client_summary_sha256")
    if attested_summary != current_summary:
        return False
    if current_summary is not None and not _is_sha256(current_summary):
        return False
    manifest_file = attestation.get("run_manifest_file")
    manifest_sha = attestation.get("run_manifest_sha256")
    if not manifest_file or not _is_sha256(manifest_sha):
        return False
    candidate = Path(manifest_file)
    if not candidate.is_absolute():
        candidate = registry_dir / candidate
        if not candidate.is_file():
            candidate = ROOT / manifest_file
    if not candidate.is_file():
        return False
    if _sha256_file(candidate) != str(manifest_sha).lower():
        return False
    try:
        run_manifest = json.loads(candidate.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return False
    for key in ("raw_sha256", "identity_sha256", "environment_sha256"):
        if run_manifest.get(key) != {
            "raw_sha256": raw_sha256,
            "identity_sha256": identity_sha256,
            "environment_sha256": environment_sha256,
        }[key]:
            return False
    if run_manifest.get("model_name") not in (None, metadata.get("model_name")):
        return False
    if run_manifest.get("framework") not in (None, metadata.get("framework")):
        return False
    if run_manifest.get("framework_version") not in (
        None,
        metadata.get("framework_version"),
    ):
        return False
    if run_manifest.get("runtime_config_sha256") != current_runtime:
        return False
    if run_manifest.get("client_summary_sha256") != current_summary:
        return False
    return (
        attestation.get("declared_source_kind") == declared
        and str(attested_raw).lower() == raw_sha256.lower()
        and str(attested_identity).lower() == identity_sha256.lower()
        and bool(attestation.get("attestation_id"))
        and bool(attestation.get("verified_by"))
        and bool(attestation.get("verified_at"))
        and isinstance(attestation.get("environment"), dict)
        and bool(attestation.get("environment"))
    )


def resolve_source_kind(
    metadata: Mapping[str, Any],
    *,
    raw_sha256: str,
    identity_sha256: str,
    registry: str | Path | None = None,
) -> dict[str, Any]:
    """返回 ``declared_source_kind`` / ``verified_source_kind`` / 依据。

    规则：

    * ``synthetic_fixture``：可自证为夹具，但永远不能当实测。
    * ``internal_measurement``：必须有登记在册、绑定原始文件与身份的受控执行记录；
      否则降级为 ``unverified_upload``。
    * 其他声明：一律 ``unverified_upload``（不能靠 metadata 字符串获得实测身份）。
    """
    declared = str(metadata.get("performance_source_kind") or "unknown")
    if declared == "synthetic_fixture":
        return {
            "declared_source_kind": declared,
            "verified_source_kind": "synthetic_fixture",
            "verification_method": "fixture_declaration",
            "attestation_id": None,
            # 来源类别已确认是夹具；但 verified=False 表示不能当真实实测。
            "verified": True,
            "verified_for_calibration": False,
            "reason": "synthetic_fixture 只用于管线回归，不是真实实测",
        }
    if declared == "internal_measurement":
        registry_path = Path(registry) if registry is not None else DEFAULT_REGISTRY
        registry_dir = registry_path.parent
        environment_sha = _canonical_sha(metadata.get("environment"))
        for attestation in load_attestations(registry):
            if _attestation_matches(
                attestation,
                raw_sha256=raw_sha256,
                identity_sha256=identity_sha256,
                declared=declared,
                environment_sha256=environment_sha,
                registry_dir=registry_dir,
                metadata=metadata,
            ):
                return {
                    "declared_source_kind": declared,
                    "verified_source_kind": "internal_measurement",
                    "verification_method": "registered_controlled_run",
                    "attestation_id": attestation["attestation_id"],
                    "verified": True,
                    "verified_for_calibration": True,
                    "reason": "匹配登记的受控执行记录",
                }
        return {
            "declared_source_kind": declared,
            "verified_source_kind": "unverified_upload",
            "verification_method": "none",
            "attestation_id": None,
            "verified": False,
            "verified_for_calibration": False,
            "reason": "缺少登记在册的受控执行记录，不能授予内部实测身份",
        }
    return {
        "declared_source_kind": declared,
        "verified_source_kind": "unverified_upload",
        "verification_method": "none",
        "attestation_id": None,
        "verified": False,
        "verified_for_calibration": False,
        "reason": "声明类型不能由上传者自行验证",
    }
