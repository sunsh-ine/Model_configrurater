# 计算公式说明（formulas）

对应任务书 §4。所有计算使用 `decimal.Decimal`，**禁止把浮点累计误差带入金额结算**。

## 0. 常量

| 常量 | 值 | 说明 |
|---|---|---|
| `SECONDS_PER_YEAR` | `365*24*3600 = 31_536_000` | 默认年度秒数。**不得**用"扣除维护日后的时间"替代 |
| `TOKEN_PRICE_UNIT` | `1_000_000` | Token 单价的分母（每百万 Token） |
| `TEN_THOUSAND` | `10_000` | 元 → 万元 |

年份口径可通过 `--year-seconds` 显式覆盖；改小它会直接破坏与旧表的等价性
（测试 `test_maintenance_adjusted_year_is_not_applied` 专门验证了这一点）。

---

## 1. 旧表复算（`legacy_full_load`）

设：`I` = 每卡输入吞吐（tok/s/卡），`O` = 每卡输出吞吐（tok/s/卡），
`h` = H 列命中率（**仅作为旧表的定价代理假设**），
`p_hit` / `p_miss` / `p_out` = 百万 Token 参考单价，`C` = 每卡年成本（万元）。

```text
input_unit_price = h * p_hit + (1 - h) * p_miss

input_revenue_10k_per_gpu_year
  = I * 31536000 * input_unit_price / 1_000_000 / 10_000

output_revenue_10k_per_gpu_year
  = O * 31536000 * p_out / 1_000_000 / 10_000

total_revenue_10k_per_gpu_year
  = input_revenue_10k_per_gpu_year + output_revenue_10k_per_gpu_year

revenue_cost_ratio = total_revenue_10k_per_gpu_year / C
```

实现位置：`engines/pricing.py` 中
`legacy_input_annual_revenue_10k_per_gpu`、`legacy_output_annual_revenue_10k_per_gpu`、
`total_annual_revenue_10k_per_gpu`、`revenue_cost_ratio`。

### 1.1 精确复算样例（**脱敏公开样本**）

> 公开仓库**不包含**真实业务价格。下面用的是 `data/examples/` 里的合成样本
> （`EXAMPLE-GPU-A` / `EXAMPLE-MODEL-A`），期望值由 `scripts/make_public_sample.py`
> 独立手算后写入 `data/examples/public_sample_expected.json`，可直接复跑核对。
> 真实工作簿第 2 行的对应期望值属内部业务信息，保留在本地 `data/private/`（不入库）。

输入：`I = 1000`，`O = 40`，`h = 0.5`，`p_hit = 1`，`p_miss = 10`，
`p_out = 50`，`C = 10`。

| 量 | 精确值 |
|---|---|
| `input_unit_price` | `0.5*1 + 0.5*10 = 5.5` |
| 输入收入 | `1000 * 31536000 * 5.5 / 1e6 / 1e4 = 17.3448` |
| 输出收入 | `40 * 31536000 * 50 / 1e6 / 1e4 = 6.3072` |
| 合计 | `23.6520` |
| 营收/成本比 | `23.6520 / 10 = 2.3652` |
| 盈亏平衡变现比例 | `10 / 23.6520 = 0.4227972264501944867241670895…` |

对**真实工作簿**的复算仍按同一公式执行（本地跑，不入库）：Excel 表内 M 列存的是
14 位有效数字的**浮点**结果，本引擎算的是精确十进制值，二者差异量级 `1e-15`，
属于 Excel 侧浮点舍入，因此比较容差取 1e-6（金额）/ 1e-8（比值）而不是 0。
32 行全量复算的**超容差不匹配数为 0**，最大误差量级见 `docs/validation_report.md`。

---

## 2. 情景模式（`scenario`，仅敏感性分析）

```text
scenario_revenue = full_load_revenue * U          # U ∈ [0, 1]
scenario_surplus_before_other_costs = scenario_revenue - annual_gpu_cost
breakeven_monetization_ratio = annual_gpu_cost / full_load_revenue
```

- `U` 为**有效产能变现比例**，是显式的敏感性参数，不是实际变现率。
- `breakeven_monetization_ratio > 1` → 即使满载也无法覆盖当前表内 GPU 成本，
  引擎会打 `breakeven_utilization_above_1` 警告。
- 可用率 `A`、可计费比例 `S` 初版**留空**。若显式启用，则与 `U` **相乘一次**
  （`U * A * S`），并在 `warnings` 中声明"已与 U 相乘一次，调用方不得重复相乘"。
  这样避免同一因子被重复计入。

---

## 3. 集群量还原

```text
total_cluster_cost_10k_year    = gpu_annual_cost_10k * deployment_gpu_count
total_cluster_revenue_10k_year = total_revenue_10k_per_gpu_year * deployment_gpu_count
scenario_cluster_revenue_10k_year = scenario_revenue_10k_per_gpu_year * deployment_gpu_count
```

`input_tps_cluster = input_tps_per_gpu * deployment_gpu_count` **仅用于同一 Benchmark
配置的归一化还原**，结果必须带 `derived_from_per_gpu` 依据说明，且：

- **绝不**用于估算扩卡后的吞吐；
- **绝不**用于估算改 TP / 改 EP / 改 P-D 拓扑后的吞吐；
- 修改 `deployment_gpu_count` 后不会自动重算集群吞吐（
  测试 `test_cluster_throughput_not_recomputed_for_other_gpu_count` 固定该行为）。

---

## 4. 除零与不可计算状态

| 场景 | 行为 |
|---|---|
| 成本 = 0，求营收/成本比 | 抛 `NotComputableError`，结果 `status="not_evaluable"` |
| 满载收入 = 0，求盈亏平衡比例 | 抛 `NotComputableError`，`breakeven_monetization_ratio=None` |
| 缺少 Token 价格 / 吞吐 / 命中率 | `status="not_evaluable"`，`not_evaluable_reason` 列出缺失项 |
| 缺少 GPU 成本（但有收入） | `status="partial"`，收入可算、比率留空 |

**任何情况下都不返回 `inf`，也不静默填 0。**

`U` 不在 `[0, 1]` 时抛 `InvalidUtilizationError`。

---

## 5. 命名与边界

- 成本边界命名固定为 `cost_scope = gpu_cost_only`。本系统没有完整 TCO，
  因此**不得**把结果称为 `total_cost_of_ownership`。
- 结果始终携带三类边界声明：`cost_scope=gpu_cost_only`、`not_customer_quote`、
  `sla_commitment_not_provided`。
- 缺少实测 P99 TTFT/TPOT 时一律标记 `ttft_p99_not_evaluable` /
  `tpot_p99_not_evaluable`；**不得**用平均吞吐、单用户 P90 tok/s 或其它型号 GPU 的
  结果反推 P99。
- 单用户输出 P90 吞吐（E 列）与 TPOT 是不同指标，引擎会显式提示
  `single_user_output_tps_p90_kept_separate`。

---

## 6. 可溯源与可复算

每次计算都会：

1. 把参与计算的输入拍平成规范 JSON（`input_snapshot_hash`，sha256）；
2. 在 `steps` 中记录每一步的代入值与结果；
3. 记录 `pricing_engine_version`、`calculation_year_seconds`、`price_unit`、
   `billing_period`、`currency_assumption`、`cost_scope`。

因此同一输入快照 + 同一引擎版本必然得到同一结果（测试
`test_row2_snapshot_hash_is_stable` 固定该性质）。

## 7. JSON 导出精度（验收整改任务 2）

**金额与比率字段以"可精确恢复的十进制字符串"导出**，例如
`"input_revenue_10k_per_gpu_year": "17.3448"`。

为什么改成字符串：JSON number 在几乎所有读取器里都会落成 IEEE-754 double，
以内部真实数据为例，金额与比率常带 14～28 位有效数字，这类值经 JSON number
往返就不再精确。字符串配上定点十进制字面量可以**位级精确**地恢复成 `Decimal`。

编码规则（`reporting/json_export.py`）：

1. 先按字段白名单（`DECIMAL_FIELD_NAMES`，约 40 个金额/比率字段名）把 `Decimal`
   格式化成不带科学计数法的定点字面量（`format(value, "f")`）；
2. 其余非金额字段保持 JSON 原生类型（如 `framework_version: "1.0"` 这类字符串
   **不会**被误转成 Decimal，有专门测试固定）；
3. 导出文件头部写 `decimals_encoding` 声明块，说明编码方式、样例与兼容读取入口。

兼容读取：`json_export.loads_tolerant(text, restore_keys=None)`——按字段名白名单把
字符串还原为 `Decimal`，并支持 `restore_keys` 自定义白名单。这样既保住精度，
又不要求调用方理解内部约定。

**计算路径不受影响**：公式层仍然全程 `Decimal`，并且所有公式函数入口都有
`_reject_float` 守卫，传入 `float` 会直接抛 `TypeError`
（`engines/pricing.py` 的 `is_decimal_path`）。JSON 只是**编码边界**，不是计算边界。

导出文件同时显式声明四件套：`currency`（未确认时 `"UNCONFIRMED"`）、
`currency_assumption`、`cost_scope`、`price_unit` / `billing_period`，以及结果类型
`result_type`（`theoretical_full_load_potential` 或 `scenario_sensitivity_estimate`）。

校验见 `tests/test_json_precision.py`（22 个用例）：`test_money_is_encoded_as_string_not_number`、
`test_high_precision_value_survives_string_encoding`、
`test_no_scientific_notation_for_small_and_large_values`、
`test_version_like_strings_are_not_mistaken_for_money`、
`test_pricing_result_high_precision_roundtrip_through_json`、
`test_tolerant_reader_accepts_legacy_json_number_without_float_loss`、
`test_formulas_stay_on_decimal_path` 等。


---

# 第二阶段公式（M15/M16/M17）

> 与第一阶段的分工：这里只算**显存与静态资源**，不含任何 Token 定价公式。
> 公式版本：`weights-1.0` / `kv-1.0` / `memory-1.0`。

## 8. 权重显存（M15）

逐组件累加（Dense decoder + GQA；组件名与真实权重张量命名一一对应）：

```text
embedding  = vocab × hidden
lm_head    = 0 if tie_word_embeddings else vocab × hidden       # 共享权重只算一次
每层 attention = hidden × (heads×head_dim)      # q_proj
               + 2 × hidden × (kv_heads×head_dim)  # k_proj, v_proj
               + (heads×head_dim) × hidden      # o_proj
               + (2 × head_dim if 存在 q_norm/k_norm)   # Qwen3 特有
每层 norm  = 2 × hidden                          # input/post_attention layernorm
final_norm = hidden
每层 ffn   = (2 × hidden × ffn if gated else hidden × ffn) + ffn × hidden   # gate/up + down
总字节数   = 参数量 × bytes_per_element(precision)
```

**TP 分布**（`DEFAULT_TP_POLICY`）：注意力投影与 FFN 三矩阵 `sharded`；
各 norm `replicated`；`embedding`/`lm_head` 标 `unknown`（是否 vocab 并行取决于框架），
因此给出**下界（等分）与上界（复制）**两个每卡值。

**一致性检查**：总字节数与权重索引 `metadata.total_size` 比较，差异必须解释。
实测：Qwen3-8B / Qwen3-32B 均为**差 0 字节**。

**精度纪律**：没有张量级元数据时 `per_rank_precision = upper_bound_estimate`，
不得声称精确每卡显存。

## 9. KV Cache（M16）

```text
每 token 每层 KV 字节 = 2（K 与 V） × kv_heads_per_rank × head_dim × bytes_per_element(kv_precision)
每副本 KV 字节      = (并发/副本 × 上下文长度) × 层数 × 上值      # 未分页（连续）布局
Paged KV：
    每请求块数 = ceil(上下文长度 / block_size_tokens)
    总块数     = 每请求块数 × 并发/副本
    物理字节   = 总块数 × block_size_tokens × 层数 × 上值
    对齐放大   = 物理字节 − 未对齐字节
```

* MHA / GQA / MQA 共用同一公式，差异只在 `kv_heads`。
* `sliding_window` 或未支持结构 **不套用** 本公式，直接 `readable=False`。
* `kv_heads % tp != 0` 或 `kv_heads < tp` 时，KV 头在 Rank 间的分布取决于框架实现
  → `kv_head_distribution=unknown` 并说明可能的复制。
* **DP 分摊并发**（`每副本并发 = ceil(全局并发 / dp)`），**不**切分单条请求的权重。

独立算例（Qwen3-32B / TP8 / BF16 / 8 并发 / 40960 ctx）：
`327,680 × 64 × 512 = 10,737,418,240 B`（10 GiB），与手算一致。

## 10. 显存合成与可行性（M17）

```text
单卡需求 = 每卡权重 + 每卡 KV + 已知运行时项
预算     = 实测可用显存（优先）或调用方给出的带来源假设
余量     = 预算 − 单卡需求
```

判定（顺序即优先级）：

| 顺序 | 条件 | 状态 |
|---|---|---|
| 1 | 模型结构未核实 | `unsupported` |
| 2 | 硬件不支持 / 注意力头不能被 TP 整除 / 运行时允许用量超出预算 | `infeasible` |
| 3 | 可用显存未知且无假设；或存在未知运行时项且无预留假设 | `insufficient_information` |
| 4 | 其余（有假设且放得下） | `conditional_feasible`（必须列假设） |

不变的边界：静态可行**不代表**能启动，**不代表**满足 SLA；不产出报价。
