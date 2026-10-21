"""S2：命令行一键 Demo 入口。

    python -m llm_capacity_pricing.app.demo_cli --mode offline

输出到独立目录，打印可直接复制的报告路径。**不做任何计算**，全部走 demo_service。
"""

from __future__ import annotations

import argparse
import sys
from decimal import Decimal
from pathlib import Path

from llm_capacity_pricing.app.demo_service import (
    DemoInputs,
    new_run_dir,
    run_demo,
    summary_lines,
)
from llm_capacity_pricing.app.scenarios import (
    SCENARIOS,
    get_scenario,
    scenario_ids,
)


def _dec(value: str | None) -> Decimal | None:
    return None if value is None else Decimal(str(value))


def _scenario_note(scenario, mode: str) -> str | None:
    """预设说明只描述离线归档；实时/无模拟模式不得沿用。"""
    return scenario.note if scenario is not None and mode == "offline" else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="推理部署方案一键 Demo")
    parser.add_argument("--out", default=None, help="结果目录（默认按时间戳新建）")
    parser.add_argument(
        "--scenario",
        choices=[*scenario_ids(), "custom"],
        default="custom",
        help="业务预设：strict/relaxed/cost；预设会覆盖其涉及的需求字段",
    )
    parser.add_argument(
        "--list-scenarios", action="store_true", help="列出预设场景后退出"
    )
    parser.add_argument(
        "--mode",
        choices=["offline", "live", "core"],
        default="offline",
        help="offline=读归档模拟结果；live=真调 AISimulate；core=不带模拟",
    )
    parser.add_argument("--model", default="qwen3-32b")
    parser.add_argument("--hardware", default="nvidia-h100-sxm-80gb")
    parser.add_argument("--server", default="hgx-h100-8gpu")
    parser.add_argument("--gpu", type=int, default=8, help="可用 GPU 上限")
    parser.add_argument("--isl", type=int, default=8192)
    parser.add_argument("--osl", type=int, default=256)
    parser.add_argument("--concurrency", type=int, default=32)
    parser.add_argument("--ttft", default="400", help="TTFT P99 上限 ms（none=不设）")
    parser.add_argument("--tpot", default="30", help="TPOT P99 上限 ms（none=不设）")
    parser.add_argument("--budget", default="3000000", help="预算金额（none=不设）")
    parser.add_argument("--precision", default="bf16")
    parser.add_argument("--framework", default="vllm")
    parser.add_argument("--framework-version", default="0.24.0")
    parser.add_argument("--top-n", type=int, default=2)
    parser.add_argument("--ranking", choices=["throughput", "cost"], default="throughput")
    args = parser.parse_args(argv)

    if args.list_scenarios:
        for scenario in SCENARIOS:
            print(f"{scenario.scenario_id}\t{scenario.label}\t{scenario.summary}")
        return 0

    def maybe(value: str) -> str | None:
        return None if value.lower() in ("none", "null", "") else value

    out_dir = args.out or str(new_run_dir())
    scenario = get_scenario(args.scenario) if args.scenario != "custom" else None
    if scenario is not None:
        preset = dict(scenario.inputs)
        preset["mode"] = args.mode
        # top_n 是模拟设置，不是需求字段：显式传入时以命令行为准
        preset["top_n"] = args.top_n
        if args.mode == "live":
            preset["work_dir"] = str(Path(out_dir) / "sim_work")
        inputs = DemoInputs(**preset)
    else:
        inputs = DemoInputs(
        model_id=args.model,
        hardware_id=args.hardware,
        server_id=maybe(args.server),
        gpu_count=args.gpu,
        input_len=args.isl,
        output_len=args.osl,
        concurrency=args.concurrency,
        ttft_p99_ms_max=_dec(maybe(args.ttft)),
        tpot_p99_ms_max=_dec(maybe(args.tpot)),
        budget_amount=_dec(maybe(args.budget)),
        weight_precision=maybe(args.precision),
        framework=maybe(args.framework),
        framework_version=maybe(args.framework_version),
        mode=args.mode,  # type: ignore[arg-type]
        top_n=args.top_n,
        ranking=args.ranking,
        work_dir=(str(Path(out_dir) / "sim_work")
                  if args.mode == "live" else None),
        )
    try:
        note = _scenario_note(scenario, args.mode)
        outcome = run_demo(
            inputs,
            out_dir=out_dir,
            demo_note=note,
            scenario_id=args.scenario if note else "custom",
        )
    except FileExistsError as exc:
        print(f"失败：{exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001
        print(f"失败：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    print(f"运行目录：{outcome.out_dir}")
    if scenario is not None:
        print(f"场景：{scenario.scenario_id}（{scenario.label}）")
        if args.mode != "offline":
            print(
                f"提示：预设说明只适用于离线归档；本次按 --mode {args.mode} 执行，"
                "报告不附带预设说明。"
            )
    for name, status, detail in outcome.stages:
        print(f"  [{status}] {name}：{detail}")
    for line in summary_lines(outcome):
        print(f"  {line}")
    print()
    print(f"方案文档：{outcome.proposal_path}")
    print(f"技术报告：{outcome.technical_path}")
    print(f"结构化结果：{outcome.json_path}")
    print()
    print("说明：模拟预测不是实测；成本为示例目录，正式报价保持禁用。")
    if note:
        print(f"演示说明：{note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
