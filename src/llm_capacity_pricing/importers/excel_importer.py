"""Excel 导入器：``Token产能计算器-0910.xlsx`` -> 规范化数据模型。

导入规则（任务书 §2）：
* 保留 N 列完整字符串为 ``deployment_strategy_raw``；允许解析但只能标记
  ``unverified_parse``，不得当成已核实的启动配置。
* C 列 ``deployment_gpu_count`` 与 ``GPU价格!B`` 的每服务器卡数**不可混用**。
* D 列统一叫 ``configured_concurrency``，不等于实测平均并发。
* E 列存 ``single_user_output_tps_p90``，不推导成 TPOT P99。
* H 列先存 ``cache_hit_rate_raw``，语义默认 unknown；旧表复算时作为定价代理
  假设必须带 ``legacy_proxy_for_billable_hit_rate=True``。
* I/J/K/L/M 列保留在 ``legacy_*``，新结果另存 ``recomputed_*``。
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from openpyxl import load_workbook

from llm_capacity_pricing.schemas.benchmark import BenchmarkRecord
from llm_capacity_pricing.schemas.costs import HardwarePrice, TokenPrice
from llm_capacity_pricing.schemas.evidence import Evidence

SHEET_GPU = "GPU价格"
SHEET_PRICE = "模型价格"
SHEET_PERF = "部署效能"

CURRENCY_ASSUMPTION = "CNY; pending business confirmation"
COST_SCOPE_ASSUMPTION = (
    "万元/卡/年 按表头语境推断；成本覆盖范围(coverage scope)待业务确认"
)

#: Excel 中 ``部署效能!O`` 列声明的数据来源。它不是 SGLang/vLLM 官方数据。
EXCEL_DECLARED_SOURCE = "InferenceX"

#: 测量来源归属：Excel 是**载体**，其内容是历史外部/第三方数据，不是自有实测。
EXCEL_MEASUREMENT_ORIGIN = "historical_external_third_party"
#: 显式质量标记：防止把"文件在内部"误读成"数据由我们实测"
HISTORICAL_EXTERNAL_FLAG = "historical_external_source_not_internal_measurement"

#: 已知需要业务确认的字段（写入每条记录的质量标记）
BUSINESS_CONFIRMATION_FLAGS = [
    "currency_pending_business_confirmation",
    "cost_coverage_scope_pending_business_confirmation",
]


class ExcelImportError(Exception):
    """Excel 结构或取值不符合已核实结构。"""


@dataclass
class ImportReport:
    source_file: str
    raw_sha256: str
    imported_at: datetime
    sheet_rows: dict[str, int] = field(default_factory=dict)
    evidences: list[Evidence] = field(default_factory=list)
    hardware_prices: list[HardwarePrice] = field(default_factory=list)
    token_prices: list[TokenPrice] = field(default_factory=list)
    benchmarks: list[BenchmarkRecord] = field(default_factory=list)
    unmatched_models: list[str] = field(default_factory=list)
    unmatched_gpus: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _dec(value: Any) -> Decimal | None:
    """把单元格值转成 Decimal，空单元格返回 None（**不填 0**）。

    走 ``str()`` 而不是 ``Decimal(float)``，以保持 Excel 显示精度而非二进制误差。
    """
    if value is None:
        return None
    if isinstance(value, str) and value.strip() == "":
        return None
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value).strip())
    except (InvalidOperation, ValueError) as exc:  # pragma: no cover - 结构异常
        raise ExcelImportError(f"单元格值无法解析为数值: {value!r}") from exc


def _int(value: Any) -> int | None:
    d = _dec(value)
    if d is None:
        return None
    if d != d.to_integral_value():
        raise ExcelImportError(f"期望整数，实际为 {value!r}")
    return int(d)


def _text(value: Any) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    return s or None


# --------------------------------------------------------------------------
# 部署策略解析（仅作未核实解析，不能当成启动配置）
# --------------------------------------------------------------------------

_TP_RE = re.compile(r"TP(\d+)")
_DP_RE = re.compile(r"(?<!D)DP(\d+)")
_EP_RE = re.compile(r"EP(\d+)")
_PP_RE = re.compile(r"PP(\d+)")
_DCP_RE = re.compile(r"DCP(\d+)")
_PD_SPLIT_RE = re.compile(r"P(\d+)D(\d+)")


def parse_deployment_strategy(raw: str | None) -> dict[str, Any] | None:
    """把策略字符串拆成 token 字典。

    返回结构始终带 ``verified: False``，因为原始文件没有提供启动配置证据。
    """
    if not raw:
        return None
    parsed: dict[str, Any] = {
        "raw": raw,
        "verified": False,
        "verification_note": (
            "由 deployment_strategy_raw 字符串解析，未经启动配置/日志核实，"
            "不得作为已核实的 TP/EP/PD 拓扑使用"
        ),
    }
    for key, rx in (
        ("tp", _TP_RE),
        ("dp", _DP_RE),
        ("ep", _EP_RE),
        ("pp", _PP_RE),
        ("dcp", _DCP_RE),
    ):
        m = rx.search(raw)
        if m:
            parsed[key] = int(m.group(1))
    m = _PD_SPLIT_RE.search(raw)
    if m:
        parsed["prefill_p"] = int(m.group(1))
        parsed["decode_d"] = int(m.group(2))
        parsed["deployment_mode_hint"] = "prefill_decode_disaggregated"
    if "MTP" in raw.upper():
        parsed["mtp"] = True
    if "DP Attn" in raw or "DPATTN" in raw.upper().replace(" ", ""):
        parsed["dp_attention"] = True
    if "Dspark" in raw:
        parsed["dspark"] = True
    return parsed


# --------------------------------------------------------------------------
# 各表导入
# --------------------------------------------------------------------------


def _read_rows(ws) -> list[tuple[int, list[Any]]]:
    return [(r[0].row, [c.value for c in r]) for r in ws.iter_rows(min_row=2)]


def _import_gpu_prices(ws, report: ImportReport) -> None:
    for row_no, row in _read_rows(ws):
        if all(v is None for v in row):
            continue
        gpu_model = _text(row[0])
        if gpu_model is None:
            raise ExcelImportError(f"{SHEET_GPU}!A{row_no} 缺少 GPU 型号")
        gpus_per_server = _int(row[1])
        cost = _dec(row[2])
        if cost is not None and cost < 0:
            raise ExcelImportError(f"{SHEET_GPU}!C{row_no} 价格为负值: {cost}")
        if cost is None:
            report.notes.append(f"{SHEET_GPU}!C{row_no} 价格缺失，保留空值")
        ev = Evidence(
            evidence_id=f"ev_excel_{SHEET_GPU}_{row_no}",
            source_type="internal_excel",
            measurement_origin=EXCEL_MEASUREMENT_ORIGIN,
            is_internal_measurement=False,
            publisher="internal",
            source_file=report.source_file,
            source_sheet=SHEET_GPU,
            source_row=row_no,
            raw_sha256=report.raw_sha256,
            retrieved_at=report.imported_at,
            notes=(
                f"原始值: A={gpu_model!r} B={gpus_per_server!r} C={cost!r}；"
                f"单位按表头语境理解为万元/卡/年"
            ),
            quality_flags=[HISTORICAL_EXTERNAL_FLAG]
            + list(BUSINESS_CONFIRMATION_FLAGS)
            + (["per_gpu_annual_cost_missing"] if cost is None else []),
        )
        report.evidences.append(ev)
        report.hardware_prices.append(
            HardwarePrice(
                hardware_price_id=f"excel_{SHEET_GPU}_{row_no}",
                gpu_model=gpu_model,
                gpus_per_server=gpus_per_server,
                per_gpu_annual_cost_10k=cost,
                currency=None,  # Excel 未写明币种 -> 保持空值
                currency_assumption=CURRENCY_ASSUMPTION,
                cost_scope=COST_SCOPE_ASSUMPTION,
                valid_from=None,
                valid_to=None,
                evidence_id=ev.evidence_id,
            )
        )


def _import_token_prices(ws, report: ImportReport) -> None:
    for row_no, row in _read_rows(ws):
        if all(v is None for v in row):
            continue
        model_name = _text(row[0])
        if model_name is None:
            raise ExcelImportError(f"{SHEET_PRICE}!A{row_no} 缺少模型名")
        p_hit, p_miss, p_out = _dec(row[1]), _dec(row[2]), _dec(row[3])
        for label, val in (("B", p_hit), ("C", p_miss), ("D", p_out)):
            if val is not None and val < 0:
                raise ExcelImportError(f"{SHEET_PRICE}!{label}{row_no} 单价为负值: {val}")
        missing = [
            name
            for name, val in (
                ("price_cached_input_per_1m", p_hit),
                ("price_uncached_input_per_1m", p_miss),
                ("price_output_per_1m", p_out),
            )
            if val is None
        ]
        ev = Evidence(
            evidence_id=f"ev_excel_{SHEET_PRICE}_{row_no}",
            source_type="internal_excel",
            measurement_origin=EXCEL_MEASUREMENT_ORIGIN,
            is_internal_measurement=False,
            publisher="internal",
            source_file=report.source_file,
            source_sheet=SHEET_PRICE,
            source_row=row_no,
            raw_sha256=report.raw_sha256,
            retrieved_at=report.imported_at,
            notes=(
                f"原始值: 模型={model_name!r} 命中={p_hit!r} 未命中={p_miss!r} "
                f"输出={p_out!r}；单位为 货币单位/百万Token，币种待确认"
            ),
            quality_flags=[HISTORICAL_EXTERNAL_FLAG]
            + list(BUSINESS_CONFIRMATION_FLAGS)
            + ([f"missing_price_field:{m}" for m in missing] if missing else []),
        )
        report.evidences.append(ev)
        report.token_prices.append(
            TokenPrice(
                token_price_id=f"excel_{SHEET_PRICE}_{row_no}",
                model_name=model_name,
                price_cached_input_per_1m=p_hit,
                price_uncached_input_per_1m=p_miss,
                price_output_per_1m=p_out,
                currency=None,
                currency_assumption=CURRENCY_ASSUMPTION,
                effective_date=None,
                evidence_id=ev.evidence_id,
            )
        )


#: 导入后仍为空的字段（Excel 未提供，绝不从策略字符串或模型名补齐）
EXCEL_ABSENT_FIELDS = [
    "model_revision",
    "model_precision",
    "framework",
    "framework_version",
    "driver_version",
    "hardware_sku",
    "node_count",
    "gpus_per_node",
    "tp_size",
    "dp_size",
    "ep_size",
    "pp_size",
    "prefill_gpu_count",
    "decode_gpu_count",
    "mean_observed_concurrency",
    "request_rate_rps",
    "num_requests",
    "input_tokens_target",
    "output_tokens_target",
    "input_length_distribution",
    "output_length_distribution",
    "dataset_name",
    "dataset_digest",
    "ttft_mean_ms",
    "ttft_p99_ms",
    "tpot_mean_ms",
    "tpot_p99_ms",
    "itl_p99_ms",
    "e2e_p99_ms",
    "failure_rate",
    "success_count",
    "duration_s",
    "physical_cache_hit_rate",
    "billable_cached_input_ratio",
]


def _import_performance(ws, report: ImportReport) -> None:
    for row_no, row in _read_rows(ws):
        if all(v is None for v in row):
            continue
        gpu = _text(row[0])
        model = _text(row[1])
        if not gpu or not model:
            raise ExcelImportError(f"{SHEET_PERF}!A{row_no}/B{row_no} 缺少 GPU 型号或模型名")

        n_gpu = _int(row[2])
        if n_gpu is None or n_gpu <= 0:
            raise ExcelImportError(f"{SHEET_PERF}!C{row_no} 部署卡数非法: {row[2]!r}")

        strategy_raw = _text(row[13])
        data_source = _text(row[14])
        parsed = parse_deployment_strategy(strategy_raw)

        hit_rate = _dec(row[7])
        flags = [HISTORICAL_EXTERNAL_FLAG] + list(BUSINESS_CONFIRMATION_FLAGS)
        if data_source and data_source.upper() == EXCEL_DECLARED_SOURCE.upper():
            flags.append(
                "underlying_source_inferencex_not_official_framework_benchmark"
            )
        flags.append("legacy_hit_rate_used_as_billing_proxy")
        flags.append("cache_metric_semantics_unknown")
        if parsed is not None:
            flags.append("deployment_strategy_parse_unverified")
        if n_gpu != 8:
            flags.append("deployment_gpu_count_differs_from_server_gpu_count")

        ev = Evidence(
            evidence_id=f"ev_excel_{SHEET_PERF}_{row_no}",
            source_type="internal_excel",
            measurement_origin=EXCEL_MEASUREMENT_ORIGIN,
            is_internal_measurement=False,
            publisher=data_source or "internal",
            source_file=report.source_file,
            source_sheet=SHEET_PERF,
            source_row=row_no,
            raw_sha256=report.raw_sha256,
            retrieved_at=report.imported_at,
            notes=(
                f"原始行: GPU={gpu!r} 模型={model!r} 部署卡数={n_gpu!r} "
                f"并发={row[3]!r} 单用户输出P90={row[4]!r} 每卡输入={row[5]!r} "
                f"每卡输出={row[6]!r} 命中率={hit_rate!r} 策略={strategy_raw!r} "
                f"数据来源={data_source!r}。该行 I/J/K/L/M 为旧表计算结果，"
                f"仅作历史留档，不作为对外营收或利润承诺"
            ),
            quality_flags=flags,
        )
        report.evidences.append(ev)

        rec = BenchmarkRecord(
            record_id=f"excel_{SHEET_PERF}_{row_no}",
            model_name=model,
            hardware_model=gpu,
            deployment_gpu_count=n_gpu,
            evidence_id=ev.evidence_id,
            performance_source_kind="internal_excel",
            deployment_mode=(
                "prefill_decode_disaggregated"
                if parsed and parsed.get("deployment_mode_hint")
                else "unknown"
            ),
            deployment_strategy_raw=strategy_raw,
            deployment_strategy_parsed=parsed,
            configured_concurrency=_int(row[3]),
            single_user_output_tps_p90=_dec(row[4]),
            input_tps_per_gpu=_dec(row[5]),
            output_tps_per_gpu=_dec(row[6]),
            cache_hit_rate_raw=hit_rate,
            cache_metric_semantics="unknown",
            legacy_proxy_for_billable_hit_rate=True,
            legacy_input_revenue_10k_per_gpu_year=_dec(row[8]),
            legacy_output_revenue_10k_per_gpu_year=_dec(row[9]),
            legacy_total_revenue_10k_per_gpu_year=_dec(row[10]),
            legacy_cost_10k_per_gpu_year=_dec(row[11]),
            legacy_revenue_cost_ratio=_dec(row[12]),
            verification_status="partial_evidence",
            quality_flags=flags,
            raw_metrics={
                "excel_row": row_no,
                "columns": {
                    "A_hardware_model": gpu,
                    "B_model_name": model,
                    "C_deployment_gpu_count": n_gpu,
                    "D_configured_concurrency": row[3],
                    "E_single_user_output_tps_p90": row[4],
                    "F_input_tps_per_gpu": row[5],
                    "G_output_tps_per_gpu": row[6],
                    "H_cache_hit_rate_raw": row[7],
                    "I_legacy_input_revenue_10k": row[8],
                    "J_legacy_output_revenue_10k": row[9],
                    "K_legacy_total_revenue_10k": row[10],
                    "L_legacy_cost_10k": row[11],
                    "M_legacy_revenue_cost_ratio": row[12],
                    "N_deployment_strategy_raw": row[13],
                    "O_data_source": row[14],
                },
            },
        )
        for f in EXCEL_ABSENT_FIELDS:
            rec.note_missing(f)
        report.benchmarks.append(rec)


def import_excel(path: str | Path, *, imported_at: datetime | None = None) -> ImportReport:
    """解析 Excel 三个工作表并返回规范化对象集合（不触碰数据库）。"""
    p = Path(path)
    if not p.exists():
        raise ExcelImportError(f"找不到 Excel 文件: {p}")

    report = ImportReport(
        source_file=p.name,
        raw_sha256=sha256_file(p),
        imported_at=imported_at or datetime.now(),
    )

    wb = load_workbook(p, data_only=True, read_only=False)
    try:
        for required in (SHEET_GPU, SHEET_PRICE, SHEET_PERF):
            if required not in wb.sheetnames:
                raise ExcelImportError(
                    f"缺少工作表 {required!r}；实际工作表: {wb.sheetnames}"
                )
        for name, handler in (
            (SHEET_GPU, _import_gpu_prices),
            (SHEET_PRICE, _import_token_prices),
            (SHEET_PERF, _import_performance),
        ):
            ws = wb[name]
            handler(ws, report)
            report.sheet_rows[name] = len(_read_rows(ws))
    finally:
        wb.close()

    # 交叉核对：效能行的 GPU/模型是否在价格表中存在。缺失只记录，不猜测。
    gpu_models = {hp.gpu_model for hp in report.hardware_prices}
    price_models = {tp.model_name for tp in report.token_prices}
    for rec in report.benchmarks:
        if rec.hardware_model not in gpu_models:
            if rec.hardware_model not in report.unmatched_gpus:
                report.unmatched_gpus.append(rec.hardware_model)
            rec.note_missing("hardware_price(no matching GPU价格 row)")
            rec.quality_flags.append("no_matching_hardware_price")
        if rec.model_name not in price_models:
            if rec.model_name not in report.unmatched_models:
                report.unmatched_models.append(rec.model_name)
            rec.note_missing("token_price(no matching 模型价格 row)")
            rec.quality_flags.append("no_matching_token_price")

    if report.unmatched_gpus:
        report.notes.append(f"未匹配到硬件价格的 GPU: {report.unmatched_gpus}")
    if report.unmatched_models:
        report.notes.append(f"未匹配到 Token 价格的模型: {report.unmatched_models}")

    return report
