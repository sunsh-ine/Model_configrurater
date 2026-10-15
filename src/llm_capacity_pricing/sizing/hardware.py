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

    if server is None:
        unverified.append("server_profile_not_specified")
    else:
        if server.gpus_per_node is None:
            unverified.append("gpus_per_node_unknown")
        elif gpu_count > server.gpus_per_node:
            # 超出单机卡数时需要跨节点互联，本轮不做跨节点带宽建模
            unverified.append("requires_multi_node_interconnect_not_modelled")
        if server.interconnect_declared is None:
            unverified.append("intra_node_interconnect_unverified")
    if "framework_kernel_support" in spec.fields_pending_verification:
        unverified.append("framework_kernel_support_unverified")

    status = "incompatible" if reasons else ("unknown" if unverified else "compatible")
    return HardwareCompatibility(
        status=status, reasons=reasons, unverified_conditions=sorted(set(unverified))
    )
