"""AG1/AG2：用实验清单核对重复性实验的完整性。

核心规则：

* 清单是**预期**，归档是**实际**；缺失的预期 seed 不能被“剩下几个通过”掩盖。
* 实验完整性（complete/incomplete/invalid）与模拟 SLA 结论
  （all_pass/all_fail/mixed/unknown）是两层状态。
* 每个 seed 至少完成 ``required_completed_requests`` 个请求，且有原始归档哈希，
  才算有效运行；只有 30 个 P99 样本只说明 P99 “可计算”，不等于重复实验完整。
"""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable, Sequence

from llm_capacity_pricing.schemas.experiment_manifest import ExperimentManifest
from llm_capacity_pricing.schemas.simulation import (
    SimulationCandidate,
    repeatability_group_key,
)

DEFAULT_MANIFEST_DIR = (
    Path(__file__).resolve().parents[3] / "data/simulation/experiments"
)


def load_manifests(
    directory: str | Path | None = None,
) -> list[ExperimentManifest]:
    """读取实验清单目录（默认 ``data/simulation/experiments``）。"""
    base = Path(directory) if directory is not None else DEFAULT_MANIFEST_DIR
    if not base.exists():
        return []
    manifests: list[ExperimentManifest] = []
    for path in sorted(base.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        manifests.append(ExperimentManifest.model_validate(payload))
    return manifests


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run_evidence_problems(
    entry: Any,
    prediction_path: Path,
    planned_run: Any,
) -> list[str]:
    """扫描器用：归档/配置哈希是否与清单的该 seed 记录一致。"""
    if planned_run is None:
        return ["实验清单缺少该 seed 的运行记录"]
    if planned_run.status != "succeeded":
        return [f"实验清单记录该 seed 为 {planned_run.status}"]
    if not planned_run.archive_sha256 or not planned_run.config_sha256:
        return ["实验清单缺少归档或配置哈希"]
    problems: list[str] = []
    if planned_run.archive and not _same_path(
        entry.native_prediction, planned_run.archive
    ):
        problems.append("实验清单声明的归档路径与实际不一致")
    actual_sha = sha256_file(prediction_path)
    if actual_sha != planned_run.archive_sha256:
        problems.append("归档哈希与实验清单不一致")
    if planned_run.config_file and not _same_path(
        entry.exp_config, planned_run.config_file
    ):
        problems.append("实验清单声明的配置路径与实际不一致")
    if planned_run.predict_config_sha256:
        if not entry.native_input:
            problems.append("归档缺少 predict.yaml")
        else:
            predict_path = Path(entry.native_input)
            if not predict_path.is_absolute():
                predict_path = Path(__file__).resolve().parents[3] / predict_path
            if not predict_path.is_file():
                problems.append("实验清单声明的 predict.yaml 不存在")
            elif sha256_file(predict_path) != planned_run.predict_config_sha256:
                problems.append("predict.yaml 哈希与实验清单不一致")
    if entry.exp_config:
        config_path = Path(entry.exp_config)
        if not config_path.is_absolute():
            config_path = Path(__file__).resolve().parents[3] / config_path
        if not config_path.is_file():
            problems.append("实验清单声明的配置文件不存在")
        elif sha256_file(config_path) != planned_run.config_sha256:
            problems.append("配置哈希与实验清单不一致")
    else:
        problems.append("归档缺少配置文件")
    return problems


def _rate(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except Exception:  # noqa: BLE001
        return None


def _same_path(left: str | None, right: str | None) -> bool:
    if not left or not right:
        return True
    try:
        base = Path(__file__).resolve().parents[3]
        lpath = Path(left)
        rpath = Path(right)
        if not lpath.is_absolute():
            lpath = base / lpath
        if not rpath.is_absolute():
            rpath = base / rpath
        return lpath.resolve() == rpath.resolve()
    except OSError:
        return False


def _optional_equal(left: Any, right: Any) -> bool:
    """清单未声明该字段时不做约束；声明了就必须一致。"""
    return right is None or left == right


def archive_matches_manifest(entry: Any, manifest: ExperimentManifest) -> bool:
    """离线归档是否属于该实验的冻结身份（不含 seed）。"""
    return (
        entry.model_name == manifest.model_name
        and _optional_equal(entry.precision, manifest.model_precision)
        and _optional_equal(entry.system, manifest.system)
        and _optional_equal(entry.backend, manifest.backend)
        and _optional_equal(entry.backend_version, manifest.backend_version)
        and _optional_equal(entry.gpu_count, manifest.gpu_count)
        and _optional_equal(entry.tp_size, manifest.tp_size)
        and _optional_equal(entry.pp_size, manifest.pp_size)
        and _optional_equal(entry.dp_size, manifest.dp_size)
        and entry.input_len == manifest.input_len
        and entry.output_len == manifest.output_len
        and _optional_equal(entry.concurrency, manifest.concurrency)
        and entry.arrival_mode == manifest.arrival_mode
        and _optional_equal(_rate(entry.request_rate_rps), _rate(manifest.request_rate_rps))
        and (
            entry.dataset is None
            or manifest.dataset is None
            or entry.dataset == manifest.dataset
        )
        and _optional_equal(entry.trace_id, manifest.trace_id)
        and _optional_equal(entry.requests, manifest.num_requests)
    )


def manifest_matches(candidate: SimulationCandidate, manifest: ExperimentManifest) -> bool:
    deployment = candidate.deployment
    workload = candidate.workload
    return (
        (
            manifest.tool_version is None
            or candidate.source_tool_version == manifest.tool_version
        )
        and deployment.model_name == manifest.model_name
        and _optional_equal(deployment.model_precision, manifest.model_precision)
        and _optional_equal(deployment.system, manifest.system)
        and _optional_equal(deployment.backend, manifest.backend)
        and _optional_equal(deployment.backend_version, manifest.backend_version)
        and _optional_equal(deployment.gpu_count, manifest.gpu_count)
        and _optional_equal(deployment.serving_mode, manifest.serving_mode)
        and _optional_equal(deployment.replicas, manifest.replicas)
        and _optional_equal(deployment.tp_size, manifest.tp_size)
        and _optional_equal(deployment.pp_size, manifest.pp_size)
        and _optional_equal(deployment.dp_size, manifest.dp_size)
        and workload.input_len == manifest.input_len
        and workload.output_len == manifest.output_len
        and _optional_equal(workload.concurrency, manifest.concurrency)
        and workload.arrival_mode == manifest.arrival_mode
        and _optional_equal(
            _rate(workload.request_rate_rps), _rate(manifest.request_rate_rps)
        )
        and _optional_equal(workload.dataset, manifest.dataset)
        and _optional_equal(workload.trace_id, manifest.trace_id)
        and _optional_equal(workload.num_requests, manifest.num_requests)
        and _optional_equal(workload.stop_condition, manifest.stop_condition)
    )


def _signature(candidate: SimulationCandidate) -> tuple[Any, ...]:
    metrics = candidate.metrics
    return (
        candidate.verdict,
        metrics.sim_p99_ttft_ms,
        metrics.sim_p99_tpot_ms,
        metrics.sim_output_throughput_tok_s,
        metrics.sim_completed_requests,
        metrics.sim_ttft_samples,
        metrics.sim_tpot_samples,
    )


def _note(candidate: SimulationCandidate, text: str) -> None:
    if text not in candidate.notes:
        candidate.notes.append(text)


def _apply(
    group: Sequence[SimulationCandidate],
    manifest: ExperimentManifest | None,
) -> str | None:
    """把完整性/重复性结果写到组内候选；返回 SLA 聚合状态。"""
    by_seed: dict[int | None, list[SimulationCandidate]] = {}
    for candidate in group:
        by_seed.setdefault(candidate.workload.seed, []).append(candidate)

    expected = set(manifest.expected_seeds) if manifest is not None else set()
    required = (
        manifest.required_completed_requests if manifest is not None else 500
    )
    all_found = {seed for seed in by_seed if seed is not None}
    out_of_scope = (
        set(manifest.out_of_scope_seeds) if manifest is not None else set()
    )
    unexpected_seeds = all_found - expected if manifest is not None else set()
    truly_unexpected = unexpected_seeds - out_of_scope
    found_seeds = all_found & expected if manifest is not None else all_found

    # 只有清单明确排除的 seed 可以不计入；其余额外 seed 使工作点 invalid。
    seedless_count = sum(1 for seed in by_seed if seed is None)
    invalid_evidence = bool(truly_unexpected) or bool(
        seedless_count and manifest is not None
    )
    incomplete_seed_count = 0
    valid_seeds: list[int] = []
    seed_signatures: dict[int, tuple[Any, ...]] = {}
    planned_runs = (
        {run.seed: run for run in manifest.runs}
        if manifest is not None
        else {}
    )
    for seed in found_seeds:
        candidates = by_seed[seed]
        signatures = {_signature(candidate) for candidate in candidates}
        if len(signatures) > 1:
            invalid_evidence = True
            continue
        seed_signatures[seed] = next(iter(signatures))
        candidate = candidates[0]
        completed = candidate.metrics.sim_completed_requests
        has_provenance = bool(candidate.source_file and candidate.source_sha256)
        if (
            candidate.verdict in ("predicted_pass", "predicted_fail")
            and completed is not None
            and completed >= required
            and has_provenance
        ):
            planned = planned_runs.get(seed)
            if manifest is not None:
                if planned is None:
                    incomplete_seed_count += 1
                    continue
                if planned.status != "succeeded":
                    if planned.status == "invalid":
                        invalid_evidence = True
                    else:
                        incomplete_seed_count += 1
                    continue
                if not planned.archive_sha256 or not planned.config_sha256:
                    # 清单必须为每次运行声明归档与配置哈希；缺失不是“匹配”。
                    incomplete_seed_count += 1
                    continue
                if planned.archive and not _same_path(
                    candidate.source_file, planned.archive
                ):
                    invalid_evidence = True
                    continue
                if candidate.source_sha256 != planned.archive_sha256:
                    invalid_evidence = True
                    continue
                if (
                    planned.completed_requests is not None
                    and completed != planned.completed_requests
                ):
                    invalid_evidence = True
                    continue
                if (
                    candidate.source_config_sha256
                    != planned.config_sha256
                ):
                    invalid_evidence = True
                    continue
                if planned.predict_config_sha256:
                    if (
                        candidate.source_predict_config_sha256
                        != planned.predict_config_sha256
                    ):
                        invalid_evidence = True
                        continue
                if (
                    planned.normalized_config_sha256
                    and manifest.base_config_sha256
                    and planned.normalized_config_sha256
                    != manifest.base_config_sha256
                ):
                    invalid_evidence = True
                    continue
            valid_seeds.append(seed)
        else:
            incomplete_seed_count += 1

    if manifest is not None:
        missing = expected - found_seeds
        if invalid_evidence:
            completeness = "invalid"
        elif missing or incomplete_seed_count:
            completeness = "incomplete"
        else:
            completeness = "complete"
        expected_runs = len(expected)
    else:
        # 没有清单时，单次运行仍是单次；多次运行无法证明“预期集合”完整。
        completeness = "incomplete" if len(found_seeds) > 1 else None
        expected_runs = None

    valid_count = len(valid_seeds)
    pass_count = sum(
        1
        for seed in valid_seeds
        if seed_signatures[seed][0] == "predicted_pass"
    )
    if completeness == "complete":
        if pass_count == valid_count:
            sla_status = "all_pass"
        elif pass_count == 0:
            sla_status = "all_fail"
        else:
            sla_status = "mixed"
    elif completeness is None:
        sla_status = None
    else:
        sla_status = "unknown"

    for candidate in group:
        seed = candidate.workload.seed
        candidate.p99_statistically_evaluable = (
            candidate.verdict in ("predicted_pass", "predicted_fail")
            and candidate.metrics.sim_ttft_samples is not None
            and candidate.metrics.sim_tpot_samples is not None
            and candidate.metrics.sim_ttft_samples >= 30
            and candidate.metrics.sim_tpot_samples >= 30
        )
        if seed is None and manifest is not None:
            candidate.experiment_completeness = "invalid"
            candidate.experiment_expected_runs = expected_runs
            candidate.experiment_found_runs = len(found_seeds)
            candidate.experiment_valid_runs = valid_count
            candidate.experiment_incomplete_runs = None
            candidate.experiment_unexpected_runs = (
                len(truly_unexpected) + seedless_count
            )
            candidate.repeatability_status = "incomplete"
            candidate.repeatability_runs = valid_count
            candidate.repeatability_pass_count = pass_count
            _note(
                candidate,
                "无 seed 的运行无法与实验清单的 seed 记录对齐，不参与重复性结论",
            )
            continue
        if seed in unexpected_seeds:
            candidate.experiment_completeness = (
                "excluded" if seed in out_of_scope else "invalid"
            )
            candidate.experiment_expected_runs = expected_runs
            candidate.experiment_found_runs = len(found_seeds)
            candidate.experiment_valid_runs = valid_count
            candidate.experiment_incomplete_runs = None
            candidate.experiment_unexpected_runs = len(truly_unexpected)
            candidate.repeatability_status = "incomplete"
            candidate.repeatability_runs = valid_count
            candidate.repeatability_pass_count = pass_count
            _note(
                candidate,
                "该 seed 不在实验清单的预期 seed 集合中，不参与聚合"
                + ("（清单已明确排除）" if seed in out_of_scope else ""),
            )
            continue
        candidate.experiment_completeness = completeness
        candidate.experiment_expected_runs = expected_runs
        candidate.experiment_found_runs = len(found_seeds)
        candidate.experiment_valid_runs = valid_count
        candidate.experiment_incomplete_runs = (
            expected_runs - valid_count
            if expected_runs is not None
            else len(found_seeds) - valid_count
        )
        candidate.experiment_unexpected_runs = (
            len(truly_unexpected) + seedless_count
        )
        if completeness is not None:
            candidate.repeatability_status = (
                "incomplete" if completeness != "complete" else sla_status
            )
            candidate.repeatability_runs = valid_count
            candidate.repeatability_pass_count = pass_count
            if completeness != "complete":
                _note(
                    candidate,
                    "实验完整性 "
                    f"{completeness}：预期 {expected_runs if expected_runs is not None else '未知'}、"
                    f"发现 {len(found_seeds)}、有效 {valid_count}",
                )
    return sla_status


def apply_experiment_manifests(
    candidates: Sequence[SimulationCandidate],
    manifests: Sequence[ExperimentManifest] | None = None,
    *,
    require_manifest_for_multi_seed: bool = True,
) -> list[str]:
    """按工作点应用清单；返回面向报告的说明。"""
    notes: list[str] = []
    groups: dict[tuple[Any, ...], list[SimulationCandidate]] = {}
    for candidate in candidates:
        groups.setdefault(repeatability_group_key(candidate), []).append(candidate)

    used: set[str] = set()
    matched_groups: dict[str, list[list[SimulationCandidate]]] = {}
    for group in groups.values():
        matches = [
            manifest
            for manifest in manifests or []
            if all(manifest_matches(candidate, manifest) for candidate in group)
        ]
        if len(matches) > 1:
            for candidate in group:
                candidate.experiment_completeness = "invalid"
                candidate.repeatability_status = "incomplete"
                _note(candidate, "同一工作点匹配到多份实验清单，无法确定预期集合")
            notes.append("同一工作点匹配到多份实验清单：按 invalid 处理")
            continue
        manifest = matches[0] if matches else None
        if manifest is not None:
            used.add(manifest.experiment_id)
            matched_groups.setdefault(manifest.experiment_id, []).append(
                list(group)
            )
        _apply(group, manifest)
        if manifest is None:
            seeds = {c.workload.seed for c in group if c.workload.seed is not None}
            if len(seeds) > 1 and require_manifest_for_multi_seed:
                notes.append(
                    "多 seed 工作点缺少实验清单：完整性保持 incomplete，"
                    "不能把找到的 seed 当作完整实验"
                )
    for experiment_id, group_list in matched_groups.items():
        if len(group_list) < 2:
            continue
        for group in group_list:
            for candidate in group:
                candidate.experiment_completeness = "invalid"
                candidate.repeatability_status = "incomplete"
                _note(
                    candidate,
                    f"同一实验清单 {experiment_id} 匹配到多个工作点，无法唯一归属",
                )
        notes.append(
            f"实验清单 {experiment_id} 匹配到多个工作点：按 invalid 处理"
        )
    unused = [
        manifest.experiment_id
        for manifest in manifests or []
        if manifest.experiment_id not in used
    ]
    if unused:
        notes.append("未被任何候选使用的实验清单：" + "、".join(unused))
    return notes
