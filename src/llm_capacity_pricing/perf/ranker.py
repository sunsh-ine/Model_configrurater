"""M24：部署候选的性能比较与排序。

流程（任务书 M24）：

1. 先排除第二阶段静态不可行/不支持的方案；
2. 对剩余方案查找匹配的 Benchmark；
3. 执行 SLA 评估；
4. 区分"已验证满足"与"证据不足"；
5. **只在可比条件下**比较吞吐/延迟/资源规模；
6. 输出推荐理由、证据与未确认风险。

两条硬约束：

* **不跨配置比较吞吐**：不同 TP/DP 的候选，其吞吐来自不同的实测配置，
  本模块**不**用吞吐对它们排序（只在同一 config 组内展示证据值）；
  排序依据是"SLA 是否被实测满足 + 资源规模"。
* **未确认价格前不做经济性评分**：不使用第一阶段的理论满载营收比作为评分依据。
"""

from __future__ import annotations

from decimal import Decimal
from typing import Iterable, Mapping, Sequence

from llm_capacity_pricing.schemas.performance import (
    BenchmarkMatchResult,
    BenchmarkRun,
    DeploymentRanking,
    RankedDeployment,
    SlaEvaluation,
    SlaPolicy,
    SlaRequirement,
    TargetRequirement,
    WorkloadSignature,
)
from llm_capacity_pricing.schemas.sizing import DeploymentCandidate
from llm_capacity_pricing.perf.benchmark_matcher import LEVEL_ORDER, signature_from_run
from llm_capacity_pricing.perf.sla_evaluator import evaluate_sla, required_metric_ids

#: 桶顺序（越小越优先）
BUCKET_ORDER: dict[str, int] = {
    "verified_sla_met": 0,
    "measured_but_sla_unmet": 1,
    "requires_benchmark": 2,
    "excluded": 3,
}


def rank_deployments(
    *,
    requirement_id: str,
    candidates: Sequence[DeploymentCandidate],
    runs: Sequence[BenchmarkRun],
    sla_requirement: SlaRequirement,
    policy: SlaPolicy,
    workload: WorkloadSignature | None = None,
    model_by_id: Mapping[str, object] | None = None,
    target_identity: Mapping[str, object] | None = None,
) -> DeploymentRanking:
    """对第二阶段候选做性能比较。``runs`` 为可用的实测记录池。"""
    entries: list[RankedDeployment] = []
    notes: list[str] = []

    for candidate in candidates:
        static_status = candidate.feasibility.status
        if static_status in ("infeasible", "unsupported"):
            entries.append(
                RankedDeployment(
                    candidate_id=candidate.candidate_id,
                    model_id=candidate.model_id,
                    hardware_id=candidate.hardware_id,
                    tp_size=candidate.tp_size,
                    dp_size=candidate.dp_size,
                    gpu_count=candidate.gpu_count,
                    static_status=static_status,
                    headroom_bytes=candidate.headroom_bytes,
                    rank_bucket="excluded",
                    reasons=[f"static_{static_status}: {candidate.feasibility.reasons[:1]}"],
                )
            )
            continue

        identity = dict(target_identity or {})
        target = TargetRequirement(
            model_name=_model_name(candidate, model_by_id),
            model_revision=identity.get("model_revision"),  # type: ignore[arg-type]
            framework=identity.get("framework"),  # type: ignore[arg-type]
            framework_version=identity.get("framework_version"),  # type: ignore[arg-type]
            hardware_model=_hardware_name(candidate),
            gpu_count=candidate.gpu_count,
            tp_size=candidate.tp_size,
            dp_size=candidate.dp_size,
            weight_precision=candidate.weight_precision,
            workload=workload or WorkloadSignature(
                configured_concurrency=candidate.global_concurrency
            ),
            # 必须是**口径 ID**（m.latency.* ），不是 SLA 字段名
            required_metrics=required_metric_ids(sla_requirement.constraints()),
            require_measured=True,
        )

        best_run, best_match = _best_match(target, runs)
        risks = list(candidate.feasibility.risk_flags) + list(
            candidate.feasibility.unverified_conditions
        )
        if static_status == "insufficient_information":
            risks.append("static_feasibility_insufficient_information")

        # 先定桶与理由，再构造（rank_bucket 是必填字段）
        bucket: str = "requires_benchmark"
        reasons: list[str] = []
        sla_status = None
        match_level = None
        matched_run_id = None
        out_tps = ttft = tpot = None

        if best_run is None or best_match is None:
            reasons.append(
                "no_benchmark_found_for_configuration: 没有可用于该配置的实测记录"
            )
        else:
            match_level = best_match.level
            matched_run_id = best_run.run_id
            out_tps = best_run.profile.output_throughput_tok_s
            ttft = best_run.profile.ttft_p99_ms
            tpot = best_run.profile.tpot_p99_ms
            sla: SlaEvaluation = evaluate_sla(
                sla_requirement,
                policy=policy,
                run=best_run,
                match_result=best_match,
                target_ref=candidate.candidate_id,
            )
            sla_status = sla.status
            if sla.status == "observed_pass":
                bucket = "verified_sla_met"
                reasons.append("matched_measurement_meets_sla")
            elif sla.status == "observed_fail":
                bucket = "measured_but_sla_unmet"
                reasons.extend(
                    f"constraint_failed:{c.constraint}"
                    for c in sla.constraints
                    if c.status == "observed_fail"
                )
            else:
                reasons.append(f"sla_{sla.status}: {sla.reasons[:1]}")

        entries.append(
            RankedDeployment(
                candidate_id=candidate.candidate_id,
                model_id=candidate.model_id,
                hardware_id=candidate.hardware_id,
                tp_size=candidate.tp_size,
                dp_size=candidate.dp_size,
                gpu_count=candidate.gpu_count,
                static_status=static_status,
                headroom_bytes=candidate.headroom_bytes,
                match_level=match_level,
                matched_run_id=matched_run_id,
                sla_status=sla_status,
                output_throughput_tok_s=out_tps,
                ttft_p99_ms=ttft,
                tpot_p99_ms=tpot,
                rank_bucket=bucket,  # type: ignore[arg-type]
                reasons=reasons,
                unverified_risks=risks,
            )
        )

    # ---- 排序 ------------------------------------------------------------
    entries.sort(
        key=lambda e: (
            BUCKET_ORDER[e.rank_bucket],
            e.gpu_count,
            -(e.headroom_bytes or 0),
            e.tp_size,
            e.dp_size,
        )
    )

    verified = [e for e in entries if e.rank_bucket == "verified_sla_met"]
    others = [e for e in entries if e.rank_bucket != "verified_sla_met"]
    notes.append(
        "排序依据：先按 'SLA 是否被匹配实测满足'，再按 GPU 数量与静态余量；"
        "**不以吞吐跨配置排序**（不同 TP/DP 的吞吐来自不同实测配置，不可直接比较）"
    )
    if not verified:
        notes.append(
            "没有任何候选具备匹配实测：全部只能列为 requires_benchmark，"
            "这属于**预期的证据缺口**，不是开发失败（任务书 M25 验收口径）"
        )
    if len({(e.tp_size, e.dp_size) for e in entries}) > 1:
        notes.append(
            "候选之间存在不同 TP/DP 组合：其吞吐/时延数值仅作证据展示，"
            "不用于跨配置排序"
        )
    notes.append("未使用经济性评分（币种与成本范围未确认前禁止）")
    aggregated = [e for e in entries if (e.matched_run_id or "").startswith("aggregate:")]
    if aggregated:
        notes.append(
            f"有 {len(aggregated)} 个候选使用**多次实测的保守聚合**作为 SLA 证据"
            f"（{AGGREGATE_NOTE}）"
        )

    blocker = None
    if not verified and others:
        blocker = (
            "requires_benchmark: 所有静态可行候选都缺少匹配的目标硬件实测数据"
            "（需按 docs/phase3_final_validation.md 的实验矩阵补测）"
        )

    return DeploymentRanking(
        requirement_id=requirement_id,
        entries=entries,
        comparability_notes=notes,
        blocker=blocker,
        economic_scoring_used=False,
    )


def matched_level_name(order: int) -> str:
    for name, value in LEVEL_ORDER.items():
        if value == order:
            return name
    return "unknown"


def _model_name(candidate: DeploymentCandidate, model_by_id: Mapping[str, object] | None) -> str | None:
    if model_by_id and candidate.model_id in model_by_id:
        spec = model_by_id[candidate.model_id]
        repo = getattr(spec, "repo_id", None)
        if repo:
            return repo
    return candidate.model_id


def _hardware_name(candidate: DeploymentCandidate) -> str | None:
    """把硬件 ID 映射成签名里的商品名（``nvidia-h100-sxm-80gb`` -> ``NVIDIA H100 SXM``）。"""
    mapping = {
        "nvidia-h100-sxm-80gb": "NVIDIA H100 SXM",
        "nvidia-h200-sxm-141gb": "NVIDIA H200 SXM",
        "nvidia-h100-nvl-94gb": "NVIDIA H100 NVL",
        "nvidia-b200-sxm": "NVIDIA B200 SXM",
        "ascend-910c": "Ascend 910C",
    }
    return mapping.get(candidate.hardware_id, candidate.hardware_id)


#: 聚合多次实测时使用的保守规则说明
AGGREGATE_NOTE = (
    "同一配置存在多次实测：按**保守口径**聚合（P99 取最大、吞吐取最小、可信等级取最弱），"
    "不使用单次最优值作为 SLA 证据"
)


def _aggregate_runs(runs: Sequence[BenchmarkRun], level: str) -> BenchmarkRun:
    """把同一等级的多条实测聚合成一条保守代表（V3.1 R6）。

    避免"任取第一条"——同一配置的多次实测可能存在性能波动，
    取最优值会让 SLA 结论偏乐观。
    """
    base = runs[0]
    profile = base.profile
    maxes = {
        field: max(
            (getattr(r.profile, field) for r in runs if getattr(r.profile, field) is not None),
            default=None,
        )
        for field in ("ttft_p99_ms", "tpot_p99_ms", "itl_p99_ms", "e2e_p99_ms")
    }
    mins = {
        field: min(
            (getattr(r.profile, field) for r in runs if getattr(r.profile, field) is not None),
            default=None,
        )
        for field in ("output_throughput_tok_s", "request_throughput_rps")
    }
    error_rates = [r.profile.error_rate for r in runs if r.profile.error_rate is not None]
    trust_rank = {
        "measured_internal": 0,
        "measured_vendor_published": 1,
        "measured_third_party": 2,
        "historical_external_no_latency": 3,
        "synthetic_fixture": 4,
        "unknown": 5,
    }
    weakest = max(runs, key=lambda r: trust_rank.get(r.data_trust, 5)).data_trust
    metric_ids = sorted({mid for r in runs for mid in r.profile.metric_definition_ids})
    aggregated_profile = profile.model_copy(
        update={
            **maxes,
            **mins,
            "error_rate": max(error_rates) if error_rates else None,
            "num_requests": sum(r.profile.num_requests or 0 for r in runs) or None,
            "success_count": sum(r.profile.success_count or 0 for r in runs) or None,
            "statistics_window": f"aggregate_of_{len(runs)}_runs",
            "metric_definition_ids": metric_ids,
            "quality_flags": sorted(
                set(profile.quality_flags)
                | {"aggregate_of_multiple_measurements", "conservative_worst_case"}
            ),
            "consistency_notes": list(profile.consistency_notes) + [AGGREGATE_NOTE],
        }
    )
    return base.model_copy(
        update={
            "run_id": "aggregate:" + ",".join(r.run_id for r in runs),
            "profile": aggregated_profile,
            "data_trust": weakest,
            "quality_flags": sorted(set(base.quality_flags) | {"aggregate_of_multiple_measurements"}),
            "notes": AGGREGATE_NOTE,
        }
    )


def _best_match(
    target: TargetRequirement, runs: Sequence[BenchmarkRun]
) -> tuple[BenchmarkRun | None, BenchmarkMatchResult | None]:
    """找匹配等级最高的一组实测；同一等级有多次时**保守聚合**（V3.1 R6）。"""
    from llm_capacity_pricing.perf.benchmark_matcher import match

    matched: list[tuple[BenchmarkRun, BenchmarkMatchResult]] = []
    for run in runs:
        result = match(target, signature_from_run(run))
        if result.level == "rejected":
            continue
        matched.append((run, result))
    if not matched:
        return None, None
    best_level = min(LEVEL_ORDER[r.level] for _run, r in matched)
    best = [(run, r) for run, r in matched if LEVEL_ORDER[r.level] == best_level]
    if len(best) == 1:
        return best[0]
    aggregated = _aggregate_runs([run for run, _r in best], matched_level_name(best_level))
    return aggregated, best[0][1]
