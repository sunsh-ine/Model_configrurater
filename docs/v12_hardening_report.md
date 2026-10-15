# V1.2 加固报告（F1–F8）

- 日期：2026-10-15
- 审查基线：`9e3fc04df78b833c5e2b473ad2fba62945737207`
- 范围：M1–M11 已完成功能的安全性 / 可靠性 / 数据一致性加固，**不进入第二阶段**
- 完整执行日志：`docs/test_run_log.txt`（`bash scripts/verify_all.sh`，**22 步全绿**）
- 配套文档：`docs/security_remediation_report.md`（F1）、`docs/risk_register.md`（风险清单）

---

## 一、四分类结论

### 1.1 已通过（有实测输出）

| # | 项 | 实测证据 |
|---|---|---|
| 1 | 敏感数据扫描门禁 | `verify_all.sh` 第 19 步：`已跟踪文件中未发现内部定价数值（按本地特征表扫描 9 个特征串）` + `忽略规则校验通过` |
| 2 | 脱敏样本可用 | 公开克隆（无 Excel、无私有 oracle）：`155 passed, 106 skipped, 0 failed` |
| 3 | 引擎与手算期望值一致 | 样本 3 行 × 4 个金额/比率量与 `scripts/make_public_sample.py` 的独立手算结果**逐位一致** |
| 4 | 来源 Fail-Closed | 正常路径 12/12 通过；**篡改/缺失/声明哈希不符三种情况均抛 `SourceVerificationError` 并阻断**（+6 个反向测试） |
| 5 | 失败导入不留痕 | 校验失败后 `list_numeric_candidates()==[]` 且 `evidence==0`（原子性测试） |
| 6 | SLA 结构化匹配 | 部署 5 维（TP/DP/EP/卡数/模式）+ 工作负载 6 维（ISL/OSL/到达方式/并发/数据集/统计口径）逐维比较，每个维度都有"不符即阻断"的参数化测试 |
| 7 | 经济性门禁 | 吞吐缺失/单位未声明/成本绑定/单价绑定/商业条件共 12 类阻断项均有测试；`can_issue_formal_quote` 恒 False |
| 8 | 缓存口径不被自动换算 | `billable_cached_input_ratio` 与 `physical_cache_hit_rate` 在 32 条业务记录上恒为 `None` |
| 9 | 参数类型化比较 | `dataset_name` 等字符串参数**现在真的参与比较**（此前被静默跳过，见 §二.3） |
| 10 | 命令溯源 | `benchmark_command` 取自官方原文（多行原始写法），候选文件命令单列保存并参与差异检查 |
| 11 | DB 字段一致 | 逐字段核对 DB 行 == Schema 值；`is_throughput_benchmark` 的 `True/False/None` 三种取值均可原样往返 |
| 12 | 跨框架可比性 | 同版本不同框架 → 不可比；口径/统计/负载任一不同 → 不可比（7 类参数化测试） |
| 13 | 全量回归 | 本地 `325 passed`（基线 260 + 加固新增 65），无跳过/失败 |
| 14 | 一键脚本 | `步骤 22 项：通过 22，失败 0`，退出码 0，全程离线 |

### 1.2 已修复（本轮定位并修掉的问题）

| # | 问题 | 根因 | 修复 |
|---|---|---|---|
| 1 | **字符串参数被静默跳过**：`dataset_name` 从未真正核对 | 参数比较统一走 `_dec()`，字符串返回 `None` 后被 `continue` 跳过 —— 一个"看起来在比对、实际没比对"的假验证 | 引入 `PARAM_SPECS`（类型化：int/decimal/string/enum/symbol/bool）与 `compare_param()`；类型不匹配时报"无法规范化"而不是静默跳过 |
| 2 | **证据校验非 Fail-Closed**：快照哈希不符时仍继续解析原文并生成带"已核对"标记的候选 | 哈希校验只是"记一条 reason"，没有中断流程 | 新增 `SourceVerificationError` 与策略参数（默认 `fail`，阻断且不写库；显式 `skip` 才跳过该来源，且绝不打核对标记） |
| 3 | **DB 硬编码覆盖源数据**：`is_throughput_benchmark=True` | 写库时写死常量 | 改为 `c.is_throughput_benchmark`；同时审计出 `benchmark_tool` 也是硬编码，改为从权威命令推导；`hardware_binding` 改为 Schema 派生属性 |
| 4 | **SLA 门禁只看粗粒度字段**：只比模型名/revision/精度/硬件，`parallel_config` 与 `workload_profile` 只是自由文本 | 缺乏结构化配置与负载模型 | 新增 `DeploymentConfig` / `WorkloadProfile` 并逐维比较；记录侧缺字段返回 `record_missing:<field>`，不再"需求侧非空即匹配" |
| 5 | **经济性计算缺少前置门禁** | 只要价格行命中就算得出来，不检查吞吐/单位/绑定一致性 | 新增 `assess_pricing_readiness()`，12 类阻断项写入每条结果的 `pricing_gate_blockers` |
| 6 | **跨框架可比性只看版本字符串** | `cross_backend_comparable(a, b)` 仅比较版本 | 改为 `BackendProfile` 结构化比较（框架/口径 ID/统计方法/负载/并发），版本相同只记为 note 不作判据 |
| 7 | **内部定价数据入库**（F1 核心问题） | `data/exports/` 未忽略；文档与测试含真实数值 | 移出跟踪 + `.gitignore` 划界 + 文档脱敏 + oracle 外置 + 日志脱敏 + 脱敏样本 + 扫描门禁 |
| 8 | **脱敏扫描自我暴露**：把真实数值写成扫描特征串，等于重新公开 | 特征表与测试硬编码了敏感值 | 特征表移到不入库的 `data/private/sensitive_patterns.txt`；公开克隆退化为忽略规则检查 |
| 9 | **低熵特征误报**：`52.5` 命中无关测试 | 短数字不适合做指纹 | 从特征表移除并写明理由（保护依赖"原始文件不入库"这一更强不变量） |
| 10 | `PriceContext` 与 readiness 重复判断绑定关系 | 同一事实两处口径 | `PriceContext` 收敛为纯商业条件（币种/成本范围/有效期/计费规则），绑定检查归 `assess_pricing_readiness` |

### 1.3 未确认（保留待确认状态，未自行假设）

| # | 事项 | 当前处理 |
|---|---|---|
| 1 | **仓库访问权限是否改为 Private** | 按你的指示**保持 Public**；已把"当前为公开仓库、历史中含内部派生金额"写入安全报告与风险清单 |
| 2 | **是否重写 Git 历史** | **未执行**。安全报告 §4 给出 A/B/C 三方案与影响评估；破坏性操作需显式授权 |
| 3 | 币种 / 成本覆盖范围 / 缓存语义 / 有效期 / 计费规则 / 年度秒数 | 全部保持 `unconfirmed`，模板在 `config/business_confirmations.example.yaml` |
| 4 | 12 条官方候选的 `model_revision` 等配置 | 保持 `null` 并登记缺口；未找到可补齐的官方发布 |
| 5 | 短数字类内部值的自动识别 | 明确列为残余风险 S3（低）；依赖不入库不变量 |

### 1.4 未实现（如实标注）

| # | 项 | 说明 |
|---|---|---|
| 1 | 完整可复现的 `official_benchmark` | 仍为 **0 条**（12 条 `partial_evidence`） |
| 2 | SLA 达标判定 | 门禁只回答"是否具备评估条件"；18 条候选全为 `can_evaluate_sla=False`，无任何达标结论 |
| 3 | 正式报价能力 | 第一阶段不提供；`can_issue_formal_quote` 恒 False |
| 4 | 自动去敏工具 | 只提供"样本生成 + 扫描拦截 + 人工 review"；未做自动改写真实数据的工具 |
| 5 | 第二阶段内容 | 显存精算、KV Cache 精算、自动 TP/DP 选型、PD 比例优化、完整 TCO、前端等**均未开发** |

---

## 二、修复前后差异说明

### 2.1 行为差异（可观察）

| 场景 | 修复前 | 修复后 |
|---|---|---|
| 官方原文快照被篡改 | 记一条 reason，**继续**解析并生成带 `values_machine_verified_against_archived_source` 的候选 | 抛 `SourceVerificationError`，**不写库**（`evidence=0`、候选=0） |
| 候选文件里的 `dataset_name` 被改 | 静默通过（字符串参数不参与比较） | 记入 `verification.mismatches`，该条不再算"已核对"（12→11） |
| 候选文件自带命令与原文命令不一致 | 不检查 | 逐参数对比并报 mismatch |
| SLA 需求与记录的 TP/DP/EP/卡数/模式不同 | 只看模型名/revision/精度/硬件 | 逐维阻断，原因形如 `mismatch:tp_size`、`record_missing:ep_size` |
| 记录缺吞吐/单位/价格绑定 | 照算，无提示 | 写入 `pricing_gate_blockers`；关键缺失时 `can_compute_theoretical=False` |
| 两个不同框架但版本号相同的结果 | 判为"可直接比较" | 判为**不可比**，并列出 `framework_differs` 等原因 |
| 提交含真实定价的导出文件 | 无拦截 | 第 19 步扫描 + 测试用例拦截；`.gitignore` 明确排除 |
| 运行日志 | 含明文金额 | 金额值替换为 `<redacted:internal>`，键名与结论保留 |

### 2.2 接口差异

| 接口 | 变化 |
|---|---|
| `build_numeric_candidates(..., on_verification_failure="fail")` | 新增策略参数（默认 fail-closed） |
| `import_sglang_candidates(..., on_verification_failure=...)` | 透传策略 |
| `assess_applicability(..., record_deployment=, record_workload=)` | 新增记录侧结构化参数 |
| `SlaRequirement(parallel_config=, workload_profile=)` | **替换为** `deployment=DeploymentConfig(...)` 与 `workload=WorkloadProfile(...)` |
| `PriceContext(hardware_cost_bound=, token_price_bound=)` | **移除**（绑定判断归 `assess_pricing_readiness`）；新增 `cost_scope_confirmed` |
| `cross_backend_comparable(a: BackendProfile, b: BackendProfile)` | 签名结构化；版本字符串不再被接受 |
| `SglangNumericCandidate.benchmark_command_from_candidate_file` | 新增字段 |
| `PricingResult.pricing_gate_blockers / commercial_quote_allowed / legacy_assumptions` | 新增字段 |
| `SourceVerificationError` | 新增异常类型 |

### 2.3 新增文件

| 文件 | 作用 |
|---|---|
| `scripts/make_public_sample.py` | 生成脱敏样本工作簿 + **独立手算**期望值 |
| `data/examples/Token产能计算器-SAMPLE.xlsx`、`public_sample_expected.json` | 可公开的测试样本 |
| `data/private/{row2_oracle.json,sensitive_patterns.txt,README.md}` | 内部 oracle 与特征表（**不入库**） |
| `tests/test_v12_hardening.py`（58 个用例） | F1/F3–F8 的正负样例 |
| `docs/security_remediation_report.md` | F1 安全报告（暴露清单、凭证扫描、历史影响评估） |
| `docs/risk_register.md` | 风险清单（数据安全/证据/口径/商业/工程五类） |
| `docs/v12_hardening_report.md` | 本文件 |

---

## 三、回归基线核对（必须不变）

| 验收项 | 预期 | 实测 | 状态 |
|---|---|---|---|
| Excel 历史数据 | 32 条 | 32 | ✅ |
| GPU 价格 | 2 条 | 2 | ✅ |
| 模型价格 | 3 条 | 3 | ✅ |
| 默认满载结果 | 32 条 | `结果条数: 32（业务 32 + 测试 0）` | ✅ |
| 默认敏感性情景 | 160 条 | `默认敏感性情景条数: 160` | ✅ |
| 默认 `PricingResult` | 192 条 | `最终 pricing_result 总数: 192` | ✅ |
| 官方数值候选 | 12 条 | `numeric_candidates: 12` | ✅ |
| 页面级候选 | 6 条 | `manifest_candidates: 6` | ✅ |
| Synthetic 参与默认计算 | 0 条 | `残留的 synthetic_fixture 结果行: 0` | ✅ |
| Excel 超容差不匹配 | 0 | `超容差不匹配数: 0` | ✅ |
| 已有测试回归 | 不发生 | 260 → **325 passed**（只增不减、无跳过） | ✅ |

**示例精度（真实工作簿）**：最大误差 I/K `4.4E-13`、J/L `0`、M `5.7E-15`，与 V1.1 基线逐位一致。

---

## 四、验证命令与结果

```bash
cd llm-capacity-pricing
bash scripts/verify_all.sh      # 退出码 0；步骤 22 项：通过 22，失败 0
.venv/bin/python -m pytest -q   # 325 passed
```

关键步骤实测输出：

| 步骤 | 输出 |
|---|---|
| 8–9 | `已导入 12 条官方数值候选（partial_evidence）`、`原文逐字段核对通过: 12/12`、重复导入幂等 |
| 10 | `candidate_count 18`、`verified_count 0`、`can_evaluate_sla 0`、`can_calculate_pricing 0` |
| 16 | `复算覆盖行数: 32`、`超容差不匹配数: 0`、`默认敏感性情景条数: 160` |
| 17 | `最终 pricing_result 总数: 192`、`残留的 synthetic_fixture 结果行: 0` |
| 19 | `已跟踪文件中未发现内部定价数值`、`忽略规则校验通过` |
| 20 | `325 passed` |

---

## 五、是否满足第一阶段正式封板条件

| 判定项 | 结论 |
|---|---|
| F1 内部定价数据安全整改 | **完成**（按"保持 Public + 本地保留真实数据 + 公开用脱敏样本"执行；历史重写待授权） |
| F2 证据校验加固 | **完成**（Fail-Closed，三种失败路径均阻断且有反向测试） |
| F3 SLA 门禁修正 | **完成**（部署 5 维 + 负载 6 维结构化匹配） |
| F4 经济性门禁完善 | **完成**（12 类阻断项；保留 Excel 复算并明示假设） |
| F5 参数解析与溯源修复 | **完成**（类型化比较；权威命令取自原文） |
| F6 数据库字段一致性 | **完成**（无硬编码覆盖；True/False/None 往返测试） |
| F7 跨框架可比性 | **完成**（版本相同不再充分） |
| F8 独立回归与封板 | **完成**（脱敏 Golden Fixture、扫描门禁、22 步全链路、325 passed） |
| 回归基线 11 项 | **全部满足** |
| 禁止事项 | **未触碰**（未做显存/KV 精算、未新增官方数值、未宣称 SLA 达标、未改 Excel 公式、未把理论收入当报价） |

**结论：满足第一阶段正式封板条件。阻塞项：无。**

唯一需要你决定的事项：**是否重写 Git 历史以清除整改前的内部数值**（安全报告 §4）。
在此之前，本仓库的**当前版本**已不含未授权披露的真实定价结果，且新增门禁可防止再次引入。
