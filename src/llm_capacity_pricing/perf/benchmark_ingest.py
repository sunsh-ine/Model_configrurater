"""AG4：真实 Benchmark 导入闭环（可在无 GPU 环境用夹具回归）。

流程：冻结配置 -> 原始日志 -> 重算指标 -> 校验身份/哈希 -> 适配器导入 -> 对比预测。

本模块不执行 Benchmark；真实执行仍受 AG5 授权门禁约束。
"""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml

from llm_capacity_pricing.importers.common import (
    BenchmarkParseError,
    dec,
    percentile_nearest_rank,
)
from llm_capacity_pricing.importers.sglang_adapter import (
    parse_sglang_bench_serving_jsonl_full,
)
from llm_capacity_pricing.perf.calibration import compare_metrics
from llm_capacity_pricing.perf.source_verification import resolve_source_kind

_IDENTITY_FIELDS = (
    "model_name",
    "model_revision",
    "weight_precision",
    "kv_precision",
    "hardware_model",
    "deployment_gpu_count",
    "framework",
    "framework_version",
    "tp_size",
    "dp_size",
    "pp_size",
    "dataset_name",
    "configured_max_concurrency",
    "arrival_mode",
    "request_rate_rps",
    "input_len",
    "output_len",
)

_CANONICAL_METRICS = {
    "ttft_p99_ms": "m.latency.ttft.p99",
    "tpot_p99_ms": "m.latency.tpot.p99",
    "output_throughput_tok_s": "m.throughput.cluster.output",
}


def _canonical_sha(payload: Any) -> str | None:
    if payload is None:
        return None
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_requests(path: str | Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            payload = json.loads(line)
            if not isinstance(payload, dict):
                raise BenchmarkParseError("逐请求文件每行必须是 JSON 对象")
            rows.append(payload)
    if not rows:
        raise BenchmarkParseError("逐请求文件没有有效行")
    return rows


def frozen_identity(metadata: Mapping[str, Any]) -> dict[str, Any]:
    return {field: metadata.get(field) for field in _IDENTITY_FIELDS}


def identity_sha256(metadata: Mapping[str, Any]) -> str:
    payload = json.dumps(
        frozen_identity(metadata), ensure_ascii=False, sort_keys=True
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _is_success(row: Mapping[str, Any]) -> bool:
    status = str(row.get("terminal_status") or row.get("status") or "").lower()
    if status in ("cancelled", "canceled"):
        return False
    if row.get("success") is False:
        return False
    return status in ("", "completed", "success", "succeeded", "ok")


def recompute_metrics(
    rows: Sequence[Mapping[str, Any]],
    *,
    measurement_window_ms: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """从逐请求原始记录独立重算指标（nearest-rank P99）。"""
    success_rows: list[Mapping[str, Any]] = []
    failed = 0
    cancelled = 0
    window_start: Decimal | None = None
    window_end: Decimal | None = None
    timed_latencies = 0
    untimed_latencies = 0
    for row in rows:
        status = str(row.get("terminal_status") or row.get("status") or "").lower()
        latency_all = dec(
            row.get("latency_ms", row.get("latency", row.get("e2e_latency_ms")))
        )
        arrival_all = dec(row.get("arrival_ms", row.get("arrival_time_ms")))
        timestamp_all = dec(row.get("timestamp"))
        if arrival_all is None and timestamp_all is not None:
            arrival_all = timestamp_all * Decimal(1000)
        if latency_all is not None:
            if arrival_all is not None:
                timed_latencies += 1
                end_all = arrival_all + latency_all
                window_start = (
                    arrival_all
                    if window_start is None
                    else min(window_start, arrival_all)
                )
                window_end = (
                    end_all if window_end is None else max(window_end, end_all)
                )
            else:
                untimed_latencies += 1
        if status in ("cancelled", "canceled"):
            cancelled += 1
        elif _is_success(row):
            output_len = dec(
                row.get("output_length", row.get("output_len"))
            )
            latency = dec(
                row.get("latency_ms", row.get("latency", row.get("e2e_latency_ms")))
            )
            if (
                output_len is not None
                and output_len > 0
                and latency is not None
            ):
                success_rows.append(row)
            else:
                failed += 1
        else:
            failed += 1

    ttfts: list[Decimal] = []
    tpots: list[Decimal] = []
    e2es: list[Decimal] = []
    total_out = Decimal(0)
    total_in = Decimal(0)
    for row in success_rows:
        ttft = dec(row.get("ttft_ms", row.get("ttft")))
        latency = dec(row.get("latency_ms", row.get("latency", row.get("e2e_latency_ms"))))
        output_len = dec(row.get("output_length", row.get("output_len")))
        input_len = dec(row.get("input_length", row.get("input_len")))
        arrival = dec(row.get("arrival_ms", row.get("arrival_time_ms")))
        timestamp = dec(row.get("timestamp"))
        if arrival is None and timestamp is not None:
            arrival = timestamp * Decimal(1000)
        if output_len is None or output_len <= 0:
            continue
        total_out += output_len
        if input_len is not None:
            total_in += input_len
        e2es.append(latency)
        if ttft is not None:
            ttfts.append(ttft)
            if output_len > 1 and latency >= ttft:
                tpots.append((latency - ttft) / (output_len - 1))
    window_source = "observed_success_requests"
    window_coverage = (
        "verified"
        if (
            rows
            and timed_latencies == len(rows)
            and timed_latencies + untimed_latencies == len(rows)
        )
        else "unverified"
    )
    duration_s = (
        (window_end - window_start) / Decimal(1000)  # type: ignore[operator]
        if (
            timed_latencies == len(rows)
            and not untimed_latencies
            and window_start is not None
        )
        else None
    )
    if measurement_window_ms:
        explicit_start = dec(measurement_window_ms.get("start_ms"))
        explicit_end = dec(measurement_window_ms.get("end_ms"))
        if explicit_start is None or explicit_end is None or explicit_end <= explicit_start:
            window_source = "invalid_declared_window"
            duration_s = None
        elif (
            window_start is not None
            and window_end is not None
            and (explicit_start > window_start or explicit_end < window_end)
        ):
            window_source = "declared_window_does_not_cover_samples"
            duration_s = None
        elif window_coverage != "verified":
            window_source = "declared_window_coverage_unverified"
            duration_s = None
        else:
            window_source = "declared_measurement_window"
            duration_s = (explicit_end - explicit_start) / Decimal(1000)
    return {
        "num_requests": len(rows),
        "success_count": len(success_rows),
        "failure_count": failed,
        "cancelled_count": cancelled,
        "ttft_p99_ms": percentile_nearest_rank(ttfts, 99),
        "tpot_p99_ms": percentile_nearest_rank(tpots, 99),
        "e2e_p99_ms": percentile_nearest_rank(e2es, 99),
        "output_throughput_tok_s": (
            total_out / duration_s if duration_s and duration_s > 0 else None
        ),
        "input_throughput_tok_s": (
            total_in / duration_s if duration_s and duration_s > 0 else None
        ),
        "duration_s": duration_s,
        "window_source": window_source,
        "window_coverage": window_coverage,
        "total_output_tokens": total_out,
        "metric_definitions": dict(_CANONICAL_METRICS),
        "metric_samples": {
            "ttft": len(ttfts),
            "tpot": len(tpots),
            "e2e": len(e2es),
            "success": len(success_rows),
        },
        "metric_coverage": {
            "ttft": (
                Decimal(len(ttfts)) / Decimal(len(success_rows))
                if success_rows
                else None
            ),
            "tpot": (
                Decimal(len(tpots)) / Decimal(len(success_rows))
                if success_rows
                else None
            ),
            "e2e": (
                Decimal(len(e2es)) / Decimal(len(success_rows))
                if success_rows
                else None
            ),
        },
        "metric_completeness": (
            "complete"
            if success_rows
            and len(ttfts) == len(tpots) == len(e2es) == len(success_rows)
            else "partial"
        ),
    }


def _relative_error(left: Decimal | None, right: Decimal | None) -> Decimal | None:
    if left is None or right is None or right == 0:
        return None
    return abs(left - right) / abs(right)


def _compare_import(
    record: Any,
    recomputed: Mapping[str, Any],
    *,
    tolerance: Decimal,
    compare_throughput: bool = True,
) -> list[str]:
    errors: list[str] = []
    pairs = [
        ("ttft_p99_ms", record.ttft_p99_ms),
        ("tpot_p99_ms", record.tpot_p99_ms),
    ]
    if compare_throughput:
        pairs.append(("output_throughput_tok_s", record.output_tps_cluster))
    for field, imported in pairs:
        expected = recomputed.get(field)
        if expected is None and imported is None:
            continue
        if expected is None or imported is None:
            errors.append(f"{field} 无法交叉核验（重算或导入值为 unknown）")
            continue
        relative = _relative_error(Decimal(str(expected)), Decimal(str(imported)))
        if relative is None or relative > tolerance:
            errors.append(
                f"{field} 重算 {expected} 与导入 {imported} 不一致"
            )
    if record.success_count != recomputed["success_count"]:
        errors.append(
            f"成功请求数不一致：重算 {recomputed['success_count']} / "
            f"导入 {record.success_count}"
        )
    if record.num_requests != recomputed["num_requests"]:
        errors.append(
            f"请求总数不一致：重算 {recomputed['num_requests']} / "
            f"导入 {record.num_requests}"
        )
    return errors


def _calibration_benchmark(
    metadata: Mapping[str, Any],
    recomputed: Mapping[str, Any],
    evidence: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "model_id": metadata.get("model_id") or metadata.get("model_name"),
        "model_revision": metadata.get("model_revision"),
        "weight_precision": metadata.get("weight_precision"),
        "kv_precision": metadata.get("kv_precision"),
        "hardware_id": metadata.get("hardware_id") or metadata.get("hardware_model"),
        "gpu_count": metadata.get("deployment_gpu_count"),
        "framework": metadata.get("framework"),
        "framework_version": metadata.get("framework_version"),
        "tp_size": metadata.get("tp_size"),
        "dp_size": metadata.get("dp_size"),
        "pp_size": metadata.get("pp_size") or 1,
        "input_len": metadata.get("input_len"),
        "output_len": metadata.get("output_len"),
        "arrival_mode": metadata.get("arrival_mode") or "fixed_concurrency",
        "concurrency": metadata.get("configured_max_concurrency"),
        "request_rate_rps": metadata.get("request_rate_rps"),
        "dataset": metadata.get("dataset_name"),
        "p99_ttft_ms": recomputed.get("ttft_p99_ms"),
        "p99_tpot_ms": recomputed.get("tpot_p99_ms"),
        "output_throughput_tok_s": recomputed.get("output_throughput_tok_s"),
        "metric_definitions": recomputed.get("metric_definitions"),
        "data_trust": metadata.get("performance_source_kind"),
        "evidence": dict(evidence),
    }


def run_import_pipeline(
    raw_path: str | Path,
    metadata: Mapping[str, Any],
    *,
    evidence_root: str | Path | None = None,
    prediction: Mapping[str, Any] | None = None,
    db_path: str | Path | None = None,
    metadata_file: str | Path | None = None,
    registry: str | Path | None = None,
    tolerance: Decimal = Decimal("0.000001"),
) -> dict[str, Any]:
    """校验并导入一次 Benchmark；任何一步不通过都返回 ``accepted=False``。"""
    path = Path(raw_path)
    meta = dict(metadata)
    meta.setdefault("source_file", path.name)
    report: dict[str, Any] = {
        "accepted": False,
        "raw_file": str(path),
        "identity": frozen_identity(meta),
        "identity_sha256": identity_sha256(meta),
        "errors": [],
        "warnings": [],
    }
    declared_identity = meta.get("frozen_identity_sha256")
    if not declared_identity:
        report["errors"].append("缺少 frozen_identity_sha256：配置未冻结")
        return report
    if declared_identity != report["identity_sha256"]:
        report["errors"].append("冻结配置身份哈希不一致：配置在运行后被修改")
        return report

    if not path.is_file():
        report["errors"].append(f"原始日志不存在：{path}")
        return report
    actual_sha = sha256_file(path)
    evidence = dict(meta.get("evidence") or {})
    declared_sha = evidence.get("source_sha256") or meta.get("source_sha256")
    report["raw_sha256"] = actual_sha
    report["declared_sha256"] = declared_sha
    report["digests"] = {
        "raw_sha256": actual_sha,
        "identity_sha256": report["identity_sha256"],
        "config_sha256": _canonical_sha(meta.get("server_args") or meta.get("config")),
        "environment_sha256": _canonical_sha(meta.get("environment")),
        "metric_definitions_sha256": _canonical_sha(
            meta.get("metric_definitions")
        ),
    }
    if not declared_sha:
        report["errors"].append("缺少原始文件 SHA256 声明，不能作为证据")
        return report
    if str(declared_sha).lower() != actual_sha.lower():
        report["errors"].append("原始文件 SHA256 与声明不一致")
        return report

    declared_defs = metadata.get("metric_definitions") or {}
    for metric, expected in _CANONICAL_METRICS.items():
        if declared_defs.get(metric) != expected:
            report["errors"].append(
                f"指标口径未声明或非标准：{metric} != {expected}"
            )
    if report["errors"]:
        return report

    verification = resolve_source_kind(
        meta,
        raw_sha256=actual_sha,
        identity_sha256=report["identity_sha256"],
        registry=registry,
    )
    report["source_verification"] = verification
    report["declared_source_kind"] = verification["declared_source_kind"]
    report["verified_source_kind"] = verification["verified_source_kind"]
    if (
        not verification["verified"]
        and verification["verified_source_kind"] != "synthetic_fixture"
    ):
        report["warnings"].append(
            "来源未认证：按 unverified_upload 处理，不产生实测身份"
        )
    adapter_meta = dict(meta)
    adapter_meta["performance_source_kind"] = verification[
        "verified_source_kind"
    ]

    try:
        rows = load_requests(path)
        recomputed = recompute_metrics(
            rows,
            measurement_window_ms=meta.get("measurement_window_ms"),
        )
    except Exception as exc:  # noqa: BLE001
        report["errors"].append(
            f"逐请求日志无法解析：{type(exc).__name__}: {exc}"
        )
        return report
    report["recomputed"] = {
        key: str(value) if isinstance(value, Decimal) else value
        for key, value in recomputed.items()
    }
    if recomputed["success_count"] == 0:
        report["errors"].append("没有任何成功请求")
        return report

    try:
        records, evidences = parse_sglang_bench_serving_jsonl_full(
            path, adapter_meta
        )
    except Exception as exc:  # noqa: BLE001
        report["errors"].append(f"适配器导入失败：{type(exc).__name__}: {exc}")
        return report
    if len(records) != 1:
        report["errors"].append(f"适配器应产出 1 条记录，实际 {len(records)} 条")
        return report
    record = records[0]
    if getattr(record, "performance_source_kind", None) == "synthetic_fixture":
        report["warnings"].append(
            "synthetic_fixture：只验证导入管线，不得用于生产 SLA 或校准"
        )

    compare_throughput = (
        recomputed.get("output_throughput_tok_s") is not None
        or record.output_tps_cluster is not None
    )
    consistency_errors = _compare_import(
        record,
        recomputed,
        tolerance=tolerance,
        compare_throughput=compare_throughput,
    )
    report["import_consistency"] = {
        "consistent": not consistency_errors,
        "errors": consistency_errors,
        "throughput_compared": compare_throughput,
    }
    if recomputed.get("metric_completeness") != "complete":
        report["warnings"].append(
            "并非每个成功请求都具备 TTFT/TPOT/E2E 全部指标；"
            "请查看 metric_samples / metric_coverage"
        )
    if consistency_errors:
        report["errors"].extend(consistency_errors)
        return report

    report["imported"] = {
        "record_id": record.record_id,
        "evidence_id": evidences[0].evidence_id if evidences else None,
        "framework": record.framework,
        "server_framework": record.framework,
        "benchmark_client": meta.get("benchmark_client")
        or meta.get("client_tool"),
        "performance_source_kind": record.performance_source_kind,
        "num_requests": record.num_requests,
        "success_count": record.success_count,
        "ttft_p99_ms": str(record.ttft_p99_ms),
        "tpot_p99_ms": str(record.tpot_p99_ms),
        "output_throughput_tok_s": str(record.output_tps_cluster),
    }

    sla_reasons: list[str] = []
    if verification["verified_source_kind"] != "internal_measurement":
        sla_reasons.append("来源未认证为 internal_measurement")
    if recomputed.get("metric_completeness") != "complete":
        sla_reasons.append("并非所有成功请求都具备 TTFT/TPOT/E2E")
    samples = recomputed.get("metric_samples") or {}
    if (samples.get("ttft") or 0) < 30 or (samples.get("tpot") or 0) < 30:
        sla_reasons.append("TTFT/TPOT 有效样本少于 30")
    if recomputed.get("window_source") != "declared_measurement_window":
        sla_reasons.append("缺少覆盖全部样本的显式测量窗口")
    if recomputed.get("window_coverage") != "verified":
        sla_reasons.append("无法验证测量窗口覆盖全部成功请求（缺到达时间）")
    if (recomputed.get("success_count") or 0) < 30:
        sla_reasons.append("成功请求少于 30")
    qualification = {
        "ingest_accepted": True,
        "sla_evaluable": not sla_reasons,
        "sla_reasons": sla_reasons,
        "calibration_usable": False,
        "calibration_reason": "未提供预测，未执行校准",
    }
    report["qualification"] = qualification

    if prediction is not None:
        evidence_root_path = (
            Path(evidence_root)
            if evidence_root is not None
            else path.parent
        )
        calibration = compare_metrics(
            prediction,
            _calibration_benchmark(
                meta,
                recomputed,
                {
                    "source_file": path.name,
                    "source_sha256": actual_sha,
                },
            ),
            evidence_root=evidence_root_path,
        )
        verified_kind = verification["verified_source_kind"]
        if verified_kind == "synthetic_fixture":
            calibration["calibration_usable"] = False
            calibration["note"] += " synthetic_fixture 只用于管线回归，不进入校准。"
            calibration_status = "fixture_only"
        elif verified_kind != "internal_measurement":
            calibration["calibration_usable"] = False
            calibration["note"] += " 来源未认证，不能作为校准样本。"
            calibration_status = "unknown"
        elif calibration["calibration_usable"]:
            calibration_status = "usable"
        else:
            calibration_status = "unknown"
        calibration["calibration_status"] = calibration_status
        report["calibration"] = calibration
        qualification["calibration_usable"] = calibration_status == "usable"
        qualification["calibration_reason"] = (
            None
            if calibration_status == "usable"
            else calibration.get("note")
        )

    record.raw_metrics["benchmark_client"] = meta.get("benchmark_client")
    record.raw_metrics["server_framework"] = record.framework
    record.raw_metrics["qualification"] = dict(qualification)
    record.quality_flags.extend(
        [
            f"ingest_accepted:{qualification['ingest_accepted']}",
            f"sla_evaluable:{qualification['sla_evaluable']}",
            f"calibration_usable:{qualification['calibration_usable']}",
        ]
    )

    if db_path is not None:
        import tempfile

        from llm_capacity_pricing.services import import_benchmark_file, init_db

        init_db(db_path)
        if metadata_file is not None:
            on_disk = yaml.safe_load(
                Path(metadata_file).read_text(encoding="utf-8")
            ) or {}
            if (
                on_disk.get("frozen_identity_sha256")
                != report["identity_sha256"]
                or (on_disk.get("evidence") or {}).get("source_sha256")
                != actual_sha
            ):
                report["errors"].append(
                    "metadata 文件与本次校验通过的配置/原始日志不一致"
                )
                return report
        handle = tempfile.NamedTemporaryFile(
            "w", suffix=".yaml", delete=False, encoding="utf-8"
        )
        handle.write(yaml.safe_dump(adapter_meta, allow_unicode=True))
        handle.close()
        meta_path = Path(handle.name)
        cleanup = meta_path
        try:
            try:
                database = import_benchmark_file(
                    "sglang", path, meta_path, db_path
                )
            except Exception as exc:  # noqa: BLE001
                report["errors"].append(
                    f"数据库写入失败：{type(exc).__name__}: {exc}"
                )
                return report
            report["database"] = database
            if not database.get("records"):
                report["errors"].append("数据库未写入任何 benchmark record")
                return report
        finally:
            if cleanup is not None:
                cleanup.unlink(missing_ok=True)

    report["accepted"] = True
    return report
