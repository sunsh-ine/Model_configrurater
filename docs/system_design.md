# 系统设计说明（S1）

配套：[`system_architecture.md`](system_architecture.md)（结构图与职责边界）。
本文件讲**契约、状态、失败处理与运行方式**，都是代码里已经存在的语义。

---

## 1. 统一契约

### 1.1 `PlanRequest`（唯一输入）

必填：`model_id`、`hardware_ids`、`workload.input_len`。
按需填写（缺就不算，不补默认值）：

| 段 | 字段 | 缺省时的行为 |
|---|---|---|
| `workload` | `output_len` / `concurrency` / `dataset` / `arrival_mode` | 负载匹配信息不完整 → 不会 `exact_match` |
| `resources` | `usable_fraction` / `runtime_reserve_fraction`（需声明来源性质） | 用保守默认并标注假设 |
| `sla` | `ttft_p99_ms_max` / `tpot_p99_ms_max` | 性能领域"未配置" |
| `cost` | `plan_file` / `template_id` / `region` / `as_of` | 成本领域"未配置" |
| `budget` | `amount` / `kind` / `currency` | 不做预算比对 |
| `demand` | 需求场景（收入测算用） | 不算收入 |
| `governance` | `sla_evaluation_id` / `approval_id` / `confirmation_id` / `billing_rule_id` | 报价资格"未配置" |
| `advanced` | `tp_sizes` / `dp_sizes` / `weight_precision` / `framework(_version)` | 精度未知 → 模拟候选不可比 |
| **`max_total_gpus`** | 用户可用卡数上限 | **不做超限判断，也不假设等于单机卡数** |
| `simulation` | 模拟数据源（离线归档或自动搜索） | 不调用任何外部工具 |

### 1.2 `PlanResult`（唯一输出）

| 字段 | 含义 |
|---|---|
| `status` | `ok` / `partial` / `blocked` —— **只表示"这次评估产出了什么"**，不是报价结论 |
| `domains` | 四个领域的独立状态：`computed` / `not_configured` / `missing_data` / `blocked` / `error` |
| `candidates` | 每个部署候选的四领域并列视图（静态/性能/成本/资格） |
| `external_references` | 公开实测参考（不参与 SLA 判定） |
| `simulation` | 模拟预测（可选；**不参与** `status` 汇总，否则"没开模拟"会把每次评估降级成 partial） |
| `blockers` / `missing_inputs` / `next_data_needed` | 缺口与下一步 |
| `recommendations` / `limitations` | 可解释结论与边界 |

### 1.3 三套取值不可混用

| 层 | 取值 | 谁产生 |
|---|---|---|
| 匹配等级 | `exact_match` / `comparable_reference` / `modeling_candidate` / `rejected` | `perf/benchmark_matcher.py` |
| 实测 SLA | `observed_pass` / `observed_fail` / `estimated_only` / `not_evaluable` | `perf/sla_evaluator.py` |
| 模拟结论 | `predicted_pass` / `predicted_fail` / `insufficient_prediction` | `schemas/simulation.py` |

**只有 `observed_*` 能说"实测达标"**；`predicted_*` 只能出现在"模拟"章节。

## 2. 模拟预测的接入方式

### 2.1 两种数据源

| 模式 | 触发 | 需要什么 |
|---|---|---|
| **离线归档**（默认） | `simulation.sweeper_csv` / `native_prediction` | 只需要仓库里的归档文件；无 GPU、无工具 |
| **自动搜索** | `simulation.auto_search: true` + `enable_external: true` | 独立环境里的 AISimulate（`AISIMULATE_PYTHON`） |

### 2.2 自动搜索的两段式

```
需求 → 生成搜索输入(exp.yaml) → 扫描器 → 身份/资源过滤 → 对齐检查
     → Top-N(≤3) → 生成 predict 输入 → 原生回放 → 关联回搜索工作点
```

* **搜索约束**：GPU 数、TP/PP/DP/MoE/CP、后端与版本、ISL/OSL、数据库模式；
* **结果过滤**：目标并发（AISimulate 的搜索空间**没有**并发维度）；
* **缺口径**：扫描器给稳态单点（≈ 原生中位数），原生回放给 P99 —— 只认 P99 判达标；
* 每次运行写 `work_dir/runs/<run_id>/`，内含输入/输出与 `manifest.json`（含 sha256）。

### 2.3 推荐资格（全部满足才进推荐池）

1. 身份明确：模型、**精度**、system、后端、**后端版本**、负载、并发；
2. 资源可行：用该候选自己的 TP/DP 走一遍现有 Sizing；
3. P99 完整：有 `sim_p99_*` 且样本数 ≥ 30；
4. 不超预算：`max_total_gpus` 与预算（成本不完整/币种未确认 → `unknown`，不算"没超"）。

不满足就降级：`recommended()` 只在 `predicted_pass` 里挑；全不达标时给
`best_effort()`，报告标题写「**未达标参考配置（模拟）**」。

## 3. 失败处理原则

| 情形 | 行为 |
|---|---|
| 某个领域缺输入 | 该领域 `not_configured`，其余照常；缺口写进 `missing_inputs` |
| 库里缺数据 | 该领域 `missing_data` + `next_data_needed` |
| 单个候选算不出成本 | 只影响该候选（`incomplete` + 原因），不影响其它候选 |
| 模拟工具未安装 | 离线模式不受影响；自动模式返回 `not_configured`/`error` 并说明 |
| 单个回放超时/失败 | 写 `manifest.errors`，**继续**下一个候选 |
| 输出目录已存在 | 每次运行独立目录，不需要覆盖旧结果 |

**统一原则：不知道就写不知道**；不用默认值、不用 0、不用均值顶替。

## 4. Demo 运行方式

| 模式 | 命令 | 数据 |
|---|---|---|
| A 离线（默认） | `bash scripts/run_system_demo.sh` | 归档的公开 Benchmark + 归档的模拟输出；无 GPU、无网络 |
| B 实时模拟 | `bash scripts/run_system_demo.sh --mode live` | 真调 AISimulate 做搜索与 P99 回放（需独立 venv） |
| C 仅核心（无模拟） | `bash scripts/run_system_demo.sh --mode core` | 只有资源/实测/成本/资格，不出现模拟章节 |

共同约定：

* 结果写到独立目录 `data/exports/system_demo/<UTC 时间戳>/`；
* 数据库建在该目录下，**不覆盖** `data/local.db` 或任何用户库；
* 不修改任何已归档的 Benchmark 原始文件；
* 任一阶段失败会打印明确原因，但**预测不达标仍然照常出报告**。

## 5. 扩展点（S4 边界）

| 想加什么 | 加在哪 | 不要做什么 |
|---|---|---|
| 新数据源 | `data/incoming/` + 一个最小转换脚本 | 不要造通用爬虫框架 |
| 新 Benchmark 适配器 | `PERFORMANCE_ADAPTERS` 查找表 | 不要改匹配器语义 |
| 新计算规则 | `engines/` 里的版本化实现 | 不要在前端重算 |
| 新界面 | 调 `evaluate_plan()` 的薄壳 | 不要在界面里写 Sizing/SLA/TCO 公式 |

## 6. 本阶段不做

真实 GPU 压测（N6）、真人易用性验收（N5）、生产报价审批与发布、
新硬件/复杂并行适配、多用户与权限系统、Git 历史重写。
