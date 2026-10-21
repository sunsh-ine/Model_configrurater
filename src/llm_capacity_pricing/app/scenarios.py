"""T 阶段：三个可复现的业务演示场景。

场景只描述**需求输入**，不包含任何预期结果；UI 与 CLI 都调用
``run_demo()``/``evaluate_plan()`` 重新计算，不能把结论写死。

三个场景：

* ``strict``  —— 严格 SLA（400/30 ms），归档预测未达标；
* ``relaxed`` —— 宽松 SLA（演示阈值 6000/40 ms），归档预测达标；
* ``cost``    —— 在宽松 SLA 上启用成本优先排序，演示合成成本的口径与门禁。

``relaxed`` 的阈值只是**演示阈值**，不是客户真实需求；报告会带演示说明。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

#: 严格 SLA 的标准需求（也是 CLI/UI 的默认值）
STRICT_INPUTS: dict[str, Any] = {
    "model_id": "qwen3-32b",
    "hardware_id": "nvidia-h100-sxm-80gb",
    "server_id": None,
    "gpu_count": 8,
    "input_len": 8192,
    "output_len": 256,
    "concurrency": 32,
    "ttft_p99_ms_max": Decimal("400"),
    "tpot_p99_ms_max": Decimal("30"),
    "budget_amount": Decimal("3000000"),
    "weight_precision": "bf16",
    "framework": "vllm",
    "framework_version": "0.24.0",
    "mode": "offline",
    "ranking": "throughput",
    "request_id": "demo-strict-sla",
    "top_n": 2,
}


@dataclass(frozen=True)
class DemoScenario:
    scenario_id: str
    label: str
    summary: str
    note: str | None
    inputs: dict[str, Any] = field(default_factory=dict)


SCENARIOS: tuple[DemoScenario, ...] = (
    DemoScenario(
        scenario_id="strict",
        label="严格 SLA：预测未达标",
        summary="TTFT 400 ms / TPOT 30 ms；严格阈值下的离线分析。",
        note=(
            "演示案例（严格 SLA）：TTFT P99 ≤ 400 ms、TPOT P99 ≤ 30 ms。"
            "归档模拟在此阈值下预测未达标，这是预期结果，不是执行失败；"
            "预测不等于实测。"
        ),
        inputs=dict(STRICT_INPUTS),
    ),
    DemoScenario(
        scenario_id="relaxed",
        label="宽松 SLA：预测推荐",
        summary="演示阈值 6000 ms / 40 ms（仅改阈值，不是客户真实需求）。",
        note=(
            "演示案例（宽松 SLA）：使用演示阈值 TTFT P99 ≤ 6000 ms、TPOT P99 ≤ 40 ms，"
            "仅用于验证“预测达标 → 推荐资格 → 报告输出”链路。"
            "该阈值不代表客户真实需求，预测也不等于实测。"
        ),
        inputs={
            **STRICT_INPUTS,
            "ttft_p99_ms_max": Decimal("6000"),
            "tpot_p99_ms_max": Decimal("40"),
            "request_id": "demo-relaxed-sla",
        },
    ),
    DemoScenario(
        scenario_id="cost",
        label="成本与预算：经济性比较",
        summary="宽松 SLA + 成本优先；成本为合成示例。",
        note=(
            "演示案例（成本与预算）：使用仓库内合成成本示例（非真实报价）。"
            "成本项覆盖完整，但价格为合成、币种未确认，因此不给出正式“成本最优”结论；"
            "成本排序降级为吞吐优先，预算结论保持未知。"
        ),
        inputs={
            **STRICT_INPUTS,
            "ttft_p99_ms_max": Decimal("6000"),
            "tpot_p99_ms_max": Decimal("40"),
            "budget_amount": Decimal("1000000"),
            "ranking": "cost",
            "request_id": "demo-cost",
        },
    ),
)

_BY_ID = {scenario.scenario_id: scenario for scenario in SCENARIOS}


def scenario_ids() -> tuple[str, ...]:
    return tuple(scenario.scenario_id for scenario in SCENARIOS)


def get_scenario(scenario_id: str) -> DemoScenario:
    try:
        return _BY_ID[scenario_id]
    except KeyError as exc:
        raise ValueError(
            f"未知场景 {scenario_id}；可选：{', '.join(scenario_ids())}"
        ) from exc


#: DemoInputs 字段 → Streamlit 控件 key
FORM_KEY_MAP = {
    "model_id": "ui_model",
    "hardware_id": "ui_hardware",
    "gpu_count": "ui_gpu",
    "input_len": "ui_isl",
    "output_len": "ui_osl",
    "concurrency": "ui_conc",
    "ttft_p99_ms_max": "ui_ttft",
    "tpot_p99_ms_max": "ui_tpot",
    "budget_amount": "ui_budget",
    "weight_precision": "ui_precision",
    "mode": "ui_mode",
    "ranking": "ui_ranking",
}


def form_state(scenario_id: str) -> dict[str, Any]:
    """把场景输入映射成 Streamlit 控件状态（只填需求字段）。"""
    scenario = get_scenario(scenario_id)
    state: dict[str, Any] = {}
    for field_name, widget_key in FORM_KEY_MAP.items():
        if field_name not in scenario.inputs:
            continue
        value = scenario.inputs[field_name]
        if value is None:
            continue
        # Streamlit 的 number_input 需要 float/int，不能直接吃 Decimal
        state[widget_key] = float(value) if isinstance(value, Decimal) else value
    # 场景都有 SLA 与预算；自定义场景不走这里
    state["ui_sla_on"] = scenario.inputs.get("ttft_p99_ms_max") is not None
    state["ui_budget_on"] = scenario.inputs.get("budget_amount") is not None
    return state
