"""M17：TP/DP 部署候选生成与静态可行性筛选。

枚举规则（本轮限定：**单阶段、共置、Dense** 部署）：

    gpu_count = tp_size × dp_size

* ``tp_size`` 取自受支持集合（默认 1/2/4/8）；
* ``dp_size`` 表示**模型副本数**：每个副本各持一套完整 TP 组权重，
  并发按副本分摊，**不是**对单条请求切分权重（任务书 M17 明确要求区分）；
* 卡数、拓扑、硬件兼容性、TP 可切分性都要检查；
* 输出四类状态：``infeasible`` / ``conditional_feasible`` / ``insufficient_information`` / ``unsupported``。

判定纪律：

* 单卡可用显存**只认实测值**；没有实测就必须由调用方给出**带来源的假设**，
  否则状态为 ``insufficient_information``（不猜）。
* 运行时未知项不得当作 0：只要存在 ``origin=unknown`` 的运行时项且调用方没有
  给出合并预留，判定不能收敛，同样返回 ``insufficient_information``。
* ``conditional_feasible`` 必须列出所依赖的假设；任何结论都不代表"能启动"或"满足 SLA"。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from llm_capacity_pricing.schemas.sizing import (
    DeploymentCandidate,
    HardwareCompatibility,
    HardwareSpec,
    MemoryEstimate,
    ModelSpec,
    Precision,
    ResourceFeasibilityResult,
    RuntimeReserve,
    ServerProfile,
)
from llm_capacity_pricing.sizing.hardware import (
    check_hardware_compatibility,
    validate_usable_memory,
)
from llm_capacity_pricing.sizing.kv_cache import default_runtime_items, estimate_kv_cache
from llm_capacity_pricing.sizing.weights import estimate_weight_memory
from llm_capacity_pricing.schemas.sizing import precision_supported

#: 受支持的 TP 集合（本轮）
SUPPORTED_TP_SIZES: tuple[int, ...] = (1, 2, 4, 8)
#: 受支持的 DP 副本数（本轮）
SUPPORTED_DP_SIZES: tuple[int, ...] = (1, 2, 4, 8)

#: 单卡可用显存的假设来源标记（用于必须写明的假设项）
USABLE_MEMORY_ASSUMED_FLAG = "usable_memory_assumed_not_measured"


#: 可用显存来源性质（V2.1 F2/P0）：必须区分实测 / 人工经验 / 测试构造
USABLE_SOURCE_KINDS: tuple[str, ...] = ("measured", "operator_estimate", "synthetic_example")


class AssumptionError(ValueError):
    """规划假设本身不合法（范围越界 / 来源缺失 / 上下文扩展未验证）。"""


@dataclass
class PlanningAssumptions:
    """规划假设。**所有**影响结论的外部输入都要写进这里并标明来源。"""

    #: 单卡实测可用显存（优先）
    per_gpu_usable_bytes: int | None = None
    #: 或：按标称容量的比例（假设值，需给出来源）
    usable_fraction_of_nominal: Decimal | None = None
    usable_memory_source: str | None = None
    #: **来源性质**：measured（真实设备测量）/ operator_estimate（人工经验值）/
    #: synthetic_example（为测试算法构造的示例值）。给出可用显存时**必须**声明。
    usable_memory_source_kind: str | None = None
    #: 经过验证的上下文扩展上限（超过模型 max_position_embeddings 时才需要）
    max_context_override: int | None = None
    max_context_override_source: str | None = None
    #: 运行时预留（按项给出；未给的项保持 unknown）
    runtime_items: dict[str, dict[str, Any]] = field(default_factory=dict)
    #: 或：合并的运行时预留比例（相对可用显存），作为假设值参与判定
    runtime_reserve_fraction: Decimal | None = None
    runtime_reserve_source: str | None = None
    #: Paged KV 块大小
    block_size_tokens: int | None = None
    #: 是否允许把未知的 KV 头分布按"复制"这一最坏情形计算
    assume_kv_head_replication: bool = True
    #: 并发口径
    global_concurrency: int | None = None
    context_length_per_request: int | None = None
    tp_sizes: tuple[int, ...] = SUPPORTED_TP_SIZES
    dp_sizes: tuple[int, ...] = SUPPORTED_DP_SIZES
    notes: list[str] = field(default_factory=list)


def validate_assumptions(assumptions: "PlanningAssumptions", hardware: HardwareSpec) -> None:
    """校验假设的合法范围（V2.1 F6）。越界一律抛错，不静默夹取。"""
    if assumptions.per_gpu_usable_bytes is not None and assumptions.usable_fraction_of_nominal is not None:
        raise AssumptionError("可用显存请只给一种口径：显式字节数或标称比例")
    if assumptions.usable_memory_source_kind is not None:
        if assumptions.usable_memory_source_kind not in USABLE_SOURCE_KINDS:
            raise AssumptionError(
                f"usable_memory_source_kind 必须是 {USABLE_SOURCE_KINDS} 之一，"
                f"收到 {assumptions.usable_memory_source_kind!r}"
            )
    if (
        assumptions.per_gpu_usable_bytes is not None or assumptions.usable_fraction_of_nominal is not None
    ) and assumptions.usable_memory_source_kind is None:
        raise AssumptionError(
            "给出可用显存时必须声明来源性质（measured / operator_estimate / "
            "synthetic_example），避免把示例值当成实测数据"
        )
    frac = assumptions.usable_fraction_of_nominal
    if frac is not None and not (Decimal(0) < frac <= Decimal(1)):
        raise AssumptionError(f"可用显存比例必须落在 (0, 1]，收到 {frac}")
    reserve = assumptions.runtime_reserve_fraction
    if reserve is not None and not (Decimal(0) <= reserve < Decimal(1)):
        raise AssumptionError(f"运行时预留比例必须落在 [0, 1)，收到 {reserve}")
    if assumptions.global_concurrency is not None and assumptions.global_concurrency < 1:
        raise AssumptionError(f"全局并发必须 ≥ 1，收到 {assumptions.global_concurrency}")
    if assumptions.context_length_per_request is not None and assumptions.context_length_per_request < 1:
        raise AssumptionError(
            f"上下文长度必须 ≥ 1，收到 {assumptions.context_length_per_request}"
        )
    if assumptions.block_size_tokens is not None and assumptions.block_size_tokens < 1:
        raise AssumptionError(f"block size 必须 ≥ 1，收到 {assumptions.block_size_tokens}")
    for name, sizes in (("tp_sizes", assumptions.tp_sizes), ("dp_sizes", assumptions.dp_sizes)):
        if not sizes or any(s < 1 for s in sizes):
            raise AssumptionError(f"{name} 必须是非空的正整数集合，收到 {sizes}")
    if assumptions.max_context_override is not None:
        if assumptions.max_context_override < 1:
            raise AssumptionError("max_context_override 必须 ≥ 1")
        if not assumptions.max_context_override_source:
            raise AssumptionError(
                "使用 max_context_override 必须给出来源（经过验证的上下文扩展配置）"
            )


def resolve_usable_memory(
    hardware: HardwareSpec, assumptions: PlanningAssumptions
) -> tuple[int | None, str, list[str]]:
    """确定单卡可用显存。返回 ``(bytes, 来源说明, 假设列表)``。"""
    if hardware.usable_memory_bytes is not None:
        return (
            hardware.usable_memory_bytes,
            f"实测值（hardware.usable_memory_bytes，来源 {hardware.source_id or 'user'}）",
            [],
        )
    if assumptions.per_gpu_usable_bytes is not None:
        validate_usable_memory(
            hardware,
            assumptions.per_gpu_usable_bytes,
            source_kind=assumptions.usable_memory_source_kind,
            source=assumptions.usable_memory_source,
        )
        kind = assumptions.usable_memory_source_kind
        return (
            assumptions.per_gpu_usable_bytes,
            f"调用方提供的显式值（来源性质：{kind}；来源："
            f"{assumptions.usable_memory_source or '未注明'}）",
            [
                f"{USABLE_MEMORY_ASSUMED_FLAG}: 单卡可用显存取自调用方输入 "
                f"{assumptions.per_gpu_usable_bytes} 字节，来源性质=**{kind}**，非硬件实测字段"
            ],
        )
    if (
        assumptions.usable_fraction_of_nominal is not None
        and hardware.total_memory_bytes is not None
    ):
        value = int(Decimal(hardware.total_memory_bytes) * assumptions.usable_fraction_of_nominal)
        validate_usable_memory(
            hardware,
            value,
            source_kind=assumptions.usable_memory_source_kind,
            source=assumptions.usable_memory_source,
        )
        kind = assumptions.usable_memory_source_kind
        return (
            value,
            f"按标称容量 {hardware.hbm_capacity_declared} 的 "
            f"{assumptions.usable_fraction_of_nominal} 折算（来源性质：{kind}；来源："
            f"{assumptions.usable_memory_source or '未注明'}）",
            [
                f"{USABLE_MEMORY_ASSUMED_FLAG}: 可用显存 = 标称 × "
                f"{assumptions.usable_fraction_of_nominal}，来源性质=**{kind}**，非实测"
            ],
        )
    return None, "无实测值且未提供假设", []


def estimate_memory(
    model: ModelSpec,
    hardware: HardwareSpec,
    *,
    tp_size: int,
    dp_size: int = 1,
    weight_precision: Precision | None = None,
    kv_precision: str = "bf16",
    assumptions: PlanningAssumptions,
) -> MemoryEstimate:
    """合成单副本显存：权重（每 Rank）+ KV（每 Rank）+ 运行时项。"""
    precision = weight_precision or model.deployment_precision
    weights = estimate_weight_memory(model, tp_size=tp_size, precision=precision)

    usable, usable_source, usable_assumptions = resolve_usable_memory(hardware, assumptions)

    # 运行时预留：先按项取用户提供值；若整块给了比例，则折算成合并预留项
    runtime_items: list[RuntimeReserve] = default_runtime_items(provided=assumptions.runtime_items)
    assumptions_list = list(usable_assumptions)
    if assumptions.runtime_reserve_fraction is not None:
        if usable is None:
            assumptions_list.append(
                "提供了运行时预留比例，但可用显存未知，无法折算为字节数"
            )
        else:
            reserve = int(Decimal(usable) * assumptions.runtime_reserve_fraction)
            runtime_items.append(
                RuntimeReserve(
                    item="combined_runtime_reserve",
                    bytes_estimate=reserve,
                    origin="assumed",
                    source=assumptions.runtime_reserve_source,
                    assumption=(
                        f"合并运行时预留 = 可用显存 × {assumptions.runtime_reserve_fraction}"
                        f"（来源：{assumptions.runtime_reserve_source or '未注明'}）"
                    ),
                )
            )
            assumptions_list.append(
                f"运行时开销采用**合并预留**方式：可用显存 × {assumptions.runtime_reserve_fraction}"
            )

    known_unknowns = [
        f"{name}: {description}" for name, description in _unknown_runtime(runtime_items)
    ]

    # KV 预算：可用显存 - 每卡权重 - 已知运行时项（未知项不扣，故 KV 预算是乐观上界）
    kv_budget: int | None = None
    if usable is not None and weights.per_rank_bytes is not None:
        known_runtime = sum(
            r.bytes_estimate for r in runtime_items if r.bytes_estimate is not None
        )
        kv_budget = max(usable - weights.per_rank_bytes - known_runtime, 0)

    kv = estimate_kv_cache(
        model,
        tp_size=tp_size,
        dp_size=dp_size,
        kv_precision=kv_precision,
        global_concurrency=assumptions.global_concurrency,
        context_length_per_request=assumptions.context_length_per_request,
        block_size_tokens=assumptions.block_size_tokens,
        kv_budget_bytes=kv_budget,
    )

    total_required: int | None = None
    headroom: int | None = None
    if weights.per_rank_bytes is not None and kv.physical_bytes is not None:
        runtime_known = sum(r.bytes_estimate for r in runtime_items if r.bytes_estimate is not None)
        total_required = weights.per_rank_bytes + kv.physical_bytes + runtime_known
        if usable is not None:
            headroom = usable - total_required

    flags: list[str] = []
    if kv.physical_bytes is None:
        flags.append("kv_bytes_unknown_so_total_is_partial")
    if known_unknowns:
        flags.append("runtime_unknown_items_present_not_treated_as_zero")

    return MemoryEstimate(
        model_id=model.model_id,
        hardware_id=hardware.hardware_id,
        tp_size=tp_size,
        dp_size=dp_size,
        weight_precision=precision,
        kv_precision=kv_precision,  # type: ignore[arg-type]
        weight=weights,
        kv=kv,
        runtime_items=runtime_items,
        per_gpu_usable_bytes=usable,
        total_required_bytes=total_required,
        headroom_bytes=headroom,
        known_unknowns=known_unknowns,
        assumptions=assumptions_list + [f"单卡可用显存来源：{usable_source}"],
        quality_flags=sorted(set(flags)),
    )


def _unknown_runtime(items: list[RuntimeReserve]) -> list[tuple[str, str]]:
    return [(i.item, i.assumption or "未提供预留值") for i in items if i.origin == "unknown"]


def plan_deployment(
    model: ModelSpec,
    hardware: HardwareSpec,
    *,
    assumptions: PlanningAssumptions,
    server: ServerProfile | None = None,
    weight_precision: Precision | None = None,
    kv_precision: str = "bf16",
) -> list[DeploymentCandidate]:
    """枚举 TP/DP 组合并给出静态可行性。返回稳定排序的候选列表。"""
    precision = weight_precision or model.deployment_precision
    validate_assumptions(assumptions, hardware)
    candidates: list[DeploymentCandidate] = []

    # 精度门禁（V2.1 F6）：本阶段只支持每元素字节数确定的 bf16/fp16/fp32
    unsupported_precision: list[str] = []
    if not precision_supported(precision, kind="weight"):
        unsupported_precision.append(f"weight_precision_not_supported_in_phase2:{precision}")
    if not precision_supported(kv_precision, kind="kv"):
        unsupported_precision.append(f"kv_precision_not_supported_in_phase2:{kv_precision}")
    if unsupported_precision:
        for tp in assumptions.tp_sizes:
            for dp in assumptions.dp_sizes:
                candidates.append(
                    _candidate(
                        model, hardware, server, tp=tp, dp=dp, precision=precision,
                        kv_precision=kv_precision, assumptions=assumptions, memory=None,
                        feasibility=ResourceFeasibilityResult(
                            status="unsupported",
                            reasons=unsupported_precision
                            + ["fp8/int8/int4 等需要量化标定与位打包，本阶段无专用显存模型"],
                            risk_flags=["unsupported_precision"],
                        ),
                    )
                )
        return _sorted(candidates)

    # 模型层面就不支持 -> 所有组合 unsupported（不生成显存结果）
    if model.status == "unsupported_architecture":
        for tp in assumptions.tp_sizes:
            for dp in assumptions.dp_sizes:
                candidates.append(
                    _candidate(
                        model,
                        hardware,
                        server,
                        tp=tp,
                        dp=dp,
                        precision=precision,
                        kv_precision=kv_precision,
                        assumptions=assumptions,
                        memory=None,
                        feasibility=ResourceFeasibilityResult(
                            status="unsupported",
                            reasons=[
                                "unsupported_architecture: 模型结构未在本项目核实，"
                                "不按标准 Dense Transformer 估算"
                            ],
                            risk_flags=["model_architecture_unsupported"],
                        ),
                    )
                )
        return _sorted(candidates)

    for tp in assumptions.tp_sizes:
        for dp in assumptions.dp_sizes:
            gpu_count = tp * dp
            # V2.1 F1/P0：兼容性必须**按候选实际卡数**判定。
            # 之前用 max(tp)×max(dp) 算一次并复用，会让 TP1×DP1 错误继承 64 卡的跨节点风险。
            compat = check_hardware_compatibility(
                spec=hardware,
                server=server,
                gpu_count=gpu_count,
                precision=precision,
                tp_size=tp,
            )
            feasibility = _assess(
                model,
                hardware,
                server,
                compat,
                tp=tp,
                dp=dp,
                precision=precision,
                assumptions=assumptions,
            )
            memory = None
            if feasibility.status != "unsupported":
                memory = estimate_memory(
                    model,
                    hardware,
                    tp_size=tp,
                    dp_size=dp,
                    weight_precision=precision,
                    kv_precision=kv_precision,
                    assumptions=assumptions,
                )
                feasibility = _apply_memory_rules(feasibility, memory, assumptions)
            candidates.append(
                _candidate(
                    model,
                    hardware,
                    server,
                    tp=tp,
                    dp=dp,
                    precision=precision,
                    kv_precision=kv_precision,
                    assumptions=assumptions,
                    memory=memory,
                    feasibility=feasibility,
                )
            )
    return _sorted(candidates)


def _assess(
    model: ModelSpec,
    hardware: HardwareSpec,
    server: ServerProfile | None,
    compat: HardwareCompatibility,
    *,
    tp: int,
    dp: int,
    precision: Precision,
    assumptions: PlanningAssumptions,
) -> ResourceFeasibilityResult:
    """结构与硬件层面的前置判定（尚未看显存数字）。"""
    reasons: list[str] = []
    unverified: list[str] = list(compat.unverified_conditions)
    risks: list[str] = []
    assumption_notes: list[str] = []

    if compat.status == "incompatible":
        return ResourceFeasibilityResult(
            status="infeasible",
            reasons=list(compat.reasons),
            unverified_conditions=unverified,
            risk_flags=["hardware_incompatible"],
        )

    # TP 可切分性：注意力头必须能被 TP 整除（张量切分的基本前提）
    if model.num_attention_heads is not None and model.num_attention_heads % tp != 0:
        reasons.append(
            f"attention_heads({model.num_attention_heads}) 不能被 TP({tp}) 整除，"
            "无法做均等的张量切分"
        )
    if model.num_key_value_heads is not None and model.num_key_value_heads < tp:
        risks.append("kv_heads_fewer_than_tp_requires_kv_replication")
        assumption_notes.append(
            "KV 头数少于 TP：按 KV 复制估算（每 Rank 保留全部 KV 头），"
            "实际行为取决于推理框架实现"
        )
    elif model.num_key_value_heads is not None and model.num_key_value_heads % tp != 0:
        unverified.append("kv_head_split_not_divisible_by_tp")
        assumption_notes.append("KV 头不能被 TP 整除：按复制口径估算，需框架侧确认")

    # 上下文长度必须落在模型已确认的支持范围内（V2.1 F3/P1）
    ctx = assumptions.context_length_per_request
    if ctx is not None:
        limit = assumptions.max_context_override or model.max_position_embeddings
        if limit is None:
            unverified.append("model_max_context_unknown")
        elif ctx > limit:
            reasons.append(
                f"context_length_exceeds_model_limit: 请求 {ctx} > "
                f"{'已验证扩展上限' if assumptions.max_context_override else '模型 max_position_embeddings'} "
                f"{limit}；如需超长上下文，请提供经过验证的上下文扩展配置"
            )
    if model.head_dim is None:
        unverified.append("head_dim_unknown")
    if server is None:
        risks.append("server_profile_unspecified_node_topology_unverified")
    elif server.gpus_per_node is not None and tp > server.gpus_per_node:
        risks.append("tp_spans_multiple_nodes_interconnect_not_modelled")

    if reasons:
        return ResourceFeasibilityResult(
            status="infeasible",
            reasons=reasons,
            assumptions=assumption_notes,
            unverified_conditions=sorted(set(unverified)),
            risk_flags=sorted(set(risks)),
        )
    if unverified or risks:
        return ResourceFeasibilityResult(
            status="conditional_feasible" if assumption_notes else "insufficient_information",
            reasons=[] if assumption_notes else ["存在未确认的前置条件，且未给出任何假设"],
            assumptions=assumption_notes,
            unverified_conditions=sorted(set(unverified)),
            risk_flags=sorted(set(risks)),
        )
    return ResourceFeasibilityResult(
        status="conditional_feasible",
        reasons=[],
        assumptions=["结构与硬件前置条件均明确（注意：显存与运行时仍需按 §2 的假设判定）"],
    )


def _apply_memory_rules(
    result: ResourceFeasibilityResult,
    memory: MemoryEstimate,
    assumptions: PlanningAssumptions,
) -> ResourceFeasibilityResult:
    """把显存数字纳入判定。

    顺序纪律（Fail-Closed）：**已在结构/硬件层面判定的结论不可被显存规则改写**——
    例如"注意力头数不能被 TP 整除"是硬约束，不能因为显存放得下就变成"条件可行"。
    显存规则只能在"尚未判定为不可行/不支持"的前提下，把结论收敛到
    `conditional_feasible` / `infeasible` / `insufficient_information`。
    """
    if result.status in ("infeasible", "unsupported"):
        return result
    reasons = list(result.reasons)
    unverified = list(result.unverified_conditions)
    risks = list(result.risk_flags)
    assumptions_out = list(result.assumptions)

    if memory.per_gpu_usable_bytes is None:
        unverified.append("per_gpu_usable_memory_unknown_no_measurement_no_assumption")
        return ResourceFeasibilityResult(
            status="insufficient_information",
            reasons=["单卡可用显存既无实测值也未提供带来源的假设，无法判断是否放得下"],
            assumptions=assumptions_out,
            unverified_conditions=sorted(set(unverified)),
            risk_flags=sorted(set(risks)),
        )

    if memory.weight.per_rank_bytes is None or memory.kv.physical_bytes is None:
        unverified.append("per_rank_weight_or_kv_bytes_unknown")
        return ResourceFeasibilityResult(
            status="insufficient_information",
            reasons=["权重或 KV 的每卡字节数不可得（结构/负载信息不全）"],
            assumptions=assumptions_out,
            unverified_conditions=sorted(set(unverified)),
            risk_flags=sorted(set(risks)),
        )

    if memory.has_unknown_runtime and assumptions.runtime_reserve_fraction is None:
        unverified.append("runtime_overhead_unknown_no_reserve_assumption")
        return ResourceFeasibilityResult(
            status="insufficient_information",
            reasons=[
                "运行时开销（workspace / CUDA Graph / 激活峰值等）未提供，"
                "且不允许当作 0：请提供实测或带来源的预留假设"
            ],
            assumptions=assumptions_out,
            unverified_conditions=sorted(set(unverified)),
            risk_flags=sorted(set(risks)),
        )

    assert memory.total_required_bytes is not None
    if memory.total_required_bytes > memory.per_gpu_usable_bytes:
        over = memory.total_required_bytes - memory.per_gpu_usable_bytes
        return ResourceFeasibilityResult(
            status="infeasible",
            # 状态变了，原因必须重写：只保留"为什么放不下"这一条
            reasons=[
                f"每卡需求 {memory.total_required_bytes} 字节 > 可用 "
                f"{memory.per_gpu_usable_bytes} 字节（超出 {over} 字节）"
            ],
            assumptions=assumptions_out,
            unverified_conditions=sorted(set(unverified)),
            risk_flags=sorted(set(risks)),
        )

    assumptions_out.append(
        f"静态余量 {memory.headroom_bytes} 字节；结论仅覆盖静态资源，"
        "不代表可成功启动或满足 SLA"
    )
    return ResourceFeasibilityResult(
        status="conditional_feasible",
        # V2.1 F5/P1：结论已是"条件可行"时，不得保留"未给出任何假设"这类
        # 前后矛盾的原因描述；只保留与当前状态一致的原因。
        reasons=[],
        assumptions=assumptions_out,
        unverified_conditions=sorted(set(unverified)),
        risk_flags=sorted(set(risks)),
    )


def _candidate(
    model: ModelSpec,
    hardware: HardwareSpec,
    server: ServerProfile | None,
    *,
    tp: int,
    dp: int,
    precision: Precision,
    kv_precision: str,
    assumptions: PlanningAssumptions,
    memory: MemoryEstimate | None,
    feasibility: ResourceFeasibilityResult,
) -> DeploymentCandidate:
    per_replica_concurrency = None
    if assumptions.global_concurrency is not None and dp > 0:
        per_replica_concurrency = -(-assumptions.global_concurrency // dp)
    nodes_required = None
    if server is not None and server.gpus_per_node:
        nodes_required = -(-(tp * dp) // server.gpus_per_node)
    status_text = {
        "infeasible": "不可行",
        "conditional_feasible": "条件可行",
        "insufficient_information": "信息不足",
        "unsupported": "不支持",
    }[feasibility.status]
    summary = (
        f"{model.model_id} × {hardware.product_name}：TP{tp} × DP{dp} = {tp*dp} 卡 → {status_text}"
    )
    return DeploymentCandidate(
        candidate_id=f"{model.model_id}__{hardware.hardware_id}__tp{tp}_dp{dp}_{precision}",
        model_id=model.model_id,
        hardware_id=hardware.hardware_id,
        server_id=server.server_id if server else None,
        weight_precision=precision,
        kv_precision=kv_precision,  # type: ignore[arg-type]
        tp_size=tp,
        dp_size=dp,
        gpu_count=tp * dp,
        nodes_required=nodes_required,
        context_length_per_request=assumptions.context_length_per_request,
        global_concurrency=assumptions.global_concurrency,
        concurrency_per_replica=per_replica_concurrency,
        memory=memory,
        feasibility=feasibility,
        total_gpu_count=tp * dp,
        headroom_bytes=memory.headroom_bytes if memory else None,
        summary=summary,
    )


def _sorted(candidates: list[DeploymentCandidate]) -> list[DeploymentCandidate]:
    """稳定排序：先按卡数升序，再按余量降序（仅排序，不声明性能优劣）。"""
    order = {"conditional_feasible": 0, "insufficient_information": 1, "infeasible": 2, "unsupported": 3}
    return sorted(
        candidates,
        key=lambda c: (
            order[c.feasibility.status],
            c.gpu_count,
            -(c.headroom_bytes or 0),
            c.tp_size,
            c.dp_size,
        ),
    )
