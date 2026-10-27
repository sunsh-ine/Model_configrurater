"""AH3/AH5：统一决策证据结构。

只从 ``PlanResult`` 已有字段派生，不重新计算 SLA、成本或匹配等级。
CLI、JSON、Markdown 与 UI 应消费同一份结构。
"""

from __future__ import annotations

from typing import Any

from llm_capacity_pricing.schemas.plan import PlanResult


def _candidate_entry(candidate: Any, simulation: Any) -> dict[str, Any]:
    repeatability = None
    if getattr(candidate, "repeatability_status", None):
        repeatability = {
            "status": candidate.repeatability_status,
            "runs": candidate.repeatability_runs,
            "pass_count": candidate.repeatability_pass_count,
            "experiment_completeness": getattr(
                candidate, "experiment_completeness", None
            ),
            "expected_runs": getattr(candidate, "experiment_expected_runs", None),
            "found_runs": getattr(candidate, "experiment_found_runs", None),
            "valid_runs": getattr(candidate, "experiment_valid_runs", None),
        }
    return {
        "candidate_id": candidate.candidate_id,
        "hardware_id": candidate.hardware_id,
        "gpu_count": candidate.gpu_count,
        "tp_size": candidate.tp_size,
        "dp_size": candidate.dp_size,
        "performance_status": candidate.performance_status,
        "sla_status": candidate.sla_status,
        "match_level": candidate.match_level,
        "rank_bucket": candidate.rank_bucket,
        "p99": {
            "ttft_ms": candidate.ttft_p99_ms,
            "tpot_ms": candidate.tpot_p99_ms,
        },
        "throughput_tok_s": candidate.output_throughput_tok_s,
        "sla_margin": {
            "ttft_ms": getattr(candidate, "sla_margin_ttft_ms", None),
            "tpot_ms": getattr(candidate, "sla_margin_tpot_ms", None),
            "sensitive_10pct": getattr(candidate, "sensitive_10pct", None),
            "sensitive_20pct": getattr(candidate, "sensitive_20pct", None),
        },
        "evidence": {
            "type": (
                "measured"
                if candidate.sla_status in ("observed_pass", "observed_fail")
                else "benchmark_reference"
                if candidate.evidence_run_ids
                else "estimated"
                if candidate.sla_status == "estimated_only"
                else "unknown"
            ),
            "run_ids": list(candidate.evidence_run_ids),
            "experiment_completeness": getattr(
                candidate, "experiment_completeness", None
            ),
            "calibration_status": getattr(candidate, "calibration_status", "unknown"),
        },
        "repeatability": repeatability,
        "economics": {
            "status": candidate.economics_status,
            "total_cash_over_horizon": candidate.total_cash_over_horizon,
            "currency": candidate.currency,
            "cost_incomplete": candidate.cost_incomplete,
        },
        "governance": {
            "status": candidate.governance_status,
            "quote_publishable": candidate.quote_publishable,
            "blockers": list(candidate.quote_blockers),
        },
        "notes": list(candidate.notes),
    }


def build_decision_evidence(result: PlanResult) -> dict[str, Any]:
    simulation = result.simulation
    recommended = None
    best_effort = None
    min_gpu = None
    throughput_best = None
    requested_ranking = (
        getattr(simulation, "ranking", "throughput") if simulation else "throughput"
    )
    cost_ok, cost_reasons = (False, ["没有模拟结果"])
    if simulation is not None and simulation.status == "computed":
        cost_ok, cost_reasons = simulation.cost_ranking_available()
    effective_ranking = (
        "cost"
        if requested_ranking == "cost" and cost_ok
        else "throughput"
    )
    ranking = effective_ranking
    if simulation is not None and simulation.status == "computed":
        recommended = simulation.recommended(ranking)
        best_effort = simulation.best_effort()
        min_gpu = simulation.min_gpu_for_sla()
        passed = [
            c
            for c in simulation.eligible()
            if c.verdict == "predicted_pass"
        ]
        if passed:
            throughput_best = max(
                passed,
                key=lambda c: c.metrics.sim_output_throughput_tok_s or 0,
            )
        if cost_ok:
            costed = [
                c
                for c in passed
                if c.cost is not None
                and c.cost.total_cash_over_horizon is not None
            ]
            if costed:
                min_cost = min(
                    costed, key=lambda c: c.cost.total_cash_over_horizon
                )

    def ref(candidate: Any) -> dict[str, Any] | None:
        if candidate is None:
            return None
        return {
            "source": "simulated",
            "candidate_id": candidate.candidate_id,
            "gpu_count": candidate.deployment.gpu_count,
            "tp_size": candidate.deployment.tp_size,
            "dp_size": candidate.deployment.dp_size,
            "verdict": candidate.verdict,
            "experiment_completeness": getattr(
                candidate, "experiment_completeness", None
            ),
            "calibration_status": getattr(candidate, "calibration_status", "unknown"),
        }

    simulation_candidates = []
    if simulation is not None:
        for candidate in simulation.candidates:
            simulation_candidates.append(
                {
                    "candidate_id": candidate.candidate_id,
                    "gpu_count": candidate.deployment.gpu_count,
                    "tp_size": candidate.deployment.tp_size,
                    "dp_size": candidate.deployment.dp_size,
                    "verdict": candidate.verdict,
                    "p99_ttft_ms": candidate.metrics.sim_p99_ttft_ms,
                    "p99_tpot_ms": candidate.metrics.sim_p99_tpot_ms,
                    "throughput_tok_s": candidate.metrics.sim_output_throughput_tok_s,
                    "repeatability_status": candidate.repeatability_status,
                    "repeatability_runs": candidate.repeatability_runs,
                    "repeatability_pass_count": candidate.repeatability_pass_count,
                    "experiment_completeness": candidate.experiment_completeness,
                    "experiment_expected_runs": candidate.experiment_expected_runs,
                    "experiment_found_runs": candidate.experiment_found_runs,
                    "experiment_valid_runs": candidate.experiment_valid_runs,
                    "calibration_status": candidate.calibration_status,
                    "eligible": candidate in simulation.eligible(),
                }
            )
    measured = [
        c
        for c in result.candidates
        if c.sla_status in ("observed_pass", "observed_fail")
    ]
    measured_passed = [c for c in measured if c.sla_status == "observed_pass"]

    def measured_ref(candidate: Any) -> dict[str, Any]:
        return {
            "source": "measured",
            "candidate_id": candidate.candidate_id,
            "gpu_count": candidate.gpu_count,
            "tp_size": candidate.tp_size,
            "dp_size": candidate.dp_size,
            "verdict": candidate.sla_status,
            "experiment_completeness": getattr(
                candidate, "experiment_completeness", None
            ),
            "calibration_status": getattr(
                candidate, "calibration_status", "unknown"
            ),
        }

    recommend_measured = None
    min_gpu_measured = None
    throughput_measured = None
    min_cost_measured = None
    if measured_passed and not result.blockers and result.status != "blocked":
        measured_configs = {
            (c.tp_size, c.dp_size, c.gpu_count) for c in measured_passed
        }
        if len(measured_configs) > 1:
            # 不同部署配置的实测吞吐不可直接横向排序；只展示，不据此推荐。
            measured_passed_for_rank = []
        else:
            measured_passed_for_rank = measured_passed
        if effective_ranking == "cost":
            costed = [
                c
                for c in measured_passed_for_rank
                if c.total_cash_over_horizon is not None
                and c.currency
                and c.cost_basis
                and c.horizon_years is not None
                and not c.cost_incomplete
            ]
            cost_groups = {
                (c.currency, c.cost_basis, c.horizon_years) for c in costed
            }
            if (
                costed
                and len(cost_groups) == 1
                and len(costed) == len(measured_passed_for_rank)
            ):
                recommend_measured = measured_ref(
                    min(costed, key=lambda c: c.total_cash_over_horizon)
                )
        else:
            throughput_pool = [
                c
                for c in measured_passed_for_rank
                if c.output_throughput_tok_s is not None
            ]
            if len(throughput_pool) == 1:
                recommend_measured = measured_ref(throughput_pool[0])
        if measured_passed_for_rank:
            min_gpu_measured = measured_ref(
                min(measured_passed_for_rank, key=lambda c: c.gpu_count)
            )
        measured_costed = [
            c
            for c in measured_passed_for_rank
            if c.total_cash_over_horizon is not None
            and c.currency
            and c.cost_basis
            and c.horizon_years is not None
            and not c.cost_incomplete
        ]
        if (
            measured_costed
            and len(
                {
                    (c.currency, c.cost_basis, c.horizon_years)
                    for c in measured_costed
                }
            )
            == 1
            and len(measured_costed) == len(measured_passed_for_rank)
        ):
            min_cost_measured = measured_ref(
                min(measured_costed, key=lambda c: c.total_cash_over_horizon)
            )
        throughput_pool = [
            c
            for c in measured_passed_for_rank
            if c.output_throughput_tok_s is not None
        ]
        if len(throughput_pool) == 1:
            throughput_measured = measured_ref(throughput_pool[0])
    simulated = [
        c
        for c in (simulation.candidates if simulation else [])
        if c.verdict in ("predicted_pass", "predicted_fail")
    ]
    evidence = {
        "measured_candidates": [c.candidate_id for c in measured],
        "simulated_candidates": [c.candidate_id for c in simulated],
        "external_reference_count": len(result.external_references),
        "simulation_status": simulation.status if simulation else None,
    }
    return {
        "schema_version": "1.0",
        "objectives_are_independent": [
            "min_gpu",
            "max_throughput",
            "min_cost",
        ],
        "min_cost_available": cost_ok,
        "requested_ranking": requested_ranking,
        "effective_ranking": effective_ranking,
        "cost_ranking_available": cost_ok,
        "cost_ranking_reasons": list(cost_reasons),
        "ranking": ranking,
        "recommended": recommend_measured or ref(recommended),
        "recommendation_source": (
            "measured"
            if recommend_measured is not None
            else "simulated"
            if ref(recommended) is not None
            else None
        ),
        "simulated_recommendation": ref(recommended),
        "best_effort": ref(best_effort),
        "min_gpu_for_sla": min_gpu_measured or ref(min_gpu),
        "throughput_best": throughput_measured or ref(throughput_best),
        "min_cost_best": min_cost_measured or (ref(min_cost) if cost_ok else None),
        "candidates": [_candidate_entry(c, simulation) for c in result.candidates],
        "simulation_candidates": simulation_candidates,
        "evidence": evidence,
        "blockers": [
            {
                "domain": blocker.domain,
                "code": blocker.code,
                "message": blocker.message,
                "candidate_id": blocker.candidate_id,
            }
            for blocker in result.blockers
        ],
        "rules": [
            "模拟预测不是实测；predicted_* 不会变成 observed_*",
            "成本、性能与报价资格三者独立，不提供综合评分",
            "min_gpu / max_throughput / min_cost 是不同目标，不能混称‘最优’",
            "实验不完整或校准 unknown 时必须显式展示，不得省略",
        ],
    }
