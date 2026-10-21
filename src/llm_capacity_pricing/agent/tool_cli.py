"""业务工具 CLI：OpenCode 通过它调用现有的 Sizing / 模拟 / SLA / TCO / Reporting。

只暴露三件事：

* ``catalog``  —— 支持的模型、GPU、机箱（只读）
* ``evaluate`` —— 结构化需求 → ``run_demo()`` → 真实 ``PlanResult`` + 报告
* ``report``   —— 按 run_id 取回已保存的结果与报告路径

数字全部由现有引擎生成；这里不实现任何公式，也不接受 Agent 自报的结论。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import uuid
from decimal import Decimal
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from llm_capacity_pricing.agent.schemas import (
    AgentPlanInput,
    AgentTaskResult,
    missing_core_fields,
)
from llm_capacity_pricing.app.demo_service import (
    DemoInputs,
    new_run_dir,
    run_demo,
    summary_lines,
)

ROOT = Path(__file__).resolve().parents[3]
RUNS = ROOT / "data/agent_runs"
TASKS = ROOT / "data/agent_tasks"
_SAFE_ID = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


def _safe_id(value: str | None, fallback: str) -> str:
    if value and _SAFE_ID.match(value):
        return value
    return fallback


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _publish(task: AgentTaskResult) -> AgentTaskResult:
    """任务结果同时写入会话任务文件与 stdout（OpenCode 工具读取 stdout）。"""
    key = _safe_id(task.session_id, task.run_id or "unknown")
    _write_json(TASKS / f"{key}.json", task.model_dump(mode="json"))
    print(task.model_dump_json())
    return task


def _failed(session_id: str | None, error: str) -> AgentTaskResult:
    return AgentTaskResult(
        status="failed",
        session_id=session_id,
        needs_confirmation=False,
        error=error,
    )


def _catalog() -> dict[str, Any]:
    import yaml

    def load(relative: str) -> dict[str, Any]:
        doc = yaml.safe_load((ROOT / relative).read_text(encoding="utf-8")) or {}
        return doc if isinstance(doc, dict) else {}

    models = load("data/models/model_manifest.yaml").get("models") or []
    hardware = load("data/hardware/hardware_catalog.yaml").get("hardware") or []
    servers = load("data/hardware/hardware_catalog.yaml").get("servers") or []
    return {
        "models": [
            {
                "model_id": item.get("model_id"),
                "repo_id": item.get("repo_id"),
            }
            for item in models
            if isinstance(item, dict)
        ],
        "hardware": [
            {
                "hardware_id": item.get("hardware_id"),
                "product_name": item.get("product_name"),
                "total_memory_gb": round(
                    (item.get("total_memory_bytes") or 0) / 1_000_000_000, 1
                )
                if item.get("total_memory_bytes")
                else None,
            }
            for item in hardware
            if isinstance(item, dict)
        ],
        "servers": [
            {
                "server_id": item.get("server_id"),
                "gpu_hardware_id": item.get("gpu_hardware_id"),
                "gpus_per_node": item.get("gpus_per_node"),
            }
            for item in servers
            if isinstance(item, dict)
        ],
        "notes": [
            "Catalog 只列本阶段支持的模型/GPU；未列出的组合不能分析。",
            "所有资源、性能、成本与 SLA 结论必须由 evaluate_deployment_plan 生成。",
        ],
    }


def _evaluate(session_id: str, payload: dict[str, Any]) -> AgentTaskResult:
    try:
        values = AgentPlanInput.model_validate(payload)
    except ValidationError as exc:
        fields = [".".join(str(part) for part in err["loc"]) for err in exc.errors()]
        return _publish(
            AgentTaskResult(
                status="needs_confirmation",
                session_id=session_id,
                needs_confirmation=True,
                missing_fields=fields or ["输入格式"],
                summary="输入字段不合法，请修正后重试。",
            )
        )

    missing = missing_core_fields(values)
    if missing:
        return _publish(
            AgentTaskResult(
                status="needs_confirmation",
                session_id=session_id,
                needs_confirmation=True,
                missing_fields=missing,
                summary="还缺以下需求字段：" + "、".join(missing),
            )
        )

    request_id = f"agent-{session_id[-10:]}"
    inputs = DemoInputs(
        model_id=values.model_id or "",
        hardware_id=values.hardware_id or "",
        server_id=values.server_id,
        gpu_count=int(values.gpu_count or 1),
        input_len=int(values.input_len or 1),
        output_len=int(values.output_len or 1),
        concurrency=int(values.concurrency or 1),
        ttft_p99_ms_max=values.ttft_p99_ms_max,
        tpot_p99_ms_max=values.tpot_p99_ms_max,
        budget_amount=values.budget_amount,
        budget_currency=values.budget_currency,
        weight_precision=values.weight_precision or "bf16",
        framework=values.framework or "vllm",
        framework_version=values.framework_version or "0.24.0",
        mode="offline",
        ranking=values.ranking or "throughput",
        top_n=2,
        request_id=request_id,
    )

    run_dir = new_run_dir(RUNS)
    try:
        outcome = run_demo(inputs, out_dir=run_dir, scenario_id="agent")
    except Exception as exc:  # noqa: BLE001 - 失败也必须给结构化契约
        return _publish(_failed(session_id, f"{type(exc).__name__}: {exc}"))

    result = outcome.result
    sim = getattr(result, "simulation", None)
    recommended = (
        sim.recommended(sim.ranking or "throughput")
        if sim is not None and sim.status == "computed"
        else None
    )
    best = (
        sim.best_effort()
        if sim is not None and sim.status == "computed"
        else None
    )
    conclusions = {
        "overall_status": result.status,
        "candidate_count": len(result.candidates),
        "observed_pass_count": sum(
            1 for c in result.candidates if c.sla_status == "observed_pass"
        ),
        "quote_publishable_count": sum(
            1 for c in result.candidates if c.quote_publishable
        ),
        "simulation_status": getattr(sim, "status", None),
        "simulation_recommendation": (
            recommended.candidate_id if recommended is not None else None
        ),
        "simulation_best_effort": best.candidate_id if best is not None else None,
    }
    artifacts = {
        "dir": str(run_dir),
        "proposal": str(outcome.proposal_path),
        "technical": str(outcome.technical_path),
        "json": str(outcome.json_path),
    }
    result_hash = hashlib.sha256(outcome.json_path.read_bytes()).hexdigest()
    task = AgentTaskResult(
        status="completed",
        session_id=session_id,
        run_id=run_dir.name,
        request_id=request_id,
        plan_result_available=True,
        needs_confirmation=False,
        summary="；".join(summary_lines(outcome)),
        artifacts=artifacts,
        conclusions=conclusions,
        result_sha256=result_hash,
        assumptions=[
            f"推理框架 {inputs.framework}"
            + ("（请求值）" if values.framework else "（示例默认）"),
            f"框架版本 {inputs.framework_version}"
            + ("（请求值）" if values.framework_version else "（示例默认）"),
            f"模型精度 {inputs.weight_precision or 'bf16'}"
            + ("（请求值）" if values.weight_precision else "（示例默认）"),
            "模拟来源：仓库内归档输出（offline，非实测）",
            (
                f"预算币种 {inputs.budget_currency}（请求值）"
                if inputs.budget_currency
                else "预算币种未指定；成本目录为 CNY（合成示例，未确认）"
            ),
        ],
    )
    _write_json(run_dir / "agent_result.json", task.model_dump(mode="json"))
    return _publish(task)


def _report(run_id: str, session_id: str | None) -> AgentTaskResult:
    if not _SAFE_ID.match(run_id):
        return _publish(_failed(session_id, f"非法 run_id：{run_id}"))
    base = RUNS.resolve()
    run_dir = (RUNS / run_id).resolve()
    if run_dir.parent != base or not run_dir.is_dir():
        return _publish(_failed(session_id, f"找不到运行目录：{run_id}"))

    contract_path = run_dir / "agent_result.json"
    if not contract_path.exists():
        return _publish(_failed(session_id, f"运行目录缺少 agent_result.json：{run_id}"))
    try:
        task = AgentTaskResult.model_validate_json(contract_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return _publish(_failed(session_id, f"任务契约校验失败：{exc}"))

    if task.status != "completed":
        return _publish(task)

    json_path = ROOT / task.artifacts.get("json", "")
    if not json_path.exists():
        return _publish(_failed(session_id, "任务引用的 result.json 不存在"))
    actual_hash = hashlib.sha256(json_path.read_bytes()).hexdigest()
    if not task.result_sha256 or actual_hash != task.result_sha256:
        return _publish(_failed(session_id, "result.json 与任务绑定的 sha256 不一致"))
    proposal_path = ROOT / task.artifacts.get("proposal", "")
    proposal = (
        proposal_path.read_text(encoding="utf-8") if proposal_path.exists() else ""
    )
    print(
        json.dumps(
            {
                "status": task.status,
                "run_id": task.run_id,
                "request_id": task.request_id,
                "summary": task.summary,
                "conclusions": task.conclusions,
                "artifacts": task.artifacts,
                "proposal_md": proposal,
            },
            ensure_ascii=False,
        )
    )
    return task


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="OpenCode 业务工具")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("catalog", help="列出支持的模型/GPU/机箱")

    ev = sub.add_parser("evaluate", help="结构化需求 -> 完整评估与报告")
    ev.add_argument("--session-id", default=None)
    ev.add_argument("--input-file", default=None)
    ev.add_argument("--input-json", default=None)

    rp = sub.add_parser("report", help="按 run_id 取回结果")
    rp.add_argument("--run-id", required=True)
    rp.add_argument("--session-id", default=None)

    args = parser.parse_args(argv)
    session_id = _safe_id(getattr(args, "session_id", None), f"session-{uuid.uuid4().hex[:12]}")

    try:
        if args.command == "catalog":
            print(json.dumps(_catalog(), ensure_ascii=False))
            return 0
        if args.command == "evaluate":
            if args.input_file:
                payload = json.loads(Path(args.input_file).read_text(encoding="utf-8"))
            elif args.input_json:
                payload = json.loads(args.input_json)
            else:
                payload = json.loads(sys.stdin.read() or "{}")
            if not isinstance(payload, dict):
                raise ValueError("输入必须是 JSON 对象")
            _evaluate(session_id, payload)
            return 0
        if args.command == "report":
            _report(args.run_id, session_id)
            return 0
    except Exception as exc:  # noqa: BLE001 - 工具调用永不抛栈，返回结构化失败
        _publish(_failed(session_id, f"{type(exc).__name__}: {exc}"))
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
