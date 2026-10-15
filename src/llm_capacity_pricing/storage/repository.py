"""仓储层：模型对象 <-> SQLite 行，按主键幂等 upsert。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable, Sequence

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from llm_capacity_pricing.schemas.benchmark import BenchmarkRecord
from llm_capacity_pricing.schemas.costs import HardwarePrice, TokenPrice
from llm_capacity_pricing.schemas.evidence import Evidence
from llm_capacity_pricing.schemas.official import (
    OfficialBenchmarkCandidate,
    SglangNumericCandidate,
)
from llm_capacity_pricing.schemas.pricing import PricingResult
from llm_capacity_pricing.storage.database import (
    BenchmarkRecordRow,
    Database,
    EvidenceRow,
    HardwarePriceRow,
    ImportRunRow,
    OfficialCandidateRow,
    PricingResultRow,
    TokenPriceRow,
)


def _d(value: Decimal | None) -> str | None:
    """Decimal -> 精确十进制字面量（Text 列）。"""
    return None if value is None else str(value)


#: 已知的 Benchmark 工具名（按权威命令里的模块名匹配）
_BENCHMARK_TOOL_MARKERS: tuple[tuple[str, str], ...] = (
    ("sglang.bench_serving", "sglang.bench_serving"),
    ("sglang.bench_one_batch", "sglang.bench_one_batch"),
    ("vllm bench serve", "vllm.bench_serve"),
    ("vllm.benchmarks", "vllm.bench_serve"),
)


def _benchmark_tool_of(command: str | None) -> str | None:
    """从测试命令推导工具名；无法识别时返回 None（不猜）。"""
    if not command:
        return None
    for marker, tool in _BENCHMARK_TOOL_MARKERS:
        if marker in command:
            return tool
    return None


def _dump(model) -> str:
    """Pydantic model -> JSON（Decimal 以字符串保留精度）。"""
    return json.dumps(model.model_dump(mode="json"), ensure_ascii=False)


@dataclass
class RepositoryCounts:
    evidence: int
    hardware_price: int
    token_price: int
    benchmark_record: int
    pricing_result: int

    def as_dict(self) -> dict[str, int]:
        return {
            "evidence": self.evidence,
            "hardware_price": self.hardware_price,
            "token_price": self.token_price,
            "benchmark_record": self.benchmark_record,
            "pricing_result": self.pricing_result,
        }


class Repository:
    def __init__(self, db: Database):
        self.db = db

    # -- 写入 -------------------------------------------------------------
    def upsert_evidence(self, session: Session, items: Iterable[Evidence]) -> int:
        n = 0
        for ev in items:
            session.merge(
                EvidenceRow(
                    evidence_id=ev.evidence_id,
                    source_type=ev.source_type,
                    publisher=ev.publisher,
                    source_url=ev.source_url,
                    source_file=ev.source_file,
                    source_sheet=ev.source_sheet,
                    source_row=ev.source_row,
                    source_version=ev.source_version,
                    retrieved_at=ev.retrieved_at,
                    raw_sha256=ev.raw_sha256,
                    notes=ev.notes,
                    quality_flags_json=json.dumps(ev.quality_flags, ensure_ascii=False),
                    measurement_origin=ev.measurement_origin,
                    is_internal_measurement=ev.is_internal_measurement,
                    payload_json=_dump(ev),
                )
            )
            n += 1
        return n

    def upsert_hardware_prices(self, session: Session, items: Iterable[HardwarePrice]) -> int:
        n = 0
        for hp in items:
            session.merge(
                HardwarePriceRow(
                    hardware_price_id=hp.hardware_price_id,
                    gpu_model=hp.gpu_model,
                    gpus_per_server=hp.gpus_per_server,
                    per_gpu_annual_cost_10k=_d(hp.per_gpu_annual_cost_10k),
                    currency=hp.currency,
                    currency_assumption=hp.currency_assumption,
                    cost_scope=hp.cost_scope,
                    evidence_id=hp.evidence_id,
                    payload_json=_dump(hp),
                )
            )
            n += 1
        return n

    def upsert_token_prices(self, session: Session, items: Iterable[TokenPrice]) -> int:
        n = 0
        for tp in items:
            session.merge(
                TokenPriceRow(
                    token_price_id=tp.token_price_id,
                    model_name=tp.model_name,
                    price_cached_input_per_1m=_d(tp.price_cached_input_per_1m),
                    price_uncached_input_per_1m=_d(tp.price_uncached_input_per_1m),
                    price_output_per_1m=_d(tp.price_output_per_1m),
                    currency=tp.currency,
                    currency_assumption=tp.currency_assumption,
                    evidence_id=tp.evidence_id,
                    payload_json=_dump(tp),
                )
            )
            n += 1
        return n

    def upsert_benchmarks(self, session: Session, items: Iterable[BenchmarkRecord]) -> int:
        n = 0
        for rec in items:
            session.merge(
                BenchmarkRecordRow(
                    record_id=rec.record_id,
                    model_name=rec.model_name,
                    hardware_model=rec.hardware_model,
                    deployment_gpu_count=rec.deployment_gpu_count,
                    performance_source_kind=rec.performance_source_kind,
                    verification_status=rec.verification_status,
                    evidence_id=rec.evidence_id,
                    configured_concurrency=rec.configured_concurrency,
                    input_tps_per_gpu=_d(rec.input_tps_per_gpu),
                    output_tps_per_gpu=_d(rec.output_tps_per_gpu),
                    cache_hit_rate_raw=_d(rec.cache_hit_rate_raw),
                    cache_metric_semantics=rec.cache_metric_semantics,
                    legacy_proxy_for_billable_hit_rate=rec.legacy_proxy_for_billable_hit_rate,
                    legacy_input_revenue_10k_per_gpu_year=_d(
                        rec.legacy_input_revenue_10k_per_gpu_year
                    ),
                    legacy_output_revenue_10k_per_gpu_year=_d(
                        rec.legacy_output_revenue_10k_per_gpu_year
                    ),
                    legacy_total_revenue_10k_per_gpu_year=_d(
                        rec.legacy_total_revenue_10k_per_gpu_year
                    ),
                    legacy_cost_10k_per_gpu_year=_d(rec.legacy_cost_10k_per_gpu_year),
                    legacy_revenue_cost_ratio=_d(rec.legacy_revenue_cost_ratio),
                    payload_json=_dump(rec),
                )
            )
            n += 1
        return n

    def upsert_pricing_results(self, session: Session, items: Iterable[PricingResult]) -> int:
        n = 0
        for res in items:
            session.merge(
                PricingResultRow(
                    scenario_id=res.scenario_id,
                    benchmark_record_id=res.benchmark_record_id,
                    mode=res.mode,
                    status=res.status,
                    scope=res.scope,
                    result_type=res.result_type,
                    sla_status=res.sla_status,
                    performance_record_id=res.performance_record_id,
                    token_price_id=res.token_price_id,
                    hardware_price_id=res.hardware_price_id,
                    evidence_ids_json=json.dumps(res.evidence_ids, ensure_ascii=False),
                    effective_monetization_ratio=_d(res.effective_monetization_ratio),
                    input_revenue_10k_per_gpu_year=_d(res.input_revenue_10k_per_gpu_year),
                    output_revenue_10k_per_gpu_year=_d(res.output_revenue_10k_per_gpu_year),
                    total_revenue_10k_per_gpu_year=_d(res.total_revenue_10k_per_gpu_year),
                    gpu_annual_cost_10k=_d(res.gpu_annual_cost_10k),
                    revenue_cost_ratio=_d(res.revenue_cost_ratio),
                    breakeven_monetization_ratio=_d(res.breakeven_monetization_ratio),
                    total_cluster_cost_10k_year=_d(res.total_cluster_cost_10k_year),
                    total_cluster_revenue_10k_year=_d(res.total_cluster_revenue_10k_year),
                    input_snapshot_hash=res.input_snapshot_hash,
                    cost_scope=res.cost_scope,
                    payload_json=_dump(res),
                )
            )
            n += 1
        return n

    def upsert_official_candidates(
        self, session: Session, items: Iterable[OfficialBenchmarkCandidate]
    ) -> int:
        n = 0
        for c in items:
            session.merge(
                OfficialCandidateRow(
                    candidate_id=c.candidate_id,
                    candidate_family="manifest_partial_evidence",
                    record_id=None,
                    manifest_id=c.manifest_id,
                    evidence_id=c.evidence_id,
                    publisher=c.publisher,
                    source_url=c.source_url,
                    source_path=None,
                    source_git_blob_sha=None,
                    framework=c.framework,
                    benchmark_tool=c.benchmark_tool,
                    verification_status=c.verification_status,
                    quality_status=c.verification_status,
                    hardware_binding=c.hardware_binding,
                    hardware_model=c.hardware_model,
                    deployment_gpu_count=c.deployment_gpu_count,
                    is_throughput_benchmark=c.is_throughput_benchmark,
                    evidence_file=c.evidence_file,
                    evidence_sha256=c.evidence_sha256,
                    can_reference_performance=c.can_reference_performance,
                    can_evaluate_sla=c.can_evaluate_sla,
                    can_calculate_pricing=c.can_calculate_pricing,
                    missing_fields_json=json.dumps(c.missing_fields, ensure_ascii=False),
                    metrics_json=json.dumps(
                        {k: str(v) for k, v in c.metrics.items()}, ensure_ascii=False
                    ),
                    payload_json=_dump(c),
                )
            )
            n += 1
        return n

    def upsert_numeric_candidates(
        self, session: Session, items: Iterable[SglangNumericCandidate]
    ) -> int:
        """写入 SGLang Cookbook **数值型**候选（同表，家族不同）。

        幂等依据 ``candidate_id``（= ``official_<record_id>``）：同一记录重复导入走
        ``merge``，不产生重复行；不同批次若内容变化，``payload_json`` 会整体覆盖但
        ``candidate_id`` 不变，历史版本的差异由证据文件的 sha256 与
        ``verification`` 记录留痕。
        """
        n = 0
        for c in items:
            session.merge(
                OfficialCandidateRow(
                    candidate_id=c.candidate_id,
                    candidate_family=c.family,
                    record_id=c.record_id,
                    manifest_id=c.source_id,
                    evidence_id=c.evidence_id,
                    publisher=c.publisher,
                    source_url=c.source_url,
                    source_path=c.source_path,
                    source_git_blob_sha=c.source_git_blob_sha,
                    framework=c.framework,
                    # 工具名从**权威命令**推导，而不是写死常量（F6）
                    benchmark_tool=_benchmark_tool_of(c.benchmark_command),
                    verification_status=c.quality_status,
                    quality_status=c.quality_status,
                    hardware_binding=c.hardware_binding,
                    hardware_model=c.hardware_model,
                    deployment_gpu_count=c.deployment_gpu_count,
                    # 必须来自 Schema 原始值，不得硬编码（F6）
                    is_throughput_benchmark=c.is_throughput_benchmark,
                    evidence_file=c.evidence_file,
                    evidence_sha256=c.evidence_sha256,
                    can_reference_performance=c.can_reference_performance,
                    can_evaluate_sla=c.can_evaluate_sla,
                    can_calculate_pricing=c.can_calculate_pricing,
                    missing_fields_json=json.dumps(c.missing_fields, ensure_ascii=False),
                    metrics_json=json.dumps(
                        {k: str(v) for k, v in c.metrics.items()}, ensure_ascii=False
                    ),
                    payload_json=_dump(c),
                )
            )
            n += 1
        return n

    def list_official_candidates(
        self, session: Session, *, family: str | None = "manifest_partial_evidence"
    ) -> list[OfficialBenchmarkCandidate]:
        """列出 manifest 家族的候选。

        ``family=None`` 表示不过滤（仅在只需要计数/统计时使用）。
        数值型候选请用 :meth:`list_numeric_candidates` —— 两者 schema 不同，
        混在一起做 ``model_validate`` 会因 ``extra=forbid`` 直接报错。
        """
        stmt = select(OfficialCandidateRow).order_by(OfficialCandidateRow.candidate_id)
        if family is not None:
            stmt = stmt.where(
                (OfficialCandidateRow.candidate_family == family)
                | (OfficialCandidateRow.candidate_family.is_(None))
            )
        rows = session.scalars(stmt).all()
        return [
            OfficialBenchmarkCandidate.model_validate(json.loads(r.payload_json)) for r in rows
        ]

    def list_numeric_candidates(
        self, session: Session, *, family: str = "sglang_numeric_cookbook"
    ) -> list[SglangNumericCandidate]:
        rows = session.scalars(
            select(OfficialCandidateRow)
            .where(OfficialCandidateRow.candidate_family == family)
            .order_by(OfficialCandidateRow.candidate_id)
        ).all()
        return [SglangNumericCandidate.model_validate(json.loads(r.payload_json)) for r in rows]

    def candidate_family_counts(self, session: Session) -> dict[str, int]:
        rows = session.execute(
            select(OfficialCandidateRow.candidate_family, func.count()).group_by(
                OfficialCandidateRow.candidate_family
            )
        ).all()
        return {(family or "manifest_partial_evidence"): int(n) for family, n in rows}

    def benchmark_ids_by_kind(self, session: Session, *kinds: str) -> list[str]:
        """按 ``performance_source_kind`` 取记录 ID。"""
        stmt = select(BenchmarkRecordRow.record_id).where(
            BenchmarkRecordRow.performance_source_kind.in_(list(kinds))
        )
        return list(session.scalars(stmt).all())

    def delete_pricing_results(
        self,
        session: Session,
        *,
        benchmark_record_ids: Sequence[str] | None = None,
        mode: str | None = None,
    ) -> int:
        """删除计算结果，可按记录 ID 与 mode 收窄范围。

        用途：默认业务计算排除 synthetic_fixture 时，清理库中遗留的测试态计算结果，
        避免"默认口径"的库里混着测试记录的结果。
        """
        stmt = delete(PricingResultRow)
        if benchmark_record_ids is not None:
            if not benchmark_record_ids:
                return 0
            stmt = stmt.where(PricingResultRow.benchmark_record_id.in_(list(benchmark_record_ids)))
        if mode is not None:
            stmt = stmt.where(PricingResultRow.mode == mode)
        return int(session.execute(stmt).rowcount or 0)

    def record_import_run(
        self,
        session: Session,
        *,
        import_run_id: str,
        source_file: str,
        raw_sha256: str,
        imported_at,
        evidence_count: int,
        hardware_price_count: int,
        token_price_count: int,
        benchmark_count: int,
        notes: Sequence[str] | None = None,
    ) -> None:
        session.merge(
            ImportRunRow(
                import_run_id=import_run_id,
                source_file=source_file,
                raw_sha256=raw_sha256,
                imported_at=imported_at,
                evidence_count=evidence_count,
                hardware_price_count=hardware_price_count,
                token_price_count=token_price_count,
                benchmark_count=benchmark_count,
                notes_json=json.dumps(list(notes or []), ensure_ascii=False),
            )
        )

    # -- 读取 -------------------------------------------------------------
    def counts(self, session: Session) -> RepositoryCounts:
        def c(row) -> int:
            return int(session.scalar(select(func.count()).select_from(row)) or 0)

        return RepositoryCounts(
            evidence=c(EvidenceRow),
            hardware_price=c(HardwarePriceRow),
            token_price=c(TokenPriceRow),
            benchmark_record=c(BenchmarkRecordRow),
            pricing_result=c(PricingResultRow),
        )

    def list_benchmarks(self, session: Session) -> list[BenchmarkRecord]:
        rows = session.scalars(
            select(BenchmarkRecordRow).order_by(BenchmarkRecordRow.record_id)
        ).all()
        return [BenchmarkRecord.model_validate(json.loads(r.payload_json)) for r in rows]

    def list_hardware_prices(self, session: Session) -> list[HardwarePrice]:
        rows = session.scalars(select(HardwarePriceRow)).all()
        return [HardwarePrice.model_validate(json.loads(r.payload_json)) for r in rows]

    def list_token_prices(self, session: Session) -> list[TokenPrice]:
        rows = session.scalars(select(TokenPriceRow)).all()
        return [TokenPrice.model_validate(json.loads(r.payload_json)) for r in rows]

    def list_evidence(self, session: Session) -> list[Evidence]:
        rows = session.scalars(select(EvidenceRow).order_by(EvidenceRow.evidence_id)).all()
        return [Evidence.model_validate(json.loads(r.payload_json)) for r in rows]

    def list_pricing_results(self, session: Session) -> list[PricingResult]:
        rows = session.scalars(
            select(PricingResultRow).order_by(PricingResultRow.scenario_id)
        ).all()
        return [PricingResult.model_validate(json.loads(r.payload_json)) for r in rows]

    def get_benchmark(self, session: Session, record_id: str) -> BenchmarkRecord | None:
        row = session.get(BenchmarkRecordRow, record_id)
        return BenchmarkRecord.model_validate(json.loads(row.payload_json)) if row else None
