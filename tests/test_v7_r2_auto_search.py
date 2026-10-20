"""V7-R2：自动搜索 -> 原生 P99 回放。

分两层：

* **纯函数层**（不需要安装工具）：搜索输入生成、对齐规则、回放输入生成、输出发现；
* **实跑层**：仅在设置了 ``AISIMULATE_PYTHON`` 且该环境可用时执行，否则 skip。
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from llm_capacity_pricing.perf.aisimulate_adapter import (
    ExternalToolConfig,
    SearchSpec,
    SimulationNotAvailable,
    build_command,
    build_predict_config,
    build_search_config,
    discover_outputs,
    external_tool_available,
    native_alignment_reasons,
    parse_sweeper_csv,
    resolve_executable,
    run_two_stage,
)
from llm_capacity_pricing.schemas.simulation import SimulationCandidate

ROOT = Path(__file__).resolve().parents[1]
SWEEPER_CSV = ROOT / "docs/poc/aisimulate/artifacts/matched_pareto_vllm_bf16.csv"


def _spec(**overrides) -> SearchSpec:
    base = dict(
        model_name="Qwen/Qwen3-32B",
        model_precision="bf16",
        system="h100_sxm",
        input_len=8192,
        output_len=256,
        concurrency=32,
        backend="vllm",
        backend_version="0.24.0",
        max_total_gpus=8,
        top_n=3,
    )
    base.update(overrides)
    return SearchSpec(**base)


# ---------------------------------------------------------------------------
# R2.1：由需求生成搜索输入
# ---------------------------------------------------------------------------


def test_search_config_is_derived_from_the_request():
    cfg = build_search_config(_spec())
    (name, entry), = cfg.items()
    assert name.startswith("auto_")
    assert entry["model_path"] == "Qwen/Qwen3-32B"
    assert entry["system_name"] == "h100_sxm"
    assert entry["backend_name"] == "vllm"
    assert entry["backend_version"] == "0.24.0"
    assert (entry["isl"], entry["osl"]) == (8192, 256)
    assert entry["serving_mode"] == "agg"
    assert entry["database_mode"] == "SILICON"


def test_search_config_bounds_the_search_space_by_the_gpu_cap():
    entry = next(iter(build_search_config(_spec(max_total_gpus=4)).values()))
    assert max(entry["agg_num_gpu_candidates"]) <= 4
    assert entry["total_gpus"] == 4
    entry = next(iter(build_search_config(_spec(max_total_gpus=None)).values()))
    assert entry["total_gpus"] == 8  # 未给上限时的默认搜索范围，**不是**用户上限


def test_search_bounds_are_deliberately_loose():
    """工具的 SLA 过滤是聚合均值口径；我们不用它筛，所以边界设到极松。"""
    entry = next(iter(build_search_config(_spec()).values()))
    assert entry["ttft"] >= 1e6
    assert entry["tpot"] >= 1e6


def test_search_config_requires_model_and_system():
    with pytest.raises(ValueError):
        build_search_config(_spec(model_name=""))
    with pytest.raises(ValueError):
        build_search_config(_spec(system=""))


# ---------------------------------------------------------------------------
# R2.2：对齐规则与回放输入
# ---------------------------------------------------------------------------


def _candidate(**overrides) -> SimulationCandidate:
    rows = [
        c
        for c in parse_sweeper_csv(SWEEPER_CSV)
        if c.workload.concurrency == 32 and c.workload.ctx_tokens == 8192
    ]
    assert rows
    candidate = rows[0]
    for key, value in overrides.items():
        if key in type(candidate.deployment).model_fields:
            setattr(candidate.deployment, key, value)
        elif key in type(candidate.workload).model_fields:
            setattr(candidate.workload, key, value)
        else:  # pragma: no cover - 测试写错时直接报出来
            raise AssertionError(f"未知字段 {key}")
    return candidate


def test_aligned_candidate_produces_a_predict_config():
    candidate = _candidate()
    assert native_alignment_reasons(candidate, _spec()) == []
    cfg = build_predict_config(_spec(), candidate, requests=200)
    engine = cfg["engine"]
    assert engine["backend"] == "vllm"
    assert engine["backend_version"] == "0.24.0"
    assert engine["workers"]["aggregated"]["parallelism"]["tensor"] == 8
    assert cfg["traffic"]["load"] == {"type": "concurrency", "concurrency": 32}
    assert cfg["traffic"]["source"]["input_tokens"] == 8192
    assert cfg["traffic"]["stop"] == {"requests": 200}


def test_ctx_tokens_difference_prevents_alignment():
    """每步上下文预算不同 = 不同口径，不能拿回放去"验证"这条搜索。"""
    candidate = _candidate(ctx_tokens=16384)
    reasons = native_alignment_reasons(candidate, _spec())
    assert any("ctx_tokens" in r for r in reasons)
    with pytest.raises(ValueError):
        build_predict_config(_spec(), candidate)


def test_data_parallel_cannot_be_aligned():
    candidate = _candidate(dp_size=2)
    assert any("DP=2" in r for r in native_alignment_reasons(candidate, _spec()))


def test_missing_or_mismatched_backend_version_prevents_alignment():
    candidate = _candidate(backend_version=None)
    assert any("版本未声明" in r for r in native_alignment_reasons(candidate, _spec()))
    candidate = _candidate(backend_version="0.23.0")
    assert any("后端版本不同" in r for r in native_alignment_reasons(candidate, _spec()))


def test_native_replay_forces_overwrite_so_reruns_do_not_fail():
    """输出目录归我们所有：重复运行不能因为"目录非空"而失败（实测踩到过）。"""
    argv = build_command("native_replay", config_arg="p.yaml", save_dir="o")
    assert "--overwrite" in argv
    # 扫描器不传：它按配置哈希写自己的子目录
    assert "--overwrite" not in build_command("sweeper", config_arg="x.yaml", save_dir="o")


def test_discover_outputs_finds_the_expected_files(tmp_path):
    (tmp_path / "a" / "b").mkdir(parents=True)
    (tmp_path / "a" / "b" / "pareto.csv").write_text("x\n", encoding="utf-8")
    found = discover_outputs(tmp_path, "sweeper")
    assert found["sweeper_csv"].endswith("pareto.csv")
    assert discover_outputs(tmp_path, "native_replay") == {}


def test_resolve_executable_reports_a_clear_error(tmp_path):
    with pytest.raises(SimulationNotAvailable):
        resolve_executable("sweeper", str(tmp_path / "nonexistent" / "python"))


# ---------------------------------------------------------------------------
# R2.2：工具不可用时给出可解释错误，并落盘 manifest
# ---------------------------------------------------------------------------


def test_run_two_stage_without_tool_reports_error_and_writes_manifest(tmp_path):
    result = run_two_stage(
        _spec(),
        tool_config=ExternalToolConfig(python="/nonexistent/python", enabled=True),
        work_dir=tmp_path / "work",
        tool_version="0.12.1",
    )
    assert result.status == "error"
    assert result.message
    manifest = json.loads((tmp_path / "work" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["errors"]
    # 搜索输入仍然被归档（即使没跑起来），便于事后复核
    assert "search_input.yaml" in manifest["inputs"]


def test_disabled_external_execution_is_reported_not_silently_ignored(tmp_path):
    """未开启外部执行时，流水线要给出可解释的 error（而不是假装算完了）。"""
    result = run_two_stage(
        _spec(),
        tool_config=ExternalToolConfig(python="/nonexistent/python", enabled=False),
        work_dir=tmp_path / "work",
    )
    assert result.status == "error"
    assert "未开启" in (result.message or "")


# ---------------------------------------------------------------------------
# R2.2 实跑（需要 AISIMULATE_PYTHON）
# ---------------------------------------------------------------------------

PYTHON = __import__("os").environ.get("AISIMULATE_PYTHON")
TOOL_READY = bool(
    PYTHON
    and external_tool_available(ExternalToolConfig(python=PYTHON, enabled=True))
)


@pytest.mark.skipif(not TOOL_READY, reason="未设置可用的 AISIMULATE_PYTHON")
def test_live_two_stage_search_then_replay(tmp_path):
    """真实跑一遍：搜索 -> 过滤 -> Top-N -> 原生 P99，并检查工作点关联。"""
    work = tmp_path / "work"
    result = run_two_stage(
        _spec(top_n=2, backend="vllm", backend_version="0.24.0"),
        tool_config=ExternalToolConfig(python=PYTHON, enabled=True, timeout_s=900),
        work_dir=work,
        predict_requests=100,
        tool_version=None,
    )
    assert result.status == "computed", result.message
    natives = [c for c in result.candidates if c.candidate_id.startswith("sim-native")]
    # Top-N 至多 3 条；这里要求至少能对 1 条完成回放
    assert 1 <= len(natives) <= 3
    for candidate in natives:
        assert candidate.linked_search_candidate_id, "回放结果必须能回关联到搜索工作点"
        assert candidate.metrics.sim_p99_ttft_ms is not None
        assert candidate.metrics.sim_ttft_samples
    manifest = json.loads((work / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["inputs"] and manifest["outputs"]
    # 所有归档的输入/输出都带 sha256
    assert all(len(v) == 64 for v in manifest["inputs"].values())
    assert all(len(v) == 64 for v in manifest["outputs"].values())
