"""M29：基于业务请求量的 Token 计费与收益情景（纯计算）。

与第一阶段的关系：

* 第一阶段的理论满载 Token 收入继续保留，**但本模块不把它当真实营业收入**；
* 收入一律按"请求量 × 输入输出分布 × 计费规则 × 使用时段"推算；
* 与第三阶段的**可信容量**比对后才可能给出 ``within_capacity``，
  没有可信实测时恒为 ``unknown_no_measurement``。

缓存口径（任务书 §三.2 明令）：

* ``BillingRule.billable_cache_ratio`` 是**合同计费口径**，来源由
  ``billable_cache_basis`` 声明；引擎物理 prefix cache 命中率**不是**合法来源；
* 业务时段给出的 ``cached_input_fraction`` 是业务侧可复用前缀估计，
  实际计入缓存价的占比取 ``min(业务估计, 合同口径)`` —— 只可能更保守。
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from llm_capacity_pricing.schemas.commercial import (
    BillingRule,
    PeriodRevenue,
    RevenueResult,
    RevenueScenario,
)

#: 每百万 token 的换算基数
PER_MILLION = Decimal("1000000")
SECONDS_PER_HOUR = Decimal("3600")


@dataclass(frozen=True)
class CapacityEvidence:
    """第三阶段给出的**可信**容量证据（由服务层判定是否 verified，本模块不自行放宽）。"""

    run_id: str
    #: 该证据是否满足"exact_match + 可信实测"（由 SLA/匹配层判定）
    is_verified: bool
    configured_concurrency: int | None = None
    #: 实测请求吞吐（req/s）
    request_throughput_rps: Decimal | None = None
    #: 实测输出吞吐（tok/s）
    output_throughput_tok_s: Decimal | None = None
    match_level: str | None = None
    data_trust: str | None = None


def _effective_cached_fraction(
    period_cached: Decimal, rule: BillingRule, warnings: list[str]
) -> Decimal:
    """实际计入缓存价的输入占比 = min(业务估计, 合同口径)。"""
    if rule.billable_cache_ratio is None:
        warnings.append(
            "billable_cache_ratio_missing: 计费规则未定义可计费缓存比例，"
            "直接采用业务侧估计（未受合同上限约束，结果可能偏乐观）"
        )
        return period_cached
    return min(period_cached, rule.billable_cache_ratio)


def compute_revenue(
    scenario: RevenueScenario,
    rule: BillingRule,
    *,
    capacity_evidence: CapacityEvidence | None = None,
) -> RevenueResult:
    """按业务需求情景计算计费量与收入。缺单价即不可算，不用 0 顶替。"""
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

    if rule.billable_cache_basis == "physical_cache_hit_rate":
        raise ValueError(
            "billable_cache_basis 不得为 physical_cache_hit_rate："
            "引擎物理命中率不是计费口径"
        )
    if rule.billable_cache_basis in ("assumed", "unknown"):
        warnings.append(
            f"billable_cache_basis={rule.billable_cache_basis}: "
            "可计费缓存比例尚未由合同确认，收入含未确认假设"
        )

    periods: list[PeriodRevenue] = []
    total = Decimal(0)
    total_input = Decimal(0)
    total_output = Decimal(0)
    total_requests = 0
    peak_rate: Decimal | None = None
    peak_output_tps: Decimal | None = None

    for period in scenario.periods:
        # 单请求最小计费输入量是合同条款，先作用在"每请求输入量"上
        per_req_input = period.avg_input_tokens
        if (
            rule.min_billable_input_tokens_per_request is not None
            and per_req_input < Decimal(rule.min_billable_input_tokens_per_request)
        ):
            per_req_input = Decimal(rule.min_billable_input_tokens_per_request)

        input_tokens = per_req_input * Decimal(period.request_count)
        cached_fraction = _effective_cached_fraction(
            period.cached_input_fraction, rule, warnings
        )
        cached_tokens = input_tokens * cached_fraction
        uncached_tokens = input_tokens - cached_tokens
        output_tokens = period.avg_output_tokens * Decimal(period.request_count)

        input_revenue = (
            uncached_tokens / PER_MILLION * rule.price_uncached_input_per_1m
            + cached_tokens / PER_MILLION * rule.price_cached_input_per_1m
        )
        output_revenue = output_tokens / PER_MILLION * rule.price_output_per_1m
        gross = input_revenue + output_revenue
        discount = Decimal(0)
        if period.batch and rule.batch_discount_fraction is not None:
            discount = gross * rule.batch_discount_fraction
        revenue = gross - discount

        rate = Decimal(period.request_count) / (period.duration_hours * SECONDS_PER_HOUR)
        if period.is_peak and (peak_rate is None or rate > peak_rate):
            peak_rate = rate
            peak_output_tps = rate * period.avg_output_tokens

        periods.append(
            PeriodRevenue(
                label=period.label,
                billable_input_tokens_uncached=uncached_tokens,
                billable_input_tokens_cached=cached_tokens,
                billable_output_tokens=output_tokens,
                input_revenue=input_revenue,
                output_revenue=output_revenue,
                gross_revenue=gross,
                batch_discount=discount,
                revenue=revenue,
                request_rate_rps=rate,
                is_peak=period.is_peak,
            )
        )
        total += revenue
        total_input += input_tokens
        total_output += output_tokens
        total_requests += period.request_count

    if scenario.periods_cover == "one_day":
        factor = Decimal(scenario.days_per_year)
    else:
        factor = Decimal(1)

    # ---- 与可信容量比对（M29 × M23/M24）--------------------------------
    capacity_check = "unknown_no_measurement"
    capacity_basis: list[str] = []
    if capacity_evidence is None or not capacity_evidence.is_verified:
        capacity_basis.append(
            "没有可信实测容量证据（需 exact_match 的实测记录）：无法判定需求是否落在产能内"
        )
        if capacity_evidence is not None:
            capacity_basis.append(
                f"已排除不可信证据: run={capacity_evidence.run_id} "
                f"match={capacity_evidence.match_level} trust={capacity_evidence.data_trust}"
            )
    elif peak_rate is None or peak_output_tps is None:
        capacity_basis.append("需求情景没有标记峰值时段（is_peak）：不做容量判定")
    else:
        ev = capacity_evidence
        available = ev.output_throughput_tok_s
        if available is None:
            capacity_basis.append(
                f"实测证据 {ev.run_id} 没有输出吞吐指标：无法判定"
            )
        else:
            capacity_basis.append(
                f"依据实测 {ev.run_id}（并发 {ev.configured_concurrency}）"
                f"输出吞吐 {available} tok/s"
            )
            capacity_basis.append(
                f"峰值需求 {peak_output_tps} tok/s（{peak_rate} req/s × 峰值时段 tok/req）"
            )
            capacity_check = (
                "within_capacity" if peak_output_tps <= available else "exceeds_capacity"
            )

    if capacity_check == "exceeds_capacity":
        warnings.append(
            "需求峰值超出已验证产能：该情景需要更多资源或更低负载才可成立"
        )
    if capacity_check == "unknown_no_measurement":
        warnings.append(
            "容量未验证：本收入情景**只作情景分析**，不得作为产能或 SLA 承诺依据"
        )

    notes = [
        "收入从业务请求量推算，未使用第一阶段的理论满载收入口径",
        "缓存输入按 min(业务侧估计, 计费口径) 计入缓存价，只会更保守",
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
        annual_requests=int(Decimal(total_requests) * factor),
        peak_request_rate_rps=peak_rate,
        capacity_check=capacity_check,  # type: ignore[arg-type]
        capacity_basis=capacity_basis,
        scenario_only=capacity_check != "within_capacity",
        warnings=list(dict.fromkeys(warnings)),
        notes=notes,
    )
