"""P1–P2：AISimulate 模拟结果的契约、解析与离线适配器。

验收对应：

* P1：能完整解析仓库已归档的 ``best_config_topn.csv``、``pareto.csv`` 与
  ``prediction.json``，且**没有数值或单位混淆**；
* P2：在没有 GPU、也没有安装 AISimulate 的机器上，能仅凭归档数据复现模拟结果展示。

本文件里的所有断言都对照**归档原文的数值**，不重新解释口径。
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from llm_capacity_pricing.perf.aisimulate_adapter import (
    MIN_SAMPLES_FOR_P99,
    ExternalToolConfig,
    SimulationNotAvailable,
    SimulationTarget,
    build_command,
    check_comparability,
    external_tool_available,
    load_archived,
    parse_native_input_yaml,
    parse_native_prediction,
    parse_sweeper_csv,
    resolve_python,
    run_external,
)
from llm_capacity_pricing.schemas.simulation import (
    SIMULATION_TRUST_LABEL,
    SimulationCandidate,
    SimulationMetrics,
)

POC = Path("docs/poc/aisimulate")
ARTIFACTS = POC / "artifacts"

#: 目标：与 Demo 一致（Qwen3-32B bf16 @ H100 SXM，8K/256/并发 32）
TARGET = SimulationTarget(
    model_name="Qwen/Qwen3-32B",
    model_precision="bf16",
    system="h100_sxm",
    input_len=8192,
    output_len=256,
    concurrency=32,
)


def _load_bf16(target: SimulationTarget = TARGET):
    return load_archived(
        sweeper_csv=ARTIFACTS / "matched_pareto_vllm_bf16.csv",
        native_prediction=POC / "17_matched_native_result_tp8_conc32_bf16.json",
        native_input=POC / "16_matched_native_input_tp8_conc32_bf16.yaml",
        native_model_precision="bf16",
        # 归档输入写的是 backend_version: current；槽位别名必须解析成实际版本，
        # 否则身份不明确、不可比较（R1.1）。0.24.0 来自工具自带 query_versions.yaml。
        version_aliases={"current": "0.24.0"},
        exp_config=ARTIFACTS / "matched_exp_config_vllm_bf16.yaml",
        per_ops_source=ARTIFACTS / "per_ops_source.json",
        tool_version="0.12.1",
        target=target,
    )


# ---------------------------------------------------------------------------
# P1：解析扫描器 CSV
# ---------------------------------------------------------------------------


def test_parse_sweeper_topn_csv_known_values():
    """逐字段对照归档 CSV 的原始数值 —— 没有换算、没有单位错位。"""
    candidates = parse_sweeper_csv(ARTIFACTS / "best_config_topn.csv")
    assert len(candidates) == 1
    c = candidates[0]
    # 原值原样（ms 就是 ms，tok/s 就是 tok/s 集群级）
    assert c.metrics.sim_mean_ttft_ms == Decimal("397.915")
    assert c.metrics.sim_mean_tpot_ms == Decimal("11.507")
    assert c.metrics.sim_output_throughput_tok_s == Decimal("978.221")
    assert c.metrics.sim_tokens_per_gpu == Decimal("122.278")
    assert c.metrics.sim_tokens_per_user == Decimal("86.903")
    assert c.metrics.sim_request_latency_ms == Decimal("3332.237")
    # 身份
    assert c.deployment.model_name == "Qwen/Qwen3-32B-FP8"
    assert c.deployment.system == "h100_sxm"
    assert c.deployment.gpu_count == 8
    assert c.deployment.tp_size == 8
    assert c.deployment.parallel == "tp8pp1dp1etp1ep1"
    assert c.workload.input_len == 8192 and c.workload.output_len == 256
    assert c.workload.concurrency == 13
    # 精度：名字带 -FP8 -> 直接取；量化列原样留存
    assert c.deployment.model_precision == "fp8"
    assert c.deployment.quant_modes["gemm"] == "fp8_block"
    # 溯源
    assert c.source_file.endswith("best_config_topn.csv")
    assert c.source_sha256 and len(c.source_sha256) == 64


def test_parse_pareto_csv_workload_not_stored_in_measured_fields():
    """``tokens/s`` 是**集群级**吞吐：不得被写进任何"单卡/实测"字段名下。"""
    metrics = SimulationMetrics.model_fields
    assert "sim_output_throughput_tok_s" in metrics
    assert "output_throughput_tok_s" not in metrics
    for name in metrics:
        assert name.startswith("sim_"), f"模拟指标必须带 sim_ 前缀：{name}"


def test_two_rows_with_same_identity_but_different_workpoint_stay_distinct():
    """反例：同一身份、不同每步上下文预算 → **必须**是不同的候选。

    ``ctx_tokens``（每步上下文 token 预算）会改变 TTFT 达 73%，
    但它不在模型/硬件身份里；不记录它会让两条"看起来一样"的配置互相矛盾。
    """
    rows = [
        c
        for c in parse_sweeper_csv(ARTIFACTS / "matched_pareto_vllm_bf16.csv")
        if c.workload.concurrency == 32
    ]
    assert len(rows) == 2, "归档里 concurrency=32 应有两行（ctx 8192 / 16384）"
    by_ctx = {c.workload.ctx_tokens: c for c in rows}
    assert set(by_ctx) == {8192, 16384}
    assert by_ctx[8192].metrics.sim_mean_ttft_ms == Decimal("370.447")
    assert by_ctx[16384].metrics.sim_mean_ttft_ms == Decimal("640.81")
    # 身份列完全一致……
    assert {c.deployment.parallel for c in rows} == {"tp8pp1dp1etp1ep1"}
    assert {c.deployment.gpu_count for c in rows} == {8}
    # ……但候选 ID 必须不同，否则回溯时会张冠李戴
    assert len({c.candidate_id for c in rows}) == 2


def test_serving_mode_recovered_from_path_or_declaration():
    """CSV 没有 serving_mode 列：从声明（exp_config）恢复，不猜测。"""
    c = parse_sweeper_csv(
        ARTIFACTS / "matched_pareto_vllm_bf16.csv", serving_mode="agg"
    )[0]
    assert c.deployment.serving_mode == "agg"


# ---------------------------------------------------------------------------
# P1：解析原生回放
# ---------------------------------------------------------------------------


def test_parse_native_prediction_keeps_mean_median_p99_separate():
    deployment, workload = parse_native_input_yaml(
        POC / "16_matched_native_input_tp8_conc32_bf16.yaml", model_precision="bf16"
    )
    assert deployment.tp_size == 8
    assert deployment.model_precision == "bf16"
    assert workload.concurrency == 32
    assert workload.input_len == 8192 and workload.output_len == 256
    assert workload.stop_condition == "requests=500"

    c = parse_native_prediction(
        POC / "17_matched_native_result_tp8_conc32_bf16.json",
        deployment=deployment,
        workload=workload,
    )
    # 三个口径分开存，**没有**用均值顶替 P99
    assert c.metrics.sim_mean_ttft_ms == Decimal("548.0465038100006")
    assert c.metrics.sim_median_ttft_ms == Decimal("375.34602000000086")
    assert c.metrics.sim_p99_ttft_ms == Decimal("5201.827252000001")
    assert c.metrics.sim_mean_tpot_ms == Decimal("30.065770863803973")
    assert c.metrics.sim_p99_tpot_ms == Decimal("31.45861707450993")
    # 样本数必须一起存：P99 没有样本数就无法判断可信度
    assert c.metrics.sim_ttft_samples == 500
    assert c.metrics.sim_completed_requests == 500
    # 吞吐：原值保留，另外给出按卡数归一化值，不改写原值
    assert c.metrics.sim_output_throughput_tok_s == Decimal("990.888548064229")
    assert c.metrics.sim_tokens_per_gpu == (Decimal("990.888548064229") / 8).quantize(
        Decimal("0.001")
    )
    assert c.deployment.gpu_count == 8


# ---------------------------------------------------------------------------
# P1：判定与可比性（全部由我们自己的阈值说了算）
# ---------------------------------------------------------------------------


def test_sweeper_candidates_can_never_produce_a_p99_verdict():
    """扫描器只有稳态单点：一律 ``insufficient_prediction``，并说明为什么。"""
    target = TARGET
    target = SimulationTarget(
        **{**TARGET.__dict__, "ttft_p99_ms_max": Decimal("600"),
           "tpot_p99_ms_max": Decimal("30")}
    )
    result = _load_bf16(target)
    sweeper = [c for c in result.candidates if c.candidate_id.startswith("sim-sweeper")]
    assert sweeper
    for c in sweeper:
        assert c.verdict == "insufficient_prediction"
        assert any("聚合均值" in r or "均值口径" in r for r in c.verdict_reasons)
    # 因此推荐只能在有 P99 的候选里产生
    assert all(not c.candidate_id.startswith("sim-sweeper") for c in result.judged())


def test_mean_passes_but_p99_fails_must_be_predicted_fail():
    """反例 #4：均值达标而 P99 超限 -> 必须 ``predicted_fail``，且点名这个陷阱。

    阈值取 TTFT 600ms / TPOT 32ms：此时**均值全部达标**
    （TTFT 548.05、TPOT 30.07），而 TTFT P99 = 5201.83 远超 600ms。
    也就是说：只看均值会判"通过"，看 P99 才判"不通过"。

    注意这个配置里 TPOT 的均值与 P99 很接近（30.07 / 31.46，两者都在界内）——
    尾部偏差集中在 **TTFT**，这是 prefill/排队受并发影响远大于 decode 的直接体现。
    """
    target = SimulationTarget(
        model_name="Qwen/Qwen3-32B",
        model_precision="bf16",
        system="h100_sxm",
        input_len=8192,
        output_len=256,
        concurrency=32,
        ttft_p99_ms_max=Decimal("600"),
        tpot_p99_ms_max=Decimal("32"),
    )
    result = _load_bf16(target)
    native = result.judged()[0]
    assert native.metrics.sim_mean_ttft_ms <= Decimal("600")  # 均值看起来达标
    assert native.metrics.sim_mean_tpot_ms <= Decimal("32")
    assert native.metrics.sim_p99_ttft_ms > Decimal("600")  # TTFT 的 P99 超限
    assert native.metrics.sim_p99_tpot_ms <= Decimal("32")  # TPOT 均值与 P99 都在界内
    assert native.verdict == "predicted_fail", native.verdict_reasons
    # 纯适配器路径没有 Sizing 校验 -> 不进推荐池（R1.3）
    assert result.eligible() == []
    assert any("口径提示" in r and "P99 超限" in r for r in native.verdict_reasons)


def test_predicted_pass_only_when_p99_within_both_bounds():
    target = SimulationTarget(
        model_name="Qwen/Qwen3-32B",
        model_precision="bf16",
        system="h100_sxm",
        input_len=8192,
        output_len=256,
        concurrency=32,
        ttft_p99_ms_max=Decimal("6000"),
        tpot_p99_ms_max=Decimal("32"),
    )
    result = _load_bf16(target)
    native = result.judged()[0]
    assert native.verdict == "predicted_pass"
    # 适配器本身不做 Sizing 校验；按 R1.3，资源未校验的候选不进正式推荐池。
    # 这里显式声明"已校验"，用于验证判定逻辑本身。
    assert result.recommended() is None
    native.resource_feasible = True
    assert result.recommended() is native


def test_no_threshold_means_no_verdict():
    result = _load_bf16(TARGET)  # 没有给任何 P99 阈值
    for c in result.candidates:
        assert c.verdict == "insufficient_prediction"
    assert result.recommended() is None
    assert result.best_effort() is None


def test_insufficient_samples_cannot_yield_a_verdict():
    """样本不足时 P99 无意义（官方文档：零样本不是"很快"）。"""
    deployment, workload = parse_native_input_yaml(
        POC / "16_matched_native_input_tp8_conc32_bf16.yaml", model_precision="bf16"
    )
    c = parse_native_prediction(
        POC / "17_matched_native_result_tp8_conc32_bf16.json",
        deployment=deployment,
        workload=workload,
    )
    c.metrics.sim_ttft_samples = MIN_SAMPLES_FOR_P99 - 1
    target = SimulationTarget(
        ttft_p99_ms_max=Decimal("6000"), tpot_p99_ms_max=Decimal("32")
    )
    from llm_capacity_pricing.perf.aisimulate_adapter import assess_verdict

    assess_verdict(c, target)
    assert c.verdict == "insufficient_prediction"
    assert any("样本不足" in r for r in c.verdict_reasons)


def test_precision_mismatch_blocks_comparison():
    """反例 #5：精度不同不得直接比较（Demo 目标是 bf16，PoC 里也有 fp8 结果）。"""
    fp8_sweeper = parse_sweeper_csv(ARTIFACTS / "matched_pareto_vllm_fp8.csv")[0]
    assert fp8_sweeper.deployment.model_precision == "fp8"
    check_comparability(fp8_sweeper, TARGET)
    assert fp8_sweeper.comparable is False
    assert any("精度不同" in r for r in fp8_sweeper.comparability_reasons)


def test_model_and_hardware_mismatch_block_comparison():
    c = parse_sweeper_csv(ARTIFACTS / "matched_pareto_vllm_bf16.csv")[0]
    wrong = SimulationTarget(
        model_name="Qwen/Qwen3-235B-A22B-Instruct-2507",
        model_precision="bf16",
        system="b200_sxm",
        input_len=8192,
        output_len=256,
        concurrency=c.workload.concurrency,
    )
    check_comparability(c, wrong)
    assert c.comparable is False
    joined = "；".join(c.comparability_reasons)
    assert "模型不同" in joined and "硬件不同" in joined


def test_concurrency_mismatch_blocks_comparison():
    """不同工作点的时延不可直接比较。"""
    c = [
        x
        for x in parse_sweeper_csv(ARTIFACTS / "matched_pareto_vllm_bf16.csv")
        if x.workload.concurrency == 32
    ][0]
    other = SimulationTarget(
        model_name="Qwen/Qwen3-32B",
        model_precision="bf16",
        system="h100_sxm",
        input_len=8192,
        output_len=256,
        concurrency=64,
    )
    check_comparability(c, other)
    assert c.comparable is False
    assert any("配置并发不同" in r for r in c.comparability_reasons)


def test_unknown_precision_is_not_treated_as_matching():
    """未知 ≠ 相同：精度缺失时判为不可比较，而不是"没矛盾就算一致"。"""
    c = parse_sweeper_csv(ARTIFACTS / "matched_pareto_vllm_bf16.csv")[0]
    c.deployment.model_precision = None
    check_comparability(c, TARGET)
    assert c.comparable is False
    assert any("精度未声明" in r for r in c.comparability_reasons)


# ---------------------------------------------------------------------------
# P1/P2：与实测的结构性隔离
# ---------------------------------------------------------------------------


def test_simulation_schema_cannot_express_measured_fields():
    """模拟对象在**结构上**无法承载实测字段名，因此不可能污染实测结论。"""
    assert "ttft_p99_ms" not in SimulationMetrics.model_fields
    assert "output_throughput_tok_s" not in SimulationMetrics.model_fields
    # 模拟对象只有自己那套 verdict/状态，不会有实测 SLA 状态字段
    assert "sla_status" not in SimulationCandidate.model_fields
    assert "match_level" not in SimulationCandidate.model_fields
    assert "rank_bucket" not in SimulationCandidate.model_fields


def test_simulation_module_does_not_import_measured_schemas():
    """源码级保证：模拟契约不 import 实测 schema（否则就可能被当作实测对象使用）。"""
    for relative in (
        "src/llm_capacity_pricing/schemas/simulation.py",
        "src/llm_capacity_pricing/perf/aisimulate_adapter.py",
    ):
        source = Path(relative).read_text(encoding="utf-8")
        # 不许 import 实测 schema
        assert "from llm_capacity_pricing.schemas.performance import" not in source
        assert "import llm_capacity_pricing.schemas.performance" not in source
        # 不许构造实测对象
        for ctor in ("BenchmarkRun(", "CapacityPoint(", "PerformanceProfile("):
            assert ctor not in source, f"{relative} 构造了实测对象 {ctor}"
        # 不许出现实测状态取值
        assert '"observed_pass"' not in source
        assert '"observed_fail"' not in source


def test_simulation_result_is_labelled_simulated():
    result = _load_bf16(TARGET)
    assert result.data_trust == SIMULATION_TRUST_LABEL == "simulated_prediction"
    assert any("不得写入 BenchmarkRun" in l for l in result.limitations)


# ---------------------------------------------------------------------------
# P2：离线可用 + 外部命令默认关闭
# ---------------------------------------------------------------------------


def test_offline_load_reproduces_the_display_without_the_tool():
    """P2 验收：没有 GPU、没有 AISimulate 也能复现模拟结果展示。"""
    result = _load_bf16(TARGET)
    assert result.status == "computed"
    assert result.tool.backend == "vllm"
    assert result.tool.backend_version == "0.24.0"
    assert result.tool.database_mode == "SILICON"
    assert result.tool.tool_version == "0.12.1"
    # 修正系数随证据留存（启发式，不是实测）
    assert result.tool.correction_factors["autoscale_ttft_correction_factor"] == Decimal("1.8")
    # 逐算子来源：SILICON 模式也含 empirical 算子
    assert result.tool.data_provenance
    assert any(v == "empirical" for v in result.tool.data_provenance.values())
    assert any("经验算子" in l for l in result.limitations)
    assert any("经验修正系数" in l for l in result.limitations)


def test_external_execution_is_disabled_by_default():
    with pytest.raises(SimulationNotAvailable):
        run_external("sweeper", config_arg="x.yaml", save_dir="out")
    assert external_tool_available(None) is False
    assert external_tool_available(ExternalToolConfig(enabled=False)) is False


def test_resolve_python_returns_none_when_tool_absent():
    """不带环境变量、PATH 上没有 aisimulate 时，必须返回 None（而不是瞎猜）。"""
    assert resolve_python(None, environ={}) is None
    assert resolve_python(ExternalToolConfig(python="/nonexistent/python"), environ={}) is None


def test_build_command_is_argv_and_never_a_shell_string():
    """命令用 argv 列表构造；带 shell 元字符的路径只是**一个参数**，不会被解释。"""
    nasty = "out; rm -rf /tmp/x && echo pwned"
    argv = build_command("sweeper", config_arg=nasty, save_dir=nasty)
    assert isinstance(argv, list)
    assert all(isinstance(part, str) for part in argv)
    assert nasty in argv  # 原样作为一个元素
    native = build_command("native_replay", config_arg="p.yaml", save_dir="o")
    assert native[:3] == ["-m", "aisimulate", "predict"]
    assert "--stack" in native and "engine" in native
    with pytest.raises(ValueError):
        build_command("nope", config_arg="x")


def test_native_result_requires_its_input_identity():
    """原生结果里没有身份：不给输入 YAML 就必须报错，而不是编一个身份。"""
    with pytest.raises(ValueError):
        load_archived(
            native_prediction=POC / "17_matched_native_result_tp8_conc32_bf16.json"
        )


def test_simulation_target_treats_missing_identity_as_unknown():
    """目标身份缺失时不得给出"可比"结论。"""
    c = parse_sweeper_csv(ARTIFACTS / "matched_pareto_vllm_bf16.csv")[0]
    check_comparability(c, SimulationTarget())
    assert c.comparable is False
    assert c.comparability_reasons
