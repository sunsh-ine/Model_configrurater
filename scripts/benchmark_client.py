#!/usr/bin/env python3
"""依赖最少的 OpenAI 流式压测客户端：保留逐请求记录与显式测量窗口。

在 vLLM/SGLang 的独立环境中运行；只需要 requests 与 transformers（用于生成
精确长度的随机 token id）。不要用它伪造结果：失败/取消/超时都原样记录。
"""

from __future__ import annotations

import argparse
import json
import math
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

FINISHED = {"stop", "length"}


def _token_ids(tokenizer, length: int, rng: random.Random) -> list[int]:
    vocab = tokenizer.get_vocab()
    banned = set(tokenizer.all_special_ids or [])
    ids = [idx for idx in vocab.values() if idx not in banned]
    if not ids:
        raise RuntimeError("tokenizer 没有可用 token")
    return [rng.choice(ids) for _ in range(length)]


def _one_request(
    *,
    session: requests.Session,
    base_url: str,
    endpoint: str,
    model: str,
    prompt_ids: list[int],
    output_len: int,
    timeout: float,
    global_start: float,
    index: int,
    tokenizer,
) -> dict[str, Any]:
    started = time.perf_counter()
    row: dict[str, Any] = {
        "index": index,
        "arrival_ms": (started - global_start) * 1000,
        "input_len": len(prompt_ids),
        "output_len": 0,
        "success": False,
        "terminal_status": "failed",
    }
    try:
        response = session.post(
            f"{base_url}{endpoint}",
            json={
                "model": model,
                "prompt": prompt_ids,
                "max_tokens": output_len,
                "temperature": 0,
                "top_p": 1,
                "ignore_eos": True,
                "stream": True,
                "stream_options": {"include_usage": True},
            },
            stream=True,
            timeout=timeout,
        )
        if response.status_code != 200:
            row["error"] = f"HTTP {response.status_code}: {response.text[:300]}"
            row["latency"] = (time.perf_counter() - started) * 1000
            return row
        first: float | None = None
        last: float | None = None
        completion_tokens = 0
        usage_seen = False
        finish_reason = None
        text_parts: list[str] = []
        for line in response.iter_lines(decode_unicode=True):
            if not line or not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if payload == "[DONE]":
                break
            try:
                chunk = json.loads(payload)
            except json.JSONDecodeError:
                continue
            if chunk.get("usage"):
                usage_seen = True
                completion_tokens = int(
                    chunk["usage"].get("completion_tokens") or completion_tokens
                )
            for choice in chunk.get("choices") or []:
                text = choice.get("text") or ""
                if text:
                    text_parts.append(text)
                    now = time.perf_counter()
                    if first is None:
                        first = now
                    last = now
                finish_reason = choice.get("finish_reason") or finish_reason
        ended = time.perf_counter()
        row["ttft"] = (first - started) * 1000 if first is not None else None
        row["latency"] = (ended - started) * 1000
        row["output_len"] = completion_tokens
        row["success"] = (
            first is not None
            and usage_seen
            and completion_tokens > 0
            and finish_reason in FINISHED
        )
        if not usage_seen and not row.get("error"):
            row["error"] = "missing_usage"
        row["terminal_status"] = (
            "completed" if row["success"] else "failed"
        )
        row["finish_reason"] = finish_reason
        return row
    except Exception as exc:  # noqa: BLE001
        row["error"] = f"{type(exc).__name__}: {exc}"
        row["latency"] = (time.perf_counter() - started) * 1000
        return row


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--endpoint", default="/v1/completions")
    parser.add_argument("--model", required=True)
    parser.add_argument("--tokenizer", required=True)
    parser.add_argument("--input-len", type=int, required=True)
    parser.add_argument("--output-len", type=int, default=256)
    parser.add_argument("--num-requests", type=int, default=500)
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--request-rate", type=float, default=None)
    parser.add_argument("--warmup", type=int, default=0)
    parser.add_argument("--timeout", type=float, default=600)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--out", required=True)
    parser.add_argument("--summary-out", default=None)
    args = parser.parse_args()

    from transformers import AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(args.tokenizer, trust_remote_code=False)
    rng = random.Random(args.seed)
    session = requests.Session()

    if args.warmup:
        prompt = _token_ids(tokenizer, args.input_len, rng)
        for _ in range(args.warmup):
            _one_request(
                session=session,
                base_url=args.base_url,
                endpoint=args.endpoint,
                model=args.model,
                prompt_ids=prompt,
                output_len=args.output_len,
                timeout=args.timeout,
                global_start=time.perf_counter(),
                index=-1,
                tokenizer=tokenizer,
            )

    prompts = [_token_ids(tokenizer, args.input_len, rng) for _ in range(args.num_requests)]
    rows: list[dict[str, Any]] = []
    lock = threading.Lock()
    global_start = time.perf_counter()
    wall_end = global_start

    def run(index: int) -> None:
        nonlocal wall_end
        row = _one_request(
            session=requests.Session(),
            base_url=args.base_url,
            endpoint=args.endpoint,
            model=args.model,
            prompt_ids=prompts[index],
            output_len=args.output_len,
            timeout=args.timeout,
            global_start=global_start,
            index=index,
            tokenizer=tokenizer,
        )
        with lock:
            rows.append(row)
            wall_end = max(wall_end, time.perf_counter())

    with ThreadPoolExecutor(max_workers=max(1, args.concurrency)) as pool:
        if args.request_rate is None:
            futures = [pool.submit(run, index) for index in range(args.num_requests)]
        else:
            futures = []
            next_at = global_start
            for index in range(args.num_requests):
                now = time.perf_counter()
                if next_at > now:
                    time.sleep(next_at - now)
                futures.append(pool.submit(run, index))
                next_at += -math.log(1 - rng.random()) / args.request_rate
        for future in futures:
            future.result()

    rows.sort(key=lambda row: row["index"])
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    summary = {
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "base_url": args.base_url,
        "model": args.model,
        "input_len_requested": args.input_len,
        "output_len_requested": args.output_len,
        "num_requests": args.num_requests,
        "concurrency": args.concurrency,
        "request_rate": args.request_rate,
        "seed": args.seed,
        "measurement_window_ms": {
            "start_ms": (global_start - global_start) * 1000,
            "end_ms": (wall_end - global_start) * 1000,
        },
        "success_count": sum(1 for row in rows if row["success"]),
        "failure_count": sum(
            1
            for row in rows
            if not row["success"] and row["terminal_status"] == "failed"
        ),
        "elapsed_s": wall_end - global_start,
    }
    summary_path = Path(args.summary_out) if args.summary_out else out.with_suffix(".summary.json")
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
