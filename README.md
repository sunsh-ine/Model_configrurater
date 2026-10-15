# llm-capacity-pricing

大模型部署基准数据与 Token 定价计算引擎（离线 MVP）。

**第一阶段**：从一份 Excel 部署效能表出发，规范化入库、复算旧表经济性、做变现比例情景分析，
并内置 SGLang `bench_serving` / vLLM `bench serve` 的**版本化** Benchmark 解析适配器。

**第二阶段（M12–M18）**：模型与硬件资源选型引擎——解析官方模型结构、管理 GPU SKU 规格、
计算权重与 KV Cache 显存、枚举 TP/DP 部署候选并给出**静态**资源可行性。
第二阶段**不写任何定价公式**，也**不做** TTFT/TPOT 性能预测。

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

# 10) 导入官方页面候选（partial_evidence）
.venv/bin/python -m llm_capacity_pricing.cli import-official \
  --manifest data/sources/official_manifest.yaml --db data/local.db --root .

# 11) 导入官方数值型候选（M7：12 条，含原文逐字段核对）
.venv/bin/python -m llm_capacity_pricing.cli import-sglang-candidates \
  --file data/incoming/sglang_official_benchmark_candidates_20261015.json \
  --db data/local.db --root .

# 12) 查看官方覆盖与门禁统计
.venv/bin/python -m llm_capacity_pricing.cli official-coverage --db data/local.db

# 13) 测试
.venv/bin/python -m pytest -q
```

### 第二阶段（资源选型）：从模型与硬件出发做静态规划

```bash
# 1) 导入模型结构（读 data/models/ 里归档的官方 config.json 与权重索引）
.venv/bin/python -m llm_capacity_pricing.cli import-models --db data/local.db --root .

# 2) 导入 GPU SKU 与服务器形态
.venv/bin/python -m llm_capacity_pricing.cli import-hardware --db data/local.db

# 3) 查看库存（模型结构 / 硬件规格 / 待核实字段）
.venv/bin/python -m llm_capacity_pricing.cli sizing-inventory --db data/local.db

# 4) 单个配置的显存构成（权重 + KV + 运行时）
.venv/bin/python -m llm_capacity_pricing.cli estimate-memory \
  --model qwen3-32b --hardware nvidia-h100-sxm-80gb --tp 8 --dp 1 \
  --context-length 8192 --concurrency 32 --block-size 16 \
  --usable-memory-bytes 85899345920 --usable-source "现场 nvidia-smi（示例）" \
  --runtime-reserve-fraction 0.1 --runtime-source "框架经验值（示例假设）" \
  --db data/local.db

# 5) TP/DP 候选规划 + JSON/Markdown 报告
.venv/bin/python -m llm_capacity_pricing.cli plan-deployment \
  --model qwen3-32b --hardware nvidia-h100-sxm-80gb --server hgx-h100-8gpu \
  --context-length 8192 --concurrency 32 --block-size 16 \
  --usable-memory-bytes 85899345920 --usable-source "示例假设" \
  --runtime-reserve-fraction 0.1 --runtime-source "示例假设" \
  --tp-sizes 1,2,4,8 --dp-sizes 1,2 \
  --out data/exports/deployment_plan.json --report data/exports/sizing_report.md \
  --db data/local.db
```

> **可用显存必须显式给出**：厂商标称容量（80GB / 141GB）**不等于**推理可分配显存。
> 不给实测值或带来源的假设，规划只会返回 `insufficient_information`（这是刻意设计）。
> 只提供假设时结论为 `conditional_feasible`，落地前必须用 `nvidia-smi` 复核。

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
| `import-sglang-candidates --file <json> --db <path> [--root <dir>]` | **M7**：导入官方 SGLang Cookbook 数值型候选，并逐字段核对存档原文 |
| `official-coverage --db <path>` | 官方覆盖情况：两家族候选数 / 已核实数 / 有解析指标数 / **M9 门禁统计** |
| `list-records --db <path>` | 列出全部 `record_id` 与来源类型 |
| `import-models --manifest <yaml> --db <path> [--root .] [--precision bf16]` | **M13**：导入模型结构与权重元数据（归档文件哈希 Fail-Closed） |
| `import-hardware --catalog <yaml> --db <path>` | **M14**：导入 GPU SKU 规格与服务器形态 |
| `sizing-inventory --db <path>` | 第二阶段库存：模型结构 / 硬件规格 / 待核实字段 |
| `estimate-memory --model <id> --hardware <id> --tp N --dp N --context-length N --concurrency N …` | **M15+M16**：单配置显存构成（权重 / KV / 运行时 / 余量） |
| `plan-deployment --model <id> --hardware <id> --context-length N --concurrency N …` | **M17+M18**：枚举 TP/DP 候选 + 静态可行性 + JSON/Markdown 导出 |

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
│   │   ├── sglang_cookbook.py             # M7：官方 Cookbook 数值候选 + 原文逐字段核对
│   │   ├── console_summary.py             # 官方控制台汇总块 -> 指标（机器解析）
│   │   └── common.py                      # 字段别名 / percentile / 证据构造
│   ├── sizing/                             # 第二阶段：资源选型
│   │   ├── model_spec.py                   # M13 模型结构解析（HF config + 权重索引）
│   │   ├── hardware.py                     # M14 GPU SKU / 服务器形态 / 兼容性
│   │   ├── weights.py                      # M15 权重显存（分解 + TP 分布 + 一致性）
│   │   ├── kv_cache.py                     # M16 KV Cache + 运行时预留
│   │   └── planner.py                      # M17 显存合成 + TP/DP 候选 + 可行性
│   ├── engines/
│   │   ├── pricing.py                     # 纯 Decimal 公式（含除零语义 + float 守卫）
│   │   ├── calculator.py                  # 编排 + 证据链 + SLA 状态 + 快照哈希
│   │   ├── applicability.py               # M9：参考/SLA/经济性三条派生门禁
│   │   └── metric_semantics.py            # M8：指标口径字典 + 一致性 + 溯源校验
│   ├── storage/
│   │   ├── database.py                    # SQLAlchemy 2.x / SQLite
│   │   └── repository.py                  # 幂等 upsert + 读取 + 测试态结果清理
│   ├── reporting/
│   │   ├── json_export.py                 # 十进制字符串编码 + 兼容读取
│   │   └── markdown.py                    # 7 节报告
│   ├── services.py                        # 服务层（CLI 与测试共用）
│   └── cli.py                             # Typer CLI
├── data/
│   ├── models/                            # 归档的官方 config.json / 权重索引 + 清单
│   ├── hardware/hardware_catalog.yaml     # GPU 规格库（含来源与待核实字段）
│   └── raw/vendor/                        # 厂商规格页存档（与 benchmark 证据分开）
├── tests/                                 # 374 个用例，全部通过
├── scripts/verify_all.sh                  # 一键复现（27 步，含第二阶段与敏感扫描）
├── scripts/make_public_sample.py          # 生成脱敏公开样本（V1.2 F1）
└── docs/
    ├── data_dictionary.md                 # 字段字典
    ├── formulas.md                        # 计算公式
    ├── evidence_policy.md                 # 证据等级策略
    ├── official_coverage_gaps.md          # 官方样本覆盖缺口
    ├── validation_report.md               # 验证报告
    └── test_run_log.txt                   # 真实执行日志
```

---

## 3.5 公开仓库的数据边界（V1.2 F1）

| 类别 | 路径 | 是否入库 | 说明 |
|---|---|---|---|
| 代码 / 测试 / 口径文档 | `src/` `tests/` `docs/` | ✅ | 公开 |
| 官方来源存档证据 | `data/raw/official/`（12 HTML + 2 MDX，含 sha256） | ✅ | 公开文档的存档，版权见 `NOTICE.md` |
| 合成 fixture | `data/fixtures/` | ✅ | 明确标注 `synthetic`，不参与默认业务计算 |
| **脱敏示例样本** | `data/examples/`（含合成工作簿与手算期望值） | ✅ | 由 `scripts/make_public_sample.py` 生成，可公开 |
| **内部业务产物** | `data/exports/`、`data/private/` | ❌ | 真实金额与派生结果，不入版本库 |
| 原始价格工作簿 | `data/raw/*.xlsx` | ❌ | 含未脱敏价格，不入版本库 |

- 克隆仓库后：把原始工作簿放进 `data/raw/`，把内部 oracle 放进 `data/private/`（可选），
  即可跑全量测试；没有它们也能跑通核心计算——用 `data/examples/` 的脱敏样本
  （实测公开克隆 `155 passed, 106 skipped, 0 failed`）。
- 提交前自检：`bash scripts/verify_all.sh` 的第 19 步会扫描已跟踪文件中是否出现内部定价数值，
  并核对忽略规则；同一规则也有测试版本 `tests/test_v12_hardening.py`。
- 详见 `docs/security_remediation_report.md`（含历史暴露影响评估）与 `docs/risk_register.md`。

---

## 4. 关键设计决策

**金额精度。** 计算全程 `Decimal`；SQLite 中金额列用 `TEXT` 存十进制字面量
（SQLite 的 `NUMERIC` 实为 float，会破坏精度）。JSON 导出把金额/比率字段写成
**可精确恢复的十进制字符串**（定点字面量，如 `"17.3448"`）：JSON number
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
（`scenario_sensitivity_estimate`）。`validate-results` 用 9 项检查固定这些性质。

**可复算。** 每次结果都带 `input_snapshot_hash`、`pricing_engine_version` 与
逐步留痕 `steps`；同一输入 + 同一版本必然得到同一结果。

**数据适用性门禁（M9）。** "能用来干什么"是**派生状态**，不是人工标记：
`can_reference_performance`（可作官方参考展示）/ `can_evaluate_sla`（可用于某个**具体**
需求的 SLA 结论，要求版本、精度、SKU、卡数、拓扑、负载、统计口径、P99 全部齐备且相符）/
`can_calculate_pricing`（可进经济性计算，另需币种、成本、价格、有效期、计费规则）。
当前 18 条候选：参考 12、SLA 0、经济性 0。正式报价在第一阶段**不存在**
（`commercial_quote_allowed` 恒 False）。

**指标口径隔离（M8）。** 口径定义集中在 `engines/metric_semantics.py`，
`docs/benchmark_metric_dictionary.md` 的表格由代码生成并有防漂移测试；
吞吐一致性校验 `吞吐 ≈ 累计 token / 时长`（容差 1e-3，实测最大误差 2.27e-4）；
每个指标的取值还要能溯源到它自己那一行控制台输出（`label_provenance_issues`）。

---

## 5. 当前能力边界

**已实现：** Excel 三表导入与幂等落库；旧表 32 行 Decimal 复算（0 不匹配）；
变现比例情景与 10/30/50/70/100% 敏感性（默认 160 条）；SGLang / vLLM 版本化适配器与
字段改名映射；业务/测试数据隔离与 `validate-results` 证据链校验；官方来源 manifest、
存档证据（sha256）、**18 条 `partial_evidence` 候选**（6 条页面级 + 12 条官方数值型）
与字段级缺口台账；**指标口径字典与吞吐一致性校验**；**三条派生门禁**
（参考展示 / SLA 评估 / 经济性计算）；**200 + 60 = 260 个自动化测试**。

**第二阶段已实现：** 官方模型结构解析（Qwen3-8B / Qwen3-32B，含 revision 与哈希）、
GPU SKU 规格库（H100 SXM / H200 SXM，型号与 SKU 分离）、权重显存估算（**与官方权重索引逐字节一致**）、
KV Cache 估算（GQA/MQA/MHA、Paged 块对齐、TP 头切分/复制）、运行时显存独立建模、
TP/DP 候选枚举与四类静态可行性状态、JSON/Markdown 资源规划报告。**49 个第二阶段测试**。

**第二阶段未实现（边界内不做）：**

- TTFT / TPOT / ITL / E2E 性能预测；按峰值并发推断所需 GPU 数量；
- 跨 GPU/TP/DP 的吞吐线性外推；
- PD 分离、MoE EP、MTP/Speculative 的资源优化（未核实结构返回 `unsupported_architecture`）；
- 未验证量化实现的精确显存核算；
- 把静态可行方案转成报价；
- Ascend 910C / B200：**数据结构与接口就绪**，规格与框架证据未核实前所有数值保持 `None`。

**未实现（如实标注）：**

- 官方 Benchmark **完整可复现记录 0 条**：12 条官方数值候选有完整测试命令与原始输出、
  已逐字段核对，但缺 `model_revision` 与完整环境 pin；6 条页面级候选缺硬件绑定。
  按"不允许补造缺失配置"的要求全部停在 `partial_evidence`，未捏造；
- **官方候选不参与经济性计算**：`can_evaluate_sla = 0`、`can_calculate_pricing = 0`（18 条全为否）；
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
| [`docs/v12_hardening_report.md`](docs/v12_hardening_report.md) | V1.2 加固（F1–F8）四分类结论与修复前后差异 |
| [`docs/security_remediation_report.md`](docs/security_remediation_report.md) | 内部定价数据暴露清单、凭证扫描、历史影响评估 |
| [`docs/risk_register.md`](docs/risk_register.md) | 风险清单（数据安全 / 证据 / 口径 / 商业 / 工程） |
