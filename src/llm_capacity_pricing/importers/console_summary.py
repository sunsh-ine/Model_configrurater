"""解析 ``vllm bench serve`` / SGLang ``bench_serving`` 的**控制台汇总块**（不是结果 JSON）。

用途：vLLM Recipes、SGLang Cookbook 等官方文档把 benchmark 工具的输出贴在页面上作为
"Test Results"。本模块直接从**存档的原始证据文件**中抽取该块，使入库数值来自
证据文件本身（可校验 sha256），而不是人工抄录。

处理要点：文档 HTML 会在数字中间插入标签，纯文本化后形如 ``16 .39``、``( s )``，
因此先做定点数字与单位的空格归一化，再按标签取值。

严格性：
* 只抽取文档中**明确写出的**标签，不推算、不补造；
* 未出现的标签一律不进入结果字典；
* ``Successful requests`` 等计数照实记录，不因数值偏小而修正；
* 括号限定词参与取值（``Total generated tokens (retokenized)`` 与
  ``Total generated tokens`` 是两个不同指标，前者**不得**覆盖后者）。
"""

from __future__ import annotations

import html
import re
from decimal import Decimal, InvalidOperation
from typing import Any

#: 控制台标签 -> 规范字段名。括号限定词（如 ``(retokenized)``）作为标签的一部分参与匹配。
LABEL_MAP: dict[str, str] = {
    "successful requests": "success_count",
    "benchmark duration": "duration_s",
    "total input tokens": "total_input_tokens",
    "total input text tokens": "total_input_text_tokens",
    "total input vision tokens": "total_input_vision_tokens",
    "total generated tokens": "total_output_tokens",
    "total generated tokens (retokenized)": "total_output_tokens_retokenized",
    "request throughput": "request_throughput_rps",
    "output token throughput": "output_throughput_tok_s",
    "peak output token throughput": "peak_output_throughput_tok_s",
    "input token throughput": "input_throughput_tok_s",
    "total token throughput": "total_token_throughput_tok_s",
    # SGLang 的 "Concurrency" = 全请求累计耗时 / 墙钟时间，属**实测观测**并发，
    # 与 "--max-concurrency" 配置值不是一回事（见 docs/benchmark_metric_dictionary.md）。
    "concurrency": "observed_concurrency",
    "max request concurrency": "configured_max_concurrency",
    "peak concurrent requests": "peak_concurrent_requests",
    "mean ttft": "ttft_mean_ms",
    "median ttft": "ttft_median_ms",
    "p99 ttft": "ttft_p99_ms",
    "mean tpot": "tpot_mean_ms",
    "median tpot": "tpot_median_ms",
    "p99 tpot": "tpot_p99_ms",
    "mean itl": "itl_mean_ms",
    "median itl": "itl_median_ms",
    "p95 itl": "itl_p95_ms",
    "p99 itl": "itl_p99_ms",
    "max itl": "itl_max_ms",
    "mean e2e latency": "e2e_mean_ms",
    "median e2e latency": "e2e_median_ms",
    "p99 e2e latency": "e2e_p99_ms",
}

#: 该块中每个指标在来源文档里的**原始标签**，保留以便追溯
RAW_LABEL: dict[str, str] = {v: k for k, v in LABEL_MAP.items()}

#: 控制台里以**符号**而非数字出现的标签（不可参与数值计算，原样保留字符串）
SYMBOL_LABELS: dict[str, str] = {
    "traffic request rate": "traffic_request_rate_raw",
}

_UNIT_RE = re.compile(r"\(\s*([^()]{1,24}?)\s*\)")
_NUM_FIX_RE = re.compile(r"(\d)\s*\.\s*(\d)")
_LABEL_RE = re.compile(
    r"([A-Za-z][A-Za-z0-9 /]{2,40}?)\s*(?:\(([^()]*)\))?\s*:\s*([0-9]+(?:\.[0-9]+)?)"
)
_SYMBOL_RE = re.compile(
    r"([A-Za-z][A-Za-z0-9 /]{2,40}?)\s*:\s*([A-Za-z][A-Za-z0-9._+-]*)", re.IGNORECASE
)


def html_to_text(raw_html: str) -> str:
    """HTML -> 纯文本（去脚本/样式与标签），保留全部可见字面量。"""
    body = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", raw_html)
    text = re.sub(r"(?s)<[^>]+>", " ", body)
    return html.unescape(re.sub(r"\s+", " ", text))


def normalize_console_text(text: str) -> str:
    """修复文档渲染造成的数字/单位断开：``16 .39`` -> ``16.39``、``( s )`` -> ``(s)``。"""
    fixed = _UNIT_RE.sub(lambda m: "(" + m.group(1).replace(" ", "") + ")", text)
    prev = None
    while prev != fixed:
        prev = fixed
        fixed = _NUM_FIX_RE.sub(r"\1.\2", fixed)
    return fixed


def extract_summary_blocks(raw_text: str) -> list[dict[str, Any]]:
    """从（已纯文本化的）文档中抽取所有 ``Serving Benchmark Result`` 块。

    返回每个块的结构化指标。块内未出现的标签不会出现在结果里。
    括号限定词参与匹配：``Total generated tokens (retokenized)`` 写入
    ``total_output_tokens_retokenized``，**不会**覆盖 ``total_output_tokens``。
    """
    text = normalize_console_text(raw_text)
    blocks: list[dict[str, Any]] = []
    for marker in re.finditer(r"={3,}\s*Serving Benchmark Result\s*={3,}", text):
        # 取标记之后到下一个分隔线结束的区间
        tail = text[marker.end() :]
        end = tail.find("=================================")
        segment = tail[: end if end > 0 else len(tail)]
        metrics: dict[str, Any] = {}
        raw_labels: dict[str, str] = {}
        symbols: dict[str, str] = {}
        for m in _LABEL_RE.finditer(segment):
            label = m.group(1).strip().lower()
            unit = m.group(2)
            value = m.group(3)
            # 先按"标签 (限定词)"整体查表，再退回裸标签
            key = None
            if unit:
                key = LABEL_MAP.get(f"{label} ({unit.strip().lower()})")
            if key is None:
                key = LABEL_MAP.get(label)
            if key is None:
                continue
            try:
                metrics[key] = Decimal(value)
            except InvalidOperation:  # pragma: no cover - 结构异常
                continue
            raw_labels[key] = f"{m.group(1).strip()}" + (f" ({unit})" if unit else "")
        for m in _SYMBOL_RE.finditer(segment):
            label = m.group(1).strip().lower()
            key = SYMBOL_LABELS.get(label)
            if key is None:
                continue
            symbols[key] = m.group(2)
            raw_labels[key] = m.group(1).strip()
        if metrics or symbols:
            blocks.append(
                {
                    "raw_labels": raw_labels,
                    "metrics": metrics,
                    "symbols": symbols,
                    "segment_excerpt": segment.strip()[:600],
                }
            )
    return blocks


def extract_first_summary(raw_text: str) -> dict[str, Any] | None:
    blocks = extract_summary_blocks(raw_text)
    return blocks[0] if blocks else None
