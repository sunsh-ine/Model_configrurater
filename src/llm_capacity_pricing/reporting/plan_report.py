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

#: 匹配等级 -> 用户语言（**只有"完全一致"才算本配置实测**）
_MATCH_LABEL = {
    "exact_match": "完全一致（本配置实测）",
    "comparable_reference": "可比参考（非本配置）",
    "modeling_candidate": "仅可建模（非本配置）",
    "rejected": "被拒绝",
}


def _match_label(value: str | None) -> str:
    if value is None:
        return "—"
    return _MATCH_LABEL.get(value, value)


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


def build_plan_report(result: PlanResult, *, reference_limit: int = 3) -> str:
    """把 ``PlanResult`` 渲染成一份面向用户的 Markdown 报告。

    ``reference_limit`` 决定正文里展开几条公开参考（其余进第 7 节附录与 JSON）：
    正文只回答"最接近的几条差在哪"，完整清单留给复核。
    """
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
    add("**本报告里的数字分五层，请勿跨层混用：**")
    add("")
    add("| 层 | 含义 | 怎么读 |")
    add("|---|---|---|")
    add("| 静态资源可行 | 按模型参数与显存算出的可行性 | 只说明「装不装得下」，不含性能 |")
    add("| 公开性能参考 | 官方/第三方**公开发布**的实测（第 2.1 节） | 了解业界水平，**不用于**本需求判定 |")
    add("| 已验证 SLA | 只有**目标配置下**、可信来源的实测才算 | 没有就是「证据不足」，不猜、不外推 |")
    add("| 假设成本 | 示例成本目录算出的金额 | 不是报价；本 Demo 中不完整 |")
    add("| 正式报价资格 | 成本 / SLA / 商务确认 / 审批四维门禁 | 当前恒为「未授权发布」 |")
    add("")

    # ---- 候选方案 ----
    add("## 2. 候选方案")
    add("")
    if not result.candidates:
        add("没有候选方案（见第 4 节的阻断原因）。")
        add("")
    else:
        add(
            "| 候选 | 卡数 | TP×DP | 静态 | 性能/SLA | 性能数据性质 | 证据匹配等级 "
            "| 成本(全周期) | 成本口径 | 预算 | 报价 |"
        )
        add("|---|---|---|---|---|---|---|---|---|---|---|")
        for c in result.candidates:
            add(
                f"| `{c.candidate_id}` | {c.gpu_count} | {c.tp_size}×{c.dp_size} "
                f"| {_static_label(c.static_status)} "
                f"| {_sla_label(c)} "
                f"| {_data_kind(c, 'performance')} "
                f"| {_match_label(c.match_level)} "
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

    # ---- 公开数据参考 ----
    refs = result.external_references
    if refs:
        shown, rest = refs[:reference_limit], refs[reference_limit:]
        # 每条都有的缺口说一次就够（通常是"你的目标需求本身没写全"），
        # 否则同一句话会在 12 条里各出现一遍，读者反而看不出真正的差异。
        common = [x for x in refs[0].not_usable_reasons if all(
            x in r.not_usable_reasons for r in refs
        )]

        def own(reference: Any) -> list[str]:
            return [x for x in reference.not_usable_reasons if x not in common]

        add("## 2.1 公开数据参考（**不参与**本需求的性能判定）")
        add("")
        add(
            f"库里共有 **{len(refs)} 条**官方/第三方**公开发布**的实测。它们用来看同类配置在业界"
            "大概什么水平，**不是**你要部署的配置的实测依据：不参与 SLA 判定，也不进报价门禁。"
        )
        add("")
        add(
            "> 排序：匹配等级 → 与目标相同的条件数 → 输入长度接近程度。"
            "比较基准取你的候选方案中**与该条数据差异最小**的一个，"
            "因此下面列的是「最有利情况下」仍然存在的差异。"
            f"完整 {len(refs)} 条见第 7 节附录，结构化数据见 JSON 结果。"
        )
        add("")
        if common:
            add(f"**所有 {len(refs)} 条都有、因此下面不再逐条重复的缺口：**")
            add("")
            for reason in common:
                add(f"- {reason}")
            add("")
        add(f"### 最相关的 {len(shown)} 条")
        add("")
        add(
            "| # | 来源 | 模型 | GPU | 卡数 | TP/DP | 框架 | ISL/OSL | 并发 "
            "| TTFT P99 | TPOT P99 | 输出吞吐 |"
        )
        add("|---|---|---|---|---|---|---|---|---|---|---|---|")
        for index, r in enumerate(shown, 1):
            add(
                f"| {index} | {_fmt(r.publisher)} | {_fmt(r.model_name)} "
                f"| {_fmt(r.hardware_model)} | {_fmt(r.gpu_count)} "
                f"| {_fmt(r.tp_size)}/{_fmt(r.dp_size)} "
                f"| {_fmt(r.framework)} {_fmt(r.framework_version)} "
                f"| {_fmt(r.input_len_target)}/{_fmt(r.output_len_target)} "
                f"| {_fmt(r.configured_concurrency)} "
                f"| {_fmt(r.ttft_p99_ms)} | {_fmt(r.tpot_p99_ms)} "
                f"| {_fmt(r.output_throughput_tok_s)} |"
            )
        add("")
        for index, r in enumerate(shown, 1):
            add(f"#### {index}. `{r.run_id}`")
            add("")
            same = list(r.same_conditions or [])
            add(
                "- **与目标相同的条件**："
                + ("；".join(same) if same else "无 —— 连模型与硬件都不同")
            )
            differences = own(r)
            if differences:
                add("- **不能直接比较的条件**：")
                for reason in differences:
                    add(f"  - {reason}")
            else:
                add("- **不能直接比较的条件**：仅上列公共缺口")
            add("")
            add(f"- 匹配等级：`{_fmt(r.match_level)}`")
            add(f"- 来源：{_fmt(r.publisher)}｜{_fmt(r.source_url)}")
            add(
                f"- 存档证据：`{_fmt(r.source_file)}`"
                f"（sha256 `{_fmt(r.source_sha256)}`）"
            )
            add(f"- 数据可信等级：`{r.data_trust}`")
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
            f"- 性能：匹配等级 {_match_label(c.match_level)}，SLA {_fmt(c.sla_status)}，"
            f"分桶 {_fmt(c.rank_bucket)}"
        )
        add(
            f"  - 输出吞吐 {_fmt(c.output_throughput_tok_s)} tok/s，"
            f"TTFT p99 {_fmt(c.ttft_p99_ms)} ms，TPOT p99 {_fmt(c.tpot_p99_ms)} ms"
            + (
                ""
                if c.match_level in (None, "exact_match")
                else "（**非本配置实测**，见下）"
            )
        )
        if c.evidence_run_ids:
            add(f"  - 依据实测：{c.evidence_run_ids}")
        for note in c.notes:
            add(f"  - {note}")
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

    # ---- 附录：其余公开参考 ----
    if refs and rest:
        add("## 7. 附录：其余公开参考数据")
        add("")
        add(
            f"以下 {len(rest)} 条与目标的差距不小于第 2.1 节展示的 {len(shown)} 条，"
            "保留供复核；判定口径与正文完全一致（同样**不参与**本需求的 SLA 判定）。"
            "完整原因清单见 JSON 结果里的 `external_references`。"
        )
        add("")
        add(
            "| 来源 | 模型 | GPU | 卡数 | TP/DP | 框架 | ISL/OSL | 并发 "
            "| TTFT P99 | TPOT P99 | 输出吞吐 | 匹配等级 | 链接 |"
        )
        add("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
        for r in rest:
            add(
                f"| `{r.run_id}` | {_fmt(r.model_name)} | {_fmt(r.hardware_model)} "
                f"| {_fmt(r.gpu_count)} | {_fmt(r.tp_size)}/{_fmt(r.dp_size)} "
                f"| {_fmt(r.framework)} {_fmt(r.framework_version)} "
                f"| {_fmt(r.input_len_target)}/{_fmt(r.output_len_target)} "
                f"| {_fmt(r.configured_concurrency)} "
                f"| {_fmt(r.ttft_p99_ms)} | {_fmt(r.tpot_p99_ms)} "
                f"| {_fmt(r.output_throughput_tok_s)} | `{_fmt(r.match_level)}` "
                f"| {_fmt(r.source_url)} |"
            )
        add("")
        for r in rest:
            differences = own(r)
            if not differences:
                continue
            add(f"- `{r.run_id}`：" + "；".join(differences))
        add("")

    return "\n".join(L)
