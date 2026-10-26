#!/usr/bin/env python3
"""AG1：从已归档的多次 seed 运行生成冻结实验清单。

清单只登记“预期跑什么”和“实际找到了什么”，不产生任何性能数值。
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from llm_capacity_pricing.app.archive_integrity import (
    verify_files,
    verify_predict_metadata,
)
from llm_capacity_pricing.app.offline_archives import find_archives

ROOT = Path(__file__).resolve().parents[1]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def rel(path: Path) -> str:
    return str(path.resolve().relative_to(ROOT.resolve()))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment-id", required=True)
    parser.add_argument("--model-id", required=True)
    parser.add_argument("--hardware-id", required=True)
    parser.add_argument("--tp", type=int, required=True)
    parser.add_argument("--dp", type=int, required=True)
    parser.add_argument("--input-len", type=int, required=True)
    parser.add_argument("--output-len", type=int, required=True)
    parser.add_argument("--arrival-mode", required=True)
    parser.add_argument("--rate", default=None)
    parser.add_argument("--concurrency", type=int, default=None)
    parser.add_argument("--dataset", default=None)
    parser.add_argument("--seeds", type=int, nargs="+", required=True)
    parser.add_argument("--required-completed", type=int, default=500)
    parser.add_argument("--precision", default="bf16")
    parser.add_argument("--framework", default="vllm")
    parser.add_argument("--framework-version", default="0.24.0")
    parser.add_argument(
        "--out", default=None, help="默认 data/simulation/experiments/<id>.json"
    )
    args = parser.parse_args()

    runs = []
    identity = None
    for seed in sorted(set(args.seeds)):
        entries = find_archives(
            model_id=args.model_id,
            hardware_id=args.hardware_id,
            input_len=args.input_len,
            output_len=args.output_len,
            concurrency=args.concurrency,
            precision=args.precision,
            framework=args.framework,
            framework_version=args.framework_version,
            gpu_limit=None,
            tp_sizes=(args.tp,),
            arrival_mode=args.arrival_mode,
            request_rate_rps=args.rate,
            seed=seed,
            min_requests=0,
        )
        entries = [
            entry
            for entry in entries
            if entry.dp_size == args.dp and entry.pp_size in (None, 1)
        ]
        if len(entries) != 1:
            runs.append(
                {
                    "seed": seed,
                    "status": "missing" if not entries else "invalid",
                    "notes": [
                        "缺少该 seed 的归档"
                        if not entries
                        else "同一 seed 存在多个不一致归档"
                    ],
                }
            )
            continue
        entry = entries[0]
        prediction_path = ROOT / entry.native_prediction
        run_dir = prediction_path.parents[2]
        manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
        rel_pred = str(prediction_path.relative_to(run_dir))
        index = prediction_path.parent.name.rsplit("_", 1)[-1]
        rel_input = f"predict/predict_{index}.yaml"
        problems = verify_files(run_dir, manifest, [rel_input, rel_pred])
        if not problems:
            problems.extend(verify_predict_metadata(run_dir, manifest, rel_input))
        payload = json.loads(prediction_path.read_text(encoding="utf-8"))
        completed = payload.get("completed_requests")
        status = (
            "invalid"
            if problems or completed is None or completed < args.required_completed
            else "succeeded"
        )
        config_file = ROOT / entry.exp_config if entry.exp_config else None
        runs.append(
            {
                "seed": seed,
                "status": status,
                "archive": entry.native_prediction,
                "archive_sha256": sha256_file(prediction_path),
                "config_file": rel(config_file) if config_file else None,
                "config_sha256": sha256_file(config_file) if config_file else None,
                "completed_requests": completed,
                "notes": problems,
            }
        )
        if identity is None:
            identity = {
                "model_name": entry.model_name,
                "system": entry.system,
                "backend": entry.backend,
                "backend_version": entry.backend_version,
                "gpu_count": entry.gpu_count,
                "tp_size": entry.tp_size,
                "pp_size": entry.pp_size,
                "dp_size": entry.dp_size,
                "dataset": entry.dataset,
                "num_requests": payload.get("num_requests"),
                "stop_condition": entry.requests
                and f"requests={entry.requests}",
            }

    if identity is None:
        raise SystemExit("没有任何 seed 找到归档，无法生成清单")
    doc = {
        "experiment_id": args.experiment_id,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "tool": "aisimulate",
        "tool_version": "0.12.1",
        "model_name": identity["model_name"],
        "model_precision": args.precision,
        "system": identity["system"],
        "backend": identity["backend"],
        "backend_version": identity["backend_version"],
        "gpu_count": identity["gpu_count"],
        "serving_mode": "agg",
        "tp_size": identity["tp_size"],
        "pp_size": identity["pp_size"],
        "dp_size": identity["dp_size"],
        "replicas": identity["dp_size"],
        "input_len": args.input_len,
        "output_len": args.output_len,
        "concurrency": args.concurrency,
        "arrival_mode": args.arrival_mode,
        "request_rate_rps": args.rate,
        "dataset": identity["dataset"] or args.dataset,
        "num_requests": identity["num_requests"],
        "stop_condition": identity["stop_condition"],
        "required_completed_requests": args.required_completed,
        "expected_seeds": sorted(set(args.seeds)),
        "config_file": runs[0].get("config_file") if runs else None,
        "config_sha256": runs[0].get("config_sha256") if runs else None,
        "runs": runs,
    }
    out = (
        Path(args.out)
        if args.out
        else ROOT / "data/simulation/experiments" / f"{args.experiment_id}.json"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"已写出实验清单：{rel(out)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
