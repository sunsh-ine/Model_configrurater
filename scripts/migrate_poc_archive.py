#!/usr/bin/env python3
"""把 N 阶段 PoC 的原生回放迁移为标准归档（原始文件不改）。

目标：
* 归档目录包含 predict 输入、prediction、exp_config、per_ops_source；
* manifest 登记所有实际使用文件的 SHA256；
* 保留原始预测值，不做任何重算或改写。
"""

from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
POC = ROOT / "docs/poc/aisimulate"
TARGET = ROOT / "data/simulation/archives/poc-qwen3-32b-h100-tp8-c32-bf16"

SOURCE_INPUT = POC / "16_matched_native_input_tp8_conc32_bf16.yaml"
SOURCE_PREDICTION = POC / "17_matched_native_result_tp8_conc32_bf16.json"
SOURCE_EXP = POC / "artifacts/matched_exp_config_vllm_bf16.yaml"
SOURCE_PER_OPS = POC / "artifacts/per_ops_source.json"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    for path in (SOURCE_INPUT, SOURCE_PREDICTION, SOURCE_EXP, SOURCE_PER_OPS):
        if not path.exists():
            raise SystemExit(f"缺少 PoC 文件：{path}")
    if TARGET.exists():
        shutil.rmtree(TARGET)
    (TARGET / "predict/out_1").mkdir(parents=True)

    copies = [
        (SOURCE_INPUT, TARGET / "predict/predict_1.yaml"),
        (SOURCE_PREDICTION, TARGET / "predict/out_1/prediction.json"),
        (SOURCE_EXP, TARGET / "exp_config.yaml"),
        (SOURCE_PER_OPS, TARGET / "per_ops_source.json"),
    ]
    for source, target in copies:
        shutil.copy2(source, target)

    # 归档输入显式写出 pipeline=1（原文件省略该字段）；不改变预测值。
    import yaml

    predict_path = TARGET / "predict/predict_1.yaml"
    cfg = yaml.safe_load(predict_path.read_text(encoding="utf-8")) or {}
    parallel = ((cfg.get("engine") or {}).get("workers") or {}).get(
        "aggregated"
    ) or {}
    parallel.setdefault("parallelism", {})
    parallel["parallelism"].setdefault("pipeline", 1)
    parallel["parallelism"].setdefault("replicas", 1)
    predict_path.write_text(
        yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )

    prediction = json.loads(SOURCE_PREDICTION.read_text(encoding="utf-8"))
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
        "tool_version": "0.12.1",
        "run_id": TARGET.name,
        "source": "N 阶段 PoC 原生回放迁移；原始 prediction 未修改",
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "spec": {
            "model": "Qwen/Qwen3-32B",
            "precision": "bf16",
            "system": "h100_sxm",
            "isl": 8192,
            "osl": 256,
            "concurrency": 32,
            "backend": "vllm",
            "backend_version": "0.24.0",
            "max_total_gpus": 8,
            "database_mode": "SILICON",
        },
        "database_mode_source": "archive_declared",
        "workpoint": {
            "model": "Qwen/Qwen3-32B",
            "system": "h100_sxm",
            "precision": "bf16",
            "input_len": 8192,
            "output_len": 256,
            "concurrency": 32,
            "tp": 8,
            "pp": 1,
            "dp": 1,
            "requests": 500,
        },
        "inputs": {
            "predict/predict_1.yaml": sha256(TARGET / "predict/predict_1.yaml"),
            "exp_config.yaml": sha256(TARGET / "exp_config.yaml"),
            "per_ops_source.json": sha256(TARGET / "per_ops_source.json"),
        },
        "outputs": {
            "predict/out_1/prediction.json": sha256(
                TARGET / "predict/out_1/prediction.json"
            )
        },
        "native_metrics": metrics,
        "errors": [],
        "run_errors": [],
        "candidate_errors": {},
        "not_aligned": {},
        "not_replayed": {},
    }
    (TARGET / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (TARGET / "archive.json").write_text(
        json.dumps(
            {
                "archived_at": manifest["created_at"],
                "kind": "poc_migration",
                "source_files": {
                    "input": str(SOURCE_INPUT.relative_to(ROOT)),
                    "prediction": str(SOURCE_PREDICTION.relative_to(ROOT)),
                    "exp_config": str(SOURCE_EXP.relative_to(ROOT)),
                    "per_ops_source": str(SOURCE_PER_OPS.relative_to(ROOT)),
                },
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"已迁移：{TARGET.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
