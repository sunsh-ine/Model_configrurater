#!/usr/bin/env python3
"""AJ0.3：核对已执行运行是否覆盖预注册实验清单。"""

from __future__ import annotations

import argparse
import importlib.util
import json
from decimal import Decimal
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def _capacity_module():
    path = ROOT / "scripts" / "aj_capacity_report.py"
    spec = importlib.util.spec_from_file_location("aj_capacity_report_audit", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--run-root", default="data/benchmark/rtx3090")
    parser.add_argument("--json-out", default=None)
    args = parser.parse_args()
    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    root = ROOT / args.run_root
    capacity = _capacity_module()
    runs = []
    for directory in sorted(root.glob("*")):
        metadata_path = directory / "metadata.yaml"
        summary_path = directory / "client_summary.json"
        requests_path = directory / "requests.jsonl"
        report_path = directory / "import_report.json"
        if not (
            metadata_path.is_file()
            and summary_path.is_file()
            and requests_path.is_file()
            and report_path.is_file()
        ):
            continue
        metadata = yaml.safe_load(metadata_path.read_text(encoding="utf-8"))
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        report = json.loads(report_path.read_text(encoding="utf-8"))
        if metadata.get("runtime_config_sha256") != manifest["runtime_config_sha256"]:
            continue
        # 只有原始文件、summary、import_report 与来源认证全部一致才计入审计
        if not capacity.evidence_integrity(directory, metadata, summary, report)[0]:
            continue
        runs.append((directory.name, metadata, summary))
    results = []
    for workpoint in manifest["workpoints"]:
        expected = set(workpoint["expected_seeds"])
        matched = []
        excluded = []
        expected_rate = workpoint.get("request_rate_rps")
        for name, metadata, summary in runs:
            run_rate = metadata.get("request_rate_rps")
            if expected_rate is None:
                arrival_ok = (
                    run_rate is None
                    and metadata.get("configured_max_concurrency")
                    == workpoint.get("concurrency")
                )
            else:
                arrival_ok = (
                    run_rate is not None
                    and Decimal(str(run_rate)) == Decimal(str(expected_rate))
                )
            if not (
                metadata.get("input_len") == workpoint["input_len"]
                and metadata.get("output_len") == workpoint["output_len"]
                and arrival_ok
                and summary.get("seed") in expected
            ):
                continue
            entry = {
                "run_id": name,
                "seed": summary.get("seed"),
                "requests": summary.get("num_requests"),
                "success": summary.get("success_count"),
            }
            if entry["requests"] != workpoint["requests_per_run"]:
                excluded.append(
                    {**entry, "reason": "请求数与预注册不一致，不能计入正式重复"}
                )
                continue
            matched.append(entry)
        found = {item["seed"] for item in matched}
        # 重复检测必须包含被排除的运行（例如同 seed 的错请求数运行），
        # 否则一个成功副本会把失败/重复的正式运行掩盖掉。
        by_seed: dict[int, list[dict]] = {}
        for item in matched + excluded:
            by_seed.setdefault(item["seed"], []).append(item)
        duplicate_seeds = {
            seed: len(items) for seed, items in by_seed.items() if len(items) > 1
        }
        valid = {
            seed
            for seed, items in by_seed.items()
            if len(items) == 1
            and items[0] in matched
            and items[0]["success"] == workpoint["requests_per_run"]
        }
        results.append(
            {
                "workpoint_id": workpoint["workpoint_id"],
                "expected_seeds": sorted(expected),
                "found_seeds": sorted(found),
                "valid_seeds": sorted(valid),
                "missing_seeds": sorted(expected - found),
                "incomplete_seeds": sorted(found - valid),
                "duplicate_seeds": duplicate_seeds,
                "completeness": "complete"
                if found == expected and valid == expected
                else "incomplete",
                "runs": matched,
                "excluded_runs": excluded,
            }
        )
    doc = {
        "experiment_id": manifest["experiment_id"],
        "expected_runs": sum(len(w["expected_seeds"]) for w in manifest["workpoints"]),
        "workpoints": results,
    }
    out = Path(args.json_out) if args.json_out else ROOT / "docs/aj_experiment_audit.json"
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps(doc, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
