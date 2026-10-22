"""AC0：归档脚本、加载器与元数据一致性。"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import yaml

from llm_capacity_pricing.app import offline_archives as oa
from llm_capacity_pricing.app.archive_integrity import (
    verify_files,
    verify_predict_metadata,
)
from scripts import archive_simulation_evidence as archive
from scripts.build_performance_coverage import read_archive_evidence


def _write_pair(run: Path, *, tp: int = 8, pipeline: int = 1, model: str = "Qwen/Qwen3-32B") -> None:
    (run / "predict" / "out_1").mkdir(parents=True, exist_ok=True)
    cfg = {
        "engine": {
            "backend": "vllm",
            "backend_version": "0.24.0",
            "hardware": "h100_sxm",
            "mode": "aggregated",
            "model": model,
            "workers": {
                "aggregated": {
                    "parallelism": {
                        "pipeline": pipeline,
                        "replicas": 1,
                        "tensor": tp,
                    }
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
        yaml.safe_dump(cfg), encoding="utf-8"
    )
    (run / "predict" / "out_1" / "prediction.json").write_text(
        json.dumps(
            {
                "p99_ttft_ms": 1000.0,
                "p99_tpot_ms": 20.0,
                "completed_requests": 500,
                "decode_gpus_per_worker": tp * pipeline,
            }
        ),
        encoding="utf-8",
    )


def _manifest(run: Path, *, tp: int = 8, pp: int = 1) -> dict:
    files = {
        "predict/predict_1.yaml": run / "predict" / "predict_1.yaml",
        "predict/out_1/prediction.json": run / "predict" / "out_1" / "prediction.json",
    }
    return {
        "archive_version": "ab-1",
        "run_id": run.name,
        "spec": {
            "model": "Qwen/Qwen3-32B",
            "system": "h100_sxm",
            "backend": "vllm",
            "backend_version": "0.24.0",
            "precision": "bf16",
            "isl": 8192,
            "osl": 256,
            "concurrency": 32,
        },
        "workpoint": {
            "model": "Qwen/Qwen3-32B",
            "system": "h100_sxm",
            "precision": "bf16",
            "input_len": 8192,
            "output_len": 256,
            "concurrency": 32,
            "tp": tp,
            "pp": pp,
            "dp": 1,
        },
        "errors": [],
        "run_errors": [],
        "candidate_errors": {},
        "failed_predicts": {},
        "inputs": {
            "predict/predict_1.yaml": hashlib.sha256(
                files["predict/predict_1.yaml"].read_bytes()
            ).hexdigest()
        },
        "outputs": {
            "predict/out_1/prediction.json": hashlib.sha256(
                files["predict/out_1/prediction.json"].read_bytes()
            ).hexdigest()
        },
    }


def test_metadata_tp_mismatch_is_rejected(tmp_path, monkeypatch):
    run = tmp_path / "archives" / "tp-mismatch"
    _write_pair(run, tp=4)
    manifest = _manifest(run, tp=8)
    (run / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    assert verify_predict_metadata(run, manifest, "predict/predict_1.yaml")
    monkeypatch.setattr(oa, "ARCHIVE_ROOT", tmp_path / "archives")
    monkeypatch.setattr(oa, "ROOT", tmp_path)
    assert oa.archive_entries() == []
    blocked_runs: list[dict] = []
    blocked_candidates: list[dict] = []
    evidence = read_archive_evidence(
        tmp_path / "archives", [], [], blocked_runs, blocked_candidates
    )
    assert evidence == []
    assert blocked_candidates and "元数据不一致" in blocked_candidates[0]["error"]


def test_symlink_archive_file_is_rejected(tmp_path, monkeypatch):
    run = tmp_path / "archives" / "symlink"
    _write_pair(run)
    outside = tmp_path / "outside.yaml"
    outside.write_bytes((run / "predict" / "predict_1.yaml").read_bytes())
    (run / "predict" / "predict_1.yaml").unlink()
    (run / "predict" / "predict_1.yaml").symlink_to(outside)
    manifest = _manifest(run)
    (run / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    assert verify_files(
        run, manifest, ["predict/predict_1.yaml", "predict/out_1/prediction.json"]
    )
    monkeypatch.setattr(oa, "ARCHIVE_ROOT", tmp_path / "archives")
    monkeypatch.setattr(oa, "ROOT", tmp_path)
    assert oa.archive_entries() == []


def test_workdir_archive_registers_root_exp_config(tmp_path, monkeypatch):
    monkeypatch.setattr(archive, "ROOT", tmp_path)
    work = tmp_path / "work"
    run = work / "runs" / "run_x"
    _write_pair(run)
    (run / "search" / "x" / "auto").mkdir(parents=True)
    (run / "search" / "x" / "auto" / "exp_config.yaml").write_text(
        "database_mode: SILICON\n", encoding="utf-8"
    )
    (run / "search" / "x" / "auto" / "pareto.csv").write_text(
        "model,isl\nQwen/Qwen3-32B,8192\n", encoding="utf-8"
    )
    (run / "manifest.json").write_text(
        json.dumps(
            {
                "run_id": "run_x",
                "tool_version": "0.12.1",
                "spec": {
                    "model": "Qwen/Qwen3-32B",
                    "precision": "bf16",
                    "system": "h100_sxm",
                    "isl": 8192,
                    "osl": 256,
                    "concurrency": 32,
                    "backend": "vllm",
                    "backend_version": "0.24.0",
                    "database_mode": "SILICON",
                },
                "errors": [],
                "run_errors": [],
                "candidate_errors": {},
            }
        ),
        encoding="utf-8",
    )
    args = argparse.Namespace(
        work_dir=str(work),
        archive_root="archives",
        name="ac-test",
        overwrite=False,
        tool_version="0.12.1",
    )
    target = archive.archive_work_dir(args)
    assert (target / "exp_config.yaml").is_file()
    manifest = json.loads((target / "manifest.json").read_text(encoding="utf-8"))
    assert "exp_config.yaml" in manifest["inputs"]
    from llm_capacity_pricing.app.archive_integrity import verify_manifest_hashes

    assert verify_manifest_hashes(target, manifest) == []


def test_symlinked_parent_directory_is_rejected(tmp_path):
    run = tmp_path / "archives" / "parent-symlink"
    _write_pair(run)
    outside = tmp_path / "outside"
    outside.mkdir()
    import shutil

    shutil.move(str(run / "predict"), str(outside / "predict"))
    (run / "predict").symlink_to(outside / "predict", target_is_directory=True)
    manifest = _manifest(run)
    reasons = verify_files(
        run, manifest, ["predict/predict_1.yaml", "predict/out_1/prediction.json"]
    )
    assert any("符号链接" in reason for reason in reasons)
