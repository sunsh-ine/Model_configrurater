"""AI3/AI4/AI6：RTX 3090 实测结果与客户场景的固定断言。"""

from __future__ import annotations

import json
from pathlib import Path


def test_rtx3090_formal_repeatability_is_all_pass():
    doc = json.loads(Path("docs/ai_rtx3090_results.json").read_text(encoding="utf-8"))
    repeat = doc["formal_repeatability"]
    assert repeat["runs"] == 5
    assert repeat["pass_count"] == 5
    assert 1765.0 <= repeat["ttft_p99_min"] < repeat["ttft_p99_max"] <= 2221.0
    assert 38.0 <= repeat["tpot_p99_min"] < repeat["tpot_p99_max"] <= 40.0


def test_aisimulate_does_not_claim_rtx3090_support():
    doc = json.loads(Path("docs/ai_rtx3090_results.json").read_text(encoding="utf-8"))
    assert doc["aisimulate_rtx3090_support"] is False


def test_scenario_d_uses_measured_evidence_and_not_quote_publishable():
    plan = json.loads(Path("docs/ai_scenario_d/plan.json").read_text(encoding="utf-8"))
    candidate = plan["candidates"][0]
    assert candidate["sla_status"] == "observed_pass"
    assert candidate["match_level"] == "exact_match"
    assert candidate["rank_bucket"] == "verified_sla_met"
    assert candidate["quote_publishable"] is False
    evidence = plan["decision_evidence"]
    assert evidence["recommended"]["candidate_id"] == candidate["candidate_id"]
    assert evidence["evidence"]["measured_candidates"]
    assert "rtx3090:ai-e1c8-seed1" in candidate["evidence_run_ids"]
    assert len(candidate["evidence_run_ids"]) == 5
