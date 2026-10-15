"""M8：SGLang / vLLM Benchmark 指标字典与口径校验。

这份模块是 ``docs/benchmark_metric_dictionary.md`` 的**代码侧真值**：文档由它生成口径，
适配器与候选记录用它打 ``metric_definition_id``，校验逻辑用它判定"有没有偷换指标"。

三条硬规则（对应任务书 §4）：

1. **配置值 ≠ 实测值**：``configured_max_concurrency``（命令行上限）与
   ``observed_concurrency``（全请求累计耗时 / 墙钟时间）必须并存，不得互相覆盖。
2. **目标长度 ≠ 实际长度**：``input_len_target`` / ``output_len_target`` 是 Benchmark
   的**目标参数**，不是逐请求实际 token 数；实际累计量只有
   ``total_input_tokens`` / ``total_output_tokens``。
3. **集群吞吐 ≠ 单卡能力**：``input/output_throughput_tok_s`` 是**整套参与测试 GPU**
   的吞吐；每卡归一化值只能在同一原始部署配置下用"集群吞吐 ÷ GPU 数"得到，
   禁止跨 TP/DP/PD/GPU 数线性外推。

另外两条容易混的口径：``tpot_p99_ms`` ≠ ``itl_p99_ms``（前者按请求级 TPOT 聚合，
后者是逐 token/chunk 间隔池）；``physical_cache_hit_rate`` ≠
``billable_cached_input_ratio``（后者需要合同计费规则，无规则不得推导）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Mapping


@dataclass(frozen=True)
class MetricDefinition:
    """一个标准字段的口径定义。``definition_id`` 稳定，可写进记录用于追溯。"""

    definition_id: str
    field_name: str
    unit: str
    definition: str
    #: 明确**禁止**用于填充本字段的来源字段（偷换指标检测）
    forbidden_substitutes: tuple[str, ...] = ()
    notes: str = ""


#: 标准字段 -> 口径定义。字段名与 Schema/控制台映射后的规范名一致。
FIELD_DEFINITIONS: dict[str, MetricDefinition] = {
    "configured_max_concurrency": MetricDefinition(
        definition_id="m.concurrency.configured",
        field_name="configured_max_concurrency",
        unit="requests",
        definition="测试命令 --max-concurrency 给出的**最大允许并发**，是配置值。",
        notes="不等于实际平均在途请求数；SGLang 输出中对应 'Max request concurrency'。",
    ),
    "observed_concurrency": MetricDefinition(
        definition_id="m.concurrency.observed",
        field_name="observed_concurrency",
        unit="requests",
        definition=(
            "文档报告的**观测**平均并发；SGLang 口径为『全请求累计耗时 / 墙钟时间』"
            "（对应输出 'Concurrency'）。"
        ),
        forbidden_substitutes=("configured_max_concurrency",),
        notes="必须注明计算窗口与统计方法；与配置并发是两个数，必须并存。",
    ),
    "traffic_request_rate_raw": MetricDefinition(
        definition_id="m.request_rate.raw",
        field_name="traffic_request_rate_raw",
        unit="req/s 或符号",
        definition="请求到达速率；``inf`` 表示负载生成器不限速（符号值，不是数值）。",
        notes="符号值不得转成 Decimal 参与任何财务计算（禁止 Decimal(Infinity)）。",
    ),
    "input_len_target": MetricDefinition(
        definition_id="m.len.target.input",
        field_name="input_len_target",
        unit="tokens",
        definition="Benchmark 目标输入长度（如 --random-input-len）。",
        notes="是目标参数，不等于逐请求实际输入长度分布。",
    ),
    "output_len_target": MetricDefinition(
        definition_id="m.len.target.output",
        field_name="output_len_target",
        unit="tokens",
        definition="Benchmark 目标输出长度（如 --random-output-len）。",
        notes="是目标参数，不等于逐请求实际生成长度分布。",
    ),
    "total_input_tokens": MetricDefinition(
        definition_id="m.tokens.total.input",
        field_name="total_input_tokens",
        unit="tokens",
        definition="同一测量窗口内实际累计的输入 token 数（来源原文定义）。",
        notes="SGLang 另行给出 text/vision 拆分，二者相加与总数不一定严格相等（retokenize 影响）。",
    ),
    "total_output_tokens": MetricDefinition(
        definition_id="m.tokens.total.output",
        field_name="total_output_tokens",
        unit="tokens",
        definition="同一测量窗口内实际累计的生成 token 数。",
        notes="控制台另有 '(retokenized)' 计数，是**另一个**指标，不得覆盖本字段。",
    ),
    "input_throughput_tok_s": MetricDefinition(
        definition_id="m.throughput.cluster.input",
        field_name="input_throughput_tok_s",
        unit="tok/s",
        definition="**整套参与测试 GPU** 的输入吞吐（集群口径）。",
        notes="不能当作单卡独立可实现能力；跨 TP/DP/PD/GPU 数外推被禁止。",
    ),
    "output_throughput_tok_s": MetricDefinition(
        definition_id="m.throughput.cluster.output",
        field_name="output_throughput_tok_s",
        unit="tok/s",
        definition="**整套参与测试 GPU** 的输出吞吐（集群口径）。",
        notes="同上；与 peak output throughput 也不是同一个量。",
    ),
    "input_tps_per_gpu_normalized": MetricDefinition(
        definition_id="m.throughput.per_gpu.input",
        field_name="input_tps_per_gpu_normalized",
        unit="tok/s/gpu",
        definition="在同一原始部署配置下，用集群吞吐 ÷ GPU 数得到的**归一化**资源效率。",
        notes="仅用于同配置内的资源效率比较，禁止跨配置外推。",
    ),
    "output_tps_per_gpu_normalized": MetricDefinition(
        definition_id="m.throughput.per_gpu.output",
        field_name="output_tps_per_gpu_normalized",
        unit="tok/s/gpu",
        definition="同一原始部署配置下，输出集群吞吐 ÷ GPU 数。",
        notes="同上。",
    ),
    "ttft_p99_ms": MetricDefinition(
        definition_id="m.latency.ttft.p99",
        field_name="ttft_p99_ms",
        unit="ms",
        definition="该 Bench 版本规定的首响应/首有效 token 计时口径下的 P99。",
        forbidden_substitutes=("ttft_mean_ms", "ttft_median_ms"),
        notes="保存 percentile 方法说明；不得用均值或中位数冒充 P99。",
    ),
    "tpot_mean_ms": MetricDefinition(
        definition_id="m.latency.tpot.mean",
        field_name="tpot_mean_ms",
        unit="ms",
        definition="请求级 TPOT（首个输出 token 之后）的均值。",
        forbidden_substitutes=("itl_mean_ms",),
        notes="≠ 全 token 间隔池的 ITL 均值。",
    ),
    "tpot_p99_ms": MetricDefinition(
        definition_id="m.latency.tpot.p99",
        field_name="tpot_p99_ms",
        unit="ms",
        definition="请求级 TPOT 的 P99。",
        forbidden_substitutes=("itl_p99_ms",),
        notes="**不得**用 ITL P99 冒名顶替（FORBIDDEN_SUBSTITUTIONS 已强制）。",
    ),
    "itl_mean_ms": MetricDefinition(
        definition_id="m.latency.itl.mean",
        field_name="itl_mean_ms",
        unit="ms",
        definition="逐 token/chunk 间隔的均值；受版本与流式协议影响。",
    ),
    "itl_p99_ms": MetricDefinition(
        definition_id="m.latency.itl.p99",
        field_name="itl_p99_ms",
        unit="ms",
        definition="逐 token/chunk 间隔的 P99。",
        notes="与 TPOT P99 并列存在，不互相替代。",
    ),
    "physical_cache_hit_rate": MetricDefinition(
        definition_id="m.cache.physical",
        field_name="physical_cache_hit_rate",
        unit="ratio",
        definition="KV Prefix 物理复用 / 框架监控到的命中率。",
    ),
    "billable_cached_input_ratio": MetricDefinition(
        definition_id="m.cache.billable",
        field_name="billable_cached_input_ratio",
        unit="ratio",
        definition="**可计费**缓存命中 token 占比，按合同计费规则定义。",
        forbidden_substitutes=("physical_cache_hit_rate", "cache_hit_rate_raw"),
        notes="无合同规则不得推导；Excel H 列只是旧表定价代理假设。",
    ),
}

#: 从控制台/命令解析出来的字段名 -> 标准字段名（别名）
FIELD_ALIASES: dict[str, str] = {
    "input_throughput_tok_s": "input_throughput_tok_s",
    "output_throughput_tok_s": "output_throughput_tok_s",
    "total_output_tokens_retokenized": "total_output_tokens_retokenized",
    "configured_max_concurrency": "configured_max_concurrency",
    "observed_concurrency": "observed_concurrency",
}

#: 一致性校验容差：公开发布值只保留 2 位小数，整数 token 数除以 2 位小数的时长，
#: 相对舍入误差最大约 1e-4（实测 12 条样本最大 2.27e-4）。取 1e-3 既能容纳舍入，
#: 又能抓住真正的单位/量纲错误（那会是 10x、1000x 级别）。
THROUGHPUT_TOLERANCE = Decimal("1e-3")


@dataclass
class ThroughputConsistency:
    checks: list[dict[str, object]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(bool(c["within_tolerance"]) for c in self.checks)

    @property
    def violations(self) -> list[str]:
        return [str(c["name"]) for c in self.checks if not c["within_tolerance"]]


def throughput_consistency(
    metrics: Mapping[str, Decimal],
    *,
    tolerance: Decimal = THROUGHPUT_TOLERANCE,
) -> ThroughputConsistency:
    """校验 ``吞吐 ≈ 累计 token / 时长``（任务书 §4 第 3 条）。

    只在三项数据都具备时检查；缺数据时**跳过**而不是判失败。
    """
    out = ThroughputConsistency()
    duration = metrics.get("duration_s")
    if duration in (None, 0):
        return out
    for name, tok_key, tps_key in (
        ("input", "total_input_tokens", "input_throughput_tok_s"),
        ("output", "total_output_tokens", "output_throughput_tok_s"),
    ):
        total = metrics.get(tok_key)
        published = metrics.get(tps_key)
        if total is None or published in (None, 0):
            continue
        computed = Decimal(total) / Decimal(duration)
        rel = abs(computed - Decimal(published)) / Decimal(published)
        out.checks.append(
            {
                "name": f"{name}_throughput_vs_tokens_over_duration",
                "published_tps": str(published),
                "computed_tps": str(computed),
                "relative_error": str(rel),
                "tolerance": str(tolerance),
                "within_tolerance": rel <= tolerance,
            }
        )
    return out


#: 由测试命令或候选文件补充的字段：它们**不来自**控制台输出块，因此没有原始标签。
NON_CONSOLE_FIELDS = frozenset({"configured_max_concurrency"})


def label_provenance_issues(
    metrics: Mapping[str, object], raw_labels: Mapping[str, str]
) -> list[str]:
    """校验每个指标的取值**来自它自己那一行**控制台输出（可判定的溯源检查）。

    为什么不用"取值相等"来判断偷换指标：TPOT 与 ITL 在单请求场景下本来就可以相等
    （实测 Coder 5.1.1.1 的 Mean TPOT 与 Mean ITL 都是 17.06），
    `configured=1 / observed=1` 同理。取值相等**不能**证明口径被混用，
    基于相等性的启发式会产生系统性误报。

    可判定的做法是核对**来源标签**：解析时保留了每个指标在原文里的原始标签
    （``raw_labels``），这里反查"该字段应当来自哪个标签"，不一致或缺失就报出来。
    """
    from llm_capacity_pricing.importers.console_summary import LABEL_MAP

    expected = {canonical: label for label, canonical in LABEL_MAP.items()}
    issues: list[str] = []
    for field in metrics:
        if field in NON_CONSOLE_FIELDS:
            continue
        label = raw_labels.get(field)
        if label is None:
            issues.append(f"{field} 缺少原始标签记录，无法确认取值来自哪一行控制台输出")
            continue
        # 存下来的原始标签保留单位后缀（如 "P99 TTFT (ms)"）。但括号有时是**标签本体**
        # 而非单位（如 "Total generated tokens (retokenized)"），因此两种形式都接受。
        raw = re.sub(r"\s+", " ", label).strip().lower()
        bare = re.sub(r"\s*\([^()]*\)\s*$", "", raw).strip()
        want = expected.get(field)
        if want and want not in (bare, raw):
            issues.append(f"{field} 的原始标签为 {label!r}，期望 {want!r}")
    return issues


def cross_backend_comparable(backend_version_a: str | None, backend_version_b: str | None) -> bool:
    """不同框架/版本若口径未核实一致，不允许合并统计或直接排序性能优劣。"""
    return bool(backend_version_a) and backend_version_a == backend_version_b


def definition_id_for(field_name: str) -> str | None:
    d = FIELD_DEFINITIONS.get(field_name)
    return d.definition_id if d else None


def metric_definition_ids(fields: Mapping[str, object]) -> dict[str, str]:
    """给一组实际出现的字段打上口径 ID（仅覆盖字典内定义的字段）。"""
    return {
        name: FIELD_DEFINITIONS[name].definition_id
        for name in fields
        if name in FIELD_DEFINITIONS
    }


def render_dictionary_markdown() -> str:
    """由字段定义生成字典正文（供 docs 使用，避免文档与代码口径漂移）。"""
    lines = [
        "| 标准字段 | 口径 ID | 单位 | 定义 | 禁止替代 |",
        "|---|---|---|---|---|",
    ]
    for name, d in FIELD_DEFINITIONS.items():
        banned = "、".join(f"`{b}`" for b in d.forbidden_substitutes) or "—"
        definition = d.definition + (f"（{d.notes}）" if d.notes else "")
        lines.append(f"| `{name}` | `{d.definition_id}` | {d.unit} | {definition} | {banned} |")
    return "\n".join(lines)
