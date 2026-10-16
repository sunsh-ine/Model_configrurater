"""M14：GPU 硬件规格与服务器拓扑。

要点（任务书 M14）：

* **型号与 SKU 分离**：``product_name``（H100）与 ``sku``（H100 SXM）不是一回事，
  容量/带宽/形态都按 SKU 记录；H100 SXM(80GB) 与 H100 NVL(94GB) 绝不可互相顶替。
* 容量内部统一 **Bytes**；``hbm_capacity_declared`` 保留厂商原始表述，
  ``capacity_normalization`` 说明归一化基准（十进制 GB 还是 GiB）。
* **标称 ≠ 可分配**：``usable_memory_bytes`` 只认实测；没有就留 None。
* 未确认的互联/节点资源**不得**默认为已满足：写进 ``verification_status`` 与
  ``fields_pending_verification``。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from llm_capacity_pricing.schemas.sizing import (
    HardwareCompatibility,
    HardwareSpec,
    ServerProfile,
)

#: 常见归一化基准 -> 每"单位"的字节数
NORMALIZATION_FACTORS: dict[str, int] = {
    "decimal_GB": 10**9,
    "binary_GiB": 1024**3,
}


class HardwareCatalogError(RuntimeError):
    """硬件目录不满足加载前提。"""


def load_catalog(path: str | Path) -> dict[str, Any]:
    p = Path(path)
    if not p.exists():
        raise HardwareCatalogError(f"硬件目录不存在: {p}")
    doc = yaml.safe_load(p.read_text(encoding="utf-8"))
    for key in ("hardware", "servers"):
        if not doc.get(key):
            raise HardwareCatalogError(f"硬件目录缺少 {key} 段")
    ids = [h.get("hardware_id") for h in doc["hardware"]]
    if len(ids) != len(set(ids)):
        raise HardwareCatalogError("hardware_id 不唯一")
    return doc


def _normalize_capacity(declared: str | None, basis: str | None) -> int | None:
    """把 ``"80GB"`` + ``decimal_GB`` 归一化成字节数。依据不明则返回 None。"""
    if not declared or basis not in NORMALIZATION_FACTORS:
        return None
    digits = "".join(ch for ch in declared if ch.isdigit())
    if not digits:
        return None
    return int(digits) * NORMALIZATION_FACTORS[basis]


def build_hardware_spec(entry: dict[str, Any], doc: dict[str, Any]) -> HardwareSpec:
    sources = doc.get("sources") or {}
    src = sources.get(entry.get("source_id") or "", {})
    computed_total = _normalize_capacity(
        entry.get("hbm_capacity_declared"), entry.get("capacity_normalization")
    )
    declared_total = entry.get("total_memory_bytes")
    if declared_total is not None and computed_total is not None and declared_total != computed_total:
        raise HardwareCatalogError(
            f"{entry.get('hardware_id')}: total_memory_bytes({declared_total}) 与 "
            f"{entry.get('hbm_capacity_declared')}/{entry.get('capacity_normalization')} "
            f"归一化结果({computed_total}) 不一致"
        )
    return HardwareSpec(
        hardware_id=entry["hardware_id"],
        vendor=entry["vendor"],
        product_name=entry["product_name"],
        sku=entry.get("sku"),
        form_factor=entry.get("form_factor"),
        architecture=entry.get("architecture"),
        hbm_type=entry.get("hbm_type"),
        hbm_capacity_declared=entry.get("hbm_capacity_declared"),
        capacity_normalization=entry.get("capacity_normalization"),
        total_memory_bytes=declared_total if declared_total is not None else computed_total,
        usable_memory_bytes=entry.get("usable_memory_bytes"),
        usable_memory_status=entry.get("usable_memory_status", "pending_measurement"),
        memory_bandwidth_declared=entry.get("memory_bandwidth_declared"),
        memory_bandwidth_bytes_per_s=entry.get("memory_bandwidth_bytes_per_s"),
        interconnect_within_node=entry.get("interconnect_within_node"),
        tdp_declared=entry.get("tdp_declared"),
        mig_declared=entry.get("mig_declared"),
        supported_weight_precisions=list(entry.get("supported_weight_precisions") or []),
        supported_kv_cache_precisions=list(entry.get("supported_kv_cache_precisions") or []),
        source_id=entry.get("source_id"),
        source_quote=entry.get("source_quote"),
        verification_status=entry.get("verification_status", "pending_verification"),
        supported_in_phase2=bool(entry.get("supported_in_phase2")),
        fields_pending_verification=list(entry.get("fields_pending_verification") or []),
        notes=(entry.get("notes") or "").strip() or None,
    )


def build_server_profile(entry: dict[str, Any]) -> ServerProfile:
    return ServerProfile(
        server_id=entry["server_id"],
        profile_name=entry["profile_name"],
        gpu_hardware_id=entry["gpu_hardware_id"],
        gpus_per_node=entry.get("gpus_per_node"),
        interconnect_declared=entry.get("interconnect_declared"),
        cpu_model=entry.get("cpu_model"),
        host_memory_bytes=entry.get("host_memory_bytes"),
        nic_bandwidth_bytes_per_s=entry.get("nic_bandwidth_bytes_per_s"),
        source_id=entry.get("source_id"),
        source_quote=entry.get("source_quote"),
        verification_status=entry.get("verification_status", "pending_verification"),
        fields_pending_verification=list(entry.get("fields_pending_verification") or []),
        note=entry.get("note"),
    )


def load_hardware(
    path: str | Path,
) -> tuple[list[HardwareSpec], list[ServerProfile], dict[str, Any]]:
    doc = load_catalog(path)
    specs = [build_hardware_spec(entry, doc) for entry in doc["hardware"]]
    servers = [build_server_profile(entry) for entry in doc["servers"]]
    summary = {
        "catalog": str(path),
        "hardware_count": len(specs),
        "server_count": len(servers),
        "supported_in_phase2": [s.hardware_id for s in specs if s.supported_in_phase2],
        "pending_verification": [
            s.hardware_id for s in specs if s.verification_status != "vendor_page_verified"
        ],
        "sources": sorted((doc.get("sources") or {}).keys()),
    }
    return specs, servers, summary


def check_hardware_compatibility(
    *,
    spec: HardwareSpec,
    server: ServerProfile | None,
    gpu_count: int,
    precision: str,
    tp_size: int | None = None,
) -> HardwareCompatibility:
    """硬件与部署需求的兼容性。

    任何"未确认"的条件都进入 ``unverified_conditions``，**不得**当作已满足；
    已确认违反的进入 ``reasons`` 并把状态置为 ``incompatible``。
    """
    reasons: list[str] = []
    unverified: list[str] = []

    if not spec.supported_in_phase2:
        reasons.append(f"hardware_not_supported_in_phase2:{spec.hardware_id}")
    if spec.total_memory_bytes is None:
        unverified.append("gpu_total_memory_bytes_unknown")
    if spec.usable_memory_bytes is None:
        unverified.append("gpu_usable_memory_not_measured")
    if not spec.supported_weight_precisions:
        unverified.append(f"weight_precision_support_unverified:{precision}")
    elif precision not in spec.supported_weight_precisions:
        reasons.append(
            f"weight_precision_not_supported_by_hardware:{precision}"
            f"（硬件已声明支持 {spec.supported_weight_precisions}）"
        )

    if server is None:
        unverified.append("server_profile_not_specified")
    else:
        # ---- 服务器必须与 GPU SKU 匹配（V2.1 F4/P0）----------------------
        # 不匹配时**直接判不可行**：不允许拿另一种卡的节点拓扑继续做判断。
        if server.gpu_hardware_id != spec.hardware_id:
            reasons.append(
                f"server_gpu_sku_mismatch: 服务器 {server.server_id} 面向 "
                f"{server.gpu_hardware_id}，而候选使用 {spec.hardware_id}"
            )
        if server.gpus_per_node is None:
            unverified.append("gpus_per_node_unknown")
        else:
            per_node = server.gpus_per_node
            effective_tp = tp_size if tp_size is not None else gpu_count
            # TP 组跨节点：all-reduce 需要走跨节点互联，本轮不建模 -> 风险
            if effective_tp > per_node:
                unverified.append("tp_group_spans_nodes_interconnect_not_modelled")
            # DP 副本跨节点：副本之间相互独立，但节点级资源与放置需核实
            elif gpu_count > per_node:
                unverified.append("dp_replicas_span_multiple_nodes_placement_unverified")
            if gpu_count > per_node and effective_tp <= per_node:
                # 仍需提醒：总卡数超过单机容量
                unverified.append("total_gpus_exceed_single_node_capacity")
        if server.interconnect_declared is None:
            unverified.append("intra_node_interconnect_unverified")
    if "framework_kernel_support" in spec.fields_pending_verification:
        unverified.append("framework_kernel_support_unverified")

    status = "incompatible" if reasons else ("unknown" if unverified else "compatible")
    return HardwareCompatibility(
        status=status, reasons=reasons, unverified_conditions=sorted(set(unverified))
    )

#: 可用显存的来源性质（V2.1 F2：必须区分实测 / 人工经验 / 测试构造）
UsableMemorySourceKind = str  # "measured" | "operator_estimate" | "synthetic_example"
VALID_USABLE_SOURCE_KINDS: tuple[str, ...] = (
    "measured",
    "operator_estimate",
    "synthetic_example",
)


class UsableMemoryError(ValueError):
    """可用显存取值或来源不合法。"""


def validate_usable_memory(
    spec: HardwareSpec,
    value: int | None,
    *,
    source_kind: str | None,
    source: str | None = None,
) -> None:
    """校验"可用显存"输入（V2.1 F2）。

    * 必须**显式声明来源性质**（实测 / 人工经验 / 测试构造）；
    * 数值不得超过同一硬件记录的标称容量（单位口径不一致时先核实，不能放行）；
    * 实测值必须给出可追溯的测量来源描述。
    """
    if value is None:
        return
    if source_kind not in VALID_USABLE_SOURCE_KINDS:
        raise UsableMemoryError(
            f"可用显存必须声明来源性质，取值为 {VALID_USABLE_SOURCE_KINDS} 之一，"
            f"收到 {source_kind!r}"
        )
    if value <= 0:
        raise UsableMemoryError(f"可用显存必须为正数，收到 {value}")
    if spec.total_memory_bytes is not None and value > spec.total_memory_bytes:
        raise UsableMemoryError(
            f"可用显存 {value} 字节超过 {spec.hardware_id} 的标称容量 "
            f"{spec.total_memory_bytes} 字节（{spec.hbm_capacity_declared}，"
            f"归一化基准 {spec.capacity_normalization}）——"
            "单位口径存在争议时应先核实并统一，不得直接放行"
        )
    if source_kind == "measured" and not source:
        raise UsableMemoryError("来源性质为 measured 时必须给出测量来源描述（设备/时间/命令）")

# ---------------------------------------------------------------------------
# S3：硬件 ID -> 第三阶段签名里的商品名（唯一权威映射）
# ---------------------------------------------------------------------------
#
# 第二阶段用 ``hardware_id``（如 ``nvidia-h100-sxm-80gb``）标识 SKU，
# 第三阶段的 Benchmark 签名用商品名（如 ``NVIDIA H100 SXM``）。两者是同一件事的
# 两种写法，映射必须只有一份 —— 否则新增一张卡时容易只改一处。
#
# 该映射必须与 ``data/hardware/hardware_catalog.yaml`` 的 ``product_name`` 一致，
# 由 tests/test_s_phase_rules.py 的契约测试保证。

SIGNATURE_PRODUCT_NAMES: dict[str, str] = {
    "nvidia-h100-sxm-80gb": "NVIDIA H100 SXM",
    "nvidia-h200-sxm-141gb": "NVIDIA H200 SXM",
    "nvidia-h100-nvl-94gb": "NVIDIA H100 NVL",
    "nvidia-b200-sxm": "NVIDIA B200 SXM",
    "ascend-910c": "Ascend 910C",
}


def product_name_for_hardware_id(hardware_id: str | None) -> str | None:
    """把硬件 ID 映射成签名商品名；未登记的 ID 原样返回（不猜、不编）。"""
    if hardware_id is None:
        return None
    return SIGNATURE_PRODUCT_NAMES.get(hardware_id, hardware_id)

