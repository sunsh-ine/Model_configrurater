"""M20：性能实测数据管理。

把"一次完整压测"建模为 :class:`BenchmarkRun`（运行参数 + 签名 + 聚合指标 +
可选逐请求明细 + 原始文件与哈希），而不是"一行吞吐加一个 P99"。

关键纪律：

* **口径必须随数据走**：每个 P99 都要有对应的 ``metric_definition_id``；
  缺失的 P99 保持 ``None``，**不得**由均值推算；
* **指标来源侧分离**：客户端实测与服务端指标分字段存放，不混算；
* **配置并发 ≠ 观测并发**：两者分别保存（任务书禁止项）；
* **两类负载不混**：固定长度标准测试与多轮/前缀复用测试用 ``load_profile_kind`` 区分，
  不允许进入同一条性能曲线；
* **一致性校验**：吞吐 ≈ 累计 token / 时长、成功数 ≤ 请求数、失败率与计数一致；
  不一致只记录，不修改原始值。
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from llm_capacity_pricing.schemas.benchmark import BenchmarkRecord
from llm_capacity_pricing.schemas.official import SglangNumericCandidate
from llm_capacity_pricing.schemas.performance import (
    PRODUCTION_TRUST_LEVELS,
    BenchmarkRun,
    BenchmarkSignature,
    DataTrust,
    LoadProfileKind,
    PerformanceProfile,
    RequestMetric,
    WorkloadSignature,
)
from llm_capacity_pricing.perf.benchmark_matcher import (
    normalize_name,
    signature_from_numeric_candidate,
    signature_from_record,
)

#: 由吞吐与累计 token / 时长比较的容差（与第一阶段口径一致）
THROUGHPUT_TOLERANCE = Decimal("1e-3")

#: ``performance_source_kind`` -> 数据可信等级（V3.1 R3）。
#: **显式映射**：未知来源一律 ``unknown``，绝不默认成"内部实测"，
#: 否则外部 Benchmark 会被错误升级为可支撑 SLA 的等级。
SOURCE_KIND_TO_TRUST: dict[str, DataTrust] = {
    "internal_measurement": "measured_internal",
    "official_benchmark": "measured_vendor_published",
    "third_party": "measured_third_party",
    "internal_excel": "historical_external_no_latency",
    "synthetic_fixture": "synthetic_fixture",
}


def trust_for_source_kind(source_kind: str | None) -> tuple[DataTrust, str | None]:
    """返回 ``(可信等级, 警告)``；未知来源返回 ``unknown`` 并给出警告。"""
    if not source_kind:
        return "unknown", "performance_source_kind_missing:treat_as_unknown_trust"
    trust = SOURCE_KIND_TO_TRUST.get(source_kind)
    if trust is None:
        return "unknown", f"unmapped_performance_source_kind:{source_kind}"
    return trust, None


def sha256_file(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _consistency(profile: PerformanceProfile) -> None:
    """就地写入一致性检查结论（不改动原始数值）。"""
    notes = list(profile.consistency_notes)
    if profile.success_count is not None and profile.num_requests is not None:
        failed = profile.num_requests - profile.success_count
        if profile.failed_count is None:
            profile.failed_count = failed
        elif profile.failed_count != failed:
            notes.append(
                f"失败数不一致：记录 {profile.failed_count}，由请求数-成功数得 {failed}"
            )
        if profile.error_rate is None and profile.num_requests:
            profile.error_rate = (
                Decimal(failed) / Decimal(profile.num_requests)
            ).quantize(Decimal("0.000001"))
    if profile.tpot_p99_ms is not None and profile.itl_p99_ms is not None:
        if profile.tpot_p99_ms == profile.itl_p99_ms:
            notes.append("TPOT P99 与 ITL P99 取值相同：已标注待人工确认（两者口径不同）")
            if "tpot_equals_itl_unverified" not in profile.quality_flags:
                profile.quality_flags.append("tpot_equals_itl_unverified")
    profile.consistency_notes = notes


def check_throughput_consistency(
    profile: PerformanceProfile, *, total_input_tokens: int | None, total_output_tokens: int | None
) -> list[str]:
    """吞吐 ≈ 累计 token / 时长（只做检查与记录，不修改数值）。"""
    notes: list[str] = []
    if not profile.duration_s or profile.duration_s <= 0:
        return ["duration_missing_cannot_check_throughput"]
    for total, published, label in (
        (total_input_tokens, profile.input_throughput_tok_s, "input"),
        (total_output_tokens, profile.output_throughput_tok_s, "output"),
    ):
        if total is None or published in (None, 0):
            continue
        computed = Decimal(total) / Decimal(profile.duration_s)
        rel = abs(computed - Decimal(published)) / Decimal(published)
        if rel > THROUGHPUT_TOLERANCE:
            notes.append(
                f"{label}_throughput_inconsistent: published={published} computed={computed:.4f} "
                f"relerr={rel:.2e}"
            )
    if notes:
        return notes
    notes.append("throughput_consistent_with_tokens_over_duration")
    return notes


def profile_from_record(record: BenchmarkRecord) -> PerformanceProfile:
    """从第一阶段记录构造 profile（Excel 32 条只有吞吐、没有时延口径）。"""
    metric_ids: list[str] = []
    if record.ttft_p99_ms is not None:
        metric_ids.append("m.latency.ttft.p99")
    if record.tpot_p99_ms is not None:
        metric_ids.append("m.latency.tpot.p99")
    if record.itl_p99_ms is not None:
        metric_ids.append("m.latency.itl.p99")
    if record.input_tps_per_gpu is not None:
        metric_ids.append("m.throughput.per_gpu.input")
    if record.output_tps_per_gpu is not None:
        metric_ids.append("m.throughput.per_gpu.output")

    missing: list[str] = []
    for name, value in (
        ("ttft_p99_ms", record.ttft_p99_ms),
        ("tpot_p99_ms", record.tpot_p99_ms),
        ("itl_p99_ms", record.itl_p99_ms),
        ("e2e_p99_ms", record.e2e_p99_ms),
        ("output_throughput_tok_s", record.output_tps_cluster),
    ):
        if value is None:
            missing.append(name)

    profile = PerformanceProfile(
        ttft_mean_ms=record.ttft_mean_ms,
        ttft_p99_ms=record.ttft_p99_ms,
        tpot_mean_ms=record.tpot_mean_ms,
        tpot_p99_ms=record.tpot_p99_ms,
        itl_p99_ms=record.itl_p99_ms,
        e2e_p99_ms=record.e2e_p99_ms,
        input_throughput_tok_s=record.input_tps_cluster,
        output_throughput_tok_s=record.output_tps_cluster,
        output_throughput_tok_s_per_gpu=record.output_tps_per_gpu,
        configured_concurrency=record.configured_concurrency,
        observed_concurrency=record.mean_observed_concurrency,
        request_rate_rps=record.request_rate_rps,
        duration_s=record.duration_s,
        num_requests=record.num_requests,
        success_count=record.success_count,
        metric_definition_ids=metric_ids,
        metric_side="client",
        missing_metrics=sorted(missing),
        quality_flags=["profile_from_historical_record_without_latency"]
        if not metric_ids
        else [],
    )
    _consistency(profile)
    # 吞吐 ≈ 累计 token / 时长（导入路径同样要检查，不能只在候选路径做）
    raw = record.raw_metrics or {}
    profile.consistency_notes += check_throughput_consistency(
        profile,
        total_input_tokens=_int_or_none(raw.get("reported_total_input_tokens")),
        total_output_tokens=_int_or_none(raw.get("reported_total_output_tokens")),
    )
    return profile


def _int_or_none(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(Decimal(str(value)))
    except Exception:  # pragma: no cover - 非法输入
        return None


def profile_from_numeric_candidate(c: SglangNumericCandidate) -> PerformanceProfile:
    """从 SGLang 数值候选构造 profile（控制台输出的原始指标，口径已核对）。"""
    m = c.metrics
    metric_ids = sorted(set(c.metric_definition_ids.values()))
    missing = [
        name
        for name, value in (
            ("ttft_p99_ms", m.get("ttft_p99_ms")),
            ("tpot_p99_ms", m.get("tpot_p99_ms")),
            ("itl_p99_ms", m.get("itl_p99_ms")),
            ("e2e_p99_ms", m.get("e2e_p99_ms")),
        )
        if value is None
    ]
    profile = PerformanceProfile(
        ttft_mean_ms=m.get("ttft_mean_ms"),
        ttft_p90_ms=m.get("ttft_p90_ms"),
        ttft_p99_ms=m.get("ttft_p99_ms"),
        tpot_mean_ms=m.get("tpot_mean_ms"),
        tpot_p90_ms=m.get("tpot_p90_ms"),
        tpot_p99_ms=m.get("tpot_p99_ms"),
        itl_mean_ms=m.get("itl_mean_ms"),
        itl_p99_ms=m.get("itl_p99_ms"),
        e2e_mean_ms=m.get("e2e_mean_ms"),
        e2e_p90_ms=m.get("e2e_p90_ms"),
        e2e_p99_ms=m.get("e2e_p99_ms"),
        request_throughput_rps=m.get("request_throughput_rps"),
        input_throughput_tok_s=m.get("input_throughput_tok_s"),
        output_throughput_tok_s=m.get("output_throughput_tok_s"),
        total_throughput_tok_s=m.get("total_token_throughput_tok_s"),
        configured_concurrency=c.configured_max_concurrency,
        observed_concurrency=m.get("observed_concurrency"),
        duration_s=m.get("duration_s"),
        num_requests=c.num_prompts,
        success_count=int(m["success_count"]) if "success_count" in m else None,
        metric_definition_ids=metric_ids,
        metric_side="client",
        missing_metrics=sorted(missing),
    )
    _consistency(profile)
    profile.consistency_notes += check_throughput_consistency(
        profile,
        total_input_tokens=int(m["total_input_tokens"]) if "total_input_tokens" in m else None,
        total_output_tokens=int(m["total_output_tokens"]) if "total_output_tokens" in m else None,
    )
    return profile


def run_from_numeric_candidate(c: SglangNumericCandidate, *, collected_at: datetime | None = None) -> BenchmarkRun:
    """把候选记录提升为 BenchmarkRun（数据可信等级 = 厂商公开发布）。"""
    sig = signature_from_numeric_candidate(c).model_copy(
        update={
            "signature_id": f"sig_run:{c.record_id}",
            "source_kind": "official_candidate_run",
            "source_ref": c.record_id,
        }
    )
    profile = profile_from_numeric_candidate(c)
    run = BenchmarkRun(
        run_id=c.record_id,
        signature=sig,
        profile=profile,
        raw_file=c.evidence_file,
        raw_sha256=c.evidence_sha256,
        raw_format="sglang_console_summary/v1",
        framework=c.framework,
        framework_version=c.framework_version,
        launch_command=c.launch_command,
        benchmark_command=c.benchmark_command,
        collected_at=collected_at or c.accessed_at,
        load_profile_kind="fixed_length_standard",
        data_trust="measured_vendor_published",
        quality_flags=list(c.quality_flags),
    )
    return run


def run_from_record(record: BenchmarkRecord) -> BenchmarkRun:
    """把第一阶段记录提升为 BenchmarkRun（用于构建容量曲线的历史点）。"""
    trust, _ = trust_for_source_kind(record.performance_source_kind)
    sig = signature_from_record(record).model_copy(
        update={"signature_id": f"sig_run:{record.record_id}", "source_kind": "benchmark_record", "source_ref": record.record_id}
    )
    return BenchmarkRun(
        run_id=record.record_id,
        signature=sig,
        profile=profile_from_record(record),
        raw_format="excel_row/v1",
        framework=record.framework,
        framework_version=record.framework_version,
        load_profile_kind="unknown",
        data_trust=trust,
        quality_flags=list(record.quality_flags),
    )


def run_from_adapter(
    record: BenchmarkRecord,
    *,
    metadata: dict[str, Any],
    raw_file: str | Path,
    raw_format: str,
) -> BenchmarkRun:
    """从 SGLang/vLLM 适配器的解析结果构造 run（真实导入路径）。"""
    sig = signature_from_record(record)
    trust, trust_warning = trust_for_source_kind(record.performance_source_kind)
    # P0-2：`internal_measurement` 是**自我声明**，metadata 无论怎么写都
    # **不能**直接授予本地可信等级 —— 导入后一律先视为**待核验**。
    # 只有 `attest-run` 这个显式核验步骤才能把它升级为 measured_internal。
    #
    # 理由：可信等级若由输入数据自己决定，任何字段齐全的文件都能自称实测；
    # 核验凭据必须来自被核验对象之外（原始文件哈希 + 核验人）。
    #
    # 刻意**不**波及 `official_benchmark`：厂商发布是另一条溯源路径，
    # 可信度来自发布方而非我们的核验；字段完整性由 M9 适用性门禁与 SLA 门禁把关。
    provenance_flags: list[str] = []
    if trust == "measured_internal":
        provenance_flags.append(
            f"pending_attestation:internal_measurement/{record.verification_status}"
        )
        trust_warning = (
            (trust_warning + "; " if trust_warning else "")
            + "internal_measurement 导入后一律视为待核验：可信等级为 unknown，"
            "需显式 attest-run 核验后才升级"
        )
        trust = "unknown"
    sig = sig.model_copy(
        update={
            "signature_id": f"sig_run:{record.record_id}",
            "source_kind": "imported_benchmark_run",
            "data_trust": trust,
        }
    )
    profile = profile_from_record(record)
    profile.metric_side = "client"
    run = BenchmarkRun(
        run_id=record.record_id,
        signature=sig,
        profile=profile,
        raw_file=str(raw_file),
        raw_sha256=sha256_file(raw_file),
        raw_format=raw_format,
        framework=record.framework,
        framework_version=record.framework_version,
        launch_command=metadata.get("launch_command"),
        benchmark_command=metadata.get("benchmark_command"),
        collected_at=datetime.now().astimezone(),
        load_profile_kind=metadata.get("load_profile_kind", "fixed_length_standard"),
        data_trust=sig.data_trust,
        quality_flags=list(record.quality_flags)
        + provenance_flags
        + ([trust_warning] if trust_warning else []),
        notes=metadata.get("label"),
    )
    return run


def build_runs_from_fixture_dir(
    index_path: str | Path,
    *,
    root: str | Path,
    importer,
) -> tuple[list[BenchmarkRun], list[str]]:
    """按 INDEX.json 逐个导入性能样本（离线，不需要 GPU）。

    ``importer`` 形如 ``lambda data_file, meta_file: (records, evidences)``，
    由服务层注入真实的适配器调用。
    """
    import json

    base = Path(root)
    index = json.loads(Path(index_path).read_text(encoding="utf-8"))
    runs: list[BenchmarkRun] = []
    skipped: list[str] = []
    for point in index.get("points", []):
        data_file = base / "data" / "fixtures" / "perf_curve" / point["file"]
        meta_file = base / "data" / "fixtures" / "perf_curve" / point["metadata"]
        if not data_file.exists() or not meta_file.exists():
            skipped.append(point["file"])
            continue
        records, _evidences = importer(data_file, meta_file)
        if not records:
            skipped.append(point["file"])
            continue
        record = records[0]
        run = run_from_adapter(
            record,
            metadata={"label": index.get("notice", ""), "load_profile_kind": "fixed_length_standard"},
            raw_file=data_file,
            raw_format="vllm-bench-serve-json/v1",
        )
        # 用 INDEX 里的部署信息补齐（fixture 的 metadata 已声明 tp/dp）
        runs.append(run)
    return runs, skipped
