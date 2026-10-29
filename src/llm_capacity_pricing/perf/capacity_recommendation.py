"""AJ6：从真实容量点生成可解释的容量推荐。

固定并发与开放到达是两个独立结论；不把固定并发换算成 Poisson 稳态容量。
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Iterable, Mapping


def _d(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except Exception:  # noqa: BLE001
        return None


def build_capacity_evidence(
    points: Iterable[Mapping[str, Any]],
    *,
    ttft_limit_ms: Decimal | None = None,
    tpot_limit_ms: Decimal | None = None,
    min_repeats: int = 5,
    require_evidence: bool = True,
) -> dict[str, Any]:
    # 一次性可迭代对象（生成器）必须先物化，否则固定并发分支会把它耗尽
    points = list(points)
    fixed = [p for p in points if p.get("concurrency") is not None and p.get("request_rate_rps") is None]
    open_arrival = [p for p in points if p.get("request_rate_rps") is not None]

    def good(p: Mapping[str, Any]) -> bool:
        ttft = _d(p.get("ttft_p99_ms"))
        tpot = _d(p.get("tpot_p99_ms"))
        success = _d(p.get("success_rate"))
        tl = (
            ttft_limit_ms
            if ttft_limit_ms is not None
            else _d(p.get("ttft_limit_ms"))
        )
        pl = (
            tpot_limit_ms
            if tpot_limit_ms is not None
            else _d(p.get("tpot_limit_ms"))
        )
        if require_evidence and not p.get("capacity_evidence_ids"):
            return False
        if (
            p.get("ingest_accepted") is not True
            or p.get("sla_evaluable") is not True
        ):
            return False
        return (
            ttft is not None
            and tpot is not None
            and success == 1
            and tl is not None
            and pl is not None
            and ttft <= tl
            and tpot <= pl
        )

    def repeated(group: list[Mapping[str, Any]]) -> bool:
        seeds = [p.get("seed") for p in group]
        return (
            len(group) >= min_repeats
            and all(seed is not None for seed in seeds)
            and len(set(seeds)) == len(group)
        )

    def worst_run(group: list[Mapping[str, Any]], key: str) -> Decimal | None:
        vals = [_d(p.get(key)) for p in group if _d(p.get(key)) is not None]
        return max(vals) if vals else None

    def _workload(point: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "input_len": point.get("input_len"),
            "output_len": point.get("output_len"),
        }

    by_c: dict[tuple[Any, ...], list[Mapping[str, Any]]] = {}
    for point in fixed:
        key = (
            point.get("deployment_id"),
            point.get("runtime_config_sha256"),
            point.get("input_len"),
            point.get("output_len"),
            int(point["concurrency"]),
        )
        by_c.setdefault(key, []).append(point)
    fixed_summary = []
    for (deployment_id, runtime_sha, isl, osl, concurrency), group in sorted(
        by_c.items(), key=lambda item: tuple(str(v) for v in item[0])
    ):
        worst_tpot = worst_run(group, "tpot_p99_ms")
        all_pass = repeated(group) and all(good(p) for p in group)
        fixed_summary.append(
            {
                "deployment_id": deployment_id,
                "runtime_config_sha256": runtime_sha,
                "input_len": isl,
                "output_len": osl,
                "concurrency": concurrency,
                "runs": len(group),
                "all_pass": all_pass,
                "worst_ttft_p99_ms": worst_run(group, "ttft_p99_ms"),
                "worst_tpot_p99_ms": worst_tpot,
                "max_output_tps": max(
                    (
                        value
                        for value in (_d(p.get("output_tps")) for p in group)
                        if value is not None
                    ),
                    default=None,
                ),
                "max_sla_goodput_rps": max(
                    (
                        value
                        for value in (_d(p.get("sla_goodput_rps")) for p in group)
                        if value is not None
                    ),
                    default=None,
                ),
                "margin_10pct": (
                    worst_tpot is not None
                    and tpot_limit_ms is not None
                    and worst_tpot <= tpot_limit_ms * Decimal("0.9")
                ),
                "margin_5pct": (
                    worst_tpot is not None
                    and tpot_limit_ms is not None
                    and worst_tpot <= tpot_limit_ms * Decimal("0.95")
                ),
                "steady_state": sorted({p.get("steady_state_status") for p in group}),
            }
        )

    passing = [s for s in fixed_summary if s["all_pass"]]
    max_fixed_entry = max(
        passing, key=lambda s: s["concurrency"], default=None
    )
    max_fixed = max_fixed_entry["concurrency"] if max_fixed_entry else None
    max_fixed_workload = _workload(max_fixed_entry) if max_fixed_entry else None
    max_verified_by_workload: dict[str, int] = {}
    for entry in passing:
        label = f"{entry['input_len']}/{entry['output_len']}"
        max_verified_by_workload[label] = max(
            max_verified_by_workload.get(label, 0), entry["concurrency"]
        )
    margin10 = [
        s for s in passing if s["margin_10pct"]
    ]
    margin5 = [s for s in passing if s["margin_5pct"]]
    margin10_concurrencies = [s["concurrency"] for s in margin10]
    margin5_concurrencies = [s["concurrency"] for s in margin5]

    open_summary = []
    for point in sorted(
        open_arrival,
        key=lambda p: (
            str(p.get("input_len")),
            str(p.get("output_len")),
            str(_d(p.get("request_rate_rps"))),
        ),
    ):
        open_summary.append(
            {
                "deployment_id": point.get("deployment_id"),
                "runtime_config_sha256": point.get("runtime_config_sha256"),
                "input_len": point.get("input_len"),
                "output_len": point.get("output_len"),
                "request_rate_rps": _d(point.get("request_rate_rps")),
                "arrival_rps": _d(point.get("arrival_rps")),
                "completed_rps": _d(point.get("completed_rps")),
                "success_rate": _d(point.get("success_rate")),
                "worst_ttft_p99_ms": _d(point.get("ttft_p99_ms")),
                "worst_tpot_p99_ms": _d(point.get("tpot_p99_ms")),
                "output_tps": _d(point.get("output_tps")),
                "steady_state_status": point.get("steady_state_status"),
                "queue_status": point.get("queue_status"),
                "seed": point.get("seed"),
                "sla_pass": good(point),
                "evidence_id": (point.get("capacity_evidence_ids") or [None])[0],
            }
        )
    open_groups: dict[tuple[Any, ...], list[Mapping[str, Any]]] = {}
    for item in open_summary:
        key = (
            item["deployment_id"],
            item["runtime_config_sha256"],
            item["input_len"],
            item["output_len"],
            item["request_rate_rps"],
        )
        open_groups.setdefault(key, []).append(item)
    steady_open = [
        items[0]
        for items in open_groups.values()
        if repeated(items)
        and all(
            item["steady_state_status"] == "observed_stable_in_window"
            and item["success_rate"] == 1
            and item["sla_pass"]
            and item["request_rate_rps"] is not None
            and item["arrival_rps"] is not None
            and item["completed_rps"] is not None
            # 实际到达/完成必须支撑计划到达率，否则只是“客户端没打满”
            and item["arrival_rps"]
            >= item["request_rate_rps"] * Decimal("0.95")
            and item["completed_rps"]
            >= item["request_rate_rps"] * Decimal("0.95")
            for item in items
        )
    ]
    best_open = max(
        steady_open,
        key=lambda item: item["request_rate_rps"] or Decimal(0),
        default=None,
    )
    max_open = best_open["request_rate_rps"] if best_open else None
    max_open_workload = _workload(best_open) if best_open else None
    max_open_by_workload: dict[str, Decimal] = {}
    for item in steady_open:
        label = f"{item['input_len']}/{item['output_len']}"
        rate = item["request_rate_rps"]
        if rate is not None:
            max_open_by_workload[label] = max(
                max_open_by_workload.get(label, Decimal(0)), rate
            )

    max_throughput = max(
        (
            _d(p.get("output_tps"))
            for p in list(fixed) + list(open_arrival)
            if _d(p.get("output_tps")) is not None
        ),
        default=None,
    )
    max_goodput = max(
        (
            _d(p.get("sla_goodput_rps"))
            for p in list(fixed) + list(open_arrival)
            if _d(p.get("sla_goodput_rps")) is not None
        ),
        default=None,
    )

    recommended_mode = None
    recommended_value = None
    recommended_workload = None
    reasons: list[str] = []
    chosen: Mapping[str, Any] | None = None
    if margin10:
        chosen = max(margin10, key=lambda s: s["concurrency"])
        recommended_mode = "fixed_concurrency"
        recommended_value = chosen["concurrency"]
        recommended_workload = _workload(chosen)
        reasons.append("存在同时满足 SLA 与 10% 内部裕量的固定并发")
    elif margin5:
        chosen = max(margin5, key=lambda s: s["concurrency"])
        recommended_mode = "fixed_concurrency"
        recommended_value = chosen["concurrency"]
        recommended_workload = _workload(chosen)
        reasons.append("存在同时满足 SLA 与 5% 内部裕量的固定并发")
    elif max_fixed_entry is not None:
        chosen = max_fixed_entry
        recommended_mode = "fixed_concurrency"
        recommended_value = max_fixed
        recommended_workload = max_fixed_workload
        reasons.append("存在 SLA 达标固定并发，但无内部裕量")
    elif best_open is not None:
        chosen = best_open
        recommended_mode = "open_arrival"
        recommended_value = max_open
        recommended_workload = max_open_workload
        reasons.append("固定并发无正式达标证据；使用有窗口证据的 Poisson 到达率")

    return {
        "schema_version": "1.0",
        "ttft_limit_ms": str(ttft_limit_ms) if ttft_limit_ms is not None else None,
        "tpot_limit_ms": str(tpot_limit_ms) if tpot_limit_ms is not None else None,
        "fixed_concurrency": fixed_summary,
        "open_arrival": open_summary,
        "max_output_tps": max_throughput,
        "max_sla_goodput_rps": max_goodput,
        "max_verified_fixed_concurrency": max_fixed,
        "max_verified_workload": max_fixed_workload,
        "max_verified_by_workload": max_verified_by_workload,
        "margin_10pct_concurrencies": margin10_concurrencies,
        "margin_5pct_concurrencies": margin5_concurrencies,
        "margin_10pct_candidates": margin10,
        "margin_5pct_candidates": margin5,
        "max_steady_open_rate_rps": max_open,
        "max_steady_open_workload": max_open_workload,
        "max_steady_open_by_workload": {
            key: str(value) for key, value in max_open_by_workload.items()
        },
        "recommended_mode": recommended_mode,
        "recommended_value": recommended_value,
        "recommended_workload": recommended_workload,
        "reasons": reasons,
        "limits": [
            "固定并发容量不等于 Poisson 稳态容量，不能互相换算。",
            "最大吞吐不等于 SLA 安全容量；探索点不作为正式推荐。",
            "没有全局队列时间序列时，Poisson 稳态最多是窗口内观察。",
        ],
    }
