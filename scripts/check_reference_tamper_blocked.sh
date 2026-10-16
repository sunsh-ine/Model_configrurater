#!/usr/bin/env bash
# 反例验证（D7）：把公开参考的一条数值声明改成归档原文里没有的值，
# 导入必须**整批拒绝** —— 一条记录都不许入库，也不许产出"部分可信"的记录。
#
#   bash scripts/check_reference_tamper_blocked.sh [输出目录]
#
# 成功时打印"核对失败（预期）…"并 exit 0；任何一步不符合预期即 exit 1。
set -u

cd "$(dirname "$0")/.."
PY="${PY:-.venv/bin/python}"
CLI="$PY -m llm_capacity_pricing.cli"
OUT="${1:-data/exports/tamper}"
SOURCE="data/incoming/public_benchmark_references_20261015.json"
TAMPERED="$OUT/references.tampered.json"
DB="$OUT/tamper.db"

mkdir -p "$OUT"
rm -f "$TAMPERED" "$DB"

$PY - "$SOURCE" "$TAMPERED" <<'PYEOF'
import json
import sys

source, target = sys.argv[1], sys.argv[2]
doc = json.loads(open(source, encoding="utf-8").read())
record = doc["records"][0]
# 只改这一处：把取值与它声明的字面量一起换成原文里不存在的数
claim = record["claims"]["metrics.output_throughput_tok_s"]
claim["value"] = 9999.99
claim["tokens"] = ["Output", "token", "throughput", "(tok/s):", "9999.99"]
with open(target, "w", encoding="utf-8") as fh:
    json.dump(doc, fh, ensure_ascii=False, indent=2)
print(f"已篡改 {record['record_id']} 的 output_throughput_tok_s -> 9999.99")
PYEOF

$CLI init-db --db "$DB" >/dev/null

output="$($PY scripts/import_public_benchmarks.py \
  --db "$DB" --root . --references "$TAMPERED" 2>&1)"
status=$?

if [ "$status" -eq 0 ]; then
  printf '篡改后的声明仍然被接受：导入竟然成功了\n%s\n' "$output"
  exit 1
fi

case "$output" in
  *核对失败*) : ;;
  *)
    printf '导入失败了，但没有指出是核对失败：\n%s\n' "$output"
    exit 1
    ;;
esac

count="$($PY - "$DB" <<'PYEOF'
import sys

sys.path.insert(0, "src")
from llm_capacity_pricing.services import open_db

db, repo = open_db(sys.argv[1])
try:
    with db.session() as session:
        print(len(repo.list_benchmark_runs(session)))
finally:
    db.dispose()
PYEOF
)"

if [ "$count" != "0" ]; then
  printf '应当一条都不入库，实际入库 %s 条\n' "$count"
  exit 1
fi

printf '核对失败（预期）：篡改的数值声明未被接受\n'
printf '入库记录数：0（整批拒绝，未产出部分可信记录）\n'
printf '失败原因摘录：%s\n' "$(printf '%s' "$output" | grep -m1 '9999.99' || printf '%s' "$output" | head -n2 | tail -n1)"
exit 0
