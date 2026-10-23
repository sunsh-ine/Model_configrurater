#!/usr/bin/env python3
"""AD1：探测 AISimulate 对普通 TP/DP 副本与 PP 的支持能力。

不预设任何组合受支持；逐项真实执行 `aisimulate predict`，记录输入、输出、
错误与状态（supported / unsupported / unknown）。
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
OUT_ROOT = ROOT / "data/simulation/capabilities/parallelism"

CASES = (
    ("qwen3-8b_h100_tp1dp1", "Qwen/Qwen3-8B", "h100_sxm", 1, 1, 1),
    ("qwen3-8b_h100_tp1dp2", "Qwen/Qwen3-8B", "h100_sxm", 1, 2, 1),
    ("qwen3-8b_h100_tp1dp4", "Qwen/Qwen3-8B", "h100_sxm", 1, 4, 1),
    ("qwen3-8b_h100_tp1pp2", "Qwen/Qwen3-8B", "h100_sxm", 1, 1, 2),
    ("qwen3-8b_h200_tp1dp1", "Qwen/Qwen3-8B", "h200_sxm", 1, 1, 1),
    ("qwen3-8b_h200_tp1dp2", "Qwen/Qwen3-8B", "h200_sxm", 1, 2, 1),
    ("qwen3-32b_h100_tp8dp1", "Qwen/Qwen3-32B", "h100_sxm", 8, 1, 1),
    ("qwen3-32b_h100_tp4dp2", "Qwen/Qwen3-32B", "h100_sxm", 4, 2, 1),
    ("qwen3-32b_h200_tp8dp1", "Qwen/Qwen3-32B", "h200_sxm", 8, 1, 1),
    ("qwen3-32b_h200_tp4dp2", "Qwen/Qwen3-32B", "h200_sxm", 4, 2, 1),
    ("qwen3-32b_h200_tp4pp2", "Qwen/Qwen3-32B", "h200_sxm", 4, 1, 2),
)


def sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def config(model: str, system: str, tp: int, dp: int, pp: int) -> dict[str, Any]:
    return {
        "engine": {
            "backend": "vllm",
            "backend_version": "0.24.0",
            "hardware": system,
            "mode": "aggregated",
            "model": model,
            "workers": {
                "aggregated": {
                    "parallelism": {
                        "tensor": tp,
                        "pipeline": pp,
                        "replicas": dp,
                    }
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
    # 只有工具**明确**结构性拒绝才判 unsupported；依赖缺失/通用报错不能
    # 推断该并行策略不受支持。
    markers = (
        "not support",
        "unsupported",
        "unknown system",
        "unknown model",
        "unknown backend",
        "no supported",
        "cannot be represented",
    )
    return "unsupported" if any(marker in text for marker in markers) else "unknown"


def probe(case, python: str, timeout: int) -> dict[str, Any]:
    name, model, system, tp, dp, pp = case
    case_dir = OUT_ROOT / name
    if case_dir.exists():
        shutil.rmtree(case_dir)
    case_dir.mkdir(parents=True)
    cfg_path = case_dir / "predict.yaml"
    cfg_path.write_text(
        yaml.safe_dump(config(model, system, tp, dp, pp), allow_unicode=True),
        encoding="utf-8",
    )
    out_dir = case_dir / "out"
    command = [
        python,
        "-m",
        "aisimulate",
        "predict",
        "-c",
        str(cfg_path),
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
        return {
            "case_id": name,
            "model": model,
            "system": system,
            "tp": tp,
            "dp": dp,
            "pp": pp,
            "status": "unknown",
            "reason": f"超时 {timeout}s",
        }
    (case_dir / "stderr.log").write_text(proc.stderr or "", encoding="utf-8")
    prediction_path = out_dir / "prediction.json"
    if proc.returncode == 0 and prediction_path.is_file():
        payload = json.loads(prediction_path.read_text(encoding="utf-8"))
        completed = int(payload.get("completed_requests") or 0)
        if payload.get("p99_ttft_ms") is not None and payload.get("p99_tpot_ms") is not None and completed >= 100:
            return {
                "case_id": name,
                "model": model,
                "system": system,
                "tp": tp,
                "dp": dp,
                "pp": pp,
                "status": "supported",
                "reason": "已生成完整 P99",
                "evidence": {
                    "predict_config": str(cfg_path.relative_to(ROOT)),
                    "predict_config_sha256": sha256(cfg_path),
                    "prediction": str(prediction_path.relative_to(ROOT)),
                    "prediction_sha256": sha256(prediction_path),
                },
                "metrics": {
                    "p99_ttft_ms": payload.get("p99_ttft_ms"),
                    "p99_tpot_ms": payload.get("p99_tpot_ms"),
                    "output_throughput_tok_s": payload.get("output_throughput_tok_s"),
                    "completed_requests": completed,
                    "decode_gpus_per_worker": payload.get("decode_gpus_per_worker"),
                },
            }
        return {
            "case_id": name,
            "model": model,
            "system": system,
            "tp": tp,
            "dp": dp,
            "pp": pp,
            "status": "unknown",
            "reason": "输出缺少 P99 或完成请求不足 100",
        }
    return {
        "case_id": name,
        "model": model,
        "system": system,
        "tp": tp,
        "dp": dp,
        "pp": pp,
        "status": classify(proc.stderr or "", proc.stdout or ""),
        "reason": (proc.stderr or proc.stdout or "").strip()[-500:] or f"exit {proc.returncode}",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", default=os.environ.get("AISIMULATE_PYTHON"))
    parser.add_argument("--timeout", type=int, default=300)
    args = parser.parse_args()
    if not args.python:
        raise SystemExit("需要 --python 或 AISIMULATE_PYTHON")
    check = subprocess.run(
        [args.python, "-c", "import aisimulate"], capture_output=True, text=True
    )
    if check.returncode != 0:
        raise SystemExit(f"该解释器无法 import aisimulate：{check.stderr[-300:]}")

    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    results = []
    for case in CASES:
        result = probe(case, args.python, args.timeout)
        results.append(result)
        print(f"{result['case_id']}: {result['status']}")

    version = subprocess.run(
        [args.python, "-c", "import importlib.metadata as m; print(m.version('aisimulate'))"],
        capture_output=True,
        text=True,
    ).stdout.strip()
    doc = {
        "schema_version": "1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "tool": "aisimulate",
        "tool_version": version,
        "database_mode": None,
        "database_mode_note": "predict 配置未暴露 database_mode；本矩阵不声明该字段",
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
    (OUT_ROOT / "parallelism_capabilities.json").write_text(
        json.dumps(doc, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    lines = [
        "# AISimulate 并行策略能力矩阵",
        "",
        f"- 工具：aisimulate {version}",
        "- 工作负载：ISL8192 / OSL256 / C32 / 500 请求 / BF16 / vLLM 0.24.0",
        "",
        "| 组合 | TP×DP×PP | 状态 | 说明 |",
        "|---|---|---|---|",
    ]
    for result in results:
        reason = result.get("reason", "").replace("|", "/")[:240]
        lines.append(
            f"| {result['model']} @ {result['system']} | "
            f"{result['tp']}×{result['dp']}×{result['pp']} | "
            f"{result['status']} | {reason} |"
        )
    lines.append("")
    (ROOT / "docs/ad_parallelism_matrix.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"已写出：{(OUT_ROOT / 'parallelism_capabilities.json').relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
