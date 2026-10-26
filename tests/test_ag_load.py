"""AG3：缺失队列字段保持 unknown，不默认 0。"""

from __future__ import annotations

import json

from scripts import load_analysis, queue_stability_report


def _write_case(tmp_path, rows):
    (tmp_path / "prediction.json").write_text(
        json.dumps({"completed_requests": len(rows)}), encoding="utf-8"
    )
    (tmp_path / "predict.yaml").write_text(
        "traffic:\n  load:\n    type: poisson\n    requests_per_second: 5\n",
        encoding="utf-8",
    )
    (tmp_path / "requests.jsonl").write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8"
    )


def test_queue_wait_missing_is_unknown_not_zero(tmp_path):
    rows = [
        {
            "arrival_time_ms": index,
            "first_token_ms": 100 + index,
            "last_token_ms": 1000 + index,
            "ttft_ms": 100,
            "e2e_latency_ms": 900,
            "output_length": 10,
            "terminal_status": "completed",
        }
        for index in range(10)
    ]
    _write_case(tmp_path, rows)
    case = queue_stability_report.analyse("case", tmp_path)
    assert case["queue_wait_available_requests"] == 0
    assert case["queue_wait_missing_requests"] == 10
    assert case["p99_queue_wait_ms"] is None
    assert case["max_queue_wait_ms"] is None


def test_load_analysis_reports_missing_scheduler_metrics_as_unknown(tmp_path):
    rows = [
        {
            "arrival_ms": index * 100,
            "first_token_ms": 200,
            "last_token_ms": 1600,
            "ttft_ms": 200,
            "e2e_latency_ms": 1600,
            "input_length": 8192,
            "output_length": 128,
            "terminal_status": "completed",
        }
        for index in range(10)
    ]
    _write_case(tmp_path, rows)
    case = load_analysis.analyse("case", tmp_path)
    full = case["windows"]["full"]
    assert full["queue_wait_p99_ms"] is None
    assert full["queue_wait_missing_requests"] == 10
    assert full["ttft_p99_ms"] == 200
    assert full["tpot_p99_ms"] is not None
    assert case["configured_arrival_rate_rps"] == 5
