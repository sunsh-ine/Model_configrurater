"""V 阶段：业务工具契约、Agent 适配层与“只认产物”的边界。"""

from __future__ import annotations

import json
from decimal import Decimal
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

    report = _run_cli(
        capsys,
        ["report", "--run-id", payload["run_id"], "--task-id", "ses_full"],
    )
    assert report["status"] == "completed"
    assert report["run_id"] == payload["run_id"]
    assert "# 推理部署方案" in report["proposal_md"]


def test_report_rejects_path_traversal(isolated_store, capsys):
    payload = _run_cli(
        capsys,
        ["report", "--run-id", "../outside", "--task-id", "ses_x"],
    )
    assert payload["status"] == "failed"
    assert "非法 run_id" in payload["error"]


@pytest.mark.parametrize(
    "payload",
    [
        {"status": "completed", "run_id": "r", "plan_result_available": True},
        {"status": "completed", "run_id": "r", "plan_result_available": True, "artifacts": {}},
        {
            "status": "completed",
            "run_id": "r",
            "plan_result_available": True,
            "artifacts": {"json": "x"},
        },
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
    def __init__(
        self,
        *,
        write_task: bool = True,
        final_text: str = "已完成",
        subtype: str = "success",
        error: str | None = None,
        tool_names: list[str] | None = None,
    ):
        self.write_task = write_task
        self.final_text = final_text
        self.subtype = subtype
        self.error = error
        self.tool_names = tool_names or []

    def run(self, text, *, task_id, session_id=None, timeout=None):
        self.seen_timeout = timeout
        self.seen_session = session_id
        self.seen_prompt = text
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
            subtype=self.subtype,
            stop_reason="end_turn",
            error=self.error,
            tool_names=self.tool_names,
            session_id="cc-session-test",
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


def test_agent_session_error_is_kept_separate_from_report(isolated_store):
    run = AgentService(
        client=_FakeCommandCodeClient(
            subtype="error", error="session crashed after tool output"
        ),
        transport="commandcode",
    ).run("自然语言需求")
    # 业务报告有效，但 Agent 会话异常：不能显示为整体完全成功
    assert run.completed
    assert run.agent_status == "error"
    assert run.agent_error == "session crashed after tool output"
    assert run.result is not None


def test_unauthorized_tool_blocks_the_report(isolated_store):
    run = AgentService(
        client=_FakeCommandCodeClient(tool_names=["shell_command", "mcp__lcp__get_platform_catalog"]),
        transport="commandcode",
    ).run("自然语言需求")
    assert not run.completed
    assert run.task.status == "failed"
    assert "未授权工具" in (run.task.error or "")
    assert run.result is None


def test_cross_task_report_reference_is_rejected(isolated_store, capsys):
    task_a = _run_cli(
        capsys,
        ["evaluate", "--session-id", "ses_a", "--input-json", json.dumps(REQUEST)],
    )
    task_b = _run_cli(
        capsys,
        ["evaluate", "--session-id", "ses_b", "--input-json", json.dumps(REQUEST)],
    )
    forged = json.loads((tool_cli.TASKS / "ses_a.json").read_text(encoding="utf-8"))
    # 把 A 的契约改成引用 B 的运行，但保留 A 的 session/request
    forged["run_id"] = task_b["run_id"]
    forged["artifacts"] = task_b["artifacts"]
    forged["result_sha256"] = task_b["result_sha256"]
    (tool_cli.TASKS / "ses_a.json").write_text(
        json.dumps(forged), encoding="utf-8"
    )

    task = AgentTaskResult.model_validate(forged)
    run = AgentService(transport="opencode")._load_completed(
        "ses_a", task, "", None
    )
    assert not run.completed
    assert "不一致" in (run.task.error or "")


def test_tampered_result_hash_is_rejected(isolated_store, capsys):
    payload = _run_cli(
        capsys,
        ["evaluate", "--session-id", "ses_hash", "--input-json", json.dumps(REQUEST)],
    )
    task = AgentTaskResult.model_validate_json(
        (tool_cli.TASKS / "ses_hash.json").read_text(encoding="utf-8")
    )
    Path(payload["artifacts"]["json"]).write_text("{}\n", encoding="utf-8")

    run = AgentService(transport="opencode")._load_completed(
        "ses_hash", task, "", None
    )
    assert not run.completed
    assert "sha256" in (run.task.error or "")


def test_continuation_reuses_task_and_session(isolated_store):
    client = _FakeCommandCodeClient()
    first = AgentService(client=client, transport="commandcode").run(
        "自然语言需求", timeout=60
    )
    continued = AgentService(client=client, transport="commandcode").continue_run(
        "GPU 型号 nvidia-h100-sxm-80gb，OSL 256",
        task_id=first.session_id,
        session_id="cc-session-test",
        timeout=60,
    )
    assert client.seen_session == "cc-session-test"
    assert "TASK_ID: " + first.session_id in client.seen_prompt
    assert "Continue the SAME task" in client.seen_prompt
    assert continued.completed
    assert continued.session_id == first.session_id


def test_prompt_does_not_guess_h100_variant():
    prompt = cc_module.build_prompt("cc-abc", "我有 8 张 H100")
    assert "nvidia-h100-sxm-80gb" not in prompt
    assert "do not choose one" in prompt
    assert "H100" in prompt
    assert "CNY" in prompt
    assert "budget_currency" in prompt
    assert "framework_version" in prompt


def test_mcp_tool_env_excludes_parent_secrets(monkeypatch):
    from llm_capacity_pricing.agent import mcp_server

    monkeypatch.setenv("SECRET_MODEL_API_KEY", "should-not-leak")
    env = mcp_server._tool_env()
    assert "SECRET_MODEL_API_KEY" not in env
    assert "PYTHONPATH" in env
    assert env["PYTHONPATH"].endswith("src")


def test_framework_and_budget_currency_are_carried_through():
    from llm_capacity_pricing.app.demo_service import DemoInputs, build_plan_request

    request = build_plan_request(
        DemoInputs(
            budget_amount=Decimal("100"),
            budget_currency="USD",
            framework="trtllm",
            framework_version="1.3.0",
        )
    )
    assert request.budget is not None
    assert request.budget.currency == "USD"
    assert request.advanced.framework == "trtllm"
    assert request.advanced.framework_version == "1.3.0"


def test_agent_tool_records_requested_framework_and_currency(isolated_store, capsys):
    payload = _run_cli(
        capsys,
        [
            "evaluate",
            "--session-id",
            "ses_fx",
            "--input-json",
            json.dumps(
                {
                    **REQUEST,
                    "framework": "trtllm",
                    "framework_version": "1.3.0",
                    "budget_amount": 100,
                    "budget_currency": "USD",
                }
            ),
        ],
    )
    if payload["status"] == "completed":
        assumptions = "；".join(payload["assumptions"])
        assert "推理框架 trtllm（请求值）" in assumptions
        assert "框架版本 1.3.0（请求值）" in assumptions
        assert "USD（请求值）" in assumptions

    partial = _run_cli(
        capsys,
        [
            "evaluate",
            "--session-id",
            "ses_fx2",
            "--input-json",
            json.dumps({**REQUEST, "framework": "trtllm"}),
        ],
    )
    if partial["status"] == "completed":
        assumptions = "；".join(partial["assumptions"])
        assert "推理框架 trtllm（请求值）" in assumptions
        assert "框架版本 0.24.0（示例默认）" in assumptions


def test_report_requires_matching_task_and_run(isolated_store, capsys):
    a = _run_cli(
        capsys,
        ["evaluate", "--session-id", "ses_a", "--input-json", json.dumps(REQUEST)],
    )
    b = _run_cli(
        capsys,
        ["evaluate", "--session-id", "ses_b", "--input-json", json.dumps(REQUEST)],
    )
    forged = _run_cli(
        capsys,
        ["report", "--run-id", b["run_id"], "--task-id", "ses_a"],
    )
    assert forged["status"] == "failed"
    assert "不对应" in forged["error"]

    traversal = _run_cli(
        capsys,
        ["report", "--run-id", a["run_id"], "--task-id", "../escape"],
    )
    assert traversal["status"] == "failed"
    assert "非法 task_id" in traversal["error"]


def test_exact_mcp_tool_allowlist(isolated_store):
    run = AgentService(
        client=_FakeCommandCodeClient(tool_names=["mcp__lcp__future_tool"]),
        transport="commandcode",
    ).run("自然语言需求")
    assert not run.completed
    assert "未授权工具" in (run.task.error or "")


def test_sandbox_is_required_by_default(monkeypatch, tmp_path):
    monkeypatch.delenv("COMMAND_CODE_SANDBOX", raising=False)
    monkeypatch.setattr(cc_module.shutil, "which", lambda name: None)
    client = cc_module.CommandCodeClient(
        binary="command-code", workspace=tmp_path / "ws"
    )
    assert client.sandbox_available() is False
    assert client.available()[0] is False
    with pytest.raises(cc_module.CommandCodeUnavailable):
        client.run("prompt", task_id="cc-nosandbox")


def test_sandbox_can_be_explicitly_disabled_for_debug(monkeypatch, tmp_path):
    script = tmp_path / "fake-command-code"
    script.write_text(
        "#!/bin/sh\n"
        "echo '{\"type\":\"result\",\"subtype\":\"success\",\"finalText\":\"OK\",\"sessionId\":\"s\"}'\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    monkeypatch.setenv("COMMAND_CODE_SANDBOX", "0")
    client = cc_module.CommandCodeClient(
        binary=str(script), workspace=tmp_path / "ws"
    )
    run = client.run("prompt", task_id="cc-debug")
    assert run.final_text == "OK"
    assert run.error is None


def test_nonzero_exit_is_an_error_even_with_success_event(tmp_path, monkeypatch):
    script = tmp_path / "fake-command-code"
    script.write_text(
        "#!/bin/sh\n"
        "echo '{\"type\":\"result\",\"subtype\":\"success\",\"finalText\":\"OK\",\"sessionId\":\"s\"}'\n"
        "exit 1\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    monkeypatch.setenv("COMMAND_CODE_SANDBOX", "0")
    client = cc_module.CommandCodeClient(
        binary=str(script), workspace=tmp_path / "ws"
    )
    run = client.run("prompt", task_id="cc-exit")
    assert run.subtype == "success"
    assert run.error and "退出码 1" in run.error


def test_run_dirs_are_atomic_and_unique(tmp_path):
    from llm_capacity_pricing.app.demo_service import new_run_dir

    first = new_run_dir(tmp_path)
    second = new_run_dir(tmp_path)
    assert first != second
    assert first.is_dir() and second.is_dir()


def test_sandbox_binds_exclude_private_data(monkeypatch, tmp_path):
    monkeypatch.delenv("COMMAND_CODE_SANDBOX", raising=False)
    client = cc_module.CommandCodeClient(
        binary="command-code", workspace=tmp_path / "ws"
    )
    if not client.sandbox_available():
        pytest.skip("bwrap 不可用")
    command = client._sandbox_command(
        "command-code", ["--version"], task_id="cc-test"
    )
    assert command is not None
    joined = " ".join(command)
    assert "data/private" not in joined
    assert "local.db" not in joined
    assert "data/agent_runs" in joined
    monkeypatch.setenv("COMMAND_CODE_SANDBOX", "0")
    assert (
        client._sandbox_command(
            "command-code", ["--version"], task_id="cc-test"
        )
        is None
    )


def test_commandcode_parse_stream_and_prompt():
    stream = "\n".join(
        [
            '{"type":"event","event":{"type":"text_delta","delta":"OK"}}',
            '{"type":"event","event":{"type":"tool_running","toolName":"mcp__lcp__get_platform_catalog"}}',
            '{"type":"result","subtype":"success","stopReason":"end_turn","finalText":"OK","sessionId":"s-1"}',
        ]
    )
    parsed = cc_module.CommandCodeClient.parse_stream(stream)
    assert parsed["subtype"] == "success"
    assert parsed["finalText"] == "OK"
    assert cc_module.CommandCodeClient.parse_tool_names(stream) == [
        "mcp__lcp__get_platform_catalog"
    ]

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
