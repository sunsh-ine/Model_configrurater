#!/usr/bin/env python3
"""AH1：从 predict.yaml 生成预注册实验清单（运行前冻结，不包含结果）。"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import yaml

from llm_capacity_pricing.perf.experiment_prereg import (
    build_preregistered_manifest,
)

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment-id", required=True)
    parser.add_argument("--predict-yaml", required=True)
    parser.add_argument("--seeds", type=int, nargs="+", required=True)
    parser.add_argument("--required-completed", type=int, default=500)
    parser.add_argument("--tool-version", default="0.12.1")
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    config = yaml.safe_load(
        Path(args.predict_yaml).read_text(encoding="utf-8")
    ) or {}
    engine = config.get("engine") or {}
    workers = (engine.get("workers") or {}).get("aggregated") or {}
    parallel = workers.get("parallelism") or {}
    traffic = config.get("traffic") or {}
    load = traffic.get("load") or {}
    source = traffic.get("source") or {}
    stop = traffic.get("stop") or {}
    tp = int(parallel.get("tensor") or 1)
    pp = int(parallel.get("pipeline") or 1)
    replicas = int(parallel.get("replicas") or 1)
    load_type = str(load.get("type") or "concurrency")
    arrival = {
        "concurrency": "fixed_concurrency",
        "poisson": "poisson",
        "constant_rate": "constant_rate",
        "trace_timestamps": "trace",
    }.get(load_type, load_type)
    manifest = build_preregistered_manifest(
        experiment_id=args.experiment_id,
        predict_config=config,
        expected_seeds=args.seeds,
        model_name=str(engine.get("model")),
        model_precision=str(source.get("precision") or source.get("dtype") or "bf16"),
        system=str(engine.get("hardware")),
        backend=str(engine.get("backend") or "vllm"),
        backend_version=str(engine.get("backend_version") or ""),
        gpu_count=tp * pp * replicas,
        tp_size=tp,
        pp_size=pp,
        dp_size=replicas,
        replicas=replicas,
        input_len=int(source.get("input_tokens") or 0),
        output_len=int(source.get("output_tokens") or 0),
        concurrency=(
            int(load.get("max_concurrency"))
            if load.get("max_concurrency") is not None
            else None
        ),
        arrival_mode=arrival,
        request_rate_rps=load.get("requests_per_second"),
        dataset=source.get("dataset") or (
            "synthetic" if source.get("type") == "synthetic" else None
        ),
        trace_id=load.get("trace_id") or load.get("trace_path"),
        num_requests=stop.get("requests"),
        stop_condition=", ".join(f"{k}={v}" for k, v in stop.items()) or None,
        required_completed_requests=args.required_completed,
        tool_version=args.tool_version,
        now=datetime.now(timezone.utc),
    )
    out = (
        Path(args.out)
        if args.out
        else ROOT / "data/simulation/experiments" / f"{args.experiment_id}.json"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        raise SystemExit(f"拒绝覆盖已有实验清单：{out}")
    out.write_text(
        json.dumps(manifest.model_dump(mode="json"), ensure_ascii=False, indent=2)
        + "\n",
        encoding="utf-8",
    )
    print(f"已预注册：{out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
