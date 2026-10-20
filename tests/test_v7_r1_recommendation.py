"""V7-R1：推荐正确性加固的反向测试。

任务书要求的五类反例（后端版本不符 / GPU 数量超额 / 静态不可行 / 拓扑不完整 /
工作点不一致）都必须导致**不可推荐**；另外锁住三条身份细节：

* 扫描器每行保留**自己**的 backend/version（不能一个全局身份套所有行）；
* ``replicas`` 不得被当成 DP；
* 版本槽位别名解析不出来时，身份不明确 -> 不可比较。
"""

from __future__ import annotations

import copy
import json
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from llm_capacity_pricing.perf.aisimulate_adapter import (
    SimulationTarget,
    check_comparability,
    load_archived,
    parse_sweeper_csv,
    resolve_backend_version,
)
from llm_capacity_pricing.services import (
    import_cost_catalog,
    import_hardware,
    import_models,
    init_db,
)
from llm_capacity_pricing.workflow import evaluate_plan, load_plan_request

ROOT = Path(__file__).resolve().parents[1]
POC = ROOT / "docs/poc/aisimulate"
SIM_CONFIG = ROOT / "data/examples/scenario_simulation.example.yaml"
NATIVE_INPUT = POC / "16_matched_native_input_tp8_conc32_bf16.yaml"
NATIVE_RESULT = POC / "17_matched_native_result_tp8_conc32_bf16.json"
SWEEPER_CSV = POC / "artifacts/matched_pareto_vllm_bf16.csv"
EXP_CONFIG = POC / "artifacts/matched_exp_config_vllm_bf16.yaml"

CSV_HEADER = (
    "model,isl,osl,prefix,concurrency,request_rate,bs,global_bs,ttft,tpot,"
    "request_latency,seq/s,seq/s/gpu,tokens/s,tokens/s/gpu,tokens/s/user,"
    "num_total_gpus,tp,pp,dp,replicas,gpus_per_worker,moe_tp,moe_ep,cp,parallel,"
    "gemm,kvcache,fmha,moe,comm,"
    "memory,balance_score,num_ctx_reqs,num_gen_reqs,num_tokens,ctx_tokens,gen_tokens,"
    "backend,version,system,power_w,tokens/s/gpu_cluster"
)


def _demo_db(tmp_path: Path) -> str:
    db = str(tmp_path / "demo.db")
    init_db(db)
    import_models(ROOT / "data/models/model_manifest.yaml", db, root=ROOT)
    import_hardware(ROOT / "data/hardware/hardware_catalog.yaml", db)
    import_cost_catalog(ROOT / "data/examples/phase4_cost_inputs.example.yaml", db)
    return db


def _sim_block(**overrides) -> dict:
    base = {
        "sweeper_csv": "docs/poc/aisimulate/artifacts/matched_pareto_vllm_bf16.csv",
        "native_prediction": "docs/poc/aisimulate/17_matched_native_result_tp8_conc32_bf16.json",
        "native_input": "docs/poc/aisimulate/16_matched_native_input_tp8_conc32_bf16.yaml",
        "exp_config": "docs/poc/aisimulate/artifacts/matched_exp_config_vllm_bf16.yaml",
        "per_ops_source": "docs/poc/aisimulate/artifacts/per_ops_source.json",
        "native_model_precision": "bf16",
        "backend_version_aliases": {"current": "0.24.0"},
        "tool_version": "0.12.1",
        "top_n": 3,
    }
    base.update(overrides)
    return base


def _config(tmp_path: Path, *, name: str = "cfg", **top_level) -> Path:
    doc = yaml.safe_load(SIM_CONFIG.read_text(encoding="utf-8"))
    doc["simulation"] = _sim_block(**top_level.pop("simulation", {}))
    for key, value in top_level.items():
        doc[key] = value
    path = tmp_path / f"{name}.yaml"
    path.write_text(yaml.safe_dump(doc, allow_unicode=True), encoding="utf-8")
    return path


def _native_candidate(result):
    return next(
        c for c in result.simulation.candidates if c.candidate_id.startswith("sim-native")
    )


def _write_sweeper_csv(tmp_path: Path, rows: list[dict]) -> Path:
    """按扫描器表头写一个最小 CSV（用于构造身份/资源反例）。"""
    defaults = {
        "model": "Qwen/Qwen3-32B",
        "isl": "8192",
        "osl": "256",
        "prefix": "0",
        "concurrency": "32",
        "request_rate": "3.8",
        "bs": "32",
        "global_bs": "32",
        "ttft": "370.0",
        "tpot": "30.6",
        "request_latency": "8181.0",
        "seq/s": "3.8",
        "seq/s/gpu": "0.48",
        "tokens/s": "997.0",
        "tokens/s/gpu": "124.6",
        "tokens/s/user": "32.6",
        "num_total_gpus": "8",
        "tp": "8",
        "pp": "1",
        "dp": "1",
        "replicas": "1",
        "gpus_per_worker": "8",
        "moe_tp": "1",
        "moe_ep": "1",
        "cp": "1",
        "parallel": "tp8pp1dp1etp1ep1",
        "gemm": "bfloat16",
        "kvcache": "bfloat16",
        "fmha": "bfloat16",
        "moe": "bfloat16",
        "comm": "half",
        "memory": "20.0",
        "balance_score": "0.1",
        "num_ctx_reqs": "1.0",
        "num_gen_reqs": "31.0",
        "num_tokens": "8223.0",
        "ctx_tokens": "8192",
        "gen_tokens": "31.0",
        "backend": "vllm",
        "version": "0.24.0",
        "system": "h100_sxm",
        "power_w": "0.0",
        "tokens/s/gpu_cluster": "124.6",
    }
    path = tmp_path / "pareto.csv"
    lines = [CSV_HEADER]
    for row in rows:
        values = dict(defaults)
        values.update({k: str(v) for k, v in row.items()})
        lines.append(",".join(values[column] for column in CSV_HEADER.split(",")))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _write_native_pair(tmp_path: Path, *, mode: str = "aggregated", tensor: int = 8,
                       pipeline: int = 1, concurrency: int = 32) -> tuple[Path, Path]:
    """写一对原生输入/结果（结果沿用归档的指标，只改身份）。"""
    doc = yaml.safe_load(NATIVE_INPUT.read_text(encoding="utf-8"))
    doc["engine"]["mode"] = mode
    doc["engine"]["workers"]["aggregated"]["parallelism"] = {"tensor": tensor, "pipeline": pipeline}
    doc["traffic"]["load"]["concurrency"] = concurrency
    input_path = tmp_path / "native.input.yaml"
    input_path.write_text(yaml.safe_dump(doc, allow_unicode=True), encoding="utf-8")
    result_path = tmp_path / "native.prediction.json"
    result_path.write_text(NATIVE_RESULT.read_text(encoding="utf-8"), encoding="utf-8")
    return input_path, result_path


# ---------------------------------------------------------------------------
# 后端版本不符
# ---------------------------------------------------------------------------


def test_backend_version_mismatch_is_not_recommendable(tmp_path):
    db = _demo_db(tmp_path)
    config = _config(
        tmp_path,
        advanced={"tp_sizes": [2, 4, 8], "dp_sizes": [1], "weight_precision": "bf16",
                  "framework": "vllm", "framework_version": "0.25.0"},
    )
    result = evaluate_plan(load_plan_request(config), db_path=db)
    native = _native_candidate(result)
    assert native.comparable is False
    assert any("后端版本不同" in r for r in native.comparability_reasons)
    assert result.simulation.eligible() == []
    assert result.simulation.recommended() is None


def test_unresolvable_version_slot_is_not_comparable(tmp_path):
    """槽位别名解析不出来 -> 身份不明确 -> 不可比较（R1.1）。"""
    db = _demo_db(tmp_path)
    config = _config(tmp_path, simulation={"backend_version_aliases": {}})
    result = evaluate_plan(load_plan_request(config), db_path=db)
    native = _native_candidate(result)
    assert native.deployment.backend_version is None
    assert native.comparable is False
    assert any("版本不可确认" in r for r in native.comparability_reasons)
    assert result.simulation.recommended() is None


def test_resolve_backend_version_rejects_bare_slots():
    assert resolve_backend_version("current") is None
    assert resolve_backend_version("current", {"current": "0.24.0"}) == "0.24.0"
    assert resolve_backend_version("0.24.0") == "0.24.0"
    assert resolve_backend_version("v1.2.3") == "v1.2.3"
    assert resolve_backend_version(None) is None


# ---------------------------------------------------------------------------
# 每行自己的身份（不能共享一个全局身份）
# ---------------------------------------------------------------------------


def test_sweeper_rows_keep_their_own_backend_and_version(tmp_path):
    csv_path = _write_sweeper_csv(
        tmp_path,
        [
            {"backend": "vllm", "version": "0.24.0", "tokens/s": "900"},
            {"backend": "sglang", "version": "0.5.14", "tokens/s": "1000"},
        ],
    )
    candidates = parse_sweeper_csv(csv_path, serving_mode="agg")
    assert {(c.deployment.backend, c.deployment.backend_version) for c in candidates} == {
        ("vllm", "0.24.0"),
        ("sglang", "0.5.14"),
    }
    target = SimulationTarget(
        model_name="Qwen/Qwen3-32B", model_precision="bf16", system="h100_sxm",
        input_len=8192, output_len=256, concurrency=32,
        backend="vllm", backend_version="0.24.0",
    )
    for candidate in candidates:
        check_comparability(candidate, target)
    by_backend = {c.deployment.backend: c for c in candidates}
    assert by_backend["vllm"].comparable is True
    assert by_backend["sglang"].comparable is False


def test_replicas_is_not_treated_as_data_parallel(tmp_path):
    csv_path = _write_sweeper_csv(
        tmp_path, [{"replicas": "3", "dp": "1", "num_total_gpus": "8", "parallel": "tp8pp1dp1"}]
    )
    candidate = parse_sweeper_csv(csv_path)[0]
    assert candidate.deployment.replicas == 3
    assert candidate.deployment.dp_size == 1  # 不能被 replicas 覆盖


# ---------------------------------------------------------------------------
# GPU 数量上限
# ---------------------------------------------------------------------------


def test_gpu_cap_excludes_over_limit_candidates(tmp_path):
    db = _demo_db(tmp_path)
    config = _config(tmp_path, max_total_gpus=4)
    result = evaluate_plan(load_plan_request(config), db_path=db)
    native = _native_candidate(result)
    assert native.within_gpu_budget is False
    assert any("超过用户上限" in r for r in result.simulation.rejection_reasons(native))
    assert result.simulation.eligible() == []
    assert result.simulation.recommended() is None


def test_no_gpu_cap_means_no_assumption(tmp_path):
    """未给上限时不得默认取服务器卡数（应为 None，而不是 8）。"""
    db = _demo_db(tmp_path)
    config = _config(tmp_path)
    doc = yaml.safe_load(config.read_text(encoding="utf-8"))
    doc.pop("max_total_gpus", None)
    config.write_text(yaml.safe_dump(doc, allow_unicode=True), encoding="utf-8")
    result = evaluate_plan(load_plan_request(config), db_path=db)
    assert all(c.within_gpu_budget is None for c in result.simulation.candidates)


# ---------------------------------------------------------------------------
# 静态不可行
# ---------------------------------------------------------------------------


def test_static_infeasible_candidate_is_not_recommendable(tmp_path):
    """把可用显存压到装不下 8 卡 -> Sizing 判不可行 -> 不得推荐。"""
    db = _demo_db(tmp_path)
    config = _config(
        tmp_path,
        resources={
            "usable_fraction": "0.05",
            "usable_source_kind": "synthetic_example",
            "runtime_reserve_fraction": "0.1",
        },
    )
    result = evaluate_plan(load_plan_request(config), db_path=db)
    assert result.simulation is not None
    native = _native_candidate(result)
    assert native.resource_feasible is False
    assert any("不可行" in r or "infeasible" in r for r in native.resource_reasons)
    assert result.simulation.recommended() is None


def test_gpu_count_disagreement_between_tool_and_sizing_blocks_recommendation(tmp_path):
    """工具报 4 卡、Sizing 口径 8 卡 -> 口径打架，不推荐。"""
    csv_path = _write_sweeper_csv(tmp_path, [{"num_total_gpus": "4"}])
    db = _demo_db(tmp_path)
    config = _config(
        tmp_path,
        simulation={"sweeper_csv": str(csv_path), "native_prediction": None,
                    "native_input": None, "top_n": 3},
    )
    result = evaluate_plan(load_plan_request(config), db_path=db)
    candidate = result.simulation.candidates[0]
    assert candidate.sizing_gpu_count == 8
    assert candidate.resource_feasible is False
    assert any("卡数口径不一致" in r for r in candidate.resource_reasons)


# ---------------------------------------------------------------------------
# 拓扑不完整（本系统不建模）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "mode,tensor,pipeline,expect",
    [
        ("aggregated", 8, 2, "PP=2"),
        ("disaggregated", 8, 1, "PD 分离"),
    ],
)
def test_unsupported_topology_is_reference_only(tmp_path, mode, tensor, pipeline, expect):
    db = _demo_db(tmp_path)
    input_path, result_path = _write_native_pair(
        tmp_path, mode=mode, tensor=tensor, pipeline=pipeline
    )
    config = _config(
        tmp_path,
        simulation={"sweeper_csv": None, "native_input": str(input_path),
                    "native_prediction": str(result_path)},
    )
    result = evaluate_plan(load_plan_request(config), db_path=db)
    candidate = result.simulation.candidates[0]
    assert candidate.resource_feasible is False
    assert any(expect in r for r in candidate.resource_reasons)
    assert any("不宣称可部署" in r for r in candidate.resource_reasons)
    assert result.simulation.recommended() is None


def test_missing_tensor_parallel_blocks_recommendation(tmp_path):
    csv_path = _write_sweeper_csv(tmp_path, [{"tp": ""}])
    db = _demo_db(tmp_path)
    config = _config(
        tmp_path,
        simulation={"sweeper_csv": str(csv_path), "native_prediction": None,
                    "native_input": None},
    )
    result = evaluate_plan(load_plan_request(config), db_path=db)
    candidate = result.simulation.candidates[0]
    assert candidate.resource_feasible is False
    assert any("未给出 TP" in r for r in candidate.resource_reasons)


# ---------------------------------------------------------------------------
# 工作点不一致
# ---------------------------------------------------------------------------


def test_concurrency_mismatch_keeps_candidates_apart(tmp_path):
    db = _demo_db(tmp_path)
    config = _config(tmp_path, workload={
        "input_len": 8192, "output_len": 256, "concurrency": 48,
        "dataset": "random", "arrival_mode": "unbounded",
    })
    result = evaluate_plan(load_plan_request(config), db_path=db)
    candidates = result.simulation.candidates
    other_workpoints = [c for c in candidates if c.workload.concurrency != 48]
    assert other_workpoints, "归档里应当存在其它并发的工作点"
    for candidate in other_workpoints:
        assert candidate.comparable is False
        assert any("配置并发不同" in r for r in candidate.comparability_reasons)
    # 推荐池里只可能留下与目标同一工作点的候选（这里它们都缺 P99，所以池子仍是空的）
    assert all(
        c.workload.concurrency == 48 for c in result.simulation.judged()
    )
    assert result.simulation.eligible() == []


# ---------------------------------------------------------------------------
# 推荐池与"未达标参考配置"的措辞
# ---------------------------------------------------------------------------


def test_failing_candidate_is_labelled_reference_not_recommendation(tmp_path):
    from llm_capacity_pricing.reporting.plan_report import build_plan_report

    db = _demo_db(tmp_path)
    result = evaluate_plan(load_plan_request(SIM_CONFIG), db_path=db)
    assert result.simulation.recommended() is None
    assert result.simulation.best_effort() is not None
    report = build_plan_report(result)
    assert "**未达标参考配置（模拟）**" in report
    assert "**推荐配置（模拟）**" not in report


def test_no_eligible_candidate_says_so_explicitly(tmp_path):
    from llm_capacity_pricing.reporting.plan_report import build_plan_report

    db = _demo_db(tmp_path)
    config = _config(tmp_path, max_total_gpus=1)  # 所有候选都超限
    result = evaluate_plan(load_plan_request(config), db_path=db)
    assert result.simulation.best_effort() is None
    assert any(
        "超过用户上限" in r
        for candidate in result.simulation.candidates
        for r in result.simulation.rejection_reasons(candidate)
    )
    report = build_plan_report(result)
    assert "无符合条件的模拟候选" in report
    assert "没有模拟候选" not in report


# ---------------------------------------------------------------------------
# F1：成本排序的降级策略（部分候选缺成本时不得报错、不得误选不达标候选）
# ---------------------------------------------------------------------------


def _sim_result(candidates):
    from llm_capacity_pricing.schemas.simulation import SimulationResult

    return SimulationResult(status="computed", candidates=candidates)


def _cand(cid, *, verdict="predicted_pass", throughput="100", cost=...):
    from llm_capacity_pricing.schemas.simulation import (
        SimulatedCost,
        SimulationCandidate,
        SimulationDeployment,
        SimulationMetrics,
    )

    kwargs = {}
    if cost is not ...:
        kwargs["cost"] = cost
    return SimulationCandidate(
        candidate_id=cid,
        deployment=SimulationDeployment(model_name="Qwen/Qwen3-32B", model_precision="bf16",
                                        system="h100_sxm", gpu_count=8, tp_size=8),
        metrics=SimulationMetrics(
            sim_output_throughput_tok_s=Decimal(throughput) if throughput else None,
            sim_p99_ttft_ms=Decimal("100"),
            sim_p99_tpot_ms=Decimal("10"),
            sim_ttft_samples=500,
            sim_tpot_samples=500,
        ),
        verdict=verdict,
        comparable=True,
        resource_feasible=True,
        **kwargs,
    )


def _cost(amount, *, currency="CNY", basis="owned", horizon=3, incomplete=False):
    from llm_capacity_pricing.schemas.simulation import SimulatedCost

    return SimulatedCost(
        hardware_id="nvidia-h100-sxm-80gb",
        currency=currency,
        currency_confirmed=True,
        cost_basis=basis,
        horizon_years=horizon,
        total_cash_over_horizon=None if incomplete else Decimal(amount),
        incomplete=incomplete,
    )


def test_cost_ranking_unavailable_when_one_candidate_lacks_cost():
    """反例：A 有完整成本、B 缺成本 -> 不排序，且**不得**抛异常。"""
    cheap = _cand("sim:A", throughput="100", cost=_cost("100"))
    unknown = _cand("sim:B", throughput="200", cost=None)
    result = _sim_result([cheap, unknown])

    available, reasons = result.cost_ranking_available()
    assert available is False
    assert any("成本不完整" in r or "金额缺失" in r for r in reasons)
    # 不排序，但**仍然**从预测达标的候选里按吞吐推荐
    picked = result.recommended("cost")
    assert picked is unknown  # 吞吐更高；关键是它 predicted_pass，而不是 None
    assert picked.verdict == "predicted_pass"


def test_cost_ranking_picks_the_cheapest_when_all_comparable():
    expensive = _cand("sim:A", cost=_cost("200"))
    cheap = _cand("sim:B", cost=_cost("100"), throughput="50")
    result = _sim_result([expensive, cheap])
    available, _ = result.cost_ranking_available()
    assert available is True
    assert result.recommended("cost").candidate_id == "sim:B"


def test_cost_ranking_rejects_mixed_currency():
    cny = _cand("sim:A", cost=_cost("100", currency="CNY"))
    usd = _cand("sim:B", cost=_cost("50", currency="USD"))
    result = _sim_result([cny, usd])
    available, reasons = result.cost_ranking_available()
    assert available is False
    assert any("币种" in r for r in reasons)
    # 降级为吞吐，不返回 None
    assert result.recommended("cost") is not None


def test_cost_ranking_never_falls_back_to_a_failing_candidate():
    """反例：达标候选成本缺失时，不能因为降级就选一个不达标的更高吞吐候选。"""
    passing = _cand("sim:pass", throughput="10", cost=None)  # 达标但没成本
    failing = _cand("sim:fail", throughput="999", verdict="predicted_fail",
                    cost=_cost("100"))
    result = _sim_result([passing, failing])
    picked = result.recommended("cost")
    assert picked is passing
    assert picked.verdict == "predicted_pass"


def test_cost_downgrade_is_stated_in_the_report(tmp_path):
    """报告要说清"已降级为吞吐优先"，不能含糊成按成本最优。"""
    from llm_capacity_pricing.reporting.plan_report import build_plan_report

    db = _demo_db(tmp_path)
    config = _config(tmp_path, simulation={"ranking": "cost"})
    result = evaluate_plan(load_plan_request(config), db_path=db)
    report = build_plan_report(result)
    assert "成本排序**不可用**" in report
    assert "已降级为吞吐优先" in report
