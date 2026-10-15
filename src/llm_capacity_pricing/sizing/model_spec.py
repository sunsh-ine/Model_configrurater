"""M13：模型结构解析与模型数据库。

数据来源纪律（与第一阶段一脉相承）：

1. **先归档再解析**：``config.json`` 与 ``model.safetensors.index.json`` 存在仓库内
   （``data/models/<model_id>/``），解析器只读本地文件 → 离线可复现；
2. **哈希校验 Fail-Closed**：归档文件 sha256 与清单不一致就抛错，不解析、不入库；
3. **不支持的结构显式拒绝**：``model_type`` 不在已核实清单内时返回
   ``unsupported_architecture``，**不**按标准 Dense Transformer 硬算（任务书 M13 要求）；
4. **权重 dtype ≠ 部署精度**：config 里的 ``torch_dtype`` 是权重文件声明的类型，
   本次部署用哪种精度由调用方显式指定，两者分开记录。
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from llm_capacity_pricing.schemas.evidence import Evidence
from llm_capacity_pricing.schemas.sizing import (
    ModelSpec,
    ModelWeightMetadata,
    Precision,
)

#: 已**核实过结构语义**的 model_type。不在表内的结构一律 unsupported，
#: 避免"看起来像 Dense Transformer"就套用同一套权重/KV 公式。
SUPPORTED_MODEL_TYPES: dict[str, dict[str, str]] = {
    "qwen3": {
        "family": "dense_decoder_gqa",
        "verified_with": "Qwen3-8B / Qwen3-32B 官方 config.json（本项目已归档并逐字段核对）",
        "notes": "dense decoder-only + GQA；use_sliding_window=false（本轮两个规格均如此）",
    },
}

#: 结构字段 -> config.json 的键名（Qwen3 已核实；其它结构未核实故不在此表）
CONFIG_FIELD_MAP: dict[str, str] = {
    "num_hidden_layers": "num_hidden_layers",
    "hidden_size": "hidden_size",
    "num_attention_heads": "num_attention_heads",
    "num_key_value_heads": "num_key_value_heads",
    "head_dim": "head_dim",
    "intermediate_size": "intermediate_size",
    "vocab_size": "vocab_size",
    "max_position_embeddings": "max_position_embeddings",
    "attention_bias": "attention_bias",
    "hidden_act": "hidden_act",
    "rms_norm_eps": "rms_norm_eps",
    "rope_theta": "rope_theta",
    "rope_scaling": "rope_scaling",
    "sliding_window": "sliding_window",
    "use_sliding_window": "use_sliding_window",
    "max_window_layers": "max_window_layers",
    "tie_word_embeddings": "tie_word_embeddings",
    "transformers_version": "transformers_version",
    "weight_dtype_declared": "torch_dtype",
}

#: 计算权重/KV 所必需的结构字段
REQUIRED_STRUCTURE_FIELDS = (
    "num_hidden_layers",
    "hidden_size",
    "num_attention_heads",
    "num_key_value_heads",
    "head_dim",
    "intermediate_size",
    "vocab_size",
)


class ModelSpecError(RuntimeError):
    """模型清单或归档文件不满足解析前提。"""


def sha256_file(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_model_manifest(manifest_path: str | Path) -> list[dict[str, Any]]:
    p = Path(manifest_path)
    if not p.exists():
        raise ModelSpecError(f"模型清单不存在: {p}")
    doc = yaml.safe_load(p.read_text(encoding="utf-8"))
    models = (doc or {}).get("models")
    if not models:
        raise ModelSpecError("模型清单缺少 models 列表")
    ids = [m.get("model_id") for m in models]
    if len(ids) != len(set(ids)):
        raise ModelSpecError("模型清单中 model_id 不唯一")
    return models


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def parse_weight_index(index: dict[str, Any], *, path: Path) -> ModelWeightMetadata:
    """解析 ``model.safetensors.index.json``：总量、分片与完整 weight_map。"""
    meta = index.get("metadata") or {}
    wmap = index.get("weight_map") or {}
    shards = sorted(set(wmap.values()))
    return ModelWeightMetadata(
        index_file=str(path),
        index_sha256=sha256_file(path),
        total_weight_bytes=meta.get("total_size"),
        shard_count=len(shards) or None,
        tensor_count=len(wmap) or None,
        shard_files=shards,
        weight_map=wmap,
        tensor_metadata_available=False,
        notes=[
            "权重文件本身未下载；总量来自索引文件的 metadata.total_size",
            "未取得张量级 shape/dtype，因此每卡权重显存只能给上界估算",
        ],
    )


def build_model_spec(
    entry: dict[str, Any],
    *,
    root: str | Path,
    deployment_precision: Precision = "bf16",
    verify_hashes: bool = True,
) -> ModelSpec:
    """把清单条目 + 归档文件解析成 :class:`ModelSpec`。"""
    base = Path(root)
    model_id = entry["model_id"]
    config_rel = (entry.get("config") or {}).get("file")
    if not config_rel:
        raise ModelSpecError(f"{model_id}: 清单缺少 config.file")
    config_path = base / config_rel
    if not config_path.exists():
        raise ModelSpecError(f"{model_id}: 归档的 config.json 不存在: {config_rel}")
    if verify_hashes:
        expected = (entry.get("config") or {}).get("sha256")
        actual = sha256_file(config_path)
        if expected and expected != actual:
            raise ModelSpecError(
                f"{model_id}: config.json sha256 不一致（清单 {expected}，实际 {actual}）——"
                "按 Fail-Closed 原则拒绝解析"
            )

    config = _read_json(config_path)
    model_type = config.get("model_type")
    supported = model_type in SUPPORTED_MODEL_TYPES

    if not supported:
        return ModelSpec(
            model_id=model_id,
            repo_id=entry.get("repo_id"),
            revision=entry.get("revision"),
            config_file=config_rel,
            config_sha256=sha256_file(config_path),
            retrieved_at=_parse_dt(entry.get("collected_at")),
            trust_level="official_repo_archived",
            architectures=list(config.get("architectures") or []),
            model_type=model_type,
            deployment_precision=deployment_precision,
            status="unsupported_architecture",
            missing_fields=list(REQUIRED_STRUCTURE_FIELDS),
            quality_flags=[
                "unsupported_architecture",
                f"model_type_not_verified:{model_type}",
                "not_computed_as_dense_transformer",
            ],
        )

    fields: dict[str, Any] = {}
    for field_name, config_key in CONFIG_FIELD_MAP.items():
        value = config.get(config_key)
        if field_name == "rms_norm_eps" and value is not None:
            value = _dec(value)
        elif field_name == "rope_theta" and value is not None:
            value = _dec(value)
        fields[field_name] = value

    missing = [f for f in REQUIRED_STRUCTURE_FIELDS if fields.get(f) is None]

    weight_meta = ModelWeightMetadata()
    index_rel = (entry.get("weight_index") or {}).get("file")
    if index_rel:
        index_path = base / index_rel
        if not index_path.exists():
            missing.append("weight_index_file")
        else:
            if verify_hashes:
                expected_idx = (entry.get("weight_index") or {}).get("sha256")
                actual_idx = sha256_file(index_path)
                if expected_idx and expected_idx != actual_idx:
                    raise ModelSpecError(
                        f"{model_id}: 权重索引 sha256 不一致（清单 {expected_idx}，"
                        f"实际 {actual_idx}）——按 Fail-Closed 原则拒绝解析"
                    )
            weight_meta = parse_weight_index(_read_json(index_path), path=index_path)

    flags: list[str] = ["official_model_repo_archived", f"model_type_verified:{model_type}"]
    if fields.get("use_sliding_window"):
        flags.append("sliding_window_enabled_kv_formula_degrades")
    if missing:
        flags.append("partially_parsed_missing_structure_fields")
    status = "parsed" if not missing else "partially_parsed"

    spec = ModelSpec(
        model_id=model_id,
        repo_id=entry.get("repo_id"),
        revision=entry.get("revision"),
        config_file=config_rel,
        config_sha256=sha256_file(config_path),
        retrieved_at=_parse_dt(entry.get("collected_at")),
        trust_level="official_repo_archived",
        architectures=list(config.get("architectures") or []),
        model_type=model_type,
        deployment_precision=deployment_precision,
        weight_metadata=weight_meta,
        status=status,
        missing_fields=missing,
        quality_flags=flags,
        **fields,
    )
    return spec


def _dec(value: Any):
    from decimal import Decimal

    try:
        return Decimal(str(value))
    except Exception:  # pragma: no cover - 非法输入
        return None


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:  # pragma: no cover
        return None


def build_evidence(entry: dict[str, Any], spec: ModelSpec) -> Evidence:
    """为模型归档建一条证据记录（复用第一阶段 Evidence 模型）。"""
    return Evidence(
        evidence_id=f"ev_model_{entry['model_id']}",
        # 模型仓库是外部发布方（相对本项目）；用质量标记说明它是**厂商官方**配置
        source_type="third_party",
        measurement_origin="historical_external_third_party",
        is_internal_measurement=False,
        publisher=(entry.get("repo_id") or "").split("/")[0] or "unknown",
        source_url=(entry.get("config") or {}).get("url"),
        source_file=(entry.get("config") or {}).get("file"),
        source_version=f"revision:{entry.get('revision')}" if entry.get("revision") else None,
        retrieved_at=spec.retrieved_at,
        raw_sha256=spec.config_sha256,
        notes=entry.get("notes"),
        quality_flags=[
            "model_vendor_official_config",
            "source_snapshot_archived",
            "specification_not_measurement",
        ],
    )


def load_model_specs(
    manifest_path: str | Path,
    *,
    root: str | Path,
    deployment_precision: Precision = "bf16",
) -> tuple[list[ModelSpec], list[Evidence], dict[str, Any]]:
    """解析清单中的全部模型，返回 ``(specs, evidences, summary)``。"""
    base = Path(root)
    entries = load_model_manifest(manifest_path)
    specs: list[ModelSpec] = []
    evidences: list[Evidence] = []
    per_model: dict[str, Any] = {}
    for entry in entries:
        spec = build_model_spec(
            entry, root=base, deployment_precision=deployment_precision, verify_hashes=True
        )
        evidences.append(build_evidence(entry, spec))
        specs.append(spec)
        per_model[spec.model_id] = {
            "status": spec.status,
            "attention_kind": spec.attention_kind,
            "layers": spec.num_hidden_layers,
            "kv_heads": spec.num_key_value_heads,
            "head_dim": spec.head_dim,
            "weight_total_bytes": spec.weight_metadata.total_weight_bytes,
            "missing_fields": spec.missing_fields,
        }
    summary = {
        "manifest": str(manifest_path),
        "model_count": len(specs),
        "parsed_count": sum(1 for s in specs if s.status == "parsed"),
        "unsupported_count": sum(1 for s in specs if s.status == "unsupported_architecture"),
        "deployment_precision": deployment_precision,
        "models": per_model,
    }
    return specs, evidences, summary
