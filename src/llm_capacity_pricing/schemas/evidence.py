"""公共溯源模型：任何数值都必须能追溯到来源、文件、工作表/行号或 URL。"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

SourceType = Literal[
    "internal_excel",
    "official_benchmark",
    "third_party",
    "internal_measurement",
    "derived",
    "assumption",
]

#: 测量来源归属。用于把"文件在我们内部"与"数据由我们实测"彻底分开：
#: ``internal_excel`` 只说明**载体**是内部 Excel，其内容属于历史外部来源。
MeasurementOrigin = Literal[
    "historical_external_third_party",  # 历史外部/第三方数据（如 InferenceX）
    "official_benchmark",  # 框架官方公开实测
    "internal_measurement",  # 我们自己的实测
    "synthetic_fixture",  # 人工构造，仅测试
    "derived_or_assumption",  # 派生或假设，非测量
]

#: 证据等级排序（数字越大越"硬"）。用于报告与对比时的显式降级说明。
EVIDENCE_STRENGTH: dict[str, int] = {
    "assumption": 0,
    "derived": 1,
    "internal_excel": 2,
    "third_party": 2,
    "official_benchmark": 3,
    "internal_measurement": 4,
}

#: ``source_type`` -> 允许的 ``measurement_origin``（防止把外部数据写成"内部实测"）
ALLOWED_ORIGINS: dict[str, tuple[str, ...]] = {
    "internal_excel": ("historical_external_third_party", "derived_or_assumption"),
    "third_party": ("historical_external_third_party",),
    "official_benchmark": ("official_benchmark",),
    "internal_measurement": ("internal_measurement",),
    "derived": ("derived_or_assumption",),
    "assumption": ("derived_or_assumption", "synthetic_fixture"),
}


class Evidence(BaseModel):
    """一条可追溯证据。

    不允许"靠模型名/策略字符串补齐"后宣称已核实：缺失字段一律留 ``None``，
    并在 ``quality_flags`` 中显式记录缺口。
    """

    model_config = ConfigDict(extra="forbid")

    evidence_id: str
    source_type: SourceType
    publisher: str | None = None
    source_url: str | None = None
    source_file: str | None = None
    source_sheet: str | None = None
    source_row: int | None = None
    source_version: str | None = None
    retrieved_at: datetime | None = None
    raw_sha256: str | None = None
    notes: str | None = None
    quality_flags: list[str] = Field(default_factory=list)

    #: 测量来源归属。默认按最保守的"历史外部/第三方"处理，绝不默认成内部实测。
    measurement_origin: MeasurementOrigin = "historical_external_third_party"
    #: 是否为自有实测。只有 ``measurement_origin == "internal_measurement"`` 才能为 True。
    is_internal_measurement: bool = False

    @model_validator(mode="after")
    def _check_origin_consistency(self) -> "Evidence":
        allowed = ALLOWED_ORIGINS[self.source_type]
        if self.measurement_origin not in allowed:
            raise ValueError(
                f"source_type={self.source_type!r} 不允许 measurement_origin="
                f"{self.measurement_origin!r}（允许: {allowed}）"
            )
        if self.is_internal_measurement != (self.measurement_origin == "internal_measurement"):
            raise ValueError(
                "is_internal_measurement 必须与 measurement_origin == 'internal_measurement' 一致；"
                f"当前 measurement_origin={self.measurement_origin!r}, "
                f"is_internal_measurement={self.is_internal_measurement}"
            )
        return self

    @property
    def strength(self) -> int:
        return EVIDENCE_STRENGTH[self.source_type]

    def location(self) -> str:
        """人类可读的定位串，用于报告。"""
        if self.source_url:
            return self.source_url
        parts: list[str] = []
        if self.source_file:
            parts.append(self.source_file)
        if self.source_sheet:
            parts.append(self.source_sheet)
        if self.source_row is not None:
            parts.append(f"row {self.source_row}")
        return "!".join(parts) if parts else "unknown"
