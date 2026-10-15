"""M27：成本目录与计费规则的离线导入。

与前三阶段的导入器保持同一纪律：

* 每个数值都必须能追到证据（``evidence_id`` -> ``Evidence``）；
* 缺字段留空，不用"行业惯例"补齐；
* 合成示例数据必须显式标注 ``synthetic_example``，并且**永远不能**通过正式报价门禁。

``origin`` -> ``(source_type, measurement_origin)`` 的映射是显式的，不猜测：

=========================  ==================  ==============================
cost origin                source_type         measurement_origin
=========================  ==================  ==============================
vendor_published           third_party         historical_external_third_party
public_list_price          third_party         historical_external_third_party
distributor_quote          third_party         historical_external_third_party
internal_actual            internal_measurement internal_measurement
assumed / unknown          assumption          derived_or_assumption
=========================  ==================  ==============================
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml

from llm_capacity_pricing.schemas.commercial import BillingRule
from llm_capacity_pricing.schemas.evidence import Evidence
from llm_capacity_pricing.schemas.tco import CostCatalogItem

#: 成本来源 -> 证据类型（显式映射，不做推断）
ORIGIN_TO_SOURCE_TYPE: dict[str, tuple[str, str]] = {
    "vendor_published": ("third_party", "historical_external_third_party"),
    "public_list_price": ("third_party", "historical_external_third_party"),
    "distributor_quote": ("third_party", "historical_external_third_party"),
    "internal_actual": ("internal_measurement", "internal_measurement"),
    "assumed": ("assumption", "derived_or_assumption"),
    "unknown": ("assumption", "derived_or_assumption"),
}


class CostCatalogError(ValueError):
    """成本目录文件格式或口径错误。"""


def _dec(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    return Decimal(str(value))


def _evidence_for(
    *,
    evidence_id: str,
    origin: str,
    source: dict[str, Any],
    notes: str | None,
) -> Evidence:
    if origin not in ORIGIN_TO_SOURCE_TYPE:
        raise CostCatalogError(f"未知的 origin: {origin!r}")
    default_type, default_origin = ORIGIN_TO_SOURCE_TYPE[origin]
    source_type = source.get("source_type") or default_type
    measurement_origin = source.get("measurement_origin") or default_origin
    return Evidence(
        evidence_id=evidence_id,
        source_type=source_type,
        measurement_origin=measurement_origin,
        is_internal_measurement=(measurement_origin == "internal_measurement"),
        publisher=source.get("publisher"),
        source_file=source.get("source_file"),
        source_url=source.get("source_url"),
        retrieved_at=source.get("retrieved_at"),
        raw_sha256=source.get("raw_sha256"),
        notes=notes,
        quality_flags=list(source.get("quality_flags") or []),
    )


def load_cost_catalog(path: str | Path) -> dict[str, Any]:
    """读取成本目录文件，返回 ``{cost_items, billing_rules, evidence, notice}``。"""
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    source = dict(raw.get("source") or {})
    notice = raw.get("notice")
    if not source.get("source_file"):
        source["source_file"] = str(path)

    cost_items: list[CostCatalogItem] = []
    billing_rules: list[BillingRule] = []
    evidence: list[Evidence] = []

    for entry in raw.get("cost_items") or []:
        item_id = entry["item_id"]
        origin = entry.get("origin", "assumed")
        evidence_id = entry.get("evidence_id") or f"ev_cost_{item_id}"
        if entry.get("status") == "synthetic_example" and not str(
            entry.get("name", "")
        ).startswith("【合成"):
            raise CostCatalogError(
                f"{item_id}: status=synthetic_example 的条目名称必须以「【合成」开头，"
                "避免合成数字被误读为真实报价"
            )
        cost_items.append(
            CostCatalogItem(
                item_id=item_id,
                category=entry["category"],
                name=entry["name"],
                basis=entry.get("basis"),
                price_basis=entry["price_basis"],
                unit_price=_dec(entry.get("unit_price")),
                unit=entry["unit"],
                currency=entry.get("currency", source.get("currency")),
                currency_status=entry.get(
                    "currency_status", source.get("currency_status", "unconfirmed")
                ),
                status=entry["status"],
                origin=origin,
                applies_to_hardware_id=entry.get("applies_to_hardware_id"),
                region=entry.get("region"),
                default_quantity=_dec(entry.get("default_quantity")),
                valid_from=entry.get("valid_from"),
                valid_to=entry.get("valid_to"),
                evidence_id=evidence_id,
                notes=entry.get("notes"),
            )
        )
        evidence.append(
            _evidence_for(
                evidence_id=evidence_id,
                origin=origin,
                source=source,
                notes=f"成本目录项 {item_id}（{entry['category']}）",
            )
        )

    for entry in raw.get("billing_rules") or []:
        rule_id = entry["rule_id"]
        evidence_id = entry.get("evidence_id") or f"ev_billing_{rule_id}"
        origin = entry.get("origin", "assumed")
        billing_rules.append(
            BillingRule(
                rule_id=rule_id,
                model_name=entry["model_name"],
                currency=entry.get("currency", source.get("currency")),
                currency_status=entry.get(
                    "currency_status", source.get("currency_status", "unconfirmed")
                ),
                price_uncached_input_per_1m=_dec(entry.get("price_uncached_input_per_1m")),
                price_cached_input_per_1m=_dec(entry.get("price_cached_input_per_1m")),
                price_output_per_1m=_dec(entry.get("price_output_per_1m")),
                billable_cache_ratio=_dec(entry.get("billable_cache_ratio")),
                billable_cache_basis=entry.get("billable_cache_basis", "unknown"),
                batch_discount_fraction=_dec(entry.get("batch_discount_fraction")),
                min_billable_input_tokens_per_request=entry.get(
                    "min_billable_input_tokens_per_request"
                ),
                settlement_note=entry.get("settlement_note"),
                status=entry.get("status", "pending_business_confirmation"),
                valid_from=entry.get("valid_from"),
                valid_to=entry.get("valid_to"),
                evidence_id=evidence_id,
                notes=entry.get("notes"),
            )
        )
        evidence.append(
            _evidence_for(
                evidence_id=evidence_id,
                origin=origin,
                source=source,
                notes=f"计费规则 {rule_id}（{entry['model_name']}）",
            )
        )

    if not cost_items and not billing_rules:
        raise CostCatalogError(f"{path} 中没有任何 cost_items 或 billing_rules")

    return {
        "file": str(path),
        "notice": notice,
        "cost_items": cost_items,
        "billing_rules": billing_rules,
        "evidence": evidence,
        "synthetic_item_count": sum(1 for i in cost_items if i.status == "synthetic_example"),
        "currency": raw.get("source", {}).get("currency"),
        "currency_status": raw.get("source", {}).get("currency_status"),
    }
