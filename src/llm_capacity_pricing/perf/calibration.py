"""AE5：预测与真实 Benchmark 的精确匹配与误差计算。

只在身份完全匹配时计算误差；不匹配只展示差异，不参与校准。
没有实测值时误差保持 ``None``，绝不当作 0。
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Mapping

_IDENTITY = (
    ("model_id", "模型"),
    ("model_revision", "模型 revision"),
    ("weight_precision", "精度"),
    ("kv_precision", "KV 精度"),
    ("hardware_id", "GPU SKU"),
    ("gpu_count", "GPU 数量"),
    ("framework", "框架"),
    ("framework_version", "框架版本"),
    ("tp_size", "TP"),
    ("dp_size", "DP"),
    ("pp_size", "PP"),
    ("input_len", "ISL"),
    ("output_len", "OSL"),
    ("arrival_mode", "到达模式"),
    ("dataset", "数据集"),
)


def match_identity(
    prediction: Mapping[str, Any], benchmark: Mapping[str, Any]
) -> tuple[bool, list[str]]:
    diffs: list[str] = []
    for field, label in _IDENTITY:
        want = prediction.get(field)
        got = benchmark.get(field)
        if want is None or got is None:
            diffs.append(f"{label}有一侧未声明")
        elif str(want) != str(got):
            diffs.append(f"{label}不同：预测 {want}，实测 {got}")
    mode = prediction.get("arrival_mode")
    supported = {"fixed_concurrency", "poisson", "constant_rate", "trace"}
    if mode not in supported:
        diffs.append(f"到达模式未支持或未知：{mode}")
    elif mode == "fixed_concurrency":
        if prediction.get("concurrency") is None or benchmark.get("concurrency") is None:
            diffs.append("固定并发有一侧未声明")
        elif prediction.get("concurrency") != benchmark.get("concurrency"):
            diffs.append(
                f"并发不同：预测 {prediction.get('concurrency')}，"
                f"实测 {benchmark.get('concurrency')}"
            )
    elif mode in ("poisson", "constant_rate"):
        if (
            prediction.get("request_rate_rps") is None
            or benchmark.get("request_rate_rps") is None
        ):
            diffs.append("开放到达率有一侧未声明")
        else:
            try:
                pred_rate = Decimal(str(prediction.get("request_rate_rps")))
                bench_rate = Decimal(str(benchmark.get("request_rate_rps")))
            except Exception:  # noqa: BLE001
                diffs.append("到达率不是有效数字")
            else:
                if pred_rate != bench_rate:
                    diffs.append(
                        f"到达率不同：预测 {prediction.get('request_rate_rps')}，"
                        f"实测 {benchmark.get('request_rate_rps')}"
                    )
    if prediction.get("arrival_mode") == "trace":
        pred_trace = prediction.get("trace_id")
        bench_trace = benchmark.get("trace_id")
        if not pred_trace or not bench_trace:
            diffs.append("trace 负载缺少 trace_id，不能确认同一轨迹")
        elif str(pred_trace) != str(bench_trace):
            diffs.append(f"trace 标识不同：预测 {pred_trace}，实测 {bench_trace}")
    return not diffs, diffs


def _error(predicted: Any, measured: Any) -> tuple[Decimal | None, Decimal | None]:
    if predicted is None or measured is None:
        return None, None
    try:
        p = Decimal(str(predicted))
        m = Decimal(str(measured))
    except Exception:  # noqa: BLE001
        return None, None
    if m == 0:
        return None, None
    return p - m, (p - m) / m


def compare_metrics(
    prediction: Mapping[str, Any], benchmark: Mapping[str, Any]
) -> dict[str, Any]:
    matched, reasons = match_identity(prediction, benchmark)
    result: dict[str, Any] = {
        "matched": matched,
        "identity_differences": reasons,
        "calibration_usable": False,
        "errors": {},
        "note": (
            "身份完全匹配才计算误差；不匹配只展示差异，不参与校准训练。"
        ),
    }
    if not matched:
        return result
    metric_pairs = (
        ("ttft_p99_ms", "p99_ttft_ms"),
        ("tpot_p99_ms", "p99_tpot_ms"),
        ("output_throughput_tok_s", "output_throughput_tok_s"),
    )
    usable = True
    for label, key in metric_pairs:
        predicted = prediction.get(key)
        measured = benchmark.get(key)
        absolute, relative = _error(predicted, measured)
        result["errors"][label] = {
            "predicted": predicted,
            "measured": measured,
            "absolute": str(absolute) if absolute is not None else None,
            "relative": str(relative) if relative is not None else None,
        }
        if absolute is None:
            usable = False
    evidence = benchmark.get("evidence") or {}
    has_provenance = bool(
        evidence.get("source_file") and evidence.get("source_sha256")
    )
    if not has_provenance:
        usable = False
        result["note"] += " 实测记录缺少原始文件/SHA256，不能作为校准样本。"
    result["calibration_usable"] = usable
    if not usable:
        result["note"] += " 缺少实测值或预测值，误差保持 unknown。"
    return result
