"""服务层：CLI 与测试共用的编排逻辑。

把"导入 -> 校验 -> 计算 -> 持久化 -> 报告"串起来，CLI 只做参数解析。
"""

from __future__ import annotations

import json

import yaml
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Sequence

from llm_capacity_pricing import SECONDS_PER_YEAR
from llm_capacity_pricing.engines.calculator import (
    calculate,
    make_legacy_scenario,
    make_scenario,
)
from llm_capacity_pricing.importers.common import load_metadata
from llm_capacity_pricing.importers.official_manifest import (
    OfficialManifestError,
    build_all_candidates,
)
from llm_capacity_pricing.importers.excel_importer import (
    EXCEL_ABSENT_FIELDS,
    ImportReport,
    import_excel,
)
from llm_capacity_pricing.importers.sglang_adapter import parse_sglang_bench_serving_jsonl_full
from llm_capacity_pricing.importers.sglang_cookbook import build_numeric_candidates
from llm_capacity_pricing.schemas.performance import SlaPolicy
from llm_capacity_pricing.perf.benchmark_matcher import (
    collect_signatures,
    match_all,
)
from llm_capacity_pricing.perf.capacity_analyzer import (
    SustainabilityCriteria,
    analyze_runs,
)
from llm_capacity_pricing.perf.estimator import estimate as _estimate_performance
from llm_capacity_pricing.perf.performance_profile import run_from_adapter
from llm_capacity_pricing.perf.ranker import (
    _best_match,
    rank_deployments as _rank_deployments,
)
from llm_capacity_pricing.perf.sla_evaluator import evaluate_sla as _evaluate_sla
from llm_capacity_pricing.reporting.performance_report import build_performance_report
from llm_capacity_pricing.reporting.sizing_report import build_sizing_report
from llm_capacity_pricing.schemas.sizing import DeploymentCandidate, ModelSpec, Precision
from llm_capacity_pricing.sizing.hardware import load_hardware as _load_hardware_catalog
from llm_capacity_pricing.sizing.model_spec import load_model_specs
from llm_capacity_pricing.sizing.planner import (
    PlanningAssumptions,
    plan_deployment as _plan_deployment,
)
from llm_capacity_pricing.importers.vllm_adapter import parse_vllm_bench_serve_json_full
from llm_capacity_pricing.reporting.json_export import (
    build_legacy_export,
    build_scenario_export,
    write_json,
)
from llm_capacity_pricing.reporting.markdown import SENSITIVITY_LEVELS, build_report
from llm_capacity_pricing.schemas.benchmark import BenchmarkRecord
from llm_capacity_pricing.schemas.costs import HardwarePrice, TokenPrice
from llm_capacity_pricing.schemas.pricing import PRICING_ENGINE_VERSION, PricingResult
from llm_capacity_pricing.storage.database import Database
from llm_capacity_pricing.storage.repository import Repository

#: 任务书 §9 的期望数据总量（仅针对 Excel 导入的部分）
EXPECTED_COUNTS = {"hardware_price": 2, "token_price": 3, "benchmark_record": 32}
#: Excel 来源的效能记录条数（与 fixture 记录分开计数）
EXPECTED_EXCEL_BENCHMARKS = 32
#: 视为"测试数据"的来源类型：默认业务计算一律排除，需显式开关才纳入
TEST_DATA_KINDS = ("synthetic_fixture",)
#: 默认敏感性分析的档位数量（10/30/50/70/100%）
DEFAULT_SENSITIVITY_LEVEL_COUNT = 5


class ServiceError(Exception):
    """服务层可预期的错误（CLI 会转成非零退出码）。"""


def is_test_record(record: BenchmarkRecord) -> bool:
    """该记录是否为人工构造的测试数据（不得进入默认业务口径）。"""
    return record.performance_source_kind in TEST_DATA_KINDS


def split_by_scope(
    records: Sequence[BenchmarkRecord], *, include_test_data: bool
) -> tuple[list[BenchmarkRecord], list[BenchmarkRecord]]:
    """按业务/测试切分记录。返回 ``(纳入计算的, 被排除的)``。"""
    business = [r for r in records if not is_test_record(r)]
    excluded = [r for r in records if is_test_record(r)]
    return (business + excluded if include_test_data else business), (
        [] if include_test_data else excluded
    )


def open_db(db_path: str | Path) -> tuple[Database, Repository]:
    db = Database(db_path)
    db.create_all()
    return db, Repository(db)


def init_db(db_path: str | Path) -> dict[str, Any]:
    db, repo = open_db(db_path)
    with db.session() as session:
        counts = repo.counts(session).as_dict()
    tables = sorted(db.engine.dialect.get_table_names(db.engine.connect()))
    db.dispose()
    return {"db_path": str(db_path), "tables": tables, "counts": counts}


# --------------------------------------------------------------------------
# 导入
# --------------------------------------------------------------------------


def import_excel_into_db(
    excel_path: str | Path,
    db_path: str | Path,
    *,
    imported_at: datetime | None = None,
) -> dict[str, Any]:
    report: ImportReport = import_excel(excel_path, imported_at=imported_at)
    db, repo = open_db(db_path)
    try:
        with db.session() as session:
            before = repo.counts(session).as_dict()
            repo.upsert_evidence(session, report.evidences)
            repo.upsert_hardware_prices(session, report.hardware_prices)
            repo.upsert_token_prices(session, report.token_prices)
            repo.upsert_benchmarks(session, report.benchmarks)
            repo.record_import_run(
                session,
                import_run_id=f"excel::{report.raw_sha256[:16]}",
                source_file=report.source_file,
                raw_sha256=report.raw_sha256,
                imported_at=report.imported_at,
                evidence_count=len(report.evidences),
                hardware_price_count=len(report.hardware_prices),
                token_price_count=len(report.token_prices),
                benchmark_count=len(report.benchmarks),
                notes=report.notes,
            )
            session.commit()
            after = repo.counts(session).as_dict()
    finally:
        db.dispose()
    return {
        "db_path": str(db_path),
        "source_file": report.source_file,
        "raw_sha256": report.raw_sha256,
        "imported_at": report.imported_at.isoformat(),
        "sheet_rows": report.sheet_rows,
        "parsed": {
            "evidence": len(report.evidences),
            "hardware_price": len(report.hardware_prices),
            "token_price": len(report.token_prices),
            "benchmark_record": len(report.benchmarks),
        },
        "counts_before": before,
        "counts_after": after,
        "idempotent": before == after,
        "unmatched_gpus": report.unmatched_gpus,
        "unmatched_models": report.unmatched_models,
        "notes": report.notes,
    }


def import_benchmark_file(
    kind: str,
    file_path: str | Path,
    metadata_path: str | Path,
    db_path: str | Path,
) -> dict[str, Any]:
    if kind not in ("sglang", "vllm"):
        raise ServiceError(f"未知的适配器类型: {kind!r}（支持 sglang / vllm）")
    metadata = load_metadata(metadata_path)
    metadata.setdefault("source_file", Path(file_path).name)
    if kind == "sglang":
        records, evidences = parse_sglang_bench_serving_jsonl_full(file_path, metadata)
    else:
        records, evidences = parse_vllm_bench_serve_json_full(file_path, metadata)

    db, repo = open_db(db_path)
    try:
        with db.session() as session:
            repo.upsert_evidence(session, evidences)
            repo.upsert_benchmarks(session, records)
            session.commit()
    finally:
        db.dispose()
    return {
        "kind": kind,
        "file": str(file_path),
        "metadata": str(metadata_path),
        "records": [r.record_id for r in records],
        "evidence_ids": [e.evidence_id for e in evidences],
        "performance_source_kind": [r.performance_source_kind for r in records],
        "verification_status": [r.verification_status for r in records],
        "missing_fields": {r.record_id: r.missing_fields for r in records},
    }


def import_official_candidates(
    manifest_path: str | Path,
    db_path: str | Path,
    *,
    root: str | Path | None = None,
) -> dict[str, Any]:
    """把 manifest 中的 ``partial_evidence`` 官方条目落库为候选记录。

    候选记录**不进入** ``benchmark_record``，也不参与任何经济性计算。
    """
    base = Path(root) if root is not None else Path(manifest_path).resolve().parent.parent
    candidates, evidences, skipped = build_all_candidates(manifest_path, root=base)
    db, repo = open_db(db_path)
    try:
        with db.session() as session:
            repo.upsert_evidence(session, evidences)
            repo.upsert_official_candidates(session, candidates)
            session.commit()
    finally:
        db.dispose()
    return {
        "manifest": str(manifest_path),
        "candidate_count": len(candidates),
        "skipped_count": len(skipped),
        "candidates": [
            {
                "candidate_id": c.candidate_id,
                "publisher": c.publisher,
                "framework": c.framework,
                "verification_status": c.verification_status,
                "hardware_binding": c.hardware_binding,
                "hardware_candidates": c.hardware_candidates,
                "is_throughput_benchmark": c.is_throughput_benchmark,
                "metric_count": len(c.metrics),
                "missing_fields": c.missing_fields,
            }
            for c in candidates
        ],
        "skipped": skipped,
    }


def official_coverage_summary(db_path: str | Path) -> dict[str, Any]:
    """官方覆盖摘要：入册候选（两个家族）+ 门禁统计。"""
    db, repo = open_db(db_path)
    try:
        with db.session() as session:
            manifest_candidates = repo.list_official_candidates(session)
            numeric_candidates = repo.list_numeric_candidates(session)
            family_counts = repo.candidate_family_counts(session)
    finally:
        db.dispose()
    all_candidates = list(manifest_candidates) + list(numeric_candidates)
    by_publisher: dict[str, int] = {}
    for c in all_candidates:
        by_publisher[c.publisher] = by_publisher.get(c.publisher, 0) + 1
    return {
        "candidate_count": len(all_candidates),
        "by_family": family_counts,
        "manifest_candidates": len(manifest_candidates),
        "numeric_candidates": len(numeric_candidates),
        # 完整可复现的 official_benchmark 仍为 0（见 docs/official_coverage_gaps.md）
        "verified_count": 0,
        "by_publisher": by_publisher,
        "with_parsed_metrics": sum(1 for c in all_candidates if c.metrics),
        "throughput_benchmarks": sum(1 for c in all_candidates if c.is_throughput_benchmark),
        "single_request_smoke_tests": sum(
            1 for c in all_candidates if c.is_throughput_benchmark is False
        ),
        # 数值型候选与原文的逐字段核对情况（M7）
        "numeric_verified_against_source": sum(
            1 for c in numeric_candidates if c.verification.verified_against_source
        ),
        "numeric_missing_model_revision": sum(
            1 for c in numeric_candidates if "model_revision" in c.missing_fields
        ),
        "numeric_source_count": len({c.source_id for c in numeric_candidates}),
        # M9 派生门禁：能作参考展示 / 能评估 SLA / 能进经济性计算
        "gates": {
            "can_reference_performance": sum(
                1 for c in all_candidates if c.can_reference_performance
            ),
            "can_evaluate_sla": sum(1 for c in all_candidates if c.can_evaluate_sla),
            "can_calculate_pricing": sum(1 for c in all_candidates if c.can_calculate_pricing),
            "commercial_quote_allowed": 0,
        },
    }


def import_sglang_candidates(
    incoming_path: str | Path,
    db_path: str | Path,
    *,
    root: str | Path | None = None,
    on_verification_failure: str = "fail",
) -> dict[str, Any]:
    """M7：导入 SGLang Cookbook 官方**数值型**候选，并逐字段核对原文。

    候选以 ``official_<record_id>`` 为稳定主键幂等 upsert，**不进入**
    ``benchmark_record``，也不参与任何经济性计算。
    """
    base = Path(root) if root is not None else Path(incoming_path).resolve().parent.parent
    # Fail-Closed：候选与证据在**打开数据库之前**全部构建完成；
    # 任一来源校验失败都会在这里抛错，因此不会留下"部分已提交"的可信记录。
    candidates, evidences, summary = build_numeric_candidates(
        incoming_path, root=base, on_verification_failure=on_verification_failure
    )
    db, repo = open_db(db_path)
    try:
        with db.session() as session:
            repo.upsert_evidence(session, evidences)
            repo.upsert_numeric_candidates(session, candidates)
            session.commit()
    finally:
        db.dispose()
    return {
        **summary,
        "imported_count": len(candidates),
        "candidate_ids": [c.candidate_id for c in candidates],
        "record_ids": [c.record_id for c in candidates],
        "per_record": [
            {
                "record_id": c.record_id,
                "source_section": c.source_section,
                "verified_against_source": c.verification.verified_against_source,
                "mismatch_count": len(c.verification.mismatches),
                "missing_fields": c.missing_fields,
                "can_reference_performance": c.can_reference_performance,
                "can_evaluate_sla": c.can_evaluate_sla,
                "can_calculate_pricing": c.can_calculate_pricing,
            }
            for c in candidates
        ],
    }


# --------------------------------------------------------------------------
# 校验
# --------------------------------------------------------------------------


def _traceable_row(record_id: str) -> int | None:
    parts = record_id.rsplit("_", 1)
    if len(parts) == 2 and parts[1].isdigit():
        return int(parts[1])
    return None


def validate_import(db_path: str | Path) -> dict[str, Any]:
    """导入后一致性校验。返回逐项检查结果与是否全部通过。"""
    db, repo = open_db(db_path)
    try:
        with db.session() as session:
            counts = repo.counts(session).as_dict()
            records = repo.list_benchmarks(session)
            evidences = {e.evidence_id: e for e in repo.list_evidence(session)}
            hardware = repo.list_hardware_prices(session)
            tokens = repo.list_token_prices(session)
    finally:
        db.dispose()

    checks: list[dict[str, Any]] = []

    def check(name: str, ok: bool, detail: str) -> None:
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    excel_records = [r for r in records if r.performance_source_kind == "internal_excel"]
    fixture_records = [
        r for r in records if r.performance_source_kind == "synthetic_fixture"
    ]
    other_records = [
        r
        for r in records
        if r.performance_source_kind not in ("internal_excel", "synthetic_fixture")
    ]

    # 价格表只来自 Excel，可直接比对总量
    for key in ("hardware_price", "token_price"):
        expected = EXPECTED_COUNTS[key]
        check(
            f"count::{key}",
            counts[key] == expected,
            f"期望 {expected}，实际 {counts[key]}",
        )
    # 效能记录按来源分级计数：Excel 32 条、fixture 单独统计、其他来源（如官方）另列
    check(
        "count::benchmark_record",
        len(excel_records) == EXPECTED_EXCEL_BENCHMARKS,
        f"Excel 来源期望 {EXPECTED_EXCEL_BENCHMARKS}，实际 {len(excel_records)}"
        f"（库内总记录 {counts['benchmark_record']} = Excel {len(excel_records)}"
        f" + fixture {len(fixture_records)} + 其他来源 {len(other_records)}）",
    )
    check(
        "source_kinds_separated",
        len(fixture_records) + len(other_records) == counts["benchmark_record"] - len(excel_records),
        f"fixture {len(fixture_records)} 条、其他来源 {len(other_records)} 条，"
        f"均不与 Excel {len(excel_records)} 条混合计数",
    )

    # 每条记录可追溯回工作表行号
    bad_trace = [r.record_id for r in excel_records if _traceable_row(r.record_id) is None]
    check("traceable_row_numbers", not bad_trace, f"无法解析行号的记录: {bad_trace}")

    missing_ev = [r.record_id for r in excel_records if r.evidence_id not in evidences]
    check("evidence_present", not missing_ev, f"缺少证据的记录: {missing_ev}")

    ev_loc_ok = all(
        evidences[r.evidence_id].source_sheet and evidences[r.evidence_id].source_row
        for r in excel_records
        if r.evidence_id in evidences
    )
    check("evidence_has_sheet_and_row", ev_loc_ok, "证据是否都带 source_sheet/source_row")

    # 不允许把缺失字段填成 0
    fabricated = {
        r.record_id: [
            f for f in EXCEL_ABSENT_FIELDS if getattr(r, f, None) not in (None, [], {})
        ]
        for r in excel_records
    }
    fabricated = {k: v for k, v in fabricated.items() if v}
    check(
        "no_fabricated_zeros",
        not fabricated,
        f"Excel 未提供的字段被填值的记录: {fabricated}",
    )

    # 币种假设必须显式记录
    cur_ok = all(h.currency is None and h.currency_assumption for h in hardware) and all(
        t.currency is None and t.currency_assumption for t in tokens
    )
    check("currency_assumption_recorded", cur_ok, "价格币种为空且 currency_assumption 已写明")

    proxy_ok = all(r.legacy_proxy_for_billable_hit_rate for r in excel_records)
    check(
        "legacy_hit_rate_proxy_flagged",
        proxy_ok,
        "旧表 H 列命中率作为定价代理时必须带 legacy_proxy_for_billable_hit_rate=True",
    )

    cache_ok = all(r.cache_metric_semantics == "unknown" for r in excel_records)
    check(
        "cache_semantics_not_fabricated",
        cache_ok,
        "Excel 命中率语义必须保持 unknown，不得宣称为计费命中",
    )

    # 部署策略只做未核实解析
    strat_ok = all(
        (r.deployment_strategy_parsed is None)
        or (r.deployment_strategy_parsed.get("verified") is False)
        for r in excel_records
    )
    check("strategy_parse_unverified", strat_ok, "策略解析结果均标记 verified=False")

    # 拓扑字段不得由字符串解析补齐
    topo_null = all(
        r.tp_size is None and r.ep_size is None and r.dp_size is None and r.pp_size is None
        for r in excel_records
    )
    check(
        "topology_fields_left_null",
        topo_null,
        "tp/dp/ep/pp 均保持空值，未从策略字符串补齐",
    )

    # 来源不混淆
    src_ok = all(
        (evidences[r.evidence_id].publisher or "").upper() == "INFERENCEX"
        for r in excel_records
        if r.evidence_id in evidences
    )
    check(
        "source_attribution_inferencex",
        src_ok,
        "Excel 效能数据来源标注为 InferenceX（第三方），未冒充 SGLang/vLLM 官方数据",
    )

    # 每条记录都要有关联来源 + 至少一个质量标记（任务 3）
    no_source = [
        r.record_id
        for r in records
        if r.evidence_id not in evidences
        or not (
            evidences[r.evidence_id].publisher
            or evidences[r.evidence_id].source_url
            or evidences[r.evidence_id].source_file
        )
    ]
    check("every_record_has_source", not no_source, f"缺少可追溯来源的记录: {no_source}")

    no_flags = [r.record_id for r in records if not r.quality_flags]
    check("every_record_has_quality_flags", not no_flags, f"无质量标记的记录: {no_flags}")

    # 历史外部来源不得被标为内部实测（任务 3）
    mislabeled = [
        r.record_id
        for r in excel_records
        if r.evidence_id in evidences
        and (
            evidences[r.evidence_id].is_internal_measurement
            or evidences[r.evidence_id].measurement_origin != "historical_external_third_party"
        )
    ]
    check(
        "excel_marked_as_historical_external",
        not mislabeled,
        f"被错误标为内部实测的记录: {mislabeled}",
    )
    missing_flag = [
        r.record_id
        for r in excel_records
        if r.evidence_id in evidences
        and "historical_external_source_not_internal_measurement"
        not in evidences[r.evidence_id].quality_flags
    ]
    check(
        "historical_external_flag_present",
        not missing_flag,
        f"缺少 historical_external_source_not_internal_measurement 标记的记录: {missing_flag}",
    )
    check(
        "no_internal_measurement_declared",
        not any(e.is_internal_measurement for e in evidences.values()),
        "库中不存在任何 internal_measurement 声明（本阶段无自有实测数据）",
    )

    # 集群吞吐仅同配置归一化
    cluster_ok = all(
        (r.input_tps_cluster_basis is not None)
        for r in excel_records
        if r.input_tps_cluster is not None
    )
    check(
        "cluster_throughput_basis_documented",
        cluster_ok,
        "集群吞吐若存在，必须带 derived_from_per_gpu 归一化说明",
    )

    passed = sum(1 for c in checks if c["ok"])
    return {
        "db_path": str(db_path),
        "checks": checks,
        "passed": passed,
        "failed": len(checks) - passed,
        "ok": passed == len(checks),
    }


def validate_results(
    db_path: str | Path, *, include_test_data: bool = False
) -> dict[str, Any]:
    """校验计算结果的可追溯性与 SLA 口径（任务 3）。

    逐条确认：

    * 结果能回溯到性能记录（``performance_record_id`` 存在）；
    * 收入侧使用的 ``token_price_id`` 存在且模型名与记录一致；
    * 成本侧使用的 ``hardware_price_id`` 存在且 GPU 型号与记录一致；
    * ``evidence_ids`` 全部能在证据表中找到；
    * ``scope`` 与记录来源一致（fixture -> test）；
    * 缺 TTFT/TPOT P99 时 ``sla_status`` 必须为 ``not_evaluable``。
    """
    results, meta = calculate_all(
        db_path, mode="legacy_full_load", persist=False, include_test_data=include_test_data
    )
    db, repo = open_db(db_path)
    try:
        with db.session() as session:
            records = {r.record_id: r for r in repo.list_benchmarks(session)}
            hardware = {h.hardware_price_id: h for h in repo.list_hardware_prices(session)}
            tokens = {t.token_price_id: t for t in repo.list_token_prices(session)}
    finally:
        db.dispose()

    checks: list[dict[str, Any]] = []

    def check(name: str, bad: list[str], detail: str) -> None:
        checks.append(
            {
                "name": name,
                "ok": not bad,
                "detail": detail if not bad else f"{detail}；问题记录: {bad[:5]}",
            }
        )

    check(
        "result_linked_to_performance_record",
        [
            r.scenario_id
            for r in results
            if not r.performance_record_id or r.performance_record_id not in records
        ],
        f"{len(results)} 条结果均可回溯到性能记录",
    )
    check(
        "result_linked_to_hardware_price",
        [
            r.scenario_id
            for r in results
            if r.gpu_annual_cost_10k is not None
            and (
                not r.hardware_price_id
                or r.hardware_price_id not in hardware
                or hardware[r.hardware_price_id].gpu_model
                != records[r.performance_record_id].hardware_model
            )
        ],
        "有成本的结果均能回溯到同型号硬件价格",
    )
    check(
        "result_linked_to_token_price",
        [
            r.scenario_id
            for r in results
            if r.total_revenue_10k_per_gpu_year is not None
            and (
                not r.token_price_id
                or r.token_price_id not in tokens
                or tokens[r.token_price_id].model_name
                != records[r.performance_record_id].model_name
            )
        ],
        "有收入的结果均能回溯到同模型 Token 单价",
    )
    missing_evidence = [
        r.scenario_id
        for r in results
        if not r.evidence_ids or not all(isinstance(e, str) and e for e in r.evidence_ids)
    ]
    check("result_has_evidence_chain", missing_evidence, "每条结果都带证据链 ID")
    check(
        "result_scope_matches_source_kind",
        [
            r.scenario_id
            for r in results
            if r.scope
            != (
                "test"
                if records[r.performance_record_id].performance_source_kind
                in TEST_DATA_KINDS
                else "business"
            )
        ],
        "结果 scope 与记录来源一致",
    )
    # SLA：缺 P99 必须 not_evaluable
    sla_violations = [
        r.scenario_id
        for r in results
        if r.benchmark_record_id in records
        and (
            records[r.benchmark_record_id].ttft_p99_ms is None
            or records[r.benchmark_record_id].tpot_p99_ms is None
        )
        and r.sla_status != "not_evaluable"
    ]
    check("sla_not_evaluable_without_p99", sla_violations, "缺 P99 时 SLA 恒为 not_evaluable")
    check(
        "sla_reasons_recorded",
        [r.scenario_id for r in results if r.sla_status == "not_evaluable" and not r.sla_not_evaluable_reasons],
        "not_evaluable 的结果都写明了原因",
    )
    check(
        "unlinked_inputs_warned",
        [
            r.scenario_id
            for r in results
            if (r.token_price_id is None or r.hardware_price_id is None)
            and not r.linkage_warnings
        ],
        "缺价格关联时给出 linkage_warnings（不静默）",
    )
    check(
        "currency_declared",
        [r.scenario_id for r in results if not r.currency or not r.cost_scope],
        "每条结果都显式声明币种与成本范围",
    )

    passed = sum(1 for c in checks if c["ok"])
    return {
        "db_path": str(db_path),
        "include_test_data": include_test_data,
        "result_count": len(results),
        "status_counts": meta["status_counts"],
        "checks": checks,
        "passed": passed,
        "failed": len(checks) - passed,
        "ok": passed == len(checks),
    }


# --------------------------------------------------------------------------
# 计算
# --------------------------------------------------------------------------


def _price_indexes(
    hardware: Sequence[HardwarePrice], tokens: Sequence[TokenPrice]
) -> tuple[dict[str, HardwarePrice], dict[str, TokenPrice]]:
    return {h.gpu_model: h for h in hardware}, {t.model_name: t for t in tokens}


def calculate_all(
    db_path: str | Path,
    *,
    mode: str = "legacy_full_load",
    utilization: Decimal | None = None,
    year_seconds: int = SECONDS_PER_YEAR,
    persist: bool = True,
    include_test_data: bool = False,
) -> tuple[list[PricingResult], dict[str, Any]]:
    """批量计算。

    默认**排除** synthetic_fixture 记录（`include_test_data=False`），因为它们是人工
    构造的解析器测试数据，不属于业务口径。需要完整 36 条口径时必须显式开启。
    """
    if mode not in ("legacy_full_load", "scenario"):
        raise ServiceError(f"未知 mode: {mode!r}")
    if mode == "scenario" and utilization is None:
        raise ServiceError("scenario 模式必须提供 utilization")

    db, repo = open_db(db_path)
    try:
        with db.session() as session:
            all_records = repo.list_benchmarks(session)
            records, excluded = split_by_scope(
                all_records, include_test_data=include_test_data
            )
            hardware, tokens = repo.list_hardware_prices(session), repo.list_token_prices(session)
            hp_idx, tp_idx = _price_indexes(hardware, tokens)

            results: list[PricingResult] = []
            for rec in sorted(records, key=lambda r: r.record_id):
                hp = hp_idx.get(rec.hardware_model)
                tp = tp_idx.get(rec.model_name)
                scenario = (
                    make_legacy_scenario(
                        rec.record_id,
                        token_price_id=tp.token_price_id if tp else None,
                        hardware_price_id=hp.hardware_price_id if hp else None,
                        year_seconds=year_seconds,
                    )
                    if mode == "legacy_full_load"
                    else make_scenario(
                        rec.record_id,
                        utilization,
                        token_price_id=tp.token_price_id if tp else None,
                        hardware_price_id=hp.hardware_price_id if hp else None,
                        year_seconds=year_seconds,
                    )
                )
                results.append(calculate(rec, tp, hp, scenario))

            if persist:
                repo.upsert_pricing_results(session, results)
                if not include_test_data:
                    # 默认口径下清掉库里遗留的测试态结果，保证"默认口径的库"里
                    # 不混入 fixture 的计算结果。
                    stale_ids = repo.benchmark_ids_by_kind(session, *TEST_DATA_KINDS)
                    repo.delete_pricing_results(
                        session, benchmark_record_ids=stale_ids, mode=mode
                    )
                session.commit()
    finally:
        db.dispose()

    ok = sum(1 for r in results if r.status == "ok")
    meta = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "pricing_engine_version": PRICING_ENGINE_VERSION,
        "mode": mode,
        "utilization": str(utilization) if utilization is not None else None,
        "calculation_year_seconds": year_seconds,
        "currency_assumption": "CNY; pending business confirmation",
        "cost_scope": "gpu_cost_only",
        "include_test_data": include_test_data,
        "business_record_count": len(results) - sum(1 for r in results if r.scope == "test"),
        "test_record_count": sum(1 for r in results if r.scope == "test"),
        "excluded_test_records": [r.record_id for r in excluded],
        "record_count": len(results),
        "status_counts": {
            "ok": ok,
            "partial": sum(1 for r in results if r.status == "partial"),
            "not_evaluable": sum(1 for r in results if r.status == "not_evaluable"),
        },
    }
    return results, meta


def legacy_recompute_comparison(
    db_path: str | Path,
    *,
    tolerance_10k: Decimal = Decimal("1e-6"),
    tolerance_ratio: Decimal = Decimal("1e-8"),
    year_seconds: int = SECONDS_PER_YEAR,
) -> dict[str, Any]:
    """逐条对比复算结果与旧表 I/J/K/L/M 原始值，返回最大误差与不匹配清单。

    这是 M2 的验收依据；同时用于生成 ``docs/validation_report.md``。
    """
    results, meta = calculate_all(
        db_path, mode="legacy_full_load", year_seconds=year_seconds, persist=False
    )
    db, repo = open_db(db_path)
    try:
        with db.session() as session:
            records = {r.record_id: r for r in repo.list_benchmarks(session)}
    finally:
        db.dispose()

    # 列 -> (复算值取法, 旧表值取法, 容差)
    spec = (
        ("I_input_revenue", lambda r: r.input_revenue_10k_per_gpu_year,
         lambda x: x.legacy_input_revenue_10k_per_gpu_year, tolerance_10k),
        ("J_output_revenue", lambda r: r.output_revenue_10k_per_gpu_year,
         lambda x: x.legacy_output_revenue_10k_per_gpu_year, tolerance_10k),
        ("K_total_revenue", lambda r: r.total_revenue_10k_per_gpu_year,
         lambda x: x.legacy_total_revenue_10k_per_gpu_year, tolerance_10k),
        ("L_cost", lambda r: r.gpu_annual_cost_10k,
         lambda x: x.legacy_cost_10k_per_gpu_year, tolerance_10k),
        ("M_revenue_cost_ratio", lambda r: r.revenue_cost_ratio,
         lambda x: x.legacy_revenue_cost_ratio, tolerance_ratio),
    )

    rows: list[dict[str, Any]] = []
    max_error: dict[str, Decimal] = {name: Decimal(0) for name, *_ in spec}
    mismatches: list[dict[str, Any]] = []

    for res in sorted(results, key=lambda r: r.benchmark_record_id):
        rec = records.get(res.benchmark_record_id)
        if rec is None or rec.performance_source_kind != "internal_excel":
            continue
        row: dict[str, Any] = {"record_id": res.benchmark_record_id, "columns": {}}
        for name, get_got, get_want, tol in spec:
            got, want = get_got(res), get_want(rec)
            if got is None and want is None:
                row["columns"][name] = {"got": None, "want": None, "error": None}
                continue
            if got is None or want is None:
                entry = {"got": str(got), "want": str(want), "error": None,
                         "mismatch": "one_side_missing"}
                row["columns"][name] = entry
                mismatches.append({"record_id": res.benchmark_record_id, "column": name, **entry})
                continue
            err = abs(got - want)
            if err > max_error[name]:
                max_error[name] = err
            entry = {"got": str(got), "want": str(want), "error": str(err)}
            if err > tol:
                entry["mismatch"] = "over_tolerance"
                mismatches.append(
                    {"record_id": res.benchmark_record_id, "column": name,
                     "tolerance": str(tol), **entry}
                )
            row["columns"][name] = entry
        rows.append(row)

    return {
        "record_count": len(rows),
        "max_error": {k: str(v) for k, v in max_error.items()},
        "max_error_decimal": max_error,
        "tolerance_10k": str(tolerance_10k),
        "tolerance_ratio": str(tolerance_ratio),
        "mismatch_count": len(mismatches),
        "mismatches": mismatches,
        "meta": meta,
        "rows": rows,
    }


def build_sensitivity(
    db_path: str | Path,
    *,
    levels: Sequence[Decimal] = tuple(SENSITIVITY_LEVELS),
    year_seconds: int = SECONDS_PER_YEAR,
    include_test_data: bool = False,
    persist: bool = True,
) -> list[PricingResult]:
    """默认敏感性分析：32 条业务记录 × 5 档 = **160** 条。

    只有显式 ``include_test_data=True`` 才会得到 36 × 5 = 180 条（含测试 fixture）。
    """
    out: list[PricingResult] = []
    for level in levels:
        results, _ = calculate_all(
            db_path,
            mode="scenario",
            utilization=level,
            year_seconds=year_seconds,
            persist=persist,
            include_test_data=include_test_data,
        )
        out.extend(results)
    return out


def export_legacy_results(
    db_path: str | Path, out_path: str | Path, *, include_test_data: bool = False
) -> dict[str, Any]:
    results, meta = calculate_all(
        db_path, mode="legacy_full_load", persist=True, include_test_data=include_test_data
    )
    payload = build_legacy_export(results, meta=meta)
    write_json(payload, out_path)
    return {
        "out": str(out_path),
        "result_count": len(results),
        "business_record_count": meta["business_record_count"],
        "test_record_count": meta["test_record_count"],
        "include_test_data": include_test_data,
        "meta": meta,
    }


def export_scenario_results(
    db_path: str | Path,
    out_path: str | Path,
    *,
    levels: Sequence[Decimal] = tuple(SENSITIVITY_LEVELS),
    include_test_data: bool = False,
) -> dict[str, Any]:
    results = build_sensitivity(db_path, levels=levels, include_test_data=include_test_data)
    meta = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "pricing_engine_version": PRICING_ENGINE_VERSION,
        "mode": "scenario",
        "levels": [str(x) for x in levels],
        "cost_scope": "gpu_cost_only",
        "result_type": "scenario_sensitivity_estimate",
        "include_test_data": include_test_data,
        "business_record_count": sum(1 for r in results if r.scope == "business"),
        "test_record_count": sum(1 for r in results if r.scope == "test"),
    }
    write_json(build_scenario_export(results, meta=meta), out_path)
    return {
        "out": str(out_path),
        "result_count": len(results),
        "business_record_count": meta["business_record_count"],
        "test_record_count": meta["test_record_count"],
        "levels": [str(x) for x in levels],
    }


def export_single_scenario(
    db_path: str | Path,
    out_path: str | Path,
    *,
    record_id: str,
    utilization: Decimal,
    include_test_data: bool = False,
) -> dict[str, Any]:
    db, repo = open_db(db_path)
    try:
        with db.session() as session:
            rec = repo.get_benchmark(session, record_id)
            if rec is None:
                raise ServiceError(
                    f"找不到 record_id={record_id!r}；可用记录见 report 输出"
                )
            if is_test_record(rec) and not include_test_data:
                raise ServiceError(
                    f"record_id={record_id!r} 是 synthetic_fixture 测试数据，"
                    "默认不参与业务计算；如确需运行请显式加 --include-test-data"
                )
            hp_idx, tp_idx = _price_indexes(
                repo.list_hardware_prices(session), repo.list_token_prices(session)
            )
            hp, tp = hp_idx.get(rec.hardware_model), tp_idx.get(rec.model_name)
            scenario = make_scenario(
                record_id,
                utilization,
                token_price_id=tp.token_price_id if tp else None,
                hardware_price_id=hp.hardware_price_id if hp else None,
            )
            result = calculate(rec, tp, hp, scenario)
            repo.upsert_pricing_results(session, [result])
            session.commit()
    finally:
        db.dispose()
    meta = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "pricing_engine_version": PRICING_ENGINE_VERSION,
        "mode": "scenario",
        "record_id": record_id,
        "utilization": str(utilization),
        "cost_scope": "gpu_cost_only",
        "result_type": "scenario_sensitivity_estimate",
        "include_test_data": include_test_data,
        "scope": result.scope,
    }
    write_json(build_scenario_export([result], meta=meta), out_path)
    return {
        "out": str(out_path),
        "record_id": record_id,
        "utilization": str(utilization),
        "status": result.status,
        "scope": result.scope,
        "result_type": result.result_type,
        "sla_status": result.sla_status,
        "token_price_id": result.token_price_id,
        "hardware_price_id": result.hardware_price_id,
        "scenario_revenue_10k_per_gpu_year": str(result.scenario_revenue_10k_per_gpu_year),
    }


def export_report(
    db_path: str | Path, out_path: str | Path, *, include_test_data: bool = False
) -> dict[str, Any]:
    """生成 Markdown 报告。

    业务统计口径默认排除 synthetic_fixture；测试记录会被单独列出并注明未参与统计。
    """
    db, repo = open_db(db_path)
    try:
        with db.session() as session:
            evidences = repo.list_evidence(session)
            hardware = repo.list_hardware_prices(session)
            tokens = repo.list_token_prices(session)
            records = repo.list_benchmarks(session)
            import_runs = _import_runs(session)
            official_candidates = repo.list_official_candidates(session)
            numeric_candidates = repo.list_numeric_candidates(session)
    finally:
        db.dispose()

    legacy, legacy_meta = calculate_all(
        db_path, mode="legacy_full_load", persist=True, include_test_data=include_test_data
    )
    scenario = build_sensitivity(db_path, include_test_data=include_test_data)
    text = build_report(
        evidences=evidences,
        hardware_prices=hardware,
        token_prices=tokens,
        records=records,
        legacy_results=legacy,
        scenario_results=scenario,
        import_runs=import_runs,
        db_path=str(db_path),
        include_test_data=include_test_data,
        excluded_test_records=legacy_meta["excluded_test_records"],
        official_candidates=official_candidates,
        numeric_candidates=numeric_candidates,
    )
    p = Path(out_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    business_records = [r for r in records if not is_test_record(r)]
    test_records = [r for r in records if is_test_record(r)]
    return {
        "out": str(p),
        "bytes": len(text.encode("utf-8")),
        "records": len(records),
        # 业务/测试记录数恒按记录来源统计，不随 include_test_data 变化
        "business_records": len(business_records),
        "test_records": len(test_records),
        "excluded_test_records": legacy_meta["excluded_test_records"],
        "scenario_results": len(scenario),
        "legacy_results": len(legacy),
        "include_test_data": include_test_data,
    }


def _import_runs(session) -> list[dict[str, Any]]:
    from llm_capacity_pricing.storage.database import ImportRunRow

    rows = session.query(ImportRunRow).order_by(ImportRunRow.imported_at).all()
    return [
        {
            "import_run_id": r.import_run_id,
            "source_file": r.source_file,
            "raw_sha256": r.raw_sha256,
            "imported_at": r.imported_at.isoformat(sep=" ", timespec="seconds"),
            "benchmark_count": r.benchmark_count,
        }
        for r in rows
    ]


# --------------------------------------------------------------------------
# 第二阶段（M12-M18）：模型 / 硬件 / 资源规划
# --------------------------------------------------------------------------

#: 默认清单路径
DEFAULT_MODEL_MANIFEST = "data/models/model_manifest.yaml"
DEFAULT_HARDWARE_CATALOG = "data/hardware/hardware_catalog.yaml"


def import_models(
    manifest_path: str | Path = DEFAULT_MODEL_MANIFEST,
    db_path: str | Path = "data/local.db",
    *,
    root: str | Path | None = None,
    deployment_precision: Precision = "bf16",
) -> dict[str, Any]:
    """导入模型结构（M13）。归档文件哈希不符时按 Fail-Closed 拒绝。"""
    base = Path(root) if root is not None else Path(manifest_path).resolve().parent.parent
    specs, evidences, summary = load_model_specs(
        manifest_path, root=base, deployment_precision=deployment_precision
    )
    db, repo = open_db(db_path)
    try:
        with db.session() as session:
            repo.upsert_evidence(session, evidences)
            repo.upsert_model_specs(
                session,
                specs,
                evidence_ids={s.model_id: f"ev_model_{s.model_id}" for s in specs},
            )
            session.commit()
    finally:
        db.dispose()
    return {
        **summary,
        "models": [
            {
                "model_id": s.model_id,
                "status": s.status,
                "attention_kind": s.attention_kind,
                "revision": s.revision,
                "weight_total_bytes": s.weight_metadata.total_weight_bytes,
                "missing_fields": s.missing_fields,
            }
            for s in specs
        ],
        "evidences": [e.evidence_id for e in evidences],
    }


def import_hardware(
    catalog_path: str | Path = DEFAULT_HARDWARE_CATALOG,
    db_path: str | Path = "data/local.db",
) -> dict[str, Any]:
    """导入 GPU 规格与服务器形态（M14）。"""
    specs, servers, summary = _load_hardware_catalog(catalog_path)
    db, repo = open_db(db_path)
    try:
        with db.session() as session:
            repo.upsert_hardware(session, specs, servers)
            session.commit()
    finally:
        db.dispose()
    return {
        **summary,
        "hardware": [
            {
                "hardware_id": h.hardware_id,
                "product_name": h.product_name,
                "sku": h.sku,
                "declared": h.hbm_capacity_declared,
                "total_memory_bytes": h.total_memory_bytes,
                "usable_memory_bytes": h.usable_memory_bytes,
                "supported_in_phase2": h.supported_in_phase2,
                "verification_status": h.verification_status,
            }
            for h in specs
        ],
    }


def sizing_inventory(db_path: str | Path = "data/local.db") -> dict[str, Any]:
    """第二阶段库存摘要：模型结构 + GPU 规格 + 服务器形态。"""
    db, repo = open_db(db_path)
    try:
        with db.session() as session:
            models = repo.list_model_specs(session)
            hardware = repo.list_hardware(session)
            servers = repo.list_servers(session)
    finally:
        db.dispose()
    return {
        "model_count": len(models),
        "hardware_count": len(hardware),
        "server_count": len(servers),
        "models": [
            {
                "model_id": m.model_id,
                "status": m.status,
                "attention_kind": m.attention_kind,
                "layers": m.num_hidden_layers,
                "hidden_size": m.hidden_size,
                "kv_heads": m.num_key_value_heads,
                "head_dim": m.head_dim,
                "revision": m.revision,
                "weight_total_bytes": m.weight_metadata.total_weight_bytes,
                "missing_fields": m.missing_fields,
            }
            for m in models
        ],
        "hardware": [
            {
                "hardware_id": h.hardware_id,
                "product_name": h.product_name,
                "sku": h.sku,
                "total_memory_bytes": h.total_memory_bytes,
                "usable_memory_bytes": h.usable_memory_bytes,
                "usable_memory_status": h.usable_memory_status,
                "supported_in_phase2": h.supported_in_phase2,
                "verification_status": h.verification_status,
                "fields_pending_verification": h.fields_pending_verification,
            }
            for h in hardware
        ],
        "servers": [
            {
                "server_id": s.server_id,
                "profile_name": s.profile_name,
                "gpu_hardware_id": s.gpu_hardware_id,
                "gpus_per_node": s.gpus_per_node,
                "verification_status": s.verification_status,
            }
            for s in servers
        ],
    }


def _load_for_planning(db_path: str | Path, model_id: str, hardware_id: str, server_id: str | None):
    db, repo = open_db(db_path)
    try:
        with db.session() as session:
            model = repo.get_model_spec(session, model_id)
            hardware = repo.get_hardware(session, hardware_id)
            server = repo.get_server(session, server_id) if server_id else None
            servers = repo.list_servers(session)
    finally:
        db.dispose()
    if model is None:
        raise ServiceError(f"模型不存在: {model_id}（先运行 import-models）")
    if hardware is None:
        raise ServiceError(f"硬件不存在: {hardware_id}（先运行 import-hardware）")
    if server is None and servers:
        server = next((s for s in servers if s.gpu_hardware_id == hardware_id), None)
    return model, hardware, server, servers


def make_assumptions(
    *,
    per_gpu_usable_bytes: int | None = None,
    usable_fraction_of_nominal: Decimal | None = None,
    usable_memory_source: str | None = None,
    usable_memory_source_kind: str | None = None,
    max_context_override: int | None = None,
    max_context_override_source: str | None = None,
    runtime_reserve_fraction: Decimal | None = None,
    runtime_reserve_source: str | None = None,
    runtime_items: dict[str, dict[str, Any]] | None = None,
    block_size_tokens: int | None = None,
    global_concurrency: int | None = None,
    context_length_per_request: int | None = None,
    tp_sizes: Sequence[int] = (1, 2, 4, 8),
    dp_sizes: Sequence[int] = (1, 2, 4, 8),
) -> PlanningAssumptions:
    """把 CLI/调用方输入收敛成 :class:`PlanningAssumptions`（所有假设显式化）。"""
    return PlanningAssumptions(
        per_gpu_usable_bytes=per_gpu_usable_bytes,
        usable_fraction_of_nominal=usable_fraction_of_nominal,
        usable_memory_source=usable_memory_source,
        usable_memory_source_kind=usable_memory_source_kind,
        max_context_override=max_context_override,
        max_context_override_source=max_context_override_source,
        runtime_items=runtime_items or {},
        runtime_reserve_fraction=runtime_reserve_fraction,
        runtime_reserve_source=runtime_reserve_source,
        block_size_tokens=block_size_tokens,
        global_concurrency=global_concurrency,
        context_length_per_request=context_length_per_request,
        tp_sizes=tuple(tp_sizes),
        dp_sizes=tuple(dp_sizes),
    )


def plan_deployment_for(
    db_path: str | Path,
    *,
    model_id: str,
    hardware_id: str,
    server_id: str | None = None,
    assumptions: PlanningAssumptions,
    weight_precision: Precision | None = None,
    kv_precision: str = "bf16",
) -> dict[str, Any]:
    """生成 TP/DP 候选与静态可行性（M17）。"""
    model, hardware, server, servers = _load_for_planning(db_path, model_id, hardware_id, server_id)
    from llm_capacity_pricing.sizing.planner import resolve_usable_memory as _resolve

    _resolved_usable = _resolve(hardware, assumptions)
    candidates = _plan_deployment(
        model,
        hardware,
        assumptions=assumptions,
        server=server,
        weight_precision=weight_precision,
        kv_precision=kv_precision,
    )
    return {
        "request": {
            "description": (
                f"{model_id} @ {hardware.product_name}, "
                f"{weight_precision or model.deployment_precision} weights / {kv_precision} kv, "
                f"ctx={assumptions.context_length_per_request}, concurrency={assumptions.global_concurrency}"
            ),
            "model_id": model_id,
            "hardware_id": hardware_id,
            "server_id": server.server_id if server else None,
            "weight_precision": weight_precision or model.deployment_precision,
            "kv_precision": kv_precision,
            "context_length_per_request": assumptions.context_length_per_request,
            "global_concurrency": assumptions.global_concurrency,
            "block_size_tokens": assumptions.block_size_tokens,
            # 用与计算完全相同的解析函数取值，避免报告与计算口径不一致
            # （例如只给 --usable-fraction 时，直接读 assumptions 会得到 None）
            "per_gpu_usable_bytes": _resolved_usable[0],
            "usable_memory_source": _resolved_usable[1],
            "usable_memory_source_kind": (
                "measured"
                if hardware.usable_memory_bytes is not None
                else assumptions.usable_memory_source_kind
            ),
            "model_max_context_declared": model.max_position_embeddings,
            "max_context_override": assumptions.max_context_override,
            "max_context_override_source": assumptions.max_context_override_source,
            "runtime_reserve_description": (
                f"合并预留 可用显存 × {assumptions.runtime_reserve_fraction}"
                if assumptions.runtime_reserve_fraction is not None
                else (f"{len(assumptions.runtime_items)} 项显式预留" if assumptions.runtime_items else None)
            ),
            "runtime_reserve_source": assumptions.runtime_reserve_source,
        },
        "model": model,
        "hardware": hardware,
        "servers": servers,
        "candidates": candidates,
        "status_counts": _status_counts(candidates),
    }


def _status_counts(candidates: Sequence[DeploymentCandidate]) -> dict[str, int]:
    out: dict[str, int] = {}
    for c in candidates:
        out[c.feasibility.status] = out.get(c.feasibility.status, 0) + 1
    return out


def export_plan(
    db_path: str | Path,
    out_path: str | Path,
    *,
    model_id: str,
    hardware_id: str,
    server_id: str | None = None,
    assumptions: PlanningAssumptions,
    weight_precision: Precision | None = None,
    kv_precision: str = "bf16",
    report_path: str | Path | None = None,
) -> dict[str, Any]:
    """导出规划结果：JSON（结构化）+ 可选 Markdown 报告。"""
    plan = plan_deployment_for(
        db_path,
        model_id=model_id,
        hardware_id=hardware_id,
        server_id=server_id,
        assumptions=assumptions,
        weight_precision=weight_precision,
        kv_precision=kv_precision,
    )
    payload = {
        "export_kind": "deployment_plan",
        "formula_versions": {"weights": "weights-1.0", "kv": "kv-1.0", "memory": "memory-1.0"},
        "boundary_notices": [
            "静态资源规划结果：不代表可成功启动，不代表满足 SLA",
            "可用显存若为假设值，结论仅为『条件可行』，落地前必须实测复核",
            "未知运行时开销未被当作 0，会显式列出",
        ],
        "request": plan["request"],
        "status_counts": plan["status_counts"],
        "candidates": [
            {
                "candidate_id": c.candidate_id,
                "summary": c.summary,
                "gpu_count": c.gpu_count,
                "tp_size": c.tp_size,
                "dp_size": c.dp_size,
                "nodes_required": c.nodes_required,
                "feasibility": {
                    "status": c.feasibility.status,
                    "reasons": c.feasibility.reasons,
                    "assumptions": c.feasibility.assumptions,
                    "unverified_conditions": c.feasibility.unverified_conditions,
                    "risk_flags": c.feasibility.risk_flags,
                },
                "memory": None
                if c.memory is None
                else {
                    "weight_per_rank_bytes": c.memory.weight.per_rank_bytes,
                    "weight_lower_bound_bytes": c.memory.weight.per_rank_bytes_lower_bound,
                    "weight_upper_bound_bytes": c.memory.weight.per_rank_bytes_upper_bound,
                    "weight_total_bytes": c.memory.weight.total_weight_bytes,
                    "weight_consistency_note": c.memory.weight.consistency_note,
                    "weight_basis": c.memory.weight.basis,
                    "kv_per_rank_bytes": c.memory.kv.physical_bytes,
                    "kv_required_resident_tokens": c.memory.kv.required_resident_tokens,
                    "kv_estimated_capacity_tokens": c.memory.kv.estimated_capacity_tokens,
                    "kv_allocated_tokens": c.memory.kv.allocated_tokens,
                    "kv_blocks": c.memory.kv.allocated_blocks,
                    "kv_head_distribution": c.memory.kv.kv_head_distribution,
                    "kv_basis": c.memory.kv.basis,
                    "runtime_known_bytes": c.memory.runtime_total_bytes,
                    "runtime_unknown_items": [
                        r.item for r in c.memory.runtime_items if r.bytes_estimate is None
                    ],
                    "per_gpu_usable_bytes": c.memory.per_gpu_usable_bytes,
                    "total_required_bytes": c.memory.total_required_bytes,
                    "headroom_bytes": c.memory.headroom_bytes,
                    "assumptions": c.memory.assumptions,
                },
            }
            for c in plan["candidates"]
        ],
    }
    p = Path(out_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")

    result: dict[str, Any] = {"plan_json": str(p), "status_counts": plan["status_counts"]}
    if report_path is not None:
        md = build_sizing_report(
            models=[plan["model"]],
            hardware=[plan["hardware"]],
            servers=plan["servers"],
            candidates=plan["candidates"],
            request=plan["request"],
            sources=_catalog_sources(),
        )
        rp = Path(report_path)
        rp.parent.mkdir(parents=True, exist_ok=True)
        rp.write_text(md, encoding="utf-8")
        result["report_markdown"] = str(rp)
        result["report_bytes"] = len(md.encode("utf-8"))
    return result



def estimate_memory_for(
    db_path: str | Path,
    *,
    model_id: str,
    hardware_id: str,
    server_id: str | None = None,
    tp_size: int = 1,
    dp_size: int = 1,
    assumptions: PlanningAssumptions,
    weight_precision: Precision | None = None,
    kv_precision: str = "bf16",
) -> dict[str, Any]:
    """单个 TP/DP 配置的显存估算（M15 权重 + M16 KV/运行时）。"""
    from llm_capacity_pricing.sizing.planner import estimate_memory as _estimate_memory

    model, hardware, server, _servers = _load_for_planning(db_path, model_id, hardware_id, server_id)
    mem = _estimate_memory(
        model,
        hardware,
        tp_size=tp_size,
        dp_size=dp_size,
        weight_precision=weight_precision,
        kv_precision=kv_precision,
        assumptions=assumptions,
    )
    return {
        "model_id": model_id,
        "hardware_id": hardware_id,
        "tp_size": tp_size,
        "dp_size": dp_size,
        "gpu_count": tp_size * dp_size,
        "weight": {
            "total_bytes": mem.weight.total_weight_bytes,
            "per_rank_bytes": mem.weight.per_rank_bytes,
            "per_rank_lower_bound": mem.weight.per_rank_bytes_lower_bound,
            "per_rank_upper_bound": mem.weight.per_rank_bytes_upper_bound,
            "precision_label": mem.weight.per_rank_precision,
            "consistency_note": mem.weight.consistency_note,
            "consistency_delta_bytes": mem.weight.consistency_delta_bytes,
            "basis": mem.weight.basis,
            "sharded_components": mem.weight.sharded_components,
            "replicated_components": mem.weight.replicated_components,
            "uncertain_components": mem.weight.uncertain_components,
        },
        "kv": {
            "attention_kind": mem.kv.attention_kind,
            "readable": mem.kv.readable,
            "kv_heads_per_rank": mem.kv.num_kv_heads_per_rank,
            "kv_head_distribution": mem.kv.kv_head_distribution,
            "required_resident_tokens": mem.kv.required_resident_tokens,
            "estimated_capacity_tokens": mem.kv.estimated_capacity_tokens,
            "allocated_tokens": mem.kv.allocated_tokens,
            "allocated_blocks": mem.kv.allocated_blocks,
            "physical_bytes": mem.kv.physical_bytes,
            "unaligned_bytes": mem.kv.unaligned_bytes,
            "alignment_overhead_bytes": mem.kv.alignment_overhead_bytes,
            "concurrency_per_replica": mem.kv.concurrency_per_replica,
            "degradation_notes": mem.kv.degradation_notes,
            "basis": mem.kv.basis,
            "missing_fields": mem.kv.missing_fields,
        },
        "runtime_items": [
            {
                "item": r.item,
                "bytes": r.bytes_estimate,
                "origin": r.origin,
                "source": r.source,
                "assumption": r.assumption,
            }
            for r in mem.runtime_items
        ],
        "runtime_known_bytes": mem.runtime_total_bytes,
        "known_unknowns": mem.known_unknowns,
        "per_gpu_usable_bytes": mem.per_gpu_usable_bytes,
        "total_required_bytes": mem.total_required_bytes,
        "headroom_bytes": mem.headroom_bytes,
        "assumptions": mem.assumptions,
        "quality_flags": mem.quality_flags,
    }


def _catalog_sources() -> list[dict[str, Any]]:
    """硬件目录里的来源索引（供报告引用）。"""
    try:
        doc = yaml.safe_load(Path(DEFAULT_HARDWARE_CATALOG).read_text(encoding="utf-8"))
    except Exception:  # pragma: no cover - 目录缺失时报告不附来源表
        return []
    out = []
    for sid, s in (doc.get("sources") or {}).items():
        out.append(
            {
                "source_id": sid,
                "publisher": s.get("vendor"),
                "url": s.get("url"),
                "file": s.get("file"),
                "sha256": s.get("sha256"),
            }
        )
    return out


# --------------------------------------------------------------------------
# 第三阶段（M19-M25）：性能评估与 SLA
# --------------------------------------------------------------------------

#: 第三阶段的测试态性能数据前缀（默认不参与正式结论）
PERF_TEST_KINDS = ("synthetic_fixture",)


def _perf_importer(kind: str):
    if kind == "sglang":
        from llm_capacity_pricing.importers.sglang_adapter import (
            parse_sglang_bench_serving_jsonl_full,
        )

        return parse_sglang_bench_serving_jsonl_full
    if kind == "vllm":
        from llm_capacity_pricing.importers.vllm_adapter import (
            parse_vllm_bench_serve_json_full,
        )

        return parse_vllm_bench_serve_json_full
    raise ServiceError(f"未知性能数据来源 {kind!r}（支持 sglang / vllm）")


def import_performance_run(
    file: str | Path,
    metadata_file: str | Path,
    db_path: str | Path = "data/local.db",
    *,
    kind: str = "vllm",
) -> dict[str, Any]:
    """导入一次压测结果（M20）。走第一阶段已验证的版本化适配器。"""
    meta = yaml.safe_load(Path(metadata_file).read_text(encoding="utf-8"))
    records, evidences = _perf_importer(kind)(file, meta)
    if not records:
        raise ServiceError(f"未从 {file} 解析出任何记录")
    runs = [
        run_from_adapter(
            record,
            metadata=meta,
            raw_file=file,
            raw_format=meta.get("format_version", f"{kind}-adapter"),
        )
        for record in records
    ]
    db, repo = open_db(db_path)
    try:
        with db.session() as session:
            repo.upsert_evidence(session, evidences)
            repo.upsert_benchmark_runs(session, runs)
            session.commit()
    finally:
        db.dispose()
    return {
        "file": str(file),
        "kind": kind,
        "run_count": len(runs),
        "run_ids": [r.run_id for r in runs],
        "data_trust": sorted({r.data_trust for r in runs}),
        "load_profile_kind": sorted({r.load_profile_kind for r in runs}),
        "trust_note": (
            "synthetic_fixture：仅用于算法验证，不得用于 SLA 结论"
            if any(r.data_trust == "synthetic_fixture" for r in runs)
            else "measured：可作为 SLA 判定依据（仍需匹配检查）"
        ),
    }


def import_performance_curve(
    index_path: str | Path,
    db_path: str | Path = "data/local.db",
    *,
    root: str | Path | None = None,
    kind: str = "vllm",
) -> dict[str, Any]:
    """按 INDEX 导入一整组曲线样本（离线，无需 GPU）。"""
    import json

    base = Path(root) if root is not None else Path(index_path).resolve().parent.parent.parent
    index = json.loads(Path(index_path).read_text(encoding="utf-8"))
    importer = _perf_importer(kind)
    imported: list[str] = []
    skipped: list[str] = []
    runs = []
    for point in index.get("points", []):
        folder = Path(index_path).parent
        data_file = folder / point["file"]
        meta_file = folder / point["metadata"]
        if not data_file.exists() or not meta_file.exists():
            skipped.append(point["file"])
            continue
        meta = yaml.safe_load(meta_file.read_text(encoding="utf-8"))
        records, _ = importer(data_file, meta)
        if not records:
            skipped.append(point["file"])
            continue
        run = run_from_adapter(
            records[0], metadata=meta, raw_file=data_file, raw_format=meta.get("format_version", "")
        )
        runs.append(run)
        imported.append(run.run_id)
    db, repo = open_db(db_path)
    try:
        with db.session() as session:
            repo.upsert_benchmark_runs(session, runs)
            session.commit()
    finally:
        db.dispose()
    return {
        "index": str(index_path),
        "run_count": len(runs),
        "run_ids": imported,
        "skipped": skipped,
        "data_trust": sorted({r.data_trust for r in runs}),
        "notice": index.get("notice"),
    }


def performance_inventory(db_path: str | Path = "data/local.db") -> dict[str, Any]:
    """第三阶段库存：各类可匹配数据源的签名数量与可信等级分布。"""
    db, repo = open_db(db_path)
    try:
        with db.session() as session:
            records = repo.list_benchmarks(session)
            numeric = repo.list_numeric_candidates(session)
            official = repo.list_official_candidates(session)
            runs = repo.list_benchmark_runs(session)
    finally:
        db.dispose()
    signatures = collect_signatures(
        records=records, numeric_candidates=numeric, official_candidates=official, runs=runs
    )
    by_trust: dict[str, int] = {}
    for sig in signatures:
        by_trust[sig.data_trust] = by_trust.get(sig.data_trust, 0) + 1
    return {
        "signature_count": len(signatures),
        "benchmark_records": len(records),
        "official_candidates": len(numeric) + len(official),
        "benchmark_runs": len(runs),
        "by_trust": by_trust,
        "sla_usable_trust_levels": ["measured_internal", "measured_vendor_published"],
    }


def match_benchmarks_for(
    db_path: str | Path,
    *,
    model_name: str | None,
    hardware_model: str | None = None,
    gpu_count: int | None = None,
    tp_size: int | None = None,
    dp_size: int | None = None,
    weight_precision: str | None = None,
    input_len_target: int | None = None,
    output_len_target: int | None = None,
    dataset_name: str | None = None,
    configured_concurrency: int | None = None,
    required_metrics: Sequence[str] = (),
    require_measured: bool = True,
    allow_synthetic: bool = False,
) -> dict[str, Any]:
    """M19：对目标需求做全源匹配。"""
    from llm_capacity_pricing.schemas.performance import (
        TargetRequirement,
        WorkloadSignature,
    )

    db, repo = open_db(db_path)
    try:
        with db.session() as session:
            signatures = collect_signatures(
                records=repo.list_benchmarks(session),
                numeric_candidates=repo.list_numeric_candidates(session),
                official_candidates=repo.list_official_candidates(session),
                runs=repo.list_benchmark_runs(session),
                include_synthetic=allow_synthetic,
            )
    finally:
        db.dispose()

    target = TargetRequirement(
        model_name=model_name,
        hardware_model=hardware_model,
        gpu_count=gpu_count,
        tp_size=tp_size,
        dp_size=dp_size,
        weight_precision=weight_precision,
        workload=WorkloadSignature(
            input_len_target=input_len_target,
            output_len_target=output_len_target,
            dataset_name=dataset_name,
            configured_concurrency=configured_concurrency,
        ),
        required_metrics=list(required_metrics),
        require_measured=require_measured,
        allow_synthetic=allow_synthetic,
    )
    results = match_all(target, signatures)
    counts: dict[str, int] = {}
    for r in results:
        counts[r.level] = counts.get(r.level, 0) + 1
    return {
        "target": {
            "model_name": model_name,
            "hardware_model": hardware_model,
            "gpu_count": gpu_count,
            "tp_size": tp_size,
            "dp_size": dp_size,
            "workload": {
                "input_len_target": input_len_target,
                "output_len_target": output_len_target,
                "dataset_name": dataset_name,
                "configured_concurrency": configured_concurrency,
            },
            "required_metrics": list(required_metrics),
        },
        "level_counts": counts,
        "matched": [r.model_dump(mode="json") for r in results if r.level != "rejected"],
        "rejected_sample": [r.model_dump(mode="json") for r in results if r.level == "rejected"][:5],
        "note": "匹配等级 ≠ 数据可信等级 ≠ SLA 状态；exact_match 也不代表 SLA 达标",
    }


def capacity_profiles_for(
    db_path: str | Path = "data/local.db",
    *,
    target_concurrency: int | None = None,
    min_duration_s: str | None = None,
    exclude_synthetic: bool = False,
) -> dict[str, Any]:
    """M21：对库内性能记录做容量曲线分析。"""
    db, repo = open_db(db_path)
    try:
        with db.session() as session:
            runs = repo.list_benchmark_runs(session)
    finally:
        db.dispose()
    criteria = (
        SustainabilityCriteria(min_duration_s=Decimal(min_duration_s))
        if min_duration_s
        else None
    )
    profiles = analyze_runs(
        runs, criteria=criteria, target_concurrency=target_concurrency, exclude_synthetic=exclude_synthetic
    )
    return {
        "run_count": len(runs),
        "group_count": len(profiles),
        "profiles": [p.model_dump(mode="json") for p in profiles],
    }


def calibrate_performance_for(
    db_path: str | Path,
    *,
    metric: str,
    target_concurrency: int,
    group_key: str | None = None,
    allow_synthetic: bool = False,
) -> dict[str, Any]:
    """M22：性能估算（精确查询 / 范围内插值 / 不可预测）。"""
    db, repo = open_db(db_path)
    try:
        with db.session() as session:
            runs = repo.list_benchmark_runs(session)
    finally:
        db.dispose()
    from llm_capacity_pricing.perf.capacity_analyzer import grouping_key

    pool = [r for r in runs if group_key is None or grouping_key(r) == group_key]
    if not pool:
        return {"estimate_kind": "not_predictable", "reasons": ["no_runs_in_scope"]}
    if group_key is None:
        # 未指定分组时按"第一条记录所在组"收窄，避免跨配置插值
        group_key = grouping_key(pool[0])
        pool = [r for r in pool if grouping_key(r) == group_key]
    allowed = (
        ("measured_internal", "measured_vendor_published", "synthetic_fixture")
        if allow_synthetic
        else ("measured_internal", "measured_vendor_published")
    )
    result = _estimate_performance(
        metric=metric,
        target_concurrency=target_concurrency,
        runs=pool,
        allowed_trust=allowed,
    )
    payload = result.model_dump(mode="json")
    payload["group_key"] = group_key
    payload["note"] = (
        "measured_reference=直接命中实测；calibrated_estimate=同配置范围内插值（附留出误差）；"
        "not_predictable=数据不足或超出覆盖范围"
    )
    return payload


def evaluate_sla_for(
    db_path: str | Path,
    *,
    requirement: dict[str, Any],
    policy: SlaPolicy | None = None,
    run_id: str | None = None,
    model_name: str | None = None,
    model_revision: str | None = None,
    hardware_model: str | None = None,
    gpu_count: int | None = None,
    tp_size: int | None = None,
    dp_size: int | None = None,
    weight_precision: str | None = None,
    framework: str | None = None,
    framework_version: str | None = None,
    input_len_target: int | None = None,
    output_len_target: int | None = None,
    dataset_name: str | None = None,
    arrival_mode: str | None = None,
    configured_concurrency: int | None = None,
    allow_synthetic: bool = False,
) -> dict[str, Any]:
    """M23：SLA 评估（要求必须能挂到一条匹配的实测记录上）。"""
    from llm_capacity_pricing.schemas.performance import SlaRequirement

    db, repo = open_db(db_path)
    try:
        with db.session() as session:
            runs = repo.list_benchmark_runs(session)
    finally:
        db.dispose()

    sla_req = SlaRequirement.model_validate(requirement)
    policy = policy or SlaPolicy()

    chosen = None
    if run_id:
        chosen = next((r for r in runs if r.run_id == run_id), None)
        if chosen is None:
            raise ServiceError(f"性能记录不存在: {run_id}")
    match_result = None
    if chosen is not None:
        from llm_capacity_pricing.schemas.performance import TargetRequirement, WorkloadSignature
        from llm_capacity_pricing.perf.benchmark_matcher import match, signature_from_run

        target = TargetRequirement(
            model_name=model_name,
            model_revision=model_revision,
            hardware_model=hardware_model,
            gpu_count=gpu_count,
            tp_size=tp_size,
            dp_size=dp_size,
            weight_precision=weight_precision,
            framework=framework,
            framework_version=framework_version,
            workload=WorkloadSignature(
                input_len_target=input_len_target,
                output_len_target=output_len_target,
                dataset_name=dataset_name,
                # CLI 未指定时用 "unknown"（Literal 不接受 None）
                arrival_mode=arrival_mode or "unknown",  # type: ignore[arg-type]
                configured_concurrency=configured_concurrency,
            ),
            require_measured=not allow_synthetic,
            allow_synthetic=allow_synthetic,
        )
        match_result = match(target, signature_from_run(chosen))

    evaluation = _evaluate_sla(
        sla_req, policy=policy, run=chosen, match_result=match_result,
        target_ref=run_id or "unspecified",
    )
    payload = evaluation.model_dump(mode="json")
    payload["note"] = (
        "observed_pass/fail=有匹配实测；estimated_only=仅估算（不算达标）；"
        "not_evaluable=缺关键指标或配置不匹配"
    )
    return payload


def rank_deployments_for(
    db_path: str | Path,
    *,
    model_id: str,
    hardware_id: str,
    requirement: dict[str, Any],
    assumptions: PlanningAssumptions,
    server_id: str | None = None,
    policy: SlaPolicy | None = None,
    input_len_target: int | None = None,
    output_len_target: int | None = None,
    dataset_name: str | None = None,
    model_revision: str | None = None,
    framework: str | None = None,
    framework_version: str | None = None,
    weight_precision: Precision | None = None,
    kv_precision: str = "bf16",
) -> dict[str, Any]:
    """M24：把第二阶段候选接上第三阶段性能证据。"""
    from llm_capacity_pricing.schemas.performance import (
        SlaRequirement,
        TargetRequirement,
        WorkloadSignature,
    )

    plan = plan_deployment_for(
        db_path,
        model_id=model_id,
        hardware_id=hardware_id,
        server_id=server_id,
        assumptions=assumptions,
        weight_precision=weight_precision,
        kv_precision=kv_precision,
    )
    db, repo = open_db(db_path)
    try:
        with db.session() as session:
            runs = repo.list_benchmark_runs(session)
            models = {m.model_id: m for m in repo.list_model_specs(session)}
    finally:
        db.dispose()

    sla_req = SlaRequirement.model_validate(requirement)
    workload = WorkloadSignature(
        input_len_target=input_len_target or assumptions.context_length_per_request,
        output_len_target=output_len_target,
        dataset_name=dataset_name,
        configured_concurrency=assumptions.global_concurrency,
    )
    ranking = _rank_deployments(
        requirement_id=sla_req.requirement_id,
        candidates=plan["candidates"],
        runs=runs,
        sla_requirement=sla_req,
        policy=policy or SlaPolicy(),
        workload=workload,
        model_by_id=models,
        target_identity={
            "model_revision": model_revision,
            "framework": framework,
            "framework_version": framework_version,
        },
    )
    payload = ranking.model_dump(mode="json")
    payload["static_status_counts"] = plan["status_counts"]
    return payload


def export_performance_report(
    db_path: str | Path,
    out_path: str | Path,
    *,
    ranking: dict[str, Any] | None = None,
    profiles: dict[str, Any] | None = None,
    inventory: dict[str, Any] | None = None,
    request: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """M25：生成第三阶段 Markdown 报告。"""
    text = build_performance_report(
        inventory=inventory or performance_inventory(db_path),
        profiles=(profiles or {}).get("profiles", []) if profiles else [],
        ranking=ranking,
        request=request,
    )
    p = Path(out_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return {"report": str(p), "bytes": len(text.encode("utf-8"))}


# --------------------------------------------------------------------------
# 第四阶段（M26-M33）：TCO、业务收益情景、报价门禁与部署决策
# --------------------------------------------------------------------------

#: 第四阶段全部合成数据的显式提示（任何报告都必须带上）
PHASE4_SYNTHETIC_NOTICE = (
    "本仓库中的第四阶段成本/计费数据为**合成示例**（synthetic_example 或"
    " pending_business_confirmation），不是任何真实报价；真实商务数据不入版本库。"
)

#: 允许作为"可信容量证据"的数据信任等级（与第三阶段 SLA 门禁一致）
PHASE4_TRUSTED_LEVELS = ("measured_internal", "measured_vendor_published")


def _load_yaml_file(path: str | Path) -> dict[str, Any]:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ServiceError(f"{path} 顶层必须是映射")
    return raw


def import_cost_catalog(
    file: str | Path, db_path: str | Path = "data/local.db"
) -> dict[str, Any]:
    """M27：导入成本目录与计费规则（离线，幂等）。"""
    from llm_capacity_pricing.importers.cost_catalog import load_cost_catalog

    info = load_cost_catalog(file)
    db, repo = open_db(db_path)
    try:
        with db.session() as session:
            repo.upsert_evidence(session, info["evidence"])
            item_count = repo.upsert_cost_catalog_items(session, info["cost_items"])
            rule_count = repo.upsert_billing_rules(session, info["billing_rules"])
            session.commit()
    finally:
        db.dispose()
    return {
        "file": str(file),
        "cost_item_count": item_count,
        "billing_rule_count": rule_count,
        "evidence_count": len(info["evidence"]),
        "synthetic_item_count": info["synthetic_item_count"],
        "currency": info["currency"],
        "currency_status": info["currency_status"],
        "notice": info["notice"],
        "synthetic_disclaimer": PHASE4_SYNTHETIC_NOTICE,
    }


def cost_catalog_summary(db_path: str | Path = "data/local.db") -> dict[str, Any]:
    """成本目录库存：按类别/口径/状态分布，并显式标明是否含合成数据。"""
    db, repo = open_db(db_path)
    try:
        with db.session() as session:
            items = repo.list_cost_catalog_items(session)
            rules = repo.list_billing_rules(session)
            confirmations = repo.list_commercial_confirmations(session)
    finally:
        db.dispose()

    by_category: dict[str, int] = {}
    by_basis: dict[str, int] = {}
    by_status: dict[str, int] = {}
    missing_price: list[str] = []
    for item in items:
        by_category[item.category] = by_category.get(item.category, 0) + 1
        by_basis[item.basis or "both"] = by_basis.get(item.basis or "both", 0) + 1
        by_status[item.status] = by_status.get(item.status, 0) + 1
        if item.unit_price is None:
            missing_price.append(item.item_id)

    return {
        "item_count": len(items),
        "billing_rule_count": len(rules),
        "confirmation_count": len(confirmations),
        "by_category": by_category,
        "by_basis": by_basis,
        "by_status": by_status,
        "items_missing_price": sorted(missing_price),
        "currencies": sorted({i.currency for i in items if i.currency}),
        "currency_confirmed_count": sum(
            1 for i in items if i.currency_status == "confirmed"
        ),
        "contains_synthetic": any(i.status == "synthetic_example" for i in items),
        "synthetic_disclaimer": PHASE4_SYNTHETIC_NOTICE,
    }


def import_commercial_confirmations(
    file: str | Path, db_path: str | Path = "data/local.db"
) -> dict[str, Any]:
    """M30：导入**人工**商务确认记录（本函数只做持久化，不生成确认）。"""
    from llm_capacity_pricing.schemas.commercial import CommercialConfirmation

    raw = _load_yaml_file(file)
    entries = raw.get("confirmations") or []
    if not entries:
        raise ServiceError(f"{file} 中没有任何 confirmations")
    confirmations = [CommercialConfirmation.model_validate(e) for e in entries]
    db, repo = open_db(db_path)
    try:
        with db.session() as session:
            n = repo.upsert_commercial_confirmations(session, confirmations)
            session.commit()
    finally:
        db.dispose()
    return {
        "file": str(file),
        "confirmation_count": n,
        "statuses": {
            c.confirmation_id: c.status for c in confirmations
        },
    }


def load_cost_plan_templates(file: str | Path) -> dict[str, dict[str, Any]]:
    """读取成本方案模板（按 template_id 索引）。"""
    raw = _load_yaml_file(file)
    templates = raw.get("plan_templates") or []
    if not templates:
        raise ServiceError(f"{file} 中没有任何 plan_templates")
    out: dict[str, dict[str, Any]] = {}
    for tpl in templates:
        tid = tpl.get("template_id")
        if not tid:
            raise ServiceError(f"{file}: 存在缺少 template_id 的模板")
        out[tid] = tpl
    return out


def load_coverage_policy(file: str | Path) -> Any:
    """读取成本范围覆盖策略（V4.1 F4.2）。缺失时返回 None —— 引擎会把 TCO 标为不完整。"""
    from llm_capacity_pricing.schemas.tco import CostCoveragePolicy

    raw = _load_yaml_file(file)
    block = raw.get("coverage_policy")
    if not block:
        return None
    return CostCoveragePolicy.model_validate(block)


def load_demand_scenarios(file: str | Path) -> dict[str, dict[str, Any]]:
    raw = _load_yaml_file(file)
    scenarios = raw.get("demand_scenarios") or []
    if not scenarios:
        raise ServiceError(f"{file} 中没有任何 demand_scenarios")
    out: dict[str, dict[str, Any]] = {}
    for sc in scenarios:
        sid = sc.get("scenario_id")
        if not sid:
            raise ServiceError(f"{file}: 存在缺少 scenario_id 的情景")
        out[sid] = sc
    return out


def build_cost_plan_from_template(
    template: dict[str, Any],
    *,
    candidate_id: str,
    hardware_id: str,
    gpu_count: int,
    server_count: int,
    tp_size: int | None = None,
    dp_size: int | None = None,
    plan_id: str | None = None,
    region: str | None = None,
    as_of_date: Any = None,
) -> Any:
    """把模板 + 第二阶段候选事实合成一个 ``DeploymentCostPlan``。"""
    from llm_capacity_pricing.schemas.tco import (
        CostLineSelection,
        DeploymentCostPlan,
        FinancingAssumptions,
    )

    from llm_capacity_pricing.schemas.tco import ScopeExclusion

    raw_assumptions = {
        k: v for k, v in (template.get("assumptions") or {}).items() if v is not None
    }
    return DeploymentCostPlan(
        plan_id=plan_id or f"{template['template_id']}::{candidate_id}",
        candidate_id=candidate_id,
        hardware_id=hardware_id,
        gpu_count=gpu_count,
        server_count=server_count,
        tp_size=tp_size,
        dp_size=dp_size,
        cost_basis=template["cost_basis"],
        region=region or template.get("region"),
        as_of_date=as_of_date or template.get("as_of_date"),
        assumptions=FinancingAssumptions(**raw_assumptions),
        selections=[
            CostLineSelection(item_id=s) for s in (template.get("selections") or [])
        ],
        scope_exclusions=[
            ScopeExclusion.model_validate(e)
            for e in (template.get("scope_exclusions") or [])
        ],
        scope_notes=list(template.get("scope_notes") or []),
    )


def tco_for(
    db_path: str | Path,
    *,
    plan_file: str | Path,
    template_id: str,
    candidate_id: str,
    hardware_id: str,
    gpu_count: int,
    server_count: int,
    tp_size: int | None = None,
    dp_size: int | None = None,
    annual_revenue: Decimal | None = None,
    revenue_basis: str = "none",
    region: str | None = None,
    as_of_date: Any = None,
) -> dict[str, Any]:
    """M28：按模板 + 成本目录计算完整 TCO 与现金流。"""
    from llm_capacity_pricing.engines.tco import build_cash_flow, compute_tco

    templates = load_cost_plan_templates(plan_file)
    if template_id not in templates:
        raise ServiceError(
            f"未知模板 {template_id!r}；可用：{sorted(templates)}"
        )
    db, repo = open_db(db_path)
    try:
        with db.session() as session:
            items = repo.list_cost_catalog_items(session)
    finally:
        db.dispose()
    if not items:
        raise ServiceError("成本目录为空：请先运行 import-cost-catalog")

    plan = build_cost_plan_from_template(
        templates[template_id],
        candidate_id=candidate_id,
        hardware_id=hardware_id,
        gpu_count=gpu_count,
        server_count=server_count,
        tp_size=tp_size,
        dp_size=dp_size,
        region=region,
        as_of_date=as_of_date,
    )
    policy = load_coverage_policy(plan_file)
    tco = compute_tco(plan, items, coverage_policy=policy)
    cash_flow = build_cash_flow(
        tco, annual_revenue=annual_revenue, revenue_basis=revenue_basis
    )
    return {
        "plan": plan.model_dump(mode="json"),
        "tco": tco.model_dump(mode="json"),
        "cash_flow": cash_flow.model_dump(mode="json"),
        "assumptions_notice": PHASE4_SYNTHETIC_NOTICE,
    }


def capacity_evidence_for(
    db_path: str | Path,
    *,
    model_name: str,
    hardware_model: str,
    gpu_count: int | None,
    tp_size: int | None,
    dp_size: int | None,
    weight_precision: str | None,
    configured_concurrency: int | None,
    input_len_target: int | None,
    output_len_target: int | None,
    dataset_name: str | None = None,
    arrival_mode: str | None = None,
    model_revision: str | None = None,
    framework: str | None = None,
    framework_version: str | None = None,
    requirement: dict[str, Any] | None = None,
    policy: Any = None,
) -> Any:
    """从第三阶段证据池里挑一条**可信**容量证据。

    只有在匹配等级达到 ``exact_match`` 且可信等级属于测量类时才算 verified；
    否则返回一个 ``is_verified=False`` 的证据（用于在报告中说明"为什么不能判定"）。
    """
    from llm_capacity_pricing.perf.benchmark_matcher import signature_from_run
    from llm_capacity_pricing.perf.ranker import _best_match
    from llm_capacity_pricing.engines.revenue import CapacityEvidence
    from llm_capacity_pricing.schemas.performance import (
        TargetRequirement,
        WorkloadSignature,
    )

    db, repo = open_db(db_path)
    try:
        with db.session() as session:
            runs = repo.list_benchmark_runs(session)
    finally:
        db.dispose()
    if not runs:
        return None

    from llm_capacity_pricing.perf.capacity_analyzer import analyze_group, grouping_key

    target = TargetRequirement(
        model_name=model_name,
        model_revision=model_revision,
        weight_precision=weight_precision,
        hardware_model=hardware_model,
        gpu_count=gpu_count,
        tp_size=tp_size,
        dp_size=dp_size,
        framework=framework,
        framework_version=framework_version,
        workload=WorkloadSignature(
            input_len_target=input_len_target,
            output_len_target=output_len_target,
            dataset_name=dataset_name,
            arrival_mode=arrival_mode or "unknown",
            configured_concurrency=configured_concurrency,
        ),
        required_metrics=[],
        require_measured=True,
    )
    best_run, best_match = _best_match(target, runs)
    if best_run is None or best_match is None:
        return None
    verified = (
        best_match.level == "exact_match"
        and best_run.data_trust in PHASE4_TRUSTED_LEVELS
    )

    # ---- 可持续性证据（F4.6）：同组曲线里"该并发是否被认定为可持续容量" ----
    sustainability = False
    group = [r for r in runs if grouping_key(r) == grouping_key(best_run)]
    try:
        profile = analyze_group(group)
        sustainability = (
            profile.maximum_sustainable_capacity is not None
            and profile.maximum_sustainable_capacity == best_run.profile.configured_concurrency
        )
    except ValueError:
        sustainability = False

    # ---- SLA 证据（F4.6）：按需求给出的时延约束对这条 run 做判定 ----
    sla_status = None
    if requirement is not None:
        try:
            evaluation = evaluate_sla_for(
                db_path,
                requirement=requirement,
                run_id=best_run.run_id,
                policy=policy,
                model_revision=model_revision,
                framework=framework,
                framework_version=framework_version,
                weight_precision=weight_precision,
                hardware_model=hardware_model,
                gpu_count=gpu_count,
                tp_size=tp_size,
                dp_size=dp_size,
                input_len_target=input_len_target,
                output_len_target=output_len_target,
                dataset_name=dataset_name,
                arrival_mode=arrival_mode,
                configured_concurrency=configured_concurrency,
            )
            sla_status = evaluation["status"]
        except Exception:  # noqa: BLE001 - 证据不足时保持 None，不猜测
            sla_status = None

    return CapacityEvidence(
        run_id=best_run.run_id,
        is_verified=verified,
        configured_concurrency=best_run.profile.configured_concurrency,
        request_throughput_rps=best_run.profile.request_throughput_rps,
        output_throughput_tok_s=best_run.profile.output_throughput_tok_s,
        match_level=best_match.level,
        data_trust=best_run.data_trust,
        input_len_target=best_run.signature.workload.input_len_target,
        output_len_target=best_run.signature.workload.output_len_target,
        ttft_p99_ms=best_run.profile.ttft_p99_ms,
        tpot_p99_ms=best_run.profile.tpot_p99_ms,
        error_rate=best_run.profile.error_rate,
        sla_status=sla_status,
        sustainability_verified=sustainability,
    )


def revenue_for(
    db_path: str | Path,
    *,
    demand_file: str | Path,
    scenario_id: str,
    capacity: dict[str, Any] | None = None,
    requirement: dict[str, Any] | None = None,
    sla_limits: dict[str, Any] | None = None,
    policy: Any = None,
    as_of: Any = None,
) -> dict[str, Any]:
    """M29：按业务需求情景计算收入；``capacity`` 为候选事实时顺便做综合容量比对（F4.6）。"""
    from llm_capacity_pricing.engines.revenue import SlaLimits, compute_revenue
    from llm_capacity_pricing.schemas.commercial import DemandPeriod, RevenueScenario

    scenarios = load_demand_scenarios(demand_file)
    if scenario_id not in scenarios:
        raise ServiceError(f"未知情景 {scenario_id!r}；可用：{sorted(scenarios)}")
    raw = scenarios[scenario_id]
    scenario = RevenueScenario(
        scenario_id=raw["scenario_id"],
        model_name=raw["model_name"],
        billing_rule_id=raw["billing_rule_id"],
        days_per_year=raw.get("days_per_year", 365),
        periods_cover=raw.get("periods_cover", "one_day"),
        scope_notes=list(raw.get("scope_notes") or []),
        periods=[DemandPeriod.model_validate(p) for p in raw["periods"]],
    )

    db, repo = open_db(db_path)
    try:
        with db.session() as session:
            rules = {r.rule_id: r for r in repo.list_billing_rules(session)}
    finally:
        db.dispose()
    rule = rules.get(scenario.billing_rule_id)
    if rule is None:
        raise ServiceError(
            f"计费规则 {scenario.billing_rule_id!r} 不在库中；可用：{sorted(rules)}"
        )

    evidence = None
    if capacity is not None:
        evidence = capacity_evidence_for(
            db_path, requirement=requirement, policy=policy, **capacity
        )
    limits = None
    if sla_limits:
        limits = SlaLimits(
            ttft_p99_ms_max=(
                Decimal(str(sla_limits["ttft_p99_ms_max"]))
                if sla_limits.get("ttft_p99_ms_max") is not None
                else None
            ),
            tpot_p99_ms_max=(
                Decimal(str(sla_limits["tpot_p99_ms_max"]))
                if sla_limits.get("tpot_p99_ms_max") is not None
                else None
            ),
        )
    result = compute_revenue(
        scenario, rule, capacity_evidence=evidence, sla_limits=limits, as_of=as_of
    )
    return {
        "scenario": scenario.model_dump(mode="json"),
        "billing_rule": rule.model_dump(mode="json"),
        "revenue": result.model_dump(mode="json"),
        "capacity_evidence": (
            {
                "run_id": evidence.run_id,
                "is_verified": evidence.is_verified,
                "match_level": evidence.match_level,
                "data_trust": evidence.data_trust,
            }
            if evidence
            else None
        ),
        "assumptions_notice": PHASE4_SYNTHETIC_NOTICE,
    }


def _diagnostic_eligibility(
    *,
    candidate_id: str,
    tco: Any,
    sla_status: str | None,
    match_level: str | None,
    plan_id: str | None = None,
) -> Any:
    """诊断入口：只反映"若不核验证据会得到什么"，结论恒为不可发布（V4.1 F4.1）。"""
    from llm_capacity_pricing.engines.quote_gate import quote_eligibility_from_claims

    result = quote_eligibility_from_claims(
        candidate_id=candidate_id,
        tco=tco,
        sla_status=sla_status,
        match_level=match_level,
    )
    if plan_id is not None:
        result = result.model_copy(update={"plan_id": plan_id})
    return result


def _canonical_run_snapshot(run: Any) -> dict[str, Any]:
    """一条实测的**规范化证据快照**（生成与复核共用同一个函数）。

    覆盖全部可能参与 SLA 判断的字段：签名身份、负载、指标口径声明、
    所有标准化指标数值、样本量与时长、失败计数、质量标记与来源哈希。
    只排除人工批注（``notes``）与逐请求明细（改为指纹），避免它们的变化
    误伤证据复现性。
    """
    import hashlib

    sig = run.signature
    pf = run.profile
    request_digest = hashlib.sha256(
        json.dumps(
            [m.model_dump(mode="json") for m in run.request_metrics],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return {
        "run_id": run.run_id,
        "raw_sha256": run.raw_sha256,
        "raw_format": run.raw_format,
        "collected_at": run.collected_at.isoformat() if run.collected_at else None,
        "data_trust": run.data_trust,
        "load_profile_kind": run.load_profile_kind,
        "quality_flags": sorted(run.quality_flags),
        "request_metrics_digest": request_digest,
        "signature": {
            "model_name": sig.model_name,
            "model_revision": sig.model_revision,
            "weight_precision": sig.weight_precision,
            "quantization": sig.quantization,
            "hardware_model": sig.hardware_model,
            "hardware_sku": sig.hardware_sku,
            "gpu_count": sig.gpu_count,
            "node_topology": sig.node_topology,
            "tp_size": sig.tp_size,
            "dp_size": sig.dp_size,
            "pp_size": sig.pp_size,
            "ep_size": sig.ep_size,
            "deployment_mode": sig.deployment_mode,
            "framework": sig.framework,
            "framework_version": sig.framework_version,
            "backend": sig.backend,
            "workload": sig.workload.model_dump(mode="json"),
            "metric_definition_ids": sorted(sig.metric_definition_ids),
            "metric_side": sig.metric_side,
            "statistics_window": sig.statistics_window,
            "sample_size": sig.sample_size,
            "data_trust": sig.data_trust,
        },
        "profile": pf.model_dump(mode="json"),
    }


def _evidence_pool_hash(runs: Sequence[Any]) -> str:
    """证据池指纹（V4.3 P1-1）：对**规范化快照**做确定性序列化后取 SHA256。

    生成与复核共用本函数与 ``_canonical_run_snapshot``；
    因此任何参与 SLA 判断的字段变化（含 ITL P99、指标口径声明等）都会改变指纹。
    """
    import hashlib

    payload = [_canonical_run_snapshot(r) for r in sorted(runs, key=lambda r: r.run_id)]
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _input_snapshot_hash(
    *,
    target_identity: dict[str, Any],
    requirement: dict[str, Any],
    match_level: str | None,
    supporting_run_ids: Sequence[str],
    policy_snapshot: dict[str, Any],
    commercial: dict[str, Any] | None = None,
) -> str:
    """规范化输入指纹（V4.2 E3）。

    覆盖：目标部署身份、SLA Requirement、匹配证明等级、参与 Run、
    评估策略快照、商务方案。任何一项变化都会让旧证据失效。
    """
    import hashlib
    import json

    payload = {
        "target_identity": target_identity,
        "requirement": requirement,
        "match_level": match_level,
        "supporting_run_ids": sorted(supporting_run_ids),
        "policy": policy_snapshot,
        "commercial": commercial or {},
    }
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _target_from_facts(facts: dict[str, Any]) -> Any:
    from llm_capacity_pricing.schemas.performance import (
        TargetRequirement,
        WorkloadSignature,
    )

    return TargetRequirement(
        model_name=facts.get("model_name"),
        model_revision=facts.get("model_revision"),
        weight_precision=facts.get("weight_precision"),
        hardware_model=facts.get("hardware_model"),
        gpu_count=facts.get("gpu_count"),
        tp_size=facts.get("tp_size"),
        dp_size=facts.get("dp_size"),
        framework=facts.get("framework"),
        framework_version=facts.get("framework_version"),
        workload=WorkloadSignature(
            input_len_target=facts.get("input_len_target"),
            output_len_target=facts.get("output_len_target"),
            dataset_name=facts.get("dataset_name"),
            arrival_mode=facts.get("arrival_mode") or "unknown",
            configured_concurrency=facts.get("configured_concurrency"),
        ),
        required_metrics=[],
        require_measured=not facts.get("allow_synthetic", False),
        allow_synthetic=bool(facts.get("allow_synthetic", False)),
    )


def _identity_from_facts(facts: dict[str, Any]) -> dict[str, Any]:
    """目标身份的规范化快照（用于比对"证据是否仍对应当前目标"）。"""
    keys = (
        "model_name",
        "model_revision",
        "weight_precision",
        "hardware_model",
        "gpu_count",
        "tp_size",
        "dp_size",
        "framework",
        "framework_version",
        "input_len_target",
        "output_len_target",
        "dataset_name",
        "arrival_mode",
        "configured_concurrency",
    )
    return {k: facts.get(k) for k in keys}


def _policy_snapshot(policy: Any) -> dict[str, Any]:
    return {
        "policy_id": getattr(policy, "policy_id", None),
        "allow_synthetic": bool(getattr(policy, "allow_synthetic", False)),
        "accepted_trust_levels": list(getattr(policy, "accepted_trust_levels", ()) or ()),
        "minimum_match_level": getattr(policy, "minimum_match_level", None),
        "allow_estimate_as_observed": bool(
            getattr(policy, "allow_estimate_as_observed", False)
        ),
    }


def record_sla_evidence_for(
    db_path: str | Path,
    *,
    candidate_id: str,
    requirement: dict[str, Any],
    facts: dict[str, Any],
    sla_evaluation_id: str | None = None,
    policy: Any = None,
    target_ref: str | None = None,
) -> dict[str, Any]:
    """V4.1 F4.1 / V4.2 E1-E3：跑一次 SLA 评估并**持久化**为可引用的证据记录。

    记录里保存：代表运行 + **全部参与运行 ID**、可信等级与匹配证明质量标记、
    策略快照、run 池指纹、规范化输入指纹。报价门禁据此重放校验。
    """
    from datetime import datetime, timezone

    from llm_capacity_pricing.schemas.commercial import SlaEvidenceRecord
    from llm_capacity_pricing.schemas.performance import SlaRequirement

    db, repo = open_db(db_path)
    try:
        with db.session() as session:
            runs = repo.list_benchmark_runs(session)
    finally:
        db.dispose()
    if not runs:
        raise ServiceError("性能证据池为空：无法生成 SLA 证据记录")

    target = _target_from_facts(facts)
    best_run, best_match = _best_match(target, runs)
    sla_req = SlaRequirement.model_validate(requirement)
    policy = policy or SlaPolicy()
    evaluation = _evaluate_sla(
        sla_req,
        policy=policy,
        run=best_run,
        match_result=best_match,
        target_ref=target_ref or candidate_id,
    )

    match_level = best_match.level if best_match else None
    supporting = (
        list(best_match.supporting_run_ids)
        if best_match and best_match.supporting_run_ids
        else ([best_run.run_id] if best_run else [])
    )
    identity = _identity_from_facts(facts)
    pol_snapshot = _policy_snapshot(policy)
    evidence_id = sla_evaluation_id or f"sla::{candidate_id}::{sla_req.requirement_id}"

    record = SlaEvidenceRecord(
        sla_evaluation_id=evidence_id,
        candidate_id=candidate_id,
        requirement_id=sla_req.requirement_id,
        requirement=sla_req.model_dump(mode="json"),
        target_identity=identity,
        status=evaluation.status,
        match_level=match_level,
        matched_run_id=best_run.run_id if best_run else None,
        supporting_run_ids=supporting,
        data_trust=best_run.data_trust if best_run else None,
        algorithm_validation_only=bool(
            best_match.algorithm_validation_only if best_match else False
        ),
        allow_synthetic_used=bool(getattr(policy, "allow_synthetic", False)),
        sla_proof_blocked=bool(
            best_match and "sla_proof" in (best_match.blocked_from or [])
        ),
        policy_snapshot=pol_snapshot,
        policy_id=policy.policy_id,
        evaluated_at=datetime.now(timezone.utc),
        run_pool_hash=_evidence_pool_hash(runs),
        input_snapshot_hash=_input_snapshot_hash(
            target_identity=identity,
            requirement=sla_req.model_dump(mode="json"),
            match_level=match_level,
            supporting_run_ids=supporting,
            policy_snapshot=pol_snapshot,
        ),
    )
    db, repo = open_db(db_path)
    try:
        with db.session() as session:
            repo.upsert_sla_evidence(session, [record])
            session.commit()
    finally:
        db.dispose()
    return {
        "sla_evaluation_id": evidence_id,
        "status": record.status,
        "match_level": record.match_level,
        "matched_run_id": record.matched_run_id,
        "supporting_run_ids": record.supporting_run_ids,
        "data_trust": record.data_trust,
        "algorithm_validation_only": record.algorithm_validation_only,
        "allow_synthetic_used": record.allow_synthetic_used,
        "run_pool_hash": record.run_pool_hash,
        "input_snapshot_hash": record.input_snapshot_hash,
        "production_grade": (
            record.status == "observed_pass"
            and record.match_level == "exact_match"
            and record.data_trust in PHASE4_TRUSTED_LEVELS
            and not record.algorithm_validation_only
            and not record.allow_synthetic_used
            and not record.sla_proof_blocked
        ),
        "note": (
            "已持久化。报价门禁会重放该证据；若原始数据、目标身份、需求或策略变化，"
            "记录会被判为 stale。algorithm_validation_only / allow_synthetic 的证据"
            "永远不能用于正式报价。"
        ),
    }


def import_quote_approvals(
    file: str | Path, db_path: str | Path = "data/local.db"
) -> dict[str, Any]:
    """V4.1 F4.1：导入**人工**审批记录（本函数只做持久化，不生成审批）。"""
    from llm_capacity_pricing.schemas.commercial import QuoteApproval

    raw = _load_yaml_file(file)
    entries = raw.get("approvals") or []
    if not entries:
        raise ServiceError(f"{file} 中没有任何 approvals")
    approvals = [QuoteApproval.model_validate(e) for e in entries]
    db, repo = open_db(db_path)
    try:
        with db.session() as session:
            n = repo.upsert_quote_approvals(session, approvals)
            session.commit()
    finally:
        db.dispose()
    return {
        "file": str(file),
        "approval_count": n,
        "decisions": {a.approval_id: a.decision for a in approvals},
        "notice": "审批记录由人工提供；Agent 无任何接口可生成审批。",
    }


def _recheck_sla_evidence(
    db_path: str | Path,
    *,
    record: Any,
    facts: dict[str, Any],
    policy: Any = None,
) -> Any:
    """V4.1 F4.1 / V4.2 E2-E3：重放 SLA 证据。

    修正要点：

    * **不用** ``record.matched_run_id`` 去原始 Run 表查（聚合证据的 ID 是
      ``aggregate:A,B`` 这种运行时标识，表里根本没有这一行）。
      改为：用记录里的**目标身份快照**重新匹配整个证据池，
      再比较重放得到的代表运行与记录是否一致；
    * 目标身份、run 池指纹都要与记录比对，不一致即旧证据失效；
    * 目标身份取自**记录**，调用方传的 facts 只用于比对（不参与计算）。
    """
    from llm_capacity_pricing.engines.quote_gate import SlaRecheck
    from llm_capacity_pricing.schemas.performance import SlaRequirement

    db, repo = open_db(db_path)
    try:
        with db.session() as session:
            runs = repo.list_benchmark_runs(session)
    finally:
        db.dispose()

    # ---- E3：目标身份必须与记录一致 ----
    caller_identity = _identity_from_facts(facts)
    target_identity_ok = caller_identity == dict(record.target_identity or {})

    # ---- E3：证据池指纹必须与记录一致 ----
    run_pool_hash_ok = _evidence_pool_hash(runs) == record.run_pool_hash

    # ---- V4.3 P1-1：规范化输入指纹必须自洽（防篡改）----
    recomputed_input = _input_snapshot_hash(
        target_identity=dict(record.target_identity or {}),
        requirement=dict(record.requirement or {}),
        match_level=record.match_level,
        supporting_run_ids=record.supporting_run_ids,
        policy_snapshot=dict(record.policy_snapshot or {}),
    )
    input_snapshot_ok = recomputed_input == record.input_snapshot_hash

    def _fail(reasons: list[str], **kw: Any) -> Any:
        return SlaRecheck(
            ok=False,
            target_identity_ok=target_identity_ok,
            run_pool_hash_ok=run_pool_hash_ok,
            reasons=reasons,
            **kw,
        )

    supporting = list(record.supporting_run_ids or [])
    if not supporting and record.matched_run_id:
        supporting = [record.matched_run_id]
    if not input_snapshot_ok:
        return _fail(
            [
                "evidence_snapshot_tampered: 记录内容与其输入快照指纹不一致"
                "（记录被改动过，证据不可采信）"
            ]
        )
    if not supporting:
        return _fail(["evidence_has_no_run: 证据未记录任何参与实测"])

    missing = [rid for rid in supporting if not any(r.run_id == rid for r in runs)]
    if missing:
        return _fail(
            [f"supporting_run_not_found: 参与实测已不存在 {missing}"]
        )

    # ---- E2：用记录里的目标身份重放匹配（含聚合）----
    target = _target_from_facts(dict(record.target_identity or {}))
    best_run, best_match = _best_match(target, runs)
    if best_match is None or best_run is None:
        return _fail(["no_comparable_run: 当前证据池中无可用实测"])
    if best_run.run_id != record.matched_run_id:
        return _fail(
            [
                "evidence_run_superseded: 重放得到的代表运行 "
                f"{best_run.run_id!r} 与记录 {record.matched_run_id!r} 不一致"
            ],
            match_level=best_match.level,
        )

    try:
        sla_req = SlaRequirement.model_validate(record.requirement)
    except Exception as exc:  # noqa: BLE001
        return _fail([f"requirement_snapshot_invalid: {exc}"])

    # 重放时也必须用同一套策略口径；策略快照不一致说明口径被换过
    policy = policy or SlaPolicy()
    current_policy = _policy_snapshot(policy)
    if record.policy_snapshot and current_policy != dict(record.policy_snapshot):
        return _fail(
            [
                "policy_changed: 当前策略与证据记录不一致 "
                f"（记录 {record.policy_snapshot} vs 当前 {current_policy}）"
            ]
        )

    evaluation = _evaluate_sla(
        sla_req,
        policy=policy,
        run=best_run,
        match_result=best_match,
        target_ref=record.candidate_id,
    )
    return SlaRecheck(
        ok=True,
        match_level=best_match.level,
        status=evaluation.status,
        run_id=best_run.run_id,
        data_trust=best_run.data_trust,
        algorithm_validation_only=bool(best_match.algorithm_validation_only),
        target_identity_ok=target_identity_ok,
        run_pool_hash_ok=run_pool_hash_ok,
        reasons=[
            f"重放：match_level={best_match.level} status={evaluation.status} "
            f"run={best_run.run_id} trust={best_run.data_trust}"
        ],
    )


def quote_eligibility_for(
    db_path: str | Path,
    *,
    candidate_id: str,
    requirement_id: str,
    facts: dict[str, Any],
    sla_evaluation_id: str | None = None,
    approval_id: str | None = None,
    confirmation_id: str | None = None,
    plan_file: str | Path | None = None,
    template_id: str | None = None,
    hardware_id: str | None = None,
    gpu_count: int | None = None,
    server_count: int | None = None,
    scenario_id: str | None = None,
    demand_file: str | Path | None = None,
    billing_rule_id: str | None = None,
    region: str | None = None,
    as_of: Any = None,
    policy: Any = None,
    quote_model_name: str | None = None,
) -> dict[str, Any]:
    """M30 / V4.1 F4.1：四维报价门禁。

    **只接受引用**（candidate_id / requirement_id / sla_evaluation_id / approval_id），
    服务端从持久化记录读取结论并重新核验；没有任何参数能让调用方"声明通过"。
    """
    from llm_capacity_pricing.engines.quote_gate import (
        billing_rule_snapshot_hash,
        evaluate_quote_eligibility,
    )
    from llm_capacity_pricing.engines.tco import compute_tco, tco_snapshot_hash

    if sla_evaluation_id is None:
        raise ServiceError(
            "必须提供 --sla-evaluation-id（持久化的 SLA 证据记录）；"
            "调用方声明的 sla_status 字符串不被接受"
        )
    if approval_id is None:
        raise ServiceError(
            "必须提供 --approval-id（人工审批记录）；非空字符串不构成授权证明"
        )

    tco = None
    plan_id = None
    snapshot = None
    if plan_file and template_id and hardware_id and gpu_count and server_count:
        templates = load_cost_plan_templates(plan_file)
        if template_id not in templates:
            raise ServiceError(f"未知模板 {template_id!r}；可用：{sorted(templates)}")
        db, repo = open_db(db_path)
        try:
            with db.session() as session:
                items = repo.list_cost_catalog_items(session)
        finally:
            db.dispose()
        if not items:
            raise ServiceError("成本目录为空：请先运行 import-cost-catalog")
        plan = build_cost_plan_from_template(
            templates[template_id],
            candidate_id=candidate_id,
            hardware_id=hardware_id,
            gpu_count=gpu_count,
            server_count=server_count,
            region=region,
            as_of_date=as_of,
        )
        tco = compute_tco(plan, items, coverage_policy=load_coverage_policy(plan_file))
        plan_id = plan.plan_id
        snapshot = tco_snapshot_hash(tco)

    # ---- V4.3 P0-1：计费规则必须真实解析出来，参与门禁与审批作用域比对 ----
    resolved_rule_id = billing_rule_id
    if demand_file and scenario_id:
        raw_scenario = load_demand_scenarios(demand_file).get(scenario_id)
        if raw_scenario is None:
            raise ServiceError(f"未知情景 {scenario_id!r}（{demand_file}）")
        from_scenario = raw_scenario.get("billing_rule_id")
        if resolved_rule_id and from_scenario and resolved_rule_id != from_scenario:
            raise ServiceError(
                f"计费规则冲突：情景 {scenario_id!r} 使用 {from_scenario!r}，"
                f"但显式传入 {resolved_rule_id!r}"
            )
        resolved_rule_id = resolved_rule_id or from_scenario

    db, repo = open_db(db_path)
    try:
        with db.session() as session:
            evidence = repo.get_sla_evidence(session, sla_evaluation_id)
            approval = repo.get_quote_approval(session, approval_id)
            confirmation = (
                repo.get_commercial_confirmation(session, confirmation_id)
                if confirmation_id
                else None
            )
            billing_rule = (
                repo.get_billing_rule(session, resolved_rule_id)
                if resolved_rule_id
                else None
            )
            rules = {r.rule_id: r for r in repo.list_billing_rules(session)}
    finally:
        db.dispose()
    if resolved_rule_id and billing_rule is None:
        raise ServiceError(
            f"计费规则不存在: {resolved_rule_id}（请先运行 import-cost-catalog 导入）"
        )

    if evidence is None:
        raise ServiceError(
            f"SLA 证据记录不存在: {sla_evaluation_id}（请先用 record-sla-evidence 生成）"
        )
    if approval is None:
        raise ServiceError(f"审批记录不存在: {approval_id}（请先用 import-quote-approvals 导入）")

    recheck = _recheck_sla_evidence(
        db_path, record=evidence, facts=facts, policy=policy
    )
    eligibility = evaluate_quote_eligibility(
        candidate_id=candidate_id,
        requirement_id=requirement_id,
        tco=tco,
        sla_evidence=evidence,
        sla_recheck=recheck,
        approval=approval,
        confirmation=confirmation,
        billing_rule=billing_rule,
        cost_snapshot_hash=snapshot,
        # 计费规则/商务确认用的是**商业模型名**（如 qwen3-32b），
        # 而 SLA 目标身份用的是签名里的模型名（如 Qwen/Qwen3-32B）——
        # 两者是不同命名空间，调用方可显式指定前者。
        quote_model_name=quote_model_name or facts.get("model_name"),
        plan_id=plan_id,
        scenario_id=scenario_id,
        as_of=as_of,
    )
    return {
        "eligibility": eligibility.model_dump(mode="json"),
        "tco_used": tco.model_dump(mode="json") if tco else None,
        "cost_snapshot_hash": snapshot,
        "sla_evidence_used": evidence.model_dump(mode="json"),
        "sla_recheck": {
            "ok": recheck.ok,
            "match_level": recheck.match_level,
            "status": recheck.status,
            "run_id": recheck.run_id,
            "reasons": recheck.reasons,
        },
        "approval_used": approval.model_dump(mode="json"),
        "confirmation_used": confirmation.model_dump(mode="json") if confirmation else None,
        "billing_rule_used": billing_rule.model_dump(mode="json") if billing_rule else None,
        "billing_rule_hash": billing_rule_snapshot_hash(billing_rule),
        "billing_rules_available": sorted(rules),
        "assumptions_notice": PHASE4_SYNTHETIC_NOTICE,
    }


def phase4_decision_for(
    db_path: str | Path,
    *,
    requirement: dict[str, Any],
    model_id: str,
    hardware_id: str,
    plan_file: str | Path,
    template_id: str,
    assumptions: Any,
    server_id: str | None = None,
    policy: Any = None,
    input_len_target: int | None = None,
    output_len_target: int | None = None,
    dataset_name: str | None = None,
    model_revision: str | None = None,
    framework: str | None = None,
    framework_version: str | None = None,
    weight_precision: Any = None,
    kv_precision: str = "bf16",
    demand_file: str | Path | None = None,
    scenario_id: str | None = None,
    budgets: Sequence[dict[str, Any]] = (),
) -> dict[str, Any]:
    """**兼容入口**：跨阶段编排已迁至应用层 ``workflow.evaluate_decision``。

    保留本函数是为了不破坏既有调用方与测试；新代码请直接用 ``workflow``。
    """
    from llm_capacity_pricing.workflow import evaluate_decision

    return evaluate_decision(
        db_path,
        requirement=requirement,
        model_id=model_id,
        hardware_id=hardware_id,
        plan_file=plan_file,
        template_id=template_id,
        assumptions=assumptions,
        server_id=server_id,
        policy=policy,
        input_len_target=input_len_target,
        output_len_target=output_len_target,
        dataset_name=dataset_name,
        model_revision=model_revision,
        framework=framework,
        framework_version=framework_version,
        weight_precision=weight_precision,
        kv_precision=kv_precision,
        demand_file=demand_file,
        scenario_id=scenario_id,
        budgets=budgets,
    )


def export_phase4_report(
    db_path: str | Path,
    out_path: str | Path,
    *,
    decision: dict[str, Any] | None = None,
    tco_details: Sequence[dict[str, Any]] = (),
    revenue: dict[str, Any] | None = None,
    eligibilities: Sequence[dict[str, Any]] = (),
    request: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """M33：生成第四阶段 Markdown 报告（含报价草稿或阻断说明）。"""
    from llm_capacity_pricing.reporting.tco_report import build_tco_report

    catalog = cost_catalog_summary(db_path)
    comparison = decision.get("comparison") if decision else None
    if decision and decision.get("eligibilities"):
        eligibilities = list(decision["eligibilities"])
    if decision:
        # 决策载荷里已经带上 TCO 明细与收益情景，报告直接用它们渲染第 3/4 节
        if not tco_details:
            tco_details = list(decision.get("tco_details") or [])
        if revenue is None:
            revenue = decision.get("revenue")
    md = build_tco_report(
        catalog=catalog,
        comparison=comparison,
        tco_details=tco_details,
        revenue=revenue,
        eligibilities=eligibilities,
        budget_checks=decision.get("budget_checks", []) if decision else (),
        sensitivities=decision.get("sensitivity", []) if decision else (),
        breakevens=decision.get("breakeven", []) if decision else (),
        cash_flows=[d.get("cash_flow", {}) for d in tco_details if d.get("cash_flow")],
        decision=decision,
        request=request,
        synthetic_notice=PHASE4_SYNTHETIC_NOTICE,
    )
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(md, encoding="utf-8")
    return {
        "report": str(path),
        "bytes": len(md.encode("utf-8")),
        "quote_publishable": any(
            e.get("quote_publishable") for e in (eligibilities or [])
        ),
        "synthetic_disclaimer": PHASE4_SYNTHETIC_NOTICE,
    }
