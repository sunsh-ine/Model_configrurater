"""M4：SGLang / vLLM Benchmark 适配器。

覆盖：版本化字段映射、单位换算、逐请求聚合、缺失字段不伪造成 0、
ITL 不得顶替 TPOT、字段改名、零成功请求、原始文件 hash 可追溯。
"""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from pathlib import Path

import pytest

from llm_capacity_pricing.importers.common import (
    AdapterMetadataError,
    BenchmarkParseError,
    load_metadata,
)
from llm_capacity_pricing.importers.sglang_adapter import (
    parse_sglang_bench_serving_jsonl,
    parse_sglang_bench_serving_jsonl_full,
)
from llm_capacity_pricing.importers.vllm_adapter import (
    parse_vllm_bench_serve_json,
    parse_vllm_bench_serve_json_full,
)
from llm_capacity_pricing.services import import_benchmark_file, open_db

SGLANG = "sglang_synthetic.jsonl"
SGLANG_RENAMED = "sglang_renamed_fields.jsonl"
VLLM = "vllm_synthetic.json"
VLLM_ZERO = "vllm_zero_success.json"


def _load(root: Path, kind: str, name: str):
    fixtures = root / "data" / "fixtures"
    config = root / "config"
    meta_name = {
        SGLANG: "fixture_sglang.yaml",
        SGLANG_RENAMED: "fixture_sglang_renamed.yaml",
        VLLM: "fixture_vllm.yaml",
        VLLM_ZERO: "fixture_vllm_zero_success.yaml",
    }[name]
    fn = parse_sglang_bench_serving_jsonl_full if kind == "sglang" else parse_vllm_bench_serve_json_full
    return fn(fixtures / name, load_metadata(config / meta_name))


# --------------------------------------------------------------------------
# SGLang：逐请求聚合
# --------------------------------------------------------------------------


def test_sglang_per_request_aggregation(root):
    records, evidences = _load(root, "sglang", SGLANG)
    assert len(records) == 1
    rec = records[0]

    assert rec.framework == "sglang"
    assert rec.format_version == "sglang-bench-serving-jsonl/v1.per_request"
    assert rec.num_requests == 3
    assert rec.success_count == 2, "output_len=0 的失败请求不计入成功"
    assert rec.duration_s == Decimal("1250.0")
    assert rec.input_tps_cluster == Decimal("4096") / Decimal("1250")
    assert rec.input_tps_per_gpu == Decimal("4096") / Decimal("1250") / Decimal("8")
    assert rec.ttft_mean_ms == Decimal("195.35")
    assert len(evidences) == 1


def test_sglang_itl_kept_separate_from_tpot(root):
    """ITL 与 TPOT 分别存储：ITL 存在时不得直接当成 TPOT。"""
    records, _ = _load(root, "sglang", SGLANG)
    rec = records[0]
    assert rec.itl_p99_ms == Decimal("8.2")
    assert rec.tpot_mean_ms is not None
    assert rec.tpot_mean_ms != rec.itl_p99_ms
    # TPOT 由 (latency - ttft)/(output_len - 1) 派生，必须写明口径
    assert "latency" in rec.metric_definitions["mean_tpot_ms"]
    assert "ITL" in rec.metric_definitions["mean_itl_ms"]
    assert "percentile" in rec.metric_definitions["p99_tpot_ms"]
    assert "aggregated_from_per_request_samples" in rec.quality_flags


def test_sglang_missing_fields_stay_none_not_zero(root):
    """第 2/3 行缺少可计算 TPOT 的列：不得填 0。"""
    records, _ = _load(root, "sglang", SGLANG)
    rec = records[0]
    assert "tpot_partially_computable" in rec.quality_flags
    # 逐请求行中未出现的字段必须是 None，不能是 0
    for field in ("ttft_p99_ms", "tpot_p99_ms"):
        assert getattr(rec, field) is not None  # 有样本，可算
    assert rec.request_rate_rps is None  # 来源未提供
    assert rec.mean_observed_concurrency is None
    assert rec.hardware_sku is None


def test_sglang_raw_samples_preserved(root):
    records, _ = _load(root, "sglang", SGLANG)
    rec = records[0]
    assert rec.raw_metrics["kind"] == "per_request_jsonl"
    assert rec.raw_metrics["line_count"] == 3
    assert rec.raw_metrics["lines"][0]["ttft"] == 210.5
    assert rec.raw_metrics["lines"][2]["success"] is False


# --------------------------------------------------------------------------
# vLLM：汇总 JSON
# --------------------------------------------------------------------------


def test_vllm_summary_mapping_and_units(root):
    records, _ = _load(root, "vllm", VLLM)
    assert len(records) == 1
    rec = records[0]
    assert rec.framework == "vllm"
    assert rec.num_requests == 200
    assert rec.success_count == 198
    assert rec.duration_s == Decimal("421.75")
    assert rec.configured_concurrency == 16
    assert rec.ttft_mean_ms == Decimal("188.42")
    assert rec.ttft_p99_ms == Decimal("452.7")
    assert rec.itl_p99_ms == Decimal("19.64")
    assert rec.e2e_p99_ms == Decimal("3120.9")
    # 集群吞吐 -> 每卡（2 卡）
    assert rec.input_tps_cluster == Decimal("971.24")
    assert rec.input_tps_per_gpu == Decimal("485.62")
    assert rec.output_tps_per_gpu == Decimal("60.7")
    assert "per_gpu_throughput_derived_from_cluster_and_gpu_count" in rec.quality_flags


def test_vllm_never_substitutes_itl_for_tpot(root):
    """fixture 故意只给 ITL：TPOT 必须留空并记录拒绝替代的依据。"""
    records, _ = _load(root, "vllm", VLLM)
    rec = records[0]
    assert rec.tpot_mean_ms is None
    assert rec.tpot_p99_ms is None
    assert rec.itl_p99_ms == Decimal("19.64"), "ITL 自身要保留"
    flags = [f for f in rec.quality_flags if f.startswith("refused_substitution:")]
    assert any("tpot_mean_ms_left_null_despite_mean_itl_ms" in f for f in flags)
    assert any("tpot_p99_ms_left_null_despite_p99_itl_ms" in f for f in flags)
    assert "tpot_mean_ms" in rec.missing_fields
    assert "tpot_p99_ms" in rec.missing_fields


def test_vllm_non_numeric_request_rate_left_null(root):
    """vLLM 在 max-concurrency 模式下写 "inf"：这是"不限速"，不是数值。"""
    records, _ = _load(root, "vllm", VLLM)
    rec = records[0]
    assert rec.request_rate_rps is None
    assert rec.raw_metrics["request_rate_raw"] == "inf"
    assert "non_numeric_request_rate_left_null" in rec.quality_flags


def test_vllm_failure_rate_derived_from_counts(root):
    records, _ = _load(root, "vllm", VLLM)
    rec = records[0]
    assert rec.failure_rate == Decimal("0.01")
    assert "failure_rate_derived_from_counts" in rec.quality_flags


def test_vllm_zero_success_handled(root):
    records, _ = _load(root, "vllm", VLLM_ZERO)
    rec = records[0]
    assert rec.success_count == 0
    assert rec.num_requests == 128
    assert "zero_successful_requests" in rec.quality_flags
    # 来源明确给出 0 的吞吐是"实测 0"，保留；来源没有的字段仍是 None
    assert rec.input_tps_cluster == Decimal("0.0")
    assert rec.tpot_mean_ms is None
    assert rec.ttft_p99_ms is None
    assert rec.itl_p99_ms is None


# --------------------------------------------------------------------------
# 版本化字段映射 / 字段改名
# --------------------------------------------------------------------------


def test_field_renaming_supported_via_metadata(root):
    records, _ = _load(root, "sglang", SGLANG_RENAMED)
    rec = records[0]
    assert rec.num_requests == 64
    assert rec.success_count == 64
    assert rec.duration_s == Decimal("312.5")
    assert rec.configured_concurrency == 32
    assert rec.request_rate_rps == Decimal("0.2048")
    assert rec.ttft_p99_ms == Decimal("612.3")
    assert rec.tpot_p99_ms == Decimal("47.82")
    assert rec.tpot_mean_ms == Decimal("30.15")
    assert rec.input_tps_per_gpu == Decimal("419.43") / Decimal("8")


def test_renamed_record_preserves_original_field_names(root):
    records, _ = _load(root, "sglang", SGLANG_RENAMED)
    original = records[0].raw_metrics["rows"][0]
    assert "ttft_p99_val" in original
    assert "tpot_p99_val" in original
    assert "mean_ttft_ms" not in original


def test_unsupported_format_version_raises(root):
    meta = load_metadata(root / "config" / "fixture_sglang.yaml")
    meta["format_version"] = "sglang-bench-serving-jsonl/v99"
    with pytest.raises(BenchmarkParseError, match="不支持的 format_version"):
        parse_sglang_bench_serving_jsonl(root / "data" / "fixtures" / SGLANG, meta)


def test_missing_metadata_required_field_raises(root):
    meta = load_metadata(root / "config" / "fixture_vllm.yaml")
    del meta["deployment_gpu_count"]
    with pytest.raises(AdapterMetadataError, match="deployment_gpu_count"):
        parse_vllm_bench_serve_json(root / "data" / "fixtures" / VLLM, meta)


def test_malformed_file_raises(root, tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(BenchmarkParseError):
        parse_vllm_bench_serve_json(bad, load_metadata(root / "config" / "fixture_vllm.yaml"))

    bad_lines = tmp_path / "bad.jsonl"
    bad_lines.write_text('{"a": 1}\nnot-json\n', encoding="utf-8")
    with pytest.raises(BenchmarkParseError):
        parse_sglang_bench_serving_jsonl(
            bad_lines, load_metadata(root / "config" / "fixture_sglang.yaml")
        )


def test_missing_file_raises(root, tmp_path):
    with pytest.raises(BenchmarkParseError, match="找不到文件"):
        parse_vllm_bench_serve_json(
            tmp_path / "nope.json", load_metadata(root / "config" / "fixture_vllm.yaml")
        )


# --------------------------------------------------------------------------
# 溯源：原始文件 hash / 不得冒充官方数据
# --------------------------------------------------------------------------


def test_raw_sha256_matches_file(root):
    p = root / "data" / "fixtures" / VLLM
    expected = hashlib.sha256(p.read_bytes()).hexdigest()
    records, evidences = _load(root, "vllm", VLLM)
    assert evidences[0].raw_sha256 == expected
    assert records[0].raw_metrics["raw_sha256"] == expected


def test_fixtures_are_not_official_evidence(root):
    """synthetic_fixture 不得与官方数据混到同一证据级别。"""
    for kind, name in (("sglang", SGLANG), ("vllm", VLLM)):
        records, evidences = _load(root, kind, name)
        assert records[0].performance_source_kind == "synthetic_fixture"
        assert records[0].verification_status == "synthetic_fixture"
        assert evidences[0].source_type == "assumption"
        assert "synthetic_fixture_not_official_performance_data" in evidences[0].quality_flags
        assert evidences[0].source_url is None


def test_metric_definitions_traceable_per_source(root):
    sg, _ = _load(root, "sglang", SGLANG)
    vl, _ = _load(root, "vllm", VLLM)
    assert sg[0].metric_definitions != vl[0].metric_definitions
    assert "vLLM" in vl[0].metric_definitions["source_schema"]
    assert "percentile" in sg[0].metric_definitions["aggregation"]


# --------------------------------------------------------------------------
# 入库与证据分级
# --------------------------------------------------------------------------


def test_import_records_fixture_into_db(full_db):
    db, repo = open_db(full_db)
    try:
        with db.session() as session:
            records = repo.list_benchmarks(session)
            evidences = {e.evidence_id: e for e in repo.list_evidence(session)}
    finally:
        db.dispose()

    fixtures = [r for r in records if r.performance_source_kind == "synthetic_fixture"]
    assert len(fixtures) == 4
    for rec in fixtures:
        assert evidences[rec.evidence_id].source_type == "assumption"
    # Excel 记录数量不受 fixture 影响
    assert sum(1 for r in records if r.performance_source_kind == "internal_excel") == 32


def test_import_benchmark_file_reports_missing(full_db, root):
    info = import_benchmark_file(
        "vllm",
        root / "data" / "fixtures" / VLLM,
        root / "config" / "fixture_vllm.yaml",
        full_db,
    )
    assert "fixture_vllm_synthetic_rec" in info["records"]
    assert "tpot_mean_ms" in info["missing_fields"]["fixture_vllm_synthetic_rec"]


def test_import_unknown_kind_raises(full_db):
    from llm_capacity_pricing.services import ServiceError

    with pytest.raises(ServiceError, match="未知的适配器类型"):
        import_benchmark_file("tensorrt", "x.json", "y.yaml", full_db)


def test_adapter_output_is_json_serializable(root):
    records, _ = _load(root, "vllm", VLLM)
    payload = json.dumps(records[0].model_dump(mode="json"), ensure_ascii=False)
    assert "452.7" in payload
