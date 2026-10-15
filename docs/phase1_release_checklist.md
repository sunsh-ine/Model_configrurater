# 第一阶段封板清单（phase 1 release checklist）

- 日期：2026-10-15　|　范围：M7–M11　|　详细验证见 `docs/phase1_final_validation.md`
- 一条命令复现全部结论：`bash scripts/verify_all.sh`（离线，退出码 0）
- 当前状态：**21/21 步通过、260 个测试通过、可封板**

## 1. 功能清单

| # | 检查项 | 状态 | 验证方式 / 实际输出 |
|---|---|---|---|
| 1 | M7 12 条官方数值候选接入 | ✅ | `已导入 12 条官方数值候选（partial_evidence）`；按来源 9 + 3 |
| 2 | 12 条逐字段核对存档原文 | ✅ | `原文逐字段核对通过: 12/12（不一致 0 条）` |
| 3 | 重复导入幂等 | ✅ | 连续 2 次导入，候选表恒 12 行、12 个唯一 `record_id` |
| 4 | 候选不进性能库、不参与计算 | ✅ | `benchmark_record = 36`（32 Excel + 4 fixture）；`official_benchmark = 0` |
| 5 | M8 指标字典齐全 | ✅ | `docs/benchmark_metric_dictionary.md`，18 个字段 + 口径 ID；文档表由代码生成 |
| 6 | 吞吐一致性校验 | ✅ | 12/12 通过 `吞吐 ≈ 累计 token / 时长`，最大相对误差 `2.27e-4`（容差 `1e-3`） |
| 7 | 配置并发与观测并发并存 | ✅ | 5.1.1.3：`configured=100`、`observed=90.50` |
| 8 | 目标长度 ≠ 实际平均长度 | ✅ | 5.1.1.2：目标 1000/1000，实测平均输入 `39668/80 = 495.85` |
| 9 | retokenized 不覆盖总数 | ✅ | `total_output_tokens=4210`、`total_output_tokens_retokenized=4206` |
| 10 | `inf` 不进 Decimal | ✅ | 存在 `traffic_request_rate_raw="inf"` 与 `symbols`；`metrics` 内全为有限 Decimal |
| 11 | M9 三条门禁为派生值 | ✅ | `official-coverage`：`can_reference_performance 12`、`can_evaluate_sla 0`、`can_calculate_pricing 0` |
| 12 | SLA 阻断（缺 revision） | ✅ | 测试：全匹配但缺 revision 仍 `record_missing:model_revision` |
| 13 | SLA 可评估路径存在（非硬编码） | ✅ | 测试：条件齐备时返回 True |
| 14 | 报价阻断 | ✅ | `commercial_quote_allowed=False`；导出含 `"commercial_quote_allowed": false` |
| 15 | M10 未确认项显式化 | ✅ | `cost_scope_unconfirmed` 6 项 + `cost_scope_confirmation_status`；报告口径表位于财务表**之前** |
| 16 | 待确认流程预置 | ✅ | `config/business_confirmations.example.yaml`（6 项，状态全 `unconfirmed`，`confirmed_by=null`） |
| 17 | M11 报告含未确认项与候选概览 | ✅ | `data/exports/overview.md` 68122 B，含 §6.4 官方数值型候选表 |

## 2. 回归清单（必须与基线一致）

| # | 检查项 | 基线 | 现在 | 状态 |
|---|---|---|---|---|
| 1 | Excel 导入行数 | 2 / 3 / 32 | 2 / 3 / 32 | ✅ |
| 2 | 旧表复算行数 / 超容差不匹配 | 32 / 0 | 32 / 0 | ✅ |
| 3 | 最大误差 I/K、J/L、M | 4.4E-13 / 0 / 5.7E-15 | 同 | ✅ |
| 4 | 默认满载 / 敏感性 | 32 / 160 | 32 / 160 | ✅ |
| 5 | 含测试数据 | 36 / 180 | 36 / 180 | ✅ |
| 6 | `pricing_result`（默认） | 192 | 192 | ✅ |
| 7 | fixture 残留结果行 | 0 | 0 | ✅ |
| 8 | 金额序列化 | Decimal 字符串 | Decimal 字符串 | ✅ |
| 9 | `validate-import` | 20/20 | 20/20 | ✅ |
| 10 | `validate-results` | 9/9 | 9/9 | ✅ |
| 11 | pytest | 200 passed | **260 passed**（新增 60，无跳过/失败） | ✅ |

## 3. 证据与来源

| # | 检查项 | 状态 | 说明 |
|---|---|---|---|
| 1 | 两个来源有 manifest 条目 | ✅ | `official_manifest.yaml` 16 条（新增 2 条 `numeric_source_partial_environment`） |
| 2 | 原文快照 + SHA256 | ✅ | 两个 `.mdx` 已存档，SHA256 与 manifest 记录一致 |
| 3 | Git Blob SHA 独立校验 | ✅ | `580640bd…` / `a743f7ba…` 与声明一致；与 SHA256 语义区分并有交叉测试 |
| 4 | 只读输入留存 | ✅ | `data/incoming/...json`，权限 444 |
| 5 | 快照缺失/被篡改时的行为 | ✅ | 缺失 → 不声称已核对；篡改 → 双哈希校验失败（两条测试） |
| 6 | 未列出但原文存在的结果块 | ✅ | Coder 的 `5.1.2.1/5.1.2.2`（NVIDIA，6 块）登记为 `unlisted_sections_with_results`，**不擅自补录** |

## 4. 封板判定

| 判定项 | 结论 |
|---|---|
| P0（M7–M10）全部满足 | **是** |
| P1（M11 文档与离线复现）完成 | **是** |
| 是否存在伪造数据 / 越权推断 | **否**（12 条候选 `verified_count=0`，缺字段保持空值并登记） |
| 是否存在回归 | **否**（§2 全部一致，测试数 200 → 260） |
| 是否可封板 | **可以** |
| 阻塞项 | **无** |

## 5. 封板后仍需业务方处理（不在本轮范围）

1. `config/business_confirmations.example.yaml` 中 6 项待确认（币种、成本范围、缓存语义、
   有效期、计费规则、年度秒数）——确认后金额结论才具备对外解释力。
2. 若要产出**完整可复现**的 `official_benchmark`：需要一份同时给出 model revision、
   精度、硬件 SKU 与卡数、并行拓扑、数据集与统计口径的官方发布（当前 12 条的缺口
   集中在 `model_revision` 与完整环境 pin）。
3. 第二阶段范围（显存精算、TP/DP/EP 自动搜索、SLA 预测、PD 比例优化、完整 TCO、
   正式报价）**本轮明确未开发**。
