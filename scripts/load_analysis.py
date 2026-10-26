#!/usr/bin/env python3
"""AG3：有限窗口负载/队列证据分析。

只使用逐请求原始字段；没有的队列指标保持 unknown，不默认 0，
也不把路由 queue_wait 解释成模型 scheduler 等待。
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
CASE_ROOT = ROOT / "data/simulation/capabilities/per_request"
CASES = (
    ("8b_h100_dp2_r5_seed3", "qwen3-8b-h100-tp1dp2-r5-seed3"),
    ("8b_h200_dp2_r5_seed1", "qwen3-8b-h200-tp1dp2-r5-seed1"),
)


def fmt(value: float | None, digits: int = 2) -> str:
    return "unknown" if value is None else f"{value:.{digits}f}"


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(q / 100 * len(ordered)) - 1)
    return ordered[index]


def load_case(directory: Path) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    prediction = json.loads((directory / "prediction.json").read_text(encoding="utf-8"))
    predict = yaml.safe_load((directory / "predict.yaml").read_text(encoding="utf-8")) or {}
    requests = [
        json.loads(line)
        for line in (directory / "requests.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    requests.sort(key=lambda item: float(item.get("arrival_time_ms") or 0.0))
    return prediction, predict, requests


def _metrics(requests: list[dict[str, Any]]) -> dict[str, Any]:
    ttfts = [float(r["ttft_ms"]) for r in requests if r.get("ttft_ms") is not None]
    e2es = [
        float(r["e2e_latency_ms"])
        for r in requests
        if r.get("e2e_latency_ms") is not None
    ]
    tpots: list[float] = []
    queue_waits: list[float] = []
    queue_missing = 0
    output_tokens = 0.0
    statuses: dict[str, int] = {}
    arrivals = [
        float(r["arrival_time_ms"])
        for r in requests
        if r.get("arrival_time_ms") is not None
    ]
    last_tokens = [
        float(r["last_token_ms"])
        for r in requests
        if r.get("last_token_ms") is not None
    ]
    for request in requests:
        output_len = int(request.get("output_length") or 0)
        output_tokens += output_len
        first = request.get("first_token_ms")
        last = request.get("last_token_ms")
        if output_len > 1 and first is not None and last is not None:
            tpots.append((float(last) - float(first)) / (output_len - 1))
        waits = [
            float(item["queue_wait_ms"])
            for item in request.get("routing_history") or []
            if item.get("queue_wait_ms") is not None
        ]
        if waits:
            queue_waits.append(max(waits))
        else:
            queue_missing += 1
        status = str(request.get("terminal_status") or "unknown")
        statuses[status] = statuses.get(status, 0) + 1
    timed_complete = all(
        request.get("arrival_time_ms") is not None
        and request.get("last_token_ms") is not None
        for request in requests
        if int(request.get("output_length") or 0) > 0
    )
    duration_s = (
        (max(last_tokens) - min(arrivals)) / 1000.0
        if arrivals and last_tokens and timed_complete
        else None
    )
    return {
        "requests": len(requests),
        "terminal_status": statuses,
        "ttft_p50_ms": percentile(ttfts, 50),
        "ttft_p95_ms": percentile(ttfts, 95),
        "ttft_p99_ms": percentile(ttfts, 99),
        "tpot_p99_ms": percentile(tpots, 99),
        "e2e_p99_ms": percentile(e2es, 99),
        "queue_wait_p99_ms": percentile(queue_waits, 99),
        "queue_wait_max_ms": max(queue_waits) if queue_waits else None,
        "queue_wait_available_requests": len(queue_waits),
        "queue_wait_missing_requests": queue_missing,
        "output_tokens": output_tokens,
        "window_duration_s": duration_s,
        "output_throughput_tok_s": (
            output_tokens / duration_s if duration_s and duration_s > 0 else None
        ),
        "timed_window_complete": timed_complete,
        "actual_request_rate_rps": (
            len(requests) / duration_s if duration_s and duration_s > 0 else None
        ),
    }


def analyse(name: str, directory: Path) -> dict[str, Any]:
    prediction, predict, requests = load_case(directory)
    traffic = (predict.get("traffic") or {})
    load = traffic.get("load") or {}
    configured_rate = load.get("requests_per_second")
    windows = {
        "full": requests,
        "first_half": requests[: len(requests) // 2],
        "second_half": requests[len(requests) // 2 :],
        "first_100": requests[:100],
        "last_100": requests[-100:],
    }
    return {
        "case_id": name,
        "prediction_completed_requests": prediction.get("completed_requests"),
        "configured_arrival_rate_rps": configured_rate,
        "load_type": load.get("type"),
        "windows": {key: _metrics(value) for key, value in windows.items()},
    }


def main() -> int:
    cases = [analyse(name, CASE_ROOT / directory) for name, directory in CASES]
    doc = {
        "schema_version": "1.0",
        "tool": "aisimulate",
        "tool_version": "0.12.1",
        "capture": "per_request",
        "cases": cases,
        "sources": [
            {
                "metric": "TTFT",
                "field": "ttft_ms",
                "definition": "请求到首 token（工具逐请求字段）",
                "status": "available",
            },
            {
                "metric": "TPOT",
                "field": "(last_token_ms - first_token_ms)/(output_length-1)",
                "definition": "逐请求推导；不是 ITL 池",
                "status": "derived",
            },
            {
                "metric": "E2E",
                "field": "e2e_latency_ms",
                "definition": "请求到末 token",
                "status": "available",
            },
            {
                "metric": "路由 queue_wait",
                "field": "routing_history[].queue_wait_ms",
                "definition": "路由层等待，不等于模型 scheduler queue time",
                "status": "available_per_request",
            },
            {
                "metric": "模型 scheduler 等待",
                "field": None,
                "definition": "工具未提供",
                "status": "unknown",
            },
            {
                "metric": "队列长度/等待请求数时间序列",
                "field": None,
                "definition": "工具未提供全局队列采样",
                "status": "unknown",
            },
        ],
        "note": (
            "有限请求窗口；分段变化只作描述，不证明长期稳定，"
            "不据此推导最大稳定 RPS。"
        ),
    }
    json_path = ROOT / "docs/ag_load_analysis.json"
    md_path = ROOT / "docs/ag_load_analysis.md"
    json_path.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    lines = [
        "# AG3 有限窗口负载与队列分析",
        "",
        "> 逐请求数据来自 AISimulate `--capture-per-request`；"
        "缺失队列字段保持 unknown，不默认 0。",
        "",
        "## 指标来源",
        "",
        "| 指标 | 字段/算法 | 口径 | 状态 |",
        "|---|---|---|---|",
    ]
    for source in doc["sources"]:
        lines.append(
            f"| {source['metric']} | `{source['field'] or '—'}` "
            f"| {source['definition']} | {source['status']} |"
        )
    for case in cases:
        lines.extend(
            [
                "",
                f"## {case['case_id']}",
                "",
                f"- 配置到达率：{case['configured_arrival_rate_rps']} req/s"
                f"（{case['load_type']}）",
                f"- 完成请求：{case['prediction_completed_requests']}",
                "",
                "| 窗口 | 请求 | TTFT P99 | TPOT P99 | E2E P99 | 队列等待 P99 | "
                "实际 RPS | 输出 tok/s |",
                "|---|---|---|---|---|---|---|---|",
            ]
        )
        for window, metrics in case["windows"].items():
            lines.append(
                f"| {window} | {metrics['requests']} | "
                f"{fmt(metrics['ttft_p99_ms'])} | {fmt(metrics['tpot_p99_ms'])} | "
                f"{fmt(metrics['e2e_p99_ms'])} | "
                f"{fmt(metrics['queue_wait_p99_ms'])} "
                f"（缺 {metrics['queue_wait_missing_requests']}） | "
                f"{fmt(metrics['actual_request_rate_rps'], 3)} | "
                f"{fmt(metrics['output_throughput_tok_s'], 1)} |"
            )
    lines.extend(
        [
            "",
            "## 结论边界",
            "",
            "- 路由 `queue_wait` 有逐请求来源；模型 scheduler 等待与全局队列长度不可用；",
            "- 前半/后半与首尾 100 请求只用于观察窗口内变化，不是显著性检验；",
            "- 不据此声明长期稳定容量或最大稳定 RPS。",
            "",
        ]
    )
    md_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"已写出：{json_path.relative_to(ROOT)} / {md_path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
