# V7.0 最终验证与封板报告（F6）

**代码基线**：`e138611`（F1–F5 加固后）
**本轮范围**：F6-1 成本币种确认与排序门禁 → F6-2 AISimulate 运行目录隔离 → F6-3 全量回归与封板
**结论**：**F6 通过，V7.0 核心计算、模拟搜索与推荐逻辑正式冻结。**

> 本轮日志：`docs/test_run_log.txt`
> （sha256 `08ef7f6b8d203b41db4364ee03f61c1443cd9f39a4a32c88c5a5867b0b8b2e4e`，
> 完成时间 `2026-10-20T11:35:57+08:00`）。第 61 步为**真实工具执行**，不是跳过分支。

---

## 1. F6-1：成本币种确认与推荐排序

### 1.1 问题成立

`_attach_simulated_costs()` 构造 `SimulatedCost` 时**没有传** `currency_confirmed`，
而 `SimulatedCost.currency_confirmed` 默认 `False`。后果有两层：

* 预算门禁永远拿不到确认状态 → 一律 `unknown`（保守，不会误判，但等于白丢信息）；
* `cost_ranking_available()` 完全没有检查币种确认 → 未确认币种的金额仍可能被拿去排序。

### 1.2 修复

* **透传**：`currency_confirmed=bool(tco.currency_confirmed)` —— 直接用现有 `TCO` 的结论，
  没有另造一套币种确认机制；
* **排序门禁**（四个条件同时满足才允许成本排序）：
  1. 参与排序的候选成本完整、金额明确；
  2. **币种已确认**；
  3. 币种 / 成本口径 / 计算年限完全一致；
  4. 参与排序的**全部**预测达标候选都满足以上条件。
* 任一不满足 → `available=False` 并给出**具体原因**（缺金额 / 币种未确认 /
  口径不一致分别说明），随后降级为吞吐优先；
* 降级后**仍只从 `predicted_pass` 里选**（不会掉到不达标候选）；
* 报告写"已降级为吞吐优先（仍然只从预测达标的候选里选）"，
  并明确未确认的成本只能作为**假设情景**展示。

### 1.3 验收用例（要求的 6 个场景全部落地）

| 场景 | 预期 | 结果 |
|---|---|---|
| 两条成本完整、币种已确认 | 允许成本排序，选便宜者 | ✅ `test_cost_ranking_allows_only_when_every_candidate_is_confirmed` |
| 一条币种未确认 | 禁止排序 | ✅ `test_cost_ranking_rejects_unconfirmed_currency` |
| 两条币种不同 | 禁止排序 | ✅ `test_cost_ranking_rejects_mixed_currency` |
| 金额存在但年限不同 | 禁止排序 | ✅ `test_cost_ranking_rejects_mixed_horizon` |
| 一条候选缺成本 | 降级吞吐，且不抛异常 | ✅ `test_cost_ranking_unavailable_when_one_candidate_lacks_cost` |
| 降级且存在不达标候选 | 不得选不达标者 | ✅ `test_cost_ranking_never_falls_back_to_a_failing_candidate` |

---

## 2. F6-2：AISimulate 运行目录隔离

### 2.1 问题成立

自动搜索此前直接写入 `work_dir`，重复执行会读到上一次的 `search/`、`predict/`
产物（F1 加固期间就实际踩到过"输出目录非空"导致的失败）。

### 2.2 修复

每次运行创建**唯一**目录（ID 由 UTC 时间戳 + 随机后缀生成，不依赖目录里的既有名称）：

```
work_dir/
└── runs/
    ├── run_20261020T033217Z_be477b/
    │   ├── search/            # 本次扫描器的输出
    │   ├── predict/out_1/     # 本次每条候选的回放输出
    │   ├── search_input.yaml
    │   └── manifest.json      # run_id / created_at / 输入输出 + sha256 / 失败项 / 未对齐项
    └── run_.../
```

对应八条要求：

1. **独立运行目录** ✅ 每次 `work_dir/runs/<run_id>/`，`mkdir(exist_ok=False)` 保证唯一；
2. **同一次运行** ✅ 搜索、回放、配置、manifest 全在本目录下；
3. **不跨历史补结果** ✅ `discover_outputs()` 只在本次运行目录内递归；
4. **记录输入/输出路径与 SHA256** ✅ `manifest.inputs` / `manifest.outputs`；
5. **单候选失败继续** ✅ 已在 F2 用 `_replay_one()` + `except Exception` 实现；
6. **不删除用户数据** ✅ 新运行不会清理既有运行目录（有专门测试）；
7. **报告证据路径指向本次运行** ✅ 实测报告里 `搜索空间存档`
   指向 `.../runs/run_20261020T033217Z_be477b/search/...`；
8. **离线归档模式兼容** ✅ 离线路径完全不变（`load_archived` 无需 work_dir）。

### 2.3 验收用例

| 场景 | 结果 |
|---|---|
| 连续运行 A、B 两组配置，B 不引用 A 的结果 | ✅ `test_second_run_never_reads_the_first_runs_artifacts` |
| A 成功、B 失败时，B 不能用 A 的旧文件伪装成功 | ✅ 同上（B 的 `outputs` 为空、`candidates` 为空） |
| 同一配置重复运行，两次分别归档 | ✅ `test_each_run_gets_its_own_directory` |
| 两次运行的 Manifest / 路径 / 哈希相互独立 | ✅ `test_manifests_and_hashes_are_independent_per_run` |
| 新运行不删除旧运行目录 | ✅ `test_run_directory_is_not_created_by_deleting_old_ones` |
| 单候选超时与异常隔离不被破坏 | ✅ F2 的 3 条用例继续通过 |

---

## 3. F6-3：全量回归

| 项 | 上轮 | 本轮实测 |
|---|---|---|
| `pytest -q` | 860 | **867 passed**（装工具时）；无工具 866 + 1 skipped |
| `bash scripts/verify_all.sh` | 65 步 | **65 步，通过 65，失败 0** |
| 第一阶段历史基线 | 32 / 160 / 192 | **32 / 160 / 192 不变** |
| 复算超差 | 0 | **0**（`mismatch_count: 0` ×24） |
| 官方候选逐字段核对 | 12/12 | **12/12（不一致 0 条）** |
| AISimulate 自动搜索 + P99 回放 | 可运行 | **可运行**（第 61 步真实执行） |
| 模拟结果进 `BenchmarkRun`/`CapacityPoint` | 否 | **否**（结构性隔离 + 运行后记录数不变） |
| `predicted_pass` → `observed_pass` | 否 | **否**（两套取值，报告显式区分） |
| 敏感数据扫描 | 未命中 | **未命中** |

本轮新增测试 7 条（F6-1 三条 + F6-2 四条），并更新了 4 条既有用例适配运行目录结构。

---

## 4. 封板结论

**V7.0 核心计算、模拟搜索与推荐逻辑自本报告起冻结。**

冻结范围：

* 资源规划（Sizing）、Benchmark 匹配、SLA 评价、TCO / 预算、报价门禁 —— 本轮未改动；
* AISimulate 适配器（离线解析 + 自动两段式）与推荐资格门槛 —— 本轮只做了 F6 的两处加固；
* 模拟与实测的结构性隔离、`predicted_*` 与 `observed_*` 的两套取值 —— 保持不变。

**不再做的事**：不新增搜索算法、不接入更多硬件平台、不开发 Web 前端、
不开启生产自动报价、未经授权不重写 Git 历史。

---

## 5. 残留问题与边界（不因封板而消失）

1. **模拟仍不是实测**：`predicted_pass` 永远不等于 `observed_pass`；
   模拟值不写 `BenchmarkRun` / `CapacityPoint`。
2. **官方精度证据不覆盖目标模型**：发布的 E2E 快照里没有 `Qwen3-32B`；
   全量 MAPE 为 TTFT 45.27% / TPOT 22.39%。模拟值只适合排序与发现明显不可行。
3. **扫描器单点 ≈ 原生中位数**，不是 P99（P99 可达其 13.9 倍）；达标判定只认 P99。
4. **本系统不建模的拓扑**（PP>1 / EP / PD 分离 / CP）只作模拟参考，不宣称可部署。
5. **搜索不以请求并发为约束**：AISimulate 的搜索空间没有该维度，
   目标并发靠结果过滤；这一点已在代码与报告中区分。
6. **生产审批与正式报价发布**仍禁用；依赖可信审批系统。
7. **Public 仓库历史提交中的内部定价数据风险**仍需独立闭环
   （当前扫描未命中不代表历史提交安全；未经明确授权不改写 Git 历史）。

---

## 6. 下一步：N5 与 N6

按任务书顺序，封板后进入两项真实验收：

* **N5 用户易用性验收**（`docs/n5_usability_test_kit.md`）：
  需要一位不熟悉源码的真实使用者，按任务单独立完成并填写反馈表。
  Agent 只负责整理材料与分析反馈，**不能**代替真人完成验收；
  结论必须等到真人操作之后才写（`docs/n5_usability_validation.md`）。
* **N6 RTX 3090 实测管线验证**（`docs/n6_real_benchmark_validation.md`）：
  先做只读环境检查并提交环境报告；**在获得明确批准前不安装软件、不下载模型、
  不变更系统配置**。它验证的是"真实 Benchmark 能否进入系统"，
  **不**为 H100 的模拟结果提供校准点。
