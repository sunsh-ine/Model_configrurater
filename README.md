# llm-capacity-pricing

大模型部署基准数据与 Token 定价计算引擎（离线 MVP）。

**第一阶段**：从一份 Excel 部署效能表出发，规范化入库、复算旧表经济性、做变现比例情景分析，
并内置 SGLang `bench_serving` / vLLM `bench serve` 的**版本化** Benchmark 解析适配器。

**第二阶段（M12–M18）**：模型与硬件资源选型引擎——解析官方模型结构、管理 GPU SKU 规格、
计算权重与 KV Cache 显存、枚举 TP/DP 部署候选并给出**静态**资源可行性。
第二阶段**不写任何定价公式**，也**不做** TTFT/TPOT 性能预测。

**第三阶段（M19–M25）**：性能评估与 SLA 引擎——Benchmark 配置匹配、统一性能指标、
并发容量曲线、受限性能估算、SLA 判定与部署候选性能比较。
核心纪律：**匹配等级 ≠ 数据可信等级 ≠ SLA 状态**；没有匹配实测就不宣称 SLA 达标。
SLA **默认只接受 `exact_match`**（模型 revision、精度、硬件、卡数、TP/DP、框架版本、
数据集、长度、到达方式、并发全部齐备且一致）；负载不同或记录身份不全的数据
只能用于展示与建模，不得用于 SLA 证明。

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

### 第三阶段（性能与 SLA）与第四阶段（TCO 与报价门禁）

第三阶段的命令见下方命令清单；第四阶段在此基础上串起成本、收益与报价门禁：

```bash
# 1) 导入成本目录与计费规则（**仓库内的是合成示例**，不是真实报价）
python -m llm_capacity_pricing.cli import-cost-catalog \
  --file data/examples/phase4_cost_inputs.example.yaml --db data/local.db

# 2) 查看成本目录库存（类别 / 买断租赁口径 / 状态 / 缺价项）
python -m llm_capacity_pricing.cli cost-catalog --db data/local.db

# 3) 完整 TCO：一次性 / 年现金 / 折旧分开，含逐项成本分解
python -m llm_capacity_pricing.cli tco \
  --plan-file data/examples/phase4_cost_plan.example.yaml --template syn-owned-3y \
  --candidate demo-owned --hardware nvidia-h100-sxm-80gb \
  --gpu-count 8 --server-count 1 --db data/local.db

# 4) 业务需求收益情景（按请求量算，**不是**理论满载收入）
python -m llm_capacity_pricing.cli revenue-scenario \
  --demand-file data/examples/phase4_demand.example.yaml \
  --scenario syn-demand-base --db data/local.db

# 5a) 生成并持久化一条 SLA 证据（门禁只认这条记录，不认状态字符串）
python -m llm_capacity_pricing.cli record-sla-evidence \
  --candidate demo-owned --requirement '{"requirement_id":"req","ttft_p99_ms_max":400}' \
  --model SYNTHETIC-MODEL-C --hardware SYNTHETIC-GPU-C --gpu-count 8 --tp 8 --dp 1 \
  --concurrency 8 --input-len 1024 --output-len 256 --db data/local.db

# 5b) 导入人工审批记录（示例占位，真实审批由业务方提供）
python -m llm_capacity_pricing.cli import-quote-approvals \
  --file data/examples/phase4_approvals.example.yaml --db data/local.db

# 5c) 报价门禁：只给引用，服务端从记录读取并重新核验
python -m llm_capacity_pricing.cli quote-eligibility \
  --candidate demo-owned --requirement-id req \
  --sla-evaluation-id "sla::demo-owned::req" --approval-id syn-approval-demo \
  --model SYNTHETIC-MODEL-C --hardware SYNTHETIC-GPU-C --gpu-count 8 --tp 8 --dp 1 \
  --concurrency 8 --input-len 1024 --output-len 256 \
  --plan-file data/examples/phase4_cost_plan.example.yaml --template syn-owned-3y \
  --hardware-id nvidia-h100-sxm-80gb --server-count 1 --db data/local.db

# 6) 全链路决策：资源 → 性能 → TCO → 收益 → 门禁 → 比较
python -m llm_capacity_pricing.cli phase4-decision \
  --model qwen3-32b --hardware nvidia-h100-sxm-80gb --server hgx-h100-8gpu \
  --plan-file data/examples/phase4_cost_plan.example.yaml --template syn-owned-3y \
  --demand-file data/examples/phase4_demand.example.yaml --scenario syn-demand-base \
  --context-length 8192 --concurrency 32 --tp-sizes 2,4,8 --dp-sizes 1 \
  --usable-fraction 0.9 --usable-source-kind synthetic_example \
  --runtime-reserve-fraction 0.1 \
  --requirement '{"requirement_id":"req","ttft_p99_ms_max":400}' \
  --out data/exports/phase4_decision.json --db data/local.db

# 7) 方案报告（含四个独立结论 + 报价草稿或阻断说明）
python -m llm_capacity_pricing.cli phase4-report \
  --out data/exports/phase4_report.md \
  --decision-json data/exports/phase4_decision.json --db data/local.db
```

> **报价门禁不会被任何参数放宽，也不接受"状态字符串"**。四个维度
> （成本完整+币种确认、SLA 实测、人工商务确认、人工审批+凭据）必须同时通过才可能
> `quote_publishable=True`；其中 SLA 结论由服务端用**持久化证据里的需求快照与目标身份
> 重新推导**，审批必须绑定**当前成本快照指纹**并在有效期内。合成示例成本、未确认币种、
> 估算型 SLA、作用域不符或快照失配一律阻断，报告只输出阻断说明、不输出金额。
> 反向场景断言见 `scripts/check_forged_quote_blocked.py`（12 项检查）。
>
> **算法验证口径进不了正式报价**：即便第三阶段显式开了 `allow_synthetic` 并产出
> `observed_pass`，第四阶段仍有一道**不可被任何策略放宽**的生产可信度门禁——
> 拒绝 `synthetic_fixture`、`algorithm_validation_only`、`allow_synthetic` 与
> `sla_proof` 禁令。证据记录会永久保留这些质量标记。
>
> **证据可重放**：同配置多条实测聚合出的证据会保存全部成员 run_id，
> 复核时用记录里的目标身份重新匹配重放，而不是拿 `aggregate:...` 去原始表里查。
>
> **计费规则必填**：正式报价必须绑定一条**已确认且有效**的计费规则，
> 其身份还会与审批作用域比对（审批 rule-A、当前用 rule-B 直接阻断）。
> 证据指纹与成本快照都覆盖"计算依据"，而不只是数值结果 ——
> 改一个 ITL P99、换一版覆盖策略，旧证据与旧审批立即失效。
>
> ⚠️ **部署级限制**：本系统未接入可信审批系统，`import-quote-approvals` 只做持久化，
> 不校验审批人身份与权限。**不得用于生产环境的报价自动发布**；
> 正式报价必须由人工在受控流程中签发。

> **买断与租赁互斥**：不属于当前口径的成本项会被标成 `excluded_by_cost_basis`
> 并写明原因（排除**不等于**零成本）；**缺价格/缺假设**的成本项标为
> `unknown_missing_price` / `unknown_missing_basis`，使 TCO 标为 `incomplete`
> （未知**不等于**零）。
>
> **成本范围不能靠"不选"规避**：方案模板必须带 `coverage_policy`，每个适用类别
> 要么被选入、要么用 `scope_exclusions` 显式排除并给出理由，否则记为覆盖缺口。
> 覆盖策略还必须带 `authority: confirmed` 与批准人/日期/凭据——
> 草稿或未授权策略算得出数字，但会被标为不完整并阻断报价。
> **价格必须适用**：GPU SKU、区域、有效期在算金额之前校验，不通过则剔除并报明原因。

### 公开数据驱动的 Demo（无需 GPU，一条命令）

```bash
bash scripts/run_demo.sh                    # 默认输出到 data/exports/
DEMO_DB=/tmp/my.db bash scripts/run_demo.sh # 指定数据库（已存在则拒绝覆盖）
```

用**公开发布的 Benchmark** 演示完整链路：需求 → 部署候选 → 公开实测参考 → SLA 可信度
→ 成本估算 → 报告。共 14 条公开数据，来源逐个与归档原文核对：声明的字面量必须能在原文里
找到，**且入库值必须等于声明值**（只改一边也会被挡），任一条不过即整批拒绝入库：

* SGLang 官方 Cookbook 12 条（`measured_vendor_published`，按章节锚点逐字段核对）；
* GPUStack 性能实验室的 Qwen3-32B / 单张 H100 1 条（同模型同 GPU 型号 → 只能作**建模参考**）；
* 第三方社区 4×H100 报告 1 条（`measured_third_party`；卡数/TP/精度一致，但原文硬件型号写法
  与目录不同 → 按**不同型号**处理，理由如实展示，不做别名推断）。

公开数据只作**参考**：报告第 2.1 节展开最相关的 3 条，逐条给出「与目标相同的条件」与
「不能直接比较的条件」+ 来源链接 + 存档 sha256；其余见第 7 节附录与 JSON 结果。
成本为假设值，报价资格与生产发布授权一律不放行。

详见 [`docs/public_data_demo.md`](docs/public_data_demo.md)。

### 一键系统 Demo（S2：一份需求 → 一份方案文档）

```bash
bash scripts/run_system_demo.sh                     # 模式 A：离线（默认，无需 GPU/工具）
bash scripts/run_system_demo.sh --mode core         # 模式 C：不带模拟
AISIMULATE_PYTHON=/opt/aisim/bin/python \
  bash scripts/run_system_demo.sh --mode live       # 模式 B：真调 AISimulate 搜索 + P99 回放
```

一次运行完成：建库 → 资源规划 → 模拟/实测 → 成本与预算 → 推荐资格 → 两份报告。
产物写到**独立目录** `data/exports/system_demo/<UTC 时间戳>/`：

* `deployment_proposal.md` —— 7 段式方案文档（需求 / 资源 / 候选 / 性能 / 成本 / 推荐 / 风险）
* `technical_report.md` —— 技术版报告（保留 Run ID、匹配等级、sha256）
* `result.json` —— 结构化结果

可传 `--model/--hardware/--gpu/--isl/--osl/--concurrency/--ttft/--tpot/--budget` 改需求。

### 单页界面（S3，可选）

```bash
.venv/bin/pip install -r requirements-app.txt     # 只有界面需要，不并入主 lock
PYTHONPATH=src .venv/bin/streamlit run app/streamlit_app.py
```

界面只负责**输入、展示、触发**；所有计算仍走 `evaluate_plan()`，
前端没有任何 Sizing / SLA / TCO 公式。没有账号、多租户或审批后台。

### 可选：模拟预测（P1–P4，默认关闭）

在配置里加一段 `simulation`，就能让报告多出一节
「**2.2 模拟部署配置推荐（非实测）**」：给定模型与 GPU，由 NVIDIA AISimulate
搜索部署配置（TP/PP/DP/副本/部署模式）并给出**含 P99** 的性能预测。

```bash
python -m llm_capacity_pricing.cli plan \
  --config data/examples/scenario_simulation.example.yaml \
  --out data/exports/simulation_report.md \
  --json-out data/exports/simulation_result.json \
  --db data/exports/demo/demo.db
```

两种用法：

* **离线（默认）**：读仓库里归档的模拟输出，不需要 GPU、不需要安装 AISimulate；
* **自动（需工具）**：见 `data/examples/scenario_simulation_auto.example.yaml` ——
  你只填一份需求 YAML，系统自己生成搜索输入、跑扫描器、按资源过滤取 Top-N、
  再逐个生成回放输入拿到 P99，并把每步的输入/输出与 sha256 归档到 `work_dir`。
  需要 `AISIMULATE_PYTHON` 指向装了 `aisimulate` 的独立环境。

三条设计约束：

* **默认关闭**：不写 `simulation` 段时，行为与输出**逐字节不变**，也不会调用任何外部工具；
* **离线优先**：默认只读仓库里归档的模拟输出，**不需要 GPU、不需要安装 AISimulate**；
  要现场执行必须显式 `enable_external: true`（argv 列表构造，不走 shell）；
* **与实测严格隔离**：模拟指标一律 `sim_` 前缀，结论只有
  `predicted_pass / predicted_fail / insufficient_prediction`，
  **永不**写入 `BenchmarkRun` / `CapacityPoint`，**永不**产生 `observed_pass`。

推荐资格也是硬的（R1）：只有**身份明确**（模型/精度/system/后端/后端版本/工作点）、
**资源可行**（用它自己的 TP/DP 走一遍现有 Sizing）、**P99 完整**、**不超预算**的候选
才进推荐池。可用卡数上限写 `max_total_gpus`（不写就不做超限判断，也不假设等于单机卡数）；
PP/EP/PD 分离等本系统不建模的拓扑只作参考，不宣称可部署。

⚠️ 两条必须知道的边界：扫描器的单点估计**≈ 原生回放的中位数**（不是 P99，P99 可达其 13.9 倍），
而官方 SLA 过滤是**均值口径**；所以达标与否**由我们用 P99 自己判**，不采纳工具的结论。
另外官方精度快照里**没有 Qwen3-32B**，全量 MAPE 为 TTFT 45.27% / TPOT 22.39% ——
模拟值只适合排序与发现明显不可行。详见
[`docs/p_simulation_final_validation.md`](docs/p_simulation_final_validation.md)。

### 统一评估入口（S1：一份配置 + 一条命令 + 一份报告）

前面各阶段的命令都是**专业入口**；日常使用只需要这一条：

```bash
# 最小场景：只有模型、候选硬件与目标负载
python -m llm_capacity_pricing.cli plan \
  --config data/examples/scenario_minimal.example.yaml \
  --out data/exports/plan_minimal.md --db data/local.db

# 完整场景：追加 SLA / 成本 / 业务需求 / 治理引用
python -m llm_capacity_pricing.cli plan \
  --config data/examples/scenario_full.example.yaml \
  --out data/exports/plan_full.md \
  --json-out data/exports/plan_full.json --db data/local.db
```

配置项里**必需**的只有 `model_id`、`hardware_ids` 与 `workload.input_len`；
`sla` / `cost` / `demand` / `budget` / `governance` 按场景填写，
删掉哪一段就只影响对应领域（该领域标为"未配置"），其余照常出结论。
专业字段收在 `advanced` 下。

报告的四个领域**分开写**（资源规划 / 性能与 SLA / 成本与收益 / 报价资格），
并逐项标注数据性质（实测 / 估算 / 假设 / 缺失）；
本入口与既有命令**共用同一套门禁**，不存在更宽松的旁路。

### 一键复现以上全部步骤

```bash
bash scripts/verify_all.sh
```

脚本会串起建库 → 导入 → 校验 → 计算 → 情景 → 报告 → 测试，任一失败即非零退出。
一次真实执行日志见 `docs/test_run_log.txt`。

> 完整链路由 **66 步**组成，其中含第四阶段门禁断言、**S6 四场景端到端验收**
> （仅资源 / 有实测无价格 / 完整合成 / 输入错误）、**Demo（D1–D9）**
> 与两条反例断言（篡改公开参考声明必须整批拒绝、`DEMO_DB` 指向已有库必须拒绝覆盖）。

---

## 1.5 三个业务演示场景（T 阶段）

页面左侧「演示案例」提供三个预设，选择后只填充需求字段，点击生成仍走
`run_demo()` → `evaluate_plan()`：

| 预设 | 阈值 / 排序 | 预期与实测结论 |
| --- | --- | --- |
| 严格 SLA | TTFT ≤ 400 ms、TPOT ≤ 30 ms；吞吐优先 | 归档预测未达标；无推荐；给出未达标参考配置 |
| 宽松 SLA | TTFT ≤ 6000 ms、TPOT ≤ 40 ms（演示阈值）；吞吐优先 | 预测达标并推荐 TP8；预测值不变，仍无实测通过 |
| 成本与预算 | 宽松 SLA；成本优先；预算 100 万 | 成本不完整/币种未确认，成本排序降级为吞吐优先；预算未知 |

CLI 同样支持：

```bash
.venv/bin/python -m llm_capacity_pricing.app.demo_cli --list-scenarios
.venv/bin/python -m llm_capacity_pricing.app.demo_cli --scenario relaxed --out /tmp/relaxed
```

三组完整报告在 `docs/demo_scenarios/`，场景说明与成本门禁解释见
[`docs/demo_scenarios.md`](docs/demo_scenarios.md)。

## 1.6 自然语言入口（OpenCode Agent，实验性）

除结构化表单外，页面支持“自然语言（OpenCode Agent）”模式：

- 默认使用 **Command Code + 本地 MCP server**（client API 已配置）；
- 业务工具只调用现有 `evaluate_plan()`，数字不经过模型生成；
- 业务系统按 `run_id` 读取工具产物生成报告，模型文本只作解释；
- 无法调用工具、任务超时或产物不一致时**不生成报告**。

```bash
.venv/bin/pip install -r requirements-agent.txt
command-code mcp add --transport stdio --scope user \
  -e "PYTHONPATH=$PWD/src" lcp -- \
  "$PWD/.venv/bin/python" -m llm_capacity_pricing.agent.mcp_server
.venv/bin/streamlit run app/streamlit_app.py   # 左侧切换输入方式
```

OpenCode 仍作为备选：`bash scripts/start_opencode_server.sh`（v2.1.2）。

Command Code + MCP 链路已真实跑通（bwrap 沙箱 + 每任务隔离 HOME），支持缺字段确认与同一会话补充；任务结果强绑定
`run_id` 与 `result.json` 的 sha256，Agent 会话异常与报告状态分开。OpenCode 仍有
serve free-tier 与 `.opencode/tools` 暴露问题。详见 `docs/commandcode_integration.md`
与 `docs/w_final_validation.md`。详见 [`docs/v_opencode_integration.md`](docs/v_opencode_integration.md)
与 [`docs/v_final_validation.md`](docs/v_final_validation.md)。

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
| `import-performance --file <json> --metadata <yaml> \| --curve-index <INDEX.json> [--kind vllm\|sglang]` | **M20**：导入性能压测记录（离线，无需 GPU） |
| `match-benchmark --model <名> --hardware <型号> …` | **M19**：按配置与负载匹配已有 Benchmark，输出等级与 reason codes |
| `capacity-profile [--target-concurrency N] [--exclude-synthetic]` | **M21**：并发容量与性能曲线 |
| `calibrate-performance --metric <名> --concurrency N [--allow-synthetic]` | **M22**：精确查询 / 范围内插值（附留出误差，≥3 个有效留出点）/ 不可预测。默认拒绝 synthetic |
| `evaluate-sla --requirement <json> --run-id <id> [--model-revision/--framework-version/--precision/--dataset …]` | **M23**：SLA 判定。**默认只接受 `exact_match`**，且拒绝 synthetic 与不匹配数据 |
| `rank-deployments --model <id> --hardware <id> --requirement <json> …` | **M24**：候选性能比较（静态筛选 + 性能证据） |
| `plan --config <yaml> [--out <md>] [--json-out <json>]` | **S1**：统一评估入口（资源 / 性能 / 成本 / 治理四领域） |
| `performance-report --out <md>` | **M25**：第三阶段 Markdown 报告 |
| `import-cost-catalog --file <yaml> --db <path>` | **M27**：导入成本目录（GPU/机房/电力/网络/软件/运维）与计费规则 |
| `cost-catalog --db <path>` | **M27**：成本目录库存（类别/口径/状态/缺价项） |
| `data-quality --db <path> [--out <json>]` | **M36**：数据质量分级（字段完整/缺失/不适用/Synthetic/未验证）+ 可执行补数据指引 |
| `plan-benchmarks --model <id> --hardware <sku> … [--batch-id <id>] [--out <json>] [--metadata-dir <dir>]` | **M37**：生成压测实验清单与 metadata 模板（不含性能数值；跨批次身份独立） |
| `check-import --db <path> [--run-ids <ids>] [--out <json>]` | **M38**：导入一致性校验（口径混用/并发语义/请求账目/缓存口径/重复 Run） |
| `attest-run --run-id <id> --confirmed-by <人> --raw-sha256 <sha>` | **加固**：显式核验现场实测，通过后才赋予测量类可信等级 |
| `validate-site --requirement <json> --model <名> --hardware <型号> --gpu-count N --tp N …` | **M39**：现场性能与 SLA 可信度验证（容量/匹配/逐子项/不可评价原因） |
| `import-commercial-confirmations --file <yaml> --db <path>` | **M30**：导入**人工**商务确认记录（本命令不生成确认） |
| `record-sla-evidence --candidate <id> --requirement <json> --model <名> --hardware <型号> --gpu-count N --tp N …` | **V4.1 F4.1**：跑一次 SLA 评估并**持久化**为可引用的证据记录 |
| `import-quote-approvals --file <yaml> --db <path>` | **V4.1 F4.1**：导入**人工**审批记录（本命令不生成审批） |
| `tco --plan-file <yaml> --template <id> --candidate <id> --hardware <id> --gpu-count N --server-count N` | **M28**：完整 TCO、成本分解与现金流 |
| `revenue-scenario --demand-file <yaml> --scenario <id>` | **M29**：业务请求量 → Token 计费与收益情景（含容量比对） |
| `quote-eligibility --candidate <id> --requirement-id <id> --sla-evaluation-id <id> --approval-id <id> [--confirmation <id> --plan-file … --template …]` | **M30/V4.1**：四维报价门禁（只接受证据引用，服务端重新核验） |
| `phase4-decision --model <id> --hardware <id> --plan-file <yaml> --template <id> …` | **M31/M32**：资源-性能-TCO-收益-门禁全链路 + 比较/敏感性/盈亏平衡 |
| `phase4-report --out <md> [--decision-json <json>]` | **M33**：第四阶段报告（四个独立结论 + 报价草稿或阻断说明） |

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
│   ├── perf/                              # 第三阶段：性能评估
│   │   ├── benchmark_matcher.py           # M19 签名/匹配等级/reason codes
│   │   ├── performance_profile.py         # M20 压测记录与统一指标
│   │   ├── capacity_analyzer.py           # M21 容量曲线
│   │   ├── estimator.py                   # M22 受限估算与留出验证
│   │   ├── sla_evaluator.py               # M23 SLA 判定
│   │   └── ranker.py                      # M24 候选比较
├── tests/                                 # 886 个用例（2 个需要可选依赖，缺则 skip）
├── scripts/verify_all.sh                  # 一键复现（66 步，含门禁断言、Demo、系统 Demo 与敏感扫描）
├── scripts/make_synthetic_perf_fixture.py # 生成合成性能曲线（仅算法验证）
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
| 官方来源存档证据 | `data/raw/official/`（13 HTML + 2 MDX + 1 MD，含 sha256） | ✅ | 公开文档的存档，版权见 `NOTICE.md` |
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

**第三阶段已实现：** Benchmark 配置匹配（四等级 + reason codes，跨模型/跨硬件一律拒绝）、
统一性能指标与版本化导入（vLLM/SGLang，幂等、离线）、并发容量曲线（`best_observed_point`
≠ `maximum_sustainable_capacity`）、受限性能估算（`measured_reference` /
`calibrated_estimate` 附留出误差 / `not_predictable`）、SLA 判定（四类状态 + 逐项约束 +
请求级 Goodput）、部署候选性能比较、JSON/Markdown 报告。**56 个第三阶段测试**。

**第三阶段未实现（边界内不做）：**

- 按 GPU FLOPS 推算 TTFT/TPOT；PP/EP/PD 分离的性能模型（字段保存并阻断）；
- 跨模型/跨硬件的通用性能预测器（只留 `PerformanceModel` 接口）；
- 跨 TP/DP 的每卡吞吐外推；P99 的插值；
- 把预测值当作已实测通过的 SLA；把静态可行当作性能达标；
- 未确认价格前的经济性评分。

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
| [`docs/phase3_final_validation.md`](docs/phase3_final_validation.md) | 第三阶段验证报告（工程功能 / 性能可信度两个维度） |
| [`docs/refactor_baseline.md`](docs/refactor_baseline.md) | **S0** 重构基线冻结（接口 / 数据 / 测试） |
| [`docs/plan_config_reference.md`](docs/plan_config_reference.md) | **S1/S5** `plan` 配置字段说明与报告导读 |
| [`docs/extension_guide.md`](docs/extension_guide.md) | **S4** 三个扩展方向的开发指南（数据 / Benchmark 适配 / 计算规则） |
| [`docs/real_data_preparation.md`](docs/real_data_preparation.md) | **M35–M38** 核心数据契约冻结 + 真实数据准备规范、质量分级与压测清单 |
| [`docs/public_data_demo.md`](docs/public_data_demo.md) | **Demo** 公开数据驱动的部署方案评估（D1–D9，含数据来源取舍、匹配口径与报告导读） |
| [`docs/demo_v1_1_final_validation.md`](docs/demo_v1_1_final_validation.md) | **Demo V1.1 封板**（N1 取值绑定加固 + N2 验证结果与冻结声明） |
| [`docs/aisimulate_poc.md`](docs/aisimulate_poc.md) | **N3/N4** NVIDIA AISimulate 独立 PoC（支持矩阵、离线实验、口径警告、字段映射与集成决策） |
| [`docs/p_simulation_final_validation.md`](docs/p_simulation_final_validation.md) | **P1–P4 封板** 模拟预测接入（口径纠正、数据契约、可选适配器、反向测试与准确性边界） |
| [`docs/v7_r1_r2_validation.md`](docs/v7_r1_r2_validation.md) | **V7.0** 推荐正确性加固（R1）+ 自动搜索与 P99 回放（R2）验收 |
| [`docs/v7_final_validation.md`](docs/v7_final_validation.md) | **V7.0 封板**（F6-1 币种门禁、F6-2 运行目录隔离、全量回归与冻结结论） |
| [`docs/system_architecture.md`](docs/system_architecture.md) | **S1** 系统总体架构（组件职责、边界、已实现/未实现对照） |
| [`docs/system_design.md`](docs/system_design.md) | **S1** 契约、状态、失败处理与运行方式 |
| [`docs/system_demo_validation.md`](docs/system_demo_validation.md) | **S2–S5** 一键 Demo、单页界面与验收记录 |
| [`docs/n5_usability_test_kit.md`](docs/n5_usability_test_kit.md) | **N5** 易用性验收总览（参与者任务单与评分指南已分开存放） |
| [`docs/n6_environment_report.md`](docs/n6_environment_report.md) | **N6-1** compute01 只读环境检查（含最小测试方案与待批事项） |
| [`docs/phase5_final_validation.md`](docs/phase5_final_validation.md) | 第五阶段验证报告（真实数据接入与方案交付验证） |
| [`docs/simple_architecture_validation.md`](docs/simple_architecture_validation.md) | **S 阶段**架构收敛进度与封板清单 |
| [`docs/phase4_baseline.md`](docs/phase4_baseline.md) | **M26** 第三阶段接口冻结与第四阶段基线 |
| [`docs/phase4_interface_design.md`](docs/phase4_interface_design.md) | **M26** 第四阶段模块划分、接口签名与禁止事项 |
| [`docs/phase4_final_validation.md`](docs/phase4_final_validation.md) | 第四阶段验证报告（工程功能 / 商业数据 / SLA 实测 / 报价发布四个独立结论） |
