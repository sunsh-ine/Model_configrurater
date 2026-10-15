"""计算编排层：BenchmarkRecord + TokenPrice + HardwarePrice -> PricingResult。

职责边界（任务书 §4）：
* 只算 **GPU 成本**，结果命名为 ``gpu_cost_only``，不得称 TCO；
* ``input_tps_cluster`` 仅按原配置卡数归一化还原，禁止跨 GPU/TP/PD 外推；
* 不输出任何 SLA 承诺；P99 缺失时明确 ``not_evaluable``。
"""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal

from llm_capacity_pricing.engines import pricing as P
from llm_capacity_pricing.schemas.benchmark import BenchmarkRecord
from llm_capacity_pricing.schemas.costs import HardwarePrice, TokenPrice
from llm_capacity_pricing.schemas.pricing import (
    COST_SCOPE,
    CURRENCY_UNCONFIRMED,
    PRICING_ENGINE_VERSION,
    PricingResult,
    PricingScenario,
)

#: 所有结果都必须携带的业务边界声明
BOUNDARY_WARNINGS = [
    "cost_scope=gpu_cost_only: 表内 GPU 成本不是完整 TCO，未含机房/网络/人力/存储/运维",
    "not_customer_quote: 理论满载 Token 收入不是实际营收，也不是对外合同报价",
    "sla_commitment_not_provided: 本引擎不提供任何 SLA 承诺",
]


def input_snapshot_hash(
    *,
    record: BenchmarkRecord,
    token_price: TokenPrice | None,
    hardware_price: HardwarePrice | None,
    scenario: PricingScenario,
) -> str:
    """把参与计算的输入拍平成规范 JSON 后取 sha256，保证结果可复算。"""
    snapshot = {
        "record_id": record.record_id,
        "model_name": record.model_name,
        "hardware_model": record.hardware_model,
        "deployment_gpu_count": record.deployment_gpu_count,
        "configured_concurrency": record.configured_concurrency,
        "input_tps_per_gpu": str(record.input_tps_per_gpu),
        "output_tps_per_gpu": str(record.output_tps_per_gpu),
        "cache_hit_rate_raw": str(record.cache_hit_rate_raw),
        "cache_hit_rate_is_legacy_proxy": record.legacy_proxy_for_billable_hit_rate,
        "p_hit": str(token_price.price_cached_input_per_1m) if token_price else None,
        "p_miss": str(token_price.price_uncached_input_per_1m) if token_price else None,
        "p_out": str(token_price.price_output_per_1m) if token_price else None,
        "currency": token_price.currency if token_price else None,
        "currency_assumption": (
            token_price.currency_assumption if token_price else None
        ),
        "gpu_annual_cost_10k": (
            str(hardware_price.per_gpu_annual_cost_10k) if hardware_price else None
        ),
        "gpu_cost_currency_assumption": (
            hardware_price.currency_assumption if hardware_price else None
        ),
        "calculation_year_seconds": scenario.calculation_year_seconds,
        "mode": scenario.mode,
        "effective_monetization_ratio": str(scenario.effective_monetization_ratio),
        "availability_ratio": str(scenario.availability_ratio),
        "billable_ratio": str(scenario.billable_ratio),
        "pricing_engine_version": PRICING_ENGINE_VERSION,
    }
    blob = json.dumps(snapshot, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _sla_warnings(record: BenchmarkRecord) -> list[str]:
    """P99 缺失 -> 不可评估 SLA。绝不根据平均吞吐或单用户 P90 反推 P99。"""
    out: list[str] = []
    if record.ttft_p99_ms is None:
        out.append("ttft_p99_not_evaluable: 无 TTFT P99 实测数据，不做 SLA 承诺")
    if record.tpot_p99_ms is None:
        out.append("tpot_p99_not_evaluable: 无 TPOT P99 实测数据，不做 SLA 承诺")
    if record.single_user_output_tps_p90 is not None and record.tpot_p99_ms is None:
        out.append(
            "single_user_output_tps_p90_kept_separate: 单用户输出 P90 吞吐不得换算为 TPOT P99"
        )
    return out


def sla_assessment(record: BenchmarkRecord) -> tuple[str, list[str]]:
    """SLA 可评估性判定。

    只有同时具备实测 P99 TTFT 与 P99 TPOT 时才可能评估；否则恒为
    ``not_evaluable`` 并给出逐项原因。**任何情况下都不会由平均吞吐、
    单用户 P90 或其它型号 GPU 的结果反推 P99。**
    """
    reasons: list[str] = []
    if record.ttft_p99_ms is None:
        reasons.append("missing_ttft_p99_ms")
    if record.tpot_p99_ms is None:
        reasons.append("missing_tpot_p99_ms")
    if reasons:
        reasons.append("no_sla_commitment_possible: 不以其它指标反推 P99")
        return "not_evaluable", reasons
    return "evaluable", []


def _result_scope(record: BenchmarkRecord) -> str:
    return "test" if record.performance_source_kind == "synthetic_fixture" else "business"


def calculate(
    record: BenchmarkRecord,
    token_price: TokenPrice | None,
    hardware_price: HardwarePrice | None,
    scenario: PricingScenario,
) -> PricingResult:
    """计算一条记录的经济性。任何时候都不抛未捕获异常给调用方。"""
    year_seconds = Decimal(scenario.calculation_year_seconds)
    sla_status, sla_reasons = sla_assessment(record)

    # 证据链：性能记录来源 + 硬件价格来源 + Token 价格来源（缺失的留空并记录）
    evidence_ids: list[str] = [record.evidence_id]
    linkage_warnings: list[str] = []
    if token_price is not None:
        evidence_ids.append(token_price.evidence_id)
    else:
        linkage_warnings.append(
            f"token_price_not_linked: 无匹配 {record.model_name!r} 的模型价格行，收入侧留空"
        )
    if hardware_price is not None:
        evidence_ids.append(hardware_price.evidence_id)
    else:
        linkage_warnings.append(
            f"hardware_price_not_linked: 无匹配 {record.hardware_model!r} 的硬件价格行，成本侧留空"
        )

    result = PricingResult(
        scenario_id=scenario.scenario_id,
        benchmark_record_id=record.record_id,
        mode=scenario.mode,
        scope=_result_scope(record),
        result_type=(
            "scenario_sensitivity_estimate"
            if scenario.is_scenario
            else "theoretical_full_load_potential"
        ),
        performance_record_id=record.record_id,
        token_price_id=token_price.token_price_id if token_price else None,
        hardware_price_id=hardware_price.hardware_price_id if hardware_price else None,
        evidence_ids=evidence_ids,
        linkage_warnings=linkage_warnings,
        deployment_gpu_count=record.deployment_gpu_count,
        calculation_year_seconds=scenario.calculation_year_seconds,
        cost_scope=COST_SCOPE,
        cache_hit_rate_used=record.cache_hit_rate_raw,
        cache_hit_rate_is_legacy_proxy=record.legacy_proxy_for_billable_hit_rate,
        sla_status=sla_status,
        sla_not_evaluable_reasons=sla_reasons,
        input_snapshot_hash=input_snapshot_hash(
            record=record, token_price=token_price, hardware_price=hardware_price, scenario=scenario
        ),
        warnings=list(BOUNDARY_WARNINGS) + linkage_warnings + _sla_warnings(record),
    )
    steps = result.steps

    # ---- 币种：Excel 未写明币种，必须显式记录假设 ----------------------
    result.currency = (token_price.currency if token_price and token_price.currency else None) or CURRENCY_UNCONFIRMED
    if token_price is not None:
        result.currency_assumption = token_price.currency_assumption
    if token_price is not None and token_price.currency is None:
        result.warnings.append(
            f"currency_unconfirmed: Excel 未显式写明价格币种，采用假设 "
            f"{token_price.currency_assumption!r}，需业务确认"
        )
    if hardware_price is not None and hardware_price.currency is None:
        result.warnings.append(
            f"hardware_currency_unconfirmed: {hardware_price.currency_assumption!r}"
        )

    # ---- 缓存命中率口径 -------------------------------------------------
    if record.legacy_proxy_for_billable_hit_rate and record.cache_hit_rate_raw is not None:
        result.warnings.append(
            "legacy_hit_rate_used_as_billing_proxy: H 列命中率被当作旧表定价代理假设使用，"
            "物理 KV 命中率不等于可计费缓存命中 Token 比例"
        )

    # ---- 是否具备计算收入的条件 -----------------------------------------
    missing: list[str] = []
    if token_price is None:
        missing.append("token_price")
    else:
        if token_price.price_cached_input_per_1m is None:
            missing.append("price_cached_input_per_1m")
        if token_price.price_uncached_input_per_1m is None:
            missing.append("price_uncached_input_per_1m")
        if token_price.price_output_per_1m is None:
            missing.append("price_output_per_1m")
    if record.input_tps_per_gpu is None:
        missing.append("input_tps_per_gpu")
    if record.output_tps_per_gpu is None:
        missing.append("output_tps_per_gpu")
    if record.cache_hit_rate_raw is None:
        missing.append("cache_hit_rate_raw")

    if missing:
        result.status = "not_evaluable"
        result.not_evaluable_reason = "缺少必需输入: " + ", ".join(missing)
        result.warnings.append(f"not_evaluable: {result.not_evaluable_reason}")
        _finish_cost_side(result, record, hardware_price, scenario)
        return result

    assert token_price is not None  # 由上面的 missing 检查保证
    assert record.input_tps_per_gpu is not None
    assert record.output_tps_per_gpu is not None
    assert record.cache_hit_rate_raw is not None

    hit = record.cache_hit_rate_raw
    p_hit = token_price.price_cached_input_per_1m
    p_miss = token_price.price_uncached_input_per_1m
    p_out = token_price.price_output_per_1m
    assert p_hit is not None and p_miss is not None and p_out is not None

    unit_price = P.effective_input_unit_price(hit, p_hit, p_miss)
    steps.append(
        f"input_unit_price = h({hit}) * p_hit({p_hit}) + (1-h) * p_miss({p_miss}) = {unit_price}"
    )

    result.input_revenue_10k_per_gpu_year = P.legacy_input_annual_revenue_10k_per_gpu(
        record.input_tps_per_gpu, hit, p_hit, p_miss, year_seconds
    )
    steps.append(
        f"input_revenue = {record.input_tps_per_gpu} * {year_seconds} * {unit_price} "
        f"/ 1e6 / 1e4 = {result.input_revenue_10k_per_gpu_year}"
    )
    result.output_revenue_10k_per_gpu_year = P.legacy_output_annual_revenue_10k_per_gpu(
        record.output_tps_per_gpu, p_out, year_seconds
    )
    steps.append(
        f"output_revenue = {record.output_tps_per_gpu} * {year_seconds} * {p_out} "
        f"/ 1e6 / 1e4 = {result.output_revenue_10k_per_gpu_year}"
    )
    result.total_revenue_10k_per_gpu_year = P.total_annual_revenue_10k_per_gpu(
        result.input_revenue_10k_per_gpu_year, result.output_revenue_10k_per_gpu_year
    )
    result.total_cluster_revenue_10k_year = P.cluster_totals(
        result.total_revenue_10k_per_gpu_year, record.deployment_gpu_count
    )
    steps.append(
        f"total_revenue_per_gpu = {result.total_revenue_10k_per_gpu_year}; "
        f"cluster(N={record.deployment_gpu_count}) = {result.total_cluster_revenue_10k_year} "
        "(同配置归一化还原，禁止跨 GPU/TP 外推)"
    )

    _finish_cost_side(result, record, hardware_price, scenario)

    # ---- 营收/成本比 ----------------------------------------------------
    if result.gpu_annual_cost_10k is None:
        if result.status == "ok":
            result.status = "partial"
        result.not_evaluable_reason = "缺少 annual_cost_10k，营收/成本比不可计算"
        result.warnings.append("revenue_cost_ratio_not_evaluable: 缺少 GPU 年成本")
    else:
        try:
            result.revenue_cost_ratio = P.revenue_cost_ratio(
                result.total_revenue_10k_per_gpu_year, result.gpu_annual_cost_10k
            )
            steps.append(
                f"revenue_cost_ratio = {result.total_revenue_10k_per_gpu_year} "
                f"/ {result.gpu_annual_cost_10k} = {result.revenue_cost_ratio}"
            )
        except P.NotComputableError as exc:
            result.status = "not_evaluable"
            result.not_evaluable_reason = str(exc)
            result.warnings.append(f"revenue_cost_ratio_not_evaluable: {exc}")

        try:
            result.breakeven_monetization_ratio = P.breakeven_monetization_ratio(
                result.gpu_annual_cost_10k, result.total_revenue_10k_per_gpu_year
            )
            steps.append(
                f"breakeven_monetization_ratio = {result.gpu_annual_cost_10k} "
                f"/ {result.total_revenue_10k_per_gpu_year} = {result.breakeven_monetization_ratio}"
            )
            if result.breakeven_monetization_ratio > Decimal(1):
                result.warnings.append(
                    "breakeven_utilization_above_1: 即使满载也无法覆盖当前表内 GPU 成本"
                )
        except P.NotComputableError as exc:
            result.warnings.append(f"breakeven_not_evaluable: {exc}")

    # ---- 情景模式 -------------------------------------------------------
    if scenario.is_scenario:
        if scenario.effective_monetization_ratio is None:
            result.status = "not_evaluable"
            result.not_evaluable_reason = "scenario 模式必须提供 effective_monetization_ratio"
            result.warnings.append(result.not_evaluable_reason)
            return result
        combined, extra = P.derived_billable_ratio(
            utilization=scenario.effective_monetization_ratio,
            availability=scenario.availability_ratio,
            billable=scenario.billable_ratio,
        )
        result.warnings.extend(extra)
        result.effective_monetization_ratio = scenario.effective_monetization_ratio
        if result.total_revenue_10k_per_gpu_year is not None:
            result.scenario_revenue_10k_per_gpu_year = P.scenario_revenue_10k(
                result.total_revenue_10k_per_gpu_year, combined
            )
            steps.append(
                f"scenario_revenue = {result.total_revenue_10k_per_gpu_year} * U({combined}) "
                f"= {result.scenario_revenue_10k_per_gpu_year}"
            )
            result.scenario_cluster_revenue_10k_year = P.cluster_totals(
                result.scenario_revenue_10k_per_gpu_year, record.deployment_gpu_count
            )
            if result.gpu_annual_cost_10k is not None:
                result.scenario_surplus_before_other_costs_10k_per_gpu_year = (
                    P.scenario_surplus_before_other_costs_10k(
                        result.scenario_revenue_10k_per_gpu_year, result.gpu_annual_cost_10k
                    )
                )
                steps.append(
                    f"scenario_surplus_before_other_costs = "
                    f"{result.scenario_revenue_10k_per_gpu_year} - "
                    f"{result.gpu_annual_cost_10k} = "
                    f"{result.scenario_surplus_before_other_costs_10k_per_gpu_year}"
                )
        result.warnings.append(
            "scenario_is_sensitivity_only: 情景结果仅用于敏感性分析，不是报价或营收承诺"
        )

    return result


def _finish_cost_side(
    result: PricingResult,
    record: BenchmarkRecord,
    hardware_price: HardwarePrice | None,
    scenario: PricingScenario,
) -> None:
    """填充成本侧（GPU 成本独立于收入是否可算）。"""
    if hardware_price is not None:
        result.gpu_annual_cost_10k = hardware_price.per_gpu_annual_cost_10k
    if result.gpu_annual_cost_10k is None:
        result.warnings.append("gpu_annual_cost_missing: 无硬件价格，成本侧留空")
        return
    result.total_cluster_cost_10k_year = P.cluster_totals(
        result.gpu_annual_cost_10k, record.deployment_gpu_count
    )
    result.steps.append(
        f"gpu_annual_cost = {result.gpu_annual_cost_10k} 万元/卡/年; "
        f"cluster(N={record.deployment_gpu_count}) = {result.total_cluster_cost_10k_year} "
        "(cost_scope=gpu_cost_only)"
    )


def make_legacy_scenario(
    record_id: str,
    *,
    token_price_id: str | None = None,
    hardware_price_id: str | None = None,
    year_seconds: int = 31_536_000,
) -> PricingScenario:
    return PricingScenario(
        scenario_id=f"legacy_full_load::{record_id}",
        benchmark_record_id=record_id,
        token_price_id=token_price_id,
        hardware_price_id=hardware_price_id,
        calculation_year_seconds=year_seconds,
        mode="legacy_full_load",
    )


def make_scenario(
    record_id: str,
    utilization: Decimal,
    *,
    token_price_id: str | None = None,
    hardware_price_id: str | None = None,
    year_seconds: int = 31_536_000,
) -> PricingScenario:
    P.validate_utilization(utilization)
    return PricingScenario(
        scenario_id=f"scenario::{record_id}::U={utilization}",
        benchmark_record_id=record_id,
        token_price_id=token_price_id,
        hardware_price_id=hardware_price_id,
        calculation_year_seconds=year_seconds,
        mode="scenario",
        effective_monetization_ratio=utilization,
    )
