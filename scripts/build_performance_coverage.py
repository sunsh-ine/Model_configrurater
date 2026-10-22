#!/usr/bin/env python3
"""Z1：生成性能预测证据覆盖矩阵（机器可读 + Markdown）。

只读三类证据，不调用 AISimulate，也不读任何价格字段：

1. 本仓库 Catalog（模型/硬件，决定“能否做静态资源规划”）；
2. 公开 benchmark 参考数据（``data/incoming/``，只作参考，不参与 SLA）；
3. AISimulate 归档（``docs/poc/aisimulate/`` 与 ``data/simulation/archives/``）。

输出：

* ``docs/performance_coverage.json`` —— 机器可读的逐条证据与分级；
* ``docs/performance_coverage.md``   —— 同一份数据的人员可读版本。

四级口径（与任务书一致）：

1. ``static_only``：只有 Catalog，能做静态资源规划；
2. ``simulated_scan_only``：有扫描器稳态单点（均值），**没有 P99**；
3. ``simulated_native_p99``：有可比较的原生回放 P99；
4. ``measured_benchmark``：目标配置的真实 Benchmark 证据。

``public_reference`` 与历史 Excel 记录单独登记为“参考证据”，
不计入上述四级，也不参与 SLA 或报价结论。
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import yaml

from llm_capacity_pricing.app.archive_integrity import verify_manifest_hashes

ROOT = Path(__file__).resolve().parents[1]

LEVEL_LABELS = {
    1: "仅静态资源规划",
    2: "模拟扫描（无 P99）",
    3: "可比较的原生 P99 预测",
    4: "目标配置的真实 Benchmark",
}
LEVEL_KEYS = {
    1: "static_only",
    2: "simulated_scan_only",
    3: "simulated_native_p99",
    4: "measured_benchmark",
}

POC = ROOT / "docs/poc/aisimulate"
ARCHIVES = ROOT / "data/simulation/archives"

#: AISimulate system 名 -> 本仓库 hardware_id（与 perf/aisimulate_adapter 一致）
SYSTEM_TO_HARDWARE_ID = {
    "h100_sxm": "nvidia-h100-sxm-80gb",
    "h200_sxm": "nvidia-h200-sxm-141gb",
    "b200_sxm": "nvidia-b200-sxm",
}
HARDWARE_NAME_TO_ID = {
    "NVIDIA H100 SXM": "nvidia-h100-sxm-80gb",
    "NVIDIA H200 SXM": "nvidia-h200-sxm-141gb",
    "NVIDIA H100 NVL": "nvidia-h100-nvl-94gb",
    "NVIDIA B200": "nvidia-b200-sxm",
    "B300": None,
}
PRECISION_ALIASES = {
    "bfloat16": "bf16",
    "bf16": "bf16",
    "float16": "fp16",
    "fp16": "fp16",
    "fp8": "fp8",
}


def sha256(path: Path) -> str | None:
    if not path.exists() or not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT.resolve()))
    except ValueError:
        return str(path)


def text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value.strip() or None
    return str(value)


def num(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def precision(value: Any) -> str | None:
    raw = text(value)
    if not raw:
        return None
    return PRECISION_ALIASES.get(raw.lower(), raw.lower())


def load_yaml(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def identity_gaps(identity: dict[str, Any]) -> list[str]:
    required = {
        "model_name": "模型",
        "hardware_id": "hardware_id",
        "precision": "权重精度",
        "framework": "推理框架",
        "framework_version": "框架版本",
        "gpu_count": "GPU 数量",
        "tp_size": "TP",
        "pp_size": "PP",
        "dp_size": "DP",
    }
    return [label for key, label in required.items() if identity.get(key) is None]


def metrics_available(metrics: dict[str, Any]) -> dict[str, bool]:
    return {
        "ttft_p99": metrics.get("ttft_p99_ms") is not None,
        "tpot_p99": metrics.get("tpot_p99_ms") is not None,
        "throughput": metrics.get("output_throughput_tok_s") is not None,
        "ttft_mean": metrics.get("ttft_mean_ms") is not None,
        "tpot_mean": metrics.get("tpot_mean_ms") is not None,
    }


def sla_usable(identity: dict[str, Any], metrics: dict[str, Any], requests: int | None) -> tuple[bool, list[str]]:
    blockers = []
    gaps = identity_gaps(identity)
    if gaps:
        blockers.append("身份不完整：" + "、".join(gaps))
    if metrics.get("ttft_p99_ms") is None:
        blockers.append("缺少 TTFT P99")
    if metrics.get("tpot_p99_ms") is None:
        blockers.append("缺少 TPOT P99")
    if requests is None:
        blockers.append("缺少 P99 样本窗口（请求数）")
    elif requests < 100:
        blockers.append(f"P99 样本不足（{requests} < 100）")
    return not blockers, blockers


def model_id_for(repo: str | None, models: list[dict[str, Any]]) -> str | None:
    if not repo:
        return None
    for model in models:
        if model.get("repo_id") == repo or model.get("model_id") == repo:
            return model.get("model_id")
    return None


def hardware_id_for(
    name: str | None, system: str | None, hardware: list[dict[str, Any]]
) -> str | None:
    if system:
        mapped = SYSTEM_TO_HARDWARE_ID.get(" ".join(system.split()).lower())
        if mapped:
            return mapped
    if not name:
        return None
    for item in hardware:
        if item.get("product_name") == name:
            return item.get("hardware_id")
    return HARDWARE_NAME_TO_ID.get(name)


def normalised_identity(identity: dict[str, Any]) -> dict[str, Any]:
    return {
        "model_name": identity.get("model_name"),
        "model_id": identity.get("model_id"),
        "precision": identity.get("precision"),
        "hardware_id": identity.get("hardware_id"),
        "hardware_model": identity.get("hardware_model"),
        "system": identity.get("system"),
        "framework": identity.get("framework"),
        "framework_version": identity.get("framework_version"),
        "gpu_count": identity.get("gpu_count"),
        "tp_size": identity.get("tp_size"),
        "pp_size": identity.get("pp_size"),
        "dp_size": identity.get("dp_size"),
        "serving_mode": identity.get("serving_mode", "unknown"),
    }


def normalised_workload(workload: dict[str, Any]) -> dict[str, Any]:
    return {
        "input_len": workload.get("input_len"),
        "output_len": workload.get("output_len"),
        "concurrency": workload.get("concurrency"),
        "ctx_tokens": workload.get("ctx_tokens"),
        "requests": workload.get("requests"),
        "dataset": workload.get("dataset"),
    }


def workpoint_key(identity: dict[str, Any], workload: dict[str, Any]) -> str:
    """按文档规定的工作点维度分组。

    只包含模型/精度/硬件/框架/并行与 ISL/OSL/并发/ctx；样本窗口
    （requests）与数据集留在每条证据上，不能把同一工作点拆成多个组。
    """
    key_workload = {
        key: workload.get(key)
        for key in ("input_len", "output_len", "concurrency", "ctx_tokens")
    }
    payload = json.dumps(
        {"identity": identity, "workload": key_workload},
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def add_evidence(store: list[dict[str, Any]], item: dict[str, Any]) -> None:
    item["metrics_available"] = metrics_available(item["metrics"])
    store.append(item)


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------


def read_catalog() -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    model_doc = load_yaml(ROOT / "data/models/model_manifest.yaml")
    hardware_doc = load_yaml(ROOT / "data/hardware/hardware_catalog.yaml")
    models = list(model_doc.get("models") or [])
    hardware = list(hardware_doc.get("hardware") or [])
    supported_models = [
        {
            "model_id": m.get("model_id"),
            "repo_id": m.get("repo_id"),
            "model_name": m.get("repo_id"),
            "revision": m.get("revision"),
        }
        for m in models
    ]
    supported_hardware = [
        {
            "hardware_id": h.get("hardware_id"),
            "product_name": h.get("product_name"),
            "total_memory_bytes": text(h.get("total_memory_bytes")),
            "memory_bandwidth_bytes_per_s": text(h.get("memory_bandwidth_bytes_per_s")),
            "supported_in_phase2": bool(h.get("supported_in_phase2")),
            "verification_status": h.get("verification_status"),
        }
        for h in hardware
    ]
    unsupported_models = list(model_doc.get("unsupported_examples") or [])
    return (
        {
            "source": "data/models/model_manifest.yaml",
            "source_sha256": sha256(ROOT / "data/models/model_manifest.yaml"),
            "models": supported_models,
            "unsupported_models": unsupported_models,
            "hardware_source": "data/hardware/hardware_catalog.yaml",
            "hardware_source_sha256": sha256(ROOT / "data/hardware/hardware_catalog.yaml"),
            "hardware": supported_hardware,
            "servers": list(hardware_doc.get("servers") or []),
        },
        supported_models,
        supported_hardware,
    )


def cost_capable_hardware() -> tuple[set[str], str]:
    path = ROOT / "data/examples/phase4_cost_inputs.example.yaml"
    doc = load_yaml(path)
    ids = {
        item.get("applies_to_hardware_id")
        for item in doc.get("cost_items") or []
        if item.get("applies_to_hardware_id")
    }
    return {value for value in ids if value}, rel(path)


# ---------------------------------------------------------------------------
# AISimulate: archived PoC
# ---------------------------------------------------------------------------


def sweep_rows(
    csv_path: Path,
    *,
    source_kind: str,
    run_id: str | None,
    tool_version: str | None,
    database_mode: str | None,
    models: list[dict[str, Any]],
    hardware: list[dict[str, Any]],
    extra_notes: Iterable[str] = (),
) -> list[dict[str, Any]]:
    if not csv_path.exists():
        return []
    digest = sha256(csv_path)
    evidence = []
    with csv_path.open(encoding="utf-8", newline="") as handle:
        for row_index, row in enumerate(csv.DictReader(handle), start=2):
            if not row:
                continue
            identity = normalised_identity(
                {
                    "model_name": text(row.get("model")),
                    "model_id": model_id_for(text(row.get("model")), models),
                    "precision": precision(row.get("gemm")),
                    "hardware_id": hardware_id_for(None, row.get("system"), hardware),
                    "hardware_model": None,
                    "system": text(row.get("system")),
                    "framework": text(row.get("backend")),
                    "framework_version": text(row.get("version")),
                    "gpu_count": int(num(row.get("num_total_gpus")) or 0) or None,
                    "tp_size": int(num(row.get("tp")) or 0) or None,
                    "pp_size": int(num(row.get("pp")) or 0) or None,
                    "dp_size": int(num(row.get("dp")) or 0) or None,
                    "serving_mode": "agg",
                }
            )
            workload = normalised_workload(
                {
                    "input_len": int(num(row.get("isl")) or 0) or None,
                    "output_len": int(num(row.get("osl")) or 0) or None,
                    "concurrency": int(num(row.get("concurrency")) or 0) or None,
                    "ctx_tokens": int(num(row.get("ctx_tokens")) or 0) or None,
                    "requests": None,
                    "dataset": None,
                }
            )
            metrics = {
                "ttft_p99_ms": None,
                "tpot_p99_ms": None,
                "ttft_mean_ms": text(row.get("ttft")),
                "tpot_mean_ms": text(row.get("tpot")),
                "output_throughput_tok_s": text(row.get("tokens/s")),
                "completed_requests": None,
            }
            add_evidence(
                evidence,
                {
                    "evidence_id": f"{run_id or 'poc'}:scan:{row_index}",
                    "source_kind": source_kind,
                    "coverage_level": 2,
                    "coverage_level_key": LEVEL_KEYS[2],
                    "artifact": rel(csv_path),
                    "artifact_sha256": digest,
                    "locator": {"row": row_index, "parallel": text(row.get("parallel"))},
                    "run_id": run_id,
                    "tool_version": tool_version,
                    "database_mode": database_mode,
                    "identity": identity,
                    "workload": workload,
                    "metrics": metrics,
                    "sla_usable": False,
                    "sla_blockers": [
                        "扫描器输出是稳态单点（均值口径），不含 P99；不能用于 SLA 判定"
                    ],
                    "reference_only": False,
                    "notes": list(extra_notes),
                },
            )
    return evidence


def native_evidence(
    *,
    input_path: Path,
    prediction_path: Path,
    exp_config_path: Path | None,
    run_id: str | None,
    tool_version: str | None,
    models: list[dict[str, Any]],
    hardware: list[dict[str, Any]],
    aliases: dict[str, str] | None = None,
    declared_precision: str | None = None,
    extra_notes: Iterable[str] = (),
) -> dict[str, Any] | None:
    if not input_path.exists() or not prediction_path.exists():
        return None
    cfg = load_yaml(input_path)
    prediction = load_json(prediction_path)
    engine = cfg.get("engine") or {}
    traffic = cfg.get("traffic") or {}
    source = traffic.get("source") or {}
    load = traffic.get("load") or {}
    stop = traffic.get("stop") or {}
    aggregated = ((engine.get("workers") or {}).get("aggregated")) or {}
    parallelism = aggregated.get("parallelism") or {}

    tensor = int(num(parallelism.get("tensor")) or 0) or None
    replicas = int(num(parallelism.get("replicas")) or 0) or None
    pp_size = int(num(parallelism.get("pipeline")) or 0) or None
    decode_gpus = int(num(prediction.get("decode_gpus_per_worker")) or 0) or None
    # 只有 decode 卡数才是可用证据；prefill 卡数不能拿来凑 GPU 总数
    gpu_count = (
        decode_gpus * replicas
        if decode_gpus and replicas
        else (tensor * replicas if tensor and replicas else None)
    )
    backend_version = text(engine.get("backend_version"))
    if backend_version and aliases:
        backend_version = aliases.get(backend_version, backend_version)

    database_mode = None
    search_space_sha = None
    if exp_config_path and exp_config_path.exists():
        exp = load_yaml(exp_config_path)
        database_mode = text(exp.get("database_mode"))
        search_space_sha = sha256(exp_config_path)

    identity = normalised_identity(
        {
            "model_name": text(engine.get("model")),
            "model_id": model_id_for(text(engine.get("model")), models),
            "precision": declared_precision,
            "hardware_id": hardware_id_for(None, engine.get("hardware"), hardware),
            "hardware_model": None,
            "system": text(engine.get("hardware")),
            "framework": text(engine.get("backend")),
            "framework_version": backend_version,
            "gpu_count": gpu_count,
            "tp_size": tensor,
            "pp_size": pp_size or 1,
            "dp_size": replicas or 1,
            "serving_mode": "agg" if engine.get("mode") in (None, "aggregated") else text(engine.get("mode")),
        }
    )
    workload = normalised_workload(
        {
            "input_len": int(num(source.get("input_tokens")) or 0) or None,
            "output_len": int(num(source.get("output_tokens")) or 0) or None,
            "concurrency": int(num(load.get("concurrency")) or 0) or None,
            "ctx_tokens": None,
            "requests": int(num(stop.get("requests")) or 0) or None,
            "dataset": text(source.get("type")),
        }
    )
    metrics = {
        "ttft_p99_ms": text(prediction.get("p99_ttft_ms")),
        "tpot_p99_ms": text(prediction.get("p99_tpot_ms")),
        "ttft_mean_ms": text(prediction.get("mean_ttft_ms")),
        "tpot_mean_ms": text(prediction.get("mean_tpot_ms")),
        "output_throughput_tok_s": text(prediction.get("output_throughput_tok_s")),
        "completed_requests": int(num(prediction.get("completed_requests")) or 0) or None,
    }
    # 样本窗口以**实际完成数**为准：请求 500 但只完成 10 个时，P99 不可用。
    sample_window = metrics.get("completed_requests")
    usable, blockers = sla_usable(identity, metrics, sample_window)
    notes = list(extra_notes)
    if not usable and backend_version is None:
        notes.append("后端版本槽位未解析成实际版本")
    item = {
        "evidence_id": f"{run_id or 'poc'}:native:{input_path.stem}",
        "source_kind": "simulated_native",
        # 原生回放有 P99 但身份不完整时，既不是 3 级（不可比较），
        # 也不是 2 级（扫描器）；单列"不完整原生证据"，不参与级别统计。
        "coverage_level": 3 if usable else None,
        "coverage_level_key": (
            LEVEL_KEYS[3] if usable else "simulated_native_incomplete"
        ),
        "artifact": rel(prediction_path),
        "artifact_sha256": sha256(prediction_path),
        "supporting_artifacts": [
            {"path": rel(input_path), "sha256": sha256(input_path)},
            *(
                [{"path": rel(exp_config_path), "sha256": search_space_sha}]
                if exp_config_path and exp_config_path.exists()
                else []
            ),
        ],
        "run_id": run_id,
        "tool_version": tool_version,
        "database_mode": database_mode,
        "identity": identity,
        "workload": workload,
        "metrics": metrics,
        "sla_usable": usable,
        "sla_blockers": blockers,
        "reference_only": False,
        "notes": notes,
    }
    add_evidence([], item)
    return item


def read_poc_evidence(
    models: list[dict[str, Any]], hardware: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    native = native_evidence(
        input_path=POC / "16_matched_native_input_tp8_conc32_bf16.yaml",
        prediction_path=POC / "17_matched_native_result_tp8_conc32_bf16.json",
        exp_config_path=POC / "artifacts/matched_exp_config_vllm_bf16.yaml",
        run_id="poc-20261020-tp8-c32-bf16",
        tool_version="0.12.1",
        models=models,
        hardware=hardware,
        aliases={"current": "0.24.0"},
        declared_precision="bf16",
        extra_notes=[
            "N 阶段 PoC 归档；500 请求的 P99 回放",
            "离线 Demo 的性能预测即引用本条证据",
        ],
    )
    if native:
        evidence.append(native)
    evidence.extend(
        sweep_rows(
            POC / "artifacts/matched_pareto_vllm_bf16.csv",
            source_kind="simulated_scan",
            run_id="poc-20261020-tp8-bf16-scan",
            tool_version="0.12.1",
            database_mode="SILICON",
            models=models,
            hardware=hardware,
            extra_notes=["扫描器结果用于候选枚举与相对比较，单点值接近原生回放的中位数"],
        )
    )
    return evidence


# ---------------------------------------------------------------------------
# AISimulate: live archives
# ---------------------------------------------------------------------------


def read_archive_evidence(
    archive_root: Path,
    models: list[dict[str, Any]],
    hardware: list[dict[str, Any]],
    blocked_runs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    if not archive_root.exists():
        return evidence
    for manifest_path in sorted(archive_root.glob("*/manifest.json")):
        run_dir = manifest_path.parent
        try:
            manifest = load_json(manifest_path)
        except Exception as exc:  # noqa: BLE001
            blocked_runs.append(
                {"run_id": run_dir.name, "error": f"manifest 无法解析：{exc}"}
            )
            continue
        run_id = text(manifest.get("run_id")) or run_dir.name
        spec = manifest.get("spec") or {}
        errors = manifest.get("errors") or []
        if errors:
            # 失败的运行即使留下了部分预测文件，也不能进入覆盖矩阵；
            # 只保留阻塞记录，避免把半成品当成证据。
            blocked_runs.append(
                {
                    "run_id": run_id,
                    "errors": errors,
                    "manifest": rel(manifest_path),
                }
            )
            continue
        integrity = verify_manifest_hashes(run_dir, manifest)
        if integrity:
            blocked_runs.append(
                {
                    "run_id": run_id,
                    "errors": ["归档完整性校验失败：" + "；".join(integrity)],
                    "manifest": rel(manifest_path),
                }
            )
            continue
        tool_version = text(manifest.get("tool_version"))
        database_mode = text(spec.get("database_mode"))
        aliases = {"current": "0.24.0"}
        declared_precision = precision(spec.get("precision"))

        predict_inputs = sorted(run_dir.glob("predict/predict_*.yaml"))
        required: list[str] = []
        for input_path in predict_inputs:
            index = input_path.stem.rsplit("_", 1)[-1]
            prediction_path = run_dir / "predict" / f"out_{index}" / "prediction.json"
            required.append(str(input_path.relative_to(run_dir)))
            if prediction_path.exists():
                required.append(str(prediction_path.relative_to(run_dir)))
        if required and verify_manifest_hashes(run_dir, manifest, required=required):
            blocked_runs.append(
                {
                    "run_id": run_id,
                    "errors": ["归档完整性校验失败：实际使用的预测/输入未被哈希覆盖"],
                    "manifest": rel(manifest_path),
                }
            )
            continue
        for input_path in predict_inputs:
            index = input_path.stem.rsplit("_", 1)[-1]
            prediction_path = run_dir / "predict" / f"out_{index}" / "prediction.json"
            native = native_evidence(
                input_path=input_path,
                prediction_path=prediction_path,
                exp_config_path=None,
                run_id=run_id,
                tool_version=tool_version,
                models=models,
                hardware=hardware,
                aliases=aliases,
                declared_precision=declared_precision,
                extra_notes=[
                    f"Z 阶段实时运行归档：{rel(run_dir)}",
                    "身份来自 predict 输入与实际 prediction 输出",
                ],
            )
            if native:
                evidence.append(native)

        for csv_path in sorted(run_dir.rglob("pareto.csv")):
            # pareto.csv 的负载与 spec 一致；逐行登记为扫描证据
            evidence.extend(
                sweep_rows(
                    csv_path,
                    source_kind="simulated_scan",
                    run_id=run_id,
                    tool_version=tool_version,
                    database_mode=database_mode,
                    models=models,
                    hardware=hardware,
                    extra_notes=[f"Z 阶段实时运行归档：{rel(run_dir)}"],
                )
            )
    return evidence


# ---------------------------------------------------------------------------
# Public references（参考证据，不是四级中的任何一级）
# ---------------------------------------------------------------------------


def public_reference_evidence(
    models: list[dict[str, Any]], hardware: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    path = ROOT / "data/incoming/public_benchmark_references_20261015.json"
    if path.exists():
        doc = load_json(path)
        digest = sha256(path)
        for record in doc.get("records") or []:
            deployment = record.get("deployment") or {}
            workload = record.get("workload") or {}
            metrics_raw = record.get("metrics") or {}
            missing = list(record.get("missing_or_unverified_fields") or [])
            identity = normalised_identity(
                {
                    "model_name": text(deployment.get("model_name")),
                    "model_id": model_id_for(text(deployment.get("model_name")), models),
                    "precision": precision(deployment.get("weight_precision")),
                    "hardware_id": hardware_id_for(
                        text(deployment.get("hardware_model")), None, hardware
                    ),
                    "hardware_model": text(deployment.get("hardware_model")),
                    "system": None,
                    "framework": text(deployment.get("framework")),
                    "framework_version": text(deployment.get("framework_version")),
                    "gpu_count": int(num(deployment.get("gpu_count")) or 0) or None,
                    "tp_size": int(num(deployment.get("tp_size")) or 0) or None,
                    "pp_size": None,
                    "dp_size": int(num(deployment.get("dp_size")) or 0) or None,
                    "serving_mode": "unknown",
                }
            )
            workload_norm = normalised_workload(
                {
                    "input_len": int(num(workload.get("input_len_target")) or 0) or None,
                    "output_len": int(num(workload.get("output_len_target")) or 0) or None,
                    "concurrency": int(
                        num(workload.get("configured_max_concurrency")) or 0
                    )
                    or None,
                    "ctx_tokens": None,
                    "requests": int(num(workload.get("num_prompts")) or 0) or None,
                    "dataset": text(workload.get("dataset_name")),
                }
            )
            metrics = {
                "ttft_p99_ms": text(metrics_raw.get("ttft_p99_ms")),
                "tpot_p99_ms": text(metrics_raw.get("tpot_p99_ms")),
                "ttft_mean_ms": text(metrics_raw.get("ttft_mean_ms")),
                "tpot_mean_ms": text(metrics_raw.get("tpot_mean_ms")),
                "output_throughput_tok_s": text(
                    metrics_raw.get("output_throughput_tok_s")
                    or metrics_raw.get("output_token_throughput_tps")
                ),
                "completed_requests": int(num(metrics_raw.get("success_count")) or 0)
                or None,
            }
            blockers = [
                "公开参考数据，不是目标配置实测",
                *[f"原文未声明：{field}" for field in missing],
            ]
            if record.get("note"):
                blockers.append(text(record.get("note")))
            add_evidence(
                evidence,
                {
                    "evidence_id": text(record.get("record_id")),
                    "source_kind": "measured_public",
                    "coverage_level": None,
                    "coverage_level_key": "public_reference",
                    "artifact": rel(path),
                    "artifact_sha256": digest,
                    "source_url": (doc.get("sources") or {})
                    .get(record.get("source_id"), {})
                    .get("source_url"),
                    "run_id": None,
                    "tool_version": None,
                    "database_mode": None,
                    "identity": identity,
                    "workload": workload_norm,
                    "metrics": metrics,
                    "sla_usable": False,
                    "sla_blockers": blockers,
                    "reference_only": True,
                    "notes": [
                        "只作参考：不参与 SLA 判定，也不进报价门禁",
                    ],
                },
            )

    sglang = ROOT / "data/incoming/sglang_official_benchmark_candidates_20261015.json"
    if sglang.exists():
        doc = load_json(sglang)
        digest = sha256(sglang)
        for record in doc.get("records") or []:
            deployment = record.get("deployment") or {}
            workload = record.get("workload") or {}
            metrics_raw = record.get("metrics") or {}
            missing = list(record.get("missing_or_unverified_fields") or [])
            identity = normalised_identity(
                {
                    "model_name": text(record.get("model_id")),
                    "model_id": model_id_for(text(record.get("model_id")), models),
                    "precision": precision(record.get("weight_precision")),
                    "hardware_id": hardware_id_for(
                        text(record.get("hardware_model")), None, hardware
                    ),
                    "hardware_model": text(record.get("hardware_model")),
                    "system": None,
                    "framework": text(record.get("framework")),
                    "framework_version": text(record.get("framework_version")),
                    "gpu_count": int(num(record.get("deployment_gpu_count")) or 0)
                    or None,
                    "tp_size": int(num(deployment.get("tp_size")) or 0) or None,
                    "pp_size": None,
                    "dp_size": int(num(deployment.get("dp_size")) or 0) or None,
                    "serving_mode": "unknown",
                }
            )
            workload_norm = normalised_workload(
                {
                    "input_len": int(
                        num(
                            workload.get("random_input_len_target")
                            or workload.get("input_len_target")
                        )
                        or 0
                    )
                    or None,
                    "output_len": int(
                        num(
                            workload.get("random_output_len_target")
                            or workload.get("output_len_target")
                        )
                        or 0
                    )
                    or None,
                    "concurrency": int(
                        num(workload.get("configured_max_concurrency")) or 0
                    )
                    or None,
                    "ctx_tokens": None,
                    "requests": int(num(workload.get("num_prompts")) or 0) or None,
                    "dataset": text(workload.get("dataset_name")),
                }
            )
            metrics = {
                "ttft_p99_ms": text(metrics_raw.get("ttft_p99_ms")),
                "tpot_p99_ms": text(metrics_raw.get("tpot_p99_ms")),
                "ttft_mean_ms": None,
                "tpot_mean_ms": text(metrics_raw.get("tpot_mean_ms")),
                "output_throughput_tok_s": text(
                    metrics_raw.get("output_token_throughput_tps")
                ),
                "completed_requests": int(num(metrics_raw.get("successful_requests")) or 0)
                or None,
            }
            add_evidence(
                evidence,
                {
                    "evidence_id": text(record.get("record_id")),
                    "source_kind": "measured_public",
                    "coverage_level": None,
                    "coverage_level_key": "public_reference",
                    "artifact": rel(sglang),
                    "artifact_sha256": digest,
                    "source_url": (doc.get("sources") or {})
                    .get(record.get("source_id"), {})
                    .get("source_url"),
                    "run_id": None,
                    "tool_version": None,
                    "database_mode": None,
                    "identity": identity,
                    "workload": workload_norm,
                    "metrics": metrics,
                    "sla_usable": False,
                    "sla_blockers": [
                        "公开参考数据，不是目标配置实测",
                        f"eligible_for_sla_guarantee={record.get('eligible_for_sla_guarantee')}",
                        *[f"原文未声明：{field}" for field in missing],
                    ],
                    "reference_only": True,
                    "notes": list(record.get("notes") or []),
                },
            )
    return evidence


def db_reference_evidence(
    models: list[dict[str, Any]], hardware: list[dict[str, Any]], gaps: list[str]
) -> list[dict[str, Any]]:
    """只读 benchmark_record 的性能字段；**绝不**读取任何价格/收入字段。"""
    path = ROOT / "data/local.db"
    if not path.exists():
        gaps.append("data/local.db 不存在：历史 Excel benchmark 未纳入本次覆盖统计")
        return []
    evidence: list[dict[str, Any]] = []
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        rows = list(con.execute("select * from benchmark_record"))
    except Exception as exc:  # noqa: BLE001
        gaps.append(f"data/local.db 无法读取：{exc}")
        return []
    finally:
        try:
            con.close()
        except Exception:  # noqa: BLE001
            pass

    digest = sha256(path)
    for row in rows:
        payload = json.loads(row["payload_json"])
        strategy = payload.get("deployment_strategy_parsed") or {}
        identity = normalised_identity(
            {
                "model_name": text(payload.get("model_name")),
                "model_id": model_id_for(text(payload.get("model_name")), models),
                "precision": precision(payload.get("model_precision")),
                "hardware_id": hardware_id_for(
                    text(payload.get("hardware_model")), None, hardware
                ),
                "hardware_model": text(payload.get("hardware_model")),
                "system": None,
                "framework": text(payload.get("framework")),
                "framework_version": text(payload.get("framework_version")),
                "gpu_count": int(num(payload.get("deployment_gpu_count")) or 0)
                or None,
                "tp_size": int(num(strategy.get("tp")) or 0) or None,
                "pp_size": int(num(strategy.get("pp")) or 0) or None,
                "dp_size": int(num(strategy.get("dp")) or 0) or None,
                "serving_mode": text(payload.get("deployment_mode")),
            }
        )
        workload = normalised_workload(
            {
                "input_len": int(num(payload.get("input_tokens_target")) or 0) or None,
                "output_len": int(num(payload.get("output_tokens_target")) or 0) or None,
                "concurrency": int(num(payload.get("configured_concurrency")) or 0)
                or None,
                "ctx_tokens": None,
                "requests": int(num(payload.get("num_requests")) or 0) or None,
                "dataset": text(payload.get("dataset_name")),
            }
        )
        metrics = {
            "ttft_p99_ms": text(payload.get("ttft_p99_ms")),
            "tpot_p99_ms": text(payload.get("tpot_p99_ms")),
            "ttft_mean_ms": text(payload.get("ttft_mean_ms")),
            "tpot_mean_ms": text(payload.get("tpot_mean_ms")),
            "output_throughput_tok_s": text(payload.get("output_tps_cluster")),
            "completed_requests": int(num(payload.get("success_count")) or 0) or None,
        }
        add_evidence(
            evidence,
            {
                "evidence_id": f"local-db:{row['record_id']}",
                "source_kind": "measured_internal",
                "coverage_level": None,
                "coverage_level_key": "internal_reference",
                "artifact": rel(path),
                "artifact_sha256": digest,
                "run_id": None,
                "tool_version": None,
                "database_mode": None,
                "identity": identity,
                "workload": workload,
                "metrics": metrics,
                "sla_usable": False,
                "sla_blockers": [
                    "历史 Excel 记录，verification_status="
                    + text(payload.get("verification_status") or "unknown"),
                    f"performance_source_kind={text(payload.get('performance_source_kind'))}",
                    *[
                        f"缺字段：{field}"
                        for field in (payload.get("missing_fields") or [])[:8]
                    ],
                ],
                "reference_only": True,
                "notes": ["只保留性能字段；价格/收入字段未读入"],
            },
        )
    return evidence


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


def group_workpoints(evidence: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, dict[str, Any]] = {}
    for item in evidence:
        if item.get("reference_only"):
            continue
        identity = item["identity"]
        workload = item["workload"]
        key = workpoint_key(identity, workload)
        group = groups.setdefault(
            key,
            {
                "workpoint_id": key,
                "identity": identity,
                "workload": workload,
                "evidence_ids": [],
                "best_level": 0,
                "best_level_key": None,
                "levels": [],
                "sla_usable": False,
                "metrics": {},
                "sla_blockers": [],
            },
        )
        group["evidence_ids"].append(item["evidence_id"])
        level = item.get("coverage_level") or 0
        group["levels"].append(level)
        if level > group["best_level"]:
            group["best_level"] = level
            group["best_level_key"] = item.get("coverage_level_key")
            group["metrics"] = item["metrics"]
            group["sla_usable"] = item["sla_usable"]
            group["sla_blockers"] = item["sla_blockers"]
        elif level == group["best_level"] and item["sla_usable"]:
            group["sla_usable"] = True
            group["sla_blockers"] = []
        if level == 3:
            group["metrics"] = item["metrics"]
            group["sla_usable"] = item["sla_usable"]
            group["sla_blockers"] = item["sla_blockers"]
    return sorted(
        groups.values(),
        key=lambda g: (
            g["identity"].get("model_id") or "",
            g["identity"].get("hardware_id") or "",
            g["workload"].get("concurrency") or 0,
            g["workload"].get("ctx_tokens") or 0,
            g["best_level"],
        ),
        reverse=False,
    )


def summarize(
    evidence: list[dict[str, Any]],
    groups: list[dict[str, Any]],
    blocked_runs: list[dict[str, Any]],
) -> dict[str, Any]:
    by_level = Counter(
        item["coverage_level"] for item in evidence if item.get("coverage_level")
    )
    by_kind = Counter(item["source_kind"] for item in evidence)
    return {
        "evidence_total": len(evidence),
        "evidence_by_kind": dict(sorted(by_kind.items())),
        "evidence_by_level": {
            LEVEL_KEYS[level]: by_level.get(level, 0) for level in sorted(LEVEL_LABELS)
        },
        "workpoints_total": len(groups),
        "workpoints_sla_usable": sum(1 for g in groups if g["sla_usable"]),
        "reference_only_records": sum(
            1 for item in evidence if item.get("reference_only")
        ),
        "blocked_runs": len(blocked_runs),
    }


def build_static_matrix(
    models: list[dict[str, Any]],
    hardware: list[dict[str, Any]],
    evidence: list[dict[str, Any]],
    cost_hardware: set[str],
) -> list[dict[str, Any]]:
    matrix = []
    supported_models = [m for m in models if m.get("model_id")]
    supported_hardware = [h for h in hardware if h.get("supported_in_phase2")]
    for model in supported_models:
        for hw in supported_hardware:
            related = [
                item
                for item in evidence
                if item["identity"].get("model_id") == model["model_id"]
                and item["identity"].get("hardware_id") == hw["hardware_id"]
                and not item.get("reference_only")
            ]
            levels = sorted(
                {
                    item["coverage_level"]
                    for item in related
                    if item.get("coverage_level")
                }
            )
            matrix.append(
                {
                    "model_id": model["model_id"],
                    "model_name": model.get("model_name"),
                    "hardware_id": hw["hardware_id"],
                    "hardware_model": hw.get("product_name"),
                    "static_planning": True,
                    "best_level": max(levels) if levels else 1,
                    "best_level_key": LEVEL_KEYS[max(levels)] if levels else "static_only",
                    "levels_present": [LEVEL_KEYS[level] for level in levels],
                    "evidence_count": len(related),
                    "sla_usable_evidence": sum(1 for item in related if item["sla_usable"]),
                    "cost_analysis_possible": hw["hardware_id"] in cost_hardware,
                    "cost_note": (
                        "仓库内只有合成示例成本项；可算 TCO，但不能当真实报价"
                        if hw["hardware_id"] in cost_hardware
                        else "没有该硬件的成本项，成本结论保持未知"
                    ),
                }
            )
    return matrix


# ---------------------------------------------------------------------------
# Markdown
# ---------------------------------------------------------------------------


def fmt(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "是" if value else "否"
    return str(value)


def render_markdown(doc: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("# 性能预测证据覆盖矩阵")
    lines.append("")
    lines.append(
        "> 本文件与 `docs/performance_coverage.json` 由 "
        "`python scripts/build_performance_coverage.py` 生成；请勿手改。"
    )
    lines.append("")
    lines.append(
        "> **预测不等于实测。** 本矩阵只回答“现有数据能支持哪一层结论”，"
        "不把扫描器均值、公开参考数据或历史 Excel 记录升级为 SLA 证据。"
    )
    lines.append("")
    lines.append("## 1. 分级口径")
    lines.append("")
    lines.append("| 级别 | 含义 | 能回答什么 | 不能回答什么 |")
    lines.append("|---|---|---|---|")
    lines.append(
        "| 1 static_only | 只有 Catalog 资源规格 | 能否装下、卡数是否够 | 性能、SLA |"
    )
    lines.append(
        "| 2 simulated_scan_only | 扫描器稳态单点（均值） | 候选枚举与相对排序 | P99 SLA |"
    )
    lines.append(
        "| 3 simulated_native_p99 | 原生回放 P99（500 请求窗口） | 预测达标/未达标 | 真实实测 |"
    )
    lines.append(
        "| 4 measured_benchmark | 目标配置的真实 Benchmark | 可作为实测证据 | 仍需审批口径 |"
    )
    lines.append("")
    lines.append(
        "公开参考数据与历史 Excel 记录单列为 `public_reference` / "
        "`internal_reference`：它们不是上述四级，不参与 SLA 判定。"
    )
    lines.append("")
    lines.append("## 2. Catalog 静态能力")
    lines.append("")
    lines.append("| 模型 | 硬件 | 最高覆盖级别 | 已有证据数 | 可做成本分析 |")
    lines.append("|---|---|---|---|---|")
    for row in doc["static_matrix"]:
        lines.append(
            "| {model} | {hw} | {level} | {count} | {cost} |".format(
                model=fmt(row["model_id"]),
                hw=fmt(row["hardware_model"]),
                level=fmt(row["best_level_key"]),
                count=row["evidence_count"],
                cost=fmt(row["cost_analysis_possible"]),
            )
        )
    lines.append("")
    lines.append(
        "`static_only` 是矩阵级别，不是一条独立证据；`evidence_by_level` "
        "只统计有实际证据的级别（2/3/4）。"
    )
    lines.append("未支持的模型（解析器会拒绝，不做硬算）：")
    for item in doc["catalog"].get("unsupported_models") or []:
        lines.append(f"- `{item.get('model_id')}`：{item.get('reason')}")
    lines.append("")
    lines.append("## 3. 工作点覆盖")
    lines.append("")
    lines.append(
        "按“模型 + 精度 + 硬件 + 框架/版本 + TP/PP/DP + ISL/OSL/并发/ctx”分组；"
        "样本窗口（请求数）与数据集记录在每条证据上，不参与分组；不同工作点之间**不做插值**。"
    )
    lines.append("")
    lines.append(
        "| 工作点 | 模型/精度 | 硬件/并行 | 负载 | 最高级别 | SLA 可用 | TTFT P99 (ms) | TPOT P99 (ms) | 吞吐 (tok/s) | 阻塞原因 |"
    )
    lines.append("|---|---|---|---|---|---|---|---|---|---|")
    for group in doc["workpoints"]:
        ident = group["identity"]
        load = group["workload"]
        metrics = group["metrics"] or {}
        parallel = "TP{tp}/PP{pp}/DP{dp}".format(
            tp=fmt(ident.get("tp_size")),
            pp=fmt(ident.get("pp_size")),
            dp=fmt(ident.get("dp_size")),
        )
        workload = "ISL{isl}/OSL{osl}/C{conc}".format(
            isl=fmt(load.get("input_len")),
            osl=fmt(load.get("output_len")),
            conc=fmt(load.get("concurrency")),
        )
        if load.get("ctx_tokens"):
            workload += f"/ctx{load['ctx_tokens']}"
        lines.append(
            "| `{wid}` | {model} / {prec} | {hw} / {parallel} | {load} | {level} | {sla} | {ttft} | {tpot} | {tput} | {blockers} |".format(
                wid=group["workpoint_id"],
                model=fmt(ident.get("model_name") or ident.get("model_id")),
                prec=fmt(ident.get("precision")),
                hw=fmt(ident.get("hardware_id") or ident.get("system")),
                parallel=parallel,
                load=workload,
                level=fmt(group["best_level_key"]),
                sla=fmt(group["sla_usable"]),
                ttft=fmt(metrics.get("ttft_p99_ms")),
                tpot=fmt(metrics.get("tpot_p99_ms")),
                tput=fmt(metrics.get("output_throughput_tok_s")),
                blockers="；".join(group.get("sla_blockers") or []) or "—",
            )
        )
    lines.append("")
    lines.append("## 4. 参考证据（不参与 SLA）")
    lines.append("")
    lines.append(
        "| 记录 | 来源类型 | 模型 | 硬件 | 负载 | TTFT P99 | TPOT P99 | 为什么不能用于 SLA |"
    )
    lines.append("|---|---|---|---|---|---|---|---|")
    for item in doc["evidence"]:
        if not item.get("reference_only"):
            continue
        ident = item["identity"]
        load = item["workload"]
        metrics = item["metrics"]
        lines.append(
            "| `{eid}` | {kind} | {model} | {hw} | ISL{isl}/OSL{osl}/C{conc} | {ttft} | {tpot} | {why} |".format(
                eid=item["evidence_id"],
                kind=item["source_kind"],
                model=fmt(ident.get("model_name") or ident.get("model_id")),
                hw=fmt(ident.get("hardware_model") or ident.get("hardware_id")),
                isl=fmt(load.get("input_len")),
                osl=fmt(load.get("output_len")),
                conc=fmt(load.get("concurrency")),
                ttft=fmt(metrics.get("ttft_p99_ms")),
                tpot=fmt(metrics.get("tpot_p99_ms")),
                why="；".join(item.get("sla_blockers") or []) or "—",
            )
        )
    lines.append("")
    lines.append("## 5. 统计与缺口")
    lines.append("")
    summary = doc["summary"]
    lines.append(f"- 证据总数：{summary['evidence_total']}")
    lines.append(
        "- 按级别："
        + "；".join(
            f"{key}={value}" for key, value in summary["evidence_by_level"].items()
        )
    )
    lines.append(f"- 工作点总数：{summary['workpoints_total']}")
    lines.append(f"- SLA 可用工作点：{summary['workpoints_sla_usable']}")
    lines.append(f"- 仅参考记录：{summary['reference_only_records']}")
    lines.append(f"- 阻塞的归档运行：{summary['blocked_runs']}")
    lines.append("")
    if doc.get("blocked_runs"):
        lines.append("阻塞运行：")
        for run in doc["blocked_runs"]:
            lines.append(f"- `{run.get('run_id')}`：{run.get('errors') or run.get('error')}")
        lines.append("")
    if doc.get("gaps"):
        lines.append("已知缺口：")
        for gap in doc["gaps"]:
            lines.append(f"- {gap}")
        lines.append("")
    lines.append("## 6. 结论边界")
    lines.append("")
    usable = [
        group
        for group in doc["workpoints"]
        if group.get("best_level", 0) >= 3 and group.get("sla_usable")
    ]
    if usable:
        lines.append(f"1. 当前有 {len(usable)} 个可比较的原生 P99 工作点：")
        for group in usable:
            ident = group["identity"]
            load = group["workload"]
            metrics = group.get("metrics") or {}
            lines.append(
                "   - {model} / {hw} / {prec} / {fw} {ver} / "
                "TP{tp}×PP{pp}×DP{dp} / C{conc} / ISL{isl} / OSL{osl}"
                " → TTFT P99 {ttft} ms、TPOT P99 {tpot} ms、吞吐 {tput} tok/s".format(
                    model=fmt(ident.get("model_name") or ident.get("model_id")),
                    hw=fmt(ident.get("hardware_id") or ident.get("system")),
                    prec=fmt(ident.get("precision")),
                    fw=fmt(ident.get("framework")),
                    ver=fmt(ident.get("framework_version")),
                    tp=fmt(ident.get("tp_size")),
                    pp=fmt(ident.get("pp_size")),
                    dp=fmt(ident.get("dp_size")),
                    conc=fmt(load.get("concurrency")),
                    isl=fmt(load.get("input_len")),
                    osl=fmt(load.get("output_len")),
                    ttft=fmt(metrics.get("ttft_p99_ms")),
                    tpot=fmt(metrics.get("tpot_p99_ms")),
                    tput=fmt(metrics.get("output_throughput_tok_s")),
                )
            )
    else:
        lines.append("1. 当前没有可比较的原生 P99 工作点。")
    lines.append("2. 其余模型、硬件与工作点最多只能做静态规划；没有 P99 时报告必须")
    lines.append("   给出缺口，不能推荐“达标”。")
    lines.append("3. 扫描器数据只用于候选枚举与相对排序，不能拿来判 SLA。")
    lines.append("4. 公开参考与历史 Excel 记录不参与 SLA、不参与报价门禁。")
    lines.append("5. 成本分析只在本仓库有对应成本项时可用，且示例价格为合成值；")
    lines.append("   “可算成本”不等于“可发布报价”。")
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


def build() -> dict[str, Any]:
    catalog, models, hardware = read_catalog()
    cost_hardware, cost_source = cost_capable_hardware()
    gaps: list[str] = []
    blocked_runs: list[dict[str, Any]] = []

    evidence = read_poc_evidence(models, hardware)
    evidence.extend(read_archive_evidence(ARCHIVES, models, hardware, blocked_runs))
    evidence.extend(public_reference_evidence(models, hardware))
    evidence.extend(db_reference_evidence(models, hardware, gaps))

    groups = group_workpoints(evidence)
    summary = summarize(evidence, groups, blocked_runs)
    static_matrix = build_static_matrix(
        models, hardware, evidence, cost_hardware
    )

    levels_with_data = {
        item["coverage_level"]
        for item in evidence
        if item.get("coverage_level")
    }
    for row in static_matrix:
        if row["best_level"] == 1:
            gaps.append(
                f"{row['model_id']} × {row['hardware_model']} 只有静态规划，"
                "没有该工作点的性能证据"
            )
    for level in (2, 3, 4):
        if level not in levels_with_data:
            gaps.append(f"没有任何证据达到 {level} 级（{LEVEL_LABELS[level]}）")
    if 4 not in levels_with_data:
        gaps.append(
            "没有目标配置的真实 Benchmark：所有实测型记录都缺身份或属于参考数据"
        )
    if not ARCHIVES.exists() or not any(ARCHIVES.glob("*/manifest.json")):
        gaps.append(
            "data/simulation/archives 下没有实时归档：本次覆盖只含 PoC 归档"
        )

    return {
        "schema_version": "1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "generator": "scripts/build_performance_coverage.py",
        "level_definitions": {
            str(level): {"key": LEVEL_KEYS[level], "label": label}
            for level, label in LEVEL_LABELS.items()
        },
        "catalog": {
            **catalog,
            "cost_source": cost_source,
            "cost_hardware_ids": sorted(cost_hardware),
        },
        "static_matrix": static_matrix,
        "workpoints": groups,
        "evidence": evidence,
        "blocked_runs": blocked_runs,
        "gaps": gaps,
        "summary": summary,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json-out", default="docs/performance_coverage.json")
    parser.add_argument("--md-out", default="docs/performance_coverage.md")
    args = parser.parse_args()

    doc = build()
    json_path = ROOT / args.json_out
    md_path = ROOT / args.md_out
    json_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(
        json.dumps(doc, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    md_path.write_text(render_markdown(doc), encoding="utf-8")
    print(
        "覆盖矩阵已生成："
        f"{json_path.relative_to(ROOT)} / {md_path.relative_to(ROOT)}；"
        f"证据 {doc['summary']['evidence_total']} 条，"
        f"工作点 {doc['summary']['workpoints_total']} 个，"
        f"SLA 可用 {doc['summary']['workpoints_sla_usable']} 个"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
