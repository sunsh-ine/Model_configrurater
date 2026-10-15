# 第一阶段封板验证报告（M7–M11）

- 报告日期：2026-10-15
- 引擎版本：`pricing_engine_version 0.1.0`；指标字典 `metric_semantics_version 1.0`
- 运行环境：Linux x86_64，Python 3.12.3，pydantic 2.13.4，sqlalchemy 2.0.54，**无 GPU、离线**
- 完整执行日志：`docs/test_run_log.txt`（`bash scripts/verify_all.sh`，**21 步全绿，退出码 0**）
- 基线 commit：`748ae68`（整改前）→ 封板 commit 见文末
- 本轮范围：M7 官方数值接入 / M8 指标口径 / M9 可用性门禁 / M10 商业口径封板 / M11 交付封板
- **未进入第二阶段**：不做显存精算、TP/DP/EP 自动搜索、SLA 预测与担保、PD 比例优化、
  完整 TCO、前端、AI 报告 Agent、正式合同报价

---

## 一、四分类结论

### 1.1 已通过（有实测输出）

| # | 验收项 | 实际输出 |
|---|---|---|
| 1 | 基线核对 | 项目仓库干净在 `748ae68`；`verify_all.sh` 旧版 19 步 0 失败；`pytest` 200 passed —— 与任务书 §1 的历史报告一致 |
| 2 | 12 条候选接入 | `已导入 12 条官方数值候选（partial_evidence）`；按来源 `{sglang_qwen3_cookbook: 9, sglang_qwen3_coder_cookbook: 3}` |
| 3 | 原文逐字段核对 | `原文逐字段核对通过: 12/12（不一致 0 条）`；每条比对 13–14 个字段（命令参数 + 控制台输出） |
| 4 | 来源快照哈希 | 两个快照 `sha256匹配=True git_blob匹配=True`（Git Blob SHA-1 与文件 SHA256 分别校验） |
| 5 | 幂等 | 连续导入 2 次：候选表恒为 12 行、12 个唯一 `record_id`、无重复 |
| 6 | 指标口径共存 | `--max-concurrency 100` 与输出 `Concurrency: 90.50` 同时存在（`configured_max_concurrency=100`、`observed_concurrency=90.50`） |
| 7 | 吞吐一致性 | 12/12 通过 `吞吐 ≈ 累计 token / 时长`，最大相对误差 `2.27e-4`，容差 `1e-3` |
| 8 | Excel 回归 | `复算覆盖行数: 32`、`超容差不匹配数: 0`、最大误差 I/K `4.4E-13`、J/L `0`、M `5.7E-15` |
| 9 | 业务口径不变 | `结果条数: 32（业务 32 + 测试 0）`、`默认敏感性情景条数: 160`、`最终 pricing_result 总数: 192`、`残留的 synthetic_fixture 结果行: 0` |
| 10 | 测试口径隔离 | `--include-test-data` 才得 36 / 180 |
| 11 | 全量测试 | `260 passed`（基线 200 + 本轮新增 60；**无跳过、无失败**） |
| 12 | 离线可复现 | 从空库跑完 21 步不联网；官方来源抓取只发生在采集阶段（见 §六） |
| 13 | 校验门禁 | `validate-import` 20/20；`validate-results` 9/9 |
| 14 | 金额精度不回退 | 导出仍为十进制字符串（`"scenario_revenue_10k_per_gpu_year": "53.1480341738880"`），roundtrip 精确 |

### 1.2 已修复（本轮定位并修掉的问题）

| # | 问题 | 定位方式 | 修复 |
|---|---|---|---|
| 1 | **四反引号围栏导致 6 个官方结果块被漏解析**：Coder 原文用 ````` ````text Output ````` 包裹并内嵌 ```python，按固定三反引号匹配会错位 | 逐节核对时发现 Coder 只解析出 3 块，而原文有 9 块 | 重写围栏扫描（`iter_fences`，闭合围栏反引号数必须 ≥ 开启围栏），恢复 9 块 |
| 2 | **块归属错位**：同一窗口内同时出现 "Model Deployment Command" 与 "Benchmark Command" 时取错 | 5.1.1.1 报 `candidate=1 / command=16`，即把下一节的命令配给了上一节 | 改为取**最近**的前置标记；并把命令/输出块的匹配限制在本节范围内 |
| 3 | **`Total generated tokens (retokenized)` 覆盖真值** | 新增标签映射时发现 | 括号限定词参与查表；retokenized 单独成字段，`total_output_tokens` 保持 4210 不被 4206 覆盖 |
| 4 | **"取值相等即判偷换指标"的启发式产生误报**：`tpot_mean_ms == itl_mean_ms`（单请求下本就相等）、`configured=1 / observed=1` 都被误判 | 新测试第一轮跑出 3 处误报 | 废弃该启发式，改为**可判定的来源标签溯源**检查（`label_provenance_issues`）：核对每个指标的取值是否来自它自己那一行控制台输出 |
| 5 | **布尔值被 `str()` 化导致校验静默失效**：`within_tolerance` 存成字符串 `"True"`，`is True` 判定永远失败 | 新测试 `assert check["within_tolerance"] is True` 失败 | 序列化时保留布尔原值（`_jsonify`） |
| 6 | **文档与代码口径不一致**：`result_type` 在 5 份文档里写成 `scenario_estimate`，实际枚举是 `scenario_sensitivity_estimate` | 写封板测试时对照 `ResultType` 发现 | 统一改正 5 份文档；并新增"字典表必须与代码生成一致"的防漂移测试 |
| 7 | 候选缺 `is_throughput_benchmark` 字段，导致覆盖摘要在数值型家族上抛 `AttributeError` | `official-coverage` 实跑报错 | 补字段并按成功请求数派生（`success_count == 1` 仍判 False 冒烟测试） |

### 1.3 未确认（代码按要求保留空值/保守标记，未杜撰）

| # | 项 | 当前处理 |
|---|---|---|
| 1 | 模型 revision（12 条数值候选） | 官方原文未给出 -> `model_revision = null`，登记 `model_revision_not_published` + `missing_fields` |
| 2 | 权重精度（Qwen3 9 条） | 原文未写 -> `model_precision = null`；Coder 3 条原文写明 FP8 才填 `FP8` |
| 3 | 完整环境 pin（驱动/CUDA/容器版本） | 原文未给 -> 缺失登记，不推测 |
| 4 | 价格币种 | `currency = "UNCONFIRMED"` + `currency_assumption = "CNY; pending business confirmation"` |
| 5 | `GPU价格!C` 成本覆盖范围 | `cost_scope = gpu_cost_only`、`cost_scope_confirmation_status = unconfirmed_pending_business_review`，并**逐项列出** `cost_scope_unconfirmed`（机房/电力/网络/人工/软件/折旧租金） |
| 6 | H 列缓存命中语义 | `cache_metric_semantics = "unknown"`，仅作旧表定价代理假设 |
| 7 | 价格有效期 / 计费规则 | 未提供 -> 门禁阻断经济性计算 |
| 8 | 正文与命令冲突（ShareGPT vs `--dataset-name random`） | 以**命令**为测试参数依据，冲突写入 `verification.command_conflicts` 并打 `narrative_conflicts_with_benchmark_command` |
| 9 | Coder 原文 6 个 NVIDIA 结果块 | **本批次输入未列出**，故不生成候选，只在导入输出中登记 `unlisted_sections_with_results`；不擅自补录 |
| 10 | 年度秒数口径 | 固定 365 天（31536000s），是否扣维护窗口待业务确认 |

### 1.4 未实现（如实标注，未宣称完成）

| # | 项 | 说明 |
|---|---|---|
| 1 | 完整可复现的 `official_benchmark` 记录 | **仍为 0 条**。12 条候选有完整命令与原始输出，但缺 model revision / 环境 pin / 精度，按规则停在 `partial_evidence` |
| 2 | SLA 评估能力 | 门禁 `can_evaluate_sla = 0/18`；无实测 P99 与目标需求的完整匹配时恒 `not_evaluable` |
| 3 | 经济性计算覆盖官方候选 | 门禁 `can_calculate_pricing = 0/18`；官方候选不进入任何金额路径 |
| 4 | 正式商业报价 | 第一阶段不提供；`commercial_quote_allowed` 恒 False |
| 5 | 完整 TCO | 仅 `gpu_cost_only` |
| 6 | 自动采集官方数据 | 只允许人工触发，不绕过访问限制 |
| 7 | 逐请求样本级 P99 复算 | 官方只发布聚合值，**不允许**由总吞吐反推 P99/E2E/长度分布 |

---

## 二、官方数据导入结果

```
已导入 12 条官方数值候选（partial_evidence）
按来源: {'sglang_qwen3_cookbook': 9, 'sglang_qwen3_coder_cookbook': 3}
原文逐字段核对通过: 12/12（不一致 0 条）
```

| 家族 | 条数 | 说明 |
|---|---|---|
| `sglang_numeric_cookbook` | **12** | 本轮新增（本轮唯一新增数据） |
| `manifest_partial_evidence` | 6 | **保留**（上轮页面级候选，未被覆盖） |
| 候选表合计 | 18 | `candidate_count = 18` |
| `verified_count` | **0** | 完整可复现官方案例仍为 0（验收允许） |

按来源计数（`import-sglang-candidates` 输出，可重复验证）：

| 来源 | 记录数 | 模型 | 硬件 | 场景 |
|---|---|---|---|---|
| `sglang_qwen3_cookbook` | 9 | Qwen/Qwen3-235B-A22B-Instruct-2507 | 8×B200, TP8 | standard/reasoning/summarization 各 3 档 |
| `sglang_qwen3_coder_cookbook` | 3 | Qwen/Qwen3-Coder-480B-A35B-Instruct-FP8 | 8×MI300X, TP8, EP2 | AMD standard 低/中/高并发 |

12 个稳定 `record_id`（`candidate_id = official_<record_id>`）：

```
sglang_qwen3_cookbook:5.1.1.1  5.1.1.2  5.1.1.3
sglang_qwen3_cookbook:5.1.2.1  5.1.2.2  5.1.2.3
sglang_qwen3_cookbook:5.1.3.1  5.1.3.2  5.1.3.3
sglang_qwen3_coder_cookbook:5.1.1.1  5.1.1.2  5.1.1.3
```

任务书 §3.3 六条参考断言逐项核对结果（**全部一致**，Decimal 精确相等）：

| 记录 | 配置并发 | 集群 Input tok/s | 集群 Output tok/s | TTFT P99 ms | TPOT P99 ms | 核对 |
|---|---:|---:|---:|---:|---:|---|
| Qwen3 5.1.1.1 | 1 | 140.07 | 96.65 | 166.05 | 10.15 | ✅ |
| Qwen3 5.1.1.2 | 16 | 810.44 | 832.04 | 727.08 | 24.09 | ✅ |
| Qwen3 5.1.1.3 | 100 | 2713.46 | 2738.78 | 863.11 | 41.19 | ✅ |
| Coder 5.1.1.1 | 1 | 82.68 | 57.19 | 662.15 | 17.08 | ✅ |
| Coder 5.1.1.2 | 16 | 455.77 | 468.83 | 1318.63 | 43.48 | ✅ |
| Coder 5.1.1.3 | 64 | 893.84 | 956.80 | 529.01 | 204.24 | ✅ |

其余 6 条（Qwen3 5.1.2.x / 5.1.3.x）同样逐字段核对通过，测试
`test_remaining_six_records_also_checked` 固定该结论。

**数值来源区分**（任务书 §7.2.9 要求）：

| 分类 | 条数 | 落库字段 |
|---|---|---|
| 提供的 JSON 内数值 | 12 | `metrics_from_candidate_file`（原始提供值，保留对照） |
| 原文逐项复核过的数值 | **12** | `metrics`（优先取原文控制台输出）+ `raw_labels`（原始标签） |
| 未复核/缺失字段 | 12 | `missing_fields` + `quality_flags`（`model_revision` 等） |

两者不一致时以**原文为准**并记入 `verification.mismatches`；测试
`test_tampered_source_values_are_reported_as_mismatch` 人为篡改一条候选值，
验证结果为"11/12 通过核对、篡改项被点名、入库值仍取原文"。

---

## 三、指标口径与数据适用性规则

- 字典正文（18 个字段 + 口径 ID）：`docs/benchmark_metric_dictionary.md`
  （表格由 `engines/metric_semantics.py::render_dictionary_markdown()` 生成，
  测试 `test_metric_dictionary_doc_matches_code` 保证两边不漂移）
- 代码侧真值：`src/llm_capacity_pricing/engines/metric_semantics.py`
- 门禁实现：`src/llm_capacity_pricing/engines/applicability.py`

三条门禁的派生规则与实测结果：

| 门禁 | 规则要点 | 12 条候选实测 | 18 条候选合计 |
|---|---|---|---|
| `can_reference_performance` | 来源可定位 + 有公开数值 + 模型/硬件/卡数/测量值/测试条件能对应 | 12 条为真 | 12 |
| `can_evaluate_sla` | 必须**针对具体需求**且模型版本/精度、GPU SKU 与数量、框架环境、并行配置、负载分布、到达模型、统计口径、P99 全部齐备且相符 | 12 条为假 | **0** |
| `can_calculate_pricing` | 另有明确且匹配的 GPU 成本、Token 价格、币种、有效期、计费规则 | 12 条为假 | **0** |

**拒绝案例（正/负样例均有测试）**

- **案例 A（同源准确匹配仍不出 SLA）**：`test_sla_blocked_even_with_matching_target_missing_revision` ——
  模型、精度、硬件、卡数、框架版本全部匹配，**只有 record 缺 model revision**，
  结果仍是 `sla_not_evaluable`，原因写明 `record_missing:model_revision`。
- **案例 B（条件齐备则可以评估，证明不是硬编码）**：
  `test_sla_evaluable_when_everything_matches_shows_rule_is_not_hardcoded` ——
  补齐 revision/精度/拓扑/负载/统计口径后返回 True。
- **案例 C（成本或币种未知则阻断报价）**：`test_pricing_gate_blocked_without_confirmed_commercial_context`
  + `test_price_context_blockers_enumerated` —— 列出 `currency_not_confirmed`、
  `hardware_cost_not_bound`、`billing_rules_not_declared` 等阻断项。
- **案例 D（synthetic fixture 不得当业务证据）**：`test_synthetic_fixture_never_passes_any_gate` ——
  三条门禁全为 False。

---

## 四、Excel 回归与业务隔离

| 指标 | 基线（整改前） | 封板后 | 结论 |
|---|---|---|---|
| Excel 导入行数 | 2 / 3 / 32 | 2 / 3 / 32 | 不变 |
| 旧表复算行数 | 32 | 32 | 不变 |
| 超容差不匹配 | 0 | **0** | 不变 |
| 最大误差 I / J / K / L / M | 4.4E-13 / 0 / 4.4E-13 / 0 / 5.7E-15 | **同上** | 不变 |
| 默认满载结果 | 32 | 32 | 不变 |
| 默认敏感性 | 160 | 160 | 不变 |
| 含测试数据 | 36 / 180 | 36 / 180 | 不变 |
| `pricing_result`（默认口径） | 192 | 192 | 不变 |
| fixture 残留结果行 | 0 | 0 | 不变 |
| 金额精度 | Decimal 字符串 | Decimal 字符串 | 不回退 |

隔离证据：候选表 18 行**不进入** `benchmark_record`（该表仍为 36 = 32 Excel + 4 fixture，
`official_benchmark` 仍为 0）；`test_business_calculation_unaffected_by_candidates`
与 `test_scenario_counts_unchanged_after_candidate_import` 在导入候选**前后**对比结果集，
记录 ID 集合完全一致。

---

## 五、验证命令与真实结果

```bash
cd llm-capacity-pricing
bash scripts/verify_all.sh          # 退出码 0；21 步全绿；完整输出写入 docs/test_run_log.txt
.venv/bin/python -m pytest -q       # 260 passed in 61.97s
```

| 步骤 | 命令 | 结果 |
|---|---|---|
| 3–5 | `import-excel` ×3 | 2/3/32；第 2、3 次 `"idempotent": true` |
| 6 | `import-sglang` ×2 / `import-vllm` ×2 | 4 条 fixture（测试数据） |
| 7 | `import-official` | `已导入 6 条官方候选证据`、`跳过 10 条未入库条目` |
| 8–9 | `import-sglang-candidates` ×2 | 12 条、核对 12/12、幂等 |
| 10 | `official-coverage` | `candidate_count 18`、`numeric_candidates 12`、`verified_count 0`、`numeric_verified_against_source 12`、`can_evaluate_sla 0`、`can_calculate_pricing 0` |
| 11 | `validate-import` | `校验项: 20，通过 20，失败 0` |
| 12–13 | `calculate-all`（默认 / 含测试） | 32 / 36 |
| 14 | `scenario --record excel_部署效能_2 --utilization 0.30` | `"scenario_revenue_10k_per_gpu_year": "53.1480341738880"` |
| 15 | `validate-results` | `校验项: 9，通过 9，失败 0` |
| 16 | 复算摘要 + 敏感性 | 0 不匹配；160 / 180 |
| 17 | 口径复核 | `最终 pricing_result 总数: 192`、`残留的 synthetic_fixture 结果行: 0` |
| 18 | `report` | 68122 字节（新增未确认项表与官方候选概览） |
| 19 | `pytest -q` | **260 passed** |
| — | 汇总 | `步骤 21 项：通过 21，失败 0` |

---

## 六、证据存档

| 来源 | URL | Git Blob SHA-1 | 快照 SHA256 | 本地文件 | 是否复核 |
|---|---|---|---|---|---|
| SGLang Qwen3 Cookbook | `github.com/sgl-project/sglang/blob/main/docs/cookbook/autoregressive/Qwen/Qwen3.mdx` | `580640bd1b9ab74fb5ea0be6120050a99fa93f6d` | `23d8e61ce899920e5606ca80c30c292a88cab02321c1bd3c610a19dba0b27425` | `data/raw/official/sglang_cookbook_qwen3_2026-10-15.mdx` | ✅ 两者均匹配声明 |
| SGLang Qwen3-Coder Cookbook | `.../Qwen/Qwen3-Coder.mdx` | `a743f7ba3eb3a204bf9f69ffc7d21d3311fa7703` | `67ca6704360ef1a87cc3259dd12659966753f1280f7ad2fa52a7a1911b85ca1e` | `data/raw/official/sglang_cookbook_qwen3_coder_2026-10-15.mdx` | ✅ 两者均匹配声明 |
| 只读候选输入 | 用户提供 | — | `bc4efd2008944e18e47d6aba089852992981a9ae9ad6b909b2194b1f42b75ee1` | `data/incoming/sglang_official_benchmark_candidates_20261015.json` | ✅ 文件权限 444（只读） |

采集说明：

- 抓取时间 2026-10-15 14:15（+08:00），`raw.githubusercontent.com` 返回 **HTTP 200**
  （33,297 B / 29,163 B），**main 分支当前内容与声明的 Blob SHA 一致**，未发生版本漂移。
- **Blob SHA（Git 对象 SHA-1）与 SHA256（文件哈希）语义不同**，两者都记录；
  代码里 `git_blob_sha1()` 按 `sha1("blob <len>\0" + content)` 计算，并有测试用
  `git hash-object` 做交叉验证。
- 上游 `github.com` 页面 URL 保留在 manifest 的 `source_url`，实际抓取的 raw URL 记录在
  `requested_url`；manifest 共 16 条（含上轮 14 条），新增 2 条状态为
  `numeric_source_partial_environment`。
- 缺失时的行为：快照不存在则**不报错但不声称已核对**（`reason: 本地快照缺失，未复核原文`），
  测试 `test_missing_snapshot_means_not_verified` 固定；快照被篡改则 sha256 与 blob 双校验
  都会失败（`test_tampered_snapshot_is_detected`）。

---

## 七、未确认事项（对外使用前必须逐项确认）

| # | 事项 | 当前值 | 影响 | 记录位置 |
|---|---|---|---|---|
| 1 | 价格币种 | `UNCONFIRMED`（假设 CNY，待确认） | 全部金额 | `config/business_confirmations.example.yaml` → `currency` |
| 2 | 成本覆盖范围（6 项） | `gpu_cost_only` + 6 项未确认 | 能否称 TCO | 同上 → `cost_scope` |
| 3 | H 列缓存语义 | `unknown`（旧表定价代理） | 输入侧收入 | 同上 → `cache_metric_semantics` |
| 4 | 价格有效期 | 未提供 | 对外结论时效 | 同上 → `price_valid_period` |
| 5 | 计费规则 | 未提供 | 可计费命中比例推导 | 同上 → `billing_rules` |
| 6 | 年度秒数口径 | 365 天 | 全部年化金额 | 同上 → `annual_seconds` |
| 7 | 模型 revision / 精度 / 环境 pin | 官方原文未给出 | 候选不可复现、不能作 SLA 依据 | 候选 `missing_fields` |

确认流程已预置：复制模板为 `config/business_confirmations.yaml`，逐项填写
`status/value/confirmed_by/confirmed_at`；**确认是显式记录，不改代码默认值**，
未确认项在 JSON 与报告口径表中始终可见。本轮**不杜撰任何确认结果**。

---

## 八、是否满足封板条件（逐项 Yes/No）

| DoD 项 | 判定 | 依据 |
|---|---|---|
| 完成实际代码、Schema 迁移与 CLI 适配 | **Yes** | 新增 `engines/applicability.py`、`engines/metric_semantics.py`、`importers/sglang_cookbook.py`；扩展候选表（含 `_ensure_columns` 轻量迁移）；新增 `import-sglang-candidates` 命令 |
| 原始 JSON 作为只读输入留存、12 条可查询 | **Yes** | `data/incoming/...json`（444）+ 候选表 12 行 |
| 两个官方来源有可审计索引；能抓取则存原文与 SHA256 | **Yes** | manifest 2 条新增；快照 + SHA256 + Blob SHA；见表 §六 |
| 12 个候选 `record_id` 唯一稳定、幂等、`partial_evidence` | **Yes** | 重复导入 2 次恒 12 行；`quality_status` 恒 `partial_evidence` |
| 新增指标字典与口径校验规则 | **Yes** | `docs/benchmark_metric_dictionary.md`（代码生成 + 防漂移测试）；吞吐一致性 12/12 |
| 默认 Excel 业务数据与经济性结果不受候选影响 | **Yes** | 32 / 160 / 192 不变（§四） |
| 32 行复算零超容差、金额高精度不回退 | **Yes** | 0 不匹配；十进制字符串 roundtrip 测试 |
| SLA/报价阻断逻辑正确 | **Yes** | `can_evaluate_sla=0`、`can_calculate_pricing=0`、`commercial_quote_allowed=False`；正/负样例齐备（§三） |
| `verify_all.sh` 不依赖 GPU/联网、干净库全程通过；全部 pytest 通过 | **Yes** | 21/21 步、`EXIT=0`、260 passed |
| 更新 README / 字段字典 / 覆盖缺口 / 封板报告与日志 | **Yes** | 见 §九 文件清单 |
| 四分类事项 + 路径/命令/计数/失败原因 | **Yes** | 本文件 §一～§六 |

**结论：本轮 M7～M11 的 P0 全部满足，P1 文档与离线复现完成，第一阶段可以封板。**
`official_benchmark` 的 `verified_count = 0` 按任务书 §8 属于允许情形：
12 条官方数值记录已正确接入、逐字段核对、且未发生任何越权推断。

**阻塞项：无。**

---

## 九、本轮改动文件

**新增源码**

| 文件 | 作用 |
|---|---|
| `src/llm_capacity_pricing/engines/applicability.py` | M9 三条门禁的派生规则 |
| `src/llm_capacity_pricing/engines/metric_semantics.py` | M8 口径字典、口径 ID、吞吐一致性、标签溯源 |
| `src/llm_capacity_pricing/importers/sglang_cookbook.py` | M7 候选读取、原文解析、逐字段核对、候选构建 |
| `config/business_confirmations.example.yaml` | M10 业务确认记录模板 |
| `docs/benchmark_metric_dictionary.md` | M8 指标字典（表格由代码生成） |
| `docs/phase1_final_validation.md` | 本报告 |
| `docs/phase1_release_checklist.md` | 封板清单 |
| `data/incoming/sglang_official_benchmark_candidates_20261015.json` | 只读输入（444） |
| `data/raw/official/sglang_cookbook_qwen3{,_coder}_2026-10-15.mdx` | 官方原文快照 |

**修改**

`schemas/official.py`（数值型候选模型 + 门禁字段）、`schemas/pricing.py`（成本范围未确认项）、
`storage/database.py`（候选表新列 + 轻量迁移）、`storage/repository.py`（按家族 upsert/查询）、
`importers/console_summary.py`（SGLang 标签 + retokenized 修复 + 符号值）、
`importers/official_manifest.py`（门禁派生 + 跳过原因）、`services.py`（新服务 + 覆盖摘要）、
`cli.py`（新命令）、`reporting/json_export.py`、`reporting/markdown.py`、
`scripts/verify_all.sh`（21 步）、`data/sources/official_manifest.yaml`（16 条）、
`README.md`、`docs/{data_dictionary,evidence_policy,formulas,official_coverage_gaps,validation_report}.md`

**新增测试**

`tests/test_sglang_numeric_candidates.py`（49 个）、`tests/test_business_sealing.py`（11 个）；
另更新 `tests/test_official_manifest.py`、`tests/test_official_partial_evidence.py`。
