"""S6：四个端到端验收场景 + 新旧入口对照。

计划 §8 要求建立四个端到端验收场景：

* **A** 只有模型与 GPU 信息 —— 正常输出静态规划，性能与经济性保留缺口；
* **B** 有匹配性能实测但无已确认价格 —— 能评价性能，**不得**生成正式报价；
* **C** 有完整合成样本 —— 四领域贯通，但 synthetic **绝不**升级为生产 SLA；
* **D** 输入错误或证据不匹配 —— 返回清楚的错误/阻断原因，无静默降级。

外加"新旧入口在同一输入、同一数据库快照下的核心业务结果一致"。
"""

from __future__ import annotations

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
    import_commercial_confirmations,
    import_cost_catalog,
    import_hardware,
    import_models,
    import_performance_curve,
    init_db,
    make_assumptions,
    open_db,
    record_sla_evidence_for,
)
from llm_capacity_pricing.workflow import evaluate_plan

runner = CliRunner()
CATALOG = "data/examples/phase4_cost_inputs.example.yaml"
PLAN_TEMPLATE = "data/examples/phase4_cost_plan.example.yaml"
DEMAND = "data/examples/phase4_demand.example.yaml"


# --------------------------------------------------------------------------
# 公共夹具
# --------------------------------------------------------------------------


def _db(tmp_path: Path, root: Path, *, with_cost: bool = False, name: str = "s6.db") -> str:
    db = str(tmp_path / name)
    init_db(db)
    import_models(root / "data/models/model_manifest.yaml", db, root=root)
    import_hardware(root / "data/hardware/hardware_catalog.yaml", db)
    if with_cost:
        import_cost_catalog(CATALOG, db)
    return db


def _resources() -> PlanResourceAssumptions:
    return PlanResourceAssumptions(
        usable_fraction=Decimal("0.9"),
        usable_source_kind="synthetic_example",
        runtime_reserve_fraction=Decimal("0.1"),
    )


def _request(**overrides) -> PlanRequest:
    base = dict(
        request_id="s6",
        model_id="qwen3-32b",
        hardware_ids=["nvidia-h100-sxm-80gb"],
        server_id="hgx-h100-8gpu",
        workload=PlanWorkload(input_len=8192, output_len=256, concurrency=32),
        resources=_resources(),
        advanced=PlanAdvanced(tp_sizes=[2, 4, 8], dp_sizes=[1]),
    )
    base.update(overrides)
    return PlanRequest(**base)



def _insert_measured_run(db: str, root: Path) -> str:
    """**机制夹具**：插入一条身份已对齐到本部署、并标记为内部实测的曲线样本。

    曲线样本本身是 synthetic，不足以支撑生产 SLA；这里只为让"性能达标"
    这条分支可走通（与第三阶段 `measured_runs` 夹具同一做法）。
    返回 run_id。
    """
    from llm_capacity_pricing.importers.vllm_adapter import parse_vllm_bench_serve_json_full
    from llm_capacity_pricing.perf.performance_profile import run_from_adapter

    folder = root / "data" / "fixtures" / "perf_curve"
    index = json.loads((folder / "INDEX.json").read_text(encoding="utf-8"))
    point = next(p for p in index["points"] if p["file"].endswith("c8.json"))
    data = folder / point["file"]
    meta = yaml.safe_load((folder / point["metadata"]).read_text(encoding="utf-8"))
    records, _ = parse_vllm_bench_serve_json_full(data, meta)
    run = run_from_adapter(
        records[0], metadata=meta, raw_file=data, raw_format="vllm-bench-serve-json/v1"
    )
    sig = run.signature.model_copy(
        update={
            "data_trust": "measured_internal",
            "model_name": "Qwen/Qwen3-32B",
            "model_revision": "synthetic-revision-1",
            "hardware_model": "NVIDIA H100 SXM",
            "tp_size": 4,
            "dp_size": 1,
            "gpu_count": 4,
            "framework": "vllm",
            "framework_version": "synthetic-fw-1.0",
        }
    )
    run = run.model_copy(update={"signature": sig, "data_trust": "measured_internal"})
    dbh, repo = open_db(db)
    try:
        with dbh.session() as session:
            repo.upsert_benchmark_runs(session, [run])
            session.commit()
    finally:
        dbh.dispose()
    return run.run_id


#: 与 `_insert_measured_run` 对齐的目标身份
ALIGNED_FACTS = {
    "model_name": "Qwen/Qwen3-32B",
    "model_revision": "synthetic-revision-1",
    "weight_precision": "bf16",
    "hardware_model": "NVIDIA H100 SXM",
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


# ==========================================================================
# 场景 A：只有模型与 GPU 信息
# ==========================================================================


def test_s6_scenario_a_model_and_gpu_only(tmp_path, root):
    """A：正常输出静态规划，性能与经济性保留缺口，且不报错。"""
    db = _db(tmp_path, root)
    result = evaluate_plan(_request(), db_path=db)

    assert result.status == "partial"          # 不是 blocked，也不是 ok
    assert result.blockers == []               # 没有错误，只有缺口
    assert result.domains["sizing"] == "computed"
    assert result.candidates and all(c.static_status for c in result.candidates)

    # 性能与经济性明确标为缺口，而不是 0 或默认值
    assert result.domains["performance"] == "not_configured"
    assert result.domains["economics"] == "not_configured"
    assert all(c.performance_status == "not_configured" for c in result.candidates)
    assert all(c.total_cash_over_horizon is None for c in result.candidates)
    assert all(c.cost_incomplete for c in result.candidates)

    # 缺口要写清楚下一步要什么
    joined = " ".join(result.missing_inputs + result.next_data_needed)
    assert "sla" in joined and "cost" in joined


# ==========================================================================
# 场景 B：有匹配性能实测但无已确认价格
# ==========================================================================


def test_s6_scenario_b_measured_performance_without_confirmed_price(tmp_path, root):
    """B：能评价性能，但不得生成正式报价。"""
    db = _db(tmp_path, root, with_cost=True, name="s6b.db")
    import_performance_curve(root / "data/fixtures/perf_curve/INDEX.json", db, root=root)

    _insert_measured_run(db, root)

    request = _request(
        request_id="s6-b",
        workload=PlanWorkload(
            input_len=1024,
            output_len=256,
            concurrency=8,
            dataset="random",
            arrival_mode="unbounded",
        ),
        sla={"requirement_id": "req-b", "ttft_p99_ms_max": 99999},  # type: ignore[arg-type]
        cost=PlanCostInput(plan_file=PLAN_TEMPLATE, template_id="syn-owned-3y"),
        advanced=PlanAdvanced(
            tp_sizes=[4],
            dp_sizes=[1],
            model_revision="synthetic-revision-1",
            framework="vllm",
            framework_version="synthetic-fw-1.0",
        ),
    )
    result = evaluate_plan(request, db_path=db)

    assert result.domains["performance"] == "computed"
    matched = [c for c in result.candidates if c.sla_status == "observed_pass"]
    assert matched, [(c.candidate_id, c.sla_status) for c in result.candidates]
    assert matched[0].match_level == "exact_match"

    # 价格是合成示例：成本算得出但不完整，且**不得**生成正式报价
    assert result.domains["economics"] == "computed"
    assert any(c.cost_incomplete for c in result.candidates)
    assert not any(c.quote_publishable for c in result.candidates)
    # 未提供治理引用时，报价资格领域根本没被评估
    assert result.domains["governance"] == "not_configured"


def test_s6_scenario_b_governance_reference_does_not_bypass_price_gap(tmp_path, root):
    """B 加强版：即便带上证据与审批引用，缺已确认价格仍然不可发布。"""
    db = _db(tmp_path, root, with_cost=True, name="s6b2.db")
    import_performance_curve(root / "data/fixtures/perf_curve/INDEX.json", db, root=root)
    import_commercial_confirmations(
        "data/examples/phase4_commercial_confirmation.example.yaml", db
    )
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
    record_sla_evidence_for(
        db,
        candidate_id="demo-owned",
        requirement={"requirement_id": "req-b", "ttft_p99_ms_max": 99999},
        facts=facts,
    )
    request = _request(
        request_id="s6-b2",
        sla={"requirement_id": "req-b", "ttft_p99_ms_max": 99999},  # type: ignore[arg-type]
        cost=PlanCostInput(plan_file=PLAN_TEMPLATE, template_id="syn-owned-3y"),
        governance=PlanGovernanceInput(
            sla_evaluation_id="sla::demo-owned::req-b", approval_id="apr-does-not-exist"
        ),
    )
    result = evaluate_plan(request, db_path=db)
    assert not any(c.quote_publishable for c in result.candidates)
    assert result.domains["governance"] == "error"


# ==========================================================================
# 场景 C：完整合成样本
# ==========================================================================


def test_s6_scenario_c_synthetic_never_becomes_production_sla(tmp_path, root):
    """C：四领域贯通，但 synthetic 样本绝不升级为生产 SLA。"""
    db = _db(tmp_path, root, with_cost=True, name="s6c.db")
    import_performance_curve(root / "data/fixtures/perf_curve/INDEX.json", db, root=root)
    import_commercial_confirmations(
        "data/examples/phase4_commercial_confirmation.example.yaml", db
    )

    request = _request(
        request_id="s6-c",
        sla={"requirement_id": "req-c", "ttft_p99_ms_max": 99999},  # type: ignore[arg-type]
        cost=PlanCostInput(plan_file=PLAN_TEMPLATE, template_id="syn-owned-3y"),
        demand=PlanDemandInput(demand_file=DEMAND, scenario_id="syn-demand-base"),
    )
    result = evaluate_plan(request, db_path=db)

    # 四领域都有结论（合成样本足以跑通链路）
    assert result.domains["sizing"] == "computed"
    assert result.domains["performance"] == "computed"
    assert result.domains["economics"] == "computed"

    # 但性能结论**绝不能**是生产级的 observed_pass
    assert not any(c.sla_status == "observed_pass" for c in result.candidates), [
        (c.candidate_id, c.sla_status, c.match_level) for c in result.candidates
    ]
    # 收入情景只作情景分析
    assert any(c.capacity_check == "unknown_no_measurement" for c in result.candidates)
    # 合成价格 -> 成本不完整 -> 不给全周期总额
    assert all(c.total_cash_over_horizon is None for c in result.candidates)


def test_s6_scenario_c_algorithm_validation_cannot_reach_publishing(tmp_path, root):
    """C 加强版：即便显式开 allow_synthetic 拿到 observed_pass，门禁仍阻断。"""
    from llm_capacity_pricing.agent_api import call_agent_op
    from llm_capacity_pricing.schemas.performance import SlaPolicy
    db = _db(tmp_path, root, with_cost=True, name="s6c2.db")
    import_performance_curve(root / "data/fixtures/perf_curve/INDEX.json", db, root=root)

    algo_policy = SlaPolicy(
        policy_id="algorithm-validation-only",
        allow_synthetic=True,
        accepted_trust_levels=[
            "measured_internal",
            "measured_vendor_published",
            "synthetic_fixture",
        ],
    )
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
        "allow_synthetic": True,
    }
    info = record_sla_evidence_for(
        db,
        candidate_id="demo-owned",
        requirement={"requirement_id": "req-c", "ttft_p99_ms_max": 99999},
        facts=facts,
        policy=algo_policy,
    )
    assert info["status"] == "observed_pass"      # 算法验证确实拿到了
    assert info["production_grade"] is False      # 但生产级判定为假

    # Agent 接口也无法据此发布：缺审批引用直接拒绝
    with pytest.raises(Exception, match="缺少证据引用"):
        call_agent_op(
            "phase4.quote_eligibility",
            db=db,
            candidate_id="demo-owned",
            requirement_id="req-c",
            sla_evaluation_id=info["sla_evaluation_id"],
        )

    # 用统一评估入口再走一遍：合成样本 + 未确认成本 -> 报价资格绝不放行
    request = _request(
        request_id="s6-c2",
        sla={"requirement_id": "req-c", "ttft_p99_ms_max": 99999},  # type: ignore[arg-type]
        cost=PlanCostInput(plan_file=PLAN_TEMPLATE, template_id="syn-owned-3y"),
        governance=PlanGovernanceInput(
            sla_evaluation_id=info["sla_evaluation_id"], approval_id="syn-approval-demo"
        ),
    )
    result = evaluate_plan(request, db_path=db)
    assert not any(c.quote_publishable for c in result.candidates)
    assert all(c.quote_blockers for c in result.candidates if c.governance_status == "computed")


# ==========================================================================
# 场景 D：输入错误或证据不匹配
# ==========================================================================


def test_s6_scenario_d_unknown_model_is_cleanly_blocked(tmp_path, root):
    db = _db(tmp_path, root)
    result = evaluate_plan(_request(model_id="no-such-model"), db_path=db)
    assert result.status == "blocked"
    assert [b.code for b in result.blockers] == ["model_not_found"]
    assert "import-models" in result.blockers[0].message
    assert result.candidates == []


def test_s6_scenario_d_unknown_cost_template_is_not_silently_ignored(tmp_path, root):
    db = _db(tmp_path, root, with_cost=True)
    result = evaluate_plan(
        _request(cost=PlanCostInput(plan_file=PLAN_TEMPLATE, template_id="nope")),
        db_path=db,
    )
    assert result.domains["economics"] == "error"
    assert any(b.code == "cost_template_not_found" for b in result.blockers)
    # 其它领域照常出结论（局部失败不拖垮整体）
    assert result.domains["sizing"] == "computed"


def test_s6_scenario_d_bad_config_is_rejected_before_any_calculation(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        yaml.safe_dump(
            {
                "model_id": "qwen3-32b",
                "hardware_ids": [],           # 非法：至少要一个候选硬件
                "workload": {"input_len": 8192},
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    out = tmp_path / "out.md"
    result = runner.invoke(
        app, ["plan", "--config", str(bad), "--out", str(out), "--db", str(tmp_path / "x.db")]
    )
    assert result.exit_code != 0
    assert "校验失败" in result.output
    assert not out.exists()


def test_s6_scenario_d_evidence_mismatch_is_reported_not_ignored(tmp_path, root):
    """证据与目标身份不符 -> 明确阻断，不能静默降级为"参考值"。"""
    from llm_capacity_pricing.services import quote_eligibility_for

    db = _db(tmp_path, root, with_cost=True, name="s6d.db")
    _insert_measured_run(db, root)
    # 引用真实存在的审批记录，才能走到"重放核验"这一步（否则更早就会因缺记录报错）
    from llm_capacity_pricing.services import import_quote_approvals

    import_quote_approvals("data/examples/phase4_approvals.example.yaml", db)
    facts = dict(ALIGNED_FACTS)
    record_sla_evidence_for(
        db,
        candidate_id="demo-owned",
        requirement={"requirement_id": "req-d", "ttft_p99_ms_max": 99999},
        facts=facts,
    )
    mismatched = dict(facts, configured_concurrency=64)  # 负载身份被改
    info = quote_eligibility_for(
        db,
        candidate_id="demo-owned",
        requirement_id="req-d",
        facts=mismatched,
        sla_evaluation_id="sla::demo-owned::req-d",
        approval_id="syn-approval-demo",
        plan_file=PLAN_TEMPLATE,
        template_id="syn-owned-3y",
        hardware_id="nvidia-h100-sxm-80gb",
        gpu_count=4,
        server_count=1,
    )
    blockers = " ".join(info["eligibility"]["blockers"])
    assert "sla_target_identity_changed" in blockers or "sla_run_pool_changed" in blockers
    assert info["eligibility"]["quote_publishable"] is False


# ==========================================================================
# 新旧入口对照
# ==========================================================================


def test_s6_new_and_old_entries_agree_on_core_results(tmp_path, root):
    """同一数据库快照下，`plan` 与既有 `rank-deployments` / `tco` 结论一致。"""
    from llm_capacity_pricing.services import rank_deployments_for, tco_for

    db = _db(tmp_path, root, with_cost=True, name="s6e.db")
    request = _request(
        sla={"requirement_id": "req-cmp", "ttft_p99_ms_max": 400},  # type: ignore[arg-type]
        cost=PlanCostInput(plan_file=PLAN_TEMPLATE, template_id="syn-owned-3y"),
    )
    plan_result = evaluate_plan(request, db_path=db)

    legacy = rank_deployments_for(
        db,
        model_id=request.model_id,
        hardware_id=request.hardware_ids[0],
        server_id=request.server_id,
        requirement={"requirement_id": "req-cmp", "ttft_p99_ms_max": 400},
        assumptions=make_assumptions(
            usable_fraction_of_nominal=request.resources.usable_fraction,
            usable_memory_source_kind=request.resources.usable_source_kind,
            runtime_reserve_fraction=request.resources.runtime_reserve_fraction,
            global_concurrency=request.workload.concurrency,
            context_length_per_request=request.workload.input_len,
            tp_sizes=tuple(request.advanced.tp_sizes),
            dp_sizes=tuple(request.advanced.dp_sizes),
        ),
    )
    old_by_id = {e["candidate_id"]: e for e in legacy["entries"]}
    for candidate in plan_result.candidates:
        old = old_by_id[candidate.candidate_id]
        assert candidate.static_status == old["static_status"]
        assert candidate.rank_bucket == old["rank_bucket"]
        assert candidate.sla_status == old["sla_status"]
        assert candidate.match_level == old["match_level"]

    legacy_tco = tco_for(
        db,
        plan_file=PLAN_TEMPLATE,
        template_id="syn-owned-3y",
        candidate_id=plan_result.candidates[0].candidate_id,
        hardware_id="nvidia-h100-sxm-80gb",
        gpu_count=plan_result.candidates[0].gpu_count,
        server_count=plan_result.candidates[0].nodes_required or 1,
    )
    new_tco = next(
        c for c in plan_result.candidates if c.candidate_id == legacy_tco["tco"]["plan_id"].split("::")[-1]
    )
    assert new_tco.capex_one_time == Decimal(legacy_tco["tco"]["capex_one_time"])
    assert new_tco.annual_cash_opex == Decimal(legacy_tco["tco"]["annual_cash_opex"])


def test_s6_cli_plan_and_agent_agree_end_to_end(tmp_path, root):
    from llm_capacity_pricing.agent_api import call_agent_op

    db = _db(tmp_path, root, with_cost=True, name="s6f.db")
    out = tmp_path / "plan.json"
    cli_result = runner.invoke(
        app,
        [
            "plan",
            "--config", "data/examples/scenario_minimal.example.yaml",
            "--json-out", str(out), "--db", db,
        ],
    )
    assert cli_result.exit_code == 0, cli_result.output
    cli_payload = json.loads(out.read_text(encoding="utf-8"))
    agent_payload = call_agent_op(
        "plan.evaluate_config",
        db=db,
        config="data/examples/scenario_minimal.example.yaml",
    )
    assert agent_payload["status"] == cli_payload["status"]
    assert agent_payload["domains"] == cli_payload["domains"]
    assert agent_payload["summary"] == cli_payload["summary"]
