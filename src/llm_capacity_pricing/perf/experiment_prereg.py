"""AH1：实验预注册、执行后补全与配置变化校验。"""

from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import yaml

from llm_capacity_pricing.app.archive_integrity import (
    verify_files,
    verify_predict_metadata,
)
from llm_capacity_pricing.app.offline_archives import find_archives
from llm_capacity_pricing.perf.aisimulate_adapter import hardware_id_for_system
from llm_capacity_pricing.schemas.experiment_manifest import (
    ExperimentManifest,
    ExperimentRun,
)

ROOT = Path(__file__).resolve().parents[3]
MODEL_MANIFEST = ROOT / "data/models/model_manifest.yaml"


DEFAULT_ALLOWED_VARIATION = ("traffic.load.seed",)


def _remove_path(doc: dict[str, Any], path: str) -> None:
    parts = path.split(".")
    current: Any = doc
    for part in parts[:-1]:
        if not isinstance(current, dict) or part not in current:
            return
        current = current[part]
    if isinstance(current, dict):
        current.pop(parts[-1], None)


def normalize_config(
    config: Mapping[str, Any],
    allowed_fields: list[str] | tuple[str, ...] = DEFAULT_ALLOWED_VARIATION,
) -> dict[str, Any]:
    """只移除声明允许变化的配置路径，其余保持原样。"""
    doc = copy.deepcopy(dict(config))
    for path in allowed_fields:
        _remove_path(doc, path)
    return doc


def config_sha256(
    config: Mapping[str, Any],
    allowed_fields: list[str] | tuple[str, ...] = DEFAULT_ALLOWED_VARIATION,
) -> str:
    payload = json.dumps(
        normalize_config(config, allowed_fields),
        ensure_ascii=False,
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _file_sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _model_id_for_repo(repo_id: str | None) -> str | None:
    if not repo_id:
        return None
    try:
        doc = yaml.safe_load(MODEL_MANIFEST.read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001
        return None
    for item in doc.get("models") or []:
        if item.get("repo_id") == repo_id:
            return item.get("model_id")
    return None


def build_preregistered_manifest(
    *,
    experiment_id: str,
    predict_config: Mapping[str, Any],
    expected_seeds: list[int],
    model_name: str,
    model_precision: str,
    system: str,
    backend: str,
    backend_version: str,
    gpu_count: int,
    tp_size: int,
    pp_size: int,
    dp_size: int,
    replicas: int | None,
    input_len: int,
    output_len: int,
    concurrency: int | None,
    arrival_mode: str,
    request_rate_rps: Any,
    dataset: str | None,
    trace_id: str | None,
    num_requests: int | None,
    stop_condition: str | None,
    required_completed_requests: int = 500,
    tool_version: str | None = None,
    now: datetime | None = None,
) -> ExperimentManifest:
    base = normalize_config(predict_config)
    created = now or datetime.now(timezone.utc)
    return ExperimentManifest(
        experiment_id=experiment_id,
        created_at=created,
        manifest_status="preregistered",
        execution_status="planned",
        preregistered_at=created,
        base_config=base,
        base_config_sha256=config_sha256(base),
        allowed_variation_fields=["traffic.load.seed"],
        tool="aisimulate",
        tool_version=tool_version,
        model_name=model_name,
        model_precision=model_precision,
        system=system,
        backend=backend,
        backend_version=backend_version,
        gpu_count=gpu_count,
        serving_mode="agg",
        tp_size=tp_size,
        pp_size=pp_size,
        dp_size=dp_size,
        replicas=replicas,
        input_len=input_len,
        output_len=output_len,
        concurrency=concurrency,
        arrival_mode=arrival_mode,
        request_rate_rps=request_rate_rps,
        dataset=dataset,
        trace_id=trace_id,
        num_requests=num_requests,
        stop_condition=stop_condition,
        required_completed_requests=required_completed_requests,
        expected_seeds=sorted(set(expected_seeds)),
        runs=[],
    )


def _finalize_seed(
    manifest: ExperimentManifest, seed: int, entry: Any
) -> ExperimentRun:
    try:
        prediction_path = ROOT / entry.native_prediction
        run_dir = prediction_path.parents[2]
        run_manifest = json.loads(
            (run_dir / "manifest.json").read_text(encoding="utf-8")
        )
        rel_pred = str(prediction_path.relative_to(run_dir))
        index = prediction_path.parent.name.rsplit("_", 1)[-1]
        rel_input = f"predict/predict_{index}.yaml"
        problems = verify_files(run_dir, run_manifest, [rel_input, rel_pred])
        if not problems:
            problems.extend(
                verify_predict_metadata(run_dir, run_manifest, rel_input)
            )
        payload = json.loads(prediction_path.read_text(encoding="utf-8"))
        completed = payload.get("completed_requests")
        predict_file = ROOT / entry.native_input
        predict_doc = yaml.safe_load(predict_file.read_text(encoding="utf-8")) or {}
        normalized_sha = config_sha256(
            predict_doc, manifest.allowed_variation_fields
        )
        if normalized_sha != manifest.base_config_sha256:
            problems.append("predict.yaml 的变化超出允许字段（seed）")
        status = (
            "invalid"
            if problems
            else "succeeded"
            if completed is not None
            and completed >= manifest.required_completed_requests
            else "failed"
        )
        config_file = ROOT / entry.exp_config if entry.exp_config else None
        return ExperimentRun(
            seed=seed,
            status=status,
            archive=entry.native_prediction,
            archive_sha256=_file_sha(prediction_path),
            config_file=entry.exp_config,
            config_sha256=_file_sha(config_file) if config_file else None,
            predict_config_file=entry.native_input,
            predict_config_sha256=_file_sha(predict_file),
            normalized_config_sha256=normalized_sha,
            completed_requests=completed,
            notes=problems,
        )
    except Exception as exc:  # noqa: BLE001
        return ExperimentRun(
            seed=seed,
            status="invalid",
            notes=[f"归档补全失败：{type(exc).__name__}: {exc}"],
        )


def finalize_manifest(
    manifest: ExperimentManifest,
    *,
    model_id: str | None = None,
    hardware_id: str | None = None,
) -> ExperimentManifest:
    """执行后补全每个 seed 的配置/结果，并核对是否超出允许变化。"""
    model = model_id or _model_id_for_repo(manifest.model_name)
    hardware = hardware_id or hardware_id_for_system(manifest.system)
    if not model or not hardware:
        raise ValueError("无法解析 model_id / hardware_id，不能补全清单")
    runs: list[ExperimentRun] = []
    all_recorded = True
    for seed in manifest.expected_seeds:
        entries = find_archives(
            model_id=model,
            hardware_id=hardware,
            input_len=manifest.input_len,
            output_len=manifest.output_len,
            concurrency=manifest.concurrency,
            precision=manifest.model_precision,
            framework=manifest.backend,
            framework_version=manifest.backend_version,
            gpu_limit=None,
            tp_sizes=(manifest.tp_size,) if manifest.tp_size else None,
            arrival_mode=manifest.arrival_mode,
            request_rate_rps=(
                str(manifest.request_rate_rps)
                if manifest.request_rate_rps is not None
                else None
            ),
            seed=seed,
            min_requests=0,
        )
        entries = [
            entry
            for entry in entries
            if entry.dp_size == manifest.dp_size
            and entry.pp_size == manifest.pp_size
            and (
                entry.dataset is None
                or manifest.dataset is None
                or entry.dataset == manifest.dataset
            )
            and (
                entry.trace_id is None
                or manifest.trace_id is None
                or entry.trace_id == manifest.trace_id
            )
            and (
                manifest.num_requests is None
                or entry.requests is None
                or entry.requests == manifest.num_requests
            )
            and (
                manifest.gpu_count is None
                or entry.gpu_count is None
                or entry.gpu_count == manifest.gpu_count
            )
        ]
        if len(entries) != 1:
            all_recorded = False
            runs.append(
                ExperimentRun(
                    seed=seed,
                    status="missing" if not entries else "invalid",
                    notes=[
                        "缺少该 seed 的归档"
                        if not entries
                        else "同一 seed 存在多个不一致归档"
                    ],
                )
            )
            continue
        run = _finalize_seed(manifest, seed, entries[0])
        if run.status != "succeeded":
            all_recorded = False
        runs.append(run)
    return manifest.model_copy(
        update={
            "runs": runs,
            "execution_status": "completed" if all_recorded else "aborted",
        }
    )
