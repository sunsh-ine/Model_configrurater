"""M31：多候选"资源 + 性能 + 经济性"并列比较（不做单一总分）。

设计约束：

* **不产生综合评分**。成本低不构成 SLA 结论，性能达标也不构成报价资格，
  因此这里只做**并列视图**与**可比性判定**；
* **跨口径不可直接比经济性**：币种不同、买断 vs 租赁、年限不同，
  一律标为 ``not_comparable_*``，且**不进入**最低成本候选的评选；
* 性能维度**只读**第三阶段的结论，不做任何改写或再判定。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

from llm_capacity_pricing.schemas.commercial import QuoteEligibility, RevenueResult
from llm_capacity_pricing.schemas.decision import (
    DeploymentComparison,
    DeploymentComparisonEntry,
)
from llm_capacity_pricing.engines.tco import currencies_comparable
from llm_capacity_pricing.schemas.performance import DeploymentRanking
from llm_capacity_pricing.schemas.tco import DeploymentCostPlan, TcoResult


@dataclass(frozen=True)
class CandidateEconomics:
    """一个候选在经济侧的三个可选产物。缺哪个就留 None，不做替代。"""

    candidate_id: str
    plan: DeploymentCostPlan | None = None
    tco: TcoResult | None = None
    revenue: RevenueResult | None = None
    eligibility: QuoteEligibility | None = None


def _margin_allowed(
    tco: TcoResult | None, revenue: RevenueResult | None
) -> tuple[bool, str | None]:
    """利润/利润率是否可算（V4.1 F4.5）。

    必须同时满足：成本与收入都存在、成本结论完整、双方币种一致且已知。
    否则不给数字 —— 例如 USD 收入减 CNY 成本得出的"利润 200"没有经济含义。
    """
    if tco is None:
        return False, "cost_missing"
    if revenue is None:
        return False, "revenue_missing"
    if tco.incomplete:
        return False, "cost_incomplete"
    comparable, reason = currencies_comparable(tco.currency, revenue.currency)
    if not comparable:
        return False, reason
    if tco.annual_total_cost is None:
        return False, "annual_cost_missing"
    return True, None


def _comparability(
    tco: TcoResult | None,
    reference: TcoResult | None,
) -> tuple[str, list[str]]:
    """相对参照方案的可比性判定。"""
    if tco is None:
        return "incomplete_cost", ["该候选没有成本核算结果"]
    # 不完整的成本**永远**不可比 —— 这一条必须先于"没有参照方案"判断，
    # 否则"只有一个候选且它不完整"会被误判为可比。
    if tco.incomplete:
        return "incomplete_cost", ["成本不完整（缺价格或缺假设），不参与成本高低比较"]
    if reference is None:
        return "comparable", []
    notes: list[str] = []
    if reference.currency and tco.currency and tco.currency != reference.currency:
        notes.append(
            f"币种不一致（{tco.currency} vs {reference.currency}）：不得直接比较金额"
        )
        return "not_comparable_currency", notes
    if tco.cost_basis != reference.cost_basis:
        notes.append(
            f"口径不一致（{tco.cost_basis} vs {reference.cost_basis}）："
            "买断的一次性投入与租赁的年支出不是同一口径"
        )
        return "not_comparable_basis", notes
    if tco.horizon_years != reference.horizon_years:
        notes.append(
            f"周期不一致（{tco.horizon_years} 年 vs {reference.horizon_years} 年）："
            "全周期总额不可直接比较"
        )
        return "not_comparable_horizon", notes
    return "comparable", notes


def compare_deployments(
    *,
    requirement_id: str,
    ranking: DeploymentRanking,
    economics: Sequence[CandidateEconomics],
) -> DeploymentComparison:
    """把第三阶段的排序结果与第四阶段的成本/收益/门禁结论并列展示。"""
    by_candidate: Mapping[str, CandidateEconomics] = {e.candidate_id: e for e in economics}

    # 参照方案：第一个"成本完整"的方案。没有参照就不给任何成本高低结论。
    reference: TcoResult | None = None
    for entry in ranking.entries:
        econ = by_candidate.get(entry.candidate_id)
        if econ and econ.tco and not econ.tco.incomplete:
            reference = econ.tco
            break

    entries: list[DeploymentComparisonEntry] = []
    for ranked in ranking.entries:
        econ = by_candidate.get(ranked.candidate_id)
        tco = econ.tco if econ else None
        plan = econ.plan if econ else None
        revenue = econ.revenue if econ else None
        eligibility = econ.eligibility if econ else None

        comparability, notes = _comparability(tco, reference)

        # ---- 利润只在口径可比时才给（V4.1 F4.5）----
        annual_revenue = revenue.annual_revenue if revenue else None
        annual_total = tco.annual_total_cost if tco else None
        margin_ok, margin_reason = _margin_allowed(tco, revenue)
        annual_margin = None
        margin_ratio = None
        if margin_ok and annual_revenue is not None and annual_total is not None:
            annual_margin = annual_revenue - annual_total
            if annual_revenue != 0:
                margin_ratio = annual_margin / annual_revenue
        elif margin_reason:
            # 口径不可比时**不给数字**，只给原因 —— 避免"看似精确的 Decimal"
            notes.append(f"margin_not_evaluable:{margin_reason}")

        if tco is not None and tco.incomplete:
            notes.append("成本结论**不完整**，不得据此报价；原因见 TCO 明细")

        entries.append(
            DeploymentComparisonEntry(
                candidate_id=ranked.candidate_id,
                hardware_id=ranked.hardware_id,
                gpu_count=ranked.gpu_count,
                tp_size=ranked.tp_size,
                dp_size=ranked.dp_size,
                static_status=ranked.static_status,
                match_level=ranked.match_level,
                sla_status=ranked.sla_status,
                rank_bucket=ranked.rank_bucket,
                output_throughput_tok_s=ranked.output_throughput_tok_s,
                ttft_p99_ms=ranked.ttft_p99_ms,
                tpot_p99_ms=ranked.tpot_p99_ms,
                evidence_run_ids=list(ranked.evidence_run_ids),
                plan_id=plan.plan_id if plan else None,
                cost_basis=plan.cost_basis if plan else None,
                currency=tco.currency if tco else None,
                capex_one_time=tco.capex_one_time if tco else None,
                annual_cash_opex=tco.annual_cash_opex if tco else None,
                annual_total_cost=annual_total,
                total_cash_over_horizon=(
                    tco.total_cash_over_horizon if tco and not tco.incomplete else None
                ),
                cost_per_gpu_year=tco.cost_per_gpu_year if tco else None,
                cost_incomplete=bool(tco.incomplete) if tco else True,
                annual_revenue=annual_revenue,
                annual_gross_margin=annual_margin,
                margin_ratio=margin_ratio,
                capacity_check=revenue.capacity_check if revenue else None,
                quote_publishable=bool(eligibility.quote_publishable) if eligibility else False,
                quote_blockers=list(eligibility.blockers) if eligibility else [],
                comparability=comparability,  # type: ignore[arg-type]
                notes=notes,
            )
        )

    comparable = [
        e
        for e in entries
        if e.comparability == "comparable" and e.total_cash_over_horizon is not None
    ]
    lowest = None
    if len(comparable) > 1:
        lowest = min(
            comparable, key=lambda e: (e.total_cash_over_horizon, e.candidate_id)
        ).candidate_id

    notes: list[str] = [
        "本比较**不产生综合评分**：成本、性能、报价资格三者独立，任一都不能替代另一个",
        "性能维度为第三阶段结论的原样展示，未做重新判定",
        "币种未确认、买断/租赁混用、周期不同的候选之间不比较金额高低",
        "未使用经济性评分（币种与合同价未确认前禁止）",
        "利润/利润率只在**成本完整、双方币种一致**时给出；否则只给不可评估的原因",
    ]
    if reference is None:
        notes.append("没有任何候选具备**完整**成本结论：不给出成本高低比较")
    elif lowest is None:
        notes.append("可比且成本完整的候选不足 2 个：不给出最低成本候选")

    return DeploymentComparison(
        requirement_id=requirement_id,
        entries=entries,
        lowest_cost_candidate_id=lowest,
        comparability_notes=notes,
        composite_score_used=False,
        economic_scoring_used=False,
    )
