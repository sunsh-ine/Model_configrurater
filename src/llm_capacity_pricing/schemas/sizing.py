"""第二阶段（M12-M18）资源选型的数据结构。

六类核心对象：

======================  ====================================================
对象                    作用
======================  ====================================================
``ModelSpec``           模型结构事实（来自官方 config.json / 权重索引）
``HardwareSpec``        GPU SKU 规格（容量/带宽/形态，带来源）
``WeightMemoryEstimate`` 权重显存（分解 + TP 分布 + 一致性检查）
``MemoryEstimate``      单副本总显存（权重 + KV + 运行时项）
``DeploymentCandidate`` TP/DP 候选（含每 Rank 占用与状态）
``ResourceFeasibilityResult`` 静态可行性判定（四类状态 + 原因）
======================  ====================================================

**贯穿全阶段的四条约束**（与第一阶段同源）：

1. 缺失字段一律 ``None``，绝不填 0；未知运行时开销不得静默为零。
2. 标称 HBM 容量 ≠ 可分配容量；``usable_memory_bytes`` 必须来自实测。
3. 没有 Tensor 布局证据时，**不得**声称精确的"每卡权重显存"——只能给上界估算并标记
   ``per_rank_precision=upper_bound_estimate``。
4. 静态可行 ≠ 能启动、≠ 满足 SLA；任何结果都不得表述为部署承诺。
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

# ---------------------------------------------------------------------------
# 枚举 / 状态
# ---------------------------------------------------------------------------

#: 模型结构解析状态
ModelSpecStatus = Literal[
    "parsed",  # 结构字段齐全，可用于计算
    "partially_parsed",  # 部分字段缺失（缺失项在 missing_fields）
    "unsupported_architecture",  # 结构不受支持，禁止按标准 Dense Transformer 硬算
]

#: 部署精度（权重/计算）
Precision = Literal["bf16", "fp16", "fp32", "fp8", "int8", "int4", "unknown"]

#: KV cache 精度
KvPrecision = Literal["bf16", "fp16", "fp8", "int8", "int4", "unknown"]

#: Attention 类型（决定 KV 公式）
AttentionKind = Literal["mha", "gqa", "mqa", "sliding_window", "unsupported"]

#: 权重在 TP 下的分布方式
TpDistribution = Literal[
    "sharded",  # 张量切分（按 TP 均分）
    "replicated",  # 每个 Rank 完整复制
    "sharded_with_replication",  # 切分后仍带复制（如 KV heads 少于 TP）
    "unknown",  # 运行时布局未确认
]

#: 静态可行性状态（M17）
FeasibilityStatus = Literal[
    "infeasible",  # 已确认违反资源或硬件约束
    "conditional_feasible",  # 在明确的运行时预留与兼容性假设下，静态资源可能足够
    "insufficient_information",  # 数据不足，无法确定
    "unsupported",  # 模型或部署方式未实现
]

#: 运行时显存项的来源性质
RuntimeItemOrigin = Literal[
    "measured",  # 实测
    "vendor_documented",  # 厂商/框架文档给出
    "assumed",  # 用户提供的假设值（必须带 source/assumption 说明）
    "unknown",  # 未提供（值为 None，且**不得**当作 0）
]

#: 每 Rank 估算的精度等级
PerRankPrecision = Literal[
    "exact",  # 有 Tensor 布局证据
    "upper_bound_estimate",  # 结构公式估算（上界）
    "unavailable",  # 缺少必要信息
]


class PrecisionByteSize:
    """精度 -> 每元素字节数。

    **只收录"每个元素占用整数字节、且不需要打包/量化标定"的格式。**
    ``fp8`` / ``int8`` / ``int4`` 等需要量化缩放因子与位打包规则，缺专用模型时
    按字节数硬算会给出"看起来精确"的错误值，因此不在此表内——请求这些精度时
    上层返回 ``unsupported``（V2.1 整改，见 docs/phase2_final_validation.md）。
    """

    BYTES: dict[str, int] = {
        "bf16": 2,
        "fp16": 2,
        "fp32": 4,
    }


#: 第二阶段**可计算**的权重精度（每元素字节数确定，无打包/标定）
SUPPORTED_WEIGHT_PRECISIONS: tuple[str, ...] = ("bf16", "fp16", "fp32")
#: 第二阶段**可计算**的 KV 精度
SUPPORTED_KV_PRECISIONS: tuple[str, ...] = ("bf16", "fp16")
#: 已知但**本阶段不支持**的精度（需要专用显存模型）
UNSUPPORTED_PRECISIONS: tuple[str, ...] = ("fp8", "int8", "int4", "unknown")


def precision_supported(precision: str | None, *, kind: str = "weight") -> bool:
    """该精度在本阶段是否可计算。"""
    allowed = (
        SUPPORTED_WEIGHT_PRECISIONS if kind == "weight" else SUPPORTED_KV_PRECISIONS
    )
    return precision in allowed


# ---------------------------------------------------------------------------
# M13：模型
# ---------------------------------------------------------------------------


class ModelWeightMetadata(BaseModel):
    """权重文件层面的元信息（来自 ``model.safetensors.index.json``）。"""

    model_config = ConfigDict(extra="forbid")

    index_file: str | None = None
    index_sha256: str | None = None
    #: 权重总量（字节）——由索引文件的 metadata.total_size 给出
    total_weight_bytes: int | None = None
    shard_count: int | None = None
    tensor_count: int | None = None
    shard_files: list[str] = Field(default_factory=list)
    #: tensor 名 -> 所在分片（保留完整映射，便于按层归类与差异检查）
    weight_map: dict[str, str] = Field(default_factory=dict)
    #: 是否取得了张量级 shape/dtype（本轮默认没有：需要下载权重）
    tensor_metadata_available: bool = False
    tensor_metadata: dict[str, dict[str, Any]] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)


class ModelSpec(BaseModel):
    """模型结构事实。所有字段来自官方配置或权重索引，缺失即 ``None``。"""

    model_config = ConfigDict(extra="forbid")

    model_id: str
    # ---- 来源 ----------------------------------------------------------
    repo_id: str | None = None
    revision: str | None = None
    config_file: str | None = None
    config_sha256: str | None = None
    retrieved_at: datetime | None = None
    trust_level: Literal["official_repo_archived", "local_file", "unknown"] = "unknown"

    # ---- 结构 ----------------------------------------------------------
    architectures: list[str] = Field(default_factory=list)
    model_type: str | None = None
    num_hidden_layers: int | None = None
    hidden_size: int | None = None
    num_attention_heads: int | None = None
    num_key_value_heads: int | None = None
    head_dim: int | None = None
    intermediate_size: int | None = None
    vocab_size: int | None = None
    max_position_embeddings: int | None = None
    #: 权重文件声明的 dtype（**不是**本次部署请求的精度）
    weight_dtype_declared: str | None = None
    #: 本次部署请求使用的精度（由调用方指定）
    deployment_precision: Precision = "unknown"
    tie_word_embeddings: bool | None = None
    attention_bias: bool | None = None
    hidden_act: str | None = None
    rms_norm_eps: Decimal | None = None
    rope_theta: Decimal | None = None
    rope_scaling: dict[str, Any] | None = None
    sliding_window: int | None = None
    use_sliding_window: bool | None = None
    max_window_layers: int | None = None
    transformers_version: str | None = None

    weight_metadata: ModelWeightMetadata = Field(default_factory=ModelWeightMetadata)

    # ---- 状态与缺口 ----------------------------------------------------
    status: ModelSpecStatus = "partially_parsed"
    #: 按类别登记的缺失字段（结构/精度/权重元数据）
    missing_fields: list[str] = Field(default_factory=list)
    quality_flags: list[str] = Field(default_factory=list)

    # ---- 派生量（只读计算）---------------------------------------------
    @property
    def attention_kind(self) -> AttentionKind:
        """由结构与滑动窗口设置推导 Attention 类型。"""
        if self.use_sliding_window and self.sliding_window:
            return "sliding_window"
        if self.num_attention_heads is None or self.num_key_value_heads is None:
            return "unsupported"
        if self.num_key_value_heads == 1:
            return "mqa"
        if self.num_key_value_heads < self.num_attention_heads:
            return "gqa"
        return "mha"

    @property
    def supports_memory_estimate(self) -> bool:
        """结构是否足以做显存估算。"""
        return (
            self.status != "unsupported_architecture"
            and None
            not in (
                self.num_hidden_layers,
                self.hidden_size,
                self.num_attention_heads,
                self.intermediate_size,
                self.vocab_size,
            )
        )

    @model_validator(mode="after")
    def _guard(self) -> "ModelSpec":
        if self.status == "unsupported_architecture":
            for name in (
                "num_hidden_layers",
                "hidden_size",
                "num_attention_heads",
                "intermediate_size",
            ):
                if getattr(self, name) is not None:
                    raise ValueError(
                        f"status=unsupported_architecture 时不得给出结构字段 {name!r}："
                        "不支持的结构不允许被当作已解析"
                    )
        return self


# ---------------------------------------------------------------------------
# M14：硬件
# ---------------------------------------------------------------------------


class HardwareSpec(BaseModel):
    """GPU SKU 规格。容量内部统一 Bytes；标称容量 ≠ 可分配容量。"""

    model_config = ConfigDict(extra="forbid")

    hardware_id: str
    vendor: str
    product_name: str
    sku: str | None = None
    form_factor: str | None = None
    architecture: str | None = None
    hbm_type: str | None = None

    hbm_capacity_declared: str | None = None
    capacity_normalization: str | None = None
    #: 厂商标称容量的归一化字节值
    total_memory_bytes: int | None = None
    #: 推理真正可分配的显存，必须实测；本轮为 None
    usable_memory_bytes: int | None = None
    usable_memory_status: Literal["measured", "pending_measurement"] = "pending_measurement"

    memory_bandwidth_declared: str | None = None
    memory_bandwidth_bytes_per_s: int | None = None
    interconnect_within_node: str | None = None
    tdp_declared: str | None = None
    mig_declared: str | None = None

    supported_weight_precisions: list[Precision] = Field(default_factory=list)
    supported_kv_cache_precisions: list[KvPrecision] = Field(default_factory=list)

    source_id: str | None = None
    source_quote: str | None = None
    verification_status: Literal[
        "vendor_page_verified", "pending_verification", "user_provided"
    ] = "pending_verification"
    supported_in_phase2: bool = False
    fields_pending_verification: list[str] = Field(default_factory=list)
    notes: str | None = None

    @model_validator(mode="after")
    def _guard(self) -> "HardwareSpec":
        if self.usable_memory_bytes is not None and self.usable_memory_status != "measured":
            raise ValueError("usable_memory_bytes 有值时必须标记 usable_memory_status=measured")
        if self.usable_memory_bytes is None and self.usable_memory_status == "measured":
            raise ValueError("usable_memory_status=measured 必须给出实测值")
        if (
            self.usable_memory_bytes is not None
            and self.total_memory_bytes is not None
            and self.usable_memory_bytes > self.total_memory_bytes
        ):
            raise ValueError("实测可用显存不得大于标称容量")
        return self

    def allocatable_bytes(self) -> int | None:
        """可用于推理的显存：只认实测值，没有就返回 None（不拿标称顶替）。"""
        return self.usable_memory_bytes


class ServerProfile(BaseModel):
    """服务器形态：单节点 GPU 数量与节点内互联。未给出的字段保持 None。"""

    model_config = ConfigDict(extra="forbid")

    server_id: str
    profile_name: str
    gpu_hardware_id: str
    gpus_per_node: int | None = None
    interconnect_declared: str | None = None
    cpu_model: str | None = None
    host_memory_bytes: int | None = None
    nic_bandwidth_bytes_per_s: int | None = None
    source_id: str | None = None
    source_quote: str | None = None
    verification_status: Literal[
        "vendor_page_verified", "pending_verification", "user_provided"
    ] = "pending_verification"
    fields_pending_verification: list[str] = Field(default_factory=list)
    note: str | None = None


#: 兼容性结论
CompatibilityStatus = Literal["compatible", "incompatible", "unknown"]


class HardwareCompatibility(BaseModel):
    """硬件与部署需求的兼容性判定。"""

    model_config = ConfigDict(extra="forbid")

    status: CompatibilityStatus
    reasons: list[str] = Field(default_factory=list)
    #: 未确认的条件（**不得**默认为已满足）
    unverified_conditions: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# M15：权重显存
# ---------------------------------------------------------------------------


class WeightBreakdown(BaseModel):
    """按组件分解的参数量与字节数（全模型口径）。"""

    model_config = ConfigDict(extra="forbid")

    embedding_bytes: int | None = None
    attention_bytes: int | None = None
    ffn_bytes: int | None = None
    norm_bytes: int | None = None
    lm_head_bytes: int | None = None
    other_bytes: int | None = None
    total_bytes: int | None = None


class WeightMemoryEstimate(BaseModel):
    """权重显存估算结果。"""

    model_config = ConfigDict(extra="forbid")

    model_id: str
    hardware_id: str | None = None
    tp_size: int
    precision: Precision
    bytes_per_element: int | None = None

    breakdown: WeightBreakdown = Field(default_factory=WeightBreakdown)
    total_weight_bytes: int | None = None
    #: 每 Rank 权重显存（估算）
    per_rank_bytes: int | None = None
    #: 各 Rank 占用（本轮的规则下通常相同；保留列表以便将来表达不均衡）
    per_rank_bytes_list: list[int] = Field(default_factory=list)
    max_rank_bytes: int | None = None

    #: 按"分布确定"假设算出的每 Rank 显存（下界）
    per_rank_bytes_lower_bound: int | None = None
    #: 按"未知即复制"的最保守假设算出的每 Rank 显存（上界）
    per_rank_bytes_upper_bound: int | None = None
    #: 分布未确认的组件（这些组件在上下界之间取值）
    uncertain_components: list[str] = Field(default_factory=list)

    per_rank_precision: PerRankPrecision = "unavailable"
    #: 各组件的 TP 分布方式（组件名 -> 分布），未确认的写 unknown
    distribution: dict[str, TpDistribution] = Field(default_factory=dict)
    #: 复制的组件（每个 Rank 都存一份）
    replicated_components: list[str] = Field(default_factory=list)
    #: 切分的组件
    sharded_components: list[str] = Field(default_factory=list)

    #: 与权重文件总大小的对比
    index_total_bytes: int | None = None
    consistency_delta_bytes: int | None = None
    consistency_ratio: Decimal | None = None
    consistency_note: str | None = None

    basis: str = ""
    formula_version: str = "weights-1.0"
    assumptions: list[str] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)
    quality_flags: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _guard(self) -> "WeightMemoryEstimate":
        if self.per_rank_precision == "exact" and self.distribution.get("unknown_components"):
            raise ValueError("分布未知时不得声称精确的每 Rank 权重显存")
        if self.per_rank_precision == "exact" and "unknown" in self.distribution.values():
            raise ValueError("存在 unknown 分布组件时不得声称 per_rank_precision=exact")
        return self


# ---------------------------------------------------------------------------
# M16：KV Cache 与运行时
# ---------------------------------------------------------------------------


class RuntimeReserve(BaseModel):
    """运行时显存项（workspace / CUDA graph / 激活峰值等）。

    ``origin=unknown`` 时 ``bytes_estimate`` 必须为 None —— 未知不是 0（任务书硬要求）。
    """

    model_config = ConfigDict(extra="forbid")

    item: str
    bytes_estimate: int | None = None
    origin: RuntimeItemOrigin = "unknown"
    source: str | None = None
    assumption: str | None = None

    @model_validator(mode="after")
    def _guard(self) -> "RuntimeReserve":
        if self.origin == "unknown" and self.bytes_estimate is not None:
            raise ValueError("origin=unknown 时不得给出字节数（未知不等于 0）")
        if self.origin == "assumed" and not (self.assumption or self.source):
            raise ValueError("假设值必须写明 assumption 或 source")
        if self.bytes_estimate is not None and self.bytes_estimate < 0:
            raise ValueError("运行时预留不得为负")
        return self


class KVCacheEstimate(BaseModel):
    """KV Cache 估算。区分逻辑 Token 容量、分配块数与物理字节。"""

    model_config = ConfigDict(extra="forbid")

    model_id: str
    attention_kind: AttentionKind
    kv_precision: KvPrecision
    bytes_per_element: int | None = None

    # ---- 结构输入 -------------------------------------------------------
    num_layers: int | None = None
    num_kv_heads_total: int | None = None
    num_kv_heads_per_rank: int | None = None
    head_dim: int | None = None
    sliding_window: int | None = None

    # ---- 负载输入 -------------------------------------------------------
    tp_size: int = 1
    dp_size: int = 1
    #: 全局并发（所有 DP 副本合计）
    global_concurrency: int | None = None
    #: 每个 DP 副本承担的请求数
    concurrency_per_replica: int | None = None
    context_length_per_request: int | None = None

    # ---- 容量结果 -------------------------------------------------------
    #: **需求**：该副本按"并发 × 上下文长度"需要同时驻留的逻辑 token 数
    required_resident_tokens: int | None = None
    #: **上限**：按 KV 显存预算反推的最大可驻留 token 数（未给预算时为 None）
    estimated_capacity_tokens: int | None = None
    #: 按 block 对齐后**实际分配**的 token 槽位（含对齐放大）
    allocated_tokens: int | None = None
    #: Paged KV：块大小与对齐后的块数
    block_size_tokens: int | None = None
    allocated_blocks: int | None = None
    #: 物理 KV 字节数（含块对齐放大）
    physical_bytes: int | None = None
    #: 未对齐时的理论最小字节（用于展示对齐开销）
    unaligned_bytes: int | None = None
    alignment_overhead_bytes: int | None = None

    #: KV Head 在 TP 下的处理方式（框架实现相关，必须显式标记）
    kv_head_distribution: TpDistribution = "unknown"
    kv_head_note: str | None = None

    readable: bool = True
    degradation_notes: list[str] = Field(default_factory=list)
    basis: str = ""
    formula_version: str = "kv-1.0"
    assumptions: list[str] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)
    quality_flags: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _guard(self) -> "KVCacheEstimate":
        if self.attention_kind in ("sliding_window", "unsupported") and self.readable:
            raise ValueError(
                f"attention_kind={self.attention_kind} 时必须显式降级（readable=False），"
                "不得套用 Full Attention 的 KV 公式"
            )
        if self.physical_bytes is not None and self.unaligned_bytes is not None:
            if self.physical_bytes < self.unaligned_bytes:
                raise ValueError("对齐后的物理字节不应小于未对齐理论值")
        if (
            self.allocated_tokens is not None
            and self.required_resident_tokens is not None
            and self.allocated_tokens < self.required_resident_tokens
        ):
            raise ValueError("对齐后分配的 token 槽位不应少于实际需求（除未分页场景）")
        return self


class MemoryEstimate(BaseModel):
    """单副本（一个 TP group）的总显存构成与余量。"""

    model_config = ConfigDict(extra="forbid")

    model_id: str
    hardware_id: str
    tp_size: int
    dp_size: int
    weight_precision: Precision
    kv_precision: KvPrecision

    weight: WeightMemoryEstimate
    kv: KVCacheEstimate
    runtime_items: list[RuntimeReserve] = Field(default_factory=list)

    #: 单卡可用显存（只认实测值）
    per_gpu_usable_bytes: int | None = None
    total_required_bytes: int | None = None
    headroom_bytes: int | None = None

    known_unknowns: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    quality_flags: list[str] = Field(default_factory=list)
    formula_version: str = "memory-1.0"

    @property
    def runtime_total_bytes(self) -> int | None:
        """已知运行时项之和；有 unknown 项时该值只是下界。"""
        known = [r.bytes_estimate for r in self.runtime_items if r.bytes_estimate is not None]
        return sum(known) if known else 0

    @property
    def has_unknown_runtime(self) -> bool:
        return any(r.origin == "unknown" for r in self.runtime_items)


# ---------------------------------------------------------------------------
# M17：部署候选与可行性
# ---------------------------------------------------------------------------


class ResourceFeasibilityResult(BaseModel):
    """静态可行性判定。**不代表**能启动，也不代表满足 SLA。"""

    model_config = ConfigDict(extra="forbid")

    status: FeasibilityStatus
    reasons: list[str] = Field(default_factory=list)
    #: 判定所依赖的假设（conditional_feasible 必须非空）
    assumptions: list[str] = Field(default_factory=list)
    unverified_conditions: list[str] = Field(default_factory=list)
    risk_flags: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _guard(self) -> "ResourceFeasibilityResult":
        if self.status == "conditional_feasible" and not self.assumptions:
            raise ValueError("conditional_feasible 必须列出所依赖的假设")
        if self.status == "infeasible" and not self.reasons:
            raise ValueError("infeasible 必须给出具体原因")
        return self


class DeploymentCandidate(BaseModel):
    """一个 TP/DP 组合的静态资源评估结果。"""

    model_config = ConfigDict(extra="forbid")

    candidate_id: str
    model_id: str
    hardware_id: str
    server_id: str | None = None
    weight_precision: Precision
    kv_precision: KvPrecision

    tp_size: int
    dp_size: int
    #: gpu_count = tp_size × dp_size（仅适用于本轮"单阶段、共置、Dense"模型）
    gpu_count: int
    nodes_required: int | None = None

    context_length_per_request: int | None = None
    global_concurrency: int | None = None
    concurrency_per_replica: int | None = None

    memory: MemoryEstimate | None = None
    feasibility: ResourceFeasibilityResult

    #: 排序用的数值（不用于性能优劣声明）
    total_gpu_count: int | None = None
    headroom_bytes: int | None = None

    summary: str = ""

    @model_validator(mode="after")
    def _guard(self) -> "DeploymentCandidate":
        if self.gpu_count != self.tp_size * self.dp_size:
            raise ValueError(
                f"gpu_count 必须等于 tp_size × dp_size（{self.tp_size}×{self.dp_size}），"
                f"当前为 {self.gpu_count}"
            )
        if self.feasibility.status == "unsupported" and self.memory is not None:
            raise ValueError("unsupported 的候选不应给出显存估算结果")
        return self
