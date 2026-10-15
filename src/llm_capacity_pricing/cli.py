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
    import_hardware,
    import_models,
    import_sglang_candidates,
    init_db,
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

if __name__ == "__main__":
    app()
