"""pytest 公共 fixture。"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from llm_capacity_pricing.services import (
    import_benchmark_file,
    import_excel_into_db,
    init_db,
)

ROOT = Path(__file__).resolve().parents[1]
EXCEL = ROOT / "data" / "raw" / "Token产能计算器-0910.xlsx"
FIXTURES = ROOT / "data" / "fixtures"
CONFIG = ROOT / "config"

#: 任务书 §9：旧表全量复算的比较精度
TOLERANCE_10K = Decimal("1e-6")  # 金额（万元）
TOLERANCE_RATIO = Decimal("1e-8")  # 营收/成本比


@pytest.fixture(scope="session")
def root() -> Path:
    return ROOT


@pytest.fixture(scope="session")
def excel_path() -> Path:
    # 原始 Excel 含价格与成本信息，按任务书 §6「敏感价格文件建议不入 Git」未纳入版本库。
    # 缺失时跳过依赖它的用例（而不是报错），克隆仓库后把文件放回 data/raw/ 即可跑全量。
    if not EXCEL.exists():
        pytest.skip(
            f"缺少输入资产 {EXCEL.relative_to(ROOT)}（未入库，需自行提供）"
        )
    return EXCEL


@pytest.fixture
def empty_db(tmp_path: Path) -> str:
    db = tmp_path / "empty.db"
    init_db(db)
    return str(db)


@pytest.fixture
def excel_db(tmp_path: Path, excel_path: Path) -> str:
    """仅导入 Excel 的数据库。"""
    db = tmp_path / "excel.db"
    init_db(db)
    import_excel_into_db(excel_path, db)
    return str(db)


@pytest.fixture
def full_db(excel_db: str) -> str:
    """Excel + 全部 synthetic fixture。"""
    for kind, file, meta in (
        ("sglang", FIXTURES / "sglang_synthetic.jsonl", CONFIG / "fixture_sglang.yaml"),
        (
            "sglang",
            FIXTURES / "sglang_renamed_fields.jsonl",
            CONFIG / "fixture_sglang_renamed.yaml",
        ),
        ("vllm", FIXTURES / "vllm_synthetic.json", CONFIG / "fixture_vllm.yaml"),
        (
            "vllm",
            FIXTURES / "vllm_zero_success.json",
            CONFIG / "fixture_vllm_zero_success.yaml",
        ),
    ):
        import_benchmark_file(kind, file, meta, excel_db)
    return excel_db


#: 真实业务数据的期望值放在**不入库**的 data/private/ 下（V1.2 F1）：
#: 公开仓库只保留脱敏样本（data/examples/），内部 oracle 留在本地。
PRIVATE_ORACLE = ROOT / "data" / "private" / "row2_oracle.json"
#: 脱敏公开样本（可入库）：与真实工作簿同结构，数值为人工构造
SAMPLE_EXCEL = ROOT / "data" / "examples" / "Token产能计算器-SAMPLE.xlsx"
SAMPLE_EXPECTED = ROOT / "data" / "examples" / "public_sample_expected.json"


@pytest.fixture
def row2_oracle() -> dict[str, Decimal]:
    """真实工作簿第 2 行的复算期望值（内部数据，缺失则跳过）。"""
    if not PRIVATE_ORACLE.exists():
        pytest.skip(
            "缺少内部 oracle：data/private/row2_oracle.json（按 V1.2 F1 不入库）。"
            "公开克隆可用 data/examples/ 的脱敏样本验证公式。"
        )
    data = json.loads(PRIVATE_ORACLE.read_text(encoding="utf-8"))
    return {k: Decimal(str(v)) for k, v in data.items() if not k.startswith("_")}


@pytest.fixture(scope="session")
def sample_excel_path() -> Path:
    """脱敏样本工作簿（公开）。"""
    if not SAMPLE_EXCEL.exists():
        pytest.skip("缺少脱敏样本，请先运行 scripts/make_public_sample.py")
    return SAMPLE_EXCEL


@pytest.fixture(scope="session")
def sample_expected() -> dict:
    if not SAMPLE_EXPECTED.exists():
        pytest.skip("缺少脱敏样本期望值，请先运行 scripts/make_public_sample.py")
    return json.loads(SAMPLE_EXPECTED.read_text(encoding="utf-8"))
