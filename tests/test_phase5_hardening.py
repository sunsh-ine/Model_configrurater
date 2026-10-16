"""第五阶段封板前的证据加固（评审指定的三条反向用例）。

1. 同一实验点的两次**独立执行**必须各留一条 Run；重试同一执行不得新增记录；
2. 未核验的实验模板导入后**不得**自动获得测量类可信等级；
3. 显式启用 Synthetic 算法验证时，即使返回 `observed_pass`，`can_claim_sla` 也必须为 False。

外加一条：字段齐备但**自相矛盾**的 Run 不得被数据质量报告声明为生产级
（复用 `check_run_consistency`，不新增第二套质量规则）。
"""

from __future__ import annotations

import json
import shutil
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from llm_capacity_pricing.perf.site_validation import validate_site_performance
from llm_capacity_pricing.schemas.performance import (
    PRODUCTION_TRUST_LEVELS,
    SlaPolicy,
    SlaRequirement,
    TargetRequirement,
    WorkloadSignature,
)
from llm_capacity_pricing.services import (
    attest_benchmark_run,
    data_quality_report,
    import_hardware,
    import_models,
    import_performance_run,
    init_db,
    make_assumptions,
    open_db,
    plan_benchmarks_for,
)
from llm_capacity_pricing.services import ServiceError

CURVE = "data/fixtures/perf_curve/perf_curve_c8.json"


def _db(tmp_path: Path, root: Path, name: str = "hardening.db") -> str:
    db = str(tmp_path / name)
    init_db(db)
    import_models(root / "data/models/model_manifest.yaml", db, root=root)
    import_hardware(root / "data/hardware/hardware_catalog.yaml", db)
    return db


def _plan_point(db: str, *, repeats: int = 2) -> dict:
    info = plan_benchmarks_for(
        db,
        model_id="qwen3-32b",
        hardware_id="nvidia-h100-sxm-80gb",
        server_id="hgx-h100-8gpu",
        assumptions=make_assumptions(
            usable_fraction_of_nominal=Decimal("0.9"),
            usable_memory_source_kind="synthetic_example",
            runtime_reserve_fraction=Decimal("0.1"),
            global_concurrency=32,
            context_length_per_request=8192,
            tp_sizes=(4,),
            dp_sizes=(1,),
        ),
        framework="vllm",
        framework_version="0.9.0",
        model_revision="rev-1",
        weight_precision="bf16",
        repeats=repeats,
    )
    return info["points"][0]


def _materialise(
    tmp_path: Path, root: Path, template: dict, *, tag: str
) -> tuple[Path, Path]:
    """把模板落成 (结果文件, metadata 文件) 对。"""
    result = tmp_path / f"result-{tag}.json"
    shutil.copyfile(root / CURVE, result)
    meta = tmp_path / f"meta-{tag}.yaml"
    meta.write_text(yaml.safe_dump(template, allow_unicode=True), encoding="utf-8")
    return result, meta


def _run_count(db: str) -> int:
    dbh, repo = open_db(db)
    try:
        with dbh.session() as session:
            return len(repo.list_benchmark_runs(session))
    finally:
        dbh.dispose()


# ==========================================================================
# 1. 独立执行 vs 重试
# ==========================================================================


def test_hardening_independent_runs_are_kept_and_retry_is_idempotent(tmp_path, root):
    """同一实验点的两次独立执行 = 两条 Run；重试同一执行不新增记录。"""
    db = _db(tmp_path, root)
    point = _plan_point(db)
    templates = point["metadata_templates"]
    assert len(templates) == 2
    # 两份模板的 record_id 必须不同（否则第二次执行会覆盖第一次）
    assert templates[0]["record_id"] != templates[1]["record_id"]
    assert templates[0]["experiment_point_id"] == templates[1]["experiment_point_id"]
    assert {t["repeat_index"] for t in templates} == {1, 2}

    result1, meta1 = _materialise(tmp_path, root, templates[0], tag="r1")
    result2, meta2 = _materialise(tmp_path, root, templates[1], tag="r2")

    import_performance_run(result1, meta1, db, kind="vllm")
    assert _run_count(db) == 1
    import_performance_run(result2, meta2, db, kind="vllm")
    assert _run_count(db) == 2, "两次独立执行必须各留一条 Run"

    # 重试同一份原始执行：不得新增、不得改动
    import_performance_run(result1, meta1, db, kind="vllm")
    import_performance_run(result1, meta1, db, kind="vllm")
    assert _run_count(db) == 2, "重试同一执行不得新增记录"

    dbh, repo = open_db(db)
    try:
        with dbh.session() as session:
            runs = {r.run_id: r for r in repo.list_benchmark_runs(session)}
    finally:
        dbh.dispose()
    assert set(runs) == {templates[0]["record_id"], templates[1]["record_id"]}
    # 两次独立执行是**同一配置**下的重复观测（并发一致、身份一致）
    concurrencies = {r.profile.configured_concurrency for r in runs.values()}
    assert len(concurrencies) == 1
    identities = {r.signature.model_name for r in runs.values()}
    assert len(identities) == 1


def test_hardening_different_experiment_configs_do_not_share_run_ids(tmp_path, root):
    """模型版本/框架版本/缓存策略不同 -> Run 身份不同，不互相覆盖。"""
    db = _db(tmp_path, root)
    base = _plan_point(db, repeats=1)["metadata_templates"][0]
    other = plan_benchmarks_for(
        db,
        model_id="qwen3-32b",
        hardware_id="nvidia-h100-sxm-80gb",
        server_id="hgx-h100-8gpu",
        assumptions=make_assumptions(
            usable_fraction_of_nominal=Decimal("0.9"),
            usable_memory_source_kind="synthetic_example",
            runtime_reserve_fraction=Decimal("0.1"),
            global_concurrency=32,
            context_length_per_request=8192,
            tp_sizes=(4,),
            dp_sizes=(1,),
        ),
        framework="vllm",
        framework_version="0.10.0",   # 框架版本不同
        model_revision="rev-1",
        weight_precision="bf16",
        repeats=1,
        prefix_cache="enabled",        # 缓存策略不同
    )["points"][0]["metadata_templates"][0]

    assert base["record_id"] != other["record_id"]
    assert base["config_fingerprint"] != other["config_fingerprint"]
    assert base["experiment_point_id"] == other["experiment_point_id"]


# ==========================================================================
# 2. 未核验模板不得自动获得生产级可信度
# ==========================================================================


def test_hardening_unverified_template_import_is_not_production_evidence(tmp_path, root):
    """**核心验收**：照模板直接导入 -> 可信等级为 unknown，不是 measured_internal。"""
    db = _db(tmp_path, root)
    point = _plan_point(db, repeats=1)
    template = point["metadata_templates"][0]
    assert template["verification_status"] == "unverified_parse"
    assert template["performance_source_kind"] == "internal_measurement"

    result, meta = _materialise(tmp_path, root, template, tag="unverified")
    info = import_performance_run(result, meta, db, kind="vllm")
    assert info["data_trust"] == ["unknown"], info

    quality = data_quality_report(db)
    item = next(
        i for i in quality["items"]
        if i["domain"] == "benchmark_run" and i["record_id"] == template["record_id"]
    )
    assert item["production_grade"] is False
    assert item["details"]["data_trust"] == "unknown"
    assert any("核验" in r for r in item["reasons"])
    assert "sla_proof" not in item["usable_for"]

    dbh, repo = open_db(db)
    try:
        with dbh.session() as session:
            run = repo.get_benchmark_run(session, template["record_id"])
    finally:
        dbh.dispose()
    assert run.data_trust == "unknown"
    assert any(
        f.startswith("source_verification_required:") for f in run.quality_flags
    ), run.quality_flags


def test_hardening_attest_requires_matching_raw_hash_and_upgrades_once(tmp_path, root):
    """核验步骤：哈希必须一致，通过后只升级这一条 Run。"""
    db = _db(tmp_path, root)
    template = _plan_point(db, repeats=1)["metadata_templates"][0]
    result, meta = _materialise(tmp_path, root, template, tag="attest")
    import_performance_run(result, meta, db, kind="vllm")

    with pytest.raises(ServiceError, match="哈希与导入时记录的不一致"):
        attest_benchmark_run(
            db, run_id=template["record_id"], confirmed_by="评审人",
            raw_sha256="0" * 64,
        )

    dbh, repo = open_db(db)
    try:
        with dbh.session() as session:
            run = repo.get_benchmark_run(session, template["record_id"])
    finally:
        dbh.dispose()

    info = attest_benchmark_run(
        db, run_id=template["record_id"], confirmed_by="评审人",
        raw_sha256=run.raw_sha256, note="核对原始输出文件",
    )
    assert info["data_trust"] == "measured_internal"
    assert info["production_grade"] is True, info
    assert "source_verified" in info["quality_flags"]
    assert any(f.startswith("verified_by:") for f in info["quality_flags"])
    assert not any(
        f.startswith("source_verification_required") for f in info["quality_flags"]
    )


def test_hardening_attest_rejects_non_attestable_sources(tmp_path, root):
    """synthetic 数据不属于可核验升级的范围。"""
    db = _db(tmp_path, root, "hardening2.db")
    from llm_capacity_pricing.services import import_performance_curve

    import_performance_curve(root / "data/fixtures/perf_curve/INDEX.json", db, root=root)
    dbh, repo = open_db(db)
    try:
        with dbh.session() as session:
            run = repo.list_benchmark_runs(session)[0]
    finally:
        dbh.dispose()
    with pytest.raises(ServiceError, match="不属于可核验升级的范围"):
        attest_benchmark_run(
            db, run_id=run.run_id, confirmed_by="评审人", raw_sha256=run.raw_sha256
        )


# ==========================================================================
# 3. Synthetic 算法验证结论不得被标记为"可对外声称"
# ==========================================================================


def test_hardening_algorithm_validation_observed_pass_cannot_claim_sla(root):
    """**核心验收**：allow_synthetic 下拿到 observed_pass，can_claim_sla 仍必须为 False。"""
    import json as _json

    from llm_capacity_pricing.importers.vllm_adapter import (
        parse_vllm_bench_serve_json_full,
    )
    from llm_capacity_pricing.perf.performance_profile import run_from_adapter

    folder = root / "data" / "fixtures" / "perf_curve"
    index = _json.loads((folder / "INDEX.json").read_text(encoding="utf-8"))
    point = next(p for p in index["points"] if p["file"].endswith("c8.json"))
    data = folder / point["file"]
    meta = yaml.safe_load((folder / point["metadata"]).read_text(encoding="utf-8"))
    records, _ = parse_vllm_bench_serve_json_full(data, meta)
    run = run_from_adapter(
        records[0], metadata=meta, raw_file=data, raw_format="vllm-bench-serve-json/v1"
    )
    assert run.data_trust == "synthetic_fixture"

    target = TargetRequirement(
        model_name="SYNTHETIC-MODEL-C",
        model_revision="synthetic-revision-1",
        weight_precision="bf16",
        hardware_model="SYNTHETIC-GPU-C",
        gpu_count=4, tp_size=4, dp_size=1,
        framework="vllm", framework_version="synthetic-fw-1.0",
        workload=WorkloadSignature(
            input_len_target=1024, output_len_target=256, dataset_name="random",
            arrival_mode="unbounded", configured_concurrency=8,
        ),
        require_measured=False, allow_synthetic=True,
    )
    algo_policy = SlaPolicy(
        policy_id="algorithm-validation-only",
        allow_synthetic=True,
        accepted_trust_levels=[*PRODUCTION_TRUST_LEVELS, "synthetic_fixture"],
    )
    validation = validate_site_performance(
        runs=[run],
        target=target,
        requirement=SlaRequirement(requirement_id="r", ttft_p99_ms_max=Decimal("99999")),
        policy=algo_policy,
    )
    # 算法验证确实给出了 observed_pass
    assert validation.sla_status == "observed_pass", validation.not_evaluable_reasons
    # 但绝不能对外声称
    assert validation.can_claim_sla is False
    assert validation.synthetic_included is True
    assert validation.data_trust == "synthetic_fixture"
    assert any("synthetic" in r for r in validation.not_evaluable_reasons)
    assert any("不具备" in n for n in validation.notes)


def test_hardening_can_claim_sla_requires_all_three_conditions(root):
    """三条条件缺一不可：exact_match + 生产可信等级 + 非 synthetic。"""
    import json as _json

    from llm_capacity_pricing.importers.vllm_adapter import (
        parse_vllm_bench_serve_json_full,
    )
    from llm_capacity_pricing.perf.performance_profile import run_from_adapter

    folder = root / "data" / "fixtures" / "perf_curve"
    index = _json.loads((folder / "INDEX.json").read_text(encoding="utf-8"))
    point = next(p for p in index["points"] if p["file"].endswith("c8.json"))
    data = folder / point["file"]
    meta = yaml.safe_load((folder / point["metadata"]).read_text(encoding="utf-8"))
    records, _ = parse_vllm_bench_serve_json_full(data, meta)
    raw = run_from_adapter(
        records[0], metadata=meta, raw_file=data, raw_format="vllm-bench-serve-json/v1"
    )
    measured = raw.model_copy(
        update={
            "data_trust": "measured_internal",
            "signature": raw.signature.model_copy(
                update={"data_trust": "measured_internal"}
            ),
        }
    )

    def _target(concurrency: int, trust: str | None = None) -> TargetRequirement:
        return TargetRequirement(
            model_name="SYNTHETIC-MODEL-C", model_revision="synthetic-revision-1",
            weight_precision="bf16", hardware_model="SYNTHETIC-GPU-C",
            gpu_count=4, tp_size=4, dp_size=1,
            framework="vllm", framework_version="synthetic-fw-1.0",
            workload=WorkloadSignature(
                input_len_target=1024, output_len_target=256, dataset_name="random",
                arrival_mode="unbounded", configured_concurrency=concurrency,
            ),
        )

    requirement = SlaRequirement(requirement_id="r", ttft_p99_ms_max=Decimal("99999"))

    # 全满足 -> True
    ok = validate_site_performance(
        runs=[measured], target=_target(8), requirement=requirement
    )
    assert ok.sla_status == "observed_pass"
    assert ok.can_claim_sla is True

    # 匹配不严格 -> False
    loose = validate_site_performance(
        runs=[measured], target=_target(64), requirement=requirement
    )
    assert loose.can_claim_sla is False

    # 可信等级不足 -> False
    weak_run = measured.model_copy(
        update={
            "data_trust": "historical_external_no_latency",
            "signature": measured.signature.model_copy(
                update={"data_trust": "historical_external_no_latency"}
            ),
        }
    )
    weak = validate_site_performance(
        runs=[weak_run], target=_target(8), requirement=requirement
    )
    assert weak.can_claim_sla is False


# ==========================================================================
# 数据质量复用一致性校验
# ==========================================================================


def test_hardening_data_quality_declines_production_grade_on_consistency_blocker(
    tmp_path, root
):
    """字段齐备但自相矛盾（错误率与计数不符）-> 不得声明为生产级。"""
    db = _db(tmp_path, root, "hardening3.db")
    template = _plan_point(db, repeats=1)["metadata_templates"][0]
    result, meta = _materialise(tmp_path, root, template, tag="inconsistent")
    import_performance_run(result, meta, db, kind="vllm")
    # 先核验成测量类，让"字段齐备"这条成立
    dbh, repo = open_db(db)
    try:
        with dbh.session() as session:
            run = repo.get_benchmark_run(session, template["record_id"])
            inconsistent = run.model_copy(
                update={
                    "data_trust": "measured_internal",
                    "signature": run.signature.model_copy(
                        update={"data_trust": "measured_internal"}
                    ),
                    "profile": run.profile.model_copy(
                        update={"error_rate": Decimal("0.42")}  # 与成功/失败计数不符
                    ),
                }
            )
            repo.upsert_benchmark_runs(session, [inconsistent])
            session.commit()
    finally:
        dbh.dispose()

    quality = data_quality_report(db)
    item = next(
        i for i in quality["items"]
        if i["domain"] == "benchmark_run" and i["record_id"] == template["record_id"]
    )
    assert item["production_grade"] is False
    assert item["usable_for"] == ["reference"]
    assert any("一致性校验未通过" in r for r in item["reasons"])
    assert "error_rate_inconsistent" in item["details"]["consistency_blockers"]
