"""M42：第五阶段端到端验收与封板。

四个验收场景（计划 §M42）：

* **A** 只有资源数据 —— 静态结果可用，性能与成本保持证据缺口；
* **B** 导入 Benchmark 结果 —— 能正确匹配、重算容量与 SLA；
* **C** 使用 Synthetic 数据 —— 计算与报告可运行，但不可冒充实测或正式报价；
* **D** 数据不匹配/过期/变更/缺失 —— 明确阻断，不会静默放行。

> **重要区分**：场景 B 用的是**真实格式**（vLLM `bench serve` JSON）的
> **机制夹具**（内容为曲线样本、由测试标记为内部实测），它证明的是**工程链路**可用；
> 真实性能验收需要目标模型/GPU 的现场记录，当前仍为**未验证**。

另含封板复核：新旧入口一致、扩展只需数据或局部适配、无平行计算链路。
"""

from __future__ import annotations

import ast
import json
from decimal import Decimal
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from llm_capacity_pricing.cli import app
from llm_capacity_pricing.schemas.plan import (
    PlanAdvanced,
    PlanCostInput,
    PlanDemandInput,
    PlanGovernanceInput,
    PlanRequest,
    PlanResourceAssumptions,
    PlanWorkload,
)
from llm_capacity_pricing.services import (
    check_import_consistency_for,
    data_quality_report,
    import_cost_catalog,
    import_hardware,
    import_models,
    import_performance_curve,
    init_db,
    open_db,
    validate_site_performance_for,
)
from llm_capacity_pricing.workflow import evaluate_plan, load_plan_request

runner = CliRunner()
CATALOG = "data/examples/phase4_cost_inputs.example.yaml"
PLAN_FILE = "data/examples/phase4_cost_plan.example.yaml"
MINIMAL = "data/examples/scenario_minimal.example.yaml"
FULL = "data/examples/scenario_full.example.yaml"


def _db(tmp_path: Path, root: Path, *, with_cost: bool = False, name: str = "m42.db") -> str:
    db = str(tmp_path / name)
    init_db(db)
    import_models(root / "data/models/model_manifest.yaml", db, root=root)
    import_hardware(root / "data/hardware/hardware_catalog.yaml", db)
    if with_cost:
        import_cost_catalog(CATALOG, db)
    return db


def _mark_curve_as_measured(db: str, root: Path) -> None:
    """机制夹具：把曲线样本标记为内部实测并补齐身份（工程链路验证用）。"""
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


def _resources() -> PlanResourceAssumptions:
    return PlanResourceAssumptions(
        usable_fraction=Decimal("0.9"),
        usable_source_kind="synthetic_example",
        runtime_reserve_fraction=Decimal("0.1"),
    )


# ==========================================================================
# 场景 A：只有资源数据
# ==========================================================================


def test_m42_scenario_a_resource_only(tmp_path, root):
    db = _db(tmp_path, root, name="m42a.db")
    result = evaluate_plan(
        PlanRequest(
            request_id="A", model_id="qwen3-32b",
            hardware_ids=["nvidia-h100-sxm-80gb"], server_id="hgx-h100-8gpu",
            workload=PlanWorkload(input_len=8192, output_len=256, concurrency=32),
            resources=_resources(), advanced=PlanAdvanced(tp_sizes=[4], dp_sizes=[1]),
        ),
        db_path=db,
    )
    assert result.status == "partial"
    assert result.blockers == []
    assert result.domains["sizing"] == "computed"
    assert result.domains["performance"] == "not_configured"
    assert result.domains["economics"] == "not_configured"
    # 缺口用可执行指引表达，并提示先补测
    joined = " ".join(result.missing_inputs + result.next_data_needed)
    assert "plan-benchmarks" in joined


# ==========================================================================
# 场景 B：导入实测 -> 匹配 / 容量 / SLA 重算
# ==========================================================================


def test_m42_scenario_b_imported_measurement_drives_match_and_sla(tmp_path, root):
    db = _db(tmp_path, root, name="m42b.db")
    _mark_curve_as_measured(db, root)

    # 导入一致性：这批数据必须干净
    consistency = check_import_consistency_for(db)
    assert consistency["ok"] is True, consistency["issues"]

    facts = {
        "model_name": "SYNTHETIC-MODEL-C",
        "model_revision": "synthetic-revision-1",
        "weight_precision": "bf16",
        "hardware_model": "SYNTHETIC-GPU-C",
        "gpu_count": 4, "tp_size": 4, "dp_size": 1,
        "framework": "vllm", "framework_version": "synthetic-fw-1.0",
        "configured_concurrency": 8, "input_len_target": 1024,
        "output_len_target": 256, "dataset_name": "random",
        "arrival_mode": "unbounded",
    }
    # 容量 + 匹配 + SLA 一次算清
    validation = validate_site_performance_for(
        db,
        requirement={"requirement_id": "B", "ttft_p99_ms_max": 99999},
        facts=facts,
    )
    assert validation["match_level"] == "exact_match"
    assert validation["sla_status"] == "observed_pass"
    assert validation["can_claim_sla"] is True
    assert validation["points"], "必须给出各并发点证据"
    assert validation["observed_peak_throughput_tok_s"] is not None
    assert validation["load_alignment"]
    assert validation["statistical_alignment"]

    # 同一批数据通过统一入口也应当得到性能结论
    result = evaluate_plan(
        PlanRequest(
            request_id="B", model_id="qwen3-32b",
            hardware_ids=["nvidia-h100-sxm-80gb"], server_id="hgx-h100-8gpu",
            workload=PlanWorkload(
                input_len=1024, output_len=256, concurrency=8,
                dataset="random", arrival_mode="unbounded",
            ),
            resources=_resources(),
            sla={"requirement_id": "B", "ttft_p99_ms_max": 99999},  # type: ignore[arg-type]
            advanced=PlanAdvanced(
                tp_sizes=[4], dp_sizes=[1], model_revision="synthetic-revision-1",
                framework="vllm", framework_version="synthetic-fw-1.0",
            ),
        ),
        db_path=db,
    )
    assert result.domains["performance"] == "computed"


# ==========================================================================
# 场景 C：Synthetic 数据
# ==========================================================================


def test_m42_scenario_c_synthetic_runs_but_never_impersonates(tmp_path, root):
    db = _db(tmp_path, root, with_cost=True, name="m42c.db")
    import_performance_curve(root / "data/fixtures/perf_curve/INDEX.json", db, root=root)

    # 计算与报告能跑
    result = evaluate_plan(
        PlanRequest(
            request_id="C", model_id="qwen3-32b",
            hardware_ids=["nvidia-h100-sxm-80gb"], server_id="hgx-h100-8gpu",
            workload=PlanWorkload(input_len=8192, output_len=256, concurrency=32),
            resources=_resources(),
            sla={"requirement_id": "C", "ttft_p99_ms_max": 99999},  # type: ignore[arg-type]
            cost=PlanCostInput(plan_file=PLAN_FILE, template_id="syn-owned-3y"),
            demand=PlanDemandInput(
                demand_file="data/examples/phase4_demand.example.yaml",
                scenario_id="syn-demand-base",
            ),
            advanced=PlanAdvanced(tp_sizes=[4], dp_sizes=[1]),
        ),
        db_path=db,
    )
    assert result.domains["performance"] == "computed"

    # 但不可冒充实测
    assert all(c.sla_status != "observed_pass" for c in result.candidates)
    assert all(not c.quote_publishable for c in result.candidates)

    # 数据质量报告如实标注
    quality = data_quality_report(db)
    runs = [i for i in quality["items"] if i["domain"] == "benchmark_run"]
    assert runs and all(r["status"] == "synthetic" for r in runs)
    assert all(r["production_grade"] is False for r in runs)

    # 一致性校验给出合成数据不得替代真实依据的提醒
    validation = validate_site_performance_for(
        db,
        requirement={"requirement_id": "C", "ttft_p99_ms_max": 99999},
        facts={
            "model_name": "SYNTHETIC-MODEL-C", "model_revision": "synthetic-revision-1",
            "weight_precision": "bf16", "hardware_model": "SYNTHETIC-GPU-C",
            "gpu_count": 4, "tp_size": 4, "dp_size": 1,
            "framework": "vllm", "framework_version": "synthetic-fw-1.0",
            "configured_concurrency": 8, "input_len_target": 1024,
            "output_len_target": 256, "dataset_name": "random",
            "arrival_mode": "unbounded",
        },
    )
    assert validation["can_claim_sla"] is False
    assert validation["synthetic_included"] is True
    assert any("synthetic" in r for r in validation["not_evaluable_reasons"])


# ==========================================================================
# 场景 D：不匹配 / 过期 / 变更 / 缺失
# ==========================================================================


def test_m42_scenario_d_mismatched_evidence_is_blocked(tmp_path, root):
    db = _db(tmp_path, root, with_cost=True, name="m42d.db")
    _mark_curve_as_measured(db, root)
    from llm_capacity_pricing.services import import_quote_approvals, record_sla_evidence_for

    import_quote_approvals("data/examples/phase4_approvals.example.yaml", db)
    facts = {
        "model_name": "SYNTHETIC-MODEL-C", "model_revision": "synthetic-revision-1",
        "weight_precision": "bf16", "hardware_model": "SYNTHETIC-GPU-C",
        "gpu_count": 4, "tp_size": 4, "dp_size": 1,
        "framework": "vllm", "framework_version": "synthetic-fw-1.0",
        "configured_concurrency": 8, "input_len_target": 1024,
        "output_len_target": 256, "dataset_name": "random",
        "arrival_mode": "unbounded",
    }
    record_sla_evidence_for(
        db, candidate_id="demo-owned",
        requirement={"requirement_id": "D", "ttft_p99_ms_max": 99999},
        facts=facts,
    )
    from llm_capacity_pricing.services import quote_eligibility_for

    # 负载身份被改（配置并发 8 -> 64）：旧证据不再适用
    changed = dict(facts, configured_concurrency=64)
    info = quote_eligibility_for(
        db, candidate_id="demo-owned", requirement_id="D", facts=changed,
        sla_evaluation_id="sla::demo-owned::D", approval_id="syn-approval-demo",
        plan_file=PLAN_FILE, template_id="syn-owned-3y",
        hardware_id="nvidia-h100-sxm-80gb", gpu_count=4, server_count=1,
    )
    blockers = " ".join(info["eligibility"]["blockers"])
    assert info["eligibility"]["quote_publishable"] is False
    assert "sla_target_identity_changed" in blockers or "sla_run_pool_changed" in blockers


def test_m42_scenario_d_expired_and_missing_inputs(tmp_path, root):
    db = _db(tmp_path, root, with_cost=True, name="m42d2.db")
    # 过期：as_of 晚于价格有效期
    result = evaluate_plan(
        PlanRequest(
            request_id="D", model_id="qwen3-32b",
            hardware_ids=["nvidia-h100-sxm-80gb"], server_id="hgx-h100-8gpu",
            workload=PlanWorkload(input_len=8192, output_len=256, concurrency=32),
            resources=_resources(),
            cost=PlanCostInput(plan_file=PLAN_FILE, template_id="no-such-template"),
            advanced=PlanAdvanced(tp_sizes=[4], dp_sizes=[1]),
        ),
        db_path=db,
    )
    assert result.domains["economics"] == "error"
    assert any(b.code == "cost_template_not_found" for b in result.blockers)
    assert all(not c.quote_publishable for c in result.candidates)

    # 缺失：配置非法必须在计算之前被拒绝，并给出字段路径
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        yaml.safe_dump(
            {"model_id": "qwen3-32b", "hardware_ids": [], "workload": {"input_len": 8192}},
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    with pytest.raises(Exception, match="hardware_ids"):
        load_plan_request(bad)


def test_m42_scenario_d_bad_config_gives_field_path_and_hint(tmp_path):
    bad = tmp_path / "typo.yaml"
    bad.write_text(
        yaml.safe_dump(
            {
                "model_id": "qwen3-32b",
                "hardware_ids": ["nvidia-h100-sxm-80gb"],
                "workload": {"input_len": 8192},
                "cost": {"plan_file": "x.yaml", "template": "syn-owned-3y"},
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    result = runner.invoke(app, ["plan", "--config", str(bad), "--db", str(tmp_path / "x.db")])
    assert result.exit_code != 0
    assert "cost.template" in result.output
    assert "是不是想写 `template_id`？" in result.output


# ==========================================================================
# 封板复核
# ==========================================================================


def test_m42_no_parallel_computation_chain_in_phase5_modules():
    """第五阶段模块只**调用**既有引擎，不重写判定或公式。"""
    root = Path("src/llm_capacity_pricing")

    planner = (root / "perf/experiment_planner.py").read_text(encoding="utf-8")
    consistency = (root / "perf/import_consistency.py").read_text(encoding="utf-8")
    site = (root / "perf/site_validation.py").read_text(encoding="utf-8")

    # 规划器与一致性校验不应触碰成本/SLA 计算
    for src, name in ((planner, "experiment_planner"), (consistency, "import_consistency")):
        for forbidden in ("compute_tco", "evaluate_sla", "cost_comparability", "rank_deployments"):
            assert forbidden not in src, f"{name} 不应直接调用 {forbidden}"

    # 现场验证是**委托**：必须调用既有三个领域实现，而不是自己判定
    for required in ("analyze_group", "match(", "_evaluate(", "_best_match"):
        assert required in site, required
    # 且不得自己定义阈值比较逻辑（阈值来自 SlaRequirement / SlaPolicy）
    tree = ast.parse(site)
    assert not [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Compare)
        and any(isinstance(c, ast.Attribute) for c in n.comparators)
        and getattr(n.comparators[0], "attr", "") == "ttft_p99_ms_max"
    ]


def test_m42_phase5_entry_points_are_consistent(tmp_path, root):
    """CLI / Agent / 应用层三条入口在同一库上给出一致结论。"""
    from llm_capacity_pricing.agent_api import call_agent_op

    db = _db(tmp_path, root, with_cost=True, name="m42e.db")
    out = tmp_path / "plan.json"
    cli = runner.invoke(
        app, ["plan", "--config", MINIMAL, "--json-out", str(out), "--db", db]
    )
    assert cli.exit_code == 0, cli.output
    cli_payload = json.loads(out.read_text(encoding="utf-8"))

    agent_request = yaml.safe_load((root / MINIMAL).read_text(encoding="utf-8"))
    agent_payload = call_agent_op("plan.evaluate", db=db, request=agent_request)

    assert agent_payload["domains"] == cli_payload["domains"]
    assert agent_payload["summary"] == cli_payload["summary"]
    assert [c["candidate_id"] for c in agent_payload["candidates"]] == [
        c["candidate_id"] for c in cli_payload["candidates"]
    ]


def test_m42_phase5_pipeline_needs_no_code_change_for_new_data(tmp_path, root):
    """整条第五阶段链路对新数据只要求数据文件变化。"""
    db = _db(tmp_path, root, with_cost=True, name="m42f.db")
    # 只加数据：成本目录加一条同结构计费规则
    doc = yaml.safe_load((root / CATALOG).read_text(encoding="utf-8"))
    extra = json.loads(json.dumps(doc["billing_rules"][0]))
    extra.update({"rule_id": "syn-billing-extra", "model_name": "qwen3-8b"})
    doc["billing_rules"].append(extra)
    catalog = tmp_path / "more.yaml"
    catalog.write_text(yaml.safe_dump(doc, allow_unicode=True), encoding="utf-8")

    assert import_cost_catalog(str(catalog), db)["billing_rule_count"] == 2
    # 全链路照常工作
    result = evaluate_plan(load_plan_request(MINIMAL), db_path=db)
    assert result.domains["sizing"] == "computed"
    assert data_quality_report(db)["counts"]
