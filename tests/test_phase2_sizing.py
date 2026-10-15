"""第二阶段（M12–M18）测试：模型解析、硬件规格、权重/KV 显存、TP/DP 规划。

覆盖任务书验收点：

* M13 结构解析与官方配置一致；不支持结构返回 ``unsupported_architecture``；哈希不符拒绝；
* M14 SKU 分离、GB/GiB、标称≠可分配、未确认条件不得默认满足；
* M15 权重总量可复算且与官方索引一致；共享权重只算一次；TP 切分/复制/未确认三态；
* M16 KV 公式有独立算例；TP 头数切分与复制；Paged 块对齐；未知运行时不为 0；
* M17 ``gpu_count = tp × dp``；四类状态；稳定排序；不声明性能优劣；
* M18 CLI 离线可用、JSON/Markdown 含来源与假设、错误输入明确失败；
* 第一阶段定价功能不受影响（另由既有 325 个用例保证）。
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest
from typer.testing import CliRunner

from llm_capacity_pricing.cli import app
from llm_capacity_pricing.schemas.sizing import (
    HardwareSpec,
    ModelSpec,
    ModelWeightMetadata,
    RuntimeReserve,
)
from llm_capacity_pricing.services import (
    estimate_memory_for,
    import_hardware,
    import_models,
    open_db,
    plan_deployment_for,
    sizing_inventory,
)
from llm_capacity_pricing.sizing.hardware import (
    check_hardware_compatibility,
    load_hardware,
    load_catalog,
)
from llm_capacity_pricing.sizing.kv_cache import (
    default_runtime_items,
    estimate_kv_cache,
    kv_bytes_per_token_per_layer,
)
from llm_capacity_pricing.sizing.model_spec import (
    ModelSpecError,
    SUPPORTED_MODEL_TYPES,
    build_model_spec,
    load_model_specs,
)
from llm_capacity_pricing.sizing.planner import (
    PlanningAssumptions,
    plan_deployment,
    resolve_usable_memory,
)
from llm_capacity_pricing.sizing.weights import (
    component_element_counts,
    estimate_weight_memory,
)

MANIFEST = "data/models/model_manifest.yaml"
CATALOG = "data/hardware/hardware_catalog.yaml"
runner = CliRunner()

#: 官方 config.json 里的关键参数（已在 data/models/ 归档，逐字段核对）
OFFICIAL = {
    "qwen3-8b": dict(layers=36, hidden=4096, heads=32, kv=8, head_dim=128, ffn=12288),
    "qwen3-32b": dict(layers=64, hidden=5120, heads=64, kv=8, head_dim=128, ffn=25600),
}


@pytest.fixture(scope="module")
def models(root: Path):
    specs, _, _ = load_model_specs(root / MANIFEST, root=root)
    return {s.model_id: s for s in specs}


@pytest.fixture(scope="module")
def hardware(root: Path):
    specs, servers, _ = load_hardware(root / CATALOG)
    return {h.hardware_id: h for h in specs}, {s.server_id: s for s in servers}


def tiny_model(**overrides) -> ModelSpec:
    """人工可复算的小模型（用于手算校验权重与 KV）。

    L=2, h=64, heads=4, kv_heads=2, head_dim=16, ffn=128, vocab=1000, tie=false
    -> 参数量 202,112；BF16 = 404,224 字节（见测试中的手算过程）。
    """
    base = dict(
        model_id="tiny-dense",
        num_hidden_layers=2,
        hidden_size=64,
        num_attention_heads=4,
        num_key_value_heads=2,
        head_dim=16,
        intermediate_size=128,
        vocab_size=1000,
        tie_word_embeddings=False,
        attention_bias=False,
        deployment_precision="bf16",
        status="parsed",
    )
    base.update(overrides)
    return ModelSpec(**base)


# --------------------------------------------------------------------------
# M12/M13：模型解析
# --------------------------------------------------------------------------


def test_two_models_parsed_with_official_values(models):
    assert set(models) == {"qwen3-8b", "qwen3-32b"}
    for model_id, expected in OFFICIAL.items():
        m = models[model_id]
        assert m.status == "parsed", m.missing_fields
        assert m.attention_kind == "gqa"
        assert m.num_hidden_layers == expected["layers"]
        assert m.hidden_size == expected["hidden"]
        assert m.num_attention_heads == expected["heads"]
        assert m.num_key_value_heads == expected["kv"]
        assert m.head_dim == expected["head_dim"]
        assert m.intermediate_size == expected["ffn"]
        assert m.vocab_size == 151936
        assert m.max_position_embeddings == 40960
        assert m.weight_dtype_declared == "bfloat16"
        assert m.tie_word_embeddings is False
        assert m.sliding_window is None and m.use_sliding_window is False


def test_model_provenance_recorded(models):
    for m in models.values():
        assert m.repo_id.startswith("Qwen/Qwen3-")
        assert m.revision and len(m.revision) == 40  # commit SHA
        assert m.config_sha256 and len(m.config_sha256) == 64
        assert m.trust_level == "official_repo_archived"
        assert m.weight_metadata.total_weight_bytes
        assert m.weight_metadata.shard_count and m.weight_metadata.tensor_count


def test_weight_dtype_vs_deployment_precision(models):
    """权重文件声明的 dtype 与本次部署请求的精度是两件事。"""
    m = models["qwen3-32b"]
    assert m.weight_dtype_declared == "bfloat16"
    assert m.deployment_precision == "bf16"
    fp8 = ModelSpec.model_validate({**m.model_dump(), "deployment_precision": "fp8"})
    assert fp8.deployment_precision == "fp8" and fp8.weight_dtype_declared == "bfloat16"


def test_unsupported_architecture_is_explicit(tmp_path: Path, root: Path):
    """未核实的 model_type 必须返回 unsupported_architecture，且不给结构字段。"""
    import shutil

    work = tmp_path / "proj"
    (work / "data" / "models" / "fake").mkdir(parents=True)
    (work / "data" / "models" / "fake" / "config.json").write_text(
        json.dumps({"model_type": "llama", "architectures": ["LlamaForCausalLM"],
                    "num_hidden_layers": 32, "hidden_size": 4096}), encoding="utf-8")
    entry = {
        "model_id": "fake",
        "repo_id": "x/y",
        "revision": "0" * 40,
        "config": {"file": "data/models/fake/config.json", "sha256": None},
    }
    spec = build_model_spec(entry, root=work, verify_hashes=False)
    assert spec.status == "unsupported_architecture"
    assert spec.num_hidden_layers is None and spec.hidden_size is None
    assert "unsupported_architecture" in spec.quality_flags
    assert spec.supports_memory_estimate is False
    assert "llama" not in SUPPORTED_MODEL_TYPES


def test_config_hash_mismatch_is_rejected(tmp_path: Path, root: Path):
    """归档文件被改动 -> Fail-Closed 拒绝（与第一阶段证据校验同一原则）。"""
    import shutil

    work = tmp_path / "proj"
    shutil.copytree(root / "data", work / "data")
    target = work / "data" / "models" / "qwen3-8b" / "config.json"
    target.write_text(target.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(ModelSpecError, match="sha256 不一致"):
        load_model_specs(work / MANIFEST, root=work)


def test_unsupported_architecture_cannot_carry_structure():
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="unsupported_architecture"):
        ModelSpec(
            model_id="x",
            status="unsupported_architecture",
            num_hidden_layers=32,
        )


def test_model_import_is_idempotent(tmp_path: Path, root: Path):
    db = str(tmp_path / "p2.db")
    first = import_models(root / MANIFEST, db, root=root)
    second = import_models(root / MANIFEST, db, root=root)
    assert first["model_count"] == second["model_count"] == 2
    handle, repo = open_db(db)
    try:
        with handle.session() as session:
            rows = repo.list_model_specs(session)
    finally:
        handle.dispose()
    assert len(rows) == 2 and {r.model_id for r in rows} == {"qwen3-8b", "qwen3-32b"}


def test_cli_import_models_offline(tmp_path: Path):
    db = str(tmp_path / "cli.db")
    r = runner.invoke(app, ["import-models", "--db", db, "--root", "."])
    assert r.exit_code == 0, r.output
    assert "已导入 2 个模型结构" in r.output


# --------------------------------------------------------------------------
# M14：硬件规格
# --------------------------------------------------------------------------


def test_h100_sxm_and_nvl_are_different_skus(hardware):
    specs, _ = hardware
    sxm = specs["nvidia-h100-sxm-80gb"]
    nvl = specs["nvidia-h100-nvl-94gb"]
    assert sxm.sku == "H100 SXM" and nvl.sku == "H100 NVL"
    assert sxm.total_memory_bytes == 80 * 10**9
    assert nvl.total_memory_bytes == 94 * 10**9
    assert sxm.memory_bandwidth_bytes_per_s == 3_350_000_000_000
    assert nvl.memory_bandwidth_bytes_per_s == 3_900_000_000_000
    assert sxm.form_factor == "SXM" and nvl.form_factor == "PCIe"
    assert sxm.total_memory_bytes != nvl.total_memory_bytes


def test_h200_sxm_spec(hardware):
    specs, _ = hardware
    h200 = specs["nvidia-h200-sxm-141gb"]
    assert h200.hbm_type == "HBM3e"
    assert h200.total_memory_bytes == 141 * 10**9
    assert h200.memory_bandwidth_bytes_per_s == 4_800_000_000_000
    assert h200.verification_status == "vendor_page_verified"


def test_nominal_capacity_is_not_allocatable(hardware):
    """标称容量不得被当作可分配显存。"""
    specs, _ = hardware
    for h in specs.values():
        assert h.usable_memory_bytes is None
        assert h.usable_memory_status == "pending_measurement"
        assert h.allocatable_bytes() is None


def test_pending_hardware_has_no_fabricated_numbers(hardware):
    specs, _ = hardware
    for hid in ("ascend-910c", "nvidia-b200-sxm"):
        h = specs[hid]
        assert h.supported_in_phase2 is False
        assert h.verification_status == "pending_verification"
        assert h.total_memory_bytes is None
        assert h.memory_bandwidth_bytes_per_s is None
        assert h.fields_pending_verification


def test_unverified_conditions_are_not_assumed_satisfied(hardware):
    specs, servers = hardware
    h100 = specs["nvidia-h100-sxm-80gb"]
    compat = check_hardware_compatibility(
        spec=h100, server=servers["hgx-h100-8gpu"], gpu_count=8, precision="bf16"
    )
    assert compat.status == "unknown"
    assert "gpu_usable_memory_not_measured" in compat.unverified_conditions
    assert "intra_node_interconnect_unverified" not in compat.unverified_conditions  # 页面已声明 NVLink


def test_unsupported_hardware_is_incompatible(hardware):
    specs, _ = hardware
    compat = check_hardware_compatibility(
        spec=specs["ascend-910c"], server=None, gpu_count=8, precision="bf16"
    )
    assert compat.status == "incompatible"
    assert any("hardware_not_supported_in_phase2" in r for r in compat.reasons)


def test_multi_node_requirement_is_flagged(hardware):
    specs, servers = hardware
    compat = check_hardware_compatibility(
        spec=specs["nvidia-h100-sxm-80gb"],
        server=servers["hgx-h100-8gpu"],
        gpu_count=16,
        precision="bf16",
        tp_size=16,
    )
    assert "tp_group_spans_nodes_interconnect_not_modelled" in compat.unverified_conditions


def test_hardware_catalog_rejects_inconsistent_capacity(tmp_path: Path, root: Path):
    import yaml

    from llm_capacity_pricing.sizing.hardware import HardwareCatalogError

    doc = yaml.safe_load((root / CATALOG).read_text(encoding="utf-8"))
    doc["hardware"][0]["total_memory_bytes"] = 123  # 与 "80GB"/decimal_GB 矛盾
    bad = tmp_path / "bad.yaml"
    bad.write_text(yaml.safe_dump(doc, allow_unicode=True), encoding="utf-8")
    with pytest.raises(HardwareCatalogError, match="不一致"):
        load_hardware(bad)


# --------------------------------------------------------------------------
# M15：权重显存
# --------------------------------------------------------------------------


def test_structural_formula_matches_official_weight_totals(models):
    """结构公式总量与官方权重索引 metadata.total_size 必须完全一致。"""
    for model_id, m in models.items():
        est = estimate_weight_memory(m, tp_size=1)
        assert est.consistency_delta_bytes == 0, model_id
        assert est.total_weight_bytes == m.weight_metadata.total_weight_bytes
        assert "完全一致" in est.consistency_note
        assert "formula_matches_official_weight_total" in est.quality_flags


def test_tiny_model_weight_bytes_hand_calculated():
    """手算校验（与引擎相互独立地算一遍）。"""
    m = tiny_model()
    counts = component_element_counts(
        __import__("llm_capacity_pricing.sizing.weights", fromlist=["_params_from_spec"])._params_from_spec(m)
    )
    # embedding 1000×64；lm_head 同；final_norm 64
    assert counts["embedding"] == 64000
    assert counts["lm_head"] == 64000
    assert counts["final_norm"] == 64
    # 每层：q 64×64，k/v 64×32 各一，o 64×64，norm 2×64，mlp 3×64×128
    # 注意：该 tiny 模型**没有权重索引**，因此公式不假定存在 q_norm/k_norm
    per_layer = 4096 + 2048 + 2048 + 4096 + 128 + 3 * (64 * 128)
    assert per_layer == 36_992
    assert sum(counts.values()) == per_layer * 2 + 64000 + 64000 + 64 == 202_048
    est = estimate_weight_memory(m, tp_size=1)
    assert est.total_weight_bytes == 202_048 * 2 == 404_096
    assert est.breakdown.embedding_bytes == 64000 * 2
    assert est.breakdown.lm_head_bytes == 64000 * 2
    assert est.breakdown.ffn_bytes == 3 * (64 * 128) * 2 * 2


def test_tied_embeddings_counted_once():
    tied = tiny_model(tie_word_embeddings=True)
    untied = tiny_model(tie_word_embeddings=False)
    a = estimate_weight_memory(tied, tp_size=1).total_weight_bytes
    b = estimate_weight_memory(untied, tp_size=1).total_weight_bytes
    assert b - a == 64000 * 2  # lm_head 不再重复计入


def test_tp_sharding_and_replication_declared(models):
    est = estimate_weight_memory(models["qwen3-32b"], tp_size=8)
    assert "ffn_gate" in est.sharded_components
    assert "attention_q" in est.sharded_components
    assert "final_norm" in est.replicated_components
    assert "layer_norm_in" in est.replicated_components
    # 词汇表维度未确认 -> 同时给出上下界
    assert set(est.uncertain_components) == {"embedding", "lm_head"}
    assert est.per_rank_bytes_lower_bound < est.per_rank_bytes_upper_bound
    assert est.per_rank_bytes == est.per_rank_bytes_upper_bound


def test_per_rank_precision_is_not_claimed_exact(models):
    """没有张量布局证据时不得声称精确每卡权重显存。"""
    est = estimate_weight_memory(models["qwen3-32b"], tp_size=4)
    assert est.per_rank_precision == "upper_bound_estimate"
    assert "per_rank_is_structural_estimate_not_tensor_layout" in est.quality_flags


def test_weight_memory_scales_with_tp(models):
    m = models["qwen3-32b"]
    ranks = [estimate_weight_memory(m, tp_size=tp).per_rank_bytes_upper_bound for tp in (1, 2, 4, 8)]
    assert ranks == sorted(ranks, reverse=True)
    # TP1 每卡就是全量
    assert ranks[0] == m.weight_metadata.total_weight_bytes


def test_unsupported_precision_is_rejected(models):
    """V2.1 F6：fp8/int8/int4/unknown 一律拒绝，不给"按 1 字节硬算"的假精度。"""
    for precision in ("unknown", "fp8", "int8", "int4"):
        est = estimate_weight_memory(models["qwen3-8b"], tp_size=1, precision=precision)
        assert est.per_rank_precision == "unavailable", precision
        assert est.total_weight_bytes is None, precision
        assert "unsupported_weight_precision" in est.quality_flags, precision
        assert any("not_supported_in_phase2" in f for f in est.missing_fields), precision


# --------------------------------------------------------------------------
# M16：KV Cache 与运行时
# --------------------------------------------------------------------------


def test_kv_bytes_per_token_per_layer():
    assert kv_bytes_per_token_per_layer(num_kv_heads=1, head_dim=128, kv_precision="bf16") == 512
    assert kv_bytes_per_token_per_layer(num_kv_heads=8, head_dim=128, kv_precision="bf16") == 4096
    # V2.1 F6：fp8 需要量化标定，不在本阶段可计算范围内
    assert kv_bytes_per_token_per_layer(num_kv_heads=8, head_dim=128, kv_precision="fp8") is None
    assert kv_bytes_per_token_per_layer(num_kv_heads=8, head_dim=128, kv_precision="unknown") is None


def test_kv_hand_calculated_case(models):
    """Qwen3-32B / TP8 / BF16 KV / 8 并发 / 40960 ctx：8 × 40960 × 64 × 512 = 10 GiB。"""
    est = estimate_kv_cache(
        models["qwen3-32b"],
        tp_size=8,
        kv_precision="bf16",
        global_concurrency=8,
        context_length_per_request=40960,
        block_size_tokens=16,
    )
    assert est.num_kv_heads_per_rank == 1
    assert est.required_resident_tokens == 327_680
    assert est.allocated_tokens == 327_680
    assert est.estimated_capacity_tokens is None  # 未给 KV 预算时不给"容量"结论
    assert est.physical_bytes == 327_680 * 64 * 512 == 10_737_418_240
    assert est.alignment_overhead_bytes == 0  # 40960 能被 16 整除


def test_paged_block_alignment_overhead(models):
    est = estimate_kv_cache(
        models["qwen3-32b"],
        tp_size=8,
        kv_precision="bf16",
        global_concurrency=1,
        context_length_per_request=40961,
        block_size_tokens=16,
    )
    assert est.allocated_blocks == 2561  # ceil(40961/16)
    assert est.allocated_tokens == 2561 * 16
    assert est.alignment_overhead_bytes == 15 * 64 * 512  # 多出的 15 个 token 槽位


def test_dp_splits_concurrency_not_weights(models):
    """DP 分摊并发，不切分单条请求的权重。"""
    kv_dp4 = estimate_kv_cache(
        models["qwen3-32b"], tp_size=8, dp_size=4, kv_precision="bf16",
        global_concurrency=32, context_length_per_request=32768, block_size_tokens=16,
    )
    kv_dp1 = estimate_kv_cache(
        models["qwen3-32b"], tp_size=8, dp_size=1, kv_precision="bf16",
        global_concurrency=32, context_length_per_request=32768, block_size_tokens=16,
    )
    assert kv_dp4.concurrency_per_replica == 8
    assert kv_dp4.physical_bytes == kv_dp1.physical_bytes // 4
    assert any("DP 副本" in a for a in kv_dp4.assumptions)


def test_kv_heads_not_divisible_by_tp_is_marked(models):
    est = estimate_kv_cache(
        models["qwen3-8b"], tp_size=16, kv_precision="bf16",
        global_concurrency=16, context_length_per_request=4096,
    )
    assert est.kv_head_distribution == "unknown"
    assert "kv_heads_fewer_than_tp_requires_replication" in est.quality_flags
    assert est.kv_head_note and "复制" in est.kv_head_note


def test_gqa_mqa_mha_use_same_formula():
    mha = tiny_model(model_id="mha", num_key_value_heads=4)
    gqa = tiny_model(model_id="gqa", num_key_value_heads=2)
    mqa = tiny_model(model_id="mqa", num_key_value_heads=1)
    assert mha.attention_kind == "mha"
    assert gqa.attention_kind == "gqa"
    assert mqa.attention_kind == "mqa"
    sizes = [
        estimate_kv_cache(m, tp_size=1, kv_precision="bf16",
                          global_concurrency=1, context_length_per_request=16).physical_bytes
        for m in (mha, gqa, mqa)
    ]
    assert sizes == [4 * sizes[2], 2 * sizes[2], sizes[2]]


def test_sliding_window_degrades_explicitly():
    sw = tiny_model(model_id="sw", use_sliding_window=True, sliding_window=4096)
    est = estimate_kv_cache(sw, tp_size=1, kv_precision="bf16",
                            global_concurrency=1, context_length_per_request=8192)
    assert est.attention_kind == "sliding_window"
    assert est.readable is False
    assert est.physical_bytes is None
    assert any("不套用" in n for n in est.degradation_notes)


def test_kv_estimate_reports_missing_inputs(models):
    est = estimate_kv_cache(models["qwen3-8b"], tp_size=1, kv_precision="bf16")
    assert est.readable is False
    assert "global_concurrency" in est.missing_fields
    assert "context_length_per_request" in est.missing_fields


def test_unknown_runtime_is_not_zero():
    items = default_runtime_items()
    assert items and all(i.origin == "unknown" and i.bytes_estimate is None for i in items)
    with pytest.raises(Exception, match="unknown"):
        RuntimeReserve(item="x", bytes_estimate=123, origin="unknown")


def test_assumed_runtime_requires_source():
    with pytest.raises(Exception, match="assumption"):
        RuntimeReserve(item="x", bytes_estimate=123, origin="assumed")
    ok = RuntimeReserve(item="x", bytes_estimate=123, origin="assumed", source="vLLM 文档 0.6.x")
    assert ok.bytes_estimate == 123


def test_provided_runtime_items_used(models):
    items = default_runtime_items(
        provided={"framework_workspace": {"bytes": 2 * 2**30, "source": "现场实测"}}
    )
    by_item = {i.item: i for i in items}
    assert by_item["framework_workspace"].bytes_estimate == 2 * 2**30
    assert by_item["framework_workspace"].origin == "assumed"
    assert by_item["cuda_graph_capture"].origin == "unknown"


# --------------------------------------------------------------------------
# M17：TP/DP 规划
# --------------------------------------------------------------------------


def _plan(models, hardware, *, kv_precision: str = "bf16", model_id: str = "qwen3-32b", **assumption_kwargs):
    specs, servers = hardware
    return plan_deployment(
        models[model_id],
        specs["nvidia-h100-sxm-80gb"],
        server=servers["hgx-h100-8gpu"],
        assumptions=PlanningAssumptions(**assumption_kwargs),
        kv_precision=kv_precision,
    )


def test_gpu_count_is_tp_times_dp(models, hardware):
    candidates = _plan(
        models, hardware,
        per_gpu_usable_bytes=76 * 10**9, usable_memory_source="test", usable_memory_source_kind="synthetic_example",
        runtime_reserve_fraction=Decimal("0.1"), runtime_reserve_source="test",
        global_concurrency=32, context_length_per_request=8192,
        tp_sizes=(1, 2, 4), dp_sizes=(1, 2),
    )
    assert candidates
    for c in candidates:
        assert c.gpu_count == c.tp_size * c.dp_size


def test_no_assumptions_gives_insufficient_information(models, hardware):
    candidates = _plan(
        models, hardware, global_concurrency=32, context_length_per_request=8192
    )
    assert all(c.feasibility.status == "insufficient_information" for c in candidates)
    assert all(c.feasibility.reasons for c in candidates)


def test_explicit_assumptions_yield_conditional_feasible(models, hardware):
    candidates = _plan(
        models, hardware,
        # 示例假设：标称 80 GB 的 90%（= 72 GB）。明确标为 synthetic_example，
        # 不得当成实测值（V2.1 F2）
        usable_fraction_of_nominal=Decimal("0.9"),
        usable_memory_source="演示用构造值（非实测）",
        usable_memory_source_kind="synthetic_example",
        runtime_reserve_fraction=Decimal("0.1"), runtime_reserve_source="框架经验（示例）",
        global_concurrency=32, context_length_per_request=8192, block_size_tokens=16,
        tp_sizes=(1, 2, 4, 8), dp_sizes=(1,),
    )
    by_tp = {c.tp_size: c for c in candidates}
    # 72 GB 可用显存（= 80 GB × 0.9，示例值）下的实际结果：
    #   TP1：61 GiB 权重 + 64 GiB KV，远超预算
    #   TP2：31.96 GiB 权重 + 32 GiB KV ≈ 68.7 GB，再加 10% 运行时预留即超出
    #   TP4/TP8：留有余量
    usable = int(80 * 10**9 * Decimal("0.9"))
    assert by_tp[1].feasibility.status == "infeasible"
    assert by_tp[2].feasibility.status == "infeasible"
    assert by_tp[4].feasibility.status == "conditional_feasible"
    assert by_tp[8].feasibility.status == "conditional_feasible"
    assert by_tp[4].feasibility.assumptions  # 条件可行必须列假设
    assert by_tp[4].memory.headroom_bytes == usable - by_tp[4].memory.total_required_bytes


def test_infeasible_reports_overage(models, hardware):
    candidates = _plan(
        models, hardware, per_gpu_usable_bytes=76 * 10**9, usable_memory_source="test", usable_memory_source_kind="synthetic_example",
        runtime_reserve_fraction=Decimal("0.1"), runtime_reserve_source="test",
        global_concurrency=32, context_length_per_request=8192,
        tp_sizes=(1,), dp_sizes=(1,),
    )
    assert candidates[0].feasibility.status == "infeasible"
    assert any("超出" in r for r in candidates[0].feasibility.reasons)


def test_tp_divisibility_checked(models, hardware):
    """注意力头数必须能被 TP 整除（Qwen3-8B 32 头 -> TP4 可以，TP3 不整除）。"""
    specs, servers = hardware
    candidates = plan_deployment(
        models["qwen3-8b"],
        specs["nvidia-h100-sxm-80gb"],
        server=servers["hgx-h100-8gpu"],
        assumptions=PlanningAssumptions(
            per_gpu_usable_bytes=76 * 10**9, usable_memory_source="test",
            usable_memory_source_kind="synthetic_example",
            runtime_reserve_fraction=Decimal("0.1"), runtime_reserve_source="test",
            global_concurrency=8, context_length_per_request=8192,
            tp_sizes=(3,), dp_sizes=(1,),
        ),
    )
    assert candidates[0].feasibility.status == "infeasible"
    assert any("不能被 TP" in r for r in candidates[0].feasibility.reasons)


def test_kv_heads_fewer_than_tp_risk_flagged(models, hardware):
    specs, servers = hardware
    candidates = plan_deployment(
        models["qwen3-32b"],
        specs["nvidia-h200-sxm-141gb"],
        server=None,
        assumptions=PlanningAssumptions(
            per_gpu_usable_bytes=132 * 10**9, usable_memory_source="test", usable_memory_source_kind="synthetic_example",
            runtime_reserve_fraction=Decimal("0.1"), runtime_reserve_source="test",
            global_concurrency=8, context_length_per_request=8192,
            tp_sizes=(16,), dp_sizes=(1,),
        ),
    )
    flags = candidates[0].feasibility.risk_flags
    assert "kv_heads_fewer_than_tp_requires_kv_replication" in flags
    assert "server_profile_unspecified_node_topology_unverified" in flags


def test_qk_norm_counted_when_tensor_names_present():
    """权重命名含 q_norm/k_norm 时公式必须计入（Qwen3 特有）。"""
    without = tiny_model(model_id="no-qk-norm")
    with_names = tiny_model(
        model_id="with-qk-norm",
        weight_metadata=ModelWeightMetadata(
            weight_map={
                "model.layers.0.self_attn.q_norm.weight": "a.safetensors",
                "model.layers.0.self_attn.k_norm.weight": "a.safetensors",
                "model.layers.0.mlp.gate_proj.weight": "a.safetensors",
                "model.layers.0.mlp.up_proj.weight": "a.safetensors",
                "model.layers.0.mlp.down_proj.weight": "a.safetensors",
            },
            tensor_count=5,
        ),
    )
    a = estimate_weight_memory(without, tp_size=1).total_weight_bytes
    b = estimate_weight_memory(with_names, tp_size=1).total_weight_bytes
    assert b - a == 2 * 2 * 16 * 2  # 2 层 × 2 个 norm × head_dim 16 × 2 字节


def test_unsupported_model_returns_unsupported(tmp_path: Path, root: Path):
    bad = ModelSpec(model_id="unsupported-x", status="unsupported_architecture")
    specs, _servers, _summary = load_hardware(root / CATALOG)
    candidates = plan_deployment(
        bad, specs[0] if isinstance(specs, dict) else specs[0] if False else next(
            h for h in specs if h.hardware_id == "nvidia-h100-sxm-80gb"
        ),
        assumptions=PlanningAssumptions(global_concurrency=1, context_length_per_request=1024),
    )
    assert all(c.feasibility.status == "unsupported" for c in candidates)
    assert all(c.memory is None for c in candidates)


def test_candidates_are_deterministically_sorted(models, hardware):
    kwargs = dict(
        per_gpu_usable_bytes=76 * 10**9, usable_memory_source="test", usable_memory_source_kind="synthetic_example",
        runtime_reserve_fraction=Decimal("0.1"), runtime_reserve_source="test",
        global_concurrency=32, context_length_per_request=8192, block_size_tokens=16,
        tp_sizes=(1, 2, 4, 8), dp_sizes=(1, 2),
    )
    a = [c.candidate_id for c in _plan(models, hardware, **kwargs)]
    b = [c.candidate_id for c in _plan(models, hardware, **kwargs)]
    assert a == b
    statuses = [c.feasibility.status for c in _plan(models, hardware, **kwargs)]
    order = {"conditional_feasible": 0, "insufficient_information": 1, "infeasible": 2, "unsupported": 3}
    assert statuses == sorted(statuses, key=lambda s: order[s])


def test_usable_memory_resolution_prefers_measured(models, hardware):
    specs, _ = hardware
    measured = specs["nvidia-h100-sxm-80gb"].model_copy(
        update={"usable_memory_bytes": 70 * 10**9, "usable_memory_status": "measured"}
    )
    value, source, assumed = resolve_usable_memory(
        measured,
        PlanningAssumptions(per_gpu_usable_bytes=1, usable_memory_source_kind="measured",
                            usable_memory_source="测试夹具"),
    )
    assert value == 70 * 10**9 and "实测值" in source and assumed == []


def test_usable_memory_fraction_is_marked_assumed(hardware):
    specs, _ = hardware
    value, source, assumed = resolve_usable_memory(
        specs["nvidia-h200-sxm-141gb"],
        PlanningAssumptions(
            usable_fraction_of_nominal=Decimal("0.93"),
            usable_memory_source="示例假设",
            usable_memory_source_kind="synthetic_example",
        ),
    )
    assert value == int(141 * 10**9 * Decimal("0.93"))
    assert assumed and "非实测" in assumed[0]


# --------------------------------------------------------------------------
# M18：CLI / 报告 / 错误输入
# --------------------------------------------------------------------------


def test_cli_inventory_and_plan(tmp_path: Path, root: Path):
    db = str(tmp_path / "cli2.db")
    assert runner.invoke(app, ["init-db", "--db", db]).exit_code == 0
    assert runner.invoke(app, ["import-models", "--db", db, "--root", "."]).exit_code == 0
    assert runner.invoke(app, ["import-hardware", "--db", db]).exit_code == 0
    inv = runner.invoke(app, ["sizing-inventory", "--db", db])
    assert inv.exit_code == 0 and '"model_count": 2' in inv.output

    plan = runner.invoke(
        app,
        [
            "plan-deployment", "--model", "qwen3-32b", "--hardware", "nvidia-h100-sxm-80gb",
            "--context-length", "8192", "--concurrency", "32", "--block-size", "16",
            "--usable-memory-bytes", str(70 * 10**9), "--usable-source", "test",
            "--usable-source-kind", "synthetic_example",
            "--runtime-reserve-fraction", "0.1", "--runtime-source", "test",
            "--tp-sizes", "1,2,4", "--dp-sizes", "1", "--db", db,
        ],
    )
    assert plan.exit_code == 0, plan.output
    assert "conditional_feasible" in plan.output or "infeasible" in plan.output


def test_cli_export_json_and_report(tmp_path: Path, root: Path):
    db = str(tmp_path / "cli3.db")
    runner.invoke(app, ["init-db", "--db", db])
    runner.invoke(app, ["import-models", "--db", db, "--root", "."])
    runner.invoke(app, ["import-hardware", "--db", db])
    out = tmp_path / "plan.json"
    report = tmp_path / "plan.md"
    r = runner.invoke(
        app,
        [
            "plan-deployment", "--model", "qwen3-8b", "--hardware", "nvidia-h200-sxm-141gb",
            "--context-length", "8192", "--concurrency", "16", "--block-size", "16",
            "--usable-memory-bytes", str(120 * 10**9), "--usable-source", "test",
            "--usable-source-kind", "synthetic_example",
            "--runtime-reserve-fraction", "0.1", "--runtime-source", "test",
            "--out", str(out), "--report", str(report), "--db", db,
        ],
    )
    assert r.exit_code == 0, r.output
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["export_kind"] == "deployment_plan"
    assert payload["formula_versions"] == {"weights": "weights-1.0", "kv": "kv-1.0", "memory": "memory-1.0"}
    assert any("不代表" in n for n in payload["boundary_notices"])
    text = report.read_text(encoding="utf-8")
    for expected in (
        "不代表**服务能成功启动",
        "不代表**满足 SLA",
        "公式版本",
        "待核实字段",
        "明确未做的事",
    ):
        assert expected in text, expected


def test_cli_missing_model_fails_clearly(tmp_path: Path):
    db = str(tmp_path / "cli4.db")
    runner.invoke(app, ["init-db", "--db", db])
    r = runner.invoke(
        app,
        ["estimate-memory", "--model", "nope", "--hardware", "nvidia-h100-sxm-80gb",
         "--context-length", "1024", "--concurrency", "1", "--db", db],
    )
    assert r.exit_code != 0
    assert "模型不存在" in r.output


def test_service_estimate_memory_fields(tmp_path: Path, root: Path):
    db = str(tmp_path / "svc.db")
    runner.invoke(app, ["init-db", "--db", db])
    import_models(root / MANIFEST, db, root=root)
    import_hardware(root / CATALOG, db)
    info = estimate_memory_for(
        db,
        model_id="qwen3-32b",
        hardware_id="nvidia-h100-sxm-80gb",
        tp_size=8,
        dp_size=1,
        assumptions=PlanningAssumptions(
            per_gpu_usable_bytes=76 * 10**9, usable_memory_source="test",
            usable_memory_source_kind="synthetic_example",
            runtime_reserve_fraction=Decimal("0.1"), runtime_reserve_source="test",
            global_concurrency=32, context_length_per_request=8192, block_size_tokens=16,
        ),
    )
    assert info["weight"]["consistency_delta_bytes"] == 0
    assert info["kv"]["kv_heads_per_rank"] == 1
    assert info["kv"]["allocated_blocks"] > 0
    assert info["runtime_known_bytes"] > 0
    assert info["known_unknowns"]  # 未知运行时项被列出，而不是当 0
    assert info["headroom_bytes"] == 76 * 10**9 - info["total_required_bytes"]


# ==========================================================================
# V2.1 整改专项（按验收意见逐项）
# ==========================================================================


def _plan_assumed(models, hardware, **kw):
    """带断言可用显存的规划（示例值，明确标为 synthetic_example）。"""
    defaults = dict(
        usable_fraction_of_nominal=Decimal("0.9"),
        usable_memory_source="演示用构造值（非实测）",
        usable_memory_source_kind="synthetic_example",
        runtime_reserve_fraction=Decimal("0.1"),
        runtime_reserve_source="演示用构造值",
        global_concurrency=32,
        context_length_per_request=8192,
        block_size_tokens=16,
    )
    defaults.update(kw)
    return _plan(models, hardware, **defaults)


def test_v21_f1_topology_checked_per_candidate(models, hardware):
    """V2.1 F1/P0：拓扑必须按候选实际卡数判定，TP1×DP1 不得继承 64 卡的风险。"""
    candidates = _plan_assumed(
        models, hardware, tp_sizes=(1, 2, 8), dp_sizes=(1, 2, 8)
    )
    by_key = {(c.tp_size, c.dp_size): c for c in candidates}
    node_flags = ("tp_group_spans_nodes_interconnect_not_modelled",
                  "dp_replicas_span_multiple_nodes_placement_unverified",
                  "total_gpus_exceed_single_node_capacity")

    single = by_key[(1, 1)]
    assert single.gpu_count == 1
    assert not [u for u in single.feasibility.unverified_conditions if u in node_flags]

    on_node = by_key[(8, 1)]          # 8 卡正好一台机器
    assert not [u for u in on_node.feasibility.unverified_conditions if u in node_flags]

    multi = by_key[(8, 8)]            # 64 卡 -> 必然跨节点
    assert "dp_replicas_span_multiple_nodes_placement_unverified" in multi.feasibility.unverified_conditions
    assert "total_gpus_exceed_single_node_capacity" in multi.feasibility.unverified_conditions

    tp_over_node = by_key[(8, 2)]
    assert tp_over_node.gpu_count == 16
    assert "total_gpus_exceed_single_node_capacity" in tp_over_node.feasibility.unverified_conditions


def test_v21_f1_topology_risk_is_not_shared(models, hardware):
    """同一批次内，不同候选的节点风险必须互不相同（防止再次复用同一份兼容性结果）。"""
    candidates = _plan_assumed(models, hardware, tp_sizes=(1, 8), dp_sizes=(1, 8))
    node_risk = {
        (c.tp_size, c.dp_size): tuple(
            u for u in c.feasibility.unverified_conditions
            if "node" in u or "exceed" in u
        )
        for c in candidates
    }
    assert len(set(node_risk.values())) > 1, node_risk
    assert node_risk[(1, 1)] == ()


def test_v21_f2_server_sku_mismatch_is_infeasible(models, hardware):
    """V2.1 F2/P0：服务器面向的 SKU 与候选不一致 -> 直接不可行，不静默复用拓扑。"""
    specs, servers = hardware
    candidates = plan_deployment(
        models["qwen3-8b"],
        specs["nvidia-h100-sxm-80gb"],
        server=servers["hgx-h200-8gpu"],   # H200 的服务器配 H100 的卡
        assumptions=PlanningAssumptions(
            usable_fraction_of_nominal=Decimal("0.9"),
            usable_memory_source_kind="synthetic_example",
            usable_memory_source="演示值",
            runtime_reserve_fraction=Decimal("0.1"),
            runtime_reserve_source="演示值",
            global_concurrency=8,
            context_length_per_request=8192,
            tp_sizes=(1,),
            dp_sizes=(1,),
        ),
    )
    assert candidates[0].feasibility.status == "infeasible"
    assert any("server_gpu_sku_mismatch" in r for r in candidates[0].feasibility.reasons)


@pytest.mark.parametrize(
    "value,source_kind,source,expected",
    [
        (81 * 10**9, "synthetic_example", "x", "超过"),
        (70 * 10**9, None, "x", "必须声明来源性质"),
        (70 * 10**9, "measured", None, "测量来源"),
        (70 * 10**9, "made_up_kind", "x", "必须声明来源性质"),
        (0, "synthetic_example", "x", "必须为正数"),
    ],
)
def test_v21_f2_usable_memory_validation(hardware, value, source_kind, source, expected):
    specs, _ = hardware
    from llm_capacity_pricing.sizing.hardware import UsableMemoryError, validate_usable_memory

    with pytest.raises(UsableMemoryError, match=expected):
        validate_usable_memory(
            specs["nvidia-h100-sxm-80gb"], value, source_kind=source_kind, source=source
        )


def test_v21_f2_example_values_are_labelled_not_measured(models, hardware):
    """示例假设必须在结果里标成 synthetic_example，不能像实测数据。"""
    candidates = _plan_assumed(models, hardware, tp_sizes=(4,), dp_sizes=(1,))
    c = candidates[0]
    assert any("synthetic_example" in a for a in c.memory.assumptions)
    assert all("synthetic_example" != s for s in [])  # 占位，真实断言在下一行
    assert "非硬件实测字段" in c.memory.assumptions[0] or any(
        "非实测" in a for a in c.memory.assumptions
    )


def test_v21_f3_context_beyond_model_limit_blocks(models, hardware):
    """V2.1 F3/P1：超过模型声明的上下文上限且无验证扩展 -> 阻断。"""
    candidates = _plan_assumed(models, hardware, context_length_per_request=65536, tp_sizes=(8,), dp_sizes=(1,))
    assert candidates[0].feasibility.status == "infeasible"
    assert any("context_length_exceeds_model_limit" in r for r in candidates[0].feasibility.reasons)


def test_v21_f3_verified_extension_allows_long_context(models, hardware):
    """有经过验证的上下文扩展配置时，按新上限计算。"""
    candidates = _plan_assumed(
        models,
        hardware,
        context_length_per_request=65536,
        global_concurrency=4,
        max_context_override=131072,
        max_context_override_source="已核实的 RoPE 扩展配置（示例）",
        tp_sizes=(8,),
        dp_sizes=(1,),
    )
    assert candidates[0].feasibility.status == "conditional_feasible"
    assert not any("exceeds_model_limit" in r for r in candidates[0].feasibility.reasons)


def test_v21_f4_kv_required_vs_capacity_separated(models):
    """V2.1 F4/P1：KV 需求、预算容量、对齐分配三者独立可查。"""
    est = estimate_kv_cache(
        models["qwen3-32b"],
        tp_size=8,
        kv_precision="bf16",
        global_concurrency=8,
        context_length_per_request=40960,
        block_size_tokens=16,
        kv_budget_bytes=20 * 2**30,
    )
    assert est.required_resident_tokens == 327_680          # 需求：8 × 40960
    assert est.allocated_tokens == 327_680                  # 对齐后分配
    assert est.estimated_capacity_tokens == 655_360         # 预算 20 GiB 可容纳
    assert est.estimated_capacity_tokens > est.required_resident_tokens


def test_v21_f4_no_budget_means_no_capacity_claim(models):
    est = estimate_kv_cache(
        models["qwen3-32b"],
        tp_size=8,
        kv_precision="bf16",
        global_concurrency=8,
        context_length_per_request=40960,
        block_size_tokens=16,
    )
    assert est.required_resident_tokens == 327_680
    assert est.estimated_capacity_tokens is None


def test_v21_f5_no_stale_reasons_after_status_change(models, hardware):
    """V2.1 F5/P1：结论变成条件可行后，不得保留"未给出任何假设"这类过时原因。"""
    candidates = _plan_assumed(models, hardware, tp_sizes=(4, 8), dp_sizes=(1,))
    for c in candidates:
        if c.feasibility.status == "conditional_feasible":
            assert c.feasibility.reasons == [], (c.candidate_id, c.feasibility.reasons)
            assert c.feasibility.assumptions, c.candidate_id
        if c.feasibility.status == "infeasible":
            assert c.feasibility.reasons, c.candidate_id
            assert all("未给出任何假设" not in r for r in c.feasibility.reasons)


def test_v21_f5_status_and_reasons_are_consistent(models, hardware):
    for c in _plan_assumed(models, hardware, tp_sizes=(1, 2, 4, 8), dp_sizes=(1, 2)):
        f = c.feasibility
        if f.status == "conditional_feasible":
            assert not f.reasons
        elif f.status == "infeasible":
            assert f.reasons
        elif f.status == "insufficient_information":
            assert f.reasons
        assert not (f.status == "unsupported" and c.memory is not None)


@pytest.mark.parametrize("precision", ["fp8", "int8", "int4", "unknown"])
def test_v21_f6_unsupported_precision_yields_unsupported(models, hardware, precision):
    """V2.1 F6/P1：未验证的量化精度不得生成"看似精确"的规划结果。"""
    candidates = _plan_assumed(
        models, hardware, tp_sizes=(1,), dp_sizes=(1,), kv_precision=precision
    )
    assert candidates[0].feasibility.status == "unsupported"
    assert candidates[0].memory is None
    assert any("not_supported_in_phase2" in r for r in candidates[0].feasibility.reasons)


@pytest.mark.parametrize(
    "kwargs,expected",
    [
        (dict(usable_fraction_of_nominal=Decimal("1.5")), "可用显存比例"),
        (dict(runtime_reserve_fraction=Decimal("1.0")), "运行时预留比例"),
        (dict(runtime_reserve_fraction=Decimal("-0.1")), "运行时预留比例"),
        (dict(global_concurrency=0), "全局并发"),
        (dict(context_length_per_request=0), "上下文长度"),
        (dict(block_size_tokens=0), "block size"),
        (dict(tp_sizes=()), "tp_sizes"),
        (dict(max_context_override=65536), "来源"),
    ],
)
def test_v21_f6_assumption_ranges_validated(models, hardware, kwargs, expected):
    from llm_capacity_pricing.sizing.planner import AssumptionError

    specs, servers = hardware
    base = dict(
        usable_memory_source_kind="synthetic_example",
        usable_memory_source="演示值",
        global_concurrency=8,
        context_length_per_request=8192,
    )
    base.update(kwargs)
    with pytest.raises(AssumptionError, match=expected):
        plan_deployment(
            models["qwen3-8b"],
            specs["nvidia-h100-sxm-80gb"],
            server=servers["hgx-h100-8gpu"],
            assumptions=PlanningAssumptions(**base),
        )


# --------------------------------------------------------------------------
# 正式验收场景 A / B / C
# --------------------------------------------------------------------------


def test_scenario_A_qwen3_32b_h100_tp_sweep(models, hardware):
    """场景 A：Qwen3-32B + H100 SXM + BF16，同一组自洽假设下比较 TP 扫描。"""
    candidates = _plan_assumed(
        models, hardware, tp_sizes=(1, 2, 4, 8), dp_sizes=(1,)
    )
    by_tp = {c.tp_size: c for c in candidates}
    # 不错误触发跨节点检查（单机 8 卡内）
    for c in candidates:
        assert "tp_group_spans_nodes_interconnect_not_modelled" not in c.feasibility.unverified_conditions
        assert "total_gpus_exceed_single_node_capacity" not in c.feasibility.unverified_conditions
    # 显存随 TP 单调下降
    lower = [by_tp[tp].memory.weight.per_rank_bytes_lower_bound for tp in (1, 2, 4, 8)]
    assert lower == sorted(lower, reverse=True)
    # 假设标注明确，没有被描述成实测
    for c in candidates:
        assert any("synthetic_example" in a for a in c.memory.assumptions)
    assert by_tp[1].feasibility.status == "infeasible"   # 61 GiB 权重 + 64 GiB KV 远超 72 GB
    assert by_tp[2].feasibility.status == "infeasible"   # 合计 75.9 GB，略超预算
    for tp in (4, 8):
        assert by_tp[tp].feasibility.status == "conditional_feasible"


def test_scenario_B_qwen3_8b_h200_dp_scaling(models, hardware):
    """场景 B：Qwen3-8B + H200 SXM + BF16，检查 DP 增加时按每副本并发计算。"""
    specs, servers = hardware
    candidates = plan_deployment(
        models["qwen3-8b"],
        specs["nvidia-h200-sxm-141gb"],
        server=None,   # 未指定服务器 -> 拓扑未确认
        assumptions=PlanningAssumptions(
            usable_fraction_of_nominal=Decimal("0.9"),
            usable_memory_source="演示用构造值",
            usable_memory_source_kind="synthetic_example",
            runtime_reserve_fraction=Decimal("0.1"),
            runtime_reserve_source="演示用构造值",
            global_concurrency=64,
            context_length_per_request=16384,
            block_size_tokens=16,
            tp_sizes=(1, 2),
            dp_sizes=(1, 2, 4),
        ),
    )
    by_key = {(c.tp_size, c.dp_size): c for c in candidates}
    # 每副本并发 = 全局并发 / DP
    for (tp, dp), c in by_key.items():
        assert c.concurrency_per_replica == -(-64 // dp)
    # DP 增大 -> 每副本 KV 下降；权重复制不变（DP 不切分权重）
    kv_dp1 = by_key[(2, 1)].memory.kv.physical_bytes
    kv_dp4 = by_key[(2, 4)].memory.kv.physical_bytes
    assert kv_dp4 == kv_dp1 // 4
    assert by_key[(2, 1)].memory.weight.per_rank_bytes == by_key[(2, 4)].memory.weight.per_rank_bytes
    # 未指定服务器 -> 拓扑风险被标记
    assert any(
        "server_profile_not_specified" in u for u in by_key[(2, 1)].feasibility.unverified_conditions
    )


def test_scenario_C_invalid_inputs_all_rejected(models, hardware, root: Path):
    """场景 C：故意输入错误数据，四种情况都必须失败或不可评估。"""
    from llm_capacity_pricing.sizing.hardware import UsableMemoryError, validate_usable_memory
    from llm_capacity_pricing.sizing.planner import AssumptionError

    specs, servers = hardware

    # ① GPU / 服务器型号不匹配
    c = plan_deployment(
        models["qwen3-8b"], specs["nvidia-h100-sxm-80gb"], server=servers["hgx-h200-8gpu"],
        assumptions=PlanningAssumptions(
            usable_fraction_of_nominal=Decimal("0.9"),
            usable_memory_source_kind="synthetic_example",
            usable_memory_source="演示值",
            runtime_reserve_fraction=Decimal("0.1"), runtime_reserve_source="演示值",
            global_concurrency=8, context_length_per_request=8192,
            tp_sizes=(1,), dp_sizes=(1,),
        ),
    )[0]
    assert c.feasibility.status == "infeasible"

    # ② 可用显存超过标称
    with pytest.raises(UsableMemoryError):
        validate_usable_memory(
            specs["nvidia-h100-sxm-80gb"], 90 * 10**9, source_kind="synthetic_example", source="x"
        )

    # ③ 上下文超过模型上限
    c = _plan_assumed(models, hardware, context_length_per_request=131072, tp_sizes=(8,), dp_sizes=(1,))[0]
    assert c.feasibility.status == "infeasible"

    # ④ 负数运行时预留 / 未支持精度
    with pytest.raises(AssumptionError):
        _plan_assumed(models, hardware, runtime_reserve_fraction=Decimal("-0.01"))
    c = _plan_assumed(models, hardware, kv_precision="int8", tp_sizes=(1,), dp_sizes=(1,))[0]
    assert c.feasibility.status == "unsupported"
