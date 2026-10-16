# S 阶段重构基线冻结（S0）

**冻结提交**：`ab5cd2e`（并包含本轮 S0 的三项门禁修正）
**冻结时点**：2026-10-16
**仓库**：`sunsh-ine/Model_configrurater`（Public）

本文件记录重构开始前冻结的**接口、数据与测试基线**。S 阶段的目标是降低复杂度，
因此先明确"哪些东西不能动"，再动结构。

---

## 一、测试与回归基线

| 项 | 基线值 | 校验方式 |
|---|---|---|
| 全量测试 | **641 passed**（S0+S1 后） | `pytest -q` |
| 全链路步骤 | **52 / 52**（S0+S1 后） | `bash scripts/verify_all.sh` |
| 第一阶段回归 | 默认情景 **160**（32×5）、`pricing_result` **192**、超容差不匹配 **0** | 验证链第 16–17 步 |
| 报价反向场景 | **17 / 17 全阻断** | `python scripts/check_forged_quote_blocked.py` |

S 阶段每一轮结束都必须跑这三条命令，任何一项回退即视为该轮失败。

## 二、冻结的公开契约

### 2.1 CLI 命令（**全部保留，不删除**）

| 分组 | 命令 |
|---|---|
| 数据管理 | `init-db` `import-excel` `import-sglang` `import-vllm` `import-official` `import-sglang-candidates` `import-models` `import-hardware` `import-performance` `import-cost-catalog` `import-commercial-confirmations` `import-quote-approvals` |
| 校验/查询 | `validate-import` `validate-results` `list-records` `official-coverage` `sizing-inventory` `cost-catalog` `performance-inventory` |
| 计算 | `calculate-all` `scenario` `estimate-memory` `plan-deployment` `match-benchmark` `capacity-profile` `calibrate-performance` `evaluate-sla` `rank-deployments` `tco` `revenue-scenario` `record-sla-evidence` |
| 治理 | `quote-eligibility` `phase4-decision` |
| 报告 | `report` `performance-report` `phase4-report` |

**约束**：S 阶段只允许**新增**命令（`plan`），不得修改或删除既有命令的选项名与语义。

### 2.2 数据库结构

| 表 | 阶段 | 冻结内容 |
|---|---|---|
| `evidence` `hardware_price` `token_price` `benchmark_record` `pricing_result` `import_run` | 一 | 列名与语义 |
| `official_benchmark_candidate` `model_spec` `hardware_spec` `server_profile` | 一/二 | 列名与语义 |
| `benchmark_run` | 三 | 列名与语义 |
| `cost_catalog_item` `billing_rule` `commercial_confirmation` | 四 | 列名与语义 |
| `sla_evidence` `quote_approval` | 四 | 列名与语义 |

**约束**：只允许**追加**可空列（`Database._ensure_columns` 已支持轻量迁移），
不得改列名、改类型、改语义或删除列。

### 2.3 报告字段

`docs/test_run_log.txt` 中出现的键名与结论行（如 `匹配等级分布`、`分桶`、
`SLA 状态`、`quote_publishable`）保持稳定；S 阶段新增的报告不得修改既有键名。

### 2.4 历史计算口径（**不得改变**）

* 第一阶段 `COST_SCOPE = "gpu_cost_only"`、`calculate_all` 的 32+160 = 192 条算法；
* 第二阶段的权重/显存/KV 公式与单位口径（GB 10^9 / GiB 2^30 显式区分）；
* 第三阶段的匹配分级、SLA 门禁（`observed_pass` 只接受 `exact_match`）、
  保守聚合规则；
* 第四阶段的 TCO 分解口径、买断/租赁互斥、未知不等于零。

## 三、S0 完成的三项局部修正

均在**现有报价门禁内**完成，未引入新层级或新的报价流程。

### 3.1 计费规则模型必须与报价模型一致

`billing_rule.model_name` 与报价模型（`facts["model_name"]`）归一化后必须相等；
任一侧缺失即不通过。原因码 `billing_rule_model_mismatch`。
归一化沿用第三阶段的 `normalize_name`（只做大小写/空白，不做别名推断）。

### 3.2 计费规则内容变化使旧审批失效

`ApprovalScope` 新增 `billing_rule_hash`，由 `billing_rule_snapshot_hash()` 计算
（覆盖规则 ID、模型、币种、三个单价、缓存比例与依据、批处理折扣、
最小计费量、失败计费口径、状态与有效期窗口）。
同一个 `rule_id` 改了单价或条款 → `approval_billing_rule_stale`。

### 3.3 商务确认必须与当前报价作用域一致

`CommercialConfirmation` 新增结构化 `scope_refs`
（`candidate_ids` / `requirement_ids` / `model_names` / `billing_rule_ids`）。
未声明或与当前候选/需求不符 → `commercial_confirmation_scope_missing` /
`commercial_confirmation_scope_mismatch:*`。

**验收**：三个反向场景全部被阻断——
`test_s0_billing_rule_model_must_match_quote_model`、
`test_s0_billing_rule_content_change_invalidates_approval`、
`test_s0_confirmation_must_match_quote_scope`；
反向场景脚本扩展至 **17 项检查**（新增路径 8/9/10）。

**生产自动报价仍为禁用状态**（见 `docs/phase4_interface_design.md` §5.2）。

## 四、S 阶段后续里程碑的验收锚点

| 里程碑 | 关键验收 |
|---|---|
| S1 | 一份 YAML + 一条 `plan` 命令贯通评估；仅有资源数据也能出部分结果；新入口不绕开门禁 |
| S2 | 主调用链清晰可追踪；CLI 与 Agent 复用同一应用服务；`services.py` 职责不再扩大 |
| S3 | 同一业务规则只有一处权威实现；修改后所有入口自动一致 |
| S4 | 新增 GPU SKU / Benchmark 格式 / 计费规则以局部修改完成 |
| S5 | 普通用户无需理解表结构/Run ID 即可完成评估；报告区分实测/估算/假设/缺失 |
| S6 | 四个端到端场景（A 仅资源 / B 有实测无价格 / C 完整合成 / D 输入错误）通过 |

## 五、S 阶段禁止事项（摘自计划 §1）

不新增微服务、Redis、Kafka、复杂插件框架、通用规则引擎、自治 Agent、
Web 前端、完整 RBAC；不一次性重写系统；不因增加层级解决简单重复。

## 六、独立跟踪的安全事项

仓库为 Public，历史提交中的内部定价数据风险**未闭环**。
S 阶段**不重写 Git 历史**，该事项单独跟踪，需用户明确授权后另行处理。
