"""适配器公共工具：元数据加载、percentile 定义、证据构造。"""

from __future__ import annotations

import hashlib
import math
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Sequence

import yaml

from llm_capacity_pricing.schemas.evidence import Evidence

#: 适配器计算 percentile 时采用的方法（与原始来源口径可能不同，必须记录）
PERCENTILE_METHOD = "nearest_rank_ceil(p/100*n)-1"


class AdapterMetadataError(Exception):
    """适配器元数据缺失或非法。"""


class BenchmarkParseError(Exception):
    """Benchmark 原始文件格式不符合已支持版本。"""


def load_metadata(path: str | Path) -> dict[str, Any]:
    p = Path(path)
    if not p.exists():
        raise AdapterMetadataError(f"找不到元数据文件: {p}")
    data = yaml.safe_load(p.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise AdapterMetadataError(f"元数据文件必须是 YAML 映射: {p}")
    return data


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_path(path: str | Path) -> str:
    return sha256_bytes(Path(path).read_bytes())


def dec(value: Any) -> Decimal | None:
    """任意标量 -> **有限** Decimal；``None``/空串/非数值/Inf/NaN 一律返回 ``None``。

    返回 ``None`` 而不是 0 或 Inf，是为了满足"缺失值不伪造成 0"的硬性要求。
    典型场景：``vllm bench serve`` 在 max-concurrency 模式下把 ``request_rate``
    写成字符串 ``"inf"``，它表示"不限速"，不是可用的请求到达率数值。
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        return value if value.is_finite() else None
    if isinstance(value, (int, float)):
        if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
            return None
        return Decimal(str(value))
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        lowered = s.lower()
        if lowered in ("inf", "-inf", "+inf", "infinity", "-infinity", "nan"):
            return None
        try:
            d = Decimal(s)
        except Exception:
            return None
        return d if d.is_finite() else None
    return None


def is_non_numeric(value: Any) -> bool:
    """值存在但不是可用数值（用于生成可追溯的质量标记）。"""
    return value is not None and dec(value) is None


def int_or_none(value: Any) -> int | None:
    d = dec(value)
    if d is None:
        return None
    if d != d.to_integral_value():
        return None
    return int(d)


def percentile_nearest_rank(samples: Sequence[Decimal], p: float) -> Decimal | None:
    """nearest-rank percentile。返回 ``None`` 表示样本不足。"""
    vals = sorted(v for v in samples if v is not None)
    if not vals:
        return None
    n = len(vals)
    idx = max(0, min(n - 1, math.ceil(p / 100.0 * n) - 1))
    return vals[idx]


def mean(samples: Sequence[Decimal]) -> Decimal | None:
    vals = [v for v in samples if v is not None]
    if not vals:
        return None
    return sum(vals) / Decimal(len(vals))


#: 默认的字段别名表。允许通过元数据 ``field_map`` 覆盖，用于适配改名的版本。
DEFAULT_ALIASES: dict[str, tuple[str, ...]] = {
    "num_requests": ("num_requests", "num_prompts", "total_requests", "numRequests"),
    "success_count": ("success_count", "completed", "successful_requests", "success"),
    "duration_s": ("duration", "duration_s", "benchmark_duration", "elapsed_time"),
    "max_concurrency": ("max_concurrency", "max_concurrent_requests", "concurrency"),
    "request_rate": ("request_rate", "request_rate_rps", "request_rate_per_second"),
    "total_input_tokens": ("total_input_tokens", "input_token_total"),
    "total_output_tokens": ("total_output_tokens", "total_generated_tokens", "output_token_total"),
    "input_throughput_tok_s": ("input_throughput", "input_throughput_tok_s"),
    "output_throughput_tok_s": ("output_throughput", "output_throughput_tok_s"),
    "request_throughput_rps": ("request_throughput", "request_throughput_rps"),
    "mean_ttft_ms": ("mean_ttft_ms", "mean_ttft", "avg_ttft_ms"),
    "p99_ttft_ms": ("p99_ttft_ms", "ttft_p99_ms", "p99_ttft"),
    "mean_tpot_ms": ("mean_tpot_ms", "mean_tpot", "avg_tpot_ms"),
    "p99_tpot_ms": ("p99_tpot_ms", "tpot_p99_ms", "p99_tpot"),
    "mean_itl_ms": ("mean_itl_ms", "mean_itl", "avg_itl_ms"),
    "p99_itl_ms": ("p99_itl_ms", "itl_p99_ms", "p99_itl"),
    "mean_e2e_ms": ("mean_e2e_ms", "mean_e2el_ms", "mean_e2e_latency_ms", "mean_latency_ms"),
    "p99_e2e_ms": ("p99_e2e_ms", "p99_e2el_ms", "p99_e2e_latency_ms", "p99_latency_ms"),
    "failure_rate": ("failure_rate",),
}

#: 绝不允许互相替代的字段。
#: 键 = ``BenchmarkRecord`` 的**字段名**（规范字段），值 = 禁止用于填充它的**原始行字段名**。
#: ITL 是逐 token 间隔，TPOT 是每输出 token 平均耗时，二者口径不同，不可互换。
FORBIDDEN_SUBSTITUTIONS: dict[str, tuple[str, ...]] = {
    "tpot_mean_ms": ("mean_itl_ms", "itl_avg_ms", "mean_itl"),
    "tpot_p99_ms": ("p99_itl_ms", "itl_p99_val", "p99_itl"),
    "ttft_p99_ms": ("mean_ttft_ms", "ttft_avg_ms", "median_ttft_ms"),
}


def build_aliases(metadata: dict[str, Any]) -> dict[str, tuple[str, ...]]:
    aliases = dict(DEFAULT_ALIASES)
    override = metadata.get("field_map") or {}
    if not isinstance(override, dict):
        raise AdapterMetadataError("field_map 必须是 映射(源字段名 -> 规范字段名)")
    # 形式二：{规范字段名: 实际字段名}
    for canonical, actual in override.items():
        names = actual if isinstance(actual, (list, tuple)) else [actual]
        aliases[canonical] = tuple([canonical] + [str(n) for n in names])
    return aliases


def pick(obj: dict[str, Any], canonical: str, aliases: dict[str, tuple[str, ...]]) -> Any:
    for name in aliases.get(canonical, (canonical,)):
        if name in obj:
            return obj[name]
    return None


#: 适配器来源类型 -> (证据 source_type, 测量来源归属)。二者必须与
#: ``schemas.evidence.ALLOWED_ORIGINS`` 一致，否则 Evidence 校验会失败。
ORIGIN_BY_SOURCE_KIND: dict[str, tuple[str, str]] = {
    "synthetic_fixture": ("assumption", "synthetic_fixture"),
    "official_benchmark": ("official_benchmark", "official_benchmark"),
    "internal_measurement": ("internal_measurement", "internal_measurement"),
    "third_party": ("third_party", "historical_external_third_party"),
    "internal_excel": ("internal_excel", "historical_external_third_party"),
    "unverified_upload": ("third_party", "historical_external_third_party"),
}


def build_evidence(
    metadata: dict[str, Any],
    *,
    raw_sha256: str,
    retrieved_at: datetime,
    publisher_default: str,
    extra_flags: Sequence[str] = (),
) -> Evidence:
    ev_meta = metadata.get("evidence") or {}
    if not isinstance(ev_meta, dict):
        raise AdapterMetadataError("evidence 段必须是映射")
    flags = list(extra_flags)
    source_kind = metadata.get("performance_source_kind", "official_benchmark")
    source_type, origin = ORIGIN_BY_SOURCE_KIND.get(
        source_kind, ("official_benchmark", "official_benchmark")
    )
    if source_kind == "synthetic_fixture":
        flags.append("synthetic_fixture_not_official_performance_data")
    if source_kind in ("third_party", "internal_excel", "unverified_upload"):
        flags.append("historical_external_source_not_internal_measurement")
    if source_kind == "unverified_upload":
        flags.append("unverified_source_upload_not_authenticated")
    if not ev_meta.get("source_url"):
        flags.append("missing_source_url")
    return Evidence(
        evidence_id=metadata["evidence_id"],
        source_type=source_type,
        publisher=ev_meta.get("publisher", publisher_default),
        source_url=ev_meta.get("source_url"),
        source_file=metadata.get("source_file"),
        source_sheet=None,
        source_row=None,
        source_version=ev_meta.get("source_version"),
        retrieved_at=retrieved_at if ev_meta.get("retrieved_at", True) else None,
        raw_sha256=raw_sha256,
        notes=ev_meta.get("notes"),
        quality_flags=flags,
        measurement_origin=origin,
        is_internal_measurement=(origin == "internal_measurement"),
    )
