"""AJ1：真实容量点与 SLA Goodput 的统一结构。

容量结论分三类，不能混写：

* 最大输出吞吐；
* 最大 SLA Goodput（逐请求 TTFT/TPOT 与成功条件）；
* 有内部裕量且可验证的容量。

固定并发不等于 Poisson 稳态容量；有限窗口不等于长期稳定。
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field

from llm_capacity_pricing.importers.common import dec, percentile_nearest_rank


class CapacityPoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: str
    input_len: int
    output_len: int
    concurrency: int | None = None
    request_rate_rps: Decimal | None = None
    seed: int | None = None
    window_s: Decimal | None = None

    arrival_rps: Decimal | None = None
    completed_rps: Decimal | None = None
    output_tps: Decimal | None = None
    ttft_p99_ms: Decimal | None = None
    tpot_p99_ms: Decimal | None = None
    e2e_p99_ms: Decimal | None = None
    success_rate: Decimal | None = None
    sla_goodput_rps: Decimal | None = None
    #: 所有成功请求都能计算 TTFT/TPOT 时才为 True；否则 goodput 视为未知
    sla_goodput_complete: bool = True

    ttft_limit_ms: Decimal | None = None
    tpot_limit_ms: Decimal | None = None
    ttft_margin_pct: Decimal | None = None
    tpot_margin_pct: Decimal | None = None

    goodput_definition: str = (
        "逐请求：success 且 TTFT<=limit 且 TPOT=(e2e-ttft)/(out-1)<=limit 的"
        "请求数 / 测量窗口"
    )
    steady_state_status: str = "unknown"
    steady_state_evidence: str = "insufficient"
    queue_status: str = "unobserved"
    capacity_evidence_ids: list[str] = Field(default_factory=list)
    errors: int = 0
    notes: list[str] = Field(default_factory=list)


def _p99(values: Sequence[Decimal]) -> Decimal | None:
    return percentile_nearest_rank(list(values), 99)


def _row_tpot(row: Mapping[str, Any]) -> Decimal | None:
    ttft = dec(row.get("ttft_ms", row.get("ttft")))
    latency = dec(row.get("latency_ms", row.get("latency", row.get("e2e_latency_ms"))))
    out_len = dec(row.get("output_length", row.get("output_len")))
    if ttft is None or latency is None or out_len is None or out_len <= 1:
        return None
    if latency < ttft:
        return None
    return (latency - ttft) / (out_len - 1)


def _window(values: Sequence[Decimal]) -> Decimal | None:
    vals = [v for v in values if v is not None]
    if not vals:
        return None
    return max(vals) - min(vals)


def build_capacity_point(
    rows: Sequence[Mapping[str, Any]],
    *,
    candidate_id: str,
    input_len: int,
    output_len: int,
    window_s: Decimal | None,
    ttft_limit_ms: Decimal,
    tpot_limit_ms: Decimal,
    concurrency: int | None = None,
    request_rate_rps: Decimal | None = None,
    seed: int | None = None,
    capacity_evidence_ids: Sequence[str] = (),
) -> CapacityPoint:
    success = [row for row in rows if row.get("success") is True]
    ttfts = [dec(row.get("ttft_ms", row.get("ttft"))) for row in success]
    e2es = [
        dec(row.get("latency_ms", row.get("latency", row.get("e2e_latency_ms"))))
        for row in success
    ]
    tpots = [_row_tpot(row) for row in success]
    valid_ttfts = [v for v in ttfts if v is not None]
    valid_e2es = [v for v in e2es if v is not None]
    valid_tpots = [v for v in tpots if v is not None]

    total = len(rows)
    success_rate = Decimal(len(success)) / Decimal(total) if total else None
    good_count = 0
    unevaluable = 0
    for row in success:
        ttft = dec(row.get("ttft_ms", row.get("ttft")))
        tpot = _row_tpot(row)
        if ttft is None or tpot is None:
            unevaluable += 1
            continue
        if ttft <= ttft_limit_ms and tpot <= tpot_limit_ms:
            good_count += 1
    goodput_complete = unevaluable == 0

    duration = window_s if window_s and window_s > 0 else None
    output_values = [
        dec(row.get("output_length", row.get("output_len"))) for row in success
    ]
    output_tokens = (
        sum(output_values)
        if all(value is not None for value in output_values)
        else None
    )
    arrivals = [dec(row.get("arrival_ms", row.get("arrival_time_ms"))) for row in rows]
    valid_arrivals = [v for v in arrivals if v is not None]
    arrival_rps = None
    # 必须每个请求都有到达时间，才能把“到达率”当作测量结果
    if duration and len(valid_arrivals) == len(rows) and rows:
        arrival_rps = Decimal(len(rows)) / duration

    ttft_p99 = _p99(valid_ttfts)
    tpot_p99 = _p99(valid_tpots)
    e2e_p99 = _p99(valid_e2es)
    point = CapacityPoint(
        candidate_id=candidate_id,
        input_len=input_len,
        output_len=output_len,
        concurrency=concurrency,
        request_rate_rps=request_rate_rps,
        seed=seed,
        window_s=duration,
        arrival_rps=arrival_rps,
        completed_rps=(Decimal(len(success)) / duration if duration else None),
        output_tps=(
            output_tokens / duration
            if duration and output_tokens is not None
            else None
        ),
        ttft_p99_ms=ttft_p99,
        tpot_p99_ms=tpot_p99,
        e2e_p99_ms=e2e_p99,
        success_rate=success_rate,
        sla_goodput_rps=(
            Decimal(good_count) / duration
            if duration and goodput_complete
            else None
        ),
        sla_goodput_complete=goodput_complete,
        ttft_limit_ms=ttft_limit_ms,
        tpot_limit_ms=tpot_limit_ms,
        ttft_margin_pct=(
            (ttft_limit_ms - ttft_p99) / ttft_limit_ms * 100
            if ttft_p99 is not None
            else None
        ),
        tpot_margin_pct=(
            (tpot_limit_ms - tpot_p99) / tpot_limit_ms * 100
            if tpot_p99 is not None
            else None
        ),
        capacity_evidence_ids=list(capacity_evidence_ids),
        errors=total - len(success),
    )

    # 有限窗口稳态：只给窗口内描述，不宣称长期稳定。
    if len(success) != len(rows):
        point.steady_state_status = "unstable"
        point.steady_state_evidence = "failed_requests_present"
    elif len(rows) < 100 or not duration or duration < Decimal("300"):
        point.steady_state_status = "unknown"
        point.steady_state_evidence = "insufficient_window"
    else:
        arrivals_all = [
            dec(r.get("arrival_ms", r.get("arrival_time_ms"))) for r in rows
        ]
        if any(value is None for value in arrivals_all):
            point.steady_state_status = "unknown"
            point.steady_state_evidence = "insufficient_arrival_order"
            return point
        ordered = [
            row
            for _, row in sorted(
                zip(arrivals_all, rows), key=lambda item: item[0]
            )
        ]
        half = len(ordered) // 2
        first = ordered[:half]
        second = ordered[half:]
        first_ttft = _p99(
            [v for v in (dec(r.get("ttft_ms", r.get("ttft"))) for r in first) if v is not None]
        )
        second_ttft = _p99(
            [v for v in (dec(r.get("ttft_ms", r.get("ttft"))) for r in second) if v is not None]
        )
        if first_ttft is None or second_ttft is None:
            point.steady_state_status = "unknown"
            point.steady_state_evidence = "insufficient_half_samples"
        elif second_ttft > first_ttft * Decimal("1.5"):
            point.steady_state_status = "unstable"
            point.steady_state_evidence = "second_half_ttft_p99_gt_1.5x"
        else:
            point.steady_state_status = "observed_stable_in_window"
            point.steady_state_evidence = "windowed_half_comparison_only"
    return point
