from __future__ import annotations

from datetime import date
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from app.services.account_plan import (
    month_index_for_date,
    phase_for_month_index,
    resolve_all_account_settings,
    resolve_unified_plan_settings,
)
from app.services.cn_market_hours import cn_etf_session
from app.services.dca_ma_factor import (
    INDEX_SYMBOL,
    live_category_ma_factor,
    resolve_ma_settings,
)
from app.services.dca_crisis_boost import max_index_drawdown_pct, resolve_crisis_boost
from app.services.dca_drawdown import estimate_annual_crisis_used_cny
from app.services.dca_schedule_core import dca_amount_for_month
from app.services.holdings import CATEGORY_LABELS
from app.services.index_history import (
    NASDAQ_SYMBOL,
    SP500_SYMBOL,
    VIX_SYMBOL,
    fetch_daily_closes,
    latest_bundled_price,
)
from app.services.investment_plan import generate_investment_plans
from app.services.market_data import fetch_yahoo_index_quote

MAINLAND_ACCOUNT = "大陆"
EXECUTE_FACTOR_THRESHOLD = 1.05
DEFER_FACTOR_THRESHOLD = 0.90
EARLY_FACTOR_THRESHOLD = 1.10


def _category_label(category: str) -> str:
    return CATEGORY_LABELS.get(category, category)


def _plan_amount(row: dict) -> float:
    weekly_base = float(row.get("base_amount_cny", row.get("amount_cny", 0)))
    rollover = float(row.get("rolled_over_amount_cny", 0))
    credit = float(row.get("credit_offset_cny", 0))
    if rollover > 0 or credit > 0:
        return max(0.0, weekly_base + rollover - credit)
    return weekly_base


def _annual_dca_budget_cny(config: dict, year: int) -> float:
    settings = resolve_unified_plan_settings(config)
    total = 0.0
    for month in range(1, 13):
        month_day = date(year, month, 1)
        month_index = month_index_for_date(settings["plan_start"], month_day)
        if phase_for_month_index(settings, month_index) == "dca":
            total += dca_amount_for_month(settings["dca_amount_schedule"], year, month)
    return round(total, 2)


def _month_mainland_dca_stats(plans: List[dict], today: date) -> dict:
    month_plans = [
        item
        for item in plans
        if item["account"] == MAINLAND_ACCOUNT
        and item["phase"] == "dca"
        and item["plan_date"].year == today.year
        and item["plan_date"].month == today.month
    ]
    month_planned = sum(float(item.get("base_amount_cny", item.get("amount_cny", 0))) for item in month_plans)
    month_matched = sum(
        float(item.get("matched_amount_cny", 0))
        for item in month_plans
        if item["plan_date"] <= today and item["status"] in ("done", "partial")
    )
    month_remaining = max(0.0, round(month_planned - month_matched, 2))
    return {
        "month_planned_cny": round(month_planned, 2),
        "month_matched_cny": round(month_matched, 2),
        "month_remaining_cny": month_remaining,
    }


def _today_mainland_items(plans: List[dict], today: date) -> List[dict]:
    return [
        item
        for item in plans
        if item["account"] == MAINLAND_ACCOUNT
        and item["plan_date"] == today
        and item["phase"] in ("dca", "building")
        and item["status"] not in ("done", "skipped")
    ]


def _suggest_action(
    has_today_plan: bool,
    avg_effective_factor: float,
    market_open: bool,
) -> dict:
    if not market_open:
        return {
            "code": "market_closed",
            "label": "非交易时段",
            "summary": "大陆 ETF 收盘后仅展示参考信号，下一交易日再关注提醒。",
        }

    if avg_effective_factor >= EARLY_FACTOR_THRESHOLD and not has_today_plan:
        return {
            "code": "early",
            "label": "可提前执行",
            "summary": "因子偏友好，本月仍有预算，可考虑提前买入大陆 ETF。",
        }
    if avg_effective_factor >= EXECUTE_FACTOR_THRESHOLD:
        return {
            "code": "execute",
            "label": "建议执行",
            "summary": "因子偏友好，适合按建议金额买入（今日有计划则优先执行计划）。",
        }
    if has_today_plan and avg_effective_factor < DEFER_FACTOR_THRESHOLD:
        return {
            "code": "defer",
            "label": "建议暂缓",
            "summary": "指数偏贵，今日原计划可暂缓，等待更优价位。",
        }
    return {
        "code": "neutral",
        "label": "观望",
        "summary": "因子中性，可按原计划执行，也可继续观望。",
    }


def _notification_hint(action_code: str, avg_factor: float) -> Optional[dict]:
    if action_code == "execute":
        return {
            "level": "positive",
            "title": "定投信号：建议执行",
            "body": f"大陆 ETF 因子 ×{avg_factor:.2f}，适合买入。",
        }
    if action_code == "early":
        return {
            "level": "positive",
            "title": "定投信号：可提前执行",
            "body": f"大陆 ETF 因子 ×{avg_factor:.2f}，本月预算内可择机买入。",
        }
    if action_code == "defer":
        return {
            "level": "warning",
            "title": "定投信号：建议暂缓",
            "body": f"大陆 ETF 因子 ×{avg_factor:.2f}，今日可暂缓执行。",
        }
    return None


def build_dca_live_signal(db: Session, config: dict) -> dict:
    today = date.today()
    market = cn_etf_session()
    ma_settings = resolve_ma_settings(config)
    plans = generate_investment_plans(db, config)
    month_stats = _month_mainland_dca_stats(plans, today)
    today_items = _today_mainland_items(plans, today)

    account_settings = resolve_all_account_settings(config)
    mainland_settings = account_settings.get(MAINLAND_ACCOUNT, {})
    plan_start = mainland_settings.get("plan_start", today)
    month_index = month_index_for_date(plan_start, today)
    in_dca_phase = phase_for_month_index(mainland_settings, month_index) == "dca"

    index_errors: Dict[str, str] = {}
    quotes: Dict[str, float] = {}
    quote_sources: Dict[str, str] = {}
    for symbol in (NASDAQ_SYMBOL, SP500_SYMBOL, VIX_SYMBOL):
        try:
            quote = fetch_yahoo_index_quote(symbol, timeout=8.0)
            quotes[symbol] = quote.price
            quote_sources[symbol] = "yahoo"
        except Exception as exc:  # noqa: BLE001
            index_errors[symbol] = str(exc)
            if symbol == VIX_SYMBOL:
                local_vix = latest_bundled_price(VIX_SYMBOL)
                if local_vix is not None:
                    quotes[symbol] = local_vix
                    quote_sources[symbol] = "local_monthly"
                    index_errors[symbol] = f"Yahoo 失败，已用本地月线: {exc}"

    nasdaq_daily = sp500_daily = None
    try:
        lead_start = date(today.year - 2, 1, 1)
        if NASDAQ_SYMBOL not in index_errors or NASDAQ_SYMBOL in quotes:
            nasdaq_daily = fetch_daily_closes(NASDAQ_SYMBOL, lead_start, today, timeout=8.0)
        if SP500_SYMBOL not in index_errors or SP500_SYMBOL in quotes:
            sp500_daily = fetch_daily_closes(SP500_SYMBOL, lead_start, today, timeout=8.0)
    except Exception:
        nasdaq_daily = nasdaq_daily or None
        sp500_daily = sp500_daily or None

    # Yahoo 失败时，指数价也可回退本地月线，保证盘中信号仍可算 MA
    for symbol in (NASDAQ_SYMBOL, SP500_SYMBOL):
        if symbol in quotes:
            continue
        local_px = latest_bundled_price(symbol)
        if local_px is None:
            continue
        quotes[symbol] = local_px
        quote_sources[symbol] = "local_monthly"

    vix_level = quotes.get(VIX_SYMBOL)
    vix_source = quote_sources.get(VIX_SYMBOL)

    # 盘中 MA 不再乘 VIX；危机加仓单独追加预算
    lookback_days = int(float(config.get("dca_boost_lookback_days", 365)))
    dd_pct, dd_cat = max_index_drawdown_pct(
        nasdaq_daily or {},
        sp500_daily or {},
        today,
        lookback_days,
    )
    crisis_base = sum(_plan_amount(item) for item in today_items)
    if crisis_base <= 0:
        crisis_base = month_stats["month_remaining_cny"]
    annual_cap_pct = max(float(config.get("dca_boost_annual_cap_pct", 50)), 0.0)
    annual_cap_amount = _annual_dca_budget_cny(config, today.year) * annual_cap_pct / 100
    annual_used_amount = estimate_annual_crisis_used_cny(db, config, today)
    crisis = resolve_crisis_boost(
        vix_level=float(vix_level) if vix_level is not None else None,
        drawdown_pct=dd_pct,
        base_amount=crisis_base,
        lookback_days=lookback_days,
        annual_cap_amount=annual_cap_amount,
        annual_used_amount=annual_used_amount,
    )
    boost_enabled = str(config.get("dca_boost_enabled", "1")) in ("1", "true", "True")
    cash_available = max(float(config.get("dca_boost_cash_available", 0) or 0), 0.0)
    monthly_cap = max(float(config.get("dca_boost_monthly_cap", 30000) or 0), 0.0)
    applied_extra = float(crisis["extra_amount"])
    if not boost_enabled or cash_available <= 0:
        applied_extra = 0.0
    else:
        applied_extra = min(applied_extra, cash_available)
        if monthly_cap > 0:
            applied_extra = min(applied_extra, monthly_cap)
    crisis["extra_amount"] = round(applied_extra, 2)
    crisis["triggered"] = applied_extra > 0
    if crisis["triggered"]:
        crisis["note"] = (
            f"危机加仓 ×{crisis['multiplier']:.2f}"
            f"（VIX {vix_level:.1f} ∧ 回撤 {dd_pct:.1f}%）"
            f" +{applied_extra:,.2f}"
        )
    elif boost_enabled and cash_available <= 0 and crisis["multiplier"] > 0:
        crisis["note"] = "危机条件已满足，但未配置可用现金"
    elif crisis["annual_cap_reached"] and crisis["multiplier"] > 0:
        crisis["note"] = "危机条件已满足，但年度加仓额度已用完"

    categories = []
    effective_factors: List[float] = []
    for category, symbol in INDEX_SYMBOL.items():
        live_price = quotes.get(symbol)
        if live_price is None:
            categories.append(
                {
                    "category": category,
                    "category_label": _category_label(category),
                    "index_symbol": symbol,
                    "live_price": None,
                    "deviation_pct": None,
                    "ma_factor": 1.0,
                    "vix_factor": 1.0,
                    "effective_factor": 1.0,
                    "error": index_errors.get(symbol),
                }
            )
            continue

        daily_series = nasdaq_daily if category == "nasdaq" else sp500_daily
        ma_info = live_category_ma_factor(
            config,
            category,
            live_price,
            as_of=today,
            daily_series=daily_series,
        )
        ma_factor = float(ma_info.get("factor", 1.0))
        effective_factors.append(ma_factor)
        categories.append(
            {
                "category": category,
                "category_label": _category_label(category),
                "index_symbol": symbol,
                "live_price": ma_info.get("live_price"),
                "deviation_pct": ma_info.get("deviation_pct"),
                "ma_factor": round(ma_factor, 4),
                "vix_factor": 1.0,
                "effective_factor": round(ma_factor, 4),
                "note": ma_info.get("note"),
                "error": None,
            }
        )

    avg_effective = sum(effective_factors) / len(effective_factors) if effective_factors else 1.0
    has_today_plan = bool(today_items)
    if crisis["triggered"] and market["open"]:
        action = {
            "code": "execute",
            "label": "危机加仓",
            "summary": crisis["note"] or "高 VIX 且大回撤，建议追加预算买入。",
        }
    else:
        action = _suggest_action(has_today_plan, avg_effective, market["open"])

    suggestions: List[dict] = []
    crisis_extra = float(crisis["extra_amount"])
    for item in today_items:
        cat_info = next((row for row in categories if row["category"] == item["category"]), None)
        ma_factor = float(cat_info["ma_factor"]) if cat_info else 1.0
        base_amount = _plan_amount(item)
        share = base_amount / crisis_base if crisis_base > 0 else 0.0
        extra = round(crisis_extra * share, 2)
        suggested = round(base_amount * ma_factor + extra, 2)
        suggestions.append(
            {
                "plan_date": item["plan_date"],
                "phase": item["phase"],
                "category": item["category"],
                "category_label": item.get("category_label") or _category_label(item["category"]),
                "target_label": item.get("target_label"),
                "planned_amount_cny": round(base_amount, 2),
                "suggested_amount_cny": suggested,
                "crisis_extra_cny": extra,
                "effective_factor": round(ma_factor, 4),
                "status": item.get("status"),
            }
        )

    if not suggestions and in_dca_phase and month_stats["month_remaining_cny"] > 0 and market["open"]:
        per_category = month_stats["month_remaining_cny"] / max(len([c for c in categories if not c.get("error")]), 1)
        for row in categories:
            if row.get("error"):
                continue
            ma_factor = float(row["ma_factor"])
            if ma_factor < EXECUTE_FACTOR_THRESHOLD and not crisis["triggered"]:
                continue
            extra = round(crisis_extra / max(len(categories), 1), 2) if crisis["triggered"] else 0.0
            suggestions.append(
                {
                    "plan_date": today,
                    "phase": "dca",
                    "category": row["category"],
                    "category_label": row["category_label"],
                    "target_label": f"{MAINLAND_ACCOUNT} · {row['category_label']}",
                    "planned_amount_cny": round(per_category, 2),
                    "suggested_amount_cny": round(per_category * ma_factor + extra, 2),
                    "crisis_extra_cny": extra,
                    "effective_factor": round(ma_factor, 4),
                    "status": "signal_only",
                }
            )

    suggested_total = round(sum(item["suggested_amount_cny"] for item in suggestions), 2)

    notify = _notification_hint(action["code"], avg_effective)
    if crisis["triggered"] and market["open"]:
        notify = {
            "level": "positive",
            "title": "定投信号：危机加仓",
            "body": crisis["note"] or f"VIX∧回撤触发，建议追加约 ¥{crisis_extra:,.0f}",
        }

    return {
        "as_of": today,
        "market": market,
        "account": MAINLAND_ACCOUNT,
        "in_dca_phase": in_dca_phase,
        "ma_enabled": ma_settings["enabled"],
        "vix": {
            "enabled": True,
            "level": round(float(vix_level), 2) if vix_level is not None else None,
            "factor": round(float(crisis["multiplier"]), 4),
            "source": vix_source,
            "error": index_errors.get(VIX_SYMBOL) if vix_source != "yahoo" else None,
        },
        "crisis": {
            "triggered": crisis["triggered"],
            "drawdown_pct": crisis["drawdown_pct"],
            "trigger_category": dd_cat,
            "multiplier": crisis["multiplier"],
            "extra_amount": crisis["extra_amount"],
            "annual_cap_amount": crisis["annual_cap_amount"],
            "annual_used_amount": crisis["annual_used_amount"],
            "annual_remaining_amount": crisis["annual_remaining_amount"],
            "annual_cap_reached": crisis["annual_cap_reached"],
            "note": crisis["note"],
        },
        "categories": categories,
        "month_planned_cny": month_stats["month_planned_cny"],
        "month_matched_cny": month_stats["month_matched_cny"],
        "month_remaining_cny": month_stats["month_remaining_cny"],
        "pool_cny": 0.0,
        "has_today_plan": has_today_plan,
        "today_suggestions": suggestions,
        "suggested_total_cny": suggested_total,
        "avg_effective_factor": round(avg_effective, 4),
        "action": action,
        "notification": notify,
        "disclaimer": "信号基于 ^IXIC/^GSPC 与 VIX；危机加仓需 VIX≥25 且回撤≥20%。实际买卖以大陆 ETF 行情与溢价为准。",
    }
