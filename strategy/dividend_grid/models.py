"""数据结构定义。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Stock:
    """一只个股的输入信息。"""

    code: str
    name: str
    dividend_yield: float  # 股息率，单位 %（如 5.26 表示 5.26%）
    weight: float = 0.12  # 该股满仓占总资产比例（默认 12%）
    yield_source: str = ""  # 股息率来源标注，如 "akshare dv_ttm 2026-08-18"
    overridden: bool = False  # 股息率是否被 overrides 文件覆盖
    group: str = "default"  # 行业分组（用于分组阈值与集中度统计）
    payout_ratio: Optional[float] = None  # 派息率%（分红质量）
    quality_flag: str = "unknown"  # ok / warn / risk / unknown
    quality_note: str = ""  # 质量说明
    data_available: bool = True
    dividend_available: bool = True
    dividend_note: str = ""
    yield_percentile: Optional[float] = None  # 该股股息率在自身滚动窗口中的分位（0~100），方案A用
    coverage_kind: str = "unknown"  # fcf / bank / unknown —— 分红可持续性判据
    coverage_flag: str = "unknown"  # ok / warn / risk / unknown
    coverage_note: str = ""  # 覆盖度说明
    fcf_coverage: Optional[float] = None  # 单窗口(TTM) FCF ÷ 分红（银行不适用则为 None）
    fcf_avg_coverage: Optional[float] = None  # 多年合计 FCF ÷ 合计分红（主判据）
    fcf_avg_years: int = 0  # 多年平均覆盖的完整财年数
    price: Optional[float] = None  # 最新价（用于推算各档股息率对应的买入/卖出价）
    dividend_fiscal_year: Optional[int] = None  # 财年口径所用财年（仅展示口径，不参与判定）
    dividend_dps: Optional[float] = None  # 该财年每股分红合计（含中期），仅展示


@dataclass
class Decision:
    """针对一只个股的网格决策结果。"""

    stock: Stock
    current_shares: float
    target_shares: float
    action: str  # BUY / SELL / HOLD / HOLD_BAND
    delta_shares: float  # 正数=买入份数，负数=卖出份数，0=维持
    band_label: str  # 所处网格档位描述
    share_value_pct: float  # 单份净值占总资产比例（%）
    buy_price_ladder: tuple[tuple[float, float], ...] = ()  # ((目标份数, 对应股价), ...)
    sell_price_ladder: tuple[tuple[float, float], ...] = ()  # ((保留份数, 对应股价), ...)
    buy_thresholds: tuple[float, ...] = ()  # 生成买入价所用的股息率档位
    sell_thresholds: tuple[float, ...] = ()  # 生成卖出价所用的股息率档位

    @property
    def delta_value_pct(self) -> float:
        """本次操作对应的净值变动（% 总资产），正买负卖。"""
        return self.delta_shares * self.share_value_pct

    @property
    def target_value_pct(self) -> float:
        return self.target_shares * self.share_value_pct

    @property
    def current_value_pct(self) -> float:
        return self.current_shares * self.share_value_pct

    @property
    def action_cn(self) -> str:
        return {
            "BUY": "买入",
            "SELL": "卖出",
            "HOLD": "维持",
            "HOLD_BAND": "持有带",
        }.get(self.action, self.action)


@dataclass
class PortfolioResult:
    """组合层面的汇总结果。"""

    decisions: list[Decision] = field(default_factory=list)
    avg_yield: float = 0.0  # 组合加权平均股息率（仅展示）
    current_cash_pct: float = 100.0  # 当前现金仓位（%）
    target_cash_pct: float = 100.0  # 目标现金仓位（%）
    current_position_pct: float = 0.0
    target_position_pct: float = 0.0
    warnings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    external_industry_pct: dict[str, float] = field(default_factory=dict)
    external_position_pct: float = 0.0
    reserved_cash_pct: float = 0.0

    @property
    def triggered(self) -> list[Decision]:
        """产生买/卖信号的个股（用于提醒）。"""
        return [d for d in self.decisions if d.action in ("BUY", "SELL")]

    @property
    def total_buy_pct(self) -> float:
        return sum(d.delta_value_pct for d in self.decisions if d.delta_shares > 0)

    @property
    def total_sell_pct(self) -> float:
        return sum(-d.delta_value_pct for d in self.decisions if d.delta_shares < 0)
