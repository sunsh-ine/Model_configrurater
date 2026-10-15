"""M2：旧表收入复算等价性。

验收标准（任务书 §8 M2）：金额单位"万元"下每条金额误差 ≤ 1e-6，
营收/成本比误差 ≤ 1e-8。

精度说明：旧表 I/J/K/M 由 Excel 以 IEEE-754 双精度浮点计算并存储，
本引擎全程使用 Decimal。因此比较的是"精确十进制结果"与"Excel 浮点结果"，
两者差异来自 Excel 侧的浮点舍入（量级 1e-13），不是本引擎的误差。
这也是容差取 1e-6 / 1e-8 而不是 0 的原因。
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from llm_capacity_pricing.engines.calculator import calculate, make_legacy_scenario
from llm_capacity_pricing.engines.pricing import (
    SECONDS_PER_YEAR,
    legacy_input_annual_revenue_10k_per_gpu,
    legacy_output_annual_revenue_10k_per_gpu,
    revenue_cost_ratio,
    total_annual_revenue_10k_per_gpu,
)
from llm_capacity_pricing.services import legacy_recompute_comparison, open_db

#: 任务书 §9 的比较精度
TOLERANCE_10K = Decimal("1e-6")  # 金额（万元）
TOLERANCE_RATIO = Decimal("1e-8")  # 营收/成本比


def _load(db_path: str, record_id: str):
    db, repo = open_db(db_path)
    try:
        with db.session() as session:
            rec = repo.get_benchmark(session, record_id)
            hp = {h.gpu_model: h for h in repo.list_hardware_prices(session)}
            tp = {t.model_name: t for t in repo.list_token_prices(session)}
    finally:
        db.dispose()
    return rec, tp[rec.model_name], hp[rec.hardware_model]


# --------------------------------------------------------------------------
# §4.1 精确复算样例
# --------------------------------------------------------------------------


def test_sample_oracle_via_pure_functions():
    """用**脱敏公开样本**（非真实业务数据）验证四个公式的精确取值。

    数值来自 data/examples/public_sample_expected.json，期望值由
    scripts/make_public_sample.py 独立手算，不由引擎生成。
    """
    p_hit, p_miss, p_out = Decimal("1"), Decimal("10"), Decimal("50")
    h = Decimal("0.5")
    cost = Decimal("10.0")
    inp = legacy_input_annual_revenue_10k_per_gpu(Decimal("1000"), h, p_hit, p_miss)
    out = legacy_output_annual_revenue_10k_per_gpu(Decimal("40"), p_out)
    total = total_annual_revenue_10k_per_gpu(inp, out)
    ratio = revenue_cost_ratio(total, cost)

    assert inp == Decimal("17.3448")
    assert out == Decimal("6.3072")
    assert total == Decimal("23.6520")
    assert ratio == Decimal("2.3652")


def test_real_excel_row2_oracle_via_pure_functions(row2_oracle):
    """真实工作簿第 2 行：用**内部 oracle**（不入库）复现四个期望值。"""
    p_hit = row2_oracle["price_cached_input_per_1m"]
    p_miss = row2_oracle["price_uncached_input_per_1m"]
    p_out = row2_oracle["price_output_per_1m"]
    h = row2_oracle["cache_hit_rate_raw"]
    inp = legacy_input_annual_revenue_10k_per_gpu(
        row2_oracle["input_tps_per_gpu"], h, p_hit, p_miss
    )
    out = legacy_output_annual_revenue_10k_per_gpu(row2_oracle["output_tps_per_gpu"], p_out)
    total = total_annual_revenue_10k_per_gpu(inp, out)
    ratio = revenue_cost_ratio(total, row2_oracle["annual_gpu_cost_10k"])

    assert inp == row2_oracle["input_revenue_10k_per_gpu_year"]
    assert out == row2_oracle["output_revenue_10k_per_gpu_year"]
    assert total == row2_oracle["total_revenue_10k_per_gpu_year"]
    assert abs(ratio - row2_oracle["revenue_cost_ratio"]) < TOLERANCE_RATIO


def test_row2_oracle_via_engine(excel_db, row2_oracle):
    rec, tp, hp = _load(excel_db, "excel_部署效能_2")
    res = calculate(rec, tp, hp, make_legacy_scenario(rec.record_id))

    assert res.status == "ok"
    assert res.input_revenue_10k_per_gpu_year == row2_oracle["input_revenue_10k_per_gpu_year"]
    assert res.output_revenue_10k_per_gpu_year == row2_oracle["output_revenue_10k_per_gpu_year"]
    assert res.total_revenue_10k_per_gpu_year == row2_oracle["total_revenue_10k_per_gpu_year"]
    assert abs(res.revenue_cost_ratio - row2_oracle["revenue_cost_ratio"]) < TOLERANCE_RATIO
    # 盈亏平衡比例 = 年成本 / 满载收入（两者都来自内部 oracle，不入库）
    assert (
        abs(
            res.breakeven_monetization_ratio
            - row2_oracle["annual_gpu_cost_10k"] / row2_oracle["total_revenue_10k_per_gpu_year"]
        )
        < Decimal("1e-20")
    )


def test_row2_snapshot_hash_is_stable(excel_db):
    rec, tp, hp = _load(excel_db, "excel_部署效能_2")
    a = calculate(rec, tp, hp, make_legacy_scenario(rec.record_id))
    b = calculate(rec, tp, hp, make_legacy_scenario(rec.record_id))
    assert a.input_snapshot_hash == b.input_snapshot_hash
    assert len(a.input_snapshot_hash) == 64


# --------------------------------------------------------------------------
# 全量 32 行复算
# --------------------------------------------------------------------------


def test_all_32_rows_reconcile_within_tolerance(excel_db):
    report = legacy_recompute_comparison(excel_db)
    assert report["record_count"] == 32, "应逐条覆盖 32 行"
    assert report["mismatch_count"] == 0, report["mismatches"][:5]
    for column, err in report["max_error_decimal"].items():
        tol = TOLERANCE_RATIO if column.startswith("M_") else TOLERANCE_10K
        assert err <= tol, f"{column} 最大误差 {err} 超出容差 {tol}"


def test_comparison_covers_all_five_columns(excel_db):
    report = legacy_recompute_comparison(excel_db)
    names = set(report["rows"][0]["columns"].keys())
    assert names == {
        "I_input_revenue",
        "J_output_revenue",
        "K_total_revenue",
        "L_cost",
        "M_revenue_cost_ratio",
    }
    # 每列都要有实际参与比较的值（不允许整列跳过）
    for name in names:
        compared = [
            r for r in report["rows"] if r["columns"][name].get("error") is not None
        ]
        assert len(compared) == 32, f"{name} 仅比较了 {len(compared)} 行"


def test_cost_column_matches_hardware_price_sheet(excel_db):
    """L 列成本应等于 GPU价格 表中同型号的每卡年成本。"""
    db, repo = open_db(excel_db)
    try:
        with db.session() as session:
            records = repo.list_benchmarks(session)
            costs = {
                h.gpu_model: h.per_gpu_annual_cost_10k
                for h in repo.list_hardware_prices(session)
            }
    finally:
        db.dispose()
    for rec in records:
        assert rec.legacy_cost_10k_per_gpu_year == costs[rec.hardware_model], rec.record_id


def test_ratio_is_total_over_cost_not_input_only(excel_db):
    """确认分母是总成本、分子是输入+输出合计（而非仅输入）。"""
    db, repo = open_db(excel_db)
    try:
        with db.session() as session:
            records = repo.list_benchmarks(session)
    finally:
        db.dispose()
    for rec in records:
        want = rec.legacy_total_revenue_10k_per_gpu_year / rec.legacy_cost_10k_per_gpu_year
        assert abs(want - rec.legacy_revenue_cost_ratio) < TOLERANCE_RATIO, rec.record_id


def test_hit_rate_is_legacy_proxy_only(excel_db):
    """H 列命中率仅作为定价代理；若换成计费口径的等价值，结果应一致，
    但必须有 legacy_hit_rate_used_as_billing_proxy 警告。"""
    rec, tp, hp = _load(excel_db, "excel_部署效能_2")
    res = calculate(rec, tp, hp, make_legacy_scenario(rec.record_id))
    assert any("legacy_hit_rate_used_as_billing_proxy" in w for w in res.warnings)
    assert res.cache_hit_rate_is_legacy_proxy is True
    assert any("physical_kv" in w or "物理" in w for w in res.warnings)


# --------------------------------------------------------------------------
# 年度时间（任务书 §9）
# --------------------------------------------------------------------------


def test_default_year_seconds_is_365_days(excel_db):
    assert SECONDS_PER_YEAR == Decimal(365 * 24 * 3600) == Decimal(31536000)
    report = legacy_recompute_comparison(excel_db)
    assert report["meta"]["calculation_year_seconds"] == 31536000


def test_maintenance_adjusted_year_is_not_applied(excel_db):
    """不得擅自用"扣除维护日后的时间"替代：改年份会破坏旧表等价性。"""
    report = legacy_recompute_comparison(excel_db, year_seconds=365 * 24 * 3600 - 86400)
    assert report["mismatch_count"] > 0, "若仍等价，说明年份参数未真正生效"
    assert report["max_error_decimal"]["K_total_revenue"] > TOLERANCE_10K


def test_unsupported_ratio_scale_change_detected(excel_db):
    """校验容差确实在起作用：人为扰动应被抓到。"""
    report = legacy_recompute_comparison(excel_db, tolerance_10k=Decimal("1e-16"))
    # 浮点舍入量级 ~1e-13，收紧到 1e-16 后必然出现不匹配
    assert report["mismatch_count"] > 0
