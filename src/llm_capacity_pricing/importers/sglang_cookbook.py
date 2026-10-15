"""M7：接入 SGLang Cookbook 官方**数值型** Benchmark 候选。

数据流（每一步都可追溯）：

1. 只读输入：``data/incoming/sglang_official_benchmark_candidates_20261015.json``
   （2 个来源对象、12 条候选记录，自带 ``quality_status=partial_evidence``）。
2. 官方原文快照：``data/raw/official/sglang_cookbook_*.mdx``，逐字节校验
   Git Blob SHA-1（声明的对象标识）与本地 SHA256（文件哈希）**两者都要**，
   二者语义不同、不可互相顶替。
3. **机器核对**：从原文按章节锚点（``5.1.1.1`` 等）取出测试命令与控制台输出块，
   与 JSON 里的数值逐字段比对，形成 :class:`CandidateVerification`。
   JSON 与原文不一致时**以原文为准并记录差异**，不做"取其一"的静默处理。
4. 环境字段（硬件型号 / 卡数 / TP / EP）同样对着原文核对：原文没写明的，
   标记为"仅候选文件声明"，不写成已核实。

严格性：本模块**不会**把候选升级为 ``official_benchmark``；
``quality_status`` 恒为 ``partial_evidence``，``model_revision`` 保持 ``None``。
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from llm_capacity_pricing.engines.applicability import (
    PriceContext,
    assess_applicability,
)
from llm_capacity_pricing.engines.metric_semantics import (
    metric_definition_ids,
    label_provenance_issues,
    throughput_consistency,
)
from llm_capacity_pricing.importers.console_summary import extract_summary_blocks
from llm_capacity_pricing.schemas.evidence import Evidence
from llm_capacity_pricing.schemas.official import (
    CandidateVerification,
    SglangNumericCandidate,
)

#: 两个来源的权威描述。``evidence_sha256`` 是**本地快照文件**的哈希，
#: ``source_git_blob_sha`` 是上游 Git 对象的 SHA-1，两者语义不同。
SOURCE_SPECS: dict[str, dict[str, Any]] = {
    "sglang_qwen3_cookbook": {
        "publisher": "sgl-project/sglang",
        "source_url": (
            "https://github.com/sgl-project/sglang/blob/main/"
            "docs/cookbook/autoregressive/Qwen/Qwen3.mdx"
        ),
        "raw_url": (
            "https://raw.githubusercontent.com/sgl-project/sglang/main/"
            "docs/cookbook/autoregressive/Qwen/Qwen3.mdx"
        ),
        "source_path": "docs/cookbook/autoregressive/Qwen/Qwen3.mdx",
        "source_git_blob_sha": "580640bd1b9ab74fb5ea0be6120050a99fa93f6d",
        "evidence_file": "data/raw/official/sglang_cookbook_qwen3_2026-10-15.mdx",
        "evidence_sha256": "23d8e61ce899920e5606ca80c30c292a88cab02321c1bd3c610a19dba0b27425",
        "retrieved_at": "2026-10-15T14:15:00+08:00",
        "expected_records": 9,
        "model_name": "Qwen/Qwen3-235B-A22B-Instruct-2507",
        "framework_version_in_doc": "0.5.6",
    },
    "sglang_qwen3_coder_cookbook": {
        "publisher": "sgl-project/sglang",
        "source_url": (
            "https://github.com/sgl-project/sglang/blob/main/"
            "docs/cookbook/autoregressive/Qwen/Qwen3-Coder.mdx"
        ),
        "raw_url": (
            "https://raw.githubusercontent.com/sgl-project/sglang/main/"
            "docs/cookbook/autoregressive/Qwen/Qwen3-Coder.mdx"
        ),
        "source_path": "docs/cookbook/autoregressive/Qwen/Qwen3-Coder.mdx",
        "source_git_blob_sha": "a743f7ba3eb3a204bf9f69ffc7d21d3311fa7703",
        "evidence_file": "data/raw/official/sglang_cookbook_qwen3_coder_2026-10-15.mdx",
        "evidence_sha256": "67ca6704360ef1a87cc3259dd12659966753f1280f7ad2fa52a7a1911b85ca1e",
        "retrieved_at": "2026-10-15T14:15:00+08:00",
        "expected_records": 3,
        "model_name": "Qwen/Qwen3-Coder-480B-A35B-Instruct-FP8",
        "framework_version_in_doc": "0.5.7",
    },
}

#: 候选 JSON 里的指标名 -> 控制台块里的规范字段名
METRIC_MAP: dict[str, str] = {
    "successful_requests": "success_count",
    "benchmark_duration_s": "duration_s",
    "total_input_tokens": "total_input_tokens",
    "total_generated_tokens": "total_output_tokens",
    "input_token_throughput_tps": "input_throughput_tok_s",
    "output_token_throughput_tps": "output_throughput_tok_s",
    "ttft_p99_ms": "ttft_p99_ms",
    "tpot_p99_ms": "tpot_p99_ms",
    "tpot_mean_ms": "tpot_mean_ms",
    "achieved_concurrency": "observed_concurrency",
}

#: 候选 JSON 工作负载字段 -> 测试命令参数
WORKLOAD_ARG_MAP: dict[str, str] = {
    "dataset_name": "--dataset-name",
    "input_len_target": "--random-input-len",
    "output_len_target": "--random-output-len",
    "configured_max_concurrency": "--max-concurrency",
    "num_prompts": "--num-prompts",
}

#: 每个候选记录在 ``metrics`` 中要求的字段（缺任一则登记缺口）
REQUIRED_METRICS = tuple(METRIC_MAP.values())

SECTION_RE = re.compile(r"^(#{3,6})\s+(\d+(?:\.\d+)+)\s*(.*)$", re.MULTILINE)
#: 围栏开启行：3 个及以上反引号 + 可选信息串
_FENCE_OPEN_RE = re.compile(r"^(`{3,})[ \t]*([^\n`]*)$", re.MULTILINE)
_HW_COUNT_RE = re.compile(r"\((\d+)x\)")
_TP_RE = re.compile(r"--tp\s+(\d+)")
_EP_RE = re.compile(r"--ep\s+(\d+)")


def iter_fences(text: str) -> list[dict[str, Any]]:
    """按 CommonMark 规则扫描代码围栏。

    关键点：**闭合围栏的反引号数量必须不少于开启围栏**。SGLang Cookbook 里存在
    ````text Output（4 个反引号）且内部再嵌 ```python 的写法；若按固定 3 个反引号
    匹配，会把闭合行当成新的开启行，导致其后所有块配对整体错位（实测会漏掉 6 个
    官方结果块）。
    """
    out: list[dict[str, Any]] = []
    pos = 0
    while True:
        m = _FENCE_OPEN_RE.search(text, pos)
        if m is None:
            break
        ticks = m.group(1)
        info = (m.group(2) or "").strip()
        close_re = re.compile(r"^`{%d,}[ \t]*$" % len(ticks), re.MULTILINE)
        cm = close_re.search(text, m.end())
        if cm is None:
            pos = m.end()
            continue
        out.append(
            {
                "ticks": len(ticks),
                "info": info,
                "body": text[m.end() : cm.start()],
                "start": m.start(),
                "end": cm.end(),
            }
        )
        pos = cm.end()
    return out


class SglangCookbookError(RuntimeError):
    """候选文件或来源证据不满足导入前提。"""


def sha256_file(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_incoming(path: str | Path) -> dict[str, Any]:
    """读取只读候选文件并做最基本的结构校验。"""
    p = Path(path)
    if not p.exists():
        raise SglangCookbookError(f"候选文件不存在: {p}")
    payload = json.loads(p.read_text(encoding="utf-8"))
    records = payload.get("records")
    if not isinstance(records, list) or not records:
        raise SglangCookbookError("候选文件缺少 records 数组")
    if payload.get("records_count") not in (None, len(records)):
        raise SglangCookbookError(
            f"records_count={payload.get('records_count')} 与实际 {len(records)} 条不一致"
        )
    ids = [r.get("record_id") for r in records]
    if len(ids) != len(set(ids)):
        raise SglangCookbookError("候选文件内 record_id 不唯一")
    for r in records:
        if r.get("source_id") not in payload.get("sources", {}):
            raise SglangCookbookError(f"记录 {r.get('record_id')} 的 source_id 未在 sources 中声明")
    return payload


def split_sections(text: str) -> dict[str, str]:
    """按标题锚点切分文档：``{"5.1.1.1": "<该节全文>"}``（不含标题行本身）。"""
    out: dict[str, str] = {}
    matches = list(SECTION_RE.finditer(text))
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        out[m.group(2)] = text[m.end() : end]
    return out


def parse_cli_args(command: str) -> dict[str, str]:
    """从多行命令中抽取 ``--flag value``（忽略反斜杠换行）。"""
    flat = command.replace("\\\n", " ")
    args: dict[str, str] = {}
    tokens = flat.split()
    for i, tok in enumerate(tokens):
        if tok.startswith("--") and i + 1 < len(tokens) and not tokens[i + 1].startswith("--"):
            args.setdefault(tok, tokens[i + 1])
    return args


def _doc_fences(text: str) -> list[dict[str, Any]]:
    """收集所有代码块并标注用途（部署命令 / 测试命令 / 输出）。

    用途由**最近的**前置标记决定：同一窗口里可能同时出现 "- Model Deployment Command:"
    与 "- Benchmark Command:"（相邻两节各有一个），此时必须取离代码块更近的那个，
    否则会把测试命令误判成部署命令。
    """
    fences: list[dict[str, Any]] = []
    for f in iter_fences(text):
        body = f["body"]
        before = text[max(0, f["start"] - 400) : f["start"]].lower()
        i_deploy = before.rfind("model deployment command")
        i_bench = before.rfind("benchmark command")
        if "serving benchmark result" in body.lower():
            kind = "output"
        elif i_bench > i_deploy:
            kind = "benchmark"
        elif i_deploy > i_bench:
            kind = "launch"
        else:
            kind = "other"
        fences.append({**f, "kind": kind})
    return fences


def parse_source_evidence(path: str | Path, spec: dict[str, Any]) -> dict[str, Any]:
    """把一个来源文件解析成 ``{section_id: {"command":…, "args":…, "block":…, …}}``。

    命令与输出块严格限制在**本节范围内**（``[标题结束, 下一标题开始)``），
    避免父章节串到子章节、或把下一节的命令配给上一节。
    """
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    fences = _doc_fences(text)
    headings = list(SECTION_RE.finditer(text))
    ev: dict[str, Any] = {"text": text, "fences": fences, "sections": {}}
    for i, m in enumerate(headings):
        sid = m.group(2)
        start = m.end()
        end = headings[i + 1].start() if i + 1 < len(headings) else len(text)
        section_text = text[start:end]
        inside = [f for f in fences if start <= f["start"] < end]
        bench_cmd = next((f for f in inside if f["kind"] == "benchmark"), None)
        block = next((f for f in inside if f["kind"] == "output"), None)
        launch = next((f for f in inside if f["kind"] == "launch"), None)
        if launch is None:
            # 部署命令通常只写在场景组的首节，向上找最近的一条
            launch = next(
                (f for f in reversed(fences) if f["kind"] == "launch" and f["start"] < start),
                None,
            )
        parsed: list[dict[str, Any]] = []
        for f in inside:
            if f["kind"] == "output":
                parsed.extend(extract_summary_blocks(f["body"]))
        ev[sid] = {
            "section_text": section_text,
            "launch_command": launch["body"].strip() if launch else None,
            "benchmark_command": bench_cmd["body"].strip() if bench_cmd else None,
            "args": parse_cli_args(bench_cmd["body"]) if bench_cmd else {},
            "block": parsed[0] if parsed else None,
            "blocks": parsed,
        }
        ev["sections"][sid] = section_text
    return ev


def _dec(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return Decimal(str(value))
    except Exception:  # pragma: no cover - 非法输入
        return None


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:  # pragma: no cover - 非法输入
        return None


def _jsonify(check: dict[str, Any]) -> dict[str, Any]:
    """把一致性校验结果里的 Decimal 转成字符串，**保留布尔原值**。

    （早期实现把所有值都 ``str()``，导致 ``within_tolerance`` 变成字符串 "True"，
    在 `is True` 判定下静默失效——这是本次测试抓出来的真问题。）
    """
    return {k: (str(v) if isinstance(v, Decimal) else v) for k, v in check.items()}


def _env_checks(record: dict[str, Any], source: dict[str, Any], section_text: str) -> tuple[list[str], list[str], list[str], list[str]]:
    """对着原文核对环境字段。

    返回 ``(compared, matched, mismatches, notes)``。原文没写明的字段**不**算 matched，
    而是进 notes 并打"仅候选文件声明"标记。
    """
    compared: list[str] = []
    matched: list[str] = []
    mismatches: list[str] = []
    notes: list[str] = []
    hw = source.get("hardware_model")
    count = source.get("deployment_gpu_count")
    tp = source.get("tp_size")
    ep = source.get("ep_size")

    if hw:
        token = hw.split()[-1]  # "NVIDIA B200" -> "B200"
        if token and token.lower() in section_text.lower():
            compared.append("env:hardware_model")
            matched.append("env:hardware_model")
        else:
            notes.append(f"hardware_model={hw!r} 仅由候选文件声明，原文该节未出现该型号")
    if count is not None:
        m = _HW_COUNT_RE.search(section_text)
        if m and int(m.group(1)) == count:
            compared.append("env:deployment_gpu_count")
            matched.append("env:deployment_gpu_count")
        elif m:
            compared.append("env:deployment_gpu_count")
            mismatches.append(
                f"env:deployment_gpu_count: candidate={count} source={m.group(1)}"
            )
        else:
            notes.append(f"deployment_gpu_count={count} 仅由候选文件声明，原文该节未写卡数")
    for label, declared in (("tp_size", tp), ("ep_size", ep)):
        if declared is None:
            continue
        pat = _TP_RE if label == "tp_size" else _EP_RE
        m = pat.search(section_text)
        if m and int(m.group(1)) == declared:
            compared.append(f"env:{label}")
            matched.append(f"env:{label}")
        elif m:
            compared.append(f"env:{label}")
            mismatches.append(f"env:{label}: candidate={declared} source={m.group(1)}")
        else:
            notes.append(f"{label}={declared} 仅由候选文件声明，原文该节未出现对应参数")
    return compared, matched, mismatches, notes


def verify_record(
    record: dict[str, Any],
    source: dict[str, Any],
    source_evidence: dict[str, Any],
) -> CandidateVerification:
    """把一条候选与原文对应章节逐字段比对。"""
    sid = record["source_section"]
    sec = source_evidence.get(sid)
    compared: list[str] = []
    matched: list[str] = []
    mismatches: list[str] = []
    notes: list[str] = []
    conflicts: list[str] = []

    if not isinstance(sec, dict):
        return CandidateVerification(
            source_section=sid,
            notes=[f"原文中未找到章节 {sid}"],
        )

    block = sec.get("block")
    args = sec.get("args") or {}
    section_text = source_evidence["sections"].get(sid, "")

    if not block:
        notes.append("该节未解析到控制台输出块")
    if not args:
        notes.append("该节未解析到 benchmark 命令参数")

    # ---- 指标逐项比对 ----------------------------------------------------
    metrics_out: dict[str, Decimal] = {}
    for json_key, console_key in METRIC_MAP.items():
        want = _dec(record.get("metrics", {}).get(json_key))
        if want is None:
            continue
        if block and console_key in block["metrics"]:
            got = block["metrics"][console_key]
            compared.append(f"metric:{json_key}")
            if got == want:
                matched.append(f"metric:{json_key}")
            else:
                mismatches.append(f"metric:{json_key}: candidate={want} source={got}")
        else:
            notes.append(f"原文未提供指标 {console_key}（{json_key}）")

    # ---- 负载 / 模型逐项比对（以**命令**为准，不以正文叙述为准）----------
    workload = record.get("workload", {})
    for json_key, flag in WORKLOAD_ARG_MAP.items():
        want = _dec(workload.get(json_key))
        if want is None:
            continue
        got_raw = args.get(flag)
        got = _dec(got_raw)
        if got is None:
            notes.append(f"原文命令未提供 {flag}")
            continue
        compared.append(f"workload:{json_key}")
        if got == want:
            matched.append(f"workload:{json_key}")
        else:
            mismatches.append(f"workload:{json_key}: candidate={want} command={flag} {got}")
    if record.get("model_id") and args.get("--model"):
        compared.append("model:model_id")
        if args["--model"] == record["model_id"]:
            matched.append("model:model_id")
        else:
            mismatches.append(
                f"model:model_id: candidate={record['model_id']} command={args['--model']}"
            )
    # 命令里的 --max-concurrency 与输出块的 Max request concurrency 应当一致
    if block and args.get("--max-concurrency") and "configured_max_concurrency" in block["metrics"]:
        a = _dec(args["--max-concurrency"])
        b = block["metrics"]["configured_max_concurrency"]
        compared.append("cross:max_concurrency_command_vs_output")
        if a == b:
            matched.append("cross:max_concurrency_command_vs_output")
        else:
            mismatches.append(
                f"cross:max_concurrency_command_vs_output: command={a} output={b}"
            )

    # 命令参数补充进指标字典（命令即测试参数依据）
    if args.get("--max-concurrency"):
        metrics_out["configured_max_concurrency"] = _dec(args["--max-concurrency"])
    metrics_out = {k: v for k, v in metrics_out.items() if v is not None}

    # ---- 正文叙述 vs 命令冲突 -------------------------------------------
    if "sharegpt" in source_evidence["text"].lower() and record.get("workload", {}).get(
        "dataset_name"
    ) not in (None, "sharegpt"):
        conflicts.append(
            "原文正文提到 ShareGPT_Vicuna_unfiltered 数据集，但实际测试命令为 "
            f"--dataset-name {workload.get('dataset_name')}；按任务书 §3.4，以命令为测试参数依据"
        )

    # ---- 环境字段 --------------------------------------------------------
    env_cmp, env_ok, env_bad, env_notes = _env_checks(record, source, section_text)
    compared += env_cmp
    matched += env_ok
    mismatches += env_bad
    notes += env_notes

    return CandidateVerification(
        source_section=sid,
        source_block_found=bool(block),
        command_found=bool(args),
        compared_fields=compared,
        matched_fields=matched,
        mismatches=mismatches,
        command_conflicts=conflicts,
        notes=notes,
    )


def build_numeric_candidates(
    incoming_path: str | Path,
    *,
    root: str | Path,
) -> tuple[list[SglangNumericCandidate], list[Evidence], dict[str, Any]]:
    """完整流程：读候选文件 → 校验来源快照 → 逐条核对原文 → 产出候选与证据。

    数值来源优先级：**原文控制台输出**（可校验）> 候选文件；两者不一致时以原文为准
    并把差异写进 ``verification.mismatches``。候选文件里的原始数值另存于
    ``metrics_from_candidate_file``，便于逐项对照。
    """
    base = Path(root)
    payload = load_incoming(incoming_path)
    candidates: list[SglangNumericCandidate] = []
    evidences: list[Evidence] = []
    per_source: dict[str, Any] = {}

    for source_id, spec in SOURCE_SPECS.items():
        declared = payload["sources"].get(source_id)
        if declared is None:
            per_source[source_id] = {"present_in_input": False}
            continue
        snapshot = base / spec["evidence_file"]
        info: dict[str, Any] = {
            "present_in_input": True,
            "evidence_file": spec["evidence_file"],
            "expected_sha256": spec["evidence_sha256"],
            "declared_git_blob_sha": declared.get("source_git_blob_sha"),
            "spec_git_blob_sha": spec["source_git_blob_sha"],
            "snapshot_present": snapshot.exists(),
        }
        if not snapshot.exists():
            info.update(
                {
                    "snapshot_sha256": None,
                    "snapshot_git_blob_sha": None,
                    "sha256_matches_spec": False,
                    "git_blob_sha_matches_declared": False,
                    "reason": "本地快照缺失，未复核原文",
                }
            )
            per_source[source_id] = info
            continue
        actual_sha = sha256_file(snapshot)
        actual_blob = git_blob_sha1(snapshot)
        info.update(
            {
                "snapshot_sha256": actual_sha,
                "snapshot_git_blob_sha": actual_blob,
                "sha256_matches_spec": actual_sha == spec["evidence_sha256"],
                "git_blob_sha_matches_declared": actual_blob == declared.get("source_git_blob_sha"),
            }
        )
        if not (info["sha256_matches_spec"] and info["git_blob_sha_matches_declared"]):
            info["reason"] = "快照哈希与声明不一致，候选标记为未重新获取原文"
        per_source[source_id] = info

        evidence_id = f"ev_official_{source_id}"
        evidences.append(
            Evidence(
                evidence_id=evidence_id,
                source_type="official_benchmark",
                publisher=spec["publisher"],
                source_url=spec["source_url"],
                source_file=spec["evidence_file"],
                source_version=f"git-blob:{spec['source_git_blob_sha']}",
                raw_sha256=spec["evidence_sha256"],
                measurement_origin="official_benchmark",
                is_internal_measurement=False,
                notes=(
                    f"SGLang Cookbook 官方原文快照（{spec['source_path']}）。"
                    f"Git Blob SHA-1 与 SHA256 语义不同，二者均已校验。"
                ),
                quality_flags=[
                    "official_project_published_source",
                    "source_snapshot_archived",
                    "git_blob_sha_verified",
                ],
            )
        )

        source_evidence = parse_source_evidence(snapshot, spec)
        records = [r for r in payload["records"] if r["source_id"] == source_id]
        if spec.get("expected_records") is not None and len(records) != spec["expected_records"]:
            info["record_count_mismatch"] = f"输入 {len(records)} 条，预期 {spec['expected_records']} 条"
        # 原文中该来源含结果块、但本轮输入未列出的章节（仅记录，不生成候选）
        listed = {r["source_section"] for r in records}
        extra = sorted(
            sid
            for sid, sec in source_evidence.items()
            if isinstance(sec, dict)
            and sec.get("blocks")
            and sid not in listed
            and _section_in_scope(sid, spec)
        )
        info["unlisted_sections_with_results"] = extra
        info["unlisted_result_block_count"] = sum(
            len(source_evidence[sid]["blocks"]) for sid in extra
        )
        info["sections_parsed"] = sorted(
            sid for sid, sec in source_evidence.items() if isinstance(sec, dict)
        )
        info["records"] = []

        for record in records:
            verification = verify_record(record, declared, source_evidence)
            sec = source_evidence.get(record["source_section"], {})
            block = sec.get("block") if isinstance(sec, dict) else None
            file_metrics = {
                console_key: _dec(record.get("metrics", {}).get(json_key))
                for json_key, console_key in METRIC_MAP.items()
            }
            file_metrics = {k: v for k, v in file_metrics.items() if v is not None}
            if block:
                metrics = dict(block["metrics"])
                # 以命令为准补上配置并发
                if sec.get("args", {}).get("--max-concurrency"):
                    metrics["configured_max_concurrency"] = _dec(sec["args"]["--max-concurrency"])
                raw_labels = dict(block.get("raw_labels", {}))
            else:
                metrics = dict(file_metrics)
                raw_labels = {}
            metrics = {k: v for k, v in metrics.items() if v is not None}
            candidate = _record_to_candidate(
                record,
                declared,
                spec,
                evidence_id,
                verification,
                metrics=metrics,
                file_metrics=file_metrics,
                raw_labels=raw_labels,
                launch_command=(sec.get("launch_command") if isinstance(sec, dict) else None),
            )
            candidates.append(candidate)
            info["records"].append(
                {
                    "record_id": candidate.record_id,
                    "candidate_id": candidate.candidate_id,
                    "verified_against_source": verification.verified_against_source,
                    "compared": len(verification.compared_fields),
                    "matched": len(verification.matched_fields),
                    "mismatches": verification.mismatches,
                }
            )

    summary = {
        "incoming_file": str(incoming_path),
        "incoming_sha256": sha256_file(incoming_path),
        "record_count": len(payload["records"]),
        "candidate_count": len(candidates),
        "by_source": {sid: len([c for c in candidates if c.source_id == sid]) for sid in SOURCE_SPECS},
        "verified_against_source": sum(1 for c in candidates if c.verification.verified_against_source),
        "sources": per_source,
    }
    return candidates, evidences, summary


def _section_in_scope(section_id: str, spec: dict[str, Any]) -> bool:
    """该章节是否属于本轮输入覆盖的范围（用于统计"原文有但未列出"的结果块）。"""
    prefixes = spec.get("section_prefixes")
    if not prefixes:
        return True
    return any(section_id.startswith(p) for p in prefixes)


def git_blob_sha1(path: str | Path) -> str:
    """按 Git 对象格式计算 Blob SHA-1（``sha1("blob <len>\\0" + content)``）。

    注意：这是 Git 的对象标识，**不是**文件内容哈希，绝不能与 SHA256 混用。
    """
    data = Path(path).read_bytes()
    header = f"blob {len(data)}\0".encode()
    return hashlib.sha1(header + data).hexdigest()


def _record_to_candidate(
    record: dict[str, Any],
    source: dict[str, Any],
    spec: dict[str, Any],
    evidence_id: str,
    verification: CandidateVerification,
    *,
    metrics: dict[str, Decimal],
    file_metrics: dict[str, Decimal],
    raw_labels: dict[str, str],
    launch_command: str | None,
) -> SglangNumericCandidate:
    deployment = record.get("deployment", {})
    workload = record.get("workload", {})
    model_revision = record.get("model_revision")
    precision = record.get("weight_precision") or source.get("weight_precision")

    missing: list[str] = []
    for name, value in (
        ("model_revision", model_revision),
        ("model_precision", precision),
        ("framework_version", record.get("framework_version")),
        ("hardware_model", source.get("hardware_model")),
        ("deployment_gpu_count", source.get("deployment_gpu_count")),
    ):
        if value in (None, ""):
            missing.append(name)
    missing += [m for m in REQUIRED_METRICS if m not in metrics]
    missing += [m for m in (record.get("missing_or_unverified_fields") or []) if m not in missing]

    flags: list[str] = ["official_published_numeric_benchmark", "not_reproducible_configuration"]
    success_count = metrics.get("success_count")
    if success_count is None:
        is_throughput: bool | None = None
    else:
        is_throughput = success_count > 1
    if success_count is not None and success_count == 1:
        flags.append("single_request_smoke_test")
    if model_revision in (None, ""):
        flags.append("model_revision_not_published")
    if any("仅由候选文件声明" in n for n in verification.notes):
        flags.append("environment_partially_declared_only_in_candidate_file")
    if verification.command_conflicts:
        flags.append("narrative_conflicts_with_benchmark_command")
    if verification.verified_against_source:
        flags.append("values_machine_verified_against_archived_source")
    else:
        flags.append("values_not_fully_verified_against_source")

    app = assess_applicability(
        source_locatable=bool(spec.get("source_url")),
        model_name=record.get("model_id"),
        model_revision=model_revision,
        model_precision=precision,
        hardware_model=source.get("hardware_model"),
        deployment_gpu_count=source.get("deployment_gpu_count"),
        framework_version=record.get("framework_version"),
        workload_defined=bool(workload),
        metrics=metrics,
        sla_requirement=None,
        price=PriceContext(currency="UNCONFIRMED", currency_confirmed=False),
        measurement_origin_is_official=True,
    )
    missing = sorted(set(missing) | set(app.missing_fields))

    return SglangNumericCandidate(
        candidate_id=f"official_{record['record_id']}",
        record_id=record["record_id"],
        source_id=record["source_id"],
        source_section=record["source_section"],
        evidence_id=evidence_id,
        publisher=spec["publisher"],
        source_url=spec["source_url"],
        source_path=spec["source_path"],
        source_git_blob_sha=spec["source_git_blob_sha"],
        evidence_file=spec["evidence_file"],
        evidence_sha256=spec["evidence_sha256"],
        accessed_at=_parse_dt(spec.get("retrieved_at")),
        evidence_type=record.get("evidence_type", "official_project_published_benchmark"),
        model_name=record.get("model_id"),
        model_revision=model_revision,
        model_precision=precision,
        framework=record.get("framework"),
        framework_version=record.get("framework_version"),
        backend_version=record.get("framework_version"),
        hardware_model=source.get("hardware_model"),
        deployment_gpu_count=source.get("deployment_gpu_count"),
        tp_size=deployment.get("tp_size"),
        ep_size=deployment.get("ep_size"),
        dp_size=deployment.get("dp_size"),
        pd_mode=deployment.get("pd_mode"),
        scenario_label=workload.get("scenario_label"),
        dataset_name=workload.get("dataset_name"),
        input_len_target=workload.get("random_input_len_target"),
        output_len_target=workload.get("random_output_len_target"),
        configured_max_concurrency=workload.get("configured_max_concurrency"),
        num_prompts=workload.get("num_prompts"),
        traffic_request_rate_raw=workload.get("traffic_request_rate"),
        metrics=metrics,
        metrics_from_candidate_file=file_metrics,
        raw_labels=raw_labels,
        symbols={"traffic_request_rate": str(workload.get("traffic_request_rate"))}
        if workload.get("traffic_request_rate") is not None
        else {},
        metric_definition_ids=metric_definition_ids(metrics),
        throughput_consistency=[_jsonify(check) for check in throughput_consistency(metrics).checks],
        semantic_warnings=label_provenance_issues(metrics, raw_labels),
        is_throughput_benchmark=is_throughput,
        launch_command=launch_command,
        benchmark_command=record.get("benchmark_command"),
        verification=verification,
        can_reference_performance=app.can_reference_performance,
        can_evaluate_sla=app.can_evaluate_sla,
        can_calculate_pricing=app.can_calculate_pricing,
        commercial_quote_allowed=app.commercial_quote_allowed,
        applicability_notes=app.applicability_notes,
        present_fields=sorted(
            k
            for k, v in {
                "model_name": record.get("model_id"),
                "model_revision": model_revision,
                "model_precision": precision,
                "framework_version": record.get("framework_version"),
                "hardware_model": source.get("hardware_model"),
                "deployment_gpu_count": source.get("deployment_gpu_count"),
                "dataset_name": workload.get("dataset_name"),
            }.items()
            if v not in (None, "")
        )
        + [f"metric:{k}" for k in sorted(metrics)],
        missing_fields=missing,
        quality_flags=sorted(set(flags) | set(app.quality_flags)),
    )
