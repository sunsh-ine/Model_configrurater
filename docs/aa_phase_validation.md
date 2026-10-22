# AA 阶段验收：多候选部署决策

基线：`afcad7a`（Z 阶段封板）
执行日期：2026-10-22

> 本阶段仍然只比较 **AISimulate 预测**。没有真实 GPU Benchmark；
> 所有 `sim-native` 结论都不会写入 `BenchmarkRun` / `CapacityPoint`，
> 也不会产生 `observed_pass`。

## 0. 结论

| 项目 | 结论 |
|---|---|
| AA0 归档完整性 / 证据 ID / 动态文档 | 已修复 |
| AA1 同工作点多部署候选 | 通过；C32 同时返回 TP4 与 TP8 |
| AA2 实时搜索与目标负载分离 | 通过；扫描行只提供部署，回放统一用目标 C32 |
| AA3 SLA 优先推荐 | 通过；先资源/SLA 门禁，再吞吐/成本排序 |
| AA4 自动化对照 | 通过；手动、CLI、Agent 使用同一候选集合与结论 |
| 真实 Benchmark（第 4 级） | 仍为 0，本阶段不产生实测 |

## 1. AA0：Z 阶段证据收尾

1. **哈希校验**：新增 `app/archive_integrity.py`。加载归档前校验 manifest
   中每个输入/输出的 SHA256；文件缺失、哈希不符、运行有 `errors` 的归档
   一律不作为 P99 证据。覆盖矩阵也会把这类运行记入 `blocked_runs`。
2. **证据 ID 唯一**：覆盖矩阵的原生证据 ID 从 `run_id:native` 改为
   `run_id:native:predict_N`；同一运行内多个预测不再重复。
3. **动态结论**：`docs/performance_coverage.md` 的“可比较工作点”由工作点
   数据生成，不再写死“只有 C32”。
4. **审核规则**：`AGENTS.md` 未变更，本阶段按仓库规定使用
   `gpt-6-luna` + `high` 做提交前审核。
5. **第 61 步真实执行**：设置 `AISIMULATE_PYTHON=/tmp/aisim-venv/bin/python`
   后运行完整 `verify_all.sh`，第 61 步不再是跳过状态。

## 2. AA1：多候选归档比较

`offline_archives.offline_simulation_paths()` 现在返回 `archives` 列表，
`PlanSimulationInput.archives` 逐条交给 `load_archived()`：

* 严格匹配模型、硬件、精度、框架/版本、ISL、OSL、并发；
* `gpu_limit`、`tp_sizes`、Sizing 静态可行性、`completed_requests ≥ 100`
  全部生效；
* 同一部署的重复归档按证据完整度/名称去重，不重复算方案；
* 不同工作点或身份不完整的证据不参与 SLA 排名；
* 原始归档与历史预测数值不变。

C32 离线结果：

| 部署 | GPU | TTFT P99 | TPOT P99 | 吞吐 | relaxed 6000/40 |
|---|---|---|---|---|---|
| TP4 | 4 | 8359.44 ms | 50.17 ms | 621.90 tok/s | 未达标 |
| TP8 | 8 | 5201.83 ms | 31.46 ms | 990.89 tok/s | 预测达标 |

门禁矩阵：

| 条件 | 结果 |
|---|---|
| strict 400/30 | 两个候选都未达标；`recommended=None`，`best_effort=TP8` |
| relaxed 6000/40 | 推荐 TP8 |
| GPU 上限 4 | 只剩 TP4；TP8 不在结果中 |
| TP 白名单 `(4,)` | 只剩 TP4 |
| C16 无原生 P99 | 不产生 `predicted_pass` |
| 合成成本不完整/币种未确认 | 成本排序不可用，降级吞吐优先 |

## 3. AA2：实时搜索与回放

`run_two_stage()` 不再用扫描行自身的并发/ctx 作为回放资格：

1. 从搜索结果提取不同部署（TP/PP/DP/卡数/后端/版本）；
2. 资源与身份过滤，未通过的写入 `manifest.not_replayed`；
3. 每个通过部署用目标 ISL/OSL/并发生成 native predict 输入；
4. 逐部署回放，保存独立 `predict_N.yaml` / `out_N/prediction.json`；
5. 统一交给 SLA 与推荐逻辑。

实时验证运行：`run_20261022T062058Z_0b64a9`
（归档：`data/simulation/archives/aa-live-multi-c32-tp4-tp8-bf16/`）。

| 部署 | 搜索时的工作点 | 回放工作点 | P99 | 结果 |
|---|---|---|---|---|
| TP8 / 8 卡 | C16 / ctx8192 | C32 / ISL8192 / OSL256 | 5201.83 / 31.46 | 达标 |
| TP4 / 4 卡 | C96 / ctx8192 | C32 / ISL8192 / OSL256 | 8359.44 / 50.17 | 未达标 |
| TP2 / 2 卡 | C32 | 未回放 | — | Sizing 不可行（75.88 GB > 72 GB/卡） |

`manifest.replay_plan` 记录了两个部署的目标负载，`manifest.not_replayed`
记录了 TP2 的具体原因。没有为了让某个配置达标而改写搜索或回放结果。

## 4. AA3：推荐顺序

沿用 `SimulationResult` 的既有规则，不新增评分公式：

1. 身份可比；
2. 资源可行、未超 GPU 上限、TP 在白名单；
3. 有完整 P99；
4. 预测达标进入正式推荐池；
5. 吞吐优先取最大；成本优先只在所有达标候选成本完整、币种确认、
   口径/年限一致时取最小；否则降级吞吐优先并写明原因；
6. 预算未知保持 unknown。

## 5. AA4：自动化对照

新增 `tests/test_aa_multi_candidate.py`，覆盖：

* C32 同时比较 TP4/TP8，relaxed 推荐 TP8；
* strict 无推荐但保留 best-effort；
* GPU 上限 4、TP 白名单 `(4,)` 排除 TP8；
* C16 无 P99 不能声称达标；
* 成本不完整不得宣称成本最优；
* C8/C64 原始预测值不变；
* manifest 篡改被拒绝；
* 覆盖矩阵证据 ID 唯一、Markdown 结论动态生成。

实时测试 `tests/test_v7_r2_auto_search.py` 新增“扫描行负载不决定回放负载”的
反例：三条不同 ctx 的扫描行必须都在目标 C32 下各回放一次。

三种入口同一需求（C32 / relaxed）的实测对照：

| 入口 | 原生候选 | 推荐 |
|---|---|---|
| 手动 `run_demo` | TP4 fail、TP8 pass | TP8 |
| CLI `demo_cli --scenario relaxed` | TP4 fail、TP8 pass | TP8 |
| Command Code Agent（run_id `20261022T071621Z_3b1b0d`） | TP4 fail、TP8 pass | TP8 |

Agent 会话 `cc-95b9a647007943bd`；业务报告仍来自 `result.json`，
模型文本不作为结论来源。

## 6. 验收执行

| 项目 | 结果 |
|---|---|
| 全量测试 | `1046 passed, 1 skipped` |
| 全链路校验（含第 61 步真实执行） | `verify_all.sh` 66 / 66 通过；第 61 步运行 `run_20261022T072523Z_95f4cf`（最终一轮），不再是跳过 |
| Codex 审核 | `gpt-6-luna` + `high` 四轮：前三轮 P2 3 项（manifest 必须同时登记 inputs/outputs；实际读取的 prediction/input 必须被哈希覆盖；回放上限截断必须声明比较不完整），均已修复；第四轮无新增可执行缺陷（聚焦测试 57 passed / 1 skipped） |

## 7. 仍未完成

* 真实 GPU Benchmark（第 4 级证据）仍为 0；
* N5/N6 保持暂停；
* 实时搜索只回放原生配置能表达的部署（DP=1、PP=1）；
* 成本仍为合成示例，正式报价发布保持禁用；
* 同工作点的更多部署（如 TP16）尚未有归档，不能凭空比较。
