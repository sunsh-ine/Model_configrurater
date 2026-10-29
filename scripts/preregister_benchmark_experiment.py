#!/usr/bin/env python3
"""AJ0.3：运行前冻结真实 Benchmark 实验清单。"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment-id", required=True)
    parser.add_argument("--runtime-config", required=True)
    parser.add_argument("--workpoint", action="append", required=True,
                        help="wp_id:isl:osl:concurrency|rate:seeds:requests")
    parser.add_argument("--out", default=None)
    args = parser.parse_args()
    runtime = json.loads(Path(args.runtime_config).read_text(encoding="utf-8"))
    runtime_sha = hashlib.sha256(
        json.dumps(runtime, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()
    workpoints = []
    for spec in args.workpoint:
        wp_id, isl, osl, load, seeds, requests = spec.split(":")
        seeds_list = [int(x) for x in seeds.split(",")]
        load_value = Decimal(load)
        workpoints.append(
            {
                "workpoint_id": wp_id,
                "input_len": int(isl),
                "output_len": int(osl),
                "concurrency": int(load_value) if "." not in load else None,
                "request_rate_rps": str(load_value) if "." in load else None,
                "expected_seeds": seeds_list,
                "requests_per_run": int(requests),
                "repeats": len(seeds_list),
                "ttft_p99_ms_max": "6000",
                "tpot_p99_ms_max": "40",
            }
        )
    doc = {
        "schema_version": "1.0",
        "experiment_id": args.experiment_id,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "manifest_status": "preregistered",
        "runtime_config_sha256": runtime_sha,
        "workpoints": workpoints,
    }
    out = (
        Path(args.out)
        if args.out
        else ROOT / "data/benchmark/experiments" / f"{args.experiment_id}.json"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        raise SystemExit(f"拒绝覆盖已有预注册清单：{out}")
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"已预注册：{out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
