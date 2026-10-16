"""S4：三个扩展方向的契约测试。

计划 §6 验收：

* 新增一个受支持 GPU SKU，不必修改主工作流；
* 增加一个 Benchmark 格式，只需局部新增适配并补相应测试；
* 增加同结构的计费规则以数据配置完成，不影响资源或性能模块；
* 不因扩展点而产生大量空接口、抽象基类和动态注册机制。
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
import yaml
from typer.testing import CliRunner

from llm_capacity_pricing.cli import app
from llm_capacity_pricing.schemas.benchmark import BenchmarkRecord
from llm_capacity_pricing.schemas.evidence import Evidence
from llm_capacity_pricing.schemas.plan import (
    PlanAdvanced,
    PlanCostInput,
    PlanRequest,
    PlanResourceAssumptions,
    PlanWorkload,
)
from llm_capacity_pricing.services import (
    PERFORMANCE_ADAPTERS,
    import_cost_catalog,
    import_hardware,
    import_models,
    import_performance_run,
    init_db,
    open_db,
)
from llm_capacity_pricing.workflow import evaluate_plan

runner = CliRunner()
REAL_CATALOG = "data/hardware/hardware_catalog.yaml"
REAL_COSTS = "data/examples/phase4_cost_inputs.example.yaml"
PLAN_TEMPLATE = "data/examples/phase4_cost_plan.example.yaml"


def _base_db(tmp_path: Path, root: Path, name: str) -> str:
    db = str(tmp_path / name)
    init_db(db)
    import_models(root / "data/models/model_manifest.yaml", db, root=root)
    return db


# --------------------------------------------------------------------------
# 方向一：数据扩展（新增 GPU SKU 只需改数据 + 一行命名映射）
# --------------------------------------------------------------------------


def _catalog_with_extra_sku(tmp_path: Path, root: Path) -> Path:
    """在真实目录基础上追加一个**结构性**新 SKU（规格沿用 H100，仅换身份）。

    这是扩展路径的结构验证，不声称该 SKU 的任何真实规格。
    """
    doc = yaml.safe_load((root / REAL_CATALOG).read_text(encoding="utf-8"))
    template = next(
        h for h in doc["hardware"] if h["hardware_id"] == "nvidia-h100-sxm-80gb"
    )
    clone = dict(template)
    clone["hardware_id"] = "test-extra-sku-80gb"
    clone["product_name"] = "Test Extra SKU 80GB"
    clone["sku"] = "TEST-EXTRA"
    clone["supported_in_phase2"] = True
    doc["hardware"].append(clone)
    out = tmp_path / "hardware_extra.yaml"
    out.write_text(yaml.safe_dump(doc, allow_unicode=True), encoding="utf-8")
    return out


def test_s4_new_gpu_sku_needs_data_plus_one_mapping(tmp_path, root):
    """新增 SKU：目录加一条 + 命名映射加一行，主工作流不改。"""
    from llm_capacity_pricing.sizing.hardware import (
        SIGNATURE_PRODUCT_NAMES,
        product_name_for_hardware_id,
    )

    catalog = _catalog_with_extra_sku(tmp_path, root)
    db = _base_db(tmp_path, root, "s4a.db")
    info = import_hardware(str(catalog), db)
    assert "test-extra-sku-80gb" in [h["hardware_id"] for h in info["hardware"]]

    # 主工作流（evaluate_plan）直接接受新 SKU，无需任何代码分支
    request = PlanRequest(
        request_id="s4-extra-sku",
        model_id="qwen3-32b",
        hardware_ids=["test-extra-sku-80gb"],
        server_id="hgx-h100-8gpu",
        workload=PlanWorkload(input_len=8192, output_len=256, concurrency=32),
        resources=PlanResourceAssumptions(
            usable_fraction=Decimal("0.9"),
            usable_source_kind="synthetic_example",
            runtime_reserve_fraction=Decimal("0.1"),
        ),
        advanced=PlanAdvanced(tp_sizes=[4], dp_sizes=[1]),
    )
    result = evaluate_plan(request, db_path=db)
    assert result.domains["sizing"] == "computed"
    assert [c.hardware_id for c in result.candidates] == ["test-extra-sku-80gb"]

    # 未登记命名映射时，签名商品名回落为 ID 本身（不猜、不编）
    assert product_name_for_hardware_id("test-extra-sku-80gb") == "test-extra-sku-80gb"
    # 登记一行之后即可参与第三阶段匹配
    SIGNATURE_PRODUCT_NAMES["test-extra-sku-80gb"] = "Test Extra SKU 80GB"
    try:
        assert product_name_for_hardware_id("test-extra-sku-80gb") == "Test Extra SKU 80GB"
    finally:
        SIGNATURE_PRODUCT_NAMES.pop("test-extra-sku-80gb", None)


def test_s4_extension_did_not_add_abstractions():
    """扩展点是普通数据结构，不是插件框架。"""
    from llm_capacity_pricing.sizing.hardware import SIGNATURE_PRODUCT_NAMES

    assert isinstance(SIGNATURE_PRODUCT_NAMES, dict)
    assert isinstance(PERFORMANCE_ADAPTERS, dict)
    # 注册表是"类型 -> (模块, 函数名)"的查找表，没有基类/装饰器/动态发现
    for kind, entry in PERFORMANCE_ADAPTERS.items():
        assert isinstance(kind, str) and isinstance(entry, tuple) and len(entry) == 2


# --------------------------------------------------------------------------
# 方向二：Benchmark 适配扩展（新增格式只加适配器 + 注册一行）
# --------------------------------------------------------------------------


def _write_minimal_result(tmp_path: Path) -> Path:
    """一种**假想的新格式**：扁平 JSON，字段命名与 vLLM/SGLang 都不同。"""
    payload = {
        "run_label": "NEWFMT-001",
        "served_model": "Qwen/Qwen3-32B",
        "revision": "main",
        "dtype": "bf16",
        "device_name": "NVIDIA H100 SXM",
        "device_count": 4,
        "tensor_parallel": 4,
        "data_parallel": 1,
        "engine_name": "somelib",
        "engine_version": "9.9.9",
        "concurrency": 8,
        "prompt_tokens": 1024,
        "completion_tokens": 256,
        "dataset_label": "random",
        "latency": {"ttft_p99": 120.0, "tpot_p99": 12.0, "e2e_p99": 900.0},
        "throughput": {"output_tokens_per_second": 1234.5, "requests_per_second": 4.8},
        "window_seconds": 120.0,
        "request_total": 576,
        "request_succeeded": 576,
    }
    path = tmp_path / "newformat.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _parse_newformat_full(
    file: str | Path, metadata: dict[str, Any]
) -> tuple[list[BenchmarkRecord], list[Evidence]]:
    """新格式适配器：**只做字段映射**，不做单位换算之外的计算。"""
    data = json.loads(Path(file).read_text(encoding="utf-8"))
    record_id = f"newfmt_{data['run_label']}"
    evidence = Evidence(
        evidence_id=f"ev_{record_id}",
        source_type="internal_measurement",
        measurement_origin="internal_measurement",
        is_internal_measurement=True,
        source_file=str(file),
        raw_sha256="0" * 64,
        publisher=metadata.get("publisher", "unknown"),
    )
    record = BenchmarkRecord(
        record_id=record_id,
        model_name=data["served_model"],
        model_revision=data["revision"],
        model_precision=data["dtype"],
        hardware_model=data["device_name"],
        deployment_gpu_count=data["device_count"],
        framework=data["engine_name"],
        framework_version=data["engine_version"],
        tp_size=data["tensor_parallel"],
        dp_size=data["data_parallel"],
        configured_concurrency=data["concurrency"],
        input_tokens_target=data["prompt_tokens"],
        output_tokens_target=data["completion_tokens"],
        dataset_name=data["dataset_label"],
        ttft_p99_ms=Decimal(str(data["latency"]["ttft_p99"])),
        tpot_p99_ms=Decimal(str(data["latency"]["tpot_p99"])),
        e2e_p99_ms=Decimal(str(data["latency"]["e2e_p99"])),
        output_tps_cluster=Decimal(str(data["throughput"]["output_tokens_per_second"])),
        duration_s=Decimal(str(data["window_seconds"])),
        num_requests=data["request_total"],
        success_count=data["request_succeeded"],
        performance_source_kind="internal_measurement",
        verification_status="verified",
        evidence_id=evidence.evidence_id,
    )
    return [record], [evidence]


def test_s4_new_benchmark_adapter_plugs_in_without_touching_domains(tmp_path, root):
    """注册一个迷你适配器 -> 导入 -> 匹配 -> SLA -> 统一评估，全程不改领域代码。"""
    db = _base_db(tmp_path, root, "s4b.db")
    import_hardware(REAL_CATALOG, db)
    import_cost_catalog(REAL_COSTS, db)

    PERFORMANCE_ADAPTERS["newformat"] = ("tests.test_s_phase_extension", "_parse_newformat_full")
    try:
        source = _write_minimal_result(tmp_path)
        meta = tmp_path / "meta.yaml"
        meta.write_text(
            yaml.safe_dump(
                {
                    "publisher": "extension-contract-test",
                    "launch_command": "somelib serve --tp 4",
                    "benchmark_command": "somelib bench --concurrency 8",
                },
                allow_unicode=True,
            ),
            encoding="utf-8",
        )
        info = import_performance_run(source, meta, db, kind="newformat")
        assert info["run_count"] == 1
        assert info["data_trust"] == ["measured_internal"]

        dbh, repo = open_db(db)
        try:
            with dbh.session() as session:
                run = repo.get_benchmark_run(session, info["run_ids"][0])
        finally:
            dbh.dispose()
        assert run is not None
        assert run.profile.ttft_p99_ms == Decimal("120.0")
        assert run.profile.output_throughput_tok_s == Decimal("1234.5")

        # 统一评估入口直接消费新格式的数据（性能领域出结论）
        request = PlanRequest(
            request_id="s4-newformat",
            model_id="qwen3-32b",
            hardware_ids=["nvidia-h100-sxm-80gb"],
            server_id="hgx-h100-8gpu",
            workload=PlanWorkload(
                input_len=1024,
                output_len=256,
                concurrency=8,
                dataset="random",
                # 目标侧身份必须齐备才可能达到 exact_match（arrival_mode 也是身份之一）
                arrival_mode="unbounded",
            ),
            resources=PlanResourceAssumptions(
                usable_fraction=Decimal("0.9"),
                usable_source_kind="synthetic_example",
                runtime_reserve_fraction=Decimal("0.1"),
            ),
            sla={"requirement_id": "req-newfmt", "ttft_p99_ms_max": 400},  # type: ignore[arg-type]
            cost=PlanCostInput(plan_file=PLAN_TEMPLATE, template_id="syn-owned-3y"),
            advanced=PlanAdvanced(
                tp_sizes=[4],
                dp_sizes=[1],
                model_revision="main",
                framework="somelib",
                framework_version="9.9.9",
            ),
        )
        result = evaluate_plan(request, db_path=db)
        assert result.domains["performance"] == "computed"
        candidate = next(c for c in result.candidates if c.sla_status is not None)
        assert candidate.match_level == "exact_match"
        assert candidate.sla_status == "observed_pass"
        assert candidate.evidence_run_ids == [info["run_ids"][0]]
    finally:
        PERFORMANCE_ADAPTERS.pop("newformat", None)


def test_s4_unregistered_adapter_kind_is_rejected(tmp_path):
    db = _base_db(tmp_path, Path(".").resolve(), "s4c.db")
    with pytest.raises(Exception, match="未知性能数据来源"):
        import_performance_run("x.json", "y.yaml", db, kind="does-not-exist")


# --------------------------------------------------------------------------
# 方向三：成本 / 计费规则扩展（纯数据配置）
# --------------------------------------------------------------------------


def test_s4_new_billing_rule_is_data_only(tmp_path, root):
    """新增一条同结构计费规则：只改数据，资源与性能模块不受影响。"""
    doc = yaml.safe_load((root / REAL_COSTS).read_text(encoding="utf-8"))
    original_rule = doc["billing_rules"][0]
    second = dict(original_rule)
    second = json.loads(json.dumps(second))
    second["rule_id"] = "syn-billing-extra-model"
    second["model_name"] = "qwen3-8b"
    doc["billing_rules"].append(second)
    catalog = tmp_path / "costs_extra.yaml"
    catalog.write_text(yaml.safe_dump(doc, allow_unicode=True), encoding="utf-8")

    db = _base_db(tmp_path, root, "s4d.db")
    import_hardware(REAL_CATALOG, db)
    info = import_cost_catalog(str(catalog), db)
    assert info["billing_rule_count"] == 2

    dbh, repo = open_db(db)
    try:
        with dbh.session() as session:
            rules = {r.rule_id: r for r in repo.list_billing_rules(session)}
            items = repo.list_cost_catalog_items(session)
    finally:
        dbh.dispose()
    assert set(rules) == {"syn-billing-qwen3-32b", "syn-billing-extra-model"}
    assert rules["syn-billing-extra-model"].model_name == "qwen3-8b"
    # 资源与性能领域的库存不受影响
    assert items  # 成本项仍在
    request = PlanRequest(
        request_id="s4-costs",
        model_id="qwen3-32b",
        hardware_ids=["nvidia-h100-sxm-80gb"],
        server_id="hgx-h100-8gpu",
        workload=PlanWorkload(input_len=8192, output_len=256, concurrency=32),
        resources=PlanResourceAssumptions(
            usable_fraction=Decimal("0.9"),
            usable_source_kind="synthetic_example",
            runtime_reserve_fraction=Decimal("0.1"),
        ),
        cost=PlanCostInput(plan_file=PLAN_TEMPLATE, template_id="syn-owned-3y"),
        advanced=PlanAdvanced(tp_sizes=[4], dp_sizes=[1]),
    )
    result = evaluate_plan(request, db_path=db)
    assert result.domains["sizing"] == "computed"
    assert result.domains["economics"] == "computed"
    assert any(c.plan_id for c in result.candidates)


def test_s4_new_cost_category_is_data_only(tmp_path, root):
    """新增一个**已有类别**的成本项（同结构）同样只需数据配置。"""
    doc = yaml.safe_load((root / REAL_COSTS).read_text(encoding="utf-8"))
    extra = json.loads(json.dumps(doc["cost_items"][0]))
    extra.update(
        {
            "item_id": "syn-gpu-purchase-alt",
            "name": "【合成示例】GPU 单卡采购价（备选批次）",
            "unit_price": "210000",
        }
    )
    doc["cost_items"].append(extra)
    catalog = tmp_path / "costs_more_items.yaml"
    catalog.write_text(yaml.safe_dump(doc, allow_unicode=True), encoding="utf-8")

    db = _base_db(tmp_path, root, "s4e.db")
    import_hardware(REAL_CATALOG, db)
    info = import_cost_catalog(str(catalog), db)
    assert info["cost_item_count"] == len(doc["cost_items"])
