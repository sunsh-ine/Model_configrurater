# 公开数据驱动的 Demo（D1–D5）

**定位**：不部署 GPU、不重新压测，用**已公开的官方 Benchmark** 证明这条链路可用：

> 用户给出模型、GPU、Workload 与 SLA → 系统利用已有数据 → 生成一份
> 完整、清晰、可追溯的部署方案报告。

**一条命令**（无需 GPU、无需联网，来源文件已归档在仓库内）：

```bash
bash scripts/run_demo.sh              # 默认输出到 data/exports/
bash scripts/run_demo.sh /tmp/demo    # 或指定目录
```

---

## 一、Demo 展示两种使用模式（**互不混淆**）

| 模式 | 数据 | 能展示什么 |
|---|---|---|
| **公开实测参考模式** | SGLang 官方 Cookbook 发布结果（`measured_vendor_published`） | 真实公开的吞吐 / TTFT P99 / TPOT P99、真实部署配置，以及**为什么它们不能直接用于你的需求** |
| **完整流程演示模式** | 目标配置 + 假设成本（`synthetic_example`） | 从需求 → 候选 → 资源规划 → 成本估算 → 报告 的完整软件工作流 |

报告里这两部分是**分开的章节**：候选方案在第 2 节，公开参考在第 2.1 节，并明确写着
"**不参与**本需求的性能判定"。

---

## 二、数据来源（D1）与取舍记录

### 2.1 采用：SGLang 官方 Qwen3 Cookbook

| 项 | 值 |
|---|---|
| 来源 | `sgl-project/sglang` 官方仓库 Cookbook |
| 归档文件 | `data/raw/official/sglang_cookbook_qwen3_2026-10-15.mdx`（sha256 `23d8e61c…`）<br>`data/raw/official/sglang_cookbook_qwen3_coder_2026-10-15.mdx`（sha256 `67ca6704…`） |
| 上游 Git blob | `580640bd…` / `a743f7ba…`（与本地快照哈希是两件事，分别记录） |
| 记录数 | 12 条（9 条 Qwen3-235B-A22B @ 8×B200，3 条 Qwen3-Coder-480B @ 8×MI300X） |
| 指标 | TTFT P99、TPOT P99/Mean、ITL、E2E、输入/输出/总吞吐、请求吞吐、时长、成功数 |
| 负载条件 | 数据集、ISL/OSL 目标长度、配置并发、请求到达方式、请求数 |
| 明确缺失 | **模型 revision**、完整环境 pin（部分记录缺权重精度） |

**为什么选它**：字段最全、由框架项目官方发布、来源快照可校验（`sha256` + 逐字段核对原文），
正好能演示"公开数据能支撑什么、不能支撑什么"。

### 2.2 评估后**未采用**的来源（如实记录）

| 来源 | 结论 | 原因 |
|---|---|---|
| GPUStack Performance Lab — Qwen3-32B / H100 | **未导入** | 页面只公布 `Total TPS` 与 `Mean TPOT`，**没有 TTFT、没有 P99、没有配置并发**，且 "Total TPS" 是输入+输出合计。把它当成 `output_throughput_tok_s` 会是口径错配，因此宁可不导入 |
| vLLM Performance Dashboard | **未使用** | 归档页面未包含可下载的结构化结果；其任务产物需要另行确认可公开下载 |
| 仓库内 `synthetic_fixture` 曲线 | **本 Demo 不使用** | 只用于算法验证，不得作为性能证据（隔离规则见第五阶段验证报告） |

> 这三条取舍本身就是产品能力的一部分：**系统知道什么有依据、什么只是参考、什么不能用。**

### 2.3 公开数据与目标配置不匹配怎么办？

公开数据是 **235B-A22B / B200** 与 **Coder-480B / MI300X**；
本仓库第二阶段支持的 SKU 只有 **H100 SXM / H200 SXM**（B200 的官方规格页尚未采集，
条目保持 `null` 且 `supported_in_phase2: false` —— 不推断）。

因此 Demo 采用：**目标 = Qwen3-32B @ H100（可生成候选）**，
**公开数据 = 外部参考（明确标注不可用于本需求）**。报告会逐条说明原因，例如：

* 公开来源未给出模型版本（revision）：无法确认与你的模型版本一致；
* 公开来源未给出配置并发：无法与你的负载并发对齐；
* 模型不同（公开数据为 `Qwen/Qwen3-235B-A22B-Instruct-2507`）：不能代表你要部署的模型。

---

## 三、怎么导入（D2）

```bash
python scripts/import_public_benchmarks.py --db data/local.db
```

这是一个**最小格式转换脚本**，不是通用适配框架：

1. 复用既有的 `build_numeric_candidates()`：**来源快照 sha256 校验 + 逐字段核对原文**，
   校验失败直接中止、不产出任何记录；
2. 候选对象里的 `metrics` 已经是**规范化字段名**，脚本只做搬运，**没有任何换算或映射**；
3. 缺失字段一律留空（`model_revision` / 部分记录的精度），并登记 `missing:<字段>` 标记；
4. 可信等级为 `measured_vendor_published` —— 由**发布方**背书，
   与 `internal_measurement`（自我声明、需 `attest-run` 核验）是两条不同的路径。

**验收用例**（`tests/test_phase5_demo.py`）：

* `test_demo_public_benchmarks_import_with_source_verification`：12 条入库，
  每条都有 `raw_file` + `raw_sha256` + 上游 blob sha；
* `test_demo_no_fabricated_fields`：**字段值与 `missing:` 标记必须一致** ——
  写了的就是来源写了的，没写的一律为空（这条断言专门用来抓"补造缺失值"）。

---

## 四、Demo 配置（D3）

`data/examples/demo_qwen3_32b_h100.yaml`：Qwen3-32B + H100 SXM，
负载 8K/256、并发 32，SLA 要求 TTFT P99 ≤ 400ms、TPOT P99 ≤ 30ms，
成本引用**假设**成本目录（`synthetic_example`，明确标记不是真实报价），
预算 300 万。

---

## 五、报告读什么（D4）

```bash
python -m llm_capacity_pricing.cli plan \
  --config data/examples/demo_qwen3_32b_h100.yaml \
  --out data/exports/demo_report.md \
  --json-out data/exports/demo_result.json \
  --db data/local.db
```

报告结构：

| 章节 | 内容 |
|---|---|
| 1. 结论摘要 | 四个领域状态；**技术资格（四维门禁）** 与 **生产发布授权** 分开陈述 |
| 2. 候选方案 | 卡数 / TP×DP / 静态可行性 / 性能数据性质 / 成本口径 / 预算 / 报价 |
| **2.1 公开数据参考** | 公开实测的指标表 + **每条为什么不能用于你的需求** + 来源文件与 sha256 |
| 3. 推荐依据 | 可解释的比较结论（不做跨维度综合评分） |
| 4. 缺口与下一步 | 阻断原因、缺少的输入、建议补什么 |
| 5. 结论边界 | 本次结论不能用来做什么 |
| 6. 附录 | Run ID、匹配等级、逐项成本分解 |

**明确不给的结论**（Demo 定位决定）：

* 候选的 SLA 状态是"证据不足" —— **不要求** `exact_match`，也不臆造成"达标"；
* 成本是假设值 → 成本结论不完整 → **不给**全周期总额；
* 报价资格一律不放行，**生产发布授权保持"未授权"**。

---

## 六、可重复性（D5）

* `scripts/run_demo.sh`：6 步走完全流程，任一步失败即非零退出；
* `tests/test_phase5_demo.py`：6 个用例，含"一键脚本在无 GPU 环境跑通"的端到端检查；
* 无需 GPU、无需联网：公开来源文件随仓库归档，导入时逐个校验 sha256。

---

## 七、这个 Demo 不声称什么

1. 不声称目标配置（Qwen3-32B @ H100）的性能已实测 —— 那是**现场压测**才能给出的结论；
2. 不声称公开数据的数值可以外推到你的配置上；
3. 不声称成本是真实价格，也不产出正式报价；
4. 不把 `synthetic_fixture` 数据混进任何性能结论。

**Demo 通过 ≠ 真实性能验收通过。** 这两条验收线在第五阶段验证报告里始终分开记录。
