"""数据适用性门禁（M9）：把"这条数据能用来干什么"变成**派生状态**，而不是人工标记。

三条门禁各自独立，避免"来源官方 ⇒ 什么都放行"：

* ``can_reference_performance`` —— 能否作为**官方参考测试**展示。
  要求：来源可定位 + 有公开数值 + 模型/硬件/GPU 数/测试条件能对应上。
* ``can_evaluate_sla`` —— 能否用于**某个具体待评估需求**的 SLA 结论。
  要求远高于上一条：模型版本与精度、GPU SKU 与数量、框架环境、并行配置、
  负载分布、请求到达模型、统计口径、P99 全部齐备**且与目标需求相符**。
  缺 model revision 的 ``partial_evidence`` 一律返回 ``not_evaluable``。
* ``can_calculate_pricing`` —— 能否进入经济性计算。
  要求另有**明确且匹配**的 GPU 成本、Token 价格、币种、有效期与计费规则；
  即使齐备，第一阶段也只允许 ``theoretical_full_load`` / ``scenario``，
  **不得**转成正式商务报价（``commercial_quote_allowed`` 恒为 False）。

设计原则：门禁结论必须**可由记录本身与上下文重新算出**，不接受人工填写；
任何一条不满足都要给出可读的原因，而不是静默 False。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

#: 币种未确认时使用的占位（与 ``schemas.pricing.CURRENCY_UNCONFIRMED`` 保持一致）
CURRENCY_UNCONFIRMED = "UNCONFIRMED"


@dataclass(frozen=True)
class PriceContext:
    """经济性计算所需的价格上下文。缺项一律视为"没有"。"""

    currency: str | None = None
    currency_confirmed: bool = False
    hardware_cost_bound: bool = False
    hardware_cost_scope: str | None = None
    token_price_bound: bool = False
    price_valid_period_declared: bool = False
    billing_rules_declared: bool = False

    def blockers(self) -> list[str]:
        out: list[str] = []
        if not self.currency_confirmed or not self.currency or self.currency == CURRENCY_UNCONFIRMED:
            out.append("currency_not_confirmed")
        if not self.hardware_cost_bound:
            out.append("hardware_cost_not_bound")
        if not self.token_price_bound:
            out.append("token_price_not_bound")
        if not self.price_valid_period_declared:
            out.append("price_valid_period_not_declared")
        if not self.billing_rules_declared:
            out.append("billing_rules_not_declared")
        return out


@dataclass(frozen=True)
class SlaRequirement:
    """一个**具体**的待评估需求。为 None 表示"没有具体需求"，此时不得评估 SLA。"""

    model_name: str | None = None
    model_revision: str | None = None
    model_precision: str | None = None
    hardware_model: str | None = None
    deployment_gpu_count: int | None = None
    framework_version: str | None = None
    parallel_config: str | None = None
    workload_profile: str | None = None


@dataclass
class Applicability:
    can_reference_performance: bool
    can_evaluate_sla: bool
    can_calculate_pricing: bool
    commercial_quote_allowed: bool
    missing_fields: list[str] = field(default_factory=list)
    quality_flags: list[str] = field(default_factory=list)
    applicability_notes: list[str] = field(default_factory=list)


#: 评估 SLA 时必须齐备的统计字段
SLA_METRIC_FIELDS = ("ttft_p99_ms", "tpot_p99_ms")


def assess_applicability(
    *,
    source_locatable: bool,
    model_name: str | None,
    model_revision: str | None,
    model_precision: str | None,
    hardware_model: str | None,
    deployment_gpu_count: int | None,
    framework_version: str | None,
    workload_defined: bool,
    metrics: Mapping[str, object],
    sla_requirement: SlaRequirement | None = None,
    price: PriceContext | None = None,
    measurement_origin_is_official: bool = True,
) -> Applicability:
    """按记录实际字段与给定上下文派生三条门禁。

    ``measurement_origin_is_official=False``（例如 synthetic fixture）时，
    三条门禁全部为 False —— 测试数据永远不能当业务证据。
    """
    missing: list[str] = []
    flags: list[str] = []
    notes: list[str] = []

    has_numeric = bool(metrics)
    if not source_locatable:
        missing.append("source_locator")
    for name, value in (
        ("model_name", model_name),
        ("model_revision", model_revision),
        ("hardware_model", hardware_model),
        ("framework_version", framework_version),
    ):
        if value in (None, ""):
            missing.append(name)
    if deployment_gpu_count is None:
        missing.append("deployment_gpu_count")
    if not has_numeric:
        missing.append("measured_metrics")
    if not workload_defined:
        missing.append("workload_definition")

    # ---- 1) 参考展示 -----------------------------------------------------
    reference_missing = [
        f
        for f in (
            "source_locator",
            "model_name",
            "hardware_model",
            "deployment_gpu_count",
            "measured_metrics",
            "workload_definition",
        )
        if f in missing
    ]
    can_reference = measurement_origin_is_official and not reference_missing
    if can_reference:
        notes.append("official_reference_measurement_available")
    else:
        notes.append(
            "not_usable_even_as_reference: " + ",".join(reference_missing or ["non_official_origin"])
        )

    # ---- 2) SLA 评估 -----------------------------------------------------
    sla_reasons: list[str] = []
    if not measurement_origin_is_official:
        sla_reasons.append("non_official_origin")
    if sla_requirement is None:
        sla_reasons.append("no_concrete_target_requirement")
    else:
        for field_name, want, got in (
            ("model_name", sla_requirement.model_name, model_name),
            ("model_revision", sla_requirement.model_revision, model_revision),
            ("model_precision", sla_requirement.model_precision, model_precision),
            ("hardware_model", sla_requirement.hardware_model, hardware_model),
            ("deployment_gpu_count", sla_requirement.deployment_gpu_count, deployment_gpu_count),
            ("framework_version", sla_requirement.framework_version, framework_version),
        ):
            if want is None:
                sla_reasons.append(f"target_requirement_missing:{field_name}")
            elif got is None:
                sla_reasons.append(f"record_missing:{field_name}")
            elif want != got:
                sla_reasons.append(f"mismatch:{field_name}")
        for field_name, value in (
            ("parallel_config", sla_requirement.parallel_config),
            ("workload_profile", sla_requirement.workload_profile),
        ):
            if value in (None, ""):
                sla_reasons.append(f"target_requirement_missing:{field_name}")
        for field_name in SLA_METRIC_FIELDS:
            if metrics.get(field_name) is None:
                sla_reasons.append(f"missing_percentile:{field_name}")
    can_sla = measurement_origin_is_official and not sla_reasons
    if can_sla:
        notes.append("sla_evaluable_against_declared_target")
    else:
        notes.append("sla_not_evaluable: " + "; ".join(sla_reasons))
        flags.append("sla_conclusion_blocked")

    # ---- 3) 经济性计算 ---------------------------------------------------
    price_blockers = price.blockers() if price is not None else ["no_price_context"]
    can_pricing = measurement_origin_is_official and not price_blockers
    if can_pricing:
        notes.append("pricing_computable_theoretical_full_load_only")
    else:
        notes.append("pricing_blocked: " + ",".join(price_blockers))
        flags.append("not_a_commercial_quote")

    # 商业报价恒不允许：第一阶段不产出对外报价
    notes.append("commercial_quote_not_provided_in_phase1")

    return Applicability(
        can_reference_performance=can_reference,
        can_evaluate_sla=can_sla,
        can_calculate_pricing=can_pricing,
        commercial_quote_allowed=False,
        missing_fields=sorted(set(missing)),
        quality_flags=sorted(set(flags)),
        applicability_notes=notes,
    )
