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
    """**商业条件**上下文（币种 / 有效期 / 计费规则 / 成本覆盖范围）。

    注意：``hardware_cost_bound`` / ``token_price_bound`` 这类"某条记录是否绑定了
    价格行"的判断属于**每条记录**的事实，由 :func:`assess_pricing_readiness` 的入参
    负责，不在这里重复，避免同一件事两处判断、两处口径不一致（V1.2 F4 收敛）。
    """

    currency: str | None = None
    currency_confirmed: bool = False
    cost_scope_confirmed: bool = False
    price_valid_period_declared: bool = False
    billing_rules_declared: bool = False

    def blockers(self) -> list[str]:
        out: list[str] = []
        if not self.currency_confirmed or not self.currency or self.currency == CURRENCY_UNCONFIRMED:
            out.append("currency_not_confirmed")
        if not self.cost_scope_confirmed:
            out.append("cost_scope_unconfirmed")
        if not self.price_valid_period_declared:
            out.append("price_valid_period_not_declared")
        if not self.billing_rules_declared:
            out.append("billing_rules_not_declared")
        return out


@dataclass(frozen=True)
class DeploymentConfig:
    """结构化部署配置（F3）。逐项比较，缺项即阻断，不做"约等于"判断。"""

    tp_size: int | None = None
    dp_size: int | None = None
    ep_size: int | None = None
    gpu_count: int | None = None
    #: 部署模式，如 ``colocated`` / ``prefill_decode_disaggregated``
    mode: str | None = None

    def as_reasons(self, prefix: str, other: "DeploymentConfig | None") -> list[str]:
        """逐字段比较，返回阻断原因（空列表表示一致）。"""
        if other is None:
            return [f"{prefix}:deployment_config_absent"]
        reasons: list[str] = []
        for field_name in ("tp_size", "dp_size", "ep_size", "gpu_count", "mode"):
            want = getattr(self, field_name)
            got = getattr(other, field_name)
            if want is None:
                reasons.append(f"target_requirement_missing:{field_name}")
            elif got is None:
                reasons.append(f"record_missing:{field_name}")
            elif want != got:
                reasons.append(f"mismatch:{field_name}")
        return reasons


@dataclass(frozen=True)
class WorkloadProfile:
    """结构化工作负载（F3）。长度、到达方式、并发、数据集与统计口径都要对得上。"""

    input_len: int | None = None
    output_len: int | None = None
    #: 请求到达方式：``unbounded``（inf）/ ``request_rate`` / ``max_concurrency``
    arrival_mode: str | None = None
    max_concurrency: int | None = None
    request_rate_rps: Decimal | None = None
    dataset_name: str | None = None
    #: 统计口径与计时口径的标识（例如 P99 的算法、TTFT 的定义版本）
    statistics_method: str | None = None

    def as_reasons(self, prefix: str, other: "WorkloadProfile | None") -> list[str]:
        if other is None:
            return [f"{prefix}:workload_profile_absent"]
        reasons: list[str] = []
        for field_name in (
            "input_len",
            "output_len",
            "arrival_mode",
            "max_concurrency",
            "dataset_name",
            "statistics_method",
        ):
            want = getattr(self, field_name)
            got = getattr(other, field_name)
            if want is None:
                reasons.append(f"target_requirement_missing:{field_name}")
            elif got is None:
                reasons.append(f"record_missing:{field_name}")
            elif want != got:
                reasons.append(f"mismatch:{field_name}")
        # 到达速率按数值比（允许一方为 None 时按上面规则处理）
        if self.request_rate_rps is not None or other.request_rate_rps is not None:
            if self.request_rate_rps is None:
                reasons.append("target_requirement_missing:request_rate_rps")
            elif other.request_rate_rps is None:
                reasons.append("record_missing:request_rate_rps")
            elif self.request_rate_rps != other.request_rate_rps:
                reasons.append("mismatch:request_rate_rps")
        return reasons


@dataclass(frozen=True)
class SlaRequirement:
    """一个**具体**的待评估需求。为 None 表示"没有具体需求"，此时不得评估 SLA。

    F3 起，部署配置与工作负载使用**结构化对象**逐维比较：
    任意一维缺失或不等都会给出具体阻断原因，不再依赖"需求侧字段非空"这种粗判。
    """

    model_name: str | None = None
    model_revision: str | None = None
    model_precision: str | None = None
    hardware_model: str | None = None
    deployment_gpu_count: int | None = None
    framework_version: str | None = None
    deployment: DeploymentConfig | None = None
    workload: WorkloadProfile | None = None


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
    record_deployment: DeploymentConfig | None = None,
    record_workload: WorkloadProfile | None = None,
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
        # F3：部署配置逐维比较（TP/DP/EP/卡数/部署模式）
        if sla_requirement.deployment is None:
            sla_reasons.append("target_requirement_missing:deployment_config")
        else:
            sla_reasons += sla_requirement.deployment.as_reasons("sla", record_deployment)
        # F3：工作负载逐维比较（长度/到达方式/并发/数据集/统计口径）
        if sla_requirement.workload is None:
            sla_reasons.append("target_requirement_missing:workload_profile")
        else:
            sla_reasons += sla_requirement.workload.as_reasons("sla", record_workload)
        for field_name in SLA_METRIC_FIELDS:
            if metrics.get(field_name) is None:
                sla_reasons.append(f"missing_percentile:{field_name}")
    can_sla = measurement_origin_is_official and not sla_reasons
    if can_sla:
        notes.append(
            "sla_evaluable_against_declared_target（仅表示*数据条件*具备，"
            "不代表 SLA 已达标；达标与否取决于实测值与约定阈值）"
        )
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


# ---------------------------------------------------------------------------
# F4：经济性计算门禁
# ---------------------------------------------------------------------------

#: 允许参与年化计算的吞吐单位（必须明确，不接受"看起来像 tok/s"）
ALLOWED_THROUGHPUT_UNITS = ("tok/s", "tokens/s", "tok/s/gpu", "tokens/s/gpu")


@dataclass
class PricingReadiness:
    """经济性计算的就绪度（保留计算能力，但决定能否出"正式"结论）。"""

    can_compute_theoretical: bool
    can_issue_formal_quote: bool
    blockers: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def assess_pricing_readiness(
    *,
    metrics: Mapping[str, object],
    throughput_unit: str | None,
    token_unit_prices_present: bool,
    cost_present: bool,
    hardware_price_bound: bool,
    hardware_model_matches: bool,
    gpu_count_matches: bool,
    token_price_bound: bool,
    token_price_model_matches: bool,
    price: PriceContext | None = None,
) -> PricingReadiness:
    """F4：计算前的商业条件检查。

    与 SLA 门禁的区别：这里**不**阻断理论计算（历史 Excel 复算必须保持可用），
    但会把"缺什么"逐条列出，并据此决定能否作为正式定价/报价结论使用。
    正式报价在第一阶段恒不允许（``can_issue_formal_quote=False``）。
    """
    blockers: list[str] = []
    notes: list[str] = []

    # 1) 输入/输出吞吐存在且单位明确
    for key in ("input_tps_per_gpu", "output_tps_per_gpu"):
        if metrics.get(key) in (None, 0):
            blockers.append(f"throughput_missing:{key}")
    if throughput_unit is None:
        blockers.append("throughput_unit_not_declared")
    elif throughput_unit not in ALLOWED_THROUGHPUT_UNITS:
        blockers.append(f"throughput_unit_unrecognized:{throughput_unit}")
    else:
        notes.append(f"throughput_unit_declared:{throughput_unit}")

    # 2) 硬件价格与 Benchmark 硬件规格/部署数量一致
    if not cost_present:
        blockers.append("gpu_cost_missing")
    if not hardware_price_bound:
        blockers.append("hardware_price_not_bound")
    else:
        if not hardware_model_matches:
            blockers.append("hardware_model_mismatch_with_benchmark")
        if not gpu_count_matches:
            blockers.append("deployment_gpu_count_mismatch_with_cost_row")

    # 3) Token 单价与模型/计费规则正确关联
    if not token_unit_prices_present:
        blockers.append("token_prices_missing")
    if not token_price_bound:
        blockers.append("token_price_not_bound")
    elif not token_price_model_matches:
        blockers.append("token_price_model_mismatch_with_benchmark")

    # 4) 币种 / 有效期 / 成本覆盖范围
    if price is not None:
        blockers += price.blockers()
    else:
        blockers.append("no_price_context")

    can_compute = not any(
        b.startswith(("throughput_missing", "token_prices_missing", "gpu_cost_missing"))
        for b in blockers
    )
    if can_compute:
        notes.append("theoretical_full_load_computable")
    notes.append("scenario_is_sensitivity_analysis_not_forecast")
    notes.append(
        "physical_cache_hit_rate_is_not_billable_cached_input_ratio"
        "（未经合同计费规则不得换算）"
    )
    return PricingReadiness(
        can_compute_theoretical=can_compute,
        can_issue_formal_quote=False,  # 第一阶段不产出正式报价
        blockers=blockers,
        notes=notes,
    )


#: 历史 Excel 复算的显式假设说明（F4 第 5 条：明确其假设性质）
LEGACY_ASSUMPTIONS: tuple[str, ...] = (
    "H 列缓存命中率仅作为旧表**定价代理假设**使用，不代表已验证的客户计费命中率",
    "成本仅为 Excel 表内 GPU 每卡年成本（gpu_cost_only），不含机房/电力/网络/人工/软件/折旧",
    "币种未确认（UNCONFIRMED）；金额只用于同口径内部复算，不构成对外报价",
    "吞吐为原表给定的每卡值，未做任何跨配置外推",
)
