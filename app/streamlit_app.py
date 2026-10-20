"""S3：推理部署方案生成器（单页 Demo）。

运行：

    .venv/bin/pip install -r requirements-app.txt
    .venv/bin/streamlit run app/streamlit_app.py

约束（与任务书一致）：

* 界面**只做**输入、展示、触发；
* 所有计算都调用 ``llm_capacity_pricing.app.demo_service`` → 现有 ``evaluate_plan()``；
* 前端**不实现**任何 Sizing / SLA / TCO 公式；
* 没有账号、多租户、任务队列或审批后台。
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import streamlit as st

from llm_capacity_pricing.app.demo_service import (
    DemoInputs,
    run_demo,
    summary_lines,
)

ROOT = Path(__file__).resolve().parents[1]

st.set_page_config(page_title="推理部署方案生成器", layout="wide")
st.title("推理部署方案生成器")
st.caption(
    "填写需求 → 一键生成部署方案。"
    "**模拟预测不等于实测**；成本为示例目录；正式报价需人工在受控流程中签发。"
)


@st.cache_data(show_spinner=False)
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


models = _catalog("data/models/model_manifest.yaml") or ["qwen3-32b"]
hardwares = _catalog("data/hardware/hardware_catalog.yaml") or ["nvidia-h100-sxm-80gb"]

# ---------------------------------------------------------------- 1. 需求输入
st.subheader("1. 需求输入")
with st.form("requirements"):
    c1, c2, c3 = st.columns(3)
    model_id = c1.selectbox("模型", models, index=0)
    hardware_id = c2.selectbox("GPU 型号", hardwares, index=0)
    gpu_count = c3.number_input("可用 GPU 上限（卡）", min_value=1, max_value=64, value=8)

    c4, c5, c6 = st.columns(3)
    input_len = c4.number_input("输入长度 (ISL)", min_value=1, value=8192)
    output_len = c5.number_input("输出长度 (OSL)", min_value=1, value=256)
    concurrency = c6.number_input("并发", min_value=1, value=32)

    c7, c8, c9 = st.columns(3)
    ttft = c7.number_input("TTFT P99 上限 (ms)", min_value=1.0, value=400.0)
    tpot = c8.number_input("TPOT P99 上限 (ms)", min_value=1.0, value=30.0)
    budget = c9.number_input("预算（元）", min_value=0.0, value=3_000_000.0)

    c10, c11, c12 = st.columns(3)
    mode_label = c10.selectbox(
        "模拟数据来源",
        [
            "离线归档（无需安装任何工具）",
            "实时模拟（需要 AISimulate 独立环境）",
            "不使用模拟",
        ],
        index=0,
    )
    precision = c11.selectbox("模型精度", ["bf16", "fp8"], index=0)
    ranking = c12.selectbox("排序目标", ["throughput", "cost"], index=0)

    submitted = st.form_submit_button("生成部署方案", type="primary")

mode = {
    "离线归档（无需安装任何工具）": "offline",
    "实时模拟（需要 AISimulate 独立环境）": "live",
    "不使用模拟": "core",
}[mode_label]

# ---------------------------------------------------------------- 2. 自动分析
if submitted:
    inputs = DemoInputs(
        model_id=model_id,
        hardware_id=hardware_id,
        gpu_count=int(gpu_count),
        input_len=int(input_len),
        output_len=int(output_len),
        concurrency=int(concurrency),
        ttft_p99_ms_max=Decimal(str(ttft)),
        tpot_p99_ms_max=Decimal(str(tpot)),
        budget_amount=Decimal(str(budget)) if budget else None,
        weight_precision=precision,
        framework="vllm",
        framework_version="0.24.0",
        mode=mode,  # type: ignore[arg-type]
        ranking=ranking,
        top_n=2,
    )
    with st.spinner("正在生成部署方案（建库 → 资源规划 → 模拟/实测 → 成本 → 报告）…"):
        try:
            st.session_state["outcome"] = run_demo(inputs)
            st.session_state["error"] = None
        except Exception as exc:  # noqa: BLE001 - 界面上只展示可读错误
            st.session_state["outcome"] = None
            st.session_state["error"] = f"{type(exc).__name__}: {exc}"

outcome = st.session_state.get("outcome")
error = st.session_state.get("error")

st.subheader("2. 自动分析")
if error:
    st.error(f"生成失败：{error}")
elif outcome is None:
    st.info("填写上面的需求后点击「生成部署方案」。")
else:
    for name, status, detail in outcome.stages:
        (st.success if status == "ok" else st.warning)(f"{name}：{status} — {detail}")
    for line in summary_lines(outcome):
        st.write(f"- {line}")

# ---------------------------------------------------------------- 3. 方案结果
st.subheader("3. 方案结果")
if outcome is not None and not error:
    result = outcome.result
    sim = getattr(result, "simulation", None)

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("整体状态", result.status)
    m2.metric("候选数", len(result.candidates))
    m3.metric("模拟状态", getattr(sim, "status", "未启用"))
    judged = sim.judged() if sim is not None and getattr(sim, "status", None) == "computed" else []
    m4.metric("可判定的模拟候选", len(judged))

    st.markdown("**候选部署配置**（静态可行性 / 性能结论 / 成本）")
    st.dataframe(
        [
            {
                "候选": c.candidate_id,
                "卡数": c.gpu_count,
                "TP×DP": f"{c.tp_size}×{c.dp_size}",
                "静态": c.static_status,
                "SLA": c.sla_status or "—",
                "成本(全周期)": str(c.total_cash_over_horizon) if c.total_cash_over_horizon else "不完整",
                "报价": "可发布" if c.quote_publishable else "不可发布",
            }
            for c in result.candidates
        ],
        use_container_width=True,
        hide_index=True,
    )

    if sim is not None and getattr(sim, "status", None) == "computed":
        st.markdown("**模拟预测（不是实测）**")
        if judged:
            st.dataframe(
                [
                    {
                        "候选": c.candidate_id,
                        "GPU": c.deployment.gpu_count,
                        "并行": c.deployment.parallelism_label,
                        "吞吐 tok/s": str(c.metrics.sim_output_throughput_tok_s or "—"),
                        "TTFT P99 ms": str(c.metrics.sim_p99_ttft_ms or "—"),
                        "TPOT P99 ms": str(c.metrics.sim_p99_tpot_ms or "—"),
                        "结论": c.verdict,
                        "静态可行": c.resource_feasible,
                    }
                    for c in judged
                ],
                use_container_width=True,
                hide_index=True,
            )
        recommended = sim.recommended(getattr(sim, "ranking", "throughput"))
        best = sim.best_effort()
        if recommended is not None:
            st.success(f"模拟推荐：{recommended.candidate_id}（预测满足阈值）")
        elif best is not None:
            st.warning(
                f"未达标参考配置：{best.candidate_id}"
                "（预测未满足阈值，只用来说明最好能到哪一步，不是达标建议）"
            )
        else:
            st.warning("无符合条件的模拟候选（身份/资源/P99/预算门槛未通过）。")
        with st.expander("模拟数据的限制"):
            for limitation in sim.limitations:
                st.write(f"- {limitation}")

    with st.expander("推荐依据与限制"):
        for item in result.recommendations:
            st.write(f"- {item}")
        for item in result.limitations:
            st.write(f"- {item}")

# ---------------------------------------------------------------- 4. 报告导出
st.subheader("4. 报告导出")
if outcome is not None and not error:
    d1, d2 = st.columns(2)
    d1.download_button(
        "下载方案文档（Markdown）",
        data=outcome.proposal_md.encode("utf-8"),
        file_name="deployment_proposal.md",
        mime="text/markdown",
        use_container_width=True,
    )
    d2.download_button(
        "下载结构化结果（JSON）",
        data=json.dumps(outcome.result_json, ensure_ascii=False, indent=2).encode("utf-8"),
        file_name="result.json",
        mime="application/json",
        use_container_width=True,
    )
    st.caption(f"本次运行的产物目录：{outcome.out_dir}")
    with st.expander("方案文档预览"):
        st.markdown(outcome.proposal_md)
else:
    st.caption("生成方案后可在此下载 Markdown 与 JSON。")

st.divider()
st.caption(
    "边界：模拟预测不是实测，不会写入实测证据；成本为示例目录；"
    "生产报价与审批发布保持禁用。"
)
