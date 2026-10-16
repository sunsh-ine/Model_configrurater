"""S1/S5：统一评估的用户报告（Markdown）。

面向"不想看数据库表、Run ID 和匹配等级"的使用者：先给结论与缺口，
再加一段可选的明细附录（证据、指标口径、阻断原因）。

报告纪律：

* 四个领域**分开写**，不合并成单一"通过/不通过"；
* 每个数字都标明它属于**实测 / 估算 / 假设 / 缺失**哪一类；
* 未配置的领域明确写"未评估"，不留空也不补默认值。
"""

from __future__ import annotations

from typing import Any

from llm_capacity_pricing.schemas.plan import PlanResult

#: 领域状态 -> 面向用户的说法
_DOMAIN_LABEL = {
    "computed": "已评估",
    "not_configured": "未配置（缺少输入）",
    "missing_data": "数据不足",
    "blocked": "被阻断",
    "error": "出错",
}
_DOMAIN_NAME = {
    "input": "输入与库存",
    "sizing": "资源规划",
    "performance": "性能与 SLA",
    "economics": "成本与收益",
    "governance": "报价资格",
}
_STATUS_LABEL = {"ok": "完整", "partial": "部分结论", "blocked": "未开始（输入有阻断）"}

#: 内部状态 -> 面向使用者的说法（报告正文不暴露内部枚举）
_SLA_LABEL = {
    "observed_pass": "实测达标",
    "observed_fail": "实测未达标",
    "estimated_only": "仅估算（不算达标）",
    "not_evaluable": "证据不足",
}
_BUCKET_LABEL = {
    "verified_sla_met": "已实测满足",
    "measured_but_sla_unmet": "实测不满足",
    "requires_benchmark": "需要补测",
    "excluded": "静态不可行",
}


def _sla_label(candidate: Any) -> str:
    if candidate.sla_status in _SLA_LABEL:
        return _SLA_LABEL[candidate.sla_status]
    if candidate.performance_status == "missing_data":
        return "缺少实测"
    if candidate.performance_status == "not_configured":
        return "未配置需求"
    return "未评估"


def _fmt(value: Any) -> str:
    return "—" if value is None else str(value)


_STATIC_LABEL = {
    "feasible": "可行",
    "conditional_feasible": "条件可行（需按假设复核）",
    "infeasible": "不可行",
    "insufficient_information": "信息不足",
    "unsupported": "本轮不支持",
}


def _static_label(status: str) -> str:
    return _STATIC_LABEL.get(status, status)


#: 成本不可比原因 -> 用户语言
_COMPARABILITY_LABEL = {
    "incomplete_cost": "成本不完整或金额缺失（不得按 0 排序）",
    "not_comparable_currency": "币种不同（不得直接比较金额）",
    "not_comparable_basis": "买断/租赁口径不同（不是同一口径）",
    "not_comparable_horizon": "计算年限不同（全周期总额不可直接比较）",
}


def _data_kind(candidate: Any, domain: str) -> str:
    """标注该领域结论的数据性质：实测 / 估算 / 假设 / 缺失。"""
    if domain == "performance":
        if candidate.sla_status == "observed_pass":
            return "实测"
        if candidate.sla_status in ("observed_fail",):
            return "实测"
        if candidate.sla_status == "estimated_only":
            return "估算"
        if candidate.performance_status != "computed":
            return "缺失"
        return "证据不足"
    if domain == "economics":
        if candidate.economics_status != "computed":
            return "缺失"
        if candidate.cost_incomplete:
            return "不完整（含未确认/缺失项）"
        return "已确认成本"
    if domain == "sizing":
        return "静态计算"
    if domain == "governance":
        if candidate.governance_status != "computed":
            return "未评估"
        return "已评估"
    return "—"


def build_plan_report(result: PlanResult) -> str:
    """把 ``PlanResult`` 渲染成一份面向用户的 Markdown 报告。"""
    L: list[str] = []

    def add(line: str = "") -> None:
        L.append(line)

    echo = result.request_echo
    add("# 方案评估报告")
    add("")
    add(f"- 请求：`{result.request_id}`")
    add(f"- 模型：`{echo.get('model_id')}`")
    add(f"- 候选硬件：{echo.get('hardware_ids')}")
    if echo.get("workload"):
        w = echo["workload"]
        add(
            f"- 目标负载：输入 {w.get('input_len')} / 输出 {w.get('output_len')} / "
            f"并发 {w.get('concurrency')}"
        )
    add(f"- 生成时间：{result.generated_at.isoformat(timespec='seconds')}")
    add("")

    # ---- 结论摘要 ----
    add("## 1. 结论摘要")
    add("")
    add(
        f"整体状态：**{_STATUS_LABEL.get(result.status, result.status)}**"
        "（只表示这次评估产出了什么，不是报价结论）"
    )
    add("")
    add("| 领域 | 状态 | 说明 |")
    add("|---|---|---|")
    for domain in ("input", "sizing", "performance", "economics", "governance"):
        status = result.domains.get(domain, "not_configured")
        add(f"| {_DOMAIN_NAME[domain]} | {_DOMAIN_LABEL.get(status, status)} | |")
    add("")
    publishable = [c.candidate_id for c in result.candidates if c.quote_publishable]
    # M35：**技术资格**与**生产发布授权**必须分开写 —— 前者是四维门禁的计算结果，
    # 后者取决于是否接入可信审批系统，本系统当前恒为"未授权"。
    add(
        "- **技术资格（四维门禁）**："
        + ("、" .join(f"`{c}`" for c in publishable) if publishable else "无候选通过")
        + "。这是成本/SLA/商务确认/审批四项证据计算的**计算结果**。"
    )
    add(
        "- **生产发布授权**：**未授权**。本系统未接入可信审批系统，"
        "审批记录只做持久化、不校验审批人身份与权限，因此"
        "**不得用于生产环境的报价自动发布**（正式报价须由人工在受控流程中签发）。"
    )
    add("")

    # ---- 候选方案 ----
    add("## 2. 候选方案")
    add("")
    if not result.candidates:
        add("没有候选方案（见第 4 节的阻断原因）。")
        add("")
    else:
        add(
            "| 候选 | 卡数 | TP×DP | 静态 | 性能/SLA | 性能数据性质 "
            "| 成本(全周期) | 成本口径 | 预算 | 报价 |"
        )
        add("|---|---|---|---|---|---|---|---|---|---|")
        for c in result.candidates:
            add(
                f"| `{c.candidate_id}` | {c.gpu_count} | {c.tp_size}×{c.dp_size} "
                f"| {_static_label(c.static_status)} "
                f"| {_sla_label(c)} "
                f"| {_data_kind(c, 'performance')} "
                f"| {_fmt(c.total_cash_over_horizon)} {_fmt(c.currency)} "
                f"| {_data_kind(c, 'economics')} "
                f"| {c.budget_status or '—'} "
                f"| {'✅ 可发布' if c.quote_publishable else '⛔ 不可发布'} |"
            )
        add("")
        for c in result.candidates:
            detail: list[str] = []
            if c.coverage_gaps:
                detail.append(f"成本覆盖缺口：{c.coverage_gaps}")
            if c.cost_incomplete and c.economics_status == "computed":
                detail.append("成本结论不完整，不得据此报价")
            if c.quote_blockers:
                detail.append("报价阻断：" + "；".join(c.quote_blockers[:3])
                              + ("…" if len(c.quote_blockers) > 3 else ""))
            if detail:
                add(f"- `{c.candidate_id}`：" + " / ".join(detail))
        add("")

    # ---- 推荐依据 ----
    add("## 3. 推荐依据")
    add("")
    for item in result.recommendations:
        add(f"- {item}")
    add("")

    # ---- 成本可比性 ----
    comparability = (result.summary or {}).get("cost_comparability") or {}
    if comparability:
        add("## 3.1 成本可比性")
        add("")
        add(
            "参照方案：`{}`".format(comparability.get("reference_candidate_id") or "无")
        )
        comparable = comparability.get("comparable") or []
        add(
            "**可比较候选**：" + ("、".join(f"`{c}`" for c in comparable) if comparable else "无")
        )
        not_comparable = comparability.get("not_comparable") or {}
        if not_comparable:
            add("")
            add("**不可比较候选**（不参与成本高低比较）：")
            add("")
            for cid, kind in not_comparable.items():
                add(f"- `{cid}`：{_COMPARABILITY_LABEL.get(kind, kind)}")
        add("")

    # ---- 缺口 ----
    add("## 4. 缺口与下一步")
    add("")
    if result.blockers:
        add("**阻断/错误原因：**")
        add("")
        for b in result.blockers:
            where = f"（{b.candidate_id}）" if b.candidate_id else ""
            add(f"- [{b.domain}] `{b.code}`{where}：{b.message}")
        add("")
    if result.missing_inputs:
        add("**缺少的输入：**")
        add("")
        for item in result.missing_inputs:
            add(f"- {item}")
        add("")
    if result.next_data_needed:
        add("**建议补充：**")
        add("")
        for item in result.next_data_needed:
            add(f"- {item}")
        add("")
    if not (result.blockers or result.missing_inputs or result.next_data_needed):
        add("无缺口。")
        add("")

    # ---- 结论边界 ----
    add("## 5. 结论边界")
    add("")
    for item in result.limitations:
        add(f"- {item}")
    add("")

    # ---- 附录 ----
    add("## 6. 附录：领域明细")
    add("")
    add("> 详细指标、证据哈希、匹配等级与逐候选阻断原因用于复核，"
        "普通使用可以忽略本节。")
    add("")
    for c in result.candidates:
        add(f"### {c.candidate_id}")
        add("")
        add(
            f"- 资源：{c.gpu_count} 卡 / TP{c.tp_size}×DP{c.dp_size}"
            f" / 服务器 {_fmt(c.nodes_required)} 台 / 静态余量 {_fmt(c.headroom_bytes)} B"
        )
        add(f"- 静态结论：{c.static_status}")
        if c.static_reasons:
            for r in c.static_reasons:
                add(f"  - {r}")
        add(
            f"- 性能：匹配等级 {_fmt(c.match_level)}，SLA {_fmt(c.sla_status)}，"
            f"分桶 {_fmt(c.rank_bucket)}"
        )
        add(
            f"  - 输出吞吐 {_fmt(c.output_throughput_tok_s)} tok/s，"
            f"TTFT p99 {_fmt(c.ttft_p99_ms)} ms，TPOT p99 {_fmt(c.tpot_p99_ms)} ms"
        )
        if c.evidence_run_ids:
            add(f"  - 依据实测：{c.evidence_run_ids}")
        if c.plan_id:
            add(
                f"- 成本：方案 `{c.plan_id}`（{_fmt(c.cost_basis)}）"
                f" 一次性 {_fmt(c.capex_one_time)} / 年现金 {_fmt(c.annual_cash_opex)}"
                f" / 年总成本 {_fmt(c.annual_total_cost)}"
            )
        if c.annual_revenue is not None:
            add(f"  - 年度收入情景 {_fmt(c.annual_revenue)}，容量比对 {_fmt(c.capacity_check)}")
        add("")
    add(f"- 候选数：{echo.get('candidate_count')}")
    add(f"- 库中计费规则：{echo.get('billing_rules_in_db')}")
    add("")

    return "\n".join(L)
