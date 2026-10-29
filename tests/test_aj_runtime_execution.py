"""AJ0：冻结运行配置必须与服务端实际执行日志一致。"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "archive_rtx3090_client_run.py"

_spec = importlib.util.spec_from_file_location("archive_rtx3090_test", SCRIPT)
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)

CONFIG = json.loads(
    (ROOT / "data/benchmark/rtx3090/runtime_config.json").read_text(encoding="utf-8")
)

LOG = (
    "INFO vLLM API server version 0.11.0\n"
    "INFO non-default args: {'dtype': 'bfloat16', 'max_model_len': 8448, "
    "'max_num_seqs': 8, 'enforce_eager': True}\n"
    "INFO Initializing a V1 LLM engine (v0.11.0) with config: dtype=torch.bfloat16, "
    "max_seq_len=8448, quantization=None, enforce_eager=True, kv_cache_dtype=auto, "
    "enable_prefix_caching=True, chunked_prefill_enabled=True\n"
)


def test_frozen_config_matches_execution_log():
    result = _module.runtime_execution_check(CONFIG, LOG)
    assert result["mismatches"] == []
    assert result["verified"]["max_num_seqs"] == {"declared": 8, "observed": 8}
    assert "gpu_memory_utilization" in result["default_inferred"]


def test_execution_mismatch_is_reported():
    bad = LOG.replace("'max_model_len': 8448", "'max_model_len': 4096").replace(
        "max_seq_len=8448", "max_seq_len=4096"
    )
    result = _module.runtime_execution_check(CONFIG, bad)
    assert any(item.startswith("max_model_len:") for item in result["mismatches"])


def test_missing_log_fields_stay_unverified():
    result = _module.runtime_execution_check(CONFIG, "INFO vLLM API server version 0.11.0\n")
    assert "max_num_seqs" in result["unverified"]
    assert result["mismatches"] == []


def test_declared_unquantized_but_log_quantized_is_a_mismatch():
    bad = CONFIG.copy()
    bad["server"] = {**CONFIG["server"], "quantization": None}
    text = LOG.replace("quantization=None", "quantization=awq")
    result = _module.runtime_execution_check(bad, text)
    assert any(item.startswith("quantization:") for item in result["mismatches"])
