# Z 阶段验收：预测覆盖与 AISimulate 集成

基线：`7e6f927`（Y 阶段封板）
执行日期：2026-10-22
范围：Z0 任务 ID 边界、Z1 覆盖矩阵、Z2 实时搜索/回放、Z3 代表性工作负载、Z4 集成一致性。

> 本阶段仍然**不做真实 GPU Benchmark**。所有 P99 都是 AISimulate 的
> **预测**（`simulated_prediction`），不是实测，不会写入 `BenchmarkRun` /
> `CapacityPoint`，也不会产生 `observed_pass`。

## 0. 结论先行

| 项目 | 结论 |
|---|---|
| Z0 任务 ID / 沙箱目录边界 | 已修复，非法 ID、路径穿越、符号链接均被拒绝 |
| Z1 覆盖矩阵 | 已生成 `docs/performance_coverage.md` + `.json` |
| Z2 实时环境 | `aisimulate 0.12.1` 在独立 venv 可运行，无需 GPU |
| Z2 实时搜索 + 回放 + 报告 | 通过；strict/relaxed 两条链路均产出结构化结论 |
| Z3 C8 / C32 / C64 | 三个工作点均有独立原生 P99 证据并已归档 |
| Z4 手动 / Agent 一致性 | 通过（C8、C64、C16 三组对照） |
| 三个原有演示场景 | strict 未达标 / relaxed 推荐 / cost 门禁保持原结论 |
| 真实 Benchmark（第 4 级） | **仍然没有**：本阶段不产生实测证据 |

## 1. Z0：任务 ID 与沙箱目录边界

修复内容（`commandcode_client.py`）：

* 所有任务 ID 统一校验：只允许字母/数字开头，长度 1–128，禁止 `.`、`..`、
  路径分隔符、空串与以 `-`/`.` 开头的值；
* `_prepare_sandbox()`、`cleanup()`、`run()`、`continue_run()`、`task_path()`
  全部走同一校验；
* 沙箱 HOME 必须是 `data/agent/cc-homes` 的**直接子目录**，符号链接直接拒绝；
* 过期清理跳过符号链接，只删除真实子目录；
* `tool_cli` 的 ID 规则同步收紧，`.` 不再能作为 task_id。

反向测试（`tests/test_v_agent.py`）覆盖：

* 非法 ID 参数化：`""`、`"."`、`".."`、`"../escape"`、`"a/b"`、`".hidden"`、
  `"-flag"`、含空格、超长；
* 清理单个任务不影响其他 HOME，也不会删除整个 `cc-homes`；
* 符号链接 HOME 被拒绝且目标目录不被删除；
* 非法 task_id 的续跑不会调用 Agent；
* `report --task-id .` 返回 `failed`。

## 2. Z1：预测证据覆盖矩阵

生成命令：

```bash
.venv/bin/python scripts/build_performance_coverage.py
```

交付：

* `docs/performance_coverage.md`（人员可读）
* `docs/performance_coverage.json`（机器可读）
* `data/simulation/archives/`（实时证据；不含价格）

当前统计（2026-10-22）：

| 指标 | 数值 |
|---|---|
| 证据总数 | 142 |
| 模拟扫描证据（2 级） | 87 |
| 原生 P99 证据（3 级） | 5 |
| 真实 Benchmark 证据（4 级） | 0 |
| 工作点总数 | 72 |
| SLA 可用工作点 | 4 |
| 仅参考记录（公开/历史 Excel） | 50 |

四级口径：

1. `static_only`：Catalog 静态规划；
2. `simulated_scan_only`：扫描器稳态单点，**无 P99**；
3. `simulated_native_p99`：可比较的原生回放 P99；
4. `measured_benchmark`：目标配置的真实 Benchmark。

公开参考数据与历史 Excel 记录单列为 `public_reference` / `internal_reference`，
不计入四级，不参与 SLA 与报价门禁。矩阵生成器**只读性能字段**，不读价格。

## 3. Z2：AISimulate 实时环境与全链路

### 3.1 环境

| 项 | 值 |
|---|---|
| 工具 | `aisimulate` |
| 版本 | `0.12.1` |
| 独立环境 | `/tmp/aisim-venv`（不修改主项目锁定依赖） |
| Python | 3.12.3 |
| GPU | 无（`nvidia-smi` 不参与） |
| 数据库模式 | `SILICON` |
| 模型 / 硬件 | Qwen/Qwen3-32B / `h100_sxm` |
| 后端 | vLLM 0.24.0，BF16 |

### 3.2 实时回放复现归档

用与 N 阶段相同的 TP8 / C32 / ISL8192 / OSL256 / 500 请求输入重新执行
`aisimulate predict`：

| 指标 | 归档 `17_...json` | 本次实时 |
|---|---|---|
| TTFT P99 (ms) | 5201.827252000001 | 5201.827252000001 |
| TPOT P99 (ms) | 31.45861707450993 | 31.45861707450993 |
| 输出吞吐 (tok/s) | 990.888548064229 | 990.888548064229 |
| 完成请求 | 500 | 500 |

结论：关键 P99 与吞吐**逐值一致**。仅浮点末位、`wall_time_ms`、
`processed_*` 等运行态字段不同；未修改任何历史归档。

归档：`data/simulation/archives/z-live-c32-tp8-isl8192-osl256-bf16/`
（manifest sha256 前 16 位：`5fb528ee7874cfa6`；prediction：`bc776a31e8a1b6c6`）。

### 3.3 全链路（需求 → 搜索 → 过滤 → Top-N → P99 → SLA → 报告）

运行：

```bash
AISIMULATE_PYTHON=/tmp/aisim-venv/bin/python \
bash scripts/run_system_demo.sh --mode live --scenario strict
```

| 项 | strict | relaxed |
|---|---|---|
| 业务运行目录 | `data/exports/z2_live_pipeline` | `data/exports/z2_live_pipeline_relaxed` |
| AISimulate run_id | `run_20261022T031934Z_780580` | `run_20261022T031950Z_0909b8` |
| 搜索候选 | 39 条扫描器候选 | 39 条扫描器候选 |
| 对齐后可回放 | TP4 / C32（1 条） | TP4 / C32（1 条） |
| 原生 P99 | TTFT 8359.441161 ms / TPOT 50.170601 ms | 同左 |
| 判定 | `predicted_fail`（400/30） | `predicted_fail`（6000/40） |
| 报告 | 已生成方案 + 技术报告 | 同左 |

**差异说明（重要）**：

* 离线 Demo 使用 TP8 归档：P99 = 5201.83 / 31.46 ms，在 6000/40 下预测达标；
* 实时自动搜索在 8 卡上限内选择了 TP4 部署（扫描器排序结果），其 P99 =
  8359.44 / 50.17 ms，在 6000/40 下预测未达标；
* 这是**部署不同**（TP4 vs TP8），不是工具版本差异，也不是把两个工作点当成同一个；
* 因此实时 relaxed 场景没有复现“预测达标”。本轮**没有**为了让结论好看而改写
  搜索结果或历史归档。

本次全链路搜索/回放证据已归档：
`data/simulation/archives/z-live-pipeline-c32-tp4-bf16/`
（manifest `717267d7cb95a3c5`，prediction `f13e535018ccb2cc`）。

### 3.4 工具身份与可复现性

* 工具版本、数据库模式、模型/硬件、后端/版本、搜索输入与回放输入均写入
  `manifest.json` / `exp_config.yaml`；
* 原始 `prediction.json` 与输入 YAML 均带 sha256；
* 每次运行一个独立 `run_id`，不覆盖历史目录；
* 未出现错误或“未对齐”记录（`errors=[]`、`not_aligned={}`）。

## 4. Z3：新增代表性工作负载

固定 Qwen3-32B / H100 SXM / BF16 / vLLM 0.24.0 / TP8 / PP1 / DP1 /
ISL8192 / OSL256 / 500 请求，只改变并发：

| 工作点 | TTFT P99 (ms) | TPOT P99 (ms) | 输出吞吐 (tok/s) | strict 400/30 | relaxed 6000/40 |
|---|---|---|---|---|---|
| C8 | 727.296924 | 11.230073 | 667.530 | 未达标（TTFT） | 预测达标 |
| C32 | 5201.827252 | 31.458617 | 990.889 | 未达标（TTFT/TPOT） | 预测达标 |
| C64 | 11211.532603 | 56.438597 | 1115.095 | 未达标 | 未达标 |

归档与哈希：

| 工作点 | manifest sha256（前 16） | prediction sha256（前 16） |
|---|---|---|
| C8 | `e1d0bfcc46374541` | `1a069690fe2524f5` |
| C32 | `5fb528ee7874cfa6` | `bc776a31e8a1b6c6` |
| C64 | `31a0cf489f468f18` | `db63e9234ea1da4d` |

解读边界：

* 三个点分别保存，**没有插值**；C16 等未测并发不能由 C8/C32 推出；
* C64 吞吐更高但尾延迟更差，不能只看吞吐判定“更好”；
* 这些是模拟预测，不是真机结果。

## 5. Z4：集成一致性

### 5.1 离线工作点选择

新增 `src/llm_capacity_pricing/app/offline_archives.py`：

* 只选择与请求**同一工作点**（模型、system、ISL/OSL/并发、精度、框架/版本）的
  原生 P99；
* 同一工作点有多个部署时，优先 GPU 数更多的（TP8 优先于 TP4）；
* 找不到原生证据时，只保留扫描器枚举，报告明确写缺口；
* 精度/框架不一致但工作点相同的归档仍会展示，但被标记为**不可比**，
  不会产生 `predicted_pass`。

### 5.2 手动与 Command Code Agent 对照

真实 Command Code 运行（非 mock）：

| 请求 | 手动结果 | Agent 结果 | 一致性 |
|---|---|---|---|
| C8，6000/40 | 原生 P99 727.30/11.23，`predicted_pass` | 同候选、同 P99、同 verdict | 一致 |
| C16，6000/40 | 无原生 P99，仅扫描器候选 | 同左，`simulation_best_effort` 与手动一致 | 一致 |
| C64，6000/40 | 原生 P99 11211.53/56.44，`predicted_fail` | 自动化测试对照一致 | 一致 |

Agent 运行记录：

* C8：run_id `20261022T050258Z_0b8e68`，session `cc-74352d76957c4ca0`
* C16：run_id `20261022T050322Z_4c6c91`，session `cc-c51f40fa97494bce`

（Agent 的实际报告在本地 `data/agent_runs`，按仓库边界不入库；上面的 run_id
与结构化结论来自 `agent_result.json`。）

**本轮发现并修复的集成问题**：bwrap 沙箱最初只绑定了
`docs/poc` 与 `data/models` 等目录，没有绑定 `data/simulation`，导致 MCP 工具
在沙箱里看不到新归档，Agent 只能读到 PoC 扫描器结果而读不到 C8/C32/C64 的
原生 P99。修复方式是在只读绑定中加入 `data/simulation`（仍然不绑定
`data/private`、`data/local.db`、`data/exports`），并补了沙箱绑定断言。
修复后上述 C8/C16 结果与手动模式一致。

### 5.3 三个原有演示场景回归

| 场景 | 结论 |
|---|---|
| strict（400/30） | `sim-native ... tp8 ... c32` 预测未达标 |
| relaxed（6000/40） | 同一 TP8 工作点预测达标并被推荐 |
| cost | 仍拒绝把合成成本当“成本最优”，预算保持未知 |

离线 Demo 的 TP8 C32 结论没有因 Z 阶段改动而变化。

## 6. 验收执行

| 项目 | 结果 |
|---|---|
| 全量测试 | `1032 passed, 1 skipped` |
| 全链路校验 | `verify_all.sh` 66 / 66 通过 |
| 实时自动搜索检查 | `check_auto_search.sh` 通过（`AISIMULATE_PYTHON=/tmp/aisim-venv/bin/python`） |
| codex 审核 | `gpt-6-sol` + `low` 六轮：P1 4 项、P2 8 项，均已修复并复跑回归 |

六轮 codex 发现的问题：

1. 归档缺失精度时被当成身份匹配 → 改为缺失字段不匹配，且不得用请求值补归档精度；
2. `--work-dir` 归档按后缀复制，可能把成本/报告带进公开仓库 → 改为性能证据白名单；
3. `--name`/`--archive-root` 可路径穿越并在 `--overwrite` 时删除仓库外目录 → 归档目标必须是归档根的直接子目录，拒绝符号链接；
4. 覆盖矩阵用“请求样本数”而不是“实际完成数”判断 P99 可用性 → 改为 `completed_requests`；
5. 失败运行的半成品预测仍进入覆盖矩阵 → 有 `errors` 的运行只记阻塞，不产出证据；
6. 归档选择忽略用户 GPU 卡数上限，可能把 8 卡预测当成 4 卡方案的可比证据 → `gpu_limit` 参与选择，超限/卡数未知一律不匹配；
7. 离线选择用 `stop.requests` 判断样本窗口 → 改为归档 `completed_requests`；
8. 无对应模型/硬件/长度时仍套用 PoC 扫描器 → 扫描器只在 Qwen3-32B / H100 SXM / ISL8192 / OSL256 下启用，否则不启用模拟；
9. 归档名符号链接在 `resolve()` 之后才检查，可能删除链接目标 → 先检查未解析路径，再解析并校验直接子目录；
10. 多候选归档（`predict_1..N`）整个被跳过 → 按 `predict_N` / `out_N` 序号配对，逐条建立可选择的证据；
11. 用户限制的 TP 列表未参与归档选择 → `tp_sizes` 参与匹配，列表外/未知 TP 不选；
12. 归档 GPU 总数只算 TP×replicas，PP>1 时会低估 → 改为 TP×PP×replicas；
13. 覆盖矩阵按完整 workload 哈希，样本窗口/数据集不同会把同一工作点拆开 → 只按文档规定的工作点维度分组，样本窗口保留在证据上。

## 7. 仍未完成 / 不做

* **真实 GPU Benchmark（第 4 级证据）**：本阶段没有执行，也没有伪造；
* N5 真人易用性验收、N6 真机压测：保持暂停；
* 生产报价发布、多用户平台部署：保持禁用；
* 实时自动搜索的候选选择仍由扫描器排序决定；本轮只记录“TP4 vs TP8”差异，
  没有为了演示效果强行改选 TP8；
* 24 小时沙箱清理仍是惰性清理（下次准备沙箱时触发），不是定时任务。
