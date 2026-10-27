# AI6 客户场景 D：Qwen3-8B / RTX 3090 24GB

基线：`fd539e6`；场景由 `scripts/ai_scenario_d.py` 生成，输出在 `docs/ai_scenario_d/`。

## 1. 客户需求

| 项 | 值 |
|---|---|
| 模型 | Qwen3-8B BF16 |
| 硬件 | 单张 NVIDIA RTX 3090 24GB |
| 负载 | ISL 2048 / OSL 256，固定并发 8 |
| SLA | TTFT P99 ≤ 6000 ms，TPOT P99 ≤ 40 ms |
| GPU 上限 | 1 |
| 成本 | 未提供已确认的 3090 成本 |

## 2. 实测证据

5 次独立运行（每 500 请求，全部成功），由 `scripts/benchmark_client.py`
采集逐请求 TTFT/TPOT/E2E，并由 `vllm 0.11.0` 服务端执行：

| Seed | TTFT P99 | TPOT P99 | 输出 tok/s | 结果 |
|---|---|---|---|---|
| 1 | 2220.33 ms | 38.26 ms | 197.97 | pass |
| 2 | 1765.26 ms | 39.22 ms | 198.29 | pass |
| 3 | 1801.10 ms | 39.62 ms | 197.31 | pass |
| 4 | 1813.72 ms | 39.36 ms | 197.62 | pass |
| 5 | 1799.35 ms | 38.43 ms | 198.78 | pass |

- 全部 5/5 满足 TTFT≤6000 ms、TPOT≤40 ms；
- 原始证据：`data/benchmark/rtx3090/ai-e1c8-seed{1..5}/`；
- 证据 ID：`rtx3090:ai-e1c8-seed1` … `seed5`；
- 数据可信等级：`measured_internal`（受控执行登记 + 显式 `attest-run`）。

## 3. 方案结论

- 严格推荐：`qwen3-8b__nvidia-rtx-3090-24gb__tp1_dp1_bf16`（1 卡，TP1×DP1）；
- 最少 GPU 达标：同配置（1 卡）；
- 吞吐最优达标：同配置（单卡场景）。
- 性能：`observed_pass`，匹配等级 `exact_match`，证据分桶 `verified_sla_met`。

## 4. 未验证与限制

- 只验证了单卡、2048/256、固定并发 8；其它 ISL/OSL/并发未验证；
- 多卡 TP/DP 未验证，不得由单卡结果推算多卡容量；
- 没有 3090 的 AISimulate 预测，校准状态 `unknown`；
- 没有已确认的 RTX 3090 成本，不给出最低成本排序；
- 报价资格仍为不可发布；
- 5 次运行是观察结果，不是生产级 SLA 概率估计。
