#!/usr/bin/env bash
# 启动专用的 OpenCode 方案 Agent（仅本机监听）。
# 密码只写入 data/agent/opencode.env（0600），不进入仓库。
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WORKDIR="$ROOT/agent/opencode"
PORT="${OPENCODE_PORT:-4096}"
BASE="http://127.0.0.1:${PORT}"
STATE="$ROOT/data/agent"
LOG="$STATE/opencode_server.log"
ENVF="$STATE/opencode.env"

mkdir -p "$STATE"
chmod 700 "$STATE"

if curl -fsS -m 2 -u "opencode:$( [ -f "$ENVF" ] && sed -n 's/^OPENCODE_SERVER_PASSWORD=//p' "$ENVF" )" \
    "$BASE/api/model" >/dev/null 2>&1; then
  echo "OpenCode server already running: $BASE"
  exit 0
fi

if ss -ltn 2>/dev/null | grep -q ":${PORT} "; then
  echo "端口 ${PORT} 已被占用，但不是可复用的 OpenCode 服务；请设置 OPENCODE_PORT" >&2
  exit 2
fi

export LCP_ROOT="$ROOT"
export LCP_PYTHON="$ROOT/.venv/bin/python"

# 隔离配置：只保留本项目的 Agent 与 provider，不带全局 MCP/skills
export XDG_CONFIG_HOME="$STATE/xdg"
"$ROOT/.venv/bin/python" "$ROOT/scripts/prepare_opencode_config.py" >/dev/null

: > "$LOG"
chmod 600 "$LOG"

(
  cd "$WORKDIR"
  exec setsid opencode serve --hostname 127.0.0.1 --port "$PORT" --print-logs
) >>"$LOG" 2>&1 &
SERVER_PID=$!

for _ in $(seq 1 60); do
  if grep -q "server password" "$LOG" 2>/dev/null; then
    break
  fi
  if ! kill -0 "$SERVER_PID" 2>/dev/null; then
    echo "OpenCode 启动失败，日志：$LOG" >&2
    tail -20 "$LOG" >&2
    exit 1
  fi
  sleep 0.5
done

PASSWORD="$(sed -n 's/.*server password //p' "$LOG" | head -1 | tr -d '\r')"
if [ -z "$PASSWORD" ]; then
  echo "未能从日志读取 server password：$LOG" >&2
  exit 1
fi

umask 077
cat > "$ENVF" <<EOF
OPENCODE_BASE_URL=$BASE
OPENCODE_SERVER_PASSWORD=$PASSWORD
EOF
chmod 600 "$ENVF"

for _ in $(seq 1 40); do
  if curl -fsS -m 2 -u "opencode:${PASSWORD}" "$BASE/api/model" >/dev/null 2>&1; then
    echo "OpenCode server ready: $BASE"
    echo "凭据文件：$ENVF（0600）"
    exit 0
  fi
  sleep 0.5
done

echo "OpenCode 端口已监听，但 /api/model 鉴权检查失败；请查看 $LOG" >&2
exit 1
