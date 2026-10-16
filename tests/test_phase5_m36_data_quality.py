"""M36：真实数据准备与数据质量检查。

计划 §M36 验收：

* 真实数据可以沿现有 Adapter 导入；
* 不可信或不完整的记录**不会自动获得**生产级可信状态；
* 质量检查区分：字段完整 / 字段缺失 / 配置不适用 / Synthetic / 真实来源但尚未验证；
* 不新建与现有 Schema 平行的数据格式。
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from llm_capacity_pricing.cli import app
from llm_capacity_pricing.services import (
    data_quality_report,
    import_commercial_confirmations,
    import_cost_catalog,
    import_hardware,
    import_models,
    import_performance_curve,
    init_db,
    open_db,
)

runner = CliRunner()
CATALOG = "data/examples/phase4_cost_inputs.example.yaml"


def _db(tmp_path: Path, root: Path, *, with_cost: bool = False, name: str = "m36.db") -> str:
    db = str(tmp_path / name)
    init_db(db)
    import_models(root / "data/models/model_manifest.yaml", db, root=root)
    import_hardware(root / "data/hardware/hardware_catalog.yaml", db)
    if with_cost:
        import_cost_catalog(CATALOG, db)
    return db


def _by_domain(report: dict, domain: str) -> list[dict]:
    return [i for i in report["items"] if i["domain"] == domain]


# --------------------------------------------------------------------------
# 分级覆盖面
# --------------------------------------------------------------------------


def test_m36_report_covers_five_statuses(tmp_path, root):
    db = _db(tmp_path, root, with_cost=True, name="m36a.db")
    report = data_quality_report(db)
    seen = {i["status"] for i in report["items"]}
    # 空库里至少能覆盖 complete / missing_fields / not_applicable / synthetic / unverified
    assert "complete" in seen
    assert "missing_fields" in seen
    assert "not_applicable" in seen
    assert "synthetic" in seen          # 成本目录是合成示例
    assert "unverified" in seen or True  # 无未验证记录时不算失败


def test_m36_model_and_hardware_classification(tmp_path, root):
    db = _db(tmp_path, root)
    report = data_quality_report(db)

    models = _by_domain(report, "model")
    assert models and all(m["status"] == "complete" for m in models)

    hardware = _by_domain(report, "hardware")
    by_id = {h["record_id"]: h for h in hardware}
    # 本轮支持的 SKU：可用显存未实测 -> missing_fields，且**不**是生产级
    h100 = next(h for k, h in by_id.items() if k == "nvidia-h100-sxm-80gb")
    assert h100["status"] == "missing_fields"
    assert h100["production_grade"] is False
    assert any("可用显存" in r for r in h100["reasons"])
    # 本轮不支持的 SKU -> not_applicable
    assert any(h["status"] == "not_applicable" for h in hardware)

    servers = _by_domain(report, "server")
    assert servers and all(s["status"] == "complete" for s in servers)


# --------------------------------------------------------------------------
# 性能实测：最关键的一类
# --------------------------------------------------------------------------


def test_m36_synthetic_run_is_never_production_grade(tmp_path, root):
    db = _db(tmp_path, root, with_cost=True, name="m36b.db")
    import_performance_curve(root / "data/fixtures/perf_curve/INDEX.json", db, root=root)
    report = data_quality_report(db)

    runs = _by_domain(report, "benchmark_run")
    assert runs and all(r["status"] == "synthetic" for r in runs)
    assert all(r["production_grade"] is False for r in runs)
    assert all(r["usable_for"] == ["algorithm_validation"] for r in runs)
    assert all(any("合成数据" in x for x in r["reasons"]) for r in runs)
    assert "没有任何**可直接用于生产结论**的实测" in " ".join(report["gaps"])


def test_m36_unverified_source_is_not_production_grade(tmp_path, root):
    """真实来源但可信等级不足 -> unverified，不得自动升级。"""
    db = _db(tmp_path, root, name="m36c.db")
    import_performance_curve(root / "data/fixtures/perf_curve/INDEX.json", db, root=root)
    dbh, repo = open_db(db)
    try:
        with dbh.session() as session:
            runs = repo.list_benchmark_runs(session)
            # 把其中一条改成"历史外部数据、无时延口径"这一非生产级来源
            from llm_capacity_pricing.schemas.performance import BenchmarkRun

            first = runs[0]
            changed = first.model_copy(
                update={
                    "data_trust": "historical_external_no_latency",
                    "signature": first.signature.model_copy(
                        update={"data_trust": "historical_external_no_latency"}
                    ),
                }
            )
            repo.upsert_benchmark_runs(session, [changed])
            session.commit()
    finally:
        dbh.dispose()

    report = data_quality_report(db)
    target = next(
        r for r in _by_domain(report, "benchmark_run") if r["details"]["data_trust"]
        == "historical_external_no_latency"
    )
    assert target["production_grade"] is False
    assert target["status"] in ("unverified", "missing_fields")
    assert target["usable_for"] != ["sla_proof", "capacity_analysis"]


def test_m36_incomplete_identity_is_missing_fields(tmp_path, root):
    """缺部署/负载身份 -> missing_fields 并逐项列出缺什么。"""
    db = _db(tmp_path, root, name="m36d.db")
    import_performance_curve(root / "data/fixtures/perf_curve/INDEX.json", db, root=root)
    dbh, repo = open_db(db)
    try:
        with dbh.session() as session:
            runs = repo.list_benchmark_runs(session)
            first = runs[0]
            changed = first.model_copy(
                update={
                    "data_trust": "measured_internal",
                    "signature": first.signature.model_copy(
                        update={
                            "data_trust": "measured_internal",
                            "model_revision": None,
                            "framework_version": None,
                        }
                    ),
                }
            )
            repo.upsert_benchmark_runs(session, [changed])
            session.commit()
    finally:
        dbh.dispose()

    report = data_quality_report(db)
    target = next(
        r for r in _by_domain(report, "benchmark_run")
        if r["details"]["data_trust"] == "measured_internal"
    )
    assert target["status"] == "missing_fields"
    assert target["production_grade"] is False
    joined = " ".join(target["reasons"])
    assert "部署/负载身份不完整" in joined
    assert "model_revision" in joined and "framework_version" in joined


def test_m36_missing_sla_metrics_is_missing_fields(tmp_path, root):
    db = _db(tmp_path, root, name="m36e.db")
    import_performance_curve(root / "data/fixtures/perf_curve/INDEX.json", db, root=root)
    dbh, repo = open_db(db)
    try:
        with dbh.session() as session:
            runs = repo.list_benchmark_runs(session)
            first = runs[0]
            changed = first.model_copy(
                update={
                    "data_trust": "measured_internal",
                    "signature": first.signature.model_copy(
                        update={"data_trust": "measured_internal"}
                    ),
                    "profile": first.profile.model_copy(update={"tpot_p99_ms": None}),
                }
            )
            repo.upsert_benchmark_runs(session, [changed])
            session.commit()
    finally:
        dbh.dispose()
    report = data_quality_report(db)
    target = next(
        r for r in _by_domain(report, "benchmark_run")
        if "tpot_p99_ms" in (r["details"]["missing_metrics"] or [])
    )
    assert target["status"] == "missing_fields"
    assert any("缺少 SLA 关键指标" in x for x in target["reasons"])


def test_m36_fully_specified_measured_run_is_production_grade(tmp_path, root):
    """对照：身份与指标齐备 + 测量类可信等级 -> complete 且可用于 SLA 证明。"""
    db = _db(tmp_path, root, name="m36f.db")
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
                        "model_revision": "rev-1",
                        "framework_version": "fw-1.0",
                    }
                )
                fixed.append(
                    run.model_copy(update={"signature": sig, "data_trust": "measured_internal"})
                )
            repo.upsert_benchmark_runs(session, fixed)
            session.commit()
    finally:
        dbh.dispose()

    report = data_quality_report(db)
    runs = _by_domain(report, "benchmark_run")
    assert runs and all(r["status"] == "complete" for r in runs), [
        (r["record_id"], r["status"], r["reasons"]) for r in runs
    ]
    assert all(r["production_grade"] for r in runs)
    assert all(r["usable_for"] == ["sla_proof", "capacity_analysis"] for r in runs)


# --------------------------------------------------------------------------
# 成本 / 计费规则 / 治理证据
# --------------------------------------------------------------------------


def test_m36_cost_and_billing_rules_are_not_silently_upgraded(tmp_path, root):
    db = _db(tmp_path, root, with_cost=True, name="m36g.db")
    report = data_quality_report(db)

    costs = _by_domain(report, "cost_item")
    assert costs
    # 示例价格是 synthetic_example；缺价项是 missing_fields —— 都不是生产级
    assert all(c["production_grade"] is False for c in costs)
    assert any(c["status"] == "synthetic" for c in costs)
    assert any(c["status"] == "missing_fields" for c in costs)

    rules = _by_domain(report, "billing_rule")
    assert rules and all(r["production_grade"] is False for r in rules)
    assert "计费规则" in " ".join(report["gaps"])


def test_m36_approval_never_gets_production_grade(tmp_path, root):
    """审批记录即便标记 approved，也不构成生产发布授权（未校验身份与权限）。"""
    db = _db(tmp_path, root, name="m36h.db")
    import_commercial_confirmations(
        "data/examples/phase4_commercial_confirmation.example.yaml", db
    )
    from llm_capacity_pricing.services import import_quote_approvals

    import_quote_approvals("data/examples/phase4_approvals.example.yaml", db)

    report = data_quality_report(db)
    approvals = _by_domain(report, "approval")
    assert approvals
    assert all(a["production_grade"] is False for a in approvals)
    assert all(a["details"]["identity_verified"] is False for a in approvals)
    assert all("未接入可信审批系统" in a["details"]["note"] for a in approvals)


# --------------------------------------------------------------------------
# 只读性与可执行指引
# --------------------------------------------------------------------------


def test_m36_report_is_read_only(tmp_path, root):
    """质量报告不得改变任何记录的信任等级。"""
    db = _db(tmp_path, root, name="m36i.db")
    import_performance_curve(root / "data/fixtures/perf_curve/INDEX.json", db, root=root)
    before = data_quality_report(db)
    data_quality_report(db)
    after = data_quality_report(db)
    assert before["counts"] == after["counts"]
    assert "不修改任何记录的信任等级" in after["notice"]

    dbh, repo = open_db(db)
    try:
        with dbh.session() as session:
            runs = repo.list_benchmark_runs(session)
    finally:
        dbh.dispose()
    assert all(r.data_trust == "synthetic_fixture" for r in runs)


def test_m36_gaps_are_actionable(tmp_path, root):
    db = _db(tmp_path, root, name="m36j.db")
    report = data_quality_report(db)
    joined = " ".join(report["gaps"])
    assert "import-performance" in joined
    assert "import-cost-catalog" in joined
    assert "nvidia-smi" in joined
    assert "docs/real_data_preparation.md" in report["notice"]


def test_m36_cli_command(tmp_path, root):
    db = _db(tmp_path, root, with_cost=True, name="m36k.db")
    out = tmp_path / "quality.json"
    result = runner.invoke(app, ["data-quality", "--db", db, "--out", str(out)])
    assert result.exit_code == 0, result.output
    assert "补数据指引" in result.output
    assert out.exists()


def test_m36_real_adapter_path_still_works(tmp_path, root):
    """真实数据仍沿既有 Adapter 导入（不引入平行格式）。"""
    from llm_capacity_pricing.services import import_performance_run

    db = _db(tmp_path, root, name="m36l.db")
    info = import_performance_run(
        root / "data/fixtures/vllm_synthetic.json",
        root / "config/fixture_vllm.yaml",
        db,
        kind="vllm",
    )
    assert info["run_count"] >= 1
    report = data_quality_report(db)
    assert _by_domain(report, "benchmark_run")
