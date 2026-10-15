"""任务 2 回归测试：金额 JSON 精度。

固定以下契约：

* 金额/比率以**可精确恢复的十进制字符串**导出（不用 JSON number，避免 double 截断）；
* 不使用科学计数法；
* 兼容读取同时接受字符串（当前标准）与 JSON number（早期产物），均以 Decimal 还原；
* 公式计算仍全程 Decimal，不经 float；
* 币种、成本范围、结果类型在结果与文件 meta 中显式声明。
"""

from __future__ import annotations

import json
from decimal import Decimal, getcontext

import pytest

from llm_capacity_pricing.engines.calculator import calculate, make_legacy_scenario, make_scenario
from llm_capacity_pricing.engines.pricing import (
    is_decimal_path,
    legacy_input_annual_revenue_10k_per_gpu,
)
from llm_capacity_pricing.reporting.json_export import (
    DECIMALS_ENCODING,
    DECIMAL_FIELD_NAMES,
    decimal_to_str,
    dumps_exact,
    export_envelope_meta,
    loads_tolerant,
    parse_decimal,
    read_json,
)
from llm_capacity_pricing.schemas.benchmark import BenchmarkRecord
from llm_capacity_pricing.schemas.costs import HardwarePrice, TokenPrice
from llm_capacity_pricing.schemas.pricing import PricingResult
from llm_capacity_pricing.services import calculate_all, export_legacy_results, open_db

#: 28 位有效数字的高精度样本（远超 double 的 ~15-17 位）
HIGH_PRECISION = Decimal("1234567890.12345678901234567890123456")
SAMPLE_MONEY_FIELDS = (
    "input_revenue_10k_per_gpu_year",
    "output_revenue_10k_per_gpu_year",
    "total_revenue_10k_per_gpu_year",
    "gpu_annual_cost_10k",
    "revenue_cost_ratio",
    "breakeven_monetization_ratio",
    "total_cluster_cost_10k_year",
    "total_cluster_revenue_10k_year",
)


# --------------------------------------------------------------------------
# 编码
# --------------------------------------------------------------------------


def test_money_is_encoded_as_string_not_number():
    text = dumps_exact({"v": Decimal("149.54340799296")})
    assert text == '{\n  "v": "149.54340799296"\n}'
    assert '"149.54340799296"' in text


def test_no_scientific_notation_for_small_and_large_values():
    for value in (Decimal("4.4E-13"), Decimal("1E-30"), Decimal("1E+20"), Decimal("0E-28")):
        s = decimal_to_str(value)
        assert "e" not in s.lower(), s
        assert Decimal(s) == value, (s, value)


def test_decimal_to_str_roundtrips_exactly_including_trailing_zeros():
    for raw in (
        "149.54340799296",
        "3.374478360246857142857142857",
        "0.00000000000044",
        "0",
        "-0",
        "1.50",
        "0.10000000000000000555111512312578270211815834045",
    ):
        v = Decimal(raw)
        assert Decimal(decimal_to_str(v)) == v


def test_high_precision_value_survives_string_encoding():
    text = dumps_exact({"v": HIGH_PRECISION})
    assert str(HIGH_PRECISION) in text
    back = loads_tolerant(text, restore_keys={"v"})["v"]
    assert isinstance(back, Decimal)
    assert back == HIGH_PRECISION
    assert str(back) == str(HIGH_PRECISION)


def test_float_would_lose_precision_but_string_does_not():
    """证明为什么必须用字符串：同一值经 float 往返会失真。"""
    as_float = float(HIGH_PRECISION)
    assert Decimal(str(as_float)) != HIGH_PRECISION, "前提：float 保不住 28 位"
    assert Decimal(decimal_to_str(HIGH_PRECISION)) == HIGH_PRECISION


# --------------------------------------------------------------------------
# 兼容读取
# --------------------------------------------------------------------------


def test_tolerant_reader_accepts_decimal_strings():
    payload = loads_tolerant('{"total_revenue_10k_per_gpu_year": "177.16011391296"}')
    assert payload["total_revenue_10k_per_gpu_year"] == Decimal("177.16011391296")


def test_tolerant_reader_accepts_legacy_json_number_without_float_loss():
    """早期产物把金额写成 JSON number；读取时用 parse_float=Decimal，不经 float。"""
    literal = "177.16011391296000000000000000001"
    payload = loads_tolerant('{"total_revenue_10k_per_gpu_year": %s}' % literal)
    got = payload["total_revenue_10k_per_gpu_year"]
    assert isinstance(got, Decimal)
    assert str(got) == literal
    # 若先转 float 就会丢掉末尾
    assert got != Decimal(str(float(literal)))


def test_tolerant_reader_leaves_non_money_strings_alone():
    payload = loads_tolerant(
        '{"currency": "UNCONFIRMED", "cost_scope": "gpu_cost_only", '
        '"scenario_id": "legacy_full_load::excel_部署效能_2", '
        '"input_snapshot_hash": "ab12"}'
    )
    assert payload["currency"] == "UNCONFIRMED"
    assert payload["cost_scope"] == "gpu_cost_only"
    assert isinstance(payload["scenario_id"], str)
    assert isinstance(payload["input_snapshot_hash"], str)


def test_tolerant_reader_handles_nested_lists_under_money_field_name():
    payload = loads_tolerant('{"total_revenue_10k_per_gpu_year": ["1.5", "2.25"]}')
    assert payload["total_revenue_10k_per_gpu_year"] == [Decimal("1.5"), Decimal("2.25")]


def test_version_like_strings_are_not_mistaken_for_money():
    """版本号字段（如 "1.0"）不得被误还原为 Decimal。"""
    payload = loads_tolerant(
        '{"framework_version": "1.0", "source_version": "0.6.3", '
        '"gpu_annual_cost_10k": "52.5", "note": "1.5"}'
    )
    assert payload["framework_version"] == "1.0"
    assert isinstance(payload["framework_version"], str)
    assert payload["source_version"] == "0.6.3"
    assert payload["gpu_annual_cost_10k"] == Decimal("52.5")
    assert isinstance(payload["note"], str)


def test_tolerant_reader_leaves_unknown_keys_alone():
    payload = loads_tolerant('{"unknown": {"x": "1.5"}}')
    assert payload["unknown"]["x"] == "1.5"


def test_parse_decimal_accepts_all_scalar_kinds_without_float_error():
    assert parse_decimal("0.1") == Decimal("0.1")
    assert parse_decimal(1) == Decimal("1")
    assert parse_decimal(Decimal("0.30")) == Decimal("0.30")
    # float 走 str()，得到最短可逆表示而非二进制展开
    assert parse_decimal(0.1) == Decimal("0.1")
    assert parse_decimal(0.1) != Decimal(0.1)
    with pytest.raises(ValueError):
        parse_decimal(True)
    with pytest.raises(ValueError):
        parse_decimal(object())


def test_decimal_field_names_cover_all_pricing_money_fields():
    for name in SAMPLE_MONEY_FIELDS:
        assert name in DECIMAL_FIELD_NAMES, name


# --------------------------------------------------------------------------
# 模型级高精度往返
# --------------------------------------------------------------------------


def test_pricing_result_high_precision_roundtrip_through_json(tmp_path):
    """构造 28 位精度金额 -> 导出 -> 读回 -> 精确相等。"""
    getcontext().prec = 50
    rec = BenchmarkRecord(
        record_id="hp",
        model_name="m",
        hardware_model="g",
        deployment_gpu_count=8,
        evidence_id="e",
        performance_source_kind="internal_excel",
        input_tps_per_gpu=Decimal("12478.922"),
        output_tps_per_gpu=Decimal("87.572"),
        cache_hit_rate_raw=Decimal("0.9"),
    )
    tp = TokenPrice(
        token_price_id="t",
        model_name="m",
        price_cached_input_per_1m=Decimal("2"),
        price_uncached_input_per_1m=Decimal("20"),
        price_output_per_1m=Decimal("100"),
        currency=None,
        currency_assumption="CNY; pending business confirmation",
        evidence_id="e",
    )
    hp = HardwarePrice(
        hardware_price_id="h",
        gpu_model="g",
        per_gpu_annual_cost_10k=HIGH_PRECISION,
        currency=None,
        currency_assumption="CNY; pending business confirmation",
        evidence_id="e",
    )
    res = calculate(rec, tp, hp, make_legacy_scenario("hp"))

    path = tmp_path / "hp.json"
    path.write_text(dumps_exact({"results": [res.model_dump(mode="python")]}), encoding="utf-8")

    back = read_json(path)["results"][0]
    assert back["gpu_annual_cost_10k"] == HIGH_PRECISION
    assert str(back["breakeven_monetization_ratio"]) == str(res.breakeven_monetization_ratio)
    assert back["revenue_cost_ratio"] == res.revenue_cost_ratio

    # 重新校验为模型也不失真
    restored = PricingResult.model_validate(back)
    assert restored.breakeven_monetization_ratio == res.breakeven_monetization_ratio
    assert restored.revenue_cost_ratio == res.revenue_cost_ratio
    assert restored.gpu_annual_cost_10k == HIGH_PRECISION
    getcontext().prec = 28


def test_exported_file_roundtrips_into_models(full_db, tmp_path):
    """真实导出文件逐条读回并重新校验：金额逐位一致。"""
    out = tmp_path / "legacy.json"
    export_legacy_results(full_db, out)
    payload = read_json(out)

    recomputed, _ = calculate_all(full_db, mode="legacy_full_load", persist=False)
    by_id = {r.benchmark_record_id: r for r in recomputed}

    assert payload["result_count"] == len(recomputed) == 32
    for item in payload["results"]:
        model = PricingResult.model_validate(item)
        ref = by_id[model.benchmark_record_id]
        for field in SAMPLE_MONEY_FIELDS:
            got, want = getattr(model, field), getattr(ref, field)
            assert got == want, (model.benchmark_record_id, field, got, want)
            assert str(got) == str(want), (model.benchmark_record_id, field)


def test_exported_json_has_no_float_literals_for_money(full_db, tmp_path):
    """金额字段在原始文本里必须是带引号的字符串，不能是裸 number。"""
    out = tmp_path / "legacy.json"
    export_legacy_results(full_db, out)
    raw = out.read_text(encoding="utf-8")
    for field in SAMPLE_MONEY_FIELDS:
        import re

        # 匹配 "field": 123  形态（裸数字），应当不存在
        assert not re.search(rf'"{field}":\s*-?[0-9]', raw), f"{field} 仍是裸数字"
        assert re.search(rf'"{field}":\s*"', raw), f"{field} 应为字符串"


def test_legacy_number_format_files_are_still_readable(tmp_path):
    """模拟早期产物（金额为裸 number），确认兼容读取不丢精度。"""
    legacy_text = json.dumps(
        {
            "export_kind": "legacy_full_load",
            "results": [
                {
                    "benchmark_record_id": "excel_部署效能_2",
                    "input_revenue_10k_per_gpu_year": 149.54340799296,
                    "total_revenue_10k_per_gpu_year": 177.16011391296,
                    "currency": "UNCONFIRMED",
                }
            ],
        }
    )
    payload = loads_tolerant(legacy_text)
    row = payload["results"][0]
    assert isinstance(row["input_revenue_10k_per_gpu_year"], Decimal)
    assert row["input_revenue_10k_per_gpu_year"] == Decimal("149.54340799296")
    assert row["total_revenue_10k_per_gpu_year"] == Decimal("177.16011391296")
    assert row["currency"] == "UNCONFIRMED"


# --------------------------------------------------------------------------
# 币种 / 成本范围 / 结果类型 / 计算路径
# --------------------------------------------------------------------------


def test_envelope_meta_states_currency_cost_scope_and_encoding():
    meta = export_envelope_meta()
    assert meta["currency"] == "UNCONFIRMED"
    assert meta["currency_assumption"] == "CNY; pending business confirmation"
    assert meta["cost_scope"] == "gpu_cost_only"
    assert meta["price_unit"] == "currency_unit_per_1M_tokens"
    assert meta["billing_period"] == "per_gpu_year"
    assert meta["decimals_encoding"]["money_and_ratio_fields"] == "decimal_string"
    assert meta["decimals_encoding"] == DECIMALS_ENCODING


def test_export_files_declare_currency_scope_and_result_type(full_db, tmp_path):
    legacy = tmp_path / "l.json"
    scenario = tmp_path / "s.json"
    export_legacy_results(full_db, legacy)
    from llm_capacity_pricing.services import export_scenario_results

    export_scenario_results(full_db, scenario)

    lp = read_json(legacy)
    sp = read_json(scenario)
    assert lp["meta"]["currency"] == "UNCONFIRMED"
    assert lp["meta"]["cost_scope"] == "gpu_cost_only"
    assert lp["result_type"] == "theoretical_full_load_potential"
    assert sp["result_type"] == "scenario_sensitivity_estimate"
    assert sp["meta"]["cost_scope"] == "gpu_cost_only"
    for r in lp["results"]:
        assert r["cost_scope"] == "gpu_cost_only"
        assert r["currency"] == "UNCONFIRMED"
        assert r["result_type"] == "theoretical_full_load_potential"


def test_formulas_stay_on_decimal_path():
    """公式入参与出参必须是 Decimal；任何 float 输入都应被拒绝。"""
    assert is_decimal_path(Decimal("1"), Decimal("2"), Decimal("3"))
    assert not is_decimal_path(1.0, Decimal("2"))
    assert not is_decimal_path(Decimal("1"), 2)

    with pytest.raises(TypeError):
        legacy_input_annual_revenue_10k_per_gpu(
            Decimal("100"), Decimal("0.5"), Decimal("2"), 20.0
        )


def test_binary_float_error_never_enters_money():
    """经典 0.1+0.2 误差不得出现在金额路径：Decimal 结果精确。"""
    assert Decimal("0.1") + Decimal("0.2") == Decimal("0.3")
    assert DECIMAL_FIELD_NAMES.isdisjoint({"scenario_id", "mode", "status"})


def test_scenario_math_uses_decimal_exact_multiple():
    res = calculate(
        BenchmarkRecord(
            record_id="r",
            model_name="m",
            hardware_model="g",
            deployment_gpu_count=1,
            evidence_id="e",
            performance_source_kind="internal_excel",
            input_tps_per_gpu=Decimal("1"),
            output_tps_per_gpu=Decimal("1"),
            cache_hit_rate_raw=Decimal("0"),
        ),
        TokenPrice(
            token_price_id="t",
            model_name="m",
            price_cached_input_per_1m=Decimal("1"),
            price_uncached_input_per_1m=Decimal("1"),
            price_output_per_1m=Decimal("1"),
            currency=None,
            currency_assumption="a",
            evidence_id="e",
        ),
        HardwarePrice(
            hardware_price_id="h",
            gpu_model="g",
            per_gpu_annual_cost_10k=Decimal("1"),
            currency=None,
            currency_assumption="a",
            evidence_id="e",
        ),
        make_scenario("r", Decimal("0.1")),
    )
    # 0.1 精确乘法：满额 × 0.1 应恰好是满额的 1/10，无二进制误差
    assert res.scenario_revenue_10k_per_gpu_year * 10 == res.total_revenue_10k_per_gpu_year
