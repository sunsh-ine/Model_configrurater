#!/usr/bin/env python
"""把**已核对的官方公开 Benchmark** 转成 BenchmarkRun 并入库（Demo 用，D2）。

这是 D2 允许的"最小格式转换脚本"，不是通用适配框架：

* 数据来源是官方项目公开发布的压测结果（SGLang Cookbook），
  已在 ``data/incoming/`` 里按 ``source_section`` 逐字段整理；
* **来源快照必须通过 sha256 校验**（复用既有的 ``build_numeric_candidates``），
  校验失败即中止，不产出任何记录；
* 缺失字段一律留空（``model_revision`` / ``weight_precision`` 等），
  **不补造、不外推**；
* 可信等级固定为 ``measured_vendor_published``（由**发布方**背书），
  并写入来源标记；这与 ``internal_measurement``（自我声明、需 attest 核验）是两条路。

用法：

    python scripts/import_public_benchmarks.py --db data/local.db
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from llm_capacity_pricing.importers.sglang_cookbook import (  # noqa: E402
    build_numeric_candidates,
)
from llm_capacity_pricing.perf.performance_profile import (  # noqa: E402
    trust_for_source_kind,
)
from llm_capacity_pricing.schemas.performance import (  # noqa: E402
    BenchmarkRun,
    BenchmarkSignature,
    PerformanceProfile,
    WorkloadSignature,
)
from llm_capacity_pricing.services import init_db, open_db  # noqa: E402

DEFAULT_INCOMING = "data/incoming/sglang_official_benchmark_candidates_20261015.json"

#: 候选对象里的 ``metrics`` 已经是**规范化字段名**（与适配器口径一致），
#: 因此这里不需要任何字段映射/换算 —— 只做搬运，缺什么就是什么。
PROFILE_FIELDS: tuple[str, ...] = (
    "ttft_mean_ms", "ttft_p99_ms",
    "tpot_mean_ms", "tpot_p99_ms",
    "itl_mean_ms", "itl_p99_ms",
    "e2e_mean_ms", "e2e_p99_ms",
    "input_throughput_tok_s", "output_throughput_tok_s", "total_throughput_tok_s",
    "request_throughput_rps", "duration_s", "error_rate",
)


def _dec(value) -> Decimal | None:
    return None if value is None else Decimal(str(value))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def candidate_to_run(candidate, *, root: Path) -> BenchmarkRun:
    """把一条**已核对**的公开候选转成 BenchmarkRun。

    字段逐项搬运：缺失的一律保持 ``None``（公开来源没写就留空），
    不做任何补造、换算或外推。``metrics`` 已是规范化字段名，无需映射。
    """
    metrics = dict(candidate.metrics or {})
    profile_kwargs: dict[str, object] = {}
    for field in PROFILE_FIELDS:
        value = _dec(metrics.get(field))
        if value is not None:
            profile_kwargs[field] = value
    if metrics.get("success_count") is not None:
        profile_kwargs["success_count"] = int(metrics["success_count"])
    if metrics.get("observed_concurrency") is not None:
        profile_kwargs["observed_concurrency"] = _dec(metrics["observed_concurrency"])

    from llm_capacity_pricing.schemas.performance import PRODUCTION_TRUST_LEVELS

    missing = list(getattr(candidate, "missing_fields", []) or [])
    profile = PerformanceProfile(
        configured_concurrency=(
            int(candidate.configured_max_concurrency)
            if candidate.configured_max_concurrency is not None
            else None
        ),
        metric_side="client",
        # 口径声明直接沿用候选里已核对过的映射（field -> definition_id）
        metric_definition_ids=sorted(
            set((candidate.metric_definition_ids or {}).values())
        ),
        missing_metrics=[
            f
            for f in ("ttft_p99_ms", "tpot_p99_ms", "output_throughput_tok_s")
            if profile_kwargs.get(f) is None
        ],
        quality_flags=["public_benchmark_converted", "partial_evidence"],
        **profile_kwargs,
    )

    arrival = "unknown"
    if candidate.traffic_request_rate_raw:
        arrival = (
            "unbounded" if str(candidate.traffic_request_rate_raw).strip() == "inf" else "request_rate"
        )

    trust, _warning = trust_for_source_kind("official_benchmark")
    signature = BenchmarkSignature(
        signature_id=f"sig_public:{candidate.record_id}",
        source_kind="official_benchmark",
        source_ref=candidate.record_id,
        model_name=candidate.model_name,
        model_revision=candidate.model_revision,        # 公开来源未给出 -> None
        weight_precision=candidate.model_precision,      # 同上
        hardware_model=candidate.hardware_model,
        gpu_count=candidate.deployment_gpu_count,
        tp_size=candidate.tp_size,
        dp_size=candidate.dp_size,
        ep_size=candidate.ep_size,
        framework=candidate.framework,
        framework_version=candidate.framework_version,
        workload=WorkloadSignature(
            dataset_name=candidate.dataset_name,
            input_len_target=candidate.input_len_target,
            output_len_target=candidate.output_len_target,
            arrival_mode=arrival,
            request_rate_rps=_dec(metrics.get("request_rate_rps")),
            configured_concurrency=(
                int(candidate.configured_max_concurrency)
                if candidate.configured_max_concurrency is not None
                else None
            ),
            num_requests=(
                int(candidate.num_prompts) if candidate.num_prompts is not None else None
            ),
            duration_s=_dec(metrics.get("duration_s")),
        ),
        data_trust=trust,
    )
    raw_sha = None
    if candidate.evidence_file:
        candidate_path = root / candidate.evidence_file
        raw_sha = _sha256(candidate_path) if candidate_path.exists() else None
    return BenchmarkRun(
        run_id=f"public::{candidate.record_id}",
        signature=signature,
        profile=profile,
        raw_file=candidate.evidence_file,
        raw_sha256=raw_sha,
        raw_format="sglang_cookbook_mdx",
        framework=candidate.framework,
        framework_version=candidate.framework_version,
        launch_command=candidate.launch_command,
        benchmark_command=candidate.benchmark_command,
        load_profile_kind="fixed_length_standard",
        data_trust=trust,
        quality_flags=[
            "official_project_published_benchmark",
            "partial_evidence",
            f"source_git_blob_sha:{candidate.source_git_blob_sha}",
            *[f"missing:{f}" for f in missing],
        ],
        notes=f"{candidate.publisher} / {candidate.source_url}",
    )


def getattr_name(kwargs: dict, name: str):
    return kwargs.get(name)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="data/local.db")
    parser.add_argument("--incoming", default=DEFAULT_INCOMING)
    parser.add_argument("--root", default=str(ROOT))
    args = parser.parse_args()

    root = Path(args.root)
    candidates, evidences, summary = build_numeric_candidates(
        args.incoming, root=root, on_verification_failure="fail"
    )
    runs = [candidate_to_run(c, root=root) for c in candidates]

    init_db(args.db)
    db, repo = open_db(args.db)
    try:
        with db.session() as session:
            repo.upsert_evidence(session, evidences)
            repo.upsert_benchmark_runs(session, runs)
            session.commit()
    finally:
        db.dispose()

    print(f"已导入 {len(runs)} 条公开实测（来源逐字段核对通过）")
    for run in runs:
        print(
            f"  {run.run_id}: {run.signature.model_name} @ {run.signature.hardware_model}"
            f" ×{run.signature.gpu_count}，可信等级 {run.data_trust}"
        )
    print("说明：缺失字段（模型版本 / 精度 / 并发）保持为空，未做任何补造或外推。")
    if summary.get("verification"):
        print(f"来源校验：{summary['verification']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
