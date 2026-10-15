#!/usr/bin/env python
"""生成**脱敏公开样本**：让公开仓库在没有任何内部数据的情况下也能跑通导入与计算测试。

产出（全部为人工构造的合成数据，**不是**真实业务数据）：

* ``data/examples/Token产能计算器-SAMPLE.xlsx`` —— 与真实工作簿同结构的三张表
  （GPU价格 / 模型价格 / 部署效能），数值为刻意设计的整数化示例；
* ``data/examples/public_sample_expected.json`` —— 上述样本的**手算**期望值，
  用于校验公式实现（期望值不是由引擎生成的，避免"自己测自己"）。

运行：``.venv/bin/python scripts/make_public_sample.py``

注意：真实业务数据**不得**写入本脚本或输出文件；内部 oracle 请放在
``data/private/``（已 gitignore）。
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from openpyxl import Workbook

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "data" / "examples"

SECONDS_PER_YEAR = 31_536_000
TOKEN_PRICE_UNIT = 1_000_000
TEN_THOUSAND = 10_000

#: 合成样本（示例数据，非真实价格）
GPU_ROWS = [("EXAMPLE-GPU-A", 8, "10.0"), ("EXAMPLE-GPU-B", 8, "5.0")]
TOKEN_ROWS = [
    ("EXAMPLE-MODEL-A", "1.0", "10.0", "50.0"),
    ("EXAMPLE-MODEL-B", "2.0", "8.0", "28.0"),
]
#: (GPU, 模型, 卡数, 并发, 单用户输出P90, 每卡输入, 每卡输出, 命中率, 策略)
BENCH_ROWS = [
    ("EXAMPLE-GPU-A", "EXAMPLE-MODEL-A", 8, 64, "25.0", "1000.0", "40.0", "0.5", "TP8DCP1EP1"),
    ("EXAMPLE-GPU-A", "EXAMPLE-MODEL-B", 8, 32, "18.5", "800.0", "25.0", "0.7", "TP8DCP1EP1"),
    ("EXAMPLE-GPU-B", "EXAMPLE-MODEL-B", 4, 16, "30.0", "500.0", "60.0", "0.0", "TP4DCP1EP1"),
]


def _legacy_values(in_tps: Decimal, out_tps: Decimal, hit: Decimal, p_hit, p_miss, p_out, cost):
    """按任务书 §4 的旧表公式手算（与引擎实现相互独立地写一遍）。"""
    blk_in = in_tps * SECONDS_PER_YEAR * (hit * p_hit + (1 - hit) * p_miss)
    inp = blk_in / TOKEN_PRICE_UNIT / TEN_THOUSAND
    out = out_tps * SECONDS_PER_YEAR * p_out / TOKEN_PRICE_UNIT / TEN_THOUSAND
    total = inp + out
    return inp, out, total, cost, (total / cost)


def build_workbook(path: Path) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "GPU价格"
    ws.append(["GPU型号", "卡数", "摊销或租金价格/卡/年"])
    for row in GPU_ROWS:
        ws.append(list(row))

    ws2 = wb.create_sheet("模型价格")
    ws2.append(["模型", "输入缓存命中/百万Token", "输入缓存未命中/百万Token", "输出/百万Token"])
    for row in TOKEN_ROWS:
        ws2.append(list(row))

    ws3 = wb.create_sheet("部署效能")
    ws3.append(
        [
            "GPU型号", "模型", "部署卡数", "并发数", "单用户输出Token/s（P90）",
            "每卡Input Token/s", "每卡Output Token/s", "总缓存命中率",
            "输入营收（万/年/卡）", "输出营收", "营收", "成本", "营收/成本", "部署策略", "数据来源",
        ]
    )
    prices = {m: (Decimal(c), Decimal(u), Decimal(o)) for m, c, u, o in TOKEN_ROWS}
    costs = {g: Decimal(c) for g, _n, c in GPU_ROWS}
    for gpu, model, gpus, conc, p90, in_tps, out_tps, hit, strategy in BENCH_ROWS:
        p_hit, p_miss, p_out = prices[model]
        inp, out, total, cost, ratio = _legacy_values(
            Decimal(in_tps), Decimal(out_tps), Decimal(hit), p_hit, p_miss, p_out, costs[gpu]
        )
        ws3.append(
            [
                gpu, model, gpus, conc, p90, in_tps, out_tps, hit,
                float(inp), float(out), float(total), float(cost), float(ratio),
                strategy, "SYNTHETIC_EXAMPLE",
            ]
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)


def build_expected() -> dict:
    prices = {m: (Decimal(c), Decimal(u), Decimal(o)) for m, c, u, o in TOKEN_ROWS}
    costs = {g: Decimal(c) for g, _n, c in GPU_ROWS}
    rows = []
    for gpu, model, gpus, conc, p90, in_tps, out_tps, hit, strategy in BENCH_ROWS:
        p_hit, p_miss, p_out = prices[model]
        inp, out, total, cost, ratio = _legacy_values(
            Decimal(in_tps), Decimal(out_tps), Decimal(hit), p_hit, p_miss, p_out, costs[gpu]
        )
        rows.append(
            {
                "gpu_model": gpu,
                "model_name": model,
                "deployment_gpu_count": gpus,
                "configured_concurrency": conc,
                "input_tps_per_gpu": in_tps,
                "output_tps_per_gpu": out_tps,
                "cache_hit_rate_raw": hit,
                "price_cached_input_per_1m": str(p_hit),
                "price_uncached_input_per_1m": str(p_miss),
                "price_output_per_1m": str(p_out),
                "annual_gpu_cost_10k": str(costs[gpu]),
                "expected_input_revenue_10k_per_gpu_year": str(inp),
                "expected_output_revenue_10k_per_gpu_year": str(out),
                "expected_total_revenue_10k_per_gpu_year": str(total),
                "expected_revenue_cost_ratio": str(ratio),
                "expected_breakeven_monetization_ratio": str(cost / total),
                "expected_cluster_gpu_count": gpus,
            }
        )
    return {
        "notice": (
            "本文件是**人工构造的脱敏示例**，数值不是真实业务数据，仅用于校验公式实现。"
            "期望值由脚本按任务书 §4 的公式独立手算一遍后写入，不由引擎生成。"
        ),
        "data_kind": "synthetic_public_sample",
        "workbook": "data/examples/Token产能计算器-SAMPLE.xlsx",
        "calculation_year_seconds": SECONDS_PER_YEAR,
        "currency": "UNCONFIRMED",
        "cost_scope": "gpu_cost_only",
        "rows": rows,
    }


def main() -> None:
    wb_path = OUT_DIR / "Token产能计算器-SAMPLE.xlsx"
    build_workbook(wb_path)
    expected = build_expected()
    (OUT_DIR / "public_sample_expected.json").write_text(
        json.dumps(expected, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"已生成: {wb_path.relative_to(ROOT)}")
    print(f"已生成: {(OUT_DIR / 'public_sample_expected.json').relative_to(ROOT)}")
    for row in expected["rows"]:
        print(
            f"  {row['model_name']:16s} 输入 {row['expected_input_revenue_10k_per_gpu_year']:>12s} "
            f"输出 {row['expected_output_revenue_10k_per_gpu_year']:>10s} "
            f"合计 {row['expected_total_revenue_10k_per_gpu_year']:>10s} "
            f"成本比 {row['expected_revenue_cost_ratio']}"
        )


if __name__ == "__main__":
    main()
