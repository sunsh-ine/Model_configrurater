#!/usr/bin/env python3
"""AI4：归档自定义 OpenAI 流式客户端的逐请求运行。"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
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


def _normalize_dtype(value) -> str | None:
    if value is None:
        return None
    text = str(value).lower().replace("torch.", "")
    return {"bf16": "bfloat16"}.get(text, text)


def _as_int(value):
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return value


def runtime_execution_check(runtime_config: dict, log_text: str) -> dict:
    """用服务端日志核对声明的运行配置；日志里看不到的字段必须显式标为未验证。"""
    _UNSET = object()
    server = runtime_config.get("server") or {}
    args: dict = {}
    match = re.search(r"non-default args:\s*(\{.*\})", log_text)
    if match:
        try:
            args = ast.literal_eval(match.group(1))
        except (ValueError, SyntaxError):
            args = {}

    def _pick(pattern: str):
        found = re.search(pattern, log_text)
        return found.group(1) if found else None

    engine = {
        "max_seq_len": _pick(r"max_seq_len=(\d+)"),
        "dtype": _pick(r"dtype=torch\.(\w+)"),
        "quantization": _pick(r"quantization=(\w+|None)"),
        "enforce_eager": _pick(r"enforce_eager=(True|False)"),
        "kv_cache_dtype": _pick(r"kv_cache_dtype=(\w+)"),
        "enable_prefix_caching": _pick(r"enable_prefix_caching=(True|False)"),
        "chunked_prefill_enabled": _pick(r"chunked_prefill_enabled=(True|False)"),
    }
    version = _pick(r"vLLM API server version ([\d.]+)") or _pick(
        r"\(v([\d.]+)\) with config"
    )
    declared = {
        "framework_version": server.get("framework_version"),
        "dtype": _normalize_dtype(server.get("dtype")),
        "max_model_len": server.get("max_model_len"),
        "max_num_seqs": server.get("max_num_seqs"),
        "enforce_eager": server.get("enforce_eager"),
        "prefix_caching": server.get("prefix_caching"),
        "chunked_prefill": server.get("chunked_prefill"),
        "quantization": (
            server["quantization"] if "quantization" in server else _UNSET
        ),
        "kv_cache_dtype": (
            str(server.get("kv_cache_dtype")).lower()
            if server.get("kv_cache_dtype") is not None
            else None
        ),
    }
    observed = {
        "framework_version": version,
        "dtype": _normalize_dtype(args.get("dtype") or engine["dtype"]),
        "max_model_len": _as_int(args.get("max_model_len") or engine["max_seq_len"]),
        "max_num_seqs": args.get("max_num_seqs"),
        "enforce_eager": (
            args.get("enforce_eager")
            if "enforce_eager" in args
            else (
                engine["enforce_eager"] == "True"
                if engine["enforce_eager"] is not None
                else None
            )
        ),
        "prefix_caching": (
            engine["enable_prefix_caching"] == "True"
            if engine["enable_prefix_caching"] is not None
            else None
        ),
        "chunked_prefill": (
            engine["chunked_prefill_enabled"] == "True"
            if engine["chunked_prefill_enabled"] is not None
            else None
        ),
        "quantization": (
            _UNSET
            if engine["quantization"] is None
            else (
                None
                if str(engine["quantization"]).lower() == "none"
                else engine["quantization"]
            )
        ),
        "kv_cache_dtype": engine["kv_cache_dtype"],
    }

    verified: dict[str, dict] = {}
    unverified: list[str] = []
    mismatches: list[str] = []
    for field, want in declared.items():
        got = observed.get(field)
        if want is _UNSET:
            continue
        if got is _UNSET:
            unverified.append(field)
            continue
        if got is None and field != "quantization":
            unverified.append(field)
            continue
        if isinstance(want, str):
            same = str(want).lower() == str(got).lower()
        else:
            same = want == got
        if same:
            verified[field] = {"declared": want, "observed": got}
        else:
            mismatches.append(f"{field}: declared={want} observed={got}")
    default_inferred: dict[str, dict] = {}
    for field in ("gpu_memory_utilization", "attention_backend"):
        want = server.get(field)
        if want is None or field in verified:
            continue
        # vLLM 只打印 non-default args；日志未列出且值等于 0.11 默认值时可推断为默认，
        # 但必须与“日志显式核对”分开记录。
        if field == "gpu_memory_utilization" and want == 0.9 and version == "0.11.0":
            default_inferred[field] = {
                "declared": want,
                "basis": "vllm 0.11.0 default; not listed as non-default in server log",
            }
        else:
            unverified.append(field)
    return {
        "verified": verified,
        "default_inferred": default_inferred,
        "unverified": sorted(unverified),
        "mismatches": mismatches,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--requests", required=True)
    parser.add_argument("--summary", required=True)
    parser.add_argument("--environment", required=True)
    parser.add_argument("--runtime-config", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--input-len", type=int, required=True)
    parser.add_argument("--output-len", type=int, required=True)
    parser.add_argument("--concurrency", type=int, required=True)
    parser.add_argument("--request-rate", type=float, default=None)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--sla-ttft", type=float, default=None)
    parser.add_argument("--sla-tpot", type=float, default=None)
    parser.add_argument("--server-log", required=True)
    parser.add_argument("--server-args", default="")
    parser.add_argument("--out-root", default="data/benchmark/rtx3090")
    args = parser.parse_args()

    out_dir = ROOT / args.out_root / args.run_id
    requests_source = Path(args.requests)
    summary_source = Path(args.summary)
    environment = json.loads(Path(args.environment).read_text(encoding="utf-8"))
    runtime_config = json.loads(
        Path(args.runtime_config).read_text(encoding="utf-8")
    )
    runtime_config_sha = canonical_sha(runtime_config)
    environment["server_args"] = args.server_args
    server_log = Path(args.server_log)
    if not server_log.is_file():
        raise SystemExit("服务端日志不存在：无法核对冻结配置是否实际生效")
    execution_check = runtime_execution_check(
        runtime_config, server_log.read_text(encoding="utf-8", errors="replace")
    )
    if execution_check["mismatches"]:
        raise SystemExit(
            "运行配置与服务端执行日志不一致：" + "；".join(execution_check["mismatches"])
        )
    # 日志里无法核对性能相关字段时不能把配置哈希当成“已绑定证据”。
    allowed_unverified = {"gpu_memory_utilization", "attention_backend"}
    unverified_required = [
        field
        for field in execution_check["unverified"]
        if field not in allowed_unverified
    ]
    if unverified_required:
        raise SystemExit(
            "服务端日志缺少性能相关配置，无法确认冻结配置已生效："
            + ", ".join(unverified_required)
        )
    environment["server_log_sha256"] = sha256_file(server_log)
    env_sha = canonical_sha(environment)
    requests_sha = sha256_file(requests_source)
    summary_sha = sha256_file(summary_source)
    summary = json.loads(summary_source.read_text(encoding="utf-8"))
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
    # 所有校验通过后才写入归档目录，避免复用 run-id 时先覆盖旧证据再失败
    out_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(requests_source, out_dir / "requests.jsonl")
    shutil.copy2(summary_source, out_dir / "client_summary.json")
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
        "sla": {
            "ttft_p99_ms_max": args.sla_ttft,
            "tpot_p99_ms_max": args.sla_tpot,
        },
        "runtime_config": runtime_config,
        "runtime_config_sha256": runtime_config_sha,
        "runtime_execution_check": execution_check,
        "client_summary_sha256": summary_sha,
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
        "runtime_config_sha256": runtime_config_sha,
        "runtime_execution_check": execution_check,
        "client_summary_sha256": summary_sha,
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
