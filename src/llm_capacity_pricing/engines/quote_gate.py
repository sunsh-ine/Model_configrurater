"""M30：正式报价资格门禁（纯判定，fail-closed）。

四个维度**相互独立**，任何一个都不能替代另一个：

======================  ==========================================================
``cost_estimate_status``   ``TcoResult`` 完整、币种已确认、不含合成价格
``sla_verification_status`` 第三阶段 ``SlaEvaluation.status == observed_pass``
                           **且**匹配等级为 ``exact_match``（沿用第三阶段门禁，不放宽）
``commercial_confirmation_status`` 人工确认币种 / 价格 / 条款
``approval_status``        人工审批
======================  ==========================================================

不知道、没确认、缺数据一律**不通过**。函数没有任何参数可以绕过门禁：
``approval_status`` 必须由调用方从**已落库的人工审批记录**传入，Agent 不得自行填
``approved``（该约束由 services 层的 API 形状保证：本函数不接受"因为所以"式理由）。
"""

from __future__ import annotations

from datetime import date

from llm_capacity_pricing.schemas.commercial import (
    CommercialConfirmation,
    QuoteEligibility,
)
from llm_capacity_pricing.schemas.tco import TcoResult

#: 第三阶段门禁在第四阶段的**唯一**合法通过值
_SLA_PASS = "observed_pass"
_REQUIRED_MATCH_LEVEL = "exact_match"


def _cost_dimension(
    tco: TcoResult | None, blockers: list[str], cautions: list[str]
) -> str:
    if tco is None:
        blockers.append("cost_estimate_missing: 没有该方案的成本核算结果")
        return "unknown"
    if tco.contains_synthetic_prices:
        blockers.append(
            "cost_contains_synthetic_prices: 成本使用了合成示例价格，不得进入正式报价"
        )
    if not tco.currency_confirmed:
        blockers.append(
            "cost_currency_not_confirmed: 成本币种未确认"
            + (f"（当前 {tco.currency!r}，状态未确认）" if tco.currency else "（未声明币种）")
        )
    if tco.incomplete:
        for reason in tco.completeness_reasons:
            blockers.append(f"cost_incomplete:{reason}")
        if tco.unknown_items:
            blockers.append(
                f"cost_unknown_items:{tco.unknown_items}（未知不等于零，不得按 0 报价）"
            )
        return "incomplete"
    if tco.notes:
        cautions.extend(tco.notes)
    return "complete"


def _sla_dimension(
    sla_status: str | None,
    match_level: str | None,
    blockers: list[str],
    cautions: list[str],
) -> str:
    if sla_status is None:
        blockers.append("sla_not_evaluated: 未做 SLA 评估")
        return "unverified"
    if sla_status == _SLA_PASS and match_level == _REQUIRED_MATCH_LEVEL:
        return "verified_measured"
    if sla_status == _SLA_PASS:
        # 理论上第三阶段已拦住；这里再拦一次，防止上游传入不一致的组合
        blockers.append(
            f"sla_pass_without_exact_match: SLA 状态为 observed_pass 但匹配等级为 "
            f"{match_level!r}，不得作为性能承诺依据"
        )
        return "not_evaluable"
    if sla_status == "estimated_only":
        blockers.append(
            "sla_estimated_only: 只有估算值，估算不等于实测，不构成性能承诺"
        )
        return "estimated_only"
    blockers.append(
        f"sla_not_verified:{sla_status}: 需要 exact_match 的实测支撑性能承诺"
    )
    return "not_evaluable"


def _commercial_dimension(
    confirmation: CommercialConfirmation | None,
    as_of: date,
    blockers: list[str],
) -> str:
    if confirmation is None:
        blockers.append("commercial_confirmation_missing: 没有人工商务确认记录")
        return "missing"
    status = confirmation.status
    if not confirmation.currency_confirmed:
        blockers.append("commercial_currency_unconfirmed: 币种未经人工确认")
    if not confirmation.price_confirmed:
        blockers.append("commercial_price_unconfirmed: 合同/目录价格未经人工确认")
    if not confirmation.terms_confirmed:
        blockers.append("commercial_terms_unconfirmed: 计费条款未经人工确认")
    if confirmation.valid_until is not None and confirmation.valid_until < as_of:
        blockers.append(
            f"commercial_confirmation_expired: 确认已于 {confirmation.valid_until} 失效"
        )
    return status


def evaluate_quote_eligibility(
    *,
    candidate_id: str,
    tco: TcoResult | None,
    sla_status: str | None,
    match_level: str | None,
    confirmation: CommercialConfirmation | None,
    approval_status: str,
    approval_ref: str | None = None,
    plan_id: str | None = None,
    scenario_id: str | None = None,
    billing_rule_id: str | None = None,
    as_of: date | None = None,
) -> QuoteEligibility:
    """判定该候选是否可以发布**正式报价**。"""
    today = as_of or date.today()
    blockers: list[str] = []
    cautions: list[str] = []

    cost_status = _cost_dimension(tco, blockers, cautions)
    sla_verification = _sla_dimension(sla_status, match_level, blockers, cautions)
    commercial_status = _commercial_dimension(confirmation, today, blockers)

    # 声明"已审批"却没有凭据：不作为有效审批，按 missing 上报并写明原因
    # （schema 层同样禁止"approved 但无凭据"的 QuoteEligibility）
    effective_approval = approval_status
    if approval_status == "approved":
        if not approval_ref:
            effective_approval = "missing"
            blockers.append(
                "approval_ref_missing: 声明已审批但未提供审批凭据（无凭据不算审批通过）"
            )
    elif approval_status == "pending":
        blockers.append("approval_pending: 人工审批尚未完成")
    else:
        blockers.append(f"approval_missing:{approval_status}: 缺少人工审批记录")

    if cost_status == "complete" and sla_verification == "verified_measured":
        cautions.append(
            "性能结论来自匹配实测；报价有效期内的 SLA 承诺仍需按合同条款复核"
        )

    publishable = (
        cost_status == "complete"
        and sla_verification == "verified_measured"
        and commercial_status == "confirmed"
        and effective_approval == "approved"
        and bool(approval_ref)
        and not blockers
    )

    return QuoteEligibility(
        candidate_id=candidate_id,
        plan_id=plan_id,
        scenario_id=scenario_id,
        billing_rule_id=billing_rule_id,
        cost_estimate_status=cost_status,  # type: ignore[arg-type]
        sla_verification_status=sla_verification,  # type: ignore[arg-type]
        commercial_confirmation_status=commercial_status,  # type: ignore[arg-type]
        approval_status=effective_approval,  # type: ignore[arg-type]
        approval_ref=approval_ref,
        quote_publishable=publishable,
        blockers=sorted(dict.fromkeys(blockers)),
        cautions=sorted(dict.fromkeys(cautions)),
    )
