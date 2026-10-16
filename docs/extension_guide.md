# 扩展开发指南（S4：最小可扩展边界）

本阶段只保留**三个**扩展方向，每个方向都给了明确入口与验收方式。
除此之外**不建**抽象基类、空接口、动态注册框架或通用规则引擎。

原则：**局部新增，不改主流程**。
判断标准不是"文件改得少"，而是"下一次加一张卡/一种格式/一条计费规则时，
不需要动业务编排"。

---

## 一、数据扩展：新增模型 / GPU / 成本数据

**能扩什么**：模型结构、GPU SKU、服务器形态、成本目录项、计费规则。

**怎么做**：只改数据文件，不改代码。

| 数据 | 文件 | 说明 |
|---|---|---|
| 模型结构 | `data/models/model_manifest.yaml` + 归档的 `config.json` / 权重索引 | 哈希不符会 Fail-Closed |
| GPU SKU / 服务器 | `data/hardware/hardware_catalog.yaml` | 数值必须有来源页面（含 sha256） |
| 成本目录 / 计费规则 | 形如 `data/examples/phase4_cost_inputs.example.yaml` | `status` 与币种状态必须如实标注 |

```bash
python -m llm_capacity_pricing.cli import-models   --manifest data/models/model_manifest.yaml --db data/local.db --root .
python -m llm_capacity_pricing.cli import-hardware --catalog  data/hardware/hardware_catalog.yaml --db data/local.db
python -m llm_capacity_pricing.cli import-cost-catalog --file <catalog.yaml> --db data/local.db
```

**唯一需要同步的一处代码**：如果新增的 GPU 需要参与第三阶段签名匹配，
还要在 `sizing/hardware.py` 的 `SIGNATURE_PRODUCT_NAMES` 里补
「硬件 ID → 商品名」一行。这是**唯一**的硬件命名权威，
有契约测试比对 `hardware_catalog.yaml` 的 `product_name`，漏改会直接失败。

**不需要改**：`workflow.py`、任何 CLI 命令、匹配/SLA/TCO 引擎。

---

## 二、Benchmark 适配扩展：新增压测框架或结果格式

**能扩什么**：新的压测工具输出格式（例如另一个框架的 JSON/JSONL）。

**怎么做**：新增一个适配器模块 + 在注册表加一行。

适配器契约（与现有 `vllm_adapter` / `sglang_adapter` 完全一致）：

```python
def parse_xxx_full(
    file: str | Path, metadata: dict[str, Any]
) -> tuple[list[BenchmarkRecord], list[Evidence]]: ...
```

* `BenchmarkRecord` 只承载**解析到的事实**：缺失字段留 `None`，不猜、不换算；
* 单位口径必须显式（时延 ms、时长 s、吞吐按元数据归一化到每卡）；
* 证据要能定位到文件/行号。

注册（`services.py`）：

```python
PERFORMANCE_ADAPTERS = {
    "vllm": (...),
    "sglang": (...),
    "xxx": ("llm_capacity_pricing.importers.xxx_adapter", "parse_xxx_full"),
}
```

之后 `run_from_adapter()` 会自动把它转成统一的 `BenchmarkRun` / `PerformanceProfile`。

**不需要改**：`perf/benchmark_matcher.py`（匹配分级）、`perf/sla_evaluator.py`
（SLA 门禁）、`engines/tco.py`（成本）。它们只认统一后的对象。

**验收**：`tests/test_s_phase_extension.py::test_s4_new_benchmark_adapter_plugs_in_without_touching_domains`
—— 测试内现场写一个迷你适配器注册进去，走完导入 → 匹配 → SLA 全链路。

---

## 三、计算规则扩展：新增或替换算法实现

**能扩什么**：某个领域内的计算方法（例如新的插值算法、新的容量口径）。

**怎么做**：在**所属领域内**新增一个**版本化**的实现，并在结果里记录版本标识；
**只有出现第二种真实实现时才抽接口**，不提前建插件系统。

约定：

* 计算函数保持纯函数（输入 → 输出），不读数据库、不写文件；
* 结果对象带上版本/口径标识，便于历史结论可复现；
* 新增实现不得改变既有实现的行为（旧结论必须仍可重算）。

**当前已有的版本化口径示例**：

| 口径 | 位置 |
|---|---|
| 定价引擎版本 | `schemas/pricing.PRICING_ENGINE_VERSION` |
| 估算器判定阈值与策略 | `perf/estimator.py`（`MIN_POINTS_FOR_CALIBRATION` 等） |
| 成本覆盖策略 | `schemas/tco.CostCoveragePolicy`（`policy_id` + `version` + `authority`） |
| SLA 策略 | `schemas/performance.SlaPolicy`（`policy_id`） |

---

## 四、不要做的事

* 不要新增微服务、消息队列、缓存中间件；
* 不要建抽象基类 + 动态注册的插件框架；
* 不要建通用规则引擎/DSL；
* 不要为了让"扩展点更漂亮"而把简单逻辑拆成多层间接；
* 不要在编排层复制业务判断 —— 规则一律回到它的权威领域（见
  `docs/simple_architecture_validation.md` §五）。

---

## 五、新增能力时的检查清单

1. 这个能力属于哪个领域？（Sizing / Performance / Economics / Governance）
2. 它是**数据**、**适配**还是**算法**？—— 三者各有固定入口，见上。
3. 是否需要动 `workflow.py`？如果答案是"是"，先确认它不是纯编排问题。
4. 业务规则是否只有一处实现？改完后跑
   `pytest tests/test_s_phase_rules.py` 确认单一权威没被破坏。
5. 跑全量回归：`pytest -q` + `bash scripts/verify_all.sh`
   （基线见 `docs/refactor_baseline.md`），并确认第一阶段 160 / 192 / 0 不变。
