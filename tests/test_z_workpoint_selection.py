"""Z4：离线工作点选择与手动/Agent 结果一致性。"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from llm_capacity_pricing.agent import commandcode_client as cc_module
from llm_capacity_pricing.agent import service as service_module
from llm_capacity_pricing.agent import tool_cli
from llm_capacity_pricing.agent.service import AgentService
from llm_capacity_pricing.app.demo_service import DemoInputs, run_demo
from llm_capacity_pricing.app import offline_archives as oa
from llm_capacity_pricing.app.offline_archives import (
    ArchiveEntry,
    available_workpoints,
    find_archive,
    offline_simulation_paths,
)

ROOT = Path(__file__).resolve().parents[1]


def _sha(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture()
def isolated_agent_store(tmp_path, monkeypatch):
    runs = tmp_path / "runs"
    tasks = tmp_path / "tasks"
    monkeypatch.setattr(tool_cli, "RUNS", runs)
    monkeypatch.setattr(tool_cli, "TASKS", tasks)
    monkeypatch.setattr(service_module, "RUNS", runs)
    monkeypatch.setattr(service_module, "TASKS", tasks)
    monkeypatch.setattr(cc_module, "TASKS", tasks)
    monkeypatch.setattr(cc_module, "WORKSPACE", tmp_path / "cc-workspace")
    monkeypatch.setattr(cc_module, "SANDBOX_HOME", tmp_path / "cc-homes")
    return runs, tasks


def _native(result) -> list:
    sim = result.simulation
    if sim is None:
        return []
    return [c for c in sim.candidates if c.candidate_id.startswith("sim-native")]


def test_c32_prefers_tp8_over_tp4():
    entry = find_archive(
        model_id="qwen3-32b",
        hardware_id="nvidia-h100-sxm-80gb",
        input_len=8192,
        output_len=256,
        concurrency=32,
        precision="bf16",
        framework="vllm",
        framework_version="0.24.0",
    )
    assert entry is not None
    assert entry.tp_size == 8
    assert entry.gpu_count == 8
    assert entry.native_prediction.endswith("prediction.json")


@pytest.mark.parametrize("concurrency", [8, 32, 64])
def test_exact_workpoint_has_native_evidence(concurrency):
    paths = offline_simulation_paths(
        model_id="qwen3-32b",
        hardware_id="nvidia-h100-sxm-80gb",
        input_len=8192,
        output_len=256,
        concurrency=concurrency,
        precision="bf16",
        framework="vllm",
        framework_version="0.24.0",
    )
    assert paths["archives"]
    assert all(item["native_model_precision"] == "bf16" for item in paths["archives"])
    assert all(item["native_prediction"].endswith("prediction.json") for item in paths["archives"])


def test_unknown_workpoint_does_not_borrow_another_p99():
    paths = offline_simulation_paths(
        model_id="qwen3-32b",
        hardware_id="nvidia-h100-sxm-80gb",
        input_len=8192,
        output_len=256,
        concurrency=16,
        precision="bf16",
        framework="vllm",
        framework_version="0.24.0",
    )
    assert "native_prediction" not in paths
    # 扫描器仍可用于候选枚举，但不提供 P99
    assert paths["sweeper_csv"]


def test_available_workpoints_are_explicit():
    points = available_workpoints(
        model_id="qwen3-32b", hardware_id="nvidia-h100-sxm-80gb"
    )
    concurrency = {item["concurrency"] for item in points}
    assert {8, 32, 64}.issubset(concurrency)
    assert all(item["precision"] == "bf16" for item in points)


def _archive_entry(**overrides) -> ArchiveEntry:
    payload = {
        "name": "fake-archive",
        "model_name": "Qwen/Qwen3-32B",
        "system": "h100_sxm",
        "precision": None,
        "backend": "vllm",
        "backend_version": "0.24.0",
        "input_len": 8192,
        "output_len": 256,
        "concurrency": 8,
        "gpu_count": 8,
        "tp_size": 8,
        "requests": 500,
        "native_prediction": "fake/prediction.json",
        "native_input": "fake/predict.yaml",
        "exp_config": None,
        "per_ops_source": None,
        "tool_version": "0.12.1",
        "completed_requests": 500,
    }
    payload.update(overrides)
    return ArchiveEntry(**payload)


def test_missing_archive_precision_is_not_an_identity_match(monkeypatch):
    """归档没写精度时，不能拿请求精度补成“已核实”。"""
    monkeypatch.setattr(oa, "archive_entries", lambda: [_archive_entry()])
    assert (
        find_archive(
            model_id="qwen3-32b",
            hardware_id="nvidia-h100-sxm-80gb",
            input_len=8192,
            output_len=256,
            concurrency=8,
            precision="bf16",
            framework="vllm",
            framework_version="0.24.0",
        )
        is None
    )
    paths = offline_simulation_paths(
        model_id="qwen3-32b",
        hardware_id="nvidia-h100-sxm-80gb",
        input_len=8192,
        output_len=256,
        concurrency=8,
        precision="bf16",
        framework="vllm",
        framework_version="0.24.0",
    )
    # 仍可展示为不可比证据，但绝不能把请求精度写成归档精度
    assert paths["archives"]
    assert paths["archives"][0]["native_model_precision"] is None


def test_archive_with_known_identity_matches(monkeypatch):
    monkeypatch.setattr(
        oa, "archive_entries", lambda: [_archive_entry(precision="bf16")]
    )
    entry = find_archive(
        model_id="qwen3-32b",
        hardware_id="nvidia-h100-sxm-80gb",
        input_len=8192,
        output_len=256,
        concurrency=8,
        precision="bf16",
        framework="vllm",
        framework_version="0.24.0",
    )
    assert entry is not None
    assert entry.precision == "bf16"


@pytest.mark.parametrize(
    ("concurrency", "ttft", "tpot", "expected"),
    [
        (8, "6000", "40", "predicted_pass"),
        (64, "6000", "40", "predicted_fail"),
        (32, "6000", "40", "predicted_pass"),
        (32, "400", "30", "predicted_fail"),
    ],
)
def test_manual_offline_workpoint_verdicts(
    tmp_path, concurrency, ttft, tpot, expected
):
    outcome = run_demo(
        DemoInputs(
            concurrency=concurrency,
            ttft_p99_ms_max=Decimal(ttft),
            tpot_p99_ms_max=Decimal(tpot),
            request_id=f"z4-manual-c{concurrency}",
        ),
        out_dir=tmp_path / f"manual-c{concurrency}-{ttft}-{tpot}",
    )
    natives = _native(outcome.result)
    assert natives
    assert any(candidate.verdict == expected for candidate in natives)
    assert all(candidate.comparable is True for candidate in natives)


def test_manual_offline_without_prediction_reports_gap(tmp_path):
    outcome = run_demo(
        DemoInputs(
            concurrency=16,
            ttft_p99_ms_max=Decimal("6000"),
            tpot_p99_ms_max=Decimal("40"),
            request_id="z4-manual-c16",
        ),
        out_dir=tmp_path / "manual-c16",
    )
    assert _native(outcome.result) == []
    assert all(
        candidate.verdict != "predicted_pass"
        for candidate in outcome.result.simulation.candidates
    )
    text = outcome.technical_md + outcome.proposal_md
    assert "模拟" in text  # 报告明确给出模拟缺口，而不是静默省略


class _FakeAgentClient:
    """模拟 Command Code 只负责触发工具；业务结果仍来自 tool_cli。"""

    def __init__(self, payload: dict):
        self.payload = payload
        self.cleaned: list[str] = []

    def run(self, text, *, task_id, session_id=None, timeout=None):
        tool_cli.main(
            [
                "evaluate",
                "--session-id",
                task_id,
                "--input-json",
                json.dumps(self.payload),
            ]
        )
        return cc_module.CommandCodeRun(
            task_id=task_id,
            final_text="已生成",
            subtype="success",
            stop_reason="end_turn",
            tool_names=[
                "mcp__lcp__get_platform_catalog",
                "mcp__lcp__evaluate_deployment_plan",
                "mcp__lcp__get_plan_report",
            ],
            session_id="cc-session-z4",
        )

    def cleanup(self, task_id):
        self.cleaned.append(task_id)


def _payload(concurrency: int, ttft: str, tpot: str) -> dict:
    return {
        "model_id": "qwen3-32b",
        "hardware_id": "nvidia-h100-sxm-80gb",
        "gpu_count": 8,
        "input_len": 8192,
        "output_len": 256,
        "concurrency": concurrency,
        "ttft_p99_ms_max": ttft,
        "tpot_p99_ms_max": tpot,
        "weight_precision": "bf16",
        "framework": "vllm",
        "framework_version": "0.24.0",
    }


@pytest.mark.parametrize(
    ("concurrency", "ttft", "tpot"),
    [(8, "6000", "40"), (64, "6000", "40"), (16, "6000", "40")],
)
def test_manual_and_agent_share_the_same_workpoint(
    isolated_agent_store, tmp_path, concurrency, ttft, tpot
):
    manual = run_demo(
        DemoInputs(
            concurrency=concurrency,
            ttft_p99_ms_max=Decimal(ttft),
            tpot_p99_ms_max=Decimal(tpot),
            request_id=f"z4-compare-c{concurrency}",
        ),
        out_dir=tmp_path / f"manual-c{concurrency}",
    )
    agent = AgentService(
        client=_FakeAgentClient(_payload(concurrency, ttft, tpot)),
        transport="commandcode",
    ).run("C{} 的部署方案".format(concurrency))
    assert agent.completed

    manual_native = _native(manual.result)
    agent_native = _native(agent.result)
    assert [c.candidate_id for c in manual_native] == [
        c.candidate_id for c in agent_native
    ]
    assert [c.verdict for c in manual_native] == [c.verdict for c in agent_native]
    assert [c.metrics.model_dump() for c in manual_native] == [
        c.metrics.model_dump() for c in agent_native
    ]
    assert agent.task.conclusions["simulation_status"] == "computed"
    if manual_native:
        assert (
            agent.task.conclusions["simulation_best_effort"]
            == manual.result.simulation.best_effort().candidate_id
        )
    else:
        assert agent.task.conclusions["simulation_best_effort"] is None


def test_gpu_limit_excludes_larger_deployment():
    # C32 同时有 TP4（4 卡）与 TP8（8 卡）归档；4 卡上限必须选 TP4
    entry = find_archive(
        model_id="qwen3-32b",
        hardware_id="nvidia-h100-sxm-80gb",
        input_len=8192,
        output_len=256,
        concurrency=32,
        precision="bf16",
        framework="vllm",
        framework_version="0.24.0",
        gpu_limit=4,
    )
    assert entry is not None
    assert entry.gpu_count == 4
    assert entry.tp_size == 4

    # C8 只有 TP8 归档：4 卡上限下不能借用
    assert (
        find_archive(
            model_id="qwen3-32b",
            hardware_id="nvidia-h100-sxm-80gb",
            input_len=8192,
            output_len=256,
            concurrency=8,
            precision="bf16",
            framework="vllm",
            framework_version="0.24.0",
            gpu_limit=4,
        )
        is None
    )
    paths = offline_simulation_paths(
        model_id="qwen3-32b",
        hardware_id="nvidia-h100-sxm-80gb",
        input_len=8192,
        output_len=256,
        concurrency=8,
        precision="bf16",
        framework="vllm",
        framework_version="0.24.0",
        gpu_limit=4,
    )
    assert paths is not None and "archives" not in paths


def test_c32_returns_both_tp4_and_tp8():
    from llm_capacity_pricing.app.offline_archives import find_archives

    entries = find_archives(
        model_id="qwen3-32b",
        hardware_id="nvidia-h100-sxm-80gb",
        input_len=8192,
        output_len=256,
        concurrency=32,
        precision="bf16",
        framework="vllm",
        framework_version="0.24.0",
        gpu_limit=8,
        tp_sizes=(4, 8),
    )
    assert {entry.tp_size for entry in entries} == {4, 8}
    assert {entry.gpu_count for entry in entries} == {4, 8}


def test_unknown_model_has_no_offline_simulation():
    paths = offline_simulation_paths(
        model_id="qwen3-8b",
        hardware_id="nvidia-h100-sxm-80gb",
        input_len=8192,
        output_len=256,
        concurrency=32,
        precision="bf16",
        framework="vllm",
        framework_version="0.24.0",
        gpu_limit=8,
    )
    assert paths is None


def test_completed_requests_are_the_sample_window(monkeypatch):
    monkeypatch.setattr(
        oa,
        "archive_entries",
        lambda: [_archive_entry(precision="bf16", completed_requests=10, requests=500)],
    )
    assert (
        find_archive(
            model_id="qwen3-32b",
            hardware_id="nvidia-h100-sxm-80gb",
            input_len=8192,
            output_len=256,
            concurrency=8,
            precision="bf16",
        )
        is None
    )
    monkeypatch.setattr(
        oa,
        "archive_entries",
        lambda: [_archive_entry(precision="bf16", completed_requests=500, requests=500)],
    )
    assert (
        find_archive(
            model_id="qwen3-32b",
            hardware_id="nvidia-h100-sxm-80gb",
            input_len=8192,
            output_len=256,
            concurrency=8,
            precision="bf16",
        )
        is not None
    )


def test_archive_without_gpu_count_is_not_selected(monkeypatch):
    monkeypatch.setattr(
        oa,
        "archive_entries",
        lambda: [_archive_entry(precision="bf16", gpu_count=None)],
    )
    assert (
        find_archive(
            model_id="qwen3-32b",
            hardware_id="nvidia-h100-sxm-80gb",
            input_len=8192,
            output_len=256,
            concurrency=8,
            precision="bf16",
        )
        is None
    )


def test_multi_candidate_archive_is_indexed(tmp_path, monkeypatch):
    import json

    import yaml

    root = tmp_path / "archives"
    run = root / "multi-run"
    (run / "predict" / "out_1").mkdir(parents=True)
    (run / "predict" / "out_2").mkdir(parents=True)

    def write_pair(index: int, tp: int) -> None:
        config = {
            "engine": {
                "backend": "vllm",
                "backend_version": "0.24.0",
                "hardware": "h100_sxm",
                "mode": "aggregated",
                "model": "Qwen/Qwen3-32B",
                "workers": {
                    "aggregated": {
                        "parallelism": {"pipeline": 1, "replicas": 1, "tensor": tp}
                    }
                },
            },
            "traffic": {
                "load": {"concurrency": 32, "type": "concurrency"},
                "source": {
                    "input_tokens": 8192,
                    "output_tokens": 256,
                    "type": "synthetic",
                },
                "stop": {"requests": 500},
            },
        }
        (run / "predict" / f"predict_{index}.yaml").write_text(
            yaml.safe_dump(config), encoding="utf-8"
        )
        (run / "predict" / f"out_{index}" / "prediction.json").write_text(
            json.dumps(
                {
                    "p99_ttft_ms": 1000.0 + tp,
                    "p99_tpot_ms": 20.0,
                    "completed_requests": 500,
                    "decode_gpus_per_worker": tp,
                }
            ),
            encoding="utf-8",
        )

    write_pair(1, 4)
    write_pair(2, 8)
    (run / "manifest.json").write_text(
        json.dumps(
            {
                "run_id": "multi-run",
                "tool_version": "0.12.1",
                "spec": {"precision": "bf16", "database_mode": "SILICON"},
                "errors": [],
                "inputs": {
                    f"predict/predict_{index}.yaml": _sha(
                        run / "predict" / f"predict_{index}.yaml"
                    )
                    for index in (1, 2)
                },
                "outputs": {
                    f"predict/out_{index}/prediction.json": _sha(
                        run / "predict" / f"out_{index}" / "prediction.json"
                    )
                    for index in (1, 2)
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(oa, "ARCHIVE_ROOT", root)
    monkeypatch.setattr(oa, "ROOT", tmp_path)
    entries = oa.archive_entries()
    names = {entry.name for entry in entries}
    assert "multi-run:predict_1" in names
    assert "multi-run:predict_2" in names
    tp8 = find_archive(
        model_id="qwen3-32b",
        hardware_id="nvidia-h100-sxm-80gb",
        input_len=8192,
        output_len=256,
        concurrency=32,
        precision="bf16",
        framework="vllm",
        framework_version="0.24.0",
        gpu_limit=8,
    )
    assert tp8 is not None and tp8.tp_size == 8
    tp4 = find_archive(
        model_id="qwen3-32b",
        hardware_id="nvidia-h100-sxm-80gb",
        input_len=8192,
        output_len=256,
        concurrency=32,
        precision="bf16",
        framework="vllm",
        framework_version="0.24.0",
        gpu_limit=4,
    )
    assert tp4 is not None and tp4.tp_size == 4


def test_tp_size_allowlist_filters_archives():
    common = dict(
        model_id="qwen3-32b",
        hardware_id="nvidia-h100-sxm-80gb",
        input_len=8192,
        output_len=256,
        concurrency=32,
        precision="bf16",
        framework="vllm",
        framework_version="0.24.0",
        gpu_limit=8,
    )
    tp4 = find_archive(**common, tp_sizes=(4,))
    tp8 = find_archive(**common, tp_sizes=(8,))
    assert tp4 is not None and tp4.tp_size == 4
    assert tp8 is not None and tp8.tp_size == 8
    assert find_archive(**common, tp_sizes=(2,)) is None


def test_pipeline_parallelism_counts_toward_gpu_total(tmp_path, monkeypatch):
    import json

    import yaml

    root = tmp_path / "archives"
    run = root / "pp-run"
    (run / "predict" / "out_1").mkdir(parents=True)
    config = {
        "engine": {
            "backend": "vllm",
            "backend_version": "0.24.0",
            "hardware": "h100_sxm",
            "mode": "aggregated",
            "model": "Qwen/Qwen3-32B",
            "workers": {
                "aggregated": {
                    "parallelism": {"pipeline": 2, "replicas": 1, "tensor": 4}
                }
            },
        },
        "traffic": {
            "load": {"concurrency": 32, "type": "concurrency"},
            "source": {"input_tokens": 8192, "output_tokens": 256, "type": "synthetic"},
            "stop": {"requests": 500},
        },
    }
    (run / "predict" / "predict_1.yaml").write_text(
        yaml.safe_dump(config), encoding="utf-8"
    )
    (run / "predict" / "out_1" / "prediction.json").write_text(
        json.dumps(
            {
                "p99_ttft_ms": 1000.0,
                "p99_tpot_ms": 20.0,
                "completed_requests": 500,
                "decode_gpus_per_worker": 8,
            }
        ),
        encoding="utf-8",
    )
    (run / "manifest.json").write_text(
        json.dumps(
            {
                "run_id": "pp-run",
                "tool_version": "0.12.1",
                "spec": {"precision": "bf16", "database_mode": "SILICON"},
                "errors": [],
                "inputs": {
                    "predict/predict_1.yaml": _sha(
                        run / "predict" / "predict_1.yaml"
                    )
                },
                "outputs": {
                    "predict/out_1/prediction.json": _sha(
                        run / "predict" / "out_1" / "prediction.json"
                    )
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(oa, "ARCHIVE_ROOT", root)
    monkeypatch.setattr(oa, "ROOT", tmp_path)
    entries = oa.archive_entries()
    assert len(entries) == 1
    assert entries[0].gpu_count == 8  # TP4 × PP2 × replicas 1
    assert (
        find_archive(
            model_id="qwen3-32b",
            hardware_id="nvidia-h100-sxm-80gb",
            input_len=8192,
            output_len=256,
            concurrency=32,
            precision="bf16",
            gpu_limit=4,
        )
        is None
    )
