"""OpenCode Server 的最小 HTTP 客户端（Basic Auth）。

只依赖标准库；密码从环境变量或 data/agent/opencode.credentials（0600）读取。
"""

from __future__ import annotations

import base64
import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
ENV_FILE = ROOT / "data/agent/opencode.credentials"
# 兼容旧安装：V 阶段早期使用 opencode.env
LEGACY_ENV_FILE = ROOT / "data/agent/opencode.env"


class OpenCodeUnavailable(RuntimeError):
    """服务不可达、未配置密码或鉴权失败。"""


class OpenCodeTimeout(RuntimeError):
    """任务超时；调用方不得据此生成报告。"""


@dataclass(frozen=True)
class OpenCodeConfig:
    base_url: str
    password: str

    @classmethod
    def load(cls) -> "OpenCodeConfig":
        import os

        base_url = os.environ.get("OPENCODE_BASE_URL")
        password = os.environ.get("OPENCODE_SERVER_PASSWORD")
        if not base_url or not password:
            for path in (ENV_FILE, LEGACY_ENV_FILE):
                if not path.exists():
                    continue
                values: dict[str, str] = {}
                for line in path.read_text(encoding="utf-8").splitlines():
                    if "=" in line:
                        key, value = line.split("=", 1)
                        values[key.strip()] = value.strip()
                base_url = base_url or values.get("OPENCODE_BASE_URL")
                password = password or values.get("OPENCODE_SERVER_PASSWORD")
                if base_url and password:
                    break
        if not base_url or not password:
            raise OpenCodeUnavailable(
                "未配置 OpenCode 服务：请先运行 scripts/start_opencode_server.sh"
            )
        return cls(base_url=base_url.rstrip("/"), password=password)


class OpenCodeClient:
    def __init__(self, config: OpenCodeConfig, *, default_timeout: float = 60.0):
        self.config = config
        self.default_timeout = default_timeout
        token = base64.b64encode(
            f"opencode:{config.password}".encode("utf-8")
        ).decode("ascii")
        self._authorization = f"Basic {token}"

    def _request(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        *,
        timeout: float | None = None,
    ) -> Any:
        data = None if body is None else json.dumps(body).encode("utf-8")
        request = urllib.request.Request(
            self.config.base_url + path,
            data=data,
            method=method,
            headers={
                "Authorization": self._authorization,
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(
                request, timeout=timeout or self.default_timeout
            ) as response:
                raw = response.read().decode("utf-8")
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:300]
            raise OpenCodeUnavailable(
                f"OpenCode HTTP {exc.code}：{detail or exc.reason}"
            ) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise OpenCodeUnavailable(f"OpenCode 不可达：{exc}") from exc

    def health(self) -> bool:
        try:
            self._request("GET", "/api/model", timeout=3)
            return True
        except OpenCodeUnavailable:
            return False

    def create_session(
        self, *, agent: str = "deployment-planner", title: str = "deployment-plan"
    ) -> str:
        payload = self._request(
            "POST", "/api/session", {"agent": agent, "title": title}
        )
        session_id = (payload or {}).get("data", {}).get("id")
        if not session_id:
            raise OpenCodeUnavailable("OpenCode 未返回 session id")
        return str(session_id)

    def prompt(self, session_id: str, text: str) -> None:
        self._request(
            "POST", f"/api/session/{session_id}/prompt", {"text": text}
        )

    def wait(self, session_id: str, timeout: float = 240.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            remaining = max(1.0, deadline - time.monotonic())
            try:
                self._request(
                    "POST",
                    f"/api/experimental/session/{session_id}/wait",
                    timeout=min(10.0, remaining),
                )
                return
            except OpenCodeUnavailable:
                pass
            try:
                payload = self._request("GET", f"/api/session/{session_id}")
            except OpenCodeUnavailable:
                payload = None
            outcome = ((payload or {}).get("data") or {}).get("outcome")
            if outcome in ("succeeded", "failed", "interrupted"):
                return
            time.sleep(1.0)
        raise OpenCodeTimeout(f"Agent 任务超过 {int(timeout)} 秒未完成")

    def messages(self, session_id: str) -> list[dict[str, Any]]:
        payload = self._request("GET", f"/api/session/{session_id}/message")
        return list((payload or {}).get("data") or [])

    @staticmethod
    def assistant_text(messages: list[dict[str, Any]]) -> str:
        texts: list[str] = []
        for message in messages:
            if message.get("type") != "assistant":
                continue
            for part in message.get("content") or []:
                if part.get("type") == "text" and part.get("text"):
                    texts.append(str(part["text"]))
        return "\n".join(texts).strip()

    @staticmethod
    def error_text(messages: list[dict[str, Any]]) -> str | None:
        for message in reversed(messages):
            if message.get("type") in ("idle", "assistant") and message.get("error"):
                error = message["error"]
                if isinstance(error, dict):
                    return str(error.get("message") or error)
                return str(error)
        return None
