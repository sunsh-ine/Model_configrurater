# 公开数据驱动的 Demo（D1–D5，V1.1 起为 D6–D9）

**定位**：不部署 GPU、不重新压测，用**已公开的 Benchmark** 证明这条链路可用：

> 用户给出模型、GPU、Workload 与 SLA → 系统利用已有数据 → 生成一份
> 完整、清晰、可追溯的部署方案报告。

**一条命令**（无需 GPU、无需联网，来源文件已归档在仓库内）：

```bash
bash scripts/run_demo.sh              # 默认输出到 data/exports/
bash scripts/run_demo.sh /tmp/demo    # 或指定目录
DEMO_DB=/tmp/my.db bash scripts/run_demo.sh   # 指定数据库（见 §六）
```

---

## 一、Demo 展示两种使用模式（**互不混淆**）

| 模式 | 数据 | 能展示什么 |
|---|---|---|
| **公开实测参考模式** | SGLang 官方 Cookbook 发布结果 + 两条更贴近目标的公开参考（`measured_vendor_published` / `measured_third_party`） | 真实公开的吞吐 / TTFT P99 / TPOT P99、真实部署配置，以及**为什么它们不能直接用于你的需求** |
| **完整流程演示模式** | 目标配置 + 假设成本（`synthetic_example`） | 从需求 → 候选 → 资源规划 → 成本估算 → 报告 的完整软件工作流 |

报告里这两部分是**分开的**：候选方案在第 2 节，其它公开参考在第 2.1 节，并明确写着
"**不参与**本需求的性能判定"。被候选当作**建模参考**采用的那条，会出现在第 2 节的
候选附录里，并在旁边写明"**非本配置实测**"+ 来源链接。

报告开头的**五层数据性质表**（静态资源可行 / 公开性能参考 / 已验证 SLA / 假设成本 /
正式报价资格）给出"这些数字各是什么性质"的统一口径，正文不再混用。

---

## 二、数据来源（D1、D7）与取舍记录

### 2.1 采用：SGLang 官方 Qwen3 Cookbook（12 条）

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

### 2.1bis 采用：两条更贴近目标的公开参考（D7，各 1 条）

原来的 12 条是 235B/B200 与 480B/MI300X —— 与目标（Qwen3-32B @ H100）差得太远，
报告虽然正确，但读者看不出"这个系统真能找到接近的参考"。因此补入两条：

| 来源 | 归档文件 / sha256 | 模型 / 硬件 / 负载 | 可信等级 | 结论 |
|---|---|---|---|---|
| GPUStack 性能实验室（2.0 版文档）`docs.gpustack.ai/2.0/performance-lab/qwen3-32b/h100/` | `data/raw/official/gpustack_perf_lab_qwen3_32b_h100_2026-10-15.html`（`7abb8417…`） | Qwen3-32B / **单张 H100 SXM** / ShareGPT，vLLM 0.11.0；TTFT P99 153161.51ms、TPOT P99 378.18ms、输出吞吐 1131.55 tok/s | `measured_vendor_published` | **同模型同 GPU 型号**，但只有 1 卡 → 匹配等级 `modeling_candidate`，被候选当作**建模参考**（报告里标注"非本配置实测"） |
| MG-Cafe 社区实验报告 `github.com/MG-Cafe/TPU_V6e_H100_Benchmarking_Qwen` | `data/raw/official/community_qwen3_32b_h100_4gpu_report_2026-10-15.md`（`422eec41…`，blob `7a270d67…`） | Qwen3-32B **bf16** / 4×H100 80GB HBM3 / TP4 / random 31400→128，vLLM 0.17.0；TTFT P99 359749.43ms、TPOT P99 154.03ms、输出吞吐 69.87 tok/s | `measured_third_party` | 卡数与 TP 与目标候选一致、精度一致，但原文 GPU 型号写的是「H100 80GB HBM3」而非 SXM → 本工具不做别名推断，按**不同型号**处理（`rejected`，理由如实展示） |

> 这两条的数字**不是**本文档转抄的：文档里的数值与 JSON 里的 claims 都在导入时
> 逐字段与归档原文核对过（见 §三）。原文没给的字段（revision、TP/DP、配置并发）
> 一律留空，含「Peak concurrent requests 1000.00」也只存为**观测**并发。

### 2.2 评估后**未采用**的来源（如实记录）

| 来源 | 结论 | 原因 |
|---|---|---|
| GPUStack 性能实验室（**最新版**文档页面） | **未导入** | 当时检查的版本只公布 `Total TPS` 与 `Mean TPOT`，**没有 TTFT、没有 P99、没有配置并发**，且 `Total TPS` 是输入+输出合计。把它当成 `output_throughput_tok_s` 会是口径错配 —— 因此宁可不导入。**2.0 版页面**字段齐全，已按 §2.1bis 采用 |
| vLLM Performance Dashboard | **未使用** | 归档页面未包含可下载的结构化结果；其任务产物需要另行确认可公开下载 |
| 仓库内 `synthetic_fixture` 曲线 | **本 Demo 不使用** | 只用于算法验证，不得作为性能证据（隔离规则见第五阶段验证报告） |

> 这些取舍本身就是产品能力的一部分：**系统知道什么有依据、什么只是参考、什么不能用。**

### 2.3 公开数据与目标配置不匹配怎么办？

公开数据主要是 **235B-A22B / B200** 与 **Coder-480B / MI300X**（另有 §2.1bis 的两条）；
本仓库第二阶段支持的 SKU 只有 **H100 SXM / H200 SXM**（B200 的官方规格页尚未采集，
条目保持 `null` 且 `supported_in_phase2: false` —— 不推断）。

因此 Demo 采用：**目标 = Qwen3-32B @ H100（可生成候选）**，
**公开数据 = 外部参考（逐条说明为什么不能用于本需求）**。报告从 D6 起不再自己判断匹配，
而是**复用第三阶段的 Benchmark Matcher**，因此原因覆盖完整维度：

* 模型不同 / GPU 型号不同 / 卡数不同 / TP-DP 不同 / 框架或版本不同 / 负载长度不同 / 并发不同；
* 记录侧缺什么（"公开来源未给出模型版本（revision）…"）；
* 目标侧缺什么（"你的目标需求也未给出推理框架…"，指向第 4 节的补数据建议）。

**口径统一**：比较用的是模型的**规范化 repo_id**（`Qwen/Qwen3-32B`），
不是商业 `model_id`（`qwen3-32b`）—— 否则同模型的公开数据会被误报成"模型不同"。

---

## 三、怎么导入（D2、D7）

```bash
python scripts/import_public_benchmarks.py --db data/local.db
```

这是一个**最小格式转换脚本**，不是通用适配框架。它处理两类来源：

1. **SGLang Cookbook 候选**（12 条）：复用既有的 `build_numeric_candidates()` ——
   **来源快照 sha256 校验 + 按章节锚点逐字段核对原文**，校验失败直接中止；
2. **公开参考**（2 条，D7）：`--references` 指定
   `data/incoming/public_benchmark_references_20261015.json`，按**逐字段字面量核对**
   （claims）接入 —— 每个入库取值都要声明一段词序列，能在归档原文里按顺序找到才收。
   没声明、核对不上、或"有值但既无 claim 也无 derived 说明"→ **拒绝整批入库**。

两条路径共同遵守：

* 缺失字段一律留空（如 `model_revision`、GPUStack 的 TP/DP），并登记 `missing:<字段>` 标记；
* 无法用字面量核对但确实要记的取值（如由 `Request Rate ∞` 推出的 `arrival_mode`）
  必须写进 `derived_fields` 并给理由，导入后带 `derived:<字段>:<理由>` 标记；
* 可信等级按发布方判定：官方项目/厂商 → `measured_vendor_published`；
  第三方社区报告 → `measured_third_party`（低于厂商）。两者都与
  `internal_measurement`（自我声明、需 `attest-run` 核验）是不同路径。

**验收用例**（`tests/test_phase5_demo.py`）：

* `test_demo_public_benchmarks_import_with_source_verification`：14 条入库，
  每条都有 `raw_file` + `raw_sha256` + 来源链接；git 托管的来源有上游 blob sha；
* `test_demo_reference_claims_are_checked_against_raw_bytes`：**篡改一个数值声明就整批拒绝**；
* `test_demo_no_fabricated_fields`：**字段值与 `missing:` 标记必须一致** ——
  写了的就是来源写了的，没写的一律为空（这条断言专门用来抓"补造缺失值"）；
* `test_reference_records_keep_unknown_fields_empty`：未声明的 TP/DP/长度/并发保持为空，
  且观测并发不写进配置并发。

### 3.1 已知限制（不假装更强）

字面量核对验证的是"该取值与标签**确实在原文中出现且相邻**"，
**不**证明它出现在表格里的正确位置，也**不**证明原文数据本身可信。
它挡住的是"把数字写错、写多、写少"，挡不住"原文自己就错"。

---

## 四、Demo 配置（D3）

`data/examples/demo_qwen3_32b_h100.yaml`：Qwen3-32B + H100 SXM，
负载 8K/256、并发 32，SLA 要求 TTFT P99 ≤ 400ms、TPOT P99 ≤ 30ms，
成本引用**假设**成本目录（`synthetic_example`，明确标记不是真实报价），
预算 300 万。

---

## 五、报告读什么（D4、D8）

```bash
python -m llm_capacity_pricing.cli plan \
  --config data/examples/demo_qwen3_32b_h100.yaml \
  --out data/exports/demo_report.md \
  --json-out data/exports/demo_result.json \
  --db data/local.db
```

报告结构（D8 后）：

| 章节 | 内容 |
|---|---|
| 1. 结论摘要 | 四个领域状态；**技术资格（四维门禁）** 与 **生产发布授权** 分开陈述；**五层数据性质表** |
| 2. 候选方案 | 卡数 / TP×DP / 静态可行性 / 性能数据性质 / **证据匹配等级** / 成本口径 / 预算 / 报价 |
| **2.1 公开数据参考** | **只展开最相关的 3 条**：每条给出「**与目标相同的条件**」与「**不能直接比较的条件**」+ 来源链接 + 存档 sha256；所有条目共有的缺口只写一次 |
| 3. 推荐依据 | 可解释的比较结论（不做跨维度综合评分） |
| 7. 附录：其余公开参考数据 | 其余条目一行一条 + 来源链接，完整原因清单在 JSON 里 |
| 6. 附录：领域明细 | Run ID、匹配等级、逐项成本分解；**借用了非本配置数据时在这里标注来源** |

正文只回答"最接近的几条差在哪"；完整 14 条明细留在 JSON 与第 7 节附录。
排序口径（匹配等级 → 与目标相同的条件数 → 输入长度接近程度）只影响**展示顺序**，
不改动任何匹配结论；比较基准取候选方案中与该条数据**差异最小**的一个，
因此列出的是「最有利情况下」仍然存在的差异。

**明确不给的结论**（Demo 定位决定）：

* 候选的 SLA 状态是"证据不足" —— **不要求** `exact_match`，也不臆造成"达标"；
  部分匹配的公开数据一律**不得**升级为目标配置的 SLA 证据；
* 成本是假设值 → 成本结论不完整 → **不给**全周期总额；
* 报价资格一律不放行，**生产发布授权保持"未授权"**。

---

## 六、可重复性（D5、D6、D9）

* `scripts/run_demo.sh`：6 步走完全流程，任一步失败即非零退出；
* **数据库不会被误删**：默认位置（`<输出目录>/demo.db`）由脚本自己清理；
  用 `DEMO_DB=/path/to/db` 指定**你自己的**库时，脚本不删除它 ——
  文件已存在就直接 `exit 2` 并说明原因，需要你换路径或先自行处理；
* `tests/test_phase5_demo.py`：20 个用例，含"一键脚本在无 GPU 环境跑通"的端到端检查、
  篡改声明必须整批拒绝、以及 `DEMO_DB` 指向已有库时必须拒绝运行；
* 无需 GPU、无需联网：公开来源文件随仓库归档，导入时逐个校验 sha256。

---

## 七、这个 Demo 不声称什么

1. 不声称目标配置（Qwen3-32B @ H100）的性能已实测 —— 那是**现场压测**才能给出的结论；
2. 不声称公开数据的数值可以外推到你的配置上（因此匹配等级一律如实展示）；
3. 不声称成本是真实价格，也不产出正式报价；
4. 不把 `synthetic_fixture` 数据混进任何性能结论；
5. 不声称两条新来源是官方的：GPUStack 那条是厂商自测（`measured_vendor_published`），
   社区报告那条是**第三方**（`measured_third_party`），报告里按等级分别标注；
6. 不声称字面量核对能证明原文正确 —— 它只证明"数字与标签确实出自这份归档原文"。

**Demo 通过 ≠ 真实性能验收通过。** 这两条验收线在第五阶段验证报告里始终分开记录。
