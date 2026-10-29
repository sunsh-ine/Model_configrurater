# AJ7 客户场景 E：RTX 3090 可持续容量与推荐并发

基线：`d32fe5b`；场景由 `scripts/aj_scenario_e.py` 生成，输出在 `docs/aj_scenario_e/`。

## 1. 客户需求

| 项 | 值 |
|---|---|
| 模型 | Qwen3-8B BF16 |
| 硬件 | 单张 NVIDIA RTX 3090 24GB |
| 负载 | ISL 2048 / OSL 256，固定并发 8 |
| SLA | TTFT P99 ≤ 6000 ms，TPOT P99 ≤ 40 ms |
| GPU 上限 | 1 |
| 运行配置 | 冻结哈希 `cb8719a6…50c594c4`（vLLM 0.11.0，max-model-len 8448，max-num-seqs 8） |
| 成本 | 未提供已确认的 3090 成本 |

## 2. 容量证据

来自 `docs/aj_capacity_scan.json`（AJ2/AJ3）：

| 结论 | 值 | 依据 |
|---|---|---|
| 最高输出吞吐 | 198.55 tok/s | `aj-e1c8-seed3`（2048/256，C8，正式） |
| 最高 SLA Goodput | 0.776 req/s | 同一工作点 |
| 最大正式验证固定并发 | C8 | 5 次 500 请求全部成功、全部满足 SLA |
| 10% / 5% 内部裕量 | 无 | 最差 TPOT P99 39.39 ms > 38 ms |
| 最大窗口稳态开放到达率 | 无（不作候选） | 0.65 req/s 只有 1 次 500 请求窗口，未达 5 次正式重复 |

限制：

- C6 正式 5×500 的 TPOT P99 为 35.49 ms，有 10%/5% 延迟裕量，
  但 1 次运行出现 1/500 失败（`aj-e1c6-seed1`），可靠性不通过，
  因此**不能**推荐 C6 作为裕量安全容量。
- 固定并发容量与 Poisson 稳态容量是独立结论，不能互相换算；
  0.65 req/s 在单个 500 请求窗口内稳定，但没有 5 次正式重复，
  只作为开放到达观察，不进入容量推荐。
- 没有服务端队列时间序列，`queue_status=unobserved`。

## 3. 场景评估

`scripts/aj_scenario_e.py` 用上述推荐（固定并发 C8）调用统一 `plan` 入口：

| 项 | 结果 |
|---|---|
| 方案状态 | `partial`（成本与报价资格未配置；不是性能失败） |
| 候选 | `qwen3-8b__nvidia-rtx-3090-24gb__tp1_dp1_bf16` |
| 匹配等级 | `exact_match`（同模型/精度/硬件/并行/冻结配置） |
| SLA 结论 | `observed_pass` |
| 证据分桶 | `verified_sla_met` |
| 实测 P99 | TTFT 1831.8 ms、TPOT 39.39 ms |
| 实测吞吐 | 197.25 tok/s |
| 证据 | `rtx3090:aj-e1c8-seed1…seed5` |
| 推荐来源 | `measured` |
| 校准 | `unknown`（AISimulate 无 3090 系统） |
| 报价发布 | 不可发布（无已确认成本、无审批） |

`PlanResult.capacity_evidence` 保存了完整容量结论（固定并发、开放到达、
裕量候选、限制），并随 `decision_evidence` 一起写入方案 JSON。

## 4. 未验证与限制

- 只验证了单卡、2048/256、C8 与 0.65 req/s；其它 ISL/OSL/并发未验证。
- C8 贴近 TPOT 上限，生产部署应保留额外余量；本系统不把“实测刚好达标”
  升级为生产级 SLA 概率。
- 多卡 TP/DP、PD 分离、SGLang 后端均未验证，不能由单卡结果推算。
- 没有已确认的 RTX 3090 成本；成本与报价领域保持未配置/不可发布。
- 5 次正式运行是观察结果，不是统计显著的生产 SLA 估计。
