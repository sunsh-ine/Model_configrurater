"""P3–P4：模拟预测接入统一评估后的反向测试。

对应任务书 P4 的八条反向测试，外加"离线可复现"的端到端检查。

核心纪律（每条都有对应用例）：

* 不开模拟时，``plan`` 行为与输出不变；
* 模拟结果**不写入** ``BenchmarkRun`` / ``CapacityPoint``；
* 模拟**不产生** ``observed_pass``；
* 均值达标而 P99 超限必须判 ``predicted_fail``；
* 身份（模型/精度/GPU/后端版本）失配不得直接比较；
* 命令失败或输出缺失要给可解释错误，且不影响其它领域；
* 不完整成本不得当作零成本；
* 正式报价门禁保持禁用。
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from llm_capacity_pricing.services import (
    import_cost_catalog,
    import_hardware,
    import_models,
    init_db,
    open_db,
)
from llm_capacity_pricing.workflow import evaluate_plan, load_plan_request

ROOT = Path(__file__).resolve().parents[1]
DEMO_CONFIG = ROOT / "data/examples/demo_qwen3_32b_h100.yaml"
SIM_CONFIG = ROOT / "data/examples/scenario_simulation.example.yaml"

SIM_BLOCK = {
    "sweeper_csv": "docs/poc/aisimulate/artifacts/matched_pareto_vllm_bf16.csv",
    "native_prediction": "docs/poc/aisimulate/17_matched_native_result_tp8_conc32_bf16.json",
    "native_input": "docs/poc/aisimulate/16_matched_native_input_tp8_conc32_bf16.yaml",
    "exp_config": "docs/poc/aisimulate/artifacts/matched_exp_config_vllm_bf16.yaml",
    "per_ops_source": "docs/poc/aisimulate/artifacts/per_ops_source.json",
    "native_model_precision": "bf16",
    "tool_version": "0.12.1",
    "top_n": 3,
}


def _demo_db(tmp_path: Path) -> str:
    db = str(tmp_path / "demo.db")
    init_db(db)
    import_models(ROOT / "data/models/model_manifest.yaml", db, root=ROOT)
    import_hardware(ROOT / "data/hardware/hardware_catalog.yaml", db)
    import_cost_catalog(ROOT / "data/examples/phase4_cost_inputs.example.yaml", db)
    return db


def _config_with(tmp_path: Path, **overrides) -> Path:
    """基于示例配置改写出一个临时 YAML（可覆盖 SLA 与 simulation 段）。"""
    doc = yaml.safe_load(SIM_CONFIG.read_text(encoding="utf-8"))
    simulation = dict(SIM_BLOCK)
    simulation.update(overrides.pop("simulation", {}))
    doc["simulation"] = simulation
    for key, value in overrides.items():
        doc[key] = value
    target = tmp_path / "config.yaml"
    target.write_text(yaml.safe_dump(doc, allow_unicode=True), encoding="utf-8")
    return target


def _run(path: Path, db: str):
    return evaluate_plan(load_plan_request(path), db_path=db)


# --------------------------------------------------------------------------
# 反向测试 1：未开模拟 / 没有 AISimulate 时，原有 plan 正常运行
# --------------------------------------------------------------------------


def test_plan_without_simulation_is_unchanged(tmp_path):
    """不给 ``simulation`` 段：行为与输出不变，且完全没有模拟对象。"""
    db = _demo_db(tmp_path)
    result = _run(DEMO_CONFIG, db)
    assert result.simulation is None
    assert result.domains["sizing"] == "computed"
    assert result.candidates
    dumped = result.model_dump(mode="json")
    assert dumped["simulation"] is None
    assert "2.2 模拟" not in _report(result)


def test_plan_works_when_the_tool_is_not_installed(tmp_path, monkeypatch):
    """机器上没有 AISimulate 时：离线归档照常工作；要现场执行则给可解释错误。

    这正是 P2 的验收条件：无 GPU、无该软件也能复现模拟结果展示。
    """
    monkeypatch.setenv("AISIMULATE_PYTHON", "/nonexistent/python")
    db = _demo_db(tmp_path)

    offline = _run(_config_with(tmp_path), db)
    assert offline.simulation is not None
    assert offline.simulation.status == "computed"
    assert offline.simulation.candidates

    # 显式要求现场执行 -> 失败但只说清原因，其它领域照旧
    external = _run(
        _config_with(
            tmp_path,
            simulation={
                "enable_external": True,
                "external_python": "/nonexistent/python",
                "external_config": "whatever.yaml",
                "external_out_dir": str(tmp_path / "out"),
            },
        ),
        db,
    )
    assert external.simulation is not None
    assert external.simulation.status == "error"
    assert "AISimulate" in (external.simulation.message or "")
    assert external.domains["sizing"] == "computed"


# --------------------------------------------------------------------------
# 反向测试 2/3：不写入实测结构、不产生 observed_*
# --------------------------------------------------------------------------


def test_simulation_never_writes_benchmark_runs_or_capacity_points(tmp_path):
    """跑一次带模拟的评估后，库里的实测记录数**不变**。"""
    db = _demo_db(tmp_path)
    dbh, repo = open_db(db)
    try:
        with dbh.session() as session:
            before = len(repo.list_benchmark_runs(session))
    finally:
        dbh.dispose()

    result = _run(_config_with(tmp_path), db)
    assert result.simulation.status == "computed"

    dbh, repo = open_db(db)
    try:
        with dbh.session() as session:
            after = repo.list_benchmark_runs(session)
    finally:
        dbh.dispose()
    assert len(after) == before == 0


def test_simulation_never_produces_observed_status(tmp_path):
    """模拟结论只可能是 ``predicted_*`` / ``insufficient_prediction``。"""
    db = _demo_db(tmp_path)
    result = _run(_config_with(tmp_path), db)
    allowed = {"predicted_pass", "predicted_fail", "insufficient_prediction"}
    assert result.simulation.candidates
    for candidate in result.simulation.candidates:
        assert candidate.verdict in allowed
    # 候选侧也照旧：没有任何实测达标
    for candidate in result.candidates:
        assert candidate.sla_status != "observed_pass"
        assert candidate.rank_bucket != "verified_sla_met"

    # 结构化结果里任何**取值**都不得是 observed_*；
    # limitations 里的那句是"说明禁止这样做"的文档文字，不算取值。
    dumped = result.model_dump(mode="json")
    limitations = list(dumped.get("limitations") or []) + list(
        (dumped.get("simulation") or {}).get("limitations") or []
    )
    for key in ("limitations",):
        dumped.pop(key, None)
    if dumped.get("simulation"):
        dumped["simulation"].pop("limitations", None)
    for verdict in ("observed_pass", "observed_fail"):
        assert verdict not in json.dumps(dumped, ensure_ascii=False)
    assert any("observed_pass" in text for text in limitations), (
        "模拟结论的限制里必须写明不得产生 observed_pass"
    )


# --------------------------------------------------------------------------
# 反向测试 4：均值达标而 P99 超限 -> predicted_fail
# --------------------------------------------------------------------------


def test_mean_pass_but_p99_fail_is_reported_as_predicted_fail(tmp_path):
    """阈值 600ms / 32ms：均值（548.05 / 30.07）达标，P99（5201.83 / 31.46）中 TTFT 超限。"""
    db = _demo_db(tmp_path)
    config = _config_with(
        tmp_path,
        sla={"requirement_id": "demo-sla-loose", "ttft_p99_ms_max": 600, "tpot_p99_ms_max": 32},
    )
    result = _run(config, db)
    judged = result.simulation.judged()
    assert judged
    native = next(c for c in judged if c.candidate_id.startswith("sim-native"))
    assert native.metrics.sim_mean_ttft_ms <= Decimal("600")
    assert native.metrics.sim_p99_ttft_ms > Decimal("600")
    assert native.verdict == "predicted_fail"
    assert result.simulation.recommended() is None  # 不达标就不给推荐
    report = _report(result)
    assert "预测**未**满足阈值（模拟）" in report
    assert "口径提示" in report


def test_predicted_pass_is_reachable_with_a_loose_enough_bound(tmp_path):
    db = _demo_db(tmp_path)
    config = _config_with(
        tmp_path,
        sla={
            "requirement_id": "demo-sla-very-loose",
            "ttft_p99_ms_max": 6000,
            "tpot_p99_ms_max": 32,
        },
    )
    result = _run(config, db)
    native = result.simulation.recommended()
    assert native is not None and native.verdict == "predicted_pass"
    assert "预测满足阈值（模拟）" in _report(result)


# --------------------------------------------------------------------------
# 反向测试 5：身份失配不得直接比较
# --------------------------------------------------------------------------


def test_precision_mismatch_makes_the_candidate_incomparable(tmp_path):
    """目标声明 fp8，而模拟是 bf16 -> 不可比较，也不给推荐。"""
    db = _demo_db(tmp_path)
    config = _config_with(tmp_path)
    doc = yaml.safe_load(config.read_text(encoding="utf-8"))
    doc["advanced"]["weight_precision"] = "fp8"
    config.write_text(yaml.safe_dump(doc, allow_unicode=True), encoding="utf-8")

    result = _run(config, db)
    native = next(
        c for c in result.simulation.candidates if c.candidate_id.startswith("sim-native")
    )
    assert native.comparable is False
    assert any("精度不同" in r for r in native.comparability_reasons)
    assert result.simulation.recommended() is None
    assert result.simulation.best_effort() is None


def test_hardware_mismatch_makes_the_candidate_incomparable(tmp_path):
    db = _demo_db(tmp_path)
    config = _config_with(tmp_path)
    doc = yaml.safe_load(config.read_text(encoding="utf-8"))
    doc["hardware_ids"] = ["nvidia-h200-sxm-141gb"]  # 目标是 H200，模拟是 H100
    doc["server_id"] = "hgx-h200-8gpu" if False else doc.get("server_id")
    config.write_text(yaml.safe_dump(doc, allow_unicode=True), encoding="utf-8")

    result = _run(config, db)
    assert result.simulation is not None
    native = [
        c for c in result.simulation.candidates if c.candidate_id.startswith("sim-native")
    ]
    assert native
    assert native[0].comparable is False
    assert any("硬件不同" in r for r in native[0].comparability_reasons)


def test_backend_version_mismatch_is_reported(tmp_path):
    """要求 vllm 0.25.0 而模拟用的是 0.24.0 -> 必须点名版本不同。"""
    db = _demo_db(tmp_path)
    config = _config_with(tmp_path)
    doc = yaml.safe_load(config.read_text(encoding="utf-8"))
    doc["advanced"]["framework"] = "vllm"
    doc["advanced"]["framework_version"] = "0.25.0"
    config.write_text(yaml.safe_dump(doc, allow_unicode=True), encoding="utf-8")

    result = _run(config, db)
    assert any("后端版本不同" in l for l in result.simulation.limitations)


# --------------------------------------------------------------------------
# 反向测试 6：命令失败 / 输出缺失 -> 可解释错误
# --------------------------------------------------------------------------


def test_missing_archive_file_yields_missing_data_not_a_crash(tmp_path):
    db = _demo_db(tmp_path)
    config = _config_with(tmp_path, simulation={"sweeper_csv": "docs/poc/aisimulate/does_not_exist.csv"})
    result = _run(config, db)
    assert result.simulation.status == "missing_data"
    assert "模拟数据不可用" in (result.simulation.message or "")
    assert result.domains["economics"] == "computed"  # 其它领域不受影响
    assert "数据不足" in _report(result)


def test_incomplete_native_output_has_no_verdict(tmp_path):
    """输出字段缺失（没有 p99）-> 不给结论，也不拿均值顶替。"""
    db = _demo_db(tmp_path)
    broken = tmp_path / "prediction.json"
    broken.write_text(json.dumps({"num_requests": 10, "output_throughput_tok_s": 5.0}), encoding="utf-8")
    config = _config_with(
        tmp_path,
        simulation={
            "sweeper_csv": None,
            "native_prediction": str(broken),
            "native_input": "docs/poc/aisimulate/16_matched_native_input_tp8_conc32_bf16.yaml",
        },
    )
    result = _run(config, db)
    assert result.simulation.status == "computed"
    candidate = result.simulation.candidates[0]
    assert candidate.metrics.sim_p99_ttft_ms is None
    assert candidate.verdict == "insufficient_prediction"
    assert result.simulation.recommended() is None


def test_external_run_is_refused_when_disabled(tmp_path):
    """配置里没开 enable_external：绝不执行外部命令。"""
    db = _demo_db(tmp_path)
    result = _run(_config_with(tmp_path), db)
    assert result.simulation.status == "computed"
    assert all("enable_external" not in l for l in result.simulation.limitations)


# --------------------------------------------------------------------------
# 反向测试 7：不完整成本不得当作零成本
# --------------------------------------------------------------------------


def test_incomplete_cost_is_not_treated_as_zero(tmp_path):
    db = _demo_db(tmp_path)
    result = _run(_config_with(tmp_path), db)
    headline = result.simulation.best_effort()
    assert headline is not None and headline.cost is not None
    # Demo 用的是假设成本目录 -> 不完整 -> 不给总额（不是 0）
    assert headline.cost.incomplete is True
    assert headline.cost.total_cash_over_horizon is None
    report = _report(result)
    assert "成本（本仓库 TCO 计算，不是模拟值）" in report
    assert "不完整，不给总额" in report


# --------------------------------------------------------------------------
# 反向测试 8：正式报价门禁保持禁用
# --------------------------------------------------------------------------


def test_quote_gate_stays_disabled_with_simulation_enabled(tmp_path):
    db = _demo_db(tmp_path)
    result = _run(_config_with(tmp_path), db)
    assert all(not c.quote_publishable for c in result.candidates)
    report = _report(result)
    assert "生产发布授权**：**未授权" in report or "未授权" in report


# --------------------------------------------------------------------------
# 端到端：离线可复现（P2/P4 验收）
# --------------------------------------------------------------------------


def _report(result) -> str:
    from llm_capacity_pricing.reporting.plan_report import build_plan_report

    return build_plan_report(result)


def test_example_config_renders_the_simulation_section_offline(tmp_path):
    """示例 YAML 在无 GPU、无 AISimulate 的环境下产出完整报告。"""
    db = _demo_db(tmp_path)
    result = _run(SIM_CONFIG, db)
    assert result.simulation.status == "computed"
    report = _report(result)
    for expected in (
        "## 2.2 模拟部署配置推荐（**非实测**）",
        "模拟状态：**已计算**",
        "| 模型 | Qwen/Qwen3-32B（bf16） |",
        "| GPU | h100_sxm × 8 |",
        "模拟输出吞吐",
        "模拟 TTFT P99",
        "预测结论",
        "**本节结论的限制**",
        "`predicted_*` 与 `observed_*` 是两套取值",
    ):
        assert expected in report, expected
    # JSON 里也有完整结构
    dumped = result.model_dump(mode="json")
    assert dumped["simulation"]["data_trust"] == "simulated_prediction"
    assert dumped["simulation"]["candidates"]
