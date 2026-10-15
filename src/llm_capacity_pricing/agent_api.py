"""M33：受限 Agent 接口。

约束（任务书 §四/§五）：

* **只读**：本模块不提供任何写库、写文件、改配置的操作；
* **白名单**：只有 ``AGENT_OPS`` 里登记的算子可调用，未知算子直接报错，不做猜测；
* **不生成确认**：商务确认与审批只能由人工写入，Agent 无法通过本接口产生；
* **不绕过门禁**：``phase4.quote_eligibility`` 复用同一个 fail-closed 门禁函数，
  且 ``approval_status`` 默认 ``missing``，必须由调用方显式提供人工凭据。

返回值一律是可 JSON 序列化的结构化结果，便于上层审计。
"""

from __future__ import annotations

from typing import Any, Callable

from llm_capacity_pricing import services as _services
from llm_capacity_pricing.services import ServiceError

__all__ = ["AGENT_OPS", "call_agent_op"]


def _cost_catalog(db: str) -> dict[str, Any]:
    return _services.cost_catalog_summary(db)


def _tco(db: str, **kwargs: Any) -> dict[str, Any]:
    return _services.tco_for(db, **kwargs)


def _revenue(db: str, **kwargs: Any) -> dict[str, Any]:
    return _services.revenue_for(db, **kwargs)


def _quote_eligibility(db: str, **kwargs: Any) -> dict[str, Any]:
    return _services.quote_eligibility_for(db, **kwargs)


def _decision(db: str, **kwargs: Any) -> dict[str, Any]:
    return _services.phase4_decision_for(db, **kwargs)


def _performance_inventory(db: str) -> dict[str, Any]:
    return _services.performance_inventory(db)


#: 允许 Agent 调用的算子（白名单）。新增算子必须在此显式登记。
AGENT_OPS: dict[str, Callable[..., dict[str, Any]]] = {
    "phase4.cost_catalog": _cost_catalog,
    "phase4.tco": _tco,
    "phase4.revenue": _revenue,
    "phase4.quote_eligibility": _quote_eligibility,
    "phase4.decision": _decision,
    "phase3.performance_inventory": _performance_inventory,
}


def call_agent_op(op: str, *, db: str, **kwargs: Any) -> dict[str, Any]:
    """调用白名单内的只读算子。未知算子直接报错（不猜测、不降级）。"""
    handler = AGENT_OPS.get(op)
    if handler is None:
        raise ServiceError(
            f"未知 Agent 算子 {op!r}；可用：{sorted(AGENT_OPS)}"
            "（本接口只读且白名单化，不做猜测式调用）"
        )
    if op == "phase4.quote_eligibility":
        # 门禁默认值必须来自调用方；这里不提供任何"默认已审批"的便利
        kwargs.setdefault("approval_status", "missing")
    return handler(db, **kwargs)
