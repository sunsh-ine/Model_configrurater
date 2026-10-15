# 安全整改报告（V1.2 F1）

- 日期：2026-10-15
- 范围：`sunsh-ine/Model_configrurater`（审查基线 `9e3fc04`，仓库当时为 **Public**）
- 本报告的处置口径（由任务负责人确认）：
  **仓库权限保持 Public；真实业务数据保留在本地；公开部分改用脱敏示例数据。**

---

## 1. 结论摘要

| 项 | 结果 |
|---|---|
| 当前版本是否仍含未授权披露的真实定价结果 | **否**（已从版本控制移除，并有扫描门禁防回归） |
| 新提交是否会重新引入此类数据 | **否**（`.gitignore` + `verify_all.sh` 第 19 步扫描 + 测试用例三重拦截） |
| 公开克隆是否仍可跑通核心计算 | **是**（脱敏样本 + 手工期望值，实测公开克隆 `155 passed / 106 skipped / 0 failed`） |
| 密钥 / 访问凭证是否泄露 | **否**（当前树与全部 Git 历史的高信号特征扫描均为 0 命中） |
| 需轮换的凭证 | **无** |
| Git 历史中的历史暴露 | **仍然存在，尚未处理**（需授权才能重写历史，详见 §4；本报告只做影响评估，未执行任何破坏性操作） |

---

## 2. 暴露清单（整改前）

### 2.1 已提交的敏感内容

| 文件 | 内容类型 | 首次进入历史 | 当前版本 |
|---|---|---|---|
| `data/exports/legacy_results.json` | 32 条满载结果的完整金额/比率/cost/breakeven | `748ae68` | 已移出版本控制（文件保留在本地） |
| `data/exports/legacy_results_with_tests.json` | 同上（含 4 条 fixture） | `748ae68` | 同上 |
| `data/exports/scenarios_sensitivity.json` | 160 条情景金额 | `748ae68` | 同上 |
| `data/exports/scenario.json` | 单条情景金额 | `748ae68` | 同上 |
| `data/exports/overview.md` | 报告正文（含金额、成本比、盈亏平衡） | `748ae68` | 同上 |
| `docs/validation_report.md` | 派生金额（最高收入案例、成本比区间、盈亏平衡跨度） | `748ae68` | 表达保留、**具体数值已移除** |
| `docs/phase1_final_validation.md` | 情景金额、业务事实 | `9e3fc04` | 同上 |
| `docs/test_run_log.txt` | 运行日志中的情景金额 | `748ae68` | **值已脱敏**（`<redacted:internal>`），脚本侧持续生效 |
| `tests/conftest.py` | 真实复算期望值（oracle） | `748ae68` | 已移入本地 `data/private/`，缺失即 skip |
| `tests/test_excel_import.py` / `test_pricing_equivalence.py` | 真实输入与期望值字面量 | `748ae68` | 改为从本地 oracle 读取或使用合成常量 |
| `docs/formulas.md` / `data_dictionary.md` / `README.md` | 真实样例金额、真实型号与单价列表 | `748ae68` | 改用脱敏样本数值，真实项仅作结构性描述 |

### 2.2 从未进入版本控制的内容（确认）

| 项 | 证据 |
|---|---|
| 原始工作簿 `Token产能计算器-0910.xlsx` | `git log --all --name-only -- '*.xlsx'` 为空 |
| 本地数据库 `data/local.db` | `git log --all --name-only -- '*.db'` 为空 |
| 任何 `.env` / 凭证文件 / 私钥 | 历史文件名扫描无命中 |

### 2.3 凭证扫描结果

| 扫描项 | 范围 | 结果 |
|---|---|---|
| `ghp_*` / `AKIA*` / `sk-*` / `xox?-*` / `BEGIN PRIVATE KEY` | 当前树 + 全部历史（排除存档文档） | **0 命中** |
| `api_key=` / `password=` 形式的赋值（≥16 字符值） | 当前树（排除存档文档） | **0 命中** |
| 敏感文件名（`.env`/`secret`/`credential`/`*.pem`/`id_rsa`） | 全部历史 | **0 命中** |

> 说明：初次扫描曾在 `data/raw/official/*.html` 命中 `OPENAI_API_KEY`、`Authorization: Bearer <key>`
> 等字样，逐条核对后确认是**官方文档里讲解用法的示例文本**（占位符 `<key>`），不是凭证。
>
> **需轮换的凭证：无。**

---

## 3. 已执行的整改（本仓库内，非破坏性）

1. **移出版本控制**：`git rm -r --cached data/exports`（文件保留在本地磁盘，仅不再跟踪）。
2. **`.gitignore` 明确划界**：内部产物（`data/exports/`、`data/private/`、`data/raw/*.xlsx`）排除；
   可公开内容（代码、测试、文档、`data/examples/`、`data/fixtures/`、`data/raw/official/`）保留。
   并对忽略规则加了核对（`verify_all.sh` 第 19 步会验证"该忽略的已忽略、该提交的可提交"）。
3. **脱敏公开样本**：新增 `scripts/make_public_sample.py` 生成
   `data/examples/Token产能计算器-SAMPLE.xlsx`（同结构、合成数值）与
   `data/examples/public_sample_expected.json`（**脚本独立手算**的期望值，不由引擎生成）。
4. **内部 oracle 外置**：真实期望值与输入移入 `data/private/row2_oracle.json`（不入库），
   `tests/conftest.py` 的 `row2_oracle` fixture 缺失即 skip，公开克隆不会因此失败。
5. **文档脱敏**：真实金额改为结构性描述或样本数值；`docs/formulas.md` 的算例改用合成样本
   （`EXAMPLE-GPU-A` / `EXAMPLE-MODEL-A`），公式与结论不变。
6. **日志脱敏**：`verify_all.sh` 增加 `redact_internal_values()`，把金额/比率字段值替换为
   `<redacted:internal>`；键名、条数、误差量级、门禁结论全部保留，日志仍可复核。
7. **回归门禁**：`verify_all.sh` 第 19 步「敏感数据扫描」+ `tests/test_v12_hardening.py` 的
   `test_no_internal_values_in_tracked_files`，两者共用**本地**特征表
   `data/private/sensitive_patterns.txt`（不入库，避免把指纹本身写进公开仓库）。

| 项 | 整改前 | 整改后 |
|---|---|---|
| 内部派生结果是否入库 | 是（5 个导出文件） | 否（`data/exports/` 已忽略） |
| 公开克隆能否验证核心计算 | 只能跳过 | 能（脱敏样本 + 手算期望值） |
| 提交时是否有自动化拦截 | 无 | 有（扫描步骤 + 测试用例） |
| 日志中的金额 | 明文 | `<redacted:internal>` |
| 真实期望值字面量 | 在 `tests/` 中 | 在本地 `data/private/`（不入库） |

---

## 4. 历史暴露与影响评估（**未执行**，等待授权）

> 任务书 F1 第 6 条要求：需要重写历史时"先提交影响评估，不得擅自执行破坏性的 force-push"。
> 本节的结论是：**历史中仍可检出这些数据**，是否清理需要你决定。

**现状**：`748ae68` 与 `9e3fc04` 两个提交都包含 `data/exports/*` 与含派生金额的文档版本，
且两者都已推送到 public 远端。即使当前版本已删除，`git log -p` 仍能取回旧内容。

| 方案 | 效果 | 代价 / 风险 | 备注 |
|---|---|---|---|
| **A. 不处理**（保持现状） | 历史中仍可检出内部金额 | 无 | 内部数值属于成本与收入推导结果，敏感性中等；若认为可接受，这是成本最低的选项 |
| **B. 重写历史后强推**（`git filter-repo` 清除 `data/exports/*` 与文档中的数值） | 远端 HEAD 与后续提交不再包含 | 所有 commit SHA 变化；需 force-push；任何已有克隆/分支失效；GitHub 上的旧对象在 GC 前可能仍可通过旧 SHA 访问 | 本仓库仅 3 个提交、无协作者与 fork，技术上可行 |
| **C. 删除远端仓库后从干净工作树重建** | 最彻底 | 会丢失 issue/PR/star 等仓库元数据；需重新建仓 | 与 B 同属破坏性操作 |

**影响评估要点**：数据自 2026-10-15 首次推送起已在公网可见；无论选 B 还是 C，
都无法撤回这段时间内可能发生的抓取/缓存（如第三方归档）。因此**是否值得重写历史，
取决于这些数值的实际敏感等级**——这一点必须由任务负责人判断，我不做假设。

**我的建议**：若这些价格属于对外保密信息，选 **C**（最彻底，且本仓库无协作痕迹）；
若只是一般内部参考值，选 **A** 并把本报告作为风险记录归档。
**在你明确授权前，我不会执行 B 或 C。**

---

## 5. 残余风险

| # | 风险 | 等级 | 处置 |
|---|---|---|---|
| 1 | Git 历史仍含内部派生金额（§4 方案 A 的后果） | 中 | 待你决定是否重写历史 |
| 2 | 短数字类指纹（如单个 GPU 每卡年成本）无法用字符串扫描可靠识别（熵太低） | 低 | 依赖"原始 Excel / local.db / exports 三者均不入库"这一更强的不变量，已由忽略规则测试保证 |
| 3 | 仓库为 Public：任何人都能读取**已脱敏**的样本与官方来源存档 | 低 | 脱敏样本明确标注 `synthetic_public_sample`；存档页面版权见 `data/raw/official/NOTICE.md` |
| 4 | 未来若有人把真实数据手工拷进 `data/examples/` 或文档 | 中 | 扫描门禁会拦截已登记的长数值指纹；**短数值仍需人工 review**（已写入 §5.2 的提交检查说明） |
| 5 | 远端仓库权限、历史重写等操作超出本地能力 | — | 本报告只提出方案与影响，不代为执行 |

### 5.2 提交前检查（可直接用于 CI）

```bash
bash scripts/verify_all.sh          # 第 19 步即"敏感数据扫描"；本地有 data/private/sensitive_patterns.txt 时按特征表扫描
.venv/bin/python -m pytest tests/test_v12_hardening.py -q   # 同一规则的测试版本
```

在公开克隆中 `data/private/` 不存在，扫描退化为**忽略规则校验**（因为公开仓库本就不该有这些值）；
在真实开发机上则会按完整特征表扫描——这样既形成拦截，又不把指纹本身写进公开仓库。

---

## 6. 复现与验证记录

| 命令 | 结果 |
|---|---|
| `git rm -r --cached data/exports` | 5 个文件移出跟踪，本地文件保留 |
| `git check-ignore -v data/exports/legacy_results.json data/private/x.json data/raw/*.xlsx` | 三者均命中忽略规则 |
| `git check-ignore data/examples/... data/raw/official/...` | 未命中（可提交）✓ |
| `.venv/bin/python scripts/make_public_sample.py` | 生成样本 + 手算期望值，3 行数值已打印 |
| 引擎复算该样本并与手算期望值比对 | **全部一致**（容差 1e-9）；公开克隆 `155 passed, 106 skipped, 0 failed` |
| `bash scripts/verify_all.sh` | `步骤 22 项：通过 22，失败 0`；第 19 步输出"已跟踪文件中未发现内部定价数值（按本地特征表扫描 9 个特征串）" |
| 凭证扫描（当前树 + 全部历史） | 0 命中，无需轮换 |
