#!/usr/bin/env python3
"""AG1/AF1：同一工作点多次 seed 的预测重复性报告。

只统计运行级 P99/吞吐，不用运行均值冒充合并请求的 P99。
预期 seed 来自冻结实验清单；缺运行、缺 P99、哈希不符都记为 incomplete。
"""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from pathlib import Path

from llm_capacity_pricing.app.archive_integrity import (
    verify_files,
    verify_predict_metadata,
)
from llm_capacity_pricing.app.offline_archives import find_archives
from llm_capacity_pricing.perf.experiment_audit import load_manifests
from llm_capacity_pricing.perf.sla_gate import evaluate_p99_gate

ROOT = Path(__file__).resolve().parents[1]
GROUPS = (
    ("8b_h100_dp2_r4", "qwen3-8b", "nvidia-h100-sxm-80gb", 1, 2, 4),
    ("8b_h100_dp2_r5", "qwen3-8b", "nvidia-h100-sxm-80gb", 1, 2, 5),
    ("8b_h200_dp2_r5", "qwen3-8b", "nvidia-h200-sxm-141gb", 1, 2, 5),
)
DEFAULT_SEEDS = (1, 2, 3, 4, 5)
TTFT = Decimal("6000")
TPOT = Decimal("40")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run_point(
    model_id,
    hardware_id,
    tp,
    dp,
    rate,
    seed,
    *,
    required_completed=500,
):
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
        min_requests=0,
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
    completed = payload.get("completed_requests")
    try:
        completed_count = int(completed) if completed is not None else None
    except (TypeError, ValueError):
        completed_count = None
    if completed_count is None or completed_count < required_completed:
        return {
            "seed": seed,
            "status": "unknown",
            "reason": (
                f"实际完成请求数不足或未知：{completed}"
                f"（需要 >= {required_completed}）"
            ),
            "completed_requests": completed,
        }
    gate = evaluate_p99_gate(
        ttft_p99_ms=payload.get("p99_ttft_ms"),
        tpot_p99_ms=payload.get("p99_tpot_ms"),
        ttft_samples=payload.get("num_ttft_samples"),
        tpot_samples=payload.get("num_tpot_samples"),
        ttft_limit_ms=TTFT,
        tpot_limit_ms=TPOT,
    )
    config_file = ROOT / entry.exp_config if entry.exp_config else None
    return {
        "seed": seed,
        "status": gate.verdict,
        "ttft_p99_ms": payload.get("p99_ttft_ms"),
        "tpot_p99_ms": payload.get("p99_tpot_ms"),
        "output_throughput_tok_s": payload.get("output_throughput_tok_s"),
        "completed_requests": completed,
        "archive": entry.native_prediction,
        "archive_sha256": sha256_file(prediction_path),
        "config_file": entry.exp_config,
        "config_sha256": (
            sha256_file(config_file)
            if config_file is not None and config_file.is_file()
            else None
        ),
    }


def main() -> int:
    manifests = {
        manifest.experiment_id: manifest for manifest in load_manifests()
    }
    report = []
    for group_id, model_id, hardware_id, tp, dp, rate in GROUPS:
        manifest = manifests.get(f"af-{group_id}")
        expected_seeds = tuple(manifest.expected_seeds) if manifest else DEFAULT_SEEDS
        required = (
            manifest.required_completed_requests if manifest else 500
        )
        runs = [
            run_point(
                model_id,
                hardware_id,
                tp,
                dp,
                rate,
                seed,
                required_completed=required,
            )
            for seed in expected_seeds
        ]
        # 清单声明的原始哈希必须与实际归档一致。
        if manifest is not None:
            planned = {run.seed: run for run in manifest.runs}
            for run in runs:
                planned_run = planned.get(run["seed"])
                if planned_run is None:
                    run["status"] = "unknown"
                    run["reason"] = "实验清单缺少该 seed 的运行记录"
                    continue
                if planned_run.status != "succeeded":
                    run["status"] = "unknown"
                    run["reason"] = (
                        f"实验清单记录该 seed 为 {planned_run.status}"
                    )
                    continue
                if not planned_run.archive_sha256 or not planned_run.config_sha256:
                    run["status"] = "unknown"
                    run["reason"] = "实验清单缺少归档或配置哈希"
                    continue
                if planned_run.archive and run.get("archive") != planned_run.archive:
                    run["status"] = "unknown"
                    run["reason"] = "实验清单声明的归档路径与实际不一致"
                    continue
                if planned_run.archive_sha256 != run.get("archive_sha256"):
                    run["status"] = "unknown"
                    run["reason"] = "实验清单声明的归档哈希与实际文件不一致"
                    continue
                if planned_run.config_sha256 != run.get("config_sha256"):
                    run["status"] = "unknown"
                    run["reason"] = "实验清单声明的配置哈希与实际文件不一致"
                    continue
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
                "experiment_id": manifest.experiment_id if manifest else None,
                "model_id": model_id,
                "hardware_id": hardware_id,
                "tp": tp,
                "dp": dp,
                "arrival_mode": "poisson",
                "request_rate_rps": rate,
                "seeds": list(expected_seeds),
                "runs": runs,
                "expected_run_count": len(runs),
                "run_count": len(known),
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
        "schema_version": "1.1",
        "sla": {"ttft_p99_ms_max": str(TTFT), "tpot_p99_ms_max": str(TPOT)},
        "note": (
            "运行级统计；预期 seed 来自冻结实验清单，缺失即 incomplete。"
            "多次运行的 P99 平均值不等于合并请求样本后的 P99；"
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
        "> 预期 seed 来自 `data/simulation/experiments/`；缺失、哈希不符或不足 500 请求",
        "> 的运行都记为 incomplete，不会用剩余 seed 冒充完整实验。",
        "",
        "| 组 | 部署 | 到达率 | 通过 | 完整性/结论 | TTFT P99 范围 | TPOT P99 范围 |",
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
