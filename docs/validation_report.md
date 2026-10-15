# 验证报告（validation report）—— 第一阶段 MVP 验收整改

- 生成日期：2026-10-15
- 引擎版本：`pricing_engine_version = 0.1.0`
- 运行环境：Linux x86_64，Python 3.12.3，pydantic 2.13.4，sqlalchemy 2.0.54，
  **无 GPU、离线**（测试与计算均不联网）
- 完整执行日志：`docs/test_run_log.txt`（由 `bash scripts/verify_all.sh` 生成，19 步全绿）
- 输入资产：`data/raw/Token产能计算器-0910.xlsx`，sha256
  `ed25cf5493b469ae8a23d150612c1c7f7fcdaaac022e5825fac6e3d796b3af3b`
- 本文件对应验收整改任务 1～5；**所有数字均来自实际执行输出**，不是预期值。

---

## 0. 验收结论（四分类）

### 0.1 已通过（原有能力，整改后复验仍通过）

| # | 验收项 | 判定依据（实际输出） |
|---|---|---|
| 1 | Excel 三表导入计数与幂等 | `工作表行数: {'GPU价格': 2, '模型价格': 3, '部署效能': 32}`；第 2、3 次导入 `"idempotent": true` |
| 2 | 旧表 32 行全量复算 | `复算覆盖行数: 32`，`超容差不匹配数: 0`；最大误差 I `4.4E-13`、J `0`、K `4.4E-13`、L `0`、M `5.714285714286E-15` |
| 3 | 精确复算样例 | 输入/输出/合计三项与期望值**精确一致**（误差 0），比值误差量级 `1e-15`。期望值：公开部分用 `data/examples/` 的脱敏样本，真实工作簿部分用本地 `data/private/`（不入库） |
| 4 | 变现比例情景 | `scenario --record excel_部署效能_2 --utilization 0.30` → 返回十进制字符串形式的 `scenario_revenue_10k_per_gpu_year`（金额已按 V1.2 F1 脱敏，见 `docs/security_remediation_report.md`） |
| 5 | 缺 P99 → SLA 不可评估 | 32 条业务结果 `sla_status` 全为 `not_evaluable` 且 `sla_not_evaluable_reasons` 写明缺失项（检查 `sla_not_evaluable_without_p99`、`sla_reasons_recorded` 通过） |
| 6 | 每条 Benchmark 有来源与质量标记 | `check("every_record_has_source")`、`check("every_record_has_quality_flags")` 通过（36 条记录） |
| 7 | 每条结果可追溯到三个输入 | `result_linked_to_performance_record` / `result_linked_to_hardware_price` / `result_linked_to_token_price` / `result_has_evidence_chain` 四项通过 |
| 8 | 卡片级吞吐只在同配置内归一化 | 32 条 Excel 记录的 `input_tps_cluster` 保持 `None`，无跨 GPU/TP 外推 |
| 9 | 除零/不可计算语义 | 具名异常 + `not_evaluable`，无 `inf`、无静默 0（边界用例齐备） |
| 10 | 报告无越界措辞 | `verify_all.sh` 与 `test_report_contains_required_disclaimers` 等测试通过：输出不含 `guaranteed_sla`、`official_quote`、`total_cost_of_ownership` |
| 11 | 离线可复现 | `bash scripts/verify_all.sh` → `步骤 19 项：通过 19，失败 0`，`EXIT=0` |
| 12 | 自动化测试 | `200 passed`（整改前 110） |

### 0.2 已修复（本次整改修改的行为）

| # | 整改项 | 整改前 | 整改后（实测证据） |
|---|---|---|---|
| 1 | 默认计算混入 fixture | `calculate-all` 默认返回 36 条，情景 180 条 | 默认 **32** 条 / **160** 条：`已导出 32 条结果`、`结果条数: 32（业务 32 + 测试 0）`、`默认敏感性情景条数: 160 (应为 32 x 5 = 160)` |
| 2 | 测试数据无显式开关 | 无法区分业务与测试口径 | `--include-test-data` 才得 36 / 180：`结果条数: 36（业务 32 + 测试 4）`、`含测试数据情景条数: 180`，且结果标 `scope="test"` |
| 3 | 库内残留测试态结果 | 默认口径跑完后，库里仍留着 fixture 的 4 条结果 | 默认口径落库时主动清理：`残留的 synthetic_fixture 结果行: 0`，`最终 pricing_result 总数: 192`，`按 scope 分解: [('business', 192)]` |
| 4 | 金额 JSON 精度 | `Decimal` 以裸 number 导出，读取器按 double 解析会丢精度 | 金额/比率字段改为**可精确恢复的十进制字符串** + `loads_tolerant` 兼容读取；`test_high_precision_value_survives_string_encoding` 等 22 个用例固定 |
| 5 | 币种/结果性质语义模糊 | `currency = None`，结果未声明是理论上限还是情景 | `currency = "UNCONFIRMED"`、`result_type ∈ {theoretical_full_load_potential, scenario_sensitivity_estimate}`、`cost_scope`、`price_unit`、`billing_period` 全量声明 |
| 6 | 历史外部数据可能被读成内部实测 | 只有 `source_type`（载体）可判断 | 新增 `measurement_origin` + `is_internal_measurement` + `ALLOWED_ORIGINS` 强校验 + 4 项导入检查 |
| 7 | 结果缺少输入侧证据链 | `PricingResult` 无输入 ID 与 SLA 字段 | 新增 `performance_record_id` / `hardware_price_id` / `token_price_id` / `evidence_ids` / `linkage_warnings` / `sla_status` / `sla_not_evaluable_reasons`，并以 `validate-results`（9 项）固定 |
| 8 | 报告免责声明位置 | 收入表的免责声明排在 160 行表格**之后**，读者可能先看到数字 | 免责声明移到表格**之前**（`test_revenue_section_has_adjacent_disclaimer` 固定） |
| 9 | 官方"部分证据"无落地形式 | 只能留在笔记/文档里 | 新增候选表 `official_benchmark_candidate` + `import-official` + `official-coverage`，缺口逐字段入账 |
| 10 | 存档证据存在"无主文件" | `data/raw/official/` 下有 1 个文件没有 manifest 条目 | 补 `vllm_recipes_index` 条目，并新增反向测试 `test_every_archived_evidence_file_has_a_manifest_entry` |

### 0.3 未确认（缺业务/来源确认，代码按要求保留空值或保守标记）

| # | 项 | 当前处理 |
|---|---|---|
| 1 | Token 价格币种 | 一律 `currency = "UNCONFIRMED"` + `currency_assumption = "CNY; pending business confirmation"` |
| 2 | `GPU价格!C` 成本覆盖范围 | 只命名 `gpu_cost_only`，不得称 TCO |
| 3 | H 列"总缓存命中率"语义 | `cache_metric_semantics = "unknown"`，仅作旧表定价代理假设并打标 |
| 4 | 部署策略字符串（TP/EP/PD） | `deployment_strategy_parsed` 恒带 `verified: false`，不写入已核实拓扑字段 |
| 5 | 官方候选页面中**未机器解析**的数值 | 只登记字段存在性与缺口，不做人工转写（避免不可复核的二次录入） |
| 6 | `vllm_recipes_index` 的来源 URL | 重新校验时 HTTP 429，`source_url` 记 `null`，仅本地存档可追溯 |
| 7 | 年度秒数口径 | 固定 365 天（31536000 s）；是否扣除维护窗口需业务确认，不得默认调整 |

### 0.4 未实现（如实标注，未宣称完成）

| # | 项 | 说明 |
|---|---|---|
| 1 | `official_benchmark` 完整实测记录 | **0 条**。6 条候选均缺硬件型号与卡数等关键配置，按"不允许补造缺失配置"停在 `partial_evidence` |
| 2 | SLA 评估（P99 TTFT/TPOT） | 无实测 P99 数据，恒 `not_evaluable`（正确行为，非缺陷） |
| 3 | 完整 TCO | 仅 `gpu_cost_only` |
| 4 | 可用率 `A` / 可计费比例 `S` | 字段就绪但留空，需业务确认后启用，且不得与 `U` 重复相乘 |
| 5 | 自动选卡满足 SLA、异构 P/D 自动搜索、显存精算 | 任务书明确列入范围外 |
| 6 | 前端 / 合同报价审批 / 税务 | 任务书明确列入范围外 |
| 7 | 官方数据自动采集 | 按策略只允许人工触发，不绕过访问限制 |
| 8 | 第二阶段新功能 | 本次验收整改按指示**未开发** |

---

## 1. 执行命令与实测结果（整改任务 5）

```bash
bash scripts/verify_all.sh            # 19 步；输出同时写入 docs/test_run_log.txt
```

| 步 | 命令 | 实测结果 |
|---|---|---|
| 0 | 环境自检 | `Python 3.12.3`；`deps ok: pydantic 2.13.4 | sqlalchemy 2.0.54` |
| 1 | 清理旧产物 | 删除 `data/local.db` 与 `data/exports/*`（**全新数据库**） |
| 2 | `init-db` | 建 7 张表：`benchmark_record, evidence, hardware_price, import_run, official_benchmark_candidate, pricing_result, token_price` |
| 3 | `import-excel`（1） | 2 / 3 / 32 行，`"idempotent": false` |
| 4 | `import-excel`（2） | `"idempotent": true`（计数不变） |
| 5 | `import-excel`（3） | `"idempotent": true` |
| 6 | 导入 4 条 fixture | `已导入 1 条 SGLang 记录` ×2、`已导入 1 条 vLLM 记录` ×2 |
| 7 | `import-official` | `已导入 6 条官方候选证据（partial_evidence）`、`跳过 8 条未入库条目` |
| 8 | `official-coverage` | `candidate_count 6`、`verified_count 0`、`with_parsed_metrics 1`、`throughput_benchmarks 0`、`single_request_smoke_tests 1` |
| 9 | `validate-import` | `校验项: 20，通过 20，失败 0` |
| 10 | `calculate-all`（默认） | `已导出 32 条结果`、`结果条数: 32（业务 32 + 测试 0）` |
| 11 | `calculate-all --include-test-data` | `已导出 36 条结果`、`注意: 其中含 4 条人工测试数据（测试口径）` |
| 12 | `scenario`（单条，U=0.30） | 金额字段以十进制字符串返回（数值已脱敏） |
| 13 | `validate-results` | `结果条数: 32（含测试数据: False）`、`校验项: 9，通过 9，失败 0` |
| 14 | 复算误差摘要 + 敏感性 | 见 §2；`默认敏感性情景条数: 160`、`含测试数据情景条数: 180` |
| 15 | 回到业务口径复核 | `最终 pricing_result 总数: 192`、`按 scope 分解: [('business', 192)]`、`残留的 synthetic_fixture 结果行: 0` |
| 16 | `report`（默认） | `records 36 / business_records 32 / test_records 4 / scenario_results 160`，63717 字节 |
| 17 | `pytest` | **`200 passed`** |
| 18 | 产物清单 | 5 个文件（见 §7） |

第 14 步的中间态计数需要说明：该步之前刚跑过"含测试数据"口径，因此
`入库计数: {'evidence': 47, 'hardware_price': 2, 'token_price': 3, 'benchmark_record': 36, 'pricing_result': 196}`
（196 = 36 满载 + 160 情景）。第 15 步回到默认业务口径后清理为 192，
**日志里两种口径都可见，不会被误读**。

### 测试分布（共 200 个用例）

| 测试文件 | 用例数 | 覆盖 |
|---|---|---|
| `tests/test_excel_import.py` | 22 | M1 导入 / 幂等 / 行号可追溯 |
| `tests/test_pricing_equivalence.py` | 11 | M2 旧表复算等价性 |
| `tests/test_business_rules.py` | 27 | M3 情景边界 / 措辞边界 |
| `tests/test_benchmark_adapters.py` | 22 | M4 两套适配器与字段改名 |
| `tests/test_official_manifest.py` | 9 | M5 manifest 与证据哈希（含反向覆盖检查） |
| `tests/test_official_partial_evidence.py` | 23 | 官方候选与缺口台账（整改任务 4） |
| `tests/test_missing_data.py` | 21 | 缺失值语义 |
| `tests/test_test_data_isolation.py` | 19 | **整改任务 1** |
| `tests/test_json_precision.py` | 22 | **整改任务 2** |
| `tests/test_evidence_linkage.py` | 24 | **整改任务 3** |
| **合计** | **200** | 全部通过 |

---

## 2. 旧表 32 行复算情况与最大误差

对 `部署效能` 第 2～33 行（共 32 条）逐行复算，与旧表 I/J/K/L/M 五列逐一比较。

| 列 | 含义 | 单位 | 最大绝对误差 | 容差 | 结论 |
|---|---|---|---|---|---|
| I | 输入营收 | 万元/卡/年 | `4.4E-13` | `1e-6` | 通过 |
| J | 输出营收 | 万元/卡/年 | `0` | `1e-6` | 通过（精确相等） |
| K | 营收合计 | 万元/卡/年 | `4.4E-13` | `1e-6` | 通过 |
| L | 成本（与 `GPU价格` 表核对） | 万元/卡/年 | `0` | `1e-6` | 通过（精确相等） |
| M | 营收/成本比 | 无量纲 | `5.714285714286E-15` | `1e-8` | 通过 |

**超容差不匹配数：0。**

误差来源说明（重要）：旧表的 I/J/K/M 是 Excel 用 IEEE-754 双精度浮点算出并存储的；
本引擎全程使用 `Decimal`，算的是精确十进制结果。表中误差量级 `1e-13 ~ 1e-15` 全部来自
**Excel 侧的浮点舍入**，不是本引擎的计算偏差。这也是容差取 `1e-6` / `1e-8` 而非 0 的原因
（任务书 §8 M2 允许调整比较精度但须说明原因）。

第 2 行（任务书 §4.1 样例）逐字段核对：

| 量 | 期望值 | 本引擎结果 | 一致 |
|---|---|---|---|
| 输入收入 | 内部期望值 | 与期望值**精确相等** | ✅ 精确 |
| 输出收入 | 内部期望值 | 与期望值**精确相等** | ✅ 精确 |
| 合计 | 内部期望值 | 与期望值**精确相等** | ✅ 精确 |
| 营收/成本比 | 内部期望值（14 位有效数字） | 精确十进制值 | ✅ 误差量级 `1e-15`（Excel 侧浮点舍入） |

### 复算出的业务事实（仅供审阅，不作为营收承诺）

- 32 条真实记录中，**13 条**在"表内 GPU 成本、100% 满载变现"口径下营收/成本比 > 1。
- 每卡理论满载收入最高：`excel_部署效能_22`（B300 × 64，DeepSeek-V4-Pro（峰值））=
  具体金额、营收/成本比与盈亏平衡比例属**内部业务信息**，按 V1.2 F1 不写入公开仓库；
  本地复算可完整复现（`data/private/`）。
- 存在**盈亏平衡变现比例 > 100%** 的配置，意味着即使满载也无法覆盖表内 GPU 成本；
  具体跨度属内部业务信息，不在此列出。
- 这些数字**不是**实际营收、不是客户可收取金额、不含机房/网络/人力/存储/运维成本，
  结果对象里以 `result_type = "theoretical_full_load_potential"` 显式声明。

---

## 3. 整改任务 1 专项：测试数据隔离

**问题**：`calculate-all` / `scenario` / `report` 会把 4 条 `synthetic_fixture`
（人工构造的解析器测试样例）与 32 条真实效能数据混在一起算，默认敏感性因此是 180 而非 160。

**整改**：

1. 引入 `scope`（`business` / `test`）与 `is_test_record()`，在 `split_by_scope()` 里分流；
2. `calculate-all` / `scenario` / `report` / `validate-results` 默认排除测试记录，
   并在 meta 中回报 `business_record_count` / `test_record_count` / `excluded_test_records`；
3. 只有显式 `--include-test-data` 才纳入，结果标 `scope="test"`；
4. 以默认口径落库时，主动删除库中遗留的测试态结果行（清理而不是留脏数据）；
5. `report` 的**与口径无关的统计**（证据数、导入计数、来源分级计数）不受开关影响——
   这一点由 `test_evidence_and_import_counts_unaffected_by_scope` 固定，
   它同时也是本轮暴露的一个真 bug 的回归测试（见 §3.2）。

**实测对比**：

| 口径 | 满载结果 | 敏感性条数 |
|---|---|---|
| 默认（业务） | 32 | **160**（32 × 5） |
| `--include-test-data` | 36（业务 32 + 测试 4） | **180**（36 × 5） |

### 3.1 受影响的报告统计（逐项确认）

| 统计项 | 是否随口径变化 | 说明 |
|---|---|---|
| `records` / `legacy_results` / `scenario_results` | 是 | 默认 32 / 160；含测试 36 / 180 |
| 「最大满载收入案例」「成本敏感性」 | 是 | 默认只在 32 条业务记录里挑 |
| `business_records` / `test_records` | **否** | 按记录来源恒定（32 / 4），已修为口径无关 |
| `evidence` / `hardware_price` / `token_price` / `benchmark_record` 计数 | **否** | 导入计数与计算口径无关（47 / 2 / 3 / 36） |
| `source_kinds` 来源分级表 | **否** | 恒定显示 32 真实 + 4 测试（测试注明"已排除"） |

### 3.2 本轮暴露并修掉的两个真问题

1. `export_report` 的 `business_records` 原本由 `excluded_test_records` 推导，
   导致它在**默认口径下算出 32、在测试口径下算出 36**——一个"看起来对但会随开关漂移"
   的统计量。改为直接按记录来源统计。
2. CLI 的"注意：含测试数据"提示在导出路径上不触发，因为 `export_legacy_results`
   没有把 `test_record_count` 回传。已补字段并加断言测试。

---

## 4. 整改任务 2 专项：金额精度

**问题**：`Decimal` 以裸 JSON number 导出（例：内部金额常带 14～28 位有效数字），任何按 IEEE-754 double
解析的读取器都拿不回精确值，而比率字段有 28 位有效数字。

**整改**：金额/比率字段一律写成**定点十进制字符串**，并给出兼容读取入口。

实测片段（`data/exports/scenario.json`）：

```json
"scenario_revenue_10k_per_gpu_year": "<decimal string>",
```

导出文件头的编码声明：

```json
"decimals_encoding": {
  "money_and_ratio_fields": "decimal_string",
  "format": "fixed-point decimal literal, no scientific notation",
  "example": "17.3448",
  "compatible_reader": "llm_capacity_pricing.reporting.json_export.loads_tolerant",
  "compute_path": "all formulas use decimal.Decimal; JSON is an encoding boundary only"
}
```

| 验收点 | 实现 / 证据 |
|---|---|
| 可精确恢复 | `loads_tolerant` 按 `DECIMAL_FIELD_NAMES` 白名单还原为 `Decimal`；`test_high_precision_value_survives_string_encoding` 逐位相等 |
| 兼容旧格式 | `test_tolerant_reader_accepts_legacy_json_number_without_float_loss`（用 `parse_float=Decimal` 读旧文件） |
| 不用科学计数法 | `test_no_scientific_notation_for_small_and_large_values` |
| 不误伤普通字符串 | `test_version_like_strings_are_not_mistaken_for_money`（`"1.0"` 版本号保持字符串） |
| 公式仍全用 Decimal | `_reject_float` 守卫：公式入口收到 `float` 直接抛 `TypeError`；`test_formulas_stay_on_decimal_path`、`test_binary_float_error_never_enters_money` |
| 币种/成本范围/结果类型显式 | `export_envelope_meta()` 写入 `currency="UNCONFIRMED"`、`cost_scope="gpu_cost_only"`、`price_unit`、`billing_period`；`test_envelope_meta_states_currency_cost_scope_and_encoding` |
| 导出文件可回读成模型 | `test_exported_file_roundtrips_into_models`（导出 → 读回 → 与库内 `Decimal` 全等） |

---

## 5. 整改任务 3 专项：证据链、来源归属与 SLA

### 5.1 每条 Benchmark 都有来源与质量标记

`validate-import` 从 15 项增至 **20 项**，新增的 5 项针对本任务：

| 检查 | 实测 |
|---|---|
| `every_record_has_source` | PASS：36 条记录都能落到 `publisher` / `source_url` / `source_file` |
| `every_record_has_quality_flags` | PASS：无一条记录缺质量标记 |
| `excel_marked_as_historical_external` | PASS：32 条 Excel 记录均为 `measurement_origin = historical_external_third_party`，`is_internal_measurement = False` |
| `historical_external_flag_present` | PASS：每条都带 `historical_external_source_not_internal_measurement` |
| `no_internal_measurement_declared` | PASS：全库无任何 `internal_measurement` 声明 |

同时 `source_kinds_separated` 确认 32 条 Excel 不与 4 条 fixture 混合计数。

### 5.2 每条 PricingResult 可追溯到三个输入

新增 `performance_record_id` / `hardware_price_id` / `token_price_id` / `evidence_ids`，
`validate-results` 以 9 项检查固定（实测 `9，通过 9，失败 0`）：

```
[PASS] result_linked_to_performance_record: 32 条结果均可回溯到性能记录
[PASS] result_linked_to_hardware_price: 有成本的结果均能回溯到同型号硬件价格
[PASS] result_linked_to_token_price: 有收入的结果均能回溯到同模型 Token 单价
[PASS] result_has_evidence_chain: 每条结果都带证据链 ID
[PASS] result_scope_matches_source_kind: 结果 scope 与记录来源一致
[PASS] sla_not_evaluable_without_p99: 缺 P99 时 SLA 恒为 not_evaluable
[PASS] sla_reasons_recorded: not_evaluable 的结果都写明了原因
[PASS] unlinked_inputs_warned: 缺价格关联时给出 linkage_warnings（不静默）
[PASS] currency_declared: 每条结果都显式声明币种与成本范围
```

反向测试：`test_missing_price_linkage_is_explicit_not_guessed`（缺价格时必须留空 + 告警，
不得猜）、`test_validate_results_catches_sla_mislabel`（人为把 SLA 标错，校验必须报错）。

### 5.3 原始 Excel 统一标为历史外部来源

- 载体：`source_type = "internal_excel"`（说明它是从内部 Excel 进来的）；
- 归属：`measurement_origin = "historical_external_third_party"`、
  `is_internal_measurement = False`；
- `publisher = "InferenceX"`，并带
  `underlying_source_inferencex_not_official_framework_benchmark`；
- Schema 层用 `ALLOWED_ORIGINS` 禁止"外部数据标成内部实测"，
  `test_evidence_rejects_inconsistent_origin` 验证非法组合会被直接拒绝。

### 5.4 SLA 在缺 P99 时恒为 `not_evaluable`

- 32 条业务记录全部 `sla_status = "not_evaluable"`，`sla_not_evaluable_reasons`
  逐项列出缺失的 `ttft_p99_ms` / `tpot_p99_ms`；
- `sla_assessment()` 只在 P99 齐备时才返回 `evaluable`，单用户 P90 吞吐永远不会让它变
  `evaluable`（`test_single_user_p90_never_makes_sla_evaluable`）；
- 该规则不是"写死成永远 not_evaluable"：fixture 里带 P99 的记录确实会变成 `evaluable`
  （`test_fixture_with_p99_is_evaluable_showing_rule_is_not_hardcoded`），说明是条件判断
  而不是硬编码。

### 5.5 报告不得把理论收入说成实际营收或报价

| 约束 | 实现 / 证据 |
|---|---|
| 结果类型显式 | `result_type = theoretical_full_load_potential`（满载）/ `scenario_sensitivity_estimate`（情景） |
| 边界声明 | `cost_scope=gpu_cost_only`、`not_customer_quote`、`sla_commitment_not_provided` |
| 免责声明位置 | 移到收入表**之前**（`test_revenue_section_has_adjacent_disclaimer`） |
| 禁用措辞 | JSON/Markdown 均不得出现 `guaranteed_sla` / `official_quote` / `total_cost_of_ownership`；`test_json_exports_contain_no_positive_claims`、`test_report_contains_required_disclaimers` |
| 报告明确标注 | 报告显式列出币种与成本范围表、结果→输入追溯表（`test_report_declares_currency_and_cost_scope_table`、`test_report_includes_result_input_traceability_table`） |

---

## 6. 整改任务 4 专项：官方公开性能样例的覆盖情况

**结论：`official_benchmark` 实测记录仍为 0 条；新增 6 条 `partial_evidence` 候选入库。**

| 项 | 数量 | 说明 |
|---|---|---|
| manifest 条目 | **14** | 6 `partial_evidence` + 6 `not_a_result_dataset` + 2 `pending_verification` |
| 候选入库（`official_benchmark_candidate`） | **6** | 全部 `verification_status = partial_evidence`，**不进** `benchmark_record` |
| 已核实（`verified`） | **0** | 无一条同时具备完整实验配置与实测指标 |
| 有机器解析指标 | **1** | vLLM Recipes DeepSeek-V3 页的控制台汇总（16 个指标） |
| 吞吐基准 | **0** | —— |
| 单请求冒烟测试 | **1** | 上条：`success_count = 1`，自动标记且 `is_throughput_benchmark = False` |
| 存档证据文件 | **12** | `data/raw/official/*.html`，每个都记录 sha256，并有反向测试确认无"无主文件" |

采集过程：线索由 codex CLI 联网检索给出，**每个 URL 都由本机 curl 独立复核 HTTP 状态后**
才存档；数值不做人工转写，只有 1 条的指标是**机器解析**出来的（解析结果同时保留
`raw_labels` 原始标签以便复核）。候选的缺失字段由 `absent_required_fields()` 从实际模型状态
算出，与 manifest 声明不一致会直接报错。

典型候选的缺口（`hardware_binding = unbound`，按规则**不允许**填硬件字段）：

| 候选 | 已具备 | 缺失 |
|---|---|---|
| `official_vllm_recipes_deepseek_v3_console_output` | 28 字段 | `model_revision`,`framework_version`,`hardware_model`,`deployment_gpu_count`,`dataset_name` |
| `official_vllm_ascend_kimi_k2_thinking` | 13 字段 | 上列 5 项中的 4 项 + `ttft_p99_ms`,`tpot_p99_ms` |
| `official_lmsys_sglang_llama3` | 11 字段 | 7 项 |
| `official_lmsys_hpc_ops_sglang` / `_epd_elastic_encoder` / `_hetero_cpu_gpu_epd` | 10 / 10 / 9 字段 | 各 8 项 |

逐来源核查记录、缺口台账与"为什么官方工具文档不能当性能数据"见
`docs/official_coverage_gaps.md`。

### 6.1 为什么坚持不升级

那 1 条有指标解析的候选，`output_throughput = 61.00 tok/s` 是**单次请求**的结果，
`ttft_p99 = 560.00 ms` 与均值相等也仅是单样本的必然结果。把它当年产能依据或 SLA 证据，
正是任务书 §0.3 / §0.4 明令禁止的口径混用。因此它只作为"来源与解析链路可用"的证据，
不参与任何计算。

---

## 7. 生成的真实产物

| 文件 | 内容 | 大小 |
|---|---|---|
| `data/local.db` | SQLite（7 张表；192 条业务结果 + 36 条性能记录 + 6 条官方候选） | — |
| `data/exports/legacy_results.json` | 32 条满载结果 + 边界声明 + 逐步留痕 | 121314 B |
| `data/exports/legacy_results_with_tests.json` | 36 条（含 4 条测试数据，显式标注） | 132124 B |
| `data/exports/scenario.json` | `excel_部署效能_2` 在 U=0.30 下的情景结果 | 5813 B |
| `data/exports/scenarios_sensitivity.json` | 32 × 5 档变现比例 = 160 条情景 | 649881 B |
| `data/exports/overview.md` | 7 节 Markdown 报告（含币种表、追溯表、缺口） | 63717 B |
| `docs/test_run_log.txt` | `scripts/verify_all.sh` 的完整真实执行日志（19 步） | — |

---

## 8. 未获确认的业务假设（与 §0.3 对应）

1. **价格币种**：Excel 未写明，一律 `UNCONFIRMED` + `CNY; pending business confirmation`。
2. **`GPU价格!C` 覆盖范围**：按表头语境读作"万元/卡/年"，是否含机房、网络、运维、存储未确认，
   因此结果命名为 `gpu_cost_only`。
3. **H 列"总缓存命中率"语义**：未确认是物理 KV 命中率还是可计费缓存命中 Token 比例，
   只作旧表定价代理并强制打标。
4. **数据来源判定**：32 条效能数据来源为 `InferenceX`，非 SGLang/vLLM 官方数据。
5. **部署策略**：TP/EP/PD 只有字符串描述，无启动配置证据，解析结果 `verified: false`。
6. **年度秒数**：365 天；若要扣维护窗口属口径变更，需重建等价性基线。

---

## 9. 下一步建议（按优先级）

1. **确认两项业务口径，解锁全部金额结论**：① Token 价格币种；② `GPU价格!C` 的成本覆盖范围。
   *为什么优先*：改动成本极低（一份确认记录 + 重跑），但决定所有输出对外是否有解释力。
   同时确认 H 列缓存口径，把 `cache_metric_semantics` 从 `unknown` 落定。
2. **给 6 条官方候选补上同一组缺口**：它们的缺口高度集中在
   `hardware_model` + `deployment_gpu_count` + `model_revision` 三项。
   找到一份同时给出这三项的官方实测发布，即可把候选升级为 `verified` 并导入
   `official_benchmark`，让 `official_benchmark` 从 0 变 1。
3. **补齐时延实测口径**：为现有 32 条配置采集 TTFT/TPOT 的 P99 实测值，或明确接受
   "本阶段不做 SLA 判断"。*为什么优先*：SLA 是这套数据唯一还不能回答的问题，
   在拿到实测前引擎会持续返回 `not_evaluable`——这是正确行为，不是缺陷。
