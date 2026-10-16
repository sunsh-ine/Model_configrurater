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
    dbh, repo = open_db(db)
    try:
        with dbh.session() as session:
            runs = repo.list_benchmark_runs(session)
    finally:
        dbh.dispose()
    assert len(runs) == 12
    for run in runs:
        # 来源可追溯：归档文件 + sha256 + 上游 git blob sha
        assert run.raw_file and run.raw_file.startswith("data/raw/official/")
        assert run.raw_sha256 and len(run.raw_sha256) == 64
        assert any(f.startswith("source_git_blob_sha:") for f in run.quality_flags)
        # 厂商发布路径：不经过 attest，也不需要
        assert run.data_trust == "measured_vendor_published"
        assert not any(f.startswith("pending_attestation:") for f in run.quality_flags)


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
    assert len(with_p99) == 12


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
