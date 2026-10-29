#!/usr/bin/env python3
"""AJ7：客户场景 E —— RTX 3090 可持续容量与推荐并发。"""

from __future__ import annotations

import argparse
import hashlib
import json
from decimal import Decimal
from pathlib import Path

from llm_capacity_pricing.reporting.decision_evidence import build_decision_evidence
from llm_capacity_pricing.reporting.plan_report import build_plan_report
from llm_capacity_pricing.reporting.proposal import build_deployment_proposal
from llm_capacity_pricing.schemas.plan import (
    PlanAdvanced,
    PlanRequest,
    PlanResourceAssumptions,
    PlanWorkload,
)
from llm_capacity_pricing.schemas.performance import SlaRequirement
from llm_capacity_pricing.workflow import evaluate_plan

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="data/exports/ai_rtx3090/rtx3090.db")
    parser.add_argument("--model-revision", required=True)
    parser.add_argument("--out-root", default="docs/aj_scenario_e")
    args = parser.parse_args()
    recommendation = json.loads(
        (ROOT / "docs/aj_capacity_recommendation.json").read_text(encoding="utf-8")
    )
    if recommendation.get("recommended_mode") != "fixed_concurrency":
        raise SystemExit(
            "当前没有可用的正式固定并发推荐；不能绕过容量证据生成场景 E"
        )
    scenario_workload = {"input_len": 2048, "output_len": 256}
    if recommendation.get("recommended_workload") != scenario_workload:
        raise SystemExit(
            "推荐工作点与场景 E 不一致："
            f"{recommendation.get('recommended_workload')} != {scenario_workload}"
        )
    identity = recommendation.get("benchmark_identity") or {}
    if identity.get("model_revision") != args.model_revision:
        raise SystemExit(
            "推荐证据的模型 revision 与场景请求不一致："
            f"{identity.get('model_revision')} != {args.model_revision}"
        )
    if identity.get("model_name") != "Qwen/Qwen3-8B":
        raise SystemExit(f"推荐证据的模型不是 Qwen/Qwen3-8B：{identity.get('model_name')}")
    if identity.get("hardware_id") != "nvidia-rtx-3090-24gb":
        raise SystemExit(f"推荐证据的硬件不是 RTX 3090：{identity.get('hardware_id')}")
    concurrency = int(recommendation["recommended_value"])
    runtime_sha = recommendation.get("runtime_config_sha256")
    if not runtime_sha:
        raise SystemExit("容量推荐没有绑定冻结运行配置，不能生成场景 E")
    matched_run = None
    for directory in sorted((ROOT / "data/benchmark/rtx3090").glob("*")):
        meta_path = directory / "metadata.yaml"
        if not meta_path.is_file():
            continue
        import yaml

        meta = yaml.safe_load(meta_path.read_text(encoding="utf-8"))
        if (
            meta.get("runtime_config_sha256") == runtime_sha
            and meta.get("configured_max_concurrency") == concurrency
            and meta.get("input_len") == 2048
            and meta.get("output_len") == 256
        ):
            matched_run = directory.name
            break
    if matched_run is None:
        raise SystemExit(
            "推荐配置哈希下没有对应的 2048/256 正式运行，不能生成场景 E"
        )
    request = PlanRequest(
        request_id="aj-scenario-e-rtx3090",
        model_id="qwen3-8b",
        hardware_ids=["nvidia-rtx-3090-24gb"],
        server_id="compute01-rtx3090-1gpu",
        max_total_gpus=1,
        workload=PlanWorkload(
            input_len=2048,
            output_len=256,
            concurrency=concurrency,
            dataset="random",
            arrival_mode="max_concurrency",
        ),
        resources=PlanResourceAssumptions(
            usable_fraction=Decimal("0.9"),
            usable_source_kind="operator_estimate",
            runtime_reserve_fraction=Decimal("0.1"),
        ),
        sla=SlaRequirement(
            requirement_id="aj-3090-sla",
            ttft_p99_ms_max=Decimal("6000"),
            tpot_p99_ms_max=Decimal("40"),
        ),
        advanced=PlanAdvanced(
            tp_sizes=[1],
            dp_sizes=[1],
            weight_precision="bf16",
            kv_precision="bf16",
            model_revision=args.model_revision,
            framework="vllm",
            framework_version="0.11.0",
            runtime_config_sha256=runtime_sha,
        ),
    )
    result = evaluate_plan(request, db_path=ROOT / args.db)
    result.capacity_evidence = recommendation
    result.decision_evidence = build_decision_evidence(result)
    out_dir = ROOT / args.out_root
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "plan.json").write_text(
        json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2, default=str)
        + "\n",
        encoding="utf-8",
    )
    (out_dir / "technical_report.md").write_text(
        build_plan_report(result), encoding="utf-8"
    )
    (out_dir / "deployment_proposal.md").write_text(
        build_deployment_proposal(result), encoding="utf-8"
    )
    print(json.dumps({
        "status": result.status,
        "concurrency": concurrency,
        "capacity": recommendation,
    }, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
