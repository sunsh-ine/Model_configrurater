#!/usr/bin/env bash
# 一键复现验证：从零建库 -> 导入 -> 官方候选 -> 校验 -> 计算 -> 情景 -> 报告 -> 测试
#
# 环境要求：无 GPU、无互联网。仅需 Python 3.11+ 与 requirements.lock 中的依赖。
#
#   bash scripts/verify_all.sh
#
# 输出约定（为控制 stdout 体积，避免整段日志灌进对话上下文）：
#   * 完整输出（每步明细、失败堆栈、全部 JSON）逐行写入 $LOG，默认 docs/test_run_log.txt
#   * stdout 只打印每步一行的 [ok]/[FAIL] 摘要，外加该步的关键行（各步的 SUMMARY_RE）
#   * 某步失败时额外打印该步输出的末尾 $TAIL_LINES 行，随即停止（与原先的 || exit 1 等价）
#   * 直接运行即可，不需要重定向；若把 stdout 重定向到 $LOG，脚本会自动改用 *.full.log
#
# 可覆盖的环境变量：PY、DB、OUT_DIR、LOG、TAIL_LINES、SUMMARY_MAX
#   DB=/tmp/vtest/local.db OUT_DIR=/tmp/vtest/exports LOG=/tmp/vtest/log.txt \
#     bash scripts/verify_all.sh        # 隔离跑一遍，不动 data/ 下的产物
set -uo pipefail

cd "$(dirname "$0")/.."
ROOT="$(pwd)"
PY="${PY:-.venv/bin/python}"
CLI="$PY -m llm_capacity_pricing.cli"

EXCEL="data/raw/Token产能计算器-0910.xlsx"
MANIFEST="data/sources/official_manifest.yaml"
INCOMING="data/incoming/sglang_official_benchmark_candidates_20261015.json"
MODEL_MANIFEST="data/models/model_manifest.yaml"
HW_CATALOG="data/hardware/hardware_catalog.yaml"
PERF_CURVE_INDEX="data/fixtures/perf_curve/INDEX.json"
# 第四阶段（M26-M33）合成示例输入
P4_CATALOG="data/examples/phase4_cost_inputs.example.yaml"
P4_PLAN="data/examples/phase4_cost_plan.example.yaml"
P4_DEMAND="data/examples/phase4_demand.example.yaml"
P4_CONFIRMATION="data/examples/phase4_commercial_confirmation.example.yaml"
P4_APPROVALS="data/examples/phase4_approvals.example.yaml"
# S1：统一评估入口的示例配置
S1_MINIMAL="data/examples/scenario_minimal.example.yaml"
S1_FULL="data/examples/scenario_full.example.yaml"
DB="${DB:-data/local.db}"
OUT_DIR="${OUT_DIR:-data/exports}"

LOG="${LOG:-docs/test_run_log.txt}"
TAIL_LINES="${TAIL_LINES:-40}"
SUMMARY_MAX="${SUMMARY_MAX:-12}"

# 调用方若把 stdout 重定向到 $LOG，两边写同一个文件会互相覆盖，这里自动避让。
# 注意必须用 test -ef 直接比较：在 $( ) 里 /dev/stdout 指向的是命令替换的管道，不是本脚本的 fd 1。
if [ -e "$LOG" ] && [ /dev/stdout -ef "$LOG" ]; then
  LOG="${LOG%.log}.full.log"
fi
mkdir -p "$(dirname "$LOG")"
: > "$LOG"
{
  printf '本日志由 %s 生成；其中金额/比率类字段值已替换为 <redacted:internal>（V1.2 F1）。\n' \
    "scripts/verify_all.sh"
  printf '被脱敏的只有"值"，键名、条数、误差量级、门禁结论均保留，可用于复核。\n'
  printf '完整内部金额可在本地 data/exports/ 查看（该目录不入版本库）。\n\n'
} >>"$LOG"

STEPS_TOTAL=0
STEPS_FAILED=0
FAILED_LIST=""

redact_internal_values() { # <文件>  把内部金额/比率值替换为 <redacted>
  # 日志要保留可复核性，但**不能**把内部定价与派生金额写进公开仓库（V1.2 F1）。
  # 只屏蔽"值"，保留键名与结构，这样结论（条数/误差/门禁）仍然可读。
  local f="$1"
  sed -i -E 's/("((input|output|total|scenario|legacy)[a-z0-9_]*revenue[a-z0-9_]*|legacy_cost[a-z0-9_]*|gpu_annual_cost[a-z0-9_]*|total_cluster_(cost|revenue)[a-z0-9_]*|revenue_cost_ratio|legacy_revenue_cost_ratio|breakeven_monetization_ratio|scenario_surplus[a-z0-9_]*|cache_hit_rate_used|annual_gpu_cost_10k)"[[:space:]]*:[[:space:]]*)"[^"]*"/\1"<redacted:internal>"/g' "$f"
  sed -i -E 's/("((input|output|total|scenario|legacy)[a-z0-9_]*revenue[a-z0-9_]*|legacy_cost[a-z0-9_]*|gpu_annual_cost[a-z0-9_]*|revenue_cost_ratio|breakeven_monetization_ratio|annual_gpu_cost_10k)"[[:space:]]*:[[:space:]]*)-?[0-9]+(\.[0-9]+)?/\1"<redacted:internal>"/g' "$f"
}

print_key_lines() { # <输出文件> <关键行正则>
  local file="$1" re="$2"
  [ -n "$re" ] || return 0
  grep -aE "$re" "$file" 2>/dev/null | head -n "$SUMMARY_MAX" | sed 's/^/       /'
  return 0
}

finish() {
  {
    printf '\n==== 汇总 ====\n'
    printf '步骤 %s 项：通过 %s，失败 %s\n' \
      "$STEPS_TOTAL" "$((STEPS_TOTAL - STEPS_FAILED))" "$STEPS_FAILED"
    [ -n "$FAILED_LIST" ] && printf '失败步骤: %s\n' "$FAILED_LIST"
    printf '完成时间: %s\n' "$(date -Iseconds)"
    printf '环境: %s\n' "$($PY -V 2>&1)"
    if [ "$STEPS_FAILED" -eq 0 ]; then
      printf '全部步骤通过。\n'
    fi
  } | tee -a "$LOG"
  return 0
}

run_step() { # <编号> <名称> <失败即中止 0|1> <关键行正则> <命令...>
  local no="$1" name="$2" fatal="$3" re="$4"
  shift 4
  local tmp st
  tmp="$(mktemp)"
  {
    printf '\n=== %s. %s ===\n' "$no" "$name"
    "$@"
  } >"$tmp" 2>&1
  st=$?
  redact_internal_values "$tmp"
  cat "$tmp" >>"$LOG"

  STEPS_TOTAL=$((STEPS_TOTAL + 1))
  if [ "$st" -eq 0 ]; then
    printf '[ok]   %s. %s\n' "$no" "$name"
    print_key_lines "$tmp" "$re"
  else
    STEPS_FAILED=$((STEPS_FAILED + 1))
    FAILED_LIST="${FAILED_LIST}${no}. ${name}; "
    printf '[FAIL] %s. %s (exit %s)\n' "$no" "$name" "$st"
    print_key_lines "$tmp" "$re"
    printf '       ---- 该步输出末尾 %s 行（完整日志: %s）----\n' "$TAIL_LINES" "$LOG"
    tail -n "$TAIL_LINES" "$tmp" | sed 's/^/       /'
    printf '       %s\n' "----------------------------------------------------------"
  fi

  rm -f "$tmp"

  if [ "$st" -ne 0 ] && [ "$fatal" = 1 ]; then
    finish
    exit "$st"
  fi
  return 0
}

step_env() {
  $PY -V
  $PY -c "import pydantic, sqlalchemy, openpyxl, yaml, typer; print('deps ok: pydantic', pydantic.VERSION, '| sqlalchemy', sqlalchemy.__version__)"
}

step_cleanup() {
  rm -f "$DB" "$OUT_DIR"/*.json "$OUT_DIR"/*.md
  mkdir -p "$OUT_DIR"
}

step_import_fixtures() {
  $CLI import-sglang --file data/fixtures/sglang_synthetic.jsonl --metadata config/fixture_sglang.yaml --db "$DB" || return 1
  $CLI import-sglang --file data/fixtures/sglang_renamed_fields.jsonl --metadata config/fixture_sglang_renamed.yaml --db "$DB" || return 1
  $CLI import-vllm --file data/fixtures/vllm_synthetic.json --metadata config/fixture_vllm.yaml --db "$DB" || return 1
  $CLI import-vllm --file data/fixtures/vllm_zero_success.json --metadata config/fixture_vllm_zero_success.yaml --db "$DB" || return 1
}

step_legacy_recompute() {
  export DB OUT_DIR
  $PY - <<'PY'
import os

from llm_capacity_pricing.services import (
    legacy_recompute_comparison,
    open_db,
    export_scenario_results,
    build_sensitivity,
)

db_path = os.environ["DB"]
out_dir = os.environ["OUT_DIR"]

r = legacy_recompute_comparison(db_path)
print("复算覆盖行数:", r["record_count"])
print("最大误差:")
for k, v in r["max_error"].items():
    print(f"  {k}: {v}")
print("超容差不匹配数:", r["mismatch_count"])

sens = build_sensitivity(db_path, persist=False)
print("默认敏感性情景条数:", len(sens), "(应为 32 x 5 = 160)")
sens_t = build_sensitivity(db_path, persist=False, include_test_data=True)
print("含测试数据情景条数:", len(sens_t), "(应为 36 x 5 = 180)")
export_scenario_results(db_path, os.path.join(out_dir, "scenarios_sensitivity.json"))

db, repo = open_db(db_path)
with db.session() as s:
    print("入库计数:", repo.counts(s).as_dict())
db.dispose()
PY
}

step_sensitive_scan() { # F1/F8：内部定价数据不得进入已跟踪文件
  # 特征串从**不入库**的本地文件读取：把真实数值写进脚本或测试，等于重新公开它们。
  local pattern_file="data/private/sensitive_patterns.txt"
  local patterns=()
  if [ -f "$pattern_file" ]; then
    while IFS= read -r line; do
      case "$line" in ''|'#'*) continue ;; esac
      patterns+=("$line")
    done <"$pattern_file"
  fi

  if [ "${#patterns[@]}" -gt 0 ]; then
    local offenders=""
    while IFS= read -r f; do
      [ -f "$f" ] || continue
      for pat in "${patterns[@]}"; do
        if grep -qF -- "$pat" "$f" 2>/dev/null; then
          offenders="${offenders}${f} (匹配内部特征)\n"
        fi
      done
    done < <(git ls-files 2>/dev/null || find . -path ./.venv -prune -o -type f -print)
    if [ -n "$offenders" ]; then
      printf '发现内部定价数据出现在已跟踪文件中（V1.2 F1）:\n'
      printf '%b' "$offenders"
      return 1
    fi
    printf '已跟踪文件中未发现内部定价数值（按本地特征表扫描 %s 个特征串）\n' "${#patterns[@]}"
  else
    printf '未找到本地特征表 %s（公开克隆的正常状态）——跳过数值扫描，仅校验忽略规则\n' "$pattern_file"
  fi

  # 内部产物必须被忽略；脱敏样本必须可提交
  local must_ignore=(data/exports/legacy_results.json data/private/row2_oracle.json)
  local must_track=(data/examples/public_sample_expected.json)
  local f
  for f in "${must_ignore[@]}"; do
    if ! git check-ignore -q "$f" 2>/dev/null; then
      printf '应被 .gitignore 排除却未被排除: %s\n' "$f"
      return 1
    fi
  done
  for f in "${must_track[@]}"; do
    if git check-ignore -q "$f" 2>/dev/null; then
      printf '应可公开提交却被忽略: %s\n' "$f"
      return 1
    fi
  done
  printf '忽略规则校验通过：data/exports 与 data/private 已排除，data/examples 可提交\n'
}

step_scope_recheck() {
  # 上面第 11 步刻意跑了"含测试数据"口径，库里因此留下 4 条 fixture 的计算结果。
  # 这一步回到默认业务口径，验证默认计算会清理遗留的测试态结果行，并打印口径分解，
  # 让日志里的中间计数与最终计数（业务口径）都能自解释。
  $CLI calculate-all --mode legacy_full_load --db "$DB" >/dev/null || return 1
  export DB
  $PY - <<'PY'
import os

from sqlalchemy import text

from llm_capacity_pricing.services import open_db

db, _repo = open_db(os.environ["DB"])
with db.session() as s:
    total = s.execute(text("select count(*) from pricing_result")).scalar_one()
    scopes = s.execute(
        text("select scope, count(*) from pricing_result group by scope order by scope")
    ).all()
    modes = s.execute(
        text("select mode, count(*) from pricing_result group by mode order by mode")
    ).all()
    test_left = s.execute(
        text(
            "select count(*) from pricing_result pr"
            " join benchmark_record br on br.record_id = pr.benchmark_record_id"
            " where br.performance_source_kind = 'synthetic_fixture'"
        )
    ).scalar_one()
print("最终 pricing_result 总数:", total, "(业务口径：32 满载 + 160 情景)")
print("按 scope 分解:", [(r[0], r[1]) for r in scopes])
print("按 mode  分解:", [(r[0], r[1]) for r in modes])
print("残留的 synthetic_fixture 结果行:", test_left, "(默认口径下应为 0)")
db.dispose()
PY
}

step_sla_default_policy() { # 生产默认策略必须拒绝 synthetic（断言）
  local out
  if ! out=$($CLI evaluate-sla --requirement '{"requirement_id":"req-default","ttft_p99_ms_max":400}' \
      --run-id fixture_perf_curve_c4 --model "SYNTHETIC-MODEL-C" \
      --model-revision synthetic-revision-1 --framework vllm \
      --framework-version synthetic-fw-1.0 --precision bf16 \
      --hardware "SYNTHETIC-GPU-C" --gpu-count 4 --tp 4 --dp 1 \
      --input-len 1024 --output-len 256 --dataset random \
      --arrival-mode unbounded --concurrency 4 --db "$DB" 2>&1); then
    printf '%s\n' "$out"
    return 1
  fi
  printf '%s\n' "$out"
  if ! printf '%s' "$out" | grep -q '^SLA 状态: not_evaluable'; then
    printf '断言失败：生产默认策略应给出 not_evaluable\n'
    return 1
  fi
}

step_sla_algorithm_validation() { # 仅算法验证：显式 allow-synthetic 才可 observed_pass（断言）
  local out
  if ! out=$($CLI evaluate-sla --requirement '{"requirement_id":"req-algo","ttft_p99_ms_max":400,"tpot_p99_ms_max":30}' \
      --run-id fixture_perf_curve_c4 --model "SYNTHETIC-MODEL-C" \
      --model-revision synthetic-revision-1 --framework vllm \
      --framework-version synthetic-fw-1.0 --precision bf16 \
      --hardware "SYNTHETIC-GPU-C" --gpu-count 4 --tp 4 --dp 1 \
      --input-len 1024 --output-len 256 --dataset random \
      --arrival-mode unbounded --concurrency 4 \
      --policy-id algorithm-validation-only --allow-synthetic --db "$DB" 2>&1); then
    printf '%s\n' "$out"
    return 1
  fi
  printf '%s\n' "$out"
  if ! printf '%s' "$out" | grep -q '^SLA 状态: observed_pass'; then
    printf '断言失败：算法验证路径应给出 observed_pass\n'
    return 1
  fi
  if ! printf '%s' "$out" | grep -q '仅用于算法验证'; then
    printf '断言失败：算法验证结论必须显式打标\n'
    return 1
  fi
}

step_tco_leased() { # 租赁口径：不得出现一次性投入与折旧（断言）
  local out
  if ! out=$($CLI tco --plan-file "$P4_PLAN" --template syn-leased-3y \
      --candidate demo-leased --hardware nvidia-h100-sxm-80gb \
      --gpu-count 8 --server-count 1 --db "$DB" 2>&1); then
    printf '%s\n' "$out"; return 1
  fi
  printf '%s\n' "$out"
  if ! printf '%s' "$out" | grep -q '"capex_one_time": null'; then
    printf '断言失败：租赁口径不得产生一次性投入\n'; return 1
  fi
  if ! printf '%s' "$out" | grep -q '"annual_depreciation": null'; then
    printf '断言失败：租赁口径不得计折旧\n'; return 1
  fi
  if ! printf '%s' "$out" | grep -q 'syn-gpu-purchase'; then
    printf '断言失败：买断专属成本项必须被显式列出为已排除（排除不等于零成本）\n'; return 1
  fi
}

step_tco_unknown() { # 缺价项：未知不等于零（断言）
  local out
  if ! out=$($CLI tco --plan-file "$P4_PLAN" --template syn-owned-incomplete \
      --candidate demo-incomplete --hardware nvidia-h100-sxm-80gb \
      --gpu-count 8 --server-count 1 --db "$DB" 2>&1); then
    printf '%s\n' "$out"; return 1
  fi
  printf '%s\n' "$out"
  if ! printf '%s' "$out" | grep -q '"incomplete": true'; then
    printf '断言失败：缺价项必须让成本结论标为不完整\n'; return 1
  fi
  if ! printf '%s' "$out" | grep -q 'syn-infiniband-fabric'; then
    printf '断言失败：缺价成本项必须被列出（不得按 0 通过）\n'; return 1
  fi
}

step_record_sla_evidence() { # 生成持久化 SLA 证据；synthetic 曲线不得成为 observed_pass（断言）
  local out
  if ! out=$($CLI record-sla-evidence --candidate demo-owned \
      --requirement '{"requirement_id":"req-phase4","ttft_p99_ms_max":400,"tpot_p99_ms_max":30}' \
      --model SYNTHETIC-MODEL-C --model-revision synthetic-revision-1 --precision bf16 \
      --hardware SYNTHETIC-GPU-C --gpu-count 8 --tp 8 --dp 1 \
      --framework vllm --framework-version synthetic-fw-1.0 \
      --concurrency 8 --input-len 1024 --output-len 256 --dataset random \
      --arrival-mode unbounded --db "$DB" 2>&1); then
    printf '%s\n' "$out"; return 1
  fi
  printf '%s\n' "$out"
  if printf '%s' "$out" | grep -q '"status": "observed_pass"'; then
    printf '断言失败：synthetic 曲线不得产出 observed_pass 的 SLA 证据\n'; return 1
  fi
  if ! printf '%s' "$out" | grep -q '"sla_evaluation_id"'; then
    printf '断言失败：必须产出可引用的证据 ID\n'; return 1
  fi
  # V4.2 E1：synthetic 证据绝不能是"生产级"
  if ! printf '%s' "$out" | grep -q '"production_grade": false'; then
    printf '断言失败：synthetic 证据必须标 production_grade=false\n'; return 1
  fi
}

step_quote_blocked_by_evidence() { # 引用真实证据仍必须阻断（断言）
  local out
  if ! out=$($CLI quote-eligibility --candidate demo-owned --requirement-id req-phase4 \
      --sla-evaluation-id "sla::demo-owned::req-phase4" --approval-id syn-approval-demo \
      --model SYNTHETIC-MODEL-C --model-revision synthetic-revision-1 --precision bf16 \
      --hardware SYNTHETIC-GPU-C --gpu-count 8 --tp 8 --dp 1 \
      --framework vllm --framework-version synthetic-fw-1.0 \
      --concurrency 8 --input-len 1024 --output-len 256 --dataset random \
      --arrival-mode unbounded --confirmation syn-confirmation-demo \
      --billing-rule syn-billing-qwen3-32b \
      --plan-file "$P4_PLAN" --template syn-owned-3y \
      --hardware-id nvidia-h100-sxm-80gb --server-count 1 --db "$DB" 2>&1); then
    printf '%s\n' "$out"; return 1
  fi
  printf '%s\n' "$out"
  if ! printf '%s' "$out" | grep -q '^正式报价不可发布'; then
    printf '断言失败：证据未通过核验时不得发布报价\n'; return 1
  fi
  if ! printf '%s' "$out" | grep -q '"quote_publishable": false'; then
    printf '断言失败：quote_publishable 必须为 false\n'; return 1
  fi
  if ! printf '%s' "$out" | grep -q 'sla_recheck'; then
    printf '断言失败：必须给出 SLA 重新核验的阻断原因\n'; return 1
  fi
  # 示例审批的计费规则指纹与成本快照都是占位值：两者任一失配都必须被点名
  if ! printf '%s' "$out" | grep -qE 'approval_(billing_rule|cost_snapshot)_stale'; then
    printf '断言失败：审批绑定的快照/计费规则不符必须被指出\n'; return 1
  fi
  # V4.3 P0-1：计费规则必须真的被解析并参与判定
  if ! printf '%s' "$out" | grep -q '"billing_rule_id": "syn-billing-qwen3-32b"'; then
    printf '断言失败：计费规则必须被解析并绑定到报价判定\n'; return 1
  fi
}

step_quote_rejects_claims() { # 不接受调用方状态字符串（断言）
  local out
  if out=$($CLI quote-eligibility --candidate demo-owned --requirement-id req-phase4 \
      --model qwen3-32b --hardware nvidia-h100-sxm-80gb --gpu-count 8 --tp 8 --dp 1 \
      --db "$DB" 2>&1); then
    printf '%s\n' "$out"
    printf '断言失败：缺少证据引用时不应返回成功\n'; return 1
  fi
  printf '%s\n' "$out"
  if ! printf '%s' "$out" | grep -qE 'sla-evaluation-id|必须提供'; then
    printf '断言失败：必须明确要求持久化的证据 ID\n'; return 1
  fi
}

step_plan_minimal() { # 仅有资源数据时必须出部分结果，而不是报错（断言）
  local out
  if ! out=$($CLI plan --config "$S1_MINIMAL" --out "$OUT_DIR/plan_minimal.md" \
      --db "$DB" 2>&1); then
    printf '%s\n' "$out"; return 1
  fi
  printf '%s\n' "$out"
  if ! printf '%s' "$out" | grep -q '整体状态: partial'; then
    printf '断言失败：缺成本/实测时应给出 partial，而不是整体失败\n'; return 1
  fi
  if ! printf '%s' "$out" | grep -q 'sizing=computed'; then
    printf '断言失败：资源规划领域必须算出结论\n'; return 1
  fi
  if ! printf '%s' "$out" | grep -q '可发布正式报价的候选: 无'; then
    printf '断言失败：未配置治理时不得出现可发布候选\n'; return 1
  fi
}

run_step 0 "环境" 1 '^(Python|deps ok)' step_env

run_step 1 "清理旧产物（全新数据库）" 1 '' step_cleanup

run_step 2 "init-db" 1 '^(已初始化数据库|表: )' \
  $CLI init-db --db "$DB"

run_step 3 "import-excel（第一次）" 1 '^(导入完成|工作表行数)|"idempotent"' \
  $CLI import-excel --file "$EXCEL" --db "$DB"

run_step 4 "import-excel（第二次，验幂等）" 1 '^(导入完成|工作表行数)|"idempotent"' \
  $CLI import-excel --file "$EXCEL" --db "$DB"

run_step 5 "import-excel（第三次，再验幂等）" 1 '^(导入完成|工作表行数)|"idempotent"' \
  $CLI import-excel --file "$EXCEL" --db "$DB"

run_step 6 "import-sglang / import-vllm（synthetic fixture，测试数据）" 1 '^已导入 [0-9]+ 条' \
  step_import_fixtures

run_step 7 "import-official（官方页面候选 partial_evidence）" 1 '^(已导入 [0-9]+ 条官方候选|跳过 [0-9]+ 条)' \
  $CLI import-official --manifest "$MANIFEST" --db "$DB" --root "$ROOT"

run_step 8 "import-sglang-candidates（M7 官方数值型候选，含原文逐字段核对）" 1 '^(已导入 [0-9]+ 条官方数值候选|原文逐字段核对通过)' \
  $CLI import-sglang-candidates --file "$INCOMING" --db "$DB" --root "$ROOT"

run_step 9 "import-sglang-candidates（重复导入，验幂等）" 1 '^(已导入 [0-9]+ 条官方数值候选|原文逐字段核对通过)' \
  $CLI import-sglang-candidates --file "$INCOMING" --db "$DB" --root "$ROOT"

run_step 10 "official-coverage（两家族候选 + M9 门禁）" 1 '^  "(candidate_count|numeric_candidates|verified_count|can_evaluate_sla|can_calculate_pricing|numeric_verified_against_source)"' \
  $CLI official-coverage --db "$DB"

run_step 11 "validate-import" 1 '^(校验项:|全部校验通过)|\[FAIL\]' \
  $CLI validate-import --db "$DB"

run_step 12 "calculate-all --mode legacy_full_load（默认业务口径）" 1 '^(已导出|结果条数|注意:)' \
  $CLI calculate-all --mode legacy_full_load --db "$DB" --out "$OUT_DIR/legacy_results.json"

run_step 13 "calculate-all --mode legacy_full_load --include-test-data（测试口径）" 1 '^(已导出|结果条数|注意:)' \
  $CLI calculate-all --mode legacy_full_load --db "$DB" \
  --out "$OUT_DIR/legacy_results_with_tests.json" --include-test-data

run_step 14 "scenario（单条，U=0.30）" 1 '^(情景结果已写入)|scenario_revenue_10k_per_gpu_year' \
  $CLI scenario --record excel_部署效能_2 --utilization 0.30 --db "$DB" --out "$OUT_DIR/scenario.json"

run_step 15 "validate-results（结果可追溯 + SLA 口径）" 1 '^(结果条数:|校验项:|全部校验通过)|\[FAIL\]' \
  $CLI validate-results --db "$DB"

run_step 16 "旧表复算误差摘要 + 敏感性导出" 1 '^(复算覆盖行数|最大误差|超容差不匹配数|默认敏感性情景条数|含测试数据情景条数|入库计数)' \
  step_legacy_recompute

run_step 17 "回到业务口径并复核库内计数（清理测试态结果行）" 1 '^(最终 pricing_result 总数|按 scope 分解|按 mode  分解|残留的 synthetic_fixture)' \
  step_scope_recheck

run_step 18 "report（默认业务口径，含未确认项与官方候选概览）" 1 '^(报告已写入)|"(bytes|records|scenario_results|legacy_results)"' \
  $CLI report --db "$DB" --out "$OUT_DIR/overview.md"

# ---------------------------------------------------------------------------
# 第二阶段（M12-M18）：模型 / 硬件 / 资源规划
# ---------------------------------------------------------------------------

run_step 19 "import-models（M13 模型结构与权重元数据）" 1 '^(已导入 [0-9]+ 个模型结构|解析成功:)' \
  $CLI import-models --manifest "$MODEL_MANIFEST" --db "$DB" --root "$ROOT"

run_step 20 "import-hardware（M14 GPU SKU 与服务器形态）" 1 '^(已导入 [0-9]+ 个 GPU SKU|本轮支持:)' \
  $CLI import-hardware --catalog "$HW_CATALOG" --db "$DB"

run_step 21 "sizing-inventory（模型结构 + 硬件规格库存）" 1 '^  "(model_count|hardware_count|server_count)"' \
  $CLI sizing-inventory --db "$DB"

run_step 22 "plan-deployment（M17 TP/DP 候选 + M18 JSON/Markdown 报告）" 1 '^(规划结果已写入|报告已写入|状态分布:)' \
  $CLI plan-deployment \
  --model qwen3-32b --hardware nvidia-h100-sxm-80gb --server hgx-h100-8gpu \
  --context-length 8192 --concurrency 32 --block-size 16 \
  --usable-fraction 0.9 \
  --usable-source "演示用构造值：标称 80GB 的 90%（非实测，落地前须用 nvidia-smi 复核）" \
  --usable-source-kind synthetic_example \
  --runtime-reserve-fraction 0.1 --runtime-source "演示用构造值：运行时合并预留 10%" \
  --tp-sizes 1,2,4,8 --dp-sizes 1,2 \
  --out "$OUT_DIR/deployment_plan.json" --report "$OUT_DIR/sizing_report.md" --db "$DB"

run_step 23 "estimate-memory（M15+M16 单配置显存构成）" 1 '^  "(per_gpu_usable_bytes|total_required_bytes|headroom_bytes|runtime_known_bytes)"' \
  $CLI estimate-memory \
  --model qwen3-8b --hardware nvidia-h200-sxm-141gb --tp 2 --dp 1 \
  --context-length 16384 --concurrency 16 --block-size 16 \
  --usable-fraction 0.9 \
  --usable-source "演示用构造值：标称 141GB 的 90%（非实测）" \
  --usable-source-kind synthetic_example \
  --runtime-reserve-fraction 0.1 --runtime-source "演示用构造值" --db "$DB"

# ---------------------------------------------------------------------------
# 第三阶段（M19-M25）：性能评估、SLA 与部署方案比较
# ---------------------------------------------------------------------------

run_step 24 "import-performance（M20 性能记录，合成曲线用于算法验证）" 1 '^(已导入 [0-9]+ 条性能记录|可信等级:)' \
  $CLI import-performance --curve-index "$PERF_CURVE_INDEX" --kind vllm --root "$ROOT" --db "$DB"

run_step 25 "capacity-profile（M21 容量曲线，排除 synthetic）" 1 '^记录 [0-9]+ 条，分成|分成 0 个配置组' \
  $CLI capacity-profile --exclude-synthetic --db "$DB"

run_step 26 "capacity-profile（含 synthetic，算法验证）" 1 '^记录 [0-9]+ 条，分成|峰值并发' \
  $CLI capacity-profile --target-concurrency 8 --db "$DB"

run_step 27 "calibrate-performance（M22 范围内插值 + 留出误差；--allow-synthetic 仅算法验证）" 1 '^  "(estimate_kind|value|holdout_mae|covered_range)"' \
  $CLI calibrate-performance --metric output_throughput_tok_s --concurrency 6 --allow-synthetic --db "$DB"

run_step 28 "match-benchmark（M19：Qwen3-32B/H100 不得命中 235B/B200 数据）" 1 '^匹配等级分布:' \
  $CLI match-benchmark --model "Qwen/Qwen3-32B" --hardware "NVIDIA H100 SXM" --gpu-count 4 --tp 4 \
  --input-len 8192 --output-len 256 --db "$DB"

run_step 29 "evaluate-sla（M23 断言：默认策略必须拒绝 synthetic → not_evaluable）" 1 '^SLA 状态:' \
  step_sla_default_policy

run_step 30 "evaluate-sla（M23 断言：仅算法验证路径 → observed_pass 且打标）" 1 '^SLA 状态:|仅用于算法验证' \
  step_sla_algorithm_validation

run_step 31 "rank-deployments（M24：静态可行但无实测 -> requires_benchmark）" 1 '^(分桶:|阻塞:)' \
  $CLI rank-deployments --model qwen3-32b --hardware nvidia-h100-sxm-80gb --server hgx-h100-8gpu \
  --context-length 8192 --concurrency 32 --tp-sizes 2,4,8 --dp-sizes 1 \
  --usable-fraction 0.9 --usable-source-kind synthetic_example \
  --runtime-reserve-fraction 0.1 \
  --requirement '{"requirement_id":"req-rank","ttft_p99_ms_max":400,"tpot_p99_ms_max":30}' --db "$DB"

run_step 32 "performance-report（M25 第三阶段报告）" 1 '^报告已写入' \
  $CLI performance-report --out "$OUT_DIR/performance_report.md" --target-concurrency 8 --db "$DB"

# ---------------------------------------------------------------------------
# 第四阶段（M26-M33）：TCO、业务收益、报价门禁与部署决策
# ---------------------------------------------------------------------------

run_step 33 "import-cost-catalog（M27 成本目录 + 计费规则；合成示例）" 1 '^(已导入 [0-9]+ 个成本项|注意：)' \
  $CLI import-cost-catalog --file "$P4_CATALOG" --db "$DB"

run_step 34 "cost-catalog（M27 库存：类别 / 口径 / 状态 / 缺价项）" 1 '^  "(item_count|billing_rule_count|items_missing_price|contains_synthetic)"' \
  $CLI cost-catalog --db "$DB"

run_step 35 "tco（M28 买断：一次性 / 年现金 / 折旧分开，含成本分解）" 1 '^TCO：一次性|^  "(capex_one_time|annual_cash_opex|annual_depreciation|total_cash_over_horizon|incomplete)"' \
  $CLI tco --plan-file "$P4_PLAN" --template syn-owned-3y \
    --candidate demo-owned --hardware nvidia-h100-sxm-80gb \
    --gpu-count 8 --server-count 1 --db "$DB"

run_step 36 "tco（M28 断言：租赁无一次性投入 / 无折旧，买断项被排除）" 1 '^TCO：一次性' \
  step_tco_leased

run_step 37 "tco（M28 断言：缺价项 -> incomplete，未知不等于零）" 1 '^成本不完整' \
  step_tco_unknown

run_step 38 "revenue-scenario（M29 业务请求量收益；无实测 -> 容量未验证）" 1 '^年度收入|^  "(annual_revenue|currency|capacity_check|scenario_only|period_revenue_total)"' \
  $CLI revenue-scenario --demand-file "$P4_DEMAND" --scenario syn-demand-base --db "$DB"

run_step 39 "record-sla-evidence（F4.1 断言：synthetic 曲线不得成为 observed_pass）" 1 '^(已生成 SLA 证据|该证据不是)' \
  step_record_sla_evidence

run_step 40 "import-commercial-confirmations（人工确认记录，示例占位）" 1 '^已导入 [0-9]+ 条商务确认记录' \
  $CLI import-commercial-confirmations --file "$P4_CONFIRMATION" --db "$DB"

run_step 41 "import-quote-approvals（F4.1 人工审批记录，示例占位）" 1 '^已导入 [0-9]+ 条审批记录' \
  $CLI import-quote-approvals --file "$P4_APPROVALS" --db "$DB"

run_step 42 "quote-eligibility（F4.1 断言：引用真实证据仍必须阻断，且点名原因）" 1 '^正式报价不可发布' \
  step_quote_blocked_by_evidence

run_step 43 "quote-eligibility（F4.1 断言：不接受调用方状态字符串，必须要求证据 ID）" 1 '必须提供|sla-evaluation-id' \
  step_quote_rejects_claims

run_step 44 "反向场景断言（F4.1：伪造声明 / 未核验证据 / 失配快照全部阻断）" 1 '^反向场景检查通过' \
  $PY scripts/check_forged_quote_blocked.py

run_step 45 "phase4-decision（M31/M32 资源-性能-TCO-收益-门禁全链路）" 1 '^(候选分桶:|可发布正式报价的候选:)|^  "per_candidate"' \
  $CLI phase4-decision --model qwen3-32b --hardware nvidia-h100-sxm-80gb --server hgx-h100-8gpu \
    --plan-file "$P4_PLAN" --template syn-owned-3y \
    --demand-file "$P4_DEMAND" --scenario syn-demand-base \
    --context-length 8192 --concurrency 32 --tp-sizes 2,4,8 --dp-sizes 1 \
    --usable-fraction 0.9 --usable-source-kind synthetic_example \
    --runtime-reserve-fraction 0.1 \
    --requirement '{"requirement_id":"req-phase4","ttft_p99_ms_max":400,"tpot_p99_ms_max":30}' \
    --out "$OUT_DIR/phase4_decision.json" --db "$DB"

run_step 46 "phase4-report（M33 报告：四个独立结论 + 报价草稿阻断说明）" 1 '^报告已写入' \
  $CLI phase4-report --out "$OUT_DIR/phase4_report.md" \
    --decision-json "$OUT_DIR/phase4_decision.json" --db "$DB"

run_step 47 "plan（S1 断言：仅有资源数据 -> partial 且资源领域已计算）" 1 '^整体状态:|^候选数:' \
  step_plan_minimal

run_step 48 "plan（S1 完整配置：四领域贯通，报价仍不可发布）" 1 '^整体状态:|^可发布正式报价的候选:' \
  $CLI plan --config "$S1_FULL" --out "$OUT_DIR/plan_full.md" \
    --json-out "$OUT_DIR/plan_full.json" --db "$DB"

run_step 49 "S6 四场景端到端验收（A 仅资源 / B 有实测无价格 / C 完整合成 / D 输入错误）" 1 '^[0-9]+ passed' \
  $PY -m pytest tests/test_s_phase_end_to_end.py -q

run_step 50 "敏感数据扫描（V1.2 F1/F8：内部定价不得进入已跟踪文件）" 1 '^(已跟踪文件中未发现内部定价数值|未找到本地特征表|忽略规则校验通过|发现内部定价数据|应被 )' \
  step_sensitive_scan

run_step 51 "pytest（全量，离线）" 1 '^[0-9]+ (passed|failed|error)|^(FAILED|ERROR)' \
  $PY -m pytest -q

run_step 52 "产物清单（内部目录不入库，仅本地查看）" 1 '\.(json|md)$' \
  ls -la "$OUT_DIR"

finish
