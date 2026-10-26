"""AE5/AF4：预测与真实 Benchmark 的精确匹配与误差计算。

只在身份完全匹配、原始证据可核验、指标口径一致时计算误差；
否则只展示差异，不参与校准。没有实测值时误差保持 ``None``，绝不当作 0。

证据契约（AF4）：

* ``benchmark["evidence"]["source_file"]`` 必须是真实存在的文件；
  相对路径按 ``evidence_root`` 解析（默认当前工作目录）。
* ``source_sha256`` 必须与实际文件内容哈希一致，否则不得校准。
* 两侧都要在 ``metric_definitions`` 中声明本模块使用的三个指标
  （``ttft_p99_ms`` / ``tpot_p99_ms`` / ``output_throughput_tok_s``），
  且口径 ID 必须与 ``metric_semantics.FIELD_DEFINITIONS`` 一致、彼此相同。
"""

from __future__ import annotations

import hashlib
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping

from llm_capacity_pricing.engines.metric_semantics import FIELD_DEFINITIONS

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


def _metric_definition_errors(
    prediction: Mapping[str, Any],
    benchmark: Mapping[str, Any],
    metric_keys: tuple[str, ...],
) -> list[str]:
    """三个指标的口径必须两侧都声明、且与标准字典一致。"""
    errors: list[str] = []
    pred_defs = prediction.get("metric_definitions") or {}
    bench_defs = benchmark.get("metric_definitions") or {}
    for key in metric_keys:
        expected = FIELD_DEFINITIONS[key].definition_id
        pred_id = pred_defs.get(key)
        bench_id = bench_defs.get(key)
        if not pred_id or not bench_id:
            errors.append(f"{key} 口径有一侧未声明")
            continue
        if pred_id != expected:
            errors.append(f"预测 {key} 口径不是标准定义 {expected}")
        if bench_id != expected:
            errors.append(f"实测 {key} 口径不是标准定义 {expected}")
        if pred_id != bench_id:
            errors.append(f"{key} 口径不同：预测 {pred_id}，实测 {bench_id}")
    return errors


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _verify_provenance(
    evidence: Mapping[str, Any], evidence_root: Path
) -> dict[str, Any]:
    """核验声明文件存在且内容哈希与声明一致；任何一步失败都 fail-closed。"""
    source_file = evidence.get("source_file")
    declared = evidence.get("source_sha256")
    result: dict[str, Any] = {
        "source_file": source_file,
        "declared_sha256": declared,
        "actual_sha256": None,
        "verified": False,
        "note": None,
    }
    if not source_file or not declared:
        result["note"] = "实测记录缺少原始文件/SHA256，不能作为校准样本。"
        return result
    path = Path(str(source_file))
    if not path.is_absolute():
        path = evidence_root / path
    try:
        path = path.resolve()
        if not path.is_file():
            result["note"] = f"声明的原始文件不存在：{path}"
            return result
        actual = _sha256_file(path)
    except OSError as exc:
        result["note"] = f"无法读取声明的原始文件：{path}（{exc}）"
        return result
    result["actual_sha256"] = actual
    if actual.lower() != str(declared).strip().lower():
        result["note"] = (
            f"原始文件哈希不一致（声明 {declared}，实际 {actual}），"
            "不能作为校准样本。"
        )
        return result
    result["verified"] = True
    return result


def compare_metrics(
    prediction: Mapping[str, Any],
    benchmark: Mapping[str, Any],
    *,
    evidence_root: Path | str | None = None,
) -> dict[str, Any]:
    matched, reasons = match_identity(prediction, benchmark)
    result: dict[str, Any] = {
        "matched": matched,
        "identity_differences": reasons,
        "calibration_usable": False,
        "metric_definition_errors": [],
        "evidence": {},
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
    metric_keys = tuple(label for label, _ in metric_pairs)
    definition_errors = _metric_definition_errors(
        prediction, benchmark, metric_keys
    )
    result["metric_definition_errors"] = definition_errors
    root = Path(evidence_root) if evidence_root is not None else Path.cwd()
    provenance = _verify_provenance(benchmark.get("evidence") or {}, root)
    result["evidence"] = provenance
    evidence_ok = not definition_errors and provenance["verified"]
    usable = evidence_ok
    for label, key in metric_pairs:
        predicted = prediction.get(key)
        measured = benchmark.get(key)
        # 口径或原始证据未通过核验时，不能发布数值误差；只保留两侧取值供排查。
        absolute, relative = (
            _error(predicted, measured) if evidence_ok else (None, None)
        )
        result["errors"][label] = {
            "predicted": predicted,
            "measured": measured,
            "absolute": str(absolute) if absolute is not None else None,
            "relative": str(relative) if relative is not None else None,
        }
        if absolute is None:
            usable = False
    if definition_errors:
        result["note"] += " 指标口径未对齐，不能作为校准样本。"
    if not provenance["verified"]:
        result["note"] += f" {provenance['note']}"
    result["calibration_usable"] = usable
    if not usable and evidence_ok:
        result["note"] += " 缺少实测值或预测值，误差保持 unknown。"
    elif not usable:
        result["note"] += " 证据未核验，误差保持 unknown。"
    return result
