#!/usr/bin/env bash
# P4 校验：模拟预测的离线可复现 + 与实测严格隔离。
#
#   bash scripts/check_simulation_report.sh [数据库] [输出目录]
#
# 前置：数据库里已导入模型/硬件/成本目录（run_demo.sh 产出的 demo.db 即可）。
# 全程不需要 GPU，也不需要安装 AISimulate —— 只读仓库里归档的模拟输出。
#
# 成功时打印"模拟报告检查通过…"并 exit 0。
set -u

cd "$(dirname "$0")/.."
PY="${PY:-.venv/bin/python}"
CLI="$PY -m llm_capacity_pricing.cli"
DB="${1:-data/exports/demo/demo.db}"
OUT="${2:-data/exports/simulation}"
CONFIG="data/examples/scenario_simulation.example.yaml"

mkdir -p "$OUT"
if [ ! -f "$DB" ]; then
  printf '缺少数据库 %s：请先运行 bash scripts/run_demo.sh\n' "$DB"
  exit 1
fi

$CLI plan --config "$CONFIG" \
  --out "$OUT/simulation_report.md" \
  --json-out "$OUT/simulation_result.json" \
  --db "$DB" >"$OUT/plan.log" 2>&1
status=$?
if [ "$status" -ne 0 ]; then
  printf 'plan 执行失败（exit %s）：\n' "$status"
  tail -n 20 "$OUT/plan.log"
  exit 1
fi

fail=0
check() { # <文件> <正则> <说明>
  if ! grep -qE "$2" "$1"; then
    printf '缺少：%s\n' "$3"
    fail=1
  fi
}

check "$OUT/simulation_report.md" '^## 2\.2 模拟部署配置推荐（\*\*非实测\*\*）' "模拟章节标题"
check "$OUT/simulation_report.md" '模拟状态：\*\*已计算\*\*' "模拟状态"
check "$OUT/simulation_report.md" '模拟 TTFT P99' "模拟 TTFT P99"
check "$OUT/simulation_report.md" '模拟 TPOT P99' "模拟 TPOT P99"
check "$OUT/simulation_report.md" '预测\*\*未\*\*满足阈值（模拟）' "预测结论"
check "$OUT/simulation_report.md" '经验修正系数（\*\*非实测\*\*）' "修正系数披露"
check "$OUT/simulation_report.md" '`predicted_\*` 与 `observed_\*` 是两套取值' "与实测的区别"
# 未配置 simulation 的普通 Demo 报告里不应出现这一节
if grep -qE '^## 2\.2 模拟' data/exports/demo/demo_report.md 2>/dev/null; then
  printf '未开模拟的 Demo 报告里出现了模拟章节（不该有）\n'
  fail=1
fi

$PY - "$OUT/simulation_result.json" <<'PYEOF' || fail=1
import json
import sys

payload = json.load(open(sys.argv[1], encoding="utf-8"))
sim = payload.get("simulation")
problems = []
if not sim:
    problems.append("结果里没有 simulation 对象")
else:
    if sim.get("status") != "computed":
        problems.append(f"模拟状态不是 computed：{sim.get('status')}")
    if sim.get("data_trust") != "simulated_prediction":
        problems.append(f"可信标签不对：{sim.get('data_trust')}")
    verdicts = {c["verdict"] for c in sim.get("candidates") or []}
    if not verdicts <= {"predicted_pass", "predicted_fail", "insufficient_prediction"}:
        problems.append(f"出现非模拟结论：{verdicts}")
    if not verdicts & {"predicted_fail"}:
        problems.append("本示例应当出现 predicted_fail（P99 超限）")
    # 模拟不能写进实测结构
    if "benchmark_runs" in payload or "capacity_points" in payload:
        problems.append("结果里出现了实测结构")
    for candidate in payload.get("candidates") or []:
        if candidate.get("sla_status") == "observed_pass":
            problems.append("候选出现了 observed_pass")
    # 成本不完整时不得给总额
    for candidate in sim.get("candidates") or []:
        cost = candidate.get("cost")
        if cost and cost.get("incomplete") and cost.get("total_cash_over_horizon") is not None:
            problems.append("成本不完整却给了总额")

if problems:
    print("模拟结果检查未通过：")
    for problem in problems:
        print("  -", problem)
    raise SystemExit(1)
print("模拟结果 JSON 检查通过：判定全为 predicted_*/insufficient，可信标签 simulated_prediction")
PYEOF

if [ "$fail" -ne 0 ]; then
  exit 1
fi

printf '模拟报告检查通过：无 GPU、未安装 AISimulate 也能离线复现（%s）\n' "$OUT/simulation_report.md"
exit 0
