#!/usr/bin/env python3
"""AJ6：从容量扫描结果生成真实容量推荐。"""

from __future__ import annotations

import json
from pathlib import Path

from llm_capacity_pricing.perf.capacity_recommendation import build_capacity_evidence

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    source = json.loads((ROOT / "docs/aj_capacity_scan.json").read_text(encoding="utf-8"))
    points = [
        {
            **item["point"],
            "runtime_config_sha256": item.get("runtime_config_sha256"),
            "deployment_id": (
                f"{item.get('model_name')}|{item.get('model_revision')}|"
                f"{item.get('hardware_id')}"
                if item.get("model_name") or item.get("hardware_id")
                else None
            ),
            "ingest_accepted": (item.get("qualification") or {}).get(
                "ingest_accepted"
            ),
            "sla_evaluable": (item.get("qualification") or {}).get("sla_evaluable"),
        }
        for item in source["runs"]
    ]
    evidence = build_capacity_evidence(points)
    evidence["fixed_concurrency"] = source["fixed_concurrency"]
    evidence["open_arrival"] = source["open_arrival"]
    evidence["sla"] = source["sla"]
    evidence["benchmark_identity"] = source.get("benchmark_identity")
    evidence["runtime_config_sha256"] = source.get("runtime_config_sha256")
    evidence["goodput_definition"] = source["goodput_definition"]
    evidence["max_output_tps"] = source["max_output_tps"]
    evidence["max_output_tps_point"] = source.get("max_output_tps_point")
    evidence["max_sla_goodput_rps"] = source["max_sla_goodput_rps"]
    evidence["max_sla_goodput_point"] = source.get("max_sla_goodput_point")
    evidence["max_verified_fixed_concurrency"] = source["max_verified_concurrency"]
    evidence["max_verified_workload"] = source.get("max_verified_workload")
    evidence["max_verified_by_workload"] = source.get("max_verified_by_workload") or {}

    source_margin = source["margin_candidates"]
    # 先固定推荐所属工作点，再在该工作点内选并发；不同 ISL/OSL 的容量不可拼接。
    if source_margin:
        anchor = max(source_margin, key=lambda item: item["concurrency"])
        workload = {
            "input_len": anchor["input_len"],
            "output_len": anchor["output_len"],
        }
    elif source.get("max_verified_workload"):
        workload = source["max_verified_workload"]
    else:
        workload = source.get("max_steady_open_workload")

    def same_workload(item) -> bool:
        return workload is not None and (
            item["input_len"] == workload["input_len"]
            and item["output_len"] == workload["output_len"]
        )

    margin10 = sorted(
        item["concurrency"]
        for item in source_margin
        if same_workload(item) and item["margin_10pct"]
    )
    margin5 = sorted(
        item["concurrency"]
        for item in source_margin
        if same_workload(item) and item["margin_5pct"]
    )
    verified = (
        [source["max_verified_concurrency"]]
        if workload is not None
        and source.get("max_verified_workload") == workload
        and source.get("max_verified_concurrency") is not None
        else []
    )
    steady = [
        item for item in (source.get("open_arrival_candidates") or [])
        if same_workload(item)
    ]
    evidence["margin_10pct_concurrencies"] = margin10
    evidence["margin_5pct_concurrencies"] = margin5
    evidence["max_steady_open_rate_rps"] = (
        max(steady, key=lambda item: float(item["request_rate_rps"]))[
            "request_rate_rps"
        ]
        if steady
        else None
    )
    evidence["recommended_workload"] = workload
    if margin10:
        evidence["recommended_mode"] = "fixed_concurrency"
        evidence["recommended_value"] = max(margin10)
        evidence["reasons"] = ["存在同时满足 SLA 与 10% 内部裕量的正式固定并发"]
    elif margin5:
        evidence["recommended_mode"] = "fixed_concurrency"
        evidence["recommended_value"] = max(margin5)
        evidence["reasons"] = ["存在同时满足 SLA 与 5% 内部裕量的正式固定并发"]
    elif verified:
        evidence["recommended_mode"] = "fixed_concurrency"
        evidence["recommended_value"] = max(verified)
        evidence["reasons"] = ["正式固定并发满足 SLA，但无内部裕量"]
    elif evidence["max_steady_open_rate_rps"] is not None:
        evidence["recommended_mode"] = "open_arrival"
        evidence["recommended_value"] = evidence["max_steady_open_rate_rps"]
        evidence["reasons"] = ["固定并发无正式达标证据；使用有窗口证据的 Poisson 到达率"]
    else:
        evidence["recommended_mode"] = None
        evidence["recommended_value"] = None
        evidence["reasons"] = ["没有可验证的 SLA 容量结论"]

    evidence["caveats"] = []
    for item in source["fixed_concurrency"]:
        if (
            item["evidence_basis"] == "formal"
            and not item["all_pass"]
            and (item["margin_10pct"] or item["margin_5pct"])
        ):
            evidence["caveats"].append(
                f"ISL{item['input_len']}/C{item['concurrency']} 的 TPOT P99 "
                f"{item['worst_tpot_p99_ms']} ms 有内部裕量，但正式重复仅 "
                f"{item['pass_count']}/{item['runs']} 通过（存在失败请求），"
                "不能作为裕量安全容量。"
            )
    if source.get("legacy_unbound_formal_runs"):
        evidence["caveats"].append(
            "历史正式运行未绑定冻结 runtime_config_sha256（"
            + ", ".join(source["legacy_unbound_formal_runs"])
            + "），只作延迟参考，不参与容量推荐。"
        )
    if any(
        item["evidence_basis"] == "insufficient_repeats"
        for item in source["open_arrival"]
    ):
        evidence["caveats"].append(
            "Poisson 到达率只有 1 次 500 请求窗口，未达到 5 次正式重复，"
            "只在开放到达报告中作为窗口观察，不作为容量候选。"
        )
    if source.get("max_output_tps_point"):
        point = source["max_output_tps_point"]
        evidence["caveats"].append(
            f"最高吞吐 {point['value']} tok/s 来自 {point['run_id']}"
            f"（{point['evidence_basis']}），峰值吞吐不代表 SLA 安全容量。"
        )
    json_path = ROOT / "docs/aj_capacity_recommendation.json"
    md_path = ROOT / "docs/aj_capacity_recommendation.md"
    json_path.write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    lines = [
        "# AJ6 真实容量推荐",
        "",
        f"- 最高输出吞吐：{evidence['max_output_tps']} tok/s",
        f"- 最高 SLA Goodput：{evidence['max_sla_goodput_rps']} req/s",
        f"- 最大正式验证固定并发：{evidence['max_verified_fixed_concurrency']}"
        f"（工作点 {evidence.get('max_verified_workload')}）",

        f"- 10% 裕量候选：C{evidence['margin_10pct_concurrencies']}",
        f"- 5% 裕量候选：C{evidence['margin_5pct_concurrencies']}",
        f"- 最大窗口稳态到达率：{evidence['max_steady_open_rate_rps']} req/s",
        f"- 推荐：{evidence['recommended_mode']} = {evidence['recommended_value']}",
        "",
        "## 理由",
        "",
    ]
    lines.extend(f"- {reason}" for reason in evidence["reasons"])
    if evidence["caveats"]:
        lines.extend(["", "## 需要明确的限制", ""])
        lines.extend(f"- {caveat}" for caveat in evidence["caveats"])
    lines.extend(
        [
            "",
            "## 固定并发（正式优先）",
            "",
            "| ISL/OSL | C | 证据 | 运行 | 通过 | 最差 TPOT P99 | 10% 裕量 | 5% 裕量 |",
            "|---|---|---|---|---|---|---|---|",
        ]
    )
    for item in evidence["fixed_concurrency"]:
        lines.append(
            f"| {item['input_len']}/{item['output_len']} | C{item['concurrency']} "
            f"| {item.get('evidence_basis')} | {item['runs']} "
            f"| {item['pass_count']}/{item['runs']} | {item['worst_tpot_p99_ms']} "
            f"| {item['margin_10pct']} | {item['margin_5pct']} |"
        )
    lines.extend(["", "## 限制", ""])
    lines.extend(f"- {limit}" for limit in evidence["limits"])
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"已写出：{json_path.relative_to(ROOT)} / {md_path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
