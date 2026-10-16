#!/usr/bin/env bash
# Demo：公开数据驱动的部署方案评估（一条命令跑通）
#
#   bash scripts/run_demo.sh [输出目录]
#
# 无需 GPU、无需联网：公开 Benchmark 来源文件已归档在 data/raw/official/，
# 导入时会逐个做 sha256 校验，校验失败即中止。
#
# 数据分层（报告里逐项标注，互不混淆）：
#   公开实测 measured_vendor_published —— SGLang 官方 Cookbook 发布结果
#   假设成本 synthetic_example         —— 示例成本目录，不是真实报价
#   synthetic_fixture                  —— 本 Demo 不使用（仅算法验证用）
set -eu
# 注意：不用 pipefail —— `| head` 会因 SIGPIPE 让整个脚本提前退出

cd "$(dirname "$0")/.."
ROOT="$(pwd)"
PY="${PY:-.venv/bin/python}"
CLI="$PY -m llm_capacity_pricing.cli"
OUT_DIR="${1:-data/exports}"
DB="${DEMO_DB:-$OUT_DIR/demo.db}"
CONFIG="data/examples/demo_qwen3_32b_h100.yaml"

mkdir -p "$OUT_DIR"
rm -f "$DB"

echo "== 1/6 建库 =="
$CLI init-db --db "$DB" >/dev/null

echo "== 2/6 导入模型与硬件规格 =="
$CLI import-models   --manifest data/models/model_manifest.yaml --db "$DB" --root . >/dev/null
$CLI import-hardware --catalog  data/hardware/hardware_catalog.yaml --db "$DB" >/dev/null

echo "== 3/6 导入公开 Benchmark（来源 sha256 校验）=="
$PY scripts/import_public_benchmarks.py --db "$DB" | sed -n "1,3p"

echo "== 4/6 导入假设成本目录（合成示例，不是真实报价）=="
$CLI import-cost-catalog --file data/examples/phase4_cost_inputs.example.yaml --db "$DB" >/dev/null

echo "== 5/6 数据质量分级 =="
$CLI data-quality --db "$DB" | sed -n "1,6p"

echo "== 6/6 一条命令生成方案报告 =="
$CLI plan --config "$CONFIG" \
  --out "$OUT_DIR/demo_report.md" \
  --json-out "$OUT_DIR/demo_result.json" \
  --db "$DB"

echo
echo "报告: $OUT_DIR/demo_report.md"
echo "结果: $OUT_DIR/demo_result.json"
echo
echo "说明：公开数据只作**参考**（不参与本需求的 SLA 判定）；"
echo "      成本为假设值；正式报价自动发布保持禁用。"
