#!/usr/bin/env python3
"""AG6：预测误差与校准状态报告。

没有真实 GPU 实测时，真实校准状态保持 unknown；夹具只验证导入/误差计算链路。
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from llm_capacity_pricing.perf.benchmark_ingest import (
    load_requests,
    recompute_metrics,
    run_import_pipeline,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "data/examples/benchmark_pipeline"


def _fixture_prediction(metrics):
    return {
        "model_id": "qwen3-8b",
        "model_revision": "fixture-rev-001",
        "weight_precision": "bf16",
        "kv_precision": "bf16",
        "hardware_id": "nvidia-h200-sxm-141gb",
        "gpu_count": 2,
        "framework": "sglang",
        "framework_version": "0.5.6",
        "tp_size": 1,
        "dp_size": 2,
        "pp_size": 1,
        "input_len": 8192,
        "output_len": 256,
        "arrival_mode": "fixed_concurrency",
        "concurrency": 32,
        "dataset": "synthetic",
        "p99_ttft_ms": float(metrics["ttft_p99_ms"]),
        "p99_tpot_ms": float(metrics["tpot_p99_ms"]),
        "output_throughput_tok_s": float(metrics["output_throughput_tok_s"]),
        "metric_definitions": metrics["metric_definitions"],
    }


def main() -> int:
    metadata = yaml.safe_load(
        (FIXTURE / "metadata.yaml").read_text(encoding="utf-8")
    )
    metrics = recompute_metrics(load_requests(FIXTURE / "requests.jsonl"))
    pipeline = run_import_pipeline(
        FIXTURE / "requests.jsonl",
        metadata,
        evidence_root=FIXTURE,
        prediction=_fixture_prediction(metrics),
    )
    reproducibility = json.loads(
        (ROOT / "docs/af_reproducibility.json").read_text(encoding="utf-8")
    )
    groups = []
    for group in reproducibility["groups"]:
        groups.append(
            {
                "group_id": group["group_id"],
                "repeatability_status": group["repeatability_status"],
                "ttft_p99_min": group["ttft_p99_min"],
                "ttft_p99_max": group["ttft_p99_max"],
                "tpot_p99_min": group["tpot_p99_min"],
                "tpot_p99_max": group["tpot_p99_max"],
                "note": "模拟不确定度，不是实测误差",
            }
        )

    doc = {
        "schema_version": "1.0",
        "real_benchmark": {
            "measured_benchmark_runs": 0,
            "calibration_status": "unknown",
            "note": "没有目标配置的真实 GPU Benchmark；不得计算真实预测误差。",
        },
        "fixture_pipeline": {
            "calibration_status": "fixture_only",
            "matched": pipeline.get("calibration", {}).get("matched"),
            "calibration_usable": pipeline.get("calibration", {}).get(
                "calibration_usable"
            ),
            "errors": pipeline.get("calibration", {}).get("errors"),
            "note": "synthetic_fixture 只验证导入与误差计算链路，不进入校准库。",
        },
        "simulation_uncertainty": {
            "groups": groups,
            "note": (
                "多 seed 范围是模拟预测波动；不能当作真实模型误差，"
                "也不能据 5 次运行推算真实达标概率。"
            ),
        },
    }
    json_path = ROOT / "docs/ag_calibration_report.json"
    md_path = ROOT / "docs/ag_calibration_report.md"
    json_path.write_text(json.dumps(doc, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")

    lines = [
        "# AG6 预测误差与校准状态",
        "",
        "## 真实 Benchmark",
        "",
        "- 数量：0",
        "- 校准状态：**unknown**（没有目标配置的真实 GPU 实测）",
        "- 不计算真实预测误差，也不训练全局修正系数。",
        "",
        "## 夹具管线",
        "",
        f"- 身份匹配：{doc['fixture_pipeline']['matched']}",
        f"- 可校准：{doc['fixture_pipeline']['calibration_usable']}",
        "- 状态：**fixture_only**（synthetic_fixture 只验证链路）",
        "",
        "## 模拟不确定度（不是实测误差）",
        "",
        "| 组 | 状态 | TTFT P99 范围 | TPOT P99 范围 |",
        "|---|---|---|---|",
    ]
    for group in groups:
        lines.append(
            f"| {group['group_id']} | {group['repeatability_status']} "
            f"| {group['ttft_p99_min']}–{group['ttft_p99_max']} ms "
            f"| {group['tpot_p99_min']}–{group['tpot_p99_max']} ms |"
        )
    lines.extend(
        [
            "",
            "> 模拟不确定度与实测误差分开呈现；没有实测时实测误差保持 unknown。",
            "",
        ]
    )
    md_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"已写出：{json_path.relative_to(ROOT)} / {md_path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
