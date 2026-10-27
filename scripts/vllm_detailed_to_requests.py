#!/usr/bin/env python3
"""把 vLLM `bench serve --save-detailed` 输出转成逐请求 JSONL。

保留客户端原始 detailed JSON；逐请求行只做单位换算与派生：
E2E = TTFT + sum(ITL)，TPOT = (E2E - TTFT)/(output_len - 1)。
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--detailed", required=True)
    parser.add_argument("--out-dir", required=True)
    args = parser.parse_args()

    source = Path(args.detailed)
    data = json.loads(source.read_text(encoding="utf-8"))
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    ttfts = data.get("ttfts") or []
    itls = data.get("itls") or []
    output_lens = data.get("output_lens") or []
    input_lens = data.get("input_lens") or []
    errors = data.get("errors") or [""] * len(ttfts)
    n = len(ttfts)
    if not (len(itls) == len(output_lens) == len(input_lens) == n):
        raise SystemExit("detailed JSON 的逐请求数组长度不一致")

    rows = []
    for index in range(n):
        raw_ttft = ttfts[index]
        raw_itls = itls[index]
        ttft = float(raw_ttft) * 1000 if raw_ttft is not None else None
        itl_sum = (
            sum(float(item) for item in raw_itls) * 1000
            if raw_itls
            else None
        )
        latency = (
            ttft + itl_sum
            if ttft is not None and itl_sum is not None
            else None
        )
        output_len = int(output_lens[index] or 0)
        error = str(errors[index] or "")
        success = (
            not error
            and output_len > 0
            and ttft is not None
            and latency is not None
        )
        row = {
            "index": index,
            "input_length": int(input_lens[index] or 0),
            "output_length": output_len,
            "ttft_ms": ttft,
            "latency_ms": latency,
            "success": success,
            "terminal_status": "completed" if success else "failed",
            "client_ttft_ms": ttft,
            "client_latency_ms": latency,
        }
        if error:
            row["error"] = error
        rows.append(row)

    requests_path = out_dir / "requests.jsonl"
    requests_path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )
    summary = {
        "converted_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source_detailed": str(source),
        "source_duration_s": data.get("duration"),
        "num_requests": n,
        "completed": data.get("completed"),
        "total_input_tokens": data.get("total_input_tokens"),
        "total_output_tokens": data.get("total_output_tokens"),
        "request_throughput": data.get("request_throughput"),
        "output_throughput": data.get("output_throughput"),
        "p99_ttft_ms": data.get("p99_ttft_ms"),
        "p99_tpot_ms": data.get("p99_tpot_ms"),
        "mean_ttft_ms": data.get("mean_ttft_ms"),
        "mean_tpot_ms": data.get("mean_tpot_ms"),
        "max_concurrency": data.get("max_concurrency"),
        "request_rate": data.get("request_rate"),
        "success_count": sum(1 for row in rows if row["success"]),
        "failure_count": sum(1 for row in rows if not row["success"]),
        "measurement_window_ms": {
            "start_ms": 0,
            "end_ms": float(data.get("duration") or 0) * 1000,
        },
    }
    (out_dir / "client_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
