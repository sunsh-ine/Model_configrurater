#!/usr/bin/env python3
"""AH4：三组客户场景端到端回放（offline 归档，不调用 GPU）。"""

from __future__ import annotations

import argparse
import json
from decimal import Decimal
from pathlib import Path

from llm_capacity_pricing.app.demo_service import DemoInputs, run_demo

ROOT = Path(__file__).resolve().parents[1]
SCENARIOS = {
    "A-same-gpu-parallel": DemoInputs(
        model_id="qwen3-32b",
        hardware_id="nvidia-h100-sxm-80gb",
        gpu_count=8,
        input_len=8192,
        output_len=256,
        concurrency=32,
        tp_sizes=(8, 4),
        ttft_p99_ms_max=Decimal("6000"),
        tpot_p99_ms_max=Decimal("40"),
        budget_amount=None,
        budget_currency=None,
        request_id="ah-scenario-a",
        top_n=4,
    ),
    "B-min-gpu": DemoInputs(
        model_id="qwen3-8b",
        hardware_id="nvidia-h100-sxm-80gb",
        gpu_count=8,
        input_len=8192,
        output_len=256,
        concurrency=32,
        tp_sizes=(1,),
        ttft_p99_ms_max=Decimal("6000"),
        tpot_p99_ms_max=Decimal("40"),
        budget_amount=None,
        budget_currency=None,
        request_id="ah-scenario-b",
        top_n=5,
    ),
    "C-open-arrival-risk": DemoInputs(
        model_id="qwen3-8b",
        hardware_id="nvidia-h200-sxm-141gb",
        gpu_count=2,
        input_len=8192,
        output_len=256,
        concurrency=32,
        tp_sizes=(1,),
        arrival_mode="poisson",
        request_rate_rps=Decimal("5"),
        ttft_p99_ms_max=Decimal("6000"),
        tpot_p99_ms_max=Decimal("40"),
        budget_amount=None,
        budget_currency=None,
        request_id="ah-scenario-c",
        top_n=5,
    ),
}


def _scenario_markdown(scenario_id: str, result: dict, out_dir: Path) -> str:
    evidence = result.get("decision_evidence") or {}
    try:
        display_dir = out_dir.relative_to(ROOT)
    except ValueError:
        display_dir = out_dir
    lines = [
        f"# AH 客户场景 {scenario_id}",
        "",
        "> 由 `scripts/ah_customer_scenarios.py` 生成；数值来自同一次 `evaluate_plan()`。",
        "",
        f"- 运行目录：`{display_dir}`",
        f"- 排序目标：{evidence.get('ranking', 'throughput')}",
        "",
        "| 目标 | 候选 | GPU | 结论 | 实验完整性 | 校准 |",
        "|---|---|---|---|---|---|",
    ]
    for label, key in (
        ("推荐（严格）", "recommended"),
        ("未达标参考", "best_effort"),
        ("最少 GPU 达标", "min_gpu_for_sla"),
        ("吞吐最优达标", "throughput_best"),
    ):
        item = evidence.get(key)
        if not item:
            lines.append(f"| {label} | 无 | — | — | — | — |")
            continue
        lines.append(
            f"| {label} | `{item['candidate_id']}` | {item['gpu_count']} "
            f"| {item['verdict']} | {item.get('experiment_completeness') or '—'} "
            f"| {item.get('calibration_status') or 'unknown'} |"
        )
    lines.extend(
        [
            "",
            "## 候选证据",
            "",
            "| 候选 | GPU | TP/DP | P99 TTFT/TPOT | SLA | 证据 | 实验 | 校准 |",
            "|---|---|---|---|---|---|---|---|",
        ]
    )
    for candidate in evidence.get("candidates", []):
        lines.append(
            f"| `{candidate['candidate_id']}` | {candidate['gpu_count']} "
            f"| {candidate['tp_size']}/{candidate['dp_size']} "
            f"| {candidate['p99'].get('ttft_ms')}/{candidate['p99'].get('tpot_ms')} "
            f"| {candidate['sla_status']} | {candidate['evidence'].get('type')} "
            f"| {candidate['evidence'].get('experiment_completeness') or '—'} "
            f"| {candidate['evidence'].get('calibration_status')} |"
        )
    lines.extend(
        [
            "",
            "## 模拟候选",
            "",
            "| 候选 | GPU | TP/DP | P99 TTFT/TPOT | 结论 | 实验完整性 | 重复性 | 可推荐 |",
            "|---|---|---|---|---|---|---|---|",
        ]
    )
    for candidate in evidence.get("simulation_candidates", []):
        lines.append(
            f"| `{candidate['candidate_id']}` | {candidate['gpu_count']} "
            f"| {candidate['tp_size']}/{candidate['dp_size']} "
            f"| {candidate['p99_ttft_ms']}/{candidate['p99_tpot_ms']} "
            f"| {candidate['verdict']} "
            f"| {candidate.get('experiment_completeness') or '—'} "
            f"| {candidate.get('repeatability_status') or '—'} "
            f"| {'是' if candidate.get('eligible') else '否'} |"
        )
    lines.extend(
        [
            "",
            "> 模拟预测不是实测；没有真实 GPU Benchmark 时校准状态保持 unknown。",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-root", default="docs/ah_scenarios")
    args = parser.parse_args()
    root = ROOT / args.out_root
    for scenario_id, inputs in SCENARIOS.items():
        out_dir = root / scenario_id
        if out_dir.exists() and any(out_dir.iterdir()):
            raise SystemExit(f"场景目录非空，拒绝覆盖：{out_dir}")
        outcome = run_demo(
            inputs,
            out_dir=out_dir,
            write_files=True,
            demo_note=f"AH4 {scenario_id}",
            scenario_id=scenario_id,
        )
        summary = {
            "scenario_id": scenario_id,
            "inputs": {
                "model_id": inputs.model_id,
                "hardware_id": inputs.hardware_id,
                "gpu_count": inputs.gpu_count,
                "input_len": inputs.input_len,
                "output_len": inputs.output_len,
                "concurrency": inputs.concurrency,
                "arrival_mode": inputs.arrival_mode,
                "request_rate_rps": str(inputs.request_rate_rps)
                if inputs.request_rate_rps is not None
                else None,
                "sla": {
                    "ttft_p99_ms_max": str(inputs.ttft_p99_ms_max),
                    "tpot_p99_ms_max": str(inputs.tpot_p99_ms_max),
                },
            },
            "decision_evidence": outcome.result.decision_evidence,
            "stages": [
                (name, status, detail.replace(str(ROOT), "."))
                for name, status, detail in outcome.stages
            ],
            "proposal_path": str(
                outcome.proposal_path.relative_to(ROOT)
                if outcome.proposal_path
                else None
            ),
            "technical_path": str(
                outcome.technical_path.relative_to(ROOT)
                if outcome.technical_path
                else None
            ),
        }
        (out_dir / "summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2, default=str) + "\n",
            encoding="utf-8",
        )
        (out_dir / "scenario.md").write_text(
            _scenario_markdown(scenario_id, summary, out_dir), encoding="utf-8"
        )
        # 数据库与完整原始结果不入公开仓库；结构化摘要已包含决策证据。
        for private_name in ("demo.db", "result.json"):
            (out_dir / private_name).unlink(missing_ok=True)
        print(f"{scenario_id}: {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
