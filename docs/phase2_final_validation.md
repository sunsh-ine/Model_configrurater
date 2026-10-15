# 第二阶段验证报告（M12–M18）

- 日期：2026-10-15
- 范围：模型与硬件资源选型引擎（显存估算 + TP/DP 静态候选）；**不进入第三阶段**
- 前置条件：第一阶段 M1–M11 与 V1.2 加固（F1–F8）已完成（基线 `5b5bbbf`）
- 完整执行日志：`docs/test_run_log.txt`（`bash scripts/verify_all.sh`，**27 步全绿，退出码 0**）
- 配套：`docs/phase2_interface_design.md`（M12 接口设计）

---

## 一、四分类结论

### 1.1 已通过（有实测输出）

| # | 验收项（任务书） | 实测证据 |
|---|---|---|
| 1 | M12 第一阶段无回归 | 32 条满载 / 160 敏感性 / `pricing_result 192` / 0 超容差 / fixture 残留 0；测试 **374 passed**（第一阶段 325 + 第二阶段 49） |
| 2 | M13 两个模型结构可追溯 | `已导入 2 个模型结构`；`qwen3-32b` 64 层 / 8 KV heads / head_dim 128 与官方 config 一致；revision 固定到 commit（`9216db57…`），config sha256 记录在案 |
| 3 | M13 不支持结构显式拒绝 | `model_type=llama` 的样例 → `unsupported_architecture`，且**不带**任何结构字段（有 Schema 守卫测试） |
| 4 | M13 权重元数据 | 索引解析出 `total_size` / 分片数 / 张量数与完整 `weight_map`（8B: 15.26 GiB / 5 分片 / 399 张量；32B: 61.02 GiB / 17 / 707） |
| 5 | M14 H100 SXM 与 H100 NVL 不混用 | 两个独立 SKU：80GB/3.35TB/s/SXM 与 94GB/3.9TB/s/PCIe，均来自同一官方规格表的两列 |
| 6 | M14 标称 ≠ 可分配 | 所有 SKU 的 `usable_memory_bytes=None` + `pending_measurement`；`allocatable_bytes()` 返回 `None` |
| 7 | M14 未确认条件不默认满足 | H100 + HGX 8 卡 → 兼容性 `unknown`，`unverified_conditions` 列出"可用显存未实测/精度支持未核实"等 |
| 8 | **M15 权重总量可复算** | 结构公式与官方权重索引 `metadata.total_size` **逐字节一致（差 0）**：8B `16,381,470,720 B`、32B `65,524,246,528 B` |
| 9 | M15 共享权重只算一次 | `tie_word_embeddings=true` 时总量恰好少一个 `vocab×hidden` |
| 10 | M15 TP 切分/复制/未确认三态 | 切分：q/k/v/o、ffn gate/up/down；复制：各 norm；**未确认**：embedding/lm_head（vocab 并行取决于框架），同时给出上下界 |
| 11 | M15 不谎称精确每卡显存 | 无张量元数据时 `per_rank_precision=upper_bound_estimate` + `per_rank_is_structural_estimate_not_tensor_layout` |
| 12 | **M16 KV 公式独立算例** | Qwen3-32B/TP8/BF16/8 并发/40960 ctx：`327,680 token × 64 层 × 512 B = 10,737,418,240 B`（= 10 GiB），与手算一致 |
| 13 | M16 Paged 块对齐 | 40961 token / block 16 → 2561 块，放大 `15 × 64 × 512 = 491,520 B`，与手算一致 |
| 14 | M16 KV 头切分与复制 | 可整除→`sharded` 并给出每 Rank 头数；< TP 或不可整除→`unknown` + `kv_heads_fewer_than_tp_requires_replication` |
| 15 | M16 未知运行时不为 0 | 默认四项运行时全部 `origin=unknown`/`bytes_estimate=None`；Schema 拒绝"unknown 却给了字节数" |
| 16 | M16 Sliding Window 显式降级 | `sliding_window` → `attention_kind=sliding_window`、`readable=False`、`physical_bytes=None` |
| 17 | M17 `gpu_count = tp × dp` | 对全部候选校验成立（Schema 守卫 + 测试） |
| 18 | M17 四类状态齐备 | `unsupported`（不支持结构）/ `infeasible`（TP 不能整除头数、显存不足）/ `insufficient_information`（无可用显存假设、运行时未知）/ `conditional_feasible`（有假设且放得下） |
| 19 | M17 DP 分摊并发不切权重 | 全局 32 并发 / DP4 → 每副本 8，KV 物理字节恰为 DP1 的 1/4 |
| 20 | M17 稳定排序 | 同输入两次生成候选列表完全一致（按状态→卡数→余量） |
| 21 | M18 CLI 离线可用 | 5 个新命令在 `verify_all.sh`（离线）中全部通过 |
| 22 | M18 报告含来源/假设/公式版本 | Markdown 含边界声明、来源表、待核实字段、逐候选依据、公式版本 `weights-1.0/kv-1.0/memory-1.0` |
| 23 | M18 错误输入明确失败 | 模型不存在 → 非零退出 + `模型不存在: nope`；哈希不符 → `ModelSpecError`；目录容量自相矛盾 → `HardwareCatalogError` |

### 1.2 已修复（本轮定位并修掉的问题）

| # | 问题 | 根因 | 修复 |
|---|---|---|---|
| 1 | **`if __name__ == "__main__"` 块位于文件中部**，导致 `python -m ...` 调用时后注册的命令全部不生效 | 该块是第一阶段遗留，正好夹在 `list-records` 与新增命令之间；解释器执行到它就 `app()` 退出 | 移到文件末尾；并用 `grep` 验证 `-m` 与 import 两种方式都注册 18 个命令 |
| 2 | **硬约束被显存规则覆盖**：TP 不能整除注意力头本应 `infeasible`，却因"显存放得下"变成 `conditional_feasible` | `_apply_memory_rules` 无条件重算状态，丢弃了前序 reasons | 加顺序纪律：已判 `infeasible`/`unsupported` 直接返回，显存规则只收敛其余情形（Fail-Closed） |
| 3 | **FFN 公式被部分权重命名误导**：只看到 `q_norm` 而没有 `up_proj` 时，认定该结构"无 gate"，少算一个 FFN 矩阵 | `has_gate = "gate_proj" in names or not names`——部分索引即误判 | 改为"看到 up_proj 却没有 gate_proj"才认定非 gated，否则按标准 gated 假定并加 note |
| 4 | 硬件规格页与第一阶段 benchmark 证据混放 `data/raw/official/`，触发"每个存档文件必须有条目"的守卫测试 | 目录语义不同（benchmark 来源 vs 厂商规格） | 新建 `data/raw/vendor/` + NOTICE，硬件目录引用同步更新（哈希校验仍通过） |
| 5 | `ffn` 组件在 TP 分布中显示 `unknown` | 分组名与策略键不对齐（`ffn` vs `ffn_gate/up/down`） | 重构为与真实张量命名一致的分组，策略逐组生效 |
| 6 | 未知运行时项在摘要里取了错的字段（`item.item`） | 迭代变量已解包 | 修正并保留 `name: description` 形式 |
| 7 | B200 预留条目缺少 `fields_pending_verification` | 建目录时遗漏 | 补齐 5 个待核实字段 |

### 1.3 未确认（保留空值/待确认，未自行假设）

| # | 事项 | 当前处理 |
|---|---|---|
| 1 | 单卡**实测可用显存**（H100 SXM / H200 SXM） | `usable_memory_bytes=None` + `pending_measurement`；不给实测就无法判定，只能返回 `insufficient_information` |
| 2 | 各 SKU 的**权重/KV 精度支持矩阵** | 规格页给的是算力精度，不等于权重存储精度 → `fields_pending_verification` |
| 3 | 框架内核支持、最大序列长度支持 | 同上，未核实 |
| 4 | embedding/lm_head 是否 vocab 并行 | 取决于框架实现 → TP 分布标 `unknown`，给出上下界 |
| 5 | KV 头在 TP 下的复制行为 | 同上 → `kv_head_distribution=unknown` + 说明 |
| 6 | 运行时开销（workspace / CUDA Graph / 激活峰值 / 框架常驻） | 默认 `unknown`；判定需要调用方显式给出预留（并标来源） |
| 7 | Ascend 910C / B200 规格 | 条目存在但**所有数值为 None**，`supported_in_phase2=false` |
| 8 | HGX H200 的 8 卡形态 | 存档页未直接声明 8 卡选项 → `pending_verification` + `fields_pending_verification` |
| 9 | 跨节点互联带宽 | 未建模；多节点时打 `requires_multi_node_interconnect_not_modelled` |

### 1.4 未实现（本阶段边界内不做）

| # | 项 | 说明 |
|---|---|---|
| 1 | TTFT/TPOT/ITL/E2E 性能预测 | 第二阶段明确不做 |
| 2 | 按峰值并发推断所需 GPU 数量 | 只做静态资源可行性，不做容量规划结论 |
| 3 | 跨 GPU/TP/DP 的吞吐线性外推 | 无任何此类路径 |
| 4 | PD 分离、MoE EP、MTP/Speculative 的资源优化 | 不支持的结构返回 `unsupported_architecture` |
| 5 | 未验证量化实现的精确显存核算 | 只支持 BF16/FP16 等已知每元素字节数的精度；`int4` 等打包细节未实现 |
| 6 | 静态可行 → 正式报价 | 完全不做（第一阶段也不提供报价能力） |
| 7 | 启动成功性保证 | 任何输出都不代表能启动（报告与 JSON 均有边界声明） |

---

## 二、核心数值核验（M15/M16 为验收重点）

### 2.1 权重显存：公式 vs 官方权重索引

| 模型 | 结构公式参数量 | BF16 总量 | 官方索引 `total_size` | 差值 |
|---|---|---|---|---|
| Qwen3-8B | 8,190,735,360 | 16,381,470,720 B | 16,381,470,720 B | **0** |
| Qwen3-32B | 32,762,123,264 | 65,524,246,528 B | 65,524,246,528 B | **0** |

| 模型 | TP1 每卡上界 | TP2 | TP4 | TP8 |
|---|---|---|---|---|
| Qwen3-8B | 15.26 GiB | [7.63, 8.79] | [3.81, 5.55] | [1.91, 3.94] |
| Qwen3-32B | 61.02 GiB | [30.51, 31.96] | [15.26, 17.43] | [7.63, 10.16] |

（区间来自 embedding/lm_head 的 vocab 并行不确定性：下界=等分，上界=复制。）

### 2.2 KV Cache：独立算例

| 场景 | 手算 | 引擎 |
|---|---|---|
| 32B / TP8 / BF16 / 8 并发 / 40960 ctx / block16 | 8×40960 = 327,680 token；×64 层 ×512 B = **10,737,418,240 B**（10 GiB）；40960 整除 16 → 放大 0 | 完全一致 |
| 32B / TP8 / 1 并发 / 40961 ctx | ceil(40961/16)=2561 块 → 放大 15 token ×64 层 ×512 B = **491,520 B** | 完全一致 |
| MHA/GQA/MQA 对比（tiny 模型） | KV 字节比 4:2:1 | 完全一致 |
| 小模型（L2/h64/heads4/kv2/hd16/ffn128/vocab1000） | 权重 202,048 参数 = 404,096 B；KV(128 token) = 65,536 B | 完全一致 |

### 2.3 资源规划：Qwen3-32B × H100 SXM × BF16

请求：ctx 8192、全局并发 32、block 16；假设可用显存 76 GiB、运行时合并预留 10%。

| TP×DP | 卡数 | 权重/卡 | KV/卡 | 合计/卡 | 余量 | 状态 |
|---|---|---|---|---|---|---|
| 1×1 | 1 | 61.02 GiB | 64.00 GiB | 132.62 GiB | −56.62 GiB | `infeasible` |
| 1×2 | 2 | 61.02 GiB | 32.00 GiB | 100.62 GiB | −24.62 GiB | `infeasible` |
| 2×1 | 2 | 31.96 GiB | 32.00 GiB | 71.56 GiB | 4.44 GiB | `conditional_feasible` |
| 4×1 | 4 | 17.43 GiB | 16.00 GiB | 41.03 GiB | 34.97 GiB | `conditional_feasible` |
| 8×1 | 8 | 10.16 GiB | 8.00 GiB | 25.76 GiB | 50.24 GiB | `conditional_feasible` |
| 8×2 | 16 | 10.16 GiB | 4.00 GiB | 21.76 GiB | 54.24 GiB | `conditional_feasible` |

结论口径：**TP1 在 80GB 卡上装不下**（即使不算运行时开销），TP≥2 在给定假设下静态可行；
但报告同时声明这些数字依赖"可用显存 76 GiB + 运行时预留 10%"两项**假设**，
落地前必须实测复核。

---

## 三、验证命令与实测结果

```bash
bash scripts/verify_all.sh      # 退出码 0；步骤 27 项：通过 27，失败 0
.venv/bin/python -m pytest -q   # 374 passed
```

| 步骤 | 实测输出 |
|---|---|
| 19 | `已导入 2 个模型结构（精度 bf16）` / `解析成功: 2；不支持结构: 0` |
| 20 | `已导入 5 个 GPU SKU、2 个服务器形态` / `本轮支持: [h100-sxm-80gb, h200-sxm-141gb]` / `待核实: [ascend-910c, b200-sxm]` |
| 21 | `"model_count": 2` / `"hardware_count": 5` / `"server_count": 2` |
| 22 | `状态分布: {'conditional_feasible': 6, 'infeasible': 2}`；JSON 20,471 B + Markdown 16,599 B |
| 23 | 单配置估算输出权重/卡、KV/卡、运行时常量、余量与未知项清单 |
| 24 | `已跟踪文件中未发现内部定价数值`（V1.2 门禁仍生效） |
| 25 | `374 passed in 60.54s` |
| — | `步骤 27 项：通过 27，失败 0` |

**第一阶段回归**：`结果条数: 32`、`默认敏感性情景条数: 160`、`最终 pricing_result 总数: 192`、
`残留的 synthetic_fixture 结果行: 0` —— 全部不变。

---

## 四、是否达到第二阶段完成判定

任务书「完成判定」：*能对 Qwen3-32B + H100 SXM + BF16 的不同 TP/DP 部署组合提供可信的静态资源规划结果，
并清楚区分理论计算、明确假设和仍需要实际测试的部分。*

| 判定项 | 结论 |
|---|---|
| 给出不同 TP/DP 组合的静态资源规划结果 | **是**（§2.3，8 个候选，逐项权重/KV/余量） |
| 区分理论计算 | **是**（权重公式与官方权重逐字节一致；KV 有独立手算算例） |
| 区分明确假设 | **是**（可用显存、运行时预留均标注来源；`conditional_feasible` 必带假设） |
| 区分仍需实测的部分 | **是**（`unverified_conditions` / `fields_pending_verification` / `insufficient_information`） |
| 不越界 | **是**（无性能预测、无 SLA 结论、无报价、无跨配置外推） |
| 第一阶段不回归 | **是**（32/160/192 不变，测试 325→374 只增不减） |

**结论：第二阶段（M12–M18）完成，可交付。** 阻塞项：无。

主要待办（属第三阶段或需现场数据）：

1. **实测各 SKU 的可用显存**（`nvidia-smi`），把 `usable_memory_status` 从 `pending_measurement` 变为 `measured`；
   这是从"条件可行"走向"可复现结论"的唯一关键输入。
2. 核实各 SKU 的**权重/KV 精度支持**与**框架内核支持**（当前为待核实）。
3. 若需支持 MoE / PD 分离 / 量化显存，需先补齐对应结构与公式（返回 `unsupported_architecture` 的路径已就绪）。
4. 第三阶段可在此基础上接入 Benchmark 与仿真数据，筛选满足 TTFT/TPOT/吞吐/并发要求的方案。
