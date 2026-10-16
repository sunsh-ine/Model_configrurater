#!/usr/bin/env bash
# 反例验证（D6）：DEMO_DB 指向**已存在**的数据库时，
# run_demo.sh 必须拒绝运行，并且**不得删除或修改**这个文件。
#
#   bash scripts/check_demo_db_protected.sh [输出目录]
#
# 成功时打印"拒绝覆盖（预期）…"并 exit 0；任何一步不符合预期即 exit 1。
set -u

cd "$(dirname "$0")/.."
OUT="${1:-data/exports/guard}"
SENTINEL="$OUT/precious.db"

mkdir -p "$OUT"
printf 'do-not-touch\n' > "$SENTINEL"
before="$(sha256sum "$SENTINEL" | cut -d' ' -f1)"

output="$(DEMO_DB="$SENTINEL" bash scripts/run_demo.sh "$OUT/run" 2>&1)"
status=$?

if [ "$status" -eq 0 ]; then
  printf 'DEMO_DB 指向已有数据库时应当拒绝运行，实际退出码为 0\n%s\n' "$output"
  exit 1
fi

case "$output" in
  *拒绝覆盖*) : ;;
  *)
    printf '预期拒绝覆盖，但输出里没有这句话：\n%s\n' "$output"
    exit 1
    ;;
esac

after="$(sha256sum "$SENTINEL" | cut -d' ' -f1)"
if [ "$before" != "$after" ]; then
  printf '用户已有的数据库被改动了：%s -> %s\n' "$before" "$after"
  exit 1
fi

printf '拒绝覆盖（预期）：已有数据库未被删除或修改\n'
printf '文件：%s（sha256 %s）\n' "$SENTINEL" "$after"
printf '退出码：%s（非零，符合预期）\n' "$status"
exit 0
