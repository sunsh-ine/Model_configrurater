# AD 阶段验收：并行策略与 SLA 容量规划

基线：`eb2cbaf`（AC 阶段封板）
执行日期：2026-10-23

> 本阶段新增的并行结果全部是 AISimulate 预测；没有真实 GPU Benchmark。
> DP 副本只使用模拟器对整个部署的输出，不用单副本吞吐乘倍数。

## 0. 结论

| 项目 | 结论 |
|---|---|
| AD0 AC 遗留问题 | 已修复：未登记 `exp_config` 不再被读取、逐候选来源、测试记录口径 |
| AD1 并行能力探测 | TP/DP/PP 候选均返回 `supported`；PP 仅保留探测，不进入推荐 |
| AD2 TP/DP 接入 | 普通 DP 副本可使用 `parallelism.replicas` 表达；PP 仍 fail-closed |
| AD3 同资源比较 | TP8×DP1 与 TP4×DP2 可比较；DP2 在 32B/H100/H200 C32 均更优 |
| AD4 SLA 容量 | 只报告已验证并发点；8B/H100 DP2 最高已验证达标为 C32 |
| AD5 负载边界 | Poisson 到达可执行；固定并发结论不外推到开放到达流量 |
| AD6 回归 | 全量测试、live verify_all、Codex 审核见 §6 |
| 真实 GPU Benchmark | 仍为 0 |

## 1. AD0：AC 遗留问题

1. **未登记 exp_config 读取**：`ArchiveEntry.exp_config` 只在
   `exp_config.yaml` 出现在 manifest `inputs`/`outputs` 时返回；文件存在但未登记
   时不再暴露给 `load_archived()`。新增反向测试。
2. **逐候选来源**：`SimulationCandidate` 增加
   `source_tool_version` / `source_database_mode`，并在候选 notes 中写明
   “证据工具：aisimulate x，数据库模式 y”，避免把全局 `tool` 当成每个候选共享。
3. **测试记录口径**：AC 文档注明 `pytest -q` 为 1077 passed / 1 skipped；
   `verify_all.sh` 在 `AISIMULATE_PYTHON` 下会多跑 1 个 live 测试，
   因此日志为 1078 passed。两者对应不同命令/环境，不是结果矛盾。
4. 原有 32 个 SLA 工作点及其预测值不变。

## 2. AD1：并行能力探测

`data/simulation/capabilities/parallelism/parallelism_capabilities.json`，
摘要 `docs/ad_parallelism_matrix.md`。工具：aisimulate 0.12.1、
ISL8192/OSL256/C32/500 请求。

| 组合 | TP×DP×PP | 状态 |
|---|---|---|
| Qwen3-8B / H100 | 1×1×1、1×2×1、1×4×1、1×1×2 | supported |
| Qwen3-8B / H200 | 1×1×1、1×2×1 | supported |
| Qwen3-32B / H100 | 8×1×1、4×2×1 | supported |
| Qwen3-32B / H200 | 8×1×1、4×2×1、4×1×2 | supported |

DP 观测（C32）：

* 8B/H100 TP1：DP1 6609/46.60/671.6 → DP2 3486/27.42/1127.8 →
  DP4 1827/16.01/1880.7；
* 8B/H200 TP1：DP1 6286/40.78/767.0 → DP2 3330/22.92/1342.0；
* 32B/H100 TP4：DP1 8359/50.17/621.9 → DP2 4448/28.82/1062.4；
* 32B/H200 TP4：DP1 8481/47.49/656.4 → DP2 4521.63/26.84/1138.2。

PP2 也能生成 P99，但 `pipeline>1` 的资源/调度模型与本阶段假设不同，
因此只记录能力，不开放推荐。

## 3. AD2：TP/DP 接入

* `parallelism.replicas` 作为普通 DP 副本数；总卡数 =
  `decode_gpus_per_worker × replicas`。
* `deployment_identity_reasons()` 允许 DP≥1（已探测），仍拒绝 PP>1。
* 归档 `workpoint` 记录 TP/PP/DP；离线选择按硬件/工作点/TP 过滤，
  DP 候选分别参与资源门槛与 SLA。
* 不使用“单副本吞吐 × DP”估算总吞吐；所有数值来自整部署的 prediction。
* DP-Attention、EP、PD 分离没有映射为普通 DP；仍是独立未支持状态。

## 4. AD3：同资源预算比较

C32 / ISL8192 / relaxed 6000/40：

| 模型/硬件 | 部署 | GPU | TTFT P99 | TPOT P99 | 吞吐 | 结论 |
|---|---|---|---|---|---|---|
| 32B/H100 | TP8×DP1 | 8 | 5201.83 | 31.46 | 990.89 | 达标 |
| 32B/H100 | TP4×DP2 | 8 | 4448.28 | 28.82 | 1062.45 | 达标，推荐 |
| 32B/H200 | TP8×DP1 | 8 | 4955.54 | 28.77 | 1083.00 | 达标 |
| 32B/H200 | TP4×DP2 | 8 | 4521.63 | 26.84 | 1138.19 | 达标，推荐 |
| 8B/H100 | TP1×DP1 | 1 | 6609.39 | 46.60 | 671.62 | 未达标 |
| 8B/H100 | TP1×DP2 | 2 | 3486.43 | 27.42 | 1127.78 | 达标 |
| 8B/H100 | TP1×DP4 | 4 | 1827.79 | 16.01 | 1880.72 | 达标 |
| 8B/H200 | TP1×DP1 | 1 | 6286.38 | 40.78 | 767.04 | 未达标 |
| 8B/H200 | TP1×DP2 | 2 | 3330.33 | 22.92 | 1341.99 | 达标 |

8B/H100 C32：吞吐优先推荐 DP4；满足 SLA 的最小配置是 DP2（2 卡）。
这说明“副本扩展”确实能改变 SLA 结论，但收益不是无限：C64 起两者都失败。

## 5. AD4：SLA 容量边界

脚本：`scripts/capacity_scan.py`（只读归档，不做插值）。

* 8B/H100 TP1×DP2，ISL8192/OSL256，6000/40：
  C8 达标、C32 达标、C64/96/128 未达标；已验证最高达标并发 **C32**。
  详见 `docs/ad_capacity_8b_h100_dp2.md`。
* 32B/H200 TP4×DP2，同负载/SLA：
  C8/C32 达标，C64/96/128 未达标；最高已验证 **C32**。
  详见 `docs/ad_capacity_32b_h200_tp4dp2.md`。

未验证并发点保持 `unknown`；最高已验证并发不是生产容量保证。

## 6. AD5：负载与调度边界

AISimulate 0.12.1 支持 `poisson` / `constant_rate` 到达模式。实测一次
Poisson（Qwen3-8B/H100 TP1，3 req/s，500 请求）：

* P99 TTFT 23321.41 ms、P99 TPOT 63.998 ms、输出吞吐 703.43 tok/s；
* 证据：`data/simulation/capabilities/arrival/`。

该结果**不能**与固定并发 C32 的 P99 直接比较：固定并发是闭环压测，
Poisson 是开放到达；本系统当前 SLA 容量扫描仍只使用固定并发点。
真实 GPU 校准清单留待 AE，不在本阶段启动 N6。

## 7. 验收执行

| 项目 | 结果 |
|---|---|
| 全量测试（`pytest -q`） | `1086 passed, 1 skipped` |
| 全链路校验（`AISIMULATE_PYTHON=... verify_all.sh`） | `1087 passed`（live 测试不再 skip），66 / 66 步通过；第 61 步运行 `run_20261023T060145Z_d5ecab` |
| Codex 审核 | `gpt-6-sol` + `low`（按当前 AGENTS.md）：P1 3 项（容量缺 P99 记 unknown、未知 DP 不得默认 DP1、容量读取前校验归档哈希/元数据）、P2 6 项（DP 精确匹配、manifest 与 exp_config 模式一致、探测不声明 database_mode、通用执行失败记 unknown、归档工具版本不借用请求值、容量点限定 PP1），均已修复；末轮无新增可执行缺陷 |

## 8. 仍未完成

* 真实 GPU Benchmark 与预测误差校准仍为 0；
* PP、DP-Attention、EP、PD 分离未进入推荐；
* 开放到达流量的容量结论尚未建立；
* 成本仍为合成示例，正式报价发布保持禁用；
* N5/N6 未恢复。
