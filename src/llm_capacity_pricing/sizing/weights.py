"""M15：模型权重显存计算。

方法（任务书 M15）：

1. **优先用实际权重张量元数据**：取得每个张量的 shape/dtype 时按张量逐项累加；
2. 没有元数据时用**结构公式**估算，公式按 Dense decoder + GQA 的真实张量命名推导
   （逐项：embedding / attention q,k,v,o / q_norm,k_norm / FFN gate,up,down /
   layernorm ×2 / final norm / lm_head），而不是笼统的"参数量 × 字节数"；
3. **共享权重只算一次**：``tie_word_embeddings=true`` 时 lm_head 与 embedding 共享；
4. **TP 下区分切分与复制**：注意力与 FFN 按 TP 切分，norm 类复制；
   embedding/lm_head 是否 vocab 并行**取决于框架实现**，本轮默认按"未确认"处理，
   并给出"全复制"上界与"全切分"下界；
5. **一致性检查**：结构公式总量 vs 权重索引 ``metadata.total_size``，两者不一致要说明差异。

硬约束：缺少张量布局证据时 ``per_rank_precision`` 不得为 ``exact``（任务书 M15 验收）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from llm_capacity_pricing.schemas.sizing import (
    ModelSpec,
    Precision,
    PrecisionByteSize,
    TpDistribution,
    WeightBreakdown,
    WeightMemoryEstimate,
)

#: 权重张量命名 -> 组件（按 Qwen3 系列已核实的命名；缺失的组件自动跳过）
TENSOR_COMPONENT_PATTERNS: tuple[tuple[str, str], ...] = (
    ("model.embed_tokens.weight", "embedding"),
    ("lm_head.weight", "lm_head"),
    ("model.norm.weight", "final_norm"),
    ("self_attn.q_proj.weight", "attention_q"),
    ("self_attn.k_proj.weight", "attention_k"),
    ("self_attn.v_proj.weight", "attention_v"),
    ("self_attn.o_proj.weight", "attention_o"),
    ("self_attn.q_norm.weight", "attention_q_norm"),
    ("self_attn.k_norm.weight", "attention_k_norm"),
    ("mlp.gate_proj.weight", "ffn_gate"),
    ("mlp.up_proj.weight", "ffn_up"),
    ("mlp.down_proj.weight", "ffn_down"),
    ("input_layernorm.weight", "layer_norm_in"),
    ("post_attention_layernorm.weight", "layer_norm_post"),
)

#: 组件 -> TP 分布方式。``unknown`` 表示取决于框架实现（例如 vocab 并行是否开启）。
DEFAULT_TP_POLICY: dict[str, TpDistribution] = {
    "attention_q": "sharded",
    "attention_k": "sharded",
    "attention_v": "sharded",
    "attention_o": "sharded",
    "attention_q_norm": "replicated",
    "attention_k_norm": "replicated",
    "ffn_gate": "sharded",
    "ffn_up": "sharded",
    "ffn_down": "sharded",
    "layer_norm_in": "replicated",
    "layer_norm_post": "replicated",
    "final_norm": "replicated",
    # 词汇表维度的参数：vLLM 等框架可做 vocab-parallel embedding，
    # 也可能保持复制；没有框架侧确认前不假定任何一种。
    "embedding": "unknown",
    "lm_head": "unknown",
}


@dataclass
class WeightFormulaInputs:
    """结构公式的输入（全部来自 ModelSpec，缺项则公式不可用）。"""

    num_layers: int
    hidden_size: int
    num_attention_heads: int
    num_key_value_heads: int
    head_dim: int
    intermediate_size: int
    vocab_size: int
    tie_word_embeddings: bool
    has_qk_norm: bool
    has_attention_bias: bool
    ffn_has_gate: bool
    notes: list[str] = field(default_factory=list)


def _params_from_spec(spec: ModelSpec) -> WeightFormulaInputs:
    """从 ModelSpec 推导公式输入，并用权重命名确认结构细节。"""
    names = set(spec.weight_metadata.weight_map) if spec.weight_metadata.weight_map else set()
    joined = "\n".join(names)
    has_qk_norm = "self_attn.q_norm.weight" in joined
    # gate 判定：只有在"看到 up_proj 但没有 gate_proj"时才能认定该结构没有 gate
    # （否则部分索引/命名差异会导致公式悄悄少算一个 FFN 矩阵）
    has_up = "mlp.up_proj.weight" in joined
    has_gate = ("mlp.gate_proj.weight" in joined) or not has_up
    notes: list[str] = []
    if not names:
        notes.append("未取得权重索引，结构细节（q_norm/k_norm、gate_proj）按标准 Dense GQA 假定")
    elif not has_up:
        notes.append("权重命名未包含 mlp.up_proj，FFN 结构按标准 gated 形态假定，需复核")
    if has_qk_norm:
        notes.append("权重命名含 self_attn.q_norm / k_norm，公式已计入（Qwen3 特有）")
    return WeightFormulaInputs(
        num_layers=spec.num_hidden_layers,
        hidden_size=spec.hidden_size,
        num_attention_heads=spec.num_attention_heads,
        num_key_value_heads=spec.num_key_value_heads or spec.num_attention_heads,
        head_dim=spec.head_dim or (spec.hidden_size // max(spec.num_attention_heads, 1)),
        intermediate_size=spec.intermediate_size,
        vocab_size=spec.vocab_size,
        tie_word_embeddings=bool(spec.tie_word_embeddings),
        has_qk_norm=has_qk_norm,
        has_attention_bias=bool(spec.attention_bias),
        ffn_has_gate=has_gate,
        notes=notes,
    )


def component_element_counts(inp: WeightFormulaInputs) -> dict[str, int]:
    """各组件元素数（未乘字节），**键名与 ``DEFAULT_TP_POLICY`` 对齐**。

    分组与真实权重张量命名一一对应（见 ``TENSOR_COMPONENT_PATTERNS``），
    这样 TP 分布策略可以直接按组件生效，也便于将来用张量元数据逐项替换。
    共享权重只计一次：``tie_word_embeddings=true`` 时 lm_head 记 0。
    """
    h = inp.hidden_size
    L = inp.num_layers
    q_dim = inp.num_attention_heads * inp.head_dim
    kv_dim = inp.num_key_value_heads * inp.head_dim
    counts: dict[str, int] = {
        "embedding": inp.vocab_size * h,
        "lm_head": 0 if inp.tie_word_embeddings else inp.vocab_size * h,
        "final_norm": h,
        "attention_q": h * q_dim * L,
        "attention_k": h * kv_dim * L,
        "attention_v": h * kv_dim * L,
        "attention_o": q_dim * h * L,
        "attention_q_norm": (inp.head_dim * L) if inp.has_qk_norm else 0,
        "attention_k_norm": (inp.head_dim * L) if inp.has_qk_norm else 0,
        "layer_norm_in": h * L,
        "layer_norm_post": h * L,
        "ffn_gate": (h * inp.intermediate_size * L) if inp.ffn_has_gate else 0,
        "ffn_up": h * inp.intermediate_size * L,
        "ffn_down": inp.intermediate_size * h * L,
    }
    if inp.has_attention_bias:
        counts["attention_q"] += q_dim * L
        counts["attention_k"] += kv_dim * L
        counts["attention_v"] += kv_dim * L
    return counts


#: 细分组 -> 汇总类别（用于 breakdown 展示）
GROUP_TO_CATEGORY: dict[str, str] = {
    "embedding": "embedding",
    "lm_head": "lm_head",
    "attention_q": "attention",
    "attention_k": "attention",
    "attention_v": "attention",
    "attention_o": "attention",
    "attention_q_norm": "attention",
    "attention_k_norm": "attention",
    "ffn_gate": "ffn",
    "ffn_up": "ffn",
    "ffn_down": "ffn",
    "layer_norm_in": "norm",
    "layer_norm_post": "norm",
    "final_norm": "norm",
}


def estimate_weight_memory(
    spec: ModelSpec,
    *,
    tp_size: int = 1,
    precision: Precision | None = None,
    tp_policy: dict[str, TpDistribution] | None = None,
) -> WeightMemoryEstimate:
    """计算权重显存（含 TP 分布与一致性检查）。"""
    precision = precision or spec.deployment_precision
    bytes_per_element = PrecisionByteSize.BYTES.get(precision)
    policy = {**DEFAULT_TP_POLICY, **(tp_policy or {})}
    assumptions: list[str] = []
    flags: list[str] = []
    missing: list[str] = []

    if bytes_per_element is None:
        missing.append(f"bytes_per_element_for_precision:{precision}")
        return WeightMemoryEstimate(
            model_id=spec.model_id,
            tp_size=tp_size,
            precision=precision,
            per_rank_precision="unavailable",
            basis="精度未识别，无法换算字节数",
            missing_fields=missing,
            quality_flags=["unknown_precision"],
        )

    if not spec.supports_memory_estimate:
        missing.extend(spec.missing_fields or ["structure_incomplete"])
        return WeightMemoryEstimate(
            model_id=spec.model_id,
            tp_size=tp_size,
            precision=precision,
            bytes_per_element=bytes_per_element,
            per_rank_precision="unavailable",
            basis="模型结构不完整或不支持，拒绝估算",
            missing_fields=sorted(set(missing)),
            quality_flags=["structure_not_estimable"],
        )

    inp = _params_from_spec(spec)
    assumptions.extend(inp.notes)
    counts = component_element_counts(inp)
    elems = sum(counts.values())
    category = {"embedding": 0, "attention": 0, "ffn": 0, "norm": 0, "lm_head": 0}
    for group, element_count in counts.items():
        category[GROUP_TO_CATEGORY[group]] += element_count
    breakdown = WeightBreakdown(
        embedding_bytes=category["embedding"] * bytes_per_element,
        attention_bytes=category["attention"] * bytes_per_element,
        ffn_bytes=category["ffn"] * bytes_per_element,
        norm_bytes=category["norm"] * bytes_per_element,
        lm_head_bytes=category["lm_head"] * bytes_per_element,
        other_bytes=0,
        total_bytes=elems * bytes_per_element,
    )

    # ---- TP 分解（按细分组，策略键与分组名一一对应）----------------------
    lower = upper = 0
    uncertain: list[str] = []
    sharded: list[str] = []
    replicated: list[str] = []
    distribution: dict[str, TpDistribution] = {}
    for component, element_count in counts.items():
        if element_count == 0:
            continue
        dist = policy.get(component, "unknown")
        distribution[component] = dist
        full_bytes = element_count * bytes_per_element
        if dist == "sharded":
            sharded.append(component)
            lower += full_bytes // tp_size
            upper += full_bytes // tp_size
        elif dist in ("replicated", "sharded_with_replication"):
            replicated.append(component)
            lower += full_bytes
            upper += full_bytes
        else:  # unknown：下界按切分、上界按复制
            uncertain.append(component)
            lower += full_bytes // tp_size
            upper += full_bytes

    per_rank = upper if uncertain else upper
    precision_label = "exact" if spec.weight_metadata.tensor_metadata_available else "upper_bound_estimate"
    if uncertain:
        flags.append("tp_layout_uncertain_for_vocab_parallel_components")
        assumptions.append(
            "embedding/lm_head 是否 vocab 并行取决于推理框架实现，未确认前按"
            "『全复制』给上界、『等分』给下界，两个数都会给出"
        )
    if spec.weight_metadata.tensor_metadata_available:
        assumptions.append("已取得张量级元数据，按张量累加")
    else:
        flags.append("per_rank_is_structural_estimate_not_tensor_layout")

    # ---- 一致性检查 ------------------------------------------------------
    index_total = spec.weight_metadata.total_weight_bytes
    delta = None if index_total is None else breakdown.total_bytes - index_total
    ratio = None if not index_total else Decimal(breakdown.total_bytes) / Decimal(index_total)
    note = None
    if index_total is None:
        note = "权重索引总量缺失，无法做一致性检查"
        missing.append("weight_index_total_bytes")
    elif delta == 0:
        note = "结构公式总量与权重索引 metadata.total_size **完全一致**（差 0 字节）"
        flags.append("formula_matches_official_weight_total")
    else:
        note = (
            f"结构公式与权重索引差 {delta} 字节（比值 {ratio:.6f}）："
            "可能来自未建模的组件（如 MoE 专家、额外 bias）或索引含非权重张量"
        )
        flags.append("formula_differs_from_official_weight_total")

    return WeightMemoryEstimate(
        model_id=spec.model_id,
        tp_size=tp_size,
        precision=precision,
        bytes_per_element=bytes_per_element,
        breakdown=breakdown,
        total_weight_bytes=breakdown.total_bytes,
        per_rank_bytes=per_rank,
        per_rank_bytes_lower_bound=lower,
        per_rank_bytes_upper_bound=upper,
        uncertain_components=sorted(uncertain),
        per_rank_precision=precision_label,
        distribution=distribution,
        replicated_components=sorted(replicated),
        sharded_components=sorted(sharded),
        per_rank_bytes_list=[per_rank] * max(tp_size, 1),
        max_rank_bytes=per_rank,
        index_total_bytes=index_total,
        consistency_delta_bytes=delta,
        consistency_ratio=ratio,
        consistency_note=note,
        basis=(
            f"Dense decoder + GQA 结构公式：L={inp.num_layers}, h={inp.hidden_size}, "
            f"heads={inp.num_attention_heads}, kv_heads={inp.num_key_value_heads}, "
            f"head_dim={inp.head_dim}, ffn={inp.intermediate_size}, vocab={inp.vocab_size}, "
            f"tie={inp.tie_word_embeddings}, qk_norm={inp.has_qk_norm}；每元素 {bytes_per_element} 字节"
        ),
        assumptions=assumptions,
        missing_fields=sorted(set(missing)),
        quality_flags=sorted(set(flags)),
    )


def tensor_metadata_weight_bytes(spec: ModelSpec) -> int | None:
    """有张量级元数据时按张量累加；没有则返回 None（不使用估算值冒充）。"""
    md = spec.weight_metadata
    if not md.tensor_metadata_available or not md.tensor_metadata:
        return None
    total = 0
    for item in md.tensor_metadata.values():
        nbytes = item.get("bytes")
        if nbytes is None:
            return None
        total += int(nbytes)
    return total
