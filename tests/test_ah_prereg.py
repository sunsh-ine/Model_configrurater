"""AH1：预注册实验、执行后补全与允许变化校验。"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import yaml

from llm_capacity_pricing.perf.experiment_prereg import (
    build_preregistered_manifest,
    config_sha256,
    finalize_manifest,
    normalize_config,
)

PREDICT = Path(
    "data/simulation/archives/af-live-qwen3-8b-h200-tp1dp2-r5-seed1/predict/predict_1.yaml"
)


def _manifest():
    config = yaml.safe_load(PREDICT.read_text(encoding="utf-8"))
    return build_preregistered_manifest(
        experiment_id="ah-test-prereg",
        predict_config=config,
        expected_seeds=[1, 2, 3, 4, 5],
        model_name="Qwen/Qwen3-8B",
        model_precision="bf16",
        system="h200_sxm",
        backend="vllm",
        backend_version="0.24.0",
        gpu_count=2,
        tp_size=1,
        pp_size=1,
        dp_size=2,
        replicas=2,
        input_len=8192,
        output_len=256,
        concurrency=None,
        arrival_mode="poisson",
        request_rate_rps=Decimal("5"),
        dataset="synthetic",
        trace_id=None,
        num_requests=500,
        stop_condition="requests=500",
        tool_version="0.12.1",
    )


def test_normalization_only_allows_seed_change():
    config = yaml.safe_load(PREDICT.read_text(encoding="utf-8"))
    changed_seed = yaml.safe_load(PREDICT.read_text(encoding="utf-8"))
    changed_seed["traffic"]["load"]["seed"] = 999
    assert config_sha256(config) == config_sha256(changed_seed)

    changed_rate = yaml.safe_load(PREDICT.read_text(encoding="utf-8"))
    changed_rate["traffic"]["load"]["requests_per_second"] = 6
    assert config_sha256(config) != config_sha256(changed_rate)
    assert "seed" not in normalize_config(config)["traffic"]["load"]


def test_preregistered_manifest_starts_planned_and_can_be_finalized():
    manifest = _manifest()
    assert manifest.manifest_status == "preregistered"
    assert manifest.execution_status == "planned"
    assert manifest.runs == []
    assert manifest.base_config_sha256

    finalized = finalize_manifest(manifest)
    assert finalized.execution_status == "completed"
    assert len(finalized.runs) == 5
    assert all(run.status == "succeeded" for run in finalized.runs)
    assert all(run.normalized_config_sha256 == manifest.base_config_sha256 for run in finalized.runs)
    assert all(run.predict_config_sha256 for run in finalized.runs)


def test_out_of_plan_config_change_invalidates_runs():
    manifest = _manifest().model_copy(update={"base_config_sha256": "0" * 64})
    finalized = finalize_manifest(manifest)
    assert all(run.status == "invalid" for run in finalized.runs)
    assert any("超出允许字段" in note for run in finalized.runs for note in run.notes)


def test_aborted_preregistered_manifest_round_trips():
    manifest = _manifest().model_copy(update={"execution_status": "aborted"})
    dumped = manifest.model_dump(mode="json")
    reloaded = type(manifest).model_validate(dumped)
    assert reloaded.execution_status == "aborted"
    assert reloaded.manifest_status == "preregistered"
