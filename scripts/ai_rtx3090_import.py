#!/usr/bin/env python3
"""AI4：归档、校验、入库并核验一条 RTX 3090 实测。"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

from llm_capacity_pricing.perf.benchmark_ingest import run_import_pipeline
from llm_capacity_pricing.services import (
    import_performance_run,
    init_db,
    open_db,
)

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--requests", required=True)
    parser.add_argument("--summary", required=True)
    parser.add_argument("--environment", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--input-len", type=int, required=True)
    parser.add_argument("--output-len", type=int, required=True)
    parser.add_argument("--concurrency", type=int, required=True)
    parser.add_argument("--request-rate", type=float, default=None)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--server-log", required=True)
    parser.add_argument("--server-args", default="")
    parser.add_argument("--db", default=None)
    args = parser.parse_args()

    import sys

    cmd = [
        sys.executable,
        str(Path(__file__).with_name("archive_rtx3090_client_run.py")),
        "--requests", args.requests,
        "--summary", args.summary,
        "--environment", args.environment,
        "--run-id", args.run_id,
        "--input-len", str(args.input_len),
        "--output-len", str(args.output_len),
        "--concurrency", str(args.concurrency),
        "--seed", str(args.seed),
        "--server-log", args.server_log,
        "--server-args", args.server_args,
    ]
    if args.request_rate is not None:
        cmd += ["--request-rate", str(args.request_rate)]
    subprocess.run(cmd, check=True)
    out_dir = ROOT / "data/benchmark/rtx3090" / args.run_id
    metadata_path = out_dir / "metadata.yaml"
    import yaml

    metadata = yaml.safe_load(metadata_path.read_text(encoding="utf-8"))
    db_path = ROOT / args.db if args.db else None
    validation = run_import_pipeline(
        out_dir / "requests.jsonl",
        metadata,
        evidence_root=out_dir,
        metadata_file=metadata_path,
        # 先只做校验；持久化走 BenchmarkRun 路径，避免把同一份记录写两套。
        db_path=None,
        registry=ROOT / "data/benchmark/source_attestations.json",
    )
    if not validation.get("accepted"):
        raise SystemExit(f"导入被拒绝：{validation.get('errors')}")
    if db_path is not None and not (
        validation.get("qualification") or {}
    ).get("sla_evaluable"):
        raise SystemExit(
            "该运行未通过 sla_evaluable，拒绝作为 SLA 证据入库："
            f"{(validation.get('qualification') or {}).get('sla_reasons')}；"
            "请先运行 scripts/attest_rtx3090_run.py 完成受控登记，再重新执行本导入。"
        )
    result = {"validation": validation}
    if db_path is not None:
        init_db(db_path)
        runs = import_performance_run(
            out_dir / "requests.jsonl",
            metadata_path,
            db_path,
            kind="sglang",
            qualification=validation.get("qualification"),
        )
        result["import"] = runs
        result["data_trust_before_attest"] = runs["data_trust"]
        result["note"] = (
            "运行已入库；来源须先由 attest_rtx3090_run.py 登记，"
            "再由 attest-run 升级为 measured_internal。"
        )
    out_json = out_dir / "import_report.json"
    out_json.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
