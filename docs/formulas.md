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

### 1.1 精确复算样例（`部署效能` 第 2 行）

输入：`I = 12478.922`，`O = 87.572`，`h = 0.9`，`p_hit = 2`，`p_miss = 20`，
`p_out = 100`，`C = 52.5`。

| 量 | 精确值 |
|---|---|
| `input_unit_price` | `0.9*2 + 0.1*20 = 3.8` |
| 输入收入 | `12478.922 * 31536000 * 3.8 / 1e6 / 1e4 = 149.54340799296` |
| 输出收入 | `87.572 * 31536000 * 100 / 1e6 / 1e4 = 27.61670592` |
| 合计 | `177.16011391296` |
| 营收/成本比 | `177.16011391296 / 52.5 = 3.374478360246857142857…` |
| 盈亏平衡变现比例 | `52.5 / 177.16011391296 = 0.2963420989…` |

Excel 表内 M 列存的是 14 位有效数字的浮点结果 `3.37447836024686`；
本引擎算出的是精确十进制值。二者差异 `≈2.9e-15`，属于 Excel 侧浮点舍入，
因此比较容差取 1e-6（金额）/ 1e-8（比值）而不是 0。

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
`"input_revenue_10k_per_gpu_year": "149.54340799296"`。

为什么改成字符串：JSON number 在几乎所有读取器里都会落成 IEEE-754 double，
`149.54340799296` 这类 14～17 位有效数字的值读回来就不再精确（更别说 28 位有效数字的
比率值）。字符串配上定点十进制字面量可以**位级精确**地恢复成 `Decimal`。

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

