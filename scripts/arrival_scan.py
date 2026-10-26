#!/usr/bin/env python3
"""AE3：报告 Poisson 到达模式的有限窗口预测；不做 RPS 插值。"""

from __future__ import annotations

import argparse
import json
from decimal import Decimal
from pathlib import Path

from llm_capacity_pricing.app.archive_integrity import (
    verify_files,
    verify_predict_metadata,
)
from llm_capacity_pricing.app.offline_archives import find_archives

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-id", required=True)
    parser.add_argument("--hardware-id", required=True)
    parser.add_argument("--tp", type=int, required=True)
    parser.add_argument("--dp", type=int, required=True)
    parser.add_argument("--input-len", type=int, required=True)
    parser.add_argument("--output-len", type=int, required=True)
    parser.add_argument("--ttft", type=Decimal, required=True)
    parser.add_argument("--tpot", type=Decimal, required=True)
    parser.add_argument("--rate", type=Decimal, nargs="+", required=True)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--json-out", default="docs/ae_poisson_capacity.json")
    parser.add_argument("--md-out", default="docs/ae_poisson_capacity.md")
    args = parser.parse_args()

    points = []
    for rate in sorted(set(args.rate)):
        entries = find_archives(
            model_id=args.model_id,
            hardware_id=args.hardware_id,
            input_len=args.input_len,
            output_len=args.output_len,
            concurrency=None,
            precision="bf16",
            framework="vllm",
            framework_version="0.24.0",
            gpu_limit=None,
            tp_sizes=(args.tp,),
            arrival_mode="poisson",
            request_rate_rps=str(rate),
            seed=args.seed,
            min_requests=500,
        )
        entries = [
            entry
            for entry in entries
            if entry.dp_size == args.dp and entry.pp_size in (None, 1)
        ]
        if not entries:
            points.append(
                {"request_rate_rps": str(rate), "status": "unknown", "reason": "没有该到达率的证据"}
            )
            continue
        entry = entries[0]
        prediction_path = ROOT / entry.native_prediction
        run_dir = prediction_path.parents[2]
        manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
        rel_pred = str(prediction_path.relative_to(run_dir))
        index = prediction_path.parent.name.rsplit("_", 1)[-1]
        rel_input = f"predict/predict_{index}.yaml"
        problems = verify_files(run_dir, manifest, [rel_input, rel_pred])
        if not problems:
            problems.extend(verify_predict_metadata(run_dir, manifest, rel_input))
        if problems:
            points.append(
                {"request_rate_rps": str(rate), "status": "unknown", "reason": "；".join(problems)}
            )
            continue
        payload = json.loads(prediction_path.read_text(encoding="utf-8"))
        ttft = payload.get("p99_ttft_ms")
        tpot = payload.get("p99_tpot_ms")
        if ttft is None or tpot is None:
            points.append(
                {"request_rate_rps": str(rate), "status": "unknown", "reason": "缺少 P99"}
            )
            continue
        passed = Decimal(str(ttft)) <= args.ttft and Decimal(str(tpot)) <= args.tpot
        points.append(
            {
                "request_rate_rps": str(rate),
                "seed": entry.seed,
                "status": "predicted_pass" if passed else "predicted_fail",
                "ttft_p99_ms": ttft,
                "tpot_p99_ms": tpot,
                "output_throughput_tok_s": payload.get("output_throughput_tok_s"),
                "request_throughput_rps": payload.get("request_throughput_rps"),
                "completed_requests": payload.get("completed_requests"),
                "archive": entry.native_prediction,
            }
        )

    passed_rates = [
        point["request_rate_rps"]
        for point in points
        if point["status"] == "predicted_pass"
    ]
    doc = {
        "schema_version": "1.0",
        "model_id": args.model_id,
        "hardware_id": args.hardware_id,
        "tp": args.tp,
        "dp": args.dp,
        "input_len": args.input_len,
        "output_len": args.output_len,
        "arrival_mode": "poisson",
        "sla": {"ttft_p99_ms_max": str(args.ttft), "tpot_p99_ms_max": str(args.tpot)},
        "points": points,
        "verified_pass_rates": passed_rates,
        "highest_verified_pass_rate": max(passed_rates, key=Decimal) if passed_rates else None,
        "note": (
            "只报告 500 请求有限窗口的模拟预测；RPS 不插值，"
            "最高通过到达率不是长期稳定服务容量保证。"
        ),
    }
    json_path = ROOT / args.json_out
    md_path = ROOT / args.md_out
    json_path.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = [
        "# Poisson 到达率有限窗口预测",
        "",
        f"- 模型：{args.model_id}；硬件：{args.hardware_id}；TP{args.tp}×DP{args.dp}",
        f"- 负载：ISL{args.input_len}/OSL{args.output_len}；至少 500 完成请求（实际见下表）",
        f"- SLA：TTFT P99 ≤ {args.ttft} ms，TPOT P99 ≤ {args.tpot} ms",
        "",
        "| 到达率 req/s | 状态 | TTFT P99 | TPOT P99 | 输出吞吐 | 实际完成 RPS | 完成请求 |",
        "|---|---|---|---|---|---|---|",
    ]
    for point in points:
        lines.append(
            f"| {point['request_rate_rps']} | {point['status']} | "
            f"{point.get('ttft_p99_ms', '—')} | {point.get('tpot_p99_ms', '—')} | "
            f"{point.get('output_throughput_tok_s', '—')} | "
            f"{point.get('request_throughput_rps', '—')} | "
            f"{point.get('completed_requests', '—')} |"
        )
    lines.extend(
        [
            "",
            f"- 已验证通过的到达率：{passed_rates or '无'}",
            "",
            "> 有限请求窗口的模拟预测；不插值，不代表长期稳定最大 RPS。",
            "",
        ]
    )
    md_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"已写出：{json_path.relative_to(ROOT)} / {md_path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
