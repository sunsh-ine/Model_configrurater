# 第二阶段接口设计与数据模型（M12 交付物）

- 日期：2026-10-15
- 基线：第一阶段（M1–M11）+ V1.2 加固（F1–F8）已完成，稳定提交 `5b5bbbf`
- 本文档定义第二阶段的模块边界、六类核心数据结构与依赖关系；实现见 `src/llm_capacity_pricing/sizing/`

---

## 1. 与第一阶段的关系（不动的部分）

| 项目 | 处理 |
|---|---|
| Token 定价引擎（`engines/pricing.py`、`engines/calculator.py`、`schemas/pricing.py`） | **完全不改**：第二阶段不写任何定价公式，`calculate-all` / `scenario` / `report` 行为不变 |
| Excel 导入与 32 条历史复算 | 不改；回归基线保持（32 / 160 / 192、0 超容差） |
| 证据模型 `Evidence` | **复用**：模型配置与厂商规格页都建 Evidence 记录（`source_type=third_party` + 厂商官方标记） |
| 存储 / CLI / 报告骨架 | 复用同一套 `Database` + `Repository` + Typer CLI + JSON/Markdown 导出风格 |
| 门禁思想（Fail-Closed、未知≠0、缺失留空） | 直接继承到第二阶段的显存与可行性判定 |

**新增模块**（互不侵入）：

```text
src/llm_capacity_pricing/
  schemas/sizing.py          # 六类结构定义（本文档 §2）
  sizing/
    model_spec.py            # M13 模型结构解析（HF config.json + 权重索引）
    hardware.py              # M14 GPU SKU / 服务器形态 / 兼容性
    weights.py               # M15 权重显存（分解 + TP 分布 + 一致性检查）
    kv_cache.py              # M16 KV Cache + 运行时预留项
    planner.py               # M17 显存合成 + TP/DP 候选 + 可行性
  reporting/sizing_report.py # M18 Markdown 报告
data/
  models/                    # 归档的官方 config.json / index.json + 清单
  hardware/hardware_catalog.yaml   # GPU 规格库（带来源与待核实字段）
  raw/vendor/                # 厂商规格页存档（与第一阶段 benchmark 证据分开）
```

---

## 2. 六类核心数据结构

### 2.1 `ModelSpec`（M13）

模型**结构事实**，全部来自官方 `config.json` / 权重索引。

| 分组 | 字段 | 说明 |
|---|---|---|
| 溯源 | `model_id` `repo_id` `revision` `config_file` `config_sha256` `retrieved_at` `trust_level` | revision 固定到 commit |
| 结构 | `num_hidden_layers` `hidden_size` `num_attention_heads` `num_key_value_heads` `head_dim` `intermediate_size` `vocab_size` `max_position_embeddings` | 缺失即 `None` |
| 结构细节 | `tie_word_embeddings` `attention_bias` `hidden_act` `rms_norm_eps` `rope_theta` `rope_scaling` `sliding_window` `use_sliding_window` `max_window_layers` | 影响权重与 KV 公式 |
| 精度 | `weight_dtype_declared`（权重文件声明） / `deployment_precision`（本次部署请求） | **两者分离** |
| 权重元数据 | `ModelWeightMetadata`：`total_weight_bytes` `shard_count` `tensor_count` `weight_map` `tensor_metadata_available` | 来自 index.json |
| 状态 | `status`（`parsed` / `partially_parsed` / `unsupported_architecture`）、`missing_fields`、`quality_flags` | 不支持的结构**不得**带结构字段 |

派生量：`attention_kind`（mha/gqa/mqa/sliding_window/unsupported）、`supports_memory_estimate`。

### 2.2 `HardwareSpec` / `ServerProfile` / `HardwareCompatibility`（M14）

| 对象 | 关键字段 | 纪律 |
|---|---|---|
| `HardwareSpec` | `hardware_id` `product_name` `sku` `form_factor` `hbm_type` `hbm_capacity_declared` `capacity_normalization` `total_memory_bytes` `usable_memory_bytes` `memory_bandwidth_bytes_per_s` `interconnect_within_node` `source_id` `source_quote` `verification_status` `supported_in_phase2` `fields_pending_verification` | 标称 ≠ 可分配；`usable_memory_bytes` 只认实测 |
| `ServerProfile` | `gpus_per_node` `interconnect_declared` `cpu_model` `host_memory_bytes` `nic_bandwidth_bytes_per_s` `verification_status` | 未给出的保持 `None` |
| `HardwareCompatibility` | `status`（compatible/incompatible/**unknown**）`reasons` `unverified_conditions` | 未确认条件不得当作已满足 |

### 2.3 `WeightMemoryEstimate`（M15）

`breakdown`（embedding/attention/ffn/norm/lm_head）、`total_weight_bytes`、
`per_rank_bytes` + `per_rank_bytes_lower_bound/upper_bound` + `uncertain_components`、
`distribution`（组件 → sharded/replicated/unknown）、`sharded_components`、`replicated_components`、
`per_rank_precision`（`exact` / `upper_bound_estimate` / `unavailable`）、
`index_total_bytes` + `consistency_delta_bytes` + `consistency_ratio` + `consistency_note`、
`basis`（公式代入值）、`formula_version`。

### 2.4 `KVCacheEstimate` / `MemoryEstimate`（M16）

| 对象 | 关键字段 |
|---|---|
| `KVCacheEstimate` | `attention_kind` `kv_precision` `num_kv_heads_total/per_rank` `head_dim` `global_concurrency` `concurrency_per_replica` `context_length_per_request` `logical_token_capacity` `block_size_tokens` `allocated_blocks` `physical_bytes` `unaligned_bytes` `alignment_overhead_bytes` `kv_head_distribution` `readable` `degradation_notes` |
| `RuntimeReserve` | `item` `bytes_estimate` `origin`（measured/vendor_documented/**assumed**/**unknown**）`source` `assumption` |
| `MemoryEstimate` | `weight` + `kv` + `runtime_items` + `per_gpu_usable_bytes` + `total_required_bytes` + `headroom_bytes` + `known_unknowns` |

不变式：`origin=unknown` ⇒ `bytes_estimate=None`（未知不是 0）；`origin=assumed` ⇒ 必须写 `assumption`/`source`。

### 2.5 `DeploymentCandidate` / `ResourceFeasibilityResult`（M17）

`DeploymentCandidate`：`candidate_id` `tp_size` `dp_size` `gpu_count`（**校验 = tp×dp**）`nodes_required`
`context_length_per_request` `global_concurrency` `concurrency_per_replica` `memory` `feasibility`
`total_gpu_count` `headroom_bytes` `summary`。

四类状态与判定顺序（**先硬约束，后资源**）：

```text
unsupported             模型结构或部署方式未实现（不产出显存结果）
infeasible              已确认违反约束：硬件不支持 / TP 无法整除注意力头 / 显存放不下
insufficient_information 数据不足：无实测可用显存且未给假设；或运行时有未知项且无预留假设
conditional_feasible    在明确假设下静态资源可能足够（必须列出假设）
```

---

## 3. 依赖关系（数据流）

```text
官方 config.json ──┐
权重索引 index.json ─┴─► ModelSpec ─┐
                                   ├─► WeightMemoryEstimate ─┐
厂商规格页 ─► hardware_catalog ─► HardwareSpec ─┐            ├─► MemoryEstimate ─► DeploymentCandidate
                                                │            │        ▲
负载假设（ctx/并发/块大小）──────────────────────┴─► KVCacheEstimate ─┘
运行时假设（预留比例/分项）────────────────────────► RuntimeReserve ─┘
可用显存（实测或假设）────────────────────────────► resolve_usable_memory ─┘
```

**离线保证**：所有外部输入在采集阶段归档（`data/models/`、`data/raw/vendor/`），
解析与计算只读本地文件，`pytest` 与 `verify_all.sh` 全程不联网。

---

## 4. 模块接口（对外函数）

| 模块 | 函数 | 说明 |
|---|---|---|
| `sizing.model_spec` | `load_model_specs(manifest, root, deployment_precision)` | 解析清单全部模型（哈希 Fail-Closed） |
| `sizing.hardware` | `load_hardware(catalog)` / `check_hardware_compatibility(...)` | 硬件库与兼容性 |
| `sizing.weights` | `estimate_weight_memory(spec, tp_size, precision)` | 权重显存 |
| `sizing.kv_cache` | `estimate_kv_cache(spec, tp_size, dp_size, ...)` / `default_runtime_items(...)` | KV 与运行时 |
| `sizing.planner` | `estimate_memory(...)` / `plan_deployment(...)` / `resolve_usable_memory(...)` | 显存合成与候选规划 |
| `services` | `import_models` `import_hardware` `sizing_inventory` `estimate_memory_for` `plan_deployment_for` `export_plan` | CLI 与测试共用 |
| `reporting.sizing_report` | `build_sizing_report(...)` | Markdown 报告 |

CLI：`import-models` / `import-hardware` / `sizing-inventory` / `estimate-memory` / `plan-deployment`。

---

## 5. 本轮明确不做（接口预留但不实现）

| 能力 | 预留方式 |
|---|---|
| Ascend 910C | `hardware_catalog.yaml` 已有条目，字段为 `None` + `supported_in_phase2: false`；接入时只需补规格与框架并行实现证据 |
| B200 | 同上（本轮未采集官方规格页） |
| MoE / PD 分离 / 量化显存 / SLA 预测 | 不在第二阶段边界内；`unsupported_architecture` 已能拒绝未核实结构 |
| 跨节点互联建模 | `nodes_required` 已计算，但跨节点带宽不建模，且会打 `requires_multi_node_interconnect_not_modelled` |
