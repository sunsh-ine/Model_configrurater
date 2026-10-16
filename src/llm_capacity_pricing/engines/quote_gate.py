"""M30 / V4.1 F4.1：正式报价资格门禁（纯判定，fail-closed）。

**V4.1 的核心修正**：门禁不再接受调用方传来的状态字符串。
``evaluate_quote_eligibility`` 只接收**已经解析好的持久化记录**：

* ``SlaEvidenceRecord`` —— 服务端用它的需求快照与目标身份**重新推导**匹配等级与
  SLA 状态，并把重新推导的结果与记录里的结论比对；不一致即为证据失效；
* ``QuoteApproval`` —— 必须存在、作用域匹配当前候选/需求/方案、
  成本快照指纹一致、且在有效期内；
* 成本快照指纹 —— 由 ``tco_snapshot_hash`` 现算，审批必须绑定同一个。

四个维度相互独立，不知道、没确认、缺数据一律**不通过**。

诊断用的 ``quote_eligibility_from_claims`` 保留原始字符串入口，
但它**永远**返回 ``quote_publishable=False``，且结果带 ``claims_only=True``。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from llm_capacity_pricing.schemas.commercial import (
    BillingRule,
    CommercialConfirmation,
    ConfirmationScope,
    QuoteApproval,
    QuoteEligibility,
    SlaEvidenceRecord,
)
from llm_capacity_pricing.schemas.tco import TcoResult

#: 第三阶段门禁在第四阶段的**唯一**合法通过值
_SLA_PASS = "observed_pass"
_REQUIRED_MATCH_LEVEL = "exact_match"

#: 正式报价允许的数据可信等级（V4.2 E1）。
#: 这是**第四阶段自己的**门禁：即便调用方在第三阶段显式开了 allow_synthetic，
#: 正式报价也一律拒绝 synthetic_fixture —— 没有任何策略参数可以放宽它。
PRODUCTION_TRUST_LEVELS: tuple[str, ...] = (
    "measured_internal",
    "measured_vendor_published",
)


@dataclass
class SlaRecheck:
    """报价时对 SLA 证据的**重新推导**结果（服务层负责生成）。"""

    ok: bool
    match_level: str | None = None
    status: str | None = None
    run_id: str | None = None
    #: 重放得到的代表运行的可信等级（V4.2 E1）
    data_trust: str | None = None
    #: 重放得到的匹配证明是否仅限算法验证
    algorithm_validation_only: bool = False
    #: 重放时目标身份是否与证据记录一致（V4.2 E3）
    target_identity_ok: bool = True
    #: 重放时 run 池指纹是否与证据记录一致（V4.2 E3）
    run_pool_hash_ok: bool = True
    reasons: list[str] = field(default_factory=list)


def billing_rule_snapshot_hash(rule: BillingRule | None) -> str | None:
    """计费规则**内容**指纹（S0）。

    只绑定 ``rule_id`` 无法发现"同一个规则被改了单价或条款"，
    因此把决定报价金额的字段全部纳入指纹。
    """
    if rule is None:
        return None
    import hashlib
    import json

    payload = {
        "rule_id": rule.rule_id,
        "model_name": rule.model_name,
        "currency": rule.currency,
        "currency_status": rule.currency_status,
        "price_uncached_input_per_1m": str(rule.price_uncached_input_per_1m),
        "price_cached_input_per_1m": str(rule.price_cached_input_per_1m),
        "price_output_per_1m": str(rule.price_output_per_1m),
        "billable_cache_ratio": str(rule.billable_cache_ratio),
        "billable_cache_basis": rule.billable_cache_basis,
        "batch_discount_fraction": str(rule.batch_discount_fraction),
        "min_billable_input_tokens_per_request": rule.min_billable_input_tokens_per_request,
        "billable_on_failure": rule.billable_on_failure,
        "status": rule.status,
        "valid_from": rule.valid_from.isoformat() if rule.valid_from else None,
        "valid_to": rule.valid_to.isoformat() if rule.valid_to else None,
    }
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


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
    if tco.coverage_policy_authority != "confirmed":
        blockers.append(
            f"cost_coverage_policy_not_authorized:{tco.coverage_policy_authority}"
            "（覆盖策略决定「哪些成本必须算」，未授权/草稿策略不能支撑生产级完整 TCO）"
        )
    if tco.unconfirmed_items:
        blockers.append(
            f"cost_items_unconfirmed:{tco.unconfirmed_items}"
            "（价格状态不是 confirmed，报价前必须逐项确认）"
        )
    if tco.incomplete:
        for reason in tco.completeness_reasons:
            blockers.append(f"cost_incomplete:{reason}")
        if tco.unknown_items:
            blockers.append(
                f"cost_unknown_items:{tco.unknown_items}（未知不等于零，不得按 0 报价）"
            )
        if tco.coverage_gaps:
            blockers.append(
                f"cost_coverage_gaps:{tco.coverage_gaps}"
                "（这些成本类别既未选入也未显式排除：所谓完整 TCO 其实漏了成本）"
            )
        if tco.applicability_errors:
            blockers.append(
                f"cost_price_not_applicable:{tco.applicability_errors}"
                "（价格不适用于本方案，已从总额剔除）"
            )
        return "incomplete"
    if tco.validity_unknown_items:
        cautions.append(
            f"price_validity_unknown:{tco.validity_unknown_items}"
            "（价格未声明有效期，报价前建议确认）"
        )
    cautions.extend(tco.notes)
    return "complete"


def _sla_dimension(
    *,
    candidate_id: str,
    requirement_id: str,
    sla_evidence: SlaEvidenceRecord | None,
    recheck: SlaRecheck | None,
    blockers: list[str],
    cautions: list[str],
) -> tuple[str, str | None]:
    """返回 ``(SLA 验证状态, 引用的证据 ID)``。"""
    if sla_evidence is None:
        blockers.append(
            "sla_evidence_missing: 未提供持久化的 SLA 证据记录"
            "（不接受调用方声明的 observed_pass 字符串）"
        )
        return "unverified", None

    evidence_id = sla_evidence.sla_evaluation_id
    if sla_evidence.candidate_id != candidate_id:
        blockers.append(
            f"sla_evidence_scope_mismatch:candidate {sla_evidence.candidate_id!r} != "
            f"{candidate_id!r}"
        )
        return "not_evaluable", evidence_id
    if sla_evidence.requirement_id != requirement_id:
        blockers.append(
            f"sla_evidence_scope_mismatch:requirement {sla_evidence.requirement_id!r} != "
            f"{requirement_id!r}"
        )
        return "not_evaluable", evidence_id

    if recheck is None:
        blockers.append("sla_evidence_not_reverified: 未对 SLA 证据做重新推导")
        return "not_evaluable", evidence_id
    if not recheck.ok:
        for reason in recheck.reasons:
            blockers.append(f"sla_recheck_failed:{reason}")
        return "not_evaluable", evidence_id
    # V4.2 E3：目标身份与 run 池必须与证据记录一致，否则旧证据不再适用
    if not recheck.target_identity_ok:
        blockers.append(
            "sla_target_identity_changed: 当前目标身份（模型/硬件/拓扑/负载）"
            "与证据记录不一致，旧证据不再适用"
        )
        return "not_evaluable", evidence_id
    if not recheck.run_pool_hash_ok:
        blockers.append(
            "sla_run_pool_changed: 证据池内容已变化（不是同一批实测数据），"
            "证据不可复现"
        )
        return "not_evaluable", evidence_id
    if recheck.status != _SLA_PASS or recheck.match_level != _REQUIRED_MATCH_LEVEL:
        blockers.append(
            f"sla_recheck_not_pass: 重新推导得到 status={recheck.status!r} "
            f"match_level={recheck.match_level!r}，不足以支撑性能承诺"
        )
        return "not_evaluable", evidence_id

    # 记录里的结论必须与重新推导一致，否则说明数据或结论已变
    if sla_evidence.status != recheck.status:
        blockers.append(
            f"sla_evidence_stale: 记录 status={sla_evidence.status!r} 与重新推导 "
            f"{recheck.status!r} 不一致"
        )
        return "not_evaluable", evidence_id
    if (sla_evidence.match_level or None) != recheck.match_level:
        blockers.append(
            f"sla_evidence_stale: 记录 match_level={sla_evidence.match_level!r} 与重新推导 "
            f"{recheck.match_level!r} 不一致"
        )
        return "not_evaluable", evidence_id
    if recheck.run_id and sla_evidence.matched_run_id != recheck.run_id:
        blockers.append(
            f"sla_evidence_stale: 记录 matched_run_id={sla_evidence.matched_run_id!r} "
            f"与重新推导 {recheck.run_id!r} 不一致"
        )
        return "not_evaluable", evidence_id

    # ---- V4.2 E1：生产可信度硬门禁（不可被任何策略放宽）----
    # 记录里声明的质量标记
    if sla_evidence.algorithm_validation_only:
        blockers.append(
            "sla_algorithm_validation_only: 该证据仅用于算法验证"
            "（第三阶段 allow_synthetic 路径），不得作为正式报价的性能依据"
        )
        return "not_evaluable", evidence_id
    if sla_evidence.allow_synthetic_used:
        blockers.append(
            "sla_allow_synthetic_used: 该次 SLA 评估启用了 allow_synthetic，"
            "属于算法验证口径，正式报价一律拒绝"
        )
        return "not_evaluable", evidence_id
    if sla_evidence.sla_proof_blocked:
        blockers.append(
            "sla_proof_blocked_by_match: 匹配证明声明禁止用于 sla_proof，"
            "不得作为正式报价的性能依据"
        )
        return "not_evaluable", evidence_id
    if sla_evidence.data_trust not in PRODUCTION_TRUST_LEVELS:
        blockers.append(
            f"sla_data_trust_not_production:{sla_evidence.data_trust}"
            f"（正式报价只接受 {list(PRODUCTION_TRUST_LEVELS)}）"
        )
        return "not_evaluable", evidence_id
    # 重放侧的质量标记也必须干净：重新推导的结论同样要过生产门禁
    if recheck.algorithm_validation_only:
        blockers.append(
            "sla_recheck_algorithm_validation_only: 重放得到的匹配证明仅限算法验证"
        )
        return "not_evaluable", evidence_id
    if recheck.data_trust is not None and recheck.data_trust not in PRODUCTION_TRUST_LEVELS:
        blockers.append(
            f"sla_recheck_data_trust_not_production:{recheck.data_trust}"
        )
        return "not_evaluable", evidence_id

    cautions.append(
        f"SLA 结论已按持久化证据 {evidence_id} 重新核验"
        f"（run={recheck.run_id}，trust={recheck.data_trust}）"
    )
    return "verified_measured", evidence_id


def _model_matches(rule_model: str | None, quote_model: str | None) -> bool:
    """计费规则的模型必须与报价模型一致（S0）。

    只做大小写/空白归一化，不做别名推断（不同代模型不等价）；
    任一侧缺失都无法确认一致性，按不通过处理。
    """
    from llm_capacity_pricing.perf.benchmark_matcher import normalize_name

    left, right = normalize_name(rule_model), normalize_name(quote_model)
    if left is None or right is None:
        return False
    return left == right


def _confirmation_scope_ok(
    scope_refs: ConfirmationScope | None,
    *,
    candidate_id: str,
    requirement_id: str,
    model_name: str | None,
    billing_rule_id: str | None,
) -> tuple[bool, str | None]:
    """商务确认必须与**当前**报价作用域一致（S0）。"""
    if scope_refs is None or not scope_refs.is_scoped:
        return False, "commercial_confirmation_scope_missing: 确认未声明结构化作用域"
    if candidate_id not in scope_refs.candidate_ids:
        return False, f"commercial_confirmation_scope_mismatch:candidate {candidate_id!r}"
    if requirement_id not in scope_refs.requirement_ids:
        return False, (
            f"commercial_confirmation_scope_mismatch:requirement {requirement_id!r}"
        )
    if model_name and scope_refs.model_names and model_name not in scope_refs.model_names:
        return False, f"commercial_confirmation_scope_mismatch:model {model_name!r}"
    if (
        billing_rule_id
        and scope_refs.billing_rule_ids
        and billing_rule_id not in scope_refs.billing_rule_ids
    ):
        return False, (
            f"commercial_confirmation_scope_mismatch:billing_rule {billing_rule_id!r}"
        )
    return True, None


def _commercial_dimension(
    confirmation: CommercialConfirmation | None,
    billing_rule: BillingRule | None,
    as_of: date,
    blockers: list[str],
    *,
    candidate_id: str,
    requirement_id: str,
    quote_model_name: str | None,
) -> tuple[str, str | None]:
    """商务维度 = 人工确认 + **计费规则有效**（V4.3：计费条款属于商务条件）。

    正式报价必然包含 Token 单价或计费条款，因此找不到规则、规则未确认或已失效
    一律阻断；规则身份还会与审批作用域比对。
    """
    if billing_rule is None:
        blockers.append(
            "billing_rule_missing: 正式报价必须绑定计费规则"
            "（找不到规则时不得输出计费条款）"
        )
    else:
        if billing_rule.status != "confirmed":
            blockers.append(
                f"billing_rule_not_confirmed:{billing_rule.status}"
                "（计费规则未经人工确认）"
            )
        if billing_rule.currency_status != "confirmed":
            blockers.append(
                f"billing_rule_currency_unconfirmed:{billing_rule.currency_status}"
            )
        if billing_rule.valid_from is not None and billing_rule.valid_from > as_of:
            blockers.append(f"billing_rule_not_yet_effective:{billing_rule.valid_from}")
        if billing_rule.valid_to is not None and billing_rule.valid_to < as_of:
            blockers.append(f"billing_rule_expired:{billing_rule.valid_to}")

    # 计费规则的模型必须与报价模型一致（S0）
    if billing_rule is not None and quote_model_name is not None:
        if not _model_matches(billing_rule.model_name, quote_model_name):
            blockers.append(
                f"billing_rule_model_mismatch: 计费规则是给 {billing_rule.model_name!r} 的，"
                f"报价模型为 {quote_model_name!r}"
            )

    if confirmation is None:
        blockers.append("commercial_confirmation_missing: 没有人工商务确认记录")
        return "missing", None
    scope_ok, scope_reason = _confirmation_scope_ok(
        confirmation.scope_refs,
        candidate_id=candidate_id,
        requirement_id=requirement_id,
        model_name=quote_model_name,
        billing_rule_id=billing_rule.rule_id if billing_rule else None,
    )
    if not scope_ok and scope_reason:
        blockers.append(scope_reason)
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
    return status, confirmation.confirmation_id


def _approval_dimension(
    *,
    approval: QuoteApproval | None,
    candidate_id: str,
    requirement_id: str,
    plan_id: str | None,
    scenario_id: str | None,
    billing_rule_id: str | None,
    cost_snapshot_hash: str | None,
    billing_rule_hash: str | None,
    as_of: date,
    blockers: list[str],
) -> tuple[str, str | None, str | None]:
    """返回 ``(审批状态, approval_id, approval_ref)``。"""
    if approval is None:
        blockers.append(
            "approval_record_missing: 没有人工审批记录"
            "（非空字符串不构成授权证明）"
        )
        return "missing", None, None

    approval_id = approval.approval_id
    if approval.decision != "approved":
        blockers.append(f"approval_not_approved:{approval.decision}")
        return "pending" if approval.decision == "pending" else "missing", approval_id, None

    scope = approval.scope
    if scope.candidate_id != candidate_id:
        blockers.append(
            f"approval_scope_mismatch:candidate {scope.candidate_id!r} != {candidate_id!r}"
        )
        return "missing", approval_id, None
    if scope.requirement_id != requirement_id:
        blockers.append(
            f"approval_scope_mismatch:requirement {scope.requirement_id!r} != "
            f"{requirement_id!r}"
        )
        return "missing", approval_id, None
    if plan_id is not None and scope.plan_id != plan_id:
        blockers.append(
            f"approval_scope_mismatch:plan {scope.plan_id!r} != {plan_id!r}"
        )
        return "missing", approval_id, None
    # V4.2 E3：需求情景与计费规则也属于审批作用域
    if scenario_id is not None and scope.scenario_id != scenario_id:
        blockers.append(
            f"approval_scope_mismatch:scenario {scope.scenario_id!r} != {scenario_id!r}"
        )
        return "missing", approval_id, None
    if billing_rule_id is not None and scope.billing_rule_id != billing_rule_id:
        blockers.append(
            f"approval_scope_mismatch:billing_rule {scope.billing_rule_id!r} != "
            f"{billing_rule_id!r}"
        )
        return "missing", approval_id, None
    # S0：同一个 rule_id 改了单价/条款，旧审批同样失效
    if scope.billing_rule_hash != billing_rule_hash:
        blockers.append(
            "approval_billing_rule_stale: 审批绑定的计费规则内容与当前不一致"
            f"（审批 {(scope.billing_rule_hash or 'none')[:12]}… vs 当前 "
            f"{(billing_rule_hash or 'none')[:12]}…）"
        )
        return "missing", approval_id, None
    if cost_snapshot_hash is None or scope.cost_snapshot_hash != cost_snapshot_hash:
        blockers.append(
            "approval_cost_snapshot_stale: 审批绑定的成本快照与当前成本不一致"
            f"（审批 {scope.cost_snapshot_hash[:12]}… vs 当前 "
            f"{(cost_snapshot_hash or 'none')[:12]}…）"
        )
        return "missing", approval_id, None
    if approval.valid_until is not None and approval.valid_until < as_of:
        blockers.append(
            f"approval_expired: 审批已于 {approval.valid_until} 失效"
        )
        return "missing", approval_id, None

    return "approved", approval_id, approval.document_ref


def evaluate_quote_eligibility(
    *,
    candidate_id: str,
    requirement_id: str,
    tco: TcoResult | None,
    sla_evidence: SlaEvidenceRecord | None,
    sla_recheck: SlaRecheck | None,
    approval: QuoteApproval | None,
    confirmation: CommercialConfirmation | None,
    billing_rule: BillingRule | None,
    cost_snapshot_hash: str | None,
    quote_model_name: str | None = None,
    plan_id: str | None = None,
    scenario_id: str | None = None,
    as_of: date | None = None,
) -> QuoteEligibility:
    """判定该候选是否可以发布**正式报价**。

    所有结论都来自**持久化记录 + 服务端重新推导**，没有任何参数可以放宽门禁。
    """
    today = as_of or date.today()
    blockers: list[str] = []
    cautions: list[str] = []

    cost_status = _cost_dimension(tco, blockers, cautions)
    sla_status, sla_evidence_id = _sla_dimension(
        candidate_id=candidate_id,
        requirement_id=requirement_id,
        sla_evidence=sla_evidence,
        recheck=sla_recheck,
        blockers=blockers,
        cautions=cautions,
    )
    commercial_status, confirmation_id = _commercial_dimension(
        confirmation,
        billing_rule,
        today,
        blockers,
        candidate_id=candidate_id,
        requirement_id=requirement_id,
        quote_model_name=quote_model_name,
    )
    approval_status, approval_id, approval_ref = _approval_dimension(
        approval=approval,
        candidate_id=candidate_id,
        requirement_id=requirement_id,
        plan_id=plan_id,
        scenario_id=scenario_id,
        # 作用域基线**只**来自实际使用的计费规则对象，避免"传了规则但没传 ID"的断点
        billing_rule_id=billing_rule.rule_id if billing_rule else None,
        cost_snapshot_hash=cost_snapshot_hash,
        billing_rule_hash=billing_rule_snapshot_hash(billing_rule),
        as_of=today,
        blockers=blockers,
    )

    if cost_status == "complete" and sla_status == "verified_measured":
        cautions.append(
            "性能结论来自匹配实测；报价有效期内的 SLA 承诺仍需按合同条款复核"
        )

    publishable = (
        cost_status == "complete"
        and sla_status == "verified_measured"
        and commercial_status == "confirmed"
        and approval_status == "approved"
        and not blockers
    )

    return QuoteEligibility(
        candidate_id=candidate_id,
        plan_id=plan_id,
        scenario_id=scenario_id,
        cost_estimate_status=cost_status,  # type: ignore[arg-type]
        sla_verification_status=sla_status,  # type: ignore[arg-type]
        commercial_confirmation_status=commercial_status,  # type: ignore[arg-type]
        approval_status=approval_status,  # type: ignore[arg-type]
        sla_evaluation_id=sla_evidence_id,
        approval_id=approval_id,
        confirmation_id=confirmation_id,
        billing_rule_id=billing_rule.rule_id if billing_rule else None,
        cost_snapshot_hash=cost_snapshot_hash,
        approval_ref=approval_ref,
        quote_publishable=publishable,
        blockers=sorted(dict.fromkeys(blockers)),
        cautions=sorted(dict.fromkeys(cautions)),
        claims_only=False,
    )


def quote_eligibility_from_claims(
    *,
    candidate_id: str,
    tco: TcoResult | None = None,
    sla_status: str | None = None,
    match_level: str | None = None,
    approval_status: str = "missing",
    approval_ref: str | None = None,
) -> QuoteEligibility:
    """**仅用于内部诊断**的旧式入口：直接吃调用方声明的状态字符串。

    它永远返回 ``quote_publishable=False``，并在 blockers 里写明原因 ——
    保留它只是为了让历史用例与人工排查能复现"如果只看字符串会得到什么"，
    不能用来生成可发布的报价资格。
    """
    blockers = [
        "claims_only_never_publishable: 该入口只接受调用方声明的状态字符串，"
        "没有核验持久化的 SLA 证据、审批记录与成本快照，因此永不可发布；"
        "正式判定请使用 evaluate_quote_eligibility() 并传入证据 ID"
    ]
    return QuoteEligibility(
        candidate_id=candidate_id,
        cost_estimate_status=(
            "unknown" if tco is None else ("incomplete" if tco.incomplete else "complete")
        ),
        # 诊断路径**不**把声明升级为"已验证"：没有证据记录就没有 verified_measured
        sla_verification_status="unverified",
        commercial_confirmation_status="missing",
        approval_status="missing",
        approval_ref=approval_ref,
        quote_publishable=False,
        blockers=blockers,
        cautions=[
            f"调用方声明：sla_status={sla_status!r} match_level={match_level!r} "
            f"approval_status={approval_status!r}（均未被核验）"
        ],
        claims_only=True,
    )
