# 第一阶段风险清单（risk register）

- 更新日期：2026-10-15（V1.2 加固后）
- 配套文档：`docs/security_remediation_report.md`（F1 安全）、`docs/phase1_final_validation.md`（F1–F8 验收）
- 等级：高 = 会影响对外结论或数据安全；中 = 影响可复现性/一致性；低 = 影响可读性或便利性

## 1. 数据与安全

| # | 风险 | 等级 | 现状 | 处置 / 缓解 |
|---|---|---|---|---|
| S1 | Git 历史中仍可检出整改前的内部派生金额（提交 `748ae68`、`9e3fc04`） | 中 | **未处理**，等待授权 | 见安全报告 §4 的三方案与影响评估；未授权前不重写历史 |
| S2 | 仓库为 Public，官方来源存档页面（12 HTML + 2 MDX）公开可读 | 低 | 已存在 | `data/raw/official/NOTICE.md` 说明版权归属与可删除方式 |
| S3 | 短数字类内部值（如单个每卡年成本）无法用字符串扫描识别（熵低） | 低 | 已知 | 依赖"原始 Excel / local.db / exports 均不入库"这一强不变量（有测试保证） |
| S4 | 未来有人手工把真实数据写进 `data/examples/` 或文档 | 中 | — | `verify_all.sh` 第 19 步 + `test_no_internal_values_in_tracked_files` 拦截已登记指纹 |
| S5 | 凭证泄露 | — | **未发生** | 当前树与全部历史扫描 0 命中；无需轮换 |

## 2. 证据与来源

| # | 风险 | 等级 | 现状 | 处置 / 缓解 |
|---|---|---|---|---|
| E1 | 官方来源页面发生版本漂移，快照与上游不一致 | 中 | 当前两个快照的 Blob SHA 与 SHA256 均匹配 | Fail-Closed：任一哈希不符即**阻断导入**（`SourceVerificationError`），不产生任何记录 |
| E2 | `verified_count = 0`：无完整可复现官方案例 | 中 | 12 条 `partial_evidence` | 已如实登记缺口（缺 `model_revision`、环境 pin、精度）；不虚构 |
| E3 | 12 条候选缺 `model_revision`，无法作为 SLA 依据 | 中 | 已知 | `can_evaluate_sla = False`（18 条候选全为 0），报告与 JSON 均标注 |
| E4 | 官方候选被误当成业务数据参与计算 | 中 | 已阻断 | 候选不进 `benchmark_record`；`can_calculate_pricing = False`；有隔离测试 |

## 3. 指标口径

| # | 风险 | 等级 | 现状 | 处置 / 缓解 |
|---|---|---|---|---|
| M1 | 配置并发被当成观测并发 | 中 | 已隔离 | 两个字段并存 + 测试固定（`configured=100` / `observed=90.5`） |
| M2 | ITL 被当成 TPOT | 中 | 已隔离 | 解析器禁止替代 + 口径 ID + 标签溯源检查；取值相等不判违规（避免误报） |
| M3 | 集群吞吐被当成单卡能力 | 中 | 已隔离 | 口径字典明确；每卡值只在同配置内归一化 |
| M4 | 物理命中率被当成可计费命中比例 | 中 | 已隔离 | `billable_cached_input_ratio` 恒为 `None`；无合同计费规则不推导（F4 有测试） |
| M5 | 跨框架结果被直接比较/排序 | 中 | 已阻断 | F7：需框架名、口径 ID、统计方法、负载与并发全部一致才可比；版本相同**不充分** |

## 4. 经济性与商业边界

| # | 风险 | 等级 | 现状 | 处置 / 缓解 |
|---|---|---|---|---|
| B1 | 理论满载收入被误读为实际营收/报价 | 高 | 已封板 | `result_type` 显式；报告未确认项置于财务表**之前**；`commercial_quote_allowed=False`；禁用措辞扫描 |
| B2 | 币种未确认 | 高 | 未确认 | `currency="UNCONFIRMED"` + 假设说明；`currency_not_confirmed` 阻断项 |
| B3 | 成本覆盖范围未确认 | 高 | 未确认 | `cost_scope=gpu_cost_only` + `cost_scope_unconfirmed` **逐项**列 6 类成本 |
| B4 | H 列缓存语义未确认 | 中 | 未确认 | 仅作旧表定价代理假设并强制打标；`legacy_assumptions` 在每条 legacy 结果上明示 |
| B5 | 缺吞吐/单位/价格绑定时仍产出"可用"结论 | 中 | 已阻断 | F4：`pricing_gate_blockers` 逐条记录；关键缺失时 `can_compute_theoretical=False` |
| B6 | 正式报价能力被误以为已具备 | 中 | 不存在 | 第一阶段不提供报价：`can_issue_formal_quote` 恒 False |

## 5. 工程与运行

| # | 风险 | 等级 | 现状 | 处置 / 缓解 |
|---|---|---|---|---|
| T1 | 测试依赖真实 Excel，公开克隆无法验证 | 中 | 已缓解 | 脱敏样本 + 手算期望值；公开克隆 `155 passed / 106 skipped / 0 failed` |
| T2 | SIL 缺失导致测试静默跳过（掩盖回归） | 中 | 已注意 | 本地全量基线 `325 passed`；公开克隆的 skipped 是"缺内部数据"的预期结果，不是失败 |
| T3 | 一键脚本依赖联网 | 低 | 无 | 采集与验收分离：`verify_all.sh` 全程离线 |
| T4 | SQLite 精度（NUMERIC 实为 REAL） | 中 | 已规避 | 金额列一律 `TEXT` + `payload_json` 权威副本 |
| T5 | JSON 金额经 double 往返丢精度 | 中 | 已规避 | 十进制字符串 + `loads_tolerant`；有 roundtrip 测试 |
| T6 | 轻量迁移（新增列）在旧库上失败 | 低 | 已缓解 | `_ensure_columns` 只补可空列；`verify_all.sh` 每次从空库验证 |

## 6. 未确认事项（需业务方决定，代码不代为假设）

1. 价格币种（`config/business_confirmations.example.yaml` → `currency`）
2. `GPU价格!C` 的成本覆盖范围（6 项逐条确认）
3. H 列缓存命中语义
4. 价格有效期与计费规则
5. 年度秒数口径（是否扣维护窗口）
6. 官方候选缺 `model_revision` 等配置——需找到同时给出配置与指标的官方发布
7. **Git 历史是否重写**（安全报告 §4；属破坏性操作，需显式授权）
