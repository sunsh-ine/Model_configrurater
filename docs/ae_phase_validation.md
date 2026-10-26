# AE 阶段验收：负载真实性、容量可靠性与校准准备

基线：`b4dd888`（AD 阶段封板）
执行日期：2026-10-23

> 真实 GPU Benchmark 仍为 0；本阶段所有新增结论仍是 AISimulate 预测。
> 固定并发与 Poisson 到达分开建模，不合并排名。

## 0. 结论

| 项目 | 结论 |
|---|---|
| AE0 AD 遗留 | 已修复：数据库模式来源、未登记 exp_config、容量/主推荐口径 |
| AE1 负载身份 | `arrival_mode`/到达率/dataset/seed 进入证据身份；跨模式不可比较 |
| AE2 固定并发补点 | 8B/H100 DP2 最高已验证 C48；32B/H200 TP4DP2 最高 C40 |
| AE3 Poisson 实验 | 三个部署多到达率；最高已验证 4/4/5 req/s（有限窗口） |
| AE4 报告集成 | 技术报告显示到达模式/到达率；容量与到达率扫描独立输出 |
| AE5 校准准备 | 元数据模板 + 精确匹配/误差契约 + 反向测试；无真机 |
| AE6 回归 | 全量测试、live verify_all、Codex 审核见 §6 |

## 1. AE0/AE1：证据与负载身份

* `database_mode_source` 区分 `tool_observed` / `explicitly_configured` /
  `archive_declared` / `unknown`；当前归档中的 SILICON 是
  `archive_declared`，不是 tool 实测确认。能力探测矩阵不声明 SILICON。
* `SimulationWorkload`/`ArchiveEntry`/覆盖矩阵包含
  `arrival_mode`、`request_rate_rps`、`dataset`、`seed`。
* `check_comparability()` 要求到达模式一致、开放到达率一致；固定并发
  不与 Poisson/constant-rate 互相复用 P99。
* 历史归档缺 dataset 时保留 unknown 并提示，不据此否定已有工作点。
* 未登记 `exp_config.yaml` 不暴露给候选；manifest 与 exp_config 的
  database_mode 冲突会拒绝。

## 2. AE2：固定并发容量补点

SLA 6000/40、ISL8192/OSL256、500 请求：

* Qwen3-8B/H100 TP1×DP2：C8/C32/C40/C48 通过，C56 起失败；
  **最高已验证 C48**；C48–C56 之间未验证。
* Qwen3-32B/H200 TP4×DP2：C8/C32/C40 通过，C48 起失败；
  **最高已验证 C40**；C40–C48 之间未验证。

详见 `docs/ae_capacity_comparison.md`、`docs/ad_capacity_*.md`。

## 3. AE3：Poisson 到达率

ISL8192/OSL256、500 请求、seed 42：

| 部署 | 通过的到达率 | 未通过 |
|---|---|---|
| 8B/H100 TP1×DP1 | 1 req/s | 2/3/4 req/s |
| 8B/H100 TP1×DP2 | 2/3/4 req/s | 5 req/s |
| 8B/H200 TP1×DP2 | 2/3/4/5 req/s | —（只测到 5） |

种子稳定性（8B/H100 DP2，3 req/s）：seed42 582.19/23.55，
seed7 593.52/20.91，均通过；这是有限窗口随机波动，不是实测误差校准。

证据：`data/simulation/archives/ae-live-*poisson*`；报告
`docs/ae_poisson_*.md`。固定并发与 Poisson 不合并排名。

## 4. AE4：报告与容量范围

* 技术报告在负载行显示固定并发或“到达率 x req/s（poisson）”。
* 离线多归档提示“本次只评估同一工作点；容量边界需单独扫描，未验证区间 unknown”。
* `scripts/capacity_scan.py` 与 `scripts/arrival_scan.py` 只报告已验证点，
  缺 P99/样本/身份时 unknown，不插值。
* 同一套 P99 阈值用于主推荐和容量扫描；样本窗口仍要求 ≥100 完成请求。

## 5. AE5：真实 Benchmark 校准准备

`docs/ae_benchmark_calibration_plan.md` +
`data/examples/benchmark_calibration_metadata.example.json`。

* 身份完全匹配才计算绝对/相对误差；不匹配只展示差异。
* 缺实测或预测值为 unknown，不能写 0。
* 固定并发比较 concurrency，Poisson/constant-rate 比较到达率。
* 未来三组优先基准：8B/H100 DP1/DP2、32B/H100 TP8/TP4DP2、
  32B/H200 TP8/TP4DP2。
* 真机执行需要 GPU、框架环境与明确授权；本阶段不自动恢复 N6。

## 6. 验收执行

| 项目 | 结果 |
|---|---|
| 全量测试（`pytest -q`） | `1105 passed, 1 skipped` |
| 全链路校验（`AISIMULATE_PYTHON=... verify_all.sh`） | `1106 passed`（live 测试不再 skip），66 / 66 步通过；第 61 步运行 `run_20261026T021710Z_e5da7b` |
| Codex 审核 | `gpt-6-sol` + `low`（按当前 AGENTS.md）：P1 6 项（开放到达率未进入 PlanWorkload、数据集未声明仍可比、离线未按到达率选、live 开放到达仍按固定并发搜索、trace 身份、校准来源）与 P2 13 项（校准缺 KV/数据集、unknown 到达模式、trace 标识、Poisson 样本窗口、Demo 离线未透传到达率、覆盖矩阵 synthetic 数据集、到达率数值比较、开放到达仍带固定并发、归档到达率字符串比较、开放到达 sizing 仍用固定并发），均已修复；末轮见审核记录 |

## 7. 仍未完成

* 真实 GPU Benchmark 与误差校准仍为 0；
* 最高已验证并发/到达率不是生产容量保证；
* 固定并发与 Poisson 的长期稳定性、队列积压未验证；
* PP、DPA、EP、PD 分离不在推荐范围；
* 成本仍为合成示例，正式报价发布保持禁用。
