# llm-capacity-pricing

大模型部署基准数据与 Token 定价计算引擎（离线 MVP）。

从一份 Excel 部署效能表出发，规范化入库、复算旧表经济性、做变现比例情景分析，
并内置 SGLang `bench_serving` / vLLM `bench serve` 的**版本化** Benchmark 解析适配器。

> **这不是私有化部署 SLA 预测系统，也不是对外报价系统。**
> 所有金额为万元，`cost_scope = gpu_cost_only`（仅表内 GPU 成本，不是完整 TCO）。
> 满载 Token 收入是理论上限，不是实际营收。

---

## 1. 快速开始（从零到 JSON/Markdown）

环境要求：**Python 3.11+，无 GPU，无需联网**（计算与测试均在本地完成）。

> **仓库不含原始价格 Excel。** `Token产能计算器-0910.xlsx` 含未脱敏的 GPU 成本与 Token 单价，
> 按任务书 §6「敏感价格文件建议不入 Git」未纳入版本库。克隆后请先把该文件放到
> `data/raw/`，否则第 3 步会报缺文件。实测（缺该文件时的行为）：
>
> - `pytest` → **`105 passed, 95 skipped`**（依赖 Excel 的用例自动 skip，不会误报失败）；
> - `import-excel` 报缺文件并返回非零；`validate-import` 如实报 `校验项: 20，通过 17，失败 3`
>   （三项分别是 GPU 价格 2 行、模型价格 3 行、效能 32 行的计数不符，这正是"缺数据就说缺数据"）；
> - 不依赖 Excel 的部分仍可完整运行：`init-db`、`import-sglang`、`import-vllm`、
>   `import-official`（6 条官方候选）、`official-coverage`。

```bash
cd llm-capacity-pricing

# 1) 建虚拟环境并安装锁定依赖
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
.venv/bin/python -m pip install -e .          # 让 `python -m llm_capacity_pricing.cli` 可导入

# 2) 建库
.venv/bin/python -m llm_capacity_pricing.cli init-db --db data/local.db

# 3) 导入 Excel（重复执行幂等，不会产生重复记录）
.venv/bin/python -m llm_capacity_pricing.cli import-excel \
  --file 'data/raw/Token产能计算器-0910.xlsx' --db data/local.db

# 4) 校验导入（20 项检查：总量 / 可追溯性 / 缺失值语义 / 历史外部来源标注 / 币种假设 …）
.venv/bin/python -m llm_capacity_pricing.cli validate-import --db data/local.db

# 5) 旧表满载复算 -> JSON（默认业务口径，不含人工测试数据）
.venv/bin/python -m llm_capacity_pricing.cli calculate-all \
  --mode legacy_full_load --db data/local.db --out data/exports/legacy_results.json

# 6) 单条记录情景分析（U = 0.30）-> JSON
.venv/bin/python -m llm_capacity_pricing.cli scenario \
  --record excel_部署效能_2 --utilization 0.30 --db data/local.db \
  --out data/exports/scenario.json

# 7) 校验计算结果可追溯到性能记录 / 硬件成本 / Token 单价（9 项检查）
.venv/bin/python -m llm_capacity_pricing.cli validate-results --db data/local.db

# 8) Markdown 概览报告
.venv/bin/python -m llm_capacity_pricing.cli report \
  --db data/local.db --out data/exports/overview.md

# 9) 导入 synthetic fixture（验证两套适配器；属测试数据，默认不参与计算）
.venv/bin/python -m llm_capacity_pricing.cli import-sglang \
  --file data/fixtures/sglang_synthetic.jsonl --metadata config/fixture_sglang.yaml --db data/local.db
.venv/bin/python -m llm_capacity_pricing.cli import-vllm \
  --file data/fixtures/vllm_synthetic.json --metadata config/fixture_vllm.yaml --db data/local.db

# 10) 导入官方候选（partial_evidence）+ 查看官方覆盖情况
.venv/bin/python -m llm_capacity_pricing.cli import-official \
  --manifest data/sources/official_manifest.yaml --db data/local.db --root .
.venv/bin/python -m llm_capacity_pricing.cli official-coverage --db data/local.db

# 11) 测试
.venv/bin/python -m pytest -q
```

### 一键复现以上全部步骤

```bash
bash scripts/verify_all.sh
```

脚本会串起建库 → 导入 → 校验 → 计算 → 情景 → 报告 → 测试，任一失败即非零退出。
一次真实执行日志见 `docs/test_run_log.txt`。

---

## 2. 命令清单

| 命令 | 作用 |
|---|---|
| `init-db --db <path>` | 建库建表 |
| `import-excel --file <xlsx> --db <path>` | 导入 Excel 三张表（幂等） |
| `validate-import --db <path>` | 导入后一致性校验（20 项），全部通过才返回 0 |
| `calculate-all --mode {legacy_full_load,scenario} --db <path> [--out <json>] [--utilization <U>] [--include-test-data]` | 批量计算并可选导出。**默认只算业务数据** |
| `scenario --record <record_id> --utilization <U> --db <path> [--out <json>] [--include-test-data]` | 单条记录情景分析 |
| `report --db <path> --out <md> [--include-test-data]` | Markdown 概览报告（默认业务口径） |
| `validate-results --db <path> [--include-test-data]` | 校验结果→性能记录/硬件成本/单价的可追溯性与 SLA 口径（9 项） |
| `import-sglang --file <jsonl> --metadata <yaml> [--db <path>]` | SGLang 适配器导入 |
| `import-vllm --file <json> --metadata <yaml> [--db <path>]` | vLLM 适配器导入 |
| `import-official --manifest <yaml> --db <path> [--root <dir>]` | 把 manifest 中 `partial_evidence` 条目导入候选表（含 sha256 校验） |
| `official-coverage --db <path>` | 官方覆盖情况：候选数 / 已核实数 / 有解析指标数 / 冒烟测试数 |
| `list-records --db <path>` | 列出全部 `record_id` 与来源类型 |

所有命令成功返回 `0`，失败返回非零并输出明确错误信息（缺文件、非法百分比、
负价格、无效单位、找不到记录等）。

---

## 3. 项目结构

```text
llm-capacity-pricing/
├── pyproject.toml / requirements.lock     # 依赖与锁定版本
├── config/
│   ├── settings.example.yaml              # 运行配置样例（含 network.allow_fetch: false）
│   └── fixture_*.yaml                     # 适配器元数据（fixture 用）
├── data/
│   ├── raw/                               # 原始 Excel + 存档的官方页面
│   │   └── official/                      # 人工保存的官网证据（含 sha256 记录）
│   ├── sources/official_manifest.yaml     # 官方来源 curated manifest
│   ├── fixtures/                          # synthetic fixture（人工构造）
│   └── exports/                           # JSON / Markdown 输出
├── src/llm_capacity_pricing/
│   ├── schemas/                           # Pydantic v2 模型
│   │   ├── evidence.py  costs.py  benchmark.py  pricing.py
│   │   └── official.py                    # 官方候选（partial_evidence）与缺口台账规则
│   ├── importers/
│   │   ├── excel_importer.py              # Excel -> 规范化模型
│   │   ├── sglang_adapter.py              # bench_serving JSONL
│   │   ├── vllm_adapter.py                # bench serve JSON
│   │   ├── official_manifest.py           # manifest -> 候选记录（sha256 校验）
│   │   ├── console_summary.py             # 官方控制台汇总块 -> 指标（机器解析）
│   │   └── common.py                      # 字段别名 / percentile / 证据构造
│   ├── engines/
│   │   ├── pricing.py                     # 纯 Decimal 公式（含除零语义 + float 守卫）
│   │   └── calculator.py                  # 编排 + 证据链 + SLA 状态 + 快照哈希
│   ├── storage/
│   │   ├── database.py                    # SQLAlchemy 2.x / SQLite
│   │   └── repository.py                  # 幂等 upsert + 读取 + 测试态结果清理
│   ├── reporting/
│   │   ├── json_export.py                 # 十进制字符串编码 + 兼容读取
│   │   └── markdown.py                    # 7 节报告
│   ├── services.py                        # 服务层（CLI 与测试共用）
│   └── cli.py                             # Typer CLI
├── tests/                                 # 200 个用例，全部通过
├── scripts/verify_all.sh                  # 一键复现（19 步）
└── docs/
    ├── data_dictionary.md                 # 字段字典
    ├── formulas.md                        # 计算公式
    ├── evidence_policy.md                 # 证据等级策略
    ├── official_coverage_gaps.md          # 官方样本覆盖缺口
    ├── validation_report.md               # 验证报告
    └── test_run_log.txt                   # 真实执行日志
```

---

## 4. 关键设计决策

**金额精度。** 计算全程 `Decimal`；SQLite 中金额列用 `TEXT` 存十进制字面量
（SQLite 的 `NUMERIC` 实为 float，会破坏精度）。JSON 导出把金额/比率字段写成
**可精确恢复的十进制字符串**（定点字面量，如 `"149.54340799296"`）：JSON number
会被多数读取器落成 IEEE-754 double，28 位有效数字不保真。文件头写
`decimals_encoding` 声明编码方式，兼容读取用
`llm_capacity_pricing.reporting.json_export.loads_tolerant`。公式层入口有
`_reject_float` 守卫，传 `float` 直接抛 `TypeError`。

**不做未证实的补齐。** 缺 TP/EP/DP 证据、缺 TTFT/TPOT P99、缺 GPU SKU 时字段一律
`null` 并在 `missing_fields` / `quality_flags` 登记。`deployment_strategy_parsed`
始终带 `verified: false`，不会写回已核实的拓扑字段。官方候选缺硬件配置时
`hardware_binding = unbound`，架构上不允许填硬件字段。

**口径隔离。** `configured_concurrency` ≠ 实测平均并发；`tpot_mean_ms` 不会被
`mean_itl_ms` 顶替（有 `refused_substitution:*` 标记）；`cache_hit_rate_raw` 与
`physical_cache_hit_rate` / `billable_cached_input_ratio` 分开存；旧表 I/J/K/L/M
保留在 `legacy_*`，复算结果写在 `PricingResult`。

**测试数据隔离。** 4 条 `synthetic_fixture` 是人工构造的解析器测试样例，
`calculate-all` / `scenario` / `report` / `validate-results` **默认全部排除**它们
（所以默认敏感性是 32 × 5 = 160 条）；要算进去必须显式加 `--include-test-data`
（36 × 5 = 180 条），且结果会标 `scope = "test"`。以默认口径落库时，
引擎会主动删除库中遗留的测试态结果行，避免"业务口径的库里混着测试结果"。

**除零与不可计算。** 返回具名异常与 `status="not_evaluable"`，绝不返回 `inf`，
绝不静默填 0。

**来源不混淆。** 32 条效能数据来源标为 `internal_excel`（载体）、
`measurement_origin = historical_external_third_party`（归属）、`publisher=InferenceX`，
并带 `underlying_source_inferencex_not_official_framework_benchmark` 与
`historical_external_source_not_internal_measurement` 标记：**放在内部 Excel 里
不等于内部实测**。`official_benchmark` 目前 0 条（详见 `docs/official_coverage_gaps.md`）。
synthetic fixture 以 `assumption` 级证据入库，报告与校验按来源分级计数，不混合统计。

**结果可追溯。** 每条 `PricingResult` 都带 `performance_record_id` /
`hardware_price_id` / `token_price_id` / `evidence_ids`，缺价格关联时写
`linkage_warnings` 而不是静默；`sla_status` 在缺 P99 时恒为 `not_evaluable` 并附原因；
`result_type` 明确该结果是理论上限（`theoretical_full_load_potential`）还是情景估算
（`scenario_estimate`）。`validate-results` 用 9 项检查固定这些性质。

**可复算。** 每次结果都带 `input_snapshot_hash`、`pricing_engine_version` 与
逐步留痕 `steps`；同一输入 + 同一版本必然得到同一结果。

---

## 5. 当前能力边界

**已实现：** Excel 三表导入与幂等落库；旧表 32 行 Decimal 复算（0 不匹配）；
变现比例情景与 10/30/50/70/100% 敏感性（默认 160 条）；SGLang / vLLM 版本化适配器与
字段改名映射；业务/测试数据隔离与 `validate-results` 证据链校验；官方来源 manifest、
存档证据（sha256）、6 条 `partial_evidence` 候选与字段级缺口台账；JSON / Markdown 报告；
**200 个自动化测试**。

**未实现（如实标注）：**

- 官方 Benchmark **完整实测记录 0 条**：6 条候选都缺硬件型号与卡数等关键配置，
  按"不允许补造缺失配置"的要求停在 `partial_evidence`，未捏造；
- SLA 评估不可用：无 P99 TTFT/TPOT 实测数据，相关字段持续 `not_evaluable`；
- 完整 TCO 未实现：只有 GPU 成本；
- **有意不做**：跨配置的每卡→集群吞吐外推、自动选卡满足 SLA、异构 P/D 自动搜索、
  模型显存精算、前端、合同报价审批、税务。

**待业务确认：**

1. Token 价格币种 → 当前假设 `CNY; pending business confirmation`；
2. `GPU价格!C` 的成本覆盖范围（是否含机房/网络/运维）→ 结果只命名 `gpu_cost_only`；
3. H 列缓存命中率的计费语义 → 当前仅作旧表定价代理假设并显式打标。

---

## 6. 与任务书结构的差异

- 额外增加了 `src/llm_capacity_pricing/services.py`（服务层），使 CLI 保持薄参数解析、
  测试可直接调用同一编排逻辑，避免"CLI 能跑但逻辑不可测"。
- 额外增加了 `src/llm_capacity_pricing/schemas/official.py`、
  `importers/official_manifest.py` 与 `importers/console_summary.py`：任务书 M5 允许
  用 `partial_evidence` 保存不完整官方案例，这里把它做成了一等公民（候选表 + 字段级
  缺口台账 + 机器解析控制台汇总），而不是让"部分证据"消失在笔记里。
- 额外增加了 `tests/test_official_manifest.py`（9 个用例）与
  `tests/test_official_partial_evidence.py`（23 个用例），用于固定"不完整官方案例
  不得升级为 `official_benchmark`""候选不得进入 `benchmark_record`"这些规则。
- 额外增加了 `tests/test_test_data_isolation.py`、`tests/test_json_precision.py`、
  `tests/test_evidence_linkage.py`，对应验收整改任务 1～3。
- 额外增加了 `scripts/verify_all.sh` 与 `docs/test_run_log.txt`，用于交付可复现的真实
  执行日志（任务书 §10 DoD 第 7 条）。

## 7. 文档索引

| 文档 | 内容 |
|---|---|
| [`docs/data_dictionary.md`](docs/data_dictionary.md) | 每个字段的类型、可空性、Excel 列映射与口径陷阱 |
| [`docs/formulas.md`](docs/formulas.md) | 计算公式、精确样例、除零语义、命名边界 |
| [`docs/evidence_policy.md`](docs/evidence_policy.md) | 证据等级、溯源要求、指标口径隔离、联网策略 |
| [`docs/official_coverage_gaps.md`](docs/official_coverage_gaps.md) | 官方样本缺口与逐来源核查记录 |
| [`docs/validation_report.md`](docs/validation_report.md) | 复算最大误差、已完成/未完成能力、下一步建议 |
