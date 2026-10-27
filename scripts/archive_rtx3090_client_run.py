#!/usr/bin/env python3
"""AI4：归档自定义 OpenAI 流式客户端的逐请求运行。"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

import yaml

from llm_capacity_pricing.perf.benchmark_ingest import identity_sha256

ROOT = Path(__file__).resolve().parents[1]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_sha(payload) -> str:
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--requests", required=True)
    parser.add_argument("--summary", required=True)
    parser.add_argument("--environment", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--input-len", type=int, required=True)
    parser.add_argument("--output-len", type=int, required=True)
    parser.add_argument("--concurrency", type=int, required=True)
    parser.add_argument("--request-rate", type=float, default=None)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--server-log", required=True)
    parser.add_argument("--server-args", default="")
    parser.add_argument("--out-root", default="data/benchmark/rtx3090")
    args = parser.parse_args()

    out_dir = ROOT / args.out_root / args.run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(args.requests, out_dir / "requests.jsonl")
    shutil.copy2(args.summary, out_dir / "client_summary.json")
    environment = json.loads(Path(args.environment).read_text(encoding="utf-8"))
    environment["server_args"] = args.server_args
    server_log = Path(args.server_log)
    environment["server_log_sha256"] = (
        sha256_file(server_log) if server_log.is_file() else None
    )
    env_sha = canonical_sha(environment)
    requests_sha = sha256_file(out_dir / "requests.jsonl")
    summary = json.loads((out_dir / "client_summary.json").read_text(encoding="utf-8"))
    identity_problems = []
    if summary.get("model") != "Qwen/Qwen3-8B":
        identity_problems.append("client summary 的模型不是 Qwen/Qwen3-8B")
    if summary.get("input_len_requested") != args.input_len:
        identity_problems.append("client summary 的输入长度与声明不一致")
    if summary.get("output_len_requested") != args.output_len:
        identity_problems.append("client summary 的输出长度与声明不一致")
    if summary.get("concurrency") != args.concurrency:
        identity_problems.append("client summary 的并发与声明不一致")
    if summary.get("request_rate") != args.request_rate:
        identity_problems.append("client summary 的请求速率与声明不一致")
    if summary.get("seed") != args.seed:
        identity_problems.append("client summary 的 seed 与声明不一致")
    if environment.get("framework") != "vllm" or environment.get(
        "framework_version"
    ) != "0.11.0":
        identity_problems.append("environment 的框架/版本与声明不一致")
    if "RTX 3090" not in str(environment.get("gpu_name")):
        identity_problems.append("environment 的 GPU 不是 RTX 3090")
    if environment.get("model_precision") != "bf16":
        identity_problems.append("environment 的精度不是 bf16")
    if identity_problems:
        raise SystemExit("身份校验失败：" + "；".join(identity_problems))
    model_sha = environment["model_files_sha256"]
    metadata = {
        "format_version": "sglang-bench-serving-jsonl/v1.per_request",
        "evidence_id": f"rtx3090:{args.run_id}",
        "record_id": f"rtx3090:{args.run_id}",
        "model_id": "qwen3-8b",
        "model_name": "Qwen/Qwen3-8B",
        "model_revision": f"local-sha256:{model_sha[:12]}",
        "weight_precision": "bf16",
        "kv_precision": "bf16",
        "hardware_id": "nvidia-rtx-3090-24gb",
        "hardware_model": "NVIDIA GeForce RTX 3090",
        "deployment_gpu_count": 1,
        "framework": "vllm",
        "framework_version": "0.11.0",
        "tp_size": 1,
        "dp_size": 1,
        "pp_size": 1,
        "dataset_name": "random",
        "configured_max_concurrency": args.concurrency,
        "arrival_mode": "fixed_concurrency" if args.request_rate is None else "poisson",
        "request_rate_rps": args.request_rate,
        "input_len": args.input_len,
        "output_len": args.output_len,
        "num_requests": summary.get("num_requests"),
        "performance_source_kind": "internal_measurement",
        "benchmark_client": "openai-compatible-stream-client/v1",
        "client_events": {
            "start": "request send",
            "first_token": "first streamed content token",
            "last_token": "request completion",
            "tpot": "(e2e - ttft)/(output_tokens-1); not ITL pool",
        },
        "metric_definitions": {
            "ttft_p99_ms": "m.latency.ttft.p99",
            "tpot_p99_ms": "m.latency.tpot.p99",
            "output_throughput_tok_s": "m.throughput.cluster.output",
        },
        "measurement_window_ms": summary["measurement_window_ms"],
        "environment": environment,
        "evidence": {
            "source_file": "requests.jsonl",
            "source_sha256": requests_sha,
            "collected_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "operator": "compute01-controlled-run",
        },
    }
    identity = identity_sha256(metadata)
    metadata["frozen_identity_sha256"] = identity
    (out_dir / "metadata.yaml").write_text(
        yaml.safe_dump(metadata, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    run_manifest = {
        "run_id": args.run_id,
        "raw_sha256": requests_sha,
        "identity_sha256": identity,
        "environment_sha256": env_sha,
        "model_name": metadata["model_name"],
        "model_revision": metadata["model_revision"],
        "framework": metadata["framework"],
        "framework_version": metadata["framework_version"],
        "gpu_name": environment["gpu_name"],
        "driver_version": environment["driver_version"],
        "server_args": args.server_args,
    }
    manifest_path = ROOT / "data/benchmark/run_manifests" / f"{args.run_id}.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(run_manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "run_id": args.run_id,
                "requests_sha256": requests_sha,
                "identity_sha256": identity,
                "environment_sha256": env_sha,
                "run_manifest_sha256": sha256_file(manifest_path),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
