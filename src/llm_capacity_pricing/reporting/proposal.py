"""S4：面向评审/客户的部署方案文档（7 段式）。

与 ``plan_report.py``（技术版）的分工：

* 技术版：面向复核，保留 Run ID、匹配等级、sha256、逐项阻断原因；
* **本文件**：面向技术评审与客户沟通，按固定七段组织，每个数字都标注它属于
  模拟预测 / 公开实测 / 真实实测 / 假设成本。

硬约束（与任务书一致）：

* 只读取 ``PlanResult`` 的结构化字段，**不做任何二次计算**，也不调用大模型；
* 缺数据写"未提供/证据不足"，不补 0、不猜测；
* ``predicted_*`` 与 ``observed_*`` 分节呈现，措辞上不互相代替。
"""

from __future__ import annotations

from typing import Any

from llm_capacity_pricing.reporting.plan_report import (
    _COMPARABILITY_LABEL,
    _SLA_LABEL,
    _data_kind,
    _fmt,
    _match_label,
    _sla_label,
    _static_label,
)

_SIM_VERDICT = {
    "predicted_pass": "模拟预测满足阈值",
    "predicted_fail": "模拟预测**未**满足阈值",
    "insufficient_prediction": "模拟预测不足以下结论",
}


def build_deployment_proposal(result: Any) -> str:
    """把 ``PlanResult`` 渲染成 7 段式方案文档（Markdown）。"""
    L: list[str] = []

    def add(line: str = "") -> None:
        L.append(line)

    echo = result.request_echo
    workload = echo.get("workload") or {}
    sla = echo.get("sla") or {}

    add("# 推理部署方案")
    add("")
    add(
        "> 本文档由系统按结构化计算结果生成。**模拟预测不等于实测**；"
        "没有真实测量支撑的结论一律标注为「证据不足」。"
    )
    add("")

    # ---- 1. 客户需求概览 ----
    add("## 1. 客户需求概览")
    add("")
    add("| 项 | 值 |")
    add("|---|---|")
    add(f"| 模型 | `{_fmt(echo.get('model_id'))}`（{_fmt(echo.get('model_repo_id'))}） |")
    add(f"| 候选硬件 | {_fmt(echo.get('hardware_ids'))} |")
    if workload:
        add(
            f"| 业务负载 | 输入 {_fmt(workload.get('input_len'))} / "
            f"输出 {_fmt(workload.get('output_len'))} / "
            f"并发 {_fmt(workload.get('concurrency'))} |"
        )
    if sla:
        add(
            f"| SLA 要求 | TTFT P99 ≤ {_fmt(sla.get('ttft_p99_ms_max'))} ms，"
            f"TPOT P99 ≤ {_fmt(sla.get('tpot_p99_ms_max'))} ms |"
        )
    else:
        add("| SLA 要求 | 未提供（性能领域不会给出达标结论） |")
    budget = echo.get("budget") or {}
    if budget:
        currency = budget.get("currency") or "（币种未声明）"
        add(
            f"| 预算 | {_fmt(budget.get('amount'))} {currency}"
            f"（口径 {_fmt(budget.get('kind'))}） |"
        )
    else:
        add("| 预算 | 未提供 |")
    if echo.get("max_total_gpus") is not None:
        add(f"| 可用 GPU 上限 | {echo['max_total_gpus']} 卡 |")
    add(f"| 候选数量 | {len(result.candidates)} |")
    add("")

    # ---- 2. 模型与硬件资源分析 ----
    add("## 2. 模型与硬件资源分析")
    add("")
    if not result.candidates:
        add("没有生成候选（见第 7 节的阻断原因）。")
        add("")
    else:
        add("| 候选 | 卡数 | TP×DP | 静态结论 | 单卡余量 (B) |")
        add("|---|---|---|---|---|")
        for c in result.candidates:
            add(
                f"| `{c.candidate_id}` | {c.gpu_count} | {c.tp_size}×{c.dp_size} "
                f"| {_static_label(c.static_status)} | {_fmt(c.headroom_bytes)} |"
            )
        add("")
        add("> 静态结论只说明「装不装得下」，**不含**任何性能判断。")
        add("")

    # ---- 3. 候选部署配置 ----
    add("## 3. 候选部署配置")
    add("")
    if result.candidates:
        add("| 候选 | 静态 | 性能结论 | 数据性质 | 证据匹配等级 | 成本(全周期) | 报价 |")
        add("|---|---|---|---|---|---|---|")
        for c in result.candidates:
            add(
                f"| `{c.candidate_id}` | {_static_label(c.static_status)} "
                f"| {_sla_label(c)} | {_data_kind(c, 'performance')} "
                f"| {_match_label(c.match_level)} "
                f"| {_fmt(c.total_cash_over_horizon)} {_fmt(c.currency)} "
                f"| {'可发布' if c.quote_publishable else '不可发布'} |"
            )
        add("")
    if result.external_references:
        add(
            f"另有 **{len(result.external_references)} 条公开实测参考**"
            "（官方/第三方公开发布），它们**不参与**本需求的判定，仅作行业对照；"
            "明细见技术报告第 2.1 节与 JSON。"
        )
        add("")

    # ---- 4. 性能预测与 SLA 判断 ----
    add("## 4. 性能预测与 SLA 判断")
    add("")
    add("**实测证据（`observed_*`）**：")
    add("")
    observed_any = False
    for c in result.candidates:
        if c.sla_status in ("observed_pass", "observed_fail"):
            observed_any = True
            add(
                f"- `{c.candidate_id}`：{_SLA_LABEL.get(c.sla_status, c.sla_status)}"
                f"（依据 {_fmt(c.evidence_run_ids)}）"
            )
    if not observed_any:
        add(
            "- 没有任何候选具备目标配置下的真实实测，因此**不给出实测达标结论**"
            "（状态为「证据不足」）。这是数据缺口，不是「未通过」。"
        )
    add("")

    simulation = getattr(result, "simulation", None)
    if simulation is not None:
        add("**模拟预测（`predicted_*`，不是实测）**：")
        add("")
        if getattr(simulation, "status", None) != "computed":
            add(f"- 模拟未产出结论：{_fmt(getattr(simulation, 'message', None))}")
            add("")
        else:
            judged = simulation.judged()
            if not judged:
                add(
                    "- 本次没有能与目标直接比较、且给出 P99 的模拟候选"
                    "（原因见技术报告第 2.2 节）。"
                )
                add("")
            else:
                add(
                    "| 候选 | 吞吐 tok/s | TTFT P99 | TPOT P99 | 模拟结论 | GPU | 并行 |"
                )
                add("|---|---|---|---|---|---|---|")
                for c in judged:
                    m = c.metrics
                    d = c.deployment
                    add(
                        f"| `{c.candidate_id}` | {_fmt(m.sim_output_throughput_tok_s)} "
                        f"| {_fmt(m.sim_p99_ttft_ms)} ms | {_fmt(m.sim_p99_tpot_ms)} ms "
                        f"| {_SIM_VERDICT.get(c.verdict, c.verdict)} "
                        f"| {_fmt(d.gpu_count)} "
                        f"| TP{_fmt(d.tp_size)}×PP{_fmt(d.pp_size)}×DP{_fmt(d.dp_size)} |"
                    )
                add("")
                add(
                    "> 模拟值来自 AISimulate 的性能模型，**不是**在这台机器上测出来的；"
                    "官方精度证据也不覆盖本模型（见第 7 节）。"
                )
                add("")
    else:
        add("**模拟预测**：本次未启用（`simulation` 未配置），因此没有预测值。")
        add("")

    # ---- 5. 成本与预算分析 ----
    add("## 5. 成本与预算分析")
    add("")
    any_cost = False
    for c in result.candidates:
        if c.economics_status != "computed":
            continue
        any_cost = True
        detail = (
            f"{_fmt(c.total_cash_over_horizon)} {_fmt(c.currency)}"
            if c.total_cash_over_horizon is not None
            else "**不完整，不给全周期总额**"
        )
        add(
            f"- `{c.candidate_id}`：{detail}"
            f"（口径 {_fmt(c.cost_basis)}，{_fmt(c.horizon_years)} 年）"
            f"；成本性质：{_data_kind(c, 'economics')}"
            + (f"；覆盖缺口 {c.coverage_gaps}" if c.coverage_gaps else "")
        )
    if not any_cost:
        add("- 成本领域未产出结论（未配置成本模板或库内无成本目录）。")
    add("")
    comparability = (result.summary or {}).get("cost_comparability") or {}
    if comparability:
        reference = comparability.get("reference_candidate_id")
        not_comparable = comparability.get("not_comparable") or {}
        add(f"成本可比性参照：`{_fmt(reference)}`")
        add("")
        if not_comparable:
            add("以下候选**不参与**成本高低比较：")
            add("")
            for cid, kind in not_comparable.items():
                add(f"- `{cid}`：{_COMPARABILITY_LABEL.get(kind, kind)}")
            add("")
    add(
        "> 本 Demo 的成本来自**假设成本目录**（`synthetic_example`），"
        "不是供应商报价；成本不完整时不得据此报价。"
    )
    add("")

    # ---- 6. 推荐配置及依据 ----
    add("## 6. 推荐配置及依据")
    add("")
    if result.recommendations:
        for item in result.recommendations:
            add(f"- {item}")
    else:
        add("- 没有形成推荐结论（见第 7 节）。")
    add("")
    if simulation is not None and getattr(simulation, "status", None) == "computed":
        recommended = simulation.recommended(getattr(simulation, "ranking", "throughput"))
        best_effort = simulation.best_effort()
        if recommended is not None:
            add(f"**模拟推荐配置**：`{recommended.candidate_id}`（预测满足阈值）")
        elif best_effort is not None:
            add(
                f"**未达标参考配置**：`{best_effort.candidate_id}`"
                "（预测**未**满足阈值，仅说明最好能到哪一步，**不是**达标建议）"
            )
        else:
            add("**无符合条件的模拟候选**：没有候选同时满足身份、资源、P99 与预算门槛。")
        add("")
    add(
        "> 推荐依据全部来自结构化计算结果；"
        "本系统不提供跨维度综合评分，也不使用大模型改写数值。"
    )
    add("")

    # ---- 7. 限制、风险和待验证事项 ----
    add("## 7. 限制、风险与待验证事项")
    add("")
    for item in result.limitations:
        add(f"- {item}")
    if result.blockers:
        add("")
        add("**阻断项**：")
        add("")
        for b in result.blockers:
            where = f"（{b.candidate_id}）" if b.candidate_id else ""
            add(f"- [{b.domain}] `{b.code}`{where}：{b.message}")
    if result.next_data_needed:
        add("")
        add("**建议补齐**：")
        add("")
        for item in result.next_data_needed:
            add(f"- {item}")
    add("")
    add(
        "> **风险提示**：模拟预测可为排序与排除明显不可行的配置提供依据，"
        "但不能替代目标配置下的真实压测；正式报价须经人工在受控流程中签发。"
    )
    add("")

    return "\n".join(L)
