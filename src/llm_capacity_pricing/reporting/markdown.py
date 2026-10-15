"""Markdown 报告生成（M6）。

报告必须显式包含：数据概览、可用场景数、数据缺口、最大理论满载收入案例、
成本敏感性、逐条来源说明，以及所有业务边界声明。
"""

from __future__ import annotations

from collections import Counter
from decimal import Decimal
from typing import Any, Iterable, Sequence

from llm_capacity_pricing.schemas.benchmark import BenchmarkRecord
from llm_capacity_pricing.schemas.costs import HardwarePrice, TokenPrice
from llm_capacity_pricing.schemas.evidence import Evidence
from llm_capacity_pricing.schemas.pricing import (
    COST_SCOPE_CONFIRMATION_STATUS,
    COST_SCOPE_UNCONFIRMED,
    PricingResult,
)

SENSITIVITY_LEVELS = [Decimal("0.10"), Decimal("0.30"), Decimal("0.50"), Decimal("0.70"), Decimal("1.00")]


def summarize_missing(records: Iterable[BenchmarkRecord]) -> Counter[str]:
    c: Counter[str] = Counter()
    for rec in records:
        for f in rec.missing_fields:
            c[f] += 1
    return c


def _fmt(v: Decimal | None) -> str:
    if v is None:
        return "—"
    q = v.quantize(Decimal("0.0001"))
    return f"{q:,}"


def _pct(v: Decimal | None) -> str:
    if v is None:
        return "—"
    return f"{(v * 100).quantize(Decimal('0.01'))}%"


def build_report(
    *,
    evidences: Sequence[Evidence],
    hardware_prices: Sequence[HardwarePrice],
    token_prices: Sequence[TokenPrice],
    records: Sequence[BenchmarkRecord],
    legacy_results: Sequence[PricingResult],
    scenario_results: Sequence[PricingResult],
    import_runs: Sequence[dict] | None = None,
    db_path: str = "data/local.db",
    include_test_data: bool = False,
    excluded_test_records: Sequence[str] = (),
    official_candidates: Sequence[Any] = (),
    numeric_candidates: Sequence[Any] = (),
) -> str:
    L: list[str] = []
    add = L.append

    add("# 大模型部署基准数据与 Token 定价 —— 计算报告")
    add("")
    add("> 本报告由离线计算引擎自动生成。**不是对外报价，不是 SLA 承诺，")
    add("> 不是实际营收**；所有金额为**理论满载潜力**（或指定变现比例下的情景估算），")
    add("> 单位万元，`cost_scope = gpu_cost_only`（仅表内 GPU 成本，不是完整 TCO）。")
    add("")
    add("### 口径声明（务必先读）")
    add("")
    add("| 项 | 值 |")
    add("|---|---|")
    add(
        "| 结果类型 | 理论满载潜力 / 情景敏感性估算 —— **不是实际收入，不是客户报价，"
        "不是可收取金额** |"
    )
    add("| 币种 | `UNCONFIRMED`（假设 `CNY; pending business confirmation`，待业务确认） |")
    add("| 成本范围 | `gpu_cost_only` —— 仅表内 GPU 摊销/租金成本，**不是**完整 TCO |")
    add("| 价格单位 / 计费周期 | 货币单位/百万 Token；万元/卡/年 |")
    add(
        "| SLA | 不提供任何 SLA 承诺；缺 TTFT/TPOT 的 P99 实测数据时恒为 "
        "`not_evaluable` |"
    )
    add("| 金额编码 | 导出 JSON 中金额为可精确恢复的十进制字符串 |")
    add(
        "| 成本范围未确认项 | "
        + "、".join(f"`{c}`" for c in COST_SCOPE_UNCONFIRMED)
        + f"（确认状态 `{COST_SCOPE_CONFIRMATION_STATUS}`）—— 上述各项**是否已包含在"
        "表内成本中尚未确认**，因此本报告的成本与收入比不能当作完整 TCO 结论 |"
    )
    if numeric_candidates:
        by_source: dict[str, int] = {}
        for c in numeric_candidates:
            by_source[c.source_id] = by_source.get(c.source_id, 0) + 1
        add(
            "| 官方数值候选 | 本次接入 "
            f"{len(numeric_candidates)} 条（"
            + "，".join(f"{k} {v} 条" for k, v in sorted(by_source.items()))
            + "）；全部为 `partial_evidence`，**完整可复现的 verified=0**，"
            "不参与本报告任何经济性计算 |"
        )
    if official_candidates:
        add(
            f"| 官方页面候选 | {len(official_candidates)} 条（页面级证据，同上不参与计算） |"
        )
    add("")

    # "真实"数据（Excel/官方/实测）与人工 fixture 严格分开统计，
    # 避免把人工构造数据与原始数据混到同一证据级别（任务书 §8 M6 验收）。
    real_records = [
        r for r in records if r.performance_source_kind != "synthetic_fixture"
    ]
    fixture_records = [
        r for r in records if r.performance_source_kind == "synthetic_fixture"
    ]
    real_ids = {r.record_id for r in real_records}
    real_results = [r for r in legacy_results if r.benchmark_record_id in real_ids]
    fixture_results = [r for r in legacy_results if r.benchmark_record_id not in real_ids]
    business_scenarios = [r for r in scenario_results if r.benchmark_record_id in real_ids]
    test_scenarios = [r for r in scenario_results if r.benchmark_record_id not in real_ids]
    excluded = list(excluded_test_records)

    # ---- 1. 数据概览 ---------------------------------------------------
    add("## 1. 数据概览")
    add("")
    add(f"- 数据库: `{db_path}`")
    add(f"- 证据条目: {len(evidences)}")
    add(f"- GPU 硬件价格: {len(hardware_prices)} 条")
    add(f"- 模型 Token 价格: {len(token_prices)} 条")
    add(f"- 部署效能记录: {len(records)} 条（业务 {len(real_records)} + 人工测试 {len(fixture_records)}）")
    add(f"- 满载计算结果: {len(legacy_results)} 条（业务 {len(real_results)} + 测试 {len(fixture_results)}）")
    add(f"- 情景结果: {len(scenario_results)} 条（业务 {len(business_scenarios)} + 测试 {len(test_scenarios)}）")
    add(f"- 本次报告是否纳入测试数据: **{'是' if include_test_data else '否'}**")
    add("")
    add("### 1.1 测试数据隔离口径")
    add("")
    add(
        "`synthetic_fixture` 是人工构造的解析器测试样例，**默认不参与任何业务统计**。"
    )
    if excluded:
        add("")
        add(f"本次被排除在业务计算之外的测试记录（{len(excluded)} 条）：")
        add("")
        for rid in excluded:
            add(f"- `{rid}`")
    if include_test_data:
        add("")
        add(
            "> 注意：本次报告由 `include_test_data=True` 生成，业务统计已**包含**测试记录，"
            "结果仅供测试核对，不得用于任何对外结论。"
        )
    else:
        add("")
        add(
            "> 如需包含测试数据的完整口径（36 条 × 5 档 = 180 条情景），"
            "必须显式启用：`--include-test-data`。"
        )
    add("")
    kinds = Counter(e.source_type for e in evidences)
    add("证据来源构成：")
    add("")
    add("| source_type | 条数 |")
    add("|---|---|")
    for k, v in sorted(kinds.items()):
        add(f"| {k} | {v} |")
    add("")
    perf_kinds = Counter(r.performance_source_kind for r in records)
    add("效能数据来源类型（`performance_source_kind`）：")
    add("")
    add("| 类型 | 条数 | 业务口径 |")
    add("|---|---|---|")
    for k, v in sorted(perf_kinds.items()):
        included = "不纳入" if k == "synthetic_fixture" else "纳入"
        add(f"| {k} | {v} | {included} |")
    add("")

    if import_runs:
        add("导入批次：")
        add("")
        add("| import_run_id | 文件 | sha256(前 12) | 时间 | 效能行数 |")
        add("|---|---|---|---|---|")
        for r in import_runs:
            add(
                f"| {r['import_run_id']} | {r['source_file']} | {r['raw_sha256'][:12]} "
                f"| {r['imported_at']} | {r['benchmark_count']} |"
            )
        add("")

    # ---- 2. 可用场景数 -------------------------------------------------
    add("## 2. 可用场景数")
    add("")
    ok = [r for r in real_results if r.status == "ok"]
    partial = [r for r in real_results if r.status == "partial"]
    not_eval = [r for r in real_results if r.status == "not_evaluable"]
    add(f"**真实数据**（{len(real_results)} 条）：")
    add("")
    add(f"- 可完整计算（status=ok）: **{len(ok)}** / {len(real_results)}")
    add(f"- 部分可算（status=partial）: {len(partial)}")
    add(f"- 不可计算（status=not_evaluable）: {len(not_eval)}")
    add("")
    if fixture_results:
        fok = sum(1 for r in fixture_results if r.status == "ok")
        add(
            f"另有人工 `synthetic_fixture` 记录 {len(fixture_results)} 条"
            f"（其中可完整计算 {fok} 条）。这些是解析器测试数据，"
            "**不作为经济性结论依据**。"
        )
        add("")
    add(
        "> `not_evaluable` 表示缺少必需输入（价格/吞吐/成本）；引擎不返回 inf，"
        "也不静默填 0。"
    )
    add("")
    if not_eval:
        add("不可计算明细（真实数据）：")
        add("")
        add("| record_id | 原因 |")
        add("|---|---|")
        for r in not_eval[:20]:
            add(f"| {r.benchmark_record_id} | {r.not_evaluable_reason} |")
        add("")

    # ---- 3. 数据缺口 ---------------------------------------------------
    add("## 3. 数据缺口")
    add("")
    miss = summarize_missing(real_records)
    add(
        f"以下字段在 {len(real_records)} 条真实效能记录中全部或部分缺失"
        "（原始文件未提供，未做任何补齐）："
    )
    add("")
    add("| 字段 | 缺失条数 | 缺失比例 |")
    add("|---|---|---|")
    total = max(len(real_records), 1)
    for f, n in miss.most_common():
        add(f"| `{f}` | {n} | {n / total:.0%} |")
    add("")
    flags = Counter(f for r in real_records for f in r.quality_flags)
    add("质量标记统计（真实记录）：")
    add("")
    add("| quality_flag | 条数 |")
    add("|---|---|")
    for f, n in flags.most_common():
        add(f"| `{f}` | {n} |")
    add("")
    if fixture_records:
        add(
            f"另有 {len(fixture_records)} 条 `synthetic_fixture` 解析器测试记录"
            "（人工构造，**不进入性能数据库、不参与经济性结论**）："
        )
        add("")
        add("| record_id | 框架 | 缺失字段数 |")
        add("|---|---|---|")
        for r in fixture_records:
            add(f"| `{r.record_id}` | {r.framework or '—'} | {len(r.missing_fields)} |")
        add("")
    add("需业务确认的关键问题：")
    add("")
    add("1. Token 价格币种未写明，当前假设 `CNY; pending business confirmation`。")
    add("2. `GPU价格!C` 的“摊销或租金价格/卡/年”覆盖范围（是否含机房、网络、运维）未确认。")
    add("3. H 列“总缓存命中率”的计费语义未确认：物理 KV 命中率 ≠ 可计费缓存命中 Token 比例。")
    add("4. 全部 32 条效能数据来源为 `InferenceX`（第三方），**不是** SGLang/vLLM 官方 benchmark 数据。")
    add("5. 部署策略（TP/EP/PD）仅有字符串描述，没有启动配置或日志证据。")
    add("")

    # ---- 4. 最大理论满载收入案例 ---------------------------------------
    add("## 4. 最大理论满载收入案例")
    add("")
    add(
        "> 本节数字是**理论满载潜力**（theoretical_full_load_potential），"
        "**不是实际收入、不是客户报价、不是可收取金额**。"
    )
    add("")
    ranked = [
        r
        for r in real_results
        if r.total_revenue_10k_per_gpu_year is not None
    ]
    ranked.sort(key=lambda r: r.total_revenue_10k_per_gpu_year, reverse=True)
    if ranked:
        top = ranked[0]
        rec = next((x for x in records if x.record_id == top.benchmark_record_id), None)
        add(f"按**每卡**理论满载年收入排序，最高为 `{top.benchmark_record_id}`：")
        add("")
        add("| 项目 | 值 |")
        add("|---|---|")
        add(f"| 模型 | {rec.model_name if rec else '—'} |")
        add(f"| GPU 型号 | {rec.hardware_model if rec else '—'} |")
        add(f"| 部署卡数 | {top.deployment_gpu_count} |")
        add(f"| 配置并发 | {rec.configured_concurrency if rec else '—'} |")
        add(f"| 输入收入(万元/卡/年) | {_fmt(top.input_revenue_10k_per_gpu_year)} |")
        add(f"| 输出收入(万元/卡/年) | {_fmt(top.output_revenue_10k_per_gpu_year)} |")
        add(f"| 合计收入(万元/卡/年) | {_fmt(top.total_revenue_10k_per_gpu_year)} |")
        add(f"| GPU 年成本(万元/卡/年) | {_fmt(top.gpu_annual_cost_10k)} |")
        add(f"| 营收/成本比 | {_fmt(top.revenue_cost_ratio)} |")
        add(f"| 盈亏平衡变现比例 | {_pct(top.breakeven_monetization_ratio)} |")
        add(f"| 集群总成本(万元/年) | {_fmt(top.total_cluster_cost_10k_year)} |")
        add(f"| 集群满载收入(万元/年) | {_fmt(top.total_cluster_revenue_10k_year)} |")
        add("")
        add(
            "> 这是**理论满载**结果：假设全年 31536000 秒持续跑满该 Benchmark 配置的吞吐，"
            "并把全部 Token 变现。它不是实际营收，也不是客户可收取金额。"
        )
        add("")
        add("营收/成本比 > 1 的记录（在表内 GPU 成本口径下）：")
        add("")
        add("| record_id | 模型 | 营收/成本 | 盈亏平衡变现比例 |")
        add("|---|---|---|---|")
        for r in [x for x in ranked if x.revenue_cost_ratio and x.revenue_cost_ratio > 1][:15]:
            rc = next((x for x in records if x.record_id == r.benchmark_record_id), None)
            add(
                f"| {r.benchmark_record_id} | {rc.model_name if rc else '—'} "
                f"| {_fmt(r.revenue_cost_ratio)} | {_pct(r.breakeven_monetization_ratio)} |"
            )
        add("")
    else:
        add("无可用满载收入结果。")
        add("")

    # ---- 5. 成本敏感性 -------------------------------------------------
    add("## 5. 成本敏感性（变现比例情景）")
    add("")
    if scenario_results:
        by_record: dict[str, list[PricingResult]] = {}
        for r in business_scenarios:
            by_record.setdefault(r.benchmark_record_id, []).append(r)
        shown = sum(len(v) for v in by_record.values())
        add(
            f"业务口径：对 {len(by_record)} 条真实记录生成 {shown} 条情景"
            f"（U = 10/30/50/70/100%，即 {len(by_record)} × "
            f"{max(len(v) for v in by_record.values()) if by_record else 0}）。"
        )
        add("")
        add(
            f"测试口径：人工 `synthetic_fixture` 记录产生 {len(test_scenarios)} 条情景，"
            f"**{'已纳入' if include_test_data else '已排除'}**。"
            + (
                "默认口径下应为 32 × 5 = 160 条；"
                "36 × 5 = 180 条仅在显式 `--include-test-data` 时出现。"
            )
        )
        add("")
        # 反声明放在表格**之前**：160 行的表格下方放免责声明等于没有声明。
        add(
            "> 本节所有数字都是**情景敏感性估算**（scenario_sensitivity_estimate），"
            "**不是实际收入、不是客户报价**；「结余」为负表示该变现比例下收入低于 GPU 成本，"
            "且**未包含**机房、网络、人力、存储、运维等成本，不能作为利润结论。"
        )
        add("")
        add("")
        add("| record_id | U | 情景收入(万元/卡/年) | 减 GPU 成本后结余(万元/卡/年) |")
        add("|---|---|---|---|")
        for rid in sorted(by_record):
            for r in sorted(by_record[rid], key=lambda x: x.effective_monetization_ratio or 0):
                add(
                    f"| {rid} | {_pct(r.effective_monetization_ratio)} "
                    f"| {_fmt(r.scenario_revenue_10k_per_gpu_year)} "
                    f"| {_fmt(r.scenario_surplus_before_other_costs_10k_per_gpu_year)} |"
                )
        add("")
        add(
            "> 结余为负表示该变现比例下收入低于 GPU 成本；该结余**未包含**机房、网络、"
            "人力、存储、运维等成本，不能作为利润结论。"
        )
    else:
        add("本次报告未生成情景结果。")
    add("")

    # ---- 6. 逐条来源说明 -----------------------------------------------
    add("## 6. 逐条来源说明")
    add("")
    add("### 6.1 结果 → 输入 追溯链")
    add("")
    add(
        "每条计算结果都能回溯到：性能记录 + 硬件价格 + Token 单价 + 证据链。"
        "缺失的价格关联不会被猜测补齐，而是留空并在该结果的 `linkage_warnings` 中记录。"
    )
    add("")
    add(
        "| record_id | 硬件价格 ID | Token 价格 ID | 证据链 | SLA | 关联告警 |"
    )
    add("|---|---|---|---|---|---|")
    for r in sorted(real_results, key=lambda x: x.benchmark_record_id):
        warn = "; ".join(r.linkage_warnings) if r.linkage_warnings else "—"
        ev = ", ".join(f"`{e}`" for e in r.evidence_ids) or "—"
        add(
            f"| `{r.benchmark_record_id}` | {r.hardware_price_id or '—'} "
            f"| {r.token_price_id or '—'} | {ev} | `{r.sla_status}` | {warn} |"
        )
    add("")
    sla_na = sum(1 for r in real_results if r.sla_status == "not_evaluable")
    add(
        f"其中 `sla_status = not_evaluable` 共 **{sla_na}** / {len(real_results)} 条，"
        "原因：无 TTFT/TPOT 的 P99 实测数据，且引擎不以其它指标反推 P99。"
    )
    add("")
    add("### 6.2 证据明细")
    add("")
    ev_by_id = {e.evidence_id: e for e in evidences}
    add(f"共 {len(evidences)} 条证据，按 `source_type` 分组列出。")
    add("")
    if official_candidates:
        add("### 6.3 官方 Benchmark 候选证据（partial_evidence）")
        add("")
        add(
            f"共 {len(official_candidates)} 条官方候选。它们**不进入性能数据库、"
            "不参与任何经济性计算**；原因是缺少完整比对必需的配置或指标。"
        )
        add("")
        add("| candidate_id | 发布方 | 框架 | 指标条数 | 吞吐基准? | 硬件绑定 | 缺失字段 |")
        add("|---|---|---|---|---|---|---|")
        for c in official_candidates:
            tp = {True: "是", False: "否（单请求冒烟）", None: "未知"}[c.is_throughput_benchmark]
            add(
                f"| `{c.candidate_id}` | {c.publisher} | {c.framework or '—'} "
                f"| {len(c.metrics)} | {tp} | `{c.hardware_binding}` "
                f"| {', '.join(f'`{f}`' for f in c.missing_fields) or '—'} |"
            )
        add("")
        parsed = [c for c in official_candidates if c.metrics]
        if parsed:
            add("可由证据文件机器解析出指标的候选：")
            add("")
            add("| candidate_id | 指标 | 值 | 原始标签 |")
            add("|---|---|---|---|")
            for c in parsed:
                for k, v in c.metrics.items():
                    add(
                        f"| `{c.candidate_id}` | `{k}` | {v} "
                        f"| {c.raw_labels.get(k, '—')} |"
                    )
            add("")
            add(
                "> 上表数值由 `importers/console_summary.py` 从**存档的原始 HTML** 中解析，"
                "文件 sha256 记录在候选记录中，非人工抄录。"
            )
            add("")
        add(
            "> **已核实的完整官方案例仍然为 0 条**：没有任何候选同时具备完整部署配置"
            "（模型 revision、框架版本、硬件型号与卡数、数据集）与全部必需指标。"
            "详见 `docs/official_coverage_gaps.md`。"
        )
        add("")

    if numeric_candidates:
        add("### 6.4 官方数值型候选（SGLang Cookbook，M7 接入）")
        add("")
        verified = sum(1 for c in numeric_candidates if c.verification.verified_against_source)
        add(
            f"共 {len(numeric_candidates)} 条，其中 **{verified} 条**已与本地存档的官方原文"
            "逐字段机器核对一致（命令参数 + 控制台输出）。全部保持 `partial_evidence`："
            "官方原文未给出 model revision 等配置项。"
        )
        add("")
        add(
            "| record_id | 来源 | 场景 | 配置并发 | 观测并发 | 集群 in/out tok/s "
            "| TTFT P99 | TPOT P99 | 原文核对 | 参考可用 | SLA 可用 | 经济性可用 |"
        )
        add("|---|---|---|---|---|---|---|---|---|---|---|---|")
        for c in numeric_candidates:
            m = c.metrics
            add(
                f"| `{c.record_id}` | {c.source_id} | {c.scenario_label} "
                f"| {c.configured_max_concurrency} | {m.get('observed_concurrency', '—')} "
                f"| {m.get('input_throughput_tok_s', '—')} / {m.get('output_throughput_tok_s', '—')} "
                f"| {m.get('ttft_p99_ms', '—')} | {m.get('tpot_p99_ms', '—')} "
                f"| {'✅' if c.verification.verified_against_source else '❌'} "
                f"| {'是' if c.can_reference_performance else '否'} "
                f"| {'是' if c.can_evaluate_sla else '否'} "
                f"| {'是' if c.can_calculate_pricing else '否'} |"
            )
        add("")
        add(
            "> **集群吞吐是整套 8 卡一起跑出来的**，不是单卡能力；`配置并发` 与 `观测并发`"
            "是两个不同口径（前者来自测试命令 `--max-concurrency`，后者来自控制台的"
            " `Concurrency` 行）。指标口径见 `docs/benchmark_metric_dictionary.md`。"
        )
        add("")
        add(
            "> 这些候选**不参与**本报告第 4、5 节的经济性计算：'经济性可用' 一列全为否，"
            "因为币种与成本范围尚未业务确认，且候选缺 model revision。"
        )
        add("")
    grouped: dict[str, list[Evidence]] = {}
    for e in evidences:
        grouped.setdefault(e.source_type, []).append(e)
    for st in sorted(grouped):
        add(f"### {st}（{len(grouped[st])} 条）")
        add("")
        add("| evidence_id | 发布方 | 位置 | 版本 | 抓取时间 | 原始 sha256 | 质量标记 |")
        add("|---|---|---|---|---|---|---|")
        for e in grouped[st]:
            add(
                f"| `{e.evidence_id}` | {e.publisher or '—'} | {e.location()} "
                f"| {e.source_version or '—'} | {e.retrieved_at or '—'} "
                f"| {(e.raw_sha256 or '—')[:12]} | {', '.join(e.quality_flags) or '—'} |"
            )
        add("")
        notes = [(e.evidence_id, e.notes) for e in grouped[st] if e.notes]
        if notes:
            add("备注（原始值留档）：")
            add("")
            for eid, note in notes:
                add(f"- `{eid}`: {note}")
            add("")

    # ---- 7. 边界声明 ---------------------------------------------------
    add("## 7. 边界与免责声明")
    add("")
    add("- 本引擎不提供任何 SLA 承诺；缺少实测 P99 TTFT/TPOT 时一律标记 `not_evaluable`。")
    add("- `input_tps_cluster` 仅按原 Benchmark 配置的卡数归一化还原，禁止跨 GPU/TP/PD 外推。")
    add("- 引擎不预测扩卡、改 TP、改 P/D 后的吞吐；不自动选择满足 SLA 的 GPU 数量。")
    add("- 情景模式下的 `effective_monetization_ratio` 是敏感性参数，不是实际变现率。")
    add("- 结果哈希 `input_snapshot_hash` 可用于复算校验。")
    add("")
    return "\n".join(L)
