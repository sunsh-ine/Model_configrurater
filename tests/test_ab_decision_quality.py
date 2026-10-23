"""AB3/AB4：SLA 裕量、敏感性、资源效率与搜索审计。"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from llm_capacity_pricing.app.demo_service import DemoInputs, run_demo


def _run(tmp_path: Path, name: str, **overrides):
    values = {
        "concurrency": 32,
        "ttft_p99_ms_max": Decimal("6000"),
        "tpot_p99_ms_max": Decimal("40"),
        "request_id": f"ab-{name}",
    }
    values.update(overrides)
    return run_demo(DemoInputs(**values), out_dir=tmp_path / name)


def _by_tp(result):
    # AB 基线只比较 DP1；AD 的 DP2 结果由 test_ad_parallel 覆盖
    return {
        c.deployment.tp_size: c
        for c in result.simulation.candidates
        if c.candidate_id.startswith("sim-native") and c.deployment.dp_size == 1
    }


def test_sla_margin_and_sensitivity_for_tp8(tmp_path):
    outcome = _run(tmp_path, "c32")
    tp8 = _by_tp(outcome.result)[8]
    assert tp8.sla_margin_ttft_ms == Decimal("6000") - Decimal("5201.827252000001")
    assert tp8.sla_margin_tpot_ms == Decimal("40") - Decimal("31.45861707450993")
    assert tp8.sla_margin_ttft_ratio is not None
    assert tp8.sla_margin_ttft_ratio > 0
    assert tp8.sensitive_10pct is False
    assert tp8.sensitive_20pct is True
    assert tp8.evidence_status == "boundary_sensitive"
    assert tp8.verdict == "predicted_pass"  # 证据状态不改变 SLA 结论


def test_failed_candidate_has_negative_margin(tmp_path):
    tp4 = _by_tp(_run(tmp_path, "c32") .result)[4]
    assert tp4.sla_margin_ttft_ms < 0
    assert tp4.sla_margin_tpot_ms < 0
    assert tp4.evidence_status == "predicted_fail"


def test_min_gpu_for_sla_and_recommendation_can_differ(tmp_path):
    result = _run(tmp_path, "c8", concurrency=8).result
    by_tp = _by_tp(result)
    assert by_tp[4].verdict == "predicted_pass"
    assert by_tp[8].verdict == "predicted_pass"
    # 吞吐优先仍选 TP8，但满足 SLA 的最小配置是 TP4
    assert result.simulation.recommended("throughput").deployment.tp_size == 8
    assert result.simulation.min_gpu_for_sla().deployment.tp_size == 4
    assert by_tp[4].metrics.sim_tokens_per_gpu is not None
    assert by_tp[8].metrics.sim_tokens_per_gpu is not None


def test_no_predicted_pass_has_no_min_gpu(tmp_path):
    result = _run(tmp_path, "c64", concurrency=64).result
    assert result.simulation.min_gpu_for_sla() is None
    assert result.simulation.recommended("throughput") is None
    assert result.simulation.best_effort() is not None


def test_report_renders_decision_quality_and_audit(tmp_path):
    outcome = _run(tmp_path, "report")
    text = outcome.technical_md
    for phrase in (
        "SLA 裕量",
        "预测敏感性",
        "证据状态",
        "资源效率",
        "满足 SLA 的最小配置",
        "候选搜索与推荐有效范围",
        "离线归档选择",
    ):
        assert phrase in text, phrase
    audit = outcome.result.simulation.search_audit
    assert audit is not None
    # C32/TP4 DP1、TP4 DP2、TP8 DP1 三份归档
    assert audit.discovered_deployments == 3
    assert audit.replayed_deployments == 3
    assert audit.effective_scope == "offline_archives"


def test_insufficient_prediction_has_no_sensitivity_claim(tmp_path):
    result = _run(tmp_path, "c16", concurrency=16).result
    assert not [
        c for c in result.simulation.candidates if c.candidate_id.startswith("sim-native")
    ]
    assert result.simulation.min_gpu_for_sla() is None


def test_failed_candidate_does_not_block_successful_archive(tmp_path, monkeypatch):
    """AB0：候选级失败只影响该候选，不能否定同运行的成功预测。"""
    import hashlib
    import json

    import yaml

    from llm_capacity_pricing.app import offline_archives as oa
    from scripts.build_performance_coverage import read_archive_evidence

    root = tmp_path / "archives"
    run = root / "mixed-run"
    (run / "predict" / "out_1").mkdir(parents=True)
    (run / "predict" / "out_2").mkdir(parents=True)

    def write_pair(index: int, tp: int, *, prediction: bool) -> None:
        cfg = {
            "engine": {
                "backend": "vllm",
                "backend_version": "0.24.0",
                "hardware": "h100_sxm",
                "mode": "aggregated",
                "model": "Qwen/Qwen3-32B",
                "workers": {"aggregated": {"parallelism": {"pipeline": 1, "replicas": 1, "tensor": tp}}},
            },
            "traffic": {
                "load": {"concurrency": 32, "type": "concurrency"},
                "source": {"input_tokens": 8192, "output_tokens": 256, "type": "synthetic"},
                "stop": {"requests": 500},
            },
        }
        (run / "predict" / f"predict_{index}.yaml").write_text(
            yaml.safe_dump(cfg), encoding="utf-8"
        )
        if prediction:
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

    write_pair(1, 8, prediction=True)
    write_pair(2, 4, prediction=False)
    # 失败候选留下一个已登记但随后损坏/缺失的输出，验证不会拖累成功候选
    failed_output = run / "predict" / "out_2" / "prediction.json"
    failed_output.write_text("{\"partial\": true}", encoding="utf-8")
    failed_digest = hashlib.sha256(failed_output.read_bytes()).hexdigest()
    failed_output.unlink()
    manifest = {
        "run_id": "mixed-run",
        "tool_version": "0.12.1",
        "spec": {"precision": "bf16", "database_mode": "SILICON"},
        "errors": ["sim-sweeper:tp4: TimeoutExpired"],
        "run_errors": [],
        "candidate_errors": {"predict/predict_2.yaml": "TimeoutExpired"},
        "inputs": {
            "predict/predict_1.yaml": hashlib.sha256(
                (run / "predict" / "predict_1.yaml").read_bytes()
            ).hexdigest(),
            "predict/predict_2.yaml": hashlib.sha256(
                (run / "predict" / "predict_2.yaml").read_bytes()
            ).hexdigest(),
        },
        "outputs": {
            "predict/out_1/prediction.json": hashlib.sha256(
                (run / "predict" / "out_1" / "prediction.json").read_bytes()
            ).hexdigest(),
            "predict/out_2/prediction.json": failed_digest,
        },
    }
    (run / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setattr(oa, "ARCHIVE_ROOT", root)
    monkeypatch.setattr(oa, "ROOT", tmp_path)
    entries = oa.archive_entries()
    assert [entry.tp_size for entry in entries] == [8]

    blocked: list[dict] = []
    candidates: list[dict] = []
    evidence = read_archive_evidence(root, [], [], blocked, candidates)
    assert len(evidence) == 1
    assert blocked == []
    assert candidates and candidates[0]["predict"] == "predict/predict_2.yaml"


def test_poc_is_a_standard_archive():
    from llm_capacity_pricing.app.offline_archives import archive_entries

    poc = [
        entry
        for entry in archive_entries()
        if entry.name.startswith("poc-qwen3-32b-h100-tp8-c32-bf16")
    ]
    assert len(poc) == 1
    assert poc[0].native_prediction.startswith("data/simulation/archives/")
    assert poc[0].exp_config is not None


def test_ab_archives_have_version_and_workpoint_metadata():
    import json
    from pathlib import Path

    root = Path("data/simulation/archives")
    manifests = sorted(root.glob("ab-live-tp*/manifest.json"))
    assert len(manifests) == 8
    for path in manifests:
        manifest = json.loads(path.read_text(encoding="utf-8"))
        assert manifest["archive_version"] == "ab-1"
        assert manifest["workpoint"]["model"] == "Qwen/Qwen3-32B"
        assert manifest["workpoint"]["input_len"] in (8192, 1024)
        assert manifest["predictions"]
        assert manifest["run_errors"] == []
        assert manifest["candidate_errors"] == {}


def test_ab2_covers_twelve_measured_workpoints():
    from llm_capacity_pricing.app.offline_archives import find_archives

    found = set()
    for isl, osl in ((8192, 256), (1024, 1024)):
        for tp in (4, 8):
            for concurrency in (8, 32, 64):
                entries = find_archives(
                    model_id="qwen3-32b",
                    hardware_id="nvidia-h100-sxm-80gb",
                    input_len=isl,
                    output_len=osl,
                    concurrency=concurrency,
                    precision="bf16",
                    framework="vllm",
                    framework_version="0.24.0",
                    gpu_limit=8,
                    tp_sizes=(tp,),
                )
                assert entries, (isl, osl, tp, concurrency)
                found.add((isl, osl, tp, concurrency))
    assert len(found) == 12


def test_unknown_sensitivity_is_not_reported_as_safe():
    from llm_capacity_pricing.reporting.plan_report import _sensitivity_text

    assert "未评估（预测不足）" in _sensitivity_text(None, 10)
    assert "越过阈值" in _sensitivity_text(True, 10)
    assert "仍不越过阈值" in _sensitivity_text(False, 10)


def test_tampered_sweep_csv_is_not_imported(tmp_path):
    import hashlib
    import json

    from scripts.build_performance_coverage import read_archive_evidence

    run = tmp_path / "archives" / "sweep-run"
    (run / "search" / "auto").mkdir(parents=True)
    csv_path = run / "search" / "auto" / "pareto.csv"
    csv_path.write_text("model,isl\nQwen/Qwen3-32B,8192\n", encoding="utf-8")
    digest = hashlib.sha256(csv_path.read_bytes()).hexdigest()
    out = run / "predict" / "out_1" / "prediction.json"
    out.parent.mkdir(parents=True)
    out.write_text("{}", encoding="utf-8")
    (run / "manifest.json").write_text(
        json.dumps(
            {
                "run_id": "sweep-run",
                "spec": {"precision": "bf16", "database_mode": "SILICON"},
                "errors": [],
                "run_errors": [],
                "candidate_errors": {},
                "inputs": {"search/auto/pareto.csv": digest},
                "outputs": {
                    "predict/out_1/prediction.json": hashlib.sha256(
                        out.read_bytes()
                    ).hexdigest()
                },
            }
        ),
        encoding="utf-8",
    )
    csv_path.write_text("model,isl\nQwen/Qwen3-32B,9999\n", encoding="utf-8")
    blocked_runs: list[dict] = []
    blocked_candidates: list[dict] = []
    evidence = read_archive_evidence(
        tmp_path / "archives", [], [], blocked_runs, blocked_candidates
    )
    assert evidence == []
    assert blocked_candidates
    assert "扫描器证据完整性" in blocked_candidates[0]["error"]
