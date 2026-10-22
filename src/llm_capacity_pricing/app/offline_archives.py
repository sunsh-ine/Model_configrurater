"""离线模拟归档的选择规则（Z4）。

目的：用户只填需求时，系统自动挑**同一工作点**的原生回放证据；
挑不到就不放 P99，而不是拿另一个并发/长度的结果凑数。

选择顺序（确定性）：

1. 模型仓库、system、ISL/OSL/并发必须完全一致；
2. 请求声明了精度/框架/版本时，必须一致；
3. 有多个部署时，优先 GPU 数更多的部署（例如 TP8 优先于 TP4），
   再按归档时间/名称排序。

扫描器 CSV 只用于候选枚举，可以跨工作点使用；但原生 P99 只能来自
与请求可比较的那一条归档。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from llm_capacity_pricing.app.archive_integrity import verify_files
from llm_capacity_pricing.perf.aisimulate_adapter import system_for_hardware_id

ROOT = Path(__file__).resolve().parents[3]
ARCHIVE_ROOT = ROOT / "data/simulation/archives"
MODEL_MANIFEST = ROOT / "data/models/model_manifest.yaml"

POC_SWEEPER = "docs/poc/aisimulate/artifacts/matched_pareto_vllm_bf16.csv"

#: 归档里可能出现的版本槽位别名；解析成实际版本后才算身份匹配。
VERSION_ALIASES = {"current": "0.24.0"}


@dataclass(frozen=True)
class ArchiveEntry:
    name: str
    model_name: str | None
    system: str | None
    precision: str | None
    backend: str | None
    backend_version: str | None
    input_len: int | None
    output_len: int | None
    concurrency: int | None
    gpu_count: int | None
    tp_size: int | None
    requests: int | None
    native_prediction: str
    native_input: str
    exp_config: str | None
    per_ops_source: str | None
    tool_version: str | None
    #: 实际完成请求数（P99 的真实样本窗口）；缺省视为不足，不能只信 stop.requests
    completed_requests: int | None = None
    pp_size: int | None = None
    dp_size: int | None = None

    @property
    def deployment_key(self) -> tuple[Any, ...]:
        """同一部署的归档只保留一条，不重复算成多个方案。"""
        return (
            self.model_name,
            self.system,
            self.precision,
            self.backend,
            self.backend_version,
            self.gpu_count,
            self.tp_size,
            self.pp_size,
            self.dp_size,
        )

    @property
    def sort_key(self) -> tuple[int, int, str]:
        return (
            self.gpu_count or 0,
            self.completed_requests or 0,
            self.name,
        )


def _completed_requests(path: Path) -> int | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    return _int(payload.get("completed_requests"))


def _model_repo_map() -> dict[str, str]:
    try:
        doc = yaml.safe_load(MODEL_MANIFEST.read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001
        return {}
    return {
        str(item.get("model_id")): str(item.get("repo_id"))
        for item in doc.get("models") or []
        if item.get("model_id") and item.get("repo_id")
    }


def _version(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return VERSION_ALIASES.get(text, text) or None


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _entry_from_paths(
    *,
    name: str,
    native_prediction: Path,
    native_input: Path,
    exp_config: Path | None,
    per_ops_source: Path | None,
    model_name: str | None,
    system: str | None,
    precision: str | None,
    backend: str | None,
    backend_version: str | None,
    input_len: int | None,
    output_len: int | None,
    concurrency: int | None,
    gpu_count: int | None,
    tp_size: int | None,
    requests: int | None,
    tool_version: str | None,
    completed_requests: int | None = None,
    pp_size: int | None = None,
    dp_size: int | None = None,
) -> ArchiveEntry:
    return ArchiveEntry(
        name=name,
        model_name=model_name,
        system=system,
        precision=precision,
        backend=backend,
        backend_version=_version(backend_version),
        input_len=input_len,
        output_len=output_len,
        concurrency=concurrency,
        gpu_count=gpu_count,
        tp_size=tp_size,
        requests=requests,
        completed_requests=(
            completed_requests
            if completed_requests is not None
            else _completed_requests(native_prediction)
        ),
        pp_size=pp_size,
        dp_size=dp_size,
        native_prediction=str(native_prediction.relative_to(ROOT)),
        native_input=str(native_input.relative_to(ROOT)),
        exp_config=(
            str(exp_config.relative_to(ROOT)) if exp_config and exp_config.exists() else None
        ),
        per_ops_source=(
            str(per_ops_source.relative_to(ROOT))
            if per_ops_source and per_ops_source.exists()
            else None
        ),
        tool_version=tool_version,
    )


def archive_entries() -> list[ArchiveEntry]:
    """列出所有可用原生回放归档；PoC 已迁移为标准归档，走同一校验。"""
    entries: list[ArchiveEntry] = []
    if not ARCHIVE_ROOT.exists():
        return entries
    for manifest_path in sorted(ARCHIVE_ROOT.glob("*/manifest.json")):
        run_dir = manifest_path.parent
        try:
            manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
        except Exception:  # noqa: BLE001
            continue
        spec = manifest.get("spec") or {}
        run_errors = manifest.get("run_errors")
        if run_errors is None:
            # 旧 manifest 没有分层字段：保守地按运行级错误处理
            if manifest.get("errors"):
                continue
        elif run_errors:
            continue
        candidate_errors = manifest.get("candidate_errors") or {}
        exp_config = run_dir / "exp_config.yaml"
        for input_path in sorted(run_dir.glob("predict/predict_*.yaml")):
            relative_input = str(input_path.relative_to(run_dir))
            if relative_input in candidate_errors:
                continue
            index = input_path.stem.rsplit("_", 1)[-1]
            prediction_path = run_dir / "predict" / f"out_{index}" / "prediction.json"
            if not prediction_path.exists():
                continue
            required = [
                str(input_path.relative_to(run_dir)),
                str(prediction_path.relative_to(run_dir)),
            ]
            registered = {
                **(manifest.get("inputs") or {}),
                **(manifest.get("outputs") or {}),
            }
            if "exp_config.yaml" in registered:
                # 登记了就要求存在且哈希一致；文件被删不能静默跳过。
                required.append("exp_config.yaml")
            if verify_files(run_dir, manifest, required):
                # 实际要读的文件必须被哈希覆盖；只登记别的文件不算数。
                continue
            cfg = yaml.safe_load(input_path.read_text(encoding="utf-8")) or {}
            engine = cfg.get("engine") or {}
            traffic = cfg.get("traffic") or {}
            source = traffic.get("source") or {}
            load = traffic.get("load") or {}
            stop = traffic.get("stop") or {}
            parallel = ((engine.get("workers") or {}).get("aggregated") or {}).get(
                "parallelism"
            ) or {}
            run_name = str(manifest.get("run_id") or run_dir.name)
            entries.append(
                _entry_from_paths(
                    # 多候选用 run_id + 预测序号区分，避免同名覆盖
                    name=f"{run_name}:predict_{index}",
                    native_prediction=prediction_path,
                    native_input=input_path,
                    exp_config=exp_config,
                    per_ops_source=None,
                    model_name=engine.get("model") or spec.get("model"),
                    system=engine.get("hardware") or spec.get("system"),
                    precision=spec.get("precision"),
                    backend=engine.get("backend") or spec.get("backend"),
                    backend_version=engine.get("backend_version")
                    or spec.get("backend_version"),
                    input_len=_int(source.get("input_tokens") or spec.get("isl")),
                    output_len=_int(source.get("output_tokens") or spec.get("osl")),
                    concurrency=_int(
                        load.get("concurrency") or spec.get("concurrency")
                    ),
                    gpu_count=(
                        (_int(parallel.get("tensor")) or 0)
                        * (_int(parallel.get("pipeline")) or 1)
                        * (_int(parallel.get("replicas")) or 1)
                    )
                    or None,
                    tp_size=_int(parallel.get("tensor")),
                    pp_size=_int(parallel.get("pipeline")) or 1,
                    dp_size=_int(parallel.get("replicas")) or 1,
                    requests=_int(stop.get("requests")),
                    tool_version=manifest.get("tool_version"),
                )
            )
    return entries


def find_archives(
    *,
    model_id: str,
    hardware_id: str,
    input_len: int,
    output_len: int,
    concurrency: int,
    precision: str | None = None,
    framework: str | None = None,
    framework_version: str | None = None,
    min_requests: int = 100,
    match_identity: bool = True,
    gpu_limit: int | None = None,
    tp_sizes: tuple[int, ...] | None = None,
) -> list[ArchiveEntry]:
    """返回与请求**同一工作点**的全部有效部署证据（不是只挑一条）。

    ``gpu_limit`` / ``tp_sizes`` 是用户约束；超限、未知卡数、列表外 TP
    一律不进入候选。同一部署的多份归档只保留证据最完整/最新的一份，
    避免把重复归档算成多个方案。
    """
    model_repo = _model_repo_map().get(model_id)
    system = system_for_hardware_id(hardware_id)
    if not model_repo or not system:
        return []

    matches = [
        entry
        for entry in archive_entries()
        if entry.model_name == model_repo
        and entry.system == system
        and entry.input_len == input_len
        and entry.output_len == output_len
        and entry.concurrency == concurrency
        and (entry.completed_requests or 0) >= min_requests
        and entry.gpu_count is not None
        and (gpu_limit is None or entry.gpu_count <= gpu_limit)
        and (
            tp_sizes is None
            or (entry.tp_size is not None and entry.tp_size in tp_sizes)
        )
        and (
            not match_identity
            or (
                (
                    precision is None
                    or (entry.precision is not None and entry.precision == precision)
                )
                and (
                    framework is None
                    or (entry.backend is not None and entry.backend == framework)
                )
                and (
                    framework_version is None
                    or (
                        entry.backend_version is not None
                        and entry.backend_version == framework_version
                    )
                )
            )
        )
    ]
    deduped: dict[tuple[Any, ...], ArchiveEntry] = {}
    for entry in matches:
        current = deduped.get(entry.deployment_key)
        if current is None or entry.sort_key > current.sort_key:
            deduped[entry.deployment_key] = entry
    return sorted(
        deduped.values(),
        key=lambda entry: (entry.gpu_count or 0, entry.name),
    )


def find_archive(**kwargs: Any) -> ArchiveEntry | None:
    """单条便捷接口（兼容旧调用）；业务选择应使用 :func:`find_archives`。"""
    entries = find_archives(**kwargs)
    if not entries:
        return None
    return max(entries, key=lambda entry: entry.sort_key)


def available_workpoints(
    *, model_id: str, hardware_id: str, min_requests: int = 100
) -> list[dict[str, Any]]:
    """列出该模型/硬件上已有原生 P99 的工作点（用于报告缺口说明）。"""
    model_repo = _model_repo_map().get(model_id)
    system = system_for_hardware_id(hardware_id)
    if not model_repo or not system:
        return []
    seen: dict[tuple[Any, ...], dict[str, Any]] = {}
    for entry in archive_entries():
        if (
            entry.model_name != model_repo
            or entry.system != system
            or (entry.completed_requests or 0) < min_requests
        ):
            continue
        key = (
            entry.input_len,
            entry.output_len,
            entry.concurrency,
            entry.precision,
        )
        if key in seen:
            continue
        seen[key] = {
            "input_len": entry.input_len,
            "output_len": entry.output_len,
            "concurrency": entry.concurrency,
            "precision": entry.precision,
            "gpu_count": entry.gpu_count,
            "requests": entry.requests,
        }
    return sorted(
        seen.values(),
        key=lambda item: (
            item["input_len"] or 0,
            item["output_len"] or 0,
            item["concurrency"] or 0,
        ),
    )


def offline_simulation_paths(
    *,
    model_id: str,
    hardware_id: str,
    input_len: int,
    output_len: int,
    concurrency: int,
    precision: str | None,
    framework: str | None,
    framework_version: str | None,
    gpu_limit: int | None = None,
    tp_sizes: tuple[int, ...] | None = None,
) -> dict[str, Any] | None:
    """构造离线 ``PlanSimulationInput`` 的字段；没有匹配的原生证据就不放 P99。

    扫描器 CSV 只覆盖 Qwen3-32B / H100 SXM / ISL8192 / OSL256；其他模型、
    硬件或长度既不套用该扫描器，也不套用其他工作点的 P99，返回 ``None``。
    """
    sweeper_applicable = (
        model_id == "qwen3-32b"
        and hardware_id == "nvidia-h100-sxm-80gb"
        and input_len == 8192
        and output_len == 256
    )
    paths: dict[str, Any] = {
        "native_model_precision": None,
        "tool_version": "0.12.1",
        "backend_version_aliases": dict(VERSION_ALIASES),
    }
    if sweeper_applicable:
        paths["sweeper_csv"] = POC_SWEEPER
    entries = find_archives(
        model_id=model_id,
        hardware_id=hardware_id,
        input_len=input_len,
        output_len=output_len,
        concurrency=concurrency,
        precision=precision,
        framework=framework,
        framework_version=framework_version,
        gpu_limit=gpu_limit,
        tp_sizes=tp_sizes,
    )
    if not entries:
        # 同一工作点存在、但精度/框架不一致：仍然展示为“不可比”证据，
        # 由 check_comparability 明确写出原因；绝不把它当成可比 P99。
        entries = find_archives(
            model_id=model_id,
            hardware_id=hardware_id,
            input_len=input_len,
            output_len=output_len,
            concurrency=concurrency,
            match_identity=False,
            gpu_limit=gpu_limit,
            tp_sizes=tp_sizes,
        )
    if not entries:
        return paths if "sweeper_csv" in paths else None
    paths["archives"] = [
        {
            "native_prediction": entry.native_prediction,
            "native_input": entry.native_input,
            # 不得用请求值补归档缺失的精度：那会把“未声明”伪造成“已核实”。
            "native_model_precision": entry.precision,
            "tool_version": entry.tool_version,
            "label": entry.name,
            **({"exp_config": entry.exp_config} if entry.exp_config else {}),
            **(
                {"per_ops_source": entry.per_ops_source}
                if entry.per_ops_source
                else {}
            ),
        }
        for entry in entries
    ]
    return paths
