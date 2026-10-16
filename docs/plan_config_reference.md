# `plan` 配置字段说明（S5）

统一评估入口只读**一份**配置：

```bash
python -m llm_capacity_pricing.cli plan --config <scenario.yaml> --out report.md \
  [--json-out result.json] [--db data/local.db]
```

> **只维护一份配置。** 同一件事不要在多个文件里各写一遍：模型、硬件、负载写在
> 本文件；成本与计费规则写在成本目录文件里（本文件只**引用**其路径与模板 ID）；
> 需求情景写在情景文件里（本文件只引用）。这样改动只有一处来源。

---

## 一、必需字段（最少三样）

| 字段 | 类型 | 说明 |
|---|---|---|
| `model_id` | str | 模型清单里的 ID，如 `qwen3-32b`（先 `import-models`） |
| `hardware_ids` | list[str] | 候选硬件 SKU，可多个，逐一出候选方案（先 `import-hardware`） |
| `workload.input_len` | int | 每请求目标输入长度（token） |

建议同时给出（不给会得到"信息不足"，不会自动补默认值）：

| 字段 | 说明 |
|---|---|
| `server_id` | 服务器形态 ID，如 `hgx-h100-8gpu` |
| `workload.output_len` / `workload.concurrency` | 输出长度与全局并发 |
| `resources.usable_fraction` + `resources.usable_source_kind` | 可用显存假设；**厂商标称容量不等于可分配显存** |

## 二、按场景选填的段落

删掉任一段落只影响对应领域（该领域标为"未配置"），其余照常出结论。

| 段落 | 作用 | 缺省行为 |
|---|---|---|
| `sla` | 性能与 SLA 评价（`requirement_id` + 时延上限等） | 领域标 `not_configured`，报告写"未配置需求" |
| `cost` | TCO（`plan_file` + `template_id`） | 领域标 `not_configured`，报告写"未配置" |
| `demand` | 业务收益情景（`demand_file` + `scenario_id`） | 不计算收入 |
| `budget` | 预算比对（`budget_id` / `amount` / `kind`） | 不比对预算 |
| `governance` | 报价资格引用（证据 ID / 审批 ID / 确认 ID / 计费规则 ID） | 领域标 `not_configured`，报告写"未评估" |

**治理段落是不放行开关**：不给引用就永远不产生"可发布"结论；
给了引用也必须通过服务端重放核验（见 `docs/phase4_interface_design.md` §3.3）。

## 三、`advanced`（专业字段，普通使用可忽略）

| 字段 | 默认 | 说明 |
|---|---|---|
| `tp_sizes` / `dp_sizes` | `[1,2,4,8]` | 枚举的并行组合 |
| `block_size_tokens` | 无 | Paged KV 块大小 |
| `weight_precision` / `kv_precision` | 模型声明 / `bf16` | 部署精度 |
| `model_revision` / `framework` / `framework_version` | 无 | 目标身份补充项；**要与已签发的 SLA 证据对齐时才填** |

> 目标侧的部署身份与负载身份必须齐备才可能达到"实测达标"：
> 除上面的补充项外，`workload` 里的 `dataset` 与 `arrival_mode` 也属于身份，
> 缺一项就只会得到"证据不足/参考级"结论 —— 这是刻意设计，不是缺陷。

## 四、示例

* 最小：`data/examples/scenario_minimal.example.yaml`（只有资源）
* 完整：`data/examples/scenario_full.example.yaml`（四个领域 + 预算 + 治理引用）

## 五、报告怎么读

| 章节 | 面向谁 | 内容 |
|---|---|---|
| 1. 结论摘要 | 所有人 | 四个领域状态 + 是否具备报价资格 |
| 2. 候选方案 | 所有人 | 卡数 / 并行组合 / 静态可行性 / **性能数据性质**（实测·估算·缺失）/ 成本与口径 / 预算 / 报价 |
| 3. 推荐依据 | 所有人 | 可解释的比较结论（**不做跨维度综合评分**） |
| 4. 缺口与下一步 | 所有人 | 阻断原因、缺少的输入、建议补什么数据 |
| 5. 结论边界 | 所有人 | 本次结论不能用来做什么 |
| 6. 附录 | 专业复核 | Run ID、匹配等级、证据明细、逐项成本分解 |

普通使用只需要看第 1–5 节；定位问题或复现计算时再看附录。
