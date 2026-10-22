"""S2–S4：一键 Demo、方案文档与界面外壳。

分工：

* ``demo_service`` —— 一份需求 → 建库 → 评估 → 两份报告（S2/S3 共用）；
* ``build_deployment_proposal`` —— 7 段式客户版文档（S4）；
* ``app/streamlit_app.py`` —— 只做输入/展示/触发（用 ``AppTest`` 无头验证）。
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from llm_capacity_pricing.app.demo_service import (
    DemoInputs,
    build_plan_request,
    new_run_dir,
    prepare_demo_db,
    run_demo,
    summary_lines,
)
from llm_capacity_pricing.reporting.proposal import build_deployment_proposal
from llm_capacity_pricing.workflow import evaluate_plan, load_plan_request

ROOT = Path(__file__).resolve().parents[1]

PROPOSAL_SECTIONS = (
    "## 1. 客户需求概览",
    "## 2. 模型与硬件资源分析",
    "## 3. 候选部署配置",
    "## 4. 性能预测与 SLA 判断",
    "## 5. 成本与预算分析",
    "## 6. 推荐配置及依据",
    "## 7. 限制、风险与待验证事项",
)


# --------------------------------------------------------------------------
# S2：请求构建与数据库准备
# --------------------------------------------------------------------------


def test_request_carries_the_user_requirements():
    request = build_plan_request(DemoInputs())
    assert request.model_id == "qwen3-32b"
    assert request.max_total_gpus == 8
    assert request.workload.input_len == 8192
    assert request.workload.concurrency == 32
    assert request.sla is not None and request.sla.ttft_p99_ms_max == Decimal("400")
    assert request.budget is not None and request.budget.amount == Decimal("3000000")
    assert request.advanced.weight_precision == "bf16"


@pytest.mark.parametrize("mode", ["offline", "live", "core"])
def test_simulation_mode_mapping(mode):
    request = build_plan_request(DemoInputs(mode=mode))
    if mode == "core":
        assert request.simulation is None
    elif mode == "offline":
        assert request.simulation is not None
        assert request.simulation.enable_external is False
        assert request.simulation.sweeper_csv
    else:
        assert request.simulation is not None
        assert request.simulation.enable_external is True
        assert request.simulation.auto_search is True


def test_missing_sla_or_budget_is_not_invented():
    request = build_plan_request(
        DemoInputs(ttft_p99_ms_max=None, tpot_p99_ms_max=None, budget_amount=None)
    )
    assert request.sla is None
    assert request.budget is None


def test_prepare_demo_db_refuses_to_overwrite(tmp_path):
    db = tmp_path / "demo.db"
    prepare_demo_db(db)
    assert db.exists()
    with pytest.raises(FileExistsError):
        prepare_demo_db(db)


def test_new_run_dir_is_unique(tmp_path):
    # X1.3：目录由 new_run_dir 原子创建，调用方不再自己 mkdir
    first = new_run_dir(tmp_path)
    second = new_run_dir(tmp_path)
    assert first != second
    assert first.is_dir() and second.is_dir()


# --------------------------------------------------------------------------
# S2/S4：离线一键 Demo 与 7 段式文档
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def offline_outcome(tmp_path_factory):
    out = tmp_path_factory.mktemp("sysdemo") / "run"
    return run_demo(DemoInputs(mode="offline"), out_dir=out)


def test_offline_demo_writes_three_artifacts(offline_outcome):
    assert offline_outcome.proposal_path.exists()
    assert offline_outcome.technical_path.exists()
    assert offline_outcome.json_path.exists()
    payload = json.loads(offline_outcome.json_path.read_text(encoding="utf-8"))
    assert payload["domains"]["sizing"] == "computed"
    assert payload["simulation"]["data_trust"] == "simulated_prediction"


def test_proposal_has_the_seven_sections(offline_outcome):
    md = offline_outcome.proposal_md
    for section in PROPOSAL_SECTIONS:
        assert section in md, section


def test_proposal_labels_data_classes_and_never_claims_observed_pass(offline_outcome):
    md = offline_outcome.proposal_md
    # 四类数字的措辞必须出现，便于读者区分
    assert "模拟预测" in md
    assert "公开实测参考" in md
    assert "假设成本" in md
    assert "实测证据" in md
    # 没有实测就不能声称达标
    assert "observed_pass" not in md
    assert "实测达标」" not in md
    assert "不给出实测达标结论" in md
    # 模拟不达标时要明说
    assert "模拟预测**未**满足阈值" in md


def test_proposal_does_not_invent_numbers(offline_outcome):
    """文档里出现的候选数值必须能在结构化结果里找到（防编造）。"""
    md = offline_outcome.proposal_md
    payload = offline_outcome.result_json
    known = set()

    def collect(value):
        if isinstance(value, dict):
            for item in value.values():
                collect(item)
        elif isinstance(value, list):
            for item in value:
                collect(item)
        elif isinstance(value, str):
            known.add(value)
        elif value is not None:
            known.add(str(value))

    collect(payload)
    for token in ("5201.827252000001", "31.45861707450993", "990.888548064229"):
        assert token in md, token
        assert token in known, f"{token} 出现在文档里但不在结构化结果中"


def test_simulation_does_not_add_any_benchmark_run(tmp_path):
    """评估前后库里的 run 数不变：模拟结果不写 BenchmarkRun。

    注意 Demo 库里本来就有 14 条**公开参考**（供报告的外部对照用），
    所以这里比的是"评估前后是否新增"，以及"有没有出现模拟/内部实测的可信等级"。
    """
    from llm_capacity_pricing.services import open_db

    out = tmp_path / "iso"
    out.mkdir()
    db = prepare_demo_db(out / "demo.db")

    def snapshot():
        dbh, repo = open_db(str(db))
        try:
            with dbh.session() as session:
                return [(r.run_id, r.data_trust) for r in repo.list_benchmark_runs(session)]
        finally:
            dbh.dispose()

    before = snapshot()
    evaluate_plan(build_plan_request(DemoInputs(mode="offline")), db_path=str(db))
    after = snapshot()
    assert after == before, "评估过程不得新增/修改 BenchmarkRun"
    assert all(trust in ("measured_vendor_published", "measured_third_party") for _id, trust in after)
    assert not any(trust == "measured_internal" for _id, trust in after)


def test_repeat_runs_do_not_share_artifacts(tmp_path):
    first = run_demo(DemoInputs(mode="offline"), out_dir=tmp_path / "a")
    second = run_demo(DemoInputs(mode="offline"), out_dir=tmp_path / "b")
    assert first.out_dir != second.out_dir
    assert first.db_path != second.db_path
    assert first.proposal_path != second.proposal_path


# --------------------------------------------------------------------------
# S5 需要的异常场景
# --------------------------------------------------------------------------


def test_core_mode_has_no_simulation_values(tmp_path):
    outcome = run_demo(DemoInputs(mode="core"), out_dir=tmp_path / "core")
    assert outcome.result.simulation is None
    assert "本次未启用" in outcome.proposal_md
    # 其余三个领域照常
    assert outcome.result.domains["sizing"] == "computed"


def test_live_mode_without_tool_still_produces_a_report(tmp_path, monkeypatch):
    """AISimulate 不可用：报告照常出，只是模拟章节写明原因。"""
    monkeypatch.setenv("AISIMULATE_PYTHON", "/nonexistent/python")
    outcome = run_demo(DemoInputs(mode="live"), out_dir=tmp_path / "live")
    sim = outcome.result.simulation
    assert sim is not None and sim.status == "error"
    assert outcome.proposal_path.exists()
    assert "模拟未产出结论" in outcome.proposal_md
    assert outcome.result.domains["sizing"] == "computed"


def test_hardware_shortage_is_reported_not_raised(tmp_path):
    """卡数上限太小：候选静态不可行，但仍要出报告。"""
    outcome = run_demo(
        DemoInputs(mode="offline", gpu_count=1), out_dir=tmp_path / "tiny"
    )
    assert outcome.result.candidates
    assert all(
        c.static_status in ("infeasible", "conditional_feasible", "insufficient_information")
        for c in outcome.result.candidates
    )
    assert outcome.proposal_path.exists()


def test_incomplete_cost_never_becomes_a_total(tmp_path):
    outcome = run_demo(DemoInputs(mode="offline"), out_dir=tmp_path / "cost")
    for candidate in outcome.result.candidates:
        if candidate.cost_incomplete:
            assert candidate.total_cash_over_horizon is None
    assert "不完整" in outcome.proposal_md


def test_summary_lines_only_restate_structured_fields(offline_outcome):
    lines = summary_lines(offline_outcome)
    assert any("整体状态" in line for line in lines)
    assert any("模拟" in line for line in lines)


# --------------------------------------------------------------------------
# S3：界面外壳（无头执行脚本，确认没有运行时异常）
# --------------------------------------------------------------------------


def _select(app, label: str, value: str):
    widget = next(item for item in app.selectbox if item.label == label)
    widget.set_value(value).run()


def test_streamlit_app_runs_headlessly():
    st_testing = pytest.importorskip("streamlit.testing.v1")
    AppTest = st_testing.AppTest
    app = AppTest.from_file(str(ROOT / "app" / "streamlit_app.py"), default_timeout=300)
    app.run()
    assert not app.exception, app.exception
    # 只读控件：模型/GPU/负载/SLA/预算
    assert len(app.selectbox) >= 3
    assert len(app.number_input) >= 6


def test_streamlit_interaction_hints(monkeypatch):
    """界面上的联动提示必须跟着输入变化，而不是写死一段静态说明。"""
    st_testing = pytest.importorskip("streamlit.testing.v1")
    AppTest = st_testing.AppTest
    import streamlit as st

    monkeypatch.setattr(
        "llm_capacity_pricing.perf.aisimulate_adapter.resolve_python",
        lambda *args, **kwargs: None,
    )
    st.cache_data.clear()
    app = AppTest.from_file(str(ROOT / "app" / "streamlit_app.py"), default_timeout=120)
    app.run()

    def text() -> str:
        return "\n".join(item.value for item in app.markdown)

    assert "hgx-h100-8gpu" in text()

    # GPU 换成 H200：机箱必须跟着换，否则候选会被判 server_gpu_sku_mismatch
    _select(app, "GPU 型号", "nvidia-h200-sxm-141gb")
    assert "hgx-h200-8gpu" in text()

    # 目录里没有匹配机箱的 GPU：不能说成 server_gpu_sku_mismatch
    _select(app, "GPU 型号", "nvidia-h100-nvl-94gb")
    assert "没有与该 GPU 匹配的机箱" in text()
    assert "server_gpu_sku_mismatch" not in text()

    # 离线归档是单一工作点：偏离项必须逐条列出
    _select(app, "模型精度", "fp8")
    assert "与离线归档工作点不一致" in text()
    assert "精度：当前 fp8，归档 bf16" in text()

    # 成本排序的生效条件
    next(r for r in app.radio if r.label == "排序目标").set_value("cost").run()
    assert "成本排序只在所有候选成本完整" in text()

    # 没有 AISimulate 时实时模式必须禁用，而不是等运行失败
    _select(app, "模拟数据来源", "live")
    assert "未检测到 AISimulate" in text()
    assert app.button[0].disabled is True

    # 关掉 SLA / 预算：不再传约束，界面要说明后果
    app.toggle[0].set_value(False).run()
    app.toggle[1].set_value(False).run()
    assert "不设阈值时" in text()
    assert "不设预算：候选仍会算成本" in text()
    assert not app.exception, app.exception


def test_streamlit_status_maps_cover_evaluator_literals():
    """界面状态映射必须覆盖后端真实取值，否则会把结论静默显示成“未配置”。"""
    import runpy
    from typing import get_args

    from llm_capacity_pricing.schemas.decision import BudgetCheck
    from llm_capacity_pricing.schemas.performance import SlaStatus
    from llm_capacity_pricing.schemas.simulation import SimulationVerdict
    from llm_capacity_pricing.schemas.sizing import FeasibilityStatus

    namespace = runpy.run_path(str(ROOT / "app" / "streamlit_app.py"))
    assert set(get_args(FeasibilityStatus)) <= set(namespace["STATIC"])
    assert set(get_args(SlaStatus)) <= set(namespace["SLA"])
    assert set(get_args(SimulationVerdict)) <= set(namespace["VERDICT"])
    budget_literals = set(
        get_args(BudgetCheck.model_fields["status"].annotation)
    )
    assert budget_literals <= set(namespace["BUDGET"])


def test_ledger_counts_estimated_only_candidates():
    """仅有估算既不是实测结论，也不是“不可评估”，必须单独计数。"""
    import runpy
    from types import SimpleNamespace as NS

    ledger = runpy.run_path(str(ROOT / "app" / "streamlit_app.py"))["_ledger"]
    result = NS(
        candidates=[
            NS(
                static_status="conditional_feasible",
                sla_status="estimated_only",
                quote_publishable=False,
            ),
            NS(
                static_status="conditional_feasible",
                sla_status="not_evaluable",
                quote_publishable=False,
            ),
        ],
        request_echo={"sla": {"ttft_p99_ms_max": 400}},
        domains={"governance": "not_configured"},
    )
    html = ledger(result)
    assert "1 个仅有估算" in html
    assert "1 个不可评估" in html
    assert "这是证据缺口" in html

    blocked = NS(
        candidates=[
            NS(
                static_status="infeasible",
                sla_status="not_evaluable",
                quote_publishable=False,
            ),
        ],
        request_echo={"sla": {"ttft_p99_ms_max": 400}},
        domains={"governance": "not_configured"},
    )
    blocked_html = ledger(blocked)
    assert "补测不能解决" in blocked_html
    assert "这是证据缺口" not in blocked_html

    mixed = NS(
        candidates=[
            NS(
                static_status="conditional_feasible",
                sla_status="not_evaluable",
                quote_publishable=False,
            ),
            NS(
                static_status="infeasible",
                sla_status="not_evaluable",
                quote_publishable=False,
            ),
        ],
        request_echo={"sla": {"ttft_p99_ms_max": 400}},
        domains={"governance": "not_configured"},
    )
    mixed_html = ledger(mixed)
    assert "1 个候选已被资源、硬件或 GPU 上限排除" in mixed_html
    # 证据缺口只统计还能进入性能评估的候选
    assert "1 个不可评估" in mixed_html


def test_insufficient_predictions_are_counted_outside_the_judged_pool():
    """sim.judged() 不含预测不充分的候选，汇总必须从全量候选中计数。"""
    st_testing = pytest.importorskip("streamlit.testing.v1")
    AppTest = st_testing.AppTest
    app = AppTest.from_file(str(ROOT / "app" / "streamlit_app.py"), default_timeout=300)
    app.run()
    app.button[0].click().run()

    text = "\n".join(item.value for item in app.markdown)
    assert "共 3 个候选（其中不可比 0 个）" in text
    assert "预测不充分 2" in text
    assert "可判定 1 个" in text
    assert not app.exception, app.exception


def test_live_mode_rejects_an_interpreter_that_cannot_import_aisimulate(monkeypatch):
    """解释器存在但导不进来时，实时模式必须仍被禁用。"""
    st_testing = pytest.importorskip("streamlit.testing.v1")
    AppTest = st_testing.AppTest
    import streamlit as st

    monkeypatch.setattr(
        "llm_capacity_pricing.perf.aisimulate_adapter.resolve_python",
        lambda *args, **kwargs: "/usr/bin/python3",
    )
    monkeypatch.setattr(
        "llm_capacity_pricing.perf.aisimulate_adapter.external_tool_available",
        lambda *args, **kwargs: False,
    )
    st.cache_data.clear()
    app = AppTest.from_file(str(ROOT / "app" / "streamlit_app.py"), default_timeout=120)
    app.run()
    _select(app, "模拟数据来源", "live")

    text = "\n".join(item.value for item in app.markdown)
    assert "无法导入 aiconfigurator / aisimulate" in text
    assert app.button[0].disabled is True
    assert not app.exception, app.exception


def test_fp8_offline_summary_counts_are_consistent():
    """不可比 + 预测未达标会同时出现：三个 verdict 数与不可比数必须自洽。"""
    st_testing = pytest.importorskip("streamlit.testing.v1")
    AppTest = st_testing.AppTest
    app = AppTest.from_file(str(ROOT / "app" / "streamlit_app.py"), default_timeout=300)
    app.run()
    _select(app, "模型精度", "fp8")
    app.button[0].click().run()

    text = "\n".join(item.value for item in app.markdown)
    assert "共 3 个候选（其中不可比 3 个）" in text
    assert "预测达标 0" in text
    assert "预测未达标 1" in text
    assert "预测不充分 2" in text
    assert "可判定 0 个" in text
    assert not app.exception, app.exception


def test_unset_sla_is_not_reported_as_an_evidence_gap():
    """关掉 SLA 阈值是“未配置”，不能被说成“找不到实测证据”。"""
    st_testing = pytest.importorskip("streamlit.testing.v1")
    AppTest = st_testing.AppTest
    app = AppTest.from_file(str(ROOT / "app" / "streamlit_app.py"), default_timeout=300)
    app.run()
    app.toggle[0].set_value(False).run()
    app.button[0].click().run()

    outcome = app.session_state["outcome"]
    assert outcome.result.request_echo["sla"] is None

    ledger = next(
        item.value
        for item in app.markdown
        if "本次没有设置 SLA 阈值" in item.value
    )
    assert "证据不足" not in ledger
    assert not app.exception, app.exception


def test_failed_regeneration_keeps_the_previous_result(monkeypatch, tmp_path):
    """新一次生成失败时，上一次成功的方案不能从界面上消失。"""
    st_testing = pytest.importorskip("streamlit.testing.v1")
    AppTest = st_testing.AppTest
    app = AppTest.from_file(str(ROOT / "app" / "streamlit_app.py"), default_timeout=300)
    app.run()
    app.button[0].click().run()
    assert "方案已生成" in "\n".join(item.value for item in app.markdown)

    def explode(*args, **kwargs):
        raise RuntimeError("boom")

    import llm_capacity_pricing.app.demo_service as demo_service

    monkeypatch.setattr(demo_service, "run_demo", explode)
    app.button[0].click().run()

    text = "\n".join(item.value for item in app.markdown)
    assert "生成失败" in text
    assert "RuntimeError: boom" in text
    assert "下方仍是上一次成功的评估结果" in text
    assert "方案已生成" in text
    assert not app.exception, app.exception


# --------------------------------------------------------------------------
# V1.1：页面减法、表格精简、语义色
# --------------------------------------------------------------------------


def _presenters():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "ui_presenters", ROOT / "app" / "ui_presenters.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_core_table_has_five_columns_and_gpu_cap():
    """主表最多 5 列，并把 GPU 上限显示为展示层条件（不改 static_status）。"""
    from types import SimpleNamespace as NS

    presenters = _presenters()
    result = NS(
        candidates=[
            NS(
                tp_size=8,
                dp_size=1,
                gpu_count=8,
                static_status="conditional_feasible",
                sla_status="not_evaluable",
                output_throughput_tok_s=None,
            )
        ],
        request_echo={"max_total_gpus": 4},
    )
    html = presenters.candidates_table_html(result)
    assert html.count("<th ") == 5
    assert "超过上限 4" in html
    assert "8 / 4" in html
    # 原始静态结论未被改写
    assert result.candidates[0].static_status == "conditional_feasible"


def test_headline_and_ledger_respect_gpu_cap():
    """GPU 超限的候选不能出现在“静态可行”的结论里。"""
    from types import SimpleNamespace as NS

    presenters = _presenters()
    result = NS(
        candidates=[
            NS(
                tp_size=8,
                dp_size=1,
                gpu_count=8,
                static_status="conditional_feasible",
                sla_status="not_evaluable",
                quote_publishable=False,
            ),
            NS(
                tp_size=4,
                dp_size=1,
                gpu_count=4,
                static_status="conditional_feasible",
                sla_status="not_evaluable",
                quote_publishable=False,
            ),
        ],
        request_echo={"sla": None, "max_total_gpus": 4},
        domains={"governance": "not_configured"},
        simulation=None,
    )
    sentence = presenters.headline(result)
    assert "1 个候选在 4 卡上限内静态可行" in sentence
    ledger = presenters.ledger_html(result)
    assert "1 个静态条件可行的候选超过 4 卡上限" in ledger


def test_headline_does_not_count_observed_evidence_of_over_cap_candidates():
    """GPU 超限的候选即使有实测通过，也不能算进“可用候选的实测结论”。"""
    from types import SimpleNamespace as NS

    presenters = _presenters()
    result = NS(
        candidates=[
            NS(
                tp_size=4,
                dp_size=1,
                gpu_count=4,
                static_status="conditional_feasible",
                sla_status="not_evaluable",
                quote_publishable=False,
            ),
            NS(
                tp_size=8,
                dp_size=1,
                gpu_count=8,
                static_status="conditional_feasible",
                sla_status="observed_pass",
                quote_publishable=False,
            ),
        ],
        request_echo={"sla": {"ttft_p99_ms_max": 400}, "max_total_gpus": 4},
        domains={"governance": "not_configured"},
        simulation=None,
    )
    sentence = presenters.headline(result)
    assert "1 个候选在 4 卡上限内静态可行" in sentence
    assert "可用候选中尚无实测 SLA 结论" in sentence
    assert "1 个候选不在当前可用范围内" in sentence
    assert "1 个可用候选有实测 SLA 结论" not in sentence


def test_predicted_pass_is_blue_observed_pass_is_green():
    presenters = _presenters()
    assert presenters.VERDICT["predicted_pass"][0] == "pred"
    assert presenters.VERDICT["predicted_fail"][0] == "fail"
    assert presenters.SLA["observed_pass"][0] == "ok"
    assert presenters.SLA["observed_fail"][0] == "fail"


def test_offline_mismatches_cover_all_workpoint_dimensions():
    presenters = _presenters()
    current = {
        "model_id": "other-model",
        "hardware_id": "nvidia-h200-sxm-141gb",
        "framework": "vllm",
        "framework_version": "0.24.0",
        "weight_precision": "fp8",
        "input_len": 4096,
        "output_len": 128,
        "concurrency": 64,
    }
    mismatches = presenters.offline_mismatches(current)
    assert len(mismatches) == 6
    joined = "；".join(mismatches)
    for label in ("模型", "GPU", "精度", "输入长度", "输出长度", "并发"):
        assert label in joined


def test_offline_mode_does_not_probe_aisimulate(monkeypatch):
    """离线页面不应触发任何外部工具探测。"""
    st_testing = pytest.importorskip("streamlit.testing.v1")
    AppTest = st_testing.AppTest
    import streamlit as st

    def boom(*args, **kwargs):
        raise AssertionError("offline mode must not probe AISimulate")

    monkeypatch.setattr(
        "llm_capacity_pricing.perf.aisimulate_adapter.resolve_python", boom
    )
    monkeypatch.setattr(
        "llm_capacity_pricing.perf.aisimulate_adapter.external_tool_available", boom
    )
    st.cache_data.clear()
    app = AppTest.from_file(str(ROOT / "app" / "streamlit_app.py"), default_timeout=120)
    app.run()
    assert not app.exception, app.exception


def test_result_homepage_prioritises_conclusion_and_downloads():
    """首页先给执行状态、关键结论、核心对比和下载，细节在折叠区。"""
    st_testing = pytest.importorskip("streamlit.testing.v1")
    AppTest = st_testing.AppTest
    app = AppTest.from_file(str(ROOT / "app" / "streamlit_app.py"), default_timeout=300)
    app.run()
    app.button[0].click().run()

    text = "\n".join(item.value for item in app.markdown)
    assert "方案已生成" in text
    assert "分析覆盖" in text
    assert "核心候选对比" in text
    assert "可用候选中尚无实测 SLA 结论" in text

    labels = [expander.label for expander in app.expander]
    assert "评估链路与三线结论" in labels
    assert "性能与 SLA 明细" in labels
    assert "成本与预算" in labels
    assert "模拟预测（不是实测）" in labels
    assert "推荐依据、阻断与适用边界" in labels
    assert not app.exception, app.exception


# --------------------------------------------------------------------------
# codex 复审指出的 4 个问题（P2）的反向用例
# --------------------------------------------------------------------------


def test_server_is_matched_to_the_selected_gpu():
    """选 H200 时不能用 H100 机箱：否则候选会被判 server_gpu_sku_mismatch。"""
    from llm_capacity_pricing.app.demo_service import server_for_hardware

    assert server_for_hardware("nvidia-h100-sxm-80gb") == "hgx-h100-8gpu"
    assert server_for_hardware("nvidia-h200-sxm-141gb") == "hgx-h200-8gpu"
    assert server_for_hardware("unknown-gpu") is None  # 不猜

    request = build_plan_request(DemoInputs(hardware_id="nvidia-h200-sxm-141gb"))
    assert request.server_id == "hgx-h200-8gpu"


def test_h200_demo_does_not_fail_on_server_mismatch(tmp_path):
    outcome = run_demo(
        DemoInputs(mode="offline", hardware_id="nvidia-h200-sxm-141gb"),
        out_dir=tmp_path / "h200",
    )
    assert outcome.result.domains["sizing"] == "computed"
    assert not any(
        "server_gpu_sku_mismatch" in " ".join(c.static_reasons)
        for c in outcome.result.candidates
    )


def test_zero_budget_is_a_real_constraint_not_unknown(monkeypatch):
    """预算 0 与"未给预算"必须区分（未知 ≠ 0）。"""
    request = build_plan_request(DemoInputs(budget_amount=Decimal("0")))
    assert request.budget is not None
    assert request.budget.amount == Decimal("0")

    request = build_plan_request(DemoInputs(budget_amount=None))
    assert request.budget is None


def test_offline_mode_honours_the_ranking_objective(tmp_path):
    """离线模式也要把 ranking 传下去，否则 --ranking cost 会被静默忽略。"""
    request = build_plan_request(DemoInputs(mode="offline", ranking="cost"))
    assert request.simulation is not None
    assert request.simulation.ranking == "cost"

    outcome = run_demo(
        DemoInputs(mode="offline", ranking="cost"), out_dir=tmp_path / "costrank"
    )
    assert outcome.result_json["simulation"]["ranking"] == "cost"


def test_existing_nonempty_out_dir_is_refused(tmp_path):
    """--out 指向已有产物目录时不得覆盖（历史结果要保留）。"""
    out = tmp_path / "used"
    out.mkdir()
    marker = out / "deployment_proposal.md"
    marker.write_text("old result", encoding="utf-8")

    with pytest.raises(FileExistsError) as excinfo:
        run_demo(DemoInputs(mode="offline"), out_dir=out)
    assert "非空" in str(excinfo.value)
    assert marker.read_text(encoding="utf-8") == "old result"


def test_live_work_dir_stays_inside_the_run_dir(tmp_path):
    """实时模式的搜索/回放产物必须落在本次运行目录下。"""
    from llm_capacity_pricing.app.demo_service import DemoInputs as DI

    out = tmp_path / "live"
    captured = {}

    import llm_capacity_pricing.app.demo_service as svc

    original = svc.evaluate_plan

    def spy(request, *, db_path):
        captured["work_dir"] = request.simulation.work_dir if request.simulation else None
        return original(request, db_path=db_path)

    svc.evaluate_plan = spy
    try:
        run_demo(DI(mode="live", ttft_p99_ms_max=None, tpot_p99_ms_max=None), out_dir=out)
    finally:
        svc.evaluate_plan = original

    assert captured["work_dir"] is not None
    assert str(out) in captured["work_dir"]
