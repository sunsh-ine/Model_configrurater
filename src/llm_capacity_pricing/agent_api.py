"""M33 / V4.1 F4.1：受限 Agent 接口。

约束：

* **只读**：不提供任何写库、写文件、改配置的操作；
* **白名单 + 参数白名单**：算子白名单之外一律报错；每个算子还显式声明它接受哪些参数，
  未声明的参数直接拒绝 —— "只读"不等于"参数可以随便传"，
  尤其报价资格只接受**证据引用**，不接受任何状态字符串；
* **不生成确认与审批**：商务确认与审批只能由人工写入；
* **不绕过门禁**：``phase4.quote_eligibility`` 复用同一个 fail-closed 门禁，
  且强制要求 ``sla_evaluation_id`` 与 ``approval_id``。

返回值一律是可 JSON 序列化的结构化结果，便于上层审计。
"""

from __future__ import annotations

from typing import Any, Callable, Mapping

from llm_capacity_pricing import services as _services
from llm_capacity_pricing.services import ServiceError

__all__ = ["AGENT_OPS", "AGENT_OP_PARAMS", "call_agent_op"]

#: 报价算子接受的参数：**只有引用与事实**，没有任何状态字符串
ALLOWED_QUOTE_PARAMS = frozenset(
    {
        "candidate_id",
        "requirement_id",
        "sla_evaluation_id",
        "approval_id",
        "confirmation_id",
        "facts",
        "plan_file",
        "template_id",
        "hardware_id",
        "gpu_count",
        "server_count",
        "scenario_id",
        "region",
        "as_of",
    }
)

#: 每个算子允许的关键字参数白名单（``db`` 由调用方注入，不在此列）
AGENT_OP_PARAMS: Mapping[str, frozenset[str]] = {
    "phase4.cost_catalog": frozenset(),
    "phase3.performance_inventory": frozenset(),
    "phase4.tco": frozenset(
        {
            "plan_file",
            "template_id",
            "candidate_id",
            "hardware_id",
            "gpu_count",
            "server_count",
            "tp_size",
            "dp_size",
            "annual_revenue",
            "revenue_basis",
            "region",
            "as_of_date",
        }
    ),
    "phase4.revenue": frozenset(
        {
            "demand_file",
            "scenario_id",
            "capacity",
            "requirement",
            "sla_limits",
            "policy",
            "as_of",
        }
    ),
    "phase4.quote_eligibility": ALLOWED_QUOTE_PARAMS,
    "phase4.decision": frozenset(
        {
            "requirement",
            "model_id",
            "hardware_id",
            "server_id",
            "plan_file",
            "template_id",
            "assumptions",
            "input_len_target",
            "output_len_target",
            "dataset_name",
            "demand_file",
            "scenario_id",
            "budgets",
        }
    ),
}


def _impl(op: str) -> Callable[..., dict[str, Any]]:
    if op == "phase4.cost_catalog":
        return _services.cost_catalog_summary
    if op == "phase3.performance_inventory":
        return _services.performance_inventory
    if op == "phase4.tco":
        return _services.tco_for
    if op == "phase4.revenue":
        return _services.revenue_for
    if op == "phase4.quote_eligibility":
        return _services.quote_eligibility_for
    if op == "phase4.decision":
        return _services.phase4_decision_for
    raise ServiceError(f"未实现的算子 {op!r}")  # pragma: no cover


#: 允许 Agent 调用的算子（白名单）。新增算子必须在此显式登记。
AGENT_OPS: Mapping[str, Callable[..., dict[str, Any]]] = {
    op: _impl(op) for op in AGENT_OP_PARAMS
}


def call_agent_op(op: str, *, db: str, **kwargs: Any) -> dict[str, Any]:
    """调用白名单内的只读算子。未知算子、未声明参数一律报错。"""
    handler = AGENT_OPS.get(op)
    if handler is None:
        raise ServiceError(
            f"未知 Agent 算子 {op!r}；可用：{sorted(AGENT_OPS)}"
            "（本接口只读且白名单化，不做猜测式调用）"
        )
    allowed = AGENT_OP_PARAMS[op]
    extra = sorted(set(kwargs) - allowed)
    if extra:
        raise ServiceError(
            f"算子 {op!r} 不接受参数 {extra}；允许的参数：{sorted(allowed)}"
            "（参数白名单用于阻止通过旁路参数影响结论）"
        )
    if op == "phase4.quote_eligibility":
        # 门禁只认证据引用；缺引用直接拒绝，不提供任何"默认通过"的便利
        required = ("candidate_id", "requirement_id", "sla_evaluation_id", "approval_id")
        missing = [k for k in required if not kwargs.get(k)]
        if missing:
            raise ServiceError(
                f"phase4.quote_eligibility 缺少证据引用 {missing}："
                "报价资格只接受持久化的 SLA 证据与审批记录，不接受状态字符串"
            )
    return handler(db, **kwargs)
