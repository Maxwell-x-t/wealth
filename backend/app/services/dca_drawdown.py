from __future__ import annotations

from datetime import date, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from app.services.allocation import find_instrument, resolve_instrument_codes
from app.services.holdings import CATEGORY_LABELS, build_historical_price_index, price_on_date

DCA_INDEX_CATEGORIES = ("nasdaq", "sp500")


def _is_truthy_config(value) -> bool:
    return str(value) in ("1", "true", "True")


def _instrument_drawdown_pct(
    price_index: dict,
    instrument_id: int,
    as_of: date,
    lookback_days: int,
) -> Optional[float]:
    series = price_index.get(instrument_id, [])
    if not series:
        return None

    current = price_on_date(price_index, instrument_id, as_of)
    if current is None or current <= 0:
        return None

    window_start = as_of - timedelta(days=lookback_days)
    recent_high = None
    for snap_date, price in series:
        if snap_date > as_of:
            break
        if snap_date >= window_start:
            recent_high = price if recent_high is None or price > recent_high else recent_high

    if recent_high is None or recent_high <= 0:
        return None

    return float((recent_high - current) / recent_high * 100)


def _category_drawdown(
    db: Session,
    config: dict,
    category: str,
    price_index: dict,
    as_of: date,
    lookback_days: int,
) -> float:
    codes = resolve_instrument_codes(config)
    max_dd = 0.0
    for account_key, account_name in (("mainland", "大陆"), ("hk", "香港")):
        code = codes.get(account_key, {}).get(category)
        if not code:
            continue
        instrument = find_instrument(db, account_name, code)
        if not instrument:
            continue
        dd = _instrument_drawdown_pct(price_index, instrument.id, as_of, lookback_days)
        if dd is not None and dd > max_dd:
            max_dd = dd
    return max_dd


def compute_drawdown_boost(
    db: Session,
    config: dict,
    as_of: Optional[date] = None,
) -> dict:
    """计算本月跌幅加仓：取 20/30/40% 最高档，受可用现金与 40% 档上限约束。"""
    as_of = as_of or date.today()
    cash_available = float(config.get("dca_boost_cash_available", 0) or 0)

    result = {
        "enabled": _is_truthy_config(config.get("dca_boost_enabled", "1")),
        "tier": None,
        "tier_amount": 0.0,
        "applied_amount": 0.0,
        "cash_available": round(cash_available, 2),
        "max_drawdown_pct": 0.0,
        "trigger_category": None,
        "drawdowns": {},
        "note": None,
    }

    if not result["enabled"]:
        return result

    lookback_days = int(float(config.get("dca_boost_lookback_days", 365)))
    price_index = build_historical_price_index(db)

    drawdowns: dict[str, float] = {}
    for category in DCA_INDEX_CATEGORIES:
        drawdowns[category] = round(
            _category_drawdown(db, config, category, price_index, as_of, lookback_days),
            2,
        )

    if not drawdowns:
        return result

    max_dd = max(drawdowns.values())
    trigger_category = max(drawdowns, key=lambda key: drawdowns[key])
    result["drawdowns"] = drawdowns
    result["max_drawdown_pct"] = round(max_dd, 2)
    result["trigger_category"] = trigger_category

    tiers = [
        (40, float(config.get("dca_boost_40_pct_amount", 30000))),
        (30, float(config.get("dca_boost_30_pct_amount", 20000))),
        (20, float(config.get("dca_boost_20_pct_amount", 10000))),
    ]
    tier = None
    tier_amount = 0.0
    for threshold, amount in tiers:
        if max_dd >= threshold:
            tier = threshold
            tier_amount = amount
            break

    if tier is None or tier_amount <= 0:
        return result

    result["tier"] = tier
    result["tier_amount"] = round(tier_amount, 2)

    if cash_available <= 0:
        label = CATEGORY_LABELS.get(trigger_category, trigger_category)
        result["note"] = (
            f"建议跌幅加仓 ¥{tier_amount:,.0f}（{label}回撤 {max_dd:.1f}%），请填写可用现金"
        )
        return result

    applied = min(tier_amount, cash_available)
    if tier >= 40:
        cap = float(config.get("dca_boost_monthly_cap", 30000))
        applied = min(applied, cap)

    applied = round(max(applied, 0.0), 2)
    result["applied_amount"] = applied

    if applied > 0:
        label = CATEGORY_LABELS.get(trigger_category, trigger_category)
        result["note"] = f"跌幅加仓 +¥{applied:,.0f}（{label}回撤 {max_dd:.1f}%）"

    return result
