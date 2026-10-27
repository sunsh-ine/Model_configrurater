# AI2/AI3 RTX 3090 Benchmark 协议与实验设计

基线：`fd539e6`；硬件/部署见 `ai_rtx3090_environment.md`。

## 1. 客户端

* 客户端：vLLM 0.11.0 `vllm bench serve`
* 后端：OpenAI-compatible `/v1/completions`
* 输入：`--dataset-name random --random-input-len`（精确 token 数）
* 输出：`--random-output-len`
* 保留：`--save-result --save-detailed`，detailed JSON 含逐请求 TTFT、ITL 列表、
  input/output 长度与错误。
* 独立重算：`scripts/vllm_detailed_to_requests.py` 转成逐请求 JSONL；
  E2E = TTFT + ΣITL，TPOT = (E2E - TTFT)/(output_len-1)。
* 测量窗口：vLLM 报告的整体 `duration` 作为显式窗口；不把单请求时延当窗口。

## 2. 指标资格

| 层 | 条件 |
|---|---|
| `ingest_accepted` | 原始日志/身份/哈希/格式通过 |
| `sla_evaluable` | 来源认证 + 全部成功请求指标完整 + TTFT/TPOT 样本 ≥30 + 显式测量窗口 + 成功请求 ≥30 |
| `calibration_usable` | SLA 资格 + 身份完全匹配 + 预测可用 + 校准契约通过 |

 exploratory 数据可以入库，但不自动获得 SLA/校准资格。

## 3. 实验矩阵

| 组 | ISL | OSL | 并发 | 性质 |
|---|---|---|---|---|
| E0 | 512 | 128 | 1 | 冒烟 |
| E1 | 2048 | 256 | 1/2/4/8 | 探索；**C8 为正式重复点** |
| E2 | 4096 | 256 | 2/4 | 探索 |
| E3 | 8192 | 256 | 2 | 长上下文探索 |
| E4 | 8192 | 256 | 2→4 | 显存/并发边界探索 |
| E5 | 2048 | 256 | Poisson 0.5 req/s | 开放到达探索 |

正式点：E1 C8，每个 seed 500 完成请求，5 个独立 seed；不删除失败 seed。
exploratory 点使用较少请求，只用于能力/瓶颈判断，不作为正式 SLA 证据。

## 4. 已确认限制

* 8192 输入需要 `max-model-len >= 8448`（256 输出预留）；8192 输入下 C2
  吞吐约 0.19 req/s，500 请求成本过高，因此只做探索。
* RTX 3090 实测不能替代 H100/H200 模拟校准。
* 没有全局队列长度时间序列，E5 只能报告客户端/服务端已观测指标。
