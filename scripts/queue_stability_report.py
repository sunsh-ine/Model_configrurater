#!/usr/bin/env python3
"""AF3：基于 AISimulate 每请求输出分析有限窗口队列/尾延迟。

仅使用实际存在的字段；不推断长期稳定容量。
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ROOT_DIR = ROOT / "data/simulation/capabilities/per_request"
CASES = (
    ("8b_h100_dp2_r5_seed3", "qwen3-8b-h100-tp1dp2-r5-seed3"),
    ("8b_h200_dp2_r5_seed1", "qwen3-8b-h200-tp1dp2-r5-seed1"),
)


def p99(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(0.99 * len(ordered)) - 1)
    return ordered[index]


def load_run(case_dir: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    prediction = json.loads((case_dir / "prediction.json").read_text(encoding="utf-8"))
    requests = [
        json.loads(line)
        for line in (case_dir / "requests.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    return prediction, requests


def analyse(name: str, case_dir: Path) -> dict[str, Any]:
    prediction, requests = load_run(case_dir)
    requests.sort(key=lambda item: float(item.get("arrival_time_ms") or 0.0))
    ttfts = [float(r["ttft_ms"]) for r in requests if r.get("ttft_ms") is not None]
    tpots: list[float] = []
    queue_waits: list[float] = []
    statuses: dict[str, int] = {}
    for request in requests:
        output_len = int(request.get("output_length") or 0)
        first = request.get("first_token_ms")
        last = request.get("last_token_ms")
        if output_len > 1 and first is not None and last is not None:
            tpots.append((float(last) - float(first)) / (output_len - 1))
        waits = [
            float(item.get("queue_wait_ms") or 0.0)
            for item in request.get("routing_history") or []
        ]
        queue_waits.append(max(waits) if waits else 0.0)
        status = str(request.get("terminal_status") or "unknown")
        statuses[status] = statuses.get(status, 0) + 1
    half = len(requests) // 2
    first_half = requests[:half]
    second_half = requests[half:]
    return {
        "case_id": name,
        "requests": len(requests),
        "terminal_status": statuses,
        "completed": prediction.get("completed_requests"),
        "p99_ttft_ms_all": p99(ttfts),
        "p99_tpot_ms_all": p99(tpots),
        "p99_queue_wait_ms": p99(queue_waits),
        "max_queue_wait_ms": max(queue_waits) if queue_waits else None,
        "p99_ttft_first_half": p99(
            [float(r["ttft_ms"]) for r in first_half if r.get("ttft_ms") is not None]
        ),
        "p99_ttft_second_half": p99(
            [float(r["ttft_ms"]) for r in second_half if r.get("ttft_ms") is not None]
        ),
        "note": (
            "有限请求窗口；可按到达时间分段比较，但不能据此证明长期队列稳定。"
        ),
    }


def main() -> int:
    cases = [analyse(name, ROOT_DIR / directory) for name, directory in CASES]
    doc = {
        "schema_version": "1.0",
        "tool": "aisimulate",
        "tool_version": "0.12.1",
        "capture": "per_request",
        "cases": cases,
        "note": "有限窗口预测；不推断长期稳定最大 RPS。",
    }
    json_path = ROOT / "docs/af_load_stability.json"
    md_path = ROOT / "docs/af_load_stability.md"
    json_path.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = [
        "# AF3 有限窗口队列与尾延迟",
        "",
        "> 每请求字段来自 AISimulate `--capture-per-request`；"
        "以下是有限请求窗口，不是长期容量证明。",
        "",
        "| 组 | 完成 | TTFT P99 | TPOT P99 | 队列等待 P99 | 前半 TTFT P99 | 后半 TTFT P99 |",
        "|---|---|---|---|---|---|---|",
    ]
    for case in cases:
        lines.append(
            f"| {case['case_id']} | {case['completed']} | {case['p99_ttft_ms_all']:.2f} "
            f"| {case['p99_tpot_ms_all']:.2f} | {case['p99_queue_wait_ms']:.2f} "
            f"| {case['p99_ttft_first_half']:.2f} | {case['p99_ttft_second_half']:.2f} |"
        )
    lines.extend(
        [
            "",
            "## 能力矩阵",
            "",
            "| 能力 | 状态 | 说明 |",
            "|---|---|---|",
            "| 每请求到达/首 token/末 token 时间 | 可用 | `arrival_time_ms` / "
            "`first_token_ms` / `last_token_ms` |",
            "| 每请求 TTFT / E2E | 可用 | `ttft_ms` / `e2e_latency_ms` |",
            "| 每请求路由队列等待 | 可用 | `routing_history[].queue_wait_ms` |",
            "| 每请求终止状态 | 可用 | `terminal_status` 计数 |",
            "| 队列长度/等待请求数时间序列 | **不可用** | 工具只给单请求路由等待，"
            "没有全局队列采样 |",
            "| 长期稳定最大 RPS | **不可用** | 有限窗口只能说明窗口内是否恶化 |",
            "| 分段尾延迟对比 | 可用（有限） | 前半/后半是描述性对比，不是显著性检验 |",
            "",
            "## 结论边界",
            "",
            "- 上表只能说明该有限请求窗口内的尾延迟与队列等待；",
            "- 前半/后半 P99 上升不等于长期发散，下降也不证明稳定；",
            "- 不据此给出「最大稳定 RPS」，容量仍只认离散验证点。",
            "",
        ]
    )
    md_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"已写出：{json_path.relative_to(ROOT)} / {md_path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
