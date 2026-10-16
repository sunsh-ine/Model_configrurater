"""Demo 验收：公开数据驱动的部署方案评估（D5）。

验收口径按 Demo 定位（不要求 exact_match / 现场压测 / 真实价格 / 报价发布）：

* 能从用户需求生成部署候选；
* 能读取**真实公开**性能指标；
* 能生成可解释的性能与成本报告；
* 能追溯公开数据来源；
* 每个结论明确标注「公开实测 / 估算 / Synthetic」，且**没有编造**缺失字段。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import pytest

from llm_capacity_pricing.reporting.plan_report import build_plan_report
from llm_capacity_pricing.services import (
    import_cost_catalog,
    import_hardware,
    import_models,
    init_db,
    open_db,
)
from llm_capacity_pricing.workflow import evaluate_plan, load_plan_request

ROOT = Path(__file__).resolve().parents[1]
DEMO_CONFIG = "data/examples/demo_qwen3_32b_h100.yaml"
CONVERTER = ROOT / "scripts" / "import_public_benchmarks.py"


def _run_converter(db: str) -> str:
    result = subprocess.run(
        [sys.executable, str(CONVERTER), "--db", db, "--root", str(ROOT)],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def _demo_db(tmp_path: Path) -> str:
    db = str(tmp_path / "demo.db")
    init_db(db)
    import_models(ROOT / "data/models/model_manifest.yaml", db, root=ROOT)
    import_hardware(ROOT / "data/hardware/hardware_catalog.yaml", db)
    import_cost_catalog(ROOT / "data/examples/phase4_cost_inputs.example.yaml", db)
    _run_converter(db)
    return db


# --------------------------------------------------------------------------
# D1/D2：公开数据的导入与来源追溯
# --------------------------------------------------------------------------


def test_demo_public_benchmarks_import_with_source_verification(tmp_path):
    db = str(tmp_path / "pub.db")
    init_db(db)
    out = _run_converter(db)
    assert "来源逐字段核对通过" in out
    assert "字面量逐字段核对通过" in out
    dbh, repo = open_db(db)
    try:
        with dbh.session() as session:
            runs = repo.list_benchmark_runs(session)
    finally:
        dbh.dispose()
    # 12 条 SGLang Cookbook（章节锚点核对）+ 2 条公开参考（字面量核对）
    assert len(runs) == 14
    assert len([r for r in runs if "sglang_" in r.run_id]) == 12
    assert len([r for r in runs if "sglang_" not in r.run_id]) == 2
    for run in runs:
        # 来源可追溯：归档文件 + sha256 + 上游 git blob sha
        assert run.raw_file and run.raw_file.startswith("data/raw/official/")
        assert run.raw_sha256 and len(run.raw_sha256) == 64
        # git 托管的来源必须有上游 blob sha；文档站页面没有这一项
        has_blob = any(f.startswith("source_git_blob_sha:") for f in run.quality_flags)
        assert has_blob == ("github.com" in " ".join(run.quality_flags))
        # 公开来源路径：不经过 attest，也不需要
        assert run.data_trust in ("measured_vendor_published", "measured_third_party")
        assert not any(f.startswith("pending_attestation:") for f in run.quality_flags)
        # 可追溯链接：报告要能直接给出原文地址，而不是写 None
        assert any(f.startswith("source_url:https://") for f in run.quality_flags)


def test_demo_reference_claims_are_checked_against_raw_bytes(tmp_path):
    """反例：篡改一个数值声明 -> 整批拒绝入库，不留「部分可信」的记录。

    这是 D7 的诚实性底线：入库的每个数都必须在归档原文里找得到。
    """
    doc = json.loads(
        (ROOT / "data/incoming/public_benchmark_references_20261015.json").read_text(
            encoding="utf-8"
        )
    )
    claim = doc["records"][0]["claims"]["metrics.output_throughput_tok_s"]
    claim["value"] = 9999.99
    claim["tokens"] = ["Output", "token", "throughput", "(tok/s):", "9999.99"]
    tampered = tmp_path / "tampered.json"
    tampered.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")

    db = str(tmp_path / "tampered.db")
    init_db(db)
    result = subprocess.run(
        [
            sys.executable,
            str(CONVERTER),
            "--db",
            db,
            "--root",
            str(ROOT),
            "--references",
            str(tampered),
        ],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    assert result.returncode != 0
    assert "核对失败" in (result.stdout + result.stderr)
    dbh, repo = open_db(db)
    try:
        with dbh.session() as session:
            assert repo.list_benchmark_runs(session) == []
    finally:
        dbh.dispose()


def test_demo_no_fabricated_fields(tmp_path):
    """公开来源没给的字段必须保持为空 —— 不补造、不外推。"""
    db = str(tmp_path / "pub2.db")
    init_db(db)
    _run_converter(db)
    dbh, repo = open_db(db)
    try:
        with dbh.session() as session:
            runs = repo.list_benchmark_runs(session)
    finally:
        dbh.dispose()
    for run in runs:
        # 公开来源没写的字段：必须为空，且必须被登记（不是静默）
        # 写了的就是写了（例如 Qwen3-Coder 来源明确是 FP8），二者必须一致 ——
        # 这条断言正是用来抓"给缺失字段补造一个值"的
        for field in ("model_revision", "weight_precision"):
            value = getattr(run.signature, field)
            flagged = f"missing:{field}" in run.quality_flags
            assert (value is None) == flagged, (
                f"{run.run_id}.{field}={value!r} 与质量标记不一致（不得补造）"
            )
        assert any(f.startswith("missing:") for f in run.quality_flags)
    # 模型版本在所有来源里都未发布
    assert all(r.signature.model_revision is None for r in runs)
    # 指标确实搬进来了（不是空壳）
    with_p99 = [r for r in runs if r.profile.ttft_p99_ms is not None]
    assert len(with_p99) == 14


# --------------------------------------------------------------------------
# D3/D4：一份配置跑出候选 + 参考 + 成本
# --------------------------------------------------------------------------


def test_demo_plan_generates_candidates_and_references(tmp_path):
    db = _demo_db(tmp_path)
    result = evaluate_plan(load_plan_request(ROOT / DEMO_CONFIG), db_path=db)

    # 1) 能从需求生成部署候选
    assert result.domains["sizing"] == "computed"
    assert len(result.candidates) >= 2
    assert any(c.static_status == "conditional_feasible" for c in result.candidates)

    # 2) 公开数据被读取并作为**外部参考**列示（不参与候选判定）
    assert result.external_references, "公开实测必须出现在报告里"
    ref = result.external_references[0]
    assert ref.ttft_p99_ms is not None
    assert ref.output_throughput_tok_s is not None
    assert ref.source_file and ref.source_sha256
    assert ref.not_usable_reasons, "必须说明为什么不能直接用于本需求"

    # 3) 候选的性能结论没有臆造成"达标"
    assert all(c.sla_status != "observed_pass" for c in result.candidates)
    # 4) 成本是假设值 -> 不完整 -> 不给全周期总额
    assert all(c.cost_incomplete for c in result.candidates)
    assert all(c.total_cash_over_horizon is None for c in result.candidates)
    # 5) 报价资格没有被顺带放行
    assert all(not c.quote_publishable for c in result.candidates)


def test_demo_report_labels_every_layer(tmp_path):
    db = _demo_db(tmp_path)
    result = evaluate_plan(load_plan_request(ROOT / DEMO_CONFIG), db_path=db)
    md = build_plan_report(result)

    # 结论摘要 / 候选 / 公开参考 / 推荐 / 缺口 / 边界 / 附录
    for section in (
        "## 1. 结论摘要",
        "## 2. 候选方案",
        "## 2.1 公开数据参考",
        "## 3. 推荐依据",
        "## 4. 缺口与下一步",
        "## 5. 结论边界",
    ):
        assert section in md, section

    # 公开参考节：来源文件 + sha256 + 为什么不能用
    assert "公开数据参考" in md and "不参与" in md
    assert "data/raw/official/sglang_cookbook_qwen3" in md
    assert "公开来源未给出模型版本" in md

    # 三层数据性质都要能被读者区分
    assert "未授权" in md and "不得用于生产环境的报价自动发布" in md
    assert "不完整（含未确认/缺失项）" in md


def test_demo_result_guard_keeps_layers_separate(tmp_path):
    """参考数据不得混进候选的 SLA 结论。"""
    db = _demo_db(tmp_path)
    result = evaluate_plan(load_plan_request(ROOT / DEMO_CONFIG), db_path=db)
    ref_ids = {r.run_id for r in result.external_references}
    for candidate in result.candidates:
        assert not (set(candidate.evidence_run_ids) & ref_ids), (
            "公开参考数据不得成为候选的性能证据"
        )


# --------------------------------------------------------------------------
# D5：无 GPU 可重复执行
# --------------------------------------------------------------------------


def test_demo_script_runs_offline_end_to_end(tmp_path):
    """一键脚本在无 GPU 环境下可复现（含报告与结果文件）。"""
    out_dir = tmp_path / "demo"
    result = subprocess.run(
        ["bash", str(ROOT / "scripts" / "run_demo.sh"), str(out_dir)],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "报告:" in result.stdout
    report = out_dir / "demo_report.md"
    payload = out_dir / "demo_result.json"
    assert report.exists() and payload.exists()

    data = json.loads(payload.read_text(encoding="utf-8"))
    assert data["domains"]["sizing"] == "computed"
    assert data["candidates"]
    assert data["external_references"]
    assert data["summary"]["publishable"] == []
    assert "公开数据只作" in result.stdout


# --------------------------------------------------------------------------
# D6：参考数据的匹配与来源展示
# --------------------------------------------------------------------------


def test_reference_matching_uses_repo_id_not_commercial_model_id(tmp_path):
    """商业 model_id（``qwen3-32b``）不得与公开数据的 repo_id（``Qwen/Qwen3-32B``）
    直接比较 —— 否则同模型的公开数据会被误报成「模型不同」。

    反例同时验证：真正不同的模型**必须**仍然报出来。
    """
    db = _demo_db(tmp_path)
    result = evaluate_plan(load_plan_request(ROOT / DEMO_CONFIG), db_path=db)

    same_model = [
        r for r in result.external_references if r.model_name == "Qwen/Qwen3-32B"
    ]
    assert same_model, "Demo 里应当有同模型的公开参考"
    for ref in same_model:
        assert not any("模型不同" in reason for reason in ref.not_usable_reasons), (
            f"{ref.run_id} 是同模型数据，却被报成模型不同：{ref.not_usable_reasons}"
        )
    # 真正不同的模型仍然必须被指出
    other_model = [r for r in result.external_references if r.model_name != "Qwen/Qwen3-32B"]
    assert other_model
    for ref in other_model:
        assert any("模型不同" in reason for reason in ref.not_usable_reasons)


def test_reference_reasons_cover_all_identity_dimensions(tmp_path):
    """差异不能只看模型名：GPU / 卡数 / TP / 框架 / 负载都要能落到原因里。"""
    db = _demo_db(tmp_path)
    result = evaluate_plan(load_plan_request(ROOT / DEMO_CONFIG), db_path=db)
    ref = next(r for r in result.external_references if r.model_name == "Qwen/Qwen3-32B")
    joined = "；".join(ref.not_usable_reasons)
    assert "GPU 型号不同" in joined
    assert "负载长度不同" in joined
    # 记录侧没写的字段也要点名，不能沉默
    assert "公开来源未给出" in joined
    # 每条都要有明确的匹配等级（由 Matcher 给出，不是 None）
    assert ref.match_level in ("exact_match", "comparable_reference",
                               "modeling_candidate", "rejected")


def test_reference_same_conditions_require_both_sides(tmp_path):
    """反例：「与目标相同的条件」不能只看目标侧。

    公开数据没给配置并发/数据集时，绝不能写成「并发一致」——
    那会把"没数据"读成"条件相符"。
    """
    db = _demo_db(tmp_path)
    result = evaluate_plan(load_plan_request(ROOT / DEMO_CONFIG), db_path=db)
    for ref in result.external_references:
        if ref.configured_concurrency is None:
            assert not any("配置并发" in item for item in ref.same_conditions), (
                f"{ref.run_id} 没给并发，却声明并发条件相同：{ref.same_conditions}"
            )
        if ref.dataset_name is None:
            assert not any("数据集" in item for item in ref.same_conditions)
        if ref.input_len_target is None:
            assert not any("长度" in item for item in ref.same_conditions)


def test_reference_source_url_is_traceable(tmp_path):
    """来源链接必须来自数据本身，不是写死的 None。"""
    db = _demo_db(tmp_path)
    result = evaluate_plan(load_plan_request(ROOT / DEMO_CONFIG), db_path=db)
    for ref in result.external_references:
        assert ref.source_url and ref.source_url.startswith("https://"), (
            f"{ref.run_id} 没有可追溯的来源链接"
        )
        assert ref.publisher
    # 本轮新增的两个来源都要能被追溯到原文：
    # 社区报告在参考清单里；GPUStack 那条被候选采用，因此走候选的来源标注。
    ref_urls = {r.source_url for r in result.external_references}
    assert any("github.com/MG-Cafe" in u for u in ref_urls)
    md = build_plan_report(result)
    assert "https://docs.gpustack.ai/2.0/performance-lab/qwen3-32b/h100/" in md
    assert "https://github.com/MG-Cafe" in md


def test_reference_sorting_puts_closest_first(tmp_path):
    """排序按（匹配等级 -> 相同条件数 -> 输入长度接近程度），只影响展示。"""
    db = _demo_db(tmp_path)
    result = evaluate_plan(load_plan_request(ROOT / DEMO_CONFIG), db_path=db)
    refs = result.external_references
    order = {"exact_match": 0, "comparable_reference": 1, "modeling_candidate": 2,
             "rejected": 3}
    levels = [order[r.match_level] for r in refs]
    assert levels == sorted(levels), "匹配等级必须单调不降"
    # 同等级内部：相同条件多的排前面
    for a, b in zip(refs, refs[1:]):
        if order[a.match_level] == order[b.match_level]:
            assert len(a.same_conditions) >= len(b.same_conditions)


# --------------------------------------------------------------------------
# D7：贴近目标的公开参考
# --------------------------------------------------------------------------


def test_reference_never_becomes_sla_evidence(tmp_path):
    """部分匹配的公开数据**不得**升级为目标配置的 SLA 证据。"""
    db = _demo_db(tmp_path)
    result = evaluate_plan(load_plan_request(ROOT / DEMO_CONFIG), db_path=db)
    for candidate in result.candidates:
        assert candidate.sla_status != "observed_pass"
        assert candidate.rank_bucket != "verified_sla_met"
        # 只要匹配等级不是完全一致，就必须在候选旁边写明数字的来源
        if candidate.match_level not in (None, "exact_match"):
            assert any("不是**本配置" in n or "不是本配置" in n for n in candidate.notes), (
                f"{candidate.candidate_id} 借用了非本配置的性能数字，却没有标注来源"
            )


def test_reference_records_keep_unknown_fields_empty(tmp_path):
    """公开来源没给的字段（revision / 并发 / 长度）保持为空，不推算。"""
    db = _demo_db(tmp_path)
    dbh, repo = open_db(db)
    try:
        with dbh.session() as session:
            runs = repo.list_benchmark_runs(session)
    finally:
        dbh.dispose()
    by_id = {r.run_id: r for r in runs}
    gpustack = by_id["public::gpustack_perf_lab_qwen3_32b_h100:vllm-sharegpt"]
    # 单卡 H100；页面未声明 TP/DP 与长度
    assert gpustack.signature.gpu_count == 1
    assert gpustack.signature.tp_size is None
    assert gpustack.signature.workload.input_len_target is None
    assert gpustack.signature.workload.configured_concurrency is None
    # 观测峰值并发必须与"配置并发"分开存
    assert gpustack.profile.observed_concurrency == 1000
    assert gpustack.profile.configured_concurrency is None
    # 「未声明」必须被登记，不是静默
    for field in ("model_revision", "weight_precision", "tp_size", "dp_size"):
        assert f"missing:{field}" in gpustack.quality_flags

    community = by_id[
        "public::community_qwen3_32b_h100_4gpu_report:h100-4gpu-random-32k"
    ]
    # 原文写了 bf16 -> 不能反手标成缺失
    assert community.signature.weight_precision == "bfloat16"
    assert "missing:weight_precision" not in community.quality_flags
    # GPU 型号按原文记录，不做别名推断
    assert community.signature.hardware_model == "NVIDIA H100 80GB HBM3"
    assert community.data_trust == "measured_third_party"


# --------------------------------------------------------------------------
# D8：报告解释力
# --------------------------------------------------------------------------


def test_report_shows_only_the_closest_references(tmp_path):
    """正文只展开最相关的几条，其余进附录；JSON 里仍然完整。"""
    db = _demo_db(tmp_path)
    result = evaluate_plan(load_plan_request(ROOT / DEMO_CONFIG), db_path=db)
    md = build_plan_report(result)

    assert "### 最相关的 3 条" in md
    assert "## 7. 附录：其余公开参考数据" in md
    # 附录必须说明"其余"有多少条，且与总数对得上
    assert f"以下 {len(result.external_references) - 3} 条" in md
    assert f"库里共有 **{len(result.external_references)} 条**" in md
    # 完整清单不会丢：结构化结果里每条都在
    dumped = result.model_dump(mode="json")
    assert len(dumped["external_references"]) == len(result.external_references)
    for ref in dumped["external_references"]:
        assert "same_conditions" in ref
        assert ref["not_usable_reasons"]


def test_report_shows_same_and_incomparable_conditions(tmp_path):
    db = _demo_db(tmp_path)
    result = evaluate_plan(load_plan_request(ROOT / DEMO_CONFIG), db_path=db)
    md = build_plan_report(result)
    assert "**与目标相同的条件**" in md
    assert "**不能直接比较的条件**" in md
    # 每条展示出来的参考都要有来源链接与存档哈希
    assert "https://github.com/sgl-project/sglang" in md
    assert "sha256 `" in md


def test_report_states_five_data_layers(tmp_path):
    db = _demo_db(tmp_path)
    result = evaluate_plan(load_plan_request(ROOT / DEMO_CONFIG), db_path=db)
    md = build_plan_report(result)
    for layer in (
        "静态资源可行",
        "公开性能参考",
        "已验证 SLA",
        "假设成本",
        "正式报价资格",
    ):
        assert layer in md, layer
    assert "请勿跨层混用" in md


def test_report_marks_borrowed_performance_numbers(tmp_path):
    """候选借用了非本配置的性能数字时，主表与附录都必须标明。"""
    db = _demo_db(tmp_path)
    result = evaluate_plan(load_plan_request(ROOT / DEMO_CONFIG), db_path=db)
    md = build_plan_report(result)
    borrowed = [
        c for c in result.candidates if c.match_level not in (None, "exact_match")
    ]
    if borrowed:
        assert "仅可建模（非本配置）" in md or "可比参考（非本配置）" in md
        assert "**非本配置实测**" in md


# --------------------------------------------------------------------------
# D9：交付纪律
# --------------------------------------------------------------------------


def test_demo_script_refuses_to_delete_an_existing_custom_db(tmp_path):
    """DEMO_DB 指向已有数据库时必须拒绝运行，且不得删除它（D6）。"""
    sentinel = tmp_path / "my-precious.db"
    sentinel.write_text("do-not-touch", encoding="utf-8")
    result = subprocess.run(
        ["bash", str(ROOT / "scripts" / "run_demo.sh"), str(tmp_path / "out")],
        capture_output=True,
        text=True,
        cwd=ROOT,
        env={**os.environ, "DEMO_DB": str(sentinel)},
    )
    assert result.returncode != 0
    assert "拒绝覆盖" in (result.stdout + result.stderr)
    assert sentinel.read_text(encoding="utf-8") == "do-not-touch"


def test_demo_script_accepts_a_fresh_custom_db(tmp_path):
    """未占用时，DEMO_DB 指定全新路径可以正常运行。"""
    target = tmp_path / "fresh.db"
    result = subprocess.run(
        ["bash", str(ROOT / "scripts" / "run_demo.sh"), str(tmp_path / "out")],
        capture_output=True,
        text=True,
        cwd=ROOT,
        env={**os.environ, "DEMO_DB": str(target)},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert target.exists()
