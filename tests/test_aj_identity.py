"""AJ0：冻结运行配置必须贯穿目标身份、匹配与排序。"""

from __future__ import annotations

from llm_capacity_pricing.perf.benchmark_matcher import match
from llm_capacity_pricing.schemas.performance import (
    BenchmarkSignature,
    TargetRequirement,
    WorkloadSignature,
)


def _target(sha: str | None):
    return TargetRequirement(
        model_name="Qwen/Qwen3-8B",
        model_revision="rev-test",
        weight_precision="bf16",
        hardware_model="NVIDIA GeForce RTX 3090",
        gpu_count=1,
        tp_size=1,
        dp_size=1,
        framework="vllm",
        framework_version="0.11.0",
        runtime_config_sha256=sha,
        workload=WorkloadSignature(
            input_len_target=2048,
            output_len_target=256,
            dataset_name="random",
            arrival_mode="unbounded",
            configured_concurrency=8,
        ),
    )


def _signature(sha: str | None):
    return BenchmarkSignature(
        signature_id="sig-test",
        source_kind="benchmark_run",
        source_ref="rtx3090:test",
        model_name="Qwen/Qwen3-8B",
        model_revision="rev-test",
        weight_precision="bf16",
        hardware_model="NVIDIA GeForce RTX 3090",
        gpu_count=1,
        tp_size=1,
        dp_size=1,
        framework="vllm",
        framework_version="0.11.0",
        runtime_config_sha256=sha,
        workload=WorkloadSignature(
            input_len_target=2048,
            output_len_target=256,
            dataset_name="random",
            arrival_mode="unbounded",
            configured_concurrency=8,
        ),
        data_trust="measured_internal",
    )


def test_same_runtime_config_can_be_exact_match():
    result = match(_target("a" * 64), _signature("a" * 64))
    assert result.level == "exact_match"


def test_missing_runtime_config_blocks_exact_match():
    result = match(_target("a" * 64), _signature(None))
    assert "unverified:runtime_config_sha256_unverified" in result.reasons
    assert result.level != "exact_match"


def test_different_runtime_config_is_a_mismatch_not_a_reference():
    result = match(_target("a" * 64), _signature("b" * 64))
    assert any(
        reason.startswith("runtime_config_sha256_mismatch:")
        for reason in result.reasons
    )


def test_target_facts_and_identity_carry_runtime_config():
    from llm_capacity_pricing.services import _identity_from_facts, _target_from_facts

    facts = {
        "model_name": "Qwen/Qwen3-8B",
        "hardware_model": "NVIDIA GeForce RTX 3090",
        "gpu_count": 1,
        "tp_size": 1,
        "dp_size": 1,
        "runtime_config_sha256": "c" * 64,
        "input_len_target": 2048,
        "output_len_target": 256,
        "arrival_mode": "unbounded",
        "configured_concurrency": 8,
    }
    assert _target_from_facts(facts).runtime_config_sha256 == "c" * 64
    assert _identity_from_facts(facts)["runtime_config_sha256"] == "c" * 64
