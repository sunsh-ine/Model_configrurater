#!/usr/bin/env python3
"""生成隔离的 OpenCode 配置：只保留本项目的方案 Agent 和模型 provider。

全局配置里的 MCP、skills、instructions 不会进入 Agent 服务；
provider 的密钥只写入 data/agent/xdg/（0600，git 忽略）。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AGENT_CONFIG = ROOT / "agent/opencode/opencode.json"
GLOBAL_CONFIG = Path.home() / ".config/opencode/opencode.json"
OUT = ROOT / "data/agent/xdg/opencode/opencode.json"


def main() -> int:
    if not AGENT_CONFIG.exists():
        print(f"缺少 {AGENT_CONFIG}", file=sys.stderr)
        return 1
    config = json.loads(AGENT_CONFIG.read_text(encoding="utf-8"))

    if GLOBAL_CONFIG.exists():
        global_config = json.loads(GLOBAL_CONFIG.read_text(encoding="utf-8"))
        # 只复制 provider（含服务端密钥）与默认模型，不复制 MCP/skills/instructions
        if isinstance(global_config.get("provider"), dict):
            config.setdefault("provider", {})
            for name, provider in global_config["provider"].items():
                config["provider"].setdefault(name, provider)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(OUT)
    print(OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
