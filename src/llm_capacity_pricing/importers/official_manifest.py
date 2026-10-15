"""官方来源清单加载器：把 manifest 中的 ``partial_evidence`` 条目落成候选记录。

要点：

* **只处理 ``partial_evidence``**。``pending_verification`` 与
  ``not_a_result_dataset`` 不入库（前者尚无证据文件，后者根本不是结果数据）。
* **校验证据文件哈希**：manifest 记录的 ``evidence_sha256`` 必须与本地存档文件一致，
  否则报错而不是继续。
* **指标只从证据文件机器解析**（``parse_console_summary: true`` 时），不采用人工抄录值；
  没有可解析格式的页面不写 ``metrics``，只在缺口台账里登记"数值存在于文档但未解析"。
* **缺口台账由模型状态推导**，保证 ``missing_fields`` 与实际空值严格一致；
  manifest 里声明的字段清单只作为人工文档保留。
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml

from llm_capacity_pricing.importers.console_summary import (
    extract_summary_blocks,
    html_to_text,
)
from llm_capacity_pricing.importers.excel_importer import HISTORICAL_EXTERNAL_FLAG
from llm_capacity_pricing.schemas.evidence import Evidence
from llm_capacity_pricing.schemas.official import (
    REQUIRED_CONFIG_FIELDS,
    REQUIRED_METRIC_FIELDS,
    OfficialBenchmarkCandidate,
)

#: 会被登记进缺口台账的指标字段
METRIC_LEDGER_FIELDS = ("ttft_p99_ms", "tpot_p99_ms", "itl_p99_ms", "output_throughput_tok_s")

#: 候选记录一律携带的质量标记
CANDIDATE_BASE_FLAGS = [
    "official_source_page_archived",
    "partial_evidence_candidate_not_in_performance_table",
]


class OfficialManifestError(Exception):
    """manifest 结构非法或证据文件不一致。"""


def load_manifest(path: str | Path) -> list[dict[str, Any]]:
    p = Path(path)
    if not p.exists():
        raise OfficialManifestError(f"找不到 manifest 文件: {p}")
    data = yaml.safe_load(p.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise OfficialManifestError(f"manifest 必须是 YAML 列表: {p}")
    for i, entry in enumerate(data):
        if not isinstance(entry, dict) or "id" not in entry or "status" not in entry:
            raise OfficialManifestError(f"manifest 第 {i} 条缺少 id/status: {entry!r}")
    return data


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _parse_dt(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def build_candidate(
    entry: dict[str, Any], *, root: str | Path
) -> tuple[OfficialBenchmarkCandidate, Evidence]:
    """把一条 manifest 条目转成（候选记录, 证据）。"""
    if entry.get("status") != "partial_evidence":
        raise OfficialManifestError(
            f"只有 status=partial_evidence 的条目才会入库，收到 {entry.get('status')!r} "
            f"(id={entry.get('id')})"
        )
    evidence_file = entry.get("evidence_file")
    if not evidence_file:
        raise OfficialManifestError(f"id={entry['id']} 缺少 evidence_file，无法作为 partial_evidence 入库")
    path = Path(root) / evidence_file
    if not path.exists():
        raise OfficialManifestError(f"id={entry['id']} 的证据文件缺失: {path}")
    actual_sha = _sha256(path)
    declared = entry.get("evidence_sha256")
    if declared and declared != actual_sha:
        raise OfficialManifestError(
            f"id={entry['id']} 证据文件 sha256 不一致：manifest={declared} 实际={actual_sha}"
        )

    metrics: dict[str, Decimal] = {}
    raw_labels: dict[str, str] = {}
    flags = list(CANDIDATE_BASE_FLAGS)
    notes = entry.get("notes")
    if entry.get("parse_console_summary"):
        blocks = extract_summary_blocks(html_to_text(path.read_text(encoding="utf-8", errors="replace")))
        if not blocks:
            raise OfficialManifestError(
                f"id={entry['id']} 声明 parse_console_summary 但未在证据文件中找到可解析的汇总块"
            )
        block = blocks[0]
        metrics = dict(block["metrics"])
        raw_labels = dict(block["raw_labels"])
        flags.append("metrics_machine_parsed_from_evidence_file")
        if len(blocks) > 1:
            flags.append(f"evidence_contains_{len(blocks)}_summary_blocks_used_first")

    success = metrics.get("success_count")
    if success is not None:
        is_throughput: bool | None = success > 1
    else:
        is_throughput = None  # 未机器解析出指标 -> 无法判定，不猜
    if success is not None and success == 1:
        flags.append("single_request_smoke_test")
    if not metrics:
        flags.append("metrics_not_machine_parsed_present_in_document_only")

    accessed = _parse_dt(entry.get("accessed_at"))
    publisher = entry.get("publisher") or "unknown"

    evidence = Evidence(
        evidence_id=f"ev_official_{entry['id']}",
        source_type="official_benchmark",
        measurement_origin="official_benchmark",
        is_internal_measurement=False,
        publisher=publisher,
        source_url=entry.get("source_url"),
        source_file=evidence_file,
        source_version=entry.get("framework_version"),
        retrieved_at=accessed,
        raw_sha256=actual_sha,
        notes=notes,
        quality_flags=flags + [f"manifest_status:{entry['status']}"],
    )

    # 缺口台账由实际取值推导，保证与空值一致
    missing_config = [
        f for f in REQUIRED_CONFIG_FIELDS if entry.get(f) in (None, "")
    ]
    missing_metrics = [f for f in REQUIRED_METRIC_FIELDS if f not in metrics]
    missing = missing_config + missing_metrics
    present = sorted(
        k
        for k, v in entry.items()
        if k in OfficialBenchmarkCandidate.model_fields
        and v not in (None, "", [], {})
        and k not in missing
    )
    present += sorted(f"metrics.{k}" for k in metrics)

    candidate = OfficialBenchmarkCandidate(
        candidate_id=f"official_{entry['id']}",
        manifest_id=entry["id"],
        evidence_id=evidence.evidence_id,
        publisher=publisher,
        source_url=entry.get("source_url"),
        source_version=entry.get("framework_version"),
        evidence_file=evidence_file,
        evidence_sha256=actual_sha,
        accessed_at=accessed,
        framework=entry.get("framework"),
        benchmark_tool=entry.get("benchmark_tool"),
        verification_status="partial_evidence",
        model_name=entry.get("model_name"),
        model_revision=entry.get("model_revision"),
        model_precision=entry.get("model_precision"),
        framework_version=entry.get("framework_version"),
        hardware_candidates=list(entry.get("hardware_candidates") or []),
        hardware_binding=entry.get("hardware_binding", "unbound"),
        hardware_model=entry.get("hardware_model"),
        deployment_gpu_count=entry.get("deployment_gpu_count"),
        deployment_strategy_raw=entry.get("deployment_strategy_raw"),
        dataset_name=entry.get("dataset_name"),
        input_length=entry.get("input_length"),
        output_length=entry.get("output_length"),
        configured_concurrency=entry.get("configured_concurrency"),
        request_rate_rps=(
            Decimal(str(entry["request_rate_rps"]))
            if entry.get("request_rate_rps") is not None
            else None
        ),
        metrics=metrics,
        raw_labels=raw_labels,
        is_throughput_benchmark=is_throughput,
        present_fields=present,
        missing_fields=missing,
        notes=notes,
        quality_flags=flags,
    )
    return candidate, evidence


def build_all_candidates(
    manifest_path: str | Path, *, root: str | Path
) -> tuple[list[OfficialBenchmarkCandidate], list[Evidence], list[dict[str, Any]]]:
    """返回 ``(候选记录, 证据, 未入库条目摘要)``。"""
    entries = load_manifest(manifest_path)
    candidates: list[OfficialBenchmarkCandidate] = []
    evidences: list[Evidence] = []
    skipped: list[dict[str, Any]] = []
    for entry in entries:
        if entry.get("status") != "partial_evidence":
            skipped.append(
                {
                    "id": entry["id"],
                    "status": entry["status"],
                    "reason": (
                        "尚无证据文件，等待补齐"
                        if entry["status"] == "pending_verification"
                        else "非结果数据集（仅工具/格式说明）"
                    ),
                }
            )
            continue
        candidate, evidence = build_candidate(entry, root=root)
        candidates.append(candidate)
        evidences.append(evidence)
    return candidates, evidences, skipped
