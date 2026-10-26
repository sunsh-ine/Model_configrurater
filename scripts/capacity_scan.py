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
from llm_capacity_pricing.perf.experiment_audit import (
    archive_matches_manifest,
    load_manifests,
    run_evidence_problems,
)
from llm_capacity_pricing.perf.sla_gate import (
    aggregate_run_verdicts,
    evaluate_p99_gate,
)

ROOT = Path(__file__).resolve().parents[1]
MIN_COMPLETED = 500


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

    manifests = load_manifests()
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
            arrival_mode="fixed_concurrency",
            min_requests=0,
        )
        entries = [
            entry
            for entry in entries
            if entry.dp_size == args.dp and entry.pp_size == 1
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
        workloads: dict[tuple[Any, ...], list[Any]] = {}
        for entry in entries:
            key = (
                entry.model_name,
                entry.system,
                entry.precision,
                entry.backend,
                entry.backend_version,
                entry.gpu_count,
                entry.tp_size,
                entry.pp_size,
                entry.dp_size,
                entry.arrival_mode,
                entry.request_rate_rps,
                entry.dataset,
                entry.requests,
                entry.trace_id,
            )
            workloads.setdefault(key, []).append(entry)
        if len(workloads) > 1:
            points.append(
                {
                    "concurrency": concurrency,
                    "status": "unknown",
                    "reason": (
                        "同一并发点存在多个不同部署/负载身份，不能合并为重复运行"
                        "不能合并为重复运行"
                    ),
                    "variants": len(workloads),
                }
            )
            continue
        entries = next(iter(workloads.values()))
        matching = [
            manifest
            for manifest in manifests
            if entries and archive_matches_manifest(entries[0], manifest)
        ]
        if len(matching) > 1:
            points.append(
                {
                    "concurrency": concurrency,
                    "status": "unknown",
                    "reason": "同一工作点匹配到多份实验清单，无法唯一归属",
                }
            )
            continue
        matching_manifest = matching[0] if matching else None
        expected_seeds = (
            set(matching_manifest.expected_seeds)
            if matching_manifest is not None
            else None
        )
        required_completed = (
            matching_manifest.required_completed_requests
            if matching_manifest is not None
            else MIN_COMPLETED
        )
        if matching_manifest is not None and matching_manifest.out_of_scope_seeds:
            entries = [
                entry
                for entry in entries
                if entry.seed not in matching_manifest.out_of_scope_seeds
            ]
        run_results = []
        for entry in entries:
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
                run_results.append(
                    {
                        "status": "unknown",
                        "reason": "无法定位归档 manifest，不能验证预测来源",
                        "seed": entry.seed,
                        "archive": entry.native_prediction,
                    }
                )
                continue
            problems = verify_files(run_dir, manifest, [rel_input, rel_pred])
            if not problems:
                problems.extend(verify_predict_metadata(run_dir, manifest, rel_input))
            if problems:
                run_results.append(
                    {
                        "status": "unknown",
                        "reason": "归档完整性/元数据校验失败：" + "；".join(problems),
                        "seed": entry.seed,
                        "archive": entry.native_prediction,
                    }
                )
                continue
            try:
                prediction = json.loads(
                    prediction_path.read_text(encoding="utf-8")
                )
            except Exception:  # noqa: BLE001
                run_results.append(
                    {
                        "status": "unknown",
                        "reason": "预测文件不是有效 JSON",
                        "seed": entry.seed,
                        "archive": entry.native_prediction,
                    }
                )
                continue
            if matching_manifest is not None:
                planned = {
                    run.seed: run for run in matching_manifest.runs
                }.get(entry.seed)
                hash_problems = run_evidence_problems(
                    entry, prediction_path, planned
                )
                if hash_problems:
                    run_results.append(
                        {
                            "status": "unknown",
                            "reason": "；".join(hash_problems),
                            "seed": entry.seed,
                            "archive": entry.native_prediction,
                        }
                    )
                    continue
            completed = prediction.get("completed_requests")
            try:
                completed_count = int(completed) if completed is not None else None
            except (TypeError, ValueError):
                completed_count = None
            if completed_count is None or completed_count < required_completed:
                run_results.append(
                    {
                        "status": "unknown",
                        "reason": (
                            f"实际完成请求数不足或未知：{completed}"
                            f"（需要 >= {required_completed}）"
                        ),
                        "seed": entry.seed,
                        "archive": entry.native_prediction,
                    }
                )
                continue
            gate = evaluate_p99_gate(
                ttft_p99_ms=prediction.get("p99_ttft_ms"),
                tpot_p99_ms=prediction.get("p99_tpot_ms"),
                ttft_samples=prediction.get("num_ttft_samples"),
                tpot_samples=prediction.get("num_tpot_samples"),
                ttft_limit_ms=args.ttft,
                tpot_limit_ms=args.tpot,
            )
            if gate.verdict == "insufficient_prediction":
                run_results.append(
                    {
                        "status": "unknown",
                        "reason": "；".join(gate.reasons),
                        "seed": entry.seed,
                        "archive": entry.native_prediction,
                    }
                )
                continue
            run_results.append(
                {
                    "status": gate.verdict,
                    "ttft_p99_ms": prediction.get("p99_ttft_ms"),
                    "tpot_p99_ms": prediction.get("p99_tpot_ms"),
                    "throughput_tok_s": prediction.get("output_throughput_tok_s"),
                    "completed_requests": prediction.get("completed_requests"),
                    "seed": entry.seed,
                    "archive": entry.native_prediction,
                }
            )
        same_seed: dict[Any, tuple[Any, ...]] = {}
        seed_conflict = False
        for run in run_results:
            seed = run.get("seed")
            if seed is None:
                continue
            signature = (
                run.get("status"),
                run.get("ttft_p99_ms"),
                run.get("tpot_p99_ms"),
                run.get("throughput_tok_s"),
                run.get("output_throughput_tok_s"),
                run.get("completed_requests"),
                run.get("request_throughput_rps"),
            )
            previous = same_seed.get(seed)
            if previous is not None and previous != signature:
                seed_conflict = True
            else:
                same_seed[seed] = signature
        if seed_conflict:
            points.append(
                {
                    "concurrency": concurrency,
                    "status": "unknown",
                    "reason": "同一 seed 的多个归档预测值/结论冲突，不能合并",
                }
            )
            continue
        if expected_seeds is not None:
            found_seeds = {
                run.get("seed")
                for run in run_results
                if run.get("seed") is not None
            }
            if found_seeds != expected_seeds:
                points.append(
                    {
                        "concurrency": concurrency,
                        "status": "unknown",
                        "reason": (
                            "实验清单不完整：预期 "
                            f"{sorted(expected_seeds)}，实际 {sorted(found_seeds)}"
                        ),
                        "expected_runs": len(expected_seeds),
                        "found_runs": len(found_seeds),
                    }
                )
                continue
        status, pass_count = aggregate_run_verdicts(
            [run["status"] for run in run_results]
        )
        if status == "unknown":
            reasons = []
            for run in run_results:
                reason = run.get("reason")
                if reason and reason not in reasons:
                    reasons.append(reason)
            points.append(
                {
                    "concurrency": concurrency,
                    "status": "unknown",
                    "reason": "；".join(reasons) or "多 seed 运行中存在缺 P99 的归档",
                    "runs": len(run_results),
                    "pass_count": pass_count,
                    "seeds": [run.get("seed") for run in run_results],
                }
            )
            continue
        known = [
            run
            for run in run_results
            if run["status"] in ("predicted_pass", "predicted_fail")
        ]
        worst = max(
            known,
            key=lambda run: Decimal(str(run.get("tpot_p99_ms") or 0)),
        )
        ttfts = [
            Decimal(str(run["ttft_p99_ms"]))
            for run in known
            if run.get("ttft_p99_ms") is not None
        ]
        tpots = [
            Decimal(str(run["tpot_p99_ms"]))
            for run in known
            if run.get("tpot_p99_ms") is not None
        ]
        throughputs = [
            Decimal(str(run["throughput_tok_s"]))
            for run in known
            if run.get("throughput_tok_s") is not None
        ]
        points.append(
            {
                "concurrency": concurrency,
                "status": status,
                "ttft_p99_ms": float(max(ttfts)) if ttfts else None,
                "tpot_p99_ms": float(max(tpots)) if tpots else None,
                "throughput_tok_s": float(min(throughputs)) if throughputs else None,
                "completed_requests": min(
                    run["completed_requests"]
                    for run in known
                    if run.get("completed_requests") is not None
                )
                if any(run.get("completed_requests") is not None for run in known)
                else None,
                "archive": worst.get("archive"),
                "runs": len(run_results),
                "pass_count": pass_count,
                "seeds": [run.get("seed") for run in run_results],
                "reason": (
                    f"多 seed 结果不一致：{pass_count}/{len(run_results)} 通过"
                    if status == "mixed"
                    else None
                ),
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
    mixed = [
        point["concurrency"] for point in points if point["status"] == "mixed"
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
        "mixed_concurrency": mixed,
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
        "| 并发 | 状态 | 运行/通过 | TTFT P99 | TPOT P99 | 吞吐 tok/s | 说明 |",
        "|---|---|---|---|---|---|---|",
    ]
    for point in points:
        lines.append(
            f"| C{point['concurrency']} | {point['status']} | "
            f"{point.get('runs', 1)}/{point.get('pass_count', '—')} | "
            f"{point.get('ttft_p99_ms', '—')} | {point.get('tpot_p99_ms', '—')} | "
            f"{point.get('throughput_tok_s', '—')} | {point.get('reason') or '—'} |"
        )
    lines.extend(
        [
            "",
            f"- 已验证最高达标并发：{doc['highest_verified_pass'] or '无'}",
            f"- 未验证并发点：{unknown or '无'}",
            f"- 多 seed 结果不一致的并发点：{mixed or '无'}",
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
