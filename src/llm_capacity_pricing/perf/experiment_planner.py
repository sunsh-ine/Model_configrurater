"""M37：标准化压测实验清单生成。

输入是第二阶段的**可行候选**（或统一评估入口的候选），输出是"要跑哪些压测"，
每个点带部署身份、负载条件、证据强度要求、metadata 模板与命令骨架。

命令骨架里的每个参数都来自仓库内归档的官方文档（见 ``FLAG_SOURCES``），
不凭印象拼写；**本模块不产生任何性能数值**。
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Sequence

from llm_capacity_pricing.schemas.experiment import (
    DEFAULT_EXPERIMENT_GRID,
    FLAG_SOURCES,
    REQUIRED_METRICS,
    BenchmarkExperimentPlan,
    ExperimentGridPoint,
    ExperimentPoint,
    Framework,
)

__all__ = ["plan_benchmarks", "default_grid"]


def default_grid() -> tuple[ExperimentGridPoint, ...]:
    return DEFAULT_EXPERIMENT_GRID


def _server_args(
    *,
    framework: Framework,
    tp_size: int,
    dp_size: int,
    max_model_len: int,
    prefix_cache: str,
) -> tuple[dict[str, Any], list[str]]:
    """服务端必须对齐的参数（返回参数表与说明）。

    **刻意不生成启动命令**：服务端启动参数名与现场部署方式强相关，
    而本仓库只归档了压测**客户端**文档（``FLAG_SOURCES``）——
    无法为其参数拼写背书，因此只给出"必须对齐什么"，由现场按所用版本文档落地。
    """
    args: dict[str, Any] = {
        "model": "<模型路径或仓库 ID>",
        "tensor_parallel_size": tp_size,
        "data_parallel_size": dp_size,
        "max_model_len": max_model_len,
        "prefix_cache": prefix_cache,
    }
    notes = [
        "服务端启动命令由现场环境决定：本清单只给出**必须对齐**的参数值，"
        "参数名请以所用框架版本文档为准（本仓库未归档服务端启动文档）",
        f"前缀缓存策略必须与压测时一致：本点为 {prefix_cache}",
    ]
    if framework == "vllm":
        notes.append(
            "vLLM 客户端没有独立的 warmup 参数：请在正式测量前先发一轮预热请求"
        )
    return args, notes


def _client_command(
    *,
    framework: Framework,
    model_name: str,
    input_len: int,
    output_len: int,
    concurrency: int,
    num_prompts: int,
    arrival_mode: str,
    request_rate: Decimal | None,
    result_dir: str = "<RESULT_DIR>",
) -> str:
    """客户端压测命令骨架（参数出处见 ``FLAG_SOURCES``）。"""
    rate = (
        f" --request-rate {request_rate}"
        if arrival_mode == "request_rate" and request_rate is not None
        else ""
    )
    common = (
        f"--dataset-name random --random-input-len {input_len} "
        f"--random-output-len {output_len} --max-concurrency {concurrency} "
        f"--num-prompts {num_prompts}{rate}"
    )
    if framework == "sglang":
        return f"python -m sglang.bench_serving --backend sglang {common}"
    return (
        f"vllm bench serve --model {model_name} --served-model-name {model_name} "
        f"--base-url http://<HOST>:<PORT> {common} "
        f"--save-result --result-dir {result_dir}"
    )


def _metadata_template(
    *,
    point_id: str,
    model_name: str,
    model_revision: str | None,
    weight_precision: str | None,
    hardware_model: str | None,
    gpu_count: int,
    tp_size: int,
    dp_size: int,
    framework: Framework,
    framework_version: str | None,
    input_len: int,
    output_len: int,
    concurrency: int,
    arrival_mode: str,
    dataset_name: str,
) -> dict[str, Any]:
    """与现有适配器一一对应的 metadata 模板（照着填即可导入）。"""
    return {
        "evidence_id": f"{point_id}_evidence",
        "record_id": point_id,
        "format_version": (
            "vllm-bench-serve-json/v1"
            if framework == "vllm"
            else "sglang-bench-serving-jsonl/v1"
        ),
        # 真实实测：由现场运行产生，因此是 internal_measurement
        "performance_source_kind": "internal_measurement",
        "verification_status": "verified",
        "model_name": model_name,
        "model_revision": model_revision,
        "weight_precision": weight_precision,
        "hardware_model": hardware_model,
        "deployment_gpu_count": gpu_count,
        "tp_size": tp_size,
        "dp_size": dp_size,
        "framework": framework,
        "framework_version": framework_version,
        "input_len_target": input_len,
        "output_len_target": output_len,
        "configured_concurrency": concurrency,
        "arrival_mode": arrival_mode,
        "dataset_name": dataset_name,
        "launch_command": "<SERVER_LAUNCH_COMMAND>",
        "benchmark_command": "<CLIENT_BENCHMARK_COMMAND>",
        "evidence": {
            "publisher": "<你的团队/机构>",
            "source_url": None,
            "source_version": None,
            "notes": "现场实测结果；请保留原始输出文件并记录 sha256",
        },
    }


def _point_id(
    *, model_id: str, hardware_id: str, tp: int, dp: int, input_len: int, output_len: int, c: int
) -> str:
    return (
        f"exp-{model_id}-{hardware_id}-tp{tp}dp{dp}-{input_len}in{output_len}out-c{c}"
    )


def plan_benchmarks(
    *,
    candidates: Sequence[Any],
    model_id: str,
    model_name: str,
    hardware_id: str,
    hardware_model: str | None = None,
    model_revision: str | None = None,
    weight_precision: str | None = None,
    kv_precision: str | None = None,
    framework: Framework = "vllm",
    framework_version: str | None = None,
    grid: Sequence[ExperimentGridPoint] | None = None,
    repeats: int = 2,
    min_duration_s: Decimal | None = None,
    warmup_requests: int = 8,
    prefix_cache: str = "disabled",
    request_id: str | None = None,
) -> BenchmarkExperimentPlan:
    """按矩阵与候选生成实验清单。``candidates`` 为第二阶段的部署候选。"""
    from llm_capacity_pricing.perf.capacity_analyzer import SustainabilityCriteria

    grid = tuple(grid) if grid else DEFAULT_EXPERIMENT_GRID
    criteria = SustainabilityCriteria()
    duration = min_duration_s if min_duration_s is not None else criteria.min_duration_s
    repeats = max(repeats, criteria.min_repeats_per_point)

    points: list[ExperimentPoint] = []
    missing: list[str] = []
    if model_revision is None:
        missing.append(
            "model_revision: 未给出模型版本；采回来的数据无法与需求对齐，"
            "匹配等级只会停在 comparable_reference"
        )
    if framework_version is None:
        missing.append("framework_version: 未给出框架版本；目标身份不完整")
    if hardware_model is None:
        missing.append("hardware_model: 未给出硬件商品名（签名用的写法），无法与实测对齐")

    # 只对**静态可行**的候选安排实验（不可行的配置跑出来也没有意义）
    feasible = [
        c for c in candidates
        if getattr(c, "feasibility", None) is not None
        and c.feasibility.status in ("feasible", "conditional_feasible")
    ]
    if not feasible:
        feasible = list(candidates)  # 没有静态结论时仍然给清单，但会在 notes 里说明

    for candidate in feasible:
        tp = getattr(candidate, "tp_size", None)
        dp = getattr(candidate, "dp_size", None)
        gpu_count = getattr(candidate, "gpu_count", None)
        nodes = getattr(candidate, "nodes_required", None)
        if None in (tp, dp, gpu_count):
            continue
        for grid_point in grid:
            for concurrency in grid_point.concurrencies:
                point_id = _point_id(
                    model_id=model_id, hardware_id=hardware_id, tp=tp, dp=dp,
                    input_len=grid_point.input_len, output_len=grid_point.output_len,
                    c=concurrency,
                )
                max_model_len = max(grid_point.input_len + grid_point.output_len, grid_point.input_len)
                server_args, server_notes = _server_args(
                    framework=framework, tp_size=tp, dp_size=dp,
                    max_model_len=max_model_len, prefix_cache=prefix_cache,
                )
                notes = [
                    "本清单只给参数，不含任何性能数值；结果必须来自真实运行",
                    f"重复次数 ≥ {repeats}，单次时长 ≥ {duration}s、"
                    "错误率门槛见容量分析默认标准",
                ] + server_notes
                if not feasible:
                    notes.append("候选没有静态可行性结论：请先确认资源假设后再执行")
                points.append(
                    ExperimentPoint(
                        point_id=point_id,
                        purpose=grid_point.purpose,
                        model_id=model_id,
                        model_name=model_name,
                        model_revision=model_revision,
                        weight_precision=weight_precision,
                        kv_precision=kv_precision,
                        hardware_id=hardware_id,
                        hardware_model=hardware_model,
                        gpu_count=gpu_count,
                        tp_size=tp,
                        dp_size=dp,
                        nodes_required=nodes,
                        framework=framework,
                        framework_version=framework_version,
                        input_len=grid_point.input_len,
                        output_len=grid_point.output_len,
                        concurrency=concurrency,
                        arrival_mode="unbounded",
                        dataset_name="random",
                        prefix_cache=prefix_cache,  # type: ignore[arg-type]
                        warmup_requests=warmup_requests,
                        duration_s=duration,
                        repeats=repeats,
                        required_metrics=list(REQUIRED_METRICS),
                        server_args=server_args,
                        client_command=_client_command(
                            framework=framework,
                            model_name=model_name,
                            input_len=grid_point.input_len,
                            output_len=grid_point.output_len,
                            concurrency=concurrency,
                            num_prompts=max(concurrency * 20, 200),
                            arrival_mode="unbounded",
                            request_rate=None,
                        ),
                        metadata_template=_metadata_template(
                            point_id=point_id,
                            model_name=model_name,
                            model_revision=model_revision,
                            weight_precision=weight_precision,
                            hardware_model=hardware_model,
                            gpu_count=gpu_count,
                            tp_size=tp,
                            dp_size=dp,
                            framework=framework,
                            framework_version=framework_version,
                            input_len=grid_point.input_len,
                            output_len=grid_point.output_len,
                            concurrency=concurrency,
                            arrival_mode="unbounded",
                            dataset_name="random",
                        ),
                        # 只列**客户端**文档：本点的命令参数全部出自这里
                        flag_sources=[FLAG_SOURCES[framework]],
                        notes=notes,
                    )
                )

    if not points:
        raise ValueError("没有任何可安排的实验点：请检查候选的 TP/DP/卡数是否完整")

    limitations = [
        "本阶段只做实验规划、元数据与结果整理，**不含**跨服务器部署调度",
        "清单里的压测命令是骨架：``<...>`` 占位需按现场填写（主机、端口、结果目录）",
        "压测命令的参数出处见各点 flag_sources（仓库内归档的官方文档）",
        "**不提供**服务端启动命令：其参数名与现场部署方式强相关，"
        "本仓库未归档服务端启动文档，不予背书",
        "**没有真实现场运行，就没有真实性能验收** —— 清单本身不构成任何性能结论",
    ]
    return BenchmarkExperimentPlan(
        plan_id=f"bench-exp::{model_id}::{hardware_id}",
        request_id=request_id,
        points=points,
        missing_inputs=missing,
        limitations=limitations,
        notes=[
            f"共 {len(points)} 个实验点，覆盖 {len(grid)} 组负载条件"
            f"与 {len({(p.tp_size, p.dp_size) for p in points})} 种并行配置",
            "每点至少重复 "
            f"{repeats} 次；能否验证最大可持续容量还需满足时长与错误率门槛",
        ],
    )
