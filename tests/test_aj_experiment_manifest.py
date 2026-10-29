"""AJ0.3：真实 Benchmark 预注册清单的契约断言。"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from llm_capacity_pricing.schemas.benchmark_experiment import (
    BenchmarkExperimentManifest,
    BenchmarkWorkpoint,
)

SHA = "a" * 64


def _workpoint(**overrides):
    payload = {
        "workpoint_id": "wp-c6",
        "input_len": 2048,
        "output_len": 256,
        "concurrency": 6,
        "expected_seeds": [1, 2, 3, 4, 5],
        "requests_per_run": 500,
        "repeats": 5,
        "ttft_p99_ms_max": "6000",
        "tpot_p99_ms_max": "40",
    }
    payload.update(overrides)
    return BenchmarkWorkpoint(**payload)


def _manifest(**overrides):
    payload = {
        "experiment_id": "aj-formal-c6",
        "created_at": datetime.now(timezone.utc),
        "runtime_config_sha256": SHA,
        "workpoints": [_workpoint()],
    }
    payload.update(overrides)
    return BenchmarkExperimentManifest(**payload)


def test_manifest_accepts_fixed_concurrency_workpoint():
    manifest = _manifest()
    assert manifest.expected_run_count() == 5
    assert manifest.manifest_status == "preregistered"


def test_duplicate_seeds_rejected():
    with pytest.raises(ValidationError):
        _workpoint(expected_seeds=[1, 1, 2, 3, 4])


def test_repeats_must_match_seed_count():
    with pytest.raises(ValidationError):
        _workpoint(expected_seeds=[1, 2, 3], repeats=5)


def test_concurrency_and_rate_are_mutually_exclusive():
    with pytest.raises(ValidationError):
        _workpoint(request_rate_rps="0.65")
    with pytest.raises(ValidationError):
        _workpoint(concurrency=None)


def test_open_arrival_workpoint_requires_rate():
    point = _workpoint(concurrency=None, request_rate_rps="0.65")
    assert point.request_rate_rps is not None
    assert point.concurrency is None


def test_manifest_requires_workpoints():
    with pytest.raises(ValidationError):
        _manifest(workpoints=[])
