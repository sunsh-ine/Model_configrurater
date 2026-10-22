"""X2：Agent 业务场景矩阵（结构化、Mock，不依赖真实付费 API）。

自然语言抽取由模型完成；这里验证业务工具的确定性边界：缺字段、歧义目录、
币种、门禁绕过、未知模型和手动/工具一致性。真实 Command Code 只跑少量代表场景。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from llm_capacity_pricing.agent import tool_cli
from llm_capacity_pricing.agent.commandcode_client import build_prompt
from llm_capacity_pricing.agent.schemas import AgentPlanInput
from llm_capacity_pricing.app.demo_service import DemoInputs, run_demo

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
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(tool_cli, "RUNS", tmp_path / "runs")
    monkeypatch.setattr(tool_cli, "TASKS", tmp_path / "tasks")
    return tmp_path


def _evaluate(capsys, session: str, payload: dict) -> dict:
    assert (
        tool_cli.main(
            [
                "evaluate",
                "--session-id",
                session,
                "--input-json",
                json.dumps(payload),
            ]
        )
        == 0
    )
    return json.loads(capsys.readouterr().out.strip().splitlines()[-1])


def test_strict_complete_has_no_recommendation(store, capsys):
    out = _evaluate(capsys, "x-strict", BASE)
    assert out["status"] == "completed"
    assert out["conclusions"]["simulation_recommendation"] is None
    assert out["conclusions"]["observed_pass_count"] == 0


def test_relaxed_complete_recommends_predicted_pass(store, capsys):
    payload = {**BASE, "ttft_p99_ms_max": 6000, "tpot_p99_ms_max": 40}
    out = _evaluate(capsys, "x-relaxed", payload)
    assert out["status"] == "completed"
    assert out["conclusions"]["simulation_recommendation"]
    assert out["conclusions"]["observed_pass_count"] == 0


@pytest.mark.parametrize(
    "field,label",
    [
        ("hardware_id", "GPU 型号"),
        ("output_len", "输出长度 OSL"),
        ("concurrency", "并发"),
    ],
)
def test_missing_fields_are_reported(store, capsys, field, label):
    payload = {key: value for key, value in BASE.items() if key != field}
    out = _evaluate(capsys, f"x-missing-{field}", payload)
    assert out["status"] == "needs_confirmation"
    assert label in out["missing_fields"]
    assert out["run_id"] is None


def test_ambiguous_h100_is_not_guessed_in_prompt():
    prompt = build_prompt("x-1", "我有 8 张 H100")
    assert "nvidia-h100-sxm-80gb" not in prompt
    assert "do not choose one" in prompt
    assert "H100" in prompt


def test_prompt_maps_only_explicit_units():
    prompt = build_prompt("x-2", "输入 8K，TTFT 2 秒")
    assert '"输入 8K" -> 8192' in prompt
    assert '"TTFT P99 2 秒" -> 2000 ms' in prompt


def test_budget_without_currency_is_an_assumption(store, capsys):
    out = _evaluate(capsys, "x-budget", {**BASE, "budget_amount": 100})
    assert out["status"] == "completed"
    assert any("预算币种未指定" in item for item in out["assumptions"])


def test_budget_currency_mismatch_does_not_claim_within(store, capsys):
    out = _evaluate(
        capsys,
        "x-usd",
        {**BASE, "budget_amount": 100, "budget_currency": "USD"},
    )
    assert out["status"] == "completed"
    assert any("USD（请求值）" in item for item in out["assumptions"])
    # 跨币种不做“在预算内”的结论
    assert out["conclusions"]["quote_publishable_count"] == 0
    run_dir = Path(out["artifacts"]["dir"])
    result = json.loads((run_dir / "result.json").read_text(encoding="utf-8"))
    assert all(
        candidate["budget_status"] in (None, "unknown", "not_configured")
        for candidate in result["candidates"]
    )


def test_gate_bypass_field_is_rejected(store, capsys):
    out = _evaluate(capsys, "x-bypass", {**BASE, "skip_sla": True})
    assert out["status"] == "needs_confirmation"
    assert "skip_sla" in out["missing_fields"]
    # AgentPlanInput 本身没有“跳过门禁”字段
    assert "skip_sla" not in AgentPlanInput.model_fields


def test_unsupported_model_is_a_blocked_plan_not_a_fake_report(store, capsys):
    out = _evaluate(capsys, "x-unknown", {**BASE, "model_id": "no-such-model"})
    assert out["status"] == "completed"
    assert out["conclusions"]["overall_status"] == "blocked"
    assert out["conclusions"]["candidate_count"] == 0
    assert out["conclusions"]["quote_publishable_count"] == 0


def test_tool_and_manual_engine_agree(store, capsys, tmp_path):
    out = _evaluate(capsys, "x-parity", BASE)
    agent_result = json.loads(
        (Path(out["artifacts"]["json"])).read_text(encoding="utf-8")
    )
    manual = run_demo(
        DemoInputs(**BASE), out_dir=tmp_path / "manual", write_files=False
    ).result

    def snapshot(result: dict | object):
        if isinstance(result, dict):
            candidates = result["candidates"]
            sim = (result.get("simulation") or {}).get("candidates") or []
            return (
                [(c["candidate_id"], c["static_status"], c["sla_status"]) for c in candidates],
                [(c["candidate_id"], c["verdict"]) for c in sim],
            )
        return (
            [(c.candidate_id, c.static_status, c.sla_status) for c in result.candidates],
            [(c.candidate_id, c.verdict) for c in (result.simulation.candidates if result.simulation else [])],
        )

    assert snapshot(agent_result) == snapshot(manual)
