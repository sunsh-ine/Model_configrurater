"""第二阶段：资源规划报告（Markdown）。

报告纪律（任务书 M18）：

* 每个数字都要能追到**公式版本 + 来源 + 假设**；
* 区分「实测 / 厂商文档 / 假设 / 未知」四类输入；
* 未知运行时开销显式列出，不写成 0；
* 结论只讲静态资源，明确声明**不代表可启动、不代表满足 SLA**。
"""

from __future__ import annotations

from typing import Any, Iterable, Sequence

from llm_capacity_pricing.schemas.sizing import (
    DeploymentCandidate,
    HardwareSpec,
    ModelSpec,
    ServerProfile,
)

SIZING_DISCLAIMERS = (
    "本报告是**静态资源规划**：只回答『按给定假设，静态显存是否放得下』。",
    "**不代表**服务能成功启动：框架内核支持、通信、拓扑、驱动等均未验证。",
    "**不代表**满足 SLA：本阶段不做 TTFT/TPOT/吞吐预测（第二阶段边界）。",
    "可用显存若来自假设而非实测，结论只能算『条件可行』，落地前必须实测复核。",
)


def _gib(value: int | None) -> str:
    return "—" if value is None else f"{value / 1024**3:.2f} GiB"


def _gb(value: int | None) -> str:
    return "—" if value is None else f"{value / 10**9:.2f} GB"


def build_sizing_report(
    *,
    models: Sequence[ModelSpec],
    hardware: Sequence[HardwareSpec],
    servers: Sequence[ServerProfile],
    candidates: Iterable[DeploymentCandidate],
    request: dict[str, Any],
    sources: Sequence[dict[str, Any]] = (),
) -> str:
    L: list[str] = []
    add = L.append

    add("# 模型与硬件资源规划报告（第二阶段）")
    add("")
    add(f"- 请求场景：{request.get('description', '—')}")
    add(f"- 模型：`{request.get('model_id')}`　硬件：`{request.get('hardware_id')}`"
        f"　精度：{request.get('weight_precision')} 权重 / {request.get('kv_precision')} KV")
    add(f"- 上下文长度：{request.get('context_length_per_request')} tokens/请求"
        f"　全局并发：{request.get('global_concurrency')}")
    add(f"- 公式版本：weights-1.0 / kv-1.0 / memory-1.0")
    add("")
    add("### 边界声明（务必先读）")
    add("")
    for d in SIZING_DISCLAIMERS:
        add(f"> {d}")
    add("")

    # ---- 输入来源与假设 ------------------------------------------------
    add("## 1. 输入：来源、假设与缺口")
    add("")
    add("| 项 | 值 | 性质 |")
    add("|---|---|---|")
    for m in models:
        add(
            f"| 模型 `{m.model_id}` | 结构来自官方 config.json，revision `{m.revision}`，"
            f"sha256 `{(m.config_sha256 or '')[:16]}…` | 厂商官方（已归档） |"
        )
    for h in hardware:
        add(
            f"| 硬件 `{h.hardware_id}` | {h.product_name}，标称 {h.hbm_capacity_declared} "
            f"({_gb(h.total_memory_bytes)})，带宽 {h.memory_bandwidth_declared} | "
            f"{h.verification_status} |"
        )
    add(
        f"| 单卡可用显存 | {_gib(request.get('per_gpu_usable_bytes'))} | "
        f"{request.get('usable_memory_source')} |"
    )
    add(
        f"| 运行时预留 | {request.get('runtime_reserve_description', '未提供')} | "
        f"{request.get('runtime_reserve_source') or '未提供'} |"
    )
    add("")
    pending = [
        f"`{h.hardware_id}`: {', '.join(h.fields_pending_verification)}"
        for h in hardware
        if h.fields_pending_verification
    ]
    if pending:
        add("**待核实字段**（不得默认满足）：")
        add("")
        for item in pending:
            add(f"- {item}")
        add("")

    # ---- 候选表 --------------------------------------------------------
    add("## 2. 部署候选（按状态与卡数排序，不代表性能优劣）")
    add("")
    add("| 候选 | 卡数 | TP | DP | 权重/卡 | KV/卡 | 合计/卡 | 余量 | 状态 |")
    add("|---|---|---|---|---|---|---|---|---|")
    candidates = list(candidates)
    for c in candidates:
        m = c.memory
        add(
            f"| `{c.candidate_id}` | {c.gpu_count} | {c.tp_size} | {c.dp_size} "
            f"| {_gib(m.weight.per_rank_bytes if m else None)} "
            f"| {_gib(m.kv.physical_bytes if m else None)} "
            f"| {_gib(m.total_required_bytes if m else None)} "
            f"| {_gib(m.headroom_bytes if m else None)} "
            f"| `{c.feasibility.status}` |"
        )
    add("")
    status_counts: dict[str, int] = {}
    for c in candidates:
        status_counts[c.feasibility.status] = status_counts.get(c.feasibility.status, 0) + 1
    add(f"状态分布：{status_counts}")
    add("")

    # ---- 逐候选依据 ----------------------------------------------------
    add("## 3. 每个候选的判定依据")
    add("")
    for c in candidates:
        add(f"### {c.candidate_id}")
        add("")
        add(f"- 结论：`{c.feasibility.status}`（{c.gpu_count} 卡 = TP{c.tp_size} × DP{c.dp_size}）")
        if c.memory:
            m = c.memory
            add(f"- 权重：{_gib(m.weight.per_rank_bytes)} / 卡（{m.weight.per_rank_precision}）")
            add(
                f"  - 分布：切分 {m.weight.sharded_components}；复制 {m.weight.replicated_components}；"
                f"未确认 {m.weight.uncertain_components}"
            )
            add(f"  - 依据：{m.weight.basis}")
            add(f"  - 与官方权重总量一致性：{m.weight.consistency_note}")
            add(
                f"- KV：{_gib(m.kv.physical_bytes)} / 卡（{m.kv.kv_head_distribution}）；"
                f"依据：{m.kv.basis}"
            )
            if m.kv.degradation_notes:
                add(f"  - 说明：{'；'.join(m.kv.degradation_notes)}")
            known = [r for r in m.runtime_items if r.bytes_estimate is not None]
            unknown = [r for r in m.runtime_items if r.bytes_estimate is None]
            add(
                f"- 运行时：已知 {len(known)} 项（合计 {_gib(m.runtime_total_bytes)}），"
                f"未知 {len(unknown)} 项：{[r.item for r in unknown] or '无'}"
            )
            add(f"- 单卡可用：{_gib(m.per_gpu_usable_bytes)}；合计需求：{_gib(m.total_required_bytes)}；余量：{_gib(m.headroom_bytes)}")
        for a in c.feasibility.assumptions:
            add(f"- 依赖假设：{a}")
        for r in c.feasibility.reasons:
            add(f"- 原因：{r}")
        if c.feasibility.unverified_conditions:
            add(f"- 未确认条件：{c.feasibility.unverified_conditions}")
        if c.feasibility.risk_flags:
            add(f"- 风险标记：{c.feasibility.risk_flags}")
        add("")

    # ---- 来源索引 ------------------------------------------------------
    add("## 4. 来源与证据索引")
    add("")
    if sources:
        add("| 来源 | 发布方 | URL | 文件 | sha256 |")
        add("|---|---|---|---|---|")
        for s in sources:
            add(
                f"| `{s.get('source_id')}` | {s.get('publisher')} | {s.get('url')} "
                f"| `{s.get('file')}` | `{(s.get('sha256') or '')[:16]}…` |"
            )
    else:
        add("（本次未附来源表）")
    add("")
    add("## 5. 明确未做的事（第二阶段边界）")
    add("")
    for item in (
        "不做 TTFT/TPOT/ITL/E2E 的性能预测",
        "不根据峰值并发推断所需 GPU 数量",
        "不做跨 GPU/TP/DP 的吞吐线性外推",
        "不覆盖 PD 分离、MoE EP、MTP/Speculative 的资源优化",
        "不做未验证量化实现的精确显存核算",
        "不把静态可行方案转换成客户报价",
    ):
        add(f"- {item}")
    add("")
    return "\n".join(L)
