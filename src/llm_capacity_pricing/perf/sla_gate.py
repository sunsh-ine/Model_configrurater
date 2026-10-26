"""AF0：统一的 P99 SLA 门禁。

主推荐（assess_verdict）、容量扫描和到达率扫描都必须调用这里，
不能各自用阈值比较一遍。
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal, Sequence

MIN_SAMPLES_FOR_P99 = 30


@dataclass(frozen=True)
class P99GateResult:
    verdict: Literal["predicted_pass", "predicted_fail", "insufficient_prediction"]
    reasons: list[str]


def _dec(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        parsed = Decimal(str(value))
    except Exception:  # noqa: BLE001
        return None
    return parsed if parsed.is_finite() else None


def evaluate_p99_gate(
    *,
    ttft_p99_ms: Any,
    tpot_p99_ms: Any,
    ttft_samples: Any,
    tpot_samples: Any,
    ttft_limit_ms: Any,
    tpot_limit_ms: Any,
    min_samples: int = MIN_SAMPLES_FOR_P99,
) -> P99GateResult:
    ttft_limit = _dec(ttft_limit_ms)
    tpot_limit = _dec(tpot_limit_ms)
    if ttft_limit is None and tpot_limit is None:
        return P99GateResult(
            "insufficient_prediction",
            ["未给出任何 P99 阈值：没有判据，不给模拟结论"],
        )

    ttft = _dec(ttft_p99_ms)
    tpot = _dec(tpot_p99_ms)
    reasons: list[str] = []
    missing: list[str] = []
    if ttft_limit is not None and ttft is None:
        missing.append("TTFT P99")
    if tpot_limit is not None and tpot is None:
        missing.append("TPOT P99")
    if missing:
        return P99GateResult(
            "insufficient_prediction", ["缺少预测值：" + "、".join(missing)]
        )

    sample_issues: list[str] = []
    for label, samples, needed in (
        ("TTFT", ttft_samples, ttft_limit is not None),
        ("TPOT", tpot_samples, tpot_limit is not None),
    ):
        if not needed:
            continue
        if samples is None:
            sample_issues.append(f"{label} 样本数未给出")
            continue
        try:
            count = int(samples)
        except (TypeError, ValueError):
            sample_issues.append(f"{label} 样本数无效：{samples}")
            continue
        if count < min_samples:
            sample_issues.append(f"{label} 样本数仅 {count}（< {min_samples}）")
    if sample_issues:
        return P99GateResult(
            "insufficient_prediction",
            ["样本不足，P99 不可信：" + "、".join(sample_issues)],
        )

    failures: list[str] = []
    if ttft_limit is not None and ttft > ttft_limit:
        failures.append(f"TTFT P99 {ttft}ms > 阈值 {ttft_limit}ms")
    if tpot_limit is not None and tpot > tpot_limit:
        failures.append(f"TPOT P99 {tpot}ms > 阈值 {tpot_limit}ms")
    if failures:
        return P99GateResult("predicted_fail", failures)
    return P99GateResult("predicted_pass", reasons)


def aggregate_run_verdicts(verdicts: Sequence[str]) -> tuple[str, int]:
    """AF1/AF0：同一工作点多 seed/多归档的聚合状态。

    返回 ``(status, pass_count)``；status 可能是 ``predicted_pass`` /
    ``predicted_fail`` / ``mixed`` / ``unknown``。只要有任何运行缺 P99，
    整点就是 ``unknown`` —— 不能挑一个通过的 seed 代表工作点。
    """
    if not verdicts:
        return "unknown", 0
    if any(v not in ("predicted_pass", "predicted_fail") for v in verdicts):
        return "unknown", sum(1 for v in verdicts if v == "predicted_pass")
    passed = sum(1 for v in verdicts if v == "predicted_pass")
    if passed == len(verdicts):
        return "predicted_pass", passed
    if passed == 0:
        return "predicted_fail", 0
    return "mixed", passed
