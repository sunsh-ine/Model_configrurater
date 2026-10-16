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
        f.startswith("pending_attestation:") for f in run.quality_flags
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
        f.startswith("pending_attestation") for f in info["quality_flags"]
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


# ==========================================================================
# 加固第二轮：独立观测、自我声明不可绕过核验、跨批次身份
# ==========================================================================


def test_hardening_same_raw_sha_is_not_an_independent_observation(tmp_path, root):
    """**评审指定用例**：两个 Run ID 对应同一原始哈希 -> 不构成独立重复观测。"""
    from llm_capacity_pricing.perf.capacity_analyzer import (
        SustainabilityCriteria,
        analyze_group,
    )

    db = _db(tmp_path, root, "hardening4.db")
    point = _plan_point(db, repeats=2)
    templates = point["metadata_templates"]
    # 把同一份**原始结果**复制成两个文件：字节相同 -> 哈希相同
    result1 = tmp_path / "same-a.json"
    result2 = tmp_path / "same-b.json"
    shutil.copyfile(root / CURVE, result1)
    shutil.copyfile(root / CURVE, result2)
    meta1 = tmp_path / "m1.yaml"
    meta2 = tmp_path / "m2.yaml"
    meta1.write_text(yaml.safe_dump(templates[0], allow_unicode=True), encoding="utf-8")
    meta2.write_text(yaml.safe_dump(templates[1], allow_unicode=True), encoding="utf-8")
    import_performance_run(result1, meta1, db, kind="vllm")
    import_performance_run(result2, meta2, db, kind="vllm")

    dbh, repo = open_db(db)
    try:
        with dbh.session() as session:
            runs = repo.list_benchmark_runs(session)
    finally:
        dbh.dispose()
    assert len(runs) == 2, "两条记录仍然各自保留（身份独立）"
    assert len({r.run_id for r in runs}) == 2
    assert len({r.raw_sha256 for r in runs}) == 1, "但原始哈希相同"

    criteria = SustainabilityCriteria(min_duration_s=Decimal("0"))  # 放宽时长，聚焦重复次数
    profile = analyze_group(list(runs), criteria=criteria)
    assert any(f.startswith("duplicate_observation:") for f in profile.quality_flags), (
        profile.quality_flags
    )
    assert profile.maximum_sustainable_capacity is None, "不得据此认定可持续容量"
    assert any("原始文件哈希相同" in n for n in profile.notes)


def test_hardening_metadata_self_declared_verified_cannot_bypass_attestation(
    tmp_path, root
):
    """**评审指定用例**：metadata 自己写 verified，也不得自动获得 measured_internal。"""
    db = _db(tmp_path, root, "hardening5.db")
    template = dict(_plan_point(db, repeats=1)["metadata_templates"][0])
    template["performance_source_kind"] = "internal_measurement"
    template["verification_status"] = "verified"  # 自我声明为"已验证"

    result, meta = _materialise(tmp_path, root, template, tag="selfverified")
    info = import_performance_run(result, meta, db, kind="vllm")
    assert info["data_trust"] == ["unknown"], info

    dbh, repo = open_db(db)
    try:
        with dbh.session() as session:
            run = repo.get_benchmark_run(session, template["record_id"])
    finally:
        dbh.dispose()
    assert run.data_trust == "unknown"
    assert any(f.startswith("pending_attestation:") for f in run.quality_flags)

    quality = data_quality_report(db)
    item = next(
        i for i in quality["items"]
        if i["domain"] == "benchmark_run" and i["record_id"] == template["record_id"]
    )
    assert item["production_grade"] is False

    # 只有显式核验才能升级
    attested = attest_benchmark_run(
        db, run_id=template["record_id"], confirmed_by="评审人",
        raw_sha256=run.raw_sha256,
    )
    assert attested["data_trust"] == "measured_internal"
    # 核验的能力边界必须写在返回值里
    joined = " ".join(attested["limitations"])
    assert "不能证明" in joined and "身份认证" in joined


def test_hardening_cross_batch_ids_are_distinct_and_same_batch_is_idempotent(
    tmp_path, root
):
    """**评审指定用例**：跨批次各自独立保存；同一批次重试仍幂等。"""
    db = _db(tmp_path, root, "hardening6.db")
    common = dict(
        model_id="qwen3-32b",
        hardware_id="nvidia-h100-sxm-80gb",
        server_id="hgx-h100-8gpu",
        framework_version="0.9.0",
        model_revision="rev-1",
        repeats=1,
    )
    assumptions = make_assumptions(
        usable_fraction_of_nominal=Decimal("0.9"),
        usable_memory_source_kind="synthetic_example",
        runtime_reserve_fraction=Decimal("0.1"),
        global_concurrency=32,
        context_length_per_request=8192,
        tp_sizes=(4,),
        dp_sizes=(1,),
    )
    batch_a1 = plan_benchmarks_for(
        db, assumptions=assumptions, batch_id="batch-A", **common
    )
    batch_a2 = plan_benchmarks_for(
        db, assumptions=assumptions, batch_id="batch-A", **common
    )
    batch_b = plan_benchmarks_for(
        db, assumptions=assumptions, batch_id="batch-B", **common
    )
    rid_a1 = batch_a1["points"][0]["metadata_templates"][0]["record_id"]
    rid_a2 = batch_a2["points"][0]["metadata_templates"][0]["record_id"]
    rid_b = batch_b["points"][0]["metadata_templates"][0]["record_id"]

    assert rid_a1 == rid_a2, "同一批次重复生成必须得到相同身份（重试幂等）"
    assert rid_a1 != rid_b, "跨批次必须得到不同身份（各自独立保存）"
    assert batch_a1["batch_id"] == "batch-A" and batch_b["batch_id"] == "batch-B"

    # 两个批次的数据都能各自入库存留
    for tag, tpl in (("A", batch_a1["points"][0]["metadata_templates"][0]),
                     ("B", batch_b["points"][0]["metadata_templates"][0])):
        result, meta = _materialise(tmp_path, root, tpl, tag=f"batch{tag}")
        import_performance_run(result, meta, db, kind="vllm")
    assert _run_count(db) == 2

    # 同一批次重试：不新增
    result_a, meta_a = _materialise(
        tmp_path, root, batch_a1["points"][0]["metadata_templates"][0], tag="batchA"
    )
    import_performance_run(result_a, meta_a, db, kind="vllm")
    assert _run_count(db) == 2
