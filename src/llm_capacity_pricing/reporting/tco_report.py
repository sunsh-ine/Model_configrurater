"""M33：第四阶段方案报告与报价草稿（Markdown）。

报告纪律：**结论先行，但三个结论分开写**。

报告从不给出单一的"通过"。它固定分成四段结论：

1. 工程功能是否完成（计算链路是否跑通）；
2. 商业数据是否确认（币种 / 价格 / 条款）；
3. SLA 是否具备实测依据；
4. 正式报价是否可发布。

报价草稿只在第 4 项为真时才输出金额；否则输出**阻断说明**，
把每条 blocker 原样列出 —— 草稿不带金额，避免被当成可发布的报价。
"""

from __future__ import annotations

from typing import Any, Sequence

PERF_DISCLAIMERS = (
    "**三个结论相互独立**：成本可行 ≠ SLA 已验证 ≠ 报价可发布。",
    "成本数据为合成示例或待确认状态时，TCO 只用于验证计算链路，不得对外报价。",
    "性能与 SLA 结论原样取自第三阶段，本阶段不做重新判定。",
    "没有可信实测容量时，收入情景只作情景分析，不构成产能承诺。",
    "本阶段不产出跨维度综合评分：成本低不能推出性能达标，性能达标不能推出可报价。",
)

#: 报价草稿被阻断时打印的显式水印（防止被误当成可发布文件）
BLOCKED_WATERMARK = "⛔ 草稿已阻断（不具备发布资格）"


def _fmt(value: Any) -> str:
    return "—" if value is None else str(value)


def _yesno(value: bool) -> str:
    return "是" if value else "否"


def _conclusion_block(
    *,
    engineering_done: bool,
    commercial_confirmed: bool,
    sla_verified: bool,
    quote_publishable: bool,
) -> list[str]:
    lines = ["## 0. 四个独立结论", ""]
    lines.append("| 结论 | 状态 | 说明 |")
    lines.append("|---|---|---|")
    lines.append(
        f"| 工程功能是否完成 | {'✅ 完成' if engineering_done else '❌ 未完成'} "
        "| 第四阶段计算链路是否跑通（与数据真实性无关） |"
    )
    lines.append(
        f"| 商业数据是否确认 | {'✅ 已确认' if commercial_confirmed else '⛔ 未确认'} "
        "| 币种 / 价格 / 条款需人工确认 |"
    )
    lines.append(
        f"| SLA 是否具备实测依据 | {'✅ 具备' if sla_verified else '⛔ 不具备'} "
        "| 需要 exact_match 的可信实测 |"
    )
    lines.append(
        f"| 正式报价是否可发布 | {'✅ 可发布' if quote_publishable else '⛔ 不可发布'} "
        "| 四维门禁全通过才可发布 |"
    )
    lines.append("")
    return lines


def build_tco_report(
    *,
    catalog: dict[str, Any],
    comparison: dict[str, Any] | None = None,
    tco_details: Sequence[dict[str, Any]] = (),
    revenue: dict[str, Any] | None = None,
    eligibilities: Sequence[dict[str, Any]] = (),
    budget_checks: Sequence[dict[str, Any]] = (),
    sensitivities: Sequence[dict[str, Any]] = (),
    breakevens: Sequence[dict[str, Any]] = (),
    cash_flows: Sequence[dict[str, Any]] = (),
    decision: dict[str, Any] | None = None,
    request: dict[str, Any] | None = None,
    synthetic_notice: str | None = None,
) -> str:
    """生成第四阶段 Markdown 报告（含报价草稿或阻断说明）。"""
    L: list[str] = []

    def add(line: str = "") -> None:
        L.append(line)

    add("# 第四阶段报告：TCO、经济性与报价资格")
    add("")
    add(f"生成时间：{_now()}")
    add("")
    if synthetic_notice:
        add(f"> ⚠️ {synthetic_notice}")
        add("")
    for item in PERF_DISCLAIMERS:
        add(f"> {item}")
    add("")

    if request:
        add("## 需求输入")
        add("")
        for key, value in request.items():
            add(f"- {key}：{_fmt(value)}")
        add("")

    elig_list = list(eligibilities)
    quote_ok = any(e.get("quote_publishable") for e in elig_list)
    sla_ok = all(
        e.get("sla_verification_status") == "verified_measured" for e in elig_list
    ) and bool(elig_list)
    comm_ok = all(
        e.get("commercial_confirmation_status") == "confirmed" for e in elig_list
    ) and bool(elig_list)
    engineering_done = bool(comparison and comparison.get("entries"))
    has_decision = bool(comparison)
    L.extend(
        _conclusion_block(
            engineering_done=engineering_done,
            commercial_confirmed=comm_ok,
            sla_verified=sla_ok,
            quote_publishable=quote_ok,
        )
    )
    if not has_decision:
        add(
            "> ⚠️ 本报告未附决策结果（``phase4-report`` 未传 ``--decision-json``）："
            "第 2–6 节（比较 / TCO 明细 / 收益 / 门禁 / 敏感性）被省略，"
            "上面的四个结论因此只能反映**成本目录**层的信息，不代表完整链路结论。"
        )
        add("")

    # ---- 1. 成本目录 ----
    add("## 1. 成本目录（M27）")
    add("")
    add(f"- 成本项：{catalog.get('item_count')} 项；计费规则：{catalog.get('billing_rule_count')} 条")
    add(f"- 币种：{catalog.get('currencies')}（已确认条目 {catalog.get('currency_confirmed_count')}）")
    add(f"- 按类别：{catalog.get('by_category')}")
    add(f"- 按买断/租赁：{catalog.get('by_basis')}")
    add(f"- 按状态：{catalog.get('by_status')}")
    if catalog.get("items_missing_price"):
        add(
            f"- **缺单价的成本项**：{catalog['items_missing_price']}"
            "（缺价不等于 0，相关方案的成本结论会被标为不完整）"
        )
    add("")

    # ---- 2. 多候选比较 ----
    if comparison:
        add("## 2. 多候选比较（M31）")
        add("")
        add("| 候选 | 卡数 | TP×DP | 静态 | 匹配等级 | SLA | 买断/租赁 | 一次性 | 年现金 | 全周期 | 单位卡年 | 可比性 |")
        add("|---|---|---|---|---|---|---|---|---|---|---|---|")
        for e in comparison.get("entries", []):
            add(
                f"| `{e.get('candidate_id')}` | {e.get('gpu_count')} | {e.get('tp_size')}×{e.get('dp_size')} "
                f"| {_fmt(e.get('static_status'))} | {_fmt(e.get('match_level'))} "
                f"| {_fmt(e.get('sla_status'))} | {_fmt(e.get('cost_basis'))} "
                f"| {_fmt(e.get('capex_one_time'))} | {_fmt(e.get('annual_cash_opex'))} "
                f"| {_fmt(e.get('total_cash_over_horizon'))} | {_fmt(e.get('cost_per_gpu_year'))} "
                f"| {_fmt(e.get('comparability'))} |"
            )
        add("")
        if comparison.get("lowest_cost_candidate_id"):
            add(
                f"- 可比且成本完整的候选中的最低成本：`{comparison['lowest_cost_candidate_id']}`"
                "（**仅为成本结论，不含性能与报价判断**）"
            )
        else:
            add("- 未给出最低成本候选：可比且成本完整的候选不足 2 个")
        add("")
        for note in comparison.get("comparability_notes", []):
            add(f"> {note}")
        add("")

    # ---- 3. TCO 明细 ----
    if tco_details:
        add("## 3. TCO 成本分解（M28）")
        add("")
        for detail in tco_details:
            tco = detail.get("tco", {})
            add(f"### 方案 `{tco.get('plan_id')}`（`{tco.get('candidate_id')}`）")
            add("")
            add(
                f"- 口径：{tco.get('cost_basis')}；周期：{tco.get('horizon_years')} 年；"
                f"币种：{_fmt(tco.get('currency'))}（已确认：{_yesno(bool(tco.get('currency_confirmed')))}）"
            )
            add(f"- 一次性投入：{_fmt(tco.get('capex_one_time'))}")
            add(f"- 年现金支出：{_fmt(tco.get('annual_cash_opex'))}")
            add(f"- 年折旧（会计口径，非现金）：{_fmt(tco.get('annual_depreciation'))}")
            add(f"- 年总成本：{_fmt(tco.get('annual_total_cost'))}")
            add(f"- 全周期现金总额：{_fmt(tco.get('total_cash_over_horizon'))}")
            add(f"- 全周期折现总额：{_fmt(tco.get('total_pv_over_horizon'))}")
            add(f"- 单位卡年成本：{_fmt(tco.get('cost_per_gpu_year'))}")
            if tco.get("excluded_items"):
                add(
                    f"- **因买断/租赁口径排除**：{tco['excluded_items']}"
                    "（排除不等于零成本）"
                )
            if tco.get("unknown_items"):
                add(
                    f"- **缺数据无法核算**：{tco['unknown_items']}"
                    "（未知不等于零，因此总额不完整）"
                )
            add("")
            add("| 类别 | 成本项 | 计价口径 | 数量 | 单价 | 年成本 | 一次性 | 结论 |")
            add("|---|---|---|---|---|---|---|---|")
            for comp in tco.get("components", []):
                add(
                    f"| {comp.get('category')} | `{comp.get('item_id')}` | {comp.get('price_basis')} "
                    f"| {_fmt(comp.get('quantity'))} | {_fmt(comp.get('unit_price'))} "
                    f"| {_fmt(comp.get('annual_cost'))} | {_fmt(comp.get('one_time_cost'))} "
                    f"| {comp.get('determination')} |"
                )
            add("")
            for note in tco.get("notes", []):
                add(f"> {note}")
            add("")

    # ---- 4. 现金流 ----
    if cash_flows:
        add("### 3.1 现金流（M28）")
        add("")
        for cf in cash_flows:
            add(f"方案 `{cf.get('plan_id')}`：NPV = {_fmt(cf.get('npv'))}，"
                f"回收年 = {_fmt(cf.get('payback_year'))}，"
                f"收入口径 = {_fmt(cf.get('revenue_basis'))}")
            add("")
            add("| 年 | 一次性 | 年支出 | 收入 | 净现金 | 累计 | 折现净现金 |")
            add("|---|---|---|---|---|---|---|")
            for row in cf.get("years", []):
                add(
                    f"| {row.get('year_index')} | {_fmt(row.get('capex'))} | {_fmt(row.get('opex'))} "
                    f"| {_fmt(row.get('revenue'))} | {_fmt(row.get('net_cash'))} "
                    f"| {_fmt(row.get('cumulative_net_cash'))} | {_fmt(row.get('discounted_net_cash'))} |"
                )
            add("")
            for note in cf.get("notes", []):
                add(f"> {note}")
            add("")

    # ---- 5. 收益情景 ----
    if revenue:
        add("## 4. 业务收益情景（M29）")
        add("")
        add(f"- 情景：`{revenue.get('scenario_id')}`；计费规则：`{revenue.get('billing_rule_id')}`")
        add(f"- 年度收入：{_fmt(revenue.get('annual_revenue'))} {_fmt(revenue.get('currency'))}")
        add(f"- 年度计费输入 token：{_fmt(revenue.get('annual_billable_input_tokens'))}")
        add(f"- 年度计费输出 token：{_fmt(revenue.get('annual_billable_output_tokens'))}")
        add(f"- 峰值请求速率：{_fmt(revenue.get('peak_request_rate_rps'))} req/s")
        add(f"- **容量比对**：`{revenue.get('capacity_check')}`"
            f"（仅情景：{_yesno(bool(revenue.get('scenario_only')))}）")
        add("")
        add("| 时段 | 未缓存输入 | 缓存输入 | 输出 | 毛收入 | 折扣 | 净收入 | 请求速率 |")
        add("|---|---|---|---|---|---|---|---|")
        for p in revenue.get("periods", []):
            add(
                f"| {p.get('label')} | {_fmt(p.get('billable_input_tokens_uncached'))} "
                f"| {_fmt(p.get('billable_input_tokens_cached'))} | {_fmt(p.get('billable_output_tokens'))} "
                f"| {_fmt(p.get('gross_revenue'))} | {_fmt(p.get('batch_discount'))} "
                f"| {_fmt(p.get('revenue'))} | {_fmt(p.get('request_rate_rps'))} |"
            )
        add("")
        for basis in revenue.get("capacity_basis", []):
            add(f"> 容量依据：{basis}")
        for warn in revenue.get("warnings", []):
            add(f"> ⚠️ {warn}")
        add("")

    # ---- 6. 报价资格门禁 ----
    if elig_list:
        add("## 5. 报价资格门禁（M30）")
        add("")
        add("| 候选 | 成本估算 | SLA 验证 | 商务确认 | 审批 | 可发布 |")
        add("|---|---|---|---|---|---|")
        for e in elig_list:
            add(
                f"| `{e.get('candidate_id')}` | {_fmt(e.get('cost_estimate_status'))} "
                f"| {_fmt(e.get('sla_verification_status'))} "
                f"| {_fmt(e.get('commercial_confirmation_status'))} "
                f"| {_fmt(e.get('approval_status'))} | {'✅' if e.get('quote_publishable') else '⛔'} |"
            )
        add("")
        for e in elig_list:
            if e.get("blockers"):
                add(f"**`{e.get('candidate_id')}` 阻断原因：**")
                add("")
                for b in e["blockers"]:
                    add(f"- {b}")
                add("")
            for c in e.get("cautions", []):
                add(f"> 注意：{c}")
        add("")

    # ---- 7. 预算 / 敏感性 / 盈亏平衡 ----
    if budget_checks or sensitivities or breakevens:
        add("## 6. 预算、敏感性与盈亏平衡（M32）")
        add("")
        for b in budget_checks:
            add(
                f"- 预算 `{b.get('budget_id')}`：{_fmt(b.get('status'))}"
                f"（口径 {_fmt(b.get('budget_kind'))}，预算 {_fmt(b.get('budget_amount'))}，"
                f"对比值 {_fmt(b.get('compared_value'))}，余量 {_fmt(b.get('headroom'))}）"
            )
            for r in b.get("reasons", []):
                add(f"  - {r}")
        for s in sensitivities:
            add(f"- 敏感性 方案 `{s.get('plan_id')}`：基准 {_fmt(s.get('base_value'))}"
                f"，驱动 {len(s.get('drivers', []))} 项，不完整={_yesno(bool(s.get('incomplete')))}")
            for d in s.get("drivers", []):
                if not d.get("computable"):
                    add(f"  - `{d.get('driver')}` Δ{_fmt(d.get('delta_fraction'))}：不可算 {d.get('notes')}")
                elif d.get("relative_change") is not None:
                    add(
                        f"  - `{d.get('driver')}` Δ{_fmt(d.get('delta_fraction'))} → "
                        f"{_fmt(d.get('perturbed_value'))}（相对 {_fmt(d.get('relative_change'))}）"
                    )
        for be in breakevens:
            add(
                f"- 盈亏平衡 方案 `{be.get('plan_id')}`：年度成本 {_fmt(be.get('annual_cost'))}"
                f"，所需年收入 {_fmt(be.get('required_annual_revenue'))}"
                f"，所需计费输出 token {_fmt(be.get('breakeven_billable_output_tokens'))}"
                f"，所需输出单价 {_fmt(be.get('breakeven_output_price_per_1m'))}"
                f"，容量校验 {_fmt(be.get('capacity_check'))}"
            )
            for r in be.get("reasons", []):
                add(f"  - {r}")
            for n in be.get("notes", []):
                add(f"  > {n}")
        add("")

    # ---- 8. 报价草稿 ----
    add("## 7. 报价草稿")
    add("")
    if quote_ok:
        add("> 四维门禁全部通过，以下为**草稿**，正式发布前仍需按合同条款复核。")
        add("")
        for e in elig_list:
            if not e.get("quote_publishable"):
                continue
            add(f"- 候选 `{e.get('candidate_id')}`：成本口径 {_fmt(e.get('cost_estimate_status'))}，"
                f"SLA {_fmt(e.get('sla_verification_status'))}，"
                f"商务确认 {_fmt(e.get('commercial_confirmation_status'))}，"
                f"审批 {_fmt(e.get('approval_status'))}（凭据 {_fmt(e.get('approval_ref'))}）")
    else:
        add(f"**{BLOCKED_WATERMARK}**——不输出任何报价金额。")
        add("")
        add("阻断原因（四维门禁逐项）：")
        add("")
        for e in elig_list:
            add(f"- `{e.get('candidate_id')}`：")
            for b in e.get("blockers", []):
                add(f"  - {b}")
        if not elig_list:
            add("- 没有候选通过成本与 SLA 前置检查，未进入门禁评估")
        add("")

    # ---- 9. 结论边界 ----
    if decision:
        add("## 8. 结论边界（本阶段不做的事）")
        add("")
        for item in decision.get("limitations", []):
            add(f"- {item}")
        add("")

    add("## 9. 未确认与未实现（如实列出）")
    add("")
    for item in (
        "真实商务价格与合同条款未接入：现有成本/计费数据为合成示例或待确认状态",
        "目标硬件（H100/H200 × Qwen3-32B）缺少本地实测数据 → SLA 恒为 not_evaluable / requires_benchmark",
        "未实现跨模型/跨硬件的通用性能预测器",
        "未实现需求预测与排队模型（需求由业务侧给定）",
        "未开发 Web 前端与自治商务 Agent（按任务书要求暂缓）",
        "经济性评分（含加权综合评分）**未实现**：币种与合同价确认前不启用",
    ):
        add(f"- {item}")
    add("")

    return "\n".join(L)


def _now() -> str:
    from datetime import datetime

    return datetime.now().astimezone().isoformat(timespec="seconds")
