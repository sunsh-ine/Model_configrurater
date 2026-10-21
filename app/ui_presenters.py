"""纯展示层：状态映射、数值格式化与 HTML 片段。

这里**不依赖 Streamlit**，也不做任何计算——只把结构化结果转成可读文本/表格，
方便单独测试（颜色映射、资源条件、离线工作点差异等）。
"""

from __future__ import annotations

import html
import re
from decimal import Decimal, InvalidOperation
from typing import Any

# ---------------------------------------------------------------------------
# 状态映射（键必须覆盖后端真实取值，见 tests/test_s_phase_demo.py）
# ---------------------------------------------------------------------------

#: 执行状态：方案是否生成成功（与“分析覆盖”分开）
PLAN_EXECUTION = {
    "ok": ("ok", "已生成"),
    "partial": ("ok", "已生成"),
    "blocked": ("fail", "被阻断"),
}

DOMAIN_CLASS = {
    "computed": "ok",
    "not_configured": "off",
    "missing_data": "warn",
    "error": "fail",
    "blocked": "fail",
}

DOMAIN_TEXT = {
    "computed": "已计算",
    "not_configured": "未配置",
    "missing_data": "缺数据",
    "error": "出错",
    "blocked": "被阻断",
}

DOMAIN_LABELS = {
    "input": "需求输入",
    "sizing": "资源规划",
    "performance": "性能与 SLA",
    "economics": "成本与预算",
    "governance": "报价治理",
}

STATIC = {
    "conditional_feasible": ("warn", "条件可行"),
    "infeasible": ("fail", "不可行"),
    "insufficient_information": ("off", "信息不足"),
    "unsupported": ("off", "不支持"),
}

SLA = {
    "observed_pass": ("ok", "实测通过"),
    "observed_fail": ("fail", "实测未通过"),
    "estimated_only": ("warn", "仅估算"),
    "not_evaluable": ("off", "不可评估"),
}

MATCH = {
    "exact_match": "精确匹配",
    "comparable_reference": "可比参考",
    "modeling_candidate": "仅可建模",
    "rejected": "已排除",
}

#: 模拟预测用蓝色，实测通过才是绿色——避免把预测当成实测
VERDICT = {
    "predicted_pass": ("pred", "预测达标"),
    "predicted_fail": ("fail", "预测未达标"),
    "insufficient_prediction": ("off", "预测不充分"),
}

BUDGET = {
    "within_budget": ("ok", "在预算内"),
    "exceeds_budget": ("fail", "超预算"),
    "unknown": ("off", "未知"),
    "not_configured": ("off", "未配置"),
}

SURFACE = {
    "sweeper": "配置扫描器",
    "native_replay": "原生回放",
    "mixed": "扫描 + 回放",
    "unknown": "未知",
}

#: 离线归档的单一工作点（与 demo_service.OFFLINE_SIM 保持一致）
OFFLINE_WORKPOINT = {
    "model_id": "qwen3-32b",
    "hardware_id": "nvidia-h100-sxm-80gb",
    "framework": "vllm",
    "framework_version": "0.24.0",
    "weight_precision": "bf16",
    "input_len": 8192,
    "output_len": 256,
    "concurrency": 32,
}

WORKPOINT_LABELS = {
    "model_id": "模型",
    "hardware_id": "GPU",
    "framework": "推理框架",
    "framework_version": "框架版本",
    "weight_precision": "精度",
    "input_len": "输入长度 ISL",
    "output_len": "输出长度 OSL",
    "concurrency": "并发",
}


# ---------------------------------------------------------------------------
# 格式化
# ---------------------------------------------------------------------------


def esc(value: object) -> str:
    return html.escape("" if value is None else str(value))


def inline(value: object) -> str:
    """把结构化结果里的少量 Markdown 强调转成 HTML，避免列表里出现 ``**``。"""
    text = esc(value)
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    return text.replace("`", "")


def num(value: object, digits: int = 1, dash: str = "—") -> str:
    if value is None or value == "":
        return dash
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return esc(value)
    if number == number.to_integral_value():
        return f"{number:,.0f}"
    return f"{number:,.{digits}f}"


def money(
    value: object, currency: str | None = None, *, show_currency: bool = True
) -> str:
    if value is None:
        return "<span class='muted'>—</span>"
    unit = (
        f" <span class='muted'>{esc(currency)}</span>"
        if show_currency and currency
        else ""
    )
    return f"{num(value, 0)}{unit}"


def chip(kind: str, text: str) -> str:
    return f"<span class='chip {kind}'>{esc(text)}</span>"


def spec(rows: list[tuple[str, str]]) -> str:
    body = "".join(
        f"<tr><th>{esc(key)}</th><td>{value}</td></tr>" for key, value in rows
    )
    return f"<table class='spec'>{body}</table>"


def table(
    headers: list[str], rows: list[list[str]], numeric: tuple[int, ...] = ()
) -> str:
    head = "".join(
        f"<th class='{'num' if index in numeric else ''}'>{esc(text)}</th>"
        for index, text in enumerate(headers)
    )
    body = ""
    for row in rows:
        cells = "".join(
            f"<td class='{'num' if index in numeric else ''}'>{cell}</td>"
            for index, cell in enumerate(row)
        )
        body += f"<tr>{cells}</tr>"
    return (
        "<div class='tbl'><table class='doc'>"
        f"<thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>"
    )


def safe_url(value: object) -> str | None:
    text = str(value or "")
    return text if text.startswith(("http://", "https://")) else None


# ---------------------------------------------------------------------------
# 派生展示结论（只做计数与拼接，不改变任何判定）
# ---------------------------------------------------------------------------


def offline_mismatches(current: dict[str, Any]) -> list[str]:
    """当前表单与离线归档工作点的差异（覆盖模型/GPU/框架/精度/负载/并发）。"""
    out = []
    for key, expected in OFFLINE_WORKPOINT.items():
        actual = current.get(key)
        if actual is None:
            continue
        if str(actual) != str(expected):
            out.append(f"{WORKPOINT_LABELS[key]}：当前 {actual}，归档 {expected}")
    return out


def resource_condition(candidate: Any, gpu_cap: int | None) -> tuple[str, str]:
    """主表的“资源条件”：静态结论 + GPU 总量，不改底层 static_status。"""
    status = getattr(candidate, "static_status", None)
    if status == "infeasible":
        return "fail", "不可行"
    if status == "unsupported":
        return "fail", "不支持"
    if status == "insufficient_information":
        return "off", "信息不足"
    if gpu_cap is not None and candidate.gpu_count > gpu_cap:
        return "fail", f"超过上限 {gpu_cap}"
    return "warn", "条件可行"


def coverage_summary(result: Any) -> str:
    sim = getattr(result, "simulation", None)
    items = [
        ("需求输入", result.domains.get("input", "not_configured")),
        ("资源规划", result.domains.get("sizing", "not_configured")),
        ("性能与 SLA", result.domains.get("performance", "not_configured")),
        ("成本与预算", result.domains.get("economics", "not_configured")),
        ("报价治理", result.domains.get("governance", "not_configured")),
        ("模拟预测", getattr(sim, "status", "not_configured") if sim else "not_configured"),
    ]
    computed = sum(1 for _, status in items if status == "computed")
    missing = [label for label, status in items if status != "computed"]
    text = f"分析覆盖：{computed} / {len(items)} 个领域已算出"
    if missing:
        text += "；未算出：" + "、".join(missing)
    return text


def _sim_label(candidate: Any) -> str:
    deployment = getattr(candidate, "deployment", None)
    label = getattr(deployment, "parallelism_label", None)
    return str(label or candidate.candidate_id)


def headline(result: Any) -> str:
    candidates = result.candidates
    cap = result.request_echo.get("max_total_gpus")
    # 与主表、账本同一判据：GPU 超限的候选不能算作“可用”
    feasible = [c for c in candidates if resource_condition(c, cap)[1] == "条件可行"]
    observed_all = sum(
        1 for c in candidates if c.sla_status in ("observed_pass", "observed_fail")
    )
    observed = sum(
        1 for c in feasible if c.sla_status in ("observed_pass", "observed_fail")
    )
    excluded_observed = observed_all - observed
    sla_configured = result.request_echo.get("sla") is not None
    parts: list[str] = []
    if feasible:
        within = f"在 {cap} 卡上限内" if cap else ""
        parts.append(f"{len(feasible)} 个候选{within}静态可行")
    elif cap:
        parts.append(f"没有在 {cap} 卡上限内静态可行的候选")
    else:
        parts.append("没有静态可行候选")
    if sla_configured:
        if observed:
            parts.append(f"{observed} 个可用候选有实测 SLA 结论")
        elif feasible:
            parts.append("可用候选中尚无实测 SLA 结论")
        else:
            parts.append("尚无目标配置的实测 SLA 证据")
        if excluded_observed:
            parts.append(
                f"另有 {excluded_observed} 个候选不在当前可用范围内，"
                "其实测数据仅作历史参考"
            )
    else:
        parts.append("未设置 SLA 阈值，本次不判定达标")
    sim = getattr(result, "simulation", None)
    if sim is not None and sim.status == "computed":
        recommended = sim.recommended(sim.ranking or "throughput")
        best = sim.best_effort()
        if recommended is not None:
            parts.append(
                f"模拟推荐 {_sim_label(recommended)} 满足阈值（预测，不是实测）"
            )
        elif best is not None:
            parts.append(f"模拟最好到 {_sim_label(best)}，仍未达阈值")
        else:
            parts.append("没有可比较的模拟候选")
    elif sim is not None:
        parts.append(f"模拟未产出结论（{DOMAIN_TEXT.get(sim.status, sim.status)}）")
    return "；".join(parts) + "。"


# ---------------------------------------------------------------------------
# 页面片段
# ---------------------------------------------------------------------------


def masthead_html(cost_currency: str | None) -> str:
    currency = cost_currency or "未知"
    return (
        "<div class='masthead'>"
        "<div class='masthead-main'>"
        "<h1>推理部署方案生成器</h1>"
        "<p class='lede'>填一份需求，得到一份带证据等级的部署方案。"
        "资源可行、性能达标、报价可发布是三个独立结论，不会被合并成一个「通过」。</p>"
        "</div>"
        "<div class='masthead-meta'>"
        + spec(
            [
                ("评估入口", "<code>evaluate_plan()</code>"),
                ("数据口径", "公开实测、归档模拟、合成成本"),
                ("示例成本币种", f"{esc(currency)}（未确认）"),
                ("报价发布", "<span class='muted'>未开放</span>"),
            ]
        )
        + "</div></div><div class='hairstrip'></div>"
    )


def empty_html(current: dict[str, Any], cost_currency: str | None) -> str:
    sla_text = (
        f"TTFT ≤ {num(current['ttft'])} ms，TPOT ≤ {num(current['tpot'])} ms"
        if current["sla_on"]
        else "<span class='muted'>未设置</span>"
    )
    budget_text = (
        f"{num(current['budget'], 0)}"
        + (f" <span class='muted'>{esc(cost_currency)}</span>" if cost_currency else "")
        if current["budget_on"]
        else "<span class='muted'>未设置</span>"
    )
    mode_text = {
        "offline": "离线归档",
        "live": "实时 AISimulate",
        "core": "不使用模拟",
    }[str(current["mode"])]
    return (
        "<div class='empty'>"
        "<h2>还没有评估结果</h2>"
        "<p>左侧填写需求后点击「生成部署方案」。整个过程离线可跑，"
        "每次生成都会新建一个运行目录，不会覆盖历史结果。</p>"
        "<ol>"
        "<li><b>需求</b><span>模型、硬件上限、负载与 SLA 阈值，决定候选的搜索空间。</span></li>"
        "<li><b>证据</b><span>公开实测、归档模拟与成本目录各自独立进入，缺失一律标为未知。</span></li>"
        "<li><b>方案</b><span>先给结论与核心候选，再展开性能、成本与模拟细节。</span></li>"
        "</ol>"
        "</div>"
        "<div class='sec'><h3>当前需求</h3><span>生成前预览，不是结论</span></div>"
        + spec(
            [
                ("模型", esc(current["model_id"])),
                ("GPU", esc(current["hardware_id"])),
                ("卡数上限", esc(current["gpu_count"])),
                (
                    "负载",
                    f"ISL {num(current['input_len'], 0)} / OSL "
                    f"{num(current['output_len'], 0)} / 并发 {num(current['concurrency'], 0)}",
                ),
                ("SLA", sla_text),
                ("预算", budget_text),
                (
                    "高级设置",
                    f"{esc(current['precision'])} · {esc(mode_text)} · "
                    + ("吞吐优先" if current["ranking"] == "throughput" else "成本优先"),
                ),
            ]
        )
    )


def result_head_html(result: Any) -> str:
    kind, label = PLAN_EXECUTION.get(result.status, ("off", result.status))
    if result.status == "blocked":
        title = "评估被阻断"
        sentence = "输入有错或全部候选被排除，没有可用结论。请查看下方阻断原因。"
    else:
        title = "方案已生成"
        sentence = headline(result)
    return (
        "<div class='result-head'>"
        "<div>"
        "<div class='runline'>"
        f"<span>评估编号 <b>{esc(result.request_id)}</b></span>"
        f"<span>生成于 {esc(result.generated_at.isoformat(timespec='minutes'))}</span>"
        f"<span>状态码 {esc(result.status)}</span>"
        "</div>"
        f"<h2>{title}</h2>"
        f"<p>{esc(sentence)}</p>"
        f"<div class='coverage'>{esc(coverage_summary(result))}</div>"
        "</div>"
        f"<div class='seal {kind}'><span>{label}</span><small>{esc(result.status)}</small></div>"
        "</div>"
    )


def rail_html(result: Any, data_ok: bool) -> str:
    sim = getattr(result, "simulation", None)
    stations = [
        ("需求输入", result.domains.get("input", "not_configured")),
        ("资源规划", result.domains.get("sizing", "not_configured")),
        ("性能与 SLA", result.domains.get("performance", "not_configured")),
        ("成本与预算", result.domains.get("economics", "not_configured")),
        ("报价治理", result.domains.get("governance", "not_configured")),
        (
            "模拟预测",
            getattr(sim, "status", "not_configured") if sim is not None else "not_configured",
        ),
    ]
    if not data_ok:
        stations[0] = ("需求输入", "error")
    nodes = ""
    for name, status in stations:
        css = DOMAIN_CLASS.get(status, "off")
        nodes += (
            f"<div class='node {css}'><div class='bar'></div>"
            f"<b>{esc(name)}</b><span>{esc(DOMAIN_TEXT.get(status, status))}</span></div>"
        )
    return (
        f"<div class='rail'>{nodes}</div>"
        "<p class='rail-note'>六个领域彼此独立：资源可行不等于性能达标，"
        "模拟预测不属于实测，报价治理单独判定。</p>"
    )


def ledger_html(result: Any) -> str:
    candidates = result.candidates
    total = len(candidates)

    cap = result.request_echo.get("max_total_gpus")

    def in_cap(candidate: Any) -> bool:
        return cap is None or candidate.gpu_count <= cap

    conditional = [c for c in candidates if c.static_status == "conditional_feasible"]
    feasible = [c for c in conditional if in_cap(c)]
    over_cap = len(conditional) - len(feasible)
    infeasible = sum(1 for c in candidates if c.static_status == "infeasible")
    unsupported = sum(1 for c in candidates if c.static_status == "unsupported")
    within = f"在 {cap} 卡上限内" if cap else ""
    if total == 0:
        resource_chip = chip("fail", "无候选")
        resource_text = "没有候选通过输入校验，资源结论为空。"
    elif feasible:
        resource_chip = chip("warn", "条件可行")
        over_note = (
            f"{over_cap} 个静态条件可行的候选超过 {cap} 卡上限，不计入可部署候选；"
            if over_cap
            else ""
        )
        resource_text = (
            f"{len(feasible)} / {total} 个候选{within}静态可行；{over_note}"
            f"{infeasible} 个不可行。这是资源结论，不含性能与经济性判断。"
        )
    elif over_cap:
        resource_chip = chip("fail", "超过 GPU 上限")
        resource_text = (
            f"{over_cap} 个静态条件可行的候选都超过 {cap} 卡上限，"
            f"没有可部署候选；{infeasible} 个不可行。"
        )
    elif infeasible + unsupported == total:
        resource_chip = chip("fail", "不可行")
        resource_text = f"全部 {total} 个候选都被资源或硬件约束排除。"
    else:
        resource_chip = chip("off", "信息不足")
        resource_text = f"{total} 个候选里没有一个能确认静态可行（数据不足或不支持）。"

    # 资源阶段就被排除的候选没有资格再谈“补测”：两者必须分开计数
    viable = feasible
    blocked = infeasible + unsupported + over_cap
    observed_pass = sum(1 for c in viable if c.sla_status == "observed_pass")
    observed_fail = sum(1 for c in viable if c.sla_status == "observed_fail")
    estimated = sum(1 for c in viable if c.sla_status == "estimated_only")
    not_evaluable = sum(1 for c in viable if c.sla_status in (None, "not_evaluable"))
    observed = observed_pass + observed_fail
    excluded_note = (
        f"{blocked} 个候选已被资源、硬件或 GPU 上限排除；" if blocked else ""
    )
    sla_configured = result.request_echo.get("sla") is not None
    if not sla_configured:
        # 没设阈值 ≠ 找不到证据：这是用户主动关掉的判定维度
        perf_chip = chip("off", "未设置")
        perf_text = "本次没有设置 SLA 阈值，未做达标/不达标判定。"
    elif not viable:
        if total and blocked == total:
            perf_chip = chip("fail", "被资源排除")
            perf_text = (
                "全部候选都被资源、硬件或 GPU 上限排除，性能侧无法进入实测判定；"
                "补测不能解决，需要换支持的硬件、拓扑或调整 GPU 上限。"
            )
        else:
            perf_chip = chip("off", "资源未确认")
            perf_text = (
                "没有能确认静态可行的候选（资源信息不足或不受支持），"
                "性能侧无法评估；先补齐资源信息。"
            )
    elif observed:
        kind = "ok" if observed_pass else "fail"
        perf_chip = chip(kind, "有实测结论")
        perf_text = (
            f"{excluded_note}{observed} 个可评估候选有实测 SLA 结论"
            f"（通过 {observed_pass}，未通过 {observed_fail}）；"
            f"{estimated} 个仅有估算，{not_evaluable} 个不可评估。"
        )
    else:
        estimated_note = (
            f"{estimated} 个仅有估算（不能当作达标），" if estimated else ""
        )
        perf_chip = chip("off", "证据不足")
        perf_text = (
            f"{excluded_note}0 个可评估候选有实测 SLA 结论，{estimated_note}"
            f"{not_evaluable} 个不可评估。"
            "这是证据缺口，需要补测，不能用估算替代。"
        )

    publishable = sum(1 for c in candidates if c.quote_publishable)
    governance = result.domains.get("governance", "not_configured")
    if publishable:
        quote_chip = chip("ok", "可发布")
        quote_text = f"{publishable} 个候选满足报价发布门槛。"
    else:
        quote_chip = chip("off", "不可发布")
        quote_text = (
            f"0 个候选可发布正式报价；治理领域状态为「"
            f"{DOMAIN_TEXT.get(governance, governance)}」，生产发布保持关闭。"
        )

    return (
        "<table class='ledger'>"
        f"<tr><th>资源</th><td class='verdict'>{resource_chip}</td><td>{resource_text}</td></tr>"
        f"<tr><th>性能</th><td class='verdict'>{perf_chip}</td><td>{perf_text}</td></tr>"
        f"<tr><th>报价</th><td class='verdict'>{quote_chip}</td><td>{quote_text}</td></tr>"
        "</table>"
    )


def candidates_table_html(result: Any) -> str:
    cap = result.request_echo.get("max_total_gpus")
    header_gpu = "GPU（已用 / 上限）" if cap else "GPU（卡）"
    rows = []
    for candidate in result.candidates:
        sla_kind, sla_text = SLA.get(
            candidate.sla_status, ("off", candidate.sla_status or "未评估")
        )
        kind, text = resource_condition(candidate, cap)
        gpu = f"{num(candidate.gpu_count, 0)} / {num(cap, 0)}" if cap else num(
            candidate.gpu_count, 0
        )
        rows.append(
            [
                f"<b>TP{candidate.tp_size} × DP{candidate.dp_size}</b>",
                gpu,
                chip(kind, text),
                chip(sla_kind, sla_text),
                num(candidate.output_throughput_tok_s),
            ]
        )
    if not rows:
        return "<div class='callout fail'>没有候选：先检查阻断原因与缺失输入。</div>"
    return table(
        ["配置", header_gpu, "资源条件", "SLA", "吞吐 tok/s"],
        rows,
        numeric=(1, 4),
    )


def perf_table_html(result: Any) -> str:
    rows = []
    for candidate in result.candidates:
        sla_kind, sla_text = SLA.get(
            candidate.sla_status, ("off", candidate.sla_status or "未评估")
        )
        rows.append(
            [
                f"TP{candidate.tp_size} × DP{candidate.dp_size}"
                f"<span class='ident'>{esc(candidate.candidate_id)}</span>",
                MATCH.get(candidate.match_level or "", candidate.match_level or "—"),
                chip(sla_kind, sla_text),
                num(candidate.output_throughput_tok_s),
                num(candidate.ttft_p99_ms),
                num(candidate.tpot_p99_ms),
            ]
        )
    if not rows:
        return "<p class='note'>没有候选。</p>"
    return table(
        ["候选", "匹配等级", "SLA", "吞吐 tok/s", "TTFT P99 ms", "TPOT P99 ms"],
        rows,
        numeric=(3, 4, 5),
    )


def refs_html(result: Any) -> str:
    if not result.external_references:
        return "<p class='note'>本次没有外部公开实测参考。</p>"
    refs = result.external_references[:3]
    usable = [
        ref
        for ref in refs
        if (ref.match_level or "") in ("exact_match", "comparable_reference")
    ]
    if usable:
        note = (
            "<div class='callout'>以下参考与需求的配置接近，可用于对照；"
            "但仍然不进入 SLA 判定与报价门禁。</div>"
        )
    else:
        note = (
            "<div class='callout warn'>本次没有可直接对照的公开实测："
            f"以下是最接近的 {len(refs)} 条，均因身份差异或字段缺失被排除。"
            "它们只能说明业界水平，不能作为本需求的性能证据。</div>"
        )
    rows = []
    for ref in refs:
        config = (
            f"{esc(ref.model_name or '未知模型')}"
            f"<span class='ident'>{esc(ref.weight_precision or '精度未知')} · "
            f"{esc(ref.hardware_model or '硬件未知')} × {num(ref.gpu_count, 0)} 卡 · "
            f"并发 {num(ref.configured_concurrency, 0)}</span>"
        )
        source = f"{esc(ref.publisher or '未署名')}"
        url = safe_url(ref.source_url)
        if url:
            source += (
                f"<span class='ident'><a href='{esc(url)}' target='_blank' "
                "rel='noopener'>查看原文</a></span>"
            )
        else:
            source += f"<span class='ident'>{esc(ref.run_id)}</span>"
        level = ref.match_level or ""
        level_kind = "ok" if level in ("exact_match", "comparable_reference") else "off"
        rows.append(
            [
                source,
                config,
                num(ref.output_throughput_tok_s),
                f"{num(ref.ttft_p99_ms)} / {num(ref.tpot_p99_ms)}",
                chip(level_kind, MATCH.get(level, level or "—")),
            ]
        )
    reasons = ""
    for ref in refs:
        lines = []
        if ref.same_conditions:
            lines.append("条件相同：" + "；".join(ref.same_conditions))
        if ref.not_usable_reasons:
            lines.append("不可用：" + "；".join(ref.not_usable_reasons))
        if lines:
            reasons += (
                f"<p class='note'><b>{esc(ref.publisher or '未署名')}</b></p>"
                "<ul class='plain'>"
                + "".join(f"<li>{inline(line)}</li>" for line in lines)
                + "</ul>"
            )
    return (
        note
        + table(
            ["来源", "配置", "吞吐 tok/s", "P99 ms（TTFT / TPOT）", "匹配等级"],
            rows,
            numeric=(2, 3),
        )
        + "<p class='note'>完整差异见技术报告 §2.1；这些数据不进入 SLA 判定与报价门禁。</p>"
        + reasons
    )


def cost_table_html(result: Any) -> str:
    rows = []
    currencies = set()
    for candidate in result.candidates:
        budget_kind, budget_text = BUDGET.get(
            candidate.budget_status or "not_configured", ("off", "未配置")
        )
        if candidate.currency:
            currencies.add(candidate.currency)
        rows.append(
            [
                f"TP{candidate.tp_size} × DP{candidate.dp_size}"
                f"<span class='ident'>{esc(candidate.candidate_id)}</span>",
                money(candidate.capex_one_time, candidate.currency, show_currency=False),
                money(candidate.annual_cash_opex, candidate.currency, show_currency=False),
                money(
                    candidate.total_cash_over_horizon,
                    candidate.currency,
                    show_currency=False,
                ),
                chip("warn", "不完整")
                if candidate.cost_incomplete
                else chip("ok", "完整"),
                chip(budget_kind, budget_text),
            ]
        )
    if not rows:
        return "<p class='note'>没有候选。</p>"
    unit = " / ".join(sorted(currencies)) if currencies else "币种未知"
    return (
        f"<p class='note'>金额单位：{esc(unit)}（示例成本目录，不是报价）。"
        "“全周期现金”缺失表示成本不完整，不能当作 0。</p>"
        + table(
            ["候选", "一次性", "年现金支出", "全周期现金", "完整性", "预算"],
            rows,
            numeric=(1, 2, 3),
        )
    )


def _sim_band(sim: Any) -> str:
    computed = sim.status == "computed"
    judged = sim.judged() if computed else []
    total = len(sim.candidates)
    passed = sum(1 for c in sim.candidates if c.verdict == "predicted_pass")
    failed = sum(1 for c in sim.candidates if c.verdict == "predicted_fail")
    insufficient = sum(
        1 for c in sim.candidates if c.verdict == "insufficient_prediction"
    )
    not_comparable = sum(1 for c in sim.candidates if not c.comparable)
    recommended = sim.recommended(sim.ranking or "throughput") if computed else None
    best = sim.best_effort() if computed else None

    if not computed:
        return (
            "<div class='callout pred'><b>模拟预测不是实测</b>：本次模拟状态为 "
            + esc(sim.status)
            + "，没有可用的预测候选。</div>"
        )
    if recommended is not None:
        verdict = (
            "推荐：<b>"
            + esc(recommended.candidate_id)
            + "</b>，预测满足阈值（预测不等于实测）。"
        )
    elif best is not None:
        verdict = (
            "未达标参考配置：<b>"
            + esc(best.candidate_id)
            + "</b>。预测未满足阈值，只用来说明最好能到哪一步，不是达标建议。"
        )
    else:
        verdict = "没有通过身份、资源、P99 与预算门槛的模拟候选。"
    return (
        "<div class='callout pred'><b>模拟预测不是实测</b>："
        f"共 {total} 个候选（其中不可比 {not_comparable} 个）："
        f"预测达标 {passed}，预测未达标 {failed}，预测不充分 {insufficient}；"
        f"可判定 {len(judged)} 个。{verdict}"
        "模拟结果不会写成 BenchmarkRun，也不会获得 observed_pass。</div>"
    )


def sim_html(sim: Any) -> str:
    if sim is None:
        return "<p class='note'>本次没有启用模拟。</p>"
    tool = sim.tool
    parts = [
        _sim_band(sim),
        spec(
            [
                ("工具", f"{esc(tool.tool or '—')} {esc(tool.tool_version or '')}"),
                ("形态", esc(SURFACE.get(tool.surface, tool.surface))),
                ("后端", f"{esc(tool.backend or '—')} {esc(tool.backend_version or '')}"),
                ("系统", esc(tool.system or "—")),
                ("搜索空间", esc(tool.search_space_file or "—")),
                (
                    "排名目标",
                    "吞吐优先"
                    if (sim.ranking or "throughput") == "throughput"
                    else "成本优先",
                ),
            ]
        ),
    ]
    if sim.status == "computed" and sim.candidates:
        rows = []
        for candidate in sim.candidates:
            kind, text = VERDICT.get(candidate.verdict, ("off", candidate.verdict))
            verdict = chip(kind, text)
            if not candidate.comparable:
                verdict += " " + chip("off", "不可比")
            rows.append(
                [
                    f"<b>{esc(candidate.deployment.parallelism_label or '—')}</b>"
                    f"<span class='ident'>{esc(candidate.candidate_id)}</span>",
                    num(candidate.deployment.gpu_count, 0),
                    num(candidate.metrics.sim_output_throughput_tok_s),
                    num(candidate.metrics.sim_p99_ttft_ms),
                    num(candidate.metrics.sim_p99_tpot_ms),
                    verdict,
                ]
            )
        parts.append(
            table(
                ["配置", "GPU", "吞吐 tok/s", "TTFT P99 ms", "TPOT P99 ms", "预测结论"],
                rows,
                numeric=(1, 2, 3, 4),
            )
        )
        if (sim.ranking or "throughput") == "cost":
            available, reasons = sim.cost_ranking_available()
            if available:
                parts.append(
                    "<div class='callout'>成本排序已生效：所有参与候选的成本完整且币种已确认。</div>"
                )
            else:
                parts.append(
                    "<div class='callout warn'>成本排序未生效，已降级为吞吐优先："
                    + esc("；".join(reasons))
                    + "</div>"
                )
    elif sim.status == "computed":
        parts.append("<div class='callout warn'>模拟已计算，但没有产生候选。</div>")

    if sim.limitations:
        parts.append(
            "<p class='note'><b>模拟数据的限制</b></p><ul class='plain'>"
            + "".join(f"<li>{inline(item)}</li>" for item in sim.limitations)
            + "</ul>"
        )
    rejection = ""
    for candidate in sim.candidates:
        reasons = sim.rejection_reasons(candidate)
        if not reasons:
            continue
        rejection += (
            f"<p class='note'><b>{esc(candidate.candidate_id)}</b></p>"
            "<ul class='plain'>"
            + "".join(f"<li>{inline(item)}</li>" for item in reasons)
            + "</ul>"
        )
    if rejection:
        parts.append(
            "<p class='note'><b>未被推荐的原因（可比性 / 资源 / 预算 / 预测）</b></p>"
            + rejection
        )
    return "".join(parts)


def recommendations_html(result: Any) -> str:
    parts = []
    if result.blockers:
        parts.append(
            f"<p class='note'><b>阻断原因（{len(result.blockers)} 项）</b></p>"
            "<ul class='plain'>"
            + "".join(
                f"<li><b>{esc(b.domain)}</b> / {esc(b.code)}：{inline(b.message)}</li>"
                for b in result.blockers
            )
            + "</ul>"
        )
    if result.recommendations:
        parts.append(
            f"<p class='note'><b>推荐依据（{len(result.recommendations)} 条）</b></p>"
            "<ul class='plain'>"
            + "".join(f"<li>{inline(item)}</li>" for item in result.recommendations)
            + "</ul>"
        )
    if result.missing_inputs or result.next_data_needed:
        items = list(result.missing_inputs) + list(result.next_data_needed)
        parts.append(
            "<p class='note'><b>还缺什么数据</b></p><ul class='plain'>"
            + "".join(f"<li>{inline(item)}</li>" for item in items)
            + "</ul>"
        )
    if result.limitations:
        parts.append(
            "<p class='note'><b>本次结论的适用边界</b></p><ul class='plain'>"
            + "".join(f"<li>{inline(item)}</li>" for item in result.limitations)
            + "</ul>"
        )
    return "".join(parts) or "<p class='note'>没有额外说明。</p>"
