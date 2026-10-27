# AI 阶段验收：RTX 3090 真实 Benchmark 与证据闭环

基线：`fd539e6`
目标硬件：`10.0.102.61` 单张 NVIDIA RTX 3090 24GB
目标模型：Qwen3-8B BF16
推理框架：vLLM 0.11.0（SGLang 未安装；按任务书允许的替代路径）

## 已通过

### AI0 AH 遗留整改

* 预注册清单 `aborted` 状态可重新加载，并有状态转换测试。
* Benchmark 三档资格分离：`ingest_accepted` / `sla_evaluable` / `calibration_usable`。
* 来源认证要求 environment SHA256、run manifest 文件与哈希；归档与登记分开。
* 允许变化字段精确到配置路径（`traffic.load.seed`）。
* 客户端工具与服务端推理框架分开记录。

### AI1 环境与部署

* RTX 3090 24GB、SM86、driver 570.124.04、CUDA 12.8。
* 独立 venv：`/user-storage/ai-bench/venv128`。
* vLLM 0.11.0 + torch 2.8.0+cu128 + transformers 4.57.1。
* 本地 Qwen3-8B BF16 权重，未联网下载。
* 服务端口 18000，未干扰既有 gunicorn（127.0.0.1:8000）。
* 详见 `ai_rtx3090_environment.md`。

### AI2/AI3 实测

* 自定义 OpenAI 流式客户端保留逐请求 arrival/TTFT/latency/output/status。
* 正式点：2048/256、C8、500 请求 × 5 独立 seed。
* 5/5 成功，TTFT P99 1765–2220 ms，TPOT P99 38.26–39.62 ms，
  输出吞吐 197.3–198.8 tok/s。
* 正式结果 5/5 同时满足 TTFT≤6000 ms、TPOT≤40 ms。
* 探索点：C4、4096 C8、8192 C2、Poisson 0.5 req/s；
  只作能力/瓶颈分析，不作为正式 SLA 证据。
* 详见 `ai_rtx3090_results.md` / `ai_benchmark_protocol.md`。

### AI4 认证与入库

* 5 个正式运行完成：原始逐请求日志 → 独立重算 → 来源登记 →
  `BenchmarkRun` 入库 → `attest-run` → 查询 → 报告。
* 原始数据、环境、身份、配置与 run manifest 均有 SHA256。
* 运行可信等级：`measured_internal`；资格：`sla_evaluable=true`。
* 数据位于 `data/benchmark/rtx3090/`，登记表
  `data/benchmark/source_attestations.json` 仅 5 条正式运行。

### AI5 校准边界

* AISimulate 0.12.1 没有 `rtx3090` 系统；按情况 B，校准保持 `unknown`。
* 不使用 H100/H200 预测替代 3090，不做跨 GPU P99 复用。
* 详见 `ai_calibration_analysis.md`。

### AI6 客户场景 D

* `docs/ai_scenario_d/` 输出客户方案与内部报告。
* 1×RTX 3090、2048/256、C8、SLA 6000/40：
  `observed_pass`、`exact_match`、`verified_sla_met`。
* 5 个实测运行均作为证据列出。
* 详见 `ai_customer_scenario.md`。

### AI7 成本与报价

* 没有已确认的 RTX 3090 成本；成本/报价领域保持未配置/不可发布。
* 实测 SLA 达标不解锁报价资格；`quote_publishable=false`。

### AI8 回归

* 全量 pytest：**1179 passed**。
* live `verify_all.sh`：**66/66 通过**。
* 自动搜索：`run_20261027T071451Z_620c3a`。
* 系统 Demo：`20261027T071501Z_debe99`。
* 覆盖矩阵：309 条模拟/参考证据、79 个模拟 SLA 工作点；
  另计 **5 次已验证真实运行、1 个真实工作点**。

## 已修复

codex `gpt-6-sol`（low）与 OMP 审核发现并修复：

* `aborted` 预注册清单无法重载；
* 来源认证只看字段、不校验 run manifest/环境；
* 归档命令可自我登记认证；
* 指标缺失/窗口未覆盖仍可获得 SLA 资格；
* 成功流没有 finish_reason 仍被算成功；
* 客户端/服务端框架混写；
* 预注册允许变化字段过宽；
* 测量窗口无到达时间仍被当作已验证；
* 真实覆盖率统计包含未认证探索运行；
* 实测通过候选绕过阻断条件被推荐。

## 未确认

* RTX 3090 的长期稳定性：只有有限请求窗口，没有全局队列长度时间序列；
* 4096/8192、C4/C8 之外的并发与上下文组合未验证；
* 多卡 TP/DP 未验证，不能由单卡结果推算；
* 真实预测误差：AISimulate 无 3090 支持，保持 unknown；
* 生产级 SLA 概率：5 次运行不是统计显著估计。

## 未实现

* SGLang 在 SM86 上的实测（未安装/未验证）；
* 真实 GPU 多卡、PD 分离、PP/DP 扩展；
* 已确认的 RTX 3090 成本与正式报价发布。
