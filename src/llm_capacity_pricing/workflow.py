"""S1：统一评估工作流。

**一份配置 → 一次调用 → 一份结果。**

本模块是"应用层"：只负责按顺序调用四个领域（Sizing / Performance /
Economics / Governance）的既有实现，并把结果与**缺口**汇总成 ``PlanResult``。
它**不实现**任何业务规则 —— 门禁、匹配、TCO 公式仍在各自领域。

关键行为（计划 §3 验收）：

* 领域逐个执行、各自捕获异常：缺成本或缺实测**不会**让整份评估报错，
  只会把对应领域标为 ``not_configured`` / ``missing_data``；
* 需要真实数据的结论（成本、报价资格）绝不用默认值补造；
* 治理领域调用与 CLI 完全相同的门禁函数，不存在"新入口更宽松"的路径。

## 调用链（S2：唯一应用入口）

```
CLI:   plan / phase4-decision ─┐
Agent: plan.evaluate / phase4.decision ─┤
                                        ├─→ workflow.evaluate_plan()        （S1 统一入口）
                                        │   workflow.evaluate_decision()    （跨阶段决策链）
                                        │        │
                                        │        ├─ build_ranking()  → perf.ranker      性能/SLA
                                        │        ├─ services.tco_for / engines.tco      TCO
                                        │        ├─ engines.revenue                     收益情景
                                        │        ├─ engines.quote_gate                  报价门禁
                                        │        └─ decision.{comparison,sensitivity}   比较/预算
                                        └─→ 报告层只做格式化，不再实现业务判断
```

**依赖方向**：``workflow`` → ``services`` / ``engines`` / ``perf`` / ``decision``（单向）。
``services`` 对 ``workflow`` 的引用只在兼容包装函数内部延迟导入，
因此模块层不存在循环依赖。

本模块**不实现**任何业务规则：匹配等级、SLA 门禁、TCO 公式、报价资格
仍由各自领域负责；这里只做输入组织、调用顺序与结果汇总。
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml

from llm_capacity_pricing.schemas.plan import (
    PlanBlocker,
    PlanCandidate,
    PlanRequest,
    PlanResult,
)
from llm_capacity_pricing.services import ServiceError

__all__ = ["load_plan_request", "evaluate_plan"]

#: 四个领域的固定顺序（结果里始终按此顺序出现）
DOMAINS: tuple[str, ...] = ("input", "sizing", "performance", "economics", "governance")


#: 常见校验错误 -> 修正建议（M41：报错要能直接照着改）
_ERROR_HINTS: dict[str, str] = {
    "missing": "该字段必填，请补上",
    "extra_forbidden": "不认识这个字段名，请改用正确字段名",
    "literal_error": "取值不在允许范围内，请改用列出的取值之一",
    "int_parsing": "需要整数",
    "decimal_parsing": "需要数字（可带引号，如 \"0.9\"）",
    "greater_than_equal": "数值超出下限",
    "string_type": "需要字符串",
    "list_type": "需要列表，例如 [1, 2, 4, 8]",
    "dict_type": "需要键值对",
    "model_type": "该段结构不正确",
}

#: 已知字段的常见写错 -> 正确名（用于"是不是想写 X"）
_FIELD_ALIAS_HINTS: dict[str, str] = {
    "template": "template_id",
    "hardware": "hardware_ids（顶层是列表）",
    "model": "model_id",
    "sla_requirement": "sla",
    "cost_template": "cost",
    "benchmark": "cost（成本模板在 cost 段）",
}


def _suggest_field(name: str, known: Sequence[str]) -> str | None:
    """给一个拼错的字段名猜正确名（只提示，不自动改）。"""
    if name in _FIELD_ALIAS_HINTS:
        return _FIELD_ALIAS_HINTS[name]
    import difflib

    close = difflib.get_close_matches(name, list(known), n=1, cutoff=0.75)
    return close[0] if close else None


def _format_config_error(config: str | Path, exc: Exception) -> str:
    """把 Pydantic 校验错误翻译成"文件 + 字段路径 + 修正建议"。"""
    errors = getattr(exc, "errors", None)
    if not callable(errors):
        return f"配置 {config} 校验失败: {exc}"
    known_fields = list(PlanRequest.model_fields)
    lines = [f"配置 {config} 校验失败（{len(errors())} 处）："]
    for error in errors():
        loc = ".".join(str(x) for x in error.get("loc") or ()) or "<顶层>"
        hint = _ERROR_HINTS.get(error.get("type", ""), "")
        message = str(error.get("msg") or "").strip()
        suggestion = _suggest_field(loc.split(".")[-1], known_fields)
        detail = f"  - {loc}: {hint or message}"
        if suggestion:
            detail += f"（是不是想写 `{suggestion}`？）"
        elif message and hint:
            detail += f"（原始信息：{message}）"
        lines.append(detail)
    lines.append(
        "参考 data/examples/scenario_minimal.example.yaml 与 "
        "docs/plan_config_reference.md 的字段说明。"
    )
    return "\n".join(lines)


def load_plan_request(config: str | Path) -> PlanRequest:
    """从 YAML 读取统一评估配置。字段即 ``PlanRequest`` 的结构。

    校验失败时给出**字段路径 + 修正建议**，而不是抛一段 Pydantic 原始堆栈（M41）。
    """
    raw = yaml.safe_load(Path(config).read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ServiceError(f"{config} 顶层必须是映射（键值对），当前是 {type(raw).__name__}")
    try:
        return PlanRequest.model_validate(raw)
    except Exception as exc:  # noqa: BLE001 - 转成带字段路径的提示
        raise ServiceError(_format_config_error(config, exc)) from exc


def _assumptions(request: PlanRequest):
    from llm_capacity_pricing.services import make_assumptions

    r = request.resources
    return make_assumptions(
        per_gpu_usable_bytes=r.per_gpu_usable_bytes,
        usable_fraction_of_nominal=r.usable_fraction,
        usable_memory_source=r.usable_source,
        usable_memory_source_kind=r.usable_source_kind,
        max_context_override=r.max_context_override,
        max_context_override_source=r.max_context_override_source,
        runtime_reserve_fraction=r.runtime_reserve_fraction,
        runtime_reserve_source=r.runtime_reserve_source,
        block_size_tokens=request.advanced.block_size_tokens,
        global_concurrency=request.workload.concurrency,
        context_length_per_request=request.workload.input_len,
        tp_sizes=tuple(request.advanced.tp_sizes),
        dp_sizes=tuple(request.advanced.dp_sizes),
    )


def _workload_signature(request: PlanRequest):
    from llm_capacity_pricing.schemas.performance import WorkloadSignature

    return WorkloadSignature(
        input_len_target=request.workload.input_len,
        output_len_target=request.workload.output_len,
        dataset_name=request.workload.dataset,
        arrival_mode=request.workload.arrival_mode,  # type: ignore[arg-type]
        configured_concurrency=request.workload.concurrency,
    )


def _parse_as_of(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _model_repo_id(model_spec: Any, fallback: str) -> str:
    repo = getattr(model_spec, "repo_id", None)
    return repo or fallback


def _hardware_product_name(hardware_id: str) -> str | None:
    """S3：映射的唯一权威在 sizing/hardware.py。"""
    from llm_capacity_pricing.sizing.hardware import product_name_for_hardware_id

    return product_name_for_hardware_id(hardware_id)


def _target_facts(
    request: PlanRequest, model_repo_id: str, hardware_id: str, candidate: Any
) -> dict[str, Any]:
    adv = request.advanced
    return {
        "model_name": model_repo_id,
        "model_revision": adv.model_revision,
        "weight_precision": candidate.weight_precision,
        "hardware_model": _hardware_product_name(hardware_id),
        "gpu_count": candidate.gpu_count,
        "tp_size": candidate.tp_size,
        "dp_size": candidate.dp_size,
        "framework": adv.framework,
        "framework_version": adv.framework_version,
        "configured_concurrency": request.workload.concurrency,
        "input_len_target": request.workload.input_len,
        "output_len_target": request.workload.output_len,
        "dataset_name": request.workload.dataset,
        "arrival_mode": request.workload.arrival_mode,
    }


def evaluate_plan(
    request: PlanRequest, *, db_path: str | Path = "data/local.db"
) -> PlanResult:
    """执行一次完整评估。四个领域逐个执行，缺输入不报错、只留缺口。"""
    from llm_capacity_pricing.services import (
        open_db,
        plan_deployment_for,
    )

    blockers: list[PlanBlocker] = []
    missing: list[str] = []
    next_data: list[str] = []
    domains: dict[str, str] = {"input": "computed"}

    def block(domain: str, code: str, message: str, candidate_id: str | None = None) -> None:
        blockers.append(
            PlanBlocker(domain=domain, code=code, message=message, candidate_id=candidate_id)
        )

    # ------------------------------------------------------------------
    # 输入与库存
    # ------------------------------------------------------------------
    try:
        db, repo = open_db(db_path)
        try:
            with db.session() as session:
                models = {m.model_id: m for m in repo.list_model_specs(session)}
                runs = repo.list_benchmark_runs(session)
                cost_items = repo.list_cost_catalog_items(session)
                billing_rules = {r.rule_id: r for r in repo.list_billing_rules(session)}
        finally:
            db.dispose()
    except Exception as exc:  # noqa: BLE001
        block("input", "database_unavailable", f"无法读取数据库 {db_path}: {exc}")
        return _blocked_result(request, domains, blockers)

    if request.model_id not in models:
        block(
            "input",
            "model_not_found",
            f"模型中不存在 {request.model_id!r}：请先运行 import-models",
        )
        return _blocked_result(request, domains, blockers)

    model_spec = models[request.model_id]
    model_repo_id = _model_repo_id(model_spec, request.model_id)

    assumptions = _assumptions(request)
    workload = _workload_signature(request)

    # ------------------------------------------------------------------
    # Sizing
    # ------------------------------------------------------------------
    plans: dict[str, Any] = {}
    for hardware_id in request.hardware_ids:
        try:
            plans[hardware_id] = plan_deployment_for(
                db_path,
                model_id=request.model_id,
                hardware_id=hardware_id,
                server_id=request.server_id,
                assumptions=assumptions,
                weight_precision=request.advanced.weight_precision,  # type: ignore[arg-type]
                kv_precision=request.advanced.kv_precision,
            )
        except Exception as exc:  # noqa: BLE001
            block("sizing", "sizing_failed", f"{hardware_id}: {exc}")
    if not plans:
        domains["sizing"] = "error"
        return _blocked_result(request, domains, blockers)
    domains["sizing"] = "computed"

    # ------------------------------------------------------------------
    # Performance（第三阶段结论原样搬运）
    # ------------------------------------------------------------------
    performance_status = "not_configured"
    ranked: dict[str, Any] = {}
    if not runs:
        # M41：无论是否配置了 SLA，只要库里没有实测，就直接告诉用户怎么补
        next_data.append(
            "导入目标配置的性能实测（import-performance）后才可能给出 SLA 结论"
        )
        next_data.append(
            "没有现成实测时：先 `plan-benchmarks` 生成压测清单与 metadata 模板，"
            "按清单采集后再用 `import-performance` 导入"
        )
    if request.sla is None:
        missing.append("sla: 未给出 SLA 需求，无法评价性能是否达标")
    else:
        performance_status = "missing_data" if not runs else "computed"
        for hardware_id, plan in plans.items():
            try:
                ranking = build_ranking(
                    requirement_id=request.sla.requirement_id,
                    candidates=plan["candidates"],
                    runs=runs,
                    requirement=request.sla.model_dump(mode="json"),
                    workload=workload,
                    models=models,
                    target_identity={
                        "model_revision": request.advanced.model_revision,
                        "framework": request.advanced.framework,
                        "framework_version": request.advanced.framework_version,
                    },
                )
            except Exception as exc:  # noqa: BLE001
                block("performance", "ranking_failed", f"{hardware_id}: {exc}")
                performance_status = "error"
                continue
            for entry in ranking.entries:
                ranked[entry.candidate_id] = entry
        if not runs:
            performance_status = "missing_data"
    domains["performance"] = performance_status

    # ------------------------------------------------------------------
    # Economics（成本可选；缺就标 not_configured，不报错）
    # ------------------------------------------------------------------
    economics_status = "not_configured"
    tco_by_candidate: dict[str, Any] = {}
    revenue_by_candidate: dict[str, Any] = {}
    budget_by_candidate: dict[str, str] = {}

    if request.cost is None:
        missing.append("cost: 未给出成本方案模板，无法计算 TCO")
        next_data.append("提供 cost.plan_file / cost.template_id（并导入成本目录）")
    elif not cost_items:
        economics_status = "missing_data"
        next_data.append("运行 import-cost-catalog 导入成本目录")
    else:
        economics_status = "computed"
        from llm_capacity_pricing.engines.tco import compute_tco
        from llm_capacity_pricing.services import (
            build_cost_plan_from_template,
            load_coverage_policy,
            load_cost_plan_templates,
        )

        try:
            templates = load_cost_plan_templates(request.cost.plan_file)
            policy = load_coverage_policy(request.cost.plan_file)
        except Exception as exc:  # noqa: BLE001
            block("economics", "cost_template_invalid", str(exc))
            economics_status = "error"
            templates, policy = {}, None

        if request.cost.template_id not in templates:
            block(
                "economics",
                "cost_template_not_found",
                f"成本模板 {request.cost.template_id!r} 不存在；可用：{sorted(templates)}",
            )
            economics_status = "error"
        else:
            as_of = _parse_as_of(request.cost.as_of)
            if request.cost.as_of and as_of is None:
                block(
                    "economics", "cost_as_of_invalid",
                    f"as_of 不是合法日期: {request.cost.as_of!r}（应为 YYYY-MM-DD）",
                )
                economics_status = "error"
            for hardware_id, plan in plans.items():
                for candidate in plan["candidates"]:
                    if candidate.nodes_required is None:
                        block(
                            "economics",
                            "nodes_required_missing",
                            "候选未给出所需服务器数，无法核算机房相关成本",
                            candidate.candidate_id,
                        )
                        continue
                    try:
                        cost_plan = build_cost_plan_from_template(
                            templates[request.cost.template_id],
                            candidate_id=candidate.candidate_id,
                            hardware_id=candidate.hardware_id,
                            gpu_count=candidate.gpu_count,
                            server_count=candidate.nodes_required,
                            tp_size=candidate.tp_size,
                            dp_size=candidate.dp_size,
                            region=request.cost.region,
                            as_of_date=as_of,
                        )
                        tco_by_candidate[candidate.candidate_id] = compute_tco(
                            cost_plan, cost_items, coverage_policy=policy
                        )
                    except Exception as exc:  # noqa: BLE001
                        block(
                            "economics",
                            "tco_failed",
                            str(exc),
                            candidate.candidate_id,
                        )

    # ---- 业务收益情景（可选）----
    if request.demand is not None and economics_status == "computed":
        from llm_capacity_pricing.services import load_demand_scenarios, revenue_for

        try:
            scenarios = load_demand_scenarios(request.demand.demand_file)
            if request.demand.scenario_id not in scenarios:
                block(
                    "economics",
                    "demand_scenario_not_found",
                    f"情景 {request.demand.scenario_id!r} 不存在；可用：{sorted(scenarios)}",
                )
            else:
                for candidate_id in list(tco_by_candidate):
                    entry = ranked.get(candidate_id)
                    if entry is None:
                        continue
                    try:
                        info = revenue_for(
                            db_path,
                            demand_file=request.demand.demand_file,
                            scenario_id=request.demand.scenario_id,
                            capacity=None,
                        )
                        revenue_by_candidate[candidate_id] = info["revenue"]
                    except Exception as exc:  # noqa: BLE001
                        block("economics", "revenue_failed", str(exc), candidate_id)
        except Exception as exc:  # noqa: BLE001
            block("economics", "demand_file_invalid", str(exc))
    elif request.demand is not None:
        missing.append("demand: 收益情景需要先有可计算的 TCO")

    # ---- 预算（可选）----
    if request.budget is not None and tco_by_candidate:
        from llm_capacity_pricing.decision.sensitivity import check_budget

        for candidate_id, tco in tco_by_candidate.items():
            kind = request.budget.kind
            compared = {
                "one_time_capex": tco.capex_one_time,
                "annual_opex": tco.annual_cash_opex,
                "total_horizon": tco.total_cash_over_horizon,
            }[kind]
            result = check_budget(
                budget_id=request.budget.budget_id,
                budget_amount=request.budget.amount,
                budget_kind=kind,
                compared_value=compared,
                currency=request.budget.currency or tco.currency,
                currency_confirmed=tco.currency_confirmed,
                compared_currency=tco.currency,
            )
            budget_by_candidate[candidate_id] = result.status
    elif request.budget is not None:
        missing.append("budget: 预算比对需要先有可计算的 TCO")

    # ------------------------------------------------------------------
    # Governance（复用与 CLI 完全相同的门禁；缺引用一律不放行）
    # ------------------------------------------------------------------
    governance_status = "not_configured"
    governance_by_candidate: dict[str, Any] = {}
    if request.governance is None:
        missing.append(
            "governance: 未给出 SLA 证据与审批引用，因此不生成报价资格结论"
        )
        next_data.append(
            "如需报价资格：先 record-sla-evidence 生成证据、import-quote-approvals 导入审批，"
            "再在 governance 中引用其 ID"
        )
    elif request.sla is None:
        governance_status = "error"
        block("governance", "sla_required", "给出治理引用时必须同时给出 sla 需求")
    elif request.cost is None:
        governance_status = "not_configured"
        missing.append("governance: 报价资格需要成本快照，请先配置 cost")
    else:
        governance_status = "computed"
        from llm_capacity_pricing.services import quote_eligibility_for
        from llm_capacity_pricing.services import ServiceError as _ServiceError

        #: 前置条件（证据/审批/计费规则）缺失是**配置级**问题，只报一次
        prerequisite_failed = False
        for hardware_id, plan in plans.items():
            if prerequisite_failed:
                break
            for candidate in plan["candidates"]:
                tco = tco_by_candidate.get(candidate.candidate_id)
                if tco is None:
                    continue
                if candidate.nodes_required is None:
                    continue
                facts = _target_facts(request, model_repo_id, hardware_id, candidate)
                try:
                    info = quote_eligibility_for(
                        db_path,
                        candidate_id=candidate.candidate_id,
                        requirement_id=request.sla.requirement_id,
                        facts=facts,
                        sla_evaluation_id=request.governance.sla_evaluation_id,
                        approval_id=request.governance.approval_id,
                        confirmation_id=request.governance.confirmation_id,
                        plan_file=request.cost.plan_file,
                        template_id=request.cost.template_id,
                        hardware_id=candidate.hardware_id,
                        gpu_count=candidate.gpu_count,
                        server_count=candidate.nodes_required,
                        scenario_id=(
                            request.demand.scenario_id if request.demand else None
                        ),
                        demand_file=(
                            request.demand.demand_file if request.demand else None
                        ),
                        billing_rule_id=request.governance.billing_rule_id,
                        # 商业模型名用清单里的 model_id，与计费规则/商务确认对齐
                        quote_model_name=request.model_id,
                        region=request.cost.region,
                        as_of=_parse_as_of(request.cost.as_of),
                    )
                    governance_by_candidate[candidate.candidate_id] = info["eligibility"]
                except _ServiceError as exc:
                    # 证据/审批/规则不存在属于输入问题：报一次，别对每个候选重复
                    governance_status = "error"
                    prerequisite_failed = True
                    block("governance", "prerequisite_missing", str(exc))
                    next_data.append(str(exc))
                    break
                except Exception as exc:  # noqa: BLE001
                    governance_status = "error"
                    block("governance", "eligibility_failed", str(exc), candidate.candidate_id)
    domains["governance"] = governance_status
    domains["economics"] = economics_status

    # ------------------------------------------------------------------
    # 组装候选
    # ------------------------------------------------------------------
    candidates: list[PlanCandidate] = []
    for hardware_id, plan in plans.items():
        for candidate in plan["candidates"]:
            entry = ranked.get(candidate.candidate_id)
            tco = tco_by_candidate.get(candidate.candidate_id)
            revenue = revenue_by_candidate.get(candidate.candidate_id)
            eligibility = governance_by_candidate.get(candidate.candidate_id)

            perf_status = performance_status
            if perf_status == "computed" and entry is None:
                perf_status = "missing_data"

            econ_status = economics_status
            if econ_status == "computed" and tco is None:
                econ_status = "error"

            gov_status = governance_status
            if gov_status == "computed" and eligibility is None:
                gov_status = "error"

            candidates.append(
                PlanCandidate(
                    candidate_id=candidate.candidate_id,
                    hardware_id=candidate.hardware_id,
                    tp_size=candidate.tp_size,
                    dp_size=candidate.dp_size,
                    gpu_count=candidate.gpu_count,
                    nodes_required=candidate.nodes_required,
                    static_status=candidate.feasibility.status,
                    headroom_bytes=candidate.headroom_bytes,
                    static_reasons=list(candidate.feasibility.reasons),
                    performance_status=perf_status,  # type: ignore[arg-type]
                    match_level=entry.match_level if entry else None,
                    sla_status=entry.sla_status if entry else None,
                    rank_bucket=entry.rank_bucket if entry else None,
                    output_throughput_tok_s=(
                        entry.output_throughput_tok_s if entry else None
                    ),
                    ttft_p99_ms=entry.ttft_p99_ms if entry else None,
                    tpot_p99_ms=entry.tpot_p99_ms if entry else None,
                    evidence_run_ids=list(entry.evidence_run_ids) if entry else [],
                    economics_status=econ_status,  # type: ignore[arg-type]
                    plan_id=tco.plan_id if tco else None,
                    cost_basis=tco.cost_basis if tco else None,
                    currency=tco.currency if tco else None,
                    horizon_years=tco.horizon_years if tco else None,
                    capex_one_time=tco.capex_one_time if tco else None,
                    annual_cash_opex=tco.annual_cash_opex if tco else None,
                    annual_total_cost=tco.annual_total_cost if tco else None,
                    total_cash_over_horizon=(
                        tco.total_cash_over_horizon if tco and not tco.incomplete else None
                    ),
                    cost_incomplete=bool(tco.incomplete) if tco else True,
                    coverage_gaps=list(tco.coverage_gaps) if tco else [],
                    annual_revenue=revenue.get("annual_revenue") if revenue else None,
                    capacity_check=revenue.get("capacity_check") if revenue else None,
                    budget_status=budget_by_candidate.get(candidate.candidate_id),
                    governance_status=gov_status,  # type: ignore[arg-type]
                    quote_publishable=bool(
                        eligibility and eligibility["quote_publishable"]
                    ),
                    quote_blockers=list(eligibility["blockers"]) if eligibility else [],
                    notes=[candidate.summary] if candidate.summary else [],
                )
            )

    external_references = _external_references(runs, ranked, target_hint=request)
    recommendations = _recommendations(candidates, domains)
    blockers = _dedupe_blockers(blockers)
    status = _overall_status(domains, blockers)

    return PlanResult(
        request_id=request.request_id,
        status=status,
        domains=domains,
        request_echo={
            "model_id": request.model_id,
            "model_repo_id": model_repo_id,
            "hardware_ids": list(request.hardware_ids),
            "server_id": request.server_id,
            "workload": request.workload.model_dump(mode="json"),
            "sla_requirement_id": request.sla.requirement_id if request.sla else None,
            "cost_template": (
                f"{request.cost.plan_file}#{request.cost.template_id}"
                if request.cost
                else None
            ),
            "demand_scenario": request.demand.scenario_id if request.demand else None,
            "candidate_count": len(candidates),
            "billing_rules_in_db": sorted(billing_rules),
        },
        candidates=candidates,
        external_references=external_references,
        blockers=blockers,
        missing_inputs=missing,
        next_data_needed=list(dict.fromkeys(next_data)),
        recommendations=recommendations,
        summary={
            "sizing": plan_status_counts(plans),
            "performance_buckets": _bucket_counts(candidates),
            "publishable": [c.candidate_id for c in candidates if c.quote_publishable],
            "cost_complete": [
                c.candidate_id
                for c in candidates
                if c.economics_status == "computed" and not c.cost_incomplete
            ],
            # M40：可比较 / 不可比较候选（含原因），供报告与下游直接消费
            "cost_comparability": (
                _cost_comparability_summary(candidates)
                if economics_status == "computed"
                else {}
            ),
        },
        limitations=_limitations(domains),
        generated_at=datetime.now(timezone.utc),
    )


def _dedupe_blockers(blockers: Sequence[PlanBlocker]) -> list[PlanBlocker]:
    """同一领域 + 同一原因码 + 同一说明只保留一条（候选级原因仍按候选区分）。"""
    seen: set[tuple[str, str, str, str | None]] = set()
    out: list[PlanBlocker] = []
    for b in blockers:
        key = (b.domain, b.code, b.message, b.candidate_id)
        if key in seen:
            continue
        seen.add(key)
        out.append(b)
    return out


def plan_status_counts(plans: dict[str, Any]) -> dict[str, int]:
    out: dict[str, int] = {}
    for plan in plans.values():
        for status, count in (plan.get("status_counts") or {}).items():
            out[status] = out.get(status, 0) + count
    return out


def _bucket_counts(candidates: Sequence[PlanCandidate]) -> dict[str, int]:
    out: dict[str, int] = {}
    for c in candidates:
        key = c.rank_bucket or f"no_ranking({c.performance_status})"
        out[key] = out.get(key, 0) + 1
    return out


def _recommendations(
    candidates: Sequence[PlanCandidate], domains: dict[str, str]
) -> list[str]:
    """只给**可解释**的比较结论，不做跨维度综合评分。"""
    out: list[str] = []
    feasible = [c for c in candidates if c.static_status not in ("infeasible", "unsupported")]
    if not feasible:
        out.append("没有静态可行的候选：当前资源假设下无法部署，需要调整卡数或上下文长度")
    else:
        smallest = min(feasible, key=lambda c: (c.gpu_count, c.tp_size))
        out.append(
            f"资源占用最小的静态可行候选：`{smallest.candidate_id}`"
            f"（{smallest.gpu_count} 卡，TP{smallest.tp_size}×DP{smallest.dp_size}）"
            "—— 这是**资源**结论，不含性能与经济性判断"
        )
    verified = [c for c in candidates if c.sla_status == "observed_pass"]
    if verified:
        out.append(
            "具备匹配实测且达标 SLA 的候选："
            + "、".join(f"`{c.candidate_id}`" for c in verified)
        )
    else:
        out.append(
            "没有候选具备匹配实测（SLA 全部不可评估）：这属于**证据缺口**，"
            "需要用目标配置补测，不能由估算替代"
        )
    affordable = [c for c in candidates if c.budget_status == "within_budget"]
    if affordable:
        out.append(
            "在预算内的候选：" + "、".join(f"`{c.candidate_id}`" for c in affordable)
        )
    if domains.get("economics") == "computed":
        out.extend(_cost_recommendations(candidates))
    buckets = {}
    for c in candidates:
        buckets[c.rank_bucket or c.performance_status] = (
            buckets.get(c.rank_bucket or c.performance_status, 0) + 1
        )
    if buckets:
        from llm_capacity_pricing.reporting.plan_report import _BUCKET_LABEL

        readable = "、".join(
            f"{_BUCKET_LABEL.get(k, k)} {v} 个" for k, v in sorted(buckets.items())
        )
        out.append(f"性能证据分布：{readable}")
    out.append(
        "本结果**不包含**跨维度综合评分：成本、性能与报价资格三者独立"
    )
    return out


def _external_references(
    runs: Sequence[Any], ranked: Mapping[str, Any], *, target_hint: Any
) -> list[Any]:
    """列出库里的**外部公开实测**，并说明它们为什么不能用于本需求（Demo）。

    与候选性能结论严格分开：这些数据不参与 SLA 判定，也不进报价门禁。
    只有公开来源（厂商/项目发布）才进入本列表 —— 内部实测归属候选链路。
    """
    from llm_capacity_pricing.schemas.plan import PlanExternalReference

    # ranked 的值是 RankedDeployment：它携带**全部**支撑 run（聚合时不止一条）
    matched_run_ids = {rid for r in ranked.values() for rid in (r.evidence_run_ids or [])}
    out: list[PlanExternalReference] = []
    for run in runs:
        if run.run_id in matched_run_ids:
            continue  # 已被候选采用，不重复列
        if run.data_trust not in (
            "measured_vendor_published",
            "measured_third_party",
        ):
            continue
        sig = run.signature
        profile = run.profile
        reasons: list[str] = []
        if not sig.model_revision:
            reasons.append("公开来源未给出模型版本（revision）：无法确认与你的模型版本一致")
        if not sig.weight_precision:
            reasons.append("公开来源未给出权重精度：无法确认与你的部署精度一致")
        if not profile.configured_concurrency:
            reasons.append("公开来源未给出配置并发：无法与你的负载并发对齐")
        if sig.model_name and target_hint.model_id and sig.model_name != target_hint.model_id:
            reasons.append(
                f"模型不同（公开数据为 {sig.model_name}）：不能代表你要部署的模型"
            )
        out.append(
            PlanExternalReference(
                run_id=run.run_id,
                publisher=(run.notes or None),
                source_url=None,
                source_file=run.raw_file,
                source_sha256=run.raw_sha256,
                data_trust=run.data_trust,
                model_name=sig.model_name,
                model_revision=sig.model_revision,
                weight_precision=sig.weight_precision,
                hardware_model=sig.hardware_model,
                gpu_count=sig.gpu_count,
                tp_size=sig.tp_size,
                dp_size=sig.dp_size,
                framework=sig.framework,
                framework_version=sig.framework_version,
                dataset_name=sig.workload.dataset_name,
                input_len_target=sig.workload.input_len_target,
                output_len_target=sig.workload.output_len_target,
                configured_concurrency=sig.workload.configured_concurrency,
                ttft_p99_ms=profile.ttft_p99_ms,
                tpot_p99_ms=profile.tpot_p99_ms,
                output_throughput_tok_s=profile.output_throughput_tok_s,
                match_level=None,
                not_usable_reasons=reasons
                or ["该数据未被本需求的候选采用（见匹配说明）"],
                quality_flags=list(run.quality_flags),
            )
        )
    return sorted(out, key=lambda r: r.run_id)


def _cost_profile(candidate: PlanCandidate) -> Any:
    """把候选的成本字段归约成可比性判据的输入（S3/M35：复用唯一权威规则）。"""
    from llm_capacity_pricing.decision.comparison import CostProfile

    return CostProfile(
        currency=candidate.currency,
        cost_basis=candidate.cost_basis,
        horizon_years=candidate.horizon_years,
        incomplete=candidate.cost_incomplete,
        total_cash_over_horizon=candidate.total_cash_over_horizon,
    )


def _cost_comparability_summary(candidates: Sequence[PlanCandidate]) -> dict[str, Any]:
    """M40：把"可比较 / 不可比较"候选结构化输出（含不可比较原因）。

    与 `_cost_recommendations` 共用同一套判据（``cost_comparability``），
    因此报告文字与结构化结果不会打架。
    """
    from llm_capacity_pricing.decision.comparison import cost_comparability

    profiles = {c.candidate_id: _cost_profile(c) for c in candidates}
    reference_id = None
    reference_profile = None
    for candidate in candidates:
        profile = profiles[candidate.candidate_id]
        if not profile.incomplete and profile.total_cash_over_horizon is not None:
            reference_id, reference_profile = candidate.candidate_id, profile
            break
    comparable: list[str] = []
    not_comparable: dict[str, str] = {}
    if reference_profile is not None:
        for candidate in candidates:
            kind, _notes = cost_comparability(
                profiles[candidate.candidate_id], reference_profile
            )
            if kind == "comparable":
                comparable.append(candidate.candidate_id)
            elif candidate.candidate_id != reference_id:
                not_comparable[candidate.candidate_id] = kind
    else:
        for candidate in candidates:
            kind, _notes = cost_comparability(
                profiles[candidate.candidate_id], None
            )
            not_comparable[candidate.candidate_id] = kind
    return {
        "reference_candidate_id": reference_id,
        "comparable": sorted(comparable),
        "not_comparable": dict(sorted(not_comparable.items())),
    }


def _cost_recommendations(candidates: Sequence[PlanCandidate]) -> list[str]:
    """成本高低结论（M35）。

    复用第四阶段 ``cost_comparability``：**跨币种、跨买断/租赁口径、跨计算周期，
    以及全周期金额缺失**的候选一律不参与比较 —— 尤其不得把缺失当成 0，
    否则"最便宜"会落在根本没有成本数据的候选上。
    """
    from llm_capacity_pricing.decision.comparison import cost_comparability

    out: list[str] = []
    profiles = {c.candidate_id: _cost_profile(c) for c in candidates}

    # 参照方案：第一个"成本完整且全周期金额已知"的候选
    reference_id = None
    reference_profile = None
    for candidate in candidates:
        profile = profiles[candidate.candidate_id]
        if not profile.incomplete and profile.total_cash_over_horizon is not None:
            reference_id, reference_profile = candidate.candidate_id, profile
            break

    if reference_profile is None:
        out.append(
            "没有任何候选具备**完整且金额已知**的成本结论："
            "本结果不给出成本高低结论"
        )
        return out

    comparable: list[PlanCandidate] = []
    reasons: dict[str, str] = {}
    for candidate in candidates:
        kind, _notes = cost_comparability(profiles[candidate.candidate_id], reference_profile)
        if kind == "comparable":
            comparable.append(candidate)
        elif candidate.candidate_id != reference_id:
            reasons[candidate.candidate_id] = kind

    if len(comparable) < 2:
        out.append(
            "可比且成本完整的候选不足 2 个：不给出最低成本候选"
            f"（参照方案 `{reference_id}`"
            f"，币种 {reference_profile.currency} / 口径 {reference_profile.cost_basis}"
            f" / {reference_profile.horizon_years} 年）"
        )
        if reasons:
            out.append(
                "不可比候选及原因："
                + "；".join(f"`{cid}` {kind}" for cid, kind in sorted(reasons.items()))
            )
        return out

    cheapest = min(comparable, key=lambda c: c.total_cash_over_horizon)
    out.append(
        f"成本可比者中全周期成本最低：`{cheapest.candidate_id}`"
        f"（与参照方案 `{reference_id}` 同为 {reference_profile.currency} / "
        f"{reference_profile.cost_basis} / {reference_profile.horizon_years} 年）"
        "—— 仅成本结论，不代表性能或可报价"
    )
    # M40：成本最低 ≠ 最优部署。缺性能证据时把这句话说死。
    if cheapest.sla_status not in ("observed_pass", "observed_fail"):
        out.append(
            f"⚠️ `{cheapest.candidate_id}` **没有可用的性能实测**"
            f"（SLA {cheapest.sla_status or '不可评估'}）："
            "**不得**把它称为最优部署 —— 成本低与性能达标是两个独立维度，"
            "缺性能证据的方案需要先补测"
        )
    if reasons:
        out.append(
            "不可比候选及原因："
            + "；".join(f"`{cid}` {kind}" for cid, kind in sorted(reasons.items()))
            + "（跨币种/口径/周期或金额缺失，不参与成本排序）"
        )
    return out


def _overall_status(domains: dict[str, str], blockers: Sequence[PlanBlocker]) -> str:
    if not blockers and all(v == "computed" for v in domains.values()):
        return "ok"
    if any(b.domain in ("input", "sizing") for b in blockers):
        return "blocked"
    if any(v in ("not_configured", "missing_data", "error") for v in domains.values()):
        return "partial"
    return "ok"


def _limitations(domains: dict[str, str]) -> list[str]:
    out = [
        "结论按领域独立给出：静态可行 ≠ 性能达标 ≠ 报价可发布",
        "未提供的数据一律标为未配置/缺失，不使用默认值补齐",
    ]
    if domains.get("performance") != "computed":
        out.append("性能与 SLA 领域未产出结论：不得据此声称产能或时延承诺")
    if domains.get("economics") != "computed":
        out.append("经济性领域未产出结论：不得据此比较成本高低")
    if domains.get("governance") != "computed":
        out.append("治理领域未产出结论：正式报价资格未被评估（默认不可发布）")
    return out


def _blocked_result(
    request: PlanRequest, domains: dict[str, str], blockers: list[PlanBlocker]
) -> PlanResult:
    filled = {name: domains.get(name, "not_configured") for name in DOMAINS}
    return PlanResult(
        request_id=request.request_id,
        status="blocked",
        domains=filled,
        request_echo={
            "model_id": request.model_id,
            "hardware_ids": list(request.hardware_ids),
        },
        candidates=[],
        blockers=blockers,
        missing_inputs=[],
        next_data_needed=["修正上面的阻断原因后重新执行"],
        recommendations=[],
        summary={},
        limitations=["评估未能开始：输入或库存存在阻断问题"],
        generated_at=datetime.now(timezone.utc),
    )


# --------------------------------------------------------------------------
# 应用层：跨阶段决策编排（原 services.phase4_decision_for）
#
# 迁移说明（S2）：这段编排把第二/三/四阶段串起来，属于**应用层职责**，
# 因此从 services.py 搬到本模块。services.phase4_decision_for 保留为兼容入口，
# 转发到这里；业务规则仍在各领域内，本函数只做调用与汇总。
# --------------------------------------------------------------------------


def build_ranking(
    *,
    requirement_id: str,
    candidates: Sequence[Any],
    runs: Sequence[Any],
    requirement: dict[str, Any],
    policy: Any = None,
    workload: Any,
    models: dict[str, Any],
    target_identity: dict[str, Any],
) -> Any:
    """调用第三阶段排序器的**唯一**入口（S2/S3：避免多处重复编排）。

    应用层只负责组织输入；匹配等级、SLA 门禁与分桶规则仍在领域内实现。
    """
    from llm_capacity_pricing.perf.ranker import rank_deployments
    from llm_capacity_pricing.schemas.performance import SlaPolicy, SlaRequirement

    return rank_deployments(
        requirement_id=requirement_id,
        candidates=candidates,
        runs=runs,
        sla_requirement=SlaRequirement.model_validate(requirement),
        policy=policy or SlaPolicy(),
        workload=workload,
        model_by_id=models,
        target_identity=target_identity,
    )


def evaluate_decision(
    db_path: str | Path,
    *,
    requirement: dict[str, Any],
    model_id: str,
    hardware_id: str,
    plan_file: str | Path,
    template_id: str,
    assumptions: Any,
    server_id: str | None = None,
    policy: Any = None,
    input_len_target: int | None = None,
    output_len_target: int | None = None,
    dataset_name: str | None = None,
    model_revision: str | None = None,
    framework: str | None = None,
    framework_version: str | None = None,
    weight_precision: Any = None,
    kv_precision: str = "bf16",
    demand_file: str | Path | None = None,
    scenario_id: str | None = None,
    budgets: Sequence[dict[str, Any]] = (),
) -> dict[str, Any]:
    """M31/M32：把第二/三/四阶段串成一条可审计的部署决策链。

    链路：静态候选 → 性能与 SLA（只读第三阶段结论）→ TCO → 收益情景 →
    报价门禁 → 多候选比较 + 预算/敏感性/盈亏平衡。

    任何一步缺证据都不会被"补齐"：缺就留空并写明原因。
    """
    from llm_capacity_pricing.decision.comparison import CandidateEconomics, compare_deployments
    from llm_capacity_pricing.decision.sensitivity import (
        analyze_breakeven,
        analyze_sensitivity,
        check_budget,
    )
    from llm_capacity_pricing.engines.quote_gate import evaluate_quote_eligibility
    from llm_capacity_pricing.engines.revenue import compute_revenue
    from llm_capacity_pricing.engines.tco import build_cash_flow, compute_tco
    from llm_capacity_pricing.schemas.commercial import DemandPeriod, RevenueScenario
    from llm_capacity_pricing.schemas.performance import (
        SlaRequirement,
        TargetRequirement,
        WorkloadSignature,
    )
    from llm_capacity_pricing.services import (
        PHASE4_SYNTHETIC_NOTICE,
        _diagnostic_eligibility,
        build_cost_plan_from_template,
        capacity_evidence_for,
        load_cost_plan_templates,
        load_coverage_policy,
        load_demand_scenarios,
        open_db,
        plan_deployment_for,
    )

    plan = plan_deployment_for(
        db_path,
        model_id=model_id,
        hardware_id=hardware_id,
        server_id=server_id,
        assumptions=assumptions,
        weight_precision=weight_precision,
        kv_precision=kv_precision,
    )
    db, repo = open_db(db_path)
    try:
        with db.session() as session:
            runs = repo.list_benchmark_runs(session)
            models = {m.model_id: m for m in repo.list_model_specs(session)}
            items = repo.list_cost_catalog_items(session)
            rules = {r.rule_id: r for r in repo.list_billing_rules(session)}
    finally:
        db.dispose()

    sla_req = SlaRequirement.model_validate(requirement)
    workload = WorkloadSignature(
        input_len_target=input_len_target or assumptions.context_length_per_request,
        output_len_target=output_len_target,
        dataset_name=dataset_name,
        configured_concurrency=assumptions.global_concurrency,
    )
    target_identity = {
        "model_revision": model_revision,
        "framework": framework,
        "framework_version": framework_version,
    }
    ranking = build_ranking(
        requirement_id=sla_req.requirement_id,
        candidates=plan["candidates"],
        runs=runs,
        requirement=requirement,
        policy=policy,
        workload=workload,
        models=models,
        target_identity=target_identity,
    )

    if not items:
        raise ServiceError("成本目录为空：请先运行 import-cost-catalog")

    templates = load_cost_plan_templates(plan_file)
    if template_id not in templates:
        raise ServiceError(f"未知模板 {template_id!r}；可用：{sorted(templates)}")
    template = templates[template_id]
    coverage_policy = load_coverage_policy(plan_file)

    scenario = None
    rule = None
    if demand_file and scenario_id:
        raw = load_demand_scenarios(demand_file).get(scenario_id)
        if raw is None:
            raise ServiceError(f"未知情景 {scenario_id!r}")
        scenario = RevenueScenario(
            scenario_id=raw["scenario_id"],
            model_name=raw["model_name"],
            billing_rule_id=raw["billing_rule_id"],
            days_per_year=raw.get("days_per_year", 365),
            periods_cover=raw.get("periods_cover", "one_day"),
            scope_notes=list(raw.get("scope_notes") or []),
            periods=[DemandPeriod.model_validate(p) for p in raw["periods"]],
        )
        rule = rules.get(scenario.billing_rule_id)
        if rule is None:
            raise ServiceError(f"计费规则 {scenario.billing_rule_id!r} 不在库中")

    ranked_by_id = {e.candidate_id: e for e in ranking.entries}
    economics: list[CandidateEconomics] = []
    sensitivities: list[Any] = []
    breakevens: list[Any] = []
    budget_checks: list[Any] = []
    per_candidate: dict[str, Any] = {}
    tco_details: list[dict[str, Any]] = []
    revenue_representative: Any = None

    for candidate in plan["candidates"]:
        server_count = candidate.nodes_required
        if server_count is None:
            per_candidate[candidate.candidate_id] = {
                "reason": "nodes_required_missing: 候选未给出所需服务器数，无法核算机房相关成本"
            }
            economics.append(CandidateEconomics(candidate_id=candidate.candidate_id))
            continue

        cost_plan = build_cost_plan_from_template(
            template,
            candidate_id=candidate.candidate_id,
            hardware_id=candidate.hardware_id,
            gpu_count=candidate.gpu_count,
            server_count=server_count,
            tp_size=candidate.tp_size,
            dp_size=candidate.dp_size,
        )
        tco = compute_tco(cost_plan, items, coverage_policy=coverage_policy)

        revenue = None
        if scenario is not None and rule is not None:
            evidence = capacity_evidence_for(
                db_path,
                model_name=_signature_model_name(candidate, models),
                hardware_model=_candidate_hardware_name(candidate),
                gpu_count=candidate.gpu_count,
                tp_size=candidate.tp_size,
                dp_size=candidate.dp_size,
                weight_precision=candidate.weight_precision,
                configured_concurrency=assumptions.global_concurrency,
                input_len_target=input_len_target
                or assumptions.context_length_per_request,
                output_len_target=output_len_target,
                dataset_name=dataset_name,
                model_revision=model_revision,
                framework=framework,
                framework_version=framework_version,
            )
            revenue = compute_revenue(scenario, rule, capacity_evidence=evidence)

        ranked = ranked_by_id.get(candidate.candidate_id)
        # V4.1 F4.1：决策链的资格结论也**只能**来自持久化证据。
        # 这里没有证据 ID，因此一律走诊断入口，结论固定为"不可发布"。
        eligibility = _diagnostic_eligibility(
            candidate_id=candidate.candidate_id,
            tco=tco,
            sla_status=ranked.sla_status if ranked else None,
            match_level=ranked.match_level if ranked else None,
            plan_id=cost_plan.plan_id,
        )

        cash_flow = build_cash_flow(
            tco,
            annual_revenue=revenue.annual_revenue if revenue else None,
            revenue_basis="scenario_only" if revenue else "none",
        )
        economics.append(
            CandidateEconomics(
                candidate_id=candidate.candidate_id,
                plan=cost_plan,
                tco=tco,
                revenue=revenue,
                eligibility=eligibility,
            )
        )
        sensitivities.append(analyze_sensitivity(plan=cost_plan, items=items))
        breakevens.append(analyze_breakeven(tco=tco, revenue=revenue, rule=rule))
        for budget in budgets:
            kind = budget.get("budget_kind", "total_horizon")
            compared = {
                "one_time_capex": tco.capex_one_time,
                "annual_opex": tco.annual_cash_opex,
                "total_horizon": tco.total_cash_over_horizon,
            }[kind]
            budget_checks.append(
                check_budget(
                    budget_id=f"{budget['budget_id']}::{candidate.candidate_id}",
                    budget_amount=(
                        Decimal(str(budget["budget_amount"]))
                        if budget.get("budget_amount") is not None
                        else None
                    ),
                    budget_kind=kind,
                    compared_value=compared,
                    currency=tco.currency,
                    currency_confirmed=tco.currency_confirmed,
                )
            )
        tco_details.append(
            {
                "plan": cost_plan.model_dump(mode="json"),
                "tco": tco.model_dump(mode="json"),
                "cash_flow": cash_flow.model_dump(mode="json"),
                "revenue": revenue.model_dump(mode="json") if revenue else None,
            }
        )
        if revenue_representative is None and revenue is not None:
            revenue_representative = revenue.model_dump(mode="json")
        per_candidate[candidate.candidate_id] = {
            "plan_id": cost_plan.plan_id,
            "tco_incomplete": tco.incomplete,
            "total_cash_over_horizon": tco.total_cash_over_horizon,
            "annual_total_cost": tco.annual_total_cost,
            "annual_revenue": revenue.annual_revenue if revenue else None,
            "capacity_check": revenue.capacity_check if revenue else None,
            "quote_publishable": eligibility.quote_publishable,
            "cash_flow_npv": cash_flow.npv,
            "cash_flow_payback_year": cash_flow.payback_year,
        }

    comparison = compare_deployments(
        requirement_id=sla_req.requirement_id,
        ranking=ranking,
        economics=economics,
    )

    limitations = [
        "成本数据为合成示例或待确认状态时，TCO 结论只用于验证计算链路，不得对外报价",
        "性能与 SLA 结论原样取自第三阶段，本阶段不做重新判定",
        "报价资格四维独立，任一未过即不可发布；本阶段不提供任何绕过参数",
        "没有可信实测容量时，收入情景只作情景分析，不构成产能承诺",
        "不产出跨维度的综合评分或加权排名",
    ]
    return {
        "requirement_id": sla_req.requirement_id,
        "ranking": ranking.model_dump(mode="json"),
        "comparison": comparison.model_dump(mode="json"),
        "eligibilities": [
            e.model_dump(mode="json")
            for e in (ec.eligibility for ec in economics)
            if e is not None
        ],
        "budget_checks": [b.model_dump(mode="json") for b in budget_checks],
        "sensitivity": [x.model_dump(mode="json") for x in sensitivities],
        "breakeven": [b.model_dump(mode="json") for b in breakevens],
        "per_candidate": per_candidate,
        "tco_details": tco_details,
        "revenue": revenue_representative,
        "limitations": limitations,
        "assumptions_notice": PHASE4_SYNTHETIC_NOTICE,
    }


def _signature_model_name(candidate: DeploymentCandidate, models: dict[str, Any]) -> str:
    spec = models.get(candidate.model_id)
    repo_id = getattr(spec, "repo_id", None) if spec else None
    return repo_id or candidate.model_id


def _candidate_hardware_name(candidate: DeploymentCandidate) -> str | None:
    """S3：映射的唯一权威在 sizing/hardware.py。"""
    from llm_capacity_pricing.sizing.hardware import product_name_for_hardware_id

    return product_name_for_hardware_id(candidate.hardware_id)


