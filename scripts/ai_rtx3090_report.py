#!/usr/bin/env python3
"""AI3/AI4/AI5：汇总 RTX 3090 实测、资格与校准边界。"""

from __future__ import annotations

import json
from pathlib import Path
from statistics import mean

ROOT = Path(__file__).resolve().parents[1]
RUN_ROOT = ROOT / "data/benchmark/rtx3090"
DB_PATH = ROOT / "data/exports/ai_rtx3090/rtx3090.db"
SLA_TTFT = 6000.0
SLA_TPOT = 40.0


def _p99(values):
    if not values:
        return None
    values = sorted(values)
    return values[max(0, int(0.99 * len(values) + 0.999999) - 1)]


def _metrics_from_requests(path: Path, summary: dict):
    rows = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    ttfts = [
        float(r.get("ttft_ms", r.get("ttft")))
        for r in rows
        if r.get("success")
        and r.get("ttft_ms", r.get("ttft")) is not None
    ]
    tpots = []
    total_out = 0
    for row in rows:
        if not row.get("success"):
            continue
        out_len = int(row.get("output_length", row.get("output_len")) or 0)
        total_out += out_len
        latency = row.get("latency_ms", row.get("latency"))
        ttft = row.get("ttft_ms", row.get("ttft"))
        if out_len > 1 and latency is not None and ttft is not None:
            tpots.append((float(latency) - float(ttft)) / (out_len - 1))
    window = summary.get("measurement_window_ms") or {}
    duration_s = (float(window.get("end_ms") or 0) - float(window.get("start_ms") or 0)) / 1000
    return {
        "ttft_p99_ms": _p99(ttfts),
        "tpot_p99_ms": _p99(tpots),
        "output_throughput_tok_s": total_out / duration_s if duration_s > 0 else None,
    }


def _db_trust() -> dict[str, str]:
    if not DB_PATH.is_file():
        return {}
    try:
        from llm_capacity_pricing.services import open_db

        db, repo = open_db(DB_PATH)
        try:
            with db.session() as session:
                return {
                    run.run_id: run.data_trust
                    for run in repo.list_benchmark_runs(session)
                }
        finally:
            db.dispose()
    except Exception:  # noqa: BLE001
        return {}


def _load_runs():
    runs = []
    db_trust = _db_trust()
    for directory in sorted(RUN_ROOT.glob("*")):
        summary_path = directory / "client_summary.json"
        if not summary_path.is_file():
            continue
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        import_path = directory / "import_report.json"
        imported = (
            json.loads(import_path.read_text(encoding="utf-8"))
            if import_path.is_file()
            else {}
        )
        validation = imported.get("validation") or {}
        recomputed = validation.get("recomputed") or {}
        metrics = {
            "ttft_p99_ms": recomputed.get("ttft_p99_ms")
            or summary.get("client_p99_ttft_ms"),
            "tpot_p99_ms": recomputed.get("tpot_p99_ms")
            or summary.get("client_p99_tpot_ms"),
            "output_throughput_tok_s": recomputed.get("output_throughput_tok_s")
            or summary.get("client_output_throughput"),
        }
        if not any(metrics.values()):
            metrics = _metrics_from_requests(directory / "requests.jsonl", summary)
        runs.append(
            {
                "run_id": directory.name,
                "summary": summary,
                "metrics": metrics,
                "qualification": validation.get("qualification"),
                "data_trust": db_trust.get(
                    f"rtx3090:{directory.name}",
                    (imported.get("attestation") or {}).get("data_trust"),
                ),
                "source_verified": (validation.get("source_verification") or {}).get(
                    "verified_source_kind"
                ),
            }
        )
    return runs


def main() -> int:
    runs = _load_runs()
    formal = [
        run
        for run in runs
        if run["run_id"].startswith("ai-e1c8-seed")
        and (run.get("qualification") or {}).get("sla_evaluable") is True
        and run.get("data_trust") == "measured_internal"
    ]
    for run in runs:
        summary = run["summary"]
        run["sla"] = {
            "ttft_pass": (
                run["metrics"]["ttft_p99_ms"] is not None
                and float(run["metrics"]["ttft_p99_ms"]) <= SLA_TTFT
            ),
            "tpot_pass": (
                run["metrics"]["tpot_p99_ms"] is not None
                and float(run["metrics"]["tpot_p99_ms"]) <= SLA_TPOT
            ),
        }
    repeat = None
    if formal:
        repeat = {
            "runs": len(formal),
            "pass_count": sum(
                1
                for run in formal
                if run["sla"]["ttft_pass"] and run["sla"]["tpot_pass"]
            ),
            "ttft_p99_min": min(
                float(run["metrics"]["ttft_p99_ms"]) for run in formal
            ),
            "ttft_p99_max": max(
                float(run["metrics"]["ttft_p99_ms"]) for run in formal
            ),
            "tpot_p99_min": min(
                float(run["metrics"]["tpot_p99_ms"]) for run in formal
            ),
            "tpot_p99_max": max(
                float(run["metrics"]["tpot_p99_ms"]) for run in formal
            ),
            "throughput_min": min(
                float(run["metrics"]["output_throughput_tok_s"]) for run in formal
            ),
            "throughput_max": max(
                float(run["metrics"]["output_throughput_tok_s"]) for run in formal
            ),
        }
    doc = {
        "schema_version": "1.0",
        "hardware": "NVIDIA GeForce RTX 3090 24GB",
        "model": "Qwen/Qwen3-8B BF16",
        "framework": "vllm 0.11.0",
        "sla_for_scenario_d": {"ttft_p99_ms_max": SLA_TTFT, "tpot_p99_ms_max": SLA_TPOT},
        "aisimulate_rtx3090_support": False,
        "aisimulate_note": (
            "AISimulate 0.12.1 的 systems 目录没有 rtx3090；按 AI5 情况 B，"
            "RTX 3090 实测只作为独立真实证据，预测校准保持 unknown。"
        ),
        "runs": runs,
        "formal_repeatability": repeat,
    }
    json_path = ROOT / "docs/ai_rtx3090_results.json"
    md_path = ROOT / "docs/ai_rtx3090_results.md"
    json_path.write_text(
        json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    lines = [
        "# AI3/AI4 RTX 3090 实测结果",
        "",
        "> vLLM 0.11.0 / Qwen3-8B BF16 / 单卡 RTX 3090；原始逐请求记录在 `data/benchmark/rtx3090/`。",
        "",
        "| 运行 | ISL/OSL | C/Rate | Seed | 成功 | TTFT P99 | TPOT P99 | 输出 tok/s | 资格 | data_trust |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for run in runs:
        summary = run["summary"]
        qualification = run["qualification"] or {}
        lines.append(
            f"| `{run['run_id']}` | "
            f"{summary.get('input_len_requested')}/{summary.get('output_len_requested')} "
            f"| C{summary.get('concurrency') or summary.get('max_concurrency')} / "
            f"{summary.get('request_rate')} "
            f"| {summary.get('seed')} "
            f"| {summary.get('success_count')}/{summary.get('num_requests')} "
            f"| {run['metrics']['ttft_p99_ms']} | {run['metrics']['tpot_p99_ms']} "
            f"| {run['metrics']['output_throughput_tok_s']} "
            f"| sla={qualification.get('sla_evaluable')} "
            f"| {run.get('data_trust')} |"
        )
    if repeat:
        lines.extend(
            [
                "",
                "## 正式重复性",
                "",
                f"- 运行数：{repeat['runs']}",
                f"- 同时满足 TTFT≤{SLA_TTFT}ms、TPOT≤{SLA_TPOT}ms：**{repeat['pass_count']}/{repeat['runs']}**",
                f"- TTFT P99 范围：{repeat['ttft_p99_min']}–{repeat['ttft_p99_max']} ms",
                f"- TPOT P99 范围：{repeat['tpot_p99_min']}–{repeat['tpot_p99_max']} ms",
                f"- 输出吞吐范围：{repeat['throughput_min']}–{repeat['throughput_max']} tok/s",
                "",
                "> 这是 5 次真实运行的观察结果，不是生产级 SLA 概率估计。",
            ]
        )
    lines.extend(
        [
            "",
            "## 瓶颈观察（实测）",
            "",
            "- 2048/256：C4 到 C8，输出吞吐从约 126 提升到约 198 tok/s，"
            "但 TPOT P99 从 31.2 升到 38.3–39.6 ms，TTFT P99 从 1.83 s 升到 1.77–2.22 s；"
            "吞吐提升伴随尾延迟上升。",
            "- 4096/256 C8：TTFT P99 7.09 s、TPOT P99 54.2 ms，长输入使 prefill/排队明显变重。",
            "- 8192/256 C2：输出吞吐约 47.6 tok/s，TPOT P99 35.0 ms；长上下文受 KV/显存约束。",
            "- Poisson 0.5 req/s（2048/256）：100 请求全部成功，TPOT P99 37.9 ms、"
            "输出吞吐约 129.7 tok/s；没有全局队列长度数据，不能据此宣称长期稳定。",
            "",
            "> 以上为探索性运行，除 E1 C8 的 5 次正式重复外，不作为正式 SLA 证据。",
            "",
            "## AI5 校准边界",
            "",
            "- AISimulate 0.12.1 的系统目录没有 `rtx3090`；",
            "- 因此没有同工作点的 3090 模拟预测，不能计算真实预测误差；",
            "- 3090 实测不用于校准 H100/H200，也不与其它 GPU 复用 P99；",
            "- `calibration_status=unknown`。",
            "",
        ]
    )
    md_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"已写出：{json_path.relative_to(ROOT)} / {md_path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
