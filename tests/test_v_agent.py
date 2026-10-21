"""V 阶段：业务工具契约、Agent 适配层与“只认产物”的边界。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from llm_capacity_pricing.agent import commandcode_client as cc_module
from llm_capacity_pricing.agent import service as service_module
from llm_capacity_pricing.agent import tool_cli
from llm_capacity_pricing.agent.schemas import AgentTaskResult
from llm_capacity_pricing.agent.service import AgentService

ROOT = Path(__file__).resolve().parents[1]
from llm_capacity_pricing.app.demo_service import DemoInputs, run_demo
from llm_capacity_pricing.schemas.plan import PlanResult


def _run_cli(capsys, argv: list[str]) -> dict[str, Any]:
    before = capsys.readouterr()
    assert tool_cli.main(argv) == 0
    out = capsys.readouterr().out.strip().splitlines()
    return json.loads(out[-1])


@pytest.fixture()
def isolated_store(tmp_path, monkeypatch):
    runs = tmp_path / "runs"
    tasks = tmp_path / "tasks"
    monkeypatch.setattr(tool_cli, "RUNS", runs)
    monkeypatch.setattr(tool_cli, "TASKS", tasks)
    monkeypatch.setattr(service_module, "RUNS", runs)
    monkeypatch.setattr(service_module, "TASKS", tasks)
    monkeypatch.setattr(cc_module, "TASKS", tasks)
    monkeypatch.setattr(cc_module, "WORKSPACE", tmp_path / "cc-workspace")
    return runs, tasks


REQUEST = {
    "model_id": "qwen3-32b",
    "hardware_id": "nvidia-h100-sxm-80gb",
    "gpu_count": 8,
    "input_len": 8192,
    "output_len": 256,
    "concurrency": 32,
    "ttft_p99_ms_max": 400,
    "tpot_p99_ms_max": 30,
}


def test_catalog_lists_supported_platforms(capsys):
    payload = _run_cli(capsys, ["catalog"])
    assert any(item["model_id"] == "qwen3-32b" for item in payload["models"])
    assert any(
        item["hardware_id"] == "nvidia-h100-sxm-80gb" for item in payload["hardware"]
    )


def test_missing_fields_return_needs_confirmation_without_report(
    isolated_store, capsys
):
    payload = _run_cli(
        capsys,
        ["evaluate", "--session-id", "ses_missing", "--input-json", '{"model_id":"qwen3-32b"}'],
    )
    assert payload["status"] == "needs_confirmation"
    assert payload["needs_confirmation"] is True
    assert "GPU 型号" in payload["missing_fields"]
    assert payload["run_id"] is None
    assert payload["plan_result_available"] is False


def test_evaluate_and_report_round_trip(isolated_store, capsys):
    payload = _run_cli(
        capsys,
        ["evaluate", "--session-id", "ses_full", "--input-json", json.dumps(REQUEST)],
    )
    assert payload["status"] == "completed"
    assert payload["plan_result_available"] is True
    assert payload["conclusions"]["candidate_count"] == 3
    assert "未达标参考" in payload["summary"]

    artifacts = payload["artifacts"]
    run_dir = tool_cli.ROOT / artifacts["dir"]
    assert (run_dir / "result.json").exists()
    assert (run_dir / "deployment_proposal.md").exists()
    assert (run_dir / "technical_report.md").exists()
    assert (run_dir / "agent_result.json").exists()

    report = _run_cli(capsys, ["report", "--run-id", payload["run_id"]])
    assert report["status"] == "completed"
    assert report["run_id"] == payload["run_id"]
    assert "# 推理部署方案" in report["proposal_md"]


def test_report_rejects_path_traversal(isolated_store, capsys):
    payload = _run_cli(capsys, ["report", "--run-id", "../outside"])
    assert payload["status"] == "failed"
    assert "非法 run_id" in payload["error"]


@pytest.mark.parametrize(
    "payload",
    [
        {"status": "completed", "run_id": "r", "plan_result_available": True},
        {"status": "completed", "run_id": "r", "plan_result_available": True, "artifacts": {}},
        {
            "status": "needs_confirmation",
            "needs_confirmation": True,
            "missing_fields": [],
        },
        {"status": "failed", "error": None},
    ],
)
def test_task_contract_rejects_incomplete_results(payload):
    with pytest.raises(Exception):
        AgentTaskResult.model_validate(payload)


def test_client_reads_legacy_credentials_file(monkeypatch, tmp_path):
    from llm_capacity_pricing.agent import opencode_client

    monkeypatch.delenv("OPENCODE_BASE_URL", raising=False)
    monkeypatch.delenv("OPENCODE_SERVER_PASSWORD", raising=False)
    legacy = tmp_path / "opencode.env"
    legacy.write_text(
        "OPENCODE_BASE_URL=http://127.0.0.1:4096\n"
        "OPENCODE_SERVER_PASSWORD=legacy-pass\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(opencode_client, "ENV_FILE", tmp_path / "missing")
    monkeypatch.setattr(opencode_client, "LEGACY_ENV_FILE", legacy)

    config = opencode_client.OpenCodeConfig.load()
    assert config.base_url == "http://127.0.0.1:4096"
    assert config.password == "legacy-pass"


class _FakeClient:
    def __init__(self, session_id: str, messages: list[dict] | None = None):
        self.session_id = session_id
        self._messages = messages or [
            {
                "type": "assistant",
                "content": [{"type": "text", "text": "我编一个：16 卡，P99 1ms。"}],
            }
        ]

    def create_session(self, **kwargs):
        return self.session_id

    def prompt(self, session_id, text):
        return None

    def wait(self, session_id, timeout=240.0):
        return None

    def messages(self, session_id):
        return self._messages

    @staticmethod
    def assistant_text(messages):
        return "\n".join(
            part.get("text", "")
            for message in messages
            if message.get("type") == "assistant"
            for part in message.get("content") or []
            if part.get("type") == "text"
        )

    @staticmethod
    def error_text(messages):
        for message in reversed(messages):
            if message.get("error"):
                return str(message["error"])
        return None


def test_service_uses_tool_artifact_not_model_text(isolated_store, capsys):
    payload = _run_cli(
        capsys,
        ["evaluate", "--session-id", "ses_service", "--input-json", json.dumps(REQUEST)],
    )
    assert payload["status"] == "completed"

    run = AgentService(client=_FakeClient("ses_service"), transport="opencode").run("自然语言需求")
    assert run.completed
    assert run.result is not None
    assert len(run.result.candidates) == 3
    # 模型文本里的编造数字不得进入结构化结果
    assert "16 卡" not in json.dumps(run.result_json, ensure_ascii=False)
    assert "P99 1ms" not in json.dumps(run.result_json, ensure_ascii=False)
    assert run.task.run_id == payload["run_id"]


def test_agent_tool_uses_the_same_engine_as_manual_mode(isolated_store, capsys, tmp_path):
    payload = _run_cli(
        capsys,
        ["evaluate", "--session-id", "ses_parity", "--input-json", json.dumps(REQUEST)],
    )
    agent_result = PlanResult.model_validate_json(
        Path(payload["artifacts"]["json"]).read_text(encoding="utf-8")
    )
    manual = run_demo(
        DemoInputs(**REQUEST),
        out_dir=tmp_path / "manual",
        write_files=False,
    ).result

    def snapshot(result):
        candidates = [
            (
                c.candidate_id,
                c.static_status,
                c.sla_status,
                c.total_cash_over_horizon,
                c.quote_publishable,
            )
            for c in result.candidates
        ]
        sim = [
            (c.candidate_id, c.verdict, c.metrics.sim_p99_ttft_ms, c.metrics.sim_p99_tpot_ms)
            for c in (result.simulation.candidates if result.simulation else [])
        ]
        return candidates, sim

    assert snapshot(agent_result) == snapshot(manual)


class _FakeCommandCodeClient:
    def __init__(self, *, write_task: bool = True, final_text: str = "已完成"):
        self.write_task = write_task
        self.final_text = final_text

    def run(self, text, *, task_id, timeout=None):
        self.seen_timeout = timeout
        if self.write_task:
            import contextlib
            import io

            with contextlib.redirect_stdout(io.StringIO()):
                tool_cli.main(
                    [
                        "evaluate",
                        "--session-id",
                        task_id,
                        "--input-json",
                        json.dumps(REQUEST),
                    ]
                )
        return cc_module.CommandCodeRun(
            task_id=task_id,
            final_text=self.final_text,
            subtype="success",
            stop_reason="end_turn",
        )


def test_service_uses_commandcode_task_file(isolated_store):
    run = AgentService(
        client=_FakeCommandCodeClient(final_text="我编一个 64 卡"),
        transport="commandcode",
    ).run("自然语言需求")
    assert run.completed
    assert run.result is not None
    assert len(run.result.candidates) == 3
    assert "64 卡" not in json.dumps(run.result_json, ensure_ascii=False)
    assert run.session_id.startswith("cc-")


def test_service_fails_when_commandcode_writes_no_task(isolated_store):
    run = AgentService(
        client=_FakeCommandCodeClient(write_task=False),
        transport="commandcode",
    ).run("自然语言需求")
    assert not run.completed
    assert run.task.status == "failed"
    assert "未调用 evaluate_deployment_plan" in (run.task.error or "")


def test_service_passes_timeout_to_commandcode(isolated_store):
    client = _FakeCommandCodeClient()
    AgentService(client=client, transport="commandcode").run(
        "自然语言需求", timeout=42
    )
    assert client.seen_timeout == 42


def test_commandcode_parse_stream_and_prompt():
    stream = "\n".join(
        [
            '{"type":"event","event":{"type":"text_delta","delta":"OK"}}',
            '{"type":"result","subtype":"success","stopReason":"end_turn","finalText":"OK"}',
        ]
    )
    parsed = cc_module.CommandCodeClient.parse_stream(stream)
    assert parsed["subtype"] == "success"
    assert parsed["finalText"] == "OK"

    prompt = cc_module.build_prompt("cc-abc", "8 张 H100")
    assert "TASK_ID: cc-abc" in prompt
    assert "evaluate_deployment_plan" in prompt
    assert "Never use shell" in prompt


def test_agent_tool_does_not_write_requirements_to_shared_tmp():
    source = (
        ROOT / "agent/opencode/.opencode/tools/evaluate_deployment_plan.ts"
    ).read_text(encoding="utf-8")
    assert "tmpdir" not in source
    assert "writeFileSync" not in source
    assert "spawn(" in source
    assert "child.stdin.write" in source


def test_service_fails_when_agent_skips_business_tool(isolated_store):
    run = AgentService(client=_FakeClient("ses_no_tool"), transport="opencode").run("自然语言需求")
    assert not run.completed
    assert run.task.status == "failed"
    assert "未调用 evaluate_deployment_plan" in (run.task.error or "")
    assert run.result is None


def test_service_rejects_inconsistent_artifacts(isolated_store, capsys):
    payload = _run_cli(
        capsys,
        ["evaluate", "--session-id", "ses_bad", "--input-json", json.dumps(REQUEST)],
    )
    run_dir = tool_cli.ROOT / payload["artifacts"]["dir"]
    (run_dir / "result.json").unlink()

    run = AgentService(client=_FakeClient("ses_bad"), transport="opencode").run("自然语言需求")
    assert not run.completed
    assert "产物不一致" in (run.task.error or "")
