#!/usr/bin/env bash
# S2：一份需求 → 配置搜索 → 性能预测 → 成本 → 方案文档（一条命令）
#
#   bash scripts/run_system_demo.sh                     # 模式 A：离线（默认）
#   bash scripts/run_system_demo.sh --mode live         # 模式 B：真调 AISimulate
#   bash scripts/run_system_demo.sh --mode core         # 模式 C：不带模拟
#   bash scripts/run_system_demo.sh --gpu 4 --concurrency 64 --ttft 300 --out /tmp/demo
#
# 三种模式都不需要 GPU。模式 B 需要独立环境里的 AISimulate（AISIMULATE_PYTHON）。
# 每次运行写到独立目录，不覆盖已有数据库与历史结果。
set -u

cd "$(dirname "$0")/.."
PY="${PY:-.venv/bin/python}"

# 透传所有参数给 CLI；--out 留空则按时间戳新建目录
MODE="offline"
for arg in "$@"; do
  case "$arg" in
    --mode=*) MODE="${arg#--mode=}" ;;
    --mode) shift_next=1 ;;
  esac
done
# 处理 "--mode live" 这种分离写法
prev=""
for arg in "$@"; do
  if [ "$prev" = "--mode" ]; then MODE="$arg"; fi
  prev="$arg"
done

if [ "$MODE" = "live" ]; then
  if [ -z "${AISIMULATE_PYTHON:-}" ]; then
    printf '模式 B 需要 AISIMULATE_PYTHON 指向装了 aisimulate 的独立环境。\n' >&2
    printf '例：AISIMULATE_PYTHON=/opt/aisim/bin/python bash scripts/run_system_demo.sh --mode live\n' >&2
    printf '（模式 A 离线演示不需要该工具。）\n' >&2
    exit 2
  fi
fi

exec "$PY" -m llm_capacity_pricing.app.demo_cli "$@"
