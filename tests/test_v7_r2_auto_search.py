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
    manifest = json.loads((_only_run_dir(tmp_path / "work") / "manifest.json").read_text(encoding="utf-8"))
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
    manifest = json.loads((_only_run_dir(work) / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["inputs"] and manifest["outputs"]
    # 所有归档的输入/输出都带 sha256
    assert all(len(v) == 64 for v in manifest["inputs"].values())
    assert all(len(v) == 64 for v in manifest["outputs"].values())


# ---------------------------------------------------------------------------
# F2/F3：超时隔离 + 先判对齐再取 Top-N（用假工具，不需要真装 AISimulate）
# ---------------------------------------------------------------------------

CSV_COLUMNS = (
    "model,isl,osl,concurrency,ctx_tokens,num_total_gpus,tp,pp,dp,replicas,"
    "parallel,ttft,tpot,tokens/s,tokens/s/gpu,tokens/s/user,seq/s,"
    "gemm,kvcache,fmha,moe,comm,backend,version,system"
)


def _csv_rows(rows: list[dict]) -> str:
    base = {
        "model": "Qwen/Qwen3-32B",
        "isl": "8192",
        "osl": "256",
        "concurrency": "32",
        "ctx_tokens": "8192",
        "num_total_gpus": "8",
        "tp": "8",
        "pp": "1",
        "dp": "1",
        "replicas": "1",
        "parallel": "tp8pp1dp1",
        "ttft": "370.0",
        "tpot": "30.6",
        "tokens/s": "990.0",
        "tokens/s/gpu": "124.0",
        "tokens/s/user": "32.0",
        "seq/s": "3.8",
        "gemm": "bfloat16",
        "kvcache": "bfloat16",
        "fmha": "bfloat16",
        "moe": "bfloat16",
        "comm": "half",
        "backend": "vllm",
        "version": "0.24.0",
        "system": "h100_sxm",
    }
    lines = [CSV_COLUMNS]
    for row in rows:
        values = dict(base)
        values.update({k: str(v) for k, v in row.items()})
        lines.append(",".join(values[c] for c in CSV_COLUMNS.split(",")))
    return "\n".join(lines) + "\n"


def _install_fake_tool(monkeypatch, csv_text: str, *, timeout_on_call: int | None = None):
    """替换 run_external：sweeper 直接吐 CSV，native 直接吐归档的 prediction.json。"""
    import shutil
    import subprocess

    import llm_capacity_pricing.perf.aisimulate_adapter as adapter

    state = {"predict": 0}

    def fake(surface, *, config_arg, save_dir=None, extra=(), config=None, environ=None):
        out = Path(save_dir)
        out.mkdir(parents=True, exist_ok=True)
        if surface == "sweeper":
            (out / "pareto.csv").write_text(csv_text, encoding="utf-8")
            return out
        state["predict"] += 1
        if timeout_on_call is not None and state["predict"] == timeout_on_call:
            raise subprocess.TimeoutExpired(cmd=["aisimulate"], timeout=1)
        shutil.copy(
            ROOT / "docs/poc/aisimulate/17_matched_native_result_tp8_conc32_bf16.json",
            out / "prediction.json",
        )
        return out

    monkeypatch.setattr(adapter, "run_external", fake)
    return state


def _run_dirs(work: Path) -> list[Path]:
    return sorted((work / "runs").glob("run_*"))


def _only_run_dir(work: Path) -> Path:
    dirs = _run_dirs(work)
    assert len(dirs) == 1, f"期望恰好一个运行目录，实际 {len(dirs)}"
    return dirs[0]


def _fake_spec(**overrides) -> SearchSpec:
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
        top_n=2,
    )
    base.update(overrides)
    return SearchSpec(**base)


def test_timeout_on_one_candidate_does_not_stop_the_others(tmp_path, monkeypatch):
    """反例：第一条回放超时，第二条仍要跑完并入库（F2）。"""
    csv_text = _csv_rows([
        {"tp": "8", "tokens/s": "990"},
        {"tp": "4", "tokens/s": "633", "num_total_gpus": "4", "parallel": "tp4pp1dp1"},
    ])
    _install_fake_tool(monkeypatch, csv_text, timeout_on_call=1)

    result = run_two_stage(
        _fake_spec(), tool_config=ExternalToolConfig(python="/fake", enabled=True),
        work_dir=tmp_path / "work",
    )
    manifest = json.loads((_only_run_dir(tmp_path / "work") / "manifest.json").read_text(encoding="utf-8"))
    natives = [c for c in result.candidates if c.candidate_id.startswith("sim-native")]
    assert len(natives) == 1, "超时的那条被跳过，第二条应当成功"
    assert any("TimeoutExpired" in e for e in manifest["errors"])
    assert result.status == "computed"


def test_timeout_is_converted_to_a_controlled_error(monkeypatch):
    """subprocess 超时要转成受控异常，而不是把 TimeoutExpired 抛给上层。"""
    import subprocess
    import sys

    import llm_capacity_pricing.perf.aisimulate_adapter as adapter

    def boom(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=["aisimulate"], timeout=1)

    # 跳过可执行文件解析（本环境没装工具），直接验证 subprocess 层的行为
    monkeypatch.setattr(adapter, "resolve_executable", lambda surface, python=None: ["/bin/true"])
    original = adapter.subprocess.run
    adapter.subprocess.run = boom
    try:
        with pytest.raises(SimulationNotAvailable) as excinfo:
            adapter.run_external(
                "sweeper", config_arg="x.yaml", save_dir="/tmp/never",
                config=ExternalToolConfig(python=sys.executable, enabled=True),
            )
        assert "超时" in str(excinfo.value)
    finally:
        adapter.subprocess.run = original


def test_unaligned_candidates_do_not_consume_the_replay_quota(tmp_path, monkeypatch):
    """反例：吞吐最高但 ctx_tokens 不一致的候选不得占用 Top-N 名额（F3）。

    构造三条：A 吞吐最高但 ctx=16384（不可对齐）；B 次高且可对齐；C 最低。
    top_n=1 时必须回放 B。
    """
    csv_text = _csv_rows([
        {"tp": "8", "tokens/s": "1500", "ctx_tokens": "16384", "parallel": "tp8pp1dp1"},
        {"tp": "4", "tokens/s": "900", "num_total_gpus": "4", "parallel": "tp4pp1dp1"},
        {"tp": "2", "tokens/s": "500", "num_total_gpus": "2", "parallel": "tp2pp1dp1"},
    ])
    _install_fake_tool(monkeypatch, csv_text)

    result = run_two_stage(
        _fake_spec(top_n=1), tool_config=ExternalToolConfig(python="/fake", enabled=True),
        work_dir=tmp_path / "work",
    )
    manifest = json.loads((_only_run_dir(tmp_path / "work") / "manifest.json").read_text(encoding="utf-8"))
    natives = [c for c in result.candidates if c.candidate_id.startswith("sim-native")]
    assert len(natives) == 1
    assert "tp4" in (natives[0].linked_search_candidate_id or ""), natives[0].linked_search_candidate_id
    # 不可对齐的那条仍然留在结果里，并说明原因
    assert any("tp8" in key for key in manifest["not_aligned"])
    non_aligned = [
        c for c in result.candidates
        if c.candidate_id.startswith("sim-sweeper") and c.link_reasons
    ]
    assert non_aligned and any("ctx_tokens" in r for c in non_aligned for r in c.link_reasons)


def test_context_budget_is_not_a_search_constraint_but_a_filter():
    """F4：扫描器**没有**以请求并发为搜索约束的字段，只能靠结果过滤。

    这条用例把这件事固定下来，避免以后误以为"搜索是按并发 32 优化的"。
    """
    entry = next(iter(build_search_config(_spec()).values()))
    # 搜索空间里只有 GPU/TP/PP/DP/MoE/CP 维度，没有请求并发维度
    assert not any("concurren" in key for key in entry)
    assert "decode_max_batch_size" not in entry


# ---------------------------------------------------------------------------
# F6-2：运行目录隔离（每次运行独立目录，不跨运行补结果）
# ---------------------------------------------------------------------------


def test_each_run_gets_its_own_directory(tmp_path, monkeypatch):
    csv_text = _csv_rows([{"tp": "8"}, {"tp": "4", "num_total_gpus": "4"}])
    _install_fake_tool(monkeypatch, csv_text)
    work = tmp_path / "work"

    first = run_two_stage(
        _fake_spec(), tool_config=ExternalToolConfig(python="/fake", enabled=True), work_dir=work
    )
    second = run_two_stage(
        _fake_spec(), tool_config=ExternalToolConfig(python="/fake", enabled=True), work_dir=work
    )

    dirs = _run_dirs(work)
    assert len(dirs) == 2, "两次运行必须各有一个独立目录"
    assert first.source_dir != second.source_dir
    assert Path(first.source_dir).name.startswith("run_")
    # 结果里的证据路径都指向本次运行目录
    for candidate in first.candidates:
        if candidate.source_file:
            assert str(first.source_dir) in candidate.source_file


def test_second_run_never_reads_the_first_runs_artifacts(tmp_path, monkeypatch):
    """A 成功、B 失败时，B 不得拿 A 的旧预测文件伪装成功。"""
    good_csv = _csv_rows([{"tp": "8"}])
    _install_fake_tool(monkeypatch, good_csv)
    work = tmp_path / "work"
    first = run_two_stage(
        _fake_spec(), tool_config=ExternalToolConfig(python="/fake", enabled=True), work_dir=work
    )
    assert [c for c in first.candidates if c.candidate_id.startswith("sim-native")]

    # 第二次：工具在搜索阶段就失败
    import llm_capacity_pricing.perf.aisimulate_adapter as adapter

    def failing(surface, *, config_arg, save_dir=None, extra=(), config=None, environ=None):
        raise adapter.SimulationNotAvailable("模拟工具故障")

    monkeypatch.setattr(adapter, "run_external", failing)
    second = run_two_stage(
        _fake_spec(), tool_config=ExternalToolConfig(python="/fake", enabled=True), work_dir=work
    )

    assert second.status == "error"
    assert second.candidates == []
    assert second.source_dir != first.source_dir
    run_b = Path(second.source_dir)
    manifest_b = json.loads((run_b / "manifest.json").read_text(encoding="utf-8"))
    # B 的清单里不能出现 A 目录下的任何文件
    assert all(str(run_b) in path or not str(path).startswith("/") for path in manifest_b["inputs"])
    assert manifest_b["outputs"] == {}


def test_manifests_and_hashes_are_independent_per_run(tmp_path, monkeypatch):
    csv_text = _csv_rows([{"tp": "8"}])
    _install_fake_tool(monkeypatch, csv_text)
    work = tmp_path / "work"
    for _ in range(2):
        run_two_stage(
            _fake_spec(), tool_config=ExternalToolConfig(python="/fake", enabled=True), work_dir=work
        )
    first, second = _run_dirs(work)
    m1 = json.loads((first / "manifest.json").read_text(encoding="utf-8"))
    m2 = json.loads((second / "manifest.json").read_text(encoding="utf-8"))
    assert m1["run_id"] != m2["run_id"]
    assert m1["run_dir"] != m2["run_dir"]
    # 输入内容相同（同配置），但归档路径与运行 ID 独立
    assert set(m1["inputs"]) == set(m2["inputs"])
    assert all(len(v) == 64 for v in m1["inputs"].values())
    assert all(len(v) == 64 for v in m2["inputs"].values())


def test_run_directory_is_not_created_by_deleting_old_ones(tmp_path, monkeypatch):
    """新运行不得删除既有运行目录（不通过清空 work_dir 解决覆盖）。"""
    _install_fake_tool(monkeypatch, _csv_rows([{"tp": "8"}]))
    work = tmp_path / "work"
    run_two_stage(
        _fake_spec(), tool_config=ExternalToolConfig(python="/fake", enabled=True), work_dir=work
    )
    marker = _run_dirs(work)[0] / "keep_me.txt"
    marker.write_text("x", encoding="utf-8")
    run_two_stage(
        _fake_spec(), tool_config=ExternalToolConfig(python="/fake", enabled=True), work_dir=work
    )
    assert marker.exists(), "旧运行目录不得被删除"
    assert len(_run_dirs(work)) == 2
