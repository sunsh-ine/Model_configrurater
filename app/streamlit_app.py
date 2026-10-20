"""S3：推理部署方案生成器（单页）。

运行：

    .venv/bin/pip install -r requirements-app.txt
    .venv/bin/streamlit run app/streamlit_app.py

约束（与任务书一致）：

* 界面**只做**输入、展示、触发；
* 所有计算都调用 ``llm_capacity_pricing.app.demo_service`` → 现有 ``evaluate_plan()``；
* 前端**不实现**任何 Sizing / SLA / TCO 公式，只做取值与格式化；
* 没有账号、多租户、任务队列或审批后台。
"""

from __future__ import annotations

import html
import json
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path

import streamlit as st

from llm_capacity_pricing.app.demo_service import (
    DemoInputs,
    run_demo,
    server_for_hardware,
)

ROOT = Path(__file__).resolve().parents[1]

st.set_page_config(
    page_title="推理部署方案生成器",
    page_icon="▦",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ---------------------------------------------------------------------------
# 视觉体系：石绿纸面 + 墨色 + 松绿主色；朱印只用于阻断，预测蓝只用于模拟
# ---------------------------------------------------------------------------

CSS = """
<style>
:root{
  --paper:#E8EBE4;
  --surface:#FCFCF8;
  --surface-2:#F3F4EE;
  --ink:#18211C;
  --muted:#5B6861;
  --rule:#CFD5CB;
  --rule-strong:#A7B1A7;
  --pine:#1C5A49;
  --pine-wash:#E4EEE8;
  --seal:#A23A2C;
  --seal-wash:#F6E7E2;
  --pred:#2B5A8C;
  --pred-wash:#E7EEF5;
  --amber:#77580F;
  --amber-wash:#F3EDD8;
  --sans:"PingFang SC","Noto Sans SC","Noto Sans CJK SC","Source Han Sans SC","Microsoft YaHei","WenQuanYi Micro Hei",system-ui,-apple-system,sans-serif;
  --serif:"Songti SC","Noto Serif SC","Noto Serif CJK SC","Source Han Serif SC","STSong","SimSun","宋体","AR PL UMing CN",serif;
}

html, body, .stApp, [data-testid="stAppViewContainer"]{
  background:var(--paper) !important;
  color:var(--ink);
  font-family:var(--sans);
}
[data-testid="stAppViewContainer"] p,
[data-testid="stAppViewContainer"] li,
[data-testid="stAppViewContainer"] label,
[data-testid="stAppViewContainer"] input,
[data-testid="stAppViewContainer"] button{
  font-family:var(--sans);
}
[data-testid="stHeader"]{background:transparent !important;}
[data-testid="stToolbar"]{display:none;}
[data-testid="stDecoration"]{display:none;}
[data-testid="stMainBlockContainer"]{
  padding:1.5rem 2.2rem 4.5rem;
  max-width:1520px;
}
footer{display:none;}

h1,h2,h3,h4{font-family:var(--serif); color:var(--ink);}
.masthead h1, .result-head h2, .empty h2, .sec h3, .panel-head b, table.ledger th, .seal{
  font-family:var(--serif) !important;
}

/* ---------------- 页头 ---------------- */
.masthead{
  display:flex; gap:40px; justify-content:space-between; align-items:flex-end;
  border-bottom:2px solid var(--ink); padding-bottom:18px;
}
.masthead-main{max-width:640px;}
.masthead h1{font-size:36px; line-height:1.12; margin:0 0 10px; letter-spacing:.01em;}
.masthead .lede{
  font-size:13.5px; line-height:1.8; color:var(--muted); margin:0; max-width:60ch;
}
.masthead-meta{flex:0 0 330px; width:330px; padding-bottom:2px;}
.hairstrip{height:1px; background:var(--rule); margin:7px 0 26px;}

table.spec{width:100%; border-collapse:collapse; font-size:12.5px;}
table.spec th{
  text-align:left; font-weight:400; color:var(--muted);
  padding:7px 0; width:92px; border-bottom:1px solid var(--rule);
  vertical-align:top;
}
table.spec td{
  padding:7px 0; border-bottom:1px solid var(--rule);
  font-variant-numeric:tabular-nums; line-height:1.6;
}
table.spec tr:last-child th, table.spec tr:last-child td{border-bottom:none;}
table.spec code{
  font-family:var(--sans); font-size:12.5px; background:var(--surface-2);
  border:1px solid var(--rule); padding:0 5px; border-radius:2px;
}

/* ---------------- 需求单 ---------------- */
[data-testid="stVerticalBlockBorderWrapper"]{
  border:1px solid var(--rule-strong) !important;
  border-radius:2px !important;
  background:var(--surface);
  box-shadow:none !important;
}
.panel-head{
  display:flex; justify-content:space-between; align-items:baseline;
  border-bottom:1px solid var(--ink); padding-bottom:9px; margin-bottom:4px;
}
.panel-head b{font-family:var(--serif); font-size:19px; font-weight:600;}
.panel-head small{font-size:11.5px; color:var(--muted);}
.group{
  font-size:11.5px; color:var(--muted); border-top:1px solid var(--rule);
  padding-top:12px; margin:18px 0 4px; letter-spacing:.02em;
}
.group.first{border-top:none; margin-top:14px; padding-top:0;}
.hint{font-size:11.5px; line-height:1.65; color:var(--muted); margin:3px 0 10px;}
.hint.warn{color:var(--amber);}
.hint.fail{color:var(--seal);}
.hint b{font-weight:600; color:var(--ink);}

/* Streamlit 控件：方角、纸面、细线 */
[data-testid="stWidgetLabel"] p{font-size:12px; color:var(--muted); margin-bottom:2px;}
[data-baseweb="input"], [data-baseweb="select"] > div{
  background:var(--surface) !important;
  border:1px solid var(--rule) !important;
  border-radius:2px !important;
  box-shadow:none !important;
}
[data-baseweb="input"]:focus-within, [data-baseweb="select"] > div:focus-within{
  border-color:var(--pine) !important;
  box-shadow:0 0 0 1px var(--pine) !important;
}
[data-baseweb="input"] input, [data-baseweb="select"] span{color:var(--ink) !important;}
[data-testid="stNumberInput"] button{
  background:var(--surface-2) !important; color:var(--ink) !important;
  border-color:var(--rule) !important; border-radius:0 !important;
}
[data-testid="stRadio"] label p{font-size:13px;}
[data-testid="stToggle"] label p{font-size:12.5px;}

.stButton > button, [data-testid="stFormSubmitButton"] > button{
  border-radius:2px !important;
  border:1px solid var(--ink) !important;
  background:var(--ink) !important;
  color:#F6F5EF !important;
  font-weight:600; font-size:14px; padding:.62rem 1rem;
  transition:none;
}
.stButton > button:hover, [data-testid="stFormSubmitButton"] > button:hover{
  background:var(--pine) !important; border-color:var(--pine) !important;
}
.stButton > button:disabled{
  background:var(--rule) !important; border-color:var(--rule) !important;
  color:#6E776F !important;
}
[data-testid="stDownloadButton"] button{
  background:transparent !important; color:var(--ink) !important;
  border:1px solid var(--rule-strong) !important; border-radius:2px !important;
  font-weight:500; font-size:13px; padding:.5rem .8rem;
}
[data-testid="stDownloadButton"] button:hover{
  border-color:var(--ink) !important; background:var(--surface-2) !important;
}
:focus-visible{outline:2px solid var(--pine); outline-offset:2px;}
[data-testid="stExpander"] details{
  border:1px solid var(--rule) !important; border-radius:2px !important;
  background:var(--surface);
}
[data-testid="stExpander"] summary{font-size:13px; color:var(--ink);}
[data-testid="stAlert"]{border-radius:2px !important;}

/* ---------------- 空状态 ---------------- */
.empty{border:1px dashed var(--rule-strong); background:transparent; padding:30px 30px 24px;}
.empty h2{font-size:23px; margin:0 0 8px;}
.empty p{font-size:13px; line-height:1.8; color:var(--muted); margin:0; max-width:58ch;}
.empty ol{list-style:none; counter-reset:s; margin:22px 0 0; padding:0;}
.empty li{counter-increment:s; position:relative; padding-left:36px; margin-bottom:16px; font-size:13px; line-height:1.7;}
.empty li:last-child{margin-bottom:0;}
.empty li::before{
  content:counter(s); position:absolute; left:0; top:2px;
  width:22px; height:22px; border:1px solid var(--rule-strong);
  display:flex; align-items:center; justify-content:center;
  font-size:11px; color:var(--muted);
}
.empty li b{display:block; font-size:13.5px; font-weight:600; margin-bottom:1px;}
.empty li span{color:var(--muted);}

/* ---------------- 结果：状态与链路 ---------------- */
.result-head{
  display:flex; justify-content:space-between; align-items:flex-start; gap:28px;
  border-bottom:1px solid var(--ink); padding-bottom:16px;
}
.result-head .runline{font-size:11.5px; color:var(--muted); margin-bottom:8px;}
.result-head .runline b{font-weight:600; color:var(--ink);}
.result-head .runline span{margin-right:14px;}
.result-head h2{font-size:27px; margin:0 0 8px; line-height:1.2;}
.result-head p{font-size:13px; line-height:1.75; color:var(--muted); margin:0; max-width:60ch;}

.seal{
  flex:0 0 92px; width:92px; height:92px; border:2.5px solid var(--seal);
  border-radius:4px; color:var(--seal); background:var(--seal-wash);
  display:flex; flex-direction:column; align-items:center; justify-content:center;
  transform:rotate(-4deg);
  box-shadow:inset 0 0 0 3px var(--surface), inset 0 0 0 4px var(--seal);
  letter-spacing:.14em; font-family:var(--serif); font-size:23px; font-weight:700;
}
.seal small{
  font-family:var(--sans); font-size:9px; letter-spacing:.16em;
  font-weight:500; margin-top:1px; text-transform:none;
}
.seal.ok{--seal:#1C5A49; --seal-wash:#E4EEE8;}
.seal.warn{--seal:#77580F; --seal-wash:#F3EDD8;}

.rail{display:flex; border:1px solid var(--rule); background:var(--surface); margin-top:22px;}
.node{flex:1; padding:14px 14px 12px; border-right:1px solid var(--rule); min-width:0;}
.node:last-child{border-right:none;}
.node .bar{height:3px; margin:-14px -14px 11px; background:var(--rule-strong);}
.node.ok .bar{background:var(--pine);}
.node.warn .bar{background:var(--amber);}
.node.fail .bar{background:var(--seal);}
.node.off .bar{background:var(--rule);}
.node b{display:block; font-size:12.5px; font-weight:600; margin-bottom:2px;}
.node span{font-size:11px; color:var(--muted);}
.rail-note{font-size:11.5px; color:var(--muted); margin:7px 0 0;}

table.ledger{width:100%; border-collapse:collapse; background:var(--surface); border:1px solid var(--rule); margin-top:20px;}
table.ledger th{
  width:78px; text-align:left; padding:14px; font-family:var(--serif);
  font-size:15px; font-weight:600; vertical-align:top;
  border-bottom:1px solid var(--rule); border-right:1px solid var(--rule);
}
table.ledger td{padding:13px 14px; border-bottom:1px solid var(--rule); font-size:12.5px; line-height:1.7; vertical-align:top;}
table.ledger tr:last-child th, table.ledger tr:last-child td{border-bottom:none;}
table.ledger td.verdict{width:104px; white-space:nowrap;}

.chip{
  display:inline-block; padding:1px 8px; border:1px solid currentColor;
  border-radius:2px; font-size:11.5px; line-height:1.7; white-space:nowrap;
}
.chip.ok{color:var(--pine); background:var(--pine-wash); border-color:#B9CFC4;}
.chip.warn{color:var(--amber); background:var(--amber-wash); border-color:#DCCF9F;}
.chip.fail{color:var(--seal); background:var(--seal-wash); border-color:#DDB9AF;}
.chip.off{color:var(--muted); background:var(--surface-2); border-color:var(--rule);}
.chip.pred{color:var(--pred); background:var(--pred-wash); border-color:#B9CBE0;}

.callout{
  border-left:3px solid var(--pine); background:var(--surface);
  padding:13px 16px; margin:16px 0; font-size:12.5px; line-height:1.75;
}
.callout.pred{border-left-color:var(--pred); background:var(--pred-wash);}
.callout.warn{border-left-color:var(--amber); background:var(--amber-wash);}
.callout.fail{border-left-color:var(--seal); background:var(--seal-wash);}
.callout b{font-weight:600; overflow-wrap:anywhere;}

.sec{
  display:flex; align-items:baseline; justify-content:space-between;
  border-bottom:1px solid var(--ink); margin:36px 0 14px; padding-bottom:6px;
}
.sec h3{margin:0; font-size:19px; font-weight:600;}
.sec span{font-size:11.5px; color:var(--muted);}

.tbl{overflow-x:auto; border:1px solid var(--rule); background:var(--surface);}
table.doc{width:100%; border-collapse:collapse; font-size:12.5px;}
table.doc th{
  text-align:left; font-weight:600; color:var(--muted); font-size:11.5px;
  padding:9px 12px; border-bottom:1px solid var(--rule-strong); white-space:nowrap;
  background:var(--surface-2);
}
table.doc td{padding:10px 12px; border-bottom:1px solid var(--rule); vertical-align:top; line-height:1.6;}
table.doc tr:last-child td{border-bottom:none;}
table.doc td.num, table.doc th.num{text-align:right; font-variant-numeric:tabular-nums; white-space:nowrap;}
table.doc .ident{display:block; font-size:10.5px; color:var(--muted); margin-top:2px;}

ul.plain{margin:0; padding-left:18px; font-size:12.5px; line-height:1.8; color:var(--ink);}
ul.plain li{margin-bottom:4px;}
.muted{color:var(--muted);}
.big{font-family:var(--serif); font-size:19px; font-variant-numeric:tabular-nums;}
a{color:var(--pine); text-decoration:none; border-bottom:1px solid rgba(28,90,73,.35);}
a:hover{border-bottom-color:var(--pine);}

@media (max-width:820px){
  [data-testid="stMainBlockContainer"]{padding:1rem 1rem 3rem;}
  .masthead{display:block;}
  .masthead h1{font-size:27px;}
  .masthead-meta{width:100%; flex:none; margin-top:18px;}
  .result-head{display:block;}
  .seal{margin-top:16px;}
  .rail{flex-wrap:wrap;}
  .node{flex:1 1 33%; border-bottom:1px solid var(--rule);}
  table.ledger th{width:64px;}
}
</style>
"""

st.markdown(CSS, unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# 格式化小工具（只做展示，不做计算）
# ---------------------------------------------------------------------------

PLAN_STATUS = {
    "ok": ("ok", "完整", "所有已配置领域都已算出结论。"),
    "partial": ("warn", "部分", "存在未配置或缺失的领域，结论只覆盖已算出的部分。"),
    "blocked": ("fail", "阻断", "输入有错或全部候选被排除，没有可用结论。"),
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

STATIC = {
    "conditional_feasible": ("warn", "有条件可行"),
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

VERDICT = {
    "predicted_pass": ("ok", "预测达标"),
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


def _esc(value: object) -> str:
    return html.escape("" if value is None else str(value))


def _inline(value: object) -> str:
    """把结构化结果里的少量 Markdown 强调转成 HTML，避免列表里出现 ``**``。"""
    text = _esc(value)
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    return text.replace("`", "")


def _safe_url(value: object) -> str | None:
    text = str(value or "")
    return text if text.startswith(("http://", "https://")) else None


def _chip(kind: str, text: str) -> str:
    return f"<span class='chip {kind}'>{_esc(text)}</span>"


def _num(value: object, digits: int = 1, dash: str = "—") -> str:
    if value is None or value == "":
        return dash
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return _esc(value)
    if number == number.to_integral_value():
        return f"{number:,.0f}"
    return f"{number:,.{digits}f}"


def _money(value: object, currency: str | None) -> str:
    if value is None:
        return "<span class='muted'>—</span>"
    unit = f" <span class='muted'>{_esc(currency)}</span>" if currency else ""
    return f"{_num(value, 0)}{unit}"


def _spec(rows: list[tuple[str, str]]) -> str:
    body = "".join(f"<tr><th>{_esc(key)}</th><td>{value}</td></tr>" for key, value in rows)
    return f"<table class='spec'>{body}</table>"


def _table(headers: list[str], rows: list[list[str]], numeric: tuple[int, ...] = ()) -> str:
    head = "".join(
        f"<th class='{'num' if index in numeric else ''}'>{_esc(text)}</th>"
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


@st.cache_data(show_spinner=False, ttl=300)
def _aisimulate_probe() -> tuple[str | None, bool]:
    """实时模式预检：解释器存在还不够，必须能真正导入 aiconfigurator 与 aisimulate。"""
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


def _fingerprint(values: dict[str, object]) -> str:
    return json.dumps({key: str(value) for key, value in values.items()}, sort_keys=True)


# ---------------------------------------------------------------------------
# 页头
# ---------------------------------------------------------------------------

st.markdown(
    "<div class='masthead'>"
    "<div class='masthead-main'>"
    "<h1>推理部署方案生成器</h1>"
    "<p class='lede'>填一份需求，得到一份带证据等级的部署方案。"
    "资源可行、性能达标、报价可发布是三个独立结论，不会被合并成一个「通过」。</p>"
    "</div>"
    "<div class='masthead-meta'>"
    + _spec(
        [
            ("评估入口", "<code>evaluate_plan()</code>"),
            ("数据口径", "公开实测、归档模拟、合成成本"),
            ("报价发布", "<span class='muted'>未开放</span>"),
        ]
    )
    + "</div></div><div class='hairstrip'></div>",
    unsafe_allow_html=True,
)

models = _catalog("data/models/model_manifest.yaml") or ["qwen3-32b"]
hardwares = _catalog("data/hardware/hardware_catalog.yaml") or ["nvidia-h100-sxm-80gb"]

left, right = st.columns([0.36, 0.64], gap="large")

with left:
    with st.container(border=True):
        st.markdown(
            "<div class='panel-head'><b>需求单</b>"
            "<small>一次评估对应一份需求</small></div>",
            unsafe_allow_html=True,
        )

        st.markdown("<div class='group first'>模型与硬件</div>", unsafe_allow_html=True)
        model_id = st.selectbox(
            "模型", models, index=_index_before(models, "qwen3-32b")
        )
        hardware_id = st.selectbox(
            "GPU 型号", hardwares, index=_index_before(hardwares, "nvidia-h100-sxm-80gb")
        )
        gpu_count = st.number_input(
            "GPU 上限（卡）", min_value=1, max_value=64, value=8, step=1
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
            "输入长度 ISL", min_value=1, max_value=131072, value=8192, step=256
        )
        output_len = w2.number_input(
            "输出长度 OSL", min_value=1, max_value=8192, value=256, step=16
        )
        concurrency = st.number_input(
            "并发请求数", min_value=1, max_value=1024, value=32, step=1
        )

        st.markdown("<div class='group'>SLA 阈值</div>", unsafe_allow_html=True)
        sla_on = st.toggle("设置 SLA 阈值", value=True)
        ttft_value: float | None = None
        tpot_value: float | None = None
        if sla_on:
            s1, s2 = st.columns(2)
            ttft_value = s1.number_input(
                "TTFT P99（ms）", min_value=1.0, value=400.0, step=10.0, format="%.1f"
            )
            tpot_value = s2.number_input(
                "TPOT P99（ms）", min_value=1.0, value=30.0, step=1.0, format="%.1f"
            )
        else:
            st.markdown(
                "<p class='hint'>不设阈值时，性能领域只给数据匹配结论，不判定达标与否。</p>",
                unsafe_allow_html=True,
            )

        st.markdown("<div class='group'>成本与预算</div>", unsafe_allow_html=True)
        budget_on = st.toggle("设置预算上限", value=True)
        budget_value: float | None = None
        if budget_on:
            budget_value = st.number_input(
                "预算（元）",
                min_value=0.0,
                max_value=1e10,
                value=3_000_000.0,
                step=100_000.0,
                format="%.0f",
            )
        else:
            st.markdown(
                "<p class='hint'>不设预算：候选仍会算成本，但不做预算比对。</p>",
                unsafe_allow_html=True,
            )

        st.markdown("<div class='group'>模拟与排序</div>", unsafe_allow_html=True)
        precision = st.selectbox("模型精度", ["bf16", "fp8"], index=0)
        mode_label = st.selectbox(
            "模拟数据来源",
            ["离线归档（无需安装工具）", "实时模拟（调用 AISimulate）", "不使用模拟"],
            index=0,
        )
        mode = {
            "离线归档（无需安装工具）": "offline",
            "实时模拟（调用 AISimulate）": "live",
            "不使用模拟": "core",
        }[mode_label]

        ranking_label = st.radio(
            "排序目标",
            ["吞吐优先", "成本优先"],
            index=0,
            horizontal=True,
        )
        ranking = "throughput" if ranking_label == "吞吐优先" else "cost"

        live_python, live_ready = _aisimulate_probe()
        blocking_reason = None
        if mode == "offline":
            st.markdown(
                "<p class='hint'>读取仓库内归档的 AISimulate 输出：单一工作点、不联网。"
                "归档为 Qwen3-32B / H100 / bf16；换模型、GPU 或精度会降低可比性。</p>",
                unsafe_allow_html=True,
            )
            if precision != "bf16":
                st.markdown(
                    "<p class='hint warn'>离线归档只有 bf16 工作点：选择 fp8 后"
                    "模拟候选会因精度不同被判为不可比较，不会有达标结论。</p>",
                    unsafe_allow_html=True,
                )
        elif mode == "live":
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

        st.markdown("<div class='group'>生成</div>", unsafe_allow_html=True)
        clicked = st.button(
            "生成部署方案",
            type="primary",
            width="stretch",
            disabled=blocking_reason is not None,
        )
        if blocking_reason:
            st.markdown(
                "<p class='hint fail'>按钮已禁用：先解决上面的环境问题。</p>",
                unsafe_allow_html=True,
            )

current_values = {
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
            st.session_state["outcome"] = run_demo(request_inputs)
            st.session_state["error"] = None
            st.session_state["fingerprint"] = current_fp
        except Exception as exc:  # noqa: BLE001 - 界面上只展示可读错误
            # 失败不应清空上一次成功生成的方案
            st.session_state["error"] = f"{type(exc).__name__}: {exc}"

outcome = st.session_state.get("outcome")
error = st.session_state.get("error")
stale = outcome is not None and st.session_state.get("fingerprint") != current_fp


# ---------------------------------------------------------------------------
# 结果区
# ---------------------------------------------------------------------------


def _render_empty(current: dict[str, object]) -> None:
    st.markdown(
        "<div class='empty'>"
        "<h2>还没有评估结果</h2>"
        "<p>左侧填写需求后点击「生成部署方案」。整个过程离线可跑，"
        "每次生成都会新建一个运行目录，不会覆盖历史结果。</p>"
        "<ol>"
        "<li><b>需求</b><span>模型、硬件上限、负载与 SLA 阈值，决定候选的搜索空间。</span></li>"
        "<li><b>证据</b><span>公开实测、归档模拟与成本目录各自独立进入，缺失一律标为未知。</span></li>"
        "<li><b>方案</b><span>资源 / 性能 / 模拟 / 报价四条线分开汇报，并导出方案文档。</span></li>"
        "</ol>"
        "</div>",
        unsafe_allow_html=True,
    )
    st.markdown(
        "<div class='sec'><h3>当前需求</h3><span>生成前预览，不是结论</span></div>",
        unsafe_allow_html=True,
    )
    sla_text = (
        f"TTFT ≤ {_num(current['ttft'])} ms，TPOT ≤ {_num(current['tpot'])} ms"
        if current["sla_on"]
        else "<span class='muted'>未设置</span>"
    )
    budget_text = (
        f"{_num(current['budget'], 0)} 元"
        if current["budget_on"]
        else "<span class='muted'>未设置</span>"
    )
    st.markdown(
        _spec(
            [
                ("模型", _esc(current["model_id"])),
                ("GPU", _esc(current["hardware_id"])),
                ("卡数上限", _esc(current["gpu_count"])),
                (
                    "负载",
                    f"ISL {_num(current['input_len'], 0)} / OSL "
                    f"{_num(current['output_len'], 0)} / 并发 {_num(current['concurrency'], 0)}",
                ),
                ("SLA", sla_text),
                ("预算", budget_text),
                ("精度", _esc(current["precision"])),
                (
                    "模拟",
                    {
                        "offline": "离线归档",
                        "live": "实时 AISimulate",
                        "core": "不使用模拟",
                    }[str(current["mode"])],
                ),
                (
                    "排序",
                    "吞吐优先" if current["ranking"] == "throughput" else "成本优先",
                ),
            ]
        ),
        unsafe_allow_html=True,
    )


def _rail(outcome_obj) -> str:
    result = outcome_obj.result
    sim = getattr(result, "simulation", None)
    data_status = outcome_obj.stages[0][1] if outcome_obj.stages else "error"
    stations = [
        ("数据准备", "computed" if data_status == "ok" else "error"),
        ("资源规划", result.domains.get("sizing", "not_configured")),
        ("性能与 SLA", result.domains.get("performance", "not_configured")),
        ("成本与预算", result.domains.get("economics", "not_configured")),
        ("报价治理", result.domains.get("governance", "not_configured")),
        ("模拟预测", getattr(sim, "status", "not_configured")),
    ]
    nodes = ""
    for name, status in stations:
        css = DOMAIN_CLASS.get(status, "off")
        nodes += (
            f"<div class='node {css}'><div class='bar'></div>"
            f"<b>{_esc(name)}</b><span>{_esc(DOMAIN_TEXT.get(status, status))}</span></div>"
        )
    return (
        f"<div class='rail'>{nodes}</div>"
        "<p class='rail-note'>六个领域彼此独立：资源可行不等于性能达标，"
        "模拟预测不属于实测，报价治理单独判定。</p>"
    )


def _ledger(result) -> str:
    candidates = result.candidates
    total = len(candidates)

    conditional = sum(1 for c in candidates if c.static_status == "conditional_feasible")
    infeasible = sum(1 for c in candidates if c.static_status == "infeasible")
    if total == 0:
        resource_chip = _chip("fail", "无候选")
        resource_text = "没有候选通过输入校验，资源结论为空。"
    elif conditional:
        resource_chip = _chip("warn", "有条件可行")
        resource_text = (
            f"{conditional} / {total} 个候选在明确运行时假设下静态可行；"
            f"{infeasible} 个不可行。这是资源结论，不含性能与经济性判断。"
        )
    elif infeasible == total:
        resource_chip = _chip("fail", "不可行")
        resource_text = f"全部 {total} 个候选都被资源或硬件约束排除。"
    else:
        resource_chip = _chip("off", "信息不足")
        resource_text = f"{total} 个候选里没有一个能确认静态可行（数据不足或不支持）。"

    # 资源阶段就被排除的候选没有资格再谈“补测”：两者必须分开计数
    viable = [c for c in candidates if c.static_status == "conditional_feasible"]
    excluded = sum(
        1 for c in candidates if c.static_status in ("infeasible", "unsupported")
    )
    observed_pass = sum(1 for c in viable if c.sla_status == "observed_pass")
    observed_fail = sum(1 for c in viable if c.sla_status == "observed_fail")
    estimated = sum(1 for c in viable if c.sla_status == "estimated_only")
    not_evaluable = sum(1 for c in viable if c.sla_status in (None, "not_evaluable"))
    observed = observed_pass + observed_fail
    excluded_note = (
        f"{excluded} 个候选已被资源或硬件排除；" if excluded else ""
    )
    sla_configured = result.request_echo.get("sla") is not None
    if not sla_configured:
        # 没设阈值 ≠ 找不到证据：这是用户主动关掉的判定维度
        perf_chip = _chip("off", "未设置")
        perf_text = "本次没有设置 SLA 阈值，未做达标/不达标判定。"
    elif not viable:
        if total and excluded == total:
            perf_chip = _chip("fail", "被资源排除")
            perf_text = (
                "全部候选都被资源或硬件约束排除，性能侧无法进入实测判定；"
                "补测不能解决，需要换支持的硬件或拓扑。"
            )
        else:
            perf_chip = _chip("off", "资源未确认")
            perf_text = (
                "没有能确认静态可行的候选（资源信息不足或不受支持），"
                "性能侧无法评估；先补齐资源信息。"
            )
    elif observed:
        kind = "ok" if observed_pass else "fail"
        perf_chip = _chip(kind, "有实测结论")
        perf_text = (
            f"{excluded_note}{observed} 个可评估候选有实测 SLA 结论"
            f"（通过 {observed_pass}，未通过 {observed_fail}）；"
            f"{estimated} 个仅有估算，{not_evaluable} 个不可评估。"
        )
    else:
        estimated_note = (
            f"{estimated} 个仅有估算（不能当作达标），" if estimated else ""
        )
        perf_chip = _chip("off", "证据不足")
        perf_text = (
            f"{excluded_note}0 个可评估候选有实测 SLA 结论，{estimated_note}"
            f"{not_evaluable} 个不可评估。"
            "这是证据缺口，需要补测，不能用估算替代。"
        )

    publishable = sum(1 for c in candidates if c.quote_publishable)
    governance = result.domains.get("governance", "not_configured")
    if publishable:
        quote_chip = _chip("ok", "可发布")
        quote_text = f"{publishable} 个候选满足报价发布门槛。"
    else:
        quote_chip = _chip("off", "不可发布")
        quote_text = (
            f"0 个候选可发布正式报价；治理领域状态为「{DOMAIN_TEXT.get(governance, governance)}」，"
            "生产发布保持关闭。"
        )

    return (
        "<table class='ledger'>"
        f"<tr><th>资源</th><td class='verdict'>{resource_chip}</td><td>{resource_text}</td></tr>"
        f"<tr><th>性能</th><td class='verdict'>{perf_chip}</td><td>{perf_text}</td></tr>"
        f"<tr><th>报价</th><td class='verdict'>{quote_chip}</td><td>{quote_text}</td></tr>"
        "</table>"
    )


def _sim_band(sim) -> str:
    if sim is None:
        return ""
    computed = sim.status == "computed"
    judged = sim.judged() if computed else []
    # 三个 verdict 是对全量候选的划分；judged() 额外要求"可比"，
    # 因此达标/未达标数也必须从全量候选里数，否则会漏掉不可比的预测。
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
            "<div class='callout pred'>"
            "<b>模拟预测不是实测</b>：本次模拟状态为 "
            + _esc(sim.status)
            + "，没有可用的预测候选。"
            "</div>"
        )

    if recommended is not None:
        verdict = (
            "推荐：<b>"
            + _esc(recommended.candidate_id)
            + "</b>，预测满足阈值（预测不等于实测）。"
        )
    elif best is not None:
        verdict = (
            "未达标参考配置：<b>"
            + _esc(best.candidate_id)
            + "</b>。预测未满足阈值，只用来说明最好能到哪一步，不是达标建议。"
        )
    else:
        verdict = "没有通过身份、资源、P99 与预算门槛的模拟候选。"

    return (
        "<div class='callout pred'>"
        "<b>模拟预测不是实测</b>："
        f"共 {total} 个候选（其中不可比 {not_comparable} 个）："
        f"预测达标 {passed}，预测未达标 {failed}，预测不充分 {insufficient}；"
        f"可判定 {len(judged)} 个。{verdict}"
        "模拟结果不会写成 BenchmarkRun，也不会获得 observed_pass。"
        "</div>"
    )


def _render_result(outcome_obj, is_stale: bool) -> None:
    result = outcome_obj.result
    sim = getattr(result, "simulation", None)
    css, label, description = PLAN_STATUS.get(result.status, ("off", result.status, ""))

    st.markdown(
        "<div class='result-head'>"
        "<div>"
        "<div class='runline'>"
        f"<span>评估编号 <b>{_esc(result.request_id)}</b></span>"
        f"<span>生成于 {_esc(result.generated_at.isoformat(timespec='minutes'))}</span>"
        f"<span>状态码 {_esc(result.status)}</span>"
        "</div>"
        "<h2>本次评估卷宗</h2>"
        f"<p>{_esc(description)}</p>"
        "</div>"
        f"<div class='seal {css}'><span>{label}</span><small>{_esc(result.status)}</small></div>"
        "</div>",
        unsafe_allow_html=True,
    )

    if is_stale:
        st.markdown(
            "<div class='callout warn'>需求已改动：左侧当前值与这份结果对应的需求不同。"
            "重新生成会新建运行目录，不会覆盖这份结果。</div>",
            unsafe_allow_html=True,
        )

    st.markdown(_rail(outcome_obj), unsafe_allow_html=True)
    st.markdown(_ledger(result), unsafe_allow_html=True)
    st.markdown(_sim_band(sim), unsafe_allow_html=True)

    # ---- 候选配置 ----
    st.markdown(
        f"<div class='sec'><h3>候选配置</h3><span>{len(result.candidates)} 个候选</span></div>",
        unsafe_allow_html=True,
    )
    if result.candidates:
        rows = []
        for candidate in result.candidates:
            static_kind, static_text = STATIC.get(
                candidate.static_status, ("off", candidate.static_status)
            )
            sla_kind, sla_text = SLA.get(
                candidate.sla_status, ("off", candidate.sla_status or "未评估")
            )
            cost = (
                _money(candidate.total_cash_over_horizon, candidate.currency)
                if not candidate.cost_incomplete
                else "<span class='muted'>不完整</span>"
            )
            rows.append(
                [
                    f"<b>TP{candidate.tp_size} × DP{candidate.dp_size}</b>"
                    f"<span class='ident'>{_esc(candidate.candidate_id)}</span>",
                    f"{_num(candidate.gpu_count, 0)}",
                    _chip(static_kind, static_text),
                    _chip(sla_kind, sla_text),
                    _num(candidate.output_throughput_tok_s),
                    cost,
                    _chip("ok", "可发布") if candidate.quote_publishable else _chip("off", "不可发布"),
                ]
            )
        st.markdown(
            _table(
                ["并行 / 候选", "卡数", "静态可行", "SLA", "吞吐 tok/s", "全周期现金", "报价"],
                rows,
                numeric=(1, 4, 5),
            ),
            unsafe_allow_html=True,
        )

        with st.expander("候选判定的依据（静态原因 / 匹配等级 / 成本缺口）"):
            for candidate in result.candidates:
                st.markdown(
                    f"**TP{candidate.tp_size} × DP{candidate.dp_size}** "
                    f"（{_esc(candidate.candidate_id)}）"
                )
                lines = []
                if candidate.static_reasons:
                    lines.append("静态：" + "；".join(candidate.static_reasons))
                if candidate.match_level:
                    lines.append("匹配：" + MATCH.get(candidate.match_level, candidate.match_level))
                if candidate.evidence_run_ids:
                    lines.append("证据 run：" + "、".join(candidate.evidence_run_ids))
                if candidate.coverage_gaps:
                    lines.append("成本缺口：" + "；".join(candidate.coverage_gaps))
                if candidate.budget_status:
                    lines.append("预算：" + candidate.budget_status)
                if not lines:
                    lines.append("没有额外说明。")
                st.markdown(
                    "<ul class='plain'>"
                    + "".join(f"<li>{_inline(line)}</li>" for line in lines)
                    + "</ul>",
                    unsafe_allow_html=True,
                )
    else:
        st.markdown(
            "<div class='callout fail'>没有候选：先检查阻断原因与缺失输入。</div>",
            unsafe_allow_html=True,
        )

    # ---- 性能与 SLA ----
    st.markdown(
        "<div class='sec'><h3>性能与 SLA</h3>"
        "<span>只有实测能给出达标/不达标</span></div>",
        unsafe_allow_html=True,
    )
    perf_rows = []
    for candidate in result.candidates:
        sla_kind, sla_text = SLA.get(
            candidate.sla_status, ("off", candidate.sla_status or "未评估")
        )
        perf_rows.append(
            [
                f"TP{candidate.tp_size} × DP{candidate.dp_size}",
                MATCH.get(candidate.match_level or "", candidate.match_level or "—"),
                _chip(sla_kind, sla_text),
                _num(candidate.output_throughput_tok_s),
                _num(candidate.ttft_p99_ms),
                _num(candidate.tpot_p99_ms),
                f"{len(candidate.evidence_run_ids)}",
            ]
        )
    if perf_rows:
        st.markdown(
            _table(
                ["候选", "匹配等级", "SLA", "吞吐 tok/s", "TTFT P99 ms", "TPOT P99 ms", "证据 run"],
                perf_rows,
                numeric=(3, 4, 5, 6),
            ),
            unsafe_allow_html=True,
        )
    if result.external_references:
        refs = result.external_references[:3]
        usable = [
            ref
            for ref in refs
            if (ref.match_level or "") in ("exact_match", "comparable_reference")
        ]
        st.markdown(
            "<div class='sec'><h3>外部公开实测参考</h3>"
            f"<span>{len(refs)} 条，不参与本次判定</span></div>",
            unsafe_allow_html=True,
        )
        if usable:
            st.markdown(
                "<div class='callout'>以下参考与需求的配置接近，可用于对照；"
                "但仍然不进入 SLA 判定与报价门禁。</div>",
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                "<div class='callout warn'>本次没有可直接对照的公开实测："
                f"以下是最接近的 {len(refs)} 条，均因身份差异或字段缺失被排除。"
                "它们只能说明业界水平，不能作为本需求的性能证据。</div>",
                unsafe_allow_html=True,
            )
        ref_rows = []
        for ref in refs:
            config = (
                f"{_esc(ref.model_name or '未知模型')}"
                f" / {_esc(ref.weight_precision or '精度未知')}"
                f"<span class='ident'>{_esc(ref.hardware_model or '硬件未知')}"
                f" × {_num(ref.gpu_count, 0)} 卡 · 并发 {_num(ref.configured_concurrency, 0)}</span>"
            )
            source = f"{_esc(ref.publisher or '未署名')}"
            url = _safe_url(ref.source_url)
            if url:
                source += (
                    f"<span class='ident'><a href='{_esc(url)}' target='_blank' "
                    "rel='noopener'>查看原文</a></span>"
                )
            else:
                source += f"<span class='ident'>{_esc(ref.run_id)}</span>"
            ref_rows.append(
                [
                    source,
                    config,
                    _num(ref.output_throughput_tok_s),
                    _num(ref.ttft_p99_ms),
                    _num(ref.tpot_p99_ms),
                    _chip(
                        "ok"
                        if (ref.match_level or "") in ("exact_match", "comparable_reference")
                        else "off",
                        MATCH.get(ref.match_level or "", ref.match_level or "—"),
                    ),
                ]
            )
        st.markdown(
            _table(
                ["来源", "配置", "吞吐 tok/s", "TTFT P99 ms", "TPOT P99 ms", "匹配等级"],
                ref_rows,
                numeric=(2, 3, 4),
            ),
            unsafe_allow_html=True,
        )
        with st.expander("这些参考为什么不能直接使用"):
            for ref in refs:
                st.markdown(
                    f"**{_esc(ref.publisher or '未署名')}**"
                    f"（{_esc(ref.model_name or '未知模型')}）"
                )
                lines = []
                if ref.same_conditions:
                    lines.append("条件相同：" + "；".join(ref.same_conditions))
                if ref.not_usable_reasons:
                    lines.append("不可用：" + "；".join(ref.not_usable_reasons))
                st.markdown(
                    "<ul class='plain'>"
                    + "".join(f"<li>{_inline(line)}</li>" for line in lines)
                    + "</ul>",
                    unsafe_allow_html=True,
                )
        st.markdown(
            "<p class='hint'>完整差异清单见技术报告 §2.1；这些数据不进入 SLA 判定与报价门禁。</p>",
            unsafe_allow_html=True,
        )

    # ---- 成本与预算 ----
    st.markdown(
        "<div class='sec'><h3>成本与预算</h3>"
        "<span>示例成本目录，不是报价</span></div>",
        unsafe_allow_html=True,
    )
    cost_rows = []
    for candidate in result.candidates:
        budget_kind, budget_text = BUDGET.get(
            candidate.budget_status or "not_configured", ("off", "未配置")
        )
        cost_rows.append(
            [
                f"TP{candidate.tp_size} × DP{candidate.dp_size}",
                _esc(candidate.currency or "—"),
                _num(candidate.horizon_years, 0),
                _money(candidate.capex_one_time, candidate.currency),
                _money(candidate.annual_cash_opex, candidate.currency),
                _money(candidate.total_cash_over_horizon, candidate.currency),
                _chip("warn", "不完整") if candidate.cost_incomplete else _chip("ok", "完整"),
                _chip(budget_kind, budget_text),
            ]
        )
    if cost_rows:
        st.markdown(
            _table(
                ["候选", "币种", "周期(年)", "一次性", "年现金支出", "全周期现金", "完整性", "预算"],
                cost_rows,
                numeric=(2, 3, 4, 5),
            ),
            unsafe_allow_html=True,
        )

    # ---- 模拟预测 ----
    if sim is not None:
        st.markdown(
            "<div class='sec'><h3>模拟预测</h3>"
            "<span>simulated_prediction · 不是实测</span></div>",
            unsafe_allow_html=True,
        )
        tool = sim.tool
        st.markdown(
            _spec(
                [
                    ("工具", f"{_esc(tool.tool or '—')} {_esc(tool.tool_version or '')}"),
                    ("形态", _esc(SURFACE.get(tool.surface, tool.surface))),
                    (
                        "后端",
                        f"{_esc(tool.backend or '—')} {_esc(tool.backend_version or '')}",
                    ),
                    ("系统", _esc(tool.system or "—")),
                    ("搜索空间", _esc(tool.search_space_file or "—")),
                    ("排名目标", "吞吐优先" if (sim.ranking or "throughput") == "throughput" else "成本优先"),
                ]
            ),
            unsafe_allow_html=True,
        )
        if sim.status == "computed" and sim.candidates:
            sim_rows = []
            for candidate in sim.candidates:
                kind, text = VERDICT.get(candidate.verdict, ("off", candidate.verdict))
                sim_rows.append(
                    [
                        f"<b>{_esc(candidate.deployment.parallelism_label or '—')}</b>"
                        f"<span class='ident'>{_esc(candidate.candidate_id)}</span>",
                        f"{_num(candidate.deployment.gpu_count, 0)}",
                        _num(candidate.metrics.sim_output_throughput_tok_s),
                        _num(candidate.metrics.sim_p99_ttft_ms),
                        _num(candidate.metrics.sim_p99_tpot_ms),
                        _chip(kind, text),
                        _chip("ok", "可比") if candidate.comparable else _chip("off", "不可比"),
                    ]
                )
            st.markdown(
                _table(
                    ["并行 / 候选", "卡数", "吞吐 tok/s", "TTFT P99 ms", "TPOT P99 ms", "预测结论", "可比性"],
                    sim_rows,
                    numeric=(1, 2, 3, 4),
                ),
                unsafe_allow_html=True,
            )
            if (sim.ranking or "throughput") == "cost":
                available, reasons = sim.cost_ranking_available()
                if available:
                    st.markdown(
                        "<div class='callout'>成本排序已生效：所有参与候选的成本完整且币种已确认。</div>",
                        unsafe_allow_html=True,
                    )
                else:
                    st.markdown(
                        "<div class='callout warn'>成本排序未生效，已降级为吞吐优先："
                        + _esc("；".join(reasons))
                        + "</div>",
                        unsafe_allow_html=True,
                    )
        elif sim.status == "computed":
            st.markdown(
                "<div class='callout warn'>模拟已计算，但没有产生候选。</div>",
                unsafe_allow_html=True,
            )

        if sim.limitations:
            with st.expander("模拟数据的限制"):
                st.markdown(
                    "<ul class='plain'>"
                    + "".join(f"<li>{_inline(item)}</li>" for item in sim.limitations)
                    + "</ul>",
                    unsafe_allow_html=True,
                )
        if sim.candidates:
            with st.expander("未被推荐的原因（可比性 / 资源 / 预算 / 预测）"):
                for candidate in sim.candidates:
                    reasons = sim.rejection_reasons(candidate)
                    if not reasons:
                        continue
                    st.markdown(f"**{_esc(candidate.candidate_id)}**")
                    st.markdown(
                        "<ul class='plain'>"
                        + "".join(f"<li>{_inline(item)}</li>" for item in reasons)
                        + "</ul>",
                        unsafe_allow_html=True,
                    )

    # ---- 阻断与推荐依据 ----
    if result.blockers:
        st.markdown(
            "<div class='sec'><h3>阻断原因</h3>"
            f"<span>{len(result.blockers)} 项</span></div>",
            unsafe_allow_html=True,
        )
        st.markdown(
            "<ul class='plain'>"
            + "".join(
                f"<li><b>{_esc(blocker.domain)}</b> / {_esc(blocker.code)}："
                f"{_inline(blocker.message)}</li>"
                for blocker in result.blockers
            )
            + "</ul>",
            unsafe_allow_html=True,
        )

    st.markdown(
        f"<div class='sec'><h3>推荐依据与限制</h3>"
        f"<span>{len(result.recommendations)} 条依据</span></div>",
        unsafe_allow_html=True,
    )
    if result.recommendations:
        st.markdown(
            "<ul class='plain'>"
            + "".join(f"<li>{_inline(item)}</li>" for item in result.recommendations)
            + "</ul>",
            unsafe_allow_html=True,
        )
    if result.missing_inputs or result.next_data_needed:
        with st.expander("还缺什么数据"):
            items = list(result.missing_inputs) + list(result.next_data_needed)
            st.markdown(
                "<ul class='plain'>"
                + "".join(f"<li>{_inline(item)}</li>" for item in items)
                + "</ul>",
                unsafe_allow_html=True,
            )
    if result.limitations:
        with st.expander("本次结论的适用边界"):
            st.markdown(
                "<ul class='plain'>"
                + "".join(f"<li>{_inline(item)}</li>" for item in result.limitations)
                + "</ul>",
                unsafe_allow_html=True,
            )

    # ---- 产物 ----
    st.markdown(
        "<div class='sec'><h3>方案文档与产物</h3>"
        "<span>本次运行独立目录</span></div>",
        unsafe_allow_html=True,
    )
    d1, d2, d3 = st.columns(3)
    d1.download_button(
        "部署方案文档",
        data=outcome_obj.proposal_md.encode("utf-8"),
        file_name="deployment_proposal.md",
        mime="text/markdown",
        width="stretch",
    )
    d2.download_button(
        "技术评估报告",
        data=outcome_obj.technical_md.encode("utf-8"),
        file_name="technical_report.md",
        mime="text/markdown",
        width="stretch",
    )
    d3.download_button(
        "结构化结果 JSON",
        data=json.dumps(outcome_obj.result_json, ensure_ascii=False, indent=2).encode("utf-8"),
        file_name="result.json",
        mime="application/json",
        width="stretch",
    )
    st.markdown(
        f"<p class='hint'>产物目录：<b>{_esc(outcome_obj.out_dir)}</b></p>",
        unsafe_allow_html=True,
    )
    with st.expander("部署方案文档预览"):
        st.markdown(outcome_obj.proposal_md)


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
        _render_result(outcome, stale)
    elif not error:
        _render_empty(current_values)

st.markdown(
    "<p class='hint' style='margin-top:44px;border-top:1px solid #CFD5CB;padding-top:14px'>"
    "边界：模拟预测不是实测，不会写入实测证据；成本来自示例目录；"
    "生产报价与审批发布保持禁用。界面不实现任何计算，全部结论来自 evaluate_plan()。"
    "</p>",
    unsafe_allow_html=True,
)
