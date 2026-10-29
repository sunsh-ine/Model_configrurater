# AJ 阶段验收：真实容量边界与 SLA Goodput 验证

基线：`d32fe5b`
目标硬件：`10.0.102.61` 单张 NVIDIA RTX 3090 24GB
目标模型：Qwen3-8B BF16
推理框架：vLLM 0.11.0（冻结运行配置见 `data/benchmark/rtx3090/runtime_config.json`）

## 已通过

### AJ0 冻结配置、分层与预注册

* 冻结运行配置写入 `data/benchmark/rtx3090/runtime_config.json`，
  每条 AJ 运行归档 `runtime_config_sha256`；同一工作点的正式重复必须同哈希。
* 资格分层：`ingest_accepted` / `sla_evaluable`（仅延迟）/
  `reliability_status` / `observed_sla_result` / `calibration_usable` /
  `production_commitment_allowed`。单次失败即 `reliability_status=fail`，
  `observed_sla_result=unknown`，不因延迟达标而升级。
* C6 预注册清单：`data/benchmark/experiments/aj-formal-c6.json`
  （5 个预期 seed、每 seed 500 请求、SLA 6000/40、配置哈希
  `cb8719a6…50c594c4`），由 `scripts/preregister_benchmark_experiment.py` 生成。
* 实验清单审计：`scripts/audit_benchmark_experiment.py` 只计入预注册请求数；
  100 请求的探索运行标记 `excluded_runs`，不冒充正式重复。
* 客户端口径：`scripts/benchmark_client.py` 与服务端 vLLM 分开记录，
  不与 vLLM `bench serve` 统计混用（`docs/ai_benchmark_protocol.md` §3.1–3.2）。

### AJ1 容量指标结构

* `perf/capacity_analysis.py`：`CapacityPoint` 分开 arrival/completed/output、
  P99、success_rate、逐请求 SLA Goodput、延迟裕量、窗口稳态与队列状态。
* Goodput 只计“成功且 TTFT≤6000 ms 且 TPOT≤40 ms”的请求；失败请求不进入 P99 样本。
* 稳态只有 `observed_stable_in_window` / `unstable` / `unknown` 三种，
  有限窗口不宣称长期稳定；没有服务端队列时序时 `queue_status=unobserved`。

### AJ2 固定并发扫描

* 2048/256：C2、C4、C6、C8、C10、C12；4096/256：C2–C8；8192/256：C1、C2。
* 探索点均为 100 请求（8192 C1 为 20 请求），不作为正式容量。
* 结果：`docs/aj_capacity_scan.md` / `aj_capacity_scan.json`。

### AJ3 正式重复与 SLA 裕量

* C6 正式 5×500（预注册）：4 次 500/500，1 次 499/500；
  TPOT P99 34.91–35.49 ms，TTFT P99 1370–1818 ms。
  延迟有 10%/5% 内部裕量，但可靠性不通过，**不作为裕量安全容量**。
* C8 正式复现 5×500（冻结配置）：5/5 成功，
  TTFT P99 1788–1832 ms，TPOT P99 38.63–39.39 ms，吞吐 197.3–198.5 tok/s。
  全部满足 SLA，但无 10%/5% 裕量（最差 TPOT 39.39 ms > 38 ms）。
* 归档、来源登记、入库、`attest-run` 全链路完成；
  `source_attestations.json` 含 AI 5 条 + AJ 11 条受控运行。
* 失败运行保留，未被替换或删除。
* 结果：`docs/aj_sla_margin_analysis.md`、`docs/aj_experiment_audit.json`。

### AJ4 Poisson 开放到达

* 0.3 / 0.5 / 0.65 / 0.8 / 1.0 req/s 各 100 请求；0.65 追加 500 请求正式窗口。
* 0.65：500/500 成功，实际到达/完成 0.646 req/s，
  TTFT P99 1396 ms、TPOT P99 39.12 ms、Goodput 0.644 req/s，
  窗口内 `observed_stable_in_window`；但只有 1 次 500 请求窗口，
  未达到 5 次正式重复，标记 `insufficient_repeats`，不作为容量候选。
* 0.8：1/100 失败，`unstable`；1.0：实际只到 0.756 req/s，属于有限过载观察。
* 固定并发与开放到达分别报告，不互相换算。
* 结果：`docs/aj_open_arrival_stability.md`。

### AJ5 长上下文与瓶颈归因

* 4096/256：C2 通过（TPOT 30.75 ms），C4 38.39 ms，C6/C8 46.2/54.2–54.6 ms 超标。
* 8192/256：C1/C2 TTFT P99 2.06/4.13 s，吞吐 31.0/47.8 tok/s；更高并发未验证。
* 客户端 C10/C12 超过服务端 `max-num-seqs=8` 后吞吐平台化、TTFT 跳到 11.5–12.5 s；
  只能确认现象，不能把增量精确归因到单一内部队列。
* 结果：`docs/aj_performance_bottlenecks.md`。

### AJ6 真实容量推荐

* `perf/capacity_recommendation.py` + `scripts/aj_capacity_recommendation.py`：
  固定并发与开放到达分开，只有“正式 + 全部成功 + 配置绑定”的点进入容量结论。
* 结论：最大正式固定并发 **C8**（无内部裕量）；开放到达 0.65 req/s 只作为窗口观察；
  10%/5% 裕量候选 **0 个**；最高输出吞吐 198.55 tok/s（`aj-e1c8-seed3`）。
* 正式容量只认“命名批次 + 配置哈希 + 导入校验 + 原始文件哈希/来源 + 5 个独立 seed”
  同时成立；缺一项即降级，不允许单次成功运行升级为容量。
* C6 的延迟裕量被明确写成“不可用原因”，不进入候选。
* 结果：`docs/aj_capacity_recommendation.md` / `.json`。

### AJ7 客户场景 E

* `scripts/aj_scenario_e.py` 用推荐 C8 调用统一 `plan` 入口：
  `exact_match`、`observed_pass`、`verified_sla_met`，
  证据为 `rtx3090:aj-e1c8-seed1…seed5`。
* 实测 P99：TTFT 1831.8 ms、TPOT 39.39 ms；吞吐 197.25 tok/s。
* `PlanResult.capacity_evidence` 保存完整容量结论，并随 `decision_evidence` 输出。
* 方案状态 `partial` 仅因成本与报价资格未配置；报价仍不可发布。
* 结果：`docs/aj_customer_scenario_e.md`、`docs/aj_scenario_e/`。

### AJ8 回归

* 全量 pytest：**1218 passed / 1 skipped**。
* live `verify_all.sh`：**66/66 通过**。
* 覆盖矩阵重新生成：309 条模拟/参考证据、79 个模拟 SLA 工作点；
  另计 **16 次已验证真实运行、3 个真实工作点**。

## 已修复

AJ 实现与审核过程中发现并修复：

* `TargetRequirement` 缺 `runtime_config_sha256`，场景 E 排序直接报
  `AttributeError`（P0）；补齐字段并把哈希贯穿 `_target_from_facts`、
  `_identity_from_facts`、ranker 与 workflow 目标身份。
* 配置哈希比较把“双方都未记录”当成“未确认”，会让全部历史证据降级；
  收窄为：双方都缺失不阻断，单边缺失才标记未确认，双方不同才判不匹配。
* 数字型/官方候选签名误用未定义的 `record` 变量（NameError），改为
  `runtime_config_sha256=None`。
* 容量报告按目录名判正式，导致未绑定配置的 AI 运行进入正式容量；
  改为“目录前缀 + 冻结配置哈希”双条件，并把历史运行标为 `legacy_unbound`。
* 实验审计把 100 请求探索运行算进同一 seed；改为只计预注册请求数，
  其余列入 `excluded_runs`。
* 固定并发汇总的稳态取集合并集（出现 `stable,unstable`）；改为最坏优先：
  任一 `unstable` 即 `unstable`。
* 峰值吞吐/ Goodput 跨 ISL 混排；改为带 `run_id`/工作点来源，并注明
  “峰值吞吐不等于安全容量”。
* 来源认证只比较声明的哈希字符串；现在同时重算 `metadata.runtime_config`
  的规范哈希，并要求与登记值、run manifest 完全一致（不允许只改配置内容）。
* 容量报告直接信任旧的 `import_report.json`；现在每次重算原始文件哈希、
  身份哈希、client summary 与测量窗口，并重新执行来源认证，任一不符即降级。
* 正式容量只按目录名判断；现在要求 5 个独立 seed、每个 ≥500 请求，
  不足则标 `insufficient_repeats`，不进入推荐。
* 实验审计中同 seed 的失败副本会被成功副本掩盖；现在重复 seed 显式列出
  并使该 seed 失去有效资格。
* 测量窗口用 `end_ms` 当作时长；改为 `end_ms - start_ms`。
* 归档只对传入的 runtime JSON 做哈希，未核对服务端是否真的按该配置启动；
  现在归档解析服务端日志的 non-default args / engine config，逐项比对
  dtype、max_model_len、max_num_seqs、enforce_eager、prefix cache、
  chunked prefill、kv cache dtype 与版本，任一不一致即拒绝归档；
  日志未打印的 gpu_memory_utilization 按 vLLM 默认值推断并单列，attention_backend 标为未验证
  （见 `docs/aj_runtime_execution_check.json`）。
* 容量推荐构建器接受任意单点；现在要求证据 ID、合格标记与 5 个独立 seed，
  单次运行或未认证输入不能成为“已验证容量”。
* 正式批次中失败/未通过校验的运行会被 `formal` 过滤掉；现在保留为
  `invalid_formal_runs` 并阻断同一工作点，不再“少一个也算完整”。
* 峰值吞吐选择只看配置哈希；现在同时要求完整性/来源合格。
* 成功请求缺 output 长度时按 0 计入吞吐；现在吞吐为 unknown。
* 审计的重复 seed 检测遗漏了被排除的错请求数运行；现在一并计入。
* 声明 `quantization: null` 时不再跳过比对；日志出现量化即判为不一致。
* 开放到达先按全部重复分组，再要求整组稳定/成功，失败运行不会被排除后
  由剩余 5 个成功运行伪造稳态容量。
* 容量判定优先使用调用方给出的 SLA 上限，而不是数据点自带的旧上限。
* 场景 E 会校验推荐证据的模型 revision/硬件与请求一致，不一致直接拒绝。
* 固定并发与开放到达使用统一但区分模式的工作点键，分块过滤不再 KeyError。
* 推荐构建按部署身份 + 冻结配置 + 负载 + 并发分组，跨配置运行不会互相充当
  重复 seed；推荐脚本复用扫描结果中已过滤的候选，而不是自行重建资格。
* 服务端日志缺少性能相关字段时不再“无 mismatch 即通过”，必填字段未验证
  即拒绝归档；`quantization: null` 与日志 `quantization=None` 视为已验证。
* 容量分组与开放到达分组都加入模型/revision/硬件身份，跨部署运行不再合并。
* 日志回退的 `max_seq_len` 统一转整数后再比较，避免类型不一致误报。
* 无成功请求或缺 TTFT/TPOT 的运行不再让报告崩溃，也不再输出数值 Goodput；
  Goodput 不可完整计算时保持 unknown。
* 推荐输入保留 `ingest_accepted`/`sla_evaluable`，与扫描的过滤口径一致。
* 场景 E 直接使用推荐证据绑定的 runtime hash，不再从目录里挑第一个同并发运行。
* seed 现在由 `client_summary_sha256` 绑定到归档记录/run manifest/登记表；
  报告重算 summary 哈希，改 seed 不能被当成独立重复。
* 开放到达候选要求实际到达/完成 ≥ 计划到达率的 95%，客户端没打满的速率
  不会被当成观测容量。
* 吞吐/Goodput 缺失时返回 unknown，不再用 0 代替。
* 正式资格改为读取 `data/benchmark/experiments/*.json` 预注册工作点，
  新实验不依赖目录名前缀也能进入正式判定。
* 开放到达汇总改用全部正式重复中的最小实际到达/完成率，不再只看第一条运行。
* 容量构建要求 `ingest_accepted`/`sla_evaluable` 明确为 true，缺失不再默认通过。
* 历史归档没有 summary 哈希时仍可重新登记；只有声明了哈希才做强绑定。
* 到达时间不完整时 arrival_rps 保持 unknown，不按总请求数估算。
* 实验审计先执行原始文件/summary/import_report/来源完整性校验，再计入覆盖。
* 容量重复分组包含模型 revision，跨 revision 不能拼成五个重复。
* GPU 显存利用率无法从日志读取时，仅当值等于 vLLM 0.11 默认 0.9 且未出现在
  non-default args 中才记为“默认值推断”，与显式核对分开。
* 预注册清单支持 `expected_run_ids`，错误请求数或同 seed 的探索运行不会被算作
  正式槽位；正式重复仍要求 5 个唯一 seed。
* 归档先完成全部校验再写入目标目录，复用 run-id 不会在校验失败前覆盖旧证据。
* 稳态比较按到达时间排序后再切前后半窗；缺到达时间则 unknown。

## 未确认

* C8 的长期稳定性：只有 5×500 有限窗口，无服务端队列时间序列；
* C8 贴近 TPOT 上限（39.39 ms / 40 ms），生产余量未验证；
* C6 的 1/500 失败是偶发还是系统性，本轮无法判定（未重跑同一 seed）；
* Poisson 0.65 req/s 只证明窗口内稳定，不是长期稳态上限；
* 多卡 TP/DP、PD 分离、SGLang 后端未验证；
* AISimulate 无 RTX 3090 系统，真实校准保持 `unknown`；
* 已确认的 RTX 3090 成本缺失，成本/报价领域不可用。

## 未实现

* C8 的统计显著生产级 SLA 概率（5 次观察不构成概率估计）；
* 服务端队列长度/调度延迟时间序列采集；
* 真实 GPU 多卡扩展与跨节点实验；
* 正式报价发布（仍受“未接可信审批系统”限制）。
