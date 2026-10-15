"""JSON 导出与读回。

## 编码口径（任务 2）

**金额与比率字段以十进制字符串导出**，例如 ``"149.54340799296"``：

* 可**精确恢复**：``Decimal("149.54340799296")`` 与原值逐位相同，不经过二进制浮点；
* 不使用 JSON number，因为多数 JSON 读取器会把 number 解析成 IEEE-754 double，
  28 位有效数字的 Decimal 结果会在读取端被静默截断；
* 不使用科学计数法（``1E-13`` 这类字面量会被部分实现误读），统一写成定点形式。

**兼容读取**：``loads_tolerant`` 同时接受两种历史格式——
① 十进制字符串（当前标准）；② JSON number（早期版本产物）。
其中 number 用 ``parse_float=Decimal`` 解析，避免先转成 float 再转 Decimal 造成的精度损失。

计算过程本身仍全程使用 ``Decimal``（见 ``engines/pricing.py``）；JSON 只是边界编码。
"""

from __future__ import annotations

import json
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Collection

from pydantic import BaseModel

from llm_capacity_pricing.schemas.pricing import (
    BILLING_PERIOD,
    COST_SCOPE,
    CURRENCY_ASSUMPTION,
    PRICE_UNIT,
    PricingResult,
)

#: 判定"看起来像十进制数"的字符串（用于兼容读取时把金额字符串还原为 Decimal）
_DECIMAL_RE = re.compile(r"^[+-]?(\d+\.?\d*|\.\d+)$")

#: 明确按十进制字符串导出的字段名集合（金额 / 比率 / 计数类小数）
DECIMAL_FIELD_NAMES: frozenset[str] = frozenset(
    {
        # BenchmarkRecord / Excel 原始值
        "input_tps_per_gpu",
        "output_tps_per_gpu",
        "input_tps_cluster",
        "output_tps_cluster",
        "single_user_output_tps_p90",
        "cache_hit_rate_raw",
        "physical_cache_hit_rate",
        "billable_cached_input_ratio",
        "legacy_input_revenue_10k_per_gpu_year",
        "legacy_output_revenue_10k_per_gpu_year",
        "legacy_total_revenue_10k_per_gpu_year",
        "legacy_cost_10k_per_gpu_year",
        "legacy_revenue_cost_ratio",
        "ttft_mean_ms",
        "ttft_p99_ms",
        "tpot_mean_ms",
        "tpot_p99_ms",
        "itl_p99_ms",
        "e2e_p99_ms",
        "failure_rate",
        "duration_s",
        "mean_observed_concurrency",
        "request_rate_rps",
        # 价格
        "per_gpu_annual_cost_10k",
        "price_cached_input_per_1m",
        "price_uncached_input_per_1m",
        "price_output_per_1m",
        # 计算结果
        "effective_monetization_ratio",
        "input_revenue_10k_per_gpu_year",
        "output_revenue_10k_per_gpu_year",
        "total_revenue_10k_per_gpu_year",
        "gpu_annual_cost_10k",
        "revenue_cost_ratio",
        "breakeven_monetization_ratio",
        "total_cluster_cost_10k_year",
        "total_cluster_revenue_10k_year",
        "scenario_revenue_10k_per_gpu_year",
        "scenario_surplus_before_other_costs_10k_per_gpu_year",
        "scenario_cluster_revenue_10k_year",
        "cache_hit_rate_used",
    }
)

#: 编码说明，写入导出文件的 meta，供下游读取方参考
DECIMALS_ENCODING = {
    "money_and_ratio_fields": "decimal_string",
    "format": "fixed-point decimal literal, no scientific notation",
    "example": "149.54340799296",
    "rationale": (
        "JSON number 会被多数读取器解析为 IEEE-754 double，28 位有效数字不保真；"
        "字符串可精确恢复为 Decimal"
    ),
    "compatible_reader": "llm_capacity_pricing.reporting.json_export.loads_tolerant",
    "compute_path": "all formulas use decimal.Decimal; JSON is an encoding boundary only",
}


def decimal_to_str(value: Decimal) -> str:
    """Decimal -> 可精确恢复的定点十进制字符串。"""
    text = str(value)
    if "e" in text or "E" in text:
        return format(value, "f")
    return text


def parse_decimal(value: Any) -> Decimal:
    """宽容地把 ``str`` / ``int`` / ``float`` / ``Decimal`` 还原为 Decimal。

    ``float`` 走 ``str()``（最短可逆表示）而不是 ``Decimal(float)``，避免把二进制
    浮点误差（如 0.1000000000000000055511151231257827）带进来。
    """
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool):
        raise ValueError(f"布尔值不能作为金额: {value!r}")
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, str):
        try:
            return Decimal(value.strip())
        except InvalidOperation as exc:  # pragma: no cover - 结构异常
            raise ValueError(f"无法解析为 Decimal: {value!r}") from exc
    raise ValueError(f"不支持的金额类型: {type(value).__name__}")


def _prepare(obj: Any) -> Any:
    """把结构中的 Decimal 转成十进制字符串，其余递归处理。"""
    if isinstance(obj, Decimal):
        return decimal_to_str(obj)
    if isinstance(obj, BaseModel):
        return _prepare(obj.model_dump(mode="python"))
    if isinstance(obj, dict):
        return {str(k): _prepare(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_prepare(v) for v in obj]
    return obj


def dumps_exact(obj: Any, *, indent: int | None = 2) -> str:
    """序列化为 JSON 文本；金额/比率以十进制字符串写出（可精确恢复）。"""
    return json.dumps(_prepare(obj), ensure_ascii=False, indent=indent)


def write_json(obj: Any, path: str | Path) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(dumps_exact(obj), encoding="utf-8")
    return p


# ---- 兼容读取 ------------------------------------------------------------


def loads_tolerant(
    text: str, *, restore_keys: Collection[str] | None = None
) -> Any:
    """读取本引擎导出的 JSON，兼容两种历史编码。

    * JSON number 用 ``parse_float=Decimal`` 解析（不经 float，避免精度损失）；
    * 十进制字符串**按字段名**还原为 ``Decimal``：默认只在已知金额字段名
      （``DECIMAL_FIELD_NAMES``）上还原，避免把 ``framework_version="1.0"`` 这类
      版本号字符串误当成金额。需要处理自定义字段名时传 ``restore_keys``。
    """
    keys = DECIMAL_FIELD_NAMES if restore_keys is None else frozenset(restore_keys)
    return _restore(json.loads(text, parse_float=Decimal), keys)


def read_json(path: str | Path, *, restore_keys: Collection[str] | None = None) -> Any:
    return loads_tolerant(Path(path).read_text(encoding="utf-8"), restore_keys=restore_keys)


def _restore(obj: Any, keys: Collection[str], key: str | None = None) -> Any:
    """把"看起来是金额的字符串"还原为 Decimal；递归处理嵌套结构。"""
    if isinstance(obj, dict):
        return {k: _restore(v, keys, k) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_restore(v, keys, key) for v in obj]
    if isinstance(obj, str) and key in keys and _DECIMAL_RE.match(obj.strip()):
        return Decimal(obj.strip())
    return obj


EXPORT_BOUNDARY_NOTICES = [
    "本文件为离线计算 MVP 输出，不是对外报价，不是 SLA 承诺，也不是实际营收。",
    "cost_scope = gpu_cost_only：仅含表内 GPU 摊销/租金成本，不是完整 TCO。",
    "金额为理论满载潜力（或指定变现比例下的情景估算），非实际营收。",
    "旧表 I/J/K/L/M 保留在 legacy_* 字段，recomputed 结果另存，不互相覆盖。",
    f"金额/比率字段以可精确恢复的十进制字符串编码；兼容读取见 {DECIMALS_ENCODING['compatible_reader']}。",
]


def _result_payload(res: PricingResult) -> dict[str, Any]:
    return res.model_dump(mode="python")


def export_envelope_meta(extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """所有导出文件共用的口径声明（币种 / 成本范围 / 结果类型 / 数值编码）。"""
    meta: dict[str, Any] = {
        "currency": "UNCONFIRMED",
        "currency_assumption": CURRENCY_ASSUMPTION,
        "cost_scope": COST_SCOPE,
        "price_unit": PRICE_UNIT,
        "billing_period": BILLING_PERIOD,
        "decimals_encoding": DECIMALS_ENCODING,
    }
    if extra:
        meta.update(extra)
    return meta


def build_legacy_export(
    results: Iterable[PricingResult],
    *,
    meta: dict[str, Any],
) -> dict[str, Any]:
    items = sorted(results, key=lambda r: r.benchmark_record_id)
    return {
        "export_kind": "legacy_full_load",
        "result_type": "theoretical_full_load_potential",
        "meta": export_envelope_meta(meta),
        "boundary_notices": EXPORT_BOUNDARY_NOTICES,
        "result_count": len(items),
        "results": [_result_payload(r) for r in items],
    }


def build_scenario_export(
    results: Iterable[PricingResult],
    *,
    meta: dict[str, Any],
) -> dict[str, Any]:
    items = sorted(results, key=lambda r: (r.scenario_id, r.benchmark_record_id))
    return {
        "export_kind": "scenario",
        "result_type": "scenario_sensitivity_estimate",
        "meta": export_envelope_meta(meta),
        "boundary_notices": EXPORT_BOUNDARY_NOTICES
        + ["scenario 结果仅用于敏感性分析：scenario_revenue = full_load_revenue * U。"],
        "result_count": len(items),
        "results": [_result_payload(r) for r in items],
    }
