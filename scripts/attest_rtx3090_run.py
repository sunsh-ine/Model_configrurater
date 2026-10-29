#!/usr/bin/env python3
"""AI4：独立核验并登记一条 RTX 3090 受控运行（与归档步骤分开）。"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import yaml

from llm_capacity_pricing.perf.benchmark_ingest import identity_sha256

ROOT = Path(__file__).resolve().parents[1]


def sha256_file(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_sha(payload) -> str:
    import hashlib

    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--verified-by", required=True)
    parser.add_argument("--note", default=None)
    args = parser.parse_args()

    run_dir = ROOT / "data/benchmark/rtx3090" / args.run_id
    metadata = yaml.safe_load((run_dir / "metadata.yaml").read_text(encoding="utf-8"))
    manifest_path = ROOT / "data/benchmark/run_manifests" / f"{args.run_id}.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    requests = run_dir / "requests.jsonl"
    errors = []
    if sha256_file(requests) != manifest["raw_sha256"]:
        errors.append("requests.jsonl 哈希与 run manifest 不一致")
    if identity_sha256(metadata) != manifest["identity_sha256"]:
        errors.append("metadata 身份哈希与 run manifest 不一致")
    # 归档脚本把 server_args/server_log_sha256 加入 environment 后再哈希。
    env_sha = canonical_sha(metadata["environment"])
    if env_sha != manifest["environment_sha256"]:
        errors.append("环境哈希与 run manifest 不一致")
    if (metadata.get("evidence") or {}).get("source_sha256") != manifest["raw_sha256"]:
        errors.append("metadata 声明的原始哈希与 run manifest 不一致")
    summary_sha = None
    summary_path = run_dir / "client_summary.json"
    if summary_path.is_file():
        summary_sha = sha256_file(summary_path)
        declared_summary = metadata.get("client_summary_sha256")
        # 旧归档没有 summary 哈希；只有声明了才做绑定校验，避免无法重新登记历史运行
        if declared_summary is not None:
            if declared_summary != summary_sha:
                errors.append("client summary 哈希与 metadata 不一致")
            if manifest.get("client_summary_sha256") != summary_sha:
                errors.append("client summary 哈希与 run manifest 不一致")
    if errors:
        raise SystemExit("；".join(errors))
    registry_path = ROOT / "data/benchmark/source_attestations.json"
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    entries = registry.setdefault("attestations", [])
    entries = [
        item
        for item in entries
        if item.get("attestation_id") != f"att-{args.run_id}"
    ]
    entries.append(
        {
            "attestation_id": f"att-{args.run_id}",
            "declared_source_kind": "internal_measurement",
            "raw_sha256": manifest["raw_sha256"],
            "identity_sha256": manifest["identity_sha256"],
            "environment_sha256": manifest["environment_sha256"],
            "runtime_config_sha256": manifest.get("runtime_config_sha256"),
            "client_summary_sha256": (
                summary_sha
                if metadata.get("client_summary_sha256") is not None
                else None
            ),
            "verified_by": args.verified_by,
            "verified_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "environment": metadata["environment"],
            "run_manifest_file": f"run_manifests/{args.run_id}.json",
            "run_manifest_sha256": sha256_file(manifest_path),
            "note": args.note,
        }
    )
    registry["attestations"] = entries
    registry_path.write_text(
        json.dumps(registry, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"已登记：att-{args.run_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
