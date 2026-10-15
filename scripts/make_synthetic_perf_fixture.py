#!/usr/bin/env python
"""生成**合成性能曲线样本**（第三阶段 M21/M22/M23 的算法验证数据）。

用途：容量曲线、估算器、SLA 判定这些算法需要"一组内部自洽的多并发点"才能测。
本脚本按 vLLM ``bench serve`` 结果 JSON 的字段名生成 5 个并发点，数值全部由
公式推出（吞吐 = token 数 / 时长），并标注 ``synthetic_fixture``。

**禁止事项**：这些数据**不得**用于任何正式 SLA 结论（``SlaPolicy`` 默认拒绝
synthetic）；它们只能验证算法与流程。

运行：``.venv/bin/python scripts/make_synthetic_perf_fixture.py``
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "fixtures" / "perf_curve"

MODEL = "SYNTHETIC-MODEL-C"
#: 合成样本也**完整声明身份字段**（revision / 框架版本 / 数据集）：
#: 严格匹配要求这些字段齐备，缺一项就不能评为 exact_match（V3.1 R2）。
GPU = "SYNTHETIC-GPU-C"
REVISION = "synthetic-revision-1"
FRAMEWORK_VERSION = "synthetic-fw-1.0"
DATASET = "random"
TP = 4
DP = 1
ISL, OSL, REQUESTS = 1024, 256, 100

#: (并发, TTFT p99 ms, TPOT p99 ms, ITL p99 ms, 输出吞吐 tok/s)
#: 形状：并发升高 -> 吞吐上升并趋饱和、时延显著上升（用于验证曲线识别与 SLA 判定）
POINTS: tuple[tuple[int, float, float, float, float], ...] = (
    (1, 120.0, 12.0, 12.6, 45.0),
    (2, 150.0, 14.0, 14.8, 88.0),
    (4, 210.0, 18.0, 19.2, 165.0),
    (8, 340.0, 26.0, 27.5, 280.0),
    (16, 720.0, 45.0, 47.0, 380.0),
)


def _point_payload(concurrency: int, ttft_p99: float, tpot_p99: float, itl_p99: float, out_tps: float) -> dict:
    """按"吞吐 = token / 时长"自洽地推出其余字段。"""
    total_out = REQUESTS * OSL
    total_in = REQUESTS * ISL
    duration = total_out / out_tps
    return {
        "date": "2026-10-15T00:00:00",
        "endpoint_type": "openai",
        "num_requests": REQUESTS,
        "max_concurrency": concurrency,
        "request_rate": "inf",
        "completed": REQUESTS,
        "total_input_tokens": total_in,
        "total_output_tokens": total_out,
        "duration": round(duration, 3),
        # 时延（客户端口径）
        "mean_ttft_ms": round(ttft_p99 * 0.45, 2),
        "median_ttft_ms": round(ttft_p99 * 0.40, 2),
        "p90_ttft_ms": round(ttft_p99 * 0.72, 2),
        "p99_ttft_ms": ttft_p99,
        # 请求级 TPOT（与 ITL 分开；本样本刻意让两者不同）
        "mean_tpot_ms": round(tpot_p99 * 0.62, 2),
        "p90_tpot_ms": round(tpot_p99 * 0.85, 2),
        "p99_tpot_ms": tpot_p99,
        # 逐 token 间隔（ITL）
        "mean_itl_ms": round(itl_p99 * 0.60, 2),
        "p99_itl_ms": itl_p99,
        "mean_e2el_ms": round(ttft_p99 * 0.45 + tpot_p99 * 0.62 * OSL, 2),
        "p90_e2el_ms": round(ttft_p99 * 0.72 + tpot_p99 * 0.85 * OSL, 2),
        "p99_e2el_ms": round(ttft_p99 + tpot_p99 * OSL, 2),
        # 吞吐（与 token/时长自洽）
        "request_throughput": round(REQUESTS / duration, 6),
        "input_throughput": round(total_in / duration, 4),
        "output_throughput": out_tps,
        "mean_input_tokens": float(ISL),
        "mean_output_tokens": float(OSL),
        "model_id": MODEL,
        "tokenizer_id": "SYNTHETIC-TOKENIZER-C",
        "gpu_type": GPU,
    }


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    index = []
    for concurrency, ttft, tpot, itl, out_tps in POINTS:
        name = f"perf_curve_c{concurrency}"
        payload = _point_payload(concurrency, ttft, tpot, itl, out_tps)
        (OUT / f"{name}.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        meta = f"""# 合成性能曲线样本（并发 {concurrency}）—— 仅用于算法验证
evidence_id: fixture_perf_curve_c{concurrency}
record_id: fixture_perf_curve_c{concurrency}
format_version: vllm-bench-serve-json/v1
performance_source_kind: synthetic_fixture
verification_status: synthetic_fixture

model_name: {MODEL}
hardware_model: {GPU}
deployment_gpu_count: {TP * DP}
tp_size: {TP}
dp_size: {DP}
framework: vllm
framework_version: {FRAMEWORK_VERSION}
model_revision: {REVISION}
dataset_name: {DATASET}
weight_precision: bf16
input_len_target: {ISL}
output_len_target: {OSL}
arrival_mode: unbounded

metric_definitions:
  ttft: "客户端 TTFT（含分位数；人工构造）"
  tpot: "请求级 TPOT（与 ITL 分开，本样本两者不等）"
  itl: "逐 token 间隔 ITL"

evidence:
  publisher: synthetic_fixture
  source_url: null
  source_version: "synthetic/perf-curve/v1"
  notes: >-
    人工构造的多并发点样本，数值由"吞吐 = token 数 / 时长"推出，用于验证容量曲线、
    估算器与 SLA 判定算法。**不得**用于任何正式 SLA 结论。
"""
        (OUT / f"{name}.yaml").write_text(meta, encoding="utf-8")
        index.append({"file": f"{name}.json", "metadata": f"{name}.yaml", "concurrency": concurrency})

    (OUT / "INDEX.json").write_text(
        json.dumps(
            {
                "notice": "合成性能曲线样本（synthetic_fixture），仅用于算法验证，不得用于 SLA 结论",
                "model_name": MODEL,
                "hardware_model": GPU,
                "model_revision": REVISION,
                "framework_version": FRAMEWORK_VERSION,
                "dataset_name": DATASET,
                "tp_size": TP,
                "dp_size": DP,
                "input_len_target": ISL,
                "output_len_target": OSL,
                "points": index,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"已生成 {len(POINTS)} 个并发点到 {OUT.relative_to(ROOT)}")
    for concurrency, ttft, tpot, itl, out_tps in POINTS:
        print(f"  c={concurrency:>3} TTFT p99={ttft:>6.1f}ms TPOT p99={tpot:>5.1f}ms 输出={out_tps:>6.1f} tok/s")


if __name__ == "__main__":
    main()
