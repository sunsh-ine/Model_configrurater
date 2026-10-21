"""V 阶段：Agent 输入与任务结果契约。

OpenCode 只负责编排；这里的结构化结果才是业务系统的可信来源。
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

CORE_FIELDS = {
    "model_id": "模型",
    "hardware_id": "GPU 型号",
    "gpu_count": "GPU 上限（卡）",
    "input_len": "输入长度 ISL",
    "output_len": "输出长度 OSL",
    "concurrency": "并发",
}


class AgentPlanInput(BaseModel):
    """OpenCode 调用业务工具时提交的结构化需求。

    全部字段可选：缺字段时工具返回 ``needs_confirmation``，由 Agent 向用户追问，
    而不是猜测或填默认值。
    """

    model_config = ConfigDict(extra="forbid")

    model_id: str | None = None
    hardware_id: str | None = None
    server_id: str | None = None
    gpu_count: int | None = Field(default=None, ge=1, le=64)
    input_len: int | None = Field(default=None, ge=1, le=131072)
    output_len: int | None = Field(default=None, ge=1, le=8192)
    concurrency: int | None = Field(default=None, ge=1, le=1024)
    ttft_p99_ms_max: Decimal | None = Field(default=None, gt=0)
    tpot_p99_ms_max: Decimal | None = Field(default=None, gt=0)
    budget_amount: Decimal | None = Field(default=None, ge=0)
    budget_currency: str | None = None
    weight_precision: Literal["bf16", "fp8"] | None = None
    framework: str | None = None
    framework_version: str | None = None
    ranking: Literal["throughput", "cost"] | None = None


class AgentTaskResult(BaseModel):
    """一次 Agent 任务的业务契约（由 Python 工具写入，不采信模型文本）。"""

    model_config = ConfigDict(extra="forbid")

    status: Literal["completed", "needs_confirmation", "failed"]
    session_id: str | None = None
    run_id: str | None = None
    request_id: str | None = None
    plan_result_available: bool = False
    needs_confirmation: bool = False
    summary: str = ""
    missing_fields: list[str] = Field(default_factory=list)
    error: str | None = None
    artifacts: dict[str, str] = Field(default_factory=dict)
    conclusions: dict[str, Any] = Field(default_factory=dict)
    #: result.json 的 sha256：防止任务文件被改指向其他历史报告
    result_sha256: str | None = None
    #: 本次分析采用的默认假设（框架/精度/成本币种等）
    assumptions: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _guard(self) -> "AgentTaskResult":
        if self.needs_confirmation != (self.status == "needs_confirmation"):
            raise ValueError("needs_confirmation 必须与 status 一致")
        if self.status == "completed":
            if not self.run_id or not self.plan_result_available:
                raise ValueError("completed 必须提供 run_id 与 plan_result_available")
            if "json" not in self.artifacts:
                raise ValueError("completed 必须引用 result.json")
            if not self.result_sha256:
                raise ValueError("completed 必须绑定 result.json 的 sha256")
        if self.status == "needs_confirmation" and not self.missing_fields:
            raise ValueError("needs_confirmation 必须列出缺失字段")
        if self.status == "failed" and not self.error:
            raise ValueError("failed 必须给出原因")
        return self


def missing_core_fields(values: AgentPlanInput) -> list[str]:
    return [
        label
        for field_name, label in CORE_FIELDS.items()
        if getattr(values, field_name) is None
    ]
