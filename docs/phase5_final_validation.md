# 第五阶段验证报告（M35–M42）

**基线**：`92944e3`（S 阶段封板）
**阶段定位**：真实数据接入、性能验证与方案交付 MVP ——
把**真实数据流**接到现有工作流上，而不是再造一套系统。
**文档状态**：第一轮（M35 + M36）已完成；M37–M42 待执行。

---

## 一、总体目标与约束

依托现有 `PlanRequest → evaluate_plan() → PlanResult` 主工作流，
让系统可以接收真实模型、GPU、Benchmark 与成本数据，生成可解释、可复核的部署评估结论。

继续坚持 Keep It Simple：不重写四阶段计算引擎；复用现有 SQLite / Importer /
BenchmarkRun / SLA / TCO；数据、适配器与业务判断各自保持单一权威；
不新增插件框架、微服务或分布式任务系统；用户仍以一份 YAML + 一条主命令完成评估。

**两条独立验收线**（不可混为一谈）：

| 验收线 | 依据 | 当前状态 |
|---|---|---|
| 工程验收 | 接口、导入、质量检查、报告与回归，可用明确标记的测试数据完成 | 本轮已完成 M35/M36 部分 |
| 真实性能验收 | 必须来自目标模型 / GPU / 配置 / Workload 的真实运行记录 | **未验证**（无真实压测数据） |
| 商业报价 | 独立门禁 | **不可发布**（未接入可信审批系统） |

---

## 二、M35：基线修正与交付口径冻结 ✅

### 2.1 修复的问题

复审发现统一报告的"全周期成本最低"推荐**没有复用**第四阶段已有的成本可比性规则：
它直接从"成本完整"的候选中取最小金额，既没核对币种、买断/租赁口径与计算周期，
也没排除全周期金额为 `None` 的情况 —— 而 `x or Decimal(0)` 会把**缺失**当成最便宜。

### 2.2 修复方式

把可比性判据抽成**唯一权威实现** `decision/comparison.cost_comparability()`，
输入是 `CostProfile`（币种 / 口径 / 周期 / 完整性 / 全周期金额）：
第四阶段比较器由 `TcoResult` 归约，统一入口由 `PlanCandidate` 归约 ——
**同一规则不再有第二份实现**。

判定顺序（刻意如此）：

1. 没有成本结果 / 成本不完整 —— 永远不可比（先于"没有参照"判断）；
2. 没有参照方案 —— 不给任何"最低成本"结论；
3. 币种 / 买断租赁口径 / 计算年限任一不同 —— 不可比，并给出原因码；
4. **全周期金额缺失 —— 不可比**（不得按 0 排序）。

### 2.3 交付口径：技术资格 vs 生产发布授权

报告与 CLI 现在分开陈述两件不同的事：

* **技术资格（四维门禁）**：成本/SLA/商务确认/审批四项证据的**计算结果**；
* **生产发布授权**：**未授权** —— 未接入可信审批系统，
  审批记录只做持久化、不校验审批人身份与权限，**不得用于生产环境的报价自动发布**。

### 2.4 验收对照

| 计划 §M35 验收 | 结果 |
|---|---|
| 复用第四阶段成本可比性判断 | ✅ 唯一权威 `cost_comparability()`，两处调用 |
| 不跨币种/口径/周期比较最低成本 | ✅ `test_m35_usd_candidate_cannot_win_lowest_cost`、`test_m35_leased_candidate_cannot_win_lowest_cost`、`test_m35_different_horizon_cannot_win_lowest_cost` |
| 全周期成本缺失不按 0 排序 | ✅ `test_m35_missing_total_is_not_zero`、`test_m35_missing_total_cannot_win_lowest_cost` |
| 报告区分技术资格与生产发布授权 | ✅ `test_m35_report_separates_technical_eligibility_from_production_authorization` |
| 冻结核心数据契约 | ✅ `docs/real_data_preparation.md` §一 |
| 原有回归不变 | ✅ 709 passed / 54 步 / 第一阶段 160-192-0 不变 |

---

## 三、M36：真实数据准备与质量检查 ✅

### 3.1 数据准备规范

`docs/real_data_preparation.md`：逐域列出"要准备什么、从哪来、什么算合格"，
覆盖 `ModelSpec` / `HardwareSpec` / `BenchmarkRun` / `CostCatalogItem` + `BillingRule` /
治理证据，并附**常见拒绝原因速查表**（现象 → 原因 → 处理）。

**不新建平行数据格式**：数据只走既有 `import-*` 通道。

### 3.2 数据质量分级

新增只读能力 `services.data_quality_report()` 与 CLI：

```bash
python -m llm_capacity_pricing.cli data-quality --db data/local.db --out quality.json
```

五类状态：`complete` / `unverified` / `missing_fields` / `not_applicable` / `synthetic`。

**默认不乐观**：

* `production_grade` 只在 `status == complete` 时为真（schema 层强制）；
* 只有 `complete` 的性能实测才标 `usable_for = [sla_proof, capacity_analysis]`；
* 合成数据一律 `usable_for = [algorithm_validation]`；
* **审批记录恒为 `production_grade = False`**，并写明"审批人身份与权限未经校验"；
* 报告**只读**：不修改任何记录的信任等级，也不做升级。

报告末尾的 `gaps` 是可执行的补数据指引（含 `import-performance`、
`import-cost-catalog`、`nvidia-smi` 等具体动作）。

### 3.3 验收对照

| 计划 §M36 验收 | 结果 |
|---|---|
| 真实数据可沿现有 Adapter 导入 | ✅ `test_m36_real_adapter_path_still_works` |
| 不可信/不完整记录不会自动获得生产级状态 | ✅ `test_m36_unverified_source_is_not_production_grade`、`test_m36_incomplete_identity_is_missing_fields`、`test_m36_cost_and_billing_rules_are_not_silently_upgraded` |
| 区分五种质量状态 | ✅ `test_m36_report_covers_five_statuses` 等 |
| 不新建平行数据格式 | ✅ 复用既有 Schema；质量报告只是只读输出 |
| 报告不改动信任等级 | ✅ `test_m36_report_is_read_only` |
| 指引可执行 | ✅ `test_m36_gaps_are_actionable` |

---

## 四、M37–M42：待执行

| 里程碑 | 目标 | 状态 |
|---|---|---|
| M37 | 标准化压测实验清单（实验点、元数据模板、可执行命令示例） | ⬜ 未开始 |
| M38 | 真实 Benchmark 导入与一致性验证（客户端/服务端口径、并发语义、失败与超时、重复导入幂等） | ⬜ 未开始 |
| M39 | 现场性能与 SLA 可信度验证（容量曲线、稳定容量、逐子项判定） | ⬜ 未开始 |
| M40 | 真实成本与方案比较闭环（可比/不可比候选并列） | ⬜ 未开始 |
| M41 | 降低用户操作成本（配置报错定位、报告层次、补测提示） | ⬜ 未开始 |
| M42 | 端到端验收与第五阶段封板 | ⬜ 未开始 |

---

## 五、回归记录

| 轮次 | 全量测试 | 全链路步骤 | 第一阶段回归 | 反向场景 |
|---|---|---|---|---|
| S 阶段封板（92944e3） | 683 passed | 53 / 53 | 160 / 192 / 0 | 17 / 17 |
| M35 + M36 | **709 passed** | **54 / 54** | **160 / 192 / 0** | **17 / 17** |

---

## 六、未闭环事项（与前几阶段一致）

1. **真实性能验收未验证** —— 缺少目标模型 / GPU / 配置的真实压测记录；
2. **商业报价不可发布** —— 未接入可信审批系统，且无已确认真实价格；
3. **任务书 V4.0 正文未提供**，因此始终未做原文逐条对照；
4. **仓库为 Public，历史提交中的内部定价数据风险未闭环** —— 不重写 Git 历史。
