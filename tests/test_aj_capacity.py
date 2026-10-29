"""AJ1/AJ6：容量点与推荐结构的核心断言。"""

from __future__ import annotations

from decimal import Decimal

from llm_capacity_pricing.perf.capacity_analysis import build_capacity_point
from llm_capacity_pricing.perf.capacity_recommendation import build_capacity_evidence


def _rows(n=100, *, success=True, ttft=1000.0, latency=8000.0, out=256):
    return [
        {
            "arrival_ms": index * 100,
            "ttft_ms": ttft,
            "latency_ms": latency,
            "output_length": out,
            "success": success,
        }
        for index in range(n)
    ]


def test_capacity_point_separates_throughput_and_goodput():
    point = build_capacity_point(
        _rows(),
        candidate_id="x",
        input_len=2048,
        output_len=256,
        window_s=Decimal("100"),
        ttft_limit_ms=Decimal("6000"),
        tpot_limit_ms=Decimal("40"),
        concurrency=8,
    )
    assert point.success_rate == 1
    assert point.output_tps == Decimal(25600) / Decimal(100)
    assert point.sla_goodput_rps is not None


def test_failed_request_blocks_full_reliability():
    rows = _rows()
    rows[0]["success"] = False
    point = build_capacity_point(
        rows,
        candidate_id="x",
        input_len=2048,
        output_len=256,
        window_s=Decimal("100"),
        ttft_limit_ms=Decimal("6000"),
        tpot_limit_ms=Decimal("40"),
        concurrency=8,
    )
    assert point.success_rate < 1
    assert point.steady_state_status == "unstable"


def test_margin_marks_tpot_near_limit():
    point = build_capacity_point(
        _rows(ttft=1000.0, latency=1000.0 + 39 * 255),
        candidate_id="x",
        input_len=2048,
        output_len=256,
        window_s=Decimal("100"),
        ttft_limit_ms=Decimal("6000"),
        tpot_limit_ms=Decimal("40"),
        concurrency=8,
    )
    assert point.tpot_margin_pct is not None
    assert point.tpot_margin_pct < 5




def _ev(
    *,
    concurrency: int | None = 8,
    rate: str | None = None,
    ttft: float = 2000,
    tpot: float = 39,
    success=1,
    tps: float = 190,
    goodput: float = 0.7,
    steady: str = "observed_stable_in_window",
    seed: int = 1,
    isl: int = 2048,
    osl: int = 256,
):
    point = {
        "concurrency": None if rate is not None else concurrency,
        "request_rate_rps": rate,
        "input_len": isl,
        "output_len": osl,
        "ttft_p99_ms": ttft,
        "tpot_p99_ms": tpot,
        "success_rate": success,
        "ttft_limit_ms": 6000,
        "tpot_limit_ms": 40,
        "output_tps": tps,
        "sla_goodput_rps": goodput,
        "steady_state_status": steady,
        "seed": seed,
        "capacity_evidence_ids": [f"rtx3090:test-{concurrency}-{rate}-{seed}"],
        "ingest_accepted": True,
        "sla_evaluable": True,
    }
    if rate is not None:
        point["arrival_rps"] = "0.646"
        point["completed_rps"] = "0.646"
    return point


def test_capacity_recommendation_prefers_margin_then_sla():
    fixed = [
        *[_ev(concurrency=4, ttft=1000, tpot=34, tps=100, goodput=0.4, seed=i)
          for i in range(1, 6)],
        *[_ev(concurrency=8, ttft=2000, tpot=39, tps=190, goodput=0.7, seed=i)
          for i in range(1, 6)],
    ]
    evidence = build_capacity_evidence(
        fixed, ttft_limit_ms=Decimal("6000"), tpot_limit_ms=Decimal("40")
    )
    assert evidence["max_output_tps"] == Decimal("190")
    assert evidence["margin_10pct_concurrencies"] == [4]
    assert evidence["recommended_value"] == 4


def test_margin_point_with_a_failure_is_not_recommended():
    """单次失败就足以取消裕量资格：延迟裕量不能覆盖可靠性失败。"""
    fixed = [
        *[_ev(concurrency=4, ttft=1000, tpot=34, tps=100, goodput=0.4, seed=i)
          for i in range(1, 5)],
        _ev(concurrency=4, ttft=1000, tpot=34, success="0.998", tps=100,
            goodput=0.4, steady="unstable", seed=5),
        *[_ev(concurrency=8, ttft=2000, tpot=39.5, tps=190, goodput=0.7, seed=i)
          for i in range(1, 6)],
    ]
    evidence = build_capacity_evidence(fixed, tpot_limit_ms=Decimal("40"))
    assert evidence["margin_10pct_concurrencies"] == []
    assert evidence["margin_5pct_concurrencies"] == []
    assert evidence["recommended_value"] == 8


def test_single_run_cannot_become_verified_capacity():
    evidence = build_capacity_evidence(
        [_ev(concurrency=8, seed=1)],
        ttft_limit_ms=Decimal("6000"),
        tpot_limit_ms=Decimal("40"),
    )
    assert evidence["max_verified_fixed_concurrency"] is None
    assert evidence["recommended_mode"] is None


def test_unverified_evidence_is_not_verified_capacity():
    fixed = [
        {**_ev(concurrency=8, seed=i), "capacity_evidence_ids": []}
        for i in range(1, 6)
    ]
    evidence = build_capacity_evidence(
        fixed, ttft_limit_ms=Decimal("6000"), tpot_limit_ms=Decimal("40")
    )
    assert evidence["max_verified_fixed_concurrency"] is None


def test_goodput_excludes_sla_violations_even_when_successful():
    rows = _rows(10, ttft=1000.0, latency=8000.0)
    rows[0] = {**rows[0], "ttft_ms": 7000.0}  # 成功但违反 TTFT SLA
    point = build_capacity_point(
        rows,
        candidate_id="x",
        input_len=2048,
        output_len=256,
        window_s=Decimal("100"),
        ttft_limit_ms=Decimal("6000"),
        tpot_limit_ms=Decimal("40"),
        concurrency=8,
    )
    assert point.success_rate == 1
    assert point.sla_goodput_rps == Decimal(9) / Decimal("100")


def test_missing_output_length_makes_throughput_unknown():
    rows = _rows(10)
    rows[0] = {**rows[0], "output_length": None}
    point = build_capacity_point(
        rows,
        candidate_id="x",
        input_len=2048,
        output_len=256,
        window_s=Decimal("100"),
        ttft_limit_ms=Decimal("6000"),
        tpot_limit_ms=Decimal("40"),
        concurrency=8,
    )
    assert point.output_tps is None


def test_short_window_stays_unknown_even_without_failures():
    point = build_capacity_point(
        _rows(50),
        candidate_id="x",
        input_len=2048,
        output_len=256,
        window_s=Decimal("200"),
        ttft_limit_ms=Decimal("6000"),
        tpot_limit_ms=Decimal("40"),
        concurrency=8,
    )
    assert point.steady_state_status == "unknown"
    assert point.steady_state_evidence == "insufficient_window"


def test_open_arrival_used_only_when_fixed_capacity_unproven():
    points = [
        _ev(concurrency=8, tpot=41, steady="unstable", seed=1),
        *[_ev(rate="0.65", ttft=1400, tpot=39, tps=165, goodput=0.64, seed=i)
          for i in range(1, 6)],
    ]
    evidence = build_capacity_evidence(
        points, ttft_limit_ms=Decimal("6000"), tpot_limit_ms=Decimal("40")
    )
    assert evidence["max_verified_fixed_concurrency"] is None
    assert evidence["recommended_mode"] == "open_arrival"
    assert evidence["recommended_value"] == Decimal("0.65")


def test_open_arrival_single_window_is_not_a_candidate():
    points = [
        _ev(rate="0.65", ttft=1400, tpot=39, tps=165, goodput=0.64, seed=1),
    ]
    evidence = build_capacity_evidence(
        points, ttft_limit_ms=Decimal("6000"), tpot_limit_ms=Decimal("40")
    )
    assert evidence["max_steady_open_rate_rps"] is None


def test_generator_input_keeps_open_arrival_points():
    points = (
        p
        for p in [
            *[_ev(concurrency=8, ttft=2000, tpot=39, seed=i) for i in range(1, 6)],
            *[_ev(rate="0.65", ttft=1400, tpot=39, tps=165, goodput=0.64, seed=i)
              for i in range(1, 6)],
        ]
    )
    evidence = build_capacity_evidence(
        points, ttft_limit_ms=Decimal("6000"), tpot_limit_ms=Decimal("40")
    )
    assert len(evidence["open_arrival"]) == 5
    assert evidence["max_steady_open_rate_rps"] == Decimal("0.65")


def test_missing_ttft_in_half_is_unknown_not_stable():
    rows = _rows(100)
    for row in rows[:50]:
        row["ttft_ms"] = None
    point = build_capacity_point(
        rows,
        candidate_id="x",
        input_len=2048,
        output_len=256,
        window_s=Decimal("400"),
        ttft_limit_ms=Decimal("6000"),
        tpot_limit_ms=Decimal("40"),
        concurrency=8,
    )
    assert point.steady_state_status == "unknown"
    assert point.steady_state_evidence == "insufficient_half_samples"


def test_recommendation_carries_workload_with_its_concurrency():
    fixed = [
        *[_ev(concurrency=4, isl=1024, osl=128, ttft=1000, tpot=30,
              tps=100, goodput=0.4, seed=i) for i in range(1, 6)],
        *[_ev(concurrency=2, isl=8192, osl=256, ttft=3000, tpot=35,
              tps=40, goodput=0.1, seed=i) for i in range(1, 6)],
    ]
    evidence = build_capacity_evidence(
        fixed, ttft_limit_ms=Decimal("6000"), tpot_limit_ms=Decimal("40")
    )
    assert evidence["recommended_value"] == 4
    assert evidence["recommended_workload"] == {"input_len": 1024, "output_len": 128}
    assert evidence["max_verified_by_workload"] == {"1024/128": 4, "8192/256": 2}


def test_formal_eligibility_requires_qualification_and_config_binding():
    import importlib.util
    from pathlib import Path

    script = Path(__file__).resolve().parents[1] / "scripts" / "aj_capacity_report.py"
    spec = importlib.util.spec_from_file_location("aj_capacity_report_test", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    qualified = {"ingest_accepted": True, "sla_evaluable": True}
    frozen = module.FROZEN_RUNTIME_SHA
    assert module.classify_run("aj-e1c8-seed1", frozen, qualified)["formal"] is True
    assert (
        module.classify_run("aj-e1c8-seed1", frozen, {})["formal"] is False
    )
    assert (
        module.classify_run(
            "aj-e1c8-seed1",
            frozen,
            {"ingest_accepted": True, "sla_evaluable": False},
        )["formal"]
        is False
    )
    assert (
        module.classify_run("aj-e1c8-seed1", "0" * 64, qualified)["formal"] is False
    )
    assert (
        module.classify_run("random-run", frozen, qualified)["formal"] is False
    )


def test_missing_latency_makes_goodput_unknown():
    rows = _rows(10)
    rows[0] = {**rows[0], "ttft_ms": None}
    point = build_capacity_point(
        rows,
        candidate_id="x",
        input_len=2048,
        output_len=256,
        window_s=Decimal("100"),
        ttft_limit_ms=Decimal("6000"),
        tpot_limit_ms=Decimal("40"),
        concurrency=8,
    )
    assert point.sla_goodput_rps is None
    assert point.sla_goodput_complete is False


def test_unknown_throughput_is_not_reported_as_zero():
    fixed = [
        {**_ev(concurrency=8, seed=i), "output_tps": None, "sla_goodput_rps": None}
        for i in range(1, 6)
    ]
    evidence = build_capacity_evidence(
        fixed, ttft_limit_ms=Decimal("6000"), tpot_limit_ms=Decimal("40")
    )
    assert evidence["max_output_tps"] is None
    assert evidence["max_sla_goodput_rps"] is None
    assert evidence["fixed_concurrency"][0]["max_output_tps"] is None


def test_underdelivered_arrival_rate_is_not_steady_capacity():
    points = [
        _ev(rate="1.0", ttft=1400, tpot=39, tps=190, goodput=0.75, seed=i)
        for i in range(1, 6)
    ]
    for point in points:
        point["arrival_rps"] = "0.756"
        point["completed_rps"] = "0.756"
    evidence = build_capacity_evidence(
        points, ttft_limit_ms=Decimal("6000"), tpot_limit_ms=Decimal("40")
    )
    assert evidence["max_steady_open_rate_rps"] is None


def test_preregistered_manifest_matching_is_name_independent():
    import importlib.util
    from pathlib import Path

    script = Path(__file__).resolve().parents[1] / "scripts" / "aj_capacity_report.py"
    spec = importlib.util.spec_from_file_location("aj_capacity_report_prereg", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    frozen = module.FROZEN_RUNTIME_SHA
    manifest = {
        "runtime_config_sha256": frozen,
        "input_len": 2048,
        "output_len": 256,
        "concurrency": 6,
        "request_rate_rps": None,
        "expected_seeds": {1, 2, 3, 4, 5},
        "requests_per_run": 500,
    }
    assert module.matches_preregistration(
        manifest,
        run_id="aj-e1c6-seed3",
        runtime_sha=frozen,
        input_len=2048,
        output_len=256,
        concurrency=6,
        request_rate_rps=None,
        seed=3,
        num_requests=500,
    )
    assert not module.matches_preregistration(
        manifest,
        run_id="aj-e1c6-seed6",
        runtime_sha=frozen,
        input_len=2048,
        output_len=256,
        concurrency=6,
        request_rate_rps=None,
        seed=6,
        num_requests=500,
    )
    flags = module.classify_run(
        "brand-new-run-name", frozen,
        {"ingest_accepted": True, "sla_evaluable": True},
        preregistered=True,
    )
    assert flags["formal"] is True
