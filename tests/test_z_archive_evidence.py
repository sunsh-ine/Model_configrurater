"""Z 阶段证据归档脚本的数据边界。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from scripts import archive_simulation_evidence as archive


def _args(work: Path, *, name: str = "z-test") -> argparse.Namespace:
    return argparse.Namespace(
        work_dir=str(work),
        archive_root="archives",
        name=name,
        overwrite=False,
        tool_version="0.12.1",
    )


def test_workdir_archive_only_copies_performance_artifacts(tmp_path, monkeypatch):
    monkeypatch.setattr(archive, "ROOT", tmp_path)
    work = tmp_path / "work"
    run = work / "runs" / "run_x"
    (run / "predict" / "out_1").mkdir(parents=True)
    (run / "search" / "auto").mkdir(parents=True)
    (run / "predict" / "predict_1.yaml").write_text(
        "engine: {model: Qwen/Qwen3-32B}\n", encoding="utf-8"
    )
    (run / "predict" / "out_1" / "prediction.json").write_text(
        "{}", encoding="utf-8"
    )
    (run / "search" / "auto" / "pareto.csv").write_text(
        "model,isl\nQwen/Qwen3-32B,8192\n", encoding="utf-8"
    )
    (run / "search" / "auto" / "per_ops_source.json").write_text(
        "{}", encoding="utf-8"
    )
    # 非性能文件：即使后缀是 json/yaml，也不能进入公开仓库归档
    (run / "result.json").write_text('{"unit_price": "123"}', encoding="utf-8")
    (run / "cost.yaml").write_text("unit_price: 123\n", encoding="utf-8")
    (run / "report.md").write_text("price 123\n", encoding="utf-8")
    (run / "manifest.json").write_text(
        json.dumps(
            {
                "run_id": "run_x",
                "tool_version": "0.12.1",
                "spec": {"precision": "bf16", "database_mode": "SILICON"},
                "errors": [],
                "inputs": {},
                "outputs": {"result.json": "deadbeef"},
            }
        ),
        encoding="utf-8",
    )

    target = archive.archive_work_dir(_args(work))
    files = {
        str(path.relative_to(target))
        for path in target.rglob("*")
        if path.is_file()
    }
    assert "predict/predict_1.yaml" in files
    assert "predict/out_1/prediction.json" in files
    assert "search/auto/pareto.csv" in files
    assert "search/auto/per_ops_source.json" in files
    assert "result.json" not in files
    assert "cost.yaml" not in files
    assert "report.md" not in files

    manifest = json.loads((target / "manifest.json").read_text(encoding="utf-8"))
    assert "result.json" not in manifest["outputs"]
    assert "predict/out_1/prediction.json" in manifest["outputs"]
    assert "search/auto/pareto.csv" in manifest["outputs"]


def test_performance_allowlist_rejects_reports_and_prices():
    assert archive._is_performance_artifact(Path("predict/predict_1.yaml"))
    assert archive._is_performance_artifact(Path("predict/out_1/prediction.json"))
    assert archive._is_performance_artifact(Path("search/x/pareto.csv"))
    assert archive._is_performance_artifact(Path("search/x/exp_config.yaml"))
    assert archive._is_performance_artifact(Path("search/x/per_ops_source.json"))
    assert not archive._is_performance_artifact(Path("result.json"))
    assert not archive._is_performance_artifact(Path("cost.yaml"))
    assert not archive._is_performance_artifact(Path("deployment_proposal.md"))
    assert not archive._is_performance_artifact(Path("search/x/top1/sflow.yaml"))
    assert not archive._is_performance_artifact(Path("search/x/notes.json"))


def test_archive_target_rejects_traversal(tmp_path, monkeypatch):
    monkeypatch.setattr(archive, "ROOT", tmp_path)
    (tmp_path / "archives").mkdir()
    assert archive.archive_target("archives", "z-test") == (
        tmp_path / "archives" / "z-test"
    ).resolve()
    for bad_name in ("../escape", "..", ".", "a/b", ""):
        with pytest.raises(SystemExit):
            archive.archive_target("archives", bad_name)
    with pytest.raises(SystemExit):
        archive.archive_target("../outside", "z-test")
    with pytest.raises(SystemExit):
        archive.archive_target(".", "z-test")


def test_archive_target_rejects_symlink(tmp_path, monkeypatch):
    monkeypatch.setattr(archive, "ROOT", tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / "archives").mkdir()
    (tmp_path / "archives" / "z-test").symlink_to(outside, target_is_directory=True)
    with pytest.raises(SystemExit):
        archive.archive_target("archives", "z-test")


def test_archive_target_rejects_internal_symlink(tmp_path, monkeypatch):
    monkeypatch.setattr(archive, "ROOT", tmp_path)
    archives = tmp_path / "archives"
    real = archives / "z-real"
    real.mkdir(parents=True)
    (real / "keep.txt").write_text("keep", encoding="utf-8")
    (archives / "z-link").symlink_to(real, target_is_directory=True)
    with pytest.raises(SystemExit):
        archive.archive_target("archives", "z-link")
    assert (real / "keep.txt").exists()
