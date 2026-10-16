"""M38：真实 Benchmark 导入后的一致性校验。

适配器负责"把外部格式翻译成统一对象"，本模块负责回答**这批数据能不能用**：

* 客户端与服务端指标是否混用；
* TTFT/TPOT/ITL 的统计口径是否声明完整；
* 配置并发与实际并发是否分开、是否自相矛盾；
* 请求成功/失败/超时是否齐全、错误率是否与计数一致；
* Prefix Cache 的**物理命中率**与**可计费缓存比例**是否被混为一谈；
* 同一配置的重复 Run 会不会被当作一条（应保守聚合）；
* 有没有拿 Mean 顶替 P99 的痕迹。

只报告，不改数据；缺失就如实说缺什么，**绝不补造数值**。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Literal, Sequence

Severity = Literal["blocker", "warning", "info"]

#: 百分比类字段的一致性容差（错误率与计数换算允许的舍入）
ERROR_RATE_TOLERANCE = Decimal("0.01")

#: SLA 关键指标：缺口径声明属于 blocker；其余（itl/e2e）只提醒。
#: 与第四阶段 TCO/SLA 的"关键指标"口径保持一致，避免两处定义打架。
SLA_CRITICAL_PERCENTILES: tuple[tuple[str, str], ...] = (
    ("ttft_p99_ms", "m.latency.ttft.p99"),
    ("tpot_p99_ms", "m.latency.tpot.p99"),
)
#: 信息性分位指标：缺口径只提醒
INFORMATIVE_PERCENTILES: tuple[tuple[str, str], ...] = (
    ("itl_p99_ms", "m.latency.itl.p99"),
    ("e2e_p99_ms", "m.latency.e2e.p99"),
)


@dataclass(frozen=True)
class ConsistencyIssue:
    """一条一致性问题。``severity=blocker`` 表示"不得用于生产结论"。"""

    code: str
    severity: Severity
    message: str
    run_id: str | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class ConsistencyReport:
    """一次一致性校验的结果。"""

    checked_run_count: int
    issues: list[ConsistencyIssue] = field(default_factory=list)

    @property
    def blockers(self) -> list[ConsistencyIssue]:
        return [i for i in self.issues if i.severity == "blocker"]

    @property
    def warnings(self) -> list[ConsistencyIssue]:
        return [i for i in self.issues if i.severity == "warning"]

    @property
    def ok(self) -> bool:
        return not self.blockers

    def to_dict(self) -> dict[str, Any]:
        return {
            "checked_run_count": self.checked_run_count,
            "ok": self.ok,
            "blocker_count": len(self.blockers),
            "warning_count": len(self.warnings),
            "issues": [
                {
                    "code": i.code,
                    "severity": i.severity,
                    "message": i.message,
                    "run_id": i.run_id,
                    "details": i.details,
                }
                for i in self.issues
            ],
        }


def _percentile_issues(run: Any, out: list[ConsistencyIssue]) -> None:
    """统计口径声明与 P99/Max 混用。"""
    profile = run.profile
    declared = set(profile.metric_definition_ids)
    pairs = SLA_CRITICAL_PERCENTILES + INFORMATIVE_PERCENTILES
    critical_fields = {name for name, _ in SLA_CRITICAL_PERCENTILES}
    for field_name, definition_id in pairs:
        value = getattr(profile, field_name, None)
        if value is not None and definition_id not in declared:
            out.append(
                ConsistencyIssue(
                    code="metric_definition_missing",
                    # SLA 关键指标缺口径是硬伤；itl/e2e 只提醒
                    severity="blocker" if field_name in critical_fields else "warning",
                    message=(
                        f"{field_name} 有值但没有声明口径 {definition_id}："
                        "无法确认它是不是 P99（不得当作 P99 使用）"
                    ),
                    run_id=run.run_id,
                    details={"field": field_name, "definition_id": definition_id},
                )
            )
    # P99 缺失但没记进 missing_metrics：账目不清
    unbookkept = [
        name
        for name, _ in pairs
        if getattr(profile, name, None) is None and name not in set(profile.missing_metrics)
    ]
    if unbookkept:
        out.append(
            ConsistencyIssue(
                code="missing_metrics_bookkeeping_incomplete",
                severity="warning",
                message=(
                    f"这些 P99 字段为空但没有记入 missing_metrics：{unbookkept}；"
                    "账目不清会让下游误以为数据完整"
                ),
                run_id=run.run_id,
                details={"fields": unbookkept},
            )
        )
    if all(getattr(profile, name, None) is None for name, _ in pairs):
        out.append(
            ConsistencyIssue(
                code="no_percentile_metrics",
                severity="warning",
                message="没有任何 P99 指标：可用于容量参考，但无法支撑 SLA 判定",
                run_id=run.run_id,
            )
        )
    # 只有 Mean 时不得声称 P99
    mean_fields = ("ttft_mean_ms", "tpot_mean_ms", "itl_mean_ms", "e2e_mean_ms")
    if any(getattr(profile, f, None) is not None for f in mean_fields) and all(
        getattr(profile, name, None) is None for name, _ in pairs
    ):
        out.append(
            ConsistencyIssue(
                code="mean_only_do_not_infer_p99",
                severity="info",
                message="只有 Mean 指标：**不得**据此推算 P99（适配器与门禁都禁止该推算）",
                run_id=run.run_id,
            )
        )


def _metric_side_issues(run: Any, out: list[ConsistencyIssue]) -> None:
    """客户端与服务端指标不得混用。"""
    profile = run.profile
    # "客户端指标是否齐备"只看 **SLA 关键**分位：itl/e2e 属于信息性字段，
    # 拿它们判断"有没有客户端口径"会把纯服务端数据误判成客户端数据。
    client_present = any(
        getattr(profile, f, None) is not None for f, _ in SLA_CRITICAL_PERCENTILES
    )
    server_present = any(
        getattr(profile, f, None) is not None
        for f in ("server_ttft_p99_ms", "server_tpot_p99_ms")
    )
    if client_present and server_present:
        out.append(
            ConsistencyIssue(
                code="client_server_metrics_mixed",
                severity="warning",
                message=(
                    "同一 Run 同时存在客户端与服务端时延指标：下游只用客户端口径，"
                    "两者不得混算或互相顶替"
                ),
                run_id=run.run_id,
                details={"metric_side": profile.metric_side},
            )
        )
    if not client_present and server_present:
        out.append(
            ConsistencyIssue(
                code="server_side_only",
                severity="blocker",
                message=(
                    "只有服务端时延指标：服务端口径与客户端口径不可互换，"
                    "不得用于 SLA 判定"
                ),
                run_id=run.run_id,
            )
        )


def _concurrency_issues(run: Any, out: list[ConsistencyIssue]) -> None:
    """配置并发与实际并发必须分开且自洽。"""
    profile = run.profile
    configured = profile.configured_concurrency
    observed = profile.observed_concurrency
    if configured is None:
        out.append(
            ConsistencyIssue(
                code="configured_concurrency_missing",
                severity="warning",
                message=(
                    "未记录**配置并发**：并发直接决定吞吐与时延，"
                    "缺它就无法与目标负载对齐（匹配等级会降级）"
                ),
                run_id=run.run_id,
            )
        )
    elif observed is not None and observed > Decimal(configured) * Decimal("1.5"):
        out.append(
            ConsistencyIssue(
                code="observed_concurrency_exceeds_configured",
                severity="warning",
                message=(
                    f"实际并发 {observed} 显著高于配置并发 {configured}："
                    "疑似把实际并发填进了配置并发字段，需核对"
                ),
                run_id=run.run_id,
                details={"configured": configured, "observed": str(observed)},
            )
        )
    arrival = run.signature.workload.arrival_mode
    if arrival == "unbounded" and profile.request_rate_rps is not None:
        out.append(
            ConsistencyIssue(
                code="arrival_mode_conflicts_with_rate",
                severity="warning",
                message=(
                    "到达方式标为 unbounded，却又记录了 request_rate："
                    "两者语义冲突（限速测试的并发是被动结果）"
                ),
                run_id=run.run_id,
                details={"request_rate_rps": str(profile.request_rate_rps)},
            )
        )


def _request_accounting_issues(run: Any, out: list[ConsistencyIssue]) -> None:
    """请求成功/失败/超时与错误率。"""
    profile = run.profile
    total, success, failed = (
        profile.num_requests,
        profile.success_count,
        profile.failed_count,
    )
    if total is None or total <= 0:
        out.append(
            ConsistencyIssue(
                code="request_count_missing",
                severity="warning",
                message="未记录请求总数：无法判断样本量是否足以支撑 P99",
                run_id=run.run_id,
            )
        )
        return
    if success is None:
        out.append(
            ConsistencyIssue(
                code="success_count_missing",
                severity="warning",
                message="未记录成功请求数：无法核算失败率，容量结论只能保守处理",
                run_id=run.run_id,
            )
        )
        return
    implied_failed = total - success
    if failed is not None and failed != implied_failed:
        out.append(
            ConsistencyIssue(
                code="request_accounting_inconsistent",
                severity="warning",
                message=(
                    f"总数 {total} − 成功 {success} = {implied_failed}，"
                    f"但记录失败数为 {failed}：账目不一致"
                ),
                run_id=run.run_id,
            )
        )
    if profile.error_rate is not None:
        implied = Decimal(implied_failed) / Decimal(total)
        if abs(implied - profile.error_rate) > ERROR_RATE_TOLERANCE:
            out.append(
                ConsistencyIssue(
                    code="error_rate_inconsistent",
                    severity="blocker",
                    message=(
                        f"错误率 {profile.error_rate} 与计数推算的 {implied} 不一致"
                        f"（容差 {ERROR_RATE_TOLERANCE}）：疑似解析或口径错误"
                    ),
                    run_id=run.run_id,
                    details={"recorded": str(profile.error_rate), "implied": str(implied)},
                )
            )


def _cache_issues(run: Any, out: list[ConsistencyIssue]) -> None:
    """物理命中率 ≠ 可计费缓存比例。"""
    profile = run.profile
    physical = profile.physical_cache_hit_rate
    billable = profile.billable_cached_input_ratio
    if physical is None and billable is None:
        return
    if physical is not None and billable is not None and physical == billable:
        out.append(
            ConsistencyIssue(
                code="cache_ratio_equals_physical_rate",
                severity="warning",
                message=(
                    "可计费缓存比例与引擎物理命中率完全相同："
                    "可计费比例应由合同口径给出，直接照抄物理命中率属于口径混用"
                ),
                run_id=run.run_id,
                details={"value": str(physical)},
            )
        )
    if billable is not None:
        out.append(
            ConsistencyIssue(
                code="billable_cache_ratio_requires_contract_basis",
                severity="info",
                message=(
                    "已记录可计费缓存比例：请确认它来自合同/价目口径，"
                    "而不是从物理命中率推得"
                ),
                run_id=run.run_id,
            )
        )


def check_run_consistency(run: Any) -> list[ConsistencyIssue]:
    """单条 Run 的一致性检查。"""
    out: list[ConsistencyIssue] = []
    _metric_side_issues(run, out)
    _percentile_issues(run, out)
    _concurrency_issues(run, out)
    _request_accounting_issues(run, out)
    _cache_issues(run, out)
    return out


def check_batch_consistency(runs: Sequence[Any]) -> list[ConsistencyIssue]:
    """批次级检查：重复 run_id、同组身份不一致、同点重复观测。"""
    from llm_capacity_pricing.perf.capacity_analyzer import grouping_key

    out: list[ConsistencyIssue] = []
    seen: dict[str, int] = {}
    for run in runs:
        seen[run.run_id] = seen.get(run.run_id, 0) + 1
    for run_id, count in sorted(seen.items()):
        if count > 1:
            out.append(
                ConsistencyIssue(
                    code="duplicate_run_id_in_batch",
                    severity="blocker",
                    message=f"同一批次里出现 {count} 条相同 run_id：导入结果不可重放",
                    run_id=run_id,
                    details={"count": count},
                )
            )

    # 分组键应当只由"同配置 + 同负载"决定；键相同但身份不同说明分组逻辑被绕过
    by_group: dict[str, set[tuple[Any, ...]]] = {}
    for run in runs:
        sig = run.signature
        identity = (
            sig.model_name,
            sig.model_revision,
            sig.weight_precision,
            sig.hardware_model,
            sig.tp_size,
            sig.dp_size,
            sig.gpu_count,
            sig.framework,
            sig.framework_version,
        )
        by_group.setdefault(grouping_key(run), set()).add(identity)
    for key, identities in sorted(by_group.items()):
        if len(identities) > 1:
            out.append(
                ConsistencyIssue(
                    code="identity_mismatch_within_group",
                    severity="blocker",
                    message=(
                        f"同一分组键下出现 {len(identities)} 种部署身份："
                        "不同配置/负载不得混入同一曲线"
                    ),
                    details={"group_key": key[:80], "identity_count": len(identities)},
                )
            )

    # 同组同并发多条 -> 保守聚合（信息性说明，不是问题）
    by_point: dict[tuple[str, Any], list[str]] = {}
    for run in runs:
        by_point.setdefault(
            (grouping_key(run), run.profile.configured_concurrency), []
        ).append(run.run_id)
    repeated = {k: v for k, v in by_point.items() if len(v) > 1}
    if repeated:
        out.append(
            ConsistencyIssue(
                code="repeat_runs_will_be_conservatively_aggregated",
                severity="info",
                message=(
                    f"有 {len(repeated)} 个（配置, 并发）点存在多条实测："
                    "评估时会按保守口径聚合（P99 取最大、吞吐取最小），不会取最优值"
                ),
                details={"points": len(repeated)},
            )
        )
    return out


def check_import_consistency(runs: Sequence[Any]) -> ConsistencyReport:
    """对一批 Run 做完整一致性校验。"""
    issues: list[ConsistencyIssue] = []
    for run in runs:
        issues.extend(check_run_consistency(run))
    issues.extend(check_batch_consistency(runs))
    return ConsistencyReport(checked_run_count=len(runs), issues=issues)
