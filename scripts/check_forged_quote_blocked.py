#!/usr/bin/env python
"""V4.1 F4.1 反向场景断言：伪造的"已通过"声明必须被阻断。

评审指定的反向场景：**全部成本项标记为已确认 + 调用方声称 SLA 已通过 +
审批凭据只是一个任意字符串**。这份脚本把它固化成一个可执行断言：
如果任何一条入口给出了 ``quote_publishable=True``，脚本以非零退出。

用法（离线，不需要 GPU / 数据库文件）：

    python scripts/check_forged_quote_blocked.py

退出码 0 = 全部被阻断（预期），非零 = 存在可绕过门禁的路径（回归）。
"""

from __future__ import annotations

import sys
import tempfile
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from llm_capacity_pricing.agent_api import call_agent_op  # noqa: E402
from llm_capacity_pricing.engines.quote_gate import (  # noqa: E402
    SlaRecheck,
    billing_rule_snapshot_hash,
    evaluate_quote_eligibility,
    quote_eligibility_from_claims,
)
from llm_capacity_pricing.engines.tco import compute_tco  # noqa: E402
from llm_capacity_pricing.schemas.commercial import (  # noqa: E402
    ApprovalScope,
    BillingRule,
    CommercialConfirmation,
    ConfirmationScope,
    QuoteApproval,
)
from llm_capacity_pricing.schemas.tco import (  # noqa: E402
    CostCatalogItem,
    CostCoveragePolicy,
    CostCoverageRequirement,
    CostLineSelection,
    DeploymentCostPlan,
    FinancingAssumptions,
)
from llm_capacity_pricing.services import ServiceError, init_db, quote_eligibility_for  # noqa: E402

FAILURES: list[str] = []
CHECKED = 0


def check(label: str, blocked: bool, detail: str = "") -> None:
    global CHECKED
    CHECKED += 1
    if blocked:
        print(f"[ok]   {label}")
    else:
        FAILURES.append(label)
        print(f"[FAIL] {label} {detail}")


def confirmed_cost():
    """全部成本项 status=confirmed、币种已确认 —— 成本侧"看起来"完整。"""
    items = [
        CostCatalogItem(
            item_id="gpu", category="gpu_hardware", name="fixture gpu", basis="owned",
            price_basis="one_time_per_gpu", unit_price=Decimal("100000"),
            unit="currency_per_gpu", currency="CNY", currency_status="confirmed",
            status="confirmed", origin="internal_actual", evidence_id="ev_gpu",
        ),
        CostCatalogItem(
            item_id="power", category="power_energy", name="fixture power",
            price_basis="per_kwh", unit_price=Decimal("1"), unit="currency_per_kwh",
            currency="CNY", currency_status="confirmed", status="confirmed",
            origin="internal_actual", evidence_id="ev_power",
        ),
    ]
    plan = DeploymentCostPlan(
        plan_id="forged-plan", candidate_id="forged-cand",
        hardware_id="nvidia-h100-sxm-80gb", gpu_count=8, server_count=1,
        cost_basis="owned",
        assumptions=FinancingAssumptions(
            horizon_years=3, depreciation_years=3,
            residual_value_fraction=Decimal("0"),
            pue=Decimal("1.2"), pue_basis="assumed",
            gpu_power_watts=Decimal("1000"), gpu_power_basis="assumed",
            server_overhead_watts=Decimal("0"),
            it_power_utilization=Decimal("1"),
        ),
        selections=[CostLineSelection(item_id=i.item_id) for i in items],
    )
    # 覆盖策略也做成"完全已授权"，把成本侧的缺口全部堵死 ——
    # 反向场景要证明的是：即便四维看起来都齐备，门禁依然阻断。
    policy = CostCoveragePolicy(
        policy_id="forged", version="1.0", authority="confirmed",
        approved_by="示例批准人", approved_at="2026-01-01",
        document_ref="COV-FORGED-CHECK",
        requirements=[
            CostCoverageRequirement(category="gpu_hardware"),
            CostCoverageRequirement(category="power_energy"),
        ],
    )
    return compute_tco(plan, items, coverage_policy=policy), plan


def main() -> int:
    tco, plan = confirmed_cost()
    check("成本侧确实「完整」（否则这个反向场景不成立）", not tco.incomplete,
          f"incomplete={tco.incomplete} {tco.completeness_reasons}")

    # ---- 路径 1：仅凭调用方声明的状态字符串 ----
    forged = quote_eligibility_from_claims(
        candidate_id="forged-cand",
        tco=tco,
        sla_status="observed_pass",
        match_level="exact_match",
        approval_status="approved",
        approval_ref="一个随便写的字符串",
    )
    check("声明式入口不得发布报价", not forged.quote_publishable, str(forged.blockers))
    check("声明式入口必须打 claims_only 标记", forged.claims_only)
    check(
        "声明式入口不得声称 SLA 已验证",
        forged.sla_verification_status != "verified_measured",
        forged.sla_verification_status,
    )

    # ---- 路径 2：Agent 接口传状态字符串 / 缺证据引用 ----
    # 用临时库，避免在仓库里留下派生产物
    db = str(Path(tempfile.mkdtemp(prefix="forged-check-")) / "check.db")
    init_db(db)
    try:
        call_agent_op(
            "phase4.quote_eligibility", db=db, candidate_id="forged-cand",
            sla_status="observed_pass", match_level="exact_match",
            approval_status="approved", approval_ref="任意字符串",
        )
        check("Agent 接口必须拒绝状态字符串参数", False, "居然没有报错")
    except ServiceError as exc:
        check("Agent 接口必须拒绝状态字符串参数", "不接受参数" in str(exc), str(exc))

    try:
        call_agent_op("phase4.quote_eligibility", db=db, candidate_id="forged-cand",
                      requirement_id="r")
        check("Agent 接口必须拒绝缺失证据引用", False, "居然没有报错")
    except ServiceError as exc:
        check("Agent 接口必须拒绝缺失证据引用", "缺少证据引用" in str(exc), str(exc))

    # ---- 路径 3：服务层缺证据 ID ----
    for kwargs, label in (
        ({}, "服务层必须拒绝缺失 SLA 证据 ID"),
        ({"sla_evaluation_id": "sla-x"}, "服务层必须拒绝缺失审批 ID"),
    ):
        try:
            quote_eligibility_for(
                db, candidate_id="forged-cand", requirement_id="r", facts={}, **kwargs
            )
            check(label, False, "居然没有报错")
        except ServiceError as exc:
            check(label, "必须提供" in str(exc) or "id" in str(exc), str(exc))

    # ---- 路径 4：证据与审批都"存在"，但内容经不起核验 ----
    from datetime import date

    from llm_capacity_pricing.schemas.commercial import SlaEvidenceRecord

    fake_evidence = SlaEvidenceRecord(
        sla_evaluation_id="sla-forged", candidate_id="forged-cand",
        requirement_id="r",
        requirement={"requirement_id": "r", "ttft_p99_ms_max": 400},
        target_identity={"model_name": "m"},
        status="observed_pass", match_level="exact_match",
        matched_run_id="run-does-not-exist", supporting_run_ids=["run-does-not-exist"],
        data_trust="measured_internal", policy_id="default",
        evaluated_at="2026-10-15T10:00:00+08:00", run_pool_hash="0" * 64,
        input_snapshot_hash="1" * 64,
    )
    snapshot = "deadbeef" * 8
    forged_rule = BillingRule(
        rule_id="rule-forged", model_name="m", currency="CNY",
        currency_status="confirmed", price_uncached_input_per_1m=Decimal("4"),
        price_cached_input_per_1m=Decimal("1"), price_output_per_1m=Decimal("12"),
        billable_cache_ratio=Decimal("0.3"), billable_cache_basis="contract_defined",
        status="confirmed", evidence_id="ev-forged",
    )

    stale_approval = QuoteApproval(
        approval_id="apr-forged", decision="approved",
        approved_by="某个人", approved_at="2026-10-15T10:00:00+08:00",
        valid_until=date(2099, 12, 31), document_ref="任意凭据编号",
        scope=ApprovalScope(
            candidate_id="forged-cand", requirement_id="r", plan_id=plan.plan_id,
            billing_rule_id="rule-forged",
            # 计费规则内容与当前一致 —— 这样"失配的成本快照"才是唯一原因
            billing_rule_hash=billing_rule_snapshot_hash(forged_rule),
            cost_snapshot_hash="0" * 64,   # 与当前成本快照不一致
        ),
    )
    confirmation = CommercialConfirmation(
        confirmation_id="cfm-forged", scope="forged",
        scope_refs=ConfirmationScope(
            candidate_ids=["forged-cand"], requirement_ids=["r"],
            model_names=["m"], billing_rule_ids=["rule-forged"],
        ),
        currency_confirmed=True, price_confirmed=True, terms_confirmed=True,
        confirmed_by="某个人", confirmed_at="2026-10-15T10:00:00+08:00",
        valid_until=date(2099, 12, 31), document_ref="任意凭据",
    )
    result = evaluate_quote_eligibility(
        candidate_id="forged-cand", requirement_id="r", tco=tco,
        sla_evidence=fake_evidence,
        sla_recheck=SlaRecheck(ok=False, reasons=["matched_run_not_found"]),
        approval=stale_approval, confirmation=confirmation,
        billing_rule=forged_rule,
        cost_snapshot_hash=snapshot, plan_id=plan.plan_id,
    )
    check("SLA 重放失败必须阻断", not result.quote_publishable, str(result.blockers))
    check("失配的成本快照必须阻断审批",
          any("approval_cost_snapshot_stale" in b for b in result.blockers))

    # ---- 路径 5：算法验证口径的 observed_pass（V4.2 E1）----
    algo_evidence = fake_evidence.model_copy(
        update={
            "data_trust": "synthetic_fixture",
            "algorithm_validation_only": True,
            "allow_synthetic_used": True,
            "matched_run_id": "syn-run",
            "supporting_run_ids": ["syn-run"],
        }
    )
    algo_result = evaluate_quote_eligibility(
        candidate_id="forged-cand", requirement_id="r", tco=tco,
        sla_evidence=algo_evidence,
        sla_recheck=SlaRecheck(
            ok=True, match_level="exact_match", status="observed_pass",
            run_id="syn-run", data_trust="synthetic_fixture",
            algorithm_validation_only=True,
        ),
        approval=stale_approval.model_copy(
            update={
                "scope": stale_approval.scope.model_copy(
                    update={"cost_snapshot_hash": snapshot}
                )
            }
        ),
        confirmation=confirmation,
        billing_rule=forged_rule,
        cost_snapshot_hash=snapshot, plan_id=plan.plan_id,
    )
    check(
        "算法验证口径的 observed_pass 不得进入正式报价",
        not algo_result.quote_publishable,
        str(algo_result.blockers),
    )
    check(
        "必须点名 algorithm_validation_only / synthetic 可信等级",
        any(
            k in " ".join(algo_result.blockers)
            for k in (
                "sla_algorithm_validation_only",
                "sla_allow_synthetic_used",
                "sla_data_trust_not_production",
            )
        ),
        str(algo_result.blockers),
    )

    # ---- 路径 6：计费规则作用域（V4.3 P0-1）----
    # 审批绑定 rule-forged，但报价改用另一条计费规则 -> 必须阻断
    other_rule = forged_rule.model_copy(update={"rule_id": "rule-other"})
    rule_scope = evaluate_quote_eligibility(
        candidate_id="forged-cand", requirement_id="r", tco=tco,
        sla_evidence=fake_evidence,
        sla_recheck=SlaRecheck(
            ok=True, match_level="exact_match", status="observed_pass",
            run_id="run-does-not-exist", data_trust="measured_internal",
        ),
        approval=stale_approval.model_copy(
            update={
                "scope": stale_approval.scope.model_copy(
                    update={"cost_snapshot_hash": snapshot}
                )
            }
        ),
        confirmation=confirmation,
        billing_rule=other_rule,
        cost_snapshot_hash=snapshot, plan_id=plan.plan_id,
    )
    check(
        "计费规则与审批作用域不符必须阻断",
        any("approval_scope_mismatch:billing_rule" in b for b in rule_scope.blockers),
        str(rule_scope.blockers),
    )

    # ---- 路径 7：计费规则缺失 / 未确认（V4.3 P0-1）----
    no_rule = evaluate_quote_eligibility(
        candidate_id="forged-cand", requirement_id="r", tco=tco,
        sla_evidence=fake_evidence,
        sla_recheck=SlaRecheck(ok=False, reasons=["matched_run_not_found"]),
        approval=stale_approval, confirmation=confirmation,
        billing_rule=None,
        cost_snapshot_hash=snapshot, plan_id=plan.plan_id,
    )
    check(
        "没有计费规则时不得输出计费条款",
        any("billing_rule_missing" in b for b in no_rule.blockers),
        str(no_rule.blockers),
    )

    # ---- 路径 8：S0 计费规则模型一致性 ----
    both = stale_approval.model_copy(
        update={
            "scope": stale_approval.scope.model_copy(
                update={
                    "cost_snapshot_hash": snapshot,
                    "billing_rule_hash": billing_rule_snapshot_hash(forged_rule),
                }
            )
        }
    )
    model_gate = evaluate_quote_eligibility(
        candidate_id="forged-cand", requirement_id="r", tco=tco,
        sla_evidence=fake_evidence,
        sla_recheck=SlaRecheck(
            ok=True, match_level="exact_match", status="observed_pass",
            run_id="run-does-not-exist", data_trust="measured_internal",
        ),
        approval=both, confirmation=confirmation, billing_rule=forged_rule,
        cost_snapshot_hash=snapshot, quote_model_name="别的模型",
        plan_id=plan.plan_id,
    )
    check(
        "计费规则模型与报价模型不一致必须阻断",
        any("billing_rule_model_mismatch" in b for b in model_gate.blockers),
        str(model_gate.blockers),
    )

    # ---- 路径 9：S0 计费规则内容变化使旧审批失效 ----
    repriced = forged_rule.model_copy(update={"price_output_per_1m": Decimal("99")})
    content_gate = evaluate_quote_eligibility(
        candidate_id="forged-cand", requirement_id="r", tco=tco,
        sla_evidence=fake_evidence,
        sla_recheck=SlaRecheck(
            ok=True, match_level="exact_match", status="observed_pass",
            run_id="run-does-not-exist", data_trust="measured_internal",
        ),
        approval=both, confirmation=confirmation, billing_rule=repriced,
        cost_snapshot_hash=snapshot, quote_model_name="m", plan_id=plan.plan_id,
    )
    check(
        "计费规则单价变化必须使旧审批失效",
        any("approval_billing_rule_stale" in b for b in content_gate.blockers),
        str(content_gate.blockers),
    )

    # ---- 路径 10：S0 商务确认作用域 ----
    scope_gate = evaluate_quote_eligibility(
        candidate_id="forged-cand", requirement_id="r", tco=tco,
        sla_evidence=fake_evidence,
        sla_recheck=SlaRecheck(
            ok=True, match_level="exact_match", status="observed_pass",
            run_id="run-does-not-exist", data_trust="measured_internal",
        ),
        approval=both,
        confirmation=confirmation.model_copy(
            update={
                "scope_refs": ConfirmationScope(
                    candidate_ids=["另一个候选"], requirement_ids=["r"]
                )
            }
        ),
        billing_rule=forged_rule, cost_snapshot_hash=snapshot,
        quote_model_name="m", plan_id=plan.plan_id,
    )
    check(
        "商务确认作用域与当前报价不一致必须阻断",
        any(
            "commercial_confirmation_scope_mismatch" in b for b in scope_gate.blockers
        ),
        str(scope_gate.blockers),
    )

    print()
    if FAILURES:
        print(f"反向场景检查失败 {len(FAILURES)}/{CHECKED}：{FAILURES}")
        return 1
    print(f"反向场景检查通过 {CHECKED}/{CHECKED}：伪造声明、缺证据引用、"
          "未核验证据与失配成本快照全部被阻断。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
