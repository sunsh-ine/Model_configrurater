"""Agent 任务编排：Command Code / OpenCode 负责理解与调用工具，业务系统只认工具产物。"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from llm_capacity_pricing.agent.commandcode_client import (
    CommandCodeClient,
    CommandCodeTimeout,
    CommandCodeUnavailable,
    build_continuation_prompt,
    build_prompt,
)
from llm_capacity_pricing.agent.opencode_client import (
    OpenCodeClient,
    OpenCodeConfig,
    OpenCodeTimeout,
    OpenCodeUnavailable,
)
from llm_capacity_pricing.agent.schemas import AgentTaskResult
from llm_capacity_pricing.schemas.plan import PlanResult

ROOT = Path(__file__).resolve().parents[3]
RUNS = ROOT / "data/agent_runs"
TASKS = ROOT / "data/agent_tasks"


@dataclass
class AgentRun:
    session_id: str
    task: AgentTaskResult
    assistant_text: str = ""
    provider_error: str | None = None
    out_dir: Path | None = None
    result: PlanResult | None = None
    proposal_md: str | None = None
    technical_md: str | None = None
    result_json: dict[str, Any] | None = None
    artifacts: dict[str, str] = field(default_factory=dict)
    #: Agent 会话状态与业务报告状态分开记录
    agent_status: str = "ok"
    agent_error: str | None = None
    agent_session_id: str | None = None

    @property
    def completed(self) -> bool:
        return (
            self.task.status == "completed"
            and self.result is not None
            and self.out_dir is not None
        )

    def as_demo_outcome(self):
        """把 Agent 产物包装成与手动模式相同的 DemoOutcome，复用现有渲染。"""
        if not self.completed:
            return None
        from llm_capacity_pricing.app.demo_service import DemoOutcome

        def path(key: str) -> Path | None:
            value = self.artifacts.get(key)
            return ROOT / value if value else None

        return DemoOutcome(
            out_dir=self.out_dir,
            db_path=(self.out_dir or ROOT) / "demo.db",
            result=self.result,
            proposal_md=self.proposal_md or "",
            technical_md=self.technical_md or "",
            result_json=self.result_json or {},
            proposal_path=path("proposal"),
            technical_path=path("technical"),
            json_path=path("json"),
            stages=[("数据准备", "ok", str((self.out_dir or ROOT) / "demo.db"))],
        )


class AgentService:
    """统一入口：Command Code（默认，若已安装）或 OpenCode。

    两条 transport 只在“如何让模型调用工具”上不同；任务产物、校验和报告
    完全共用 ``data/agent_tasks`` 与 ``data/agent_runs``。
    """

    def __init__(self, client=None, *, transport: str | None = None):
        self.transport = (
            transport
            or os.environ.get("AGENT_TRANSPORT")
            or ("commandcode" if shutil.which("command-code") else "opencode")
        )
        self.client = client

    def _get_client(self):
        if self.client is not None:
            return self.client
        if self.transport == "commandcode":
            self.client = CommandCodeClient()
        else:
            self.client = OpenCodeClient(OpenCodeConfig.load())
        return self.client

    def available(self) -> tuple[bool, str]:
        try:
            client = self._get_client()
        except OpenCodeUnavailable as exc:
            return False, str(exc)
        if self.transport == "commandcode":
            return client.available()
        if client.health():
            return True, "OpenCode 服务可用（模型权限由服务端配置决定）"
        return False, "OpenCode 服务不可达"

    def run(self, text: str, *, timeout: float = 300.0) -> AgentRun:
        if self.transport == "commandcode":
            return self._run_commandcode(text, timeout=timeout)
        return self._run_opencode(text, timeout=timeout)

    # ------------------------------------------------------------------
    # Command Code
    # ------------------------------------------------------------------

    def continue_run(
        self,
        additional: str,
        *,
        task_id: str,
        session_id: str,
        timeout: float = 300.0,
    ) -> AgentRun:
        """在同一 Command Code 会话里补充信息，继续原任务。"""
        if self.transport != "commandcode":
            return self._failed(task_id, "当前 transport 不支持继续补充信息")
        prompt = build_continuation_prompt(task_id, additional)
        return self._run_cc_prompt(
            prompt, task_id=task_id, timeout=timeout, session_id=session_id
        )

    def _run_commandcode(self, text: str, *, timeout: float) -> AgentRun:
        task_id = f"cc-{uuid.uuid4().hex[:16]}"
        prompt = build_prompt(task_id, text)
        return self._run_cc_prompt(prompt, task_id=task_id, timeout=timeout)

    def _run_cc_prompt(
        self,
        prompt: str,
        *,
        task_id: str,
        timeout: float,
        session_id: str | None = None,
    ) -> AgentRun:
        try:
            result = self._get_client().run(
                prompt,
                task_id=task_id,
                session_id=session_id,
                timeout=timeout,
            )
        except CommandCodeTimeout as exc:
            return self._failed(task_id, str(exc))
        except (CommandCodeUnavailable, OSError) as exc:
            return self._failed(task_id, str(exc))

        allowed = {
            "mcp__lcp__get_platform_catalog",
            "mcp__lcp__evaluate_deployment_plan",
            "mcp__lcp__get_plan_report",
            "search_tools",
            "select_tool",
        }
        unauthorized = [name for name in result.tool_names if name not in allowed]
        if unauthorized:
            return self._failed(
                task_id, "Agent 使用了未授权工具：" + "、".join(unauthorized)
            )

        agent_ok = result.subtype == "success" and not result.error
        agent_status = "ok" if agent_ok else "error"
        agent_error = result.error or (
            None
            if agent_ok
            else f"Agent 会话结束状态：{result.subtype or result.stop_reason or 'unknown'}"
        )

        task_path = TASKS / f"{task_id}.json"
        if not task_path.exists():
            return AgentRun(
                session_id=task_id,
                task=AgentTaskResult(
                    status="failed",
                    session_id=task_id,
                    needs_confirmation=False,
                    error=result.error
                    or "Agent 未调用 evaluate_deployment_plan，未生成任何报告",
                ),
                assistant_text=result.final_text,
                provider_error=result.error,
                agent_status=agent_status,
                agent_error=agent_error,
                agent_session_id=result.session_id,
            )
        task = self._parse_task(task_path, task_id)
        if task.status != "completed":
            return AgentRun(
                session_id=task_id,
                task=task,
                assistant_text=result.final_text,
                provider_error=result.error,
                agent_status=agent_status,
                agent_error=agent_error,
                agent_session_id=result.session_id,
            )
        return self._load_completed(
            task_id,
            task,
            result.final_text,
            result.error,
            agent_status=agent_status,
            agent_error=agent_error,
            agent_session_id=result.session_id,
        )

    # ------------------------------------------------------------------
    # OpenCode
    # ------------------------------------------------------------------

    def _run_opencode(self, text: str, *, timeout: float) -> AgentRun:
        client = self._get_client()
        session_id = client.create_session(title="deployment-plan")
        client.prompt(session_id, text)
        try:
            client.wait(session_id, timeout=timeout)
        except OpenCodeTimeout as exc:
            return self._failed(session_id, str(exc))

        messages = client.messages(session_id)
        assistant = client.assistant_text(messages)
        provider_error = client.error_text(messages)
        task_path = TASKS / f"{session_id}.json"
        if not task_path.exists():
            return AgentRun(
                session_id=session_id,
                task=AgentTaskResult(
                    status="failed",
                    session_id=session_id,
                    needs_confirmation=False,
                    error=provider_error
                    or "Agent 未调用 evaluate_deployment_plan，未生成任何报告",
                ),
                assistant_text=assistant,
                provider_error=provider_error,
            )
        task = self._parse_task(task_path, session_id)
        agent_status = "error" if provider_error else "ok"
        if task.status != "completed":
            return AgentRun(
                session_id=session_id,
                task=task,
                assistant_text=assistant,
                provider_error=provider_error,
                agent_status=agent_status,
                agent_error=provider_error,
            )
        return self._load_completed(
            session_id,
            task,
            assistant,
            provider_error,
            agent_status=agent_status,
            agent_error=provider_error,
        )

    # ------------------------------------------------------------------
    # 共用：契约校验与产物加载
    # ------------------------------------------------------------------

    @staticmethod
    def _failed(session_id: str, error: str) -> AgentRun:
        return AgentRun(
            session_id=session_id,
            task=AgentTaskResult(
                status="failed",
                session_id=session_id,
                needs_confirmation=False,
                error=error,
            ),
        )

    @staticmethod
    def _parse_task(path: Path, session_id: str) -> AgentTaskResult:
        try:
            return AgentTaskResult.model_validate_json(
                path.read_text(encoding="utf-8")
            )
        except Exception as exc:  # noqa: BLE001
            return AgentTaskResult(
                status="failed",
                session_id=session_id,
                needs_confirmation=False,
                error=f"Agent 任务契约校验失败：{exc}",
            )

    def _load_completed(
        self,
        session_id: str,
        task: AgentTaskResult,
        assistant: str,
        provider_error: str | None,
        *,
        agent_status: str = "ok",
        agent_error: str | None = None,
        agent_session_id: str | None = None,
    ) -> AgentRun:
        try:
            base = RUNS.resolve()
            run_id = task.run_id or ""
            run_dir = (RUNS / run_id).resolve()
            if not run_id or run_dir.parent != base or not run_dir.is_dir():
                raise ValueError("run_id 与运行目录不一致")
            if task.session_id != session_id:
                raise ValueError("task_id 与任务契约不一致")
            if (ROOT / task.artifacts.get("dir", "")).resolve() != run_dir:
                raise ValueError("任务引用的目录不是本次运行目录")

            paths: dict[str, Path] = {}
            for key in ("json", "proposal", "technical"):
                raw = task.artifacts.get(key)
                if not raw:
                    raise ValueError(f"缺少产物引用：{key}")
                path = (ROOT / raw).resolve()
                if not path.is_relative_to(run_dir):
                    raise ValueError(f"产物 {key} 不在本次运行目录下")
                if not path.exists():
                    raise ValueError(f"缺少产物：{path.name}")
                paths[key] = path

            actual_hash = hashlib.sha256(paths["json"].read_bytes()).hexdigest()
            if not task.result_sha256 or actual_hash != task.result_sha256:
                raise ValueError("result.json 的 sha256 与任务绑定不一致")

            run_contract = AgentTaskResult.model_validate_json(
                (run_dir / "agent_result.json").read_text(encoding="utf-8")
            )
            if (
                run_contract.run_id != task.run_id
                or run_contract.session_id != session_id
                or run_contract.result_sha256 != task.result_sha256
            ):
                raise ValueError("本次运行目录的契约与任务文件不一致")

            result_json = json.loads(paths["json"].read_text(encoding="utf-8"))
            if result_json.get("request_id") != task.request_id:
                raise ValueError("result.json 的 request_id 与任务不一致")
            result = PlanResult.model_validate(result_json)
            return AgentRun(
                session_id=session_id,
                task=task,
                assistant_text=assistant,
                provider_error=provider_error,
                out_dir=run_dir,
                result=result,
                proposal_md=paths["proposal"].read_text(encoding="utf-8"),
                technical_md=paths["technical"].read_text(encoding="utf-8"),
                result_json=result_json,
                artifacts=dict(task.artifacts),
                agent_status=agent_status,
                agent_error=agent_error,
                agent_session_id=agent_session_id,
            )
        except Exception as exc:  # noqa: BLE001
            return AgentRun(
                session_id=session_id,
                task=AgentTaskResult(
                    status="failed",
                    session_id=session_id,
                    needs_confirmation=False,
                    error=f"Agent 产物不一致：{exc}",
                ),
                assistant_text=assistant,
                provider_error=provider_error,
                agent_status="error",
                agent_error=f"Agent 产物不一致：{exc}",
                agent_session_id=agent_session_id,
            )
