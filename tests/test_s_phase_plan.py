"""S1：统一评估入口（PlanRequest / PlanResult / evaluate_plan / plan CLI）。

覆盖计划 §3 的验收：

* 一次调用贯通当前可执行的资源、性能、经济性与方案评估；
* 仅有资源数据时也能输出部分结果，不因缺少成本或实测而整体报错；
* 新入口不能绕开正式 SLA 或报价门禁；
* 原 CLI 行为兼容。
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from llm_capacity_pricing.cli import app
from llm_capacity_pricing.reporting.plan_report import build_plan_report
from llm_capacity_pricing.schemas.plan import (
    PlanAdvanced,
    PlanRequest,
    PlanResult,
    PlanWorkload,
)
from llm_capacity_pricing.services import (
    ServiceError,
    import_cost_catalog,
    import_hardware,
    import_models,
    init_db,
    open_db,
)
from llm_capacity_pricing.workflow import evaluate_plan, load_plan_request

runner = CliRunner()

MINIMAL = "data/examples/scenario_minimal.example.yaml"
FULL = "data/examples/scenario_full.example.yaml"
CATALOG = "data/examples/phase4_cost_inputs.example.yaml"
PLAN_TEMPLATE = "data/examples/phase4_cost_plan.example.yaml"


# --------------------------------------------------------------------------
# 夹具
# --------------------------------------------------------------------------


def _sizing_db(tmp_path: Path, root: Path, name: str = "s1.db") -> str:
    """只有模型与硬件规格的库（最小可用环境）。"""
    db = str(tmp_path / name)
    init_db(db)
    import_models(root / "data/models/model_manifest.yaml", db, root=root)
    import_hardware(root / "data/hardware/hardware_catalog.yaml", db)
    return db


def _workload(**overrides) -> PlanWorkload:
    base = dict(input_len=8192, output_len=256, concurrency=32)
    base.update(overrides)
    return PlanWorkload(**base)


def _request(**overrides) -> PlanRequest:
    from llm_capacity_pricing.schemas.plan import PlanResourceAssumptions

    base = dict(
        request_id="t",
        model_id="qwen3-32b",
        hardware_ids=["nvidia-h100-sxm-80gb"],
        server_id="hgx-h100-8gpu",
        workload=_workload(),
        # 可用显存必须显式给出，否则规划只会返回 insufficient_information
        resources=PlanResourceAssumptions(
            usable_fraction=Decimal("0.9"),
            usable_source_kind="synthetic_example",
            runtime_reserve_fraction=Decimal("0.1"),
        ),
        advanced=PlanAdvanced(tp_sizes=[2, 4, 8], dp_sizes=[1]),
    )
    base.update(overrides)
    return PlanRequest(**base)


# --------------------------------------------------------------------------
# 配置契约
# --------------------------------------------------------------------------


def test_s1_request_requires_only_model_hardware_and_workload():
    """最小必需项就是这三样，其余字段全可空。"""
    request = _request()
    assert request.sla is None
    assert request.cost is None
    assert request.demand is None
    assert request.governance is None


def test_s1_request_rejects_empty_or_duplicate_hardware():
    with pytest.raises(Exception, match="至少给出一个"):
        _request(hardware_ids=[])
    with pytest.raises(Exception, match="不得重复"):
        _request(hardware_ids=["a", "a"])


def test_s1_governance_requires_sla_requirement():
    """给出治理引用却没有 SLA 需求 -> 直接拒绝（无法核验性能承诺）。"""
    from llm_capacity_pricing.schemas.plan import PlanGovernanceInput

    with pytest.raises(Exception, match="必须同时给出 sla"):
        _request(governance=PlanGovernanceInput(sla_evaluation_id="sla-1"))


def test_s1_example_configs_are_valid():
    assert load_plan_request(MINIMAL).model_id == "qwen3-32b"
    full = load_plan_request(FULL)
    assert full.sla is not None
    assert full.cost is not None and full.cost.template_id == "syn-owned-3y"
    assert full.governance is not None


def test_s1_invalid_config_reports_file_name(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("model_id: qwen3-32b\n", encoding="utf-8")
    with pytest.raises(ServiceError, match="校验失败"):
        load_plan_request(bad)


# --------------------------------------------------------------------------
# 只有资源数据时的部分结果
# --------------------------------------------------------------------------


def test_s1_resource_only_yields_partial_without_error(tmp_path, root):
    """**核心验收**：仅有资源数据时正常出部分结果，不整体报错。"""
    db = _sizing_db(tmp_path, root)
    result = evaluate_plan(_request(), db_path=db)

    assert isinstance(result, PlanResult)
    assert result.status == "partial"
    assert result.domains["sizing"] == "computed"
    assert result.domains["performance"] == "not_configured"
    assert result.domains["economics"] == "not_configured"
    assert result.domains["governance"] == "not_configured"
    # 有候选、有静态结论
    assert result.candidates
    assert all(c.static_status for c in result.candidates)
    # 没有"错误"型阻断，只有缺少输入
    assert result.blockers == []
    assert any("sla" in m for m in result.missing_inputs)
    assert any("cost" in m for m in result.missing_inputs)
    # 未配置的领域不得被当成"通过"
    assert all(not c.quote_publishable for c in result.candidates)


def test_s1_missing_model_is_blocked_with_reason(tmp_path, root):
    db = _sizing_db(tmp_path, root)
    result = evaluate_plan(_request(model_id="not-a-model"), db_path=db)
    assert result.status == "blocked"
    assert [b.code for b in result.blockers] == ["model_not_found"]
    assert result.candidates == []


def test_s1_databases_absent_is_blocked(tmp_path):
    result = evaluate_plan(_request(), db_path=str(tmp_path / "missing.db"))
    assert result.status == "blocked"
    # 空库会自动建表，因此这里表现为"模型不存在"
    assert result.blockers[0].code in ("model_not_found", "database_unavailable")


# --------------------------------------------------------------------------
# 成本领域：配置了才计算，缺目录如实标记
# --------------------------------------------------------------------------


def test_s1_cost_configured_but_catalog_empty_is_missing_data(tmp_path, root):
    from llm_capacity_pricing.schemas.plan import PlanCostInput

    db = _sizing_db(tmp_path, root)
    result = evaluate_plan(
        _request(cost=PlanCostInput(plan_file=PLAN_TEMPLATE, template_id="syn-owned-3y")),
        db_path=db,
    )
    assert result.domains["economics"] == "missing_data"
    assert any("import-cost-catalog" in m for m in result.next_data_needed)
    assert result.status == "partial"


def test_s1_cost_computed_with_catalog(tmp_path, root):
    from llm_capacity_pricing.schemas.plan import PlanCostInput

    db = _sizing_db(tmp_path, root)
    import_cost_catalog(CATALOG, db)
    result = evaluate_plan(
        _request(cost=PlanCostInput(plan_file=PLAN_TEMPLATE, template_id="syn-owned-3y")),
        db_path=db,
    )
    assert result.domains["economics"] == "computed"
    by_id = {c.candidate_id: c for c in result.candidates}
    feasible = next(c for c in result.candidates if c.economics_status == "computed")
    assert feasible.plan_id
    assert feasible.capex_one_time is not None
    # 合成价格 -> 成本不完整，因此不给全周期总额
    assert feasible.cost_incomplete is True
    assert feasible.total_cash_over_horizon is None
    assert by_id  # 保持引用，便于将来扩展断言


def test_s1_unknown_cost_template_is_reported_not_raised(tmp_path, root):
    from llm_capacity_pricing.schemas.plan import PlanCostInput

    db = _sizing_db(tmp_path, root)
    import_cost_catalog(CATALOG, db)
    result = evaluate_plan(
        _request(cost=PlanCostInput(plan_file=PLAN_TEMPLATE, template_id="nope")),
        db_path=db,
    )
    assert result.domains["economics"] == "error"
    assert any(b.code == "cost_template_not_found" for b in result.blockers)
    assert result.status == "partial"  # 其它领域照常出结论


# --------------------------------------------------------------------------
# 治理领域：不绕开门禁
# --------------------------------------------------------------------------


def test_s1_governance_without_references_is_never_publishable(tmp_path, root):
    from llm_capacity_pricing.schemas.plan import PlanCostInput, PlanGovernanceInput

    db = _sizing_db(tmp_path, root)
    import_cost_catalog(CATALOG, db)
    result = evaluate_plan(
        _request(
            sla={"requirement_id": "req-1", "ttft_p99_ms_max": 400},  # type: ignore[arg-type]
            cost=PlanCostInput(plan_file=PLAN_TEMPLATE, template_id="syn-owned-3y"),
            governance=PlanGovernanceInput(),
        ),
        db_path=db,
    )
    assert result.domains["governance"] == "error"
    assert any(b.code == "prerequisite_missing" for b in result.blockers)
    assert all(not c.quote_publishable for c in result.candidates)


def test_s1_governance_cannot_bypass_evidence_records(tmp_path, root):
    """给出引用但库里没有证据记录 -> 报一次配置级阻断，绝不静默放行。"""
    from llm_capacity_pricing.schemas.plan import (
        PlanCostInput,
        PlanGovernanceInput,
    )

    db = _sizing_db(tmp_path, root)
    import_cost_catalog(CATALOG, db)
    result = evaluate_plan(
        _request(
            sla={"requirement_id": "req-1", "ttft_p99_ms_max": 400},  # type: ignore[arg-type]
            cost=PlanCostInput(plan_file=PLAN_TEMPLATE, template_id="syn-owned-3y"),
            governance=PlanGovernanceInput(
                sla_evaluation_id="sla-does-not-exist", approval_id="apr-does-not-exist"
            ),
        ),
        db_path=db,
    )
    # 只报一次（不对每个候选重复）
    assert sum(1 for b in result.blockers if b.code == "prerequisite_missing") == 1
    assert all(not c.quote_publishable for c in result.candidates)
    assert result.status == "partial"


def test_s1_result_guard_rejects_publishable_without_governance():
    """契约层再拦一次：治理未计算时不得标记可发布。"""
    from datetime import datetime, timezone

    with pytest.raises(Exception, match="quote_publishable"):
        PlanResult(
            request_id="x",
            status="partial",
            domains={"governance": "not_configured"},
            candidates=[
                {
                    "candidate_id": "c",
                    "hardware_id": "h",
                    "tp_size": 1,
                    "dp_size": 1,
                    "gpu_count": 1,
                    "static_status": "conditional_feasible",
                    "governance_status": "not_configured",
                    "quote_publishable": True,
                }
            ],
            generated_at=datetime.now(timezone.utc),
        )


# --------------------------------------------------------------------------
# 报告
# --------------------------------------------------------------------------


def test_s1_report_distinguishes_data_kinds(tmp_path, root):
    db = _sizing_db(tmp_path, root)
    result = evaluate_plan(_request(), db_path=db)
    md = build_plan_report(result)

    assert "方案评估报告" in md
    # 四个领域分开写
    for label in ("资源规划", "性能与 SLA", "成本与收益", "报价资格"):
        assert label in md
    assert "未配置" in md
    assert "正式报价资格" in md
    # 不出现"综合评分"
    assert "不包含**跨维度综合评分**" in md or "不包含" in md


def test_s1_report_marks_missing_and_confirmed_cost(tmp_path, root):
    from llm_capacity_pricing.schemas.plan import PlanCostInput

    db = _sizing_db(tmp_path, root)
    import_cost_catalog(CATALOG, db)
    result = evaluate_plan(
        _request(cost=PlanCostInput(plan_file=PLAN_TEMPLATE, template_id="syn-owned-3y")),
        db_path=db,
    )
    md = build_plan_report(result)
    assert any(c.economics_status == "computed" for c in result.candidates), [
        (c.candidate_id, c.economics_status) for c in result.candidates
    ]
    assert "不完整（含未确认/缺失项）" in md


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def test_s1_cli_plan_minimal(tmp_path, root):
    db = _sizing_db(tmp_path, root)
    out = tmp_path / "plan.md"
    json_out = tmp_path / "plan.json"
    result = runner.invoke(
        app,
        [
            "plan",
            "--config", MINIMAL,
            "--out", str(out),
            "--json-out", str(json_out),
            "--db", db,
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert out.exists() and "方案评估报告" in out.read_text(encoding="utf-8")
    payload = json.loads(json_out.read_text(encoding="utf-8"))
    assert payload["status"] == "partial"
    assert payload["domains"]["sizing"] == "computed"
    assert payload["summary"]["publishable"] == []


def test_s1_cli_plan_full_without_governance_data(tmp_path, root):
    """配置里引用了不存在的证据 -> 明确报阻断，退出码仍为 0（partial 不是失败）。"""
    db = _sizing_db(tmp_path, root)
    import_cost_catalog(CATALOG, db)
    result = runner.invoke(app, ["plan", "--config", FULL, "--db", db])
    assert result.exit_code == 0, result.stdout
    assert "可发布正式报价的候选: 无" in result.stdout


def test_s1_cli_plan_bad_config_fails_loudly(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("model_id: qwen3-32b\n", encoding="utf-8")
    result = runner.invoke(app, ["plan", "--config", str(bad), "--db", str(tmp_path / "x.db")])
    assert result.exit_code != 0
    assert "校验失败" in result.output


def test_s1_existing_cli_commands_still_present():
    """S1 只允许新增命令，不得删除既有命令。"""
    result = runner.invoke(app, ["--help"])
    for name in (
        "init-db", "import-excel", "calculate-all", "scenario", "report",
        "import-models", "import-hardware", "plan-deployment", "estimate-memory",
        "import-performance", "match-benchmark", "capacity-profile",
        "calibrate-performance", "evaluate-sla", "rank-deployments",
        "performance-report", "import-cost-catalog", "cost-catalog", "tco",
        "revenue-scenario", "record-sla-evidence", "import-quote-approvals",
        "import-commercial-confirmations", "quote-eligibility",
        "phase4-decision", "phase4-report",
    ):
        assert name in result.stdout, name
    assert "plan" in result.stdout


# --------------------------------------------------------------------------
# 新旧入口一致性（S6 会扩展为完整对照）
# --------------------------------------------------------------------------


def test_s1_plan_matches_underlying_sizing_and_ranking(tmp_path, root):
    """同一数据库快照下，`plan` 的静态与性能结论必须与底层引擎一致。"""
    from llm_capacity_pricing.perf.ranker import rank_deployments
    from llm_capacity_pricing.schemas.performance import SlaPolicy, SlaRequirement
    from llm_capacity_pricing.services import make_assumptions, plan_deployment_for

    db = _sizing_db(tmp_path, root)
    request = _request(sla={"requirement_id": "req-1", "ttft_p99_ms_max": 400})  # type: ignore[arg-type]
    result = evaluate_plan(request, db_path=db)

    assumptions = make_assumptions(
        usable_fraction_of_nominal=request.resources.usable_fraction,
        usable_memory_source_kind=request.resources.usable_source_kind,
        runtime_reserve_fraction=request.resources.runtime_reserve_fraction,
        global_concurrency=request.workload.concurrency,
        context_length_per_request=request.workload.input_len,
        tp_sizes=tuple(request.advanced.tp_sizes),
        dp_sizes=tuple(request.advanced.dp_sizes),
    )
    plan = plan_deployment_for(
        db,
        model_id=request.model_id,
        hardware_id=request.hardware_ids[0],
        server_id=request.server_id,
        assumptions=assumptions,
    )
    by_id = {c.candidate_id: c for c in result.candidates}
    for candidate in plan["candidates"]:
        entry = by_id[candidate.candidate_id]
        assert entry.gpu_count == candidate.gpu_count
        assert entry.tp_size == candidate.tp_size
        assert entry.dp_size == candidate.dp_size
        assert entry.static_status == candidate.feasibility.status

    # 性能结论与直接调用排序器一致（没有实测 -> 无匹配证据）
    dbh, repo = open_db(db)
    try:
        with dbh.session() as session:
            runs = repo.list_benchmark_runs(session)
            models = {m.model_id: m for m in repo.list_model_specs(session)}
    finally:
        dbh.dispose()
    from llm_capacity_pricing.schemas.performance import WorkloadSignature

    ranking = rank_deployments(
        requirement_id="req-1",
        candidates=plan["candidates"],
        runs=runs,
        sla_requirement=SlaRequirement(requirement_id="req-1", ttft_p99_ms_max=Decimal("400")),
        policy=SlaPolicy(),
        workload=WorkloadSignature(
            input_len_target=request.workload.input_len,
            output_len_target=request.workload.output_len,
            configured_concurrency=request.workload.concurrency,
        ),
        model_by_id=models,
        target_identity={},
    )
    for entry in ranking.entries:
        assert by_id[entry.candidate_id].rank_bucket == entry.rank_bucket
        assert by_id[entry.candidate_id].sla_status == entry.sla_status


# ==========================================================================
# S2：CLI / Agent / 报告复用同一个应用服务
# ==========================================================================


def test_s2_cli_and_agent_share_the_same_application_service(tmp_path, root):
    """同一数据库快照 + 同一配置 -> CLI 与 Agent 结果逐字段一致。"""
    from llm_capacity_pricing.agent_api import call_agent_op

    db = _sizing_db(tmp_path, root)
    import_cost_catalog(CATALOG, db)

    out = tmp_path / "cli.json"
    cli_result = runner.invoke(
        app, ["plan", "--config", FULL, "--json-out", str(out), "--db", db]
    )
    assert cli_result.exit_code == 0, cli_result.stdout
    cli_payload = json.loads(out.read_text(encoding="utf-8"))

    agent_payload = call_agent_op("plan.evaluate_config", db=db, config=FULL)

    assert agent_payload["status"] == cli_payload["status"]
    assert agent_payload["domains"] == cli_payload["domains"]
    assert agent_payload["summary"] == cli_payload["summary"]
    assert [c["candidate_id"] for c in agent_payload["candidates"]] == [
        c["candidate_id"] for c in cli_payload["candidates"]
    ]
    assert [c["quote_publishable"] for c in agent_payload["candidates"]] == [
        c["quote_publishable"] for c in cli_payload["candidates"]
    ]


def test_s2_agent_plan_op_accepts_request_object(tmp_path, root):
    from llm_capacity_pricing.agent_api import call_agent_op

    db = _sizing_db(tmp_path, root)
    payload = call_agent_op(
        "plan.evaluate",
        db=db,
        request={
            "request_id": "agent",
            "model_id": "qwen3-32b",
            "hardware_ids": ["nvidia-h100-sxm-80gb"],
            "server_id": "hgx-h100-8gpu",
            "workload": {"input_len": 8192, "output_len": 256, "concurrency": 32},
            "resources": {
                "usable_fraction": "0.9",
                "usable_source_kind": "synthetic_example",
                "runtime_reserve_fraction": "0.1",
            },
            "advanced": {"tp_sizes": [4], "dp_sizes": [1]},
        },
    )
    assert payload["request_id"] == "agent"
    assert payload["domains"]["sizing"] == "computed"
    # 与 CLI 一样：未配置治理时绝不出现可发布候选
    assert payload["summary"]["publishable"] == []


def test_s2_agent_plan_op_rejects_unknown_params(tmp_path):
    from llm_capacity_pricing.agent_api import call_agent_op

    with pytest.raises(ServiceError, match="不接受参数"):
        call_agent_op("plan.evaluate", db=str(tmp_path / "x.db"), config="x", extra=1)


def test_s2_services_decision_is_a_thin_compatibility_wrapper(tmp_path, root):
    """`services.phase4_decision_for` 仍可用，且与应用层结果一致。"""
    from llm_capacity_pricing.services import make_assumptions
    from llm_capacity_pricing.workflow import evaluate_decision

    db = _sizing_db(tmp_path, root)
    import_cost_catalog(CATALOG, db)
    requirement = {"requirement_id": "req-1", "ttft_p99_ms_max": 400}
    assumptions = make_assumptions(
        usable_fraction_of_nominal=Decimal("0.9"),
        usable_memory_source_kind="synthetic_example",
        runtime_reserve_fraction=Decimal("0.1"),
        global_concurrency=32,
        context_length_per_request=8192,
        tp_sizes=(2, 4, 8),
        dp_sizes=(1,),
    )
    kwargs = dict(
        requirement=requirement,
        model_id="qwen3-32b",
        hardware_id="nvidia-h100-sxm-80gb",
        server_id="hgx-h100-8gpu",
        plan_file=PLAN_TEMPLATE,
        template_id="syn-owned-3y",
        assumptions=assumptions,
    )
    from llm_capacity_pricing.services import phase4_decision_for

    legacy = phase4_decision_for(db, **kwargs)
    modern = evaluate_decision(db, **kwargs)
    assert legacy["per_candidate"] == modern["per_candidate"]
    assert legacy["comparison"] == modern["comparison"]


def test_s2_no_module_level_circular_dependency():
    """services 与 workflow 之间不得出现模块级循环依赖。"""
    import ast
    from pathlib import Path as _P

    root = _P("src/llm_capacity_pricing")

    def top_level_imports(name: str) -> list[str]:
        tree = ast.parse((root / f"{name}.py").read_text(encoding="utf-8"))
        out = []
        for node in tree.body:
            if isinstance(node, ast.ImportFrom) and node.module:
                out.append(node.module)
            elif isinstance(node, ast.Import):
                out.extend(a.name for a in node.names)
        return out

    assert not [m for m in top_level_imports("services") if m.endswith(".workflow")]
    assert any(m.endswith(".services") for m in top_level_imports("workflow"))


def test_s2_reports_do_not_reimplement_business_rules(tmp_path, root):
    """报告层只格式化：不得在报告模块里出现门禁/匹配判定逻辑。"""
    from pathlib import Path as _P

    for name in ("plan_report.py", "tco_report.py", "performance_report.py"):
        src = (_P("src/llm_capacity_pricing/reporting") / name).read_text(encoding="utf-8")
        for forbidden in ("evaluate_sla(", "match(", "compute_tco(", "evaluate_quote_eligibility("):
            assert forbidden not in src, f"{name} 不应直接调用 {forbidden}"
