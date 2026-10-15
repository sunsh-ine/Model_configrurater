# 第三阶段验证报告（M19–M25）

- 日期：2026-10-15
- 开发基线：`924dc80`（第二阶段 V2.1 整改后）
- 范围：Benchmark 匹配、性能实测数据管理、并发容量分析、性能估算、SLA 评估、部署候选比较
- 完整执行日志：`docs/test_run_log.txt`（`bash scripts/verify_all.sh`，**36 步全绿，退出码 0**）
- 配套：`docs/phase2_final_validation.md`（M12–M18）、`docs/security_remediation_report.md`（F1）

---

## 一、两个独立验收维度（任务书 §5 要求）

任务书明确要求把"工程功能"与"性能可信度"**分开验收**。本报告据此分节。

### 1.1 工程功能验收：通过

| # | 验收项 | 实测证据 |
|---|---|---|
| 1 | 全链路离线可运行 | `步骤 36 项：通过 36，失败 0`，退出码 0（无 GPU、无联网） |
| 2 | 自动化测试 | **460 passed**（第二阶段的 404 + 第三阶段 56） |
| 3 | 第一阶段回归 | `结果条数: 32`、`默认敏感性情景条数: 160`、`最终 pricing_result 总数: 192`、`超容差不匹配数: 0`、`残留的 synthetic_fixture 结果行: 0`——全部不变 |
| 4 | 七个 CLI 命令接通 | `import-performance` / `match-benchmark` / `capacity-profile` / `calibrate-performance` / `evaluate-sla` / `rank-deployments` / `performance-report` 在链路中全部通过 |
| 5 | JSON + Markdown 报告 | `data/exports/performance_report.md`（2482 字节，含证据概览、容量曲线、候选比较、未确认清单） |
| 6 | 官方候选可检索可解释 | 18 条候选全部进签名池；匹配结果带结构化的 `reason_codes` 与 `differences` |
| 7 | 官方候选不会变成"内部实测" | 候选的 `data_trust` 恒为 `measured_vendor_published`；任何 synthetic 结论必须显式打标 |

### 1.2 性能可信度验收：**未验证（保持未验证）**

| 验收项 | 状态 | 原因 |
|---|---|---|
| 目标部署（Qwen3-32B + H100/H200）的真实实测数据 | **缺失** | 本项目**没有**该组合的任何实测记录；现有材料只有 Qwen3-235B/B200 与 Qwen3-Coder/MI300X 的官方公开数据 |
| 样本量与覆盖范围满足既定政策 | **未满足** | 目标配置没有任何并发点，谈不上覆盖 |
| 预测误差验证 | **未进行** | 没有可校准的实测数据；估算器对目标配置一律返回 `not_predictable` |

因此第三阶段**只能交付"评估引擎"，不能交付"性能结论"**。
按任务书要求，这种情况下的正确输出就是 `not_evaluable` / `requires_benchmark`——
链路中已实测得到该结果（见 §3.4、§3.5），**这不是开发失败**。

---

## 二、四分类结论

### 2.1 已通过

| # | 项 | 实测输出 |
|---|---|---|
| 1 | M19 跨模型/跨硬件不误命中 | `匹配等级分布: {'rejected': 55}`，`非拒绝项: 0 条`（目标 Qwen3-32B + H100 SXM） |
| 2 | M19 四种匹配等级齐备 | `exact_match`（配置+负载全等）、`comparable_reference`（负载不同）、`modeling_candidate`（同模型同硬件但 TP/卡数不同）、`rejected`（模型/硬件/精度不符、EP/PD、缺指标、synthetic 未开 opt-in） |
| 3 | M19 有明确 reason codes | 如 `hardware_model_mismatch:NVIDIA H100 SXM!=NVIDIA B200`、`unsupported_deployment_field:ep_size`、`data_trust_rejected:synthetic_fixture_not_allowed` |
| 4 | M20 两套框架统一导入 | vLLM `bench serve` JSON 与 SGLang 逐请求 JSONL 均落到 `BenchmarkRun` + `PerformanceProfile` |
| 5 | M20 原始值可逐项核对 | 测试对同一原始 JSON 的 `p99_ttft_ms`/`p99_tpot_ms`/`p99_itl_ms`/`output_throughput` 逐项比对通过 |
| 6 | M20 幂等 | 同一曲线重复导入 2 次仍为 5 条 |
| 7 | M20 P99 缺失不填零 | Excel 记录（无时延口径）的 P99 保持 `None`，并登记 `missing_metrics` |
| 8 | M20 吞吐自洽校验 | `throughput_consistent_with_tokens_over_duration`；人为放大 10 倍后被判 `output_throughput_inconsistent` |
| 9 | M20 配置并发与观测并发分离 | 合成样本只给配置并发 → 观测并发保持 `None`，不被顶替 |
| 10 | M21 容量曲线 | 5 个并发点：吞吐与 TTFT 均随并发单调上升；覆盖 `[1,2,4,8,16]`，标出缺失点 32；`coverage_status=sufficient` |
| 11 | M21 单点最优 ≠ 可持续 | 默认判据下 `maximum_sustainable_capacity=16`（附持续时间与失败率证据）；把时长门限提到 1000s 后变回 `None` 并打 `sustainable_capacity_not_evidenced` |
| 12 | M21 不混配置/负载 | 混合不同 TP 或不同负载类型直接抛错（两条测试） |
| 13 | M22 精确查询优先 | 命中已观测并发点 → `measured_reference`，不使用模型 |
| 14 | M22 范围内插值带留出误差 | 并发 6（区间 1–16 内）→ `calibrated_estimate`，值 222.5，`holdout_mae=19.78`、相对误差 10.3%（**留一验证**，不是拟合残差） |
| 15 | M22 不外推 | 并发 64 → `not_predictable: out_of_covered_range` |
| 16 | M22 P99 不插值 | `ttft/tpot/itl/e2e` 四个 P99 全部 `not_predictable: p99_not_interpolatable` |
| 17 | M23 四类状态齐全 | `observed_pass` / `observed_fail` / `estimated_only` / `not_evaluable` 均有测试覆盖 |
| 18 | M23 默认策略拒绝 synthetic | 链路中 `SLA 状态: not_evaluable`（`data_trust_not_accepted:synthetic_fixture`） |
| 19 | M23 实测超标 → observed_fail | 阈值 150ms 对实测 210ms，逐项给出"超过上限" |
| 20 | M23 估算不得当实测 | 仅估算 → `estimated_only`，明示"未经实测验证" |
| 21 | M23 Goodput 需逐请求明细 | 无明细 → `not_evaluable` 且拒绝用聚合 P99 反推；有明细 → 8/10 条满足并除以测试时长 |
| 22 | M23 SLA 必须挂匹配判定 | 不传匹配结果 → `not_evaluable: match_result_required_for_sla_proof`（无后门） |
| 23 | M24 静态不可行先排除 | 分桶 `{'requires_benchmark': 2, 'excluded': 1}`，`excluded` 全为 `infeasible` |
| 24 | M24 无性能证据 → requires_benchmark | 链路输出 `阻塞: requires_benchmark: 所有静态可行候选都缺少匹配的目标硬件实测数据` |
| 25 | M24 有匹配实测 → verified_sla_met | 测试中喂入对齐的实测记录后进入 `verified_sla_met` 并附 `matched_run_id` |
| 26 | M24 不做跨配置吞吐排序 / 不做经济性评分 | `comparability_notes` 显式声明；`economic_scoring_used=False` |
| 27 | M25 报告含全部边界声明 | 6 条边界声明逐一断言通过 |

### 2.2 已修复（开发中发现并修掉的问题）

| # | 问题 | 根因 | 修复 |
|---|---|---|---|
| 1 | **ranker 把 SLA 字段名当指标口径 ID**：`required_metrics=["ttft_p99_ms"]` 而签名里是 `m.latency.ttft.p99` → 所有记录都被判"缺指标"，候选全部落到 `requires_benchmark` | 匹配需要的是**口径 ID**，不是字段名 | 新增 `required_metric_ids()` 做映射；ranker 改用它。若未修，`verified_sla_met` 分支永远不可达（被测试抓出） |
| 2 | **TP/DP 差异被当成硬阻断**：同模型同硬件、仅 TP 不同也被判 `rejected`，`modeling_candidate` 形同虚设 | `gpu_count_mismatch` 被列入硬阻断原因 | 拆分为"数据层不一致"（模型/硬件/精度/指标 → rejected）与"部署层不一致"（tp/dp/卡数 → modeling_candidate） |
| 3 | **`deployment_mode="unknown"` 被当成"有值但未确认"**，导致配置完全一致的合成样本只能降级 | 未区分"真实取值"与"未指定占位值" | 引入 `is_absent()`，把 `unknown/none/空` 视为缺失 |
| 4 | **SLA 可以绕过匹配判定**：不传 `match_result` 也能给出 `observed_pass` | 缺省即放行 | 改为强制：无匹配判定 → `not_evaluable`（`match_result_required_for_sla_proof`） |
| 5 | **降级原因不可解释**：只写 `downgraded:unverified_details_present`，不说是什么未确认 | 未把未确认项写入原因 | 未确认项以 `unverified:<item>` 形式进入 `reasons`（例：`unverified:deployment_mode_unverified:unknown`） |
| 6 | `RankedDeployment` 构造时漏传必填的 `rank_bucket` | 先构造后赋值的写法与 Pydantic 校验冲突 | 改为先定桶与理由再构造 |
| 7 | 服务层缺少 `SlaPolicy` 导入，`rank-deployments` 直接报 `NameError` | 类型注解之外的运行时引用 | 补齐导入 |

### 2.3 未确认（保留待确认，未自行假设）

| # | 事项 | 当前处理 |
|---|---|---|
| 1 | **目标部署（Qwen3-32B + H100/H200）的实测性能** | 无数据 → 匹配全 `rejected`、SLA `not_evaluable`、排序 `requires_benchmark` |
| 2 | 目标硬件的实测可用显存（第二阶段的遗留缺口） | `usable_memory_status=pending_measurement`，规划只能到 `conditional_feasible` |
| 3 | 官方候选的 `model_revision` / 环境 pin | 保持 `null`；因此候选之间即使配置相近也不能当作同一部署 |
| 4 | 观测并发 | 官方候选与合成样本都只给配置并发；观测并发保持 `None` |
| 5 | 统计学口径（P99 的算法、计时窗口、预热策略） | 记录为 `metric_definition_ids` + `statistics_window`，未提供即留空 |
| 6 | 价格币种与成本覆盖范围 | 仍未确认；因此**不使用经济性评分**（M24 硬约束） |
| 7 | Git 历史中的定价数据 | 仓库仍为 public，方案见安全报告 §4，未授权不重写历史 |

### 2.4 未实现（本阶段边界内不做）

| # | 项 | 说明 |
|---|---|---|
| 1 | 按 GPU FLOPS 推算 TTFT/TPOT | 任务书禁止项；代码中无此路径 |
| 2 | PP / EP / PD 分离的性能模型 | 字段保存并**显式阻断**（`unsupported_deployment_field`） |
| 3 | 跨模型/跨硬件的通用性能预测器 | 只保留 `PerformanceModel` 协议接口；未实现 |
| 4 | 跨 TP/DP 的每卡吞吐外推 | 禁止；`modeling_candidate` 明确标注 `cross_config_extrapolation` 禁止使用 |
| 5 | Goodput 的聚合反推 | 只允许由逐请求明细计算 |
| 6 | P99 的插值与统计保证 | P99 一律不可插值 |
| 7 | 把静态可行当性能达标 | 两种结论在数据结构上就是不同的字段（`feasibility` vs `sla_status`） |

---

## 三、关键实测输出

### 3.1 匹配：目标需求 vs 全源数据

```text
$ match-benchmark --model "Qwen/Qwen3-32B" --hardware "NVIDIA H100 SXM" --gpu-count 4 --tp 4 \
    --input-len 8192 --output-len 256
匹配等级分布: {'rejected': 55}
非拒绝项: 0 条
```

55 = 32 条 Excel 历史记录 + 4 条 synthetic fixture + 12 条数值候选 + 6 条页面候选 + 1 条导入的性能记录。
**全部被拒绝是正确行为**：没有任何一条数据是在 Qwen3-32B + H100 上测出来的。

### 3.2 容量曲线（合成样本，仅算法验证）

```text
$ capacity-profile --target-concurrency 8
记录 5 条，分成 1 个配置组
  点数=5 覆盖=sufficient 峰值并发=16 输出=380.0 可持续容量=16
```

| 配置并发 | 输出 tok/s | TTFT p99 | TPOT p99 | ITL p99 |
|---|---|---|---|---|
| 1 | 45.0 | 120.0 | 12.0 | 12.6 |
| 2 | 88.0 | 150.0 | 14.0 | 14.8 |
| 4 | 165.0 | 210.0 | 18.0 | 19.2 |
| 8 | 280.0 | 340.0 | 26.0 | 27.5 |
| 16 | 380.0 | 720.0 | 45.0 | 47.0 |

`--exclude-synthetic` 时输出 `分成 0 个配置组`——**正式结论不会碰到 synthetic 数据**。

### 3.3 估算（M22）

```text
$ calibrate-performance --metric output_throughput_tok_s --concurrency 6
estimate_kind = calibrated_estimate
value = 222.5        （介于 c=4 的 165 与 c=8 的 280 之间）
holdout_mae = 19.77777777777777777777777777
holdout_relative_error = 0.103224
covered_range = [1, 16]
```

并发 64（范围外）→ `not_predictable`；四个 P99 → `not_predictable`。

### 3.4 SLA

```text
$ evaluate-sla --requirement '{"requirement_id":"req-default","ttft_p99_ms_max":400}' \
    --run-id fixture_perf_curve_c4 …（默认策略）
SLA 状态: not_evaluable       # synthetic 被生产策略拒绝

$ evaluate-sla … --policy-id algorithm-validation-only --allow-synthetic
SLA 状态: observed_pass
  注意：该结论仅用于算法验证（synthetic 数据），不得对外使用
```

同一份数据在两种策略下给出不同结论，且后者被显式打标——这是"算法可验证、结论不可对外"的实现方式。

### 3.5 候选排序（M24，目标配置的真实情况）

```text
$ rank-deployments --model qwen3-32b --hardware nvidia-h100-sxm-80gb --tp-sizes 2,4,8 --dp-sizes 1 …
分桶: {'requires_benchmark': 2, 'excluded': 1}
阻塞: requires_benchmark: 所有静态可行候选都缺少匹配的目标硬件实测数据
```

| 候选 | 静态状态 | 分桶 |
|---|---|---|
| TP4×DP1 | conditional_feasible | `requires_benchmark` |
| TP8×DP1 | conditional_feasible | `requires_benchmark` |
| TP2×DP1 | infeasible | `excluded` |

---

## 四、验证命令与结果

```bash
bash scripts/verify_all.sh      # 退出码 0；步骤 36 项：通过 36，失败 0
.venv/bin/python -m pytest -q   # 460 passed
```

| 步骤 | 实测输出 |
|---|---|
| 24 | `已导入 5 条性能记录`，`可信等级: ['synthetic_fixture']`，附"仅用于算法验证"说明 |
| 25 | `分成 0 个配置组`（排除 synthetic 后无正式曲线） |
| 26 | `点数=5 覆盖=sufficient 峰值并发=16 输出=380.0 可持续容量=16` |
| 27 | `estimate_kind: calibrated_estimate` + 留出误差 |
| 28 | `匹配等级分布: {'rejected': 55}` |
| 29 | `SLA 状态: not_evaluable` |
| 30 | `SLA 状态: observed_pass` + `注意：该结论仅用于算法验证` |
| 31 | `分桶: {'requires_benchmark': 2, 'excluded': 1}` + 阻塞说明 |
| 32 | `报告已写入 data/exports/performance_report.md` |
| 33 | `已跟踪文件中未发现内部定价数值`（V1.2 门禁仍生效） |
| 34 | `460 passed` |

---

## 五、下一步：如何把"未验证"变成"已验证"

任务书 §4 已给出建议实验矩阵，落地时**按此补测即可接入**（无需再改代码）：

| 维度 | 建议取值 | 备注 |
|---|---|---|
| 模型 | Qwen3-32B BF16（记录 revision） | 与 `data/models/` 归档的 revision 对齐 |
| GPU | H100 SXM / H200 SXM | 同时记录 `nvidia-smi` 实测可用显存 |
| 部署候选 | TP4×DP1、TP2×DP2 | 正是第二阶段判定为"条件可行"的两组 |
| 输入/输出 | 1K/1K、8K/256、32K/256 | 32K 需确认模型上下文上限（声明 40960，可用） |
| 并发 | 1、2、4、8、16、32 | ≥3 点才能做范围内插值，≥5 点曲线才有意义 |
| 必采指标 | TTFT P99、TPOT P99、ITL P99、E2E P99、Input/Output TPS、失败率 | 缺 P99 的项会被判 `not_evaluable` |
| 必存元数据 | 模型 revision、框架版本、完整启动与测试命令、实际 token 长度、卡数与测试持续时间 | 持续时间决定能否给出"可持续容量" |
| 两类负载 | 固定长度标准测试 + 多轮/前缀复用测试 | **分文件导入**，不得混入同一条曲线 |

导入方式（无需 GPU、无需联网）：

```bash
python -m llm_capacity_pricing.cli import-performance \
  --file <bench_result.json> --metadata <meta.yaml> --kind vllm --db data/local.db
```

补测后的预期变化：`match-benchmark` 对目标配置给出 `exact_match`；
`evaluate-sla`（默认策略）给出 `observed_pass`/`observed_fail`；
`rank-deployments` 的 `verified_sla_met` 桶被填充。

---

## 六、是否达到第三阶段完成判定

任务书要求：*能完成一次完整流程——业务场景输入 → 资源筛选 → Benchmark 匹配 → 性能分析 →
SLA 评估 → 候选排序或返回证据缺口*。

| 判定项 | 结论 |
|---|---|
| 工程功能验收 | **通过**（36 步全绿、460 passed、7 个 CLI、JSON/Markdown 报告） |
| 性能可信度验收 | **未验证**（缺目标硬件实测，属预期；已如实输出 `not_evaluable` / `requires_benchmark`） |
| 是否越界 | **无**（未按 FLOPS 推算、未跨配置外推、未用均值代 P99、未用 synthetic 出正式结论） |
| 第一阶段回归 | **不变**（32 / 160 / 192 / 0 超容差） |

**结论：第三阶段工程功能完成、可交付；性能可信度维度保持未验证，等待现场实测数据接入。**
