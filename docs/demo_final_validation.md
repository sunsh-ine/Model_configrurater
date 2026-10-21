# T 阶段演示验收记录

日期：2026-10-21
基线：V1.1 冻结（`35f91c6`）+ 本阶段改动
环境：Python 3.12.3，无 GPU，离线归档模拟（`AISIMULATE_PYTHON` 未设置）

## 结论

三个业务场景都能一键运行，结论与请求一致；预测与实测结构性隔离；
成本与预算在“合成价格 + 币种未确认”时保持未知/不完整，不给出正式成本最优；
原有历史复算基线与全链路无回归。

全量结果：**918 passed, 1 skipped**；`scripts/verify_all.sh`：**66/66**。

## 十项检查

| # | 检查项 | 结果 | 证据 |
| --- | --- | --- | --- |
| 1 | 三个案例均能一键运行 | 通过 | `docs/demo_scenarios/{strict,relaxed,cost}/` 各有方案、技术报告、JSON；UI 预设选择后同样走 `run_demo()` |
| 2 | 请求与结果身份一致 | 通过 | `result.json.request_echo`：strict 400/30、relaxed 6000/40、cost 6000/40 + budget 1,000,000 + ranking cost；`max_total_gpus=8` |
| 3 | 模拟预测与实测隔离 | 通过 | 三个场景 `sla_status` 均为 `not_evaluable`/`None`；结果 JSON 中 `"observed_pass"` 出现 0 次；既有 `test_simulation_does_not_add_any_benchmark_run` 继续通过 |
| 4 | 推荐资格、P99、预算、成本结论 | 通过 | relaxed/cost 推荐 `sim-native:…tp8pp1dp1-c32-n500`（`predicted_pass`）；cost 的 `cost_ranking_available=False`；预算全部 `unknown`；三个场景 `quote_publishable=False` |
| 5 | 改变 SLA 不改预测值 | 通过 | 同一原生回放在 400/30 与 6000/40 下均为 TTFT 5,201.83 ms、TPOT 31.46 ms，只有 verdict 从 fail 变 pass |
| 6 | 关闭预算 ≠ 预算为零 | 通过 | `test_zero_budget_is_a_real_constraint_not_unknown`；cost 场景额外验证 `budget_amount=None` 与 `Decimal("0")` 是两种不同输入 |
| 7 | 页面导出与 JSON/Markdown 一致 | 通过 | 报告由同一次 `PlanResult` 生成；宽松场景首行演示说明与 `request_echo.sla` 一致；`result.json` 与方案文档的 TTFT/TPOT 相同 |
| 8 | 重复运行不覆盖历史 | 通过 | 再次对 `docs/demo_scenarios/relaxed` 运行 CLI 返回 2：“Demo 不会覆盖已有产物”；`test_existing_nonempty_out_dir_is_refused` 通过 |
| 9 | 无 AISimulate 也能离线展示 | 通过 | 三个场景全部 `mode=offline`；`verify_all.sh` 第 61 步明确记录“未找到 AISimulate 独立环境，跳过” |
| 10 | 历史复算基线不变 | 通过 | `verify_all.sh` 66/66，包含默认业务口径 32/160/192 与超差 0 的既有检查 |

## 三个场景的实际输出

### strict（严格 SLA）

- `sim-native`：TTFT P99 5,201.83 ms > 400，TPOT P99 31.46 ms > 30 → `predicted_fail`；
- 无推荐配置；报告给出“未达标参考配置”；
- 候选 SLA 全部不可评估，报价不可发布。

### relaxed（宽松 SLA，演示阈值）

- 预测值完全不变，仅 verdict 变为 `predicted_pass`；
- 推荐 `sim-native:Qwen/Qwen3-32B@h100_sxm-tp8pp1dp1-c32-n500`；
- 仍无 `observed_pass`；方案文档与技术报告首行都写明“演示阈值，不代表客户真实需求”。

### cost（成本与预算）

- 预测达标且存在推荐候选；
- 成本排序不成立：候选成本不完整（合成价格 + 币种未确认），降级为吞吐优先；
- 三个候选的一次性/年现金支出可见，全周期总额显示 `—`（不完整不是 0）；
- 预算状态 `unknown`，报价 `不可发布`；
- 成本排序算法本身（完整 + 已确认 fixture）由 `test_cost_ranking_mechanics_with_complete_confirmed_fixture` 验证。

## 截图

| 文件 | 内容 |
| --- | --- |
| `docs/ui/t-phase/relaxed_predicted_pass.png` | 蓝色“预测达标”与推荐候选 |
| `docs/ui/t-phase/cost_components.png` | 成本明细、完整性、预算未知 |
| `docs/ui/t-phase/cost_ranking_refused.png` | 成本排序降级的明确原因 |

## 未验证项（如实记录）

- `verify_all.sh` 第 61 步的实时 AISimulate 搜索/回放因没有独立环境而**跳过**。
  本记录不能作为实时模式已验证的证据；需要安装 AISimulate 的环境单独执行。
- 本轮不包含 N5 真人易用性验收、N6 RTX 3090 真机 Benchmark。
- 成本场景使用合成示例价格，不构成任何报价或商务结论。
