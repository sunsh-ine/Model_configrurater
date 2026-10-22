#!/usr/bin/env python3
"""把 AISimulate 的实时证据归档到 ``data/simulation/archives/``。

两种输入形态：

1. ``--predict-config`` + ``--prediction``：单次原生回放（最小证据）；
2. ``--work-dir``：本系统自动搜索产生的工作目录（manifest + 搜索 + 回放）。

只复制**性能证据**（预测、输入、配置、Pareto 表），不复制任何价格、
成本或报告文件。目标目录必须不存在（或显式 ``--overwrite``）。

归档完成后，``scripts/build_performance_coverage.py`` 会自动把它纳入覆盖矩阵。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]

_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def archive_target(archive_root: str, name: str) -> Path:
    """校验归档根目录与名称，返回必须位于根目录下的直接子目录。"""
    if not _NAME_RE.fullmatch(name or ""):
        raise SystemExit(f"非法归档名：{name!r}")
    root = ROOT.resolve()
    base = (root / archive_root).resolve()
    if base == root or not base.is_relative_to(root):
        raise SystemExit(f"归档根目录必须位于仓库内：{archive_root}")
    unresolved = base / name
    if unresolved.is_symlink():
        raise SystemExit(f"拒绝覆盖符号链接目录：{unresolved}")
    target = unresolved.resolve()
    if target.parent != base:
        raise SystemExit(f"归档目录越界：{name!r}")
    if target.exists() and not target.is_dir():
        raise SystemExit(f"归档目标不是普通目录：{target}")
    return target


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )


def prepare_dir(path: Path, overwrite: bool) -> None:
    if path.exists():
        if path.is_symlink() or not path.is_dir():
            raise SystemExit(f"归档目标不是普通目录：{path}")
        if not overwrite:
            raise SystemExit(f"归档目录已存在：{path}（用 --overwrite 覆盖）")
        shutil.rmtree(path)
    path.mkdir(parents=True)


def copy_with_hash(source: Path, target: Path) -> dict[str, str]:
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    return {"path": str(target.relative_to(ROOT)), "sha256": sha256(target)}


def spec_from_predict(
    config: dict[str, Any], precision: str | None, database_mode: str | None
) -> dict[str, Any]:
    engine = config.get("engine") or {}
    traffic = config.get("traffic") or {}
    source = traffic.get("source") or {}
    load = traffic.get("load") or {}
    return {
        "model": engine.get("model"),
        "precision": precision,
        "system": engine.get("hardware"),
        "isl": source.get("input_tokens"),
        "osl": source.get("output_tokens"),
        "concurrency": load.get("concurrency"),
        "backend": engine.get("backend"),
        "backend_version": engine.get("backend_version"),
        "max_total_gpus": None,
        "database_mode": database_mode,
    }


def archive_predict(args: argparse.Namespace) -> Path:
    config_path = Path(args.predict_config)
    prediction_path = Path(args.prediction)
    if not config_path.exists() or not prediction_path.exists():
        raise SystemExit("predict 配置或 prediction.json 不存在")
    config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    prediction = json.loads(prediction_path.read_text(encoding="utf-8"))

    target = archive_target(args.archive_root, args.name)
    prepare_dir(target, args.overwrite)
    config_copy = copy_with_hash(config_path, target / "predict/predict_1.yaml")
    prediction_copy = copy_with_hash(
        prediction_path, target / "predict/out_1/prediction.json"
    )
    engine = config.get("engine") or {}
    identity_files = [config_copy, prediction_copy]
    if args.database_mode:
        exp_config = {
            "serving_mode": "agg",
            "database_mode": args.database_mode,
            "model_path": engine.get("model"),
            "system_name": engine.get("hardware"),
            "backend_name": engine.get("backend"),
            "backend_version": engine.get("backend_version"),
        }
        exp_path = target / "exp_config.yaml"
        exp_path.write_text(
            yaml.safe_dump(exp_config, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        identity_files.append(
            {"path": str(exp_path.relative_to(ROOT)), "sha256": sha256(exp_path)}
        )
    metrics = {
        key: prediction.get(key)
        for key in (
            "p99_ttft_ms",
            "p99_tpot_ms",
            "mean_ttft_ms",
            "mean_tpot_ms",
            "output_throughput_tok_s",
            "completed_requests",
            "decode_gpus_per_worker",
        )
    }
    manifest = {
        "archive_version": "ab-1",
        "tool": "aisimulate",
        "tool_version": args.tool_version,
        "run_id": args.name,
        "run_dir": str(target.relative_to(ROOT)),
        "work_root": str(target.relative_to(ROOT)),
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": args.source or "manual live replay",
        "spec": spec_from_predict(config, args.precision, args.database_mode),
        "workpoint": {
            "model": engine.get("model"),
            "system": engine.get("hardware"),
            "precision": args.precision,
            "input_len": (config.get("traffic") or {}).get("source", {}).get("input_tokens"),
            "output_len": (config.get("traffic") or {}).get("source", {}).get("output_tokens"),
            "concurrency": (config.get("traffic") or {}).get("load", {}).get("concurrency"),
            "tp": (config.get("engine") or {}).get("workers", {}).get("aggregated", {}).get("parallelism", {}).get("tensor"),
            "pp": (config.get("engine") or {}).get("workers", {}).get("aggregated", {}).get("parallelism", {}).get("pipeline"),
            "dp": (config.get("engine") or {}).get("workers", {}).get("aggregated", {}).get("parallelism", {}).get("replicas"),
            "requests": (config.get("traffic") or {}).get("stop", {}).get("requests"),
        },
        "predictions": [
            {
                "predict": "predict/predict_1.yaml",
                "prediction": "predict/out_1/prediction.json",
                "metrics": metrics,
            }
        ],
        "inputs": {
            str((target / "predict/predict_1.yaml").relative_to(target)): config_copy[
                "sha256"
            ],
            **(
                {
                    "exp_config.yaml": sha256(target / "exp_config.yaml")
                }
                if (target / "exp_config.yaml").exists()
                else {}
            ),
        },
        "outputs": {
            str(
                (target / "predict/out_1/prediction.json").relative_to(target)
            ): prediction_copy["sha256"]
        },
        "errors": [],
        "run_errors": [],
        "candidate_errors": {},
        "not_aligned": {},
        "not_replayed": {},
        "native_metrics": metrics,
        "sla_note": "P99 是否达标由本仓库的 SLA 规则判定；此处只保存预测值",
    }
    write_json(target / "manifest.json", manifest)
    write_json(
        target / "archive.json",
        {
            "archived_at": manifest["created_at"],
            "kind": "native_replay",
            "tool_version": args.tool_version,
            "files": identity_files,
        },
    )
    return target


def _is_performance_artifact(relative: Path) -> bool:
    """只允许归档性能证据；报告、成本、报价等文件一律不复制。"""
    name = relative.name
    parts = relative.parts
    if name in {"search_input.yaml", "exp_config.yaml"}:
        return True
    if name in {"pareto.csv", "best_config_topn.csv"}:
        return True
    if parts and parts[0] == "predict":
        if name == "prediction.json":
            return True
        if name.startswith("predict_") and relative.suffix.lower() in {".yaml", ".yml"}:
            return True
    if name == "per_ops_source.json" and "search" in parts:
        return True
    return False


def archive_work_dir(args: argparse.Namespace) -> Path:
    work = Path(args.work_dir)
    manifests = sorted(work.glob("runs/*/manifest.json"))
    if not manifests:
        raise SystemExit(f"工作目录里没有 runs/*/manifest.json：{work}")
    source_run = manifests[-1].parent
    source_manifest = json.loads(manifests[-1].read_text(encoding="utf-8"))

    target = archive_target(args.archive_root, args.name)
    prepare_dir(target, args.overwrite)
    copied: list[dict[str, str]] = []

    for path in sorted(source_run.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(source_run)
        if any(part.startswith(".") for part in relative.parts):
            continue
        if not _is_performance_artifact(relative):
            continue
        copied.append(copy_with_hash(path, target / relative))

    manifest = dict(source_manifest)
    manifest["archive_version"] = "ab-1"
    manifest["run_id"] = args.name
    manifest["run_dir"] = str(target.relative_to(ROOT))
    manifest["work_root"] = str(target.relative_to(ROOT))
    manifest["tool_version"] = (
        args.tool_version or source_manifest.get("tool_version")
    )
    manifest["source_run_id"] = source_manifest.get("run_id")
    manifest["archived_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    spec = manifest.get("spec") or {}
    manifest.setdefault(
        "workpoint",
        {
            "model": spec.get("model"),
            "system": spec.get("system"),
            "precision": spec.get("precision"),
            "input_len": spec.get("isl"),
            "output_len": spec.get("osl"),
            "concurrency": spec.get("concurrency"),
        },
    )
    predictions = []
    for prediction_path in sorted(target.glob("predict/out_*/prediction.json")):
        index = prediction_path.parent.name.rsplit("_", 1)[-1]
        payload = json.loads(prediction_path.read_text(encoding="utf-8"))
        predictions.append(
            {
                "predict": f"predict/predict_{index}.yaml",
                "prediction": str(prediction_path.relative_to(target)),
                "metrics": {
                    key: payload.get(key)
                    for key in (
                        "p99_ttft_ms",
                        "p99_tpot_ms",
                        "output_throughput_tok_s",
                        "completed_requests",
                        "decode_gpus_per_worker",
                    )
                },
            }
        )
    if predictions:
        manifest["predictions"] = predictions
    # 只保留已复制的性能证据：原 manifest 里的图片/脚本没有归档，
    # 不能继续出现在 outputs 里，否则哈希校验会指向不存在的文件。
    archived = {
        str((ROOT / item["path"]).relative_to(target)): item["sha256"]
        for item in copied
    }
    manifest["inputs"] = {
        rel: digest
        for rel, digest in archived.items()
        if rel == "search_input.yaml" or rel.startswith("predict/predict_")
    }
    manifest["outputs"] = {
        rel: digest
        for rel, digest in archived.items()
        if rel.startswith("predict/out_") or rel.endswith("pareto.csv")
    }
    manifest["archive_note"] = (
        "只归档性能证据（输入、预测、Pareto 表、配置）；图片与运行脚本未复制"
    )
    write_json(target / "manifest.json", manifest)
    write_json(
        target / "archive.json",
        {
            "archived_at": manifest["archived_at"],
            "kind": "auto_search_work_dir",
            "tool_version": manifest["tool_version"],
            "source_work_dir": str(work),
            "files": copied,
        },
    )
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", required=True)
    parser.add_argument("--archive-root", default="data/simulation/archives")
    parser.add_argument("--tool-version", default="0.12.1")
    parser.add_argument("--precision", default=None, help="例如 bf16（原生回放不回显精度）")
    parser.add_argument("--database-mode", default=None, help="例如 SILICON")
    parser.add_argument("--source", default=None)
    parser.add_argument("--overwrite", action="store_true")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--predict-config")
    group.add_argument("--work-dir")
    parser.add_argument("--prediction", help="与 --predict-config 配套")
    args = parser.parse_args()

    if args.predict_config:
        if not args.prediction:
            parser.error("--predict-config 需要同时给出 --prediction")
        target = archive_predict(args)
    else:
        target = archive_work_dir(args)
    print(f"已归档：{target.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
