"""信息缺失语义：缺什么就留空什么，并给出质量警告。

任务书 §9「信息缺失」「每卡→集群吞吐」「缓存口径」「年度时间」对应本文件。
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from llm_capacity_pricing.engines.calculator import (
    calculate,
    make_legacy_scenario,
    make_scenario,
)
from llm_capacity_pricing.schemas.benchmark import BenchmarkRecord
from llm_capacity_pricing.schemas.costs import HardwarePrice, TokenPrice
from llm_capacity_pricing.services import (
    EXPECTED_COUNTS,
    legacy_recompute_comparison,
    open_db,
)

#: Excel 未提供、导入后必须保持空值的字段
MUST_STAY_NULL = (
    "model_revision",
    "model_precision",
    "framework",
    "framework_version",
    "driver_version",
    "hardware_sku",
    "node_count",
    "gpus_per_node",
    "tp_size",
    "dp_size",
    "ep_size",
    "pp_size",
    "prefill_gpu_count",
    "decode_gpu_count",
    "mean_observed_concurrency",
    "request_rate_rps",
    "num_requests",
    "ttft_mean_ms",
    "ttft_p99_ms",
    "tpot_mean_ms",
    "tpot_p99_ms",
    "itl_p99_ms",
    "e2e_p99_ms",
    "failure_rate",
    "success_count",
    "duration_s",
    "physical_cache_hit_rate",
    "billable_cached_input_ratio",
    "dataset_name",
    "dataset_digest",
)


@pytest.fixture
def excel_records(excel_db):
    db, repo = open_db(excel_db)
    try:
        with db.session() as session:
            return [
                r
                for r in repo.list_benchmarks(session)
                if r.performance_source_kind == "internal_excel"
            ]
    finally:
        db.dispose()


# --------------------------------------------------------------------------
# 缺 TP 证据 / 缺 TTFT P99 / 缺 GPU SKU
# --------------------------------------------------------------------------


def test_no_tp_evidence_keeps_topology_null(excel_records):
    for rec in excel_records:
        assert rec.tp_size is None and rec.ep_size is None
        assert rec.dp_size is None and rec.pp_size is None
        assert "tp_size" in rec.missing_fields
        assert "ep_size" in rec.missing_fields
        assert rec.deployment_strategy_parsed["verified"] is False


def test_no_ttft_p99_marks_not_evaluable_for_sla(excel_records):
    for rec in excel_records:
        assert rec.ttft_p99_ms is None
        assert rec.tpot_p99_ms is None
        assert "ttft_p99_ms" in rec.missing_fields
        assert "tpot_p99_ms" in rec.missing_fields


def test_no_gpu_sku_keeps_null_and_flags(excel_records):
    for rec in excel_records:
        assert rec.hardware_sku is None
        assert "hardware_sku" in rec.missing_fields


def test_all_must_stay_null_fields_are_null(excel_records):
    for rec in excel_records:
        for field in MUST_STAY_NULL:
            assert getattr(rec, field) is None, f"{rec.record_id}.{field} 应为空值"


def test_missing_fields_are_declared(excel_records):
    for rec in excel_records:
        assert len(rec.missing_fields) >= 30, rec.record_id
        assert rec.verification_status == "partial_evidence"


def test_no_fabricated_zero_defaults(excel_records):
    """绝不允许把"未知"写成 0。"""
    zero_allowed = {
        "price_cached_input_per_1m",
        "price_uncached_input_per_1m",
        "price_output_per_1m",
    }
    for rec in excel_records:
        payload = rec.model_dump()
        for field, value in payload.items():
            if field in zero_allowed or field in ("deployment_gpu_count",):
                continue
            if isinstance(value, Decimal):
                assert value != 0, f"{rec.record_id}.{field} 不应为 0（缺失应为 None）"


def test_quality_flags_explain_gaps(excel_records):
    rec = excel_records[0]
    for flag in (
        "underlying_source_inferencex_not_official_framework_benchmark",
        "legacy_hit_rate_used_as_billing_proxy",
        "cache_metric_semantics_unknown",
        "deployment_strategy_parse_unverified",
        "currency_pending_business_confirmation",
    ):
        assert flag in rec.quality_flags, flag


# --------------------------------------------------------------------------
# 缓存口径
# --------------------------------------------------------------------------


def test_cache_metrics_stored_separately(excel_records):
    """物理命中与计费命中分别存储；旧表假设有显式标记。"""
    for rec in excel_records:
        assert rec.cache_hit_rate_raw is not None
        assert rec.physical_cache_hit_rate is None
        assert rec.billable_cached_input_ratio is None
        assert rec.cache_metric_semantics == "unknown"
        assert rec.legacy_proxy_for_billable_hit_rate is True


def test_billable_hit_ratio_model_rejects_silent_proxy():
    """带旧表代理标记但既无口径说明也无 quality_flag 时，模型必须拒绝。"""
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="legacy_proxy_for_billable_hit_rate"):
        BenchmarkRecord(
            record_id="x",
            model_name="m",
            hardware_model="g",
            deployment_gpu_count=1,
            evidence_id="e",
            performance_source_kind="internal_excel",
            cache_hit_rate_raw=Decimal("0.9"),
            legacy_proxy_for_billable_hit_rate=True,
        )


# --------------------------------------------------------------------------
# 每卡 -> 集群吞吐（只允许同配置归一化）
# --------------------------------------------------------------------------


def test_cluster_throughput_only_same_config_normalization():
    rec = BenchmarkRecord(
        record_id="r1",
        model_name="m",
        hardware_model="g",
        deployment_gpu_count=8,
        evidence_id="e",
        performance_source_kind="internal_excel",
        input_tps_per_gpu=Decimal("100"),
        output_tps_per_gpu=Decimal("10"),
    )
    rec.derive_cluster_throughput()
    assert rec.input_tps_cluster == Decimal("800")
    assert rec.output_tps_cluster == Decimal("80")
    assert "derived_from_per_gpu" in rec.input_tps_cluster_basis
    assert "禁止跨 GPU/TP 外推" in rec.input_tps_cluster_basis


def test_cluster_throughput_not_recomputed_for_other_gpu_count():
    """换成别的卡数不会自动产生新集群吞吐：必须显式重建记录。"""
    rec = BenchmarkRecord(
        record_id="r1",
        model_name="m",
        hardware_model="g",
        deployment_gpu_count=8,
        evidence_id="e",
        performance_source_kind="internal_excel",
        input_tps_per_gpu=Decimal("100"),
    )
    rec.derive_cluster_throughput()
    before = rec.input_tps_cluster
    rec.deployment_gpu_count = 64
    assert rec.input_tps_cluster == before, "不得在改卡数后静默外推"


def test_excel_records_have_no_cluster_extrapolation(excel_records):
    """Excel 只给每卡吞吐，因此集群吞吐必须为空（无归一化依据来源）。"""
    for rec in excel_records:
        assert rec.input_tps_cluster is None
        assert rec.output_tps_cluster is None


# --------------------------------------------------------------------------
# 缺输入时的计算行为
# --------------------------------------------------------------------------


def _rec(**kw) -> BenchmarkRecord:
    base = dict(
        record_id="r",
        model_name="m",
        hardware_model="g",
        deployment_gpu_count=8,
        evidence_id="e",
        performance_source_kind="internal_excel",
        input_tps_per_gpu=Decimal("1000"),
        output_tps_per_gpu=Decimal("50"),
        cache_hit_rate_raw=Decimal("0.5"),
    )
    base.update(kw)
    return BenchmarkRecord(**base)


def _tp(**kw) -> TokenPrice:
    base = dict(
        token_price_id="t",
        model_name="m",
        price_cached_input_per_1m=Decimal("2"),
        price_uncached_input_per_1m=Decimal("20"),
        price_output_per_1m=Decimal("100"),
        currency=None,
        currency_assumption="CNY; pending business confirmation",
        evidence_id="e",
    )
    base.update(kw)
    return TokenPrice(**base)


def _hp(**kw) -> HardwarePrice:
    base = dict(
        hardware_price_id="h",
        gpu_model="g",
        per_gpu_annual_cost_10k=Decimal("52.5"),
        currency=None,
        currency_assumption="CNY; pending business confirmation",
        evidence_id="e",
    )
    base.update(kw)
    return HardwarePrice(**base)


def test_missing_token_price_is_not_evaluable():
    res = calculate(_rec(), None, _hp(), make_legacy_scenario("r"))
    assert res.status == "not_evaluable"
    assert "token_price" in res.not_evaluable_reason
    assert res.input_revenue_10k_per_gpu_year is None
    # 成本侧仍应给出（独立于收入是否可算）
    assert res.gpu_annual_cost_10k == Decimal("52.5")
    assert res.total_cluster_cost_10k_year == Decimal("420.0")


def test_missing_throughput_is_not_evaluable():
    res = calculate(_rec(input_tps_per_gpu=None), _tp(), _hp(), make_legacy_scenario("r"))
    assert res.status == "not_evaluable"
    assert "input_tps_per_gpu" in res.not_evaluable_reason


def test_missing_hit_rate_is_not_evaluable():
    res = calculate(_rec(cache_hit_rate_raw=None), _tp(), _hp(), make_legacy_scenario("r"))
    assert res.status == "not_evaluable"
    assert "cache_hit_rate_raw" in res.not_evaluable_reason


def test_missing_cost_makes_ratio_partial_not_zero():
    res = calculate(_rec(), _tp(), None, make_legacy_scenario("r"))
    assert res.status == "partial"
    assert res.total_revenue_10k_per_gpu_year is not None
    assert res.gpu_annual_cost_10k is None
    assert res.revenue_cost_ratio is None
    assert any("revenue_cost_ratio_not_evaluable" in w for w in res.warnings)


def test_missing_single_price_field_is_not_evaluable():
    res = calculate(
        _rec(), _tp(price_cached_input_per_1m=None), _hp(), make_legacy_scenario("r")
    )
    assert res.status == "not_evaluable"
    assert "price_cached_input_per_1m" in res.not_evaluable_reason


def test_sla_warnings_present_when_p99_missing():
    res = calculate(_rec(), _tp(), _hp(), make_legacy_scenario("r"))
    assert any("ttft_p99_not_evaluable" in w for w in res.warnings)
    assert any("tpot_p99_not_evaluable" in w for w in res.warnings)


def test_single_user_p90_never_becomes_tpot_p99():
    res = calculate(
        _rec(single_user_output_tps_p90=Decimal("100")), _tp(), _hp(), make_legacy_scenario("r")
    )
    assert any("single_user_output_tps_p90_kept_separate" in w for w in res.warnings)


def test_unsupported_benchmark_does_not_leak_into_legacy_counts(excel_db):
    report = legacy_recompute_comparison(excel_db)
    assert report["record_count"] == EXPECTED_COUNTS["benchmark_record"]


def test_not_evaluable_result_is_persisted_and_retrievable(full_db):
    from llm_capacity_pricing.services import calculate_all

    # 默认业务口径：排除 4 条无价格的 synthetic_fixture -> 32 条全部可算
    results, meta = calculate_all(full_db, mode="legacy_full_load", persist=True)
    assert meta["record_count"] == 32
    assert meta["status_counts"]["not_evaluable"] == 0
    assert meta["excluded_test_records"] == [
        "fixture_sglang_renamed_rec",
        "fixture_sglang_synthetic_rec",
        "fixture_vllm_synthetic_rec",
        "fixture_vllm_zero_success_rec",
    ][: len(meta["excluded_test_records"])]

    # 显式测试口径：4 条 fixture 因无对应价格而不可计算
    results_t, meta_t = calculate_all(
        full_db, mode="legacy_full_load", persist=True, include_test_data=True
    )
    assert meta_t["record_count"] == 36
    assert meta_t["status_counts"]["not_evaluable"] == 4

    db, repo = open_db(full_db)
    try:
        with db.session() as session:
            stored = repo.list_pricing_results(session)
    finally:
        db.dispose()

    assert len(stored) == len(results_t)
    assert all(r.cost_scope == "gpu_cost_only" for r in stored)
    assert all(r.pricing_engine_version for r in stored)
    assert {r.status for r in stored} == {"ok", "not_evaluable"}
    not_eval = [r for r in stored if r.status == "not_evaluable"]
    assert all(r.not_evaluable_reason for r in not_eval)
    assert all(r.scope == "test" for r in not_eval)

    # 再跑一次默认口径：库里遗留的测试结果应被清理，只留业务结果
    calculate_all(full_db, mode="legacy_full_load", persist=True)
    db, repo = open_db(full_db)
    try:
        with db.session() as session:
            stored2 = repo.list_pricing_results(session)
    finally:
        db.dispose()
    assert len(stored2) == 32
    assert all(r.scope == "business" for r in stored2)
