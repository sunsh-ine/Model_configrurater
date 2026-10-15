"""第三阶段：性能评估与 SLA 报告（Markdown）。

报告纪律：**证据先行**。每个结论都要能追到"哪条实测、什么匹配等级、什么可信等级、
样本量多少"，并且明确区分"已观测"与"仅有估算"。
"""

from __future__ import annotations

from typing import Any, Sequence

PERF_DISCLAIMERS = (
    "匹配等级 ≠ 数据可信等级 ≠ SLA 状态：三者独立，配置再一致也不代表 SLA 达标。",
    "`observed_pass` 只可能来自**匹配的实测数据**；`estimated_only` 永远不算达标。",
    "synthetic 数据仅用于算法验证，**不得**作为任何正式 SLA 结论的依据。",
    "吞吐峰值只是**已测到的最优点**，在没有持续时间/失败率证据时不等于可持续产能。",
    "不同 TP/DP 的吞吐来自不同实测配置，本报告**不做跨配置吞吐排序**。",
    "未确认币种与成本范围前，不使用经济性评分。",
)


def _fmt(value: Any) -> str:
    return "—" if value is None else str(value)


def build_performance_report(
    *,
    inventory: dict[str, Any],
    profiles: Sequence[dict[str, Any]] = (),
    ranking: dict[str, Any] | None = None,
    request: dict[str, Any] | None = None,
) -> str:
    L: list[str] = []
    add = L.append

    add("# 推理性能评估与 SLA 报告（第三阶段）")
    add("")
    if request:
        add(f"- 业务需求：{request.get('description', '—')}")
        add(f"- SLA 约束：{request.get('sla', '—')}")
    add("")
    add("### 边界声明（务必先读）")
    add("")
    for d in PERF_DISCLAIMERS:
        add(f"> {d}")
    add("")

    add("## 1. 可用证据概览")
    add("")
    add("| 项 | 数量 |")
    add("|---|---|")
    add(f"| 可匹配签名总数 | {inventory.get('signature_count')} |")
    add(f"| 历史效能记录（无时延口径） | {inventory.get('benchmark_records')} |")
    add(f"| 官方候选 | {inventory.get('official_candidates')} |")
    add(f"| 性能压测记录（BenchmarkRun） | {inventory.get('benchmark_runs')} |")
    add("")
    by_trust = inventory.get("by_trust") or {}
    if by_trust:
        add("按数据可信等级分布：")
        add("")
        add("| 可信等级 | 数量 | 能否支撑 SLA 结论 |")
        add("|---|---|---|")
        sla_ok = set(inventory.get("sla_usable_trust_levels") or [])
        for trust, count in sorted(by_trust.items()):
            add(f"| `{trust}` | {count} | {'是' if trust in sla_ok else '**否**'} |")
        add("")

    add("## 2. 容量曲线（同配置多并发点）")
    add("")
    if not profiles:
        add("当前没有可分析的性能记录（需要先导入压测结果）。")
        add("")
    for profile in profiles:
        add(f"### {profile.get('group_key')}")
        add("")
        add(
            f"- 覆盖并发：{profile.get('covered_concurrency_points')}；"
            f"缺失测试点：{profile.get('missing_concurrency_points')}；"
            f"覆盖状态：`{profile.get('coverage_status')}`"
        )
        best = profile.get("best_observed_point")
        if best:
            add(
                f"- 观测最优吞吐点：并发 {best.get('configured_concurrency')}，"
                f"输出 {best.get('output_throughput_tok_s')} tok/s，"
                f"TTFT p99 {best.get('ttft_p99_ms')} ms，TPOT p99 {best.get('tpot_p99_ms')} ms"
            )
        add(
            f"- 可持续容量：{_fmt(profile.get('maximum_sustainable_capacity'))}"
            f"（证据：{profile.get('sustainability_evidence') or '无'}）"
        )
        add("")
        add("| 配置并发 | 观测并发 | 输出 tok/s | TTFT p99 | TPOT p99 | 失败率 |")
        add("|---|---|---|---|---|---|")
        for point in profile.get("points", []):
            add(
                f"| {_fmt(point.get('configured_concurrency'))} | {_fmt(point.get('observed_concurrency'))} "
                f"| {_fmt(point.get('output_throughput_tok_s'))} | {_fmt(point.get('ttft_p99_ms'))} "
                f"| {_fmt(point.get('tpot_p99_ms'))} | {_fmt(point.get('error_rate'))} |"
            )
        add("")
        for note in profile.get("notes", []):
            add(f"> {note}")
        add("")

    if ranking is not None:
        add("## 3. 部署候选性能比较")
        add("")
        add(f"- 需求：`{ranking.get('requirement_id')}`")
        add(f"- 静态状态分布：{ranking.get('static_status_counts')}")
        if ranking.get("blocker"):
            add(f"- **阻塞**：{ranking['blocker']}")
        add("")
        add("| 候选 | 卡数 | TP×DP | 静态 | 匹配等级 | 可信 | SLA | 输出 tok/s | 依据 |")
        add("|---|---|---|---|---|---|---|---|---|")
        for entry in ranking.get("entries", []):
            add(
                f"| `{entry.get('candidate_id')}` | {entry.get('gpu_count')} "
                f"| {entry.get('tp_size')}×{entry.get('dp_size')} | {_fmt(entry.get('static_status'))} "
                f"| {_fmt(entry.get('match_level'))} | — | {_fmt(entry.get('sla_status'))} "
                f"| {_fmt(entry.get('output_throughput_tok_s'))} "
                f"| {entry.get('rank_bucket')} |"
            )
        add("")
        for note in ranking.get("comparability_notes", []):
            add(f"> {note}")
        add("")

    add("## 4. 未确认与未实现（如实列出）")
    add("")
    for item in (
        "目标硬件（H100/H200 × Qwen3-32B）缺少本地实测数据 → 相关 SLA 结论恒为 `not_evaluable` / `requires_benchmark`",
        "PP / EP / PD 分离的性能模型未实现（字段保存并阻断）",
        "未实现跨模型/跨硬件的通用性能预测器，只保留模型接口",
        "未实现按 GPU FLOPS 推算 TTFT/TPOT（任务书禁止项）",
        "理论满载营收比不作为经济性评分（币种与成本范围未确认）",
    ):
        add(f"- {item}")
    add("")
    return "\n".join(L)
