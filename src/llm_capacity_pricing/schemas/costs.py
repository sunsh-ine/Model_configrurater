"""成本与价格模型。

币种处理规则（任务书 §0.5）：现有 Excel 未显式写明价格币种，因此
``currency_assumption`` 必须写出悬而未决状态，不能默认当成已确认的人民币。
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class HardwarePrice(BaseModel):
    """GPU 摊销或租金价格。单位：万元 / 卡 / 年（按表头语境）。"""

    model_config = ConfigDict(extra="forbid")

    hardware_price_id: str
    gpu_model: str
    gpus_per_server: int | None = None  # GPU价格!B —— 每服务器卡数
    per_gpu_annual_cost_10k: Decimal | None = None
    currency: str | None = None
    currency_assumption: str | None = None
    cost_scope: str | None = None
    valid_from: date | None = None
    valid_to: date | None = None
    evidence_id: str

    @field_validator("per_gpu_annual_cost_10k")
    @classmethod
    def _non_negative(cls, v: Decimal | None) -> Decimal | None:
        if v is not None and v < 0:
            raise ValueError("per_gpu_annual_cost_10k 不允许为负值")
        return v


class TokenPrice(BaseModel):
    """模型 Token 参考单价。单位：货币单位 / 百万 Token（币种待确认）。"""

    model_config = ConfigDict(extra="forbid")

    token_price_id: str
    model_name: str
    price_cached_input_per_1m: Decimal | None = None
    price_uncached_input_per_1m: Decimal | None = None
    price_output_per_1m: Decimal | None = None
    currency: str | None = None
    currency_assumption: str | None = None
    effective_date: date | None = None
    evidence_id: str

    @field_validator(
        "price_cached_input_per_1m",
        "price_uncached_input_per_1m",
        "price_output_per_1m",
    )
    @classmethod
    def _non_negative(cls, v: Decimal | None) -> Decimal | None:
        if v is not None and v < 0:
            raise ValueError("Token 单价不允许为负值")
        return v
