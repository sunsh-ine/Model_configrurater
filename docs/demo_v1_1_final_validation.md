# Demo V1.1 最终验证报告（N1–N2）

**基线提交**：`616a86c`（Demo V1.1，D6–D9）
**本轮范围**：N1 公开数据可信度加固 + N2 封板验证
**结论**：**N1 通过，N2 通过，公开数据 Demo 冻结。**

> 本报告的所有数字都来自**本轮实际执行**，日志见 `docs/test_run_log.txt`
> （sha256 `a9a80ffdb15fbe240a84a702da4842abd7d64ff854c6d3ea206066e1a2acf81b`，
> 完成时间 `2026-10-16T15:35:25+08:00`）。该文件随本次提交一起入库，可独立复核。

---

## 1. N1：来源声明与实际入库字段的绑定

### 1.1 复审指出的缺陷（已确认成立）

`_check_claims()` 此前只验证两件事：

1. `claims` 声明的字面量是否出现在归档原文中；
2. 每个有值字段是否有 `claim` 或 `derived` 说明。

**缺的是第三条：入库字段值是否等于 `claim.value`。** 因此下面这种改动可以绕过核验：

```json
// 只改入库字段，claim 与原文都不动（1131.55 确实在原文里）
record["metrics"]["output_throughput_tok_s"] = 9999.99
```

原有的反向用例同时改了 `claim.value` 与 `tokens`，所以恰好没有覆盖这种"只改一边"的情况。

### 1.2 修复内容

`scripts/import_public_benchmarks.py` 的 `_check_claims()` 现在有三道互相独立的约束：

| 约束 | 实现 | 挡住的错误 |
|---|---|---|
| **字面量** | `_claim_found()`：tokens 按顺序出现在归档原文中 | 把来源里没有的数字写进来 |
| **取值绑定**（本轮新增） | `_resolve_record_value()` 定位实际入库字段 + `_values_equal()` 比较 | 只在一边动手脚 |
| **覆盖率** | 有值必须有 claim 或 derived | 静默采信字段 |

实现细节：

* **精确数值语义**：数值统一按 `Decimal(str(v))` 比较，不用浮点 `==`，
  避免 `1131.55` 的二进制表示差异造成误判；
* **路径解析**：claim 的键是字段路径（`metrics.output_throughput_tok_s`），
  裸字段名（`model_name`）依次在 deployment / workload / metrics 中解析；
  **声明了但记录里没有这个字段**同样拒绝（不允许"声明了却不入库"）；
* **derived 同样受绑定**：`derived_fields` 声明的取值必须与入库值一致，
  否则拒绝。即"写了理由"既不能凭空造数，也不能升级成原文实测；
* **不允许同一字段同时有 claim 与 derived**（两套口径会打架）；
* **失败在写库之前**：核验在 `import_public_references()` 内完成，任一不过即
  `SystemExit` —— `init_db()` 与任何写库动作都还没发生，不会留下半批数据。

### 1.3 反向用例（要求的三条 + 一条补充）

| 用例 | 篡改方式 | 预期 |
|---|---|---|
| `test_demo_recorded_value_must_equal_claim_value` | **只改入库字段**，claim 与字面量保持真值 | 拒绝、0 条入库 |
| `test_demo_claim_value_must_equal_recorded_value` | **只改 `claim.value`**，记录与 tokens 不动 | 拒绝、0 条入库 |
| `test_demo_derived_value_must_match_recorded_field` | 只改 `metrics.error_rate`，derived 里仍写 0 | 拒绝、0 条入库 |
| `test_demo_reference_claims_are_checked_against_raw_bytes` | 取值与字面量一起改成原文没有的数 | 拒绝、0 条入库 |
| `test_demo_unmodified_references_all_import` | 未改动 | **14 条全部正常入库** |

### 1.4 这两条用例不是空跑（已实测证明）

前两条用例的设计前提是"字面量检查仍会通过"，因此它们专门验证取值绑定。
为确认这一点，把绑定检查从脚本里摘掉后重跑：

```
FAILED tests/test_phase5_demo.py::test_demo_recorded_value_must_equal_claim_value
FAILED tests/test_phase5_demo.py::test_demo_claim_value_must_equal_recorded_value
```

摘掉即失败、装回即通过 —— 说明它们确实只在验证绑定约束。

### 1.5 一键验证链里的对应步骤

`scripts/check_reference_tamper_blocked.sh` 已扩展为跑**三类**篡改，
每类都要求"整批拒绝 + 0 条入库"：

```
✅ record-only：整批拒绝，入库 0 条
     `metrics.output_throughput_tok_s` 入库值 9999.99 与声明值 1131.55 不一致（不得只改一边）
✅ claim-only：整批拒绝，入库 0 条
     `metrics.output_throughput_tok_s` 入库值 1131.55 与声明值 9999.99 不一致（不得只改一边）
✅ both：整批拒绝，入库 0 条
核对失败（预期）：三种篡改（只改入库值 / 只改声明值 / 两者都改）均被拒绝
```

### 1.6 诚实边界（不变）

取值绑定挡的是"声明与入库脱钩"。它**不**证明：

* 该取值出现在原文表格的正确位置（只能证明它与标签相邻出现过）；
* 原文数据本身正确 —— 那是来源方的责任。

这两点在 `docs/public_data_demo.md` §3.1 与 `docs/official_coverage_gaps.md` §0bis 都已写明。

---

## 2. N2：封板验证

### 2.1 执行结果

| 项 | 基线（616a86c 报告） | 本轮实测 |
|---|---|---|
| `pytest -q` | 780 passed | **784 passed**（新增 4 条绑定反例） |
| `bash scripts/verify_all.sh` | 63 步 | **63 步，通过 63，失败 0** |
| 第一阶段历史基线 | 32 / 160 / 192 | **32 / 160 / 192 不变** |
| 复算超差 | 0 | **0**（`mismatch_count: 0`） |
| 官方候选来源逐字段核对 | 12/12 | **12/12（不一致 0 条）** |
| 敏感数据扫描 | 未发现 | **未发现**（按本地特征表 9 个特征串扫描全部已跟踪文件） |

### 2.2 复核证据

* `docs/test_run_log.txt`：本轮完整执行日志（步骤 63 项：通过 63，失败 0，
  完成时间 2026-10-16T15:35:25+08:00，sha256 见文首）。
  已重新生成并对齐本次提交，不再停留在 766 passed / 60 步的旧版本。
* 日志中保留了 `默认敏感性情景条数: 160`、`最终 pricing_result 总数: 192`、
  `原文逐字段核对通过: 12/12（不一致 0 条）`、`mismatch_count: 0` 等关键行，
  用于核对历史基线未被改动。
* 日志内的金额/比率值经 `redact_internal_values()` 脱敏（V1.2 F1），
  被替换为 `<redacted:internal>`；键名、条数、误差量级与门禁结论保留。

### 2.3 公开仓库输出检查

* 内部定价数值：扫描全部已跟踪文件（含本轮刷新的日志），**未命中**；
* `data/exports/`、`data/private/`、`data/raw/*.xlsx` 仍被 `.gitignore` 排除；
* 本轮新增的公开文件全部是**公开来源的存档或自有代码**：
  `data/raw/official/gpustack_*.html`、`data/raw/official/community_*.md`、
  `data/incoming/public_benchmark_references_20261015.json`、
  两个 `scripts/check_*.sh`。版权与使用约定见 `data/raw/official/NOTICE.md`。

### 2.4 门禁状态（未被本次改动放松）

* 正式报价自动发布：**保持禁用**（未接入可信审批系统）；
* 公开参考数据：**一律不得**成为候选的 `observed_pass`，也不进报价门禁；
* `synthetic_fixture`：仍只用于算法验证；
* GPUStack 单卡结果**没有**被认定为四卡部署的性能；社区报告的 GPU 型号
  **没有**被当作 H100 SXM（按不同型号处理，理由如实展示）。

---

## 3. 冻结声明与未闭环事项

**自本报告起，公开数据 Demo（D1–D9）冻结**：
* 不再扩大公开 Benchmark 数据源（现有 14 条足够证明导入 / 匹配 / 报告能力）；
* 不再反复修改底层导入机制 —— 除非发现新的正确性缺陷，且必须同时补反向用例。

仍然**未闭环**、需要单独处理的事项（不要因为 Demo 封板而忽略）：

1. **Public 仓库历史提交中的内部定价数据风险** —— 需要单独闭环，
   重写历史或强推必须获得明确授权，本轮未做；
2. **生产审批与正式报价发布** —— 仍依赖可信审批系统，当前为部署级禁用；
3. **目标配置（Qwen3-32B @ H100）的真实性能** —— 仍无实测，
   Demo 通过 **≠** 真实性能验收通过；这两条验收线始终分开记录。

---

## 4. 下一步（N3/N4，另起一轮）

本轮不做集成。下一项技术工作是 **NVIDIA AISimulate 独立 PoC**：
先查支持矩阵，再在独立环境跑最小实验，记录原始输出与字段映射，
最后给出"是否值得集成"的决策。若 PoC 成功，性能预测必须以
`simulated_prediction` 与实测证据严格分离；若失败，如实写明约束，不重写现有计算模块。
