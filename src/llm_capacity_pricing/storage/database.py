"""SQLite 持久化（SQLAlchemy 2.x）。

**精度决策**：SQLite 的 NUMERIC 实际以 REAL(float) 存储，会破坏 Decimal 精度。
因此所有金额/比率列一律用 ``Text`` 存放十进制字面量（如 ``"123.45678901234567"``），
并在每行额外保存完整 ``payload_json``（``model_dump(mode="json")``）作为权威记录。
读取时由 Pydantic 把字符串还原为 Decimal，保证复算与落库一致。
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
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

    两个家族共用本表（用 ``candidate_family`` 区分）：

    * ``manifest_partial_evidence`` —— 由 ``official_manifest.yaml`` 登记、页面级证据；
    * ``sglang_numeric_cookbook`` —— M7 接入的官方 Cookbook **数值型**候选：
      有完整测试命令与控制台输出、已逐字段机器核对，但仍缺 model revision。
    """

    __tablename__ = "official_benchmark_candidate"

    candidate_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    #: 家族标识；历史行可能为 NULL，读取时按 manifest 家族处理
    candidate_family: Mapped[str | None] = mapped_column(String(48), index=True)
    #: 来源内稳定记录 ID（数值型候选有；manifest 候选为 NULL）
    record_id: Mapped[str | None] = mapped_column(String(160), index=True)
    manifest_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    evidence_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("evidence.evidence_id"), nullable=False
    )
    publisher: Mapped[str] = mapped_column(String(128), nullable=False)
    source_url: Mapped[str | None] = mapped_column(Text)
    source_path: Mapped[str | None] = mapped_column(Text)
    #: 上游 Git 对象的 SHA-1；与文件 SHA256 语义不同，故分开存
    source_git_blob_sha: Mapped[str | None] = mapped_column(String(40))
    framework: Mapped[str | None] = mapped_column(String(64))
    benchmark_tool: Mapped[str | None] = mapped_column(String(128))
    verification_status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    #: 候选自带的质量状态（本阶段恒为 partial_evidence）
    quality_status: Mapped[str | None] = mapped_column(String(32))
    hardware_binding: Mapped[str] = mapped_column(String(16), nullable=False)
    hardware_model: Mapped[str | None] = mapped_column(String(255))
    deployment_gpu_count: Mapped[int | None] = mapped_column(Integer)
    is_throughput_benchmark: Mapped[bool | None] = mapped_column(Boolean)
    evidence_file: Mapped[str | None] = mapped_column(Text)
    evidence_sha256: Mapped[str | None] = mapped_column(String(64))
    #: M9 派生门禁（NULL = 本行未做门禁评估）
    can_reference_performance: Mapped[bool | None] = mapped_column(Boolean)
    can_evaluate_sla: Mapped[bool | None] = mapped_column(Boolean)
    can_calculate_pricing: Mapped[bool | None] = mapped_column(Boolean)
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
        #: ``create_all`` 过程中补加的列（用于报告"从旧库升级"的实际动作）
        self.last_migration_columns: list[str] = []

    def create_all(self) -> None:
        Base.metadata.create_all(self.engine)
        self._ensure_columns()

    def _ensure_columns(self) -> None:
        """轻量迁移：为**已存在**的表补上模型新增的可空列。

        新库由 ``create_all`` 一次建好；已有库（例如上一批交付的 ``data/local.db``）
        不会被 ``create_all`` 改动，因此这里用 ``PRAGMA table_info`` 对比后
        ``ALTER TABLE ... ADD COLUMN``。只补可空列（NOT NULL 无默认值的列无法安全补加，
        交给新库建表处理），不做任何数据改写。
        """
        from sqlalchemy import inspect, text

        inspector = inspect(self.engine)
        existing_tables = set(inspector.get_table_names())
        added: list[str] = []
        with self.engine.begin() as conn:
            for table in Base.metadata.sorted_tables:
                if table.name not in existing_tables:
                    continue
                have = {c["name"] for c in inspector.get_columns(table.name)}
                for col in table.columns:
                    if col.name in have or not col.nullable:
                        continue
                    type_sql = col.type.compile(self.engine.dialect)
                    conn.execute(
                        text(f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" {type_sql}')
                    )
                    added.append(f"{table.name}.{col.name}")
        self.last_migration_columns = added

    def session(self) -> Session:
        return self._session_factory()

    def dispose(self) -> None:
        self.engine.dispose()

# ---------------------------------------------------------------------------
# 第二阶段（M13/M14）：模型与硬件规格库
# ---------------------------------------------------------------------------


class ModelSpecRow(Base):
    """模型结构事实（来自官方 config.json 与权重索引）。"""

    __tablename__ = "model_spec"

    model_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    repo_id: Mapped[str | None] = mapped_column(String(255))
    revision: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    attention_kind: Mapped[str | None] = mapped_column(String(32))
    num_hidden_layers: Mapped[int | None] = mapped_column(Integer)
    hidden_size: Mapped[int | None] = mapped_column(Integer)
    num_attention_heads: Mapped[int | None] = mapped_column(Integer)
    num_key_value_heads: Mapped[int | None] = mapped_column(Integer)
    head_dim: Mapped[int | None] = mapped_column(Integer)
    vocab_size: Mapped[int | None] = mapped_column(Integer)
    deployment_precision: Mapped[str | None] = mapped_column(String(16))
    weight_dtype_declared: Mapped[str | None] = mapped_column(String(32))
    weight_total_bytes: Mapped[int | None] = mapped_column(BigInteger)
    config_sha256: Mapped[str | None] = mapped_column(String(64))
    evidence_id: Mapped[str | None] = mapped_column(String(128))
    missing_fields_json: Mapped[str | None] = mapped_column(Text)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)


class HardwareSpecRow(Base):
    """GPU SKU 规格（容量/带宽/形态，带来源与待核实字段）。"""

    __tablename__ = "hardware_spec"

    hardware_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    vendor: Mapped[str] = mapped_column(String(64), nullable=False)
    product_name: Mapped[str] = mapped_column(String(255), nullable=False)
    sku: Mapped[str | None] = mapped_column(String(128), index=True)
    form_factor: Mapped[str | None] = mapped_column(String(32))
    hbm_type: Mapped[str | None] = mapped_column(String(32))
    hbm_capacity_declared: Mapped[str | None] = mapped_column(String(32))
    total_memory_bytes: Mapped[int | None] = mapped_column(BigInteger)
    usable_memory_bytes: Mapped[int | None] = mapped_column(BigInteger)
    usable_memory_status: Mapped[str | None] = mapped_column(String(32))
    memory_bandwidth_bytes_per_s: Mapped[int | None] = mapped_column(BigInteger)
    verification_status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    supported_in_phase2: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    source_id: Mapped[str | None] = mapped_column(String(128))
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)


class ServerProfileRow(Base):
    __tablename__ = "server_profile"

    server_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    profile_name: Mapped[str] = mapped_column(String(255), nullable=False)
    gpu_hardware_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("hardware_spec.hardware_id"), nullable=False, index=True
    )
    gpus_per_node: Mapped[int | None] = mapped_column(Integer)
    interconnect_declared: Mapped[str | None] = mapped_column(String(255))
    verification_status: Mapped[str | None] = mapped_column(String(32))
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)


# ---------------------------------------------------------------------------
# 第三阶段（M20）：性能实测数据
# ---------------------------------------------------------------------------


class BenchmarkRunRow(Base):
    """一次完整压测（BenchmarkRun）：签名 + 聚合指标 + 原始文件与哈希。"""

    __tablename__ = "benchmark_run"

    run_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    model_name: Mapped[str | None] = mapped_column(String(255), index=True)
    hardware_model: Mapped[str | None] = mapped_column(String(255), index=True)
    gpu_count: Mapped[int | None] = mapped_column(Integer)
    tp_size: Mapped[int | None] = mapped_column(Integer)
    dp_size: Mapped[int | None] = mapped_column(Integer)
    framework: Mapped[str | None] = mapped_column(String(64))
    framework_version: Mapped[str | None] = mapped_column(String(64))
    configured_concurrency: Mapped[int | None] = mapped_column(Integer, index=True)
    input_len_target: Mapped[int | None] = mapped_column(Integer)
    output_len_target: Mapped[int | None] = mapped_column(Integer)
    #: 数据可信等级（synthetic / 厂商发布 / 内部实测 …）
    data_trust: Mapped[str] = mapped_column(String(48), nullable=False, index=True)
    load_profile_kind: Mapped[str] = mapped_column(String(48), nullable=False, index=True)
    output_throughput_tok_s: Mapped[str | None] = mapped_column(Text)
    ttft_p99_ms: Mapped[str | None] = mapped_column(Text)
    tpot_p99_ms: Mapped[str | None] = mapped_column(Text)
    error_rate: Mapped[str | None] = mapped_column(Text)
    raw_file: Mapped[str | None] = mapped_column(Text)
    raw_sha256: Mapped[str | None] = mapped_column(String(64))
    collected_at: Mapped[datetime | None] = mapped_column(DateTime)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)


# ---------------------------------------------------------------------------
# 第四阶段（M27）：成本目录与计费规则
# ---------------------------------------------------------------------------


class CostCatalogItemRow(Base):
    """成本目录项（GPU / 机房 / 电力 / 网络 / 软件 / 运维 等）。

    与第一阶段 ``hardware_price`` **并列存在**：第一阶段只覆盖 GPU 成本，
    本表是完整 TCO 的成本来源，两者口径不同，不得互相覆盖。
    """

    __tablename__ = "cost_catalog_item"

    item_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    category: Mapped[str] = mapped_column(String(48), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    basis: Mapped[str | None] = mapped_column(String(16), index=True)
    price_basis: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    unit_price: Mapped[str | None] = mapped_column(Text)
    unit: Mapped[str] = mapped_column(String(32), nullable=False)
    currency: Mapped[str | None] = mapped_column(String(16))
    currency_status: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    origin: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    applies_to_hardware_id: Mapped[str | None] = mapped_column(String(128), index=True)
    region: Mapped[str | None] = mapped_column(String(64))
    default_quantity: Mapped[str | None] = mapped_column(Text)
    valid_from: Mapped[date | None] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date)
    evidence_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("evidence.evidence_id"), nullable=False
    )
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)


class BillingRuleRow(Base):
    """Token 计费规则（M29）。与第一阶段 ``token_price`` 并存：
    ``token_price`` 是历史复算用的参考单价，本表是**面向业务的计费口径**
    （含可计费缓存比例、批处理折扣、最小计费量）。
    """

    __tablename__ = "billing_rule"

    rule_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    model_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    currency: Mapped[str | None] = mapped_column(String(16))
    currency_status: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    price_uncached_input_per_1m: Mapped[str | None] = mapped_column(Text)
    price_cached_input_per_1m: Mapped[str | None] = mapped_column(Text)
    price_output_per_1m: Mapped[str | None] = mapped_column(Text)
    billable_cache_ratio: Mapped[str | None] = mapped_column(Text)
    billable_cache_basis: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    evidence_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("evidence.evidence_id"), nullable=False
    )
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)


class CommercialConfirmationRow(Base):
    """商务确认记录（M30）。**只能由人工写入**，Agent 不生成确认。"""

    __tablename__ = "commercial_confirmation"

    confirmation_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    scope: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    currency_confirmed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    price_confirmed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    terms_confirmed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    confirmed_by: Mapped[str | None] = mapped_column(String(128))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime)
    valid_until: Mapped[date | None] = mapped_column(Date)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)


# ---------------------------------------------------------------------------
# 第四阶段（V4.1 F4.1）：SLA 证据与人工审批记录
# ---------------------------------------------------------------------------


class SlaEvidenceRow(Base):
    """持久化的 SLA 证据。报价门禁只认这里的记录，不认调用方字符串。"""

    __tablename__ = "sla_evidence"

    sla_evaluation_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    candidate_id: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    requirement_id: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    match_level: Mapped[str | None] = mapped_column(String(32), index=True)
    matched_run_id: Mapped[str | None] = mapped_column(String(200), index=True)
    data_trust: Mapped[str | None] = mapped_column(String(48))
    policy_id: Mapped[str] = mapped_column(String(128), nullable=False)
    evaluated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    run_pool_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)


class QuoteApprovalRow(Base):
    """人工审批记录（Agent 无任何接口可生成）。"""

    __tablename__ = "quote_approval"

    approval_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    decision: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    scope_candidate_id: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    scope_requirement_id: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    scope_plan_id: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    cost_snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    approved_by: Mapped[str | None] = mapped_column(String(128))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime)
    valid_until: Mapped[date | None] = mapped_column(Date)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
