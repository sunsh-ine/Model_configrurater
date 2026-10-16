"""S3：公共规则单一权威 + 错误表达统一。

计划 §5 验收：

> 修改一条已有业务规则时，核心实现只需要在其权威领域修改；
> 所有用户入口自动获得一致结果。

本文件用**契约测试**把"单一权威"固定下来：断言各消费方引用的是同一份定义，
并通过运行时改动权威定义来证明传播生效。
"""

from __future__ import annotations

import ast
import re
from decimal import Decimal
from pathlib import Path

import pytest

SRC = Path("src/llm_capacity_pricing")


def _py_files() -> list[Path]:
    return sorted(p for p in SRC.rglob("*.py") if "__pycache__" not in str(p))


# --------------------------------------------------------------------------
# 1. 数据可信等级：唯一权威 = schemas/performance.PRODUCTION_TRUST_LEVELS
# --------------------------------------------------------------------------


def test_s3_trust_levels_have_one_authoritative_definition():
    from llm_capacity_pricing.engines.quote_gate import (
        PRODUCTION_TRUST_LEVELS as gate_levels,
    )
    from llm_capacity_pricing.perf.estimator import DEFAULT_ALLOWED_TRUST
    from llm_capacity_pricing.schemas.performance import (
        PRODUCTION_TRUST_LEVELS,
        SlaPolicy,
    )
    from llm_capacity_pricing.services import PHASE4_TRUSTED_LEVELS

    assert PHASE4_TRUSTED_LEVELS == PRODUCTION_TRUST_LEVELS
    assert gate_levels == PRODUCTION_TRUST_LEVELS
    assert DEFAULT_ALLOWED_TRUST == PRODUCTION_TRUST_LEVELS
    assert tuple(SlaPolicy().accepted_trust_levels) == PRODUCTION_TRUST_LEVELS

    # 消费方只是别名，不各自维护一份字面量
    assert PHASE4_TRUSTED_LEVELS is PRODUCTION_TRUST_LEVELS
    assert gate_levels is PRODUCTION_TRUST_LEVELS


def test_s3_no_module_redeclares_the_trust_pair():
    """除权威定义文件外，不得再出现"两个等级并列成列表/元组"的写法。"""
    pattern = re.compile(
        r'["\']measured_internal["\']\s*,\s*["\']measured_vendor_published["\']'
    )
    authoritative = "schemas/performance.py"
    offenders = [
        str(p.relative_to(SRC))
        for p in _py_files()
        if str(p.relative_to(SRC)) != authoritative
        and pattern.search(p.read_text(encoding="utf-8"))
    ]
    assert offenders == [], f"可信等级被重复定义于：{offenders}"


def test_s3_cli_and_services_use_the_same_trust_levels():
    """CLI 的算法验证信任集与 services 同源（只有 synthetic 是额外项）。"""
    from llm_capacity_pricing import cli
    from llm_capacity_pricing.schemas.performance import PRODUCTION_TRUST_LEVELS
    from llm_capacity_pricing.services import PHASE4_TRUSTED_LEVELS

    source = Path(cli.__file__).read_text(encoding="utf-8")
    assert "[*PRODUCTION_TRUST_LEVELS, \"synthetic_fixture\"]" in source
    assert PHASE4_TRUSTED_LEVELS == PRODUCTION_TRUST_LEVELS


# --------------------------------------------------------------------------
# 2. 硬件 ID -> 签名商品名：唯一权威 = sizing/hardware
# --------------------------------------------------------------------------


def test_s3_hardware_name_mapping_matches_the_catalog(root: Path):
    """映射必须与 data/hardware/hardware_catalog.yaml 的 product_name 一致。

    这是"新增一张 GPU 卡"时最容易漏改的地方，因此用契约测试锁住。
    """
    import yaml

    from llm_capacity_pricing.sizing.hardware import SIGNATURE_PRODUCT_NAMES

    catalog = yaml.safe_load(
        (root / "data/hardware/hardware_catalog.yaml").read_text(encoding="utf-8")
    )
    mismatches = {}
    for entry in catalog["hardware"]:
        hw_id, product = entry["hardware_id"], entry["product_name"]
        if SIGNATURE_PRODUCT_NAMES.get(hw_id) != product:
            mismatches[hw_id] = (SIGNATURE_PRODUCT_NAMES.get(hw_id), product)
    assert mismatches == {}, f"映射与目录不一致：{mismatches}"


def test_s3_unknown_hardware_id_is_returned_as_is():
    from llm_capacity_pricing.sizing.hardware import product_name_for_hardware_id

    assert product_name_for_hardware_id("some-new-sku") == "some-new-sku"
    assert product_name_for_hardware_id(None) is None


def test_s3_adding_a_sku_propagates_to_all_consumers(root: Path):
    """运行时给权威映射加一条 -> ranker / workflow 立刻生效（不必改多处）。"""
    from llm_capacity_pricing.perf.ranker import _hardware_name
    from llm_capacity_pricing.sizing.hardware import (
        SIGNATURE_PRODUCT_NAMES,
        product_name_for_hardware_id,
    )
    from llm_capacity_pricing.workflow import _hardware_product_name

    SIGNATURE_PRODUCT_NAMES["s3-fixture-gpu"] = "S3 Fixture GPU"
    try:
        assert product_name_for_hardware_id("s3-fixture-gpu") == "S3 Fixture GPU"
        assert _hardware_product_name("s3-fixture-gpu") == "S3 Fixture GPU"

        class _Candidate:
            hardware_id = "s3-fixture-gpu"

        assert _hardware_name(_Candidate()) == "S3 Fixture GPU"
    finally:
        SIGNATURE_PRODUCT_NAMES.pop("s3-fixture-gpu", None)


def test_s3_only_one_hardware_name_mapping_exists():
    """除权威模块与测试外，源码里不得再出现硬编码的商品名映射。"""
    offenders = []
    for p in _py_files():
        if p.name == "hardware.py" and p.parent.name == "sizing":
            continue
        src = p.read_text(encoding="utf-8")
        if '"nvidia-h100-sxm-80gb": "NVIDIA H100 SXM"' in src:
            offenders.append(str(p.relative_to(SRC)))
    assert offenders == [], f"硬件名映射被重复定义于：{offenders}"


# --------------------------------------------------------------------------
# 3. 币种可比性：唯一权威 = engines/tco.currencies_comparable
# --------------------------------------------------------------------------


def test_s3_currency_rule_has_one_implementation():
    offenders = [
        str(p.relative_to(SRC))
        for p in _py_files()
        if 'f"currency_mismatch:{' in p.read_text(encoding="utf-8")
    ]
    assert offenders == ["engines/tco.py"], offenders


def test_s3_currency_rule_reason_codes_are_shared():
    from llm_capacity_pricing.engines.tco import currencies_comparable

    assert currencies_comparable("CNY", "CNY") == (True, None)
    assert currencies_comparable(None, "CNY") == (False, "currency_unknown")
    assert currencies_comparable("CNY", None) == (False, "currency_unknown")
    ok, reason = currencies_comparable("USD", "CNY")
    # 原因码与传参顺序无关（CNY 与 USD 的组合只有一个码）
    assert ok is False and reason == "currency_mismatch:CNY!=USD"
    assert currencies_comparable("CNY", "USD")[1] == reason


def test_s3_comparison_breakeven_and_budget_agree_on_the_reason():
    """同一条规则在三个消费方产生的**原因码**必须一致。"""
    from llm_capacity_pricing.decision.comparison import _margin_allowed
    from llm_capacity_pricing.decision.sensitivity import (
        analyze_breakeven,
        check_budget,
    )
    from llm_capacity_pricing.engines.tco import currencies_comparable, compute_tco
    from llm_capacity_pricing.schemas.tco import DeploymentCostPlan, FinancingAssumptions

    expected = currencies_comparable("USD", "CNY")[1]
    assert expected is not None

    class _Tco:
        incomplete = False
        currency = "CNY"
        annual_total_cost = Decimal("100")

    class _Rev:
        currency = "USD"

    ok, reason = _margin_allowed(_Tco(), _Rev())
    assert ok is False and reason == expected

    budget = check_budget(
        budget_id="b",
        budget_amount=Decimal("10"),
        budget_kind="total_horizon",
        compared_value=Decimal("5"),
        currency="CNY",
        currency_confirmed=True,
        compared_currency="USD",
    )
    assert budget.status == "unknown"
    assert any(r.startswith(expected) for r in budget.reasons)

    plan = DeploymentCostPlan(
        plan_id="p", candidate_id="c", hardware_id="h",
        gpu_count=1, server_count=1, cost_basis="owned",
        assumptions=FinancingAssumptions(),
    )
    tco = compute_tco(plan, [])
    be = analyze_breakeven(tco=tco, revenue=_Rev(), rule=None)
    assert be.revenue_cost_ratio is None


# --------------------------------------------------------------------------
# 4. 错误表达统一：PlanResult 沿用既有状态码，不另立平行体系
# --------------------------------------------------------------------------


def test_s3_plan_result_reuses_existing_status_vocabulary():
    """PlanResult 的字段取值必须落在既有 literal 的取值域内。"""
    from typing import get_args

    from llm_capacity_pricing.schemas.performance import MatchLevel, SlaStatus
    from llm_capacity_pricing.schemas.plan import DomainStatus

    sla_values = set(get_args(SlaStatus))
    match_values = set(get_args(MatchLevel))
    domain_values = set(get_args(DomainStatus))

    # 领域状态是新增的**编排层**概念，不与业务状态混用
    assert not (domain_values & sla_values)
    assert not (domain_values & match_values)
    assert "computed" in domain_values and "missing_data" in domain_values

    # 候选上的业务字段直接沿用既有 literal
    from llm_capacity_pricing.schemas.plan import PlanCandidate

    assert PlanCandidate.model_fields["sla_status"].annotation is not None
    src = (SRC / "schemas/plan.py").read_text(encoding="utf-8")
    assert "sla_status: str | None" in src or "sla_status" in src
    # 不得在 plan.py 里重新枚举 SLA 状态
    assert "observed_pass" not in src
    assert "exact_match" not in src


def test_s3_reason_codes_are_not_reinvented():
    """阻断原因码沿用领域内的既有命名，不在编排层另造一套。"""
    from llm_capacity_pricing.schemas.plan import PlanBlocker

    codes_used_by_workflow = set(
        re.findall(r'block\(\s*"[a-z_]+"\s*,\s*"([a-z_]+)"',
                   (SRC / "workflow.py").read_text(encoding="utf-8"))
    )
    # 编排层只负责"调用失败/输入缺失"这类结构性问题
    assert codes_used_by_workflow <= {
        "database_unavailable", "model_not_found", "sizing_failed", "ranking_failed",
        "cost_template_invalid", "cost_template_not_found", "cost_as_of_invalid",
        "nodes_required_missing", "tco_failed", "demand_scenario_not_found",
        "revenue_failed", "demand_file_invalid", "sla_required",
        "eligibility_failed", "prerequisite_missing",
    }
    assert PlanBlocker.model_fields["domain"] is not None


def test_s3_reports_do_not_import_domain_engines():
    """报告层只消费结构化结果，不直接调用领域引擎。"""
    forbidden = ("perf.benchmark_matcher", "perf.sla_evaluator", "engines.tco",
                 "engines.revenue", "engines.quote_gate")
    for name in ("plan_report.py", "tco_report.py", "performance_report.py"):
        src = (SRC / "reporting" / name).read_text(encoding="utf-8")
        tree = ast.parse(src)
        mods: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                mods.append(node.module)
        hit = [m for m in mods if any(f in m for f in forbidden)]
        assert hit == [], f"{name} 不应导入领域引擎：{hit}"
