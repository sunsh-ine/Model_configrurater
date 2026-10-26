# AH 客户场景端到端回放

基线：`01a1fb3`

生成脚本：`scripts/ah_customer_scenarios.py`
输出：`docs/ah_scenarios/<场景>/{scenario.md,summary.json,proposal.md,technical_report.md}`

三个场景均由同一次 `evaluate_plan()` 生成；客户版方案与内部报告引用同一份
`decision_evidence`，不手工改数。

## 场景 A：同资源不同并行策略

Qwen3-32B / H100 / BF16 / 8 GPU / ISL8192 / OSL256 / C32 / SLA 6000-40。

| 目标 | 候选 | GPU |
|---|---|---|
| 严格推荐 | `sim-native...tp4pp1dp2-c32-n500` | 8 |
| 最少 GPU 达标 | 同 TP4×DP2 | 8 |
| 吞吐最优达标 | 同 TP4×DP2 | 8 |

TP8×DP1 预测 TTFT 5201.83 / TPOT 31.46；TP4×DP2 预测 4448.28 / 28.82。
同 8 卡时 TP4×DP2 在本轮预测中优于 TP8×DP1。两者均为模拟预测，校准 unknown。

## 场景 B：最低 GPU 数满足 SLA

Qwen3-8B / H100 / BF16 / ISL8192 / OSL256 / C32 / SLA 6000-40。

| 目标 | 候选 | GPU |
|---|---|---|
| 严格推荐 | TP1×DP4 | 4 |
| 最少 GPU 达标 | TP1×DP2 | 2 |
| 吞吐最优达标 | TP1×DP4 | 4 |

DP1 预测未达标；DP2 是满足 SLA 的最小配置；DP4 吞吐更高。
“最少 GPU”与“最高吞吐”分开呈现，不合并成一个“最优”。

## 场景 C：开放到达与随机性风险

Qwen3-8B / H200 / TP1×DP2 / Poisson 5 req/s / SLA 6000-40。

历史 5 个 seed：3/5 通过，实验完整性 `complete`、重复性 `mixed`。
`mixed` 不进入正式推荐池，因此：

* 严格推荐：无；
* 最少 GPU 达标：无；
* 吞吐最优达标：无。

即使某个 seed 的 TPOT P99 低于 40 ms，也不能单独作为正式达标依据。

## 共同边界

* 全部为离线归档模拟，真实 GPU Benchmark 为 0；
* 校准状态均为 `unknown`；
* 报价资格仍为不可发布；
* 不跨工作点复用 P99；
* 场景可重复执行，结果不依赖人工编辑报告。
