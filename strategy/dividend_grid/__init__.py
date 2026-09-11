"""红利网格仓位管理与提醒（纯个股版）。

核心原则：个股的买入卖出只看各自股息率，不看组合平均。
"""

from .models import Stock, Decision, PortfolioResult
from .grid import decide, evaluate_portfolio, GridConfig, GroupThresholds
from .quality import QualityConfig, assess as assess_quality
from .datasource import QualityMetrics

__all__ = [
    "Stock",
    "Decision",
    "PortfolioResult",
    "decide",
    "evaluate_portfolio",
    "GridConfig",
    "GroupThresholds",
    "QualityConfig",
    "QualityMetrics",
    "assess_quality",
]
