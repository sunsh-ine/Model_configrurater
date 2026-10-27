#!/usr/bin/env python3
"""AI6：RTX 3090 真实实测场景 D（单卡 Qwen3-8B BF16）。"""

from __future__ import annotations

import argparse
import json
from decimal import Decimal
from pathlib import Path

from llm_capacity_pricing.app.demo_service import prepare_demo_db
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
    parser.add_argument("--db", default="data/exports/ai_rtx3090/scenario_d.db")
    parser.add_argument("--model-revision", required=True)
    parser.add_argument("--out-root", default="docs/ai_scenario_d")
    parser.add_argument("--allow-existing", action="store_true")
    args = parser.parse_args()
    db_path = ROOT / args.db
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        if not args.allow_existing:
            raise SystemExit(f"数据库已存在，拒绝覆盖：{db_path}")
    else:
        prepare_demo_db(db_path, include_public_benchmarks=False)

    request = PlanRequest(
        request_id="ai-scenario-d-rtx3090",
        model_id="qwen3-8b",
        hardware_ids=["nvidia-rtx-3090-24gb"],
        server_id="compute01-rtx3090-1gpu",
        max_total_gpus=1,
        workload=PlanWorkload(
            input_len=2048,
            output_len=256,
            concurrency=8,
            dataset="random",
            arrival_mode="max_concurrency",
        ),
        resources=PlanResourceAssumptions(
            usable_fraction=Decimal("0.9"),
            usable_source_kind="operator_estimate",
            runtime_reserve_fraction=Decimal("0.1"),
        ),
        sla=SlaRequirement(
            requirement_id="ai-3090-sla",
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
        ),
    )
    result = evaluate_plan(request, db_path=db_path)
    out_dir = ROOT / args.out_root
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "plan.json").write_text(
        json.dumps(
            result.model_dump(mode="json"), ensure_ascii=False, indent=2, default=str
        )
        + "\n",
        encoding="utf-8",
    )
    (out_dir / "technical_report.md").write_text(
        build_plan_report(result), encoding="utf-8"
    )
    (out_dir / "deployment_proposal.md").write_text(
        build_deployment_proposal(result), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "status": result.status,
                "domains": result.domains,
                "decision_evidence": result.decision_evidence,
            },
            ensure_ascii=False,
            default=str,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
