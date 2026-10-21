"""S3 / V1.1：推理部署方案生成器（单页）。

界面只做输入、展示与触发；所有计算都走 ``demo_service`` → ``evaluate_plan()``。
V1.1 的页面组织：

* 首页只给：执行状态、关键结论、核心候选对比、报告下载；
* 六域链路、三线账本、性能/成本/模拟细节、推荐依据全部折叠；
* 展示层拆分到 ``ui_presenters.py``，样式在 ``ui_style.css``。
"""

from __future__ import annotations

import json
import sys
from decimal import Decimal
from pathlib import Path

import streamlit as st

APP_DIR = Path(__file__).resolve().parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from ui_presenters import (  # noqa: E402
    BUDGET,
    SLA,
    STATIC,
    VERDICT,
    candidates_table_html,
    cost_table_html,
    empty_html,
    ledger_html as _ledger,
    masthead_html,
    offline_mismatches,
    perf_table_html,
    recommendations_html,
    refs_html,
    result_head_html,
    rail_html,
    sim_html,
)
from ui_presenters import esc as _esc  # noqa: E402

from llm_capacity_pricing.app.demo_service import (  # noqa: E402
    DemoInputs,
    run_demo,
    server_for_hardware,
)
from llm_capacity_pricing.app.scenarios import (  # noqa: E402
    form_state,
    get_scenario,
    scenario_ids,
)

ROOT = APP_DIR.parent
COST_INPUTS = ROOT / "data/examples/phase4_cost_inputs.example.yaml"

MODE_LABELS = {
    "offline": "离线归档（无需安装工具）",
    "live": "实时模拟（调用 AISimulate）",
    "core": "不使用模拟",
}
RANKING_LABELS = {"throughput": "吞吐优先", "cost": "成本优先"}

st.set_page_config(
    page_title="推理部署方案生成器",
    page_icon="▦",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.markdown(
    f"<style>{(APP_DIR / 'ui_style.css').read_text(encoding='utf-8')}</style>",
    unsafe_allow_html=True,
)


# ---------------------------------------------------------------------------
# 只读目录与探针
# ---------------------------------------------------------------------------


def _catalog(path: str) -> list[str]:
    """只读数据目录，用于下拉选项（不做任何计算）。"""
    try:
        import yaml

        doc = yaml.safe_load((ROOT / path).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - 读不到就退回手填
        return []
    if isinstance(doc, dict):
        for key in ("models", "hardware", "servers"):
            if isinstance(doc.get(key), list):
                doc = doc[key]
                break
    if not isinstance(doc, list):
        return []
    out = []
    for entry in doc:
        if isinstance(entry, dict):
            value = entry.get("model_id") or entry.get("hardware_id") or entry.get("server_id")
            if value:
                out.append(str(value))
    return out


def _index_before(options: list[str], value: str) -> int:
    try:
        return options.index(value)
    except ValueError:
        return 0


def _cost_currency() -> str | None:
    try:
        import yaml

        doc = yaml.safe_load(COST_INPUTS.read_text(encoding="utf-8")) or {}
        return (doc.get("source") or {}).get("currency")
    except Exception:  # noqa: BLE001 - 读不到就按未知展示
        return None


@st.cache_data(show_spinner=False, ttl=300)
def _aisimulate_probe() -> tuple[str | None, bool]:
    """实时模式预检：解释器存在还不够，必须能真正导入两个包。

    只在用户选择“实时模拟”时调用，离线页面不做任何外部探测。
    """
    try:
        from llm_capacity_pricing.perf.aisimulate_adapter import (
            ExternalToolConfig,
            external_tool_available,
            resolve_python,
        )

        python = resolve_python(None)
        if python is None:
            return None, False
        return python, external_tool_available(
            ExternalToolConfig(python=python, enabled=True)
        )
    except Exception:  # noqa: BLE001 - 探测失败按不可用处理
        return None, False


def _matches_scenario(scenario, current: dict[str, object]) -> bool:
    """当前输入是否仍与预设一致；不一致时不附带预设说明，避免误导。"""
    if scenario is None:
        return False
    for field_name, actual in current.items():
        if field_name not in scenario.inputs:
            continue
        expected = scenario.inputs[field_name]
        if expected is None or actual is None:
            if expected is not actual:
                return False
            continue
        try:
            if Decimal(str(expected)) != Decimal(str(actual)):
                return False
        except Exception:  # noqa: BLE001 - 非数值字段按字符串比较
            if str(expected) != str(actual):
                return False
    return True


def _fingerprint(values: dict[str, object]) -> str:
    import json

    return json.dumps({key: str(value) for key, value in values.items()}, sort_keys=True)


# ---------------------------------------------------------------------------
# 页头与输入区
# ---------------------------------------------------------------------------

cost_currency = _cost_currency()
st.markdown(masthead_html(cost_currency), unsafe_allow_html=True)

models = _catalog("data/models/model_manifest.yaml") or ["qwen3-32b"]
hardwares = _catalog("data/hardware/hardware_catalog.yaml") or ["nvidia-h100-sxm-80gb"]

left, right = st.columns([0.36, 0.64], gap="large")

with left:
    st.markdown("<span class='main-left-marker'></span>", unsafe_allow_html=True)
    with st.container(border=True):
        st.markdown(
            "<div class='panel-head'><b>需求单</b>"
            "<small>一次评估对应一份需求</small></div>",
            unsafe_allow_html=True,
        )

        scenario_options = ["custom", *scenario_ids()]
        scenario_id = st.selectbox(
            "演示案例",
            scenario_options,
            index=1,
            format_func=lambda sid: (
                "自定义（保留当前输入）"
                if sid == "custom"
                else get_scenario(sid).label
            ),
            key="ui_scenario",
        )
        if st.session_state.get("ui_applied_scenario") != scenario_id:
            if scenario_id != "custom":
                for key, value in form_state(scenario_id).items():
                    st.session_state[key] = value
            st.session_state["ui_applied_scenario"] = scenario_id
        scenario = get_scenario(scenario_id) if scenario_id != "custom" else None
        if scenario is not None:
            st.markdown(
                f"<p class='hint'>{_esc(scenario.summary)}</p>",
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                "<p class='hint'>手动填写需求；切换预设会覆盖下面字段。</p>",
                unsafe_allow_html=True,
            )

        st.markdown("<div class='group first'>模型与硬件</div>", unsafe_allow_html=True)
        model_id = st.selectbox(
            "模型", models, index=_index_before(models, "qwen3-32b"), key="ui_model"
        )
        hardware_id = st.selectbox(
            "GPU 型号",
            hardwares,
            index=_index_before(hardwares, "nvidia-h100-sxm-80gb"),
            key="ui_hardware",
        )
        gpu_count = st.number_input(
            "GPU 上限（卡）", min_value=1, max_value=64, value=8, step=1, key="ui_gpu"
        )
        server_id = server_for_hardware(hardware_id)
        if server_id:
            server_hint = "机箱按 GPU 自动匹配为 <b>" + _esc(server_id) + "</b>。"
        else:
            server_hint = (
                "<span class='hint warn'>硬件目录里没有与该 GPU 匹配的机箱："
                "Sizing 会记录机箱档案缺失；若该 GPU 本身不在本阶段支持范围内，"
                "候选会直接判为不可行。</span>"
            )
        st.markdown(f"<p class='hint'>{server_hint}</p>", unsafe_allow_html=True)

        st.markdown("<div class='group'>负载</div>", unsafe_allow_html=True)
        w1, w2 = st.columns(2)
        input_len = w1.number_input(
            "输入长度 ISL",
            min_value=1,
            max_value=131072,
            value=8192,
            step=256,
            key="ui_isl",
        )
        output_len = w2.number_input(
            "输出长度 OSL",
            min_value=1,
            max_value=8192,
            value=256,
            step=16,
            key="ui_osl",
        )
        concurrency = st.number_input(
            "并发请求数",
            min_value=1,
            max_value=1024,
            value=32,
            step=1,
            key="ui_conc",
        )

        st.markdown("<div class='group'>SLA 阈值</div>", unsafe_allow_html=True)
        sla_on = st.toggle("设置 SLA 阈值", value=True, key="ui_sla_on")
        ttft_value: float | None = None
        tpot_value: float | None = None
        if sla_on:
            s1, s2 = st.columns(2)
            ttft_value = s1.number_input(
                "TTFT P99（ms）",
                min_value=1.0,
                value=400.0,
                step=10.0,
                format="%.1f",
                key="ui_ttft",
            )
            tpot_value = s2.number_input(
                "TPOT P99（ms）",
                min_value=1.0,
                value=30.0,
                step=1.0,
                format="%.1f",
                key="ui_tpot",
            )
        else:
            st.markdown(
                "<p class='hint'>不设阈值时，性能领域只给数据匹配结论，不判定达标与否。</p>",
                unsafe_allow_html=True,
            )

        st.markdown("<div class='group'>成本与预算</div>", unsafe_allow_html=True)
        budget_on = st.toggle("设置预算上限", value=True, key="ui_budget_on")
        budget_value: float | None = None
        if budget_on:
            budget_value = st.number_input(
                "预算上限（成本目录币种）",
                min_value=0.0,
                max_value=1e10,
                value=3_000_000.0,
                step=100_000.0,
                format="%.0f",
                key="ui_budget",
            )
        else:
            st.markdown(
                "<p class='hint'>不设预算：候选仍会算成本，但不做预算比对。</p>",
                unsafe_allow_html=True,
            )
        st.markdown(
            "<p class='hint'>预算只与同币种成本比较；示例目录币种状态为 assumed，"
            "预算结论保持未知，不用于正式报价判断。</p>",
            unsafe_allow_html=True,
        )

        with st.expander("高级设置（精度 / 排序 / 模拟）", expanded=False):
            precision = st.selectbox(
                "模型精度", ["bf16", "fp8"], index=0, key="ui_precision"
            )
            mode = st.selectbox(
                "模拟数据来源",
                ["offline", "live", "core"],
                index=0,
                format_func=lambda value: MODE_LABELS[value],
                key="ui_mode",
            )
            ranking = st.radio(
                "排序目标",
                ["throughput", "cost"],
                index=0,
                format_func=lambda value: RANKING_LABELS[value],
                horizontal=True,
                key="ui_ranking",
            )

            blocking_reason = None
            if mode == "offline":
                mismatches = offline_mismatches(
                    {
                        "model_id": model_id,
                        "hardware_id": hardware_id,
                        "framework": "vllm",
                        "framework_version": "0.24.0",
                        "weight_precision": precision,
                        "input_len": int(input_len),
                        "output_len": int(output_len),
                        "concurrency": int(concurrency),
                    }
                )
                if mismatches:
                    items = "".join(f"<li>{_esc(item)}</li>" for item in mismatches)
                    st.markdown(
                        "<p class='hint warn'>与离线归档工作点不一致：</p>"
                        f"<ul class='plain'>{items}</ul>"
                        "<p class='hint'>偏离任一项都会让模拟候选不可比或只能作参考。</p>",
                        unsafe_allow_html=True,
                    )
                else:
                    st.markdown(
                        "<p class='hint'>当前组合与归档工作点一致："
                        "Qwen3-32B / H100 SXM / vllm 0.24.0 / bf16 / "
                        "ISL 8192 / OSL 256 / 并发 32。</p>",
                        unsafe_allow_html=True,
                    )
            elif mode == "live":
                # 只有选择实时模式才做外部探测
                live_python, live_ready = _aisimulate_probe()
                if live_python and live_ready:
                    st.markdown(
                        "<p class='hint'>已检测到 AISimulate 环境：<b>"
                        + _esc(live_python)
                        + "</b>（导入检查通过）。搜索与回放会写入本次运行目录。</p>",
                        unsafe_allow_html=True,
                    )
                elif live_python:
                    blocking_reason = (
                        f"找到解释器 {live_python}，但无法导入 aiconfigurator / aisimulate："
                        "实时模式不可用，请修正环境或改用离线归档。"
                    )
                    st.markdown(
                        f"<p class='hint fail'>{_esc(blocking_reason)}</p>",
                        unsafe_allow_html=True,
                    )
                else:
                    blocking_reason = (
                        "未检测到 AISimulate：请设置 AISIMULATE_PYTHON 指向独立环境，"
                        "或改用离线归档。"
                    )
                    st.markdown(
                        f"<p class='hint fail'>{_esc(blocking_reason)}</p>",
                        unsafe_allow_html=True,
                    )
            else:
                st.markdown(
                    "<p class='hint'>不使用模拟：只做资源、实测参考与成本，"
                    "性能侧通常停在「不可评估」。</p>",
                    unsafe_allow_html=True,
                )

            if ranking == "cost":
                st.markdown(
                    "<p class='hint'>成本排序只在所有候选成本完整、币种已确认时生效；"
                    "否则自动降级为吞吐优先，并在结果里写明原因。</p>",
                    unsafe_allow_html=True,
                )

        preset_values = {
            "model_id": model_id,
            "hardware_id": hardware_id,
            "gpu_count": int(gpu_count),
            "input_len": int(input_len),
            "output_len": int(output_len),
            "concurrency": int(concurrency),
            "ttft_p99_ms_max": ttft_value if sla_on else None,
            "tpot_p99_ms_max": tpot_value if sla_on else None,
            "budget_amount": budget_value if budget_on else None,
            "weight_precision": precision,
            "mode": mode,
            "ranking": ranking,
        }
        preset_matches = _matches_scenario(scenario, preset_values)
        if scenario is not None and not preset_matches:
            st.markdown(
                "<p class='hint warn'>已手动修改预设字段：本次生成不会附带预设说明。</p>",
                unsafe_allow_html=True,
            )

        st.markdown("<div class='group'>生成</div>", unsafe_allow_html=True)
        clicked = st.button(
            "生成部署方案",
            type="primary",
            width="stretch",
            disabled=blocking_reason is not None,
        )
        st.markdown(
            "<p class='hint'>当前："
            + _esc(
                f"{precision} · {MODE_LABELS[mode]} · {RANKING_LABELS[ranking]}"
            )
            + "</p>",
            unsafe_allow_html=True,
        )

current_values = {
    "scenario": scenario_id,
    "model_id": model_id,
    "hardware_id": hardware_id,
    "gpu_count": gpu_count,
    "input_len": input_len,
    "output_len": output_len,
    "concurrency": concurrency,
    "sla_on": sla_on,
    "ttft": ttft_value,
    "tpot": tpot_value,
    "budget_on": budget_on,
    "budget": budget_value,
    "precision": precision,
    "mode": mode,
    "ranking": ranking,
}
current_fp = _fingerprint(current_values)

if clicked:
    request_inputs = DemoInputs(
        model_id=model_id,
        hardware_id=hardware_id,
        gpu_count=int(gpu_count),
        input_len=int(input_len),
        output_len=int(output_len),
        concurrency=int(concurrency),
        ttft_p99_ms_max=Decimal(str(ttft_value)) if sla_on else None,
        tpot_p99_ms_max=Decimal(str(tpot_value)) if sla_on else None,
        # 0 是真实约束（"不给预算"是另一回事），因此只由开关决定是否置空
        budget_amount=Decimal(str(budget_value)) if budget_on else None,
        weight_precision=precision,
        framework="vllm",
        framework_version="0.24.0",
        mode=mode,  # type: ignore[arg-type]
        ranking=ranking,
        top_n=2,
    )
    with st.spinner("正在生成：建库 → 资源规划 → 性能/模拟 → 成本 → 报告 …"):
        try:
            st.session_state["outcome"] = run_demo(
                request_inputs,
                demo_note=scenario.note if preset_matches else None,
                scenario_id=scenario_id if preset_matches else "custom",
            )
            st.session_state["error"] = None
            st.session_state["fingerprint"] = current_fp
        except Exception as exc:  # noqa: BLE001 - 界面上只展示可读错误
            # 失败不应清空上一次成功生成的方案
            st.session_state["error"] = f"{type(exc).__name__}: {exc}"

outcome = st.session_state.get("outcome")
error = st.session_state.get("error")
stale = outcome is not None and st.session_state.get("fingerprint") != current_fp


# ---------------------------------------------------------------------------
# 结果区：首页只留结论、核心对比、下载；细节全部折叠
# ---------------------------------------------------------------------------

with right:
    if error:
        keep_note = (
            "下方仍是上一次成功的评估结果，不会因为本次失败被清空。"
            if outcome is not None
            else "修正后重新生成。"
        )
        st.markdown(
            "<div class='callout fail'><b>生成失败</b><br>"
            + _esc(error)
            + "<br><span class='muted'>"
            + keep_note
            + "</span></div>",
            unsafe_allow_html=True,
        )
        st.markdown(
            "<p class='hint'>常见原因：实时模拟环境不可用、输出目录非空、"
            "或输入组合没有可用数据。</p>",
            unsafe_allow_html=True,
        )

    if outcome is not None:
        result = outcome.result
        sim = getattr(result, "simulation", None)
        st.markdown(result_head_html(result), unsafe_allow_html=True)
        if stale:
            st.markdown(
                "<div class='callout warn'>需求已改动：左侧当前值与这份结果对应的需求不同。"
                "重新生成会新建运行目录，不会覆盖这份结果。</div>",
                unsafe_allow_html=True,
            )

        st.markdown(
            "<div class='sec'><h3>核心候选对比</h3>"
            f"<span>{len(result.candidates)} 个候选</span></div>",
            unsafe_allow_html=True,
        )
        st.markdown(candidates_table_html(result), unsafe_allow_html=True)

        d1, d2, d3 = st.columns(3)
        d1.download_button(
            "部署方案文档",
            data=outcome.proposal_md.encode("utf-8"),
            file_name="deployment_proposal.md",
            mime="text/markdown",
            width="stretch",
        )
        d2.download_button(
            "技术评估报告",
            data=outcome.technical_md.encode("utf-8"),
            file_name="technical_report.md",
            mime="text/markdown",
            width="stretch",
        )
        d3.download_button(
            "结构化结果 JSON",
            data=json.dumps(
                outcome.result_json, ensure_ascii=False, indent=2
            ).encode("utf-8"),
            file_name="result.json",
            mime="application/json",
            width="stretch",
        )
        st.markdown(
            f"<p class='note'>产物目录：<b>{_esc(outcome.out_dir)}</b></p>",
            unsafe_allow_html=True,
        )

        with st.expander("评估链路与三线结论", expanded=False):
            st.markdown(
                rail_html(result, data_ok=bool(outcome.stages and outcome.stages[0][1] == "ok")),
                unsafe_allow_html=True,
            )
            st.markdown(_ledger(result), unsafe_allow_html=True)

        with st.expander("性能与 SLA 明细", expanded=False):
            st.markdown(perf_table_html(result), unsafe_allow_html=True)
            st.markdown(
                "<div class='sec'><h3>外部公开实测参考</h3>"
                "<span>不参与本次判定</span></div>",
                unsafe_allow_html=True,
            )
            st.markdown(refs_html(result), unsafe_allow_html=True)

        with st.expander("成本与预算", expanded=False):
            st.markdown(cost_table_html(result), unsafe_allow_html=True)

        with st.expander("模拟预测（不是实测）", expanded=False):
            st.markdown(sim_html(sim), unsafe_allow_html=True)

        with st.expander("推荐依据、阻断与适用边界", expanded=False):
            st.markdown(recommendations_html(result), unsafe_allow_html=True)

        with st.expander("部署方案文档预览", expanded=False):
            st.markdown(outcome.proposal_md)

    elif not error:
        st.markdown(empty_html(current_values, cost_currency), unsafe_allow_html=True)

st.markdown(
    "<p class='hint' style='margin-top:40px;border-top:1px solid #CFD5CB;padding-top:14px'>"
    "边界：模拟预测不是实测，不会写入实测证据；成本来自示例目录；"
    "生产报价与审批发布保持禁用。界面不实现任何计算，全部结论来自 evaluate_plan()。"
    "</p>",
    unsafe_allow_html=True,
)
