"""AG1/AG2：实验清单完整性、预期 seed 与两层状态。"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from llm_capacity_pricing.app.offline_archives import find_archives
from llm_capacity_pricing.perf.aisimulate_adapter import SimulationTarget, load_archived
from llm_capacity_pricing.perf.experiment_audit import load_manifests
from llm_capacity_pricing.schemas.experiment_manifest import ExperimentManifest
from llm_capacity_pricing.schemas.plan import SimulationArchiveSpec


def _spec(seed: int) -> SimulationArchiveSpec:
    entries = find_archives(
        model_id="qwen3-8b",
        hardware_id="nvidia-h200-sxm-141gb",
        input_len=8192,
        output_len=256,
        concurrency=None,
        precision="bf16",
        framework="vllm",
        framework_version="0.24.0",
        gpu_limit=2,
        tp_sizes=(1,),
        arrival_mode="poisson",
        request_rate_rps="5",
        seed=seed,
        min_requests=0,
    )
    entry = next(item for item in entries if item.dp_size == 2)
    return SimulationArchiveSpec(
        native_prediction=entry.native_prediction,
        native_input=entry.native_input,
        hardware_id="nvidia-h200-sxm-141gb",
        exp_config=entry.exp_config,
        native_model_precision=entry.precision,
        tool_version=entry.tool_version,
        database_mode=entry.database_mode,
        database_mode_source=entry.database_mode_source,
    )


def _manifest(*, expected=(1, 2), required=500) -> ExperimentManifest:
    base = next(
        item
        for item in load_manifests()
        if item.experiment_id == "af-8b-h200-dp2-r5"
    )
    return base.model_copy(
        update={
            "expected_seeds": list(expected),
            "required_completed_requests": required,
            "runs": [run for run in base.runs if run.seed in expected],
        }
    )


def _run(seeds, manifest, target=None):
    return load_archived(
        archives=[_spec(seed) for seed in seeds],
        target=target
        or SimulationTarget(
            ttft_p99_ms_max=Decimal("6000"),
            tpot_p99_ms_max=Decimal("40"),
        ),
        experiment_manifests=[manifest],
    )


def test_expected_seed_missing_is_incomplete_even_if_others_pass():
    result = _run((1, 2), _manifest(expected=(1, 2, 3, 4, 5)))
    for candidate in result.candidates:
        assert candidate.experiment_completeness == "incomplete"
        assert candidate.experiment_expected_runs == 5
        assert candidate.experiment_found_runs == 2
        assert candidate.experiment_incomplete_runs == 3
        assert candidate.repeatability_status == "incomplete"
    assert result.recommended() is None


def test_complete_manifest_keeps_two_layer_status():
    result = _run((1, 2), _manifest(expected=(1, 2)))
    for candidate in result.candidates:
        assert candidate.experiment_completeness == "complete"
        assert candidate.repeatability_status in ("all_pass", "all_fail", "mixed")
        assert candidate.p99_statistically_evaluable is True


def test_short_completed_run_is_incomplete_not_p99_failure():
    result = _run((1, 2), _manifest(expected=(1, 2), required=501))
    for candidate in result.candidates:
        assert candidate.experiment_completeness == "incomplete"
        assert candidate.repeatability_status == "incomplete"
    assert result.recommended() is None


def test_unexpected_seed_is_excluded_from_aggregation():
    result = _run((1, 2, 42), _manifest(expected=(1, 2)))
    by_seed = {candidate.workload.seed: candidate for candidate in result.candidates}
    assert by_seed[42].experiment_completeness == "excluded"
    assert by_seed[42].repeatability_status == "incomplete"
    assert by_seed[1].experiment_completeness == "complete"
    assert by_seed[2].experiment_completeness == "complete"


def test_multi_seed_without_manifest_cannot_claim_all_pass():
    result = load_archived(
        archives=[_spec(1), _spec(2)],
        target=SimulationTarget(
            ttft_p99_ms_max=Decimal("6000"),
            tpot_p99_ms_max=Decimal("40"),
        ),
    )
    for candidate in result.candidates:
        assert candidate.experiment_completeness == "incomplete"
        assert candidate.experiment_expected_runs is None
        assert candidate.repeatability_status == "incomplete"
    assert any("缺少实验清单" in item for item in result.limitations)


def test_manifest_rejects_duplicate_or_empty_seed_list():
    import pytest

    base = _manifest(expected=(1, 2))
    for payload in (
        base.model_dump() | {"expected_seeds": []},
        base.model_dump() | {"expected_seeds": [1, 1]},
    ):
        with pytest.raises(ValueError):
            ExperimentManifest.model_validate(payload)


def test_manifest_rejects_unexpected_run_seed():
    import pytest

    base = _manifest(expected=(1, 2))
    payload = base.model_dump()
    payload["runs"] = [
        {
            "seed": 3,
            "status": "succeeded",
        }
    ]
    with pytest.raises(ValueError):
        ExperimentManifest.model_validate(payload)


def test_manifest_archive_hash_mismatch_marks_experiment_invalid():
    manifest = _manifest(expected=(1, 2))
    payload = manifest.model_dump()
    payload["runs"][0]["archive_sha256"] = "0" * 64
    manifest = ExperimentManifest.model_validate(payload)
    result = _run((1, 2), manifest)
    for candidate in result.candidates:
        assert candidate.experiment_completeness == "invalid"
        assert candidate.repeatability_status == "incomplete"
    assert result.recommended() is None


def test_manifest_failed_run_is_not_counted_as_valid():
    manifest = _manifest(expected=(1, 2))
    payload = manifest.model_dump()
    payload["runs"][0]["status"] = "failed"
    manifest = ExperimentManifest.model_validate(payload)
    result = _run((1, 2), manifest)
    for candidate in result.candidates:
        assert candidate.experiment_completeness == "incomplete"
        assert candidate.experiment_valid_runs == 1


def test_manifest_rejects_inconsistent_run_config_hashes():
    import pytest

    manifest = _manifest(expected=(1, 2))
    payload = manifest.model_dump()
    payload["runs"][0]["config_sha256"] = "f" * 64
    with pytest.raises(ValueError):
        ExperimentManifest.model_validate(payload)


def test_existing_af_manifests_are_marked_reconstructed():
    manifests = load_manifests()
    assert len(manifests) == 3
    assert all(m.manifest_status == "reconstructed" for m in manifests)
    assert all(m.execution_status == "completed" for m in manifests)
