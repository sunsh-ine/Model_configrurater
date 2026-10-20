"""S2/S3：一份需求 → 一次完整评估 → 一份方案文档。

这里**只做编排与 IO**，所有计算仍然调用现有 ``workflow.evaluate_plan()``。
界面（Streamlit）与命令行 Demo 共用本模块，避免出现"两套流程"。
"""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

from llm_capacity_pricing.reporting.plan_report import build_plan_report
from llm_capacity_pricing.reporting.proposal import build_deployment_proposal
from llm_capacity_pricing.schemas.plan import (
    PlanAdvanced,
    PlanBudgetInput,
    PlanCostInput,
    PlanRequest,
    PlanResourceAssumptions,
    PlanSimulationInput,
    PlanWorkload,
)
from llm_capacity_pricing.schemas.performance import SlaRequirement
from llm_capacity_pricing.services import (
    import_cost_catalog,
    import_hardware,
    import_models,
    init_db,
)
from llm_capacity_pricing.workflow import evaluate_plan

ROOT = Path(__file__).resolve().parents[3]
DEMO_OUT_ROOT = "data/exports/system_demo"

Mode = Literal["offline", "live", "core"]

#: 离线模式使用的归档模拟输出（仓库内，逐字段核对过）
OFFLINE_SIM = {
    "sweeper_csv": "docs/poc/aisimulate/artifacts/matched_pareto_vllm_bf16.csv",
    "native_prediction": "docs/poc/aisimulate/17_matched_native_result_tp8_conc32_bf16.json",
    "native_input": "docs/poc/aisimulate/16_matched_native_input_tp8_conc32_bf16.yaml",
    "exp_config": "docs/poc/aisimulate/artifacts/matched_exp_config_vllm_bf16.yaml",
    "per_ops_source": "docs/poc/aisimulate/artifacts/per_ops_source.json",
    "native_model_precision": "bf16",
    # 归档输入写的是 backend_version: current；槽位别名必须解析成实际版本
    "backend_version_aliases": {"current": "0.24.0"},
    "tool_version": "0.12.1",
}


@dataclass(frozen=True)
class DemoInputs:
    """用户在界面上填的东西（默认值即任务书里的示例需求）。"""

    model_id: str = "qwen3-32b"
    hardware_id: str = "nvidia-h100-sxm-80gb"
    server_id: str | None = "hgx-h100-8gpu"
    gpu_count: int = 8
    input_len: int = 8192
    output_len: int = 256
    concurrency: int = 32
    ttft_p99_ms_max: Decimal | None = Decimal("400")
    tpot_p99_ms_max: Decimal | None = Decimal("30")
    budget_amount: Decimal | None = Decimal("3000000")
    weight_precision: str | None = "bf16"
    framework: str | None = "vllm"
    framework_version: str | None = "0.24.0"
    tp_sizes: tuple[int, ...] = (2, 4, 8)
    mode: Mode = "offline"
    request_id: str = "system-demo"
    ranking: str = "throughput"
    top_n: int = 2
    cost_plan_file: str = "data/examples/phase4_cost_plan.example.yaml"
    cost_template_id: str = "syn-owned-3y"
    #: live 模式的工作目录（每次运行会在其下建 runs/<run_id>/）
    work_dir: str | None = None


@dataclass
class DemoOutcome:
    """一次 Demo 的产物。"""

    out_dir: Path
    db_path: Path
    result: Any
    proposal_md: str
    technical_md: str
    result_json: dict
    proposal_path: Path | None = None
    technical_path: Path | None = None
    json_path: Path | None = None
    stages: list[tuple[str, str, str]] = field(default_factory=list)


def build_plan_request(inputs: DemoInputs) -> PlanRequest:
    """把表单值转成 ``PlanRequest``（纯函数，便于测试与复用）。"""
    sla = None
    if inputs.ttft_p99_ms_max is not None or inputs.tpot_p99_ms_max is not None:
        sla = SlaRequirement(
            requirement_id=f"{inputs.request_id}-sla",
            ttft_p99_ms_max=inputs.ttft_p99_ms_max,
            tpot_p99_ms_max=inputs.tpot_p99_ms_max,
        )
    simulation: PlanSimulationInput | None = None
    if inputs.mode == "offline":
        simulation = PlanSimulationInput(**OFFLINE_SIM, top_n=inputs.top_n)
    elif inputs.mode == "live":
        simulation = PlanSimulationInput(
            enable_external=True,
            auto_search=True,
            work_dir=inputs.work_dir or "data/exports/system_demo_sim",
            native_model_precision=inputs.weight_precision,
            tool_version=None,
            top_n=inputs.top_n,
            ranking=inputs.ranking,
            backend_version_aliases={"current": "0.24.0"},
        )

    return PlanRequest(
        request_id=inputs.request_id,
        model_id=inputs.model_id,
        hardware_ids=[inputs.hardware_id],
        server_id=inputs.server_id,
        max_total_gpus=inputs.gpu_count,
        workload=PlanWorkload(
            input_len=inputs.input_len,
            output_len=inputs.output_len,
            concurrency=inputs.concurrency,
            dataset="random",
            arrival_mode="unbounded",
        ),
        resources=PlanResourceAssumptions(
            usable_fraction=Decimal("0.9"),
            usable_source_kind="synthetic_example",
            runtime_reserve_fraction=Decimal("0.1"),
        ),
        sla=sla,
        cost=PlanCostInput(
            plan_file=inputs.cost_plan_file, template_id=inputs.cost_template_id
        ),
        budget=(
            PlanBudgetInput(
                budget_id=f"{inputs.request_id}-budget",
                amount=inputs.budget_amount,
                kind="total_horizon",
            )
            if inputs.budget_amount is not None
            else None
        ),
        advanced=PlanAdvanced(
            tp_sizes=list(inputs.tp_sizes),
            dp_sizes=[1],
            weight_precision=inputs.weight_precision,
            framework=inputs.framework,
            framework_version=inputs.framework_version,
        ),
        simulation=simulation,
    )


def prepare_demo_db(
    db_path: str | Path, *, include_public_benchmarks: bool = True
) -> Path:
    """建一个**自包含**的演示库：模型 + 硬件 + 假设成本 + 公开 Benchmark。

    只写 ``db_path`` 指定的文件；调用方负责保证那是本次运行的独立目录。
    """
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise FileExistsError(
            f"{path} 已存在：Demo 不会覆盖已有数据库，请换一个运行目录"
        )
    init_db(str(path))
    import_models(ROOT / "data/models/model_manifest.yaml", str(path), root=ROOT)
    import_hardware(ROOT / "data/hardware/hardware_catalog.yaml", str(path))
    import_cost_catalog(
        ROOT / "data/examples/phase4_cost_inputs.example.yaml", str(path)
    )
    if include_public_benchmarks:
        result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts/import_public_benchmarks.py"),
                "--db",
                str(path),
                "--root",
                str(ROOT),
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=300,
        )
        if result.returncode != 0:
            raise RuntimeError(
                "公开 Benchmark 导入失败："
                + (result.stderr or result.stdout)[-500:]
            )
    return path


def new_run_dir(root: str | Path = DEMO_OUT_ROOT) -> Path:
    """每次运行一个独立目录（UTC 时间戳 + 序号），不覆盖历史结果。"""
    base = Path(root)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    candidate = base / stamp
    index = 1
    while candidate.exists():
        candidate = base / f"{stamp}_{index:02d}"
        index += 1
    return candidate


def run_demo(
    inputs: DemoInputs,
    *,
    out_dir: str | Path | None = None,
    write_files: bool = True,
) -> DemoOutcome:
    """执行一次完整演示：建库 → 评估 → 生成两份报告。"""
    run_dir = Path(out_dir) if out_dir is not None else new_run_dir()
    run_dir.mkdir(parents=True, exist_ok=True)
    db_path = run_dir / "demo.db"

    stages: list[tuple[str, str, str]] = []
    try:
        prepare_demo_db(db_path)
        stages.append(("数据准备", "ok", str(db_path)))
    except Exception as exc:  # noqa: BLE001
        stages.append(("数据准备", "error", f"{type(exc).__name__}: {exc}"))
        raise

    request = build_plan_request(inputs)
    result = evaluate_plan(request, db_path=str(db_path))
    stages.append(("统一评估", result.status, f"domains={result.domains}"))

    proposal = build_deployment_proposal(result)
    technical = build_plan_report(result)
    payload = result.model_dump(mode="json")

    outcome = DemoOutcome(
        out_dir=run_dir,
        db_path=db_path,
        result=result,
        proposal_md=proposal,
        technical_md=technical,
        result_json=payload,
        stages=stages,
    )
    if write_files:
        outcome.proposal_path = run_dir / "deployment_proposal.md"
        outcome.technical_path = run_dir / "technical_report.md"
        outcome.json_path = run_dir / "result.json"
        outcome.proposal_path.write_text(proposal, encoding="utf-8")
        outcome.technical_path.write_text(technical, encoding="utf-8")
        outcome.json_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return outcome


def summary_lines(outcome: DemoOutcome) -> list[str]:
    """给命令行/界面用的简短摘要（**不新造结论**，只复述结构化字段）。"""
    sim = getattr(outcome.result, "simulation", None)
    lines = [f"整体状态：{outcome.result.status}"]
    if sim is not None:
        lines.append(f"模拟状态：{getattr(sim, 'status', None)}")
        recommended = sim.recommended(getattr(sim, "ranking", "throughput"))
        best = sim.best_effort()
        if recommended is not None:
            lines.append(f"模拟推荐：{recommended.candidate_id}")
        elif best is not None:
            lines.append(f"未达标参考：{best.candidate_id}")
        else:
            lines.append("模拟推荐：无符合条件的候选")
    return lines
