"""pytest 公共 fixture。"""

from __future__ import annotations

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


@pytest.fixture
def row2_oracle() -> dict[str, Decimal]:
    """任务书 §4.1 的精确复算样例。"""
    return {
        "input_revenue_10k_per_gpu_year": Decimal("149.54340799296"),
        "output_revenue_10k_per_gpu_year": Decimal("27.61670592"),
        "total_revenue_10k_per_gpu_year": Decimal("177.16011391296"),
        "revenue_cost_ratio": Decimal("3.37447836024686"),
    }
