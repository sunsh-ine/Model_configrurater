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


def grouping_key(run: BenchmarkRun) -> str:
    """容量曲线的分组键：同组才可比较。"""
    sig = run.signature
    parts = (
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

    # ---- 可持续容量（需要证据）------------------------------------------
    sustainability: list[str] = []
    max_sustainable: int | None = None
    if best is not None:
        pool = [p for p in with_throughput if p.error_rate is None or p.error_rate <= criteria.max_error_rate]
        run_by_id = {r.run_id: r for r in runs}
        for p in pool:
            duration = run_by_id[p.run_id].profile.duration_s
            if duration is not None and duration >= criteria.min_duration_s:
                if max_sustainable is None or (
                    p.configured_concurrency or 0
                ) > (max_sustainable or 0):
                    max_sustainable = p.configured_concurrency
        if max_sustainable is not None:
            sustainability.append(
                f"该点满足持续时间 ≥ {criteria.min_duration_s}s 且失败率 ≤ {criteria.max_error_rate}"
            )
        else:
            sustainability.clear()

    coverage_status = (
        "sufficient"
        if len(points) >= criteria.min_points
        and (target_concurrency is None or target_concurrency in covered)
        else "insufficient_coverage"
    )

    notes: list[str] = []
    flags: list[str] = []
    if best is not None:
        notes.append(
            f"吞吐峰值出现在配置并发 {best.configured_concurrency}（"
            f"{best.output_throughput_tok_s} tok/s）；这只是**已测到的最优点**，"
            "不等于该配置的最大可持续产能"
        )
    if max_sustainable is None:
        notes.append(
            "缺少持续时间/失败率证据，未给出 maximum_sustainable_capacity"
            "（任务书 M21 强制要求）"
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
