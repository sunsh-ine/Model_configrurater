"""M23：SLA 评估引擎。

判定纪律（任务书 M23）：

* **逐项评估**：每个约束独立给出状态与依据，整体状态由逐项结果合成；
* **先过门禁再比数值**：匹配等级、数据可信等级、指标口径三者任一不满足，
  一律 ``not_evaluable``，**不看数值**；
* **实测 ≠ 预测**：``estimated_only`` 永远不算达标；
* **请求级 Goodput 只能由逐请求明细算**：不允许用几个聚合 P99 组合反推；
* **样本量与统计窗口随结论一起给出**。

状态语义：

====================  ==========================================================
``observed_pass``     有匹配实测且**全部**约束通过
``observed_fail``     有匹配实测且**至少一项**明确超标
``estimated_only``    只有估算/预测，无实测验证
``not_evaluable``     缺关键指标、配置不匹配或数据不可信
====================  ==========================================================
"""

from __future__ import annotations

from decimal import Decimal
from typing import Iterable

from llm_capacity_pricing.schemas.performance import (
    BenchmarkMatchResult,
    BenchmarkRun,
    DataTrust,
    PerformanceEstimate,
    SlaConstraintResult,
    SlaEvaluation,
    SlaPolicy,
    SlaRequirement,
    SlaStatus,
)

LEVEL_ORDER: dict[str, int] = {
    "exact_match": 0,
    "comparable_reference": 1,
    "modeling_candidate": 2,
    "rejected": 3,
}

#: 约束名 -> (profile 字段, 方向, 单位, 口径 ID)
CONSTRAINT_SPEC: dict[str, tuple[str, str, str, str | None]] = {
    "ttft_p99_ms": ("ttft_p99_ms", "max", "ms", "m.latency.ttft.p99"),
    "tpot_p99_ms": ("tpot_p99_ms", "max", "ms", "m.latency.tpot.p99"),
    "itl_p99_ms": ("itl_p99_ms", "max", "ms", "m.latency.itl.p99"),
    "e2e_p99_ms": ("e2e_p99_ms", "max", "ms", "m.latency.e2e.p99"),
    "success_rate": ("success_rate", "min", "ratio", None),
    "request_throughput_rps": ("request_throughput_rps", "min", "req/s", None),
    "output_throughput_tok_s": ("output_throughput_tok_s", "min", "tok/s", None),
}


def _observed_value(constraint: str, run: BenchmarkRun) -> Decimal | None:
    prof = run.profile
    if constraint == "success_rate":
        if prof.success_count is None or prof.num_requests in (None, 0):
            return None
        return Decimal(prof.success_count) / Decimal(prof.num_requests)
    field_name = CONSTRAINT_SPEC[constraint][0]
    return getattr(prof, field_name, None)


def _request_level_goodput(run: BenchmarkRun, requirement: SlaRequirement) -> tuple[Decimal | None, str]:
    """请求级 Goodput：只有逐请求明细才能算（不允许用聚合 P99 组合反推）。"""
    if not run.has_request_metrics:
        return None, (
            "request_metrics_required_for_goodput：缺少逐请求明细，"
            "不得用聚合 P99 组合反推请求级 Goodput"
        )
    ttft = requirement.ttft_p99_ms_max
    tpot = requirement.tpot_p99_ms_max
    e2e = requirement.e2e_p99_ms_max
    ok = 0
    total = 0
    for metric in run.request_metrics:
        total += 1
        if metric.success is False:
            continue
        if ttft is not None and (metric.ttft_ms is None or metric.ttft_ms > ttft):
            continue
        if tpot is not None and (metric.tpot_ms is None or metric.tpot_ms > tpot):
            continue
        if e2e is not None and (metric.e2e_ms is None or metric.e2e_ms > e2e):
            continue
        ok += 1
    if total == 0:
        return None, "request_metrics_empty"
    duration = run.profile.duration_s
    if duration in (None, 0):
        return None, "duration_missing_cannot_compute_goodput_rate"
    goodput = (Decimal(ok) / Decimal(duration)).quantize(Decimal("0.000001"))
    return goodput, (
        f"按逐请求明细计算：{ok}/{total} 条请求同时满足全部请求级约束，"
        f"除以测试时长 {duration}s"
    )


def evaluate_sla(
    requirement: SlaRequirement,
    *,
    policy: SlaPolicy,
    run: BenchmarkRun | None = None,
    match_result: BenchmarkMatchResult | None = None,
    estimate: PerformanceEstimate | None = None,
    target_ref: str = "target",
) -> SlaEvaluation:
    """评估一个需求。``run`` 为 None 时只能给出 estimated_only / not_evaluable。"""
    constraints = requirement.constraints()
    if not constraints and not requirement.require_goodput:
        return SlaEvaluation(
            requirement_id=requirement.requirement_id,
            target_ref=target_ref,
            status="not_evaluable",
            policy_id=policy.policy_id,
            reasons=["no_constraints_defined: 需求未给出任何 SLA 约束"],
        )

    # ---- 门禁 1：没有实测数据 --------------------------------------------
    if run is None:
        if estimate is not None and estimate.estimate_kind in (
            "measured_reference",
            "calibrated_estimate",
        ) and estimate.value is not None:
            return SlaEvaluation(
                requirement_id=requirement.requirement_id,
                target_ref=target_ref,
                status="estimated_only",
                policy_id=policy.policy_id,
                constraints=[
                    SlaConstraintResult(
                        constraint=name,
                        threshold=threshold,
                        observed=estimate.value,
                        unit=CONSTRAINT_SPEC[name][2],
                        status="estimated_only",
                        reason=f"来自估算（{estimate.estimate_kind}），未经实测验证",
                    )
                    for name, threshold in constraints.items()
                ],
                reasons=[
                    "no_measured_run: 只有估算结果",
                    "estimated_only_is_not_verified",
                ],
                goodput_note="无实测数据，不计算 Goodput",
            )
        return SlaEvaluation(
            requirement_id=requirement.requirement_id,
            target_ref=target_ref,
            status="not_evaluable",
            policy_id=policy.policy_id,
            reasons=["no_measured_run: 没有可用的实测记录"],
        )

    # ---- 门禁 2：匹配等级 / 数据可信等级 ---------------------------------
    reasons: list[str] = []
    if match_result is None:
        # SLA 结论必须挂在一次明确的匹配判定上，否则"这条实测是否适用于该需求"
        # 无从追溯 —— 直接不可评估（不给"跳过匹配检查"的后门）。
        return SlaEvaluation(
            requirement_id=requirement.requirement_id,
            target_ref=target_ref,
            status="not_evaluable",
            policy_id=policy.policy_id,
            matched_run_id=run.run_id,
            data_trust=run.data_trust,
            reasons=[
                "match_result_required_for_sla_proof: 未提供匹配判定，"
                "无法确认该实测数据是否适用于目标配置与负载"
            ],
        )
    if match_result is not None:
        if LEVEL_ORDER[match_result.level] > LEVEL_ORDER[policy.minimum_match_level]:
            return SlaEvaluation(
                requirement_id=requirement.requirement_id,
                target_ref=target_ref,
                status="not_evaluable",
                policy_id=policy.policy_id,
                matched_run_id=run.run_id,
                match_level=match_result.level,
                data_trust=run.data_trust,
                reasons=[
                    f"match_level_below_policy:{match_result.level}"
                    f"<{policy.minimum_match_level}",
                    "配置不匹配的实测数据不能作为该需求的 SLA 依据",
                ],
            )
    trust: DataTrust = run.data_trust
    accepted = set(policy.accepted_trust_levels)
    if trust == "synthetic_fixture" and policy.allow_synthetic:
        accepted.add("synthetic_fixture")
    if trust not in accepted:
        return SlaEvaluation(
            requirement_id=requirement.requirement_id,
            target_ref=target_ref,
            status="not_evaluable",
            policy_id=policy.policy_id,
            matched_run_id=run.run_id,
            match_level=match_result.level if match_result else None,
            data_trust=trust,
            reasons=[
                f"data_trust_not_accepted:{trust}",
                "synthetic/历史无时延数据不得用于 SLA 结论（synthetic 仅可验证算法）",
            ],
        )

    # ---- 逐项评估 --------------------------------------------------------
    results: list[SlaConstraintResult] = []
    for name, threshold in constraints.items():
        observed = _observed_value(name, run)
        unit = CONSTRAINT_SPEC[name][2]
        if observed is None:
            results.append(
                SlaConstraintResult(
                    constraint=name,
                    threshold=threshold,
                    observed=None,
                    unit=unit,
                    status="not_evaluable",
                    reason=f"metric_not_available:{name}（记录中缺少该指标的实测值）",
                )
            )
            continue
        direction = CONSTRAINT_SPEC[name][1]
        passed = observed <= threshold if direction == "max" else observed >= threshold
        results.append(
            SlaConstraintResult(
                constraint=name,
                threshold=threshold,
                observed=observed,
                unit=unit,
                status="observed_pass" if passed else "observed_fail",
                reason=None
                if passed
                else (
                    f"observed {observed}{unit} "
                    f"{'超过上限' if direction == 'max' else '低于下限'} {threshold}{unit}"
                ),
            )
        )

    # ---- Goodput（可选，需逐请求明细）------------------------------------
    goodput: Decimal | None = None
    goodput_note: str | None = None
    if requirement.require_goodput:
        goodput, goodput_note = _request_level_goodput(run, requirement)

    # ---- 合成整体状态 ----------------------------------------------------
    statuses = {r.status for r in results}
    if "observed_fail" in statuses:
        status: SlaStatus = "observed_fail"
        reasons.append("at_least_one_constraint_failed_in_measurement")
    elif results and statuses == {"observed_pass"}:
        status = "observed_pass"
        reasons.append("all_constraints_passed_in_measurement")
    else:
        status = "not_evaluable"
        missing = [r.constraint for r in results if r.status == "not_evaluable"]
        reasons.append(f"insufficient_evidence_for_constraints:{missing}")

    if requirement.require_goodput and goodput is None:
        if status == "observed_pass":
            status = "not_evaluable"
        reasons.append(f"goodput_not_computable:{goodput_note}")

    return SlaEvaluation(
        requirement_id=requirement.requirement_id,
        target_ref=target_ref,
        status=status,
        policy_id=policy.policy_id,
        # synthetic 数据下的结论只用于算法验证，必须显式打标（生产默认策略不会走到这里）
        algorithm_validation_only=(trust == "synthetic_fixture" and policy.allow_synthetic),
        matched_run_id=run.run_id,
        match_level=match_result.level if match_result else None,
        data_trust=trust,
        constraints=results,
        reasons=reasons,
        goodput_rps=goodput,
        goodput_note=goodput_note,
        sample_size=run.profile.num_requests,
        statistics_window=run.profile.statistics_window,
    )


def required_metric_ids(constraint_names: Iterable[str]) -> list[str]:
    """把 SLA 约束名（如 ``ttft_p99_ms``）映射成指标口径 ID（如 ``m.latency.ttft.p99``）。

    匹配时需要的是**口径 ID**，不是字段名——用错会导致所有记录都被判"缺指标"
    （V3.0 实现中修正过这个错误）。
    """
    ids: list[str] = []
    for name in constraint_names:
        spec = CONSTRAINT_SPEC.get(name)
        if spec and spec[3]:
            ids.append(spec[3])
    return sorted(set(ids))
