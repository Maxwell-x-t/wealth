"""危机加仓：高 VIX ∧ 大回撤 → 直接追加预算（不走 MA 池）。

触发（与）：
- VIX >= 25
- 指数回撤 >= 20%（相对 lookback 内高点）

额外预算 = 倍数 × 当次定投额，并受年度上限约束。
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Dict, Optional, Tuple

# 回撤档 → (VIX 25–35 倍数, VIX ≥35 倍数)
CRISIS_MULT_TABLE: Tuple[Tuple[float, float, float], ...] = (
    (40.0, 0.75, 1.0),
    (30.0, 0.5, 0.75),
    (20.0, 0.25, 0.4),
)

VIX_GATE = 25.0
VIX_SEVERE = 35.0
MAX_SINGLE_MULTIPLIER = 1.0


def index_drawdown_pct(
    series: Dict[date, float],
    as_of: date,
    lookback_days: int = 365,
) -> Optional[float]:
    """as_of 收盘相对 lookback 窗口内高点的回撤（%）。"""
    if not series:
        return None
    window_start = as_of - timedelta(days=lookback_days)
    candidates = [(d, p) for d, p in series.items() if d <= as_of and p and p > 0]
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0])
    current = candidates[-1][1]
    peak = None
    for day, price in candidates:
        if day < window_start:
            continue
        peak = price if peak is None or price > peak else peak
    if peak is None or peak <= 0:
        return None
    return float((peak - current) / peak * 100.0)


def crisis_multiplier(vix_level: Optional[float], drawdown_pct: Optional[float]) -> float:
    """返回危机加仓倍数；不满足与条件时为 0。"""
    if vix_level is None or drawdown_pct is None:
        return 0.0
    if vix_level < VIX_GATE or drawdown_pct < 20.0:
        return 0.0
    severe = vix_level >= VIX_SEVERE
    for threshold, mild_mult, severe_mult in CRISIS_MULT_TABLE:
        if drawdown_pct >= threshold:
            return severe_mult if severe else mild_mult
    return 0.0


def resolve_crisis_boost(
    *,
    vix_level: Optional[float],
    drawdown_pct: Optional[float],
    base_amount: float,
    lookback_days: int = 365,
    annual_cap_amount: Optional[float] = None,
    annual_used_amount: float = 0.0,
) -> dict:
    """根据 VIX 与回撤计算直接追加预算。"""
    mult = min(crisis_multiplier(vix_level, drawdown_pct), MAX_SINGLE_MULTIPLIER)
    raw_extra = round(max(base_amount, 0.0) * mult, 2) if mult > 0 else 0.0
    annual_remaining = None
    if annual_cap_amount is not None:
        annual_remaining = max(float(annual_cap_amount) - float(annual_used_amount), 0.0)
    extra = min(raw_extra, annual_remaining) if annual_remaining is not None else raw_extra
    extra = round(extra, 2)
    tier = None
    if drawdown_pct is not None:
        for threshold, _, _ in CRISIS_MULT_TABLE:
            if drawdown_pct >= threshold:
                tier = int(threshold)
                break
    return {
        "triggered": extra > 0,
        "vix_level": round(float(vix_level), 2) if vix_level is not None else None,
        "drawdown_pct": round(float(drawdown_pct), 2) if drawdown_pct is not None else None,
        "tier": tier,
        "multiplier": round(mult, 4),
        "base_amount": round(base_amount, 2),
        "raw_extra_amount": raw_extra,
        "extra_amount": extra,
        "annual_cap_amount": round(float(annual_cap_amount), 2)
        if annual_cap_amount is not None
        else None,
        "annual_used_amount": round(max(float(annual_used_amount), 0.0), 2),
        "annual_remaining_amount": round(annual_remaining, 2)
        if annual_remaining is not None
        else None,
        "annual_cap_reached": raw_extra > extra,
        "lookback_days": lookback_days,
        "note": None
        if extra <= 0
        else (
            f"危机加仓 ×{mult:.2f}（VIX {vix_level:.1f} ∧ 回撤 {drawdown_pct:.1f}%）"
            f" +{extra:,.2f}"
        ),
    }


def max_index_drawdown_pct(
    nasdaq_series: Dict[date, float],
    sp500_series: Dict[date, float],
    as_of: date,
    lookback_days: int = 365,
) -> Tuple[Optional[float], Optional[str]]:
    """取纳指/标普较大回撤及其类别。"""
    best_dd = None
    best_cat = None
    for category, series in (("nasdaq", nasdaq_series), ("sp500", sp500_series)):
        dd = index_drawdown_pct(series, as_of, lookback_days)
        if dd is None:
            continue
        if best_dd is None or dd > best_dd:
            best_dd = dd
            best_cat = category
    return best_dd, best_cat
