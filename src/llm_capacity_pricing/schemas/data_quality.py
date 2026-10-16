"""M36：数据质量分级（真实数据接入前的自检）。

只做一件事：把库里已有的记录按**证据可用性**分成五类，并给出可执行的补数据指引。
**不新建平行的数据格式** —— 分级结果只引用既有对象（ModelSpec / HardwareSpec /
BenchmarkRun / CostCatalogItem / BillingRule），字段来源都是既有权威定义。

五类状态的语义（计划 §M36）：

============================  ==================================================
``complete``                  字段齐备、来源可追溯、可用于生产级结论
``unverified``                来源真实但尚未核实（如厂商页面提到但未逐字段核对）
``missing_fields``            字段缺失（列出具体缺什么，缺什么就补什么）
``not_applicable``            该条数据在当前配置/口径下不适用（如价格已失效）
``synthetic``                 人工构造的测试数据，只用于验证算法
============================  ==================================================

**默认不乐观**：只有同时满足"字段齐备 + 来源属于测量类可信等级"才是
``production_grade``；其余一律可用于展示/建模，但不得支撑生产结论。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

#: 质量分级
DataQualityStatus = Literal[
    "complete",
    "unverified",
    "missing_fields",
    "not_applicable",
    "synthetic",
]

#: 数据域（与既有对象一一对应，不引入新的数据种类）
DataDomain = Literal[
    "model",
    "hardware",
    "server",
    "benchmark_run",
    "cost_item",
    "billing_rule",
    "sla_evidence",
    "approval",
]


class DataQualityItem(BaseModel):
    """单条记录的质量结论。"""

    model_config = ConfigDict(extra="forbid")

    domain: DataDomain
    record_id: str
    status: DataQualityStatus
    #: 具体缺什么 / 为什么不适用（可执行：照着补即可）
    reasons: list[str] = Field(default_factory=list)
    #: 是否可用于**生产级**结论
    production_grade: bool = False
    #: 该条数据能用来做什么（沿用既有语义：reference / modeling / sla_proof …）
    usable_for: list[str] = Field(default_factory=list)
    details: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _guard(self) -> "DataQualityItem":
        if self.production_grade and self.status != "complete":
            raise ValueError("只有 complete 的记录可以是 production_grade")
        if self.status in ("missing_fields", "not_applicable") and not self.reasons:
            raise ValueError(f"{self.status} 必须列出具体原因")
        return self


class DataQualityReport(BaseModel):
    """整库的数据质量报告（M36）。"""

    model_config = ConfigDict(extra="forbid")

    #: 域 -> 状态 -> 条数
    counts: dict[str, dict[str, int]] = Field(default_factory=dict)
    items: list[DataQualityItem] = Field(default_factory=list)
    #: 可执行的补数据指引（按域给，不含具体数值）
    gaps: list[str] = Field(default_factory=list)
    #: 一句话结论
    summary: str
    #: 该报告本身不是证据：它不改变任何记录的信任等级
    notice: str = (
        "本报告只读：不修改任何记录的信任等级，也不把 synthetic/未验证数据升级为生产级。"
    )
