"""Command Code 非交互适配层。

Command Code 负责理解需求与调用 MCP 工具；业务结果仍以
``data/agent_tasks/<task_id>.json`` 为准，模型的 finalText 只作解释。

headless 默认禁止写文件/改文件/shell，因此这里**不使用** ``--yolo``、
``--tools-all`` 或 ``--accept-edits``。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
WORKSPACE = ROOT / "data/agent/cc-workspace"
TASKS = ROOT / "data/agent_tasks"
DEFAULT_MODEL = "deepseek/deepseek-v4-flash"


class CommandCodeUnavailable(RuntimeError):
    pass


class CommandCodeTimeout(RuntimeError):
    pass


@dataclass
class CommandCodeRun:
    task_id: str
    final_text: str = ""
    subtype: str | None = None
    stop_reason: str | None = None
    error: str | None = None
    raw_result: dict[str, Any] = field(default_factory=dict)
    session_id: str | None = None
    tool_names: list[str] = field(default_factory=list)


def build_prompt(task_id: str, requirement: str) -> str:
    return f"""TASK_ID: {task_id}

You are the deployment planning agent for an LLM inference capacity system.
Use ONLY the lcp MCP tools: get_platform_catalog, evaluate_deployment_plan,
get_plan_report. Never use shell, file-editing, or any other tool.
Never invent GPU counts, TTFT/TPOT, costs, feasibility, or recommendations.

Do NOT infer unstated values:
- If the user names a GPU family without a variant (for example "H100") and the
  catalog has multiple matching hardware_id values, do not choose one. Call
  evaluate_deployment_plan without hardware_id; it will return
  needs_confirmation. List the matching hardware_id candidates in your reply.
- If the user gives a budget without a currency, do not assume one. State that
  the demo cost catalog uses CNY and ask for confirmation; if the user gives a
  currency, pass it as budget_currency.
- Framework/version defaults to vllm 0.24.0 only when the user did not specify;
  if the user names a framework or version, pass framework/framework_version.
  State the effective values as assumptions after a completed run.

Map only explicitly stated values: "8 张" -> gpu_count=8; "输入 8K" -> 8192;
"TTFT P99 2 秒" -> 2000 ms.

User requirement:
{requirement}

Steps:
1. Call get_platform_catalog and confirm the model_id and hardware_id exist.
2. Map the explicitly stated fields: model_id, hardware_id, gpu_count,
   input_len, output_len, concurrency, ttft_p99_ms_max, tpot_p99_ms_max,
   budget_amount, budget_currency, weight_precision, framework,
   framework_version, ranking.
3. Call evaluate_deployment_plan with task_id="{task_id}" and those fields.
4. If status=needs_confirmation, reply with the exact missing field labels and
   the ambiguous candidates; stop and wait for the user's answer.
5. If status=failed, reply with the error and state that no plan was produced; stop.
6. If status=completed, call get_plan_report with the run_id and reply in Chinese
   with only: run_id, the summary/conclusions, recommendation or best-effort,
   assumptions, and the main limitations. Do not add any number absent from the
   tool output.
"""


def build_continuation_prompt(task_id: str, additional: str) -> str:
    return f"""TASK_ID: {task_id}

补充信息：
{additional}

Continue the SAME task; do not restart or re-ask for fields already provided.
Use ONLY the lcp MCP tools. Call evaluate_deployment_plan with
task_id="{task_id}" and the now-complete fields; if it returns completed, call
get_plan_report and reply with the same concise Chinese summary.
If it still returns needs_confirmation, list the remaining missing fields.
"""


class CommandCodeClient:
    def __init__(
        self,
        *,
        binary: str | None = None,
        model: str | None = None,
        workspace: Path | None = None,
        timeout: float = 300.0,
    ):
        self.binary = binary or os.environ.get("COMMAND_CODE_BIN") or "command-code"
        self.model = model or os.environ.get("COMMAND_CODE_MODEL") or DEFAULT_MODEL
        self.workspace = workspace or WORKSPACE
        self.timeout = timeout

    def available(self) -> tuple[bool, str]:
        path = shutil.which(self.binary)
        if not path:
            return False, f"未找到 {self.binary}"
        try:
            proc = subprocess.run(
                [path, "--version"], capture_output=True, text=True, timeout=15
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return False, f"Command Code 不可用：{exc}"
        if proc.returncode != 0:
            return False, (proc.stderr or proc.stdout).strip()[:200]
        version = (proc.stdout or "").strip().splitlines()[0]
        return True, f"Command Code {version}"

    @staticmethod
    def parse_stream(stdout: str) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for line in stdout.splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if payload.get("type") == "result":
                result = payload
        return result

    @staticmethod
    def parse_tool_names(stdout: str) -> list[str]:
        names: list[str] = []
        for line in stdout.splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            event = payload.get("event") or {}
            if event.get("type") in ("tool_running", "tool_completed"):
                name = event.get("toolName")
                if name and name not in names:
                    names.append(str(name))
        return names

    def run(
        self,
        prompt: str,
        *,
        task_id: str,
        session_id: str | None = None,
        timeout: float | None = None,
    ) -> CommandCodeRun:
        path = shutil.which(self.binary)
        if not path:
            raise CommandCodeUnavailable(f"未找到 {self.binary}")
        self.workspace.mkdir(parents=True, exist_ok=True)
        command = [
            path,
            "-p",
            "--trust",
            "--skip-onboarding",
            "--output-format",
            "json",
            "--model",
            self.model,
            "--max-turns",
            "12",
        ]
        if session_id:
            command.extend(["--session", session_id])
        command.append(prompt)
        try:
            proc = subprocess.run(
                command,
                cwd=self.workspace,
                capture_output=True,
                text=True,
                timeout=timeout or self.timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise CommandCodeTimeout(
                f"Command Code 超过 {int(timeout or self.timeout)} 秒未完成"
            ) from exc
        parsed = self.parse_stream(proc.stdout or "")
        run = CommandCodeRun(
            task_id=task_id,
            final_text=str(parsed.get("finalText") or ""),
            subtype=parsed.get("subtype"),
            stop_reason=parsed.get("stopReason"),
            raw_result=parsed,
            session_id=str(parsed.get("sessionId") or "") or None,
            tool_names=self.parse_tool_names(proc.stdout or ""),
        )
        if proc.returncode != 0 and not parsed:
            run.error = (proc.stderr or proc.stdout or "").strip()[-500:]
        elif parsed and parsed.get("subtype") != "success":
            run.error = str(parsed.get("error") or parsed.get("subtype") or "任务失败")
        return run

    @staticmethod
    def task_path(task_id: str) -> Path:
        return TASKS / f"{task_id}.json"
