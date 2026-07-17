"""定投 MA200 偏离度因子。

思路：以指数月线收盘价相对其长期均线（约 200 交易日≈10 个月）的偏离度，
调整当月定投金额——偏贵少投、偏便宜多投。考虑到市场长期多头、价格更多时间
处于均线上方，引入「中性区上移」center_pct，让因子的长期均值更接近 1。
"""

from __future__ import annotations

from datetime import date
from typing import Dict, Optional, Tuple

from app.services.holdings import CATEGORY_LABELS
from app.services.index_history import (
    NASDAQ_SYMBOL,
    SP500_SYMBOL,
    fetch_monthly_closes,
    price_on_date,
    price_on_month,
)

INDEX_SYMBOL = {"nasdaq": NASDAQ_SYMBOL, "sp500": SP500_SYMBOL}

# 一个月约 21 个交易日
TRADING_DAYS_PER_MONTH = 21
TRADING_DAYS_PER_WEEK = 5


def _is_truthy(value) -> bool:
    return str(value) in ("1", "true", "True")


def resolve_ma_settings(config: dict) -> dict:
    return {
        "enabled": _is_truthy(config.get("dca_ma_enabled", "0")),
        "window_days": int(float(config.get("dca_ma_window_days", 200))),
        "min_factor": float(config.get("dca_ma_min_factor", 0.7)),
        "max_factor": float(config.get("dca_ma_max_factor", 1.3)),
        "band_pct": float(config.get("dca_ma_band_pct", 20)),
        "center_pct": float(config.get("dca_ma_center_pct", 8)),
    }


def window_months_from_days(window_days: int) -> int:
    return max(2, round(window_days / TRADING_DAYS_PER_MONTH))


def window_weeks_from_days(window_days: int) -> int:
    return max(2, round(window_days / TRADING_DAYS_PER_WEEK))


def compute_ma_factor(deviation_pct: float, settings: dict) -> float:
    """把偏离度映射到金额因子。

    - 偏离度以 center_pct 为中性点：adj = deviation - center
    - adj>=0（偏贵）：线性从 1.0 降到 min_factor
    - adj<0（偏便宜）：线性从 1.0 升到 max_factor
    """
    center = settings.get("center_pct", 0.0)
    band = settings.get("band_pct", 20.0)
    if band <= 0:
        band = 1.0
    min_factor = settings.get("min_factor", 0.7)
    max_factor = settings.get("max_factor", 1.3)

    adj = deviation_pct - center
    if adj >= 0:
        frac = min(adj / band, 1.0)
        factor = 1.0 - frac * (1.0 - min_factor)
    else:
        frac = min(-adj / band, 1.0)
        factor = 1.0 + frac * (max_factor - 1.0)
    return max(min_factor, min(max_factor, factor))


VIX_TOTAL_MIN_FACTOR = 0.7
VIX_TOTAL_MAX_FACTOR = 1.4


def vix_risk_factor(vix_level: float) -> float:
    """VIX 风险门控：分段上调买入因子。"""
    if vix_level < 18:
        return 1.0
    if vix_level < 25:
        return 1.05
    if vix_level < 35:
        return 1.10
    return 1.20


def combine_risk_factors(ma_factor: float, vix_factor: float) -> float:
    return max(VIX_TOTAL_MIN_FACTOR, min(VIX_TOTAL_MAX_FACTOR, ma_factor * vix_factor))


def live_category_ma_factor(
    config: dict,
    category: str,
    live_price: float,
    as_of: Optional[date] = None,
    daily_series: Optional[Dict[date, float]] = None,
) -> dict:
    """盘中：用日频 MA + 实时指数价计算因子。"""
    settings = resolve_ma_settings(config)
    result = _blank_result(category, settings["enabled"])
    if live_price <= 0:
        return result

    symbol = INDEX_SYMBOL.get(category)
    if not symbol:
        return result

    as_of = as_of or date.today()
    window_days = settings["window_days"]
    lead_start = date(as_of.year - 2, 1, 1)

    if daily_series is None:
        try:
            from app.services.index_history import fetch_daily_closes

            daily_series = fetch_daily_closes(symbol, lead_start, as_of, timeout=15.0)
        except Exception:
            return result

    series = dict(daily_series)
    series[as_of] = float(live_price)

    deviation = daily_ma_deviation(series, as_of, window_days)
    if deviation is None:
        return result

    factor = compute_ma_factor(deviation, settings) if settings["enabled"] else 1.0
    result["enabled"] = settings["enabled"]
    result["deviation_pct"] = round(deviation, 2)
    result["factor"] = round(factor, 4)
    result["live_price"] = round(live_price, 2)
    if settings["enabled"] and abs(factor - 1.0) > 1e-6:
        result["note"] = _factor_note(category, deviation, factor)
    return result


def monthly_ma_deviation(
    series: Dict[Tuple[int, int], float],
    year: int,
    month: int,
    window_months: int,
) -> Optional[float]:
    """当月收盘相对最近 window_months 个月均线的偏离度（%）。"""
    keys = sorted(k for k in series if k <= (year, month))
    if len(keys) < window_months:
        return None
    window = keys[-window_months:]
    ma = sum(series[k] for k in window) / len(window)
    if ma <= 0:
        return None
    current = price_on_month(series, year, month)
    if current is None or current <= 0:
        return None
    return (current - ma) / ma * 100.0


def daily_ma_deviation(
    series: Dict[date, float],
    as_of: date,
    window_days: int,
) -> Optional[float]:
    """当日收盘相对最近 window_days 个交易日均线的偏离度（%）。"""
    keys = sorted(d for d in series if d <= as_of)
    if len(keys) < window_days:
        return None
    window = keys[-window_days:]
    ma = sum(series[k] for k in window) / len(window)
    if ma <= 0:
        return None
    current = price_on_date(series, as_of)
    if current is None or current <= 0:
        return None
    return (current - ma) / ma * 100.0


def weekly_ma_deviation(
    series: Dict[date, float],
    as_of: date,
    window_weeks: int,
) -> Optional[float]:
    """当周收盘相对最近 window_weeks 个周均线的偏离度（%）。"""
    keys = sorted(d for d in series if d <= as_of)
    if len(keys) < window_weeks:
        return None
    window = keys[-window_weeks:]
    ma = sum(series[k] for k in window) / len(window)
    if ma <= 0:
        return None
    current = price_on_date(series, as_of)
    if current is None or current <= 0:
        return None
    return (current - ma) / ma * 100.0


def _factor_note(category: str, deviation_pct: float, factor: float) -> str:
    label = CATEGORY_LABELS.get(category, category)
    if deviation_pct >= 0:
        pos = f"高于均线 {deviation_pct:.1f}%"
    else:
        pos = f"低于均线 {abs(deviation_pct):.1f}%"
    return f"{label}{pos}，本月定投 ×{factor:.2f}"


def _blank_result(category: str, enabled: bool) -> dict:
    return {
        "enabled": enabled,
        "category": category,
        "factor": 1.0,
        "deviation_pct": None,
        "note": None,
    }


def category_ma_factor(
    config: dict,
    category: str,
    as_of: Optional[date] = None,
) -> dict:
    """实盘：计算某大类当前月的 MA 因子。数据源为指数月线收盘。"""
    settings = resolve_ma_settings(config)
    result = _blank_result(category, settings["enabled"])
    if not settings["enabled"]:
        return result

    symbol = INDEX_SYMBOL.get(category)
    if not symbol:
        return result

    as_of = as_of or date.today()
    window_months = window_months_from_days(settings["window_days"])
    lead_years = window_months // 12 + 2
    ma_start = date(as_of.year - lead_years, 1, 1)
    try:
        series = fetch_monthly_closes(symbol, ma_start, as_of)
    except Exception:
        return result

    deviation = monthly_ma_deviation(series, as_of.year, as_of.month, window_months)
    if deviation is None:
        return result

    factor = compute_ma_factor(deviation, settings)
    result["deviation_pct"] = round(deviation, 2)
    result["factor"] = round(factor, 4)
    if abs(factor - 1.0) > 1e-6:
        result["note"] = _factor_note(category, deviation, factor)
    return result


def resolve_current_month_factors(
    config: dict,
    as_of: Optional[date] = None,
) -> Dict[str, dict]:
    """返回 nasdaq/sp500 两个大类的当月 MA 因子。"""
    settings = resolve_ma_settings(config)
    if not settings["enabled"]:
        return {}
    factors: Dict[str, dict] = {}
    for category in ("nasdaq", "sp500"):
        factors[category] = category_ma_factor(config, category, as_of)
    return factors
