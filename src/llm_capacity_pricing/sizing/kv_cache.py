"""M16：KV Cache 与运行时显存估算。

KV 公式（Dense decoder + GQA/MQA/MHA）：

    每 token 每层 KV 字节 = 2（K 与 V） × num_kv_heads × head_dim × bytes_per_element

* MHA：``num_kv_heads == num_attention_heads``；MQA：``num_kv_heads == 1``；
  GQA：介于两者之间。三者共用同一公式，差异只在 KV 头数。
* **Sliding Window / 未支持的 Attention**：显式降级（``readable=False``），
  绝不套用 Full Attention 公式（任务书 M16 硬要求）。
* **TP 下 KV 头如何处理取决于框架实现**：``num_kv_heads % tp == 0`` 时可均分；
  否则可能出现复制（同一 KV 头在多个 Rank 上重复）。本项目对未确认的情形
  标记 ``kv_head_distribution=unknown`` 并同时给出"均分/复制"两种口径。
* **Paged KV**：按 block 对齐后计算物理字节，并单列对齐放大；
  同时区分"逻辑可驻留 token 数"与"分配出的块数"。
* **运行时开销独立建模**：workspace / CUDA graph / 激活峰值等作为单独项，
  未提供时保持 ``origin=unknown``（**不得**静默为零）。
"""

from __future__ import annotations

from typing import Any

from llm_capacity_pricing.schemas.sizing import (
    KVCacheEstimate,
    ModelSpec,
    PrecisionByteSize,
    RuntimeReserve,
)

#: 默认 KV 精度换算（与权重共用同一张精度字节表）
KV_BYTES = PrecisionByteSize.BYTES

#: 典型运行时项清单（未提供数值时保持 unknown，不填 0）
DEFAULT_RUNTIME_ITEMS: tuple[tuple[str, str], ...] = (
    ("framework_workspace", "CUDA/ACL workspace、临时缓冲"),
    ("cuda_graph_capture", "CUDA Graph / 图捕获占用（若启用）"),
    ("activation_peak", "前向激活峰值"),
    ("framework_overhead", "框架自身常驻开销"),
)


def _align_up(value: int, unit: int) -> int:
    return ((value + unit - 1) // unit) * unit


def kv_bytes_per_token_per_layer(
    *, num_kv_heads: int, head_dim: int, kv_precision: str
) -> int | None:
    """单层单 token 的 KV 字节数（K+V）。精度未知时返回 None。"""
    bpe = KV_BYTES.get(kv_precision)
    if bpe is None:
        return None
    return 2 * num_kv_heads * head_dim * bpe


def estimate_kv_cache(
    model: ModelSpec,
    *,
    tp_size: int = 1,
    dp_size: int = 1,
    kv_precision: str = "bf16",
    global_concurrency: int | None = None,
    context_length_per_request: int | None = None,
    block_size_tokens: int | None = None,
    kv_budget_bytes: int | None = None,
) -> KVCacheEstimate:
    """估算 KV Cache。并发是**全局**口径，会按 DP 副本数摊到每个副本。"""
    notes: list[str] = []
    assumptions: list[str] = []
    missing: list[str] = []
    flags: list[str] = []

    attention_kind = model.attention_kind
    if attention_kind in ("sliding_window", "unsupported"):
        notes.append(
            f"attention_kind={attention_kind}：本轮不套用 Full Attention KV 公式，"
            "结果显式降级（readable=False）"
        )
        flags.append(f"kv_formula_not_applicable:{attention_kind}")
        return KVCacheEstimate(
            model_id=model.model_id,
            attention_kind=attention_kind,
            kv_precision=kv_precision,  # type: ignore[arg-type]
            readable=False,
            degradation_notes=notes,
            basis="Attention 结构不支持标准 KV 公式",
            missing_fields=["kv_capacity"],
            quality_flags=flags,
        )

    layers = model.num_hidden_layers
    kv_heads = model.num_key_value_heads
    head_dim = model.head_dim
    for name, value in (
        ("num_hidden_layers", layers),
        ("num_key_value_heads", kv_heads),
        ("head_dim", head_dim),
    ):
        if value is None:
            missing.append(name)
    bpe = KV_BYTES.get(kv_precision)
    if bpe is None:
        missing.append(f"bytes_per_element_for_kv_precision:{kv_precision}")

    if global_concurrency is None:
        missing.append("global_concurrency")
    if context_length_per_request is None:
        missing.append("context_length_per_request")

    # ---- TP 下 KV 头分布 --------------------------------------------------
    if kv_heads is not None and tp_size > 0:
        if kv_heads % tp_size == 0:
            kv_heads_per_rank: int | None = kv_heads // tp_size
            distribution = "sharded"
            kv_note = f"KV 头 {kv_heads} 可被 TP={tp_size} 均分，每 Rank {kv_heads_per_rank} 头"
        else:
            kv_heads_per_rank = kv_heads
            distribution = "unknown"
            kv_note = (
                f"KV 头 {kv_heads} 不能被 TP={tp_size} 整除：可能复制（每 Rank 保留 "
                f"{kv_heads} 头）或部分 Rank 空转，取决于框架实现，未确认"
            )
            flags.append("kv_head_split_not_divisible_by_tp")
        if kv_heads < tp_size:
            kv_heads_per_rank = kv_heads
            distribution = "unknown"
            kv_note += f"；且 KV 头数({kv_heads}) < TP({tp_size})，必然存在复制"
            flags.append("kv_heads_fewer_than_tp_requires_replication")
    else:
        kv_heads_per_rank = None
        distribution = "unknown"
        kv_note = "KV 头数未知，无法判断 TP 下的分布"
    if distribution == "unknown":
        assumptions.append(f"KV 头在 TP 下的分布未确认：{kv_note}")

    # ---- Paged KV 与容量 -------------------------------------------------
    concurrency_per_replica = None
    if global_concurrency is not None and dp_size > 0:
        concurrency_per_replica = -(-global_concurrency // dp_size)  # 向上取整
        assumptions.append(
            f"全局并发 {global_concurrency} 由 {dp_size} 个 DP 副本分担 → "
            f"每副本按 {concurrency_per_replica} 条请求估算（DP 分摊并发，不切分单条请求的权重）"
        )

    logical_tokens: int | None = None
    unaligned: int | None = None
    physical: int | None = None
    blocks: int | None = None
    overhead: int | None = None
    alignment_note = ""

    if (
        bpe is not None
        and layers is not None
        and kv_heads_per_rank is not None
        and head_dim is not None
        and concurrency_per_replica is not None
        and context_length_per_request is not None
    ):
        per_token_per_layer = 2 * kv_heads_per_rank * head_dim * bpe
        logical_tokens = concurrency_per_replica * context_length_per_request
        unaligned = logical_tokens * per_token_per_layer * layers
        if block_size_tokens:
            blocks_per_seq = _align_up(context_length_per_request, block_size_tokens) // block_size_tokens
            blocks = blocks_per_seq * concurrency_per_replica
            physical = blocks * block_size_tokens * per_token_per_layer * layers
            overhead = physical - unaligned
            alignment_note = (
                f"Paged KV：block_size={block_size_tokens} token/块；"
                f"每请求对齐到 {blocks_per_seq} 块 → 共 {blocks} 块；"
                f"对齐放大 {overhead} 字节（{(physical/unaligned - 1)*100:.2f}%）"
            )
            flags.append("paged_kv_block_alignment_applied")
        else:
            physical = unaligned
            alignment_note = "未指定 block_size_tokens：按未分页（连续）布局计算，无对齐放大"
            flags.append("kv_not_paged_block_size_unspecified")

    capacity_from_budget: int | None = None
    if (
        kv_budget_bytes is not None
        and bpe is not None
        and layers
        and kv_heads_per_rank is not None
        and head_dim is not None
    ):
        per_token_total = 2 * kv_heads_per_rank * head_dim * bpe * layers
        capacity_from_budget = kv_budget_bytes // per_token_total
        assumptions.append(
            f"按 KV 预算 {kv_budget_bytes} 字节反推：该副本可驻留约 "
            f"{capacity_from_budget} 个 KV token"
        )

    if missing:
        flags.append("kv_estimate_incomplete")
    readable = not missing

    return KVCacheEstimate(
        model_id=model.model_id,
        attention_kind=attention_kind,
        kv_precision=kv_precision,  # type: ignore[arg-type]
        bytes_per_element=bpe,
        num_layers=layers,
        num_kv_heads_total=kv_heads,
        num_kv_heads_per_rank=kv_heads_per_rank,
        head_dim=head_dim,
        sliding_window=model.sliding_window,
        tp_size=tp_size,
        dp_size=dp_size,
        global_concurrency=global_concurrency,
        concurrency_per_replica=concurrency_per_replica,
        context_length_per_request=context_length_per_request,
        logical_token_capacity=capacity_from_budget if capacity_from_budget is not None else logical_tokens,
        block_size_tokens=block_size_tokens,
        allocated_blocks=blocks,
        physical_bytes=physical,
        unaligned_bytes=unaligned,
        alignment_overhead_bytes=overhead,
        kv_head_distribution=distribution,  # type: ignore[arg-type]
        kv_head_note=kv_note,
        readable=readable,
        degradation_notes=([alignment_note] if alignment_note else []) + notes,
        basis=(
            f"每 token 每层 KV = 2 × kv_heads({kv_heads_per_rank}/rank) × head_dim({head_dim}) "
            f"× {bpe} 字节；层数 {layers}；每副本并发 {concurrency_per_replica} × "
            f"上下文 {context_length_per_request}"
        ),
        assumptions=assumptions,
        missing_fields=sorted(set(missing)),
        quality_flags=sorted(set(flags)),
    )


def default_runtime_items(
    *, provided: dict[str, dict[str, Any]] | None = None
) -> list[RuntimeReserve]:
    """构造运行时预留项。

    ``provided`` 形如 ``{"framework_workspace": {"bytes": 2*2**30, "source": "vLLM 文档"}}``。
    **未提供**的项保持 ``origin="unknown"`` 且 ``bytes_estimate=None``——
    未知不等于 0（任务书 M16 硬要求）。
    """
    provided = provided or {}
    items: list[RuntimeReserve] = []
    for key, description in DEFAULT_RUNTIME_ITEMS:
        spec = provided.get(key)
        if spec is None:
            items.append(
                RuntimeReserve(
                    item=key,
                    bytes_estimate=None,
                    origin="unknown",
                    assumption=None,
                    source=None,
                )
            )
            continue
        items.append(
            RuntimeReserve(
                item=key,
                bytes_estimate=int(spec["bytes"]),
                origin=spec.get("origin", "assumed"),
                source=spec.get("source"),
                assumption=spec.get("assumption") or description,
            )
        )
    return items
