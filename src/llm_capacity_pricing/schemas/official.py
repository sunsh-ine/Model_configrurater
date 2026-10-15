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
