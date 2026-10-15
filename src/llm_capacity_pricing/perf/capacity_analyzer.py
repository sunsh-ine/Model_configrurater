"""M21：并发容量与性能曲线分析。

对**同一配置**下多个负载点做曲线分析。三条硬规则（任务书 M21）：

1. **不混配置**：分组键包含模型、硬件、TP/DP、框架版本与负载类型；
   不同硬件或不同并行部署的点不会进入同一条曲线。
2. **不混负载类型**：固定长度标准测试与多轮/前缀复用测试分开成组
   （后者的 KV 复用行为不同，混在一起会得出错误的吞吐曲线）。
3. **单点最优 ≠ 可持续容量**：只给 ``best_observed_point``；
   只有在具备持续时间、失败率与稳定性证据时才给出 ``maximum_sustainable_capacity``。

配置并发与观测并发分别保留，不互相顶替。
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable, Sequence

from llm_capacity_pricing.schemas.performance import (
    BenchmarkRun,
    CapacityPoint,
    CapacityProfile,
    LoadProfileKind,
)

#: 判断"可持续"的默认门槛（可由调用方覆盖）
DEFAULT_MIN_DURATION_S = Decimal("60")
DEFAULT_MAX_ERROR_RATE = Decimal("0.01")
#: 覆盖充分的判据：至少这么多负载点，且覆盖请求的目标并发
DEFAULT_MIN_POINTS = 3


@dataclass(frozen=True)
class SustainabilityCriteria:
    """可持续容量的判据。默认从严：不满足就只报 best_observed_point。"""

    min_duration_s: Decimal = DEFAULT_MIN_DURATION_S
    max_error_rate: Decimal = DEFAULT_MAX_ERROR_RATE
    min_points: int = DEFAULT_MIN_POINTS
    #: 同一并发点至少需要几次重复观测（V3.1 R5：单次测试不足以证明长期可持续）
    min_repeats_per_point: int = 2
    #: 该并发点还需满足的时延上限（可选；给出则必须实测满足）
    ttft_p99_ms_max: Decimal | None = None
    tpot_p99_ms_max: Decimal | None = None


def grouping_key(run: BenchmarkRun) -> str:
    """容量曲线的分组键：**同配置 + 同负载**才可比较（V3.1 R4）。

    除部署条件外，还必须包含 Workload 的关键字段——1K/1K 与 32K/256
    是两条不同的曲线，混在一起会得出错误的吞吐/时延关系。
    """
    sig = run.signature
    w = sig.workload
    parts = (
        # 部署与身份
        sig.model_name or "?",
        sig.model_revision or "?",
        sig.weight_precision or "?",
        sig.hardware_model or "?",
        f"tp{sig.tp_size if sig.tp_size is not None else '?'}",
        f"dp{sig.dp_size if sig.dp_size is not None else '?'}",
        f"gpus{sig.gpu_count if sig.gpu_count is not None else '?'}",
        sig.framework or "?",
        sig.framework_version or "?",
        run.load_profile_kind,
        # 负载（V3.1 R4）
        f"isl{w.input_len_target if w.input_len_target is not None else '?'}",
        f"osl{w.output_len_target if w.output_len_target is not None else '?'}",
        w.dataset_name or "?",
        w.arrival_mode,
        f"prefix_reuse{w.prefix_reuse}",
        f"streaming{w.streaming}",
    )
    return "|".join(str(p) for p in parts)


def _point(run: BenchmarkRun) -> CapacityPoint:
    sig, prof = run.signature, run.profile
    return CapacityPoint(
        run_id=run.run_id,
        configured_concurrency=prof.configured_concurrency,
        observed_concurrency=prof.observed_concurrency,
        request_rate_rps=prof.request_rate_rps,
        input_len_target=sig.workload.input_len_target,
        output_len_target=sig.workload.output_len_target,
        ttft_p99_ms=prof.ttft_p99_ms,
        tpot_p99_ms=prof.tpot_p99_ms,
        e2e_p99_ms=prof.e2e_p99_ms,
        output_throughput_tok_s=prof.output_throughput_tok_s,
        total_throughput_tok_s=prof.total_throughput_tok_s,
        error_rate=prof.error_rate,
        data_trust=run.data_trust,
    )


def analyze_group(
    runs: Sequence[BenchmarkRun],
    *,
    criteria: SustainabilityCriteria | None = None,
    target_concurrency: int | None = None,
    concurrency_grid: Sequence[int] | None = None,
) -> CapacityProfile:
    """对一组**同配置**的 run 做曲线分析。"""
    if not runs:
        raise ValueError("analyze_group 需要至少一个 run")
    criteria = criteria or SustainabilityCriteria()
    group_key = grouping_key(runs[0])
    mismatched = [r.run_id for r in runs if grouping_key(r) != group_key]
    if mismatched:
        raise ValueError(
            f"同一组内不允许混合不同配置或不同负载类型：{mismatched}"
            "（任务书 M21 要求按 BenchmarkSignature 分组）"
        )

    sig = runs[0].signature
    notes: list[str] = []
    flags: list[str] = []
    points = sorted(
        (_point(r) for r in runs),
        key=lambda p: (p.configured_concurrency if p.configured_concurrency is not None else 10**9),
    )

    # ---- 已覆盖 / 缺失的并发点 ------------------------------------------
    covered = sorted(
        p.configured_concurrency for p in points if p.configured_concurrency is not None
    )
    grid = sorted(concurrency_grid or ([1, 2, 4, 8, 16, 32] if target_concurrency is None else [1, 2, 4, 8, 16, 32, target_concurrency]))
    missing = [c for c in grid if c not in covered]
    if target_concurrency is not None and target_concurrency not in covered:
        missing.append(target_concurrency) if target_concurrency not in missing else None

    # ---- 观测最优吞吐点 --------------------------------------------------
    with_throughput = [p for p in points if p.output_throughput_tok_s is not None]
    best = max(with_throughput, key=lambda p: p.output_throughput_tok_s) if with_throughput else None

    # ---- 可持续容量（V3.1 R5：失败率必须已知且达标；单次测试不足以证明长期可持续）----
    sustainability: list[str] = []
    observed_stable: int | None = None
    max_sustainable: int | None = None
    run_by_id = {r.run_id: r for r in runs}
    repeats: dict[int, int] = {}
    for p in with_throughput:
        if p.configured_concurrency is not None:
            repeats[p.configured_concurrency] = repeats.get(p.configured_concurrency, 0) + 1

    def _passes_basic(point: CapacityPoint) -> tuple[bool, list[str]]:
        """单点证据：失败率**必须已知**且达标 + 持续时间达标 + 时延约束（若给定）。"""
        why: list[str] = []
        if point.error_rate is None:
            return False, ["失败率未知（不得当作通过）"]
        if point.error_rate > criteria.max_error_rate:
            why.append(f"失败率 {point.error_rate} > {criteria.max_error_rate}")
        duration = run_by_id[point.run_id].profile.duration_s
        if duration is None:
            why.append("测试时长未知")
        elif duration < criteria.min_duration_s:
            why.append(f"时长 {duration}s < {criteria.min_duration_s}s")
        if criteria.ttft_p99_ms_max is not None:
            if point.ttft_p99_ms is None or point.ttft_p99_ms > criteria.ttft_p99_ms_max:
                why.append("TTFT P99 未达标或缺失")
        if criteria.tpot_p99_ms_max is not None:
            if point.tpot_p99_ms is None or point.tpot_p99_ms > criteria.tpot_p99_ms_max:
                why.append("TPOT P99 未达标或缺失")
        return not why, why

    # 按并发点归组，逐点统计"达标 / 不达标"的独立观测（V3.1 复审 P1-1）
    # 关键点：**任一次观测不达标，该并发点就不能被认定为稳定** ——
    # 不能因为"总次数够了"就把通过的那次升级为可持续容量。
    by_concurrency: dict[int, list[CapacityPoint]] = {}
    for point in with_throughput:
        if point.configured_concurrency is None:
            continue
        by_concurrency.setdefault(point.configured_concurrency, []).append(point)

    for concurrency, observations in sorted(by_concurrency.items()):
        passing: list[CapacityPoint] = []
        failing: list[tuple[CapacityPoint, list[str]]] = []
        for point in observations:
            ok, why = _passes_basic(point)
            (passing if ok else failing).append(point if ok else (point, why))
        if failing:
            # 失败观测必须显式处理：该并发点整体不认定为稳定，
            # 并把失败观测列入说明（不静默忽略）
            for point, why in failing:
                flags.append(f"point_not_stable:c{concurrency}:{'；'.join(why)}")
            flags.append(f"concurrency_has_failing_observation:c{concurrency}")
            notes.append(
                f"并发 {concurrency} 的 {len(observations)} 次观测中 "
                f"{len(failing)} 次未达标 → 该并发点不计入稳定容量"
                f"（失败观测：{[p.run_id for p, _ in failing]}）"
            )
            continue
        # 全部观测达标：再要求"达标观测次数 ≥ 门槛"
        if len(passing) >= criteria.min_repeats_per_point:
            if max_sustainable is None or concurrency > max_sustainable:
                max_sustainable = concurrency
        elif observed_stable is None or concurrency > observed_stable:
            observed_stable = concurrency

    if max_sustainable is not None:
        sustainability.append(
            f"该并发点：全部 {criteria.min_repeats_per_point}+ 次观测均达标"
            f"（失败率已知且 ≤ {criteria.max_error_rate}、时长 ≥ {criteria.min_duration_s}s）"
        )
    elif observed_stable is not None:
        flags.append("sustainable_capacity_needs_repeats")
        notes.append(
            f"并发 {observed_stable} 单次观测满足失败率与时长要求，但重复次数不足 "
            f"{criteria.min_repeats_per_point}，仅记为 observed_stable_candidate"
        )

    coverage_status = (
        "sufficient"
        if len(points) >= criteria.min_points
        and (target_concurrency is None or target_concurrency in covered)
        else "insufficient_coverage"
    )

    if best is not None:
        notes.append(
            f"吞吐峰值出现在配置并发 {best.configured_concurrency}（"
            f"{best.output_throughput_tok_s} tok/s）；这只是**已测到的最优点**，"
            "不等于该配置的最大可持续产能"
        )
    if max_sustainable is None:
        notes.append(
            "未给出 maximum_sustainable_capacity：需要失败率已知、时长达标**且**重复观测"
            "达到门槛（V3.1 R5）；单次达标只记为 observed_stable_candidate"
        )
        flags.append("sustainable_capacity_not_evidenced")
    if any(p.data_trust == "synthetic_fixture" for p in points):
        flags.append("group_contains_synthetic_data_only_for_algorithm_validation")
    if coverage_status == "insufficient_coverage":
        flags.append("insufficient_coverage")

    return CapacityProfile(
        group_key=group_key,
        model_name=sig.model_name,
        hardware_model=sig.hardware_model,
        tp_size=sig.tp_size,
        dp_size=sig.dp_size,
        tp_dp_combination=f"tp{sig.tp_size}xdp{sig.dp_size}",
        framework=sig.framework,
        framework_version=sig.framework_version,
        load_profile_kind=runs[0].load_profile_kind or "unknown",
        points=points,
        best_observed_point=best,
        observed_stable_candidate=observed_stable,
        maximum_sustainable_capacity=max_sustainable,
        sustainability_evidence=sustainability,
        covered_concurrency_points=covered,
        missing_concurrency_points=missing,
        coverage_status=coverage_status,
        notes=notes,
        quality_flags=flags,
    )


def analyze_runs(
    runs: Iterable[BenchmarkRun],
    *,
    criteria: SustainabilityCriteria | None = None,
    target_concurrency: int | None = None,
    exclude_synthetic: bool = False,
) -> list[CapacityProfile]:
    """按分组键拆分后逐组分析。"""
    groups: dict[str, list[BenchmarkRun]] = {}
    for run in runs:
        if exclude_synthetic and run.data_trust == "synthetic_fixture":
            continue
        groups.setdefault(grouping_key(run), []).append(run)
    return [
        analyze_group(group, criteria=criteria, target_concurrency=target_concurrency)
        for _key, group in sorted(groups.items())
    ]
