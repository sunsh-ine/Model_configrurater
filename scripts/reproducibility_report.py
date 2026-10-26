#!/usr/bin/env python3
"""AF1：同一工作点多次 seed 的预测重复性报告。

只统计运行级 P99/吞吐，不用运行均值冒充合并请求的 P99。
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from llm_capacity_pricing.app.archive_integrity import (
    verify_files,
    verify_predict_metadata,
)
from llm_capacity_pricing.app.offline_archives import find_archives
from llm_capacity_pricing.perf.sla_gate import evaluate_p99_gate

ROOT = Path(__file__).resolve().parents[1]
GROUPS = (
    ("8b_h100_dp2_r4", "qwen3-8b", "nvidia-h100-sxm-80gb", 1, 2, 4),
    ("8b_h100_dp2_r5", "qwen3-8b", "nvidia-h100-sxm-80gb", 1, 2, 5),
    ("8b_h200_dp2_r5", "qwen3-8b", "nvidia-h200-sxm-141gb", 1, 2, 5),
)
SEEDS = (1, 2, 3, 4, 5)
TTFT = Decimal("6000")
TPOT = Decimal("40")


def run_point(model_id, hardware_id, tp, dp, rate, seed):
    entries = find_archives(
        model_id=model_id,
        hardware_id=hardware_id,
        input_len=8192,
        output_len=256,
        concurrency=None,
        precision="bf16",
        framework="vllm",
        framework_version="0.24.0",
        gpu_limit=2,
        tp_sizes=(tp,),
        arrival_mode="poisson",
        request_rate_rps=str(rate),
        seed=seed,
        min_requests=500,
    )
    entries = [e for e in entries if e.dp_size == dp and e.pp_size in (None, 1)]
    if not entries:
        return {"seed": seed, "status": "unknown", "reason": "缺少该 seed 的证据"}
    if len(entries) > 1:
        return {
            "seed": seed,
            "status": "unknown",
            "reason": "同一 seed 存在多个预测值不一致的归档，不能合并或挑选",
        }
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
    except Exception:  # noqa: BLE001
        return {
            "seed": seed,
            "status": "unknown",
            "reason": "无法定位归档 manifest，不能验证预测来源",
        }
    problems = verify_files(run_dir, manifest, [rel_input, rel_pred])
    if not problems:
        problems.extend(verify_predict_metadata(run_dir, manifest, rel_input))
    if problems:
        return {
            "seed": seed,
            "status": "unknown",
            "reason": "归档完整性/元数据校验失败：" + "；".join(problems),
        }
    payload = json.loads(prediction_path.read_text(encoding="utf-8"))
    gate = evaluate_p99_gate(
        ttft_p99_ms=payload.get("p99_ttft_ms"),
        tpot_p99_ms=payload.get("p99_tpot_ms"),
        ttft_samples=payload.get("num_ttft_samples"),
        tpot_samples=payload.get("num_tpot_samples"),
        ttft_limit_ms=TTFT,
        tpot_limit_ms=TPOT,
    )
    return {
        "seed": seed,
        "status": gate.verdict,
        "ttft_p99_ms": payload.get("p99_ttft_ms"),
        "tpot_p99_ms": payload.get("p99_tpot_ms"),
        "output_throughput_tok_s": payload.get("output_throughput_tok_s"),
        "completed_requests": payload.get("completed_requests"),
        "archive": entry.native_prediction,
    }


def main() -> int:
    report = []
    for group_id, model_id, hardware_id, tp, dp, rate in GROUPS:
        runs = [run_point(model_id, hardware_id, tp, dp, rate, seed) for seed in SEEDS]
        known = [r for r in runs if r["status"] in ("predicted_pass", "predicted_fail")]
        ttft_values = [
            float(r["ttft_p99_ms"])
            for r in known
            if r.get("ttft_p99_ms") is not None
        ]
        tpot_values = [
            float(r["tpot_p99_ms"])
            for r in known
            if r.get("tpot_p99_ms") is not None
        ]
        throughput_values = [
            float(r["output_throughput_tok_s"])
            for r in known
            if r.get("output_throughput_tok_s") is not None
        ]
        pass_count = sum(1 for r in known if r["status"] == "predicted_pass")
        fail_count = sum(1 for r in known if r["status"] == "predicted_fail")
        missing_runs = len(runs) - len(known)
        if missing_runs:
            # 请求的 seed 有缺失或没有可用 P99：不能把剩余运行写成完整重复性。
            status = "incomplete"
        elif known and pass_count == len(known):
            status = "all_pass"
        elif known and fail_count == len(known):
            status = "all_fail"
        elif known:
            status = "mixed"
        else:
            status = "insufficient"
        report.append(
            {
                "group_id": group_id,
                "model_id": model_id,
                "hardware_id": hardware_id,
                "tp": tp,
                "dp": dp,
                "arrival_mode": "poisson",
                "request_rate_rps": rate,
                "seeds": list(SEEDS),
                "runs": runs,
                "run_count": len(known),
                "requested_run_count": len(SEEDS),
                "missing_run_count": missing_runs,
                "pass_count": pass_count,
                "fail_count": fail_count,
                "pass_ratio": (
                    pass_count / len(known)
                    if known and not missing_runs
                    else None
                ),
                "repeatability_status": status,
                "ttft_p99_min": min(ttft_values) if ttft_values else None,
                "ttft_p99_max": max(ttft_values) if ttft_values else None,
                "ttft_p99_mean_of_runs": (
                    sum(ttft_values) / len(ttft_values) if ttft_values else None
                ),
                "tpot_p99_min": min(tpot_values) if tpot_values else None,
                "tpot_p99_max": max(tpot_values) if tpot_values else None,
                "tpot_p99_mean_of_runs": (
                    sum(tpot_values) / len(tpot_values) if tpot_values else None
                ),
                "throughput_min": min(throughput_values) if throughput_values else None,
                "throughput_max": max(throughput_values) if throughput_values else None,
            }
        )

    doc = {
        "schema_version": "1.0",
        "sla": {"ttft_p99_ms_max": str(TTFT), "tpot_p99_ms_max": str(TPOT)},
        "note": (
            "运行级统计；多次运行的 P99 平均值不等于合并请求样本后的 P99。"
            "这是模拟预测波动，不是真实模型误差。"
        ),
        "groups": report,
    }
    json_path = ROOT / "docs/af_reproducibility.json"
    md_path = ROOT / "docs/af_reproducibility.md"
    json_path.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = [
        "# AF1 模拟预测重复性",
        "",
        "> 5 个独立 seed，每个 500 完成请求；运行级统计，不合并逐请求分布。",
        "",
        "| 组 | 部署 | 到达率 | 通过 | 状态 | TTFT P99 范围 | TPOT P99 范围 |",
        "|---|---|---|---|---|---|---|",
    ]
    status_labels = {
        "all_pass": "全部通过",
        "all_fail": "全部未通过",
        "mixed": "部分通过（mixed）",
        "incomplete": "未判定（缺运行/P99）",
        "insufficient": "证据不足",
    }
    for group in report:
        status_label = status_labels.get(
            group["repeatability_status"], group["repeatability_status"]
        )
        lines.append(
            f"| {group['group_id']} | TP{group['tp']}×DP{group['dp']} @ {group['hardware_id']} "
            f"| {group['request_rate_rps']} req/s | {group['pass_count']}/{group['run_count']} "
            f"| {status_label} "
            f"| {group['ttft_p99_min']}–{group['ttft_p99_max']} ms "
            f"| {group['tpot_p99_min']}–{group['tpot_p99_max']} ms |"
        )
    lines.extend(
        [
            "",
            "> 运行均值不等于池化 P99；结论仅表示模拟预测波动，不代表真实误差。",
            "",
        ]
    )
    md_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"已写出：{json_path.relative_to(ROOT)} / {md_path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
