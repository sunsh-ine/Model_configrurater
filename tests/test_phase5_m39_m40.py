"""M39（现场性能与 SLA 可信度验证）与 M40（真实成本与方案比较闭环）测试。

M39 验收：相同模型与配置但不同长度/并发的数据不能证明目标 SLA；
真实达标与真实不达标都能正确报告；Synthetic 仍不可替代真实依据。
M40 验收：允许情景估算；价格未确认时不能宣称正式价格；
缺性能数据的低成本方案不得被直接称为最优部署。
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from llm_capacity_pricing.cli import app
from llm_capacity_pricing.perf.site_validation import validate_site_performance
from llm_capacity_pricing.reporting.plan_report import build_plan_report
from llm_capacity_pricing.schemas.performance import (
    SlaRequirement,
    TargetRequirement,
    WorkloadSignature,
)
from llm_capacity_pricing.schemas.plan import (
    PlanAdvanced,
    PlanCostInput,
    PlanRequest,
    PlanResourceAssumptions,
    PlanWorkload,
)
from llm_capacity_pricing.services import (
    import_cost_catalog,
    import_hardware,
    import_models,
    import_performance_curve,
    init_db,
    open_db,
    validate_site_performance_for,
)
from llm_capacity_pricing.workflow import evaluate_plan

runner = CliRunner()
CATALOG = "data/examples/phase4_cost_inputs.example.yaml"
PLAN_TEMPLATE = "data/examples/phase4_cost_plan.yaml"


def _db(tmp_path: Path, root: Path, *, with_cost: bool = False, name: str = "m3940.db") -> str:
    db = str(tmp_path / name)
    init_db(db)
    import_models(root / "data/models/model_manifest.yaml", db, root=root)
    import_hardware(root / "data/hardware/hardware_catalog.yaml", db)
    if with_cost:
        import_cost_catalog(CATALOG, db)
    return db


def _measured_runs(root: Path, *, concurrency: int | None = None, trust: str = "measured_internal"):
    """身份对齐的机制夹具（曲线样本标记为内部实测）。"""
    folder = root / "data" / "fixtures" / "perf_curve"
    index = json.loads((folder / "INDEX.json").read_text(encoding="utf-8"))
    from llm_capacity_pricing.importers.vllm_adapter import (
        parse_vllm_bench_serve_json_full,
    )
    from llm_capacity_pricing.perf.performance_profile import run_from_adapter

    runs = []
    for point in index["points"]:
        if concurrency is not None and not point["file"].endswith(f"c{concurrency}.json"):
            continue
        data = folder / point["file"]
        meta = yaml.safe_load((folder / point["metadata"]).read_text(encoding="utf-8"))
        records, _ = parse_vllm_bench_serve_json_full(data, meta)
        run = run_from_adapter(
            records[0], metadata=meta, raw_file=data, raw_format="vllm-bench-serve-json/v1"
        )
        sig = run.signature.model_copy(
            update={
                "data_trust": trust,
                "model_revision": "synthetic-revision-1",
                "framework_version": "synthetic-fw-1.0",
            }
        )
        runs.append(run.model_copy(update={"signature": sig, "data_trust": trust}))
    return runs


def _target(input_len: int = 1024, concurrency: int = 8) -> TargetRequirement:
    return TargetRequirement(
        model_name="SYNTHETIC-MODEL-C",
        model_revision="synthetic-revision-1",
        weight_precision="bf16",
        hardware_model="SYNTHETIC-GPU-C",
        gpu_count=4,
        tp_size=4,
        dp_size=1,
        framework="vllm",
        framework_version="synthetic-fw-1.0",
        workload=WorkloadSignature(
            input_len_target=input_len,
            output_len_target=256,
            dataset_name="random",
            arrival_mode="unbounded",
            configured_concurrency=concurrency,
        ),
    )


# ==========================================================================
# M39
# ==========================================================================


def test_m39_clean_measured_group_reports_curve_and_subchecks(root):
    runs = _measured_runs(root)
    validation = validate_site_performance(
        runs=runs,
        target=_target(),
        requirement=SlaRequirement(requirement_id="r", ttft_p99_ms_max=Decimal("99999")),
    )
    assert validation.run_count == 5
    assert len(validation.points) == 5
    assert validation.observed_peak_throughput_tok_s is not None
    assert validation.observed_peak_concurrency is not None
    assert validation.match_level == "exact_match"
    assert validation.sla_status == "observed_pass"
    assert validation.can_claim_sla is True
    assert validation.sla_subchecks
    # 负载与口径对齐情况必须显式给出
    assert validation.load_alignment
    assert validation.statistical_alignment
    assert not validation.synthetic_included


def test_m39_real_fail_is_reported_correctly(root):
    runs = _measured_runs(root)
    validation = validate_site_performance(
        runs=runs,
        target=_target(),
        requirement=SlaRequirement(
            requirement_id="r",
            ttft_p99_ms_max=Decimal("1"),  # 不可能达标
            tpot_p99_ms_max=Decimal("0.001"),
        ),
    )
    assert validation.sla_status == "observed_fail"
    assert validation.can_claim_sla is True
    failing = [c for c in validation.sla_subchecks if c["status"] == "observed_fail"]
    assert failing, validation.sla_subchecks


def test_m39_different_length_cannot_prove_target_sla(root):
    """**核心验收**：同一模型与配置、但输入长度不同的数据不能证明目标 SLA。"""
    runs = _measured_runs(root)  # 曲线是 1024/256
    validation = validate_site_performance(
        runs=runs,
        target=_target(input_len=32768),  # 目标是 32K
        requirement=SlaRequirement(requirement_id="r", ttft_p99_ms_max=Decimal("99999")),
    )
    assert validation.match_level != "exact_match"
    assert validation.can_claim_sla is False
    assert any("exact_match" in r for r in validation.not_evaluable_reasons)
    assert any("输入长度" in line for line in validation.load_alignment)


def test_m39_different_concurrency_cannot_prove_target_sla(root):
    runs = _measured_runs(root, concurrency=8)
    validation = validate_site_performance(
        runs=runs,
        target=_target(concurrency=64),
        requirement=SlaRequirement(requirement_id="r", ttft_p99_ms_max=Decimal("99999")),
    )
    assert validation.match_level != "exact_match"
    assert validation.can_claim_sla is False
    assert any("配置并发" in line for line in validation.load_alignment)


def test_m39_synthetic_data_cannot_substitute_real_evidence(root):
    runs = _measured_runs(root, trust="synthetic_fixture")
    validation = validate_site_performance(
        runs=runs,
        target=_target(),
        requirement=SlaRequirement(requirement_id="r", ttft_p99_ms_max=Decimal("99999")),
    )
    assert validation.synthetic_included is True
    assert validation.sla_status != "observed_pass"
    assert any("synthetic" in r for r in validation.not_evaluable_reasons)


def test_m39_sustainability_requires_repeats_and_duration(root):
    """单次观测不足以认定可持续容量，且必须给出原因。"""
    runs = _measured_runs(root, concurrency=8)  # 只有一个并发点、一条记录
    validation = validate_site_performance(
        runs=runs,
        target=_target(),
        requirement=SlaRequirement(requirement_id="r", ttft_p99_ms_max=Decimal("99999")),
    )
    assert validation.maximum_sustainable_capacity is None
    assert any("重复观测" in r or "可持续" in r for r in validation.sustainability_reasons)


def test_m39_statistical_alignment_flags_server_side(root):
    runs = _measured_runs(root)
    broken = []
    for run in runs:
        profile = run.profile.model_copy(
            update={"metric_side": "server", "metric_definition_ids": []}
        )
        broken.append(run.model_copy(update={"profile": profile}))
    validation = validate_site_performance(
        runs=broken,
        target=_target(),
        requirement=SlaRequirement(requirement_id="r", ttft_p99_ms_max=Decimal("99999")),
    )
    joined = " ".join(validation.statistical_alignment)
    assert "metric_side" in joined
    assert "口径声明" in joined
    assert validation.sla_status != "observed_pass"


def test_m39_service_and_cli(tmp_path, root):
    db = _db(tmp_path, root, name="m39.db")
    import_performance_curve(root / "data/fixtures/perf_curve/INDEX.json", db, root=root)
    dbh, repo = open_db(db)
    try:
        with dbh.session() as session:
            runs = repo.list_benchmark_runs(session)
            fixed = []
            for run in runs:
                sig = run.signature.model_copy(
                    update={
                        "data_trust": "measured_internal",
                        "model_revision": "synthetic-revision-1",
                        "framework_version": "synthetic-fw-1.0",
                    }
                )
                fixed.append(
                    run.model_copy(update={"signature": sig, "data_trust": "measured_internal"})
                )
            repo.upsert_benchmark_runs(session, fixed)
            session.commit()
    finally:
        dbh.dispose()

    facts = {
        "model_name": "SYNTHETIC-MODEL-C",
        "model_revision": "synthetic-revision-1",
        "weight_precision": "bf16",
        "hardware_model": "SYNTHETIC-GPU-C",
        "gpu_count": 4,
        "tp_size": 4,
        "dp_size": 1,
        "framework": "vllm",
        "framework_version": "synthetic-fw-1.0",
        "configured_concurrency": 8,
        "input_len_target": 1024,
        "output_len_target": 256,
        "dataset_name": "random",
        "arrival_mode": "unbounded",
    }
    info = validate_site_performance_for(
        db,
        requirement={"requirement_id": "r", "ttft_p99_ms_max": 99999},
        facts=facts,
    )
    assert info["match_level"] == "exact_match"
    assert info["sla_status"] == "observed_pass"
    assert "只读" in info["notice"]

    out = tmp_path / "site.json"
    result = runner.invoke(
        app,
        [
            "validate-site",
            "--requirement", '{"requirement_id":"r","ttft_p99_ms_max":99999}',
            "--model", "SYNTHETIC-MODEL-C", "--model-revision", "synthetic-revision-1",
            "--precision", "bf16", "--hardware", "SYNTHETIC-GPU-C",
            "--gpu-count", "4", "--tp", "4", "--dp", "1",
            "--framework", "vllm", "--framework-version", "synthetic-fw-1.0",
            "--concurrency", "8", "--input-len", "1024", "--output-len", "256",
            "--dataset", "random", "--arrival-mode", "unbounded",
            "--out", str(out), "--db", db,
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["sla_status"] == "observed_pass"


# ==========================================================================
# M40
# ==========================================================================


def _plan_request(**overrides) -> PlanRequest:
    base = dict(
        request_id="m40",
        model_id="qwen3-32b",
        hardware_ids=["nvidia-h100-sxm-80gb"],
        server_id="hgx-h100-8gpu",
        workload=PlanWorkload(input_len=8192, output_len=256, concurrency=32),
        resources=PlanResourceAssumptions(
            usable_fraction=Decimal("0.9"),
            usable_source_kind="synthetic_example",
            runtime_reserve_fraction=Decimal("0.1"),
        ),
        advanced=PlanAdvanced(tp_sizes=[4], dp_sizes=[1]),
    )
    base.update(overrides)
    return PlanRequest(**base)


def test_m40_summary_exposes_comparable_and_not_comparable_candidates(tmp_path, root):
    db = _db(tmp_path, root, with_cost=True, name="m40.db")
    result = evaluate_plan(
        _plan_request(
            cost=PlanCostInput(
                plan_file="data/examples/phase4_cost_plan.example.yaml",
                template_id="syn-owned-3y",
            )
        ),
        db_path=db,
    )
    comparability = result.summary["cost_comparability"]
    assert "comparable" in comparability
    assert "not_comparable" in comparability
    # 合成价格下所有候选都不完整 -> 全部不可比较，且原因可读
    assert comparability["comparable"] == []
    assert comparability["not_comparable"]
    assert set(comparability["not_comparable"].values()) <= {
        "incomplete_cost", "not_comparable_currency",
        "not_comparable_basis", "not_comparable_horizon",
    }


def test_m40_cheapest_without_performance_evidence_is_not_called_optimal():
    """**核心验收**：缺性能数据的低成本方案不得被称为最优部署。"""
    from llm_capacity_pricing.schemas.plan import PlanCandidate
    from llm_capacity_pricing.workflow import _cost_recommendations

    a = PlanCandidate(
        candidate_id="cheap-no-evidence", hardware_id="h", tp_size=4, dp_size=1,
        gpu_count=4, static_status="conditional_feasible", economics_status="computed",
        currency="CNY", cost_basis="owned", horizon_years=3, cost_incomplete=False,
        total_cash_over_horizon=Decimal("1000000"),
        performance_status="missing_data", sla_status=None,
    )
    b = PlanCandidate(
        candidate_id="dear-measured", hardware_id="h", tp_size=8, dp_size=1,
        gpu_count=8, static_status="conditional_feasible", economics_status="computed",
        currency="CNY", cost_basis="owned", horizon_years=3, cost_incomplete=False,
        total_cash_over_horizon=Decimal("2000000"),
        performance_status="computed", sla_status="observed_pass",
        match_level="exact_match",
    )
    lines = _cost_recommendations([a, b])
    joined = " ".join(lines)
    assert "最低：`cheap-no-evidence`" in joined       # 成本结论照给
    assert "不得**把它称为最优部署" in joined.replace("**不得**", "不得**") or "不得" in joined
    assert "两个独立维度" in joined


def test_m40_measured_cheapest_has_no_warning():
    from llm_capacity_pricing.schemas.plan import PlanCandidate
    from llm_capacity_pricing.workflow import _cost_recommendations

    a = PlanCandidate(
        candidate_id="cheap-measured", hardware_id="h", tp_size=4, dp_size=1,
        gpu_count=4, static_status="conditional_feasible", economics_status="computed",
        currency="CNY", cost_basis="owned", horizon_years=3, cost_incomplete=False,
        total_cash_over_horizon=Decimal("1000000"),
        performance_status="computed", sla_status="observed_pass",
    )
    b = PlanCandidate(
        candidate_id="dear", hardware_id="h", tp_size=8, dp_size=1, gpu_count=8,
        static_status="conditional_feasible", economics_status="computed",
        currency="CNY", cost_basis="owned", horizon_years=3, cost_incomplete=False,
        total_cash_over_horizon=Decimal("2000000"),
        performance_status="computed", sla_status="observed_pass",
    )
    joined = " ".join(_cost_recommendations([a, b]))
    assert "最低：`cheap-measured`" in joined
    assert "不得" not in joined


def test_m40_report_renders_comparability_section(tmp_path, root):
    db = _db(tmp_path, root, with_cost=True, name="m40b.db")
    result = evaluate_plan(
        _plan_request(
            cost=PlanCostInput(
                plan_file="data/examples/phase4_cost_plan.example.yaml",
                template_id="syn-owned-3y",
            )
        ),
        db_path=db,
    )
    md = build_plan_report(result)
    assert "## 3.1 成本可比性" in md
    assert "不可比较候选" in md
    assert "不得直接比较" in md or "不是同一口径" in md or "成本不完整" in md


def test_m40_unconfirmed_price_is_never_a_formal_price(tmp_path, root):
    """允许情景估算，但价格未确认时不得宣称正式价格。"""
    db = _db(tmp_path, root, with_cost=True, name="m40c.db")
    result = evaluate_plan(
        _plan_request(
            cost=PlanCostInput(
                plan_file="data/examples/phase4_cost_plan.example.yaml",
                template_id="syn-owned-3y",
            )
        ),
        db_path=db,
    )
    for candidate in result.candidates:
        assert candidate.cost_incomplete is True
        assert candidate.total_cash_over_horizon is None
    md = build_plan_report(result)
    assert "未授权" in md
    assert "不得用于生产环境的报价自动发布" in md
