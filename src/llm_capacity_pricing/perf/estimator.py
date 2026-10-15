"""M22：性能预测与误差校准。

三步走，每一步都**只在证据允许时**才给出数值：

1. **精确查询**：目标与某条实测记录精确匹配 -> 直接返回观测值
   （``measured_reference``），不调用任何模型；
2. **同配置范围内估计**：同一模型/硬件/部署/负载类型下，若同一指标有 ≥3 个
   可靠并发点，才允许在**已覆盖的并发区间内**做线性插值（``calibrated_estimate``），
   并给出**留出验证**误差；超出覆盖范围、跨配置、或点数不足一律
   ``not_predictable``；
3. **模型接口**：预留 ``PerformanceModel`` 协议（Prefill/Decode/排队），
   本轮不实现跨硬件通用性能预测器。

红线：不跨模型 / GPU SKU / TP-DP / 精度 / Workload 插值；不平均或插值 P99 并声称统计保证；
误差必须来自留出数据，不是拟合残差。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Protocol, Sequence

from llm_capacity_pricing.schemas.performance import (
    BenchmarkRun,
    EstimateKind,
    PerformanceEstimate,
)
from llm_capacity_pricing.perf.capacity_analyzer import grouping_key

#: 可插值的指标（只支持"随并发单调"的聚合量；P99 一律不做插值）
INTERPOLATABLE_METRICS: dict[str, str] = {
    "output_throughput_tok_s": "tok/s",
    "request_throughput_rps": "req/s",
}
#: 明确**禁止**插值的指标（P99 必须有实测点，不允许插值后当结论）
NON_INTERPOLATABLE_METRICS: tuple[str, ...] = (
    "ttft_p99_ms",
    "tpot_p99_ms",
    "itl_p99_ms",
    "e2e_p99_ms",
)

#: 最少需要的独立并发点数（少于这个数不做插值）
MIN_POINTS_FOR_CALIBRATION = 3
#: 留出验证至少需要这么多**有效**留出点（V3.1 R6）
MIN_HOLDOUT_POINTS = 3
#: 默认可参与估算的数据可信等级（synthetic 只能验证算法）
DEFAULT_ALLOWED_TRUST: tuple[str, ...] = ("measured_internal", "measured_vendor_published")
#: 留出相对误差上限；超过则认为不可预测
MAX_HOLDOUT_RELATIVE_ERROR = Decimal("0.15")


class PerformanceModel(Protocol):
    """性能模型接口（第三阶段只留接口，本轮不实现通用预测器）。"""

    model_id: str
    version: str

    def predict(self, **kwargs) -> Decimal | None:  # pragma: no cover - 接口
        ...


@dataclass
class CalibrationReport:
    """一次校准的留出验证结果。"""

    method_id: str
    method_version: str
    metric: str
    covered_range: list[Decimal]
    holdout_mae: Decimal
    holdout_relative_error: Decimal
    evaluated_points: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)


def _value(run: BenchmarkRun, metric: str) -> Decimal | None:
    return getattr(run.profile, metric, None)


def _concurrency(run: BenchmarkRun) -> int | None:
    return run.profile.configured_concurrency


def group_runs_by_configuration(runs: Sequence[BenchmarkRun]) -> dict[str, list[BenchmarkRun]]:
    """按"同模型/硬件/部署/精度/负载类型"分组（跨组不得插值）。"""
    from llm_capacity_pricing.perf.capacity_analyzer import grouping_key

    groups: dict[str, list[BenchmarkRun]] = {}
    for run in runs:
        groups.setdefault(grouping_key(run), []).append(run)
    return groups


def estimate(
    *,
    metric: str,
    target_concurrency: int,
    runs: Sequence[BenchmarkRun],
    target_run: BenchmarkRun | None = None,
    allowed_trust: Sequence[str] | None = None,
) -> PerformanceEstimate:
    """估算某配置在某并发下的聚合性能指标。

    判定顺序（V3.1 复审要求：**先校验数据，再走精确查询**）：

    1. 配置/负载一致性、可信等级、target_run 作用域校验
       （否则精确查询会绕过这些检查直接返回 ``measured_reference``）；
    2. ``measured_reference``：目标并发已有实测点 —— 直接返回观测值，不调用模型；
    3. ``p99_not_interpolatable``：指标属于 P99 家族 —— 与数据可信度无关，一律不插值；
    4. 点数与覆盖范围检查；
    5. 留出验证（leave-one-out）计算误差，有效留出点不足或误差过大 -> ``not_predictable``。
    """
    unit = INTERPOLATABLE_METRICS.get(metric) or "unknown"

    # ---- 3. 数据校验（R6）------------------------------------------------
    if runs:
        group_keys = {grouping_key(r) for r in runs}
        if len(group_keys) > 1:
            return PerformanceEstimate(
                target_ref=f"concurrency={target_concurrency}",
                metric=metric,
                estimate_kind="not_predictable",
                unit=unit,
                reasons=[
                    "mixed_configurations_in_input:输入 run 不属于同一配置与负载组，"
                    "不允许跨配置/跨负载插值"
                ],
                supporting_runs=[r.run_id for r in runs],
            )
    trust_levels = {r.data_trust for r in runs}
    if len(trust_levels) > 1:
        return PerformanceEstimate(
            target_ref=f"concurrency={target_concurrency}",
            metric=metric,
            estimate_kind="not_predictable",
            unit=unit,
            reasons=[
                f"mixed_trust_levels:{sorted(trust_levels)}"
                "（不允许把不同可信等级的数据混在一起拟合）"
            ],
            supporting_runs=[r.run_id for r in runs],
        )
    if target_run is not None and runs:
        if grouping_key(target_run) != grouping_key(runs[0]):
            return PerformanceEstimate(
                target_ref=f"concurrency={target_concurrency}",
                metric=metric,
                estimate_kind="not_predictable",
                unit=unit,
                reasons=[
                    "target_run_not_in_scope: 指定的 target_run 与输入 run 不属于同一配置/负载组"
                ],
                supporting_runs=[r.run_id for r in runs],
            )
    allowed = set(allowed_trust or DEFAULT_ALLOWED_TRUST)
    # target_run 单独校验（V3.1 复审 P1-3）：即使 runs 为空，
    # 也不能用一个未授权的 target_run 直接返回 measured_reference。
    if target_run is not None and target_run.data_trust not in allowed:
        return PerformanceEstimate(
            target_ref=f"concurrency={target_concurrency}",
            metric=metric,
            estimate_kind="not_predictable",
            unit=unit,
            reasons=[
                f"target_run_trust_not_allowed:{target_run.data_trust}"
                "（精确查询同样受可信等级约束，不能绕过校验）"
            ],
            supporting_runs=[target_run.run_id],
        )
    disallowed = sorted(trust_levels - allowed)
    if disallowed:
        return PerformanceEstimate(
            target_ref=f"concurrency={target_concurrency}",
            metric=metric,
            estimate_kind="not_predictable",
            unit=unit,
            reasons=[
                f"trust_level_not_allowed_for_estimation:{disallowed}"
                "（synthetic 仅可验证算法，不可用于性能结论；如确为算法验证请显式允许）"
            ],
            supporting_runs=[r.run_id for r in runs],
        )

    # ---- 1. 精确查询 -----------------------------------------------------
    if target_run is not None:
        value = _value(target_run, metric)
        if value is not None and _concurrency(target_run) == target_concurrency:
            return PerformanceEstimate(
                target_ref=f"concurrency={target_concurrency}",
                metric=metric,
                estimate_kind="measured_reference",
                value=value,
                unit=unit,
                source_run_id=target_run.run_id,
                reasons=["exact_observed_point_used_no_model"],
            )

    # ---- 2. P99 红线 -----------------------------------------------------
    if metric in NON_INTERPOLATABLE_METRICS:
        return PerformanceEstimate(
            target_ref=f"concurrency={target_concurrency}",
            metric=metric,
            estimate_kind="not_predictable",
            unit=unit,
            reasons=[
                "p99_not_interpolatable: 缺少该并发点的实测 P99，"
                "不允许用其它并发点插值（统计保证不成立）"
            ],
        )

    # ---- 4. 点数与覆盖范围 -----------------------------------------------
    points = [
        run
        for run in runs
        if _concurrency(run) is not None and _value(run, metric) is not None
    ]
    if len(points) < MIN_POINTS_FOR_CALIBRATION:
        return PerformanceEstimate(
            target_ref=f"concurrency={target_concurrency}",
            metric=metric,
            estimate_kind="not_predictable",
            unit=unit,
            reasons=[
                f"insufficient_points:{len(points)}<{MIN_POINTS_FOR_CALIBRATION}"
                "（同配置可用的并发点不足）"
            ],
            supporting_runs=[r.run_id for r in points],
        )
    points = sorted(points, key=lambda r: _concurrency(r))  # type: ignore[arg-type,return-value]
    xs = [Decimal(_concurrency(r)) for r in points]  # type: ignore[arg-type]
    lo, hi = xs[0], xs[-1]
    x = Decimal(target_concurrency)
    if x < lo or x > hi:
        return PerformanceEstimate(
            target_ref=f"concurrency={target_concurrency}",
            metric=metric,
            estimate_kind="not_predictable",
            unit=unit,
            covered_range=[lo, hi],
            reasons=[f"out_of_covered_range:{x}not_in[{lo},{hi}]：不外推到未测过的并发区间"],
            supporting_runs=[r.run_id for r in points],
        )

    # ---- 5. 留出验证（leave-one-out），不是拟合残差 -----------------------
    errors: list[Decimal] = []
    evaluated: list[str] = []
    failures: list[str] = []
    for held_out in points:
        train = [r for r in points if r.run_id != held_out.run_id]
        if len(train) < 2:
            continue
        predicted = _linear_interpolate(train, metric, _concurrency(held_out))  # type: ignore[arg-type]
        actual = _value(held_out, metric)
        if predicted is None or actual in (None, 0):
            failures.append(held_out.run_id)
            continue
        errors.append(abs(predicted - actual))
        evaluated.append(held_out.run_id)
    if len(errors) < MIN_HOLDOUT_POINTS:
        return PerformanceEstimate(
            target_ref=f"concurrency={target_concurrency}",
            metric=metric,
            estimate_kind="not_predictable",
            unit=unit,
            covered_range=[lo, hi],
            reasons=[
                f"insufficient_holdout_points:{len(errors)}<{MIN_HOLDOUT_POINTS}"
                "（有效留出样本太少，误差不可信）"
            ],
            supporting_runs=[r.run_id for r in points],
        )
    mae = sum(errors) / Decimal(len(errors))
    mean_actual = sum((_value(r, metric) or Decimal(0)) for r in points) / Decimal(len(points))
    rel = (mae / mean_actual).quantize(Decimal("0.000001")) if mean_actual else Decimal(0)
    if rel > MAX_HOLDOUT_RELATIVE_ERROR:
        return PerformanceEstimate(
            target_ref=f"concurrency={target_concurrency}",
            metric=metric,
            estimate_kind="not_predictable",
            unit=unit,
            covered_range=[lo, hi],
            holdout_mae=mae,
            holdout_relative_error=rel,
            holdout_failures=failures,
            reasons=[
                f"holdout_error_too_large:{rel}>{MAX_HOLDOUT_RELATIVE_ERROR}"
                "（留出误差超过阈值，不给估算值）"
            ],
            supporting_runs=[r.run_id for r in points],
        )

    value = _linear_interpolate(points, metric, target_concurrency)
    if value is None:
        return PerformanceEstimate(
            target_ref=f"concurrency={target_concurrency}",
            metric=metric,
            estimate_kind="not_predictable",
            unit=unit,
            reasons=["interpolation_failed"],
        )
    return PerformanceEstimate(
        target_ref=f"concurrency={target_concurrency}",
        metric=metric,
        estimate_kind="calibrated_estimate",
        value=value,
        unit=unit,
        supporting_runs=[r.run_id for r in points],
        method_id="linear_interpolation_in_covered_range",
        method_version="v1",
        covered_range=[lo, hi],
        holdout_mae=mae,
        holdout_relative_error=rel,
        holdout_failures=failures,
        reasons=[
            f"holdout_leave_one_out_on_{len(evaluated)}_points",
            "estimate_is_within_measured_range_only",
        ],
    )


def _linear_interpolate(
    points: Sequence[BenchmarkRun], metric: str, x: int
) -> Decimal | None:
    """在已排序的点集上做线性插值（不外推）。"""
    xs = [Decimal(_concurrency(r)) for r in points]  # type: ignore[arg-type]
    ys = [_value(r, metric) for r in points]
    if any(y is None for y in ys):
        return None
    xd = Decimal(x)
    for i in range(len(points) - 1):
        if xs[i] <= xd <= xs[i + 1]:
            x0, x1 = xs[i], xs[i + 1]
            y0, y1 = ys[i], ys[i + 1]
            if x1 == x0:
                return y0
            return y0 + (y1 - y0) * (xd - x0) / (x1 - x0)  # type: ignore[operator]
    return None


def calibration_report(estimate_result: PerformanceEstimate) -> CalibrationReport | None:
    """把估算结果里的留出误差整理成报告对象。"""
    if estimate_result.estimate_kind != "calibrated_estimate":
        return None
    assert estimate_result.holdout_mae is not None
    return CalibrationReport(
        method_id=estimate_result.method_id or "unknown",
        method_version=estimate_result.method_version or "unknown",
        metric=estimate_result.metric,
        covered_range=list(estimate_result.covered_range),
        holdout_mae=estimate_result.holdout_mae,
        holdout_relative_error=estimate_result.holdout_relative_error or Decimal(0),
        evaluated_points=list(estimate_result.supporting_runs),
        failures=list(estimate_result.holdout_failures),
    )
