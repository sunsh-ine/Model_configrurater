"""M37（压测实验清单）与 M38（导入一致性校验）测试。

M37 验收：同一实验记录可以从采集结果追溯到具体部署配置；
缺失关键条件的测试不会被判为 exact_match。
M38 验收：相同数据重复导入结果稳定；字段或指标缺失时输出可解释原因，不伪造性能值。
"""

from __future__ import annotations

import json
import re
from decimal import Decimal
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from llm_capacity_pricing.cli import app
from llm_capacity_pricing.perf.experiment_planner import plan_benchmarks
from llm_capacity_pricing.perf.import_consistency import (
    check_batch_consistency,
    check_import_consistency,
    check_run_consistency,
)
from llm_capacity_pricing.schemas.experiment import (
    DEFAULT_EXPERIMENT_GRID,
    FLAG_SOURCES,
)
from llm_capacity_pricing.services import (
    check_import_consistency_for,
    import_hardware,
    import_models,
    import_performance_curve,
    import_performance_run,
    init_db,
    make_assumptions,
    open_db,
    verify_reimport_is_stable,
)

runner = CliRunner()
REPO = Path(__file__).resolve().parents[1]


def _db(tmp_path: Path, root: Path, name: str = "m3738.db") -> str:
    db = str(tmp_path / name)
    init_db(db)
    import_models(root / "data/models/model_manifest.yaml", db, root=root)
    import_hardware(root / "data/hardware/hardware_catalog.yaml", db)
    return db


def _assumptions():
    return make_assumptions(
        usable_fraction_of_nominal=Decimal("0.9"),
        usable_memory_source_kind="synthetic_example",
        runtime_reserve_fraction=Decimal("0.1"),
        global_concurrency=32,
        context_length_per_request=8192,
        tp_sizes=(4,),
        dp_sizes=(1,),
    )


def _plan(db: str, tmp_path: Path, **overrides):
    from llm_capacity_pricing.services import plan_benchmarks_for

    kwargs = dict(
        model_id="qwen3-32b",
        hardware_id="nvidia-h100-sxm-80gb",
        server_id="hgx-h100-8gpu",
        assumptions=_assumptions(),
        framework="vllm",
        framework_version="0.9.0",
        model_revision="rev-test",
        weight_precision="bf16",
    )
    kwargs.update(overrides)
    return plan_benchmarks_for(db, **kwargs)


# ==========================================================================
# M37
# ==========================================================================


def test_m37_plan_covers_grid_and_feasible_candidates(tmp_path, root):
    db = _db(tmp_path, root)
    info = _plan(db, tmp_path)
    total_cells = sum(len(g.concurrencies) for g in DEFAULT_EXPERIMENT_GRID)
    assert len(info["points"]) == total_cells
    concurrencies = {p["concurrency"] for p in info["points"]}
    assert concurrencies == {c for g in DEFAULT_EXPERIMENT_GRID for c in g.concurrencies}
    # 每个点都带上完整的部署身份
    for point in info["points"]:
        assert point["tp_size"] * point["dp_size"] == point["gpu_count"]
        assert point["model_revision"] and point["framework_version"]
        assert point["hardware_model"]


def test_m37_plan_contains_no_performance_numbers(tmp_path, root):
    """清单只给参数，不得含任何性能数值。"""
    db = _db(tmp_path, root)
    info = _plan(db, tmp_path)
    blob = json.dumps(info, ensure_ascii=False, default=str)
    # 指标名可以出现在 required_metrics 里，但**不得**出现"指标 = 数值"的形态
    for metric in ("ttft_p99_ms", "tpot_p99_ms", "itl_p99_ms", "e2e_p99_ms",
                   "output_throughput_tok_s", "request_throughput_rps", "error_rate"):
        assert f'"{metric}": ' not in blob, metric
    assert all("不含任何性能数值" in " ".join(p["notes"]) for p in info["points"])


def test_m37_command_flags_come_from_archived_docs(tmp_path, root):
    """生成的每个命令行参数都必须能在仓库内归档的官方文档里找到。"""
    db = _db(tmp_path, root)
    for framework in ("vllm", "sglang"):
        info = _plan(db, tmp_path, framework=framework)
        source = (REPO / FLAG_SOURCES[framework]).read_text(encoding="utf-8", errors="ignore")
        checked: set[str] = set()
        for point in info["points"]:
            # 只校验压测客户端命令：服务端**不生成**命令（其参数不在归档证据内）
            for flag in re.findall(r"--[a-z][a-z0-9-]+", point["client_command"]):
                checked.add(flag)
                assert flag in source, f"{framework}: {flag} 不在归档文档中"
            assert "server_command" not in point
            assert point["server_args"]["tensor_parallel_size"] == point["tp_size"]
        assert checked, framework


def test_m37_metadata_template_reaches_exact_match(tmp_path, root):
    """**核心验收**：照着模板采集的数据能与该部署配置对齐到 exact_match。"""
    from llm_capacity_pricing.perf.benchmark_matcher import (
        match,
        signature_from_run,
    )
    from llm_capacity_pricing.perf.performance_profile import run_from_adapter
    from llm_capacity_pricing.schemas.benchmark import BenchmarkRecord
    from llm_capacity_pricing.schemas.performance import (
        TargetRequirement,
        WorkloadSignature,
    )

    db = _db(tmp_path, root)
    point = _plan(db, tmp_path)["points"][0]
    assert len(point["metadata_templates"]) == point["repeats"]
    meta = point["metadata_templates"][0]

    # 模拟"照模板采回来的结果"：字段名与适配器一致
    # 模板默认是未核验；本用例验证"核验通过后能与部署对齐"，
    # 因此在这里显式把核验状态置为 verified（默认状态的行为由加固用例覆盖）
    record = BenchmarkRecord(
        record_id=meta["record_id"],
        model_name=meta["model_name"],
        model_revision=meta["model_revision"],
        model_precision=meta["weight_precision"],
        hardware_model=meta["hardware_model"],
        deployment_gpu_count=meta["deployment_gpu_count"],
        tp_size=meta["tp_size"],
        dp_size=meta["dp_size"],
        framework=meta["framework"],
        framework_version=meta["framework_version"],
        configured_concurrency=meta["configured_concurrency"],
        input_tokens_target=meta["input_len_target"],
        output_tokens_target=meta["output_len_target"],
        dataset_name=meta["dataset_name"],
        ttft_p99_ms=Decimal("120"),
        tpot_p99_ms=Decimal("12"),
        output_tps_cluster=Decimal("1500"),
        duration_s=Decimal("120"),
        num_requests=200,
        success_count=200,
        performance_source_kind=meta["performance_source_kind"],
        verification_status="verified",
        evidence_id=meta["evidence_id"],
    )
    raw = tmp_path / "raw.json"
    raw.write_text("{}", encoding="utf-8")
    run = run_from_adapter(
        record, metadata=meta, raw_file=raw, raw_format=meta["format_version"]
    )
    assert run.data_trust == "measured_internal"

    target = TargetRequirement(
        model_name=meta["model_name"],
        model_revision=meta["model_revision"],
        weight_precision=meta["weight_precision"],
        hardware_model=meta["hardware_model"],
        gpu_count=meta["deployment_gpu_count"],
        tp_size=meta["tp_size"],
        dp_size=meta["dp_size"],
        framework=meta["framework"],
        framework_version=meta["framework_version"],
        workload=WorkloadSignature(
            input_len_target=meta["input_len_target"],
            output_len_target=meta["output_len_target"],
            dataset_name=meta["dataset_name"],
            arrival_mode=meta["arrival_mode"],
            configured_concurrency=meta["configured_concurrency"],
        ),
    )
    result = match(target, signature_from_run(run))
    assert result.level == "exact_match", result.reasons


def test_m37_missing_identity_conditions_are_declared(tmp_path, root):
    """缺关键条件时：清单明确列出缺口，且这样的数据达不到 exact_match。"""
    from llm_capacity_pricing.perf.benchmark_matcher import match, signature_from_run
    from llm_capacity_pricing.perf.performance_profile import run_from_adapter
    from llm_capacity_pricing.schemas.benchmark import BenchmarkRecord
    from llm_capacity_pricing.schemas.performance import (
        TargetRequirement,
        WorkloadSignature,
    )

    db = _db(tmp_path, root)
    info = _plan(db, tmp_path, framework_version=None)
    joined = " ".join(info["missing_inputs"])
    assert "framework_version" in joined
    # 模型清单里有 revision 时会自动采用；两者都没有才报缺
    assert any(p["model_revision"] for p in info["points"])

    # 缺 revision / 框架版本的数据，对完整目标只能落在参考级
    meta = dict(info["points"][0]["metadata_templates"][0])
    meta["model_revision"] = None
    meta["framework_version"] = None  # 现场没记框架版本 -> 达不到 exact_match
    record = BenchmarkRecord(
        record_id=meta["record_id"],
        model_name=meta["model_name"],
        hardware_model=meta["hardware_model"],
        deployment_gpu_count=meta["deployment_gpu_count"],
        tp_size=meta["tp_size"],
        dp_size=meta["dp_size"],
        framework=meta["framework"],
        configured_concurrency=meta["configured_concurrency"],
        input_tokens_target=meta["input_len_target"],
        output_tokens_target=meta["output_len_target"],
        dataset_name=meta["dataset_name"],
        ttft_p99_ms=Decimal("120"),
        tpot_p99_ms=Decimal("12"),
        output_tps_cluster=Decimal("1500"),
        performance_source_kind="internal_measurement",
        verification_status="verified",
        evidence_id=meta["evidence_id"],
    )
    raw = tmp_path / "raw2.json"
    raw.write_text("{}", encoding="utf-8")
    run = run_from_adapter(
        record, metadata=meta, raw_file=raw, raw_format=meta["format_version"]
    )
    target = TargetRequirement(
        model_name=meta["model_name"],
        model_revision="rev-test",
        weight_precision=meta["weight_precision"],
        hardware_model=meta["hardware_model"],
        gpu_count=meta["deployment_gpu_count"],
        tp_size=meta["tp_size"],
        dp_size=meta["dp_size"],
        framework=meta["framework"],
        framework_version="0.9.0",
        workload=WorkloadSignature(
            input_len_target=meta["input_len_target"],
            output_len_target=meta["output_len_target"],
            dataset_name=meta["dataset_name"],
            arrival_mode=meta["arrival_mode"],
            configured_concurrency=meta["configured_concurrency"],
        ),
    )
    result = match(target, signature_from_run(run))
    assert result.level != "exact_match"


def test_m37_repeats_and_duration_meet_sustainability_thresholds(tmp_path, root):
    from llm_capacity_pricing.perf.capacity_analyzer import SustainabilityCriteria

    db = _db(tmp_path, root)
    criteria = SustainabilityCriteria()
    info = _plan(db, tmp_path, repeats=1)
    for point in info["points"]:
        assert point["repeats"] >= criteria.min_repeats_per_point
        assert Decimal(str(point["duration_s"])) >= criteria.min_duration_s


def test_m37_cli_exports_plan_and_metadata_templates(tmp_path, root):
    db = _db(tmp_path, root)
    out = tmp_path / "exp.json"
    meta_dir = tmp_path / "meta"
    result = runner.invoke(
        app,
        [
            "plan-benchmarks", "--model", "qwen3-32b",
            "--hardware", "nvidia-h100-sxm-80gb", "--server", "hgx-h100-8gpu",
            "--framework-version", "0.9.0", "--model-revision", "rev-test",
            "--precision", "bf16", "--tp-sizes", "4", "--dp-sizes", "1",
            "--out", str(out), "--metadata-dir", str(meta_dir), "--db", db,
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["points"]
    files = {p.stem: p for p in meta_dir.glob("*.yaml")}
    expected = {
        tpl["record_id"]
        for point in payload["points"]
        for tpl in point["metadata_templates"]
    }
    assert set(files) == expected
    # 每次独立执行一份：同一实验点的多份模板 record_id 互不相同
    first_point = payload["points"][0]
    assert len({tpl["record_id"] for tpl in first_point["metadata_templates"]}) == len(
        first_point["metadata_templates"]
    )
    loaded = yaml.safe_load(
        files[first_point["metadata_templates"][0]["record_id"]].read_text(encoding="utf-8")
    )
    assert loaded["experiment_point_id"] == first_point["point_id"]


# ==========================================================================
# M38
# ==========================================================================


def _measured_run(root: Path, *, concurrency: int = 8):
    """身份对齐的内部实测（机制夹具）。"""
    import json as _json

    from llm_capacity_pricing.importers.vllm_adapter import parse_vllm_bench_serve_json_full
    from llm_capacity_pricing.perf.performance_profile import run_from_adapter

    folder = root / "data" / "fixtures" / "perf_curve"
    index = _json.loads((folder / "INDEX.json").read_text(encoding="utf-8"))
    point = next(
        p for p in index["points"]
        if p["file"].endswith(f"c{concurrency}.json")
    )
    data = folder / point["file"]
    meta = yaml.safe_load((folder / point["metadata"]).read_text(encoding="utf-8"))
    records, _ = parse_vllm_bench_serve_json_full(data, meta)
    run = run_from_adapter(
        records[0], metadata=meta, raw_file=data, raw_format="vllm-bench-serve-json/v1"
    )
    sig = run.signature.model_copy(
        update={
            "data_trust": "measured_internal",
            "model_revision": "rev-test",
            "framework_version": "0.9.0",
        }
    )
    return run.model_copy(update={"signature": sig, "data_trust": "measured_internal"})


def test_m38_clean_run_has_no_blockers(root):
    run = _measured_run(root)
    report = check_import_consistency([run])
    assert report.ok, [i.message for i in report.blockers]


def test_m38_server_only_metrics_is_blocking(root):
    run = _measured_run(root)
    changed = run.model_copy(
        update={
            "profile": run.profile.model_copy(
                update={
                    "ttft_p99_ms": None,
                    "tpot_p99_ms": None,
                    "server_ttft_p99_ms": Decimal("100"),
                    "server_tpot_p99_ms": Decimal("10"),
                }
            )
        }
    )
    issues = check_run_consistency(changed)
    assert any(i.code == "server_side_only" and i.severity == "blocker" for i in issues)


def test_m38_p99_without_definition_is_blocking(root):
    run = _measured_run(root)
    changed = run.model_copy(
        update={
            "profile": run.profile.model_copy(update={"metric_definition_ids": []})
        }
    )
    issues = check_run_consistency(changed)
    assert any(
        i.code == "metric_definition_missing" and i.severity == "blocker" for i in issues
    )


def test_m38_mean_only_never_infers_p99(root):
    run = _measured_run(root)
    changed = run.model_copy(
        update={
            "profile": run.profile.model_copy(
                update={
                    "ttft_p99_ms": None,
                    "tpot_p99_ms": None,
                    "itl_p99_ms": None,
                    "e2e_p99_ms": None,
                    "ttft_mean_ms": Decimal("80"),
                    "e2e_p99_ms_max": None,
                }
            )
        }
    )
    issues = check_run_consistency(changed)
    assert any(i.code == "mean_only_do_not_infer_p99" for i in issues)
    assert any(i.code == "no_percentile_metrics" for i in issues)
    # 不得凭空补出 P99
    assert changed.profile.ttft_p99_ms is None


def test_m38_concurrency_semantics(root):
    run = _measured_run(root)
    confused = run.model_copy(
        update={
            "profile": run.profile.model_copy(
                update={"observed_concurrency": Decimal("9999")}
            )
        }
    )
    assert any(
        i.code == "observed_concurrency_exceeds_configured"
        for i in check_run_consistency(confused)
    )

    limited = run.model_copy(
        update={
            "profile": run.profile.model_copy(update={"request_rate_rps": Decimal("5")}),
            "signature": run.signature.model_copy(
                update={
                    "workload": run.signature.workload.model_copy(
                        update={"arrival_mode": "unbounded", "request_rate_rps": Decimal("5")}
                    )
                }
            ),
        }
    )
    assert any(
        i.code == "arrival_mode_conflicts_with_rate" for i in check_run_consistency(limited)
    )


def test_m38_request_accounting_and_error_rate(root):
    run = _measured_run(root)
    inconsistent = run.model_copy(
        update={"profile": run.profile.model_copy(update={"error_rate": Decimal("0.5")})}
    )
    issues = check_run_consistency(inconsistent)
    assert any(i.code == "error_rate_inconsistent" and i.severity == "blocker" for i in issues)

    no_success = run.model_copy(
        update={
            "profile": run.profile.model_copy(
                update={"success_count": None, "error_rate": None}
            )
        }
    )
    assert any(
        i.code == "success_count_missing" for i in check_run_consistency(no_success)
    )


def test_m38_cache_rate_must_not_be_copied_from_physical(root):
    run = _measured_run(root)
    same = run.model_copy(
        update={
            "profile": run.profile.model_copy(
                update={
                    "physical_cache_hit_rate": Decimal("0.42"),
                    "billable_cached_input_ratio": Decimal("0.42"),
                }
            )
        }
    )
    issues = check_run_consistency(same)
    assert any(i.code == "cache_ratio_equals_physical_rate" for i in issues)


def test_m38_batch_checks(root):
    run = _measured_run(root)
    duplicated = [run, run]
    assert any(
        i.code == "duplicate_run_id_in_batch" and i.severity == "blocker"
        for i in check_batch_consistency(duplicated)
    )

    # 同（配置, 并发）的两次独立观测 -> 保守聚合说明（信息性）
    twin = run.model_copy(update={"run_id": run.run_id + "-repeat"})
    issues = check_batch_consistency([run, twin])
    assert any(
        i.code == "repeat_runs_will_be_conservatively_aggregated" for i in issues
    )


def test_m38_reimport_is_stable(tmp_path, root):
    """**核心验收**：同一份结果重复导入，run 数与指标不变。"""
    db = _db(tmp_path, root, "m38.db")
    info = verify_reimport_is_stable(
        root / "data/fixtures/vllm_synthetic.json",
        root / "config/fixture_vllm.yaml",
        db,
        kind="vllm",
    )
    assert info["stable"] is True, info["snapshots"]
    first = info["snapshots"]["after_first_import"]
    after = info["snapshots"]["after_retry"]
    assert first["run_count"] == after["run_count"]
    assert first["run_ids"] == after["run_ids"]


def test_m38_service_reports_without_modifying_data(tmp_path, root):
    db = _db(tmp_path, root, "m38b.db")
    import_performance_curve(root / "data/fixtures/perf_curve/INDEX.json", db, root=root)
    report = check_import_consistency_for(db)
    assert report["checked_run_count"] == 5
    assert "只读" in report["notice"]
    # 数据没被改动：仍是 synthetic
    dbh, repo = open_db(db)
    try:
        with dbh.session() as session:
            runs = repo.list_benchmark_runs(session)
    finally:
        dbh.dispose()
    assert all(r.data_trust == "synthetic_fixture" for r in runs)


def test_m38_cli_check_import(tmp_path, root):
    db = _db(tmp_path, root, "m38c.db")
    import_performance_curve(root / "data/fixtures/perf_curve/INDEX.json", db, root=root)
    out = tmp_path / "consistency.json"
    result = runner.invoke(app, ["check-import", "--db", db, "--out", str(out)])
    assert result.exit_code == 0, result.output
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["checked_run_count"] == 5
    assert "codes" not in payload  # 文件里是完整 issues，不是摘要
    assert payload["issues"] is not None
