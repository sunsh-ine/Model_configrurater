"""T 阶段：三个业务场景、成本排序门禁与场景切换。

约定：

* 场景只提供需求输入，所有结论都由 ``run_demo()`` 重新计算；
* 宽松 SLA 只改阈值，不修改任何预测值；
* 合成成本即使覆盖完整，也因“合成价格 + 币种未确认”不得给出正式成本最优；
* 成本排序的**算法**用一份明确标注的完整/已确认 fixture 单独验证。
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

import json

from llm_capacity_pricing.app import demo_cli
from llm_capacity_pricing.app.demo_service import DemoInputs, run_demo
from llm_capacity_pricing.app.scenarios import SCENARIOS, get_scenario, scenario_ids
from llm_capacity_pricing.schemas.simulation import (
    SimulatedCost,
    SimulationCandidate,
    SimulationDeployment,
    SimulationMetrics,
    SimulationResult,
)

ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# 场景定义
# ---------------------------------------------------------------------------


def test_scenarios_only_describe_requirements():
    assert scenario_ids() == ("strict", "relaxed", "cost")
    strict = get_scenario("strict").inputs
    relaxed = get_scenario("relaxed").inputs
    cost = get_scenario("cost").inputs

    # 宽松场景只改阈值、请求编号，不碰负载与其他条件
    for key in ("model_id", "hardware_id", "gpu_count", "input_len", "output_len", "concurrency"):
        assert strict[key] == relaxed[key]
    assert relaxed["ttft_p99_ms_max"] == Decimal("6000")
    assert relaxed["tpot_p99_ms_max"] == Decimal("40")
    assert relaxed["request_id"] != strict["request_id"]

    assert cost["ranking"] == "cost"
    assert cost["ttft_p99_ms_max"] == relaxed["ttft_p99_ms_max"]
    assert cost["budget_amount"] == Decimal("1000000")

    # 场景不携带任何“预期结论”字段
    for scenario in SCENARIOS:
        assert "verdict" not in scenario.inputs
        assert "recommended" not in scenario.inputs


# ---------------------------------------------------------------------------
# B2：宽松 SLA -> predicted_pass -> 推荐
# ---------------------------------------------------------------------------


def test_relaxed_sla_recommends_predicted_pass_without_changing_predictions(tmp_path):
    strict = run_demo(
        DemoInputs(**get_scenario("strict").inputs),
        out_dir=tmp_path / "strict",
        write_files=False,
    )
    relaxed = run_demo(
        DemoInputs(**get_scenario("relaxed").inputs),
        out_dir=tmp_path / "relaxed",
        write_files=False,
    )

    def native(result):
        return next(
            c
            for c in result.simulation.candidates
            if "sim-native" in c.candidate_id and "tp8pp1dp1" in c.candidate_id
        )

    before, after = native(strict.result), native(relaxed.result)
    # 阈值变化不能改变预测值
    assert before.metrics.sim_p99_ttft_ms == after.metrics.sim_p99_ttft_ms
    assert before.metrics.sim_p99_tpot_ms == after.metrics.sim_p99_tpot_ms
    assert before.verdict == "predicted_fail"
    assert after.verdict == "predicted_pass"
    assert after.comparable is True
    assert after.resource_feasible is True

    recommended = relaxed.result.simulation.recommended("throughput")
    assert recommended is not None
    assert recommended.verdict == "predicted_pass"
    # AD：同 8 卡 TP4×DP2 的吞吐高于 TP8×DP1
    assert (recommended.deployment.tp_size, recommended.deployment.dp_size) == (4, 2)
    # 仍然没有实测通过：预测达标不等于实测
    assert all(
        c.sla_status != "observed_pass" for c in relaxed.result.candidates
    )


# ---------------------------------------------------------------------------
# B3：成本与预算
# ---------------------------------------------------------------------------


def test_cost_scenario_refuses_formal_cost_optimum(tmp_path):
    scenario = get_scenario("cost")
    outcome = run_demo(
        DemoInputs(**scenario.inputs),
        out_dir=tmp_path / "cost",
        write_files=False,
    )
    sim = outcome.result.simulation
    assert sim is not None and sim.status == "computed"

    # 预测达标且可推荐，但成本排序不成立
    recommended = sim.recommended("cost")
    assert recommended is not None and recommended.verdict == "predicted_pass"
    available, reasons = sim.cost_ranking_available()
    assert available is False
    assert reasons
    assert any("币种" in reason or "成本不完整" in reason for reason in reasons)

    # 至少两个候选有可比的一次性/年现金成本
    with_components = [
        c
        for c in sim.candidates
        if c.cost is not None
        and c.cost.capex_one_time is not None
        and c.cost.annual_cash_opex is not None
    ]
    assert len(with_components) >= 2
    assert all(c.cost.incomplete for c in with_components)
    assert all(c.cost.currency_confirmed is False for c in with_components)

    # 预算：币种未确认 -> unknown（不是 within/over，也不是 0）
    assert all(c.budget_status == "unknown" for c in sim.candidates)
    # 无预算 = 未配置，且与预算 0 不是同一件事
    no_budget = DemoInputs(**{**scenario.inputs, "budget_amount": None})
    assert no_budget.budget_amount is None
    zero_budget = DemoInputs(**{**scenario.inputs, "budget_amount": Decimal("0")})
    assert zero_budget.budget_amount == Decimal("0")


def test_cost_ranking_mechanics_with_complete_confirmed_fixture():
    """算法级验证：成本完整且币种已确认时，成本优先选更便宜、吞吐优先选更快。"""

    def candidate(cid: str, total: str, throughput: str) -> SimulationCandidate:
        return SimulationCandidate(
            candidate_id=cid,
            deployment=SimulationDeployment(
                model_name="fixture-model",
                system="h100_sxm",
                gpu_count=8,
                tp_size=8,
                dp_size=1,
            ),
            metrics=SimulationMetrics(
                sim_output_throughput_tok_s=Decimal(throughput)
            ),
            verdict="predicted_pass",
            comparable=True,
            resource_feasible=True,
            within_gpu_budget=True,
            budget_status="within_budget",
            cost=SimulatedCost(
                hardware_id="nvidia-h100-sxm-80gb",
                currency="CNY",
                currency_confirmed=True,
                cost_basis="owned",
                horizon_years=3,
                capex_one_time=Decimal("0"),
                annual_cash_opex=Decimal("0"),
                total_cash_over_horizon=Decimal(total),
                incomplete=False,
            ),
        )

    result = SimulationResult(
        status="computed",
        candidates=[
            candidate("fast-expensive", "2000", "200"),
            candidate("slow-cheap", "1000", "100"),
        ],
    )
    available, reasons = result.cost_ranking_available()
    assert available, reasons
    assert result.recommended("cost").candidate_id == "slow-cheap"
    assert result.recommended("throughput").candidate_id == "fast-expensive"


# ---------------------------------------------------------------------------
# 演示说明与场景切换
# ---------------------------------------------------------------------------


def test_scenario_note_is_prepended_to_customer_report(tmp_path):
    scenario = get_scenario("relaxed")
    out = run_demo(
        DemoInputs(**scenario.inputs),
        out_dir=tmp_path / "noted",
        demo_note=scenario.note,
        scenario_id=scenario.scenario_id,
    )
    assert out.proposal_md.startswith("> 演示案例（宽松 SLA）")
    assert "不代表客户真实需求" in out.proposal_md
    assert out.technical_md.startswith("> 演示案例（宽松 SLA）")
    assert "不代表客户真实需求" in out.technical_md
    note_file = tmp_path / "noted" / "scenario_note.md"
    assert note_file.exists()
    assert "scenario_id: relaxed" in note_file.read_text(encoding="utf-8")


def test_cli_scenario_note_only_applies_to_offline_mode():
    scenario = get_scenario("strict")
    assert demo_cli._scenario_note(scenario, "offline") == scenario.note
    assert demo_cli._scenario_note(scenario, "core") is None
    assert demo_cli._scenario_note(scenario, "live") is None


def test_cli_scenario_honors_core_mode_and_drops_preset_note(tmp_path, capsys):
    out = tmp_path / "core"
    code = demo_cli.main(
        ["--scenario", "strict", "--mode", "core", "--out", str(out)]
    )
    assert code == 0
    payload = json.loads((out / "result.json").read_text(encoding="utf-8"))
    assert payload["simulation"] is None
    proposal = (out / "deployment_proposal.md").read_text(encoding="utf-8")
    assert not proposal.startswith("> 演示案例")
    assert not (out / "scenario_note.md").exists()
    assert "报告不附带预设说明" in capsys.readouterr().out


def test_cli_scenario_honors_top_n(monkeypatch, tmp_path):
    captured = {}

    class FakeOutcome:
        out_dir = tmp_path / "topn"
        stages = []
        proposal_path = technical_path = json_path = None

    def fake_run_demo(inputs, *, out_dir, demo_note=None, scenario_id=None):
        captured["top_n"] = inputs.top_n
        return FakeOutcome()

    monkeypatch.setattr(demo_cli, "run_demo", fake_run_demo)
    monkeypatch.setattr(demo_cli, "summary_lines", lambda outcome: [])
    code = demo_cli.main(
        ["--scenario", "strict", "--top-n", "1", "--out", str(tmp_path / "topn")]
    )
    assert code == 0
    assert captured["top_n"] == 1


@pytest.mark.parametrize("extra", [[], ["--scenario", "strict"]])
def test_cli_live_mode_builds_work_dir(monkeypatch, tmp_path, extra):
    """live 模式必须先能构造 work_dir（曾经的 NameError 回归）。"""
    captured = {}

    class FakeOutcome:
        out_dir = tmp_path / "live"
        stages = []
        proposal_path = technical_path = json_path = None

    def fake_run_demo(inputs, *, out_dir, demo_note=None, scenario_id=None):
        captured["mode"] = inputs.mode
        captured["work_dir"] = inputs.work_dir
        return FakeOutcome()

    monkeypatch.setattr(demo_cli, "run_demo", fake_run_demo)
    monkeypatch.setattr(demo_cli, "summary_lines", lambda outcome: [])

    code = demo_cli.main(
        [*extra, "--mode", "live", "--out", str(tmp_path / "live")]
    )
    assert code == 0
    assert captured["mode"] == "live"
    assert captured["work_dir"] is not None
    assert "sim_work" in captured["work_dir"]


def test_switching_to_custom_marks_preset_result_stale():
    """场景选择也属于结果身份：切到 custom 后必须提示旧结果不属于当前选择。"""
    st_testing = pytest.importorskip("streamlit.testing.v1")
    AppTest = st_testing.AppTest
    app = AppTest.from_file(str(ROOT / "app" / "streamlit_app.py"), default_timeout=300)
    app.run()
    app.button[0].click().run()
    assert "方案已生成" in "\n".join(item.value for item in app.markdown)

    scenario_box = next(s for s in app.selectbox if s.label == "演示案例")
    scenario_box.set_value("custom").run()
    text = "\n".join(item.value for item in app.markdown)
    assert "需求已改动" in text
    assert not app.exception, app.exception


def test_ui_scenario_selector_applies_preset_and_generates():
    st_testing = pytest.importorskip("streamlit.testing.v1")
    AppTest = st_testing.AppTest
    app = AppTest.from_file(str(ROOT / "app" / "streamlit_app.py"), default_timeout=300)
    app.run()

    scenario_box = next(s for s in app.selectbox if s.label == "演示案例")
    assert scenario_box.value == "strict"

    scenario_box.set_value("relaxed").run()
    ttft = next(n for n in app.number_input if n.label == "TTFT P99（ms）")
    tpot = next(n for n in app.number_input if n.label == "TPOT P99（ms）")
    assert ttft.value == 6000.0
    assert tpot.value == 40.0

    app.button[0].click().run()
    text = "\n".join(item.value for item in app.markdown)
    assert "预测达标" in text
    assert "模拟推荐" in text
    assert app.session_state["outcome"].proposal_md.startswith("> 演示案例（宽松 SLA）")
    assert not app.exception, app.exception
