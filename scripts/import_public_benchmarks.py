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
import html as html_lib
import json
import re
import sys
from decimal import Decimal
from pathlib import Path
from typing import Mapping, Sequence

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
            # 发布方与原始链接结构化保存：报告要能给出可追溯链接，
            # 而不是让读者从 notes 里猜（D6）。
            *([f"publisher:{candidate.publisher}"] if candidate.publisher else []),
            *([f"source_url:{candidate.source_url}"] if candidate.source_url else []),
            *[f"missing:{f}" for f in missing],
        ],
        notes=f"{candidate.publisher} / {candidate.source_url}",
    )


def getattr_name(kwargs: dict, name: str):
    return kwargs.get(name)


# ---------------------------------------------------------------------------
# D7：公开参考数据（GPUStack 性能实验室 / 第三方社区报告）
#
# 与 SGLang Cookbook 那条路一样，纪律是"每个入库的取值都必须能在归档原文里
# 找到"。不同点是这里没有章节锚点与控制台块，因此改成**逐字段字面量核对**：
# 每条记录声明 claims，每个 claim 给出要在原文里出现的词序列（token 之间
# 允许空白，其余必须逐字相邻）。缺声明或核对失败 -> 整条不入库。
#
# 已知限制（写清楚，不假装更强）：核对的是"这个值与标签确实在原文中出现且
# 相邻"，不证明它出现在表格的正确位置。因此 claims 一律带上标签一起声明。
# ---------------------------------------------------------------------------

DEFAULT_REFERENCES = (
    "data/incoming/public_benchmark_references_20261015.json"
)

#: 指标字段 -> 口径定义 ID（与 engines/metric_semantics.py 的权威定义一致）。
#: 没有权威定义 ID 的字段（如 total_throughput_tok_s 无独立口径）就不声明。
METRIC_DEFINITION_IDS: dict[str, str] = {
    "ttft_p99_ms": "m.latency.ttft.p99",
    "tpot_mean_ms": "m.latency.tpot.mean",
    "tpot_p99_ms": "m.latency.tpot.p99",
    "itl_mean_ms": "m.latency.itl.mean",
    "itl_p99_ms": "m.latency.itl.p99",
    "output_throughput_tok_s": "m.throughput.cluster.output",
    "input_throughput_tok_s": "m.throughput.cluster.input",
}

#: 记录级必填声明（缺任一项即拒绝入库）
REQUIRED_CLAIM_PATHS: tuple[str, ...] = ("model_name", "hardware_model")


def _evidence_text(raw: str, fmt: str) -> str:
    """把归档原文规范化成可做字面量核对的文本。

    html：先去掉 script/style，再去标签（**不补空格**，否则被标签切开的数字
    会散开），随后反转义实体、把连续空白压成一个空格。
    markdown / 纯文本：只做实体反转义与空白压紧。
    """
    if fmt == "html":
        raw = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", raw)
        raw = re.sub(r"(?s)<[^>]+>", "", raw)
    return re.sub(r"\s+", " ", html_lib.unescape(raw))


def _claim_found(text: str, tokens: Sequence[str]) -> bool:
    """claim 是否成立：tokens 按顺序出现，token 之间只允许空白。"""
    if not tokens:
        return False
    pattern = r"\s*".join(re.escape(token) for token in tokens)
    return re.search(pattern, text) is not None


def _claim_for(record: Mapping, group: str, key: str) -> Mapping | None:
    """claims 的键是字段路径；部署/负载字段允许省略 ``deployment.``/``workload.`` 前缀。"""
    claims = record.get("claims") or {}
    return claims.get(f"{group}.{key}") or claims.get(key)


def _check_claims(record: Mapping, source: Mapping, text: str) -> list[str]:
    """逐字段核对并检查覆盖率；返回问题清单（空表示通过）。"""
    problems: list[str] = []
    record_id = record.get("record_id", "<no-record-id>")
    claims = record.get("claims") or {}
    derived = record.get("derived_fields") or {}

    for path in REQUIRED_CLAIM_PATHS:
        if not claims.get(path):
            problems.append(f"{record_id}: 缺少必填 claim `{path}`")

    for path, claim in claims.items():
        tokens = claim.get("tokens") or []
        if not _claim_found(text, tokens):
            problems.append(
                f"{record_id}: `{path}` 未在 {source['evidence_file']} 中找到字面量"
                f"「{' '.join(tokens)}」"
            )

    # 覆盖率：有值就必须能追溯（claim 或显式 derived），不留"静默采信"的字段
    for group in ("deployment", "workload", "metrics"):
        for key, value in (record.get(group) or {}).items():
            if value is None:
                continue
            if _claim_for(record, group, key) is not None:
                continue
            if derived.get(f"{group}.{key}") or derived.get(key):
                continue
            problems.append(
                f"{record_id}: `{group}.{key}` 有值但既无 claim 也无 derived 说明"
                f"（不得静默采信）"
            )
    return problems


def verify_references(doc: Mapping, *, root: Path) -> list[str]:
    """核对全部记录的来源快照与字面量声明。"""
    problems: list[str] = []
    sources = doc.get("sources") or {}
    for source_id, source in sources.items():
        path = root / source["evidence_file"]
        if not path.exists():
            problems.append(f"{source_id}: 归档原文缺失 {source['evidence_file']}")
            continue
        digest = _sha256(path)
        if digest != source.get("evidence_sha256"):
            problems.append(
                f"{source_id}: 归档原文 sha256 与声明不一致"
                f"（声明 {source.get('evidence_sha256')}，实际 {digest}）"
            )
            continue
        text = _evidence_text(
            path.read_text(encoding="utf-8", errors="replace"),
            source.get("evidence_format", "text"),
        )
        for record in doc.get("records") or []:
            if record.get("source_id") != source_id:
                continue
            problems.extend(_check_claims(record, source, text))
    return problems


def reference_to_run(record: Mapping, source: Mapping, *, root: Path) -> BenchmarkRun:
    """把一条**已核对**的公开参考转成 BenchmarkRun（缺失字段保持为空）。"""
    dep = record.get("deployment") or {}
    wl = record.get("workload") or {}
    metrics = record.get("metrics") or {}
    derived = record.get("derived_fields") or {}
    trust = source["data_trust"]

    kwargs: dict[str, object] = {}
    for field in PROFILE_FIELDS:
        value = _dec(metrics.get(field))
        if value is not None:
            kwargs[field] = value
    if metrics.get("success_count") is not None:
        kwargs["success_count"] = int(metrics["success_count"])
    if wl.get("num_prompts") is not None:
        kwargs["num_requests"] = int(wl["num_prompts"])
    if wl.get("observed_peak_concurrency") is not None:
        # 观测峰值并发 ≠ 配置并发：分开存，绝不写进 configured_concurrency
        kwargs["observed_concurrency"] = _dec(wl["observed_peak_concurrency"])

    missing = [
        f
        for f in ("ttft_p99_ms", "tpot_p99_ms", "output_throughput_tok_s")
        if kwargs.get(f) is None
    ]
    profile = PerformanceProfile(
        configured_concurrency=(
            int(wl["configured_max_concurrency"])
            if wl.get("configured_max_concurrency") is not None
            else None
        ),
        metric_side="client",
        metric_definition_ids=sorted(
            {METRIC_DEFINITION_IDS[k] for k in metrics if k in METRIC_DEFINITION_IDS}
        ),
        missing_metrics=missing,
        quality_flags=[
            "public_reference_not_target_configuration",
            "partial_evidence",
            *[f"derived:{key}:{(spec or {}).get('reason', '')}" for key, spec in derived.items()],
        ],
        **kwargs,
    )

    arrival = (derived.get("arrival_mode") or {}).get("value", "unknown")
    signature = BenchmarkSignature(
        signature_id=f"sig_public_ref:{record['record_id']}",
        source_kind="public_reference",
        source_ref=record["record_id"],
        model_name=dep.get("model_name"),
        model_revision=dep.get("model_revision"),
        weight_precision=dep.get("weight_precision"),
        hardware_model=dep.get("hardware_model"),
        gpu_count=dep.get("gpu_count"),
        tp_size=dep.get("tp_size"),
        dp_size=dep.get("dp_size"),
        framework=dep.get("framework"),
        framework_version=dep.get("framework_version"),
        workload=WorkloadSignature(
            dataset_name=wl.get("dataset_name"),
            input_len_target=wl.get("input_len_target"),
            output_len_target=wl.get("output_len_target"),
            arrival_mode=arrival,
            configured_concurrency=(
                int(wl["configured_max_concurrency"])
                if wl.get("configured_max_concurrency") is not None
                else None
            ),
            num_requests=(
                int(wl["num_prompts"]) if wl.get("num_prompts") is not None else None
            ),
            duration_s=_dec(metrics.get("duration_s")),
        ),
        data_trust=trust,
    )
    evidence_path = root / source["evidence_file"]
    raw_sha = _sha256(evidence_path) if evidence_path.exists() else None
    declared_missing = list(record.get("missing_or_unverified_fields") or [])
    flags = [
        f"publisher:{source['publisher']}",
        f"source_url:{source['source_url']}",
        *([f"source_git_blob_sha:{source['source_git_blob_sha']}"]
          if source.get("source_git_blob_sha") else []),
        "partial_evidence",
        *[f"missing:{f}" for f in declared_missing],
    ]
    if source.get("caveats"):
        flags.append("caveat:" + " / ".join(source["caveats"]))
    return BenchmarkRun(
        run_id=f"public::{record['record_id']}",
        signature=signature,
        profile=profile,
        raw_file=source["evidence_file"],
        raw_sha256=raw_sha,
        raw_format=f"public_reference_{source.get('evidence_format', 'text')}",
        framework=dep.get("framework"),
        framework_version=dep.get("framework_version"),
        load_profile_kind=(
            "fixed_length_standard"
            if wl.get("input_len_target") is not None
            and wl.get("output_len_target") is not None
            else "unknown"
        ),
        data_trust=trust,
        quality_flags=flags,
        notes=record.get("note"),
    )


def import_public_references(path: str | Path, *, root: Path) -> list[BenchmarkRun]:
    """读公开参考文件、核对来源与字面量，返回通过核对的记录。

    核对失败即中止（``SystemExit``），不产出"部分可信"的记录。
    """
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    problems = verify_references(doc, root=root)
    if problems:
        raise SystemExit(
            "公开参考数据核对失败，未导入任何记录：\n  - " + "\n  - ".join(problems)
        )
    sources = doc["sources"]
    return [
        reference_to_run(record, sources[record["source_id"]], root=root)
        for record in doc.get("records") or []
    ]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="data/local.db")
    parser.add_argument("--incoming", default=DEFAULT_INCOMING)
    parser.add_argument("--references", default=DEFAULT_REFERENCES)
    parser.add_argument("--root", default=str(ROOT))
    args = parser.parse_args()

    root = Path(args.root)
    candidates, evidences, summary = build_numeric_candidates(
        args.incoming, root=root, on_verification_failure="fail"
    )
    sglang_runs = [candidate_to_run(c, root=root) for c in candidates]
    reference_runs = import_public_references(args.references, root=root)
    runs = sglang_runs + reference_runs

    init_db(args.db)
    db, repo = open_db(args.db)
    try:
        with db.session() as session:
            repo.upsert_evidence(session, evidences)
            repo.upsert_benchmark_runs(session, runs)
            session.commit()
    finally:
        db.dispose()

    print("公开基准导入（逐字段与归档原文核对）")
    print(f"1) SGLang Cookbook 官方发布：已导入 {len(sglang_runs)} 条，来源逐字段核对通过")
    print(
        f"2) 公开参考（GPUStack 性能实验室 / 第三方社区报告）："
        f"已导入 {len(reference_runs)} 条，字面量逐字段核对通过"
    )
    for run in reference_runs:
        print(
            f"  {run.run_id}: {run.signature.model_name} @ {run.signature.hardware_model}"
            f" ×{run.signature.gpu_count}，可信等级 {run.data_trust}"
        )
    print("说明：缺失字段（模型版本 / 精度 / 并发 / 长度）保持为空，未做任何补造或外推。")
    if summary.get("verification"):
        print(f"来源校验：{summary['verification']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
