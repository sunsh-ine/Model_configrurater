"""本地 MCP server：把三个业务工具暴露给 Command Code。

MCP 的 stdout 是协议通道，因此这里通过**子进程**调用现有 tool_cli 并捕获输出，
不把任何 print 混进协议流。业务数字仍全部来自 evaluate_plan()。
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

from mcp.server.mcpserver import MCPServer

ROOT = Path(__file__).resolve().parents[3]
_SAFE_TASK_ID = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


def _tool_env() -> dict[str, str]:
    """业务工具只接收运行所需的最小环境，不继承模型凭据等变量。"""
    keep = ("PATH", "LANG", "LC_ALL", "TZ", "TMPDIR", "PYTHONIOENCODING")
    env = {key: os.environ[key] for key in keep if key in os.environ}
    env["PYTHONPATH"] = str(ROOT / "src")
    env["PYTHONHASHSEED"] = "0"
    return env

mcp = MCPServer("llm-capacity-pricing")


def _run(args: list[str], stdin: str | None = None, *, timeout: int = 240) -> str:
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "llm_capacity_pricing.agent.tool_cli", *args],
            input=stdin,
            capture_output=True,
            text=True,
            cwd=ROOT,
            env=_tool_env(),
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return json.dumps(
            {
                "status": "failed",
                "needs_confirmation": False,
                "error": f"业务工具超过 {timeout} 秒未完成",
            },
            ensure_ascii=False,
        )
    output = (proc.stdout or "").strip()
    if proc.returncode == 0 and output:
        return output
    detail = (proc.stderr or proc.stdout or "")[-800:]
    return json.dumps(
        {
            "status": "failed",
            "needs_confirmation": False,
            "error": f"业务工具失败（exit {proc.returncode}）：{detail}",
        },
        ensure_ascii=False,
    )


@mcp.tool()
def get_platform_catalog() -> str:
    """List the supported model IDs, GPU hardware IDs and server/chassis IDs."""
    return _run(["catalog"], timeout=30)


@mcp.tool()
def evaluate_deployment_plan(
    task_id: str,
    model_id: str | None = None,
    hardware_id: str | None = None,
    server_id: str | None = None,
    gpu_count: int | None = None,
    input_len: int | None = None,
    output_len: int | None = None,
    concurrency: int | None = None,
    ttft_p99_ms_max: float | None = None,
    tpot_p99_ms_max: float | None = None,
    budget_amount: float | None = None,
    budget_currency: str | None = None,
    weight_precision: str | None = None,
    framework: str | None = None,
    framework_version: str | None = None,
    ranking: str | None = None,
) -> str:
    """Run the existing deployment planner for structured requirements.

    task_id is mandatory and must be copied verbatim from the user prompt.
    Missing core fields return status=needs_confirmation; failures return
    status=failed and never produce a report.
    """
    if not _SAFE_TASK_ID.match(task_id or ""):
        return json.dumps(
            {
                "status": "failed",
                "needs_confirmation": False,
                "error": "task_id 不合法；请原样使用用户消息里的 TASK_ID",
            },
            ensure_ascii=False,
        )
    payload = {
        "model_id": model_id,
        "hardware_id": hardware_id,
        "server_id": server_id,
        "gpu_count": gpu_count,
        "input_len": input_len,
        "output_len": output_len,
        "concurrency": concurrency,
        "ttft_p99_ms_max": ttft_p99_ms_max,
        "tpot_p99_ms_max": tpot_p99_ms_max,
        "budget_amount": budget_amount,
        "budget_currency": budget_currency,
        "weight_precision": weight_precision,
        "framework": framework,
        "framework_version": framework_version,
        "ranking": ranking,
    }
    payload = {key: value for key, value in payload.items() if value is not None}
    return _run(
        ["evaluate", "--session-id", task_id],
        json.dumps(payload, ensure_ascii=False),
    )


@mcp.tool()
def get_plan_report(task_id: str, run_id: str) -> str:
    """Fetch the verified result for this task_id + run_id only.

    task_id must be the current task; guessed historical run_ids are rejected.
    """
    return _run(
        ["report", "--run-id", run_id, "--task-id", task_id], timeout=60
    )


if __name__ == "__main__":
    mcp.run(transport="stdio")
