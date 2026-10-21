# AGENTS.md

本文件约定在本仓库工作的 Agent 必须遵守的流程与边界。

## 1. 提交前必须用 codex 做代码审核（强制）

**每次写完代码后、`git commit` 之前**，必须调用本机的 codex CLI 做一轮代码审核，
并按审核结果处理问题后再提交。

固定用法（模型 `gpt-6-luna`，推理强度 `high`）：

```bash
# 审核当前未提交的改动
codex exec review --uncommitted \
  -m gpt-6-luna -c model_reasoning_effort="high"

# 审核某个已提交的 commit
codex exec review --commit <SHA> \
  -m gpt-6-luna -c model_reasoning_effort="high"

# 审核相对某分支的改动
codex exec review --base main \
  -m gpt-6-luna -c model_reasoning_effort="high"
```

要求：

1. **审核在上一步已经做完的，才算完成本步骤**；不能只跑命令不看结论。
2. 审核提出的 **P0/P1 问题必须修复**（或明确写出"为什么不修"的理由）。
3. 修复后重跑 `pytest -q` 与 `bash scripts/verify_all.sh`，确认无回归，再提交。
4. 提交信息里记录：用了哪个模型、审核发现几项、修了什么。

## 2. 仓库关键事实（避免重复摸索）

* 代码全部在 `llm-capacity-pricing/`（仓库根目录就是本项目，没有子目录）。
* Python 用 `.venv/bin/python`；**不要**往系统 Python 装包（PEP 668）。
* 全量测试：`.venv/bin/python -m pytest -q`
* 全链路：`bash scripts/verify_all.sh`（必须全绿；含历史基线 32/160/192 与超差 0）
* 一键 Demo：`bash scripts/run_system_demo.sh`（离线；`--mode live` 需 `AISIMULATE_PYTHON`）
* 单页界面：`.venv/bin/pip install -r requirements-app.txt` 后
  `PYTHONPATH=src .venv/bin/streamlit run app/streamlit_app.py`

## 3. 数据与证据纪律（不可绕过）

* **不得编造**任何 benchmark / SLA / 硬件 / 价格数据；缺就留 `None`/`unknown`，
  未知不等于 0。
* 真实定价数据只留本地（`data/private/`、`data/exports/`、`data/raw/*.xlsx` 均不入库）；
  仓库保持 **Public**。
* 模拟预测（`predicted_*`）与实测（`observed_*`）是两套取值：
  模拟不得写入 `BenchmarkRun`/`CapacityPoint`，不得产生 `observed_pass`。
* 正式报价自动发布保持禁用；报价资格只认持久化证据引用。
* **未经明确授权**：不改写 Git 历史、不强推、不在外部机器上安装软件。

## 4. 改动风格

* 优先复用现有模块（Sizing / Matcher / TCO / Governance / Reporting），
  不新增计算引擎、微服务或第二套规则。
* 局部修正优先于重构；新增结论必须能追溯到结构化计算，不靠文字润色。
* 新增能力要配反向用例（"什么情况下必须拒绝"），而不只是正向用例。
