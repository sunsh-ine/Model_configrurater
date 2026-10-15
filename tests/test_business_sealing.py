"""M10：定价与商业口径封板。

守住四件事：
1. 未确认项（币种、成本范围、缓存语义、有效期、计费规则）在**结果、JSON、报告**中显式可见；
2. 结果类型命名只能是"理论满载"或"情景估算"，不得出现"实际营收/利润/报价"式表述；
3. 正式报价路径被阻断（``commercial_quote_allowed`` 恒为 False，导出无报价字段）；
4. 金额精度不发生回退（Decimal + 十进制字符串）。
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import yaml

from llm_capacity_pricing.reporting.json_export import (
    EXPORT_BOUNDARY_NOTICES,
    export_envelope_meta,
    loads_tolerant,
)
from llm_capacity_pricing.schemas.pricing import (
    COST_SCOPE,
    COST_SCOPE_CONFIRMATION_STATUS,
    COST_SCOPE_UNCONFIRMED,
    CURRENCY_UNCONFIRMED,
)
from llm_capacity_pricing.services import (
    calculate_all,
    export_legacy_results,
    export_report,
    open_db,
)

#: 报告/JSON 中不得出现的"越界表述"
FORBIDDEN_PHRASES = (
    "guaranteed_sla",
    "official_quote",
    "total_cost_of_ownership",
    "客户实际营收",
    "预期利润",
    "正式报价单",
)


def test_cost_scope_unconfirmed_enumerated_in_results(full_db):
    results, _ = calculate_all(full_db, mode="legacy_full_load", persist=False)
    assert results
    for r in results:
        assert r.cost_scope == COST_SCOPE == "gpu_cost_only"
        assert set(r.cost_scope_unconfirmed) == set(COST_SCOPE_UNCONFIRMED)
        assert r.cost_scope_confirmation_status == COST_SCOPE_CONFIRMATION_STATUS
        assert len(r.cost_scope_unconfirmed) == 6


def test_result_type_and_currency_declared(full_db):
    results, _ = calculate_all(full_db, mode="legacy_full_load", persist=False)
    for r in results:
        assert r.currency == CURRENCY_UNCONFIRMED
        assert r.result_type == "theoretical_full_load_potential"
        assert r.currency_assumption and "pending business confirmation" in r.currency_assumption


def test_scenario_result_type_is_scenario_estimate(full_db):
    results, _ = calculate_all(
        full_db, mode="scenario", utilization=Decimal("0.3"), persist=False
    )
    assert results
    assert all(r.result_type == "scenario_sensitivity_estimate" for r in results)


def test_export_meta_carries_unconfirmed_items():
    meta = export_envelope_meta()
    assert meta["currency"] == CURRENCY_UNCONFIRMED
    assert meta["currency_confirmed"] is False
    assert meta["cost_scope"] == COST_SCOPE
    assert set(meta["cost_scope_unconfirmed"]) == set(COST_SCOPE_UNCONFIRMED)
    assert meta["cost_scope_confirmation_status"] == COST_SCOPE_CONFIRMATION_STATUS
    assert meta["commercial_quote_allowed"] is False


def test_boundary_notices_cover_sealing_items():
    joined = " ".join(EXPORT_BOUNDARY_NOTICES)
    assert "cost_scope_unconfirmed" in joined
    assert "theoretical_full_load_revenue" in joined and "scenario_revenue" in joined
    assert "不得对外表述" in joined


def _claim_text(text: str) -> str:
    """去掉显式的边界/禁止声明行后的正文，用于扫描"是否被当成结论表述"。

    边界声明里会**引用**这些词来说明"不得这样表述"（例如"不得对外表述为
    「客户实际营收」"），引用不等于断言，因此扫描前必须排除这些行。
    """
    keep = []
    for line in text.splitlines():
        if any(marker in line for marker in ("不得", "不是", "非实际", "禁止", "boundary_notices")):
            continue
        keep.append(line)
    return "\n".join(keep)


def test_exports_contain_no_forbidden_phrases(full_db, tmp_path: Path):
    out = tmp_path / "legacy.json"
    export_legacy_results(full_db, out)
    text = _claim_text(out.read_text(encoding="utf-8"))
    for phrase in FORBIDDEN_PHRASES:
        assert phrase not in text, phrase
    assert '"commercial_quote_allowed": false' in text


def test_report_contains_no_forbidden_phrases(full_db, tmp_path: Path):
    out = tmp_path / "report.md"
    export_report(full_db, out)
    text = _claim_text(out.read_text(encoding="utf-8"))
    for phrase in FORBIDDEN_PHRASES:
        assert phrase not in text, phrase


def test_report_declares_unconfirmed_cost_items_before_money_tables(full_db, tmp_path: Path):
    out = tmp_path / "report.md"
    export_report(full_db, out)
    text = out.read_text(encoding="utf-8")
    decl = text.index("成本范围未确认项")
    first_money_table = text.index("## 4. 最大理论满载收入案例")
    assert decl < first_money_table, "未确认项必须出现在财务数值表格之前"
    for item in COST_SCOPE_UNCONFIRMED:
        assert item in text[:first_money_table], item


def test_money_roundtrip_survives_sealing_changes(full_db, tmp_path: Path):
    """封板改动不得引入二进制浮点误差：金额仍是可精确恢复的十进制字符串。"""
    out = tmp_path / "legacy.json"
    export_legacy_results(full_db, out)
    raw = out.read_text(encoding="utf-8")
    payload = json.loads(raw)
    sample = payload["results"][0]
    assert isinstance(sample["total_revenue_10k_per_gpu_year"], str)
    restored = loads_tolerant(raw)
    got = restored["results"][0]["total_revenue_10k_per_gpu_year"]
    assert isinstance(got, Decimal)
    db, repo = open_db(full_db)
    try:
        with db.session() as session:
            stored = {
                r.benchmark_record_id: r
                for r in repo.list_pricing_results(session)
                if r.mode == "legacy_full_load"
            }
    finally:
        db.dispose()
    assert got == stored[sample["benchmark_record_id"]].total_revenue_10k_per_gpu_year


def test_business_confirmation_template_lists_pending_items(root: Path):
    """业务确认记录模板必须存在，且把未确认项逐条列出（本轮不杜撰确认结果）。"""
    path = root / "config" / "business_confirmations.example.yaml"
    assert path.exists()
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    keys = {item["key"] for item in doc["items"]}
    assert {
        "currency",
        "cost_scope",
        "cache_metric_semantics",
        "price_valid_period",
        "billing_rules",
        "annual_seconds",
    } <= keys
    assert all(item["status"] == "unconfirmed" for item in doc["items"])
    assert doc["confirmed_by"] is None and doc["confirmed_at"] is None
    cost_item = next(i for i in doc["items"] if i["key"] == "cost_scope")
    assert set(cost_item["checklist"]) == set(COST_SCOPE_UNCONFIRMED)


def test_no_tco_claim_anywhere(full_db, tmp_path: Path):
    """gpu_cost_only 不得被表述成完整 TCO。"""
    out = tmp_path / "report.md"
    export_report(full_db, out)
    text = out.read_text(encoding="utf-8")
    assert "不是完整 TCO" in text or "不是**完整 TCO" in text or "not TCO" in text
    assert "total_cost_of_ownership" not in text
