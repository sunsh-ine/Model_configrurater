"""官方 Benchmark **候选证据**（partial_evidence）模型。

用途：官方页面里有时确实存在**实测数值**，但配套的部署配置不完整（缺模型 revision、
缺硬件型号与卡数绑定、缺框架版本、缺负载口径）。这种数据：

* **不能**作为完整对比案例进入性能数据库（不满足 ``BenchmarkRecord`` 的必填项：
  硬件型号与 GPU 卡数无法诚实填写）；
* **也不能丢弃** —— 它是官方实测证据，对后续口径校准有价值；
* 因此以 ``partial_evidence`` 形式单独存放，并**逐字段记录"有 / 无"**。

硬约束：``verification_status`` 只有在所有完整比对必需字段齐备时才能是 ``verified``；
任一缺失即为 ``partial_evidence``。缺失字段**必须为空**，不允许"补造配置"。
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

CandidateStatus = Literal["partial_evidence", "pending_verification", "verified"]

#: 完整对比所必需的**配置**字段（模型属性）
REQUIRED_CONFIG_FIELDS = (
    "model_name",
    "model_revision",
    "framework_version",
    "hardware_model",
    "deployment_gpu_count",
    "dataset_name",
)
#: 完整对比所必需的**指标**字段（存在 ``metrics`` 字典里）
REQUIRED_METRIC_FIELDS = ("ttft_p99_ms", "tpot_p99_ms")
#: 二者合集：缺任一即不能作为完整官方案例
REQUIRED_FOR_VERIFIED = REQUIRED_CONFIG_FIELDS + REQUIRED_METRIC_FIELDS


class OfficialBenchmarkCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: str
    manifest_id: str
    evidence_id: str
    publisher: str
    source_url: str | None = None
    source_version: str | None = None
    evidence_file: str | None = None
    evidence_sha256: str | None = None
    accessed_at: datetime | None = None

    framework: str | None = None
    benchmark_tool: str | None = None
    verification_status: CandidateStatus = "partial_evidence"

    # ---- 配置：拿不到就留空，绝不补造 -----------------------------------
    model_name: str | None = None
    model_revision: str | None = None
    model_precision: str | None = None
    framework_version: str | None = None
    #: 来源中**出现过的**硬件候选（原样保留，不代表该数值由这些配置产出）
    hardware_candidates: list[str] = Field(default_factory=list)
    #: 数值与哪个硬件配置对应。页面未绑定时为 "unbound"。
    hardware_binding: Literal["bound", "unbound"] = "unbound"
    hardware_model: str | None = None
    deployment_gpu_count: int | None = None
    deployment_strategy_raw: str | None = None
    dataset_name: str | None = None
    input_length: int | None = None
    output_length: int | None = None
    configured_concurrency: int | None = None
    request_rate_rps: Decimal | None = None

    # ---- 指标（由证据文件解析得到）--------------------------------------
    metrics: dict[str, Decimal] = Field(default_factory=dict)
    raw_labels: dict[str, str] = Field(default_factory=dict)
    #: 是否为真正的吞吐基准测试。``None`` = 无法从证据判定（未机器解析指标）。
    #: 单请求冒烟测试必须显式为 ``False``。
    is_throughput_benchmark: bool | None = None

    # ---- M9 门禁（派生，不接受人工填写）----------------------------------
    can_reference_performance: bool = False
    can_evaluate_sla: bool = False
    can_calculate_pricing: bool = False
    commercial_quote_allowed: bool = False
    applicability_notes: list[str] = Field(default_factory=list)

    # ---- 缺口台账 --------------------------------------------------------
    present_fields: list[str] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)
    notes: str | None = None
    quality_flags: list[str] = Field(default_factory=list)

    def absent_required_fields(self) -> list[str]:
        """按配置/指标两类分别检查后合并的缺口清单。"""
        missing_config = [
            f for f in REQUIRED_CONFIG_FIELDS if getattr(self, f, None) in (None, "")
        ]
        missing_metrics = [f for f in REQUIRED_METRIC_FIELDS if f not in self.metrics]
        return missing_config + missing_metrics

    @model_validator(mode="after")
    def _guard(self) -> "OfficialBenchmarkCandidate":
        if self.hardware_binding == "unbound":
            if self.hardware_model is not None or self.deployment_gpu_count is not None:
                raise ValueError(
                    "hardware_binding=unbound 时 hardware_model 与 deployment_gpu_count 必须为空，"
                    "不得把候选配置写成已绑定配置"
                )
        absent = self.absent_required_fields()
        # 缺必需字段 -> 不得声称 verified
        if absent and self.verification_status == "verified":
            raise ValueError(
                f"缺少完整比对必需字段 {absent}，verification_status 不能为 'verified'"
            )
        # 缺口台账必须与实际状态一致
        missing_config = set(absent) & set(REQUIRED_CONFIG_FIELDS)
        for f in self.missing_fields:
            got = getattr(self, f, None)
            if f in missing_config and got is not None:
                raise ValueError(f"字段 {f!r} 已登记为缺失，却存在取值：{got!r}")
        if not absent and not self.missing_fields:
            pass
        elif set(self.missing_fields) != set(absent):
            raise ValueError(
                f"missing_fields 必须与实测缺口严格一致：声明 {sorted(self.missing_fields)} "
                f"vs 实际 {sorted(absent)}"
            )
        # 单请求样例必须显式标注
        if self.metrics.get("success_count") == 1:
            if self.is_throughput_benchmark is not False:
                raise ValueError("success_count=1 的单请求样例必须显式标记 is_throughput_benchmark=False")
            if "single_request_smoke_test" not in self.quality_flags:
                raise ValueError("成功请求数为 1 时必须带 single_request_smoke_test 质量标记")
        return self

    def metric(self, name: str) -> Decimal | None:
        return self.metrics.get(name)


# ---------------------------------------------------------------------------
# SGLang Cookbook 官方**数值**候选（M7）
# ---------------------------------------------------------------------------
#
# 与上面的 manifest 候选的区别：这类来源**明确给出了完整测试命令与原始控制台输出**
# （含模型、硬件、卡数、TP/EP、负载参数与全部指标），因此可以逐字段机器核对；
# 但**仍缺 model revision / 完整环境 pin / 精度**，所以依旧是 ``partial_evidence``，
# 不允许自动升级为可复现的 ``official_benchmark``。

#: 记录级核对结果：把"JSON 里的数"与"原文控制台输出的数"逐字段对上
class CandidateVerification(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_section: str
    source_block_found: bool = False
    command_found: bool = False
    compared_fields: list[str] = Field(default_factory=list)
    matched_fields: list[str] = Field(default_factory=list)
    #: 形如 ``"input_token_throughput_tps: json=140.07 source=140.08"``
    mismatches: list[str] = Field(default_factory=list)
    #: 正文叙述与测试命令冲突（如叙述 ShareGPT、命令为 --dataset-name random）
    command_conflicts: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)

    @property
    def verified_against_source(self) -> bool:
        return (
            self.source_block_found
            and self.command_found
            and bool(self.compared_fields)
            and not self.mismatches
        )


class SglangNumericCandidate(BaseModel):
    """官方公开的**数值型** Benchmark 候选（来源已存档、已逐字段核对）。"""

    model_config = ConfigDict(extra="forbid")

    # ---- 标识 ------------------------------------------------------------
    candidate_id: str
    family: Literal["sglang_numeric_cookbook"] = "sglang_numeric_cookbook"
    record_id: str
    source_id: str
    source_section: str
    evidence_id: str

    # ---- 来源与证据 ------------------------------------------------------
    publisher: str
    source_url: str | None = None
    source_path: str | None = None
    source_git_blob_sha: str | None = None
    evidence_file: str | None = None
    evidence_sha256: str | None = None
    accessed_at: datetime | None = None
    evidence_type: str = "official_project_published_benchmark"
    quality_status: Literal["partial_evidence"] = "partial_evidence"
    #: 本阶段任何候选都**不得**作为 SLA 承诺依据
    eligible_for_sla_guarantee: bool = False

    # ---- 模型 / 环境 -----------------------------------------------------
    model_name: str | None = None
    model_revision: str | None = None
    model_precision: str | None = None
    framework: str | None = None
    framework_version: str | None = None
    backend_version: str | None = None
    hardware_model: str | None = None
    deployment_gpu_count: int | None = None
    tp_size: int | None = None
    ep_size: int | None = None
    dp_size: int | None = None
    pd_mode: str | None = None

    # ---- 负载 ------------------------------------------------------------
    scenario_label: str | None = None
    dataset_name: str | None = None
    input_len_target: int | None = None
    output_len_target: int | None = None
    configured_max_concurrency: int | None = None
    num_prompts: int | None = None
    #: ``"inf"`` 等符号值原样保存在这里，**不**进入 ``metrics``，更不参与财务计算
    traffic_request_rate_raw: str | None = None

    # ---- 测量 ------------------------------------------------------------
    #: 已核对后的指标（优先取原文控制台输出；原文缺失时才回落到候选文件，并打标记）
    metrics: dict[str, Decimal] = Field(default_factory=dict)
    #: 候选文件里**原始提供的**数值，保留用于逐项对照（不参与展示口径）
    metrics_from_candidate_file: dict[str, Decimal] = Field(default_factory=dict)
    raw_labels: dict[str, str] = Field(default_factory=dict)
    symbols: dict[str, str] = Field(default_factory=dict)
    #: 字段 -> 指标口径 ID（见 docs/benchmark_metric_dictionary.md）
    metric_definition_ids: dict[str, str] = Field(default_factory=dict)
    metric_semantics_version: str = "1.0"
    #: 吞吐一致性校验（``吞吐 ≈ 累计 token / 时长``）的逐项结果
    throughput_consistency: list[dict[str, Any]] = Field(default_factory=list)
    #: 口径可疑点（如 TPOT 与 ITL 取值完全相同，需人工确认是否被混用）
    semantic_warnings: list[str] = Field(default_factory=list)

    #: 是否为真正的吞吐基准。``None`` = 无成功数无法判定；``False`` = 单请求冒烟测试。
    is_throughput_benchmark: bool | None = None

    # ---- 原始性 ----------------------------------------------------------
    launch_command: str | None = None
    #: **权威**测试命令：来自已逐字节校验的官方原文
    benchmark_command: str | None = None
    #: 候选 JSON 自带的命令，单独保留用于差异检查（F5 第 4 条）
    benchmark_command_from_candidate_file: str | None = None
    verification: CandidateVerification

    # ---- M9 门禁（派生，不接受人工填写）----------------------------------
    can_reference_performance: bool = False
    can_evaluate_sla: bool = False
    can_calculate_pricing: bool = False
    commercial_quote_allowed: bool = False
    applicability_notes: list[str] = Field(default_factory=list)

    # ---- 缺口台账 --------------------------------------------------------
    present_fields: list[str] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)
    quality_flags: list[str] = Field(default_factory=list)

    @property
    def hardware_binding(self) -> str:
        """硬件绑定状态，由 ``hardware_model`` 派生（F6：不在别处硬编码）。"""
        return "bound" if self.hardware_model else "unbound"

    @model_validator(mode="after")
    def _guard(self) -> "SglangNumericCandidate":
        for key, value in self.metrics.items():
            if not isinstance(value, Decimal) or not value.is_finite():
                raise ValueError(
                    f"metrics[{key!r}] 必须是有限 Decimal：符号值（如 inf）应留在 symbols/traffic_request_rate_raw"
                )
        if self.model_revision in (None, "") and "model_revision" not in self.missing_fields:
            raise ValueError("model_revision 缺失时必须登记在 missing_fields")
        if self.can_evaluate_sla and self.model_revision in (None, ""):
            raise ValueError("缺 model_revision 的记录不得声称 can_evaluate_sla")
        if self.eligible_for_sla_guarantee:
            raise ValueError("第一阶段候选不得标记 eligible_for_sla_guarantee=True")
        if self.commercial_quote_allowed:
            raise ValueError("第一阶段不产出正式商业报价，commercial_quote_allowed 必须为 False")
        if self.quality_status == "verified" and not self.verification.verified_against_source:
            raise ValueError("未通过原文逐字段核对的记录不得标记 verified")
        success = self.metrics.get("success_count")
        if success is not None and success == 1:
            if self.is_throughput_benchmark is not False:
                raise ValueError("成功请求数为 1 的记录必须显式标记 is_throughput_benchmark=False")
            if "single_request_smoke_test" not in self.quality_flags:
                raise ValueError("成功请求数为 1 时必须带 single_request_smoke_test 质量标记")
        return self

    def metric(self, name: str) -> Decimal | None:
        return self.metrics.get(name)
