"""部署效能记录（BenchmarkRecord）。

设计原则（任务书 §3.4）：
* 所有测量字段可空，且**绝不允许用 0 作为"未知"的默认值**；
* 同名指标在不同 benchmark 版本口径不同，必须保留 ``metric_definitions``
  与 ``format_version``；
* 缺失字段名进入 ``missing_fields``，语义不确定的进入 ``quality_flags``。
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

PerformanceSourceKind = Literal[
    "internal_excel",
    "official_benchmark",
    "third_party",
    "internal_measurement",
    "synthetic_fixture",
]

VerificationStatus = Literal[
    "unverified_parse",  # 仅由策略字符串解析而来，未核实
    "partial_evidence",  # 配置/结果不全，可入库不可作为完整对比案例
    "verified",  # 来源、配置、结果均可追溯
    "synthetic_fixture",  # 人工构造，仅用于解析器测试，不得进入性能数据库
]

CacheMetricSemantics = Literal["unknown", "physical_kv_hit_rate", "billable_cached_input_ratio"]

DeploymentMode = Literal["aggregated", "prefill_decode_disaggregated", "unknown"]

#: Excel 列 -> 规范字段名的固定映射（来自任务书 §2 已核实结构）
EXCEL_COLUMN_MAP: dict[str, str] = {
    "A": "hardware_model",
    "B": "model_name",
    "C": "deployment_gpu_count",
    "D": "configured_concurrency",
    "E": "single_user_output_tps_p90",
    "F": "input_tps_per_gpu",
    "G": "output_tps_per_gpu",
    "H": "cache_hit_rate_raw",
    "I": "legacy_input_revenue_10k_per_gpu_year",
    "J": "legacy_output_revenue_10k_per_gpu_year",
    "K": "legacy_total_revenue_10k_per_gpu_year",
    "L": "legacy_cost_10k_per_gpu_year",
    "M": "legacy_revenue_cost_ratio",
    "N": "deployment_strategy_raw",
    "O": "data_source",
}


class BenchmarkRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # ---- 必需字段 -------------------------------------------------------
    record_id: str
    model_name: str
    hardware_model: str
    deployment_gpu_count: int
    evidence_id: str
    performance_source_kind: PerformanceSourceKind

    # ---- 模型 / 框架 / 硬件（Excel 均未提供 -> null）-------------------
    model_revision: str | None = None
    model_precision: str | None = None
    framework: str | None = None
    framework_version: str | None = None
    driver_version: str | None = None
    hardware_sku: str | None = None
    node_count: int | None = None
    gpus_per_node: int | None = None

    # ---- 并行 / 拓扑（Excel 只给出未验证的策略字符串）------------------
    tp_size: int | None = None
    dp_size: int | None = None
    ep_size: int | None = None
    pp_size: int | None = None
    prefill_gpu_count: int | None = None
    decode_gpu_count: int | None = None
    deployment_mode: DeploymentMode = "unknown"
    deployment_strategy_raw: str | None = None
    deployment_strategy_parsed: dict[str, Any] | None = None

    # ---- 负载 -----------------------------------------------------------
    configured_concurrency: int | None = None
    mean_observed_concurrency: Decimal | None = None
    request_rate_rps: Decimal | None = None
    num_requests: int | None = None
    input_tokens_target: int | None = None
    output_tokens_target: int | None = None
    input_length_distribution: str | None = None
    output_length_distribution: str | None = None
    dataset_name: str | None = None
    dataset_digest: str | None = None

    # ---- 吞吐 -----------------------------------------------------------
    input_tps_per_gpu: Decimal | None = None
    output_tps_per_gpu: Decimal | None = None
    input_tps_cluster: Decimal | None = None
    output_tps_cluster: Decimal | None = None
    input_tps_cluster_basis: str | None = None
    single_user_output_tps_p90: Decimal | None = None

    # ---- 时延 / 可靠性 --------------------------------------------------
    ttft_mean_ms: Decimal | None = None
    ttft_p99_ms: Decimal | None = None
    tpot_mean_ms: Decimal | None = None
    tpot_p99_ms: Decimal | None = None
    itl_p99_ms: Decimal | None = None
    e2e_p99_ms: Decimal | None = None
    failure_rate: Decimal | None = None
    metric_definitions: dict[str, str] = Field(default_factory=dict)
    success_count: int | None = None
    duration_s: Decimal | None = None

    # ---- 缓存口径 -------------------------------------------------------
    cache_hit_rate_raw: Decimal | None = None
    physical_cache_hit_rate: Decimal | None = None
    billable_cached_input_ratio: Decimal | None = None
    cache_metric_semantics: CacheMetricSemantics = "unknown"
    legacy_proxy_for_billable_hit_rate: bool = False

    # ---- 旧表原始值（只读留档，不覆盖）----------------------------------
    legacy_input_revenue_10k_per_gpu_year: Decimal | None = None
    legacy_output_revenue_10k_per_gpu_year: Decimal | None = None
    legacy_total_revenue_10k_per_gpu_year: Decimal | None = None
    legacy_cost_10k_per_gpu_year: Decimal | None = None
    legacy_revenue_cost_ratio: Decimal | None = None

    # ---- 溯源与质量 -----------------------------------------------------
    format_version: str | None = None
    raw_metrics: dict[str, Any] = Field(default_factory=dict)
    missing_fields: list[str] = Field(default_factory=list)
    quality_flags: list[str] = Field(default_factory=list)
    verification_status: VerificationStatus = "unverified_parse"

    @model_validator(mode="after")
    def _validate(self) -> "BenchmarkRecord":
        if self.deployment_gpu_count is None or self.deployment_gpu_count <= 0:
            raise ValueError("deployment_gpu_count 必须为正整数")
        if self.legacy_proxy_for_billable_hit_rate and self.cache_metric_semantics == "unknown":
            # 显式代理标记必须伴随意图声明，避免被误读为已核实的计费口径
            if "legacy_hit_rate_used_as_billing_proxy" not in self.quality_flags:
                raise ValueError(
                    "legacy_proxy_for_billable_hit_rate=True 时必须设置 "
                    "cache_metric_semantics 或 quality_flags 中的 legacy_hit_rate_used_as_billing_proxy"
                )
        return self

    # -- 便捷方法 ---------------------------------------------------------
    def derive_cluster_throughput(self) -> None:
        """按**同一配置**的 N 卡归一化还原集群吞吐。

        只在原 Benchmark 配置下做归一化，标记 ``derived_from_per_gpu``；
        绝不用于外推扩卡 / 改 TP / 改 PD 后的吞吐（任务书 §4）。
        """
        if self.input_tps_per_gpu is not None:
            self.input_tps_cluster = self.input_tps_per_gpu * self.deployment_gpu_count
            self.input_tps_cluster_basis = (
                f"derived_from_per_gpu: input_tps_per_gpu({self.input_tps_per_gpu}) "
                f"* deployment_gpu_count({self.deployment_gpu_count}); "
                "仅还原同一 Benchmark 配置，禁止跨 GPU/TP 外推"
            )
        if self.output_tps_per_gpu is not None:
            self.output_tps_cluster = self.output_tps_per_gpu * self.deployment_gpu_count

    def note_missing(self, *fields: str) -> None:
        for f in fields:
            if f not in self.missing_fields:
                self.missing_fields.append(f)
