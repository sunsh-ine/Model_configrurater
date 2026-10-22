"""Y2：手动、预设、Agent 三种模式的业务结果一致性。"""

from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path

import pytest

from llm_capacity_pricing.agent import service as service_module
from llm_capacity_pricing.agent import tool_cli
from llm_capacity_pricing.agent.commandcode_client import CommandCodeRun
from llm_capacity_pricing.agent.service import AgentService
from llm_capacity_pricing.app.demo_service import DemoInputs, run_demo
from llm_capacity_pricing.app.scenarios import get_scenario

BASE = {
    "model_id": "qwen3-32b",
    "hardware_id": "nvidia-h100-sxm-80gb",
    "gpu_count": 8,
    "input_len": 8192,
    "output_len": 256,
    "concurrency": 32,
    "ttft_p99_ms_max": 400,
    "tpot_p99_ms_max": 30,
}


@pytest.fixture()
def stores(tmp_path, monkeypatch):
    runs = tmp_path / "runs"
    tasks = tmp_path / "tasks"
    monkeypatch.setattr(tool_cli, "RUNS", runs)
    monkeypatch.setattr(tool_cli, "TASKS", tasks)
    monkeypatch.setattr(service_module, "RUNS", runs)
    monkeypatch.setattr(service_module, "TASKS", tasks)
    return runs, tasks


def _snapshot(result) -> tuple:
    candidates = tuple(
        (
            c.candidate_id,
            c.gpu_count,
            c.static_status,
            c.sla_status,
            c.cost_incomplete,
            c.total_cash_over_horizon,
            c.quote_publishable,
        )
        for c in result.candidates
    )
    sim = tuple(
        (
            c.candidate_id,
            c.verdict,
            c.comparable,
            c.metrics.sim_p99_ttft_ms,
            c.metrics.sim_p99_tpot_ms,
        )
        for c in (result.simulation.candidates if result.simulation else [])
    )
    return candidates, sim


class _FakeAgentClient:
    def run(self, prompt, *, task_id, session_id=None, timeout=None):
        with contextlib.redirect_stdout(io.StringIO()):
            tool_cli.main(
                [
                    "evaluate",
                    "--session-id",
                    task_id,
                    "--input-json",
                    json.dumps(BASE),
                ]
            )
        return CommandCodeRun(
            task_id=task_id,
            final_text="summary",
            subtype="success",
            session_id="cc-session-y",
            tool_names=["mcp__lcp__evaluate_deployment_plan"],
        )

    def cleanup(self, task_id):
        return None



def test_manual_mode_strict(tmp_path):
    outcome = run_demo(DemoInputs(**BASE), out_dir=tmp_path / "manual")
    result = outcome.result
    assert len(result.candidates) == 3
    assert [c.static_status for c in result.candidates] == [
        "conditional_feasible",
        "conditional_feasible",
        "infeasible",
    ]
    sim = result.simulation
    assert sim is not None and sim.recommended("throughput") is None
    native = next(
        c for c in sim.candidates if c.candidate_id.startswith("sim-native")
    )
    assert native.verdict == "predicted_fail"
    assert float(native.metrics.sim_p99_ttft_ms) > 400
    assert all(c.sla_status != "observed_pass" for c in result.candidates)
    assert all(c.cost_incomplete for c in result.candidates)
    assert all(c.total_cash_over_horizon is None for c in result.candidates)
    assert all(not c.quote_publishable for c in result.candidates)

    config = json.loads((tmp_path / "manual" / "result.json").read_text())
    assert config["request_echo"]["sla"]["ttft_p99_ms_max"] == "400"
    assert len(config["candidates"]) == 3


def test_preset_modes_keep_the_same_predictions(tmp_path):
    strict = run_demo(
        DemoInputs(**get_scenario("strict").inputs),
        out_dir=tmp_path / "strict",
        write_files=False,
    ).result
    relaxed = run_demo(
        DemoInputs(**get_scenario("relaxed").inputs),
        out_dir=tmp_path / "relaxed",
        write_files=False,
    ).result
    cost = run_demo(
        DemoInputs(**get_scenario("cost").inputs),
        out_dir=tmp_path / "cost",
        write_files=False,
    ).result

    def native(result):
        return next(
            c
            for c in result.simulation.candidates
            if "sim-native" in c.candidate_id and "tp8pp1dp1" in c.candidate_id
        )

    assert native(strict).metrics.sim_p99_ttft_ms == native(relaxed).metrics.sim_p99_ttft_ms
    assert native(strict).verdict == "predicted_fail"
    assert native(relaxed).verdict == "predicted_pass"
    assert relaxed.simulation.recommended("throughput") is not None
    assert strict.simulation.recommended("throughput") is None

    assert cost.simulation.ranking == "cost"
    assert cost.simulation.cost_ranking_available()[0] is False
    assert all(c.budget_status == "unknown" for c in cost.simulation.candidates)


def test_three_modes_agree_on_business_results(stores, tmp_path):
    manual = run_demo(
        DemoInputs(**BASE), out_dir=tmp_path / "manual", write_files=False
    ).result

    # 预设 strict 与手动 BASE 是同一需求（request_id 不同）
    preset = run_demo(
        DemoInputs(**get_scenario("strict").inputs),
        out_dir=tmp_path / "preset",
        write_files=False,
    ).result

    # Agent 工具路径
    session = "y-tool"
    with contextlib.redirect_stdout(io.StringIO()):
        tool_cli.main(
            [
                "evaluate",
                "--session-id",
                session,
                "--input-json",
                json.dumps(BASE),
            ]
        )
    task = json.loads((tool_cli.TASKS / f"{session}.json").read_text())
    from llm_capacity_pricing.schemas.plan import PlanResult

    tool_result = PlanResult.model_validate_json(
        Path(task["artifacts"]["json"]).read_text()
    )

    # Agent 服务路径（Mock Command Code，业务工具真实执行）
    agent_run = AgentService(
        client=_FakeAgentClient(), transport="commandcode"
    ).run("自然语言需求")
    assert agent_run.completed
    agent_result = agent_run.result

    assert _snapshot(manual) == _snapshot(preset)
    assert _snapshot(manual) == _snapshot(tool_result)
    assert _snapshot(manual) == _snapshot(agent_result)


def test_customer_report_matches_structured_result(tmp_path):
    outcome = run_demo(DemoInputs(**BASE), out_dir=tmp_path / "report")
    proposal = outcome.proposal_md
    result = outcome.result
    native = next(
        c for c in result.simulation.candidates if c.candidate_id.startswith("sim-native")
    )
    assert native.candidate_id in proposal
    assert "TP8×PP1×DP1" in proposal
    assert "5201.827" in proposal
    assert "31.458" in proposal
    assert "不完整" in proposal
    assert "不可发布" in proposal
