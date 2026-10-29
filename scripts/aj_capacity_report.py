#!/usr/bin/env python3
"""AJ1/AJ2/AJ3/AJ4：真实容量扫描、SLA 裕量与 Goodput 报告。"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

import yaml

from llm_capacity_pricing.perf.benchmark_ingest import identity_sha256
from llm_capacity_pricing.perf.capacity_analysis import build_capacity_point
from llm_capacity_pricing.perf.source_verification import resolve_source_kind

ROOT = Path(__file__).resolve().parents[1]
RUN_ROOT = ROOT / "data/benchmark/rtx3090"
REGISTRY = ROOT / "data/benchmark/source_attestations.json"
MIN_FORMAL_REPEATS = 5
MIN_FORMAL_REQUESTS = 500
SLA_TTFT = Decimal("6000")
SLA_TPOT = Decimal("40")
MARGIN_10 = Decimal("36")
MARGIN_5 = Decimal("38")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _frozen_runtime_sha() -> str:
    payload = json.loads(
        (RUN_ROOT / "runtime_config.json").read_text(encoding="utf-8")
    )
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


FROZEN_RUNTIME_SHA = _frozen_runtime_sha()


def _q(value, places: str = "0.001") -> str:
    if value is None:
        return "unknown"
    return str(Decimal(str(value)).quantize(Decimal(places), rounding=ROUND_HALF_UP))


def _load_rows(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


FORMAL_PREFIXES = ("ai-e1c8-seed", "aj-e1c6-seed", "aj-e1c8-seed")
FORMAL_RUN_IDS = ("aj-a5-r065-500",)


def load_preregistered() -> list[dict]:
    """读取预注册清单：工作点 + 预期 seed + 每 run 请求数 + 冻结配置哈希。"""
    manifests = []
    for path in sorted((ROOT / "data/benchmark/experiments").glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("manifest_status") != "preregistered":
            continue
        for workpoint in payload.get("workpoints") or []:
            manifests.append(
                {
                    "runtime_config_sha256": payload.get("runtime_config_sha256"),
                    "input_len": workpoint.get("input_len"),
                    "output_len": workpoint.get("output_len"),
                    "concurrency": workpoint.get("concurrency"),
                    "request_rate_rps": workpoint.get("request_rate_rps"),
                    "expected_seeds": set(workpoint.get("expected_seeds") or []),
                    "expected_run_ids": set(workpoint.get("expected_run_ids") or []),
                    "requests_per_run": workpoint.get("requests_per_run"),
                }
            )
    return manifests


def matches_preregistration(
    manifest: dict,
    *,
    run_id: str,
    runtime_sha: str | None,
    input_len: int,
    output_len: int,
    concurrency: int | None,
    request_rate_rps,
    seed: int | None,
    num_requests: int | None,
) -> bool:
    expected_rate = manifest.get("request_rate_rps")
    if manifest.get("runtime_config_sha256") != runtime_sha:
        return False
    if manifest.get("input_len") != input_len or manifest.get("output_len") != output_len:
        return False
    if expected_rate is None:
        if concurrency != manifest.get("concurrency") or request_rate_rps is not None:
            return False
    else:
        if request_rate_rps is None or Decimal(str(request_rate_rps)) != Decimal(
            str(expected_rate)
        ):
            return False
    expected_ids = manifest.get("expected_run_ids") or set()
    if expected_ids:
        # 预注册绑定运行目录名：请求数错误/同 seed 的探索运行不能冒充正式槽位
        return run_id in expected_ids
    return (
        seed in (manifest.get("expected_seeds") or set())
        and num_requests == manifest.get("requests_per_run")
    )


def classify_run(
    name: str,
    runtime_sha: str | None,
    qualification: dict,
    *,
    preregistered: bool = False,
) -> dict:
    """正式资格 = 预注册/批次命名 + 配置哈希绑定 + 导入校验通过。"""
    name_formal = (
        preregistered or name.startswith(FORMAL_PREFIXES) or name in FORMAL_RUN_IDS
    )
    qualified = (
        qualification.get("ingest_accepted") is True
        and qualification.get("sla_evaluable") is True
    )
    config_bound = runtime_sha == FROZEN_RUNTIME_SHA
    return {
        "name_formal": name_formal,
        "qualified": qualified,
        "config_bound": config_bound,
        "formal": name_formal and config_bound and qualified,
        "legacy_unbound_formal": name_formal and not config_bound and qualified,
    }


def evidence_integrity(
    directory: Path,
    metadata: dict,
    summary: dict,
    report: dict,
) -> tuple[bool, list[str]]:
    """用当前文件重新核对 import_report 记录的哈希与来源，避免“旧报告 + 新数据”。"""
    problems: list[str] = []
    validation = report.get("validation") or {}
    if validation.get("accepted") is not True:
        return False, ["import_report 未接受该运行"]
    requests_sha = _sha256_file(directory / "requests.jsonl")
    if validation.get("raw_sha256") != requests_sha:
        problems.append("requests.jsonl 与 import_report 的原始哈希不一致")
    if validation.get("declared_sha256") not in (None, requests_sha):
        problems.append("requests.jsonl 与声明的哈希不一致")
    if (metadata.get("evidence") or {}).get("source_sha256") != requests_sha:
        problems.append("metadata 的 source_sha256 与当前文件不一致")
    summary_sha = _sha256_file(directory / "client_summary.json")
    declared_summary = metadata.get("client_summary_sha256")
    if declared_summary is not None and declared_summary != summary_sha:
        problems.append("client summary 哈希与 metadata/登记不一致")
    current_identity = identity_sha256(metadata)
    if validation.get("identity_sha256") != current_identity:
        problems.append("metadata 身份哈希与 import_report 不一致")
    recomputed = validation.get("recomputed") or {}
    if recomputed.get("num_requests") != summary.get("num_requests"):
        problems.append("client summary 请求数与 import_report 不一致")
    if recomputed.get("success_count") != summary.get("success_count"):
        problems.append("client summary 成功数与 import_report 不一致")
    window = summary.get("measurement_window_ms") or {}
    start = window.get("start_ms")
    end = window.get("end_ms")
    if start is not None and end is not None and recomputed.get("duration_s") is not None:
        expected = (Decimal(str(end)) - Decimal(str(start))) / Decimal(1000)
        if abs(expected - Decimal(str(recomputed["duration_s"]))) > Decimal("0.5"):
            problems.append("client summary 测量窗口与 import_report 不一致")
    verification = resolve_source_kind(
        metadata,
        raw_sha256=requests_sha,
        identity_sha256=current_identity,
        registry=REGISTRY,
    )
    if verification.get("verified_source_kind") != "internal_measurement":
        problems.append("当前来源未认证为 internal_measurement")
    return not problems, problems


def workpoint_key(input_len, output_len, concurrency, request_rate_rps):
    """固定并发与开放到达是两种工作点，键里必须区分。"""
    if request_rate_rps is not None:
        return (input_len, output_len, "rate", str(request_rate_rps))
    return (input_len, output_len, "conc", str(concurrency))


def _runs():
    out = []
    preregistered = load_preregistered()
    for directory in sorted(RUN_ROOT.glob("*")):
        metadata_path = directory / "metadata.yaml"
        summary_path = directory / "client_summary.json"
        requests_path = directory / "requests.jsonl"
        if not (metadata_path.is_file() and summary_path.is_file() and requests_path.is_file()):
            continue
        metadata = yaml.safe_load(metadata_path.read_text(encoding="utf-8"))
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        report_path = directory / "import_report.json"
        report = json.loads(report_path.read_text(encoding="utf-8")) if report_path.is_file() else {}
        qualification = (report.get("validation") or {}).get("qualification") or {}
        point = build_capacity_point(
            _load_rows(requests_path),
            candidate_id=directory.name,
            input_len=int(metadata.get("input_len") or 0),
            output_len=int(metadata.get("output_len") or 0),
            window_s=(
                (
                    Decimal(str((summary.get("measurement_window_ms") or {}).get("end_ms", 0)))
                    - Decimal(str((summary.get("measurement_window_ms") or {}).get("start_ms", 0)))
                )
                / 1000
            ),

            ttft_limit_ms=SLA_TTFT,
            tpot_limit_ms=SLA_TPOT,
            concurrency=metadata.get("configured_max_concurrency"),
            request_rate_rps=(
                Decimal(str(metadata["request_rate_rps"]))
                if metadata.get("request_rate_rps") is not None
                else None
            ),
            seed=summary.get("seed"),
            capacity_evidence_ids=[f"rtx3090:{directory.name}"],
        )
        runtime_sha = metadata.get("runtime_config_sha256")
        integrity_ok, integrity_reasons = evidence_integrity(
            directory, metadata, summary, report
        )
        if not integrity_ok:
            qualification = {
                **qualification,
                "ingest_accepted": False,
                "sla_evaluable": False,
                "integrity_reasons": integrity_reasons,
            }
        is_preregistered = any(
            matches_preregistration(
                manifest,
                run_id=directory.name,
                runtime_sha=runtime_sha,
                input_len=int(metadata.get("input_len") or 0),
                output_len=int(metadata.get("output_len") or 0),
                concurrency=metadata.get("configured_max_concurrency"),
                request_rate_rps=metadata.get("request_rate_rps"),
                seed=summary.get("seed"),
                num_requests=summary.get("num_requests"),
            )
            for manifest in preregistered
        )
        flags = classify_run(
            directory.name,
            runtime_sha,
            qualification,
            preregistered=is_preregistered,
        )
        out.append(
            {
                "run_id": directory.name,
                "runtime_config_sha256": runtime_sha,
                "preregistered": is_preregistered,
                "integrity_ok": integrity_ok,
                "integrity_reasons": integrity_reasons,
                "num_requests": summary.get("num_requests"),
                "success_count": summary.get("success_count"),
                "model_name": metadata.get("model_name"),
                "model_revision": metadata.get("model_revision"),
                "hardware_id": metadata.get("hardware_id"),
                **flags,
                "qualification": qualification,
                "point": point.model_dump(mode="json"),
            }
        )
    return out


def _group(runs, *, rate=None):
    groups = defaultdict(list)
    for run in runs:
        p = run["point"]
        if rate is None and p["request_rate_rps"] is not None:
            continue
        if rate is not None and p["request_rate_rps"] is None:
            continue
        if rate is not None and Decimal(str(p["request_rate_rps"])) != Decimal(str(rate)):
            continue
        key = (
            p["input_len"],
            p["output_len"],
            p["concurrency"],
            run.get("runtime_config_sha256"),
            run.get("model_name"),
            run.get("model_revision"),
            run.get("hardware_id"),
        )
        groups[key].append(run)
    return groups


def main() -> int:
    runs = _runs()
    fixed = _group(runs)
    rate_runs = [r for r in runs if r["point"]["request_rate_rps"] is not None]

    def summary(group):
        slots = [r for r in group if r["name_formal"]]
        formal = [r for r in group if r["formal"]]
        legacy = [r for r in group if r.get("legacy_unbound_formal")]
        invalid = [
            r
            for r in slots
            if not r["formal"] and not r.get("legacy_unbound_formal")
        ]
        seeds = [r["point"].get("seed") for r in slots]
        formal_complete = (
            len(slots) >= MIN_FORMAL_REPEATS
            and len(formal) == len(slots)
            and all(seed is not None for seed in seeds)
            and len(set(seeds)) == len(slots)
            and all(
                (r.get("num_requests") or 0) >= MIN_FORMAL_REQUESTS
                for r in slots
            )
        )
        # 无效/失败的正式重复必须留在 evaluated 里，不能被其他合格运行挤掉
        evaluated = formal if formal_complete else (slots or group)
        if formal_complete:
            strategy = "formal"
        elif invalid:
            strategy = "invalid_formal_repeat"
        elif formal:
            strategy = "insufficient_repeats"
        elif legacy and len(legacy) == len(group):
            strategy = "legacy_unbound"
        else:
            strategy = "exploratory"

        def max_metric(name):
            values = [
                Decimal(str(r["point"][name]))
                for r in evaluated
                if r["point"].get(name) is not None
            ]
            return max(values) if values else None

        worst_ttft = max_metric("ttft_p99_ms")
        worst_tpot = max_metric("tpot_p99_ms")
        best_tps = max_metric("output_tps")
        best_goodput = max_metric("sla_goodput_rps")

        def run_passes(r) -> bool:
            ttft = r["point"].get("ttft_p99_ms")
            tpot = r["point"].get("tpot_p99_ms")
            return (
                ttft is not None
                and tpot is not None
                and Decimal(str(ttft)) <= SLA_TTFT
                and Decimal(str(tpot)) <= SLA_TPOT
                and r["point"]["success_rate"] == "1"
            )

        pass_count = sum(1 for r in evaluated if run_passes(r))
        steady_states = {r["point"]["steady_state_status"] for r in evaluated}
        if "unstable" in steady_states:
            steady = "unstable"
        elif steady_states == {"observed_stable_in_window"}:
            steady = "observed_stable_in_window"
        else:
            steady = "unknown"
        return {
            "runs": len(evaluated),
            "formal_runs": len(formal),
            "exploratory_runs": len(group) - len(formal),
            "evidence_basis": strategy,
            "formal_complete": formal_complete,
            "invalid_formal_runs": [r["run_id"] for r in invalid],
            "pass_count": pass_count,
            "all_pass": pass_count == len(evaluated),
            "worst_ttft_p99_ms": _q(worst_ttft),
            "worst_tpot_p99_ms": _q(worst_tpot),
            "best_output_tps": _q(best_tps),
            "best_sla_goodput_rps": _q(best_goodput),
            "margin_10pct": worst_tpot is not None and worst_tpot <= MARGIN_10,
            "margin_5pct": worst_tpot is not None and worst_tpot <= MARGIN_5,
            "steady_state": steady,
        }

    fixed_summary = []
    for (
        isl,
        osl,
        concurrency,
        runtime_sha,
        model_name,
        model_revision,
        hardware_id,
    ), group in sorted(
        fixed.items(), key=lambda item: tuple(str(value) for value in item[0])
    ):
        item = summary(group)
        item.update(
            {
                "input_len": isl,
                "output_len": osl,
                "concurrency": concurrency,
                "runtime_config_sha256": runtime_sha,
                "model_name": model_name,
                "model_revision": model_revision,
                "hardware_id": hardware_id,
            }
        )
        fixed_summary.append(item)

    rate_summary = []
    by_rate = defaultdict(list)
    for run in rate_runs:
        by_rate[
            (
                Decimal(str(run["point"]["request_rate_rps"])),
                run.get("runtime_config_sha256"),
                run["point"]["input_len"],
                run["point"]["output_len"],
                run.get("model_name"),
                run.get("model_revision"),
                run.get("hardware_id"),
            )
        ].append(run)
    for (rate, runtime_sha, isl, osl, model_name, model_revision, hardware_id), group in sorted(
        by_rate.items(),
        key=lambda item: tuple(str(value) for value in item[0]),
    ):
        item = summary(group)
        eval_group = [run for run in group if run["formal"]] or group

        def min_rate(field: str):
            values = [
                Decimal(str(run["point"][field]))
                for run in eval_group
                if run["point"].get(field) is not None
            ]
            return min(values) if len(values) == len(eval_group) else None

        item.update(
            {
                "input_len": isl,
                "output_len": osl,
                "request_rate_rps": str(rate),
                "runtime_config_sha256": runtime_sha,
                "model_name": model_name,
                "model_revision": model_revision,
                "hardware_id": hardware_id,
                "arrival_rps": _q(min_rate("arrival_rps")),
                "completed_rps": _q(min_rate("completed_rps")),
                "queue_status": ",".join(
                    sorted({r["point"]["queue_status"] for r in eval_group})
                ),
            }
        )
        rate_summary.append(item)

    # 命名批次里任何未通过校验/配置绑定/来源认证的运行，都阻断同工作点的推荐，
    # 即使它因为哈希不同落到另一个分组。
    blocked_workpoints: dict[tuple, str] = {}
    for run in runs:
        if not run["name_formal"] or run["formal"]:
            continue
        if run.get("legacy_unbound_formal"):
            continue
        p = run["point"]
        key = workpoint_key(
            p["input_len"],
            p["output_len"],
            p["concurrency"],
            p["request_rate_rps"],
        )
        blocked_workpoints[key] = run["run_id"]

    def blocked(item) -> bool:
        return workpoint_key(
            item["input_len"],
            item["output_len"],
            item.get("concurrency"),
            item.get("request_rate_rps"),
        ) in blocked_workpoints

    fixed_ok = [
        s
        for s in fixed_summary
        if s["all_pass"]
        and s["evidence_basis"] == "formal"
        and not blocked(s)
        and not s.get("invalid_formal_runs")
    ]
    best_fixed = (
        max(fixed_ok, key=lambda s: s["concurrency"], default=None)
        if fixed_ok
        else None
    )
    max_verified_by_workload: dict[str, int] = {}
    for item in fixed_ok:
        label = f"{item['input_len']}/{item['output_len']}"
        max_verified_by_workload[label] = max(
            max_verified_by_workload.get(label, 0), item["concurrency"]
        )
    open_candidates = [
        s
        for s in rate_summary
        if s["steady_state"] == "observed_stable_in_window"
        and s["pass_count"] == s["runs"]
        and s["evidence_basis"] == "formal"
        and s.get("arrival_rps") not in (None, "unknown")
        and s.get("completed_rps") not in (None, "unknown")
        and Decimal(s["arrival_rps"]) >= Decimal(s["request_rate_rps"]) * Decimal("0.95")
        and Decimal(s["completed_rps"]) >= Decimal(s["request_rate_rps"]) * Decimal("0.95")
        and not blocked(s)
        and not s.get("invalid_formal_runs")
    ]
    best_open = (
        max(open_candidates, key=lambda s: Decimal(s["request_rate_rps"]), default=None)
        if open_candidates
        else None
    )
    best_open_by_workload: dict[str, str] = {}
    for item in open_candidates:
        label = f"{item['input_len']}/{item['output_len']}"
        current = best_open_by_workload.get(label)
        if current is None or Decimal(item["request_rate_rps"]) > Decimal(current):
            best_open_by_workload[label] = item["request_rate_rps"]
    bound_runs = [
        r
        for r in runs
        if r["config_bound"] and r.get("integrity_ok") and r.get("qualified")
    ]
    identities = {
        (r["model_name"], r["model_revision"], r["hardware_id"]) for r in bound_runs
    }
    benchmark_identity = (
        {
            "model_name": next(iter(identities))[0],
            "model_revision": next(iter(identities))[1],
            "hardware_id": next(iter(identities))[2],
        }
        if len(identities) == 1
        else None
    )

    def _best(field: str):
        candidates = [r for r in bound_runs if r["point"].get(field)]
        if not candidates:
            return None
        run = max(candidates, key=lambda r: Decimal(str(r["point"][field])))
        point = run["point"]
        return {
            "value": _q(point[field]),
            "run_id": run["run_id"],
            "input_len": point["input_len"],
            "output_len": point["output_len"],
            "concurrency": point["concurrency"],
            "request_rate_rps": point["request_rate_rps"],
            "evidence_basis": (
                "formal"
                if run["formal"]
                else (
                    "legacy_unbound"
                    if run.get("legacy_unbound_formal")
                    else "exploratory"
                )
            ),
            "sla_goodput_rps": _q(point["sla_goodput_rps"]),
        }

    best_output = _best("output_tps")
    best_goodput = _best("sla_goodput_rps")
    doc = {
        "schema_version": "1.0",
        "runtime_config_sha256": FROZEN_RUNTIME_SHA,
        "benchmark_identity": benchmark_identity,
        "sla": {"ttft_p99_ms_max": str(SLA_TTFT), "tpot_p99_ms_max": str(SLA_TPOT)},
        "internal_margins": {"10pct_tpot_ms": str(MARGIN_10), "5pct_tpot_ms": str(MARGIN_5)},
        "goodput_definition": (
            "逐请求 success 且 TTFT<=6000ms 且 TPOT<=40ms 的请求数 / 测量窗口"
        ),
        "runs": runs,
        "fixed_concurrency": fixed_summary,
        "open_arrival": rate_summary,
        "max_output_tps": best_output["value"] if best_output else None,
        "max_output_tps_point": best_output,
        "max_sla_goodput_rps": best_goodput["value"] if best_goodput else None,
        "max_sla_goodput_point": best_goodput,
        "max_verified_concurrency": (
            best_fixed["concurrency"] if best_fixed else None
        ),
        "max_verified_workload": (
            {
                "input_len": best_fixed["input_len"],
                "output_len": best_fixed["output_len"],
            }
            if best_fixed
            else None
        ),
        "max_verified_by_workload": max_verified_by_workload,
        "margin_candidates": [
            s for s in fixed_ok if s["margin_10pct"] or s["margin_5pct"]
        ],
        "max_steady_open_rate_rps": (
            best_open["request_rate_rps"] if best_open else None
        ),
        "max_steady_open_workload": (
            {
                "input_len": best_open["input_len"],
                "output_len": best_open["output_len"],
            }
            if best_open
            else None
        ),
        "max_steady_open_by_workload": best_open_by_workload,
        "exploratory_only": [
            s for s in fixed_summary if s["evidence_basis"] == "exploratory"
        ],
        "open_arrival_candidates": open_candidates,
        "legacy_unbound_formal_runs": [
            r["run_id"] for r in runs if r.get("legacy_unbound_formal")
        ],
        "blocked_workpoints": [
            {
                "run_id": run_id,
                "input_len": key[0],
                "output_len": key[1],
                "mode": "request_rate" if key[2] == "rate" else "concurrency",
                "value": key[3],
            }
            for key, run_id in blocked_workpoints.items()
        ],
        "notes": [
            "固定并发容量不等于 Poisson 稳态容量。",
            "探索运行不作为正式容量推荐。",
            "没有服务端队列长度数据时 queue_status=unobserved。",
            "正式容量要求运行元数据绑定冻结的 runtime_config_sha256；"
            "AI 阶段历史运行未绑定配置，只作为延迟参考，不进入容量推荐。",
        ],
    }
    json_path = ROOT / "docs/aj_capacity_scan.json"
    md_path = ROOT / "docs/aj_capacity_scan.md"
    json_path.write_text(json.dumps(doc, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    lines = [
        "# AJ2/AJ3 固定并发容量扫描",
        "",
        "> Goodput = 逐请求 success 且 TTFT≤6000ms、TPOT≤40ms 的完成率；"
        "与工作点 P99 达标分开。10%/5% 裕量只比较 TPOT P99。",
        "",
        "| ISL/OSL | 并发 | 证据 | 运行 | 通过 | 最差 TTFT P99 | 最差 TPOT P99 | 最高吞吐 | 最高 Goodput | 10%裕量 | 5%裕量 | 稳态 |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for s in fixed_summary:
        lines.append(
            f"| {s['input_len']}/{s['output_len']} | C{s['concurrency']} "
            f"| {s['evidence_basis']} | {s['runs']} "
            f"| {s['pass_count']}/{s['runs']} | {s['worst_ttft_p99_ms']} "
            f"| {s['worst_tpot_p99_ms']} | {s['best_output_tps']} "
            f"| {s['best_sla_goodput_rps']} | {s['margin_10pct']} "
            f"| {s['margin_5pct']} | {s['steady_state']} |"
        )
    def _point_label(item):
        if not item:
            return "none"
        load = (
            f"Poisson {item['request_rate_rps']} req/s"
            if item["request_rate_rps"] is not None
            else f"C{item['concurrency']}"
        )
        return (
            f"{item['run_id']}（ISL{item['input_len']}/OSL{item['output_len']}，"
            f"{load}，{item['evidence_basis']}）"
        )

    lines.extend(
        [
            "",
            "## 结论分类",
            "",
            f"- 最高输出吞吐：{doc['max_output_tps']} tok/s，来自 {_point_label(best_output)}；",
            f"- 最高 SLA Goodput：{doc['max_sla_goodput_rps']} req/s，"
            f"来自 {_point_label(best_goodput)}；",
            f"- 最大可验证固定并发：{doc['max_verified_concurrency']}"
            f"（工作点 {doc['max_verified_workload']}）；",

            f"- 10%/5% 裕量候选数：{len(doc['margin_candidates'])}；",
            "",
            "> 峰值吞吐通常出现在过载探索点，不等于安全容量；"
            "跨 ISL 的吞吐数字不可直接比较。探索点不进入正式推荐。",
            "",
        ]
    )
    md_path.write_text("\n".join(lines), encoding="utf-8")

    # 开放到达单独报告
    lines = [
        "# AJ4 Poisson 开放到达稳定性",
        "",
        "| 计划到达率 | 实际到达 | 完成 | 成功 | 最差 TTFT P99 | 最差 TPOT P99 | 输出 tok/s | 稳态 | 队列观测 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for s in rate_summary:
        lines.append(
            f"| {s['request_rate_rps']} | {s['arrival_rps']} | {s['completed_rps']} "
            f"| {s['pass_count']}/{s['runs']} | {s['worst_ttft_p99_ms']} "
            f"| {s['worst_tpot_p99_ms']} | {s['best_output_tps']} "
            f"| {s['steady_state']} | {s['queue_status']} |"
        )
    lines.extend(["", "## 结论", ""])
    for s in rate_summary:
        ratio = (
            Decimal(s["completed_rps"]) / Decimal(s["request_rate_rps"])
            if Decimal(s["request_rate_rps"]) > 0
            else None
        )
        lines.append(
            f"- 计划 {s['request_rate_rps']} req/s（{s['evidence_basis']}，"
            f"{s['runs']} 次运行）：实际到达 {s['arrival_rps']}、完成 "
            f"{s['completed_rps']} req/s，完成/计划="
            f"{_q(ratio) if ratio is not None else 'unknown'}，"
            f"成功 {s['pass_count']}/{s['runs']}，稳态 `{s['steady_state']}`；"
        )
    lines.extend(
        [
            "",
            "- 服务端队列时间序列不可得，所有点 `queue_status=unobserved`。",
            "- 正式重复要求至少 5 个独立 seed；`insufficient_repeats` 的到达率"
            "即使窗口内稳定，也只作为观察，不作为容量候选。",
            "",
            "> 只有 `observed_stable_in_window`、无失败且达到正式重复要求的到达率"
            "才可作为稳态候选；固定并发容量不能直接换算为 Poisson 稳态容量。",
            "",
        ]
    )
    (ROOT / "docs/aj_open_arrival_stability.md").write_text("\n".join(lines), encoding="utf-8")

    # SLA 裕量分析
    lines = [
        "# AJ3 SLA 裕量与安全容量",
        "",
        "| 工作点 | 最差 TPOT P99 | 10% 裕量(≤36) | 5% 裕量(≤38) | 正式通过 |",
        "|---|---|---|---|---|",
    ]
    for s in fixed_summary:
        lines.append(
            f"| ISL{s['input_len']}/C{s['concurrency']}（{s['evidence_basis']}） | {s['worst_tpot_p99_ms']} "
            f"| {s['margin_10pct']} | {s['margin_5pct']} | {s['pass_count']}/{s['runs']} |"
        )
    lines.extend(
        [
            "",
            "> 10%/5% 是内部风险等级，不是客户 SLA 的修改。",
            "",
        ]
    )
    (ROOT / "docs/aj_sla_margin_analysis.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"已写出：{json_path.relative_to(ROOT)} / {md_path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
