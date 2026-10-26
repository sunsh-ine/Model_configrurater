"""Z1：性能预测证据覆盖矩阵。"""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from scripts.build_performance_coverage import (
    LEVEL_KEYS,
    build,
    native_evidence,
    read_archive_evidence,
    render_markdown,
    workpoint_key,
)

ROOT = Path(__file__).resolve().parents[1]


def _native(doc: dict, concurrency: int) -> list[dict]:
    return [
        item
        for item in doc["evidence"]
        if item["source_kind"] == "simulated_native"
        and item["workload"].get("concurrency") == concurrency
    ]


def _group(doc: dict, concurrency: int) -> list[dict]:
    return [
        group
        for group in doc["workpoints"]
        if group["workload"].get("concurrency") == concurrency
        and group["identity"].get("tp_size") == 8
        and group["identity"].get("precision") == "bf16"
    ]


def test_catalog_static_matrix_only_contains_supported_hardware():
    doc = build()
    supported = {
        item["hardware_id"]
        for item in doc["catalog"]["hardware"]
        if item["supported_in_phase2"]
    }
    assert supported == {"nvidia-h100-sxm-80gb", "nvidia-h200-sxm-141gb"}
    assert {row["hardware_id"] for row in doc["static_matrix"]} == supported
    assert {row["model_id"] for row in doc["static_matrix"]} == {
        "qwen3-8b",
        "qwen3-32b",
    }


def test_native_p99_workpoints_are_exact_c8_c32_c64():
    doc = build()
    expected = {
        8: ("727.2969240000001", "11.230072717647314"),
        32: ("5201.827252000001", "31.45861707450993"),
        64: ("11211.532603000005", "56.43859718823514"),
    }
    for concurrency, (ttft, tpot) in expected.items():
        evidence = [
            item
            for item in _native(doc, concurrency)
            if item["identity"].get("tp_size") == 8
            and item["identity"].get("hardware_id") == "nvidia-h100-sxm-80gb"
            and item["workload"].get("input_len") == 8192
            and item["workload"].get("output_len") == 256
        ]
        assert evidence, f"C{concurrency} 缺 TP8 原生证据"
        assert {item["metrics"]["ttft_p99_ms"] for item in evidence} == {ttft}
        assert {item["metrics"]["tpot_p99_ms"] for item in evidence} == {tpot}
        assert all(item["sla_usable"] for item in evidence)


def test_scan_only_evidence_never_claims_p99_or_sla():
    doc = build()
    scans = [item for item in doc["evidence"] if item["source_kind"] == "simulated_scan"]
    assert scans
    for item in scans:
        assert item["coverage_level"] == 2
        assert item["coverage_level_key"] == LEVEL_KEYS[2]
        assert item["metrics"]["ttft_p99_ms"] is None
        assert item["metrics"]["tpot_p99_ms"] is None
        assert item["sla_usable"] is False


def test_public_and_internal_records_are_reference_only():
    doc = build()
    references = [item for item in doc["evidence"] if item.get("reference_only")]
    assert references
    for item in references:
        assert item["coverage_level"] is None
        assert item["sla_usable"] is False
        assert item["coverage_level_key"] in {"public_reference", "internal_reference"}
    kinds = {item["source_kind"] for item in references}
    assert kinds == {"measured_public", "measured_internal"}


def test_no_price_fields_leak_into_coverage_data():
    doc = build()
    serialized = json.dumps(doc, ensure_ascii=False)
    for forbidden in (
        "legacy_total_revenue_10k_per_gpu_year",
        "legacy_cost_10k_per_gpu_year",
        "unit_price",
        "annual_total_cost",
        "capex_one_time",
        "quote_publishable",
    ):
        assert forbidden not in serialized


def test_archived_manifests_match_their_files():
    archives = ROOT / "data/simulation/archives"
    manifests = sorted(archives.glob("*/manifest.json"))
    assert manifests, "Z 阶段应至少有一个实时归档"
    for manifest_path in manifests:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        run_dir = manifest_path.parent
        for key in ("tool_version", "spec", "errors"):
            assert key in manifest
        assert not manifest["errors"]
        assert manifest["spec"].get("precision") == "bf16"
        assert manifest["spec"].get("database_mode") == "SILICON"
        for relative, digest in (manifest.get("outputs") or {}).items():
            path = run_dir / relative
            assert path.is_file(), f"{manifest_path}: 缺 {relative}"
            import hashlib

            assert hashlib.sha256(path.read_bytes()).hexdigest() == digest


def test_markdown_is_generated_from_same_data(tmp_path: Path):
    doc = build()
    md = render_markdown(doc)
    assert "预测不等于实测" in md
    assert "simulated_native_p99" in md
    assert "ISL8192/OSL256/C32" in md
    assert "不同工作点之间**不做插值**" in md
    out = tmp_path / "coverage.md"
    out.write_text(md, encoding="utf-8")
    assert out.stat().st_size > 1000


def _fake_native_files(tmp_path: Path, *, completed: int = 500):
    config = {
        "engine": {
            "backend": "vllm",
            "backend_version": "0.24.0",
            "hardware": "h100_sxm",
            "mode": "aggregated",
            "model": "Qwen/Qwen3-32B",
            "workers": {"aggregated": {"parallelism": {"pipeline": 1, "replicas": 1, "tensor": 8}}},
        },
        "traffic": {
            "load": {"concurrency": 8, "type": "concurrency"},
            "source": {"input_tokens": 8192, "output_tokens": 256, "type": "synthetic"},
            "stop": {"requests": 500},
        },
    }
    input_path = tmp_path / "predict_1.yaml"
    prediction_path = tmp_path / "prediction.json"
    input_path.write_text(yaml.safe_dump(config), encoding="utf-8")
    prediction_path.write_text(
        json.dumps(
            {
                "p99_ttft_ms": 700.0,
                "p99_tpot_ms": 10.0,
                "output_throughput_tok_s": 600.0,
                "completed_requests": completed,
                "decode_gpus_per_worker": 8,
            }
        ),
        encoding="utf-8",
    )
    return input_path, prediction_path


def test_failed_archive_run_is_blocked_not_evidence(tmp_path: Path):
    run = tmp_path / "bad-run"
    (run / "predict" / "out_1").mkdir(parents=True)
    input_path, prediction_path = _fake_native_files(tmp_path)
    (run / "predict" / "predict_1.yaml").write_bytes(input_path.read_bytes())
    (run / "predict" / "out_1" / "prediction.json").write_bytes(
        prediction_path.read_bytes()
    )
    (run / "manifest.json").write_text(
        json.dumps(
            {
                "run_id": "bad-run",
                "spec": {"precision": "bf16", "database_mode": "SILICON"},
                "errors": ["sweeper failed"],
                "inputs": {},
                "outputs": {},
            }
        ),
        encoding="utf-8",
    )
    blocked: list[dict] = []
    evidence = read_archive_evidence(tmp_path, [], [], blocked)
    assert evidence == []
    assert [item["run_id"] for item in blocked] == ["bad-run"]


def test_undersampled_native_p99_is_not_sla_usable(tmp_path: Path):
    input_path, prediction_path = _fake_native_files(tmp_path, completed=10)
    hardware = [
        {
            "hardware_id": "nvidia-h100-sxm-80gb",
            "product_name": "NVIDIA H100 SXM",
            "supported_in_phase2": True,
        }
    ]
    item = native_evidence(
        input_path=input_path,
        prediction_path=prediction_path,
        exp_config_path=None,
        run_id="undersampled",
        tool_version="0.12.1",
        models=[],
        hardware=hardware,
        declared_precision="bf16",
    )
    assert item is not None
    assert item["metrics"]["completed_requests"] == 10
    assert item["sla_usable"] is False
    assert item["coverage_level"] is None
    assert any("样本不足" in reason for reason in item["sla_blockers"])


def test_workpoint_key_ignores_sample_window_but_keeps_ctx():
    identity = {
        "model_name": "Qwen/Qwen3-32B",
        "model_id": "qwen3-32b",
        "precision": "bf16",
        "hardware_id": "nvidia-h100-sxm-80gb",
        "framework": "vllm",
        "framework_version": "0.24.0",
        "gpu_count": 8,
        "tp_size": 8,
        "pp_size": 1,
        "dp_size": 1,
    }
    base = {"input_len": 8192, "output_len": 256, "concurrency": 32, "ctx_tokens": None}
    first = workpoint_key(identity, {**base, "requests": 100, "dataset": "synthetic"})
    second = workpoint_key(identity, {**base, "requests": 500, "dataset": "synthetic"})
    assert first == second
    other_ctx = workpoint_key(identity, {**base, "ctx_tokens": 8192})
    assert other_ctx != first
    other_dataset = workpoint_key(
        identity, {**base, "requests": 500, "dataset": "random"}
    )
    assert other_dataset != first


def test_tampered_archive_is_blocked_in_coverage(tmp_path: Path):
    run = tmp_path / "tampered-run"
    (run / "predict" / "out_1").mkdir(parents=True)
    input_path, prediction_path = _fake_native_files(tmp_path)
    input_copy = run / "predict" / "predict_1.yaml"
    pred_copy = run / "predict" / "out_1" / "prediction.json"
    input_copy.write_bytes(input_path.read_bytes())
    pred_copy.write_bytes(prediction_path.read_bytes())
    import hashlib

    manifest = {
        "run_id": "tampered-run",
        "tool_version": "0.12.1",
        "spec": {"precision": "bf16", "database_mode": "SILICON"},
        "errors": [],
        "inputs": {
            "predict/predict_1.yaml": hashlib.sha256(input_copy.read_bytes()).hexdigest()
        },
        "outputs": {
            "predict/out_1/prediction.json": hashlib.sha256(
                pred_copy.read_bytes()
            ).hexdigest()
        },
    }
    (run / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    pred_copy.write_text(
        json.dumps({"p99_ttft_ms": 1.0, "p99_tpot_ms": 1.0, "completed_requests": 500}),
        encoding="utf-8",
    )
    blocked: list[dict] = []
    blocked_candidates: list[dict] = []
    evidence = read_archive_evidence(tmp_path, [], [], blocked, blocked_candidates)
    assert evidence == []
    assert blocked_candidates
    assert "完整性" in str(blocked_candidates[0]["error"])


def test_tampered_exp_config_blocks_native_evidence(tmp_path: Path):
    import hashlib

    run = tmp_path / "exp-run"
    (run / "predict" / "out_1").mkdir(parents=True)
    input_path, prediction_path = _fake_native_files(tmp_path)
    (run / "predict" / "predict_1.yaml").write_bytes(input_path.read_bytes())
    (run / "predict" / "out_1" / "prediction.json").write_bytes(
        prediction_path.read_bytes()
    )
    exp = run / "exp_config.yaml"
    exp.write_text("database_mode: SILICON\n", encoding="utf-8")
    manifest = {
        "run_id": "exp-run",
        "tool_version": "0.12.1",
        "spec": {"precision": "bf16", "database_mode": "SILICON"},
        "errors": [],
        "run_errors": [],
        "candidate_errors": {},
        "inputs": {
            "predict/predict_1.yaml": hashlib.sha256(
                (run / "predict" / "predict_1.yaml").read_bytes()
            ).hexdigest(),
            "exp_config.yaml": hashlib.sha256(exp.read_bytes()).hexdigest(),
        },
        "outputs": {
            "predict/out_1/prediction.json": hashlib.sha256(
                (run / "predict" / "out_1" / "prediction.json").read_bytes()
            ).hexdigest()
        },
    }
    (run / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    exp.write_text("database_mode: HYBRID\n", encoding="utf-8")
    blocked: list[dict] = []
    candidates: list[dict] = []
    evidence = read_archive_evidence(tmp_path, [], [], blocked, candidates)
    assert evidence == []
    assert candidates and "exp_config" in candidates[0]["error"]


def test_missing_registered_exp_config_blocks_native_evidence(tmp_path: Path):
    import hashlib

    run = tmp_path / "missing-exp"
    (run / "predict" / "out_1").mkdir(parents=True)
    input_path, prediction_path = _fake_native_files(tmp_path)
    (run / "predict" / "predict_1.yaml").write_bytes(input_path.read_bytes())
    (run / "predict" / "out_1" / "prediction.json").write_bytes(
        prediction_path.read_bytes()
    )
    exp = run / "exp_config.yaml"
    exp.write_text("database_mode: SILICON\n", encoding="utf-8")
    manifest = {
        "run_id": "missing-exp",
        "tool_version": "0.12.1",
        "spec": {"precision": "bf16", "database_mode": "SILICON"},
        "errors": [],
        "run_errors": [],
        "candidate_errors": {},
        "inputs": {
            "predict/predict_1.yaml": hashlib.sha256(
                (run / "predict" / "predict_1.yaml").read_bytes()
            ).hexdigest(),
            "exp_config.yaml": hashlib.sha256(exp.read_bytes()).hexdigest(),
        },
        "outputs": {
            "predict/out_1/prediction.json": hashlib.sha256(
                (run / "predict" / "out_1" / "prediction.json").read_bytes()
            ).hexdigest()
        },
    }
    (run / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    exp.unlink()
    blocked: list[dict] = []
    candidates: list[dict] = []
    evidence = read_archive_evidence(tmp_path, [], [], blocked, candidates)
    assert evidence == []
    assert candidates and "exp_config.yaml" in candidates[0]["error"]


def test_registered_missing_predict_input_is_reported(tmp_path: Path):
    import hashlib

    run = tmp_path / "missing-input"
    (run / "predict" / "out_1").mkdir(parents=True)
    input_path, prediction_path = _fake_native_files(tmp_path)
    (run / "predict" / "predict_1.yaml").write_bytes(input_path.read_bytes())
    (run / "predict" / "out_1" / "prediction.json").write_bytes(
        prediction_path.read_bytes()
    )
    manifest = {
        "run_id": "missing-input",
        "spec": {"precision": "bf16", "database_mode": "SILICON"},
        "errors": [],
        "run_errors": [],
        "candidate_errors": {},
        "inputs": {
            "predict/predict_1.yaml": hashlib.sha256(
                (run / "predict" / "predict_1.yaml").read_bytes()
            ).hexdigest(),
            "predict/predict_2.yaml": "deadbeef",
        },
        "outputs": {
            "predict/out_1/prediction.json": hashlib.sha256(
                (run / "predict" / "out_1" / "prediction.json").read_bytes()
            ).hexdigest()
        },
    }
    (run / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    blocked: list[dict] = []
    candidates: list[dict] = []
    evidence = read_archive_evidence(tmp_path, [], [], blocked, candidates)
    assert len(evidence) == 1
    assert any(item["predict"] == "predict/predict_2.yaml" for item in candidates)


def test_unregistered_exp_config_is_not_read(tmp_path: Path):
    import hashlib

    run = tmp_path / "unregistered-exp"
    (run / "predict" / "out_1").mkdir(parents=True)
    input_path, prediction_path = _fake_native_files(tmp_path)
    (run / "predict" / "predict_1.yaml").write_bytes(input_path.read_bytes())
    (run / "predict" / "out_1" / "prediction.json").write_bytes(
        prediction_path.read_bytes()
    )
    # 文件存在但 manifest 未登记：不得被读取为证据身份
    (run / "exp_config.yaml").write_text("database_mode: HYBRID\n", encoding="utf-8")
    manifest = {
        "run_id": "unregistered-exp",
        "spec": {"precision": "bf16", "database_mode": "SILICON"},
        "errors": [],
        "run_errors": [],
        "candidate_errors": {},
        "inputs": {
            "predict/predict_1.yaml": hashlib.sha256(
                (run / "predict" / "predict_1.yaml").read_bytes()
            ).hexdigest()
        },
        "outputs": {
            "predict/out_1/prediction.json": hashlib.sha256(
                (run / "predict" / "out_1" / "prediction.json").read_bytes()
            ).hexdigest()
        },
    }
    (run / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    blocked: list[dict] = []
    candidates: list[dict] = []
    evidence = read_archive_evidence(tmp_path, [], [], blocked, candidates)
    assert len(evidence) == 1
    assert evidence[0]["database_mode"] == "SILICON"
    assert all(
        not item["path"].endswith("exp_config.yaml")
        for item in evidence[0]["supporting_artifacts"]
    )
