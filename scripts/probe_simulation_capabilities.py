#!/usr/bin/env python3
"""AC1：探测 AISimulate 对目标模型/硬件组合的支持能力。

真实执行 `aisimulate predict`，不伪造任何 P99。结果写入
``data/simulation/capabilities/simulation_capabilities.json`` 与 Markdown 摘要。

状态：
* ``supported``：成功输出 prediction.json，含 P99 与 ≥100 完成请求；
* ``unsupported``：工具明确拒绝（模型/系统/后端/版本不支持）；
* ``unknown``：超时、环境缺失或其它无法判定的错误。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
OUT_ROOT = ROOT / "data/simulation/capabilities"

CASES = (
    {"case_id": "qwen3-8b_h100_sxm", "model": "Qwen/Qwen3-8B", "system": "h100_sxm", "tps": (1, 2, 4, 8)},
    {"case_id": "qwen3-32b_h200_sxm", "model": "Qwen/Qwen3-32B", "system": "h200_sxm", "tps": (2, 4, 8)},
    {"case_id": "qwen3-8b_h200_sxm", "model": "Qwen/Qwen3-8B", "system": "h200_sxm", "tps": (1, 2, 4, 8)},
)


def sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def predict_config(model: str, system: str, tp: int) -> dict[str, Any]:
    return {
        "engine": {
            "backend": "vllm",
            "backend_version": "0.24.0",
            "hardware": system,
            "mode": "aggregated",
            "model": model,
            "workers": {
                "aggregated": {
                    "parallelism": {"pipeline": 1, "replicas": 1, "tensor": tp}
                }
            },
        },
        "traffic": {
            "load": {"concurrency": 32, "type": "concurrency"},
            "source": {"input_tokens": 8192, "output_tokens": 256, "type": "synthetic"},
            "stop": {"requests": 500},
        },
    }


def classify(stderr: str, stdout: str) -> str:
    text = (stderr + "\n" + stdout).lower()
    markers = (
        "not support",
        "unsupported",
        "unknown system",
        "unknown model",
        "no backend",
        "not found",
        "does not exist",
        "invalid",
        "error",
    )
    return "unsupported" if any(marker in text for marker in markers) else "unknown"


def probe(case: dict[str, Any], python: str, timeout: int) -> dict[str, Any]:
    case_dir = OUT_ROOT / case["case_id"]
    if case_dir.exists():
        shutil.rmtree(case_dir)
    case_dir.mkdir(parents=True)
    attempts: list[dict[str, Any]] = []

    for tp in case["tps"]:
        config = predict_config(case["model"], case["system"], tp)
        config_path = case_dir / f"predict_tp{tp}.yaml"
        config_path.write_text(
            yaml.safe_dump(config, allow_unicode=True), encoding="utf-8"
        )
        out_dir = case_dir / f"out_tp{tp}"
        command = [
            python,
            "-m",
            "aisimulate",
            "predict",
            "-c",
            str(config_path),
            "--output-dir",
            str(out_dir),
            "--overwrite",
            "--format",
            "json",
        ]
        try:
            proc = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=timeout,
                env={**os.environ, "PYTHONHASHSEED": "0"},
            )
        except subprocess.TimeoutExpired:
            attempts.append({"tp": tp, "status": "unknown", "reason": f"超时 {timeout}s"})
            continue
        (case_dir / f"stderr_tp{tp}.log").write_text(proc.stderr or "", encoding="utf-8")
        prediction_path = out_dir / "prediction.json"
        if proc.returncode == 0 and prediction_path.is_file():
            payload = json.loads(prediction_path.read_text(encoding="utf-8"))
            completed = int(payload.get("completed_requests") or 0)
            if payload.get("p99_ttft_ms") is not None and payload.get("p99_tpot_ms") is not None and completed >= 100:
                return {
                    "case_id": case["case_id"],
                    "model": case["model"],
                    "system": case["system"],
                    "status": "supported",
                    "tp": tp,
                    "attempts": attempts
                    + [{"tp": tp, "status": "supported", "reason": "P99 已生成"}],
                    "evidence": {
                        "predict_config": str(config_path.relative_to(ROOT)),
                        "predict_config_sha256": sha256(config_path),
                        "prediction": str(prediction_path.relative_to(ROOT)),
                        "prediction_sha256": sha256(prediction_path),
                    },
                    "metrics": {
                        "p99_ttft_ms": payload.get("p99_ttft_ms"),
                        "p99_tpot_ms": payload.get("p99_tpot_ms"),
                        "output_throughput_tok_s": payload.get("output_throughput_tok_s"),
                        "completed_requests": completed,
                    },
                }
            reason = "输出缺少 P99 或完成请求不足 100"
            status = "unknown"
        else:
            reason = (proc.stderr or proc.stdout or "").strip()[-500:] or (
                f"exit {proc.returncode}"
            )
            status = classify(proc.stderr or "", proc.stdout or "")
        attempts.append({"tp": tp, "status": status, "reason": reason})
        if status == "unsupported":
            # 同一模型/系统的结构性不支持，继续换 TP 通常也不会成功；保留尝试记录。
            continue

    final = "unsupported"
    if any(item["status"] == "unknown" for item in attempts):
        final = "unknown"
    return {
        "case_id": case["case_id"],
        "model": case["model"],
        "system": case["system"],
        "status": final,
        "tp": None,
        "attempts": attempts,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", default=os.environ.get("AISIMULATE_PYTHON"))
    parser.add_argument("--timeout", type=int, default=300)
    args = parser.parse_args()
    if not args.python:
        raise SystemExit("需要 --python 或 AISIMULATE_PYTHON（独立 AISimulate 环境）")
    check = subprocess.run(
        [args.python, "-c", "import aisimulate"],
        capture_output=True,
        text=True,
    )
    if check.returncode != 0:
        raise SystemExit(f"该解释器无法 import aisimulate：{check.stderr[-300:]}")

    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    results = []
    for case in CASES:
        result = probe(case, args.python, args.timeout)
        results.append(result)
        print(f"{result['case_id']}: {result['status']} (tp={result['tp']})")

    version = subprocess.run(
        [
            args.python,
            "-c",
            "import importlib.metadata as m; print(m.version('aisimulate'))",
        ],
        capture_output=True,
        text=True,
    ).stdout.strip()
    doc = {
        "schema_version": "1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "tool": "aisimulate",
        "tool_version": version,
        "database_mode": "SILICON",
        "workload": {
            "input_len": 8192,
            "output_len": 256,
            "concurrency": 32,
            "requests": 500,
            "backend": "vllm",
            "backend_version": "0.24.0",
            "precision": "bf16",
        },
        "cases": results,
    }
    (OUT_ROOT / "simulation_capabilities.json").write_text(
        json.dumps(doc, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    lines = [
        "# AISimulate 能力探测矩阵",
        "",
        f"- 工具：aisimulate {version}",
        f"- 工作负载：ISL8192 / OSL256 / C32 / 500 请求 / BF16 / vLLM 0.24.0",
        f"- 生成时间：{doc['generated_at']}",
        "",
        "| 组合 | 状态 | TP | 阻塞/说明 |",
        "|---|---|---|---|",
    ]
    for result in results:
        if result["status"] == "supported":
            note = "已生成完整 P99"
        else:
            note = "；".join(
                f"TP{item['tp']}: {item['reason']}" for item in result["attempts"]
            )[-300:]
        lines.append(
            f"| {result['model']} @ {result['system']} | {result['status']} "
            f"| {result['tp'] or '—'} | {note.replace('|', '/')} |"
        )
    lines.append("")
    (ROOT / "docs/ac_capability_matrix.md").write_text(
        "\n".join(lines), encoding="utf-8"
    )
    print(f"已写出：{(OUT_ROOT / 'simulation_capabilities.json').relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
