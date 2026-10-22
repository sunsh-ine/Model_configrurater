"""Command Code 非交互适配层。

Command Code 负责理解需求与调用 MCP 工具；业务结果仍以
``data/agent_tasks/<task_id>.json`` 为准，模型的 finalText 只作解释。

headless 默认禁止写文件/改文件/shell，因此这里**不使用** ``--yolo``、
``--tools-all`` 或 ``--accept-edits``。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
WORKSPACE = ROOT / "data/agent/cc-workspace"
SANDBOX_HOME = ROOT / "data/agent/cc-homes"
TASKS = ROOT / "data/agent_tasks"
DEFAULT_MODEL = "deepseek/deepseek-v4-flash"

#: 任务 ID 只允许字母/数字开头，禁止 ``.``、``..``、路径分隔符与空串。
_TASK_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def validate_task_id(task_id: str) -> str:
    """校验任务 ID；非法值一律拒绝，避免拼进沙箱目录或任务文件路径。"""
    if not isinstance(task_id, str) or not _TASK_ID_RE.fullmatch(task_id):
        raise ValueError(f"非法 task_id：{task_id!r}")
    return task_id


def _sandbox_home(task_id: str) -> Path:
    """返回 ``cc-homes`` 的直接子目录；拒绝任何越界或符号链接。"""
    validate_task_id(task_id)
    base = SANDBOX_HOME.resolve()
    home = base / task_id
    if home.parent != base:
        raise ValueError(f"非法 task_id：{task_id!r}")
    if home.is_symlink():
        raise ValueError(f"非法 task_id：{task_id!r}（符号链接）")
    return home


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
6. If status=completed, call get_plan_report with task_id="{task_id}" and the run_id,
   then reply in Chinese with only: run_id, the summary/conclusions, recommendation or best-effort,
   assumptions, and the main limitations. Do not add any number absent from the
   tool output.
"""


def build_continuation_prompt(task_id: str, additional: str) -> str:
    return f"""TASK_ID: {task_id}

补充信息：
{additional}

Continue the SAME task; do not restart or re-ask for fields already provided.
Use ONLY the lcp MCP tools. Call evaluate_deployment_plan with
task_id="{task_id}" and the now-complete fields; if it returns completed, call get_plan_report with task_id="{task_id}" and the
run_id, then reply with the same concise Chinese summary.
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
        source_home: Path | None = None,
    ):
        self.binary = binary or os.environ.get("COMMAND_CODE_BIN") or "command-code"
        self.model = model or os.environ.get("COMMAND_CODE_MODEL") or DEFAULT_MODEL
        self.workspace = workspace or WORKSPACE
        self.timeout = timeout
        self.source_home = source_home or Path.home()

    def sandbox_available(self) -> bool:
        return bool(
            shutil.which("bwrap") and shutil.which("node")
        ) and os.environ.get("COMMAND_CODE_SANDBOX") != "0"

    def _prepare_sandbox(self, task_id: str) -> Path:
        """每个任务一个隔离 HOME，避免并发任务共用 Session/状态库。"""
        home = _sandbox_home(task_id)
        (home / ".commandcode").mkdir(parents=True, exist_ok=True)
        (home / ".cache").mkdir(parents=True, exist_ok=True)
        (home / ".local/share").mkdir(parents=True, exist_ok=True)
        os.chmod(home, 0o700)
        os.chmod(home / ".commandcode", 0o700)
        source = self.source_home / ".commandcode"
        auth = source / "auth.json"
        if not auth.exists():
            raise CommandCodeUnavailable(
                "Command Code 未登录：缺少 auth.json；请先在本机登录"
            )
        (home / ".commandcode" / "auth.json").write_bytes(auth.read_bytes())
        os.chmod(home / ".commandcode" / "auth.json", 0o600)

        config = source / "config.json"
        if config.exists():
            dst = home / ".commandcode" / "config.json"
            dst.write_bytes(config.read_bytes())
            os.chmod(dst, 0o600)

        # 只保留 lcp 一个 MCP server，不继承其他无关 MCP
        mcp_source = source / "mcp.json"
        if not mcp_source.exists():
            raise CommandCodeUnavailable("未注册 lcp MCP server（缺少 mcp.json）")
        try:
            doc = json.loads(mcp_source.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            raise CommandCodeUnavailable(f"mcp.json 无法解析：{exc}") from exc
        servers = doc.get("mcpServers") or doc.get("servers") or {}
        lcp = servers.get("lcp") if isinstance(servers, dict) else None
        if not lcp:
            raise CommandCodeUnavailable("mcp.json 中没有 lcp server")
        filtered = {"mcpServers": {"lcp": lcp}}
        dst = home / ".commandcode" / "mcp.json"
        dst.write_text(
            json.dumps(filtered, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.chmod(dst, 0o600)
        self._cleanup_stale()
        return home

    def cleanup(self, task_id: str) -> None:
        """删除该任务的凭据副本；只动 data/agent/cc-homes，不碰真实 HOME。"""
        home = _sandbox_home(task_id)
        shutil.rmtree(home, ignore_errors=True)

    def _cleanup_stale(self, max_age_hours: int = 24) -> None:
        if not SANDBOX_HOME.exists():
            return
        import time

        cutoff = time.time() - max_age_hours * 3600
        for entry in SANDBOX_HOME.iterdir():
            try:
                if (
                    entry.is_dir()
                    and not entry.is_symlink()
                    and entry.stat().st_mtime < cutoff
                ):
                    shutil.rmtree(entry, ignore_errors=True)
            except OSError:
                continue

    def _sandbox_command(
        self, path: str, args: list[str], *, task_id: str
    ) -> list[str] | None:
        if not self.sandbox_available():
            return None
        bwrap = shutil.which("bwrap")
        node = shutil.which("node")
        if not bwrap or not node:
            return None
        home = self._prepare_sandbox(task_id)
        node_bin = str(Path(node).resolve().parent)
        npm_bin = str(Path(path).parent)
        ro_dirs = [
            "/usr",
            "/bin",
            "/lib",
            "/lib64",
            "/etc",
            str(Path.home() / ".npm-global"),
            str(Path.home() / ".local/share/fnm"),
            str(ROOT / ".venv"),
            str(ROOT / "src"),
            str(ROOT / "app"),
            str(ROOT / "scripts"),
            str(ROOT / "data/models"),
            str(ROOT / "data/hardware"),
            str(ROOT / "data/examples"),
            str(ROOT / "data/incoming"),
            str(ROOT / "data/raw/official"),
            str(ROOT / "data/raw/vendor"),
            str(ROOT / "data/sources"),
            str(ROOT / "data/fixtures"),
            str(ROOT / "data/simulation"),
            str(ROOT / "docs/poc"),
        ]
        rw_dirs = [
            str(ROOT / "data/agent_runs"),
            str(ROOT / "data/agent_tasks"),
            str(WORKSPACE),
            str(home),
        ]
        command = [bwrap, "--dev", "/dev", "--proc", "/proc", "--tmpfs", "/tmp"]
        for directory in ro_dirs:
            if Path(directory).exists():
                command.extend(["--ro-bind", directory, directory])
        for directory in rw_dirs:
            Path(directory).mkdir(parents=True, exist_ok=True)
            command.extend(["--bind", directory, directory])
        command.extend(
            [
                "--share-net",
                "--die-with-parent",
                "--setenv",
                "HOME",
                str(home),
                "--setenv",
                "PATH",
                f"{node_bin}:{npm_bin}:/usr/local/bin:/usr/bin:/bin",
                "--setenv",
                "LANG",
                "C.UTF-8",
                "--chdir",
                str(WORKSPACE),
                "--",
                path,
                *args,
            ]
        )
        return command

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
        if not self.sandbox_available():
            if os.environ.get("COMMAND_CODE_SANDBOX") == "0":
                return True, f"Command Code {version} · NO sandbox (debug)"
            return False, "bwrap 沙箱不可用：默认拒绝无沙箱运行（调试可显式设置 COMMAND_CODE_SANDBOX=0）"
        return True, f"Command Code {version} · bwrap sandbox"

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
        validate_task_id(task_id)
        path = shutil.which(self.binary)
        if not path:
            raise CommandCodeUnavailable(f"未找到 {self.binary}")
        self.workspace.mkdir(parents=True, exist_ok=True)
        args = [
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
            args.extend(["--session", session_id])
        args.append(prompt)
        sandboxed = self._sandbox_command(path, args, task_id=task_id)
        if sandboxed is None and os.environ.get("COMMAND_CODE_SANDBOX") != "0":
            raise CommandCodeUnavailable(
                "bwrap 沙箱不可用：拒绝在宿主机直接运行 Command Code"
            )
        command = sandboxed or [path, *args]
        try:
            proc = subprocess.run(
                command,
                cwd=self.workspace,
                capture_output=True,
                text=True,
                timeout=timeout or self.timeout,
                env={
                    key: os.environ[key]
                    for key in (
                        "PATH",
                        "LANG",
                        "LC_ALL",
                        "TERM",
                        "HTTP_PROXY",
                        "HTTPS_PROXY",
                        "NO_PROXY",
                        "http_proxy",
                        "https_proxy",
                        "no_proxy",
                    )
                    if key in os.environ
                }
                | {
                    "HOME": (
                        str(_sandbox_home(task_id))
                        if sandboxed
                        else str(Path.home())
                    )
                },
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
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "").strip()[-500:]
            run.error = f"Command Code 退出码 {proc.returncode}：{detail}"
        elif parsed and parsed.get("subtype") != "success":
            run.error = str(parsed.get("error") or parsed.get("subtype") or "任务失败")
        elif not parsed:
            run.error = (proc.stderr or proc.stdout or "").strip()[-500:] or "无结果事件"
        return run

    @staticmethod
    def task_path(task_id: str) -> Path:
        return TASKS / f"{validate_task_id(task_id)}.json"
