# 系统 Demo 验收报告（S2–S5）

**基线**：V7.0 F6 封板
**范围**：S1 设计与契约 → S2 一键 Demo → S3 单页界面 → S4 方案文档 → S5 验收
**结论**：**S1–S5 完成。** 一条命令（或一个页面）即可从需求得到完整方案文档；
模拟预测与实测证据保持隔离；未达标时同样出报告。

> 本轮日志：`docs/test_run_log.txt`
> （sha256 `d827b3207df50b1c2b1b18ee1ccb6cc9274c675ed6ea06504d0cdb31bfcd9795`，
> 完成时间 `2026-10-20T14:11:14+08:00`）。

---

## 1. 交付物

| 轮次 | 交付 | 位置 |
|---|---|---|
| S1 | 总体架构 / 设计说明 | `docs/system_architecture.md`、`docs/system_design.md` |
| S2 | 一键 Demo（三种模式） | `scripts/run_system_demo.sh` + `app/demo_service.py` + `app/demo_cli.py` |
| S3 | 单页界面 | `app/streamlit_app.py` + `requirements-app.txt` |
| S4 | 7 段式方案文档 | `src/llm_capacity_pricing/reporting/proposal.py` |
| S5 | 本报告 + 19 条测试 | `tests/test_s_phase_demo.py` |

## 2. 三种运行模式（都实测过）

| 模式 | 命令 | 需要什么 | 实测结果 |
|---|---|---|---|
| **A 离线**（默认） | `bash scripts/run_system_demo.sh` | 无（可用归档数据） | ✅ 3 个产物、模拟 `computed`、未达标参考配置 |
| **B 实时** | `--mode live` | `AISIMULATE_PYTHON` 指向独立环境 | ✅ 真实搜索 + P99 回放（tp4，500 请求） |
| **C 仅核心** | `--mode core` | 无 | ✅ 无模拟章节，其余三领域照常 |

模式 A 的实际输出（本轮第 62 步）：

```
运行目录：data/exports/system_demo/20261020T060617Z
  [ok] 数据准备：…/demo.db
  [partial] 统一评估：domains={'input': 'computed', 'sizing': 'computed',
                              'performance': 'computed', 'governance': 'not_configured',
                              'economics': 'computed'}
  整体状态：partial
  模拟状态：computed
  未达标参考：sim-native:Qwen/Qwen3-32B@h100_sxm-tp8-c32-n500
```

三个候选：`tp4` / `tp8` 为 `conditional_feasible`，`tp2` 为 `infeasible`；
成本全部不完整（示例目录），因此全周期总额为空；`publishable` 为空。

模式 B 的实际结果（独立运行）：

* 搜索 → 资源过滤 → Top-N → 原生回放，选中 `tp4`（预测吞吐最高且可对齐）；
* 工作点关联：`sim-native:Qwen/Qwen3-32B@h100_sxm-tp4pp1-c32-n500`
  ← `sim-sweeper:…tp4…-c32-ctx8192`；
* 预测：TTFT P99 **8359.44 ms**、TPOT P99 **50.17 ms**、吞吐 621.9 tok/s；
  对 400ms / 30ms 阈值 → `predicted_fail`；
* 运行目录：`/tmp/syslive/sim_work/runs/run_20261020T060203Z_c3d75e`（含 `manifest.json`）。

## 3. 方案文档（S4，7 段）

`deployment_proposal.md` 固定七段：

1. 客户需求概览（模型、硬件、负载、SLA、预算、卡数上限）
2. 模型与硬件资源分析（卡数、TP×DP、静态结论、余量）
3. 候选部署配置（静态 / 性能结论 / 数据性质 / 匹配等级 / 成本 / 报价）
4. 性能预测与 SLA 判断 —— **实测与模拟分节**，实测缺失时明说"证据不足"
5. 成本与预算分析（口径、年限、覆盖缺口、可否比较）
6. 推荐配置及依据
7. 限制、风险与待验证事项

数据分级在文中逐处标注：**公开实测参考 / 模拟预测 / 假设成本 / 真实实测**。
没有实测时不会出现"达标"字样；模拟不达标时写「模拟预测**未**满足阈值」。

**防编造测试**：文档里出现的关键数值必须同时存在于结构化结果中
（`test_proposal_does_not_invent_numbers`）。

## 4. S5 要求验证的 6 种情况

| 情况 | 结果 | 覆盖 |
|---|---|---|
| 正常生成部署方案 | ✅ 3 候选 + 成本 + 模拟参考，报告完整 | 模式 A |
| 没有任何候选满足 P99 SLA | ✅ 仍出报告，标题为「未达标参考配置」并给原因 | `test_proposal_labels_…`、模式 A/B |
| 硬件资源不足 | ✅ `tp2` 判 `infeasible` 并写明超出量；卡数上限=1 时也不报错 | `test_hardware_shortage_is_reported_not_raised` |
| 成本或预算信息不完整 | ✅ 不给全周期总额，预算结论 `unknown` | `test_incomplete_cost_never_becomes_a_total` |
| AISimulate 不可用 | ✅ 模拟状态 `error` 并说明；其余领域与报告正常 | `test_live_mode_without_tool_still_produces_a_report` |
| 重复执行不污染历史 | ✅ 每次独立目录 + 独立数据库；旧目录不删 | `test_repeat_runs_do_not_share_artifacts`、F6-2 四条 |

## 5. 界面（S3）

* 单页四区：需求输入 → 自动分析 → 方案结果 → 报告导出（Markdown / JSON 下载）；
* 前端**没有任何** Sizing / SLA / TCO 公式，全部调用 `demo_service → evaluate_plan()`；
* 无账号、多租户、任务队列、审批后台；
* 验证方式：`streamlit.testing.v1.AppTest` 无头执行脚本，断言无异常且控件齐全
  （`test_streamlit_app_runs_headlessly`）；另做了 HTTP 冒烟（`/` 200、`/_stcore/health` = ok）。

## 6. 回归

| 项 | 上轮 | 本轮 |
|---|---|---|
| `pytest -q` | 867 | **886 passed** |
| `verify_all.sh` | 65 步 | **66 步全部通过**（新增第 62 步系统 Demo） |
| 第一阶段基线 | 32 / 160 / 192 | **不变**，超差 0 |
| 官方候选逐字段核对 | 12/12 | **12/12（不一致 0）** |
| 模拟写入 BenchmarkRun | 否 | **否**（评估前后 run 数不变） |
| 敏感数据扫描 | 未命中 | **未命中** |

## 7. 边界与未做的事

1. **本阶段完成的是"自动化部署方案推荐系统"，不是"已验证性能的自动部署系统"。**
   模拟预测可为选型与排除明显不可行提供依据，但不能替代目标硬件上的真实压测。
2. 成本来自**示例目录**，不是供应商报价；正式报价仍需人工在受控流程中签发。
3. 生产审批与报价发布保持禁用。
4. 未做（按任务书）：N5 真人易用性验收、N6 RTX 3090 真机压测、
   新硬件/复杂并行适配、多用户与权限系统、Git 历史重写。
5. 单页界面是可选的演示外壳；Streamlit 依赖放在 `requirements-app.txt`，
   **不并入**主 `requirements.lock`。

## 8. 怎么跑

```bash
# 一次性演示（离线，约 10 秒）
bash scripts/run_system_demo.sh

# 换成别的需求
bash scripts/run_system_demo.sh --gpu 4 --concurrency 64 --ttft 300 --tpot 20 --budget 1500000

# 实时模拟（需独立环境）
AISIMULATE_PYTHON=/opt/aisim/bin/python bash scripts/run_system_demo.sh --mode live

# 单页界面
.venv/bin/pip install -r requirements-app.txt
PYTHONPATH=src .venv/bin/streamlit run app/streamlit_app.py
```
