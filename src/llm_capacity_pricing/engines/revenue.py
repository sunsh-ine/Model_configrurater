"""M29：基于业务请求量的 Token 计费与收益情景（纯计算）。

与第一阶段的关系：

* 第一阶段的理论满载 Token 收入继续保留，**但本模块不把它当真实营业收入**；
* 收入一律按"请求量 × 输入输出分布 × 计费规则 × 使用时段"推算。

三段请求量（V4.1 F4.4）——**需求 ≠ 成功服务 ≠ 可计费**：

* ``demand_requests``：业务需求请求量；
* ``served_requests`` = 需求 × 成功率；
* ``billable_requests``：失败请求是否计费由合同口径
  ``BillingRule.billable_on_failure`` 决定，默认 False（不假设失败也收费）。

缓存计费口径（V4.1 F4.4 修正）：

* ``BillingRule.billable_cache_ratio`` 是**合同允许按缓存价计费的最大输入占比**，
  不是"保守估计"。缓存价低于未缓存价时，**取更小的比例会提高收入预测**；
* 因此本模块给出**收入区间** ``[lower_bound, upper_bound]``：
  下界 = 按最大可辩护折扣比例（业务估计与合同上限中的较大者）计算，
  上界 = 完全不打折（缓存占比 0）。基准值落在区间内，**不宣称是保守下界**；
* 引擎物理 prefix cache 命中率**不是**合法计费依据。

容量校验（V4.1 F4.6）——不只看输出吞吐：

输出吞吐 / 请求吞吐 / 并发与负载一致性 / SLA 证据 / 可持续性证据，
五个子项分别判定，只有全部满足才给 ``within_capacity``。
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from llm_capacity_pricing.schemas.commercial import (
    BillingRule,
    CapacityCheckDetail,
    PeriodRevenue,
    RevenueResult,
    RevenueScenario,
)

#: 每百万 token 的换算基数
PER_MILLION = Decimal("1000000")
SECONDS_PER_HOUR = Decimal("3600")
MILLISECONDS_PER_SECOND = Decimal("1000")


@dataclass(frozen=True)
class SlaLimits:
    """需求侧的时延约束（用于容量子项判定；缺哪项就只判能判的）。"""

    ttft_p99_ms_max: Decimal | None = None
    tpot_p99_ms_max: Decimal | None = None


@dataclass(frozen=True)
class CapacityEvidence:
    """第三阶段的**可信**容量证据。

    ``is_verified`` 由服务层按"exact_match + 可信实测"判定，本模块不自行放宽。
    其余字段用于逐子项校验；缺字段只让对应子项变成 ``unknown``，不猜。
    """

    run_id: str
    is_verified: bool
    configured_concurrency: int | None = None
    request_throughput_rps: Decimal | None = None
    output_throughput_tok_s: Decimal | None = None
    match_level: str | None = None
    data_trust: str | None = None
    #: 该实测的负载身份（用于核对需求负载是否与证据一致）
    input_len_target: int | None = None
    output_len_target: int | None = None
    #: 实测时延与失败率（用于 SLA / 可持续性子项）
    ttft_p99_ms: Decimal | None = None
    tpot_p99_ms: Decimal | None = None
    error_rate: Decimal | None = None
    #: 该 run 的 SLA 评估结论（observed_pass / observed_fail / not_evaluable …）
    sla_status: str | None = None
    #: 是否具备可持续性证据（重复观测 / 持续时长 / 失败率均达标）
    sustainability_verified: bool = False


def _effective_cache_fractions(
    business_estimate: Decimal, rule: BillingRule
) -> tuple[Decimal, Decimal, str]:
    """返回 ``(基准折扣比例, 最大可辩护折扣比例, 规则说明)``。

    基准比例 = 若合同给了上限则取 ``min(业务估计, 上限)``，否则取业务估计。
    最大可辩护比例 = ``min(1, max(业务估计, 合同上限))`` —— 用于收入**下界**。
    **两者都不是"保守估计"**：缓存价更低时，折扣越多收入越低。
    """
    if rule.billable_cache_ratio is None:
        return (
            business_estimate,
            business_estimate,
            "未定义合同上限：按业务侧估计计费（结果未受合同约束）",
        )
    applied = min(business_estimate, rule.billable_cache_ratio)
    high = min(Decimal(1), max(business_estimate, rule.billable_cache_ratio))
    return (
        applied,
        high,
        f"按合同上限计费：min(业务估计 {business_estimate}, 合同上限 "
        f"{rule.billable_cache_ratio}) = {applied}",
    )


def _amounts(
    *,
    billable_requests: Decimal,
    avg_input_tokens: Decimal,
    avg_output_tokens: Decimal,
    cached_fraction: Decimal,
    rule: BillingRule,
    batch: bool,
) -> tuple[Decimal, Decimal, Decimal, Decimal, Decimal, Decimal]:
    """给定缓存比例，算出 (未缓存输入, 缓存输入, 输出, 输入收入, 输出收入, 净收入)。"""
    input_tokens = avg_input_tokens * billable_requests
    cached_tokens = input_tokens * cached_fraction
    uncached_tokens = input_tokens - cached_tokens
    output_tokens = avg_output_tokens * billable_requests

    input_revenue = (
        uncached_tokens / PER_MILLION * rule.price_uncached_input_per_1m
        + cached_tokens / PER_MILLION * rule.price_cached_input_per_1m
    )
    output_revenue = output_tokens / PER_MILLION * rule.price_output_per_1m
    gross = input_revenue + output_revenue
    discount = Decimal(0)
    if batch and rule.batch_discount_fraction is not None:
        discount = gross * rule.batch_discount_fraction
    return uncached_tokens, cached_tokens, output_tokens, input_revenue, output_revenue, gross - discount


def _capacity(
    *,
    evidence: CapacityEvidence | None,
    peak_rate: Decimal | None,
    peak_output_tps: Decimal | None,
    peak_input_tokens: Decimal | None,
    peak_output_tokens: Decimal | None,
    limits: SlaLimits | None,
) -> CapacityCheckDetail:
    """逐子项做容量判定（F4.6）。任一子项不满足即整体不满足。"""
    reasons: list[str] = []
    if evidence is None or not evidence.is_verified:
        reasons.append(
            "没有可信实测容量证据（需 exact_match 的可信实测）：无法判定需求是否落在产能内"
        )
        if evidence is not None:
            reasons.append(
                f"已排除不可信证据: run={evidence.run_id} "
                f"match={evidence.match_level} trust={evidence.data_trust}"
            )
        return CapacityCheckDetail(
            output_throughput="unknown",
            request_throughput="unknown",
            concurrency_and_load="unknown",
            sla_evidence="unknown",
            sustainability="unknown",
            overall="unknown_no_measurement",
            reasons=reasons,
        )

    ev = evidence

    # ---- 子项 1：输出 token 吞吐 ----
    if ev.output_throughput_tok_s is None or peak_output_tps is None:
        out_status = "unknown"
        reasons.append("输出吞吐：证据或需求缺该指标")
    else:
        out_status = (
            "satisfied"
            if peak_output_tps <= ev.output_throughput_tok_s
            else "not_satisfied"
        )
        reasons.append(
            f"输出吞吐：需求 {peak_output_tps} tok/s vs 实测 {ev.output_throughput_tok_s} tok/s"
            f" -> {out_status}"
        )

    # ---- 子项 2：请求吞吐 ----
    if ev.request_throughput_rps is None or peak_rate is None:
        req_status = "unknown"
        reasons.append("请求吞吐：证据缺 request_throughput_rps，无法判定")
    else:
        req_status = (
            "satisfied" if peak_rate <= ev.request_throughput_rps else "not_satisfied"
        )
        reasons.append(
            f"请求吞吐：需求 {peak_rate} req/s vs 实测 {ev.request_throughput_rps} req/s"
            f" -> {req_status}"
        )

    # ---- 子项 3：并发与负载一致性 ----
    load_notes: list[str] = []
    load_status: str = "satisfied"
    if (
        ev.configured_concurrency is None
        or ev.ttft_p99_ms is None
        or ev.tpot_p99_ms is None
        or peak_rate is None
        or peak_output_tokens is None
    ):
        load_status = "unknown"
        load_notes.append("并发/负载：缺并发或时延字段，无法用 Little 定律反推所需并发")
    else:
        # Little 定律：并发 ≈ 速率 × 单请求驻留时间
        residence_s = (
            ev.ttft_p99_ms + ev.tpot_p99_ms * peak_output_tokens
        ) / MILLISECONDS_PER_SECOND
        implied = peak_rate * residence_s
        load_notes.append(
            f"并发：按 Little 定律推算需求侧需要约 {implied} 并发"
            f"（速率 {peak_rate} req/s × 驻留 {residence_s} s）"
            f"，实测配置并发 {ev.configured_concurrency}"
        )
        if implied > Decimal(ev.configured_concurrency):
            load_status = "not_satisfied"
        load_notes.append(f"-> {load_status}")
    if peak_input_tokens is not None and ev.input_len_target is not None:
        if peak_input_tokens != Decimal(ev.input_len_target):
            load_status = "not_satisfied"
            load_notes.append(
                f"负载长度不一致：需求输入 {peak_input_tokens} vs 实测目标 {ev.input_len_target}"
                "（负载不同则实测产能不能直接套用）"
            )
    else:
        load_notes.append("负载长度：证据或需求缺目标长度，长度一致性未验证")
        if load_status == "satisfied":
            load_status = "unknown"
    reasons.extend(load_notes)

    # ---- 子项 4：SLA 证据 ----
    sla_status: str = "unknown"
    if limits is None:
        reasons.append("SLA 证据：需求侧未给出时延约束，无法判定")
    elif ev.ttft_p99_ms is None and ev.tpot_p99_ms is None:
        reasons.append("SLA 证据：实测证据缺时延指标，无法判定")
    else:
        ok = True
        if limits.ttft_p99_ms_max is not None:
            if ev.ttft_p99_ms is None or ev.ttft_p99_ms > limits.ttft_p99_ms_max:
                ok = False
        if limits.tpot_p99_ms_max is not None:
            if ev.tpot_p99_ms is None or ev.tpot_p99_ms > limits.tpot_p99_ms_max:
                ok = False
        sla_status = "satisfied" if ok else "not_satisfied"
        reasons.append(
            f"SLA 证据：实测 TTFT {ev.ttft_p99_ms} / TPOT {ev.tpot_p99_ms}"
            f" vs 约束 TTFT {limits.ttft_p99_ms_max} / TPOT {limits.tpot_p99_ms_max}"
            f" -> {sla_status}"
        )

    # ---- 子项 5：可持续性 ----
    if ev.sustainability_verified:
        sustain_status = "satisfied"
        reasons.append("可持续性：具备重复观测/持续时长/失败率证据")
    else:
        sustain_status = "unknown"
        reasons.append(
            "可持续性：缺少重复观测或持续时长证据（单次观测不等于可持续产能）"
        )

    sub = {
        "output_throughput": out_status,
        "request_throughput": req_status,
        "concurrency_and_load": load_status,
        "sla_evidence": sla_status,
        "sustainability": sustain_status,
    }
    if any(v == "not_satisfied" for v in sub.values()):
        overall = "exceeds_capacity"
        reasons.append("结论：至少一个子项不满足，需求超出已验证容量")
    elif all(v == "satisfied" for v in sub.values()):
        overall = "within_capacity"
        reasons.append("结论：全部子项满足，需求落在已验证容量内")
    else:
        overall = "partially_verified"
        reasons.append(
            "结论：部分子项无法判定（证据不足），只能给部分结论，**不得**当作容量可行"
        )

    return CapacityCheckDetail(
        output_throughput=out_status,  # type: ignore[arg-type]
        request_throughput=req_status,  # type: ignore[arg-type]
        concurrency_and_load=load_status,  # type: ignore[arg-type]
        sla_evidence=sla_status,  # type: ignore[arg-type]
        sustainability=sustain_status,  # type: ignore[arg-type]
        overall=overall,  # type: ignore[arg-type]
        reasons=reasons,
    )


def compute_revenue(
    scenario: RevenueScenario,
    rule: BillingRule,
    *,
    capacity_evidence: CapacityEvidence | None = None,
    sla_limits: SlaLimits | None = None,
    as_of: object = None,
) -> RevenueResult:
    """按业务需求情景计算计费量与收入。缺单价即不可算，不用 0 顶替。"""
    from datetime import date as _date

    warnings: list[str] = []
    missing_prices: list[str] = []
    if rule.price_uncached_input_per_1m is None:
        missing_prices.append("price_uncached_input_per_1m")
    if rule.price_cached_input_per_1m is None:
        missing_prices.append("price_cached_input_per_1m")
    if rule.price_output_per_1m is None:
        missing_prices.append("price_output_per_1m")
    if missing_prices:
        raise ValueError(
            f"计费规则 {rule.rule_id} 缺少单价 {missing_prices}："
            "缺价格时不得计算收入（未知不等于零）"
        )

    # ---- F4.3：计费规则适用范围校验 ----
    today = as_of or _date.today()
    if scenario.model_name != rule.model_name:
        raise ValueError(
            f"计费规则 {rule.rule_id} 是给模型 {rule.model_name!r} 的，"
            f"不能用于情景的模型 {scenario.model_name!r}（币种/单价无权跨模型套用）"
        )
    if rule.valid_from is not None and rule.valid_from > today:  # type: ignore[operator]
        raise ValueError(
            f"计费规则 {rule.rule_id} 在 {rule.valid_from} 才生效，"
            f"基准日 {today} 尚不可用"
        )
    if rule.valid_to is not None and rule.valid_to < today:  # type: ignore[operator]
        raise ValueError(
            f"计费规则 {rule.rule_id} 已于 {rule.valid_to} 失效，基准日 {today} 不得继续使用"
        )
    if rule.status != "confirmed":
        warnings.append(
            f"billing_rule_not_confirmed:{rule.status}: 计费规则未经人工确认，收入仅为情景值"
        )

    if rule.billable_cache_basis == "physical_cache_hit_rate":
        raise ValueError(
            "billable_cache_basis 不得为 physical_cache_hit_rate：引擎物理命中率不是计费口径"
        )
    if rule.billable_cache_basis in ("assumed", "unknown"):
        warnings.append(
            f"billable_cache_basis={rule.billable_cache_basis}: "
            "可计费缓存比例尚未由合同确认，收入区间因此较宽"
        )

    periods: list[PeriodRevenue] = []
    total = Decimal(0)
    total_lower = Decimal(0)
    total_upper = Decimal(0)
    total_input = Decimal(0)
    total_output = Decimal(0)
    total_demand = Decimal(0)
    total_served = Decimal(0)
    total_billable = Decimal(0)
    peak_rate: Decimal | None = None
    peak_output_tps: Decimal | None = None
    peak_input_tokens: Decimal | None = None
    peak_output_tokens: Decimal | None = None
    cache_rule_labels: list[str] = []

    for period in scenario.periods:
        # 单请求最小计费输入量是合同条款，先作用在"每请求输入量"上
        per_req_input = period.avg_input_tokens
        if (
            rule.min_billable_input_tokens_per_request is not None
            and per_req_input < Decimal(rule.min_billable_input_tokens_per_request)
        ):
            per_req_input = Decimal(rule.min_billable_input_tokens_per_request)

        demand = Decimal(period.request_count)
        served = demand * period.success_rate
        failed = demand - served
        billable = demand if rule.billable_on_failure else served
        if period.success_rate < 1 and not rule.billable_on_failure:
            warnings.append(
                f"period_has_failures:{period.label}: 成功率 {period.success_rate}，"
                "失败请求按合同口径（billable_on_failure=False）不计费"
            )

        applied, high, rule_label = _effective_cache_fractions(
            period.cached_input_fraction, rule
        )
        cache_rule_labels.append(f"{period.label}:{rule_label}")

        (
            uncached,
            cached,
            out_tokens,
            in_rev,
            out_rev,
            revenue,
        ) = _amounts(
            billable_requests=billable,
            avg_input_tokens=per_req_input,
            avg_output_tokens=period.avg_output_tokens,
            cached_fraction=applied,
            rule=rule,
            batch=period.batch,
        )
        # 收入区间：下界 = 最大可辩护折扣；上界 = 完全不打折
        _, _, _, _, _, lower = _amounts(
            billable_requests=billable,
            avg_input_tokens=per_req_input,
            avg_output_tokens=period.avg_output_tokens,
            cached_fraction=high,
            rule=rule,
            batch=period.batch,
        )
        _, _, _, _, _, upper = _amounts(
            billable_requests=billable,
            avg_input_tokens=per_req_input,
            avg_output_tokens=period.avg_output_tokens,
            cached_fraction=Decimal(0),
            rule=rule,
            batch=period.batch,
        )

        gross = in_rev + out_rev
        discount = gross - revenue
        rate = demand / (period.duration_hours * SECONDS_PER_HOUR)
        if period.is_peak and (peak_rate is None or rate > peak_rate):
            peak_rate = rate
            peak_output_tps = rate * period.avg_output_tokens
            peak_input_tokens = per_req_input
            peak_output_tokens = period.avg_output_tokens

        periods.append(
            PeriodRevenue(
                label=period.label,
                demand_requests=demand,
                served_requests=served,
                failed_requests=failed,
                billable_requests=billable,
                billable_input_tokens_uncached=uncached,
                billable_input_tokens_cached=cached,
                billable_output_tokens=out_tokens,
                input_revenue=in_rev,
                output_revenue=out_rev,
                gross_revenue=gross,
                batch_discount=discount,
                revenue=revenue,
                revenue_lower_bound=lower,
                revenue_upper_bound=upper,
                cache_rule=rule_label,
                request_rate_rps=rate,
                is_peak=period.is_peak,
            )
        )
        total += revenue
        total_lower += lower
        total_upper += upper
        total_input += per_req_input * billable
        total_output += out_tokens
        total_demand += demand
        total_served += served
        total_billable += billable

    factor = (
        Decimal(scenario.days_per_year)
        if scenario.periods_cover == "one_day"
        else Decimal(1)
    )

    detail = _capacity(
        evidence=capacity_evidence,
        peak_rate=peak_rate,
        peak_output_tps=peak_output_tps,
        peak_input_tokens=peak_input_tokens,
        peak_output_tokens=peak_output_tokens,
        limits=sla_limits,
    )

    if detail.overall == "exceeds_capacity":
        warnings.append("需求峰值超出已验证产能：需要更多资源或更低负载才可成立")
    elif detail.overall == "partially_verified":
        warnings.append(
            "容量仅**部分验证**：部分子项证据不足，不得当作容量可行"
        )
    if detail.overall != "within_capacity":
        warnings.append(
            "容量未完整验证：本收入情景**只作情景分析**，不得作为产能或 SLA 承诺依据"
        )

    notes = [
        "收入从业务请求量推算，未使用第一阶段的理论满载收入口径",
        "收入区间 = [最大折扣下界, 完全不打折上界]；基准值不是保守下界（缓存价更低时折扣越多收入越低）",
        "请求量分三段记录：需求 / 成功服务 / 可计费，失败请求是否计费由合同口径决定",
    ]
    if scenario.periods_cover == "one_day":
        notes.append(f"时段表按一天计算，年度收入 = 时段合计 × {scenario.days_per_year} 天")

    return RevenueResult(
        scenario_id=scenario.scenario_id,
        model_name=scenario.model_name,
        billing_rule_id=rule.rule_id,
        currency=rule.currency,
        currency_status=rule.currency_status,
        billing_rule_status=rule.status,
        periods_cover=scenario.periods_cover,
        periods=periods,
        period_revenue_total=total,
        annual_revenue=total * factor,
        annual_billable_input_tokens=total_input * factor,
        annual_billable_output_tokens=total_output * factor,
        annual_requests=int(total_demand * factor),
        annual_demand_requests=total_demand * factor,
        annual_served_requests=total_served * factor,
        annual_billable_requests=total_billable * factor,
        annual_failed_requests=(total_demand - total_served) * factor,
        annual_revenue_lower_bound=total_lower * factor,
        annual_revenue_upper_bound=total_upper * factor,
        cache_rule="; ".join(cache_rule_labels),
        peak_request_rate_rps=peak_rate,
        capacity_check=detail.overall,
        capacity_detail=detail,
        capacity_basis=detail.reasons,
        scenario_only=detail.overall != "within_capacity",
        warnings=list(dict.fromkeys(warnings)),
        notes=notes,
    )
