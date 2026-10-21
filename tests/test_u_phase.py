"""U 阶段：推荐身份、CLI 机箱与工作点口径。"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from llm_capacity_pricing.app import demo_cli
from llm_capacity_pricing.app.demo_service import DemoInputs, build_plan_request, run_demo
from llm_capacity_pricing.perf.aisimulate_adapter import (
    SimulationTarget,
    check_comparability,
    parallel_identity_gaps,
    parse_native_input_yaml,
    parse_native_prediction,
    parse_sweeper_csv,
)
from llm_capacity_pricing.schemas.simulation import (
    SimulationCandidate,
    SimulationDeployment,
    SimulationResult,
    SimulationToolIdentity,
)

ROOT = Path(__file__).resolve().parents[1]
POC = ROOT / "docs/poc/aisimulate"
NATIVE_INPUT = POC / "16_matched_native_input_tp8_conc32_bf16.yaml"
NATIVE_PRED = POC / "17_matched_native_result_tp8_conc32_bf16.json"
SWEEPER = POC / "artifacts/matched_pareto_vllm_bf16.csv"


def _native_candidate(
    input_path: Path = NATIVE_INPUT, prediction_path: Path = NATIVE_PRED
):
    deployment, workload = parse_native_input_yaml(
        input_path,
        model_precision="bf16",
        version_aliases={"current": "0.24.0"},
    )
    return parse_native_prediction(
        prediction_path, deployment=deployment, workload=workload
    )


def _prediction_without(tmp_path: Path, *fields: str) -> Path:
    payload = json.loads(NATIVE_PRED.read_text(encoding="utf-8"))
    for field in fields:
        payload.pop(field, None)
    path = tmp_path / "prediction.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# U1.1 并行身份
# ---------------------------------------------------------------------------


def test_native_parallel_identity_is_derived_with_basis():
    candidate = _native_candidate()
    assert candidate.deployment.tp_size == 8
    assert candidate.deployment.pp_size == 1
    assert candidate.deployment.dp_size == 1
    assert candidate.deployment.gpu_count == 8
    joined = "；".join(candidate.notes)
    assert "DP1 由" in joined and "replicas=1" in joined
    assert "PP1 由" in joined and "decode_gpus_per_worker=8" in joined
    assert parallel_identity_gaps(candidate.deployment) == []


def test_unknown_dp_is_not_silently_treated_as_one(tmp_path):
    doc = yaml.safe_load(NATIVE_INPUT.read_text(encoding="utf-8"))
    del doc["engine"]["workers"]["aggregated"]["parallelism"]["replicas"]
    modified = tmp_path / "native_no_replicas.yaml"
    modified.write_text(yaml.safe_dump(doc, allow_unicode=True), encoding="utf-8")

    candidate = _native_candidate(modified)
    assert candidate.deployment.dp_size is None
    assert any("DP 未声明" in note for note in candidate.notes)
    assert any("缺 DP" in gap for gap in parallel_identity_gaps(candidate.deployment))

    # 未知 DP 的候选不能进入推荐池
    candidate.verdict = "predicted_pass"
    candidate.comparable = True
    candidate.resource_feasible = None
    result = SimulationResult(status="computed", candidates=[candidate])
    assert result.eligible() == []


def test_missing_per_worker_counts_blocks_unknown_pp(tmp_path):
    prediction = _prediction_without(
        tmp_path, "decode_gpus_per_worker", "prefill_gpus_per_worker"
    )
    candidate = _native_candidate(prediction_path=prediction)
    assert candidate.deployment.pp_size is None
    gaps = parallel_identity_gaps(candidate.deployment)
    assert any("缺 PP" in gap for gap in gaps)

    candidate.verdict = "predicted_pass"
    candidate.comparable = True
    candidate.resource_feasible = None
    assert SimulationResult(status="computed", candidates=[candidate]).eligible() == []


def test_positive_prefill_is_not_used_as_decode_count(tmp_path):
    payload = json.loads(NATIVE_PRED.read_text(encoding="utf-8"))
    payload.pop("decode_gpus_per_worker", None)
    payload["prefill_gpus_per_worker"] = 8
    prediction = tmp_path / "prediction_prefill_only.json"
    prediction.write_text(json.dumps(payload), encoding="utf-8")

    candidate = _native_candidate(prediction_path=prediction)
    # 只有 prefill 卡数不能证明 decode 的并行身份
    assert candidate.deployment.pp_size is None
    gaps = parallel_identity_gaps(candidate.deployment)
    assert any("缺 PP" in gap for gap in gaps)
    assert not any("PP1 由" in note for note in candidate.notes)


def test_zero_prefill_is_not_used_as_gpu_count(tmp_path):
    prediction = _prediction_without(tmp_path, "decode_gpus_per_worker")
    candidate = _native_candidate(prediction_path=prediction)
    assert candidate.deployment.gpu_count != 0
    assert candidate.deployment.gpu_count == 8  # gpu_hours 回退
    assert not any("GPU 总数 0" in note for note in candidate.notes)
    assert any("缺 PP" in gap for gap in parallel_identity_gaps(candidate.deployment))


# ---------------------------------------------------------------------------
# U1.2 CLI 机箱
# ---------------------------------------------------------------------------


class _FakeOutcome:
    stages: list = []
    proposal_path = technical_path = json_path = None

    def __init__(self, out_dir: Path):
        self.out_dir = out_dir


@pytest.mark.parametrize(
    "extra, expected_server",
    [
        ([], None),
        (["--server", "hgx-h100-8gpu"], "hgx-h100-8gpu"),
    ],
)
def test_cli_server_defaults_to_auto_match(monkeypatch, tmp_path, extra, expected_server):
    captured = {}

    def fake_run_demo(inputs, *, out_dir, demo_note=None, scenario_id=None):
        captured["inputs"] = inputs
        return _FakeOutcome(Path(out_dir))

    monkeypatch.setattr(demo_cli, "run_demo", fake_run_demo)
    monkeypatch.setattr(demo_cli, "summary_lines", lambda outcome: [])
    code = demo_cli.main(
        ["--hardware", "nvidia-h200-sxm-141gb", *extra, "--out", str(tmp_path / "run")]
    )
    assert code == 0
    assert captured["inputs"].server_id == expected_server

    request = build_plan_request(captured["inputs"])
    if expected_server is None:
        assert request.server_id == "hgx-h200-8gpu"
    else:
        # 显式传入的机箱必须原样保留，不能被自动匹配掩盖
        assert request.server_id == "hgx-h100-8gpu"


# ---------------------------------------------------------------------------
# U1.3 ctx_tokens 工作点口径
# ---------------------------------------------------------------------------


def test_ctx_tokens_workpoint_note_distinguishes_comparison_from_alignment():
    tool = SimulationToolIdentity(backend="vllm", backend_version="0.24.0")
    candidates = parse_sweeper_csv(SWEEPER, tool=tool, serving_mode="agg")
    target = SimulationTarget(
        model_name="Qwen/Qwen3-32B",
        model_precision="bf16",
        system="h100_sxm",
        input_len=8192,
        output_len=256,
        concurrency=32,
        backend="vllm",
        backend_version="0.24.0",
    )
    picked = {}
    for candidate in candidates:
        if candidate.workload.concurrency != 32:
            continue
        if candidate.workload.ctx_tokens in (8192, 16384):
            check_comparability(candidate, target)
            picked[candidate.workload.ctx_tokens] = candidate

    assert set(picked) == {8192, 16384}
    notes_16k = "；".join(picked[16384].notes)
    notes_8k = "；".join(picked[8192].notes)
    assert "ctx_tokens=16384" in notes_16k
    assert "不是同一工作点" in notes_16k or "同一工作点" in notes_16k
    assert "ctx_tokens=16384" not in notes_8k


def test_report_shows_identity_derivation(tmp_path):
    from llm_capacity_pricing.app.scenarios import get_scenario

    scenario = get_scenario("relaxed")
    outcome = run_demo(
        DemoInputs(**scenario.inputs),
        out_dir=tmp_path / "relaxed",
        write_files=False,
    )
    assert "并行身份与工作点说明" in outcome.technical_md
    assert "DP1 由" in outcome.technical_md
    native = next(
        c for c in outcome.result.simulation.candidates
        if c.candidate_id.startswith("sim-native")
    )
    assert "tp8pp1dp1" in native.candidate_id
