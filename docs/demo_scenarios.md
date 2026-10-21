# T 阶段：三个可复现的业务演示场景

基线：`2a72986` 之后（V1.1 收尾 + 场景入口）。
三个场景只提供**需求输入**，结论全部由 `run_demo()` → `evaluate_plan()` 重新计算；
页面和 CLI 都没有写死任何结果。

| 场景 | 输入差异 | 实际结论（2026-10-21） |
| --- | --- | --- |
| `strict` 严格 SLA | TTFT ≤ 400 ms、TPOT ≤ 30 ms；吞吐优先 | 预测未达标；无推荐配置；给出未达标参考 TP8；无目标配置实测证据 |
| `relaxed` 宽松 SLA | TTFT ≤ 6000 ms、TPOT ≤ 40 ms（演示阈值）；其余不变 | 预测达标；推荐 `sim-native:…tp8-c32-n500`；仍无实测通过 |
| `cost` 成本与预算 | 在宽松 SLA 上启用成本优先；预算 1,000,000 CNY | 预测达标、推荐候选存在；**成本排序不成立**，降级为吞吐优先；预算未知；成本表显示一次性/年现金，但全周期总额不展示 |

三个场景的 `ranking` 目标、SLA 阈值与预算都写在请求里，报告首行的演示说明也会
随生成结果一起保存。

## 运行方式

UI：左侧「演示案例」选择 `严格 SLA / 宽松 SLA / 成本与预算`，再点「生成部署方案」。
切换预设只填充需求字段；手动修改后生成不会附带预设说明。

CLI：

```bash
.venv/bin/python -m llm_capacity_pricing.app.demo_cli --list-scenarios
.venv/bin/python -m llm_capacity_pricing.app.demo_cli --scenario strict  --out docs/demo_scenarios/strict
.venv/bin/python -m llm_capacity_pricing.app.demo_cli --scenario relaxed --out docs/demo_scenarios/relaxed
.venv/bin/python -m llm_capacity_pricing.app.demo_cli --scenario cost    --out docs/demo_scenarios/cost
```

预设默认使用离线归档。`--mode live/core` 会覆盖预设的模拟模式，但预设说明只在
离线模式下附加到报告；实时/无模拟的结果可能不同，不能沿用离线结论。

每个目录包含：

| 文件 | 内容 |
| --- | --- |
| `deployment_proposal.md` | 客户版方案（首行是演示说明） |
| `technical_report.md` | 技术报告（匹配等级、外部参考、模拟限制） |
| `result.json` | 结构化结果（可复核每个数值） |
| `scenario_note.md` | 场景与演示说明 |

`demo.db` 是派生运行库，不随仓库提交。

## 关键事实

### 严格 → 宽松只改阈值

归档原生回放的预测值完全不变：

| 指标 | 严格 400/30 | 宽松 6000/40 |
| --- | --- | --- |
| TTFT P99 | 5,201.83 ms | 5,201.83 ms |
| TPOT P99 | 31.46 ms | 31.46 ms |
| verdict | `predicted_fail` | `predicted_pass` |

宽松阈值只是**演示阈值**，不是客户真实需求；报告首行会明确写出这一点。
预测达标仍然不是实测：三个场景中 `observed_pass` 都是 0。

### 为什么成本场景不给“成本最优”

TCO 引擎对两类情况强制 `incomplete=True`：

1. 使用了合成价格（`contains_synthetic_prices`）；
2. 币种未确认（`currency_not_confirmed`）。

因此即使成本项覆盖完整、每个候选都能算一次性与年现金支出，
`total_cash_over_horizon` 也不会被当作可用总额，成本排序会被拒绝。
这是既有的安全门禁，不是本轮缺陷。要在真实业务中给出成本最优，必须换成
**已确认、非合成**的价格目录；该仓库的公开 Demo 不包含这类数据。

当前 `cost` 场景如实展示：

- 三个候选的一次性采购与年现金支出（CNY，示例目录）；
- 全周期现金显示为 `—`，完整性为「不完整」；
- 成本排序提示“成本不完整或金额缺失，无法与其他候选比较成本”，降级为吞吐优先；
- 预算状态为「未知」，不因为预算设成 100 万就声称在预算内或超预算；
- 报价发布始终为「不可发布」。

成本排序的**算法本身**（完整 + 已确认时的成本最小选择）由
`tests/test_t_scenarios.py::test_cost_ranking_mechanics_with_complete_confirmed_fixture`
用明确标注的 fixture 单独验证；不把 fixture 冒充成业务数据。

## 验收截图

- `docs/ui/t-phase/relaxed_predicted_pass.png`：蓝色「预测达标」与推荐候选；
- `docs/ui/t-phase/cost_components.png`：成本明细与完整性；
- `docs/ui/t-phase/cost_ranking_refused.png`：成本排序降级原因。

## 边界

- 模拟预测不是实测，不写 `BenchmarkRun`，不产生 `observed_pass`；
- 成本为合成示例，不是报价；正式报价与审批发布保持禁用；
- 本轮不包含 N5 真人验收、N6 真机 Benchmark、新算法或新 GPU 适配。
