"""S2–S4：一键 Demo、方案文档与界面外壳。

分工：

* ``demo_service`` —— 一份需求 → 建库 → 评估 → 两份报告（S2/S3 共用）；
* ``build_deployment_proposal`` —— 7 段式客户版文档（S4）；
* ``app/streamlit_app.py`` —— 只做输入/展示/触发（用 ``AppTest`` 无头验证）。
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from llm_capacity_pricing.app.demo_service import (
    DemoInputs,
    build_plan_request,
    new_run_dir,
    prepare_demo_db,
    run_demo,
    summary_lines,
)
from llm_capacity_pricing.reporting.proposal import build_deployment_proposal
from llm_capacity_pricing.workflow import evaluate_plan, load_plan_request

ROOT = Path(__file__).resolve().parents[1]

PROPOSAL_SECTIONS = (
    "## 1. 客户需求概览",
    "## 2. 模型与硬件资源分析",
    "## 3. 候选部署配置",
    "## 4. 性能预测与 SLA 判断",
    "## 5. 成本与预算分析",
    "## 6. 推荐配置及依据",
    "## 7. 限制、风险与待验证事项",
)


# --------------------------------------------------------------------------
# S2：请求构建与数据库准备
# --------------------------------------------------------------------------


def test_request_carries_the_user_requirements():
    request = build_plan_request(DemoInputs())
    assert request.model_id == "qwen3-32b"
    assert request.max_total_gpus == 8
    assert request.workload.input_len == 8192
    assert request.workload.concurrency == 32
    assert request.sla is not None and request.sla.ttft_p99_ms_max == Decimal("400")
    assert request.budget is not None and request.budget.amount == Decimal("3000000")
    assert request.advanced.weight_precision == "bf16"


@pytest.mark.parametrize("mode", ["offline", "live", "core"])
def test_simulation_mode_mapping(mode):
    request = build_plan_request(DemoInputs(mode=mode))
    if mode == "core":
        assert request.simulation is None
    elif mode == "offline":
        assert request.simulation is not None
        assert request.simulation.enable_external is False
        assert request.simulation.sweeper_csv
    else:
        assert request.simulation is not None
        assert request.simulation.enable_external is True
        assert request.simulation.auto_search is True


def test_missing_sla_or_budget_is_not_invented():
    request = build_plan_request(
        DemoInputs(ttft_p99_ms_max=None, tpot_p99_ms_max=None, budget_amount=None)
    )
    assert request.sla is None
    assert request.budget is None


def test_prepare_demo_db_refuses_to_overwrite(tmp_path):
    db = tmp_path / "demo.db"
    prepare_demo_db(db)
    assert db.exists()
    with pytest.raises(FileExistsError):
        prepare_demo_db(db)


def test_new_run_dir_is_unique(tmp_path):
    first = new_run_dir(tmp_path)
    first.mkdir(parents=True)
    second = new_run_dir(tmp_path)
    assert first != second


# --------------------------------------------------------------------------
# S2/S4：离线一键 Demo 与 7 段式文档
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def offline_outcome(tmp_path_factory):
    out = tmp_path_factory.mktemp("sysdemo") / "run"
    return run_demo(DemoInputs(mode="offline"), out_dir=out)


def test_offline_demo_writes_three_artifacts(offline_outcome):
    assert offline_outcome.proposal_path.exists()
    assert offline_outcome.technical_path.exists()
    assert offline_outcome.json_path.exists()
    payload = json.loads(offline_outcome.json_path.read_text(encoding="utf-8"))
    assert payload["domains"]["sizing"] == "computed"
    assert payload["simulation"]["data_trust"] == "simulated_prediction"


def test_proposal_has_the_seven_sections(offline_outcome):
    md = offline_outcome.proposal_md
    for section in PROPOSAL_SECTIONS:
        assert section in md, section


def test_proposal_labels_data_classes_and_never_claims_observed_pass(offline_outcome):
    md = offline_outcome.proposal_md
    # 四类数字的措辞必须出现，便于读者区分
    assert "模拟预测" in md
    assert "公开实测参考" in md
    assert "假设成本" in md
    assert "实测证据" in md
    # 没有实测就不能声称达标
    assert "observed_pass" not in md
    assert "实测达标」" not in md
    assert "不给出实测达标结论" in md
    # 模拟不达标时要明说
    assert "模拟预测**未**满足阈值" in md


def test_proposal_does_not_invent_numbers(offline_outcome):
    """文档里出现的候选数值必须能在结构化结果里找到（防编造）。"""
    md = offline_outcome.proposal_md
    payload = offline_outcome.result_json
    known = set()

    def collect(value):
        if isinstance(value, dict):
            for item in value.values():
                collect(item)
        elif isinstance(value, list):
            for item in value:
                collect(item)
        elif isinstance(value, str):
            known.add(value)
        elif value is not None:
            known.add(str(value))

    collect(payload)
    for token in ("5201.827252000001", "31.45861707450993", "990.888548064229"):
        assert token in md, token
        assert token in known, f"{token} 出现在文档里但不在结构化结果中"


def test_simulation_does_not_add_any_benchmark_run(tmp_path):
    """评估前后库里的 run 数不变：模拟结果不写 BenchmarkRun。

    注意 Demo 库里本来就有 14 条**公开参考**（供报告的外部对照用），
    所以这里比的是"评估前后是否新增"，以及"有没有出现模拟/内部实测的可信等级"。
    """
    from llm_capacity_pricing.services import open_db

    out = tmp_path / "iso"
    out.mkdir()
    db = prepare_demo_db(out / "demo.db")

    def snapshot():
        dbh, repo = open_db(str(db))
        try:
            with dbh.session() as session:
                return [(r.run_id, r.data_trust) for r in repo.list_benchmark_runs(session)]
        finally:
            dbh.dispose()

    before = snapshot()
    evaluate_plan(build_plan_request(DemoInputs(mode="offline")), db_path=str(db))
    after = snapshot()
    assert after == before, "评估过程不得新增/修改 BenchmarkRun"
    assert all(trust in ("measured_vendor_published", "measured_third_party") for _id, trust in after)
    assert not any(trust == "measured_internal" for _id, trust in after)


def test_repeat_runs_do_not_share_artifacts(tmp_path):
    first = run_demo(DemoInputs(mode="offline"), out_dir=tmp_path / "a")
    second = run_demo(DemoInputs(mode="offline"), out_dir=tmp_path / "b")
    assert first.out_dir != second.out_dir
    assert first.db_path != second.db_path
    assert first.proposal_path != second.proposal_path


# --------------------------------------------------------------------------
# S5 需要的异常场景
# --------------------------------------------------------------------------


def test_core_mode_has_no_simulation_values(tmp_path):
    outcome = run_demo(DemoInputs(mode="core"), out_dir=tmp_path / "core")
    assert outcome.result.simulation is None
    assert "本次未启用" in outcome.proposal_md
    # 其余三个领域照常
    assert outcome.result.domains["sizing"] == "computed"


def test_live_mode_without_tool_still_produces_a_report(tmp_path, monkeypatch):
    """AISimulate 不可用：报告照常出，只是模拟章节写明原因。"""
    monkeypatch.setenv("AISIMULATE_PYTHON", "/nonexistent/python")
    outcome = run_demo(DemoInputs(mode="live"), out_dir=tmp_path / "live")
    sim = outcome.result.simulation
    assert sim is not None and sim.status == "error"
    assert outcome.proposal_path.exists()
    assert "模拟未产出结论" in outcome.proposal_md
    assert outcome.result.domains["sizing"] == "computed"


def test_hardware_shortage_is_reported_not_raised(tmp_path):
    """卡数上限太小：候选静态不可行，但仍要出报告。"""
    outcome = run_demo(
        DemoInputs(mode="offline", gpu_count=1), out_dir=tmp_path / "tiny"
    )
    assert outcome.result.candidates
    assert all(
        c.static_status in ("infeasible", "conditional_feasible", "insufficient_information")
        for c in outcome.result.candidates
    )
    assert outcome.proposal_path.exists()


def test_incomplete_cost_never_becomes_a_total(tmp_path):
    outcome = run_demo(DemoInputs(mode="offline"), out_dir=tmp_path / "cost")
    for candidate in outcome.result.candidates:
        if candidate.cost_incomplete:
            assert candidate.total_cash_over_horizon is None
    assert "不完整" in outcome.proposal_md


def test_summary_lines_only_restate_structured_fields(offline_outcome):
    lines = summary_lines(offline_outcome)
    assert any("整体状态" in line for line in lines)
    assert any("模拟" in line for line in lines)


# --------------------------------------------------------------------------
# S3：界面外壳（无头执行脚本，确认没有运行时异常）
# --------------------------------------------------------------------------


def test_streamlit_app_runs_headlessly():
    st_testing = pytest.importorskip("streamlit.testing.v1")
    AppTest = st_testing.AppTest
    app = AppTest.from_file(str(ROOT / "app" / "streamlit_app.py"), default_timeout=300)
    app.run()
    assert not app.exception, app.exception
    # 只读控件：模型/GPU/负载/SLA/预算
    assert len(app.selectbox) >= 3
    assert len(app.number_input) >= 6


# --------------------------------------------------------------------------
# codex 复审指出的 4 个问题（P2）的反向用例
# --------------------------------------------------------------------------


def test_server_is_matched_to_the_selected_gpu():
    """选 H200 时不能用 H100 机箱：否则候选会被判 server_gpu_sku_mismatch。"""
    from llm_capacity_pricing.app.demo_service import server_for_hardware

    assert server_for_hardware("nvidia-h100-sxm-80gb") == "hgx-h100-8gpu"
    assert server_for_hardware("nvidia-h200-sxm-141gb") == "hgx-h200-8gpu"
    assert server_for_hardware("unknown-gpu") is None  # 不猜

    request = build_plan_request(DemoInputs(hardware_id="nvidia-h200-sxm-141gb"))
    assert request.server_id == "hgx-h200-8gpu"


def test_h200_demo_does_not_fail_on_server_mismatch(tmp_path):
    outcome = run_demo(
        DemoInputs(mode="offline", hardware_id="nvidia-h200-sxm-141gb"),
        out_dir=tmp_path / "h200",
    )
    assert outcome.result.domains["sizing"] == "computed"
    assert not any(
        "server_gpu_sku_mismatch" in " ".join(c.static_reasons)
        for c in outcome.result.candidates
    )


def test_zero_budget_is_a_real_constraint_not_unknown(monkeypatch):
    """预算 0 与"未给预算"必须区分（未知 ≠ 0）。"""
    request = build_plan_request(DemoInputs(budget_amount=Decimal("0")))
    assert request.budget is not None
    assert request.budget.amount == Decimal("0")

    request = build_plan_request(DemoInputs(budget_amount=None))
    assert request.budget is None


def test_offline_mode_honours_the_ranking_objective(tmp_path):
    """离线模式也要把 ranking 传下去，否则 --ranking cost 会被静默忽略。"""
    request = build_plan_request(DemoInputs(mode="offline", ranking="cost"))
    assert request.simulation is not None
    assert request.simulation.ranking == "cost"

    outcome = run_demo(
        DemoInputs(mode="offline", ranking="cost"), out_dir=tmp_path / "costrank"
    )
    assert outcome.result_json["simulation"]["ranking"] == "cost"


def test_existing_nonempty_out_dir_is_refused(tmp_path):
    """--out 指向已有产物目录时不得覆盖（历史结果要保留）。"""
    out = tmp_path / "used"
    out.mkdir()
    marker = out / "deployment_proposal.md"
    marker.write_text("old result", encoding="utf-8")

    with pytest.raises(FileExistsError) as excinfo:
        run_demo(DemoInputs(mode="offline"), out_dir=out)
    assert "非空" in str(excinfo.value)
    assert marker.read_text(encoding="utf-8") == "old result"


def test_live_work_dir_stays_inside_the_run_dir(tmp_path):
    """实时模式的搜索/回放产物必须落在本次运行目录下。"""
    from llm_capacity_pricing.app.demo_service import DemoInputs as DI

    out = tmp_path / "live"
    captured = {}

    import llm_capacity_pricing.app.demo_service as svc

    original = svc.evaluate_plan

    def spy(request, *, db_path):
        captured["work_dir"] = request.simulation.work_dir if request.simulation else None
        return original(request, db_path=db_path)

    svc.evaluate_plan = spy
    try:
        run_demo(DI(mode="live", ttft_p99_ms_max=None, tpot_p99_ms_max=None), out_dir=out)
    finally:
        svc.evaluate_plan = original

    assert captured["work_dir"] is not None
    assert str(out) in captured["work_dir"]
