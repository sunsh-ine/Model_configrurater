"""命令行入口。任务书 §7 的 CLI 契约。

每个命令成功返回 0，失败返回非零并给出清晰错误消息。

    python -m llm_capacity_pricing.cli init-db --db data/local.db
    python -m llm_capacity_pricing.cli import-excel --file data/raw/*.xlsx --db data/local.db
    python -m llm_capacity_pricing.cli validate-import --db data/local.db
    python -m llm_capacity_pricing.cli calculate-all --mode legacy_full_load --db data/local.db --out data/exports/legacy_results.json
    python -m llm_capacity_pricing.cli scenario --record excel_部署效能_2 --utilization 0.30 --db data/local.db --out data/exports/scenario.json
    python -m llm_capacity_pricing.cli report --db data/local.db --out data/exports/overview.md
    python -m llm_capacity_pricing.cli import-sglang --file data/fixtures/sglang_synthetic.jsonl --metadata config/fixture_sglang.yaml
    python -m llm_capacity_pricing.cli import-vllm --file data/fixtures/vllm_synthetic.json --metadata config/fixture_vllm.yaml
"""

from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Optional

import typer

from llm_capacity_pricing import SECONDS_PER_YEAR
from llm_capacity_pricing.engines.pricing import InvalidUtilizationError, PricingError
from llm_capacity_pricing.importers.common import AdapterMetadataError, BenchmarkParseError
from llm_capacity_pricing.importers.excel_importer import ExcelImportError
from llm_capacity_pricing.services import (
    ServiceError,
    calculate_all,
    export_legacy_results,
    export_report,
    export_scenario_results,
    export_single_scenario,
    import_benchmark_file,
    import_excel_into_db,
    import_official_candidates,
    capacity_profiles_for,
    calibrate_performance_for,
    evaluate_sla_for,
    export_performance_report,
    import_hardware,
    import_models,
    import_performance_curve,
    import_performance_run,
    import_sglang_candidates,
    match_benchmarks_for,
    performance_inventory,
    rank_deployments_for,
    init_db,
    import_cost_catalog,
    cost_catalog_summary,
    import_commercial_confirmations,
    import_quote_approvals,
    record_sla_evidence_for,
    tco_for,
    revenue_for,
    quote_eligibility_for,
    phase4_decision_for,
    export_phase4_report,
    make_assumptions,
    official_coverage_summary,
    export_plan,
    plan_deployment_for,
    sizing_inventory,
    estimate_memory_for,
    validate_import,
    validate_results,
)
from llm_capacity_pricing.importers.official_manifest import OfficialManifestError
from llm_capacity_pricing.importers.sglang_cookbook import SglangCookbookError
from llm_capacity_pricing.schemas.performance import SlaPolicy

app = typer.Typer(
    add_completion=False,
    help="离线 LLM 部署基准数据与 Token 定价计算引擎（MVP）",
    no_args_is_help=True,
)

DEFAULT_DB = "data/local.db"


def _fail(message: str, code: int = 2) -> None:
    typer.secho(f"错误: {message}", fg=typer.colors.RED, err=True)
    raise typer.Exit(code=code)


def _echo_json(payload) -> None:
    typer.echo(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


def _parse_utilization(raw: str) -> Decimal:
    try:
        value = Decimal(str(raw).strip())
    except (InvalidOperation, ValueError):
        _fail(f"非法百分比/比例: {raw!r}（应为 0~1 之间的小数，如 0.30）")
        raise  # pragma: no cover
    if value < 0 or value > 1:
        _fail(f"非法取值范围: {value}（effective_monetization_ratio 必须在 [0, 1]）")
    return value


@app.command("init-db")
def init_db_cmd(
    db: str = typer.Option(DEFAULT_DB, "--db", help="SQLite 数据库路径"),
) -> None:
    """建库并创建全部表。"""
    info = init_db(db)
    typer.secho(f"已初始化数据库: {info['db_path']}", fg=typer.colors.GREEN)
    typer.echo(f"表: {', '.join(info['tables'])}")
    _echo_json(info["counts"])


@app.command("import-excel")
def import_excel_cmd(
    file: str = typer.Option(..., "--file", help="Excel 文件路径"),
    db: str = typer.Option(DEFAULT_DB, "--db", help="SQLite 数据库路径"),
) -> None:
    """导入 Excel 三张表（保留原始行号与证据缺口）。重复导入幂等。"""
    try:
        info = import_excel_into_db(file, db)
    except (ExcelImportError, FileNotFoundError) as exc:
        _fail(str(exc))
        return
    typer.secho(
        f"导入完成: {info['source_file']} (sha256 {info['raw_sha256'][:12]}…)",
        fg=typer.colors.GREEN,
    )
    typer.echo(f"工作表行数: {info['sheet_rows']}")
    _echo_json({"parsed": info["parsed"], "counts_before": info["counts_before"], "counts_after": info["counts_after"], "idempotent": info["idempotent"]})
    if info["unmatched_gpus"] or info["unmatched_models"]:
        typer.secho(
            f"警告: 未匹配价格的 GPU {info['unmatched_gpus']} / 模型 {info['unmatched_models']}",
            fg=typer.colors.YELLOW,
        )


@app.command("validate-import")
def validate_import_cmd(
    db: str = typer.Option(DEFAULT_DB, "--db", help="SQLite 数据库路径"),
) -> None:
    """校验导入结果（数据总量、可追溯性、缺失值语义、币种假设等）。"""
    report = validate_import(db)
    typer.echo(f"校验项: {len(report['checks'])}，通过 {report['passed']}，失败 {report['failed']}")
    for c in report["checks"]:
        mark = "PASS" if c["ok"] else "FAIL"
        color = typer.colors.GREEN if c["ok"] else typer.colors.RED
        typer.secho(f"  [{mark}] {c['name']}: {c['detail']}", fg=color)
    if not report["ok"]:
        raise typer.Exit(code=1)
    typer.secho("全部校验通过。", fg=typer.colors.GREEN)


@app.command("calculate-all")
def calculate_all_cmd(
    mode: str = typer.Option("legacy_full_load", "--mode", help="legacy_full_load | scenario"),
    db: str = typer.Option(DEFAULT_DB, "--db", help="SQLite 数据库路径"),
    out: Optional[str] = typer.Option(None, "--out", help="JSON 输出路径"),
    utilization: Optional[str] = typer.Option(
        None, "--utilization", help="scenario 模式下的变现比例 U ∈ [0,1]"
    ),
    year_seconds: int = typer.Option(
        SECONDS_PER_YEAR, "--year-seconds", help="年度秒数（默认 365 天）"
    ),
    include_test_data: bool = typer.Option(
        False,
        "--include-test-data/--exclude-test-data",
        help="是否把 synthetic_fixture 人工测试数据纳入计算（默认排除）",
    ),
) -> None:
    """按指定模式批量计算，并可选导出 JSON。

    默认口径：**排除** synthetic_fixture 测试数据，因此满载为 32 条、
    敏感性为 32×5=160 条。需要 36 条 / 180 条测试口径时显式加 `--include-test-data`。
    """
    util = _parse_utilization(utilization) if utilization is not None else None
    try:
        results, meta = calculate_all(
            db,
            mode=mode,
            utilization=util,
            year_seconds=year_seconds,
            persist=True,
            include_test_data=include_test_data,
        )
    except (ServiceError, PricingError) as exc:
        _fail(str(exc))
        return
    _echo_json(meta)
    if out:
        if mode == "scenario":
            info = export_scenario_results(
                db, out, include_test_data=include_test_data
            )
        else:
            info = export_legacy_results(db, out, include_test_data=include_test_data)
        typer.secho(f"已导出 {info['result_count']} 条结果 -> {info['out']}", fg=typer.colors.GREEN)
        if info.get("test_record_count"):
            typer.secho(
                f"注意: 其中含 {info['test_record_count']} 条人工测试数据（测试口径）",
                fg=typer.colors.YELLOW,
            )
    typer.echo(
        f"结果条数: {len(results)}（业务 {meta['business_record_count']}"
        f" + 测试 {meta['test_record_count']}）"
    )


@app.command("scenario")
def scenario_cmd(
    record: str = typer.Option(..., "--record", help="benchmark record_id，如 excel_部署效能_2"),
    utilization: str = typer.Option(..., "--utilization", help="变现比例 U ∈ [0,1]"),
    db: str = typer.Option(DEFAULT_DB, "--db", help="SQLite 数据库路径"),
    out: Optional[str] = typer.Option(None, "--out", help="JSON 输出路径"),
    include_test_data: bool = typer.Option(
        False,
        "--include-test-data/--exclude-test-data",
        help="目标记录为 synthetic_fixture 时必须显式启用",
    ),
) -> None:
    """对单条记录做变现比例情景分析（不是报价）。"""
    util = _parse_utilization(utilization)
    if out is None:
        out = f"data/exports/scenario_{record}.json"
    try:
        info = export_single_scenario(
            db,
            out,
            record_id=record,
            utilization=util,
            include_test_data=include_test_data,
        )
    except (ServiceError, PricingError, InvalidUtilizationError) as exc:
        _fail(str(exc))
        return
    _echo_json(info)
    typer.secho(f"情景结果已写入 {info['out']}", fg=typer.colors.GREEN)


@app.command("report")
def report_cmd(
    db: str = typer.Option(DEFAULT_DB, "--db", help="SQLite 数据库路径"),
    out: str = typer.Option("data/exports/overview.md", "--out", help="Markdown 输出路径"),
    include_test_data: bool = typer.Option(
        False,
        "--include-test-data/--exclude-test-data",
        help="报告业务统计是否纳入 synthetic_fixture 测试数据（默认排除）",
    ),
) -> None:
    """生成 Markdown 概览报告（含数据缺口、最大满载收入案例、成本敏感性）。"""
    info = export_report(db, out, include_test_data=include_test_data)
    _echo_json({k: v for k, v in info.items() if k != "out"})
    typer.secho(f"报告已写入 {info['out']}", fg=typer.colors.GREEN)


@app.command("import-sglang")
def import_sglang_cmd(
    file: str = typer.Option(..., "--file", help="SGLang bench_serving 结果文件 (JSONL)"),
    metadata: str = typer.Option(..., "--metadata", help="元数据 YAML 路径"),
    db: str = typer.Option(DEFAULT_DB, "--db", help="SQLite 数据库路径"),
) -> None:
    """导入 SGLang bench_serving 结果（版本化适配器）。"""
    try:
        info = import_benchmark_file("sglang", file, metadata, db)
    except (BenchmarkParseError, AdapterMetadataError, FileNotFoundError) as exc:
        _fail(str(exc))
        return
    _echo_json(info)
    typer.secho(f"已导入 {len(info['records'])} 条 SGLang 记录", fg=typer.colors.GREEN)


@app.command("import-vllm")
def import_vllm_cmd(
    file: str = typer.Option(..., "--file", help="vLLM bench serve 结果文件 (JSON)"),
    metadata: str = typer.Option(..., "--metadata", help="元数据 YAML 路径"),
    db: str = typer.Option(DEFAULT_DB, "--db", help="SQLite 数据库路径"),
) -> None:
    """导入 vLLM bench serve 结果（版本化适配器）。"""
    try:
        info = import_benchmark_file("vllm", file, metadata, db)
    except (BenchmarkParseError, AdapterMetadataError, FileNotFoundError) as exc:
        _fail(str(exc))
        return
    _echo_json(info)
    typer.secho(f"已导入 {len(info['records'])} 条 vLLM 记录", fg=typer.colors.GREEN)


@app.command("import-official")
def import_official_cmd(
    manifest: str = typer.Option(
        "data/sources/official_manifest.yaml", "--manifest", help="官方来源 manifest 路径"
    ),
    db: str = typer.Option(DEFAULT_DB, "--db", help="SQLite 数据库路径"),
    root: Optional[str] = typer.Option(
        None, "--root", help="证据文件相对路径的基准目录（默认取 manifest 所在项目根）"
    ),
) -> None:
    """导入官方 manifest 中的 partial_evidence 条目为**候选记录**。

    候选记录不进入性能数据库，也不参与经济性计算；仅登记证据与缺口台账。
    """
    try:
        info = import_official_candidates(manifest, db, root=root)
    except (OfficialManifestError, FileNotFoundError) as exc:
        _fail(str(exc))
        return
    typer.secho(f"已导入 {info['candidate_count']} 条官方候选证据（partial_evidence）", fg=typer.colors.GREEN)
    typer.echo(
        f"跳过 {info['skipped_count']} 条未入库条目"
        "（pending_verification / 非结果数据集 / 数值型来源由 import-sglang-candidates 处理）"
    )
    _echo_json(
        {
            "candidates": info["candidates"],
            "skipped": info["skipped"],
        }
    )


@app.command("import-sglang-candidates")
def import_sglang_candidates_cmd(
    file: str = typer.Option(
        "data/incoming/sglang_official_benchmark_candidates_20261015.json",
        "--file",
        help="官方数值候选 JSON（只读输入）",
    ),
    db: str = typer.Option(DEFAULT_DB, "--db", help="SQLite 数据库路径"),
    root: Optional[str] = typer.Option(
        None, "--root", help="项目根目录（证据文件相对路径的基准，默认取输入文件的上级目录）"
    ),
) -> None:
    """导入官方 SGLang Cookbook **数值型**候选，并逐字段核对存档原文。

    候选保持 ``partial_evidence``，不进入性能数据库，也不参与经济性计算。
    """
    try:
        info = import_sglang_candidates(file, db, root=root)
    except (SglangCookbookError, FileNotFoundError) as exc:
        _fail(str(exc))
        return
    typer.secho(
        f"已导入 {info['imported_count']} 条官方数值候选（partial_evidence）",
        fg=typer.colors.GREEN,
    )
    typer.echo(f"按来源: {info['by_source']}")
    typer.echo(
        f"原文逐字段核对通过: {info['verified_against_source']}/{info['imported_count']}"
        f"（不一致 {info['imported_count'] - info['verified_against_source']} 条）"
    )
    for sid, meta in info["sources"].items():
        if not meta.get("present_in_input"):
            continue
        typer.echo(
            f"  {sid}: sha256匹配={meta.get('sha256_matches_spec')} "
            f"git_blob匹配={meta.get('git_blob_sha_matches_declared')} "
            f"原文未列出但有结果块的章节={meta.get('unlisted_sections_with_results')}"
        )
    _echo_json({"candidate_ids": info["candidate_ids"], "per_record": info["per_record"]})


@app.command("official-coverage")
def official_coverage_cmd(
    db: str = typer.Option(DEFAULT_DB, "--db", help="SQLite 数据库路径"),
) -> None:
    """查看官方 Benchmark 覆盖情况（候选数 / 已核实数 / 门禁统计）。"""
    _echo_json(official_coverage_summary(db))


@app.command("validate-results")
def validate_results_cmd(
    db: str = typer.Option(DEFAULT_DB, "--db", help="SQLite 数据库路径"),
    include_test_data: bool = typer.Option(
        False,
        "--include-test-data/--exclude-test-data",
        help="是否把 synthetic_fixture 一并纳入校验（默认只校验业务口径）",
    ),
) -> None:
    """校验计算结果可追溯到性能记录 / 硬件成本 / Token 单价，以及 SLA 口径。"""
    report = validate_results(db, include_test_data=include_test_data)
    typer.echo(
        f"结果条数: {report['result_count']}（含测试数据: {report['include_test_data']}）"
    )
    typer.echo(f"校验项: {len(report['checks'])}，通过 {report['passed']}，失败 {report['failed']}")
    for c in report["checks"]:
        mark = "PASS" if c["ok"] else "FAIL"
        color = typer.colors.GREEN if c["ok"] else typer.colors.RED
        typer.secho(f"  [{mark}] {c['name']}: {c['detail']}", fg=color)
    if not report["ok"]:
        raise typer.Exit(code=1)
    typer.secho("全部校验通过。", fg=typer.colors.GREEN)


@app.command("list-records")
def list_records_cmd(
    db: str = typer.Option(DEFAULT_DB, "--db", help="SQLite 数据库路径"),
) -> None:
    """列出全部 benchmark record_id 及其来源类型。"""
    from llm_capacity_pricing.services import open_db

    db_obj, repo = open_db(db)
    try:
        with db_obj.session() as session:
            rows = [
                {
                    "record_id": r.record_id,
                    "model_name": r.model_name,
                    "hardware_model": r.hardware_model,
                    "deployment_gpu_count": r.deployment_gpu_count,
                    "performance_source_kind": r.performance_source_kind,
                    "verification_status": r.verification_status,
                }
                for r in repo.list_benchmarks(session)
            ]
    finally:
        db_obj.dispose()
    _echo_json(rows)



# ---------------------------------------------------------------------------
# 第二阶段：模型 / 硬件 / 资源规划
# ---------------------------------------------------------------------------


def _parse_int_list(raw: str) -> list[int]:
    try:
        return [int(x) for x in raw.replace(" ", "").split(",") if x]
    except ValueError as exc:  # pragma: no cover - typer 层已校验字符串
        raise typer.BadParameter(f"无法解析整数列表: {raw}") from exc


@app.command("import-models")
def import_models_cmd(
    manifest: str = typer.Option(
        "data/models/model_manifest.yaml", "--manifest", help="模型清单路径"
    ),
    db: str = typer.Option(DEFAULT_DB, "--db", help="SQLite 数据库路径"),
    root: Optional[str] = typer.Option(None, "--root", help="项目根目录"),
    precision: str = typer.Option("bf16", "--precision", help="本次部署请求的权重精度"),
) -> None:
    """导入模型结构与权重元数据（M13）。归档文件哈希不符会直接失败。"""
    try:
        info = import_models(manifest, db, root=root, deployment_precision=precision)
    except Exception as exc:
        _fail(str(exc))
        return
    typer.secho(f"已导入 {info['model_count']} 个模型结构（精度 {precision}）", fg=typer.colors.GREEN)
    typer.echo(f"解析成功: {info['parsed_count']}；不支持结构: {info['unsupported_count']}")
    _echo_json(info["models"])


@app.command("import-hardware")
def import_hardware_cmd(
    catalog: str = typer.Option(
        "data/hardware/hardware_catalog.yaml", "--catalog", help="硬件目录路径"
    ),
    db: str = typer.Option(DEFAULT_DB, "--db", help="SQLite 数据库路径"),
) -> None:
    """导入 GPU SKU 规格与服务器形态（M14）。"""
    try:
        info = import_hardware(catalog, db)
    except Exception as exc:
        _fail(str(exc))
        return
    typer.secho(
        f"已导入 {info['hardware_count']} 个 GPU SKU、{info['server_count']} 个服务器形态",
        fg=typer.colors.GREEN,
    )
    typer.echo(f"本轮支持: {info['supported_in_phase2']}")
    typer.echo(f"待核实: {info['pending_verification']}")
    _echo_json(info["hardware"])


@app.command("sizing-inventory")
def sizing_inventory_cmd(
    db: str = typer.Option(DEFAULT_DB, "--db", help="SQLite 数据库路径"),
) -> None:
    """查看第二阶段的模型结构与硬件规格库存。"""
    _echo_json(sizing_inventory(db))


@app.command("estimate-memory")
def estimate_memory_cmd(
    model: str = typer.Option(..., "--model", help="模型 ID（如 qwen3-32b）"),
    hardware: str = typer.Option(..., "--hardware", help="硬件 ID（如 nvidia-h100-sxm-80gb）"),
    server: Optional[str] = typer.Option(None, "--server", help="服务器形态 ID（可选）"),
    tp: int = typer.Option(1, "--tp", help="张量并行度"),
    dp: int = typer.Option(1, "--dp", help="数据并行副本数"),
    ctx: int = typer.Option(..., "--context-length", help="每请求上下文长度（token）"),
    concurrency: int = typer.Option(..., "--concurrency", help="全局并发请求数"),
    block_size: Optional[int] = typer.Option(None, "--block-size", help="Paged KV 块大小（token）"),
    usable_bytes: Optional[int] = typer.Option(None, "--usable-memory-bytes", help="单卡可用显存（实测口径）"),
    usable_fraction: Optional[str] = typer.Option(None, "--usable-fraction", help="或按标称容量的比例（假设）"),
    usable_source: Optional[str] = typer.Option(None, "--usable-source", help="该可用显存数值的来源描述"),
    usable_source_kind: Optional[str] = typer.Option(
        None,
        "--usable-source-kind",
        help="可用显存来源性质：measured / operator_estimate / synthetic_example（给出可用显存时必填）",
    ),
    max_context_override: Optional[int] = typer.Option(
        None, "--max-context-override", help="经过验证的上下文扩展上限（超过模型声明上限时才需要）"
    ),
    max_context_override_source: Optional[str] = typer.Option(
        None, "--max-context-override-source", help="上下文扩展的验证来源"
    ),
    runtime_reserve: Optional[str] = typer.Option(None, "--runtime-reserve-fraction", help="运行时合并预留比例（假设）"),
    runtime_source: Optional[str] = typer.Option(None, "--runtime-source", help="运行时预留的来源"),
    kv_precision: str = typer.Option("bf16", "--kv-precision", help="KV cache 精度"),
    db: str = typer.Option(DEFAULT_DB, "--db", help="SQLite 数据库路径"),
) -> None:
    """估算单个 TP/DP 配置的显存构成（M15+M16）。"""
    assumptions = make_assumptions(
        per_gpu_usable_bytes=usable_bytes,
        usable_fraction_of_nominal=Decimal(usable_fraction) if usable_fraction else None,
        usable_memory_source=usable_source,
        usable_memory_source_kind=usable_source_kind,
        max_context_override=max_context_override,
        max_context_override_source=max_context_override_source,
        runtime_reserve_fraction=Decimal(runtime_reserve) if runtime_reserve else None,
        runtime_reserve_source=runtime_source,
        block_size_tokens=block_size,
        global_concurrency=concurrency,
        context_length_per_request=ctx,
    )
    try:
        info = estimate_memory_for(
            db,
            model_id=model,
            hardware_id=hardware,
            server_id=server,
            tp_size=tp,
            dp_size=dp,
            assumptions=assumptions,
            kv_precision=kv_precision,
        )
    except Exception as exc:
        _fail(str(exc))
        return
    _echo_json(info)


@app.command("plan-deployment")
def plan_deployment_cmd(
    model: str = typer.Option(..., "--model", help="模型 ID"),
    hardware: str = typer.Option(..., "--hardware", help="硬件 ID"),
    server: Optional[str] = typer.Option(None, "--server", help="服务器形态 ID（可选）"),
    ctx: int = typer.Option(..., "--context-length", help="每请求上下文长度（token）"),
    concurrency: int = typer.Option(..., "--concurrency", help="全局并发请求数"),
    tp_sizes: str = typer.Option("1,2,4,8", "--tp-sizes", help="枚举的 TP 取值"),
    dp_sizes: str = typer.Option("1,2,4,8", "--dp-sizes", help="枚举的 DP 取值"),
    block_size: Optional[int] = typer.Option(None, "--block-size", help="Paged KV 块大小"),
    usable_bytes: Optional[int] = typer.Option(None, "--usable-memory-bytes", help="单卡可用显存"),
    usable_fraction: Optional[str] = typer.Option(None, "--usable-fraction", help="按标称容量的比例（假设）"),
    usable_source: Optional[str] = typer.Option(None, "--usable-source", help="可用显存数值来源描述"),
    usable_source_kind: Optional[str] = typer.Option(
        None,
        "--usable-source-kind",
        help="可用显存来源性质：measured / operator_estimate / synthetic_example（给出可用显存时必填）",
    ),
    max_context_override: Optional[int] = typer.Option(
        None, "--max-context-override", help="经过验证的上下文扩展上限"
    ),
    max_context_override_source: Optional[str] = typer.Option(
        None, "--max-context-override-source", help="上下文扩展的验证来源"
    ),
    runtime_reserve: Optional[str] = typer.Option(None, "--runtime-reserve-fraction", help="运行时合并预留比例"),
    runtime_source: Optional[str] = typer.Option(None, "--runtime-source", help="运行时预留来源"),
    kv_precision: str = typer.Option("bf16", "--kv-precision", help="KV cache 精度"),
    out: Optional[str] = typer.Option(None, "--out", help="导出 JSON 路径"),
    report: Optional[str] = typer.Option(None, "--report", help="导出 Markdown 报告路径"),
    db: str = typer.Option(DEFAULT_DB, "--db", help="SQLite 数据库路径"),
) -> None:
    """枚举 TP/DP 组合并给出静态可行性（M17+M18）。"""
    assumptions = make_assumptions(
        per_gpu_usable_bytes=usable_bytes,
        usable_fraction_of_nominal=Decimal(usable_fraction) if usable_fraction else None,
        usable_memory_source=usable_source,
        usable_memory_source_kind=usable_source_kind,
        max_context_override=max_context_override,
        max_context_override_source=max_context_override_source,
        runtime_reserve_fraction=Decimal(runtime_reserve) if runtime_reserve else None,
        runtime_reserve_source=runtime_source,
        block_size_tokens=block_size,
        global_concurrency=concurrency,
        context_length_per_request=ctx,
        tp_sizes=_parse_int_list(tp_sizes),
        dp_sizes=_parse_int_list(dp_sizes),
    )
    common = dict(
        model_id=model,
        hardware_id=hardware,
        server_id=server,
        assumptions=assumptions,
        kv_precision=kv_precision,
    )
    try:
        if out or report:
            info = export_plan(db, out or "data/exports/deployment_plan.json", report_path=report, **common)
            typer.secho(f"规划结果已写入 {info['plan_json']}", fg=typer.colors.GREEN)
            if info.get("report_markdown"):
                typer.echo(f"报告已写入 {info['report_markdown']}（{info['report_bytes']} 字节）")
            typer.echo(f"状态分布: {info['status_counts']}")
            return
        plan = plan_deployment_for(db, **common)
        typer.echo(f"状态分布: {plan['status_counts']}")
        for c in plan["candidates"]:
            m = c.memory
            weight = f"{m.weight.per_rank_bytes:,}" if m and m.weight.per_rank_bytes else "—"
            kv = f"{m.kv.physical_bytes:,}" if m and m.kv.physical_bytes else "—"
            headroom = f"{m.headroom_bytes:,}" if m and m.headroom_bytes is not None else "—"
            typer.echo(
                f"  TP{c.tp_size}×DP{c.dp_size}={c.gpu_count:2d}卡 [{c.feasibility.status}] "
                f"权重/卡={weight} KV/卡={kv} 余量={headroom}"
            )
    except Exception as exc:
        _fail(str(exc))
        return



# ---------------------------------------------------------------------------
# 第三阶段：性能评估与 SLA
# ---------------------------------------------------------------------------


@app.command("import-performance")
def import_performance_cmd(
    file: Optional[str] = typer.Option(None, "--file", help="单个压测结果文件（SGLang JSONL / vLLM JSON）"),
    metadata: Optional[str] = typer.Option(None, "--metadata", help="对应的适配器元数据 YAML"),
    curve_index: Optional[str] = typer.Option(None, "--curve-index", help="或：曲线样本 INDEX.json（批量导入）"),
    kind: str = typer.Option("vllm", "--kind", help="来源类型：vllm / sglang"),
    root: Optional[str] = typer.Option(None, "--root", help="项目根目录"),
    db: str = typer.Option(DEFAULT_DB, "--db", help="SQLite 数据库路径"),
) -> None:
    """导入性能压测记录（M20）。离线可用，不需要 GPU。"""
    try:
        if curve_index:
            info = import_performance_curve(curve_index, db, root=root, kind=kind)
        elif file and metadata:
            info = import_performance_run(file, metadata, db, kind=kind)
        else:
            _fail("请给出 --file/--metadata 或 --curve-index")
            return
    except Exception as exc:
        _fail(str(exc))
        return
    typer.secho(f"已导入 {info['run_count']} 条性能记录", fg=typer.colors.GREEN)
    typer.echo(f"可信等级: {info['data_trust']}")
    if info.get("notice"):
        typer.echo(f"说明: {info['notice']}")
    _echo_json({"run_ids": info["run_ids"], "skipped": info.get("skipped", [])})


@app.command("match-benchmark")
def match_benchmark_cmd(
    model: str = typer.Option(..., "--model", help="目标模型名（如 Qwen/Qwen3-32B）"),
    hardware: Optional[str] = typer.Option(None, "--hardware", help="目标硬件型号"),
    gpu_count: Optional[int] = typer.Option(None, "--gpu-count", help="目标 GPU 数量"),
    tp: Optional[int] = typer.Option(None, "--tp", help="目标 TP"),
    dp: Optional[int] = typer.Option(None, "--dp", help="目标 DP"),
    input_len: Optional[int] = typer.Option(None, "--input-len", help="目标输入长度"),
    output_len: Optional[int] = typer.Option(None, "--output-len", help="目标输出长度"),
    dataset: Optional[str] = typer.Option(None, "--dataset", help="目标数据集"),
    concurrency: Optional[int] = typer.Option(None, "--concurrency", help="目标并发"),
    require_metrics: str = typer.Option("m.latency.ttft.p99,m.latency.tpot.p99", "--require-metrics", help="必需指标口径 ID"),
    allow_synthetic: bool = typer.Option(False, "--allow-synthetic", help="是否允许 synthetic（仅算法验证）"),
    db: str = typer.Option(DEFAULT_DB, "--db", help="SQLite 数据库路径"),
) -> None:
    """按配置与负载匹配已有 Benchmark（M19）。"""
    try:
        info = match_benchmarks_for(
            db,
            model_name=model,
            hardware_model=hardware,
            gpu_count=gpu_count,
            tp_size=tp,
            dp_size=dp,
            input_len_target=input_len,
            output_len_target=output_len,
            dataset_name=dataset,
            configured_concurrency=concurrency,
            required_metrics=[m.strip() for m in require_metrics.split(",") if m.strip()],
            require_measured=not allow_synthetic,
            allow_synthetic=allow_synthetic,
        )
    except Exception as exc:
        _fail(str(exc))
        return
    typer.echo(f"匹配等级分布: {info['level_counts']}")
    typer.echo(f"非拒绝项: {len(info['matched'])} 条")
    for item in info["matched"][:5]:
        typer.echo(f"  [{item['level']}] {item['source_ref']} usable_for={item['usable_for']}")
    _echo_json(info)


@app.command("capacity-profile")
def capacity_profile_cmd(
    target_concurrency: Optional[int] = typer.Option(None, "--target-concurrency", help="目标并发（检查覆盖）"),
    min_duration_s: Optional[str] = typer.Option(None, "--min-duration-s", help="可持续容量的最短测试时长（秒）"),
    exclude_synthetic: bool = typer.Option(False, "--exclude-synthetic", help="排除 synthetic 数据"),
    db: str = typer.Option(DEFAULT_DB, "--db", help="SQLite 数据库路径"),
) -> None:
    """并发容量与性能曲线分析（M21）。"""
    try:
        info = capacity_profiles_for(
            db, target_concurrency=target_concurrency, min_duration_s=min_duration_s,
            exclude_synthetic=exclude_synthetic,
        )
    except Exception as exc:
        _fail(str(exc))
        return
    typer.echo(f"记录 {info['run_count']} 条，分成 {info['group_count']} 个配置组")
    for p in info["profiles"]:
        best = p.get("best_observed_point") or {}
        typer.echo(
            f"  组 {p['group_key'][:40]}… 点数={len(p['points'])} 覆盖={p['coverage_status']} "
            f"峰值并发={best.get('configured_concurrency')} 输出={best.get('output_throughput_tok_s')} "
            f"可持续容量={p.get('maximum_sustainable_capacity')}"
        )
    _echo_json(info)


@app.command("calibrate-performance")
def calibrate_performance_cmd(
    metric: str = typer.Option("output_throughput_tok_s", "--metric", help="要估算的指标"),
    concurrency: int = typer.Option(..., "--concurrency", help="目标并发"),
    group_key: Optional[str] = typer.Option(None, "--group-key", help="指定配置组（默认取第一条所在组）"),
    allow_synthetic: bool = typer.Option(
        False, "--allow-synthetic", help="仅算法验证：允许 synthetic 数据参与估算"
    ),
    db: str = typer.Option(DEFAULT_DB, "--db", help="SQLite 数据库路径"),
) -> None:
    """性能估算：精确查询 / 范围内插值 / 不可预测（M22）。默认拒绝 synthetic。"""
    try:
        info = calibrate_performance_for(
            db,
            metric=metric,
            target_concurrency=concurrency,
            group_key=group_key,
            allow_synthetic=allow_synthetic,
        )
    except Exception as exc:
        _fail(str(exc))
        return
    _echo_json(info)


@app.command("evaluate-sla")
def evaluate_sla_cmd(
    requirement: str = typer.Option(..., "--requirement", help="SLA 需求 JSON（requirement_id/ttft_p99_ms_max/...）"),
    run_id: Optional[str] = typer.Option(None, "--run-id", help="指定用于判定的实测记录"),
    model: Optional[str] = typer.Option(None, "--model", help="目标模型（用于匹配判定）"),
    model_revision: Optional[str] = typer.Option(None, "--model-revision", help="目标模型 revision（严格匹配必需）"),
    framework: Optional[str] = typer.Option(None, "--framework", help="目标框架（sglang/vllm）"),
    framework_version: Optional[str] = typer.Option(None, "--framework-version", help="目标框架版本（严格匹配必需）"),
    precision: Optional[str] = typer.Option(None, "--precision", help="目标权重精度"),
    dataset: Optional[str] = typer.Option(None, "--dataset", help="目标数据集"),
    arrival_mode: Optional[str] = typer.Option(None, "--arrival-mode", help="到达方式：unbounded/request_rate/max_concurrency"),
    concurrency: Optional[int] = typer.Option(None, "--concurrency", help="目标配置并发（严格匹配必需）"),
    hardware: Optional[str] = typer.Option(None, "--hardware", help="目标硬件"),
    gpu_count: Optional[int] = typer.Option(None, "--gpu-count", help="目标 GPU 数"),
    tp: Optional[int] = typer.Option(None, "--tp", help="目标 TP"),
    dp: Optional[int] = typer.Option(None, "--dp", help="目标 DP"),
    input_len: Optional[int] = typer.Option(None, "--input-len", help="目标输入长度"),
    output_len: Optional[int] = typer.Option(None, "--output-len", help="目标输出长度"),
    policy_id: str = typer.Option("default", "--policy-id", help="策略标识（default=生产策略）"),
    allow_synthetic: bool = typer.Option(False, "--allow-synthetic", help="仅算法验证时开启"),
    db: str = typer.Option(DEFAULT_DB, "--db", help="SQLite 数据库路径"),
) -> None:
    """SLA 评估（M23）。默认策略拒绝 synthetic 与不匹配数据。"""
    try:
        payload = json.loads(requirement)
    except json.JSONDecodeError as exc:
        _fail(f"--requirement 不是合法 JSON: {exc}")
        return
    policy = SlaPolicy(
        policy_id=policy_id,
        allow_synthetic=allow_synthetic,
        accepted_trust_levels=(
            ["measured_internal", "measured_vendor_published", "synthetic_fixture"]
            if allow_synthetic
            else ["measured_internal", "measured_vendor_published"]
        ),
    )
    try:
        info = evaluate_sla_for(
            db,
            requirement=payload,
            policy=policy,
            run_id=run_id,
            model_name=model,
            model_revision=model_revision,
            hardware_model=hardware,
            gpu_count=gpu_count,
            tp_size=tp,
            dp_size=dp,
            weight_precision=precision,
            framework=framework,
            framework_version=framework_version,
            input_len_target=input_len,
            output_len_target=output_len,
            dataset_name=dataset,
            arrival_mode=arrival_mode,
            configured_concurrency=concurrency,
            allow_synthetic=allow_synthetic,
        )
    except Exception as exc:
        _fail(str(exc))
        return
    typer.secho(f"SLA 状态: {info['status']}", fg=typer.colors.GREEN if info["status"] == "observed_pass" else typer.colors.YELLOW)
    if info.get("algorithm_validation_only"):
        typer.secho("  注意：该结论仅用于算法验证（synthetic 数据），不得对外使用", fg=typer.colors.YELLOW)
    _echo_json(info)


@app.command("rank-deployments")
def rank_deployments_cmd(
    model: str = typer.Option(..., "--model", help="模型 ID（如 qwen3-32b）"),
    hardware: str = typer.Option(..., "--hardware", help="硬件 ID（如 nvidia-h100-sxm-80gb）"),
    requirement: str = typer.Option(..., "--requirement", help="SLA 需求 JSON"),
    server: Optional[str] = typer.Option(None, "--server", help="服务器形态 ID"),
    ctx: int = typer.Option(..., "--context-length", help="上下文长度"),
    concurrency: int = typer.Option(..., "--concurrency", help="全局并发"),
    tp_sizes: str = typer.Option("1,2,4,8", "--tp-sizes", help="枚举 TP"),
    dp_sizes: str = typer.Option("1,2", "--dp-sizes", help="枚举 DP"),
    usable_fraction: Optional[str] = typer.Option(None, "--usable-fraction", help="可用显存比例（假设）"),
    usable_source_kind: Optional[str] = typer.Option(None, "--usable-source-kind", help="来源性质"),
    runtime_reserve: Optional[str] = typer.Option(None, "--runtime-reserve-fraction", help="运行时预留比例"),
    output_len: Optional[int] = typer.Option(None, "--output-len", help="目标输出长度"),
    dataset: Optional[str] = typer.Option(None, "--dataset", help="目标数据集"),
    model_revision: Optional[str] = typer.Option(None, "--model-revision", help="目标模型 revision"),
    framework: Optional[str] = typer.Option(None, "--framework", help="目标框架"),
    framework_version: Optional[str] = typer.Option(None, "--framework-version", help="目标框架版本"),
    db: str = typer.Option(DEFAULT_DB, "--db", help="SQLite 数据库路径"),
) -> None:
    """部署候选性能比较与排序（M24）：静态筛选 + 性能证据。"""
    try:
        sla_payload = json.loads(requirement)
    except json.JSONDecodeError as exc:
        _fail(f"--requirement 不是合法 JSON: {exc}")
        return
    assumptions = make_assumptions(
        usable_fraction_of_nominal=Decimal(usable_fraction) if usable_fraction else None,
        usable_memory_source="CLI 输入（见 --usable-source-kind）",
        usable_memory_source_kind=usable_source_kind,
        runtime_reserve_fraction=Decimal(runtime_reserve) if runtime_reserve else None,
        runtime_reserve_source="CLI 输入",
        global_concurrency=concurrency,
        context_length_per_request=ctx,
        tp_sizes=_parse_int_list(tp_sizes),
        dp_sizes=_parse_int_list(dp_sizes),
    )
    try:
        info = rank_deployments_for(
            db,
            model_id=model,
            hardware_id=hardware,
            requirement=sla_payload,
            assumptions=assumptions,
            server_id=server,
            input_len_target=ctx,
            output_len_target=output_len,
            dataset_name=dataset,
            model_revision=model_revision,
            framework=framework,
            framework_version=framework_version,
        )
    except Exception as exc:
        _fail(str(exc))
        return
    buckets: dict[str, int] = {}
    for entry in info["entries"]:
        buckets[entry["rank_bucket"]] = buckets.get(entry["rank_bucket"], 0) + 1
    typer.secho(f"分桶: {buckets}", fg=typer.colors.GREEN)
    if info.get("blocker"):
        typer.secho(f"阻塞: {info['blocker']}", fg=typer.colors.YELLOW)
    _echo_json(info)


@app.command("performance-report")
def performance_report_cmd(
    out: str = typer.Option("data/exports/performance_report.md", "--out", help="Markdown 报告路径"),
    target_concurrency: Optional[int] = typer.Option(None, "--target-concurrency", help="目标并发"),
    db: str = typer.Option(DEFAULT_DB, "--db", help="SQLite 数据库路径"),
) -> None:
    """生成第三阶段性能评估报告（M25）。"""
    inv = performance_inventory(db)
    profiles = capacity_profiles_for(db, target_concurrency=target_concurrency)
    info = export_performance_report(db, out, profiles=profiles, inventory=inv)
    typer.secho(f"报告已写入 {info['report']}（{info['bytes']} 字节）", fg=typer.colors.GREEN)
    _echo_json({"inventory": inv, "group_count": profiles["group_count"]})


# --------------------------------------------------------------------------
# 第四阶段（M26–M33）：TCO、收益情景、报价门禁与部署决策
# --------------------------------------------------------------------------


@app.command("import-cost-catalog")
def import_cost_catalog_cmd(
    file: str = typer.Option(..., "--file", help="成本目录 YAML（成本项 + 计费规则）"),
    db: str = typer.Option(DEFAULT_DB, "--db", help="SQLite 数据库路径"),
) -> None:
    """M27：导入成本目录与计费规则（幂等）。"""
    info = import_cost_catalog(file, db)
    typer.secho(
        f"已导入 {info['cost_item_count']} 个成本项、{info['billing_rule_count']} 条计费规则",
        fg=typer.colors.GREEN,
    )
    if info["synthetic_item_count"]:
        typer.secho(
            f"注意：其中 {info['synthetic_item_count']} 项为合成示例价格，不得用于正式报价",
            fg=typer.colors.YELLOW,
        )
    _echo_json(
        {
            "cost_item_count": info["cost_item_count"],
            "billing_rule_count": info["billing_rule_count"],
            "synthetic_item_count": info["synthetic_item_count"],
            "currency": info["currency"],
            "currency_status": info["currency_status"],
        }
    )


@app.command("cost-catalog")
def cost_catalog_cmd(
    db: str = typer.Option(DEFAULT_DB, "--db", help="SQLite 数据库路径"),
) -> None:
    """查看成本目录库存（类别 / 口径 / 状态 / 缺价项）。"""
    info = cost_catalog_summary(db)
    typer.secho(
        f"成本项 {info['item_count']} 项，计费规则 {info['billing_rule_count']} 条，"
        f"人工确认 {info['confirmation_count']} 条",
        fg=typer.colors.GREEN,
    )
    if info["items_missing_price"]:
        typer.secho(
            f"缺单价（不得按 0 处理）：{info['items_missing_price']}", fg=typer.colors.YELLOW
        )
    _echo_json(info)


@app.command("import-commercial-confirmations")
def import_commercial_confirmations_cmd(
    file: str = typer.Option(..., "--file", help="人工商务确认 YAML"),
    db: str = typer.Option(DEFAULT_DB, "--db", help="SQLite 数据库路径"),
) -> None:
    """M30：导入人工商务确认记录（本命令只做持久化，不生成确认）。"""
    info = import_commercial_confirmations(file, db)
    typer.secho(
        f"已导入 {info['confirmation_count']} 条商务确认记录", fg=typer.colors.GREEN
    )
    _echo_json(info)


@app.command("tco")
def tco_cmd(
    plan_file: str = typer.Option(..., "--plan-file", help="成本方案模板 YAML"),
    template: str = typer.Option(..., "--template", help="模板 ID"),
    candidate_id: str = typer.Option(..., "--candidate", help="候选 ID（标签，用于追溯）"),
    hardware: str = typer.Option(..., "--hardware", help="硬件 ID"),
    gpu_count: int = typer.Option(..., "--gpu-count", help="GPU 卡数"),
    server_count: int = typer.Option(..., "--server-count", help="服务器台数"),
    annual_revenue: Optional[str] = typer.Option(
        None, "--annual-revenue", help="年收入（用于现金流；不给则只算成本侧）"
    ),
    revenue_basis: str = typer.Option(
        "none", "--revenue-basis", help="收入口径：none / scenario_only / measured_capacity_verified"
    ),
    out: Optional[str] = typer.Option(None, "--out", help="导出 JSON 路径"),
    db: str = typer.Option(DEFAULT_DB, "--db", help="SQLite 数据库路径"),
) -> None:
    """M28：计算完整 TCO 与现金流。"""
    info = tco_for(
        db,
        plan_file=plan_file,
        template_id=template,
        candidate_id=candidate_id,
        hardware_id=hardware,
        gpu_count=gpu_count,
        server_count=server_count,
        annual_revenue=Decimal(annual_revenue) if annual_revenue else None,
        revenue_basis=revenue_basis,
    )
    tco = info["tco"]
    typer.secho(
        f"TCO：一次性 {tco['capex_one_time']}，年现金 {tco['annual_cash_opex']}，"
        f"全周期 {tco['total_cash_over_horizon']}（{tco['cost_basis']}）",
        fg=typer.colors.GREEN,
    )
    if tco["incomplete"]:
        typer.secho(
            f"成本不完整（不得用于正式报价）：{tco['completeness_reasons']}",
            fg=typer.colors.YELLOW,
        )
    if out:
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_text(
            json.dumps(info, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
        )
        typer.echo(f"已写入 {out}")
    _echo_json(
        {
            "plan_id": tco["plan_id"],
            "cost_basis": tco["cost_basis"],
            "capex_one_time": tco["capex_one_time"],
            "annual_cash_opex": tco["annual_cash_opex"],
            "annual_depreciation": tco["annual_depreciation"],
            "annual_total_cost": tco["annual_total_cost"],
            "total_cash_over_horizon": tco["total_cash_over_horizon"],
            "incomplete": tco["incomplete"],
            "unknown_items": tco["unknown_items"],
            "excluded_items": tco["excluded_items"],
        }
    )


@app.command("revenue-scenario")
def revenue_scenario_cmd(
    demand_file: str = typer.Option(..., "--demand-file", help="业务需求情景 YAML"),
    scenario: str = typer.Option(..., "--scenario", help="情景 ID"),
    out: Optional[str] = typer.Option(None, "--out", help="导出 JSON 路径"),
    db: str = typer.Option(DEFAULT_DB, "--db", help="SQLite 数据库路径"),
) -> None:
    """M29：按业务请求量计算 Token 计费与收益情景。"""
    info = revenue_for(db, demand_file=demand_file, scenario_id=scenario)
    rev = info["revenue"]
    typer.secho(
        f"年度收入 {rev['annual_revenue']} {rev['currency'] or ''}"
        f"（容量比对：{rev['capacity_check']}）",
        fg=typer.colors.GREEN,
    )
    if rev["scenario_only"]:
        typer.secho(
            "容量未验证：该收入仅作情景分析，不得作为产能承诺", fg=typer.colors.YELLOW
        )
    if out:
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_text(
            json.dumps(info, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
        )
        typer.echo(f"已写入 {out}")
    _echo_json(
        {
            "scenario_id": rev["scenario_id"],
            "annual_revenue": rev["annual_revenue"],
            "currency": rev["currency"],
            "capacity_check": rev["capacity_check"],
            "scenario_only": rev["scenario_only"],
            "period_revenue_total": rev["period_revenue_total"],
        }
    )


def _phase4_facts(
    *,
    model: str | None,
    model_revision: str | None,
    precision: str | None,
    hardware: str | None,
    gpu_count: int | None,
    tp: int | None,
    dp: int | None,
    framework: str | None,
    framework_version: str | None,
    concurrency: int | None,
    input_len: int | None,
    output_len: int | None,
    dataset: str | None,
    arrival_mode: str | None,
) -> dict[str, object]:
    """把 CLI 参数收敛成"目标身份 + 负载"事实字典（供匹配与容量校验复用）。"""
    return {
        "model_name": model,
        "model_revision": model_revision,
        "weight_precision": precision,
        "hardware_model": hardware,
        "gpu_count": gpu_count,
        "tp_size": tp,
        "dp_size": dp,
        "framework": framework,
        "framework_version": framework_version,
        "configured_concurrency": concurrency,
        "input_len_target": input_len,
        "output_len_target": output_len,
        "dataset_name": dataset,
        "arrival_mode": arrival_mode,
    }


@app.command("record-sla-evidence")
def record_sla_evidence_cmd(
    candidate_id: str = typer.Option(..., "--candidate", help="候选 ID（证据绑定的对象）"),
    requirement: str = typer.Option(..., "--requirement", help="SLA 需求 JSON"),
    model: str = typer.Option(..., "--model", help="模型名（与实测签名一致）"),
    hardware: str = typer.Option(..., "--hardware", help="硬件型号（如 NVIDIA H100 SXM）"),
    gpu_count: int = typer.Option(..., "--gpu-count", help="GPU 卡数"),
    tp: int = typer.Option(..., "--tp", help="TP 大小"),
    dp: int = typer.Option(1, "--dp", help="DP 大小"),
    precision: Optional[str] = typer.Option(None, "--precision", help="权重精度"),
    model_revision: Optional[str] = typer.Option(None, "--model-revision", help="模型版本"),
    framework: Optional[str] = typer.Option(None, "--framework", help="推理框架"),
    framework_version: Optional[str] = typer.Option(None, "--framework-version", help="框架版本"),
    concurrency: Optional[int] = typer.Option(None, "--concurrency", help="配置并发"),
    input_len: Optional[int] = typer.Option(None, "--input-len", help="输入长度"),
    output_len: Optional[int] = typer.Option(None, "--output-len", help="输出长度"),
    dataset: Optional[str] = typer.Option(None, "--dataset", help="数据集"),
    arrival_mode: Optional[str] = typer.Option(None, "--arrival-mode", help="到达方式"),
    sla_evaluation_id: Optional[str] = typer.Option(
        None, "--sla-evaluation-id", help="证据 ID（缺省按 candidate+requirement 生成）"
    ),
    out: Optional[str] = typer.Option(None, "--out", help="导出 JSON 路径"),
    db: str = typer.Option(DEFAULT_DB, "--db", help="SQLite 数据库路径"),
) -> None:
    """V4.1 F4.1：跑一次 SLA 评估并持久化为可引用的证据记录。"""
    facts = _phase4_facts(
        model=model, model_revision=model_revision, precision=precision,
        hardware=hardware, gpu_count=gpu_count, tp=tp, dp=dp,
        framework=framework, framework_version=framework_version,
        concurrency=concurrency, input_len=input_len, output_len=output_len,
        dataset=dataset, arrival_mode=arrival_mode,
    )
    try:
        info = record_sla_evidence_for(
            db,
            candidate_id=candidate_id,
            requirement=json.loads(requirement),
            facts=facts,
            sla_evaluation_id=sla_evaluation_id,
            target_ref=candidate_id,
        )
    except ServiceError as exc:
        _fail(str(exc))
        return
    typer.secho(
        f"已生成 SLA 证据 {info['sla_evaluation_id']}"
        f"（status={info['status']} match_level={info['match_level']}）",
        fg=typer.colors.GREEN if info["status"] == "observed_pass" else typer.colors.YELLOW,
    )
    if info["status"] != "observed_pass":
        typer.secho(
            "该证据不是 observed_pass + exact_match：报价门禁不会据此放行性能承诺",
            fg=typer.colors.YELLOW,
        )
    if out:
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_text(
            json.dumps(info, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
        )
    _echo_json(info)


@app.command("import-quote-approvals")
def import_quote_approvals_cmd(
    file: str = typer.Option(..., "--file", help="人工审批记录 YAML"),
    db: str = typer.Option(DEFAULT_DB, "--db", help="SQLite 数据库路径"),
) -> None:
    """V4.1 F4.1：导入人工审批记录（本命令只做持久化，不生成审批）。"""
    try:
        info = import_quote_approvals(file, db)
    except ServiceError as exc:
        _fail(str(exc))
        return
    typer.secho(f"已导入 {info['approval_count']} 条审批记录", fg=typer.colors.GREEN)
    _echo_json(info)


@app.command("quote-eligibility")
def quote_eligibility_cmd(
    candidate_id: str = typer.Option(..., "--candidate", help="候选 ID"),
    requirement_id: str = typer.Option(..., "--requirement-id", help="需求 ID"),
    sla_evaluation_id: str = typer.Option(
        ..., "--sla-evaluation-id", help="持久化的 SLA 证据 ID（必需）"
    ),
    approval_id: str = typer.Option(..., "--approval-id", help="人工审批记录 ID（必需）"),
    model: Optional[str] = typer.Option(None, "--model", help="模型名（重新核验用）"),
    hardware: Optional[str] = typer.Option(None, "--hardware", help="硬件型号"),
    gpu_count: Optional[int] = typer.Option(None, "--gpu-count", help="GPU 卡数"),
    tp: Optional[int] = typer.Option(None, "--tp", help="TP 大小"),
    dp: Optional[int] = typer.Option(None, "--dp", help="DP 大小"),
    precision: Optional[str] = typer.Option(None, "--precision", help="权重精度"),
    model_revision: Optional[str] = typer.Option(None, "--model-revision", help="模型版本"),
    framework: Optional[str] = typer.Option(None, "--framework", help="推理框架"),
    framework_version: Optional[str] = typer.Option(None, "--framework-version", help="框架版本"),
    concurrency: Optional[int] = typer.Option(None, "--concurrency", help="配置并发"),
    input_len: Optional[int] = typer.Option(None, "--input-len", help="输入长度"),
    output_len: Optional[int] = typer.Option(None, "--output-len", help="输出长度"),
    dataset: Optional[str] = typer.Option(None, "--dataset", help="数据集"),
    arrival_mode: Optional[str] = typer.Option(None, "--arrival-mode", help="到达方式"),
    plan_file: Optional[str] = typer.Option(None, "--plan-file", help="成本方案模板 YAML"),
    template: Optional[str] = typer.Option(None, "--template", help="模板 ID"),
    hardware_id: Optional[str] = typer.Option(
        None, "--hardware-id", help="硬件 SKU ID（成本目录用；与 --hardware 的型号名不同）"
    ),
    server_count: Optional[int] = typer.Option(None, "--server-count", help="服务器台数"),
    confirmation: Optional[str] = typer.Option(None, "--confirmation", help="人工商务确认记录 ID"),
    scenario: Optional[str] = typer.Option(None, "--scenario", help="需求情景 ID"),
    region: Optional[str] = typer.Option(None, "--region", help="区域"),
    as_of: Optional[str] = typer.Option(None, "--as-of", help="基准日期 YYYY-MM-DD"),
    out: Optional[str] = typer.Option(None, "--out", help="导出 JSON 路径"),
    db: str = typer.Option(DEFAULT_DB, "--db", help="SQLite 数据库路径"),
) -> None:
    """M30 / V4.1 F4.1：四维报价门禁（只接受证据引用，不接受状态字符串）。"""
    from datetime import date as _date

    facts = _phase4_facts(
        model=model, model_revision=model_revision, precision=precision,
        hardware=hardware, gpu_count=gpu_count, tp=tp, dp=dp,
        framework=framework, framework_version=framework_version,
        concurrency=concurrency, input_len=input_len, output_len=output_len,
        dataset=dataset, arrival_mode=arrival_mode,
    )
    try:
        info = quote_eligibility_for(
            db,
            candidate_id=candidate_id,
            requirement_id=requirement_id,
            facts=facts,
            sla_evaluation_id=sla_evaluation_id,
            approval_id=approval_id,
            confirmation_id=confirmation,
            plan_file=plan_file,
            template_id=template,
            hardware_id=hardware_id or hardware,
            gpu_count=gpu_count,
            server_count=server_count,
            scenario_id=scenario,
            region=region,
            as_of=_date.fromisoformat(as_of) if as_of else None,
        )
    except (ServiceError, ValueError) as exc:
        _fail(str(exc))
        return
    elig = info["eligibility"]
    if elig["quote_publishable"]:
        typer.secho("正式报价可发布", fg=typer.colors.GREEN)
    else:
        typer.secho("正式报价不可发布", fg=typer.colors.RED)
        for blocker in elig["blockers"]:
            typer.echo(f"  - {blocker}")
    if out:
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_text(
            json.dumps(info, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
        )
        typer.echo(f"已写入 {out}")
    _echo_json(
        {
            "quote_publishable": elig["quote_publishable"],
            "cost_estimate_status": elig["cost_estimate_status"],
            "sla_verification_status": elig["sla_verification_status"],
            "commercial_confirmation_status": elig["commercial_confirmation_status"],
            "approval_status": elig["approval_status"],
            "sla_evaluation_id": elig["sla_evaluation_id"],
            "approval_id": elig["approval_id"],
            "cost_snapshot_hash": elig["cost_snapshot_hash"],
            "blockers": elig["blockers"],
        }
    )


@app.command("phase4-decision")
def phase4_decision_cmd(
    model: str = typer.Option(..., "--model", help="模型 ID"),
    hardware: str = typer.Option(..., "--hardware", help="硬件 ID"),
    server: Optional[str] = typer.Option(None, "--server", help="服务器形态 ID"),
    plan_file: str = typer.Option(..., "--plan-file", help="成本方案模板 YAML"),
    template: str = typer.Option(..., "--template", help="模板 ID"),
    requirement: str = typer.Option(..., "--requirement", help="SLA 需求 JSON"),
    ctx: int = typer.Option(..., "--context-length", help="每请求上下文长度（token）"),
    concurrency: int = typer.Option(..., "--concurrency", help="全局并发请求数"),
    input_len: Optional[int] = typer.Option(None, "--input-len", help="目标输入长度"),
    output_len: Optional[int] = typer.Option(None, "--output-len", help="目标输出长度"),
    dataset: Optional[str] = typer.Option(None, "--dataset", help="数据集名称"),
    tp_sizes: str = typer.Option("1,2,4,8", "--tp-sizes", help="枚举的 TP 取值"),
    dp_sizes: str = typer.Option("1,2,4,8", "--dp-sizes", help="枚举的 DP 取值"),
    block_size: Optional[int] = typer.Option(None, "--block-size", help="Paged KV 块大小"),
    usable_fraction: Optional[str] = typer.Option(None, "--usable-fraction", help="按标称容量的可用比例"),
    usable_source: Optional[str] = typer.Option(None, "--usable-source", help="可用显存来源描述"),
    usable_source_kind: Optional[str] = typer.Option(None, "--usable-source-kind", help="可用显存来源性质"),
    runtime_reserve: Optional[str] = typer.Option(None, "--runtime-reserve-fraction", help="运行时合并预留比例"),
    runtime_source: Optional[str] = typer.Option(None, "--runtime-source", help="运行时预留来源"),
    demand_file: Optional[str] = typer.Option(None, "--demand-file", help="业务需求情景 YAML"),
    scenario: Optional[str] = typer.Option(None, "--scenario", help="需求情景 ID"),
    out: Optional[str] = typer.Option(None, "--out", help="导出 JSON 路径"),
    db: str = typer.Option(DEFAULT_DB, "--db", help="SQLite 数据库路径"),
) -> None:
    """M31/M32：串起资源、性能、TCO、收益与报价门禁的完整决策链。"""
    assumptions = make_assumptions(
        usable_fraction_of_nominal=Decimal(usable_fraction) if usable_fraction else None,
        usable_memory_source=usable_source,
        usable_memory_source_kind=usable_source_kind,
        runtime_reserve_fraction=Decimal(runtime_reserve) if runtime_reserve else None,
        runtime_reserve_source=runtime_source,
        block_size_tokens=block_size,
        global_concurrency=concurrency,
        context_length_per_request=ctx,
        tp_sizes=_parse_int_list(tp_sizes),
        dp_sizes=_parse_int_list(dp_sizes),
    )
    info = phase4_decision_for(
        db,
        requirement=json.loads(requirement),
        model_id=model,
        hardware_id=hardware,
        server_id=server,
        plan_file=plan_file,
        template_id=template,
        assumptions=assumptions,
        input_len_target=input_len,
        output_len_target=output_len,
        dataset_name=dataset,
        demand_file=demand_file,
        scenario_id=scenario,
    )
    comparison = info["comparison"]
    buckets: dict[str, int] = {}
    for entry in comparison["entries"]:
        key = entry["rank_bucket"]
        buckets[key] = buckets.get(key, 0) + 1
    typer.secho(f"候选分桶: {buckets}", fg=typer.colors.GREEN)
    if comparison.get("lowest_cost_candidate_id"):
        typer.echo(f"可比且成本完整者中的最低成本候选: {comparison['lowest_cost_candidate_id']}")
    publishable = [e for e in comparison["entries"] if e["quote_publishable"]]
    typer.secho(
        f"可发布正式报价的候选: {[e['candidate_id'] for e in publishable] or '无'}",
        fg=typer.colors.GREEN if publishable else typer.colors.YELLOW,
    )
    if out:
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_text(
            json.dumps(info, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
        )
        typer.echo(f"已写入 {out}")
    _echo_json(
        {
            "requirement_id": info["requirement_id"],
            "buckets": buckets,
            "per_candidate": info["per_candidate"],
            "limitations": info["limitations"],
        }
    )


@app.command("phase4-report")
def phase4_report_cmd(
    out: str = typer.Option("data/exports/phase4_report.md", "--out", help="Markdown 报告路径"),
    decision_json: Optional[str] = typer.Option(
        None,
        "--decision-json",
        help="phase4-decision 导出的 JSON；给出后报告才会包含第 2-6 节与四维门禁明细",
    ),
    db: str = typer.Option(DEFAULT_DB, "--db", help="SQLite 数据库路径"),
) -> None:
    """M33：生成第四阶段报告（含报价草稿或阻断说明）。"""
    decision = None
    if decision_json:
        decision = json.loads(Path(decision_json).read_text(encoding="utf-8"))
    info = export_phase4_report(db, out, decision=decision)
    typer.secho(f"报告已写入 {info['report']}（{info['bytes']} 字节）", fg=typer.colors.GREEN)
    _echo_json(
        {"report": info["report"], "quote_publishable": info["quote_publishable"]}
    )


if __name__ == "__main__":
    app()
