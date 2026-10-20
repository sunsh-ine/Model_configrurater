#!/usr/bin/env bash
# V7-R2 校验：自动搜索 -> 资源过滤 -> Top-N -> 原生 P99 回放。
#
#   bash scripts/check_auto_search.sh [数据库] [输出目录]
#
# 需要独立环境里的 AISimulate（AISIMULATE_PYTHON）。**没有就跳过**（exit 0），
# 因为离线 Demo 不依赖它；跳过会打印明确原因，不假装通过。
set -u

cd "$(dirname "$0")/.."
PY="${PY:-.venv/bin/python}"
CLI="$PY -m llm_capacity_pricing.cli"
DB="${1:-data/exports/demo/demo.db}"
OUT="${2:-data/exports/auto_sim_check}"
CONFIG="data/examples/scenario_simulation_auto.example.yaml"

TOOL="${AISIMULATE_PYTHON:-$(command -v aisimulate >/dev/null 2>&1 && dirname "$(command -v aisimulate)"/python)}"
if [ -z "${TOOL:-}" ] || [ ! -x "$TOOL" ]; then
  printf '跳过：未找到 AISimulate 独立环境（设置 AISIMULATE_PYTHON 后可跑；离线 Demo 不受影响）\n'
  exit 0
fi
if ! "$TOOL" -c "import aisimulate" >/dev/null 2>&1; then
  printf '跳过：%s 里没有安装 aisimulate（离线 Demo 不受影响）\n' "$TOOL"
  exit 0
fi
if [ ! -f "$DB" ]; then
  printf '缺少数据库 %s：请先运行 bash scripts/run_demo.sh\n' "$DB"
  exit 1
fi

mkdir -p "$OUT"
rm -rf "$OUT/auto_sim"
# 把示例里的 work_dir 改写到本次输出目录，保证可重复运行且不污染仓库路径
RUNTIME_CONFIG="$OUT/auto_config.yaml"
$PY - "$CONFIG" "$RUNTIME_CONFIG" "$OUT/auto_sim" <<'PYEOF'
import sys
import yaml

src, dst, work = sys.argv[1:4]
doc = yaml.safe_load(open(src, encoding="utf-8"))
doc.setdefault("simulation", {})["work_dir"] = work
open(dst, "w", encoding="utf-8").write(yaml.safe_dump(doc, allow_unicode=True))
PYEOF
AISIMULATE_PYTHON="$TOOL" $CLI plan --config "$RUNTIME_CONFIG" \
  --out "$OUT/auto_report.md" --json-out "$OUT/auto_result.json" --db "$DB" \
  >"$OUT/plan.log" 2>&1
if [ $? -ne 0 ]; then
  printf '自动搜索 plan 执行失败：\n'; tail -n 20 "$OUT/plan.log"; exit 1
fi

fail=0
grep -qE '模拟状态：\*\*已计算\*\*' "$OUT/auto_report.md" || { printf '缺少：模拟已计算\n'; fail=1; }
grep -qE '回放的工作点来自搜索结果' "$OUT/auto_report.md" || { printf '缺少：回放与搜索工作点的关联说明\n'; fail=1; }
if ! grep -q 'sim-native' "$OUT/auto_result.json"; then
  printf '缺少：原生回放候选\n'; fail=1
fi
if grep -q '"status": "error"' "$OUT/auto_result.json"; then
  printf '模拟状态为 error\n'; fail=1
fi
[ "$fail" -eq 0 ] || exit 1

printf '自动搜索检查通过：由需求自动生成搜索输入并完成 P99 回放（工作目录 %s）\n' "$OUT/auto_sim"
exit 0
