#!/usr/bin/env python3
"""AD4：按已验证的原生回放点报告 SLA 容量边界。

只读取归档；不做插值，不给未验证并发点的结论。
"""

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
    parser.add_argument("--concurrency", type=int, nargs="+", required=True)
    parser.add_argument("--json-out", default="docs/ad_capacity.json")
    parser.add_argument("--md-out", default="docs/ad_capacity.md")
    args = parser.parse_args()

    points = []
    for concurrency in sorted(set(args.concurrency)):
        entries = find_archives(
            model_id=args.model_id,
            hardware_id=args.hardware_id,
            input_len=args.input_len,
            output_len=args.output_len,
            concurrency=concurrency,
            precision="bf16",
            framework="vllm",
            framework_version="0.24.0",
            gpu_limit=None,
            tp_sizes=(args.tp,),
        )
        entries = [
            entry
            for entry in entries
            if entry.dp_size == args.dp and entry.pp_size in (None, 1)
        ]
        if not entries:
            points.append(
                {
                    "concurrency": concurrency,
                    "status": "unknown",
                    "reason": "没有该并发点的原生 P99 证据",
                }
            )
            continue
        entry = entries[0]
        prediction_path = ROOT / entry.native_prediction
        try:
            run_dir = prediction_path.parents[2]
            manifest = json.loads(
                (run_dir / "manifest.json").read_text(encoding="utf-8")
            )
            rel_pred = str(prediction_path.relative_to(run_dir))
            index = prediction_path.parent.name.rsplit("_", 1)[-1]
            rel_input = f"predict/predict_{index}.yaml"
        except Exception:
            points.append(
                {
                    "concurrency": concurrency,
                    "status": "unknown",
                    "reason": "无法定位归档 manifest，不能验证预测来源",
                }
            )
            continue
        problems = verify_files(run_dir, manifest, [rel_input, rel_pred])
        if not problems:
            problems.extend(verify_predict_metadata(run_dir, manifest, rel_input))
        if problems:
            points.append(
                {
                    "concurrency": concurrency,
                    "status": "unknown",
                    "reason": "归档完整性/元数据校验失败：" + "；".join(problems),
                }
            )
            continue
        prediction = json.loads(prediction_path.read_text(encoding="utf-8"))
        ttft = prediction.get("p99_ttft_ms")
        tpot = prediction.get("p99_tpot_ms")
        if ttft is None or tpot is None:
            points.append(
                {
                    "concurrency": concurrency,
                    "status": "unknown",
                    "reason": "归档缺少 TTFT/TPOT P99，不能判定达标或失败",
                    "archive": entry.native_prediction,
                }
            )
            continue
        passed = (
            Decimal(str(ttft)) <= args.ttft
            and Decimal(str(tpot)) <= args.tpot
        )
        points.append(
            {
                "concurrency": concurrency,
                "status": "predicted_pass" if passed else "predicted_fail",
                "ttft_p99_ms": ttft,
                "tpot_p99_ms": tpot,
                "throughput_tok_s": prediction.get("output_throughput_tok_s"),
                "completed_requests": prediction.get("completed_requests"),
                "archive": entry.native_prediction,
            }
        )

    verified_pass = [
        point["concurrency"]
        for point in points
        if point["status"] == "predicted_pass"
    ]
    unknown = [
        point["concurrency"] for point in points if point["status"] == "unknown"
    ]
    doc = {
        "schema_version": "1.0",
        "model_id": args.model_id,
        "hardware_id": args.hardware_id,
        "tp": args.tp,
        "dp": args.dp,
        "input_len": args.input_len,
        "output_len": args.output_len,
        "sla": {"ttft_p99_ms_max": str(args.ttft), "tpot_p99_ms_max": str(args.tpot)},
        "points": points,
        "verified_pass_concurrency": verified_pass,
        "highest_verified_pass": max(verified_pass) if verified_pass else None,
        "unknown_concurrency": unknown,
        "note": (
            "只报告已验证的原生回放点；未验证并发点保持 unknown，不做插值。"
            "最高已验证并发不是生产容量保证。"
        ),
    }
    json_path = ROOT / args.json_out
    md_path = ROOT / args.md_out
    json_path.write_text(
        json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    lines = [
        "# SLA 容量边界（已验证点）",
        "",
        f"- 模型：{args.model_id}",
        f"- 硬件：{args.hardware_id}",
        f"- 并行：TP{args.tp} × DP{args.dp}",
        f"- 负载：ISL{args.input_len} / OSL{args.output_len}",
        f"- SLA：TTFT P99 ≤ {args.ttft} ms，TPOT P99 ≤ {args.tpot} ms",
        "",
        "| 并发 | 状态 | TTFT P99 | TPOT P99 | 吞吐 tok/s |",
        "|---|---|---|---|---|",
    ]
    for point in points:
        lines.append(
            f"| C{point['concurrency']} | {point['status']} | "
            f"{point.get('ttft_p99_ms', '—')} | {point.get('tpot_p99_ms', '—')} | "
            f"{point.get('throughput_tok_s', '—')} |"
        )
    lines.extend(
        [
            "",
            f"- 已验证最高达标并发：{doc['highest_verified_pass'] or '无'}",
            f"- 未验证并发点：{unknown or '无'}",
            "",
            "> 只使用已验证工作点；未验证区间不做插值，也不代表生产容量保证。",
            "",
        ]
    )
    md_path.write_text("\n".join(lines), encoding="utf-8")
    def display(path: Path) -> str:
        try:
            return str(path.relative_to(ROOT))
        except ValueError:
            return str(path)

    print(f"已写出：{display(json_path)} / {display(md_path)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
