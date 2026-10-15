"""SGLang ``bench_serving`` 结果适配器（版本化）。

支持两种输入形态，均由元数据 ``format_version`` 声明：

1. ``sglang-bench-serving-jsonl/v1``：**汇总行**格式，每行一个 JSON 对象，
   字段对应 ``bench_serving`` 汇总指标（见 ``common.DEFAULT_ALIASES``）。
   多行时每行产出一条记录（例如不同并发档位）。
2. ``sglang-bench-serving-jsonl/v1.per_request``：**逐请求**格式，每行一次请求
   （``input_len`` / ``output_len`` / ``ttft`` / ``itl`` / ``latency``），由适配器聚合。

严格遵守的规则（任务书 §5.3）：
* 只对明确映射的字段做单位换算，ms / s / tok/s 不混用；
* **禁止以平均 ITL 代替平均 TPOT**（逆替换同样禁止，见 ``FORBIDDEN_SUBSTITUTIONS``）；
* 逐请求聚合出的 P99 必须记录 percentile 口径，并标注为适配器派生；
* 原始字段名与原始值全部保留在 ``raw_metrics``；
* 缺失字段留空并进入 ``missing_fields``，**不填 0**。
"""

from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from llm_capacity_pricing.importers.common import (
    FORBIDDEN_SUBSTITUTIONS,
    PERCENTILE_METHOD,
    AdapterMetadataError,
    BenchmarkParseError,
    build_aliases,
    build_evidence,
    dec,
    int_or_none,
    is_non_numeric,
    mean,
    percentile_nearest_rank,
    pick,
    sha256_path,
)
from llm_capacity_pricing.schemas.benchmark import BenchmarkRecord
from llm_capacity_pricing.schemas.evidence import Evidence

SUPPORTED_FORMAT_VERSIONS = (
    "sglang-bench-serving-jsonl/v1",
    "sglang-bench-serving-jsonl/v1.per_request",
)

#: 逐请求行的可识别键（命中 ≥3 个即视为逐请求样本）
PER_REQUEST_KEYS = ("input_len", "output_len", "ttft", "itl", "latency", "gen_tps")

#: 适配器自身声明为缺失的字段（来源文件不含这些信息）
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
    "mean_observed_concurrency",
    "num_requests",
    "success_count",
    "duration_s",
    "ttft_p99_ms",
    "tpot_mean_ms",
    "tpot_p99_ms",
    "input_tokens_target",
    "output_tokens_target",
)


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise BenchmarkParseError(f"找不到文件: {path}")
    out: list[dict[str, Any]] = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        s = line.strip()
        if not s:
            continue
        try:
            obj = json.loads(s)
        except json.JSONDecodeError as exc:
            raise BenchmarkParseError(f"{path.name} 第 {lineno} 行不是合法 JSON: {exc}") from exc
        if not isinstance(obj, dict):
            raise BenchmarkParseError(f"{path.name} 第 {lineno} 行不是 JSON 对象")
        obj["__line__"] = lineno
        out.append(obj)
    if not out:
        raise BenchmarkParseError(f"{path.name} 没有任何有效行")
    return out


def _looks_per_request(row: dict[str, Any]) -> bool:
    return sum(1 for k in PER_REQUEST_KEYS if k in row) >= 3


def _aggregate_per_request(
    rows: list[dict[str, Any]],
) -> tuple[dict[str, Any], list[str], dict[str, str]]:
    """把逐请求样本聚合成汇总指标。

    TPOT 由逐请求 ``(latency - ttft) / (output_len - 1)`` 计算；若样本不具备该三列，
    TPOT 保持缺失并打标记——**不会用 ITL 顶替**。
    """
    flags: list[str] = ["aggregated_from_per_request_samples"]
    defs: dict[str, str] = {
        "aggregation": "由适配器对逐请求样本聚合；percentile 方法: " + PERCENTILE_METHOD,
    }

    ttfts: list[Decimal] = []
    itls: list[Decimal] = []
    tpots: list[Decimal] = []
    e2es: list[Decimal] = []
    latencies: list[Decimal] = []
    total_out = Decimal(0)
    total_in = Decimal(0)
    success = 0
    tpot_unavailable = False

    for row in rows:
        ttft = dec(row.get("ttft"))
        latency = dec(row.get("latency"))
        out_len = dec(row.get("output_len"))
        in_len = dec(row.get("input_len"))
        if in_len is not None:
            total_in += in_len
        if out_len is not None:
            total_out += out_len
        if row.get("success", True) is not False and out_len is not None and out_len > 0:
            success += 1
        if ttft is not None:
            ttfts.append(ttft)
        if latency is not None:
            e2es.append(latency)
            latencies.append(latency)
        itl = dec(row.get("itl"))
        if itl is not None:
            itls.append(itl)
        if ttft is not None and latency is not None and out_len is not None and out_len > 1:
            tpots.append((latency - ttft) / (out_len - 1))
        elif latency is not None:
            tpot_unavailable = True

    if not tpots:
        flags.append("tpot_not_computable_from_samples")
    elif tpot_unavailable:
        flags.append("tpot_partially_computable")
    if itls:
        flags.append("itl_present_kept_separate_from_tpot")

    summary: dict[str, Any] = {
        "num_requests": len(rows),
        "success_count": success,
        "total_input_tokens": total_in,
        "total_output_tokens": total_out,
        "mean_ttft_ms": mean(ttfts),
        "p99_ttft_ms": percentile_nearest_rank(ttfts, 99),
        "mean_tpot_ms": mean(tpots),
        "p99_tpot_ms": percentile_nearest_rank(tpots, 99),
        "mean_itl_ms": mean(itls),
        "p99_itl_ms": percentile_nearest_rank(itls, 99),
        "mean_e2e_ms": mean(e2es),
        "p99_e2e_ms": percentile_nearest_rank(e2es, 99),
        "duration_s": max(latencies) if latencies else None,
    }
    defs["mean_tpot_ms"] = (
        "由适配器按逐请求 (latency - ttft)/(output_len - 1) 求均值；"
        "不是来源直接给出的 TPOT，也不是 ITL"
    )
    defs["p99_tpot_ms"] = f"同上，percentile 方法: {PERCENTILE_METHOD}"
    defs["mean_itl_ms"] = "来源逐请求 itl 列的均值；ITL ≠ TPOT，不可互换"
    duration = summary["duration_s"]
    if duration and duration > 0 and tpots:
        summary["output_throughput_tok_s"] = total_out / duration
        summary["input_throughput_tok_s"] = total_in / duration
        defs["output_throughput_tok_s"] = (
            "由适配器计算: total_output_tokens / max(latency)，集群口径"
        )
    return summary, flags, defs


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
    rows = _load_jsonl(p)
    raw_sha = sha256_path(p)
    retrieved_at = datetime.now()

    per_request = fmt.endswith(".per_request") or all(_looks_per_request(r) for r in rows)
    metrics_defs: dict[str, str] = dict(metadata.get("metric_definitions") or {})
    extra_flags: list[str] = []

    if per_request:
        if len(rows) == 1 and not _looks_per_request(rows[0]):
            raise BenchmarkParseError("声明为 per_request 格式，但文件不像逐请求样本")
        aggregate, agg_flags, agg_defs = _aggregate_per_request(rows)
        metrics_defs.update(agg_defs)
        extra_flags.extend(agg_flags)
        emit_rows = [aggregate]
        raw_payload = {
            "kind": "per_request_jsonl",
            "line_count": len(rows),
            "lines": rows,
        }
    else:
        emit_rows = rows
        raw_payload = {"kind": "summary_jsonl", "row_count": len(rows), "rows": rows}

    multi = len(emit_rows) > 1
    records: list[BenchmarkRecord] = []
    evidences: list[Evidence] = []

    for i, row in enumerate(emit_rows):
        ev = build_evidence(
            metadata,
            raw_sha256=raw_sha,
            retrieved_at=retrieved_at,
            publisher_default="sglang",
            extra_flags=extra_flags + ["sglang_bench_serving_adapter"],
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
            framework="sglang",
            framework_version=metadata.get("framework_version"),
            model_revision=metadata.get("model_revision"),
            verification_status=metadata.get("verification_status", "partial_evidence"),
            format_version=fmt,
            metric_definitions=metrics_defs,
            raw_metrics={"source_file": p.name, "raw_sha256": raw_sha, **raw_payload},
            quality_flags=[
                "sglang_bench_serving_adapter",
                "raw_field_names_preserved",
                *extra_flags,
            ],
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

        # --- 时延：严格逐字段映射 ---
        rec.ttft_mean_ms = dec(pick(row, "mean_ttft_ms", aliases))
        rec.ttft_p99_ms = dec(pick(row, "p99_ttft_ms", aliases))
        rec.tpot_mean_ms = dec(pick(row, "mean_tpot_ms", aliases))
        rec.tpot_p99_ms = dec(pick(row, "p99_tpot_ms", aliases))
        rec.itl_p99_ms = dec(pick(row, "p99_itl_ms", aliases))
        rec.e2e_p99_ms = dec(pick(row, "p99_e2e_ms", aliases))
        rec.failure_rate = dec(pick(row, "failure_rate", aliases))

        for canonical, forbidden in FORBIDDEN_SUBSTITUTIONS.items():
            if getattr(rec, canonical, None) is None:
                present = [f for f in forbidden if f in (row or {}) and row[f] is not None]
                if present:
                    rec.quality_flags.append(
                        "refused_substitution:"
                        f"{canonical}_left_null_despite_{'_'.join(present)}"
                    )

        # --- 吞吐：集群 -> 每卡（同配置归一化，非跨配置外推）---
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

        # --- 缓存口径：来源未给出，保持 unknown ---
        rec.cache_metric_semantics = "unknown"
        rec.legacy_proxy_for_billable_hit_rate = False

        for f in ADAPTER_ABSENT_FIELDS:
            if getattr(rec, f) is None:
                rec.note_missing(f)
        if rec.verification_status == "verified" and (rec.missing_fields or rec.ttft_p99_ms is None):
            rec.verification_status = "partial_evidence"
            rec.quality_flags.append("downgraded_to_partial_evidence_incomplete_fields")

        records.append(rec)

    return records, evidences


def parse_sglang_bench_serving_jsonl(
    path: str | Path, metadata: dict[str, Any]
) -> list[BenchmarkRecord]:
    """任务书 §5.3 约定的入口：返回 ``list[BenchmarkRecord]``。"""
    return _parse(path, metadata)[0]


def parse_sglang_bench_serving_jsonl_full(
    path: str | Path, metadata: dict[str, Any]
) -> tuple[list[BenchmarkRecord], list[Evidence]]:
    """同时返回记录与证据，供入库使用。"""
    return _parse(path, metadata)
