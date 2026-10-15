"""M19：Benchmark 配置匹配引擎。

职责：判断"某条已有 Benchmark 数据"与"目标部署需求"的**配置接近程度**，
并把结论、差异与使用限制结构化输出。

三层区分（本模块的核心纪律）：

1. **匹配等级 ≠ 数据可信等级**：配置完全一致也不代表数据可信（可能是 synthetic）；
2. **匹配等级 ≠ SLA 状态**：``exact_match`` 只说明"这条实测就是在目标配置与负载下做的"，
   是否达标由 M23 用实测值判断；
3. **相似 ≠ 可用**：跨模型、跨 GPU SKU、跨 TP/DP 的性能数据一律 ``rejected`` 或
   降级为 ``modeling_candidate``，**绝不允许**把 Qwen3-235B/B200 的数据当成
   Qwen3-32B/H100 的性能证据（任务书 M19 验收项）。

阻断优先：先判"是否必须拒绝"，再判"能给到什么等级"。
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Iterable, Sequence

from llm_capacity_pricing.schemas.benchmark import BenchmarkRecord
from llm_capacity_pricing.schemas.official import (
    OfficialBenchmarkCandidate,
    SglangNumericCandidate,
)
from llm_capacity_pricing.schemas.performance import (
    BenchmarkMatchResult,
    BenchmarkRun,
    BenchmarkSignature,
    DataTrust,
    MatchLevel,
    TargetRequirement,
    WorkloadSignature,
)

#: 本阶段**不建性能模型**的部署维度：一旦出现就明确阻断
UNSUPPORTED_DEPLOYMENT_FIELDS: tuple[str, ...] = ("pp_size", "ep_size", "deployment_mode")

#: **严格匹配必需字段**（V3.1 R2）。
#: 这些字段描述"这条实测到底是什么"，只要记录侧缺失，就无法确认它适用于目标需求，
#: 因此**不得**评为 exact_match；缺失不等于匹配成功。
RECORD_IDENTITY_FIELDS: tuple[str, ...] = (
    "model_name",
    "model_revision",
    "weight_precision",
    "hardware_model",
    "gpu_count",
    "tp_size",
    "dp_size",
    "framework",
    "framework_version",
)
#: **目标侧**同样必须齐备的字段（V3.1 复审 P0-1）。
#: 严格匹配要求"目标说清了要什么"**且**"记录说清了测了什么"——
#: 目标没写 revision/框架版本/数据集/长度/到达方式时，无法确认这条实测适用于该需求，
#: 因此不得因为"记录侧完整"就评为 exact_match。
TARGET_IDENTITY_FIELDS: tuple[str, ...] = (
    "model_revision",
    "weight_precision",
    "framework_version",
)
TARGET_WORKLOAD_IDENTITY_FIELDS: tuple[str, ...] = (
    "dataset_name",
    "input_len_target",
    "output_len_target",
    "arrival_mode",
)

#: 负载侧的严格必需字段（同样要求记录侧具备）
RECORD_WORKLOAD_IDENTITY_FIELDS: tuple[str, ...] = (
    "dataset_name",
    "input_len_target",
    "output_len_target",
    "arrival_mode",
)
#: 若两侧都有值且不一致，就**必须降级**（这些会改变实测数值）
LOAD_SEMANTIC_FIELDS: tuple[str, ...] = (
    "streaming",
    "prefix_reuse",
    "warmup",
    "eos_policy",
)

#: 等级顺序（越小越接近目标）
LEVEL_ORDER: dict[str, int] = {
    "exact_match": 0,
    "comparable_reference": 1,
    "modeling_candidate": 2,
    "rejected": 3,
}

#: 各等级允许/禁止的用途
USABLE_FOR: dict[str, list[str]] = {
    "exact_match": ["sla_proof", "capacity_analysis", "reference"],
    "comparable_reference": ["reference", "capacity_analysis"],
    "modeling_candidate": ["modeling", "calibration"],
    "rejected": [],
}
BLOCKED_FROM: dict[str, list[str]] = {
    "exact_match": [],
    "comparable_reference": ["sla_proof", "cross_config_extrapolation"],
    "modeling_candidate": ["sla_proof", "cross_config_extrapolation", "capacity_analysis"],
    "rejected": ["sla_proof", "reference", "modeling", "capacity_analysis"],
}


#: 表示"未指定"的占位值（不是真实取值，不能当作"有值）
ABSENT_PLACEHOLDERS: frozenset[str] = frozenset({"unknown", "none", "null", "n/a", ""})


def is_absent(value: Any) -> bool:
    """判断一个取值是否等价于"未提供"。"""
    if value is None:
        return True
    if isinstance(value, str) and value.strip().lower() in ABSENT_PLACEHOLDERS:
        return True
    return False


def normalize_name(value: str | None) -> str | None:
    """模型/硬件名规范化：去空白、统一大小写。

    **只做大小写与空白归一化，不做任何别名等价推断**（任务书 §0：不同代模型不等价）。
    """
    if value is None:
        return None
    text = " ".join(value.split()).strip().lower()
    return text or None


def _normalize_precision(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip().lower()
    aliases = {"bf16": "bf16", "bfloat16": "bf16", "fp16": "fp16", "float16": "fp16",
               "fp32": "fp32", "float32": "fp32", "fp8": "fp8", "int8": "int8", "int4": "int4"}
    return aliases.get(text, text)


# ---------------------------------------------------------------------------
# 签名构造（从既有数据源）
# ---------------------------------------------------------------------------


def signature_from_record(record: BenchmarkRecord) -> BenchmarkSignature:
    """从第一阶段的 ``BenchmarkRecord``（Excel 32 条 / synthetic fixture）构造签名。"""
    if record.performance_source_kind == "synthetic_fixture":
        trust: DataTrust = "synthetic_fixture"
    elif record.performance_source_kind == "internal_excel":
        # 历史外部数据：有吞吐但**没有任何时延口径**
        trust = "historical_external_no_latency"
    else:
        trust = "unknown"

    metric_ids: list[str] = []
    if record.ttft_p99_ms is not None:
        metric_ids.append("m.latency.ttft.p99")
    if record.tpot_p99_ms is not None:
        metric_ids.append("m.latency.tpot.p99")
    if record.itl_p99_ms is not None:
        metric_ids.append("m.latency.itl.p99")

    return BenchmarkSignature(
        signature_id=f"sig_record:{record.record_id}",
        source_kind="benchmark_record",
        source_ref=record.record_id,
        model_name=record.model_name,
        model_revision=record.model_revision,
        weight_precision=_normalize_precision(record.model_precision),
        hardware_model=record.hardware_model,
        hardware_sku=record.hardware_sku,
        gpu_count=record.deployment_gpu_count,
        tp_size=record.tp_size,
        dp_size=record.dp_size,
        pp_size=record.pp_size,
        ep_size=record.ep_size,
        deployment_mode=record.deployment_mode,
        framework=record.framework,
        framework_version=record.framework_version,
        workload=WorkloadSignature(
            input_len_target=record.input_tokens_target,
            output_len_target=record.output_tokens_target,
            dataset_name=record.dataset_name,
            arrival_mode=(
                "unbounded"
                if record.request_rate_rps is None and record.configured_concurrency is not None
                else ("request_rate" if record.request_rate_rps is not None else "unknown")
            ),
            request_rate_rps=record.request_rate_rps,
            configured_concurrency=record.configured_concurrency,
            observed_concurrency=record.mean_observed_concurrency,
            duration_s=record.duration_s,
            num_requests=record.num_requests,
            success_count=record.success_count,
        ),
        metric_definition_ids=metric_ids,
        metric_side="client",
        sample_size=record.num_requests,
        data_trust=trust,
    )


def signature_from_numeric_candidate(c: SglangNumericCandidate) -> BenchmarkSignature:
    """从第二阶段的 SGLang 数值型候选构造签名。"""
    metric_ids = list(c.metric_definition_ids.values())
    return BenchmarkSignature(
        signature_id=f"sig_candidate:{c.candidate_id}",
        source_kind="official_candidate",
        source_ref=c.candidate_id,
        model_name=c.model_name,
        model_revision=c.model_revision,
        weight_precision=_normalize_precision(c.model_precision),
        hardware_model=c.hardware_model,
        gpu_count=c.deployment_gpu_count,
        tp_size=c.tp_size,
        dp_size=c.dp_size,
        ep_size=c.ep_size,
        deployment_mode=c.pd_mode,
        framework=c.framework,
        framework_version=c.framework_version,
        workload=WorkloadSignature(
            input_len_target=c.input_len_target,
            output_len_target=c.output_len_target,
            dataset_name=c.dataset_name,
            arrival_mode=(
                "unbounded" if str(c.traffic_request_rate_raw).lower() == "inf" else "request_rate"
            ),
            configured_concurrency=c.configured_max_concurrency,
            observed_concurrency=c.metrics.get("observed_concurrency"),
            num_requests=c.num_prompts,
            success_count=(
                int(c.metrics["success_count"]) if "success_count" in c.metrics else None
            ),
            duration_s=c.metrics.get("duration_s"),
        ),
        metric_definition_ids=metric_ids,
        metric_side="client",
        sample_size=c.num_prompts,
        # 厂商/框架官方公开发布的实测数据
        data_trust="measured_vendor_published",
    )


def signature_from_official_candidate(c: OfficialBenchmarkCandidate) -> BenchmarkSignature:
    metric_ids = list(("m.latency.ttft.p99", "m.latency.tpot.p99")) if c.metrics else []
    return BenchmarkSignature(
        signature_id=f"sig_candidate:{c.candidate_id}",
        source_kind="official_candidate",
        source_ref=c.candidate_id,
        model_name=c.model_name,
        model_revision=c.model_revision,
        weight_precision=_normalize_precision(c.model_precision),
        hardware_model=c.hardware_model,
        gpu_count=c.deployment_gpu_count,
        deployment_mode=(
            "prefill_decode_disaggregated"
            if (c.deployment_strategy_raw or "").upper().startswith("P")
            else None
        ),
        framework=c.framework,
        framework_version=c.framework_version,
        workload=WorkloadSignature(
            input_len_target=c.input_length,
            output_len_target=c.output_length,
            dataset_name=c.dataset_name,
            configured_concurrency=c.configured_concurrency,
            request_rate_rps=c.request_rate_rps,
        ),
        metric_definition_ids=metric_ids,
        metric_side="client",
        data_trust="measured_vendor_published",
    )


def signature_from_run(run: BenchmarkRun) -> BenchmarkSignature:
    """BenchmarkRun 自带签名（补齐来源标识与口径）。"""
    sig = run.signature.model_copy(
        update={
            "source_kind": "benchmark_run",
            "source_ref": run.run_id,
            "data_trust": run.data_trust,
            "metric_definition_ids": list(run.profile.metric_definition_ids),
            "metric_side": run.profile.metric_side,
            "statistics_window": run.profile.statistics_window,
            "sample_size": run.profile.num_requests,
        }
    )
    return sig


# ---------------------------------------------------------------------------
# 匹配
# ---------------------------------------------------------------------------


def _lengths_close(a: int | None, b: int | None, tolerance: Decimal) -> bool:
    if a is None or b is None:
        return a is None and b is None
    if a == b:
        return True
    rel = Decimal(abs(a - b)) / Decimal(max(a, b, 1))
    return rel <= tolerance


def _compare(
    target_value: Any, sig_value: Any, field: str, differences: dict, reasons: list[str]
) -> bool:
    """相等判定；不等时记录差异。返回是否相等。"""
    if is_absent(target_value) or is_absent(sig_value):
        return True  # 缺失/占位值不算"不一致"，由"未确认"另行记录
    if normalize_name(str(target_value)) == normalize_name(str(sig_value)):
        return True
    differences[field] = [target_value, sig_value]
    reasons.append(f"{field}_mismatch:{target_value}!={sig_value}")
    return False


def match(target: TargetRequirement, sig: BenchmarkSignature) -> BenchmarkMatchResult:
    """判定一条签名与目标需求的匹配等级。"""
    reasons: list[str] = []
    differences: dict[str, list[Any]] = {}
    unverified: list[str] = []

    # ---- 1. 可信等级前置门禁（synthetic 不得进入实测结论）----------------
    if target.require_measured and sig.data_trust == "synthetic_fixture":
        reasons.append("data_trust_rejected:synthetic_fixture_with_require_measured")
    if sig.data_trust == "synthetic_fixture" and not target.allow_synthetic:
        reasons.append("data_trust_rejected:synthetic_fixture_not_allowed")
    if sig.data_trust == "unknown":
        unverified.append("data_trust_unknown")

    # ---- 2. 关键维度：模型 / 精度 / 硬件 / 拓扑 --------------------------
    _compare(target.model_name, sig.model_name, "model_name", differences, reasons)
    _compare(target.model_revision, sig.model_revision, "model_revision", differences, reasons)
    _compare(
        _normalize_precision(target.weight_precision),
        _normalize_precision(sig.weight_precision),
        "weight_precision",
        differences,
        reasons,
    )
    _compare(target.quantization, sig.quantization, "quantization", differences, reasons)
    _compare(target.hardware_model, sig.hardware_model, "hardware_model", differences, reasons)
    _compare(target.hardware_sku, sig.hardware_sku, "hardware_sku", differences, reasons)
    _compare(target.gpu_count, sig.gpu_count, "gpu_count", differences, reasons)

    # ---- 3. 本阶段不建性能模型的维度：显式阻断 ---------------------------
    for field_name in UNSUPPORTED_DEPLOYMENT_FIELDS:
        sig_value = getattr(sig, field_name, None)
        target_value = getattr(target, field_name, None)
        if is_absent(sig_value) and is_absent(target_value):
            continue  # 两边都没提供 -> 不是差异
        if field_name == "deployment_mode":
            # 只有一方声明了部署模式 -> 记未确认（不阻断，但阻止 exact_match）
            if is_absent(sig_value) or is_absent(target_value):
                unverified.append(
                    f"deployment_mode_unverified:"
                    f"{sig_value if not is_absent(sig_value) else target_value}"
                )
                continue
        # 双方都给了真实取值 -> 本阶段不建该维度的性能模型，阻断
        reasons.append(
            f"unsupported_deployment_field:{field_name}"
            f"（本阶段不建 PP/EP/PD 性能模型，字段已保存但阻断）"
        )

    # ---- 4. 并行配置 -----------------------------------------------------
    _compare(target.tp_size, sig.tp_size, "tp_size", differences, reasons)
    _compare(target.dp_size, sig.dp_size, "dp_size", differences, reasons)

    # ---- 5. 框架 ---------------------------------------------------------
    framework_same = _compare(
        target.framework, sig.framework, "framework", differences, reasons
    )
    version_same = _compare(
        target.framework_version, sig.framework_version, "framework_version", differences, reasons
    )

    # ---- 6. 必需指标 -----------------------------------------------------
    for metric in target.required_metrics:
        if metric not in sig.metric_definition_ids:
            reasons.append(f"missing_metric:{metric}")

    # ---- 7. 负载 ---------------------------------------------------------
    tw, sw = target.workload, sig.workload
    workload_specified = not (
        tw.input_len_target is None
        and tw.output_len_target is None
        and tw.dataset_name is None
        and tw.arrival_mode == "unknown"
        and tw.configured_concurrency is None
    )
    if not workload_specified:
        unverified.append("workload_not_specified_in_target")
    lengths_same = (
        True
        if not workload_specified
        else (
            _lengths_close(tw.input_len_target, sw.input_len_target, target.length_tolerance)
            and _lengths_close(tw.output_len_target, sw.output_len_target, target.length_tolerance)
        )
    )
    if not lengths_same:
        differences["input_output_len"] = [
            [tw.input_len_target, tw.output_len_target],
            [sw.input_len_target, sw.output_len_target],
        ]
        reasons.append(
            f"workload_length_mismatch:{tw.input_len_target}/{tw.output_len_target}"
            f"!={sw.input_len_target}/{sw.output_len_target}"
        )
    dataset_same = True if not workload_specified else _compare(
        tw.dataset_name, sw.dataset_name, "dataset_name", differences, reasons
    )
    arrival_same = (
        not workload_specified
        or tw.arrival_mode == "unknown"
        or sw.arrival_mode == "unknown"
        or tw.arrival_mode == sw.arrival_mode
    )
    if not arrival_same:
        differences["arrival_mode"] = [tw.arrival_mode, sw.arrival_mode]
        reasons.append(f"arrival_mode_mismatch:{tw.arrival_mode}!={sw.arrival_mode}")
    # R2：并发不一致必须降级（并发直接决定实测吞吐与时延）
    concurrency_mismatch = False
    if (
        tw.configured_concurrency is not None
        and sw.configured_concurrency is not None
        and tw.configured_concurrency != sw.configured_concurrency
    ):
        concurrency_mismatch = True
        differences["configured_concurrency"] = [
            tw.configured_concurrency,
            sw.configured_concurrency,
        ]
        reasons.append(
            f"concurrency_mismatch:{tw.configured_concurrency}!={sw.configured_concurrency}"
        )
    # 请求到达速率（数值比较）
    rate_mismatch = False
    if tw.request_rate_rps is not None and sw.request_rate_rps is not None:
        if tw.request_rate_rps != sw.request_rate_rps:
            rate_mismatch = True
            differences["request_rate_rps"] = [tw.request_rate_rps, sw.request_rate_rps]
            reasons.append(
                f"request_rate_mismatch:{tw.request_rate_rps}!={sw.request_rate_rps}"
            )
    # 负载语义字段（流式/前缀复用/预热/EOS 策略）：两侧都有值时不一致 -> 降级
    load_semantic_mismatch = False
    for field_name in LOAD_SEMANTIC_FIELDS:
        want = getattr(tw, field_name, None)
        got = getattr(sw, field_name, None)
        if want is None or got is None:
            continue
        if want != got:
            load_semantic_mismatch = True
            differences[field_name] = [want, got]
            reasons.append(f"{field_name}_mismatch:{want}!={got}")
    if tw.load_profile_kind != "unknown" and sw.load_profile_kind != "unknown":
        if tw.load_profile_kind != sw.load_profile_kind:
            reasons.append(
                f"load_profile_kind_mismatch:{tw.load_profile_kind}!={sw.load_profile_kind}"
                "（固定长度与多轮/前缀复用两类负载不得混算）"
            )

    # ---- 7.5 严格必需字段（R2）：记录侧缺一项就不能 exact_match -----------
    missing_identity: list[str] = []
    for field_name in RECORD_IDENTITY_FIELDS:
        if is_absent(getattr(sig, field_name, None)):
            missing_identity.append(field_name)
    for field_name in RECORD_WORKLOAD_IDENTITY_FIELDS:
        if is_absent(getattr(sw, field_name, None)):
            missing_identity.append(f"workload.{field_name}")
    if missing_identity:
        unverified.append("record_identity_incomplete:" + ",".join(missing_identity))

    # ---- 7.6 目标侧身份完整性（V3.1 复审 P0-1）----------------------------
    missing_target: list[str] = []
    if workload_specified:
        for field_name in TARGET_IDENTITY_FIELDS:
            if is_absent(getattr(target, field_name, None)):
                missing_target.append(field_name)
        for field_name in TARGET_WORKLOAD_IDENTITY_FIELDS:
            if is_absent(getattr(tw, field_name, None)):
                missing_target.append(f"workload.{field_name}")
        if missing_target:
            unverified.append("target_identity_incomplete:" + ",".join(missing_target))

    # ---- 8. 定级 ---------------------------------------------------------
    # 硬阻断：数据来源/模型/硬件/精度层面的不一致，或指标缺失 —— 这些无法补救。
    # 注意**不包含** tp/dp/gpu_count：那是"部署条件不同"，数据本身仍有建模价值，
    # 应降级为 modeling_candidate（可建模/校准，但禁止外推与 SLA 证明）。
    hard_block = any(
        r.startswith(
            (
                "model_name_mismatch",
                "model_revision_mismatch",
                "weight_precision_mismatch",
                "quantization_mismatch",
                "hardware_model_mismatch",
                "hardware_sku_mismatch",
                "missing_metric",
                "unsupported_deployment_field",
                "data_trust_rejected",
                "load_profile_kind_mismatch",
            )
        )
        for r in reasons
    )
    deployment_mismatch = any(
        r.startswith(("tp_size_mismatch", "dp_size_mismatch", "gpu_count_mismatch"))
        for r in reasons
    )

    if hard_block:
        level: MatchLevel = "rejected"
    elif deployment_mismatch:
        # 同模型同硬件但部署条件（TP/DP/卡数）不同：只能用于建模/校准，禁止外推
        level = "modeling_candidate"
    elif (
        not lengths_same
        or not dataset_same
        or not arrival_same
        or concurrency_mismatch
        or rate_mismatch
        or load_semantic_mismatch
    ):
        # 部署条件一致、**负载不同**：可作参考，但不能当作目标负载下的实测
        level = "comparable_reference"
    elif not (framework_same and version_same):
        level = "comparable_reference"
    else:
        level = "exact_match"

    # 未确认细节始终保留在原因里（可解释性）：降级时必须说明"什么没确认"
    reasons.extend(f"unverified:{item}" for item in unverified)
    if level == "exact_match" and unverified:
        # 关键字段虽一致，但存在未确认细节 -> 降级（不冒充严格匹配）
        level = "comparable_reference"
        reasons.append("downgraded:unverified_details_present")

    usable = list(USABLE_FOR[level])
    blocked = list(BLOCKED_FROM[level])
    if sig.data_trust == "synthetic_fixture":
        # synthetic 结论只能用于算法验证：不得声称可作 SLA 证明
        usable = [u for u in usable if u != "sla_proof"]
        if "sla_proof" not in blocked:
            blocked.append("sla_proof")
    if sig.data_trust in ("historical_external_no_latency",):
        usable = [u for u in usable if u in ("reference", "modeling")]
        if "sla_proof" not in blocked:
            blocked.append("sla_proof")
    if "unverified_details_present" in " ".join(reasons):
        pass

    return BenchmarkMatchResult(
        target_ref=target.model_name or "target",
        signature_id=sig.signature_id,
        source_ref=sig.source_ref,
        level=level,
        algorithm_validation_only=(sig.data_trust == "synthetic_fixture" and target.allow_synthetic),
        reasons=sorted(set(reasons)),
        differences=differences,
        usable_for=usable,
        blocked_from=sorted(set(blocked)),
        data_trust=sig.data_trust,
    )


def match_all(
    target: TargetRequirement, signatures: Iterable[BenchmarkSignature]
) -> list[BenchmarkMatchResult]:
    """对所有签名匹配并按（等级，来源）稳定排序。"""
    results = [match(target, sig) for sig in signatures]
    return sorted(results, key=lambda r: (LEVEL_ORDER[r.level], r.source_ref))


def collect_signatures(
    *,
    records: Sequence[BenchmarkRecord] = (),
    numeric_candidates: Sequence[SglangNumericCandidate] = (),
    official_candidates: Sequence[OfficialBenchmarkCandidate] = (),
    runs: Sequence[BenchmarkRun] = (),
    include_synthetic: bool = True,
) -> list[BenchmarkSignature]:
    """把库里各类数据统一成签名列表（便于一次匹配全部）。"""
    signatures: list[BenchmarkSignature] = []
    for record in records:
        if not include_synthetic and record.performance_source_kind == "synthetic_fixture":
            continue
        signatures.append(signature_from_record(record))
    signatures.extend(signature_from_numeric_candidate(c) for c in numeric_candidates)
    signatures.extend(signature_from_official_candidate(c) for c in official_candidates)
    signatures.extend(signature_from_run(r) for r in runs)
    return signatures
