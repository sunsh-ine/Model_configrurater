"""Agent 任务编排：OpenCode 负责理解与调用工具，业务系统只认工具产物。"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

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
    def __init__(self, client: OpenCodeClient | None = None):
        self.client = client

    def _get_client(self) -> OpenCodeClient:
        if self.client is None:
            self.client = OpenCodeClient(OpenCodeConfig.load())
        return self.client

    def available(self) -> tuple[bool, str]:
        try:
            client = self._get_client()
        except OpenCodeUnavailable as exc:
            return False, str(exc)
        if client.health():
            return True, "OpenCode 服务可用（模型权限由服务端配置决定）"
        return False, "OpenCode 服务不可达"

    def run(self, text: str, *, timeout: float = 240.0) -> AgentRun:
        client = self._get_client()
        session_id = client.create_session(title="deployment-plan")
        client.prompt(session_id, text)
        try:
            client.wait(session_id, timeout=timeout)
        except OpenCodeTimeout as exc:
            return AgentRun(
                session_id=session_id,
                task=AgentTaskResult(
                    status="failed",
                    session_id=session_id,
                    needs_confirmation=False,
                    error=str(exc),
                ),
                provider_error=str(exc),
            )

        messages = client.messages(session_id)
        assistant = client.assistant_text(messages)
        provider_error = client.error_text(messages)
        task_path = TASKS / f"{session_id}.json"
        if not task_path.exists():
            # 模型说完成也不算数：没有业务工具产物就没有报告
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

        try:
            task = AgentTaskResult.model_validate_json(
                task_path.read_text(encoding="utf-8")
            )
        except Exception as exc:  # noqa: BLE001
            return AgentRun(
                session_id=session_id,
                task=AgentTaskResult(
                    status="failed",
                    session_id=session_id,
                    needs_confirmation=False,
                    error=f"Agent 任务契约校验失败：{exc}",
                ),
                assistant_text=assistant,
                provider_error=provider_error,
            )

        if task.status != "completed":
            return AgentRun(
                session_id=session_id,
                task=task,
                assistant_text=assistant,
                provider_error=provider_error,
            )

        try:
            run_dir = (ROOT / task.artifacts["dir"]).resolve()
            base = RUNS.resolve()
            if run_dir.parent != base or not run_dir.is_dir():
                raise ValueError("运行目录不在 data/agent_runs 下")
            json_path = ROOT / task.artifacts["json"]
            proposal_path = ROOT / task.artifacts["proposal"]
            technical_path = ROOT / task.artifacts["technical"]
            for path in (json_path, proposal_path, technical_path):
                if not path.exists():
                    raise ValueError(f"缺少产物：{path.name}")
            result_json = json.loads(json_path.read_text(encoding="utf-8"))
            result = PlanResult.model_validate(result_json)
            return AgentRun(
                session_id=session_id,
                task=task,
                assistant_text=assistant,
                provider_error=provider_error,
                out_dir=run_dir,
                result=result,
                proposal_md=proposal_path.read_text(encoding="utf-8"),
                technical_md=technical_path.read_text(encoding="utf-8"),
                result_json=result_json,
                artifacts=dict(task.artifacts),
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
            )
