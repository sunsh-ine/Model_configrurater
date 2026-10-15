"""vLLM ``bench serve`` 结果适配器（版本化）。

``vllm bench serve`` 的 ``--save-result`` / ``--result-dir`` 会写出一份 JSON 结果，
其字段名即官方脚本的统计口径（``mean_ttft_ms`` / ``p99_ttft_ms`` / ``mean_tpot_ms`` /
``p99_tpot_ms`` / ``mean_itl_ms`` / ``p99_itl_ms`` / ``mean_e2el_ms`` / ``p99_e2el_ms`` /
``request_throughput`` / ``input_throughput`` / ``output_throughput`` / ``duration`` /
``completed`` / ``max_concurrency`` / ``request_rate`` …）。

接受三种外层结构：单个对象、对象数组、``{"results": [...]}``。

严格遵守的规则（任务书 §5.3）：
* **禁止以平均 ITL 代替平均 TPOT**；vLLM 的 ``mean_tpot_ms`` 与 ``mean_itl_ms``
  是两个独立字段，缺失即留空；
* 单位不混用：``duration`` 以秒计，时延以毫秒计；
* 集群吞吐按元数据给出的 ``deployment_gpu_count`` 归一化为每卡吞吐，并标注依据；
* 原始 JSON 全量保留在 ``raw_metrics``。
"""

from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from llm_capacity_pricing.importers.common import (
    FORBIDDEN_SUBSTITUTIONS,
    AdapterMetadataError,
    BenchmarkParseError,
    build_aliases,
    build_evidence,
    dec,
    int_or_none,
    is_non_numeric,
    pick,
    sha256_path,
)
from llm_capacity_pricing.schemas.benchmark import BenchmarkRecord
from llm_capacity_pricing.schemas.evidence import Evidence

SUPPORTED_FORMAT_VERSIONS = (
    "vllm-bench-serve-json/v1",
)

ADAPTER_ABSENT_FIELDS = (
    "model_revision",
    "framework_version",
    "driver_version",
    "hardware_sku",
    "tp_size",
    "dp_size",
    "ep_size",
    "dataset_name",
    "dataset_digest",
    "ttft_p99_ms",
    "tpot_mean_ms",
    "tpot_p99_ms",
    "mean_observed_concurrency",
    "input_tokens_target",
    "output_tokens_target",
)


def _load_objects(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise BenchmarkParseError(f"找不到文件: {path}")
    text = path.read_text(encoding="utf-8")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise BenchmarkParseError(f"{path.name} 不是合法 JSON: {exc}") from exc

    if isinstance(data, dict):
        inner = data.get("results")
        if isinstance(inner, list):
            objects = inner
        else:
            objects = [data]
    elif isinstance(data, list):
        objects = data
    else:
        raise BenchmarkParseError(f"{path.name} 顶层必须是对象、数组或 {{'results': [...]}}")

    out: list[dict[str, Any]] = []
    for i, obj in enumerate(objects):
        if not isinstance(obj, dict):
            raise BenchmarkParseError(f"{path.name} 第 {i} 个元素不是 JSON 对象")
        out.append(obj)
    if not out:
        raise BenchmarkParseError(f"{path.name} 没有有效结果对象")
    return out


def _parse(
    path: str | Path,
    metadata: dict[str, Any],
) -> tuple[list[BenchmarkRecord], list[Evidence]]:
    p = Path(path)
    fmt = metadata.get("format_version")
    if fmt not in SUPPORTED_FORMAT_VERSIONS:
        raise BenchmarkParseError(
            f"不支持的 format_version={fmt!r}；已支持: {SUPPORTED_FORMAT_VERSIONS}"
        )
    for required in ("evidence_id", "model_name", "hardware_model", "deployment_gpu_count"):
        if metadata.get(required) in (None, ""):
            raise AdapterMetadataError(f"元数据缺少必需字段 {required!r}")

    n_gpu = int(metadata["deployment_gpu_count"])
    if n_gpu <= 0:
        raise AdapterMetadataError("deployment_gpu_count 必须为正整数")

    aliases = build_aliases(metadata)
    objects = _load_objects(p)
    raw_sha = sha256_path(p)
    retrieved_at = datetime.now()
    multi = len(objects) > 1

    metrics_defs: dict[str, str] = {
        "source_schema": (
            "vLLM `vllm bench serve` 结果 JSON；字段名为脚本原始输出名，未做重命名"
        ),
        "mean_tpot_ms": "取自来源字段 mean_tpot_ms；与 mean_itl_ms 是不同指标，不做替代",
        "p99_tpot_ms": "取自来源字段 p99_tpot_ms；percentile 口径由来源决定，本适配器不重算",
        "mean_e2el_ms": "端到端时延（含排队+prefill+decode）均值",
        "throughput": "input_throughput/output_throughput 为来源集群口径 tok/s",
        **dict(metadata.get("metric_definitions") or {}),
    }

    records: list[BenchmarkRecord] = []
    evidences: list[Evidence] = []

    for i, row in enumerate(objects):
        ev = build_evidence(
            metadata,
            raw_sha256=raw_sha,
            retrieved_at=retrieved_at,
            publisher_default="vllm",
            extra_flags=["vllm_bench_serve_adapter"],
        )
        if multi:
            ev.evidence_id = f"{metadata['evidence_id']}::{i}"
        evidences.append(ev)

        base_record_id = metadata.get("record_id") or f"{metadata['evidence_id']}::rec"
        rec = BenchmarkRecord(
            record_id=f"{base_record_id}::{i}" if multi else base_record_id,
            model_name=metadata["model_name"],
            hardware_model=metadata["hardware_model"],
            deployment_gpu_count=n_gpu,
            evidence_id=ev.evidence_id,
            performance_source_kind=metadata.get("performance_source_kind", "official_benchmark"),
            framework="vllm",
            framework_version=metadata.get("framework_version"),
            model_revision=metadata.get("model_revision"),
            # V3.1 R2：严格匹配需要这些身份字段；元数据声明了就带上，没声明保持 None
            model_precision=metadata.get("weight_precision") or metadata.get("model_precision"),
            tp_size=metadata.get("tp_size"),
            dp_size=metadata.get("dp_size"),
            dataset_name=metadata.get("dataset_name"),
            verification_status=metadata.get("verification_status", "partial_evidence"),
            format_version=fmt,
            metric_definitions=metrics_defs,
            raw_metrics={
                "source_file": p.name,
                "raw_sha256": raw_sha,
                "kind": "vllm_bench_serve_result_json",
                "object_index": i,
                "object": row,
            },
            quality_flags=["vllm_bench_serve_adapter", "raw_field_names_preserved"],
        )

        # --- 负载 ---
        rec.num_requests = int_or_none(pick(row, "num_requests", aliases))
        rec.success_count = int_or_none(pick(row, "success_count", aliases))
        rec.duration_s = dec(pick(row, "duration_s", aliases))
        rec.configured_concurrency = int_or_none(pick(row, "max_concurrency", aliases))
        rec.request_rate_rps = dec(pick(row, "request_rate", aliases))
        raw_rate = pick(row, "request_rate", aliases)
        if is_non_numeric(raw_rate):
            rec.quality_flags.append("non_numeric_request_rate_left_null")
            rec.raw_metrics["request_rate_raw"] = str(raw_rate)
        rec.input_tokens_target = int_or_none(
            metadata.get("input_tokens_target", pick(row, "mean_input_tokens", aliases))
        )
        rec.output_tokens_target = int_or_none(
            metadata.get("output_tokens_target", pick(row, "mean_output_tokens", aliases))
        )
        rec.dataset_name = metadata.get("dataset_name")
        rec.dataset_digest = metadata.get("dataset_digest")

        # --- 时延 ---
        rec.ttft_mean_ms = dec(pick(row, "mean_ttft_ms", aliases))
        rec.ttft_p99_ms = dec(pick(row, "p99_ttft_ms", aliases))
        rec.tpot_mean_ms = dec(pick(row, "mean_tpot_ms", aliases))
        rec.tpot_p99_ms = dec(pick(row, "p99_tpot_ms", aliases))
        rec.itl_p99_ms = dec(pick(row, "p99_itl_ms", aliases))
        rec.e2e_p99_ms = dec(pick(row, "p99_e2e_ms", aliases))
        rec.failure_rate = dec(pick(row, "failure_rate", aliases))

        for canonical, forbidden in FORBIDDEN_SUBSTITUTIONS.items():
            if getattr(rec, canonical, None) is None:
                present = [f for f in forbidden if f in row and row[f] is not None]
                if present:
                    rec.quality_flags.append(
                        "refused_substitution:"
                        f"{canonical}_left_null_despite_{'_'.join(present)}"
                    )

        # --- 吞吐：集群 -> 每卡 ---
        in_tp = dec(pick(row, "input_throughput_tok_s", aliases))
        out_tp = dec(pick(row, "output_throughput_tok_s", aliases))
        if in_tp is not None:
            rec.input_tps_cluster = in_tp
            rec.input_tps_per_gpu = in_tp / Decimal(n_gpu)
            rec.input_tps_cluster_basis = (
                f"derived_from_cluster_and_gpu_count: {in_tp} / {n_gpu}"
            )
            rec.quality_flags.append("per_gpu_throughput_derived_from_cluster_and_gpu_count")
        if out_tp is not None:
            rec.output_tps_cluster = out_tp
            rec.output_tps_per_gpu = out_tp / Decimal(n_gpu)

        for total_key, canonical in (
            ("total_input_tokens", "reported_total_input_tokens"),
            ("total_output_tokens", "reported_total_output_tokens"),
        ):
            val = dec(pick(row, total_key, aliases))
            if val is not None:
                rec.raw_metrics[canonical] = str(val)

        if rec.success_count == 0:
            rec.quality_flags.append("zero_successful_requests")
        if rec.num_requests is not None and rec.success_count is not None:
            if rec.success_count < rec.num_requests and rec.failure_rate is None:
                rec.failure_rate = (
                    Decimal(rec.num_requests - rec.success_count) / Decimal(rec.num_requests)
                )
                rec.quality_flags.append("failure_rate_derived_from_counts")

        rec.cache_metric_semantics = "unknown"
        rec.legacy_proxy_for_billable_hit_rate = False

        for f in ADAPTER_ABSENT_FIELDS:
            if getattr(rec, f) is None:
                rec.note_missing(f)
        if rec.verification_status == "verified" and rec.missing_fields:
            rec.verification_status = "partial_evidence"
            rec.quality_flags.append("downgraded_to_partial_evidence_incomplete_fields")

        records.append(rec)

    return records, evidences


def parse_vllm_bench_serve_json(
    path: str | Path, metadata: dict[str, Any]
) -> list[BenchmarkRecord]:
    """任务书 §5.3 约定的入口：返回 ``list[BenchmarkRecord]``。"""
    return _parse(path, metadata)[0]


def parse_vllm_bench_serve_json_full(
    path: str | Path, metadata: dict[str, Any]
) -> tuple[list[BenchmarkRecord], list[Evidence]]:
    """同时返回记录与证据，供入库使用。"""
    return _parse(path, metadata)
