"""离线 LLM 部署基准数据与 Token 定价计算引擎 (MVP)。"""

__version__ = "0.1.0"

#: 年度秒数：365 天。不得用"扣除维护日后的时间"替代（任务书 §9）。
SECONDS_PER_YEAR = 365 * 24 * 3600  # 31_536_000
TOKEN_PRICE_UNIT = 1_000_000
TEN_THOUSAND = 10_000

__all__ = ["SECONDS_PER_YEAR", "TEN_THOUSAND", "TOKEN_PRICE_UNIT", "__version__"]
