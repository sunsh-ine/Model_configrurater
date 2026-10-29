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
* 时延精度：归档 JSONL 中 `arrival_ms` / `ttft_ms` / `latency_ms` 保留 3 位小数（微秒级）；
  这是记录精度，不改变客户端测量值。

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

## 3.1 客户端与服务端口径隔离（AJ0.4）

* AI/AJ 正式数据使用 `scripts/benchmark_client.py`（OpenAI 兼容流式客户端）；
* vLLM `bench serve --save-detailed` 是另一套客户端，其 TTFT/ITL 统计口径不同；
* 两者**不得**在未核对口径的情况下混合比较或合并成一个容量结论；
* metadata 分别记录 `benchmark_client` 与 `framework`（服务端框架）。

## 3.2 冻结运行配置（AJ0.1）

每条 AJ 运行记录 `runtime_config_sha256`，覆盖服务端版本、dtype/KV 精度、
max-model-len、max-num-seqs、gpu-memory-utilization、CUDA Graph/enforce-eager、
prefix cache、chunked prefill 与客户端统计口径。同一工作点的运行必须使用相同哈希；
历史 AI 数据不能回填未记录的配置，保持 `unknown`。

AJ 正式重复：

* C6（`aj-formal-c6` 预注册）：5 个 seed × 500 请求，其中 1 次 499/500；
  延迟有内部裕量，但可靠性不通过，不作为容量；
* C8（冻结配置复现）：5 个 seed × 500 请求全部成功，作为本轮推荐容量；
* AI 阶段 5 次 C8 运行未绑定配置哈希，标记 `legacy_unbound`，只作延迟参考。

## 4. 已确认限制

* 8192 输入需要 `max-model-len >= 8448`（256 输出预留）；8192 输入下 C2
  吞吐约 0.19 req/s，500 请求成本过高，因此只做探索。
* RTX 3090 实测不能替代 H100/H200 模拟校准。
* 没有全局队列长度时间序列，E5 只能报告客户端/服务端已观测指标。
