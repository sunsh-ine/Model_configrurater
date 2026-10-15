"""SQLite 持久化（SQLAlchemy 2.x）。

**精度决策**：SQLite 的 NUMERIC 实际以 REAL(float) 存储，会破坏 Decimal 精度。
因此所有金额/比率列一律用 ``Text`` 存放十进制字面量（如 ``"149.54340799296"``），
并在每行额外保存完整 ``payload_json``（``model_dump(mode="json")``）作为权威记录。
读取时由 Pydantic 把字符串还原为 Decimal，保证复算与落库一致。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    create_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker


class Base(DeclarativeBase):
    pass


def _dec_col() -> Mapped[str | None]:
    """十进制字面量列（Text，非 NUMERIC/REAL）。"""
    return mapped_column(Text, nullable=True)


class EvidenceRow(Base):
    __tablename__ = "evidence"

    evidence_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    publisher: Mapped[str | None] = mapped_column(String(255))
    source_url: Mapped[str | None] = mapped_column(Text)
    source_file: Mapped[str | None] = mapped_column(Text)
    source_sheet: Mapped[str | None] = mapped_column(String(128))
    source_row: Mapped[int | None] = mapped_column(Integer)
    source_version: Mapped[str | None] = mapped_column(String(64))
    retrieved_at: Mapped[datetime | None] = mapped_column(DateTime)
    raw_sha256: Mapped[str | None] = mapped_column(String(64))
    notes: Mapped[str | None] = mapped_column(Text)
    quality_flags_json: Mapped[str | None] = mapped_column(Text)
    # 测量来源归属：区分"文件在内部"与"数据由我们实测"
    measurement_origin: Mapped[str] = mapped_column(String(48), nullable=False, index=True)
    is_internal_measurement: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)


class HardwarePriceRow(Base):
    __tablename__ = "hardware_price"

    hardware_price_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    gpu_model: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    gpus_per_server: Mapped[int | None] = mapped_column(Integer)
    per_gpu_annual_cost_10k: Mapped[str | None] = _dec_col()
    currency: Mapped[str | None] = mapped_column(String(16))
    currency_assumption: Mapped[str | None] = mapped_column(Text)
    cost_scope: Mapped[str | None] = mapped_column(Text)
    evidence_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("evidence.evidence_id"), nullable=False
    )
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)


class TokenPriceRow(Base):
    __tablename__ = "token_price"

    token_price_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    model_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    price_cached_input_per_1m: Mapped[str | None] = _dec_col()
    price_uncached_input_per_1m: Mapped[str | None] = _dec_col()
    price_output_per_1m: Mapped[str | None] = _dec_col()
    currency: Mapped[str | None] = mapped_column(String(16))
    currency_assumption: Mapped[str | None] = mapped_column(Text)
    evidence_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("evidence.evidence_id"), nullable=False
    )
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)


class BenchmarkRecordRow(Base):
    __tablename__ = "benchmark_record"

    record_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    model_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    hardware_model: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    deployment_gpu_count: Mapped[int] = mapped_column(Integer, nullable=False)
    performance_source_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    verification_status: Mapped[str] = mapped_column(String(32), nullable=False)
    evidence_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("evidence.evidence_id"), nullable=False
    )
    configured_concurrency: Mapped[int | None] = mapped_column(Integer)
    input_tps_per_gpu: Mapped[str | None] = _dec_col()
    output_tps_per_gpu: Mapped[str | None] = _dec_col()
    cache_hit_rate_raw: Mapped[str | None] = _dec_col()
    cache_metric_semantics: Mapped[str] = mapped_column(String(32), nullable=False)
    legacy_proxy_for_billable_hit_rate: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    legacy_input_revenue_10k_per_gpu_year: Mapped[str | None] = _dec_col()
    legacy_output_revenue_10k_per_gpu_year: Mapped[str | None] = _dec_col()
    legacy_total_revenue_10k_per_gpu_year: Mapped[str | None] = _dec_col()
    legacy_cost_10k_per_gpu_year: Mapped[str | None] = _dec_col()
    legacy_revenue_cost_ratio: Mapped[str | None] = _dec_col()
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)


class PricingResultRow(Base):
    __tablename__ = "pricing_result"

    scenario_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    benchmark_record_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("benchmark_record.record_id"), nullable=False, index=True
    )
    mode: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    # 口径：business / test（默认业务统计只看 business）
    scope: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    result_type: Mapped[str] = mapped_column(String(48), nullable=False)
    sla_status: Mapped[str] = mapped_column(String(24), nullable=False)
    # 输入追溯：性能记录 / 硬件成本 / Token 单价
    performance_record_id: Mapped[str | None] = mapped_column(String(128))
    token_price_id: Mapped[str | None] = mapped_column(String(128))
    hardware_price_id: Mapped[str | None] = mapped_column(String(128))
    evidence_ids_json: Mapped[str | None] = mapped_column(Text)
    effective_monetization_ratio: Mapped[str | None] = _dec_col()
    input_revenue_10k_per_gpu_year: Mapped[str | None] = _dec_col()
    output_revenue_10k_per_gpu_year: Mapped[str | None] = _dec_col()
    total_revenue_10k_per_gpu_year: Mapped[str | None] = _dec_col()
    gpu_annual_cost_10k: Mapped[str | None] = _dec_col()
    revenue_cost_ratio: Mapped[str | None] = _dec_col()
    breakeven_monetization_ratio: Mapped[str | None] = _dec_col()
    total_cluster_cost_10k_year: Mapped[str | None] = _dec_col()
    total_cluster_revenue_10k_year: Mapped[str | None] = _dec_col()
    input_snapshot_hash: Mapped[str | None] = mapped_column(String(64))
    cost_scope: Mapped[str] = mapped_column(String(32), nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)


class OfficialCandidateRow(Base):
    """官方 Benchmark **候选证据**（partial_evidence）。

    与 ``benchmark_record`` 分开存放：候选缺少硬件/卡数等必填绑定信息，
    不能进入性能数据库，也不参与任何经济性计算。
    """

    __tablename__ = "official_benchmark_candidate"

    candidate_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    manifest_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    evidence_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("evidence.evidence_id"), nullable=False
    )
    publisher: Mapped[str] = mapped_column(String(128), nullable=False)
    source_url: Mapped[str | None] = mapped_column(Text)
    framework: Mapped[str | None] = mapped_column(String(64))
    benchmark_tool: Mapped[str | None] = mapped_column(String(128))
    verification_status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    hardware_binding: Mapped[str] = mapped_column(String(16), nullable=False)
    hardware_model: Mapped[str | None] = mapped_column(String(255))
    deployment_gpu_count: Mapped[int | None] = mapped_column(Integer)
    is_throughput_benchmark: Mapped[bool | None] = mapped_column(Boolean)
    evidence_file: Mapped[str | None] = mapped_column(Text)
    evidence_sha256: Mapped[str | None] = mapped_column(String(64))
    missing_fields_json: Mapped[str | None] = mapped_column(Text)
    metrics_json: Mapped[str | None] = mapped_column(Text)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)


class ImportRunRow(Base):
    __tablename__ = "import_run"

    import_run_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    source_file: Mapped[str] = mapped_column(Text, nullable=False)
    raw_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    imported_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    evidence_count: Mapped[int] = mapped_column(Integer, nullable=False)
    hardware_price_count: Mapped[int] = mapped_column(Integer, nullable=False)
    token_price_count: Mapped[int] = mapped_column(Integer, nullable=False)
    benchmark_count: Mapped[int] = mapped_column(Integer, nullable=False)
    notes_json: Mapped[str | None] = mapped_column(Text)


class Database:
    """轻量数据库封装，负责建库与 Session 生命周期。"""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(f"sqlite:///{self.path}", future=True)
        self._session_factory = sessionmaker(bind=self.engine, expire_on_commit=False, future=True)

    def create_all(self) -> None:
        Base.metadata.create_all(self.engine)

    def session(self) -> Session:
        return self._session_factory()

    def dispose(self) -> None:
        self.engine.dispose()
