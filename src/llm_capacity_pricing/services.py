"""服务层：CLI 与测试共用的编排逻辑。

把"导入 -> 校验 -> 计算 -> 持久化 -> 报告"串起来，CLI 只做参数解析。
"""

from __future__ import annotations

import json
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
