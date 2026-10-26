#!/usr/bin/env python3
"""AH1：执行后补全预注册实验清单（只登记实际归档，不改预测数值）。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from llm_capacity_pricing.perf.experiment_prereg import finalize_manifest
from llm_capacity_pricing.schemas.experiment_manifest import ExperimentManifest

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--model-id", default=None)
    parser.add_argument("--hardware-id", default=None)
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    path = Path(args.manifest)
    manifest = ExperimentManifest.model_validate(
        json.loads(path.read_text(encoding="utf-8"))
    )
    final = finalize_manifest(
        manifest,
        model_id=args.model_id,
        hardware_id=args.hardware_id,
    )
    out = Path(args.out) if args.out else path
    out.write_text(
        json.dumps(final.model_dump(mode="json"), ensure_ascii=False, indent=2)
        + "\n",
        encoding="utf-8",
    )
    print(
        f"已补全：{out}；execution_status={final.execution_status}，"
        f"runs={len(final.runs)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
