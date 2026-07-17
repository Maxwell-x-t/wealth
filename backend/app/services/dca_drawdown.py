from __future__ import annotations

from datetime import date, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from app.models.models import Transaction
from app.services.allocation import find_instrument, resolve_instrument_codes
from app.services.account_plan import (
    month_index_for_date,
    phase_for_month_index,
    resolve_unified_plan_settings,
)
from app.services.dca_crisis_boost import (
    index_drawdown_pct,
    resolve_crisis_boost,
)
from app.services.dca_schedule_core import dca_amount_for_month
from app.services.holdings import CATEGORY_LABELS, build_historical_price_index, price_on_date
from app.services.index_history import (
    NASDAQ_SYMBOL,
    SP500_SYMBOL,
    VIX_SYMBOL,
    fetch_daily_closes,
    latest_bundled_price,
)
from app.services.market_data import fetch_yahoo_index_quote
from app.services.plan_phase import tx_amount_cny, tx_effective_phase

DCA_INDEX_CATEGORIES = ("nasdaq", "sp500")


def _is_truthy_config(value) -> bool:
    return str(value) in ("1", "true", "True")


def _annual_dca_budget_cny(config: dict, year: int) -> float:
    settings = resolve_unified_plan_settings(config)
    total = 0.0
    for month in range(1, 13):
        month_day = date(year, month, 1)
        month_index = month_index_for_date(settings["plan_start"], month_day)
        if phase_for_month_index(settings, month_index) != "dca":
            continue
        total += dca_amount_for_month(settings["dca_amount_schedule"], year, month)
    return round(total, 2)


def estimate_annual_crisis_used_cny(
    db: Session,
    config: dict,
    as_of: date,
) -> float:
    """保守估算年度额外投入：当年定投买入额超过截至当月常规计划的部分。"""
    settings = resolve_unified_plan_settings(config)
    planned_through_month = 0.0
    for month in range(1, as_of.month + 1):
        month_day = date(as_of.year, month, 1)
        month_index = month_index_for_date(settings["plan_start"], month_day)
        if phase_for_month_index(settings, month_index) == "dca":
            planned_through_month += dca_amount_for_month(
                settings["dca_amount_schedule"], as_of.year, month
            )

    usd_cny_rate = float(config.get("usd_cny_rate", 7.2))
    actual_dca = 0.0
    rows = (
        db.query(Transaction)
        .filter(
            Transaction.side == "buy",
            Transaction.trade_date >= date(as_of.year, 1, 1),
            Transaction.trade_date <= as_of,
        )
        .all()
    )
    for tx in rows:
        if tx_effective_phase(tx, config) == "dca":
            actual_dca += tx_amount_cny(tx, usd_cny_rate)
    return round(max(actual_dca - planned_through_month, 0.0), 2)


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


def _resolve_vix_level(as_of: date) -> tuple[Optional[float], Optional[str]]:
    try:
        quote = fetch_yahoo_index_quote(VIX_SYMBOL, timeout=8.0)
        return float(quote.price), "yahoo"
    except Exception as exc:  # noqa: BLE001
        local = latest_bundled_price(VIX_SYMBOL)
        if local is not None:
            return float(local), "local_monthly"
        try:
            series = fetch_daily_closes(VIX_SYMBOL, as_of - timedelta(days=14), as_of, timeout=8.0)
            if series:
                latest_day = max(series)
                return float(series[latest_day]), "daily_cache"
        except Exception:
            pass
        return None, str(exc)


def _resolve_index_drawdowns(as_of: date, lookback_days: int) -> dict[str, float]:
    drawdowns: dict[str, float] = {}
    lead = as_of - timedelta(days=lookback_days + 40)
    for category, symbol in (("nasdaq", NASDAQ_SYMBOL), ("sp500", SP500_SYMBOL)):
        try:
            series = fetch_daily_closes(symbol, lead, as_of, timeout=12.0)
            dd = index_drawdown_pct(series, as_of, lookback_days)
            if dd is not None:
                drawdowns[category] = round(dd, 2)
        except Exception:
            continue
    return drawdowns


def compute_drawdown_boost(
    db: Session,
    config: dict,
    as_of: Optional[date] = None,
    base_amount_cny: float = 0.0,
) -> dict:
    """危机加仓（合并原跌幅加仓）：VIX≥25 且回撤≥20% → 倍数×当月定投。

    base_amount_cny：当月统一定投额（人民币）；若为 0 则仅返回状态不落金额。
    仍受可用现金与月度上限约束。
    """
    as_of = as_of or date.today()
    cash_available = float(config.get("dca_boost_cash_available", 0) or 0)
    lookback_days = int(float(config.get("dca_boost_lookback_days", 365)))
    annual_cap_pct = max(float(config.get("dca_boost_annual_cap_pct", 50)), 0.0)
    annual_cap_amount = _annual_dca_budget_cny(config, as_of.year) * annual_cap_pct / 100
    annual_used_amount = estimate_annual_crisis_used_cny(db, config, as_of)

    result = {
        "enabled": _is_truthy_config(config.get("dca_boost_enabled", "1")),
        "mode": "crisis_and",
        "tier": None,
        "tier_amount": 0.0,
        "applied_amount": 0.0,
        "cash_available": round(cash_available, 2),
        "max_drawdown_pct": 0.0,
        "trigger_category": None,
        "drawdowns": {},
        "vix_level": None,
        "vix_source": None,
        "multiplier": 0.0,
        "annual_cap_pct": annual_cap_pct,
        "annual_cap_amount": round(annual_cap_amount, 2),
        "annual_used_amount": annual_used_amount,
        "annual_remaining_amount": round(
            max(annual_cap_amount - annual_used_amount, 0.0), 2
        ),
        "annual_cap_reached": False,
        "note": None,
    }

    if not result["enabled"]:
        return result

    # 指数回撤（与回测一致）；若拉不到再退回持仓 ETF 回撤
    drawdowns = _resolve_index_drawdowns(as_of, lookback_days)
    if not drawdowns:
        price_index = build_historical_price_index(db)
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

    vix_level, vix_source = _resolve_vix_level(as_of)
    result["vix_level"] = round(vix_level, 2) if vix_level is not None else None
    result["vix_source"] = vix_source

    # 无明确月额时，用配置档位金额作为 base 的兼容：取当前回撤档固定额当「基准」再乘？
    # 按约定：倍数 × 当次定投。base_amount_cny 由调用方传入。
    base = float(base_amount_cny or 0.0)
    if base <= 0:
        # 兼容：用旧档位金额作为展示基准（不自动入账），便于无月额时仍提示
        legacy_tiers = {
            40: float(config.get("dca_boost_40_pct_amount", 30000)),
            30: float(config.get("dca_boost_30_pct_amount", 20000)),
            20: float(config.get("dca_boost_20_pct_amount", 10000)),
        }
        for threshold, amount in legacy_tiers.items():
            if max_dd >= threshold:
                base = amount
                break

    crisis = resolve_crisis_boost(
        vix_level=vix_level,
        drawdown_pct=max_dd,
        base_amount=base,
        lookback_days=lookback_days,
        annual_cap_amount=annual_cap_amount,
        annual_used_amount=annual_used_amount,
    )
    result["tier"] = crisis["tier"]
    result["multiplier"] = crisis["multiplier"]
    result["tier_amount"] = crisis["extra_amount"]
    result["annual_cap_reached"] = crisis["annual_cap_reached"]

    if not crisis["triggered"]:
        if crisis["annual_cap_reached"] and crisis["multiplier"] > 0:
            result["note"] = (
                f"危机条件已满足，但年度加仓额度已用完"
                f"（上限 ¥{annual_cap_amount:,.0f}）"
            )
        elif max_dd >= 20 and (vix_level is None or vix_level < 25):
            label = CATEGORY_LABELS.get(trigger_category, trigger_category)
            vix_txt = f"{vix_level:.1f}" if vix_level is not None else "未知"
            result["note"] = (
                f"{label}回撤 {max_dd:.1f}% 但 VIX={vix_txt}<25，未触发危机加仓"
            )
        return result

    if cash_available <= 0:
        label = CATEGORY_LABELS.get(trigger_category, trigger_category)
        result["note"] = (
            f"建议危机加仓 ¥{crisis['extra_amount']:,.0f}"
            f"（{label}回撤 {max_dd:.1f}% ∧ VIX {vix_level:.1f}，×{crisis['multiplier']:.2f}），"
            f"请填写可用现金"
        )
        return result

    applied = min(crisis["extra_amount"], cash_available)
    cap = float(config.get("dca_boost_monthly_cap", 30000))
    if cap > 0:
        applied = min(applied, cap)
    applied = round(max(applied, 0.0), 2)
    result["applied_amount"] = applied
    result["annual_remaining_amount"] = round(
        max(annual_cap_amount - annual_used_amount - applied, 0.0), 2
    )

    if applied > 0:
        label = CATEGORY_LABELS.get(trigger_category, trigger_category)
        result["note"] = (
            f"危机加仓 +¥{applied:,.0f}"
            f"（{label}回撤 {max_dd:.1f}% ∧ VIX {vix_level:.1f}，×{crisis['multiplier']:.2f}；"
            f"年度上限 ¥{annual_cap_amount:,.0f}）"
        )
    return result
