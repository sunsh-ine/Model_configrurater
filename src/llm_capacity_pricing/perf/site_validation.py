"""M39：现场性能与 SLA 可信度验证。

把已有的三个能力串成一次**可解释的验证**，面向"这批真实数据到底能证明什么"：

1. 容量分析（``capacity_analyzer``）—— 各并发点曲线、已观测峰值、可持续容量；
2. 配置与负载匹配（``benchmark_matcher``）—— 数据能不能代表目标配置；
3. SLA 判定（``sla_evaluator``）—— 逐子项判定与**不能评价的原因**。

本模块**不新增判定规则**：所有结论都来自上面三个领域的既有实现，
这里只负责组织输入、汇总结论，并把"为什么不能评价"写成可执行的原因清单。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Sequence

from llm_capacity_pricing.schemas.performance import (
    BenchmarkRun,
    SlaEvaluation,
    SlaPolicy,
    SlaRequirement,
    TargetRequirement,
)

__all__ = ["SitePerformanceValidation", "validate_site_performance"]


@dataclass(frozen=True)
class ValidationPoint:
    """一个并发点的性能证据。"""

    concurrency: int | None
    run_id: str
    ttft_p99_ms: Decimal | None
    tpot_p99_ms: Decimal | None
    output_throughput_tok_s: Decimal | None
    error_rate: Decimal | None
    duration_s: Decimal | None
    num_requests: int | None
    success_count: int | None
    #: 是否满足"可持续"的证据门槛（时长/失败率/样本）
    sustainable: bool
    #: 计划要求的单次时长门槛（来自 SustainabilityCriteria，与实验清单同源）
    required_duration_s: Decimal | None = None
    #: 实测时长是否达到计划要求
    duration_meets_requirement: bool | None = None
    reasons: list[str] = field(default_factory=list)


@dataclass
class SitePerformanceValidation:
    """一次现场验证的完整结论。"""

    # ---- 数据集身份 ----
    group_key: str
    run_count: int
    #: 参与 Run 的可信等级集合（**全部**列出，不取"最乐观的那个"）
    data_trusts: list[str] = field(default_factory=list)
    #: 可信等级唯一时给出该值；混合时保持 None（不能声称单一可信度）
    data_trust: str | None = None
    synthetic_included: bool = False

    # ---- 容量 ----
    points: list[ValidationPoint] = field(default_factory=list)
    observed_peak_throughput_tok_s: Decimal | None = None
    observed_peak_concurrency: int | None = None
    maximum_sustainable_capacity: int | None = None
    sustainability_reasons: list[str] = field(default_factory=list)

    # ---- 匹配（数据能不能代表目标）----
    match_level: str | None = None
    match_reasons: list[str] = field(default_factory=list)
    matched_run_id: str | None = None
    load_alignment: list[str] = field(default_factory=list)
    statistical_alignment: list[str] = field(default_factory=list)

    # ---- SLA ----
    sla: SlaEvaluation | None = None
    sla_subchecks: list[dict[str, Any]] = field(default_factory=list)

    # ---- 结论与"为什么不能评价" ----
    not_evaluable_reasons: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def sla_status(self) -> str:
        return self.sla.status if self.sla is not None else "not_evaluated"

    @property
    def can_claim_sla(self) -> bool:
        """能否把这条结论**对外当作实测依据**（M39 收紧）。

        必须在 `observed_pass` / `observed_fail` 之外同时满足：

        * 匹配等级是 `exact_match`（严格匹配）；
        * 参与 Run 的可信等级属于生产允许的测量类（复用
          ``PRODUCTION_TRUST_LEVELS``，不另立一套）；
        * 不含 synthetic 数据（算法验证结论不能冒充实测结论）。

        注意与报价门禁的分工：这里管的是**报告里的布尔值**，
        报价门禁另有自己的生产可信度检查；两者结论必须一致，不得相互放宽。
        """
        from llm_capacity_pricing.schemas.performance import PRODUCTION_TRUST_LEVELS

        if self.sla_status not in ("observed_pass", "observed_fail"):
            return False
        if self.synthetic_included:
            return False
        if self.match_level != "exact_match":
            return False
        if self.data_trust is None or self.data_trust not in PRODUCTION_TRUST_LEVELS:
            return False
        return True

    def to_dict(self) -> dict[str, Any]:
        return {
            "group_key": self.group_key,
            "run_count": self.run_count,
            "data_trust": self.data_trust,
            "data_trusts": self.data_trusts,
            "synthetic_included": self.synthetic_included,
            "points": [
                {
                    "concurrency": p.concurrency,
                    "run_id": p.run_id,
                    "ttft_p99_ms": str(p.ttft_p99_ms) if p.ttft_p99_ms is not None else None,
                    "tpot_p99_ms": str(p.tpot_p99_ms) if p.tpot_p99_ms is not None else None,
                    "output_throughput_tok_s": (
                        str(p.output_throughput_tok_s)
                        if p.output_throughput_tok_s is not None
                        else None
                    ),
                    "error_rate": str(p.error_rate) if p.error_rate is not None else None,
                    "duration_s": str(p.duration_s) if p.duration_s is not None else None,
                    "num_requests": p.num_requests,
                    "success_count": p.success_count,
                    "sustainable": p.sustainable,
                    "required_duration_s": (
                        str(p.required_duration_s)
                        if p.required_duration_s is not None
                        else None
                    ),
                    "duration_meets_requirement": p.duration_meets_requirement,
                    "reasons": p.reasons,
                }
                for p in self.points
            ],
            "observed_peak_throughput_tok_s": (
                str(self.observed_peak_throughput_tok_s)
                if self.observed_peak_throughput_tok_s is not None
                else None
            ),
            "observed_peak_concurrency": self.observed_peak_concurrency,
            "maximum_sustainable_capacity": self.maximum_sustainable_capacity,
            "sustainability_reasons": self.sustainability_reasons,
            "match_level": self.match_level,
            "match_reasons": self.match_reasons,
            "matched_run_id": self.matched_run_id,
            "load_alignment": self.load_alignment,
            "statistical_alignment": self.statistical_alignment,
            "sla_status": self.sla_status,
            "can_claim_sla": self.can_claim_sla,
            "sla_subchecks": self.sla_subchecks,
            "not_evaluable_reasons": self.not_evaluable_reasons,
            "notes": self.notes,
        }


def _statistical_alignment(runs: Sequence[BenchmarkRun]) -> list[str]:
    """统计口径声明情况（P99 是否有口径、客户端/服务端是否混用）。"""
    out: list[str] = []
    for run in runs:
        declared = set(run.profile.metric_definition_ids)
        missing = [
            f
            for f, d in (
                ("ttft_p99_ms", "m.latency.ttft.p99"),
                ("tpot_p99_ms", "m.latency.tpot.p99"),
            )
            if getattr(run.profile, f, None) is not None and d not in declared
        ]
        if missing:
            out.append(f"{run.run_id}: 分位指标缺少口径声明 {missing}（不得当作 P99 使用）")
        if run.profile.metric_side != "client":
            out.append(
                f"{run.run_id}: metric_side={run.profile.metric_side!r}，"
                "服务端口径不得与客户端口径互换"
            )
    if not out:
        out.append("所有参与 Run 的 P99 口径与指标侧声明一致（客户端口径）")
    return out


def _load_alignment(target: TargetRequirement, run: BenchmarkRun | None) -> list[str]:
    """负载与配置对齐情况（逐项列出目标值 vs 数据值）。"""
    if run is None:
        return ["没有可用于比对的实测记录"]
    sig = run.signature
    tw = target.workload
    rows = (
        ("模型", target.model_name, sig.model_name),
        ("模型版本", target.model_revision, sig.model_revision),
        ("权重精度", target.weight_precision, sig.weight_precision),
        ("硬件", target.hardware_model, sig.hardware_model),
        ("卡数", target.gpu_count, sig.gpu_count),
        ("TP", target.tp_size, sig.tp_size),
        ("DP", target.dp_size, sig.dp_size),
        ("框架", target.framework, sig.framework),
        ("框架版本", target.framework_version, sig.framework_version),
        ("输入长度", tw.input_len_target, sig.workload.input_len_target),
        ("输出长度", tw.output_len_target, sig.workload.output_len_target),
        ("数据集", tw.dataset_name, sig.workload.dataset_name),
        ("到达方式", tw.arrival_mode, sig.workload.arrival_mode),
        ("配置并发", tw.configured_concurrency, sig.workload.configured_concurrency),
    )
    out: list[str] = []
    for label, want, got in rows:
        if want is None:
            out.append(f"{label}: 目标未给出")
        elif got is None:
            out.append(f"{label}: 数据缺失（目标 {want}）")
        elif want != got:
            out.append(f"{label}: 不一致（目标 {want} ≠ 数据 {got}）")
    if not out:
        out.append("部署身份与负载条件逐项一致")
    return out


def _point_from_run(run: BenchmarkRun, *, criteria: Any) -> ValidationPoint:
    profile = run.profile
    reasons: list[str] = []
    if profile.duration_s is None:
        reasons.append("未记录测试时长：无法判断持续性")
    elif profile.duration_s < criteria.min_duration_s:
        reasons.append(
            f"测试时长 {profile.duration_s}s 低于门槛 {criteria.min_duration_s}s"
        )
    if profile.error_rate is None:
        reasons.append("未记录失败率：无法判断稳定性")
    elif profile.error_rate > criteria.max_error_rate:
        reasons.append(
            f"失败率 {profile.error_rate} 高于门槛 {criteria.max_error_rate}"
        )
    if profile.success_count is None:
        reasons.append("未记录成功请求数：样本代表性无法确认")
    meets = (
        profile.duration_s >= criteria.min_duration_s
        if profile.duration_s is not None
        else None
    )
    return ValidationPoint(
        concurrency=profile.configured_concurrency,
        run_id=run.run_id,
        required_duration_s=criteria.min_duration_s,
        duration_meets_requirement=meets,
        ttft_p99_ms=profile.ttft_p99_ms,
        tpot_p99_ms=profile.tpot_p99_ms,
        output_throughput_tok_s=profile.output_throughput_tok_s,
        error_rate=profile.error_rate,
        duration_s=profile.duration_s,
        num_requests=profile.num_requests,
        success_count=profile.success_count,
        sustainable=not reasons,
        reasons=reasons,
    )


def validate_site_performance(
    *,
    runs: Sequence[BenchmarkRun],
    target: TargetRequirement,
    requirement: SlaRequirement,
    policy: SlaPolicy | None = None,
) -> SitePerformanceValidation:
    """对一组**同一配置**的现场实测做完整验证。"""
    from llm_capacity_pricing.perf.benchmark_matcher import (
        match,
        signature_from_run,
    )
    from llm_capacity_pricing.perf.capacity_analyzer import (
        SustainabilityCriteria,
        analyze_group,
        grouping_key,
    )
    from llm_capacity_pricing.perf.ranker import _best_match
    from llm_capacity_pricing.perf.sla_evaluator import evaluate_sla as _evaluate

    if not runs:
        raise ValueError("validate_site_performance 需要至少一条实测")

    policy = policy or SlaPolicy()
    criteria = SustainabilityCriteria()
    group_key = grouping_key(runs[0])

    trusts = sorted({r.data_trust for r in runs})
    validation = SitePerformanceValidation(
        group_key=group_key,
        run_count=len(runs),
        data_trusts=trusts,
        data_trust=trusts[0] if len(trusts) == 1 else None,
        synthetic_included="synthetic_fixture" in trusts,
    )

    # ---- 1. 容量：曲线、峰值、可持续容量 ----
    try:
        profile = analyze_group(list(runs), criteria=criteria)
        validation.points = [_point_from_run(r, criteria=criteria) for r in runs]
        if profile.best_observed_point is not None:
            validation.observed_peak_throughput_tok_s = (
                profile.best_observed_point.output_throughput_tok_s
            )
            validation.observed_peak_concurrency = (
                profile.best_observed_point.configured_concurrency
            )
        validation.maximum_sustainable_capacity = profile.maximum_sustainable_capacity
        validation.sustainability_reasons = list(profile.notes)
        if profile.maximum_sustainable_capacity is None:
            validation.sustainability_reasons.append(
                "未能认定可持续容量：需要满足时长/失败率门槛的**重复观测**，"
                "单次观测不足以作为可持续产能"
            )
    except ValueError as exc:
        validation.not_evaluable_reasons.append(f"容量分析无法执行：{exc}")

    # ---- 2. 匹配：这批数据能不能代表目标配置 ----
    best_run, best_match = _best_match(target, list(runs))
    if best_match is None:
        validation.not_evaluable_reasons.append(
            "没有任何实测与该目标配置可比：无法评价性能"
        )
    else:
        validation.match_level = best_match.level
        validation.match_reasons = list(best_match.reasons)
        validation.matched_run_id = best_run.run_id if best_run else None
        if best_match.level != "exact_match":
            validation.not_evaluable_reasons.append(
                f"匹配等级为 {best_match.level}（非 exact_match）："
                "该结论只能用于参考，不能证明目标 SLA"
            )

    validation.load_alignment = _load_alignment(
        target, best_run if best_match is not None else None
    )
    validation.statistical_alignment = _statistical_alignment(runs)

    # ---- 3. SLA：逐子项判定 ----
    if best_run is not None and best_match is not None:
        evaluation = _evaluate(
            requirement,
            policy=policy,
            run=best_run,
            match_result=best_match,
            target_ref=target.model_name or "target",
        )
        validation.sla = evaluation
        validation.sla_subchecks = [
            {
                "constraint": c.constraint,
                "required": str(getattr(c, "required", "") or ""),
                "observed": str(getattr(c, "observed", "") or ""),
                "status": c.status,
                "reason": getattr(c, "reason", None),
            }
            for c in evaluation.constraints
        ]
        if evaluation.status not in ("observed_pass", "observed_fail"):
            validation.not_evaluable_reasons.extend(evaluation.reasons)
    else:
        validation.not_evaluable_reasons.append("没有匹配实测：SLA 无法评估")

    if validation.synthetic_included:
        validation.not_evaluable_reasons.append(
            "数据集中包含 synthetic 数据：即使算出结论也只能用于算法验证，"
            "不能替代真实依据"
        )

    if any(p.duration_meets_requirement is False for p in validation.points):
        validation.not_evaluable_reasons.append(
            "有实测的单次时长低于计划要求：不满足可持续容量的证据门槛"
        )
    notes = [
        "容量、匹配、SLA 三条结论相互独立：容量曲线好不代表配置匹配，"
        "配置匹配也不代表时延达标",
        f"单次时长门槛 {criteria.min_duration_s}s 与实验清单同源"
        "（清单里的 duration_s 是**计划值**，这里比对的是**实测值**）",
        "本验证只读：不修改任何记录的信任等级",
    ]
    if not validation.can_claim_sla:
        notes.append(
            "本次**不具备**可对外声称的 SLA 依据（需要 exact_match 的实测 + 齐全指标）"
        )
    validation.notes = notes
    return validation
