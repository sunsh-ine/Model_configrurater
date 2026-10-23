# AC 阶段验收：多模型、多硬件适配与部署决策

基线：`a91daea`（AB 阶段封板）
执行日期：2026-10-22

> 本阶段仍只有 AISimulate 的**预测**，没有真实 GPU Benchmark。
> 所有新增工作点都是 0.12.1 的真实原生回放结果；`predicted_*` 不会写成
> `observed_*`，也不会进入报价门禁。

## 0. 结论

| 项目 | 结论 |
|---|---|
| AC0 AB 遗留问题 | 已修复：归档登记/读取一致、符号链接拒绝、元数据交叉校验、审计计数、文档百分比 |
| AC1 能力探测 | Qwen3-8B/H100、Qwen3-32B/H200、Qwen3-8B/H200 均 `supported` |
| AC2 多硬件链路 | 按每个硬件独立模拟；无匹配归档的硬件只保留静态结论 |
| AC3 新增证据 | 新增 20 个原生回放归档，覆盖 3 个模型/硬件组合 |
| AC4 跨硬件决策 | 同模型 H100/H200 独立证据比较；H200 TP8 在 C32 优于 H100 TP8 |
| AC5 回归 | 全量测试、live verify_all、Codex 审核见 §6 |
| 真实 GPU Benchmark | 仍为 0 |

## 1. AC0：AB 遗留问题

1. `archive_work_dir()` 现在会把嵌套搜索配置统一复制为归档根
   `exp_config.yaml`，并登记到 `manifest.inputs`；加载器读取的每个文件都受
   SHA256 覆盖。新增反向测试覆盖“文件存在但未登记”“登记后删除/篡改”。
2. 归档目录、manifest、预测/输入文件出现符号链接时拒绝加载。
3. 新增 `verify_predict_metadata()`：manifest 的 `spec`/`workpoint` 与
   `predict_N.yaml` 的模型、system、后端/版本、ISL/OSL/并发、TP/PP/DP
   必须一致；声明 TP8 但输入 TP4 会记录元数据冲突并拒绝作为证据。
   历史 PoC 输入已显式写入 `pipeline: 1`，未改变预测值。
4. 审计计数：候选级失败统一进入 `candidate_errors`，重放失败同时写入
   `failed_predicts`；`failed_deployments` 按候选计数，资格校验异常不再出现
   “failed=0 但 scope=partial”。
5. `docs/ab_phase_validation.md` 的 TP4 TTFT 裕量比例已修正为 **-39.3%**
   （绝对裕量 -2359.44 ms 不变）。

## 2. AC1：AISimulate 能力探测

真实执行 `aisimulate predict`（0.12.1、无 GPU、500 请求、BF16、
vLLM 0.24.0、ISL8192/OSL256/C32）：

| 组合 | 状态 | 首个成功 TP | TTFT P99 | TPOT P99 | 吞吐 |
|---|---|---|---|---|---|
| Qwen3-8B @ H100 SXM | supported | TP1 | 6609.39 ms | 46.60 ms | 671.62 tok/s |
| Qwen3-32B @ H200 SXM | supported | TP2 | 14356.54 ms | 79.91 ms | 390.36 tok/s |
| Qwen3-8B @ H200 SXM | supported | TP1 | 6286.38 ms | 40.78 ms | 767.04 tok/s |

> `predict` 配置不暴露 `database_mode`；能力探测不声明 SILICON。

机器可读：`data/simulation/capabilities/simulation_capabilities.json`；
摘要：`docs/ac_capability_matrix.md`。每个 case 的原始输入、prediction、
stderr 与 SHA256 都在 `data/simulation/capabilities/<case_id>/`。

说明：能力探测只证明“能跑出完整 P99”，不代表预测准确；未支持组合会记录
`unsupported`/`unknown` 与原始错误，不用其它硬件/模型数据替代。

## 3. AC2：多硬件模拟链路

`workflow._simulate_all()` 按 `request.hardware_ids` 逐个调用原有
`_simulate()`：

* 每个硬件独立构造 `SimulationTarget`、匹配证据、跑资源门槛与 SLA；
* 离线 `SimulationArchiveSpec.hardware_id` 决定归档归属，**不允许跨硬件复用**；
* 某硬件没有匹配归档时，返回 `missing_data` 并保留静态结论，不参与 P99 排名；
* 合并后的候选仍由既有 `recommended()` / `min_gpu_for_sla()` 排序；
* 单硬件路径与旧 JSON/CLI/Agent 行为保持不变。

测试：`tests/test_ac_multi_hardware.py`（H100+H200 独立候选、缺失归档只静态、
单硬件回归、跨硬件报告）。

## 4. AC3：新增模型/硬件证据

新增 20 个原生回放归档（`data/simulation/archives/ac-live-*`）：

* Qwen3-8B / H100 / TP1：2 负载 × C8/C32/C64 = 6；
* Qwen3-8B / H200 / TP1：6；
* Qwen3-32B / H200 / TP2：6；
* Qwen3-32B / H200 / TP4、TP8：各 1 个 C32/8192 对照。

关键结果（ISL8192/OSL256）：

| 模型 / 硬件 / TP | C8 | C32 | C64 |
|---|---|---|---|
| Qwen3-8B / H100 / TP1 | 887.92 / 16.01 | 6609.39 / 46.60 | 19798.39 / 76.13 |
| Qwen3-8B / H200 / TP1 | 859.74 / 13.59 | 6286.38 / 40.78 | 13681.38 / 75.52 |
| Qwen3-32B / H200 / TP2 | 1995.56 / 26.65 | 14356.54 / 79.91 | 30866.99 / 148.81 |

单位：TTFT P99 / TPOT P99（ms）。完整矩阵（含 1024/1024 与 H100 32B）见
`docs/performance_coverage.json`。覆盖矩阵当前为 **32 个 SLA 可用工作点**、
37 条原生证据、0 条真实实测。

观察：

* 8B 单卡在 C32 两个硬件都略超 6000/40（TTFT/TPOT 之一），C8 均达标；
* 32B/H200 的 TP2 明显不足，TP4 仍超 6000/40，TP8 达标；
* 不能由 8B 或 H100 的结果推断 H200/32B，也不能跨模型替代。

## 5. AC4：跨硬件部署决策

同模型、同精度、同框架/负载，H100 与 H200 各自使用独立证据：

C32 / ISL8192 / relaxed 6000/40：

| 硬件 / TP | TTFT P99 | TPOT P99 | 吞吐 | 结论 |
|---|---|---|---|---|
| H100 / TP8 | 5201.83 | 31.46 | 990.89 | 预测达标 |
| H200 / TP8 | 4955.54 | 28.77 | 1083.00 | 预测达标 |
| H200 / TP4 | 8481.04 | 47.49 | 656.43 | 未达标 |
| H200 / TP2 | 14356.54 | 79.91 | 390.36 | 未达标 |

AC 时点的多硬件测试中，吞吐优先推荐 H200/TP8；AD 阶段补充 TP4×DP2 证据后，
C32 的推荐变为 TP4×DP2（8 卡，吞吐更高）。AC 的 TP8 结果仍是有效证据，
只是不再是最高吞吐的达标配置。报告同时给出各候选 SLA 裕量、敏感性、
每 GPU 吞吐与搜索审计。

不同模型之间**不直接排名**：Qwen3-8B 与 Qwen3-32B 的能力/回答质量不可用吞吐
替代；没有业务质量门禁时分别展示，不宣称某模型综合最优。

## 6. 验收执行

| 项目 | 结果 |
|---|---|
| 全量测试（`pytest -q`） | `1077 passed, 1 skipped` |
| 全链路校验（`AISIMULATE_PYTHON=... verify_all.sh`） | `1078 passed`（live 测试不再 skip），66 / 66 步通过；第 61 步运行 `run_20261022T130718Z_89c6a6` |
| 记录口径说明 | 两个数字对应不同命令/环境：手动 pytest 跳过 1 个 live 测试，verify_all 配置了 AISIMULATE_PYTHON 后实际执行它 |
| Codex 审核 | `gpt-6-sol` + `low`（按当前 AGENTS.md）六轮：P1 4 项、P2 3 项，均已修复；末轮无新增可执行缺陷 |

## 7. 仍未完成

* 真实 GPU Benchmark 与预测误差校准仍为 0；
* AC 新增的是 AISimulate 支持边界内的组合，不代表工具支持所有模型/GPU；
* DP/PP、MoE、PD 分离等拓扑仍未验证；
* 成本仍为合成示例，正式报价发布保持禁用；
* N5/N6 未恢复。
