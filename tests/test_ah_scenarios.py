"""AH3/AH4/AH5：客户场景与统一决策证据。"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path("docs/ah_scenarios")


def _load(name: str):
    return json.loads((ROOT / name / "summary.json").read_text(encoding="utf-8"))


def test_scenario_a_compares_same_gpu_parallel_strategies():
    evidence = _load("A-same-gpu-parallel")["decision_evidence"]
    assert evidence["recommended"]["gpu_count"] == 8
    assert evidence["min_gpu_for_sla"]["gpu_count"] == 8
    assert {c["tp_size"] for c in evidence["simulation_candidates"]} >= {4, 8}
    assert any(
        c["tp_size"] == 4 and c["dp_size"] == 2
        for c in evidence["simulation_candidates"]
    )


def test_scenario_b_separates_min_gpu_from_max_throughput():
    evidence = _load("B-min-gpu")["decision_evidence"]
    assert evidence["min_gpu_for_sla"]["gpu_count"] == 2
    assert evidence["throughput_best"]["gpu_count"] == 4
    assert evidence["min_gpu_for_sla"]["candidate_id"] != evidence["throughput_best"]["candidate_id"]


def test_scenario_c_mixed_seeds_never_form_a_recommendation():
    evidence = _load("C-open-arrival-risk")["decision_evidence"]
    assert evidence["recommended"] is None
    assert evidence["min_gpu_for_sla"] is None
    statuses = {c["repeatability_status"] for c in evidence["simulation_candidates"]}
    assert statuses == {"mixed"}
    assert all(c["eligible"] is False for c in evidence["simulation_candidates"])
    assert all(c["experiment_completeness"] == "complete" for c in evidence["simulation_candidates"])


def test_all_scenarios_share_the_same_evidence_structure():
    for name in ("A-same-gpu-parallel", "B-min-gpu", "C-open-arrival-risk"):
        evidence = _load(name)["decision_evidence"]
        assert evidence["schema_version"] == "1.0"
        assert "candidates" in evidence
        assert "simulation_candidates" in evidence
        assert "evidence" in evidence
        assert evidence["evidence"]["simulation_status"] == "computed"
        assert evidence["objectives_are_independent"] == [
            "min_gpu",
            "max_throughput",
            "min_cost",
        ]


def test_simulation_success_does_not_unlock_quote_publication():
    for name in ("A-same-gpu-parallel", "B-min-gpu", "C-open-arrival-risk"):
        evidence = _load(name)["decision_evidence"]
        for candidate in evidence["candidates"]:
            assert candidate["governance"]["quote_publishable"] is False
