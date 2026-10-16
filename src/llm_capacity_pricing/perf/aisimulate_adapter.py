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
from typing import Any, Mapping, Sequence

import yaml

from llm_capacity_pricing.schemas.simulation import (
    SIMULATION_TRUST_LABEL,
    SimulationCandidate,
    SimulationDeployment,
    SimulationMetrics,
    SimulationResult,
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

    for label, want, got in (
        ("输入长度", target.input_len, wl.input_len),
        ("输出长度", target.output_len, wl.output_len),
    ):
        if want is None or got is None:
            reasons.append(f"{label}有一侧未声明：无法确认负载一致")
        elif want != got:
            reasons.append(f"{label}不同：目标 {want}，模拟 {got}")

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
        if attr in ("model_name", "system", "parallel"):
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
    path: str | Path, *, model_precision: str | None = None
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
        serving_mode="agg" if engine.get("mode") == "aggregated" else "unknown",
        tp_size=parallel.get("tensor"),
        pp_size=parallel.get("pipeline"),
        dp_size=None,  # 原生 schema 的 replicas ≠ 数据并行，不混为一谈
        moe_tp_size=parallel.get("moe_tensor"),
        moe_ep_size=parallel.get("moe_expert"),
        replicas=parallel.get("replicas"),
        gpu_count=None,
        quant_modes={"__backend__": backend} if backend else {},
    )
    workload = SimulationWorkload(
        input_len=source.get("input_tokens"),
        output_len=source.get("output_tokens"),
        concurrency=load.get("concurrency"),
        stop_condition=", ".join(f"{k}={v}" for k, v in stop.items()) or None,
        num_requests=stop.get("requests"),
    )
    return deployment, workload


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

#: 允许的入口；不做任意命令执行
_EXTERNAL_ENTRYPOINTS: dict[str, tuple[str, ...]] = {
    "sweeper": ("-m", "aiconfigurator.cli", "exp"),
    "native_replay": ("-m", "aisimulate", "predict"),
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
    probe = subprocess.run(
        [python, "-c", "import aiconfigurator, aisimulate"],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    return probe.returncode == 0


def build_command(
    surface: str,
    *,
    config_arg: str | Path,
    save_dir: str | Path | None = None,
    extra: Sequence[str] = (),
) -> list[str]:
    """构造 argv 列表（**不做 shell 解析**）。"""
    entry = _EXTERNAL_ENTRYPOINTS.get(surface)
    if entry is None:
        raise ValueError(f"不支持的外部入口：{surface}")
    argv: list[str] = list(entry)
    argv += ["--config" if surface == "native_replay" else "--yaml-path", str(config_arg)]
    if surface == "native_replay":
        argv += ["--stack", "engine"]
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
    argv = [python, *build_command(surface, config_arg=config_arg, save_dir=save_dir, extra=extra)]
    result = subprocess.run(
        argv,
        capture_output=True,
        text=True,
        check=False,
        timeout=config.timeout_s,
    )
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
    exp_config: str | Path | None = None,
    per_ops_source: str | Path | None = None,
    tool_version: str | None = None,
    default_backend: str | None = None,
    target: SimulationTarget | None = None,
    top_n: int | None = None,
) -> SimulationResult:
    """只读本地归档输出，拼出一个 :class:`SimulationResult`。

    这是 **P2 第一阶段**：不需要安装 AISimulate、不需要 GPU。
    """
    candidates: list[SimulationCandidate] = []
    limitations: list[str] = []

    tool = tool_identity_from_archives(
        exp_config=exp_config,
        per_ops_source=per_ops_source,
        tool_version=tool_version,
        surface=(
            "sweeper" if sweeper_csv is not None and native_prediction is None
            else "native_replay" if native_prediction is not None and sweeper_csv is None
            else "mixed"
        ),
        backend=default_backend,
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
            native_input, model_precision=native_model_precision
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

    return SimulationResult(
        status="computed",
        data_trust=SIMULATION_TRUST_LABEL,
        tool=tool,
        candidates=candidates,
        limitations=limitations,
        source_dir=str(Path(sweeper_csv or native_prediction).parent),
    )
