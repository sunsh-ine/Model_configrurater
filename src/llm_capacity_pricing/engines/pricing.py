"""定价公式引擎（纯函数，全程 Decimal）。

公式来源：任务书 §4。所有金额单位为**万元**，年份口径为
``SECONDS_PER_YEAR = 365*24*3600``。

除零处理：一律抛出具名异常 ``NotComputableError`` 并返回
``status="not_evaluable"``；**绝不返回 inf，也不静默填 0**。
"""

from __future__ import annotations

from decimal import Decimal

#: 年度秒数：默认 365 天。不得替换为"扣除维护日后的时间"。
SECONDS_PER_YEAR = Decimal(365 * 24 * 3600)  # 31536000
TOKEN_PRICE_UNIT = Decimal(1_000_000)
TEN_THOUSAND = Decimal(10_000)
ONE = Decimal(1)
ZERO = Decimal(0)


class PricingError(Exception):
    """定价计算基类异常。"""


class MissingInputError(PricingError):
    """缺少必需输入（价格 / 吞吐 / 成本），无法计算。"""


class NotComputableError(PricingError):
    """数学上不可计算（通常为除零）。不允许用 inf/0 代替。"""


class InvalidUtilizationError(PricingError):
    """变现比例 U 不在 [0, 1] 区间。"""


def _require(name: str, *values: object) -> None:
    for v in values:
        if v is None:
            raise MissingInputError(f"缺少必需输入: {name}")


def is_decimal_path(*values: object) -> bool:
    """所有入参都是 ``Decimal``（或 ``None``）时返回 True。

    用于断言公式没有走二进制浮点路径。``int`` 是精确类型，但本引擎的金额入参
    统一由 ``Decimal`` 承载，故这里只接受 ``Decimal``。
    """
    return all(v is None or isinstance(v, Decimal) for v in values)


def _reject_float(*values: object) -> None:
    """拒绝 float 入参：float 会把二进制舍入误差带进金额结算。"""
    for v in values:
        if isinstance(v, float):
            raise TypeError(
                "金额/比例计算不接受 float 入参（会引入二进制浮点误差），"
                f"请传入 Decimal 或字符串: {v!r}"
            )


def validate_utilization(ratio: Decimal) -> Decimal:
    """校验有效产能变现比例 U ∈ [0, 1]。"""
    _reject_float(ratio)
    if ratio < ZERO or ratio > ONE:
        raise InvalidUtilizationError(
            f"effective_monetization_ratio 必须在 [0, 1] 区间，实际为 {ratio}"
        )
    return ratio


def annual_tokens(tps: Decimal, year_seconds: Decimal = SECONDS_PER_YEAR) -> Decimal:
    """每秒 Token 数 -> 年度 Token 数。"""
    _reject_float(tps, year_seconds)
    return tps * year_seconds


def tokens_to_10k(tokens: Decimal, unit_price: Decimal) -> Decimal:
    """Token 数 × 单价(货币单位/百万Token) -> 万元。"""
    _reject_float(tokens, unit_price)
    return tokens * unit_price / TOKEN_PRICE_UNIT / TEN_THOUSAND


def effective_input_unit_price(
    hit_rate: Decimal, p_hit: Decimal, p_miss: Decimal
) -> Decimal:
    """输入侧有效单价：``h * p_hit + (1 - h) * p_miss``。

    ``h`` 在旧表复算中只作为**定价代理假设**。
    """
    _reject_float(hit_rate, p_hit, p_miss)
    return hit_rate * p_hit + (ONE - hit_rate) * p_miss


def legacy_input_annual_revenue_10k_per_gpu(
    input_tps_per_gpu: Decimal,
    hit_rate: Decimal,
    p_hit: Decimal,
    p_miss: Decimal,
    year_seconds: Decimal = SECONDS_PER_YEAR,
) -> Decimal:
    """旧表输入侧年度收入（万元/卡/年）。"""
    _require("input_tps_per_gpu", input_tps_per_gpu)
    _reject_float(input_tps_per_gpu, hit_rate, p_hit, p_miss, year_seconds)
    unit_price = effective_input_unit_price(hit_rate, p_hit, p_miss)
    return tokens_to_10k(annual_tokens(input_tps_per_gpu, year_seconds), unit_price)


def legacy_output_annual_revenue_10k_per_gpu(
    output_tps_per_gpu: Decimal,
    p_out: Decimal,
    year_seconds: Decimal = SECONDS_PER_YEAR,
) -> Decimal:
    """旧表输出侧年度收入（万元/卡/年）。"""
    _require("output_tps_per_gpu", output_tps_per_gpu)
    _reject_float(output_tps_per_gpu, p_out, year_seconds)
    return tokens_to_10k(annual_tokens(output_tps_per_gpu, year_seconds), p_out)


def total_annual_revenue_10k_per_gpu(
    input_revenue: Decimal, output_revenue: Decimal
) -> Decimal:
    _reject_float(input_revenue, output_revenue)
    return input_revenue + output_revenue


def revenue_cost_ratio(total_revenue_10k: Decimal, annual_cost_10k: Decimal) -> Decimal:
    """营收/成本比。成本为 0 时不可计算（抛 NotComputableError）。"""
    _reject_float(total_revenue_10k, annual_cost_10k)
    if annual_cost_10k == ZERO:
        raise NotComputableError(
            "annual_cost_10k 为 0，营收/成本比不可计算（不返回 inf）"
        )
    return total_revenue_10k / annual_cost_10k


def breakeven_monetization_ratio(
    annual_cost_10k: Decimal, full_load_revenue_10k: Decimal
) -> Decimal:
    """盈亏平衡变现比例 = 成本 / 满载收入。

    ``> 1`` 表示即使满载也无法覆盖当前表内成本。
    满载收入为 0 时不可计算。
    """
    _reject_float(annual_cost_10k, full_load_revenue_10k)
    if full_load_revenue_10k == ZERO:
        raise NotComputableError(
            "full_load_revenue_10k 为 0，盈亏平衡比例不可计算（不返回 inf）"
        )
    return annual_cost_10k / full_load_revenue_10k


# ---- 情景模式 ------------------------------------------------------------


def scenario_revenue_10k(full_load_revenue_10k: Decimal, utilization: Decimal) -> Decimal:
    """``scenario_revenue = full_load_revenue * U``（仅作敏感性情景分析）。"""
    validate_utilization(utilization)
    _reject_float(full_load_revenue_10k)
    return full_load_revenue_10k * utilization


def scenario_surplus_before_other_costs_10k(
    scenario_revenue_10k_value: Decimal, annual_cost_10k: Decimal
) -> Decimal:
    """``scenario_revenue - annual_gpu_cost``（成本仅含 GPU 成本）。"""
    _reject_float(scenario_revenue_10k_value, annual_cost_10k)
    return scenario_revenue_10k_value - annual_cost_10k


def cluster_totals(
    per_gpu_value_10k: Decimal, deployment_gpu_count: int
) -> Decimal:
    """按原配置的卡数线性还原集群量（同配置归一化，禁止跨 GPU/TP 外推）。"""
    _reject_float(per_gpu_value_10k)
    if not isinstance(deployment_gpu_count, int) or isinstance(deployment_gpu_count, bool):
        raise TypeError(
            f"deployment_gpu_count 必须为 int，实际为 {type(deployment_gpu_count).__name__}"
        )
    return per_gpu_value_10k * Decimal(deployment_gpu_count)


def derived_billable_ratio(
    *,
    utilization: Decimal | None,
    availability: Decimal | None,
    billable: Decimal | None,
) -> tuple[Decimal, list[str]]:
    """合并 U / A / S，**避免重复相乘**。

    规则：初版只开放 ``U``。若显式提供 ``A`` / ``S``，则它们与 U **相乘一次**，
    但必须在结果中标注三者已同时生效，防止调用方再次重复相乘。
    """
    warnings: list[str] = []
    ratio = ONE
    if utilization is not None:
        ratio *= validate_utilization(utilization)
    else:
        warnings.append("utilization_not_provided: 默认按 U=1 计算满载")
    if availability is not None:
        validate_utilization(availability)
        ratio *= availability
        warnings.append(
            "availability_ratio_applied: 已与 U 相乘一次，调用方不得重复相乘"
        )
    if billable is not None:
        validate_utilization(billable)
        ratio *= billable
        warnings.append("billable_ratio_applied: 已与 U 相乘一次，调用方不得重复相乘")
    return ratio, warnings
