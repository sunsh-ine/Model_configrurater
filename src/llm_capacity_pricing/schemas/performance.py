"""第三阶段（M19-M25）性能评估与 SLA 的数据结构。

四个核心区分（贯穿全阶段，任何结果都必须落在其中之一）：

1. **匹配等级 ≠ 数据可信等级 ≠ SLA 状态**
   ``MatchLevel`` 说的是"这条 Benchmark 与目标需求的配置有多接近"；
   ``DataTrust`` 说的是"这条数据本身有多可信"；
   ``SlaStatus`` 说的是"针对这个需求，SLA 是否已被**实测**验证"。
   三者互不蕴含：配置完全匹配的 synthetic 数据也不能支撑 ``observed_pass``；
   官方发布的公开数据即使可信，也不能当作内部部署的实测值。

2. **预测 ≠ 实测**
   ``PerformanceEstimate.estimate_kind`` 区分 ``measured_reference``（直接命中实测）、
   ``calibrated_estimate``（同配置范围内插值，带留出误差）与 ``not_predictable``。

3. **单点最优 ≠ 可持续容量**
   ``CapacityProfile`` 只给 ``best_observed_point``；``maximum_sustainable_capacity``
   必须有持续时间/失败率/稳定性证据，否则保持 ``None``。

4. **配置并发 ≠ 观测并发**；**客户端指标 ≠ 服务端指标**；
   **PP/EP/PD 字段保存但阻断**（本阶段不建其性能模型）。
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

# ---------------------------------------------------------------------------
# 枚举
# ---------------------------------------------------------------------------

#: Benchmark 与目标需求的配置匹配等级（M19）
MatchLevel = Literal[
    "exact_match",  # 关键部署条件与 Workload 均满足严格匹配规则
    "comparable_reference",  # 有参考价值，但存在非关键差异或细节未确认
    "modeling_candidate",  # 可进入显式建模/校准流程，**不能**直接用于 SLA 证明
    "rejected",  # 关键配置不一致 / 来源不可信 / 指标缺失
]

#: 数据可信等级（与加载方式无关，描述数据来源与测量方式）
DataTrust = Literal[
    "measured_internal",  # 自有环境实测
    "measured_vendor_published",  # 厂商/框架官方公开发布的实测
    "measured_third_party",  # 第三方（非厂商、非本项目）发布的实测
    "historical_external_no_latency",  # 历史外部数据，无时延口径（如 Excel 32 条）
    "synthetic_fixture",  # 人工构造，仅用于算法验证
    "unknown",
]

#: 指标来源侧
MetricSide = Literal["client", "server", "unknown"]

#: 请求到达方式
ArrivalMode = Literal["unbounded", "request_rate", "max_concurrency", "unknown"]

#: SLA 判定状态（M23）
SlaStatus = Literal[
    "observed_pass",  # 有匹配的实测数据且全部约束通过
    "observed_fail",  # 有匹配的实测数据且至少一项明确超标
    "estimated_only",  # 只有预测/估算，无实测验证
    "not_evaluable",  # 缺关键指标或配置不匹配
]

#: 性能结论来源类型（M22）
EstimateKind = Literal[
    "measured_reference",  # 精确命中已观测点
    "calibrated_estimate",  # 同配置范围内估计，附留出误差
    "not_predictable",  # 数据不足或误差过大
]

#: 单次压测的用途分类（两类负载不得混入同一条曲线）
LoadProfileKind = Literal[
    "fixed_length_standard",  # 固定长度标准测试（用于性能曲线）
    "multi_turn_prefix_reuse",  # 多轮会话/前缀复用（研究 KV 影响）
    "unknown",
]


# ---------------------------------------------------------------------------
# M19：签名与匹配
# ---------------------------------------------------------------------------


class WorkloadSignature(BaseModel):
    """一次 Benchmark 的负载条件。"""

    model_config = ConfigDict(extra="forbid")

    #: 目标长度（测试参数）
    input_len_target: int | None = None
    output_len_target: int | None = None
    #: 实际长度（测量结果；缺则留空）
    input_len_actual_mean: Decimal | None = None
    output_len_actual_mean: Decimal | None = None
    input_len_distribution: str | None = None
    output_len_distribution: str | None = None

    dataset_name: str | None = None
    #: 会话结构 / 是否复用前缀（两类负载不得混算）
    session_structure: str | None = None
    prefix_reuse: bool | None = None
    load_profile_kind: LoadProfileKind = "unknown"

    arrival_mode: ArrivalMode = "unknown"
    request_rate_rps: Decimal | None = None
    configured_concurrency: int | None = None
    observed_concurrency: Decimal | None = None

    streaming: bool | None = None
    warmup: bool | None = None
    eos_policy: str | None = None

    #: 压测时长与样本量（判断"可持续容量"的证据）
    duration_s: Decimal | None = None
    num_requests: int | None = None
    success_count: int | None = None
    failed_count: int | None = None


class BenchmarkSignature(BaseModel):
    """描述"这条 Benchmark 是在什么部署与负载下测出来的"。"""

    model_config = ConfigDict(extra="forbid")

    signature_id: str
    source_kind: str  # benchmark_record / official_candidate / benchmark_run
    source_ref: str  # record_id / candidate_id / run_id

    # ---- 模型 ----
    model_name: str | None = None
    model_revision: str | None = None
    weight_precision: str | None = None
    quantization: str | None = None

    # ---- 硬件 ----
    hardware_model: str | None = None
    hardware_sku: str | None = None
    gpu_count: int | None = None
    node_topology: str | None = None  # 单机 / 多机

    # ---- 部署 ----
    tp_size: int | None = None
    dp_size: int | None = None
    pp_size: int | None = None
    ep_size: int | None = None
    deployment_mode: str | None = None  # colocated / prefill_decode_disaggregated

    # ---- 框架 ----
    framework: str | None = None
    framework_version: str | None = None
    backend: str | None = None
    runtime_args: dict[str, Any] = Field(default_factory=dict)

    # ---- 负载 ----
    workload: WorkloadSignature = Field(default_factory=WorkloadSignature)

    # ---- 指标口径 ----
    metric_definition_ids: list[str] = Field(default_factory=list)
    metric_side: MetricSide = "unknown"
    statistics_window: str | None = None
    sample_size: int | None = None

    # ---- 可信等级 ----
    data_trust: DataTrust = "unknown"

    def has_capability(self, field: str) -> bool:
        """该签名是否具备某个字段/指标的证据（用于阻断判断）。"""
        return getattr(self, field, None) is not None


class TargetRequirement(BaseModel):
    """待评估的目标需求（来自业务输入或第二阶段的部署候选）。"""

    model_config = ConfigDict(extra="forbid")

    model_name: str | None = None
    model_revision: str | None = None
    weight_precision: str | None = None
    quantization: str | None = None

    hardware_model: str | None = None
    hardware_sku: str | None = None
    gpu_count: int | None = None

    tp_size: int | None = None
    dp_size: int | None = None
    pp_size: int | None = None
    ep_size: int | None = None
    deployment_mode: str | None = None

    framework: str | None = None
    framework_version: str | None = None

    workload: WorkloadSignature = Field(default_factory=WorkloadSignature)

    #: 需要哪些指标（缺任一 -> rejected，避免"配置像但没数据"）
    required_metrics: list[str] = Field(default_factory=list)
    #: 是否要求数据可信等级达到"实测"（SLA 场景必须为 True）
    require_measured: bool = False
    #: 是否允许 synthetic 数据参与（**仅算法验证**可用，SLA 场景必须为 False）
    allow_synthetic: bool = False

    #: 负载长度容差（相对值），用于 exact_match 判定
    length_tolerance: Decimal = Decimal("0.05")


class BenchmarkMatchResult(BaseModel):
    """匹配结果：等级 + 差异 + 使用限制。"""

    model_config = ConfigDict(extra="forbid")

    target_ref: str
    signature_id: str
    source_ref: str
    level: MatchLevel
    #: 结构化原因码，形如 ``model_mismatch:Qwen3-32B!=Qwen3-235B``
    reasons: list[str] = Field(default_factory=list)
    #: 关键字段差异（字段 -> [目标值, 数据值]）
    differences: dict[str, list[Any]] = Field(default_factory=dict)
    #: 该结果**可以**用于什么
    usable_for: list[str] = Field(default_factory=list)
    #: 该结果**禁止**用于什么
    blocked_from: list[str] = Field(default_factory=list)
    data_trust: DataTrust = "unknown"
    #: 该结论是否**仅用于算法验证**（目标显式 allow_synthetic 时置 True）
    algorithm_validation_only: bool = False

    # ---- 证据构成（V3.1 最终复审 P1：聚合证据必须可追溯到每条 Run）--------
    #: 该匹配结论覆盖的**全部**实测运行：单条匹配时只有一个元素，聚合时是全部参与运行。
    supporting_run_ids: list[str] = Field(default_factory=list)
    #: 每条参与运行各自的匹配等级（run_id -> level）
    per_run_levels: dict[str, str] = Field(default_factory=dict)
    #: 每条参与运行的来源标识（run_id -> source_ref）
    per_run_source_refs: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _guard(self) -> "BenchmarkMatchResult":
        if self.supporting_run_ids:
            declared = set(self.supporting_run_ids)
            if len(declared) != len(self.supporting_run_ids):
                raise ValueError("supporting_run_ids 不得重复")
            for label, mapping in (
                ("per_run_levels", self.per_run_levels),
                ("per_run_source_refs", self.per_run_source_refs),
            ):
                if mapping and set(mapping) != declared:
                    raise ValueError(
                        f"{label} 的键必须与 supporting_run_ids 完全一致"
                        f"（缺 {sorted(declared - set(mapping))}，多 {sorted(set(mapping) - declared)}）"
                    )
        if self.level == "exact_match" and any(
            r.startswith(("model_", "hardware_", "missing_metric", "unsupported_", "data_trust_rejected"))
            for r in self.reasons
        ):
            raise ValueError("exact_match 不应携带阻断性原因码")
        if self.level == "exact_match" and self.data_trust == "synthetic_fixture":
            # synthetic 数据的 exact_match 只允许在**显式算法验证**场景出现
            if not self.algorithm_validation_only:
                raise ValueError(
                    "synthetic 数据不得被评为 exact_match（配置再像也不是实测）；"
                    "如确为算法验证，请让目标显式 allow_synthetic=True"
                )
        if self.level in ("modeling_candidate", "rejected") and "sla_proof" in self.usable_for:
            raise ValueError(f"{self.level} 不得用于 SLA 证明")
        return self


# ---------------------------------------------------------------------------
# M20：性能实测数据
# ---------------------------------------------------------------------------


class RequestMetric(BaseModel):
    """逐请求明细（可选；有它才能算请求级 Goodput）。"""

    model_config = ConfigDict(extra="forbid")

    request_id: str | None = None
    ttft_ms: Decimal | None = None
    tpot_ms: Decimal | None = None
    e2e_ms: Decimal | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    success: bool | None = None
    error_reason: str | None = None


class PerformanceProfile(BaseModel):
    """一次压测的聚合性能指标（统一口径）。"""

    model_config = ConfigDict(extra="forbid")

    # ---- 时延（客户端口径为默认；服务端指标另存）----
    ttft_mean_ms: Decimal | None = None
    ttft_p90_ms: Decimal | None = None
    ttft_p99_ms: Decimal | None = None
    tpot_mean_ms: Decimal | None = None
    tpot_p90_ms: Decimal | None = None
    tpot_p99_ms: Decimal | None = None
    itl_mean_ms: Decimal | None = None
    itl_p99_ms: Decimal | None = None
    e2e_mean_ms: Decimal | None = None
    e2e_p90_ms: Decimal | None = None
    e2e_p99_ms: Decimal | None = None

    # ---- 服务端口径（独立字段，不与客户端混算）----
    server_ttft_p99_ms: Decimal | None = None
    server_tpot_p99_ms: Decimal | None = None

    # ---- 吞吐 ----
    request_throughput_rps: Decimal | None = None
    input_throughput_tok_s: Decimal | None = None
    output_throughput_tok_s: Decimal | None = None
    total_throughput_tok_s: Decimal | None = None
    #: 每卡归一化（同配置内）
    output_throughput_tok_s_per_gpu: Decimal | None = None

    # ---- 并发 / 负载 ----
    configured_concurrency: int | None = None
    observed_concurrency: Decimal | None = None
    request_rate_rps: Decimal | None = None
    duration_s: Decimal | None = None
    num_requests: int | None = None
    success_count: int | None = None
    failed_count: int | None = None
    error_rate: Decimal | None = None
    error_breakdown: dict[str, int] = Field(default_factory=dict)

    # ---- 缓存（两种语义分开）----
    physical_cache_hit_rate: Decimal | None = None
    billable_cached_input_ratio: Decimal | None = None

    # ---- 口径 ----
    metric_definition_ids: list[str] = Field(default_factory=list)
    metric_side: MetricSide = "client"
    statistics_window: str | None = None

    #: 该 profile 内部的自洽性检查结果
    consistency_notes: list[str] = Field(default_factory=list)
    quality_flags: list[str] = Field(default_factory=list)
    missing_metrics: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _guard(self) -> "PerformanceProfile":
        # 缺失的 P99 不得由均值"推算"出来（这里只做结构保证：不填即 None）
        if self.tpot_p99_ms is not None and "m.latency.tpot.p99" not in self.metric_definition_ids:
            raise ValueError("给出 tpot_p99_ms 必须同时声明口径 m.latency.tpot.p99")
        if self.ttft_p99_ms is not None and "m.latency.ttft.p99" not in self.metric_definition_ids:
            raise ValueError("给出 ttft_p99_ms 必须同时声明口径 m.latency.ttft.p99")
        if self.tpot_p99_ms is not None and self.itl_p99_ms is not None:
            if self.tpot_p99_ms == self.itl_p99_ms and "tpot_equals_itl_unverified" not in self.quality_flags:
                raise ValueError(
                    "tpot_p99 与 itl_p99 取值相同：可能是同一口径被复用，"
                    "请确认后在 quality_flags 标注 tpot_equals_itl_unverified"
                )
        if self.success_count is not None and self.num_requests is not None:
            if self.success_count > self.num_requests:
                raise ValueError("成功数不应大于请求数")
        return self


class BenchmarkRun(BaseModel):
    """一次完整压测的运行记录（M20 的核心对象）。"""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    signature: BenchmarkSignature
    profile: PerformanceProfile

    # ---- 原始性与可复现 ----
    raw_file: str | None = None
    raw_sha256: str | None = None
    raw_format: str | None = None  # sglang_bench_serving_jsonl / vllm_bench_serve_json
    framework: str | None = None
    framework_version: str | None = None
    launch_command: str | None = None
    benchmark_command: str | None = None
    collected_at: datetime | None = None

    #: 逐请求明细（可选）
    request_metrics: list[RequestMetric] = Field(default_factory=list)

    #: 负载用途分类：固定长度标准测试与多轮/前缀复用测试不得混算
    load_profile_kind: LoadProfileKind = "unknown"
    #: 与真实业务的对应关系说明（可选）
    notes: str | None = None

    data_trust: DataTrust = "unknown"
    quality_flags: list[str] = Field(default_factory=list)

    @property
    def has_request_metrics(self) -> bool:
        return bool(self.request_metrics)


# ---------------------------------------------------------------------------
# M21：容量与曲线
# ---------------------------------------------------------------------------


class CapacityPoint(BaseModel):
    """单个负载点的归一化结果。"""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    configured_concurrency: int | None = None
    observed_concurrency: Decimal | None = None
    request_rate_rps: Decimal | None = None
    input_len_target: int | None = None
    output_len_target: int | None = None
    ttft_p99_ms: Decimal | None = None
    tpot_p99_ms: Decimal | None = None
    e2e_p99_ms: Decimal | None = None
    output_throughput_tok_s: Decimal | None = None
    total_throughput_tok_s: Decimal | None = None
    error_rate: Decimal | None = None
    data_trust: DataTrust = "unknown"


class CapacityProfile(BaseModel):
    """同一配置下多个负载点的容量曲线分析结果（M21）。"""

    model_config = ConfigDict(extra="forbid")

    group_key: str
    model_name: str | None = None
    hardware_model: str | None = None
    tp_size: int | None = None
    dp_size: int | None = None
    tp_dp_combination: str | None = None
    framework: str | None = None
    framework_version: str | None = None
    load_profile_kind: LoadProfileKind = "unknown"

    points: list[CapacityPoint] = Field(default_factory=list)
    #: 观测到的吞吐峰值点（**只表示已测到的最优点**）
    best_observed_point: CapacityPoint | None = None
    #: **单次观测**满足失败率与时长要求的最大并发（弱于可持续容量）
    observed_stable_candidate: int | None = None
    #: 可持续容量：必须另有重复观测/持续性/失败率证据，否则为 None（V3.1 R5）
    maximum_sustainable_capacity: int | None = None
    sustainability_evidence: list[str] = Field(default_factory=list)

    #: 已覆盖的并发范围（以及缺失的测试点）
    covered_concurrency_points: list[int] = Field(default_factory=list)
    missing_concurrency_points: list[int] = Field(default_factory=list)

    coverage_status: Literal["sufficient", "insufficient_coverage"] = "insufficient_coverage"
    notes: list[str] = Field(default_factory=list)
    quality_flags: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _guard(self) -> "CapacityProfile":
        if self.maximum_sustainable_capacity is not None and not self.sustainability_evidence:
            raise ValueError(
                "给出 maximum_sustainable_capacity 必须提供持续性证据"
                "（持续时间 / 失败率 / 稳定性），否则只能用 best_observed_point"
            )
        if len(self.points) < 2 and self.coverage_status == "sufficient":
            raise ValueError("少于 2 个负载点不能声称覆盖充分")
        return self


# ---------------------------------------------------------------------------
# M22：性能估算
# ---------------------------------------------------------------------------


class PerformanceEstimate(BaseModel):
    """性能估算结果（M22）。"""

    model_config = ConfigDict(extra="forbid")

    target_ref: str
    metric: str
    estimate_kind: EstimateKind
    value: Decimal | None = None
    unit: str | None = None
    #: measured_reference 时指向来源；calibrated_estimate 时列出参与拟合的点
    source_run_id: str | None = None
    supporting_runs: list[str] = Field(default_factory=list)

    method_id: str | None = None
    method_version: str | None = None
    #: 插值覆盖范围（超出范围不得外推）
    covered_range: list[Decimal] = Field(default_factory=list)
    #: 留出验证误差（不是拟合误差）
    holdout_mae: Decimal | None = None
    holdout_relative_error: Decimal | None = None
    holdout_failures: list[str] = Field(default_factory=list)

    reasons: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _guard(self) -> "PerformanceEstimate":
        if self.estimate_kind == "not_predictable":
            if self.value is not None:
                raise ValueError("not_predictable 不得给出数值")
            if not self.reasons:
                raise ValueError("not_predictable 必须说明原因")
        if self.estimate_kind == "measured_reference" and not self.source_run_id:
            raise ValueError("measured_reference 必须指向来源 run")
        if self.estimate_kind == "calibrated_estimate":
            if self.value is None or not self.method_id:
                raise ValueError("calibrated_estimate 必须给出数值与方法标识")
            if not self.covered_range:
                raise ValueError("calibrated_estimate 必须声明覆盖范围")
            if self.holdout_mae is None:
                raise ValueError("calibrated_estimate 必须给出留出误差（MAE）")
        return self


# ---------------------------------------------------------------------------
# M23：SLA
# ---------------------------------------------------------------------------


class SlaRequirement(BaseModel):
    """SLA 约束（逐项上限）。"""

    model_config = ConfigDict(extra="forbid")

    requirement_id: str
    ttft_p99_ms_max: Decimal | None = None
    tpot_p99_ms_max: Decimal | None = None
    itl_p99_ms_max: Decimal | None = None
    e2e_p99_ms_max: Decimal | None = None
    min_success_rate: Decimal | None = None
    min_request_throughput_rps: Decimal | None = None
    min_output_throughput_tok_s: Decimal | None = None
    #: 是否要求请求级 Goodput（需要逐请求明细）
    require_goodput: bool = False

    def constraints(self) -> dict[str, Decimal]:
        return {
            k: v
            for k, v in {
                "ttft_p99_ms": self.ttft_p99_ms_max,
                "tpot_p99_ms": self.tpot_p99_ms_max,
                "itl_p99_ms": self.itl_p99_ms_max,
                "e2e_p99_ms": self.e2e_p99_ms_max,
                "success_rate": self.min_success_rate,
                "request_throughput_rps": self.min_request_throughput_rps,
                "output_throughput_tok_s": self.min_output_throughput_tok_s,
            }.items()
            if v is not None
        }


class SlaPolicy(BaseModel):
    """SLA 判定策略：决定哪些数据可信到可以下结论。"""

    model_config = ConfigDict(extra="forbid")

    policy_id: str = "default"
    #: 允许作为 observed_pass/fail 依据的数据可信等级
    accepted_trust_levels: list[DataTrust] = Field(
        default_factory=lambda: ["measured_internal", "measured_vendor_published"]
    )
    #: 允许的最低匹配等级。**默认要求 exact_match**（V3.1 R1）：
    #:   * ``comparable_reference``（负载/细节不同）只用于展示、研究与对比；
    #:   * ``modeling_candidate``（部署条件不同）只用于建模与校准；
    #:   * 二者都**不得**直接用于 SLA 证明 —— 32K 的需求不能拿 1K 的实测来"达标"。
    minimum_match_level: MatchLevel = "exact_match"
    #: 是否允许 synthetic 数据参与判定（**默认禁止**；仅算法验证时显式开启）
    allow_synthetic: bool = False
    #: 允许 predicted 值作为 observed_* 吗（默认禁止，恒为 False）
    allow_estimate_as_observed: bool = False


class SlaConstraintResult(BaseModel):
    """单项约束的判定。"""

    model_config = ConfigDict(extra="forbid")

    constraint: str
    threshold: Decimal | None = None
    observed: Decimal | None = None
    unit: str | None = None
    status: SlaStatus
    reason: str | None = None


class SlaEvaluation(BaseModel):
    """SLA 评估结果（M23）。"""

    model_config = ConfigDict(extra="forbid")

    requirement_id: str
    target_ref: str
    status: SlaStatus
    policy_id: str = "default"

    #: 依据的数据
    matched_run_id: str | None = None
    match_level: MatchLevel | None = None
    data_trust: DataTrust | None = None

    constraints: list[SlaConstraintResult] = Field(default_factory=list)
    #: 每个约束独立评估后的整体理由
    reasons: list[str] = Field(default_factory=list)
    #: 观测到的请求级 Goodput（需要逐请求明细）
    goodput_rps: Decimal | None = None
    goodput_note: str | None = None
    sample_size: int | None = None
    statistics_window: str | None = None
    #: 该结论是否**仅用于算法验证**（数据为 synthetic）。生产结论必须为 False。
    algorithm_validation_only: bool = False

    @model_validator(mode="after")
    def _guard(self) -> "SlaEvaluation":
        if self.status in ("observed_pass", "observed_fail"):
            if self.matched_run_id is None:
                raise ValueError(f"{self.status} 必须指向具体的实测 run")
            if self.data_trust in ("unknown", "historical_external_no_latency"):
                raise ValueError(f"数据可信等级 {self.data_trust} 不能支撑 {self.status}")
            if self.data_trust == "synthetic_fixture":
                # synthetic 数据只允许用于**算法验证**，且必须显式打标 + 非默认策略；
                # 生产策略（default）下永远不可能得到 observed_*。
                if not self.algorithm_validation_only:
                    raise ValueError(
                        "synthetic 数据不能支撑 observed_pass/fail（仅可验证算法）；"
                        "如确为算法验证，请显式标记 algorithm_validation_only=True"
                    )
                if self.policy_id == "default":
                    raise ValueError("algorithm_validation_only 结果不得使用默认策略")
        if self.status == "estimated_only" and not any(
            c.status == "estimated_only" for c in self.constraints
        ):
            raise ValueError("estimated_only 必须至少有一项约束来自估算")
        return self


# ---------------------------------------------------------------------------
# M24：部署候选比较
# ---------------------------------------------------------------------------


class RankedDeployment(BaseModel):
    """一个部署候选的性能比较条目。"""

    model_config = ConfigDict(extra="forbid")

    candidate_id: str
    model_id: str
    hardware_id: str
    tp_size: int
    dp_size: int
    gpu_count: int

    #: 第二阶段静态结论
    static_status: str | None = None
    headroom_bytes: int | None = None

    #: 第三阶段性能证据
    match_level: MatchLevel | None = None
    matched_run_id: str | None = None
    #: 支撑该结论的**全部** run（聚合时不止一条；单条匹配时为 1 条）
    evidence_run_ids: list[str] = Field(default_factory=list)
    #: 每条支撑 run 各自的匹配等级（run_id -> level）
    evidence_per_run_levels: dict[str, str] = Field(default_factory=dict)
    sla_status: SlaStatus | None = None
    output_throughput_tok_s: Decimal | None = None
    ttft_p99_ms: Decimal | None = None
    tpot_p99_ms: Decimal | None = None

    rank_bucket: Literal[
        "verified_sla_met",  # 有匹配实测且满足 SLA
        "measured_but_sla_unmet",  # 有匹配实测但不满足
        "requires_benchmark",  # 静态可行但无性能证据
        "excluded",  # 静态不可行/不支持
    ]
    reasons: list[str] = Field(default_factory=list)
    unverified_risks: list[str] = Field(default_factory=list)


class DeploymentRanking(BaseModel):
    """候选排序结果（M24）。"""

    model_config = ConfigDict(extra="forbid")

    requirement_id: str
    entries: list[RankedDeployment] = Field(default_factory=list)
    #: 排序依据说明（可比性前提）
    comparability_notes: list[str] = Field(default_factory=list)
    #: 无法排序时的说明
    blocker: str | None = None
    economic_scoring_used: bool = False

    @model_validator(mode="after")
    def _guard(self) -> "DeploymentRanking":
        if self.economic_scoring_used:
            raise ValueError("币种与成本范围未确认前，不得使用理论满载营收比作为经济性评分")
        return self
