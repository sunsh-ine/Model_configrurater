"""P2：AISimulate 可选适配器。

职责**只有**五件事，刻意不做别的：

1. 把我们的需求映射成 AISimulate 输入；
2. 取配置搜索结果（候选）；
3. 对少量候选取原生 P99；
4. 转成 :class:`SimulationResult`；
5. 归档输入、原始输出、版本与来源。

三条硬约束：

* **离线优先**：默认只读本地归档输出，不需要安装 AISimulate；
  外部命令必须显式开启（``enable_external=True``），未开启时行为与"没装"完全一致。
* **不碰实测**：只用 ``schemas/simulation.py`` 的对象；不写 ``BenchmarkRun`` /
  ``CapacityPoint`` / 任何实测字段。因此模拟结果**结构上**无法进入 SLA 或报价结论。
* **口径不采信**：判定只看 ``sim_p99_*`` 与用户阈值；
  AISimulate 扫描器的"均值型 SLA 过滤"结论一律**不采纳**
  （官方文档原文：``It is not a p99 constraint``）。

命令一律用 argv 列表构造、``shell=False``、带超时，不拼接 shell 字符串。
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

import yaml

from llm_capacity_pricing.schemas.simulation import (
    SIMULATION_TRUST_LABEL,
    SimulationCandidate,
    SimulationDeployment,
    SimulationMetrics,
    SimulationResult,
    SimulationSearchAudit,
    SimulationToolIdentity,
    SimulationWorkload,
)

#: 扫描器 CSV 列 -> SimulationDeployment 属性
_SWEEPER_DEPLOYMENT_COLUMNS: dict[str, str] = {
    "model": "model_name",
    "system": "system",
    "num_total_gpus": "gpu_count",
    "tp": "tp_size",
    "pp": "pp_size",
    "dp": "dp_size",
    "moe_tp": "moe_tp_size",
    "moe_ep": "moe_ep_size",
    "cp": "cp_size",
    "replicas": "replicas",
    "gpus_per_worker": "gpus_per_worker",
    "parallel": "parallel",
    "backend": "backend",
    "version": "backend_version",
    "bs": "batch_size",
    "memory": "memory_gib",
    "balance_score": "balance_score",
}

#: 扫描器 CSV 列 -> SimulationWorkload 属性
_SWEEPER_WORKLOAD_COLUMNS: dict[str, str] = {
    "isl": "input_len",
    "osl": "output_len",
    "concurrency": "concurrency",
    "prefix": "prefix",
    "ctx_tokens": "ctx_tokens",
}

#: 扫描器 CSV 列 -> SimulationMetrics 属性（**全部加 sim_ 前缀**）
_SWEEPER_METRIC_COLUMNS: dict[str, str] = {
    "ttft": "sim_mean_ttft_ms",
    "tpot": "sim_mean_tpot_ms",
    "request_latency": "sim_request_latency_ms",
    "tokens/s": "sim_output_throughput_tok_s",
    "tokens/s/gpu": "sim_tokens_per_gpu",
    "tokens/s/user": "sim_tokens_per_user",
    "seq/s": "sim_request_throughput_rps",
}

#: 量化列（原样留存，不解释）
_QUANT_COLUMNS: tuple[str, ...] = ("gemm", "kvcache", "fmha", "moe", "comm")

#: 原生回放 prediction.json 字段 -> SimulationMetrics（``p99_*`` / ``median_*`` / ``mean_*``）
_NATIVE_METRIC_FIELDS: tuple[tuple[str, str], ...] = (
    ("mean_ttft_ms", "sim_mean_ttft_ms"),
    ("median_ttft_ms", "sim_median_ttft_ms"),
    ("p99_ttft_ms", "sim_p99_ttft_ms"),
    ("mean_tpot_ms", "sim_mean_tpot_ms"),
    ("median_tpot_ms", "sim_median_tpot_ms"),
    ("p99_tpot_ms", "sim_p99_tpot_ms"),
    ("mean_e2e_latency_ms", "sim_request_latency_ms"),
    ("output_throughput_tok_s", "sim_output_throughput_tok_s"),
    ("request_throughput_rps", "sim_request_throughput_rps"),
)
_NATIVE_COUNT_FIELDS: tuple[tuple[str, str], ...] = (
    ("num_ttft_samples", "sim_ttft_samples"),
    ("num_tpot_samples", "sim_tpot_samples"),
    ("completed_requests", "sim_completed_requests"),
)

#: AISimulate 的 system 名 <-> 本仓库目录里的 hardware_id。
#: **显式声明**而不是"按名字猜"；测试会拿目录里的商品名逐条校验，防止它悄悄漂移。
SYSTEM_TO_HARDWARE_ID: dict[str, str] = {
    "h100_sxm": "nvidia-h100-sxm-80gb",
    "h200_sxm": "nvidia-h200-sxm-141gb",
    "b200_sxm": "nvidia-b200-sxm",
}
HARDWARE_ID_TO_SYSTEM: dict[str, str] = {
    hardware_id: system for system, hardware_id in SYSTEM_TO_HARDWARE_ID.items()
}


def hardware_id_for_system(system: str | None) -> str | None:
    """把 AISimulate 的 system 映射到本仓库的 hardware_id；没登记的返回 ``None``（不猜）。"""
    if not system:
        return None
    return SYSTEM_TO_HARDWARE_ID.get(" ".join(system.split()).strip().lower())


def system_for_hardware_id(hardware_id: str | None) -> str | None:
    """反向映射：把本仓库的 hardware_id 变成 AISimulate 的 system 名。"""
    if not hardware_id:
        return None
    return HARDWARE_ID_TO_SYSTEM.get(hardware_id)


def normalize_system_token(value: str | None) -> str | None:
    """把 system 名与商品名归一化到可比形式（去掉厂商前缀与非字母数字）。"""
    if not value:
        return None
    text = "".join(ch for ch in value.lower() if ch.isalnum())
    return text[6:] if text.startswith("nvidia") else text


#: 判定 P99 至少需要多少样本 —— 样本不足时 P99 无意义（官方文档亦如此强调）
MIN_SAMPLES_FOR_P99 = 30

#: 扫描器输出里能区分同一身份不同工作点的列；不记录会让两个"看起来一样"的配置互相矛盾
_SWEEPER_WORKPOINT_COLUMNS: tuple[str, ...] = (
    "ctx_tokens",
    "num_ctx_reqs",
    "num_gen_reqs",
    "num_tokens",
)


def _dec(value: Any) -> Decimal | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in {"nan", "none", "null", ""}:
        return None
    try:
        return Decimal(text)
    except Exception:  # noqa: BLE001 - 非数值列一律视为缺失
        return None


def _int(value: Any) -> int | None:
    dec = _dec(value)
    if dec is None:
        return None
    try:
        return int(dec)
    except Exception:  # noqa: BLE001
        return None


def sha256_file(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def precision_from_model_name(model_name: str | None) -> str | None:
    """从 HF 模型名推断精度 —— **仅在名字明确带后缀时**才给结论。

    没有后缀就不猜（返回 ``None``），由身份检查判为"不可比较"。
    """
    if not model_name:
        return None
    upper = model_name.upper()
    for suffix, precision in (
        ("-FP8", "fp8"),
        ("-NVFP4", "nvfp4"),
        ("-FP4", "fp4"),
        ("-AWQ", "int4"),
        ("-GPTQ", "int4"),
        ("-INT8", "int8"),
    ):
        if upper.endswith(suffix):
            return precision
    return None


#: 只有"权重精度"才算模型精度。
#: ``comm``（通信 dtype）、``fmha``（注意力内核）、``kvcache``（KV 缓存）都**不是**模型权重精度 ——
#: 把它们混进来会让 bf16 的模型被误判成"精度不明"（本轮实测踩到过）。
_WEIGHT_QUANT_KEYS: tuple[str, ...] = ("gemm", "moe")

_QUANT_TO_PRECISION: dict[str, str] = {
    "bfloat16": "bf16",
    "bf16": "bf16",
    "fp16": "fp16",
    "float16": "fp16",
    "fp8": "fp8",
    "fp8_block": "fp8",
    "fp8_ootb": "fp8",
    "int8": "int8",
    "int4": "int4",
    "int4_wo": "int4",
    "nvfp4": "nvfp4",
    "fp4": "fp4",
}


def _precision_from_quant_modes(quant_modes: Mapping[str, str]) -> str | None:
    """从**权重**量化列推断精度；权重列之间不一致或不可识别时留空（不猜）。"""
    values = {
        str(quant_modes[key]).strip().lower()
        for key in _WEIGHT_QUANT_KEYS
        if quant_modes.get(key)
    }
    if len(values) != 1:
        return None
    return _QUANT_TO_PRECISION.get(next(iter(values)))


# ---------------------------------------------------------------------------
# 目标侧身份（P3 会从 PlanRequest 构造它）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SimulationTarget:
    """要与模拟结果比对的目标身份。

    身份不齐就**不能**得出一致性结论 —— 缺的字段留 ``None``，检查时如实报原因。
    """

    model_name: str | None = None
    model_precision: str | None = None
    system: str | None = None
    input_len: int | None = None
    output_len: int | None = None
    concurrency: int | None = None
    ttft_p99_ms_max: Decimal | None = None
    tpot_p99_ms_max: Decimal | None = None
    backend: str | None = None
    backend_version: str | None = None


def resolve_backend_version(
    value: str | None, aliases: Mapping[str, str] | None = None
) -> str | None:
    """把版本取值解析成**实际版本**。

    AISimulate 允许用槽位别名（``current`` / ``previous`` / ``next``）指定后端版本。
    别名本身不是版本号：**解析不出来就不能认为身份匹配**（R1.1）。
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    table = aliases or {}
    if text in table:
        return str(table[text])
    if text[:1].isdigit():
        return text
    if text[:1].lower() == "v" and text[1:2].isdigit():
        return text
    return None


def load_version_slots(path: str | Path) -> dict[tuple[str, str], dict[str, str]]:
    """读取 AISimulate 的 ``query_versions.yaml``（system, backend -> slot -> version）。

    槽位别名（current / previous / next）本身**不是**版本号；能解析成实际版本才算身份明确。
    该文件由工具随包提供，解析失败时本函数返回空表（调用方据此判"不可确认"）。
    """
    try:
        doc = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}
    out: dict[tuple[str, str], dict[str, str]] = {}
    defaults = doc.get("defaults") or {}
    for backend, slots in defaults.items():
        if isinstance(slots, dict):
            out[("*", str(backend))] = {
                str(k): str(v) for k, v in slots.items() if v is not None
            }
    for system, backends in (doc.get("overrides") or {}).items():
        if not isinstance(backends, dict):
            continue
        for backend, slots in backends.items():
            if isinstance(slots, dict):
                out[(str(system), str(backend))] = {
                    str(k): str(v) for k, v in slots.items() if v is not None
                }
    return out


def aliases_for(
    slots: Mapping[tuple[str, str], Mapping[str, str]],
    *,
    system: str | None,
    backend: str | None,
) -> dict[str, str]:
    """取某个 (system, backend) 的槽位别名表；没有就退回默认值。"""
    if not backend:
        return {}
    table = slots.get((str(system), str(backend))) if system else None
    return dict(table or slots.get(("*", str(backend))) or {})


def find_version_slot_file(python: str | None) -> Path | None:
    """在**独立环境**里定位 ``query_versions.yaml``（只读；不 import 任何东西）。

    找不到就返回 ``None`` —— 调用方只能用配置里显式给出的别名。
    """
    if not python:
        return None
    try:
        probe = subprocess.run(
            [python, "-c", "import sysconfig; print(sysconfig.get_paths()['purelib'])"],
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if probe.returncode != 0:
        return None
    candidate = (
        Path(probe.stdout.strip()) / "aiconfigurator_core" / "systems" / "query_versions.yaml"
    )
    return candidate if candidate.exists() else None


def _same_name(left: str | None, right: str | None) -> bool | None:
    """规范化后比较名字；任一侧缺失返回 ``None``（未知 ≠ 相同）。"""
    if not left or not right:
        return None
    return " ".join(left.split()).strip().lower() == " ".join(right.split()).strip().lower()


# ---------------------------------------------------------------------------
# 身份完整性 / 可比性
# ---------------------------------------------------------------------------


def check_comparability(candidate: SimulationCandidate, target: SimulationTarget) -> None:
    """就地判定该候选能否与目标直接比较；不能时写明原因。"""
    reasons: list[str] = []
    dep, wl = candidate.deployment, candidate.workload

    model_same = _same_name(dep.model_name, target.model_name)
    if model_same is None:
        reasons.append("模型未声明：无法确认与目标是否同一模型")
    elif not model_same:
        reasons.append(f"模型不同：目标 {target.model_name}，模拟 {dep.model_name}")

    # 精度必须双方都明确且一致（P1：精度不一致的结果不得合并比较）
    if dep.model_precision is None:
        reasons.append("模拟的模型精度未声明：无法确认与目标精度一致")
    elif target.model_precision is None:
        reasons.append("目标精度未声明：无法确认与模拟精度一致")
    elif dep.model_precision != target.model_precision:
        reasons.append(
            f"模型精度不同：目标 {target.model_precision}，模拟 {dep.model_precision}"
        )

    system_same = _same_name(dep.system, target.system)
    if system_same is None:
        reasons.append("模拟未声明 system：无法确认与目标硬件一致")
    elif not system_same:
        reasons.append(f"硬件不同：目标 {target.system}，模拟 {dep.system}")

    # 后端与后端版本属于**证据身份**：性能数据是按版本标定的。
    if not dep.backend:
        reasons.append("模拟未声明后端：无法确认与目标的框架一致")
    elif target.backend is not None and dep.backend != target.backend:
        reasons.append(f"后端不同：目标 {target.backend}，模拟 {dep.backend}")

    if not dep.backend_version:
        reasons.append(
            "模拟的后端版本不可确认（槽位别名未解析或未声明）："
            "性能数据按版本标定，缺版本无法判为可比较"
        )
    elif target.backend_version is not None and dep.backend_version != target.backend_version:
        reasons.append(
            f"后端版本不同：目标 {target.backend_version}，模拟 {dep.backend_version}"
        )

    for label, want, got in (
        ("输入长度", target.input_len, wl.input_len),
        ("输出长度", target.output_len, wl.output_len),
    ):
        if want is None or got is None:
            reasons.append(f"{label}有一侧未声明：无法确认负载一致")
        elif want != got:
            reasons.append(f"{label}不同：目标 {want}，模拟 {got}")

    if (
        wl.ctx_tokens is not None
        and target.input_len is not None
        and wl.ctx_tokens != target.input_len
    ):
        candidate.notes.append(
            f"工作点提示：ctx_tokens={wl.ctx_tokens} ≠ 目标 ISL={target.input_len}；"
            "可用于相同请求负载下的配置比较，但不能与原生回放（ctx=ISL）"
            "视为同一工作点"
        )

    if target.concurrency is None or wl.concurrency is None:
        reasons.append("配置并发有一侧未声明：无法确认同一工作点")
    elif target.concurrency != wl.concurrency:
        reasons.append(
            f"配置并发不同：目标 {target.concurrency}，模拟 {wl.concurrency}"
            "（时延随并发变化，不同工作点不可直接比较）"
        )

    candidate.comparable = not reasons
    candidate.comparability_reasons = reasons


def check_tool_identity(
    tool: SimulationToolIdentity, target: SimulationToolIdentity | None
) -> list[str]:
    """后端 / 后端版本 / 数据库模式必须明确；与目标要求不一致时给出原因。"""
    problems: list[str] = []
    if not tool.backend:
        problems.append("模拟未声明后端：无法确认与目标的框架一致")
    if not tool.backend_version:
        problems.append("模拟未声明后端版本：性能数据是按版本标定的，缺版本无法复核")
    if not tool.database_mode:
        problems.append("模拟未声明数据库模式（SILICON / HYBRID / ...）：可复现性未知")
    if target is not None:
        if target.backend and tool.backend and target.backend != tool.backend:
            problems.append(f"后端不同：要求 {target.backend}，模拟 {tool.backend}")
        if (
            target.backend_version
            and tool.backend_version
            and target.backend_version != tool.backend_version
        ):
            problems.append(
                f"后端版本不同：要求 {target.backend_version}，模拟 {tool.backend_version}"
            )
    return problems


# ---------------------------------------------------------------------------
# P99 判定（只用 sim_p99_*，绝不采纳外部工具的均值结论）
# ---------------------------------------------------------------------------


def assess_verdict(candidate: SimulationCandidate, target: SimulationTarget) -> None:
    """就地算出 ``predicted_pass`` / ``predicted_fail`` / ``insufficient_prediction``。

    规则（全部由我们自己的阈值说了算）：

    * 缺 P99、或样本不足 -> ``insufficient_prediction``（**不得**用均值顶替 P99）；
    * 未设任何 P99 阈值 -> ``insufficient_prediction``（没有判据就不给结论）；
    * 任一项 P99 超阈 -> ``predicted_fail``；两项都不超 -> ``predicted_pass``。

    另外记录"均值达标但 P99 超限"这类口径陷阱，便于报告直接引用。
    """
    reasons: list[str] = []
    metrics = candidate.metrics

    if target.ttft_p99_ms_max is None and target.tpot_p99_ms_max is None:
        candidate.verdict = "insufficient_prediction"
        candidate.verdict_reasons = ["未给出任何 P99 阈值：没有判据，不给模拟结论"]
        return

    missing: list[str] = []
    if target.ttft_p99_ms_max is not None and metrics.sim_p99_ttft_ms is None:
        missing.append("TTFT P99")
    if target.tpot_p99_ms_max is not None and metrics.sim_p99_tpot_ms is None:
        missing.append("TPOT P99")
    if missing:
        reasons.append("缺少预测值：" + "、".join(missing))
        if metrics.sim_mean_ttft_ms is not None or metrics.sim_mean_tpot_ms is not None:
            reasons.append(
                "只有均值口径的预测（扫描器输出）；官方明确其 SLA 过滤是聚合均值、"
                "不是 P99，因此**不得**据此判定达标"
            )
        candidate.verdict = "insufficient_prediction"
        candidate.verdict_reasons = reasons
        return

    # 样本数：P99 必须有样本支撑（官方文档：零样本不是"很快"）
    sample_issues: list[str] = []
    for label, samples, needed in (
        ("TTFT", metrics.sim_ttft_samples, target.ttft_p99_ms_max is not None),
        ("TPOT", metrics.sim_tpot_samples, target.tpot_p99_ms_max is not None),
    ):
        if not needed:
            continue
        if samples is None:
            sample_issues.append(f"{label} 样本数未给出")
        elif samples < MIN_SAMPLES_FOR_P99:
            sample_issues.append(f"{label} 样本数仅 {samples}（< {MIN_SAMPLES_FOR_P99}）")
    if sample_issues:
        reasons.append("样本不足，P99 不可信：" + "、".join(sample_issues))
        candidate.verdict = "insufficient_prediction"
        candidate.verdict_reasons = reasons
        return

    failures: list[str] = []
    if target.ttft_p99_ms_max is not None and metrics.sim_p99_ttft_ms > target.ttft_p99_ms_max:
        failures.append(
            f"TTFT P99 {metrics.sim_p99_ttft_ms}ms > 阈值 {target.ttft_p99_ms_max}ms"
        )
    if target.tpot_p99_ms_max is not None and metrics.sim_p99_tpot_ms > target.tpot_p99_ms_max:
        failures.append(
            f"TPOT P99 {metrics.sim_p99_tpot_ms}ms > 阈值 {target.tpot_p99_ms_max}ms"
        )

    # 口径陷阱：均值达标但 P99 超限 —— 必须判 fail，并把陷阱写出来
    mean_passes = (
        (target.ttft_p99_ms_max is None or metrics.sim_mean_ttft_ms is None
         or metrics.sim_mean_ttft_ms <= target.ttft_p99_ms_max)
        and (target.tpot_p99_ms_max is None or metrics.sim_mean_tpot_ms is None
             or metrics.sim_mean_tpot_ms <= target.tpot_p99_ms_max)
    )
    if failures and mean_passes:
        reasons.append(
            "口径提示：均值口径看起来达标，但 P99 超限 —— "
            "只按均值判断会得出相反结论（官方 SLA 过滤即均值口径）"
        )

    candidate.verdict = "predicted_fail" if failures else "predicted_pass"
    candidate.verdict_reasons = reasons + failures
    _attach_decision_quality(candidate, target)


def _attach_decision_quality(
    candidate: SimulationCandidate, target: SimulationTarget
) -> None:
    """AB3：余量、敏感性、证据状态。只影响展示，不改变 verdict。"""
    metrics = candidate.metrics

    def margin(predicted: Decimal | None, limit: Decimal | None) -> tuple[Decimal | None, Decimal | None]:
        if predicted is None or limit is None or limit == 0:
            return None, None
        gap = limit - predicted
        return gap, gap / limit

    ttft_gap, ttft_ratio = margin(metrics.sim_p99_ttft_ms, target.ttft_p99_ms_max)
    tpot_gap, tpot_ratio = margin(metrics.sim_p99_tpot_ms, target.tpot_p99_ms_max)
    candidate.sla_margin_ttft_ms = ttft_gap
    candidate.sla_margin_tpot_ms = tpot_gap
    candidate.sla_margin_ttft_ratio = ttft_ratio
    candidate.sla_margin_tpot_ratio = tpot_ratio

    def sensitive(factor: Decimal) -> bool:
        for predicted, limit in (
            (metrics.sim_p99_ttft_ms, target.ttft_p99_ms_max),
            (metrics.sim_p99_tpot_ms, target.tpot_p99_ms_max),
        ):
            if predicted is not None and limit is not None and predicted * factor > limit:
                return True
        return False

    candidate.sensitive_10pct = sensitive(Decimal("1.1")) if candidate.verdict != "insufficient_prediction" else None
    candidate.sensitive_20pct = sensitive(Decimal("1.2")) if candidate.verdict != "insufficient_prediction" else None

    if candidate.verdict == "predicted_fail":
        candidate.evidence_status = "predicted_fail"
    elif candidate.verdict == "insufficient_prediction":
        candidate.evidence_status = "insufficient_prediction"
    elif candidate.sensitive_10pct or candidate.sensitive_20pct:
        candidate.evidence_status = "boundary_sensitive"
    else:
        candidate.evidence_status = "predicted_pass"


# ---------------------------------------------------------------------------
# 解析：扫描器 CSV
# ---------------------------------------------------------------------------


def parse_sweeper_row(
    row: Mapping[str, str],
    *,
    source_file: str,
    source_sha256: str | None = None,
    row_index: int | None = None,
    tool: SimulationToolIdentity | None = None,
) -> SimulationCandidate:
    """把扫描器 CSV 的一行转成候选。

    扫描器**不输出 P99**，因此它的 ``verdict`` 注定需要原始回放来补；
    这里只如实搬运与它给出的字段，不推断 P99。
    """
    dep_kwargs: dict[str, Any] = {}
    for column, attr in _SWEEPER_DEPLOYMENT_COLUMNS.items():
        raw = row.get(column)
        if attr in ("model_name", "system", "parallel", "backend", "backend_version"):
            value: Any = (str(raw).strip() or None) if raw is not None else None
        elif attr in ("memory_gib", "balance_score"):
            value = _dec(raw)
        else:
            value = _int(raw)
        if value is not None:
            dep_kwargs[attr] = value

    dep_kwargs["quant_modes"] = {
        column: str(row[column]).strip()
        for column in _QUANT_COLUMNS
        if row.get(column) not in (None, "", "none")
    }
    precision = precision_from_model_name(dep_kwargs.get("model_name"))
    inferred = None
    if precision is None:
        inferred = _precision_from_quant_modes(dep_kwargs["quant_modes"])
        precision = inferred
    dep_kwargs["model_precision"] = precision

    workload_kwargs: dict[str, Any] = {}
    for column, attr in _SWEEPER_WORKLOAD_COLUMNS.items():
        value = _int(row.get(column))
        if value is not None:
            workload_kwargs[attr] = value

    metric_kwargs: dict[str, Any] = {}
    for column, attr in _SWEEPER_METRIC_COLUMNS.items():
        value = _dec(row.get(column))
        if value is not None:
            metric_kwargs[attr] = value

    if dep_kwargs.get("batch_size") is None and workload_kwargs.get("concurrency") is not None:
        # pareto.csv 没有 bs 列；agg 模式下 bs ≡ 并发（官方 task_v2 注释：
        # "batch_size: concurrency (max in-flight requests)"）
        dep_kwargs["batch_size"] = workload_kwargs["concurrency"]

    concurrency = workload_kwargs.get("concurrency")
    candidate_id = (
        f"sim-sweeper:{dep_kwargs.get('model_name')}"
        f"@{dep_kwargs.get('system')}"
        f"-{dep_kwargs.get('parallel') or 'parallel?'}"
        f"-c{concurrency if concurrency is not None else '?'}"
        f"-ctx{workload_kwargs.get('ctx_tokens', '?')}"
    )
    candidate = SimulationCandidate(
        candidate_id=candidate_id,
        deployment=SimulationDeployment(**dep_kwargs),
        workload=SimulationWorkload(**workload_kwargs),
        metrics=SimulationMetrics(**metric_kwargs),
        source_file=source_file,
        source_sha256=source_sha256,
        source_row=row_index,
    )
    if inferred is not None:
        candidate.notes.append(
            f"模型精度 {inferred} 由权重量化列（gemm/moe）推断，模型名未带精度后缀"
        )
    return candidate


def _serving_mode_from_path(path: Path) -> str:
    """CSV 里没有 serving_mode 列：从保存目录名（agg / disagg）推断，推不出就 unknown。"""
    parts = {part.lower() for part in path.parts}
    if "disagg" in parts:
        return "disagg"
    if "agg" in parts:
        return "agg"
    return "unknown"


def parse_sweeper_csv(
    path: str | Path,
    *,
    tool: SimulationToolIdentity | None = None,
    serving_mode: str | None = None,
) -> list[SimulationCandidate]:
    """解析 ``best_config_topn.csv`` / ``pareto.csv``。

    CSV 本身没有 serving_mode 列：先看保存目录名，再看调用方给的声明
    （``exp_config.yaml`` 里的 ``serving_mode``），都没有就记 ``unknown``。
    """
    path = Path(path)
    digest = sha256_file(path)
    mode = _serving_mode_from_path(path)
    if mode == "unknown" and serving_mode in ("agg", "disagg"):
        mode = serving_mode
    with path.open(encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    out: list[SimulationCandidate] = []
    for index, row in enumerate(rows):
        candidate = parse_sweeper_row(
            row, source_file=str(path), source_sha256=digest, row_index=index
        )
        candidate.deployment.serving_mode = mode  # type: ignore[assignment]
        out.append(candidate)
    return out


# ---------------------------------------------------------------------------
# 解析：原生回放
# ---------------------------------------------------------------------------


def parse_native_input_yaml(
    path: str | Path,
    *,
    model_precision: str | None = None,
    version_aliases: Mapping[str, str] | None = None,
) -> tuple[SimulationDeployment, SimulationWorkload]:
    """从原生预测的输入 YAML 取回身份与负载（``prediction.json`` 里没有身份）。

    原生回放**不在输出里回显模型精度**（它按 HF config 自行推断）。因此当模型名不带
    精度后缀时，需要调用方把"我们要求模拟的精度"作为**输入身份**传进来；
    解析结果会带一条 ``note`` 说明这一点，不假装是工具回显的。
    """
    doc = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    engine = doc.get("engine") or {}
    traffic = doc.get("traffic") or {}
    source = traffic.get("source") or {}
    load = traffic.get("load") or {}
    stop = traffic.get("stop") or {}
    workers = (engine.get("workers") or {}).get("aggregated") or {}
    parallel = workers.get("parallelism") or {}

    backend = engine.get("backend")
    if isinstance(backend, dict):  # 推荐式写法：{choices: [...]}
        backend = None
    deployment = SimulationDeployment(
        model_name=engine.get("model"),
        model_precision=(
            precision_from_model_name(engine.get("model")) or model_precision
        ),
        system=engine.get("hardware"),
        serving_mode={
            "aggregated": "agg",
            "disaggregated": "disagg",
        }.get(str(engine.get("mode")), "unknown"),  # type: ignore[arg-type]
        tp_size=parallel.get("tensor"),
        pp_size=parallel.get("pipeline"),
        dp_size=None,  # 原生 schema 的 replicas ≠ 数据并行，不混为一谈
        moe_tp_size=parallel.get("moe_tensor"),
        moe_ep_size=parallel.get("moe_expert"),
        replicas=parallel.get("replicas"),
        gpu_count=None,
        backend=str(backend) if backend else None,
        backend_version=resolve_backend_version(
            engine.get("backend_version"), version_aliases
        ),
        quant_modes={},
    )
    workload = SimulationWorkload(
        input_len=source.get("input_tokens"),
        output_len=source.get("output_tokens"),
        concurrency=load.get("concurrency"),
        stop_condition=", ".join(f"{k}={v}" for k, v in stop.items()) or None,
        num_requests=stop.get("requests"),
    )
    return deployment, workload


def parallel_identity_gaps(deployment: SimulationDeployment) -> list[str]:
    """推荐所需的并行身份缺口；未知一律保留，不默认成 1。"""
    gaps: list[str] = []
    if deployment.tp_size is None:
        gaps.append("缺 TP")
    if deployment.dp_size is None:
        gaps.append("缺 DP（未给出可推导的 replicas，保留未知，不以 DP1 代替）")
    if deployment.pp_size is None:
        gaps.append("缺 PP（未声明 pipeline，且无法从 GPU 数推导）")
    elif deployment.pp_size != 1:
        gaps.append(f"PP={deployment.pp_size}（本系统不建模流水线并行）")
    return gaps


def derive_native_identity(
    deployment: SimulationDeployment, *, gpus_per_worker: int | None
) -> tuple[SimulationDeployment, list[str]]:
    """从原生回放的 aggregated 拓扑推导 PP/DP/GPU 数，并记录依据。

    原生 schema 只有 ``parallelism.tensor / pipeline / replicas``：
    * ``pipeline`` 缺省且 ``decode_gpus_per_worker == tensor`` → PP1；
    * 单一 aggregated worker 组下 ``replicas`` 即模型副本数 → DP；
    * 总卡数 = ``decode_gpus_per_worker × replicas``。
    任何一项推不出来就保持未知，由 ``parallel_identity_gaps`` 阻止推荐。
    """
    notes: list[str] = []
    updates: dict[str, Any] = {}
    tp = deployment.tp_size
    pp = deployment.pp_size
    dp = deployment.dp_size
    replicas = deployment.replicas

    if pp is None and tp is not None and gpus_per_worker == tp:
        pp = 1
        updates["pp_size"] = 1
        notes.append(
            f"PP1 由输入未声明 pipeline 且 decode_gpus_per_worker={gpus_per_worker} "
            f"== tensor={tp} 推导（aggregated 单 worker）"
        )
    if dp is None and replicas is not None and replicas > 0:
        dp = replicas
        updates["dp_size"] = dp
        notes.append(
            f"DP{dp} 由 workers.aggregated.replicas={replicas} 推导"
            "（单一 aggregated worker 组；原生 schema 无独立 dp 维度）"
        )
    elif dp is None:
        notes.append("DP 未声明且无可推导的 replicas：保留未知，不以 DP1 代替")
    if (
        deployment.gpu_count is None
        and gpus_per_worker is not None
        and gpus_per_worker > 0
        and replicas is not None
        and replicas > 0
    ):
        total = gpus_per_worker * replicas
        updates["gpu_count"] = total
        notes.append(
            f"GPU 总数 {total} 由 decode_gpus_per_worker={gpus_per_worker} "
            f"× replicas={replicas} 推导"
        )

    if updates:
        deployment = deployment.model_copy(update=updates)
    return deployment, notes


def parse_native_prediction(
    path: str | Path,
    *,
    deployment: SimulationDeployment,
    workload: SimulationWorkload,
    tool: SimulationToolIdentity | None = None,
) -> SimulationCandidate:
    """解析 ``prediction.json``（原生回放，含 P99）。"""
    path = Path(path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    # 只认 decode worker 的卡数；prefill 数量属于另一种 worker，不能拿来
    # 推导解码拓扑（disagg 本身也不在本系统建模范围内）。
    per_worker = _int(payload.get("decode_gpus_per_worker"))
    if per_worker is not None and per_worker <= 0:
        per_worker = None
    deployment, identity_notes = derive_native_identity(
        deployment, gpus_per_worker=per_worker
    )
    metric_kwargs: dict[str, Any] = {}
    for field_name, attr in _NATIVE_METRIC_FIELDS:
        value = _dec(payload.get(field_name))
        if value is not None:
            metric_kwargs[attr] = value
    for field_name, attr in _NATIVE_COUNT_FIELDS:
        value = _int(payload.get(field_name))
        if value is not None:
            metric_kwargs[attr] = value

    # 原生回放只报集群级吞吐；按卡数归一化，便于跨卡数比较（保留原值不动）
    gpu_count = deployment.gpu_count
    if gpu_count is None:
        gpu_hours = _dec(payload.get("gpu_hours"))
        duration_ms = _dec(payload.get("duration_ms"))
        if gpu_hours is not None and duration_ms:
            gpu_count = int(
                (gpu_hours / (duration_ms / Decimal(3600_000))).to_integral_value()
            )
            deployment = deployment.model_copy(update={"gpu_count": gpu_count})
    throughput = metric_kwargs.get("sim_output_throughput_tok_s")
    if throughput is not None and gpu_count:
        metric_kwargs["sim_tokens_per_gpu"] = (throughput / Decimal(gpu_count)).quantize(
            Decimal("0.001")
        )

    dep = deployment
    parallel_bits = "".join(
        f"{name}{value}"
        for name, value in (("tp", dep.tp_size), ("pp", dep.pp_size), ("dp", dep.dp_size))
        if value is not None
    ) or "parallel?"
    candidate_id = (
        f"sim-native:{dep.model_name}@{dep.system}-{parallel_bits}"
        f"-c{workload.concurrency}-n{workload.num_requests}"
    )
    return SimulationCandidate(
        candidate_id=candidate_id,
        deployment=dep,
        workload=workload,
        metrics=SimulationMetrics(**metric_kwargs),
        notes=list(identity_notes),
        source_file=str(path),
        source_sha256=sha256_file(path),
        source_row=None,
    )


# ---------------------------------------------------------------------------
# 工具身份：从归档的 exp_config.yaml / per_ops_source.json 读回
# ---------------------------------------------------------------------------

_CORRECTION_FACTOR_KEYS: tuple[str, ...] = (
    "prefill_latency_correction",
    "decode_latency_correction",
    "encoder_latency_correction",
    "rate_match_prefill_degradation",
    "rate_match_decode_degradation",
    "rate_match_encoder_degradation",
    "autoscale_ttft_correction_factor",
)


def tool_identity_from_archives(
    *,
    exp_config: str | Path | None = None,
    per_ops_source: str | Path | None = None,
    tool_version: str | None = None,
    surface: str = "sweeper",
    backend: str | None = None,
) -> SimulationToolIdentity:
    """从归档文件恢复"这次模拟是谁算的、用什么数据算的"。"""
    identity = SimulationToolIdentity(
        tool_version=tool_version, surface=surface, backend=backend  # type: ignore[arg-type]
    )
    if exp_config is not None:
        path = Path(exp_config)
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        identity.backend = backend or doc.get("backend_name") or identity.backend
        identity.backend_version = doc.get("backend_version")
        identity.system = doc.get("system_name")
        identity.database_mode = doc.get("database_mode")
        identity.search_space_file = str(path)
        identity.search_space_sha256 = sha256_file(path)
        identity.correction_factors = {
            key: _dec(doc[key])
            for key in _CORRECTION_FACTOR_KEYS
            if _dec(doc.get(key)) is not None
        }
    if per_ops_source is not None:
        path = Path(per_ops_source)
        doc = json.loads(path.read_text(encoding="utf-8"))
        flat: dict[str, str] = {}
        for phase, ops in (doc or {}).items():
            if isinstance(ops, dict):
                for op_name, origin in ops.items():
                    flat[f"{phase}.{op_name}"] = str(origin)
        identity.data_provenance = flat
    return identity


# ---------------------------------------------------------------------------
# 外部命令（P2 第二阶段：**默认关闭**）
# ---------------------------------------------------------------------------

#: 允许的入口（**console script 名** + 子命令）；不做任意命令执行。
#: 注意 ``python -m aiconfigurator.cli`` 不是有效入口（它是包，没有 __main__），
#: 必须直接用 wheel 装的 console script。
_EXTERNAL_ENTRYPOINTS: dict[str, tuple[str, ...]] = {
    "sweeper": ("aiconfigurator", "cli", "exp"),
    "native_replay": ("aisimulate", "predict"),
}


class SimulationNotAvailable(RuntimeError):
    """外部工具不可用（未安装、未开启或版本不支持）。"""


@dataclass(frozen=True)
class ExternalToolConfig:
    """外部工具的运行方式。**python 必须是独立环境**，不与主环境合并依赖。"""

    python: str | None = None
    enabled: bool = False
    timeout_s: int = 1800


def resolve_python(config: ExternalToolConfig | None, *, environ: Mapping[str, str] | None = None) -> str | None:
    """解析独立环境的解释器：显式配置 > 环境变量 > PATH 上的 ``aisimulate``。"""
    env = environ if environ is not None else os.environ
    if config is not None and config.python:
        return config.python if Path(config.python).exists() else None
    from_env = env.get("AISIMULATE_PYTHON")
    if from_env:
        return from_env if Path(from_env).exists() else None
    found = shutil.which("aisimulate")
    if found:
        return str(Path(found).resolve().parent / "python")
    return None


def external_tool_available(config: ExternalToolConfig | None, *, environ: Mapping[str, str] | None = None) -> bool:
    if config is None or not config.enabled:
        return False
    python = resolve_python(config, environ=environ)
    if python is None:
        return False
    try:
        probe = subprocess.run(
            [python, "-c", "import aiconfigurator, aisimulate"],
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return probe.returncode == 0


def resolve_executable(surface: str, python: str | None = None) -> list[str]:
    """定位 console script（优先用独立环境自己的 bin 目录）。

    找不到就明确报错 —— 不退化成 ``python -m``（那个入口不存在）。
    """
    entry = _EXTERNAL_ENTRYPOINTS.get(surface)
    if entry is None:
        raise ValueError(f"不支持的外部入口：{surface}")
    name = entry[0]
    if python:
        candidate = Path(python).parent / name
        if candidate.exists():
            return [str(candidate)]
    found = shutil.which(name)
    if found:
        return [found]
    raise SimulationNotAvailable(
        f"找不到可执行文件 {name}：请在独立环境里安装 aisimulate，"
        "并用 AISIMULATE_PYTHON 指向该环境的解释器"
    )


def build_command(
    surface: str,
    *,
    config_arg: str | Path,
    save_dir: str | Path | None = None,
    extra: Sequence[str] = (),
) -> list[str]:
    """构造**参数**列表（不含可执行文件；**不做 shell 解析**）。"""
    entry = _EXTERNAL_ENTRYPOINTS.get(surface)
    if entry is None:
        raise ValueError(f"不支持的外部入口：{surface}")
    argv: list[str] = list(entry[1:])
    argv += ["--config" if surface == "native_replay" else "--yaml-path", str(config_arg)]
    if surface == "native_replay":
        argv += ["--stack", "engine"]
        # 输出目录归我们所有：重复运行同一配置时，工具默认拒绝写非空目录，这里显式覆盖
        argv += ["--overwrite"]
    if save_dir is not None:
        argv += ["--output-dir" if surface == "native_replay" else "--save-dir", str(save_dir)]
    argv += list(extra)
    return argv


def run_external(
    surface: str,
    *,
    config_arg: str | Path,
    save_dir: str | Path | None = None,
    extra: Sequence[str] = (),
    config: ExternalToolConfig | None = None,
    environ: Mapping[str, str] | None = None,
) -> Path:
    """执行外部命令并返回输出目录。未开启/不可用一律抛 :class:`SimulationNotAvailable`。"""
    if config is None or not config.enabled:
        raise SimulationNotAvailable(
            "未开启外部工具执行（enable_external=False）：本次只读本地归档"
        )
    python = resolve_python(config, environ=environ)
    if python is None:
        raise SimulationNotAvailable(
            "找不到 AISimulate 独立环境；请设置 AISIMULATE_PYTHON 指向该环境的解释器"
        )
    argv = [
        *resolve_executable(surface, python),
        *build_command(surface, config_arg=config_arg, save_dir=save_dir, extra=extra),
    ]
    try:
        result = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            check=False,
            timeout=config.timeout_s,
        )
    except subprocess.TimeoutExpired as exc:
        # 超时是可预期的失败：转成受控异常，让调用方按"单个候选失败"处理
        raise SimulationNotAvailable(
            f"AISimulate 执行超时（{config.timeout_s}s）：{exc.cmd[0] if exc.cmd else surface}"
        ) from exc
    except OSError as exc:
        raise SimulationNotAvailable(
            f"AISimulate 无法启动：{type(exc).__name__}: {exc}"
        ) from exc
    if result.returncode != 0:
        raise SimulationNotAvailable(
            f"AISimulate 执行失败（exit {result.returncode}）：{(result.stderr or '').strip()[-500:]}"
        )
    if save_dir is None:
        raise SimulationNotAvailable("未指定输出目录，无法归档结果")
    return Path(save_dir)


# ---------------------------------------------------------------------------
# 离线装载：P1 验收的入口
# ---------------------------------------------------------------------------


def load_archived(
    *,
    sweeper_csv: str | Path | None = None,
    native_prediction: str | Path | None = None,
    native_input: str | Path | None = None,
    native_model_precision: str | None = None,
    version_aliases: Mapping[str, str] | None = None,
    exp_config: str | Path | None = None,
    per_ops_source: str | Path | None = None,
    tool_version: str | None = None,
    default_backend: str | None = None,
    target: SimulationTarget | None = None,
    top_n: int | None = None,
    archives: Sequence[Any] | None = None,
) -> SimulationResult:
    """只读本地归档输出，拼出一个 :class:`SimulationResult`。

    ``archives`` 是多部署归档列表；每条独立解析，互不覆盖。它与顶层
    ``native_prediction`` 可以同时存在（兼容旧调用）。
    """
    candidates: list[SimulationCandidate] = []
    limitations: list[str] = []
    archives = list(archives or [])

    def archive_field(spec: Any, name: str) -> Any:
        if isinstance(spec, Mapping):
            return spec.get(name)
        return getattr(spec, name, None)

    tool = tool_identity_from_archives(
        exp_config=exp_config,
        per_ops_source=per_ops_source,
        tool_version=tool_version,
        surface=(
            "sweeper"
            if sweeper_csv is not None
            and native_prediction is None
            and not archives
            else "native_replay"
            if native_prediction is not None and sweeper_csv is None and not archives
            else "mixed"
        ),
        backend=default_backend,
    )
    if archives:
        first = archives[0]
        tool = tool_identity_from_archives(
            exp_config=archive_field(first, "exp_config") or exp_config,
            per_ops_source=archive_field(first, "per_ops_source") or per_ops_source,
            tool_version=archive_field(first, "tool_version") or tool_version,
            surface="mixed" if sweeper_csv is not None else "native_replay",
            backend=default_backend,
        )
        # 多个原生回放归档可能来自不同搜索配置：不把某个搜索的修正系数
        # 施加到所有原生 P99 上；原生回放输出本身就是最终预测值。
        tool = tool.model_copy(update={"correction_factors": {}})
        limitations.append(
            "多部署归档：各候选独立解析；搜索阶段的修正系数不跨归档合并，"
            "原生 P99 不额外乘系数"
        )

    declared_mode: str | None = None
    if exp_config is not None:
        declared = (yaml.safe_load(Path(exp_config).read_text(encoding="utf-8")) or {}).get(
            "serving_mode"
        )
        declared_mode = declared if declared in ("agg", "disagg") else None

    if sweeper_csv is not None:
        candidates += parse_sweeper_csv(
            sweeper_csv, tool=tool, serving_mode=declared_mode
        )
        limitations.append(
            "扫描器输出**不含 P99**（只有稳态单点估计，实测约等于原生回放的中位数）："
            "因此扫描器候选一律无法给出 P99 判定，需要原生回放补测"
        )

    if native_prediction is not None:
        if native_input is None:
            raise ValueError("解析原生回放结果必须同时给出它的输入 YAML（身份不在结果里）")
        deployment, workload = parse_native_input_yaml(
            native_input,
            model_precision=native_model_precision,
            version_aliases=version_aliases,
        )
        native_candidate = parse_native_prediction(
            native_prediction, deployment=deployment, workload=workload
        )
        if native_model_precision and precision_from_model_name(deployment.model_name) is None:
            native_candidate.notes.append(
                f"模型精度 {native_model_precision} 由**输入身份**给出"
                "（原生回放输出不回显精度，它按 HF config 自行推断）"
            )
        candidates.append(native_candidate)

    for spec in archives:
        prediction_path = archive_field(spec, "native_prediction")
        input_path = archive_field(spec, "native_input")
        if not prediction_path or not input_path:
            limitations.append("有一条多部署归档缺少 prediction/input，已跳过")
            continue
        declared_precision = (
            archive_field(spec, "native_model_precision") or native_model_precision
        )
        deployment, workload = parse_native_input_yaml(
            str(input_path),
            model_precision=declared_precision,
            version_aliases=version_aliases,
        )
        archive_candidate = parse_native_prediction(
            str(prediction_path), deployment=deployment, workload=workload
        )
        if declared_precision and precision_from_model_name(deployment.model_name) is None:
            archive_candidate.notes.append(
                f"模型精度 {declared_precision} 由**输入身份**给出"
                "（原生回放输出不回显精度）"
            )
        label = archive_field(spec, "label")
        if label:
            archive_candidate.notes.append(f"归档：{label}")
        candidates.append(archive_candidate)

    if archives:
        identities = {
            (c.deployment.backend, c.deployment.backend_version, c.deployment.system)
            for c in candidates
            if c.candidate_id.startswith("sim-native")
        }
        if len(identities) > 1:
            limitations.append(
                "多部署归档的身份（后端/版本/system）不一致：每个候选保留自己的"
                "身份，不合并成一个全局结论"
            )
        modes = set()
        for spec in archives:
            exp_path = archive_field(spec, "exp_config")
            if not exp_path:
                modes.add(None)
                continue
            try:
                exp_doc = yaml.safe_load(
                    Path(exp_path).read_text(encoding="utf-8")
                ) or {}
            except Exception:  # noqa: BLE001
                modes.add(None)
                continue
            modes.add(exp_doc.get("database_mode"))
        if len(modes) > 1 or None in modes:
            tool = tool.model_copy(update={"database_mode": None})
            limitations.append(
                "多部署归档的数据库模式不一致或缺失：不宣称统一的可复现性等级"
            )

    if not candidates:
        return SimulationResult(
            status="not_configured",
            tool=tool,
            message="未提供任何归档输出（sweeper_csv / native_prediction）",
        )

    effective_target = target if target is not None else SimulationTarget()
    for candidate in candidates:
        check_comparability(candidate, effective_target)
        assess_verdict(candidate, effective_target)

    if top_n is not None and top_n > 0:
        # 短名单**在判完可比性之后**裁：优先保留能与目标直接比较的候选，
        # 否则"吞吐最高的 3 条"很可能恰好全是不可比的工作点，短名单就失去意义。
        # 原生候选数量本来就少、且是唯一能给出 P99 的来源，因此不参与裁剪。
        native = [c for c in candidates if c.candidate_id.startswith("sim-native")]
        sweeper = [c for c in candidates if not c.candidate_id.startswith("sim-native")]
        sweeper.sort(
            key=lambda c: (
                not c.comparable,
                -(c.metrics.sim_output_throughput_tok_s or Decimal(0)),
            )
        )
        candidates = sweeper[:top_n] + native
        if target is not None:
            limitations.append(
                f"扫描器候选共 {len(sweeper) + len(native) - len(native)} 行，"
                f"报告只展示其中的 {len(candidates) - len(native)} 行"
                "（优先展示能与目标直接比较的工作点）"
            )

    expected_tool = (
        SimulationToolIdentity(backend=target.backend, backend_version=target.backend_version)
        if target is not None
        else None
    )
    for problem in check_tool_identity(tool, expected_tool):
        limitations.append(problem)

    if tool.surface == "mixed":
        limitations.append(
            "同时使用了配置扫描器与原生回放两种输出：两者口径不同"
            "（扫描器=稳态单点，原生=含尾部的分布），只**分别列示**，不合并为同一结论"
        )
    if tool.database_mode and tool.database_mode.upper() != "SILICON":
        limitations.append(
            f"数据库模式为 {tool.database_mode}：官方声明只有 SILICON 的结果可复现，"
            "其余模式仅供研究"
        )
    if tool.data_provenance:
        empirical = sorted(
            name for name, origin in tool.data_provenance.items() if origin == "empirical"
        )
        if empirical:
            limitations.append(
                f"即使 SILICON 模式也含经验算子 {len(empirical)} 个"
                f"（例如 {', '.join(empirical[:3])} ...）：不是全部来自实测"
            )
    if tool.correction_factors:
        limitations.append(
            "搜索默认叠加了经验修正系数（非实测）："
            + ", ".join(f"{k}={v}" for k, v in sorted(tool.correction_factors.items()))
        )
    limitations.append(
        "模拟结论**不是实测**：不得写入 BenchmarkRun / CapacityPoint，"
        "不得产生 observed_pass / observed_fail"
    )

    source = sweeper_csv or native_prediction
    if source is None and archives:
        source = archive_field(archives[0], "native_prediction")
    audit = None
    if archives:
        native = [c for c in candidates if c.candidate_id.startswith("sim-native")]
        audit = SimulationSearchAudit(
            discovered_deployments=len(archives),
            accepted_deployments=len(archives),
            replayed_deployments=len(native),
            effective_scope="offline_archives",
            replayed=[c.candidate_id for c in native],
        )
    return SimulationResult(
        status="computed",
        data_trust=SIMULATION_TRUST_LABEL,
        tool=tool,
        candidates=candidates,
        limitations=limitations,
        source_dir=str(Path(source).parent) if source else None,
        search_audit=audit,
    )


# ---------------------------------------------------------------------------
# R2：自动两段式（搜索 -> 资源过滤 -> Top-N -> 原生 P99 回放）
# ---------------------------------------------------------------------------

#: 搜索阶段刻意给一个**很松**的 SLA 边界：工具自带的过滤是**聚合均值**口径，
#: 我们不用它做判断（官方原文：It is not a p99 constraint）。松边界 = 不筛。
_LOOSE_BOUND = Decimal("1000000")

#: 一轮评估最多回放多少个**不同部署**（安全上限，不是推荐名次）
_MAX_DEPLOYMENT_REPLAYS = 8


@dataclass(frozen=True)
class SearchSpec:
    """自动搜索所需的输入（从 PlanRequest 提取，不引入第二份用户 YAML）。"""

    model_name: str
    model_precision: str | None
    system: str
    input_len: int
    output_len: int
    concurrency: int
    backend: str | None = None
    backend_version: str | None = None
    max_total_gpus: int | None = None
    database_mode: str = "SILICON"
    top_n: int = 3
    ttft_p99_ms_max: Decimal | None = None
    tpot_p99_ms_max: Decimal | None = None


def _gpu_candidates(limit: int | None) -> list[int]:
    sizes = [1, 2, 4, 8, 16, 32]
    if limit is None:
        return [1, 2, 4, 8]
    return [size for size in sizes if size <= limit] or [1]


def build_search_config(spec: SearchSpec) -> dict[str, Any]:
    """生成扫描器的 exp YAML（**由需求推导**，不让用户再写一份模拟配置）。"""
    if not spec.model_name:
        raise ValueError("自动搜索需要模型名")
    if not spec.system:
        raise ValueError("自动搜索需要 system（由 hardware_id 映射而来）")
    gpus = _gpu_candidates(spec.max_total_gpus)
    backend = spec.backend or "trtllm"
    entry: dict[str, Any] = {
        "serving_mode": "agg",
        "model_path": spec.model_name,
        "system_name": spec.system,
        "backend_name": backend,
        "isl": spec.input_len,
        "osl": spec.output_len,
        "ttft": float(_LOOSE_BOUND),
        "tpot": float(_LOOSE_BOUND),
        "total_gpus": max(gpus),
        "database_mode": spec.database_mode,
        "agg_num_gpu_candidates": gpus,
        "agg_tp_candidates": [size for size in gpus if size <= 8] or [1],
        "agg_pp_candidates": [1],
        "agg_dp_candidates": [1],
        "agg_moe_tp_candidates": [1],
        "agg_moe_ep_candidates": [1],
        "agg_cp_candidates": [1],
    }
    if spec.backend_version:
        entry["backend_version"] = spec.backend_version
    return {f"auto_{spec.model_name.replace('/', '_')}": entry}


def deployment_identity_reasons(
    candidate: SimulationCandidate, target: Any
) -> list[str]:
    """只检查**部署身份**（不含 ISL/OSL/并发/ctx）。

    AA2 用它从搜索结果里发现可回放的部署；扫描行的负载只说明它当时
    被搜索到的工作点，回放会按用户目标负载重新构造。
    """
    dep = candidate.deployment
    reasons: list[str] = []
    model_same = _same_name(dep.model_name, getattr(target, "model_name", None))
    if model_same is None:
        reasons.append("模型未声明：无法确认部署身份")
    elif not model_same:
        reasons.append(
            f"模型不同：目标 {getattr(target, 'model_name', None)}，搜索 {dep.model_name}"
        )
    want_precision = getattr(target, "model_precision", None)
    if dep.model_precision is None:
        reasons.append("搜索未声明模型精度：无法确认部署身份")
    elif want_precision is not None and dep.model_precision != want_precision:
        reasons.append(f"模型精度不同：目标 {want_precision}，搜索 {dep.model_precision}")
    system_same = _same_name(dep.system, getattr(target, "system", None))
    if system_same is None:
        reasons.append("搜索未声明 system：无法确认硬件")
    elif not system_same:
        reasons.append(
            f"硬件不同：目标 {getattr(target, 'system', None)}，搜索 {dep.system}"
        )
    if not dep.backend:
        reasons.append("搜索未声明后端")
    elif getattr(target, "backend", None) and dep.backend != target.backend:
        reasons.append(f"后端不同：搜索用 {dep.backend}，要求 {target.backend}")
    if not dep.backend_version:
        reasons.append("搜索未声明后端版本")
    elif (
        getattr(target, "backend_version", None)
        and dep.backend_version != target.backend_version
    ):
        reasons.append(
            f"后端版本不同：搜索用 {dep.backend_version}，要求 {target.backend_version}"
        )
    if dep.tp_size is None:
        reasons.append("搜索未给出 TP：无法构造回放配置")
    if dep.dp_size not in (None, 1):
        reasons.append(f"DP={dep.dp_size}：原生回放的并行配置无法表达 data parallel")
    if dep.pp_size not in (None, 1):
        reasons.append(f"PP={dep.pp_size}：原生回放只支持 pipeline=1")
    return reasons


def native_alignment_reasons(
    candidate: SimulationCandidate, spec: SearchSpec
) -> list[str]:
    """这条搜索结果能否用原生回放**精确对齐**；不能就写明原因。

    保留给旧的调用方/测试：除了部署身份外，还要求扫描行自身的
    ``ctx_tokens`` 与 ISL 一致。AA2 的部署发现流程使用
    :func:`deployment_identity_reasons`，不把扫描行负载当成目标负载。
    """
    reasons = deployment_identity_reasons(candidate, spec)
    dep, wl = candidate.deployment, candidate.workload
    if wl.ctx_tokens is not None and wl.input_len is not None and wl.ctx_tokens != wl.input_len:
        reasons.append(
            f"每步上下文预算 ctx_tokens={wl.ctx_tokens} ≠ ISL={wl.input_len}："
            "与原生回放的整段 prefill 不是同一口径"
        )
    return reasons


def build_predict_config(
    spec: SearchSpec, candidate: SimulationCandidate, *, requests: int = 500
) -> dict[str, Any]:
    """为一个**部署**生成原生回放输入。

    回放的负载一律取 ``spec``（用户目标），不取扫描行自身的并发/长度；
    扫描行只贡献部署拓扑（TP/PP/replicas/后端/版本）。
    """
    reasons = deployment_identity_reasons(candidate, spec)
    if reasons:
        raise ValueError("该候选无法与原生回放对齐：" + "；".join(reasons))
    dep = candidate.deployment
    return {
        "engine": {
            "mode": "aggregated",
            "model": spec.model_name,
            "hardware": spec.system,
            "backend": dep.backend,
            "backend_version": dep.backend_version,
            "workers": {
                "aggregated": {
                    "parallelism": {
                        "tensor": dep.tp_size,
                        "pipeline": 1,
                        "replicas": dep.replicas or 1,
                    }
                }
            },
        },
        "traffic": {
            "source": {
                "type": "synthetic",
                "input_tokens": spec.input_len,
                "output_tokens": spec.output_len,
            },
            "load": {"type": "concurrency", "concurrency": spec.concurrency},
            "stop": {"requests": int(requests)},
        },
    }


def new_run_id() -> str:
    """生成唯一运行 ID：UTC 时间戳 + 随机后缀（不依赖目录里的既有名称）。"""
    from datetime import datetime, timezone
    from uuid import uuid4

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"run_{stamp}_{uuid4().hex[:6]}"


def _write_yaml(directory: Path, name: str, doc: Any) -> tuple[Path, str]:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(yaml.safe_dump(doc, allow_unicode=True), encoding="utf-8")
    return path, sha256_file(path)


def discover_outputs(out_dir: str | Path, surface: str) -> dict[str, str]:
    """在外部工具输出目录里找结果文件（新→旧）；找不到返回空。"""
    root = Path(out_dir)
    if not root.exists():
        return {}
    wanted = (
        {"pareto.csv": "sweeper_csv", "best_config_topn.csv": "sweeper_csv"}
        if surface == "sweeper"
        else {"prediction.json": "native_prediction"}
    )
    found: dict[str, str] = {}
    for pattern, key in wanted.items():
        if key in found:
            continue
        hits = sorted(root.rglob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
        if hits:
            found[key] = str(hits[0])
    return found


def _replay_one(
    spec: SearchSpec,
    candidate: SimulationCandidate,
    *,
    index: int,
    root: Path,
    tool_config: ExternalToolConfig,
    predict_requests: int,
    version_aliases: Mapping[str, str] | None,
    effective_target: SimulationTarget,
    manifest: dict[str, Any],
) -> SimulationCandidate:
    """对**一个**候选做原生回放。任何异常都向上抛，由调用方按候选隔离。"""
    predict_cfg = build_predict_config(spec, candidate, requests=predict_requests)
    predict_path, predict_sha = _write_yaml(
        root / "predict", f"predict_{index}.yaml", predict_cfg
    )
    manifest["inputs"][str(predict_path.relative_to(root))] = predict_sha

    predict_out = run_external(
        "native_replay",
        config_arg=predict_path,
        save_dir=root / "predict" / f"out_{index}",
        config=tool_config,
    )
    produced = discover_outputs(predict_out, "native_replay")
    if not produced.get("native_prediction"):
        raise SimulationNotAvailable("回放输出里没有 prediction.json")
    for path in predict_out.rglob("*"):
        if path.is_file():
            manifest["outputs"][str(path.relative_to(root))] = sha256_file(path)

    deployment, workload = parse_native_input_yaml(
        predict_path,
        model_precision=spec.model_precision,
        version_aliases=version_aliases,
    )
    native_candidate = parse_native_prediction(
        produced["native_prediction"], deployment=deployment, workload=workload
    )
    native_candidate.linked_search_candidate_id = candidate.candidate_id
    native_candidate.deployment.backend = candidate.deployment.backend
    native_candidate.deployment.backend_version = candidate.deployment.backend_version
    native_candidate.notes.append(
        f"部署来自搜索结果 `{candidate.candidate_id}`；"
        f"回放负载按目标 ISL={spec.input_len}/OSL={spec.output_len}/"
        f"并发={spec.concurrency} 重新构造"
    )
    check_comparability(native_candidate, effective_target)
    assess_verdict(native_candidate, effective_target)
    return native_candidate


def run_two_stage(
    spec: SearchSpec,
    *,
    tool_config: ExternalToolConfig,
    work_dir: str | Path,
    target: SimulationTarget | None = None,
    accept: Any = None,
    predict_requests: int = 500,
    tool_version: str | None = None,
    version_aliases: Mapping[str, str] | None = None,
) -> SimulationResult:
    """R2 主流程：搜索 -> 过滤 -> Top-N -> 原生 P99 回放。

    ``accept`` 是调用方给的资格回调（身份 + 资源校验）：只有它返回 True 的候选
    才进入 Top-N。单个候选回放失败**不影响**其它候选。
    """
    work_root = Path(work_dir)
    # 每次运行一个**独立**目录：work_dir/runs/<run_id>/
    # 只读本次运行目录，**不**跨历史运行目录补结果（F6-2）。
    run_id = new_run_id()
    root = work_root / "runs" / run_id
    root.mkdir(parents=True, exist_ok=False)
    manifest: dict[str, Any] = {
        "tool": "aisimulate",
        "tool_version": tool_version,
        "run_id": run_id,
        "run_dir": str(root),
        "work_root": str(work_root),
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "spec": {
            "model": spec.model_name,
            "precision": spec.model_precision,
            "system": spec.system,
            "isl": spec.input_len,
            "osl": spec.output_len,
            "concurrency": spec.concurrency,
            "backend": spec.backend,
            "backend_version": spec.backend_version,
            "max_total_gpus": spec.max_total_gpus,
            "database_mode": spec.database_mode,
        },
        "inputs": {},
        "outputs": {},
        "errors": [],
        #: 运行级错误（搜索未执行/无输出）：整次运行不可复用
        "run_errors": [],
        #: 候选级错误：键是候选 ID 或 predict/predict_N.yaml，只影响对应候选
        "candidate_errors": {},
        "failed_predicts": {},
        "not_aligned": {},
        "not_replayed": {},
    }

    try:
        search_cfg = build_search_config(spec)
    except ValueError as exc:
        return SimulationResult(status="blocked", message=str(exc), source_dir=str(root))
    search_path, search_sha = _write_yaml(root, "search_input.yaml", search_cfg)
    manifest["inputs"]["search_input.yaml"] = search_sha

    try:
        search_out = run_external(
            "sweeper", config_arg=search_path, save_dir=root / "search", config=tool_config
        )
    except Exception as exc:  # noqa: BLE001 - 含超时/启动失败
        message = f"sweeper: {type(exc).__name__}: {exc}"
        manifest["errors"].append(message)
        manifest["run_errors"].append(message)
        (root / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return SimulationResult(
            status="error", message=str(exc), source_dir=str(root),
            limitations=["自动搜索未执行：外部工具不可用"],
        )

    found = discover_outputs(search_out, "sweeper")
    if not found.get("sweeper_csv"):
        message = "sweeper: 输出目录里没有 pareto.csv / best_config_topn.csv"
        manifest["errors"].append(message)
        manifest["run_errors"].append(message)
        (root / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return SimulationResult(
            status="missing_data",
            message=f"搜索已执行，但在 {search_out} 下没找到结果文件",
            source_dir=str(root),
        )
    for path in (root / "search").rglob("*"):
        if path.is_file():
            manifest["outputs"][str(path.relative_to(root))] = sha256_file(path)

    exp_config = next(iter((root / "search").rglob("exp_config.yaml")), None)
    tool = tool_identity_from_archives(
        exp_config=exp_config,
        tool_version=tool_version,
        surface="mixed",
        backend=spec.backend,
    )
    effective_target = target or SimulationTarget(
        model_name=spec.model_name,
        model_precision=spec.model_precision,
        system=spec.system,
        input_len=spec.input_len,
        output_len=spec.output_len,
        concurrency=spec.concurrency,
        ttft_p99_ms_max=spec.ttft_p99_ms_max,
        tpot_p99_ms_max=spec.tpot_p99_ms_max,
        backend=spec.backend,
        backend_version=spec.backend_version,
    )

    search_candidates = parse_sweeper_csv(found["sweeper_csv"])
    for candidate in search_candidates:
        check_comparability(candidate, effective_target)
        assess_verdict(candidate, effective_target)

    # AA2：搜索行只用于发现**部署配置**。扫描行的 ISL/OSL/并发/ctx 是
    # 它被搜索到时的负载，不是用户目标负载；回放统一用 spec 重新构造。
    deployments: dict[tuple[Any, ...], SimulationCandidate] = {}
    discovered_keys: set[tuple[Any, ...]] = set()

    def deployment_key(candidate: SimulationCandidate) -> tuple[Any, ...]:
        dep = candidate.deployment
        return (
            dep.model_name,
            dep.system,
            dep.tp_size,
            dep.pp_size,
            dep.dp_size,
            dep.gpu_count,
            dep.backend,
            dep.backend_version,
        )

    for candidate in search_candidates:
        discovered_keys.add(deployment_key(candidate))
    manifest.setdefault("not_replayed", {})
    for candidate in search_candidates:
        reasons = deployment_identity_reasons(candidate, effective_target)
        if reasons:
            candidate.link_reasons = reasons
            manifest["not_aligned"][candidate.candidate_id] = reasons
            continue
        try:
            accepted = accept(candidate) if accept is not None else True
        except Exception as exc:  # noqa: BLE001 - 资格回调本身出错也不能中断整轮
            message = (
                f"{candidate.candidate_id}: 资格校验异常 {type(exc).__name__}: {exc}"
            )
            manifest["errors"].append(message)
            manifest["candidate_errors"][candidate.candidate_id] = message
            accepted = False
        if not accepted:
            why = list(candidate.resource_reasons or [])
            if candidate.within_gpu_budget is False:
                why.insert(0, "GPU 卡数超过用户上限")
            manifest["not_replayed"][candidate.candidate_id] = why or ["未通过资格筛选"]
            continue
        dep = candidate.deployment
        key = (
            dep.model_name,
            dep.system,
            dep.tp_size,
            dep.pp_size,
            dep.dp_size,
            dep.gpu_count,
            dep.backend,
            dep.backend_version,
        )
        current = deployments.get(key)
        if current is None or (
            candidate.metrics.sim_output_throughput_tok_s or Decimal(0)
        ) > (current.metrics.sim_output_throughput_tok_s or Decimal(0)):
            deployments[key] = candidate

    # 对每个不同部署各回放一次；不再用扫描行的并发/ctx 卡名额。
    pool = sorted(
        deployments.values(),
        key=lambda c: c.metrics.sim_output_throughput_tok_s or Decimal(0),
        reverse=True,
    )
    shortlist = pool[:_MAX_DEPLOYMENT_REPLAYS]
    truncated = pool[_MAX_DEPLOYMENT_REPLAYS:]
    for candidate in truncated:
        manifest["not_replayed"][candidate.candidate_id] = [
            f"超过本轮最多回放 {_MAX_DEPLOYMENT_REPLAYS} 个不同部署的上限"
        ]
    if truncated:
        limitations_note = (
            f"本轮有 {len(truncated)} 个不同部署因回放上限未验证 P99；"
            "候选比较**不完整**，不能据此断言已找到全局最优"
        )
    else:
        limitations_note = None
    manifest["replay_plan"] = [
        {
            "candidate_id": candidate.candidate_id,
            "tp": candidate.deployment.tp_size,
            "pp": candidate.deployment.pp_size,
            "dp": candidate.deployment.dp_size,
            "gpu_count": candidate.deployment.gpu_count,
            "replay_input_len": spec.input_len,
            "replay_output_len": spec.output_len,
            "replay_concurrency": spec.concurrency,
        }
        for candidate in shortlist
    ]

    native_candidates: list[SimulationCandidate] = []
    for index, candidate in enumerate(shortlist, 1):
        try:
            native_candidates.append(
                _replay_one(
                    spec,
                    candidate,
                    index=index,
                    root=root,
                    tool_config=tool_config,
                    predict_requests=predict_requests,
                    version_aliases=version_aliases,
                    effective_target=effective_target,
                    manifest=manifest,
                )
            )
        except Exception as exc:  # noqa: BLE001
            # 单候选的任何失败（超时、启动失败、输出损坏、解析异常）都只记录，不中断
            message = f"{candidate.candidate_id}: {type(exc).__name__}: {exc}"
            manifest["errors"].append(message)
            manifest["candidate_errors"][candidate.candidate_id] = message
            manifest["failed_predicts"][f"predict/predict_{index}.yaml"] = message
        else:
            manifest.setdefault("predict_candidates", {})[
                f"predict/predict_{index}.yaml"
            ] = candidate.candidate_id

    (root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    limitations = [
        "自动搜索由本系统按需求生成输入 YAML；搜索阶段的 SLA 边界刻意设为极松 —— "
        "工具自带的 SLA 过滤是**聚合均值**口径，不用于我们的判断",
        "搜索只产出**Pareto 前沿**上的点：被支配的配置（例如某些 TP）不会出现，"
        "这是工具的搜索口径，不是本系统的过滤",
        "搜索行只用于发现**部署配置**；P99 回放统一使用目标的 ISL/OSL/并发，"
        "不把扫描行当时的并发/ctx 当成目标工作点",
        "对每个通过身份与资源筛选的不同部署各回放一次；"
        "DP≠1 / PP≠1 / 版本不一致 / 资源超限的候选会记录未回放原因",
        "模拟结论**不是实测**：不得写入 BenchmarkRun / CapacityPoint，不得产生 observed_pass",
    ]
    if limitations_note:
        limitations.append(limitations_note)
    if manifest["errors"]:
        limitations.append(
            f"有 {len(manifest['errors'])} 个候选回放失败（不影响其它候选），"
            "详见工作目录下的 manifest.json"
        )
    status = "computed" if (search_candidates or native_candidates) else "missing_data"
    failed_candidates = {
        key: value
        for key, value in manifest["candidate_errors"].items()
        if not key.startswith("predict/")
    }
    if truncated:
        effective_scope = "partial"
    elif failed_candidates:
        effective_scope = "partial_with_failures"
    else:
        effective_scope = "complete"
    audit = SimulationSearchAudit(
        discovered_deployments=len(discovered_keys),
        accepted_deployments=len(pool),
        replayed_deployments=len(native_candidates),
        failed_deployments=len(manifest["candidate_errors"]),
        truncated_deployments=len(truncated),
        filtered={**manifest["not_aligned"], **manifest["not_replayed"]},
        replayed=[candidate.candidate_id for candidate in native_candidates],
        failed=failed_candidates,
        truncated=[candidate.candidate_id for candidate in truncated],
        effective_scope=effective_scope,
    )
    return SimulationResult(
        status=status,
        tool=tool,
        candidates=search_candidates + native_candidates,
        limitations=limitations,
        source_dir=str(root),
        search_audit=audit,
    )
