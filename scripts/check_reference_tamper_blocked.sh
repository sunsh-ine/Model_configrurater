#!/usr/bin/env bash
# 反例验证（D7 / N1）：公开参考的"来源声明"与"入库取值"必须绑死。
#
#   bash scripts/check_reference_tamper_blocked.sh [输出目录]
#
# 三种篡改都必须**整批拒绝**（一条记录都不许入库）：
#   A. 只改入库字段，claim 原样保留        -> 字面量检查会通过，只有取值绑定能挡住
#   B. 只改 claim.value，tokens 原样保留   -> 字面量检查会通过，只有取值绑定能挡住
#   C. 取值与字面量一起改成原文里没有的值  -> 字面量检查就能挡住
#
# 成功时打印"核对失败（预期）…"并 exit 0；任何一步不符合预期即 exit 1。
set -u

cd "$(dirname "$0")/.."
PY="${PY:-.venv/bin/python}"
CLI="$PY -m llm_capacity_pricing.cli"
OUT="${1:-data/exports/tamper}"
SOURCE="data/incoming/public_benchmark_references_20261015.json"
METRIC="output_throughput_tok_s"

mkdir -p "$OUT"

# 造三份篡改输入（只改一处，其余原样）
$PY - "$SOURCE" "$OUT" <<'PYEOF'
import json
import sys
from pathlib import Path

source, out_dir = sys.argv[1], Path(sys.argv[2])
base = json.loads(Path(source).read_text(encoding="utf-8"))
metric = "output_throughput_tok_s"
claim_path = f"metrics.{metric}"


def dump(doc, name):
    path = out_dir / f"references.{name}.json"
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


# A：只改入库字段（claim 与字面量都是原文里的真值）
a = json.loads(json.dumps(base))
a["records"][0]["metrics"][metric] = 9999.99
dump(a, "record-only")

# B：只改 claim.value（记录与 tokens 不动，字面量仍在原文里）
b = json.loads(json.dumps(base))
b["records"][0]["claims"][claim_path]["value"] = 9999.99
dump(b, "claim-only")

# C：取值与字面量一起改成原文里没有的数
c = json.loads(json.dumps(base))
c["records"][0]["claims"][claim_path]["value"] = 9999.99
c["records"][0]["claims"][claim_path]["tokens"] = [
    "Output", "token", "throughput", "(tok/s):", "9999.99",
]
dump(c, "both")
print("已生成 3 份篡改输入：record-only / claim-only / both")
PYEOF

fail=0
for case in record-only claim-only both; do
  db="$OUT/$case.db"
  rm -f "$db"
  $CLI init-db --db "$db" >/dev/null

  output="$($PY scripts/import_public_benchmarks.py \
    --db "$db" --root . --references "$OUT/references.$case.json" 2>&1)"
  status=$?

  if [ "$status" -eq 0 ]; then
    printf '❌ %s：篡改后的输入竟然被接受\n%s\n' "$case" "$output"
    fail=1
    continue
  fi
  case "$output" in
    *核对失败*) : ;;
    *)
      printf '❌ %s：失败了，但没有指出是核对失败：\n%s\n' "$case" "$output"
      fail=1
      continue
      ;;
  esac

  count="$($PY - "$db" <<'PYEOF'
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
    printf '❌ %s：应当一条都不入库，实际入库 %s 条\n' "$case" "$count"
    fail=1
    continue
  fi
  reason="$(printf '%s' "$output" | grep -m1 '不一致' || printf '%s' "$output" | grep -m1 '未在' || true)"
  printf '✅ %s：整批拒绝，入库 0 条\n' "$case"
  printf '     %s\n' "$(printf '%s' "$reason" | sed 's/^ *//')"
done

if [ "$fail" -ne 0 ]; then
  exit 1
fi

printf '核对失败（预期）：三种篡改（只改入库值 / 只改声明值 / 两者都改）均被拒绝\n'
printf '每种情况入库记录数：0（整批拒绝，未产出部分可信记录）\n'
exit 0
