from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Dict, List, Literal, Optional, Tuple

from app.services.account_plan import (
    ACCOUNT_SPECS,
    building_amount_for_month,
    dca_amount_for_account_month,
    phase_for_month_index,
    resolve_all_account_settings,
    week_days_for_month,
)
from app.services.dca_ma_factor import (
    compute_ma_factor,
    daily_ma_deviation,
    monthly_ma_deviation,
    resolve_ma_settings,
    weekly_ma_deviation,
    window_months_from_days,
    window_weeks_from_days,
)
from app.services.dca_crisis_boost import (
    max_index_drawdown_pct,
    resolve_crisis_boost,
)
from app.services.index_history import (
    NASDAQ_SYMBOL,
    SP500_SYMBOL,
    VIX_SYMBOL,
    fetch_daily_closes,
    iter_months,
    price_on_date,
    to_weekly_closes,
    weekly_end_dates,
)

ACCOUNT_CURRENCY = {"大陆": "CNY", "香港": "USD"}
BacktestFrequency = Literal["weekly", "daily"]
PriceCadence = Literal["daily", "weekly", "monthly"]

VIX_TOTAL_MIN_FACTOR = 0.7
VIX_TOTAL_MAX_FACTOR = 1.4
CRISIS_LOOKBACK_DAYS = 365


def _median_gap_days(series: Dict[date, float]) -> float:
    dates = sorted(series)
    if len(dates) < 3:
        return 30.0
    sample = dates[: min(len(dates), 80)]
    gaps = sorted((sample[i] - sample[i - 1]).days for i in range(1, len(sample)))
    return float(gaps[len(gaps) // 2])


def detect_price_cadence(series: Dict[date, float]) -> PriceCadence:
    """根据点间距判断实际数据密度（日频拉取失败时可能是月线兜底）。"""
    gap = _median_gap_days(series)
    if gap <= 3:
        return "daily"
    if gap <= 10:
        return "weekly"
    return "monthly"


def _to_monthly_series(series: Dict[date, float]) -> Dict[Tuple[int, int], float]:
    by_month: Dict[Tuple[int, int], float] = {}
    for day, price in sorted(series.items()):
        by_month[(day.year, day.month)] = price
    return by_month


def _ma_factor_for(
    settings: dict,
    ma_cadence: PriceCadence,
    nasdaq_series: Dict[date, float],
    sp500_series: Dict[date, float],
    as_of: date,
) -> Tuple[float, float]:
    if not settings.get("enabled"):
        return 1.0, 1.0

    window_days = settings["window_days"]
    if ma_cadence == "monthly":
        window_months = window_months_from_days(window_days)
        nasdaq_m = _to_monthly_series(nasdaq_series)
        sp500_m = _to_monthly_series(sp500_series)
        dev_n = monthly_ma_deviation(nasdaq_m, as_of.year, as_of.month, window_months)
        dev_s = monthly_ma_deviation(sp500_m, as_of.year, as_of.month, window_months)
    elif ma_cadence == "weekly":
        window_weeks = window_weeks_from_days(window_days)
        dev_n = weekly_ma_deviation(nasdaq_series, as_of, window_weeks)
        dev_s = weekly_ma_deviation(sp500_series, as_of, window_weeks)
    else:
        dev_n = daily_ma_deviation(nasdaq_series, as_of, window_days)
        dev_s = daily_ma_deviation(sp500_series, as_of, window_days)

    factor_n = compute_ma_factor(dev_n, settings) if dev_n is not None else 1.0
    factor_s = compute_ma_factor(dev_s, settings) if dev_s is not None else 1.0
    return factor_n, factor_s


def _apply_reserve(
    reserve: Dict[Tuple[str, str], float],
    currency: str,
    category: str,
    base_buy: float,
    factor: float,
) -> float:
    """按 MA 因子调整买入额，少投入池、多投从池里取，实现预算守恒。"""
    key = (currency, category)
    if factor < 1.0:
        withhold = base_buy * (1.0 - factor)
        reserve[key] = reserve.get(key, 0.0) + withhold
        return base_buy - withhold
    if factor > 1.0:
        want_extra = base_buy * (factor - 1.0)
        pool = reserve.get(key, 0.0)
        draw = min(want_extra, pool)
        reserve[key] = pool - draw
        return base_buy + draw
    return base_buy


def _reserve_cash(reserve: Dict[Tuple[str, str], float], currency: str) -> float:
    return sum(v for (cur, _), v in reserve.items() if cur == currency)


@dataclass
class BuyEvent:
    plan_day: date
    currency: str
    amount: float
    phase: str


@dataclass
class Ledger:
    currency: str
    nasdaq_shares: float = 0.0
    sp500_shares: float = 0.0
    principal: float = 0.0

    def value(self, nasdaq_price: float, sp500_price: float) -> float:
        return self.nasdaq_shares * nasdaq_price + self.sp500_shares * sp500_price


@dataclass
class BacktestState:
    ledgers: Dict[str, Ledger] = field(default_factory=dict)
    peak_by_currency: Dict[str, float] = field(default_factory=dict)
    max_drawdown_by_currency: Dict[str, float] = field(default_factory=dict)

    def ledger(self, currency: str) -> Ledger:
        if currency not in self.ledgers:
            self.ledgers[currency] = Ledger(currency=currency)
            self.peak_by_currency[currency] = 0.0
            self.max_drawdown_by_currency[currency] = 0.0
        return self.ledgers[currency]

    def update_drawdown(self, currency: str, assets: float) -> None:
        peak = self.peak_by_currency.get(currency, 0.0)
        if assets > peak:
            peak = assets
            self.peak_by_currency[currency] = peak
        if peak > 0:
            dd = (peak - assets) / peak * 100.0
            self.max_drawdown_by_currency[currency] = max(
                self.max_drawdown_by_currency.get(currency, 0.0),
                dd,
            )


def _category_weights(config: dict) -> Tuple[float, float]:
    nasdaq = float(config.get("nasdaq", 70))
    sp500 = float(config.get("sp500", 30))
    total = nasdaq + sp500
    if total <= 0:
        return 0.7, 0.3
    return nasdaq / total, sp500 / total


def _month_index_from_start(plan_start: date, year: int, month: int) -> int:
    return (year - plan_start.year) * 12 + (month - plan_start.month)


def _ma_lead_start(start: date, cadence: PriceCadence, window_days: int) -> date:
    if cadence == "monthly":
        months = window_months_from_days(window_days)
        return date(start.year - (months // 12 + 2), 1, 1)
    if cadence == "weekly":
        weeks = window_weeks_from_days(window_days)
        return start - timedelta(days=weeks * 7 + 60)
    return start - timedelta(days=int(window_days * 1.6) + 60)


def _build_buy_events(
    config: dict,
    account_settings: Dict[str, dict],
    plan_start: date,
    end: date,
) -> List[BuyEvent]:
    events: List[BuyEvent] = []
    for year, month in iter_months(plan_start, end):
        for spec in ACCOUNT_SPECS:
            name = spec["name"]
            settings = account_settings[name]
            currency = ACCOUNT_CURRENCY[name]
            month_index = _month_index_from_start(plan_start, year, month)
            if month_index < 0:
                continue

            phase = phase_for_month_index(settings, month_index)
            if phase == "building":
                monthly_amount = building_amount_for_month(settings, month_index)
            else:
                monthly_amount = dca_amount_for_account_month(
                    config,
                    settings["key"],
                    settings["dca_amount_schedule"],
                    year,
                    month,
                )
            if monthly_amount <= 0:
                continue

            week_days = week_days_for_month(
                year, month, settings["market"], settings["weeks_per_month"]
            )
            if not week_days:
                continue

            active_week_days = week_days
            if month_index == 0:
                active_week_days = [day for day in week_days if day >= plan_start]
            if not active_week_days:
                continue

            if phase == "building":
                weekly_pool = monthly_amount / len(active_week_days)
                for plan_day in active_week_days:
                    if plan_day < plan_start or plan_day > end:
                        continue
                    events.append(
                        BuyEvent(
                            plan_day=plan_day,
                            currency=currency,
                            amount=weekly_pool,
                            phase=phase,
                        )
                    )
            else:
                weekly_divisor = max(len(active_week_days), settings["weeks_per_month"])
                weekly_amount = monthly_amount / weekly_divisor
                for plan_day in active_week_days:
                    if plan_day < plan_start or plan_day > end:
                        continue
                    events.append(
                        BuyEvent(
                            plan_day=plan_day,
                            currency=currency,
                            amount=weekly_amount,
                            phase=phase,
                        )
                    )
    events.sort(key=lambda item: item.plan_day)
    return events


def _timeline_dates(
    frequency: BacktestFrequency,
    nasdaq_daily: Dict[date, float],
    sp500_daily: Dict[date, float],
    start: date,
    end: date,
) -> List[date]:
    candidates = sorted(
        {d for d in nasdaq_daily if start <= d <= end}
        | {d for d in sp500_daily if start <= d <= end}
    )
    trading_days = [
        d
        for d in candidates
        if price_on_date(nasdaq_daily, d) is not None and price_on_date(sp500_daily, d) is not None
    ]
    if frequency == "daily":
        return trading_days
    return weekly_end_dates(
        {d: price_on_date(nasdaq_daily, d) for d in trading_days},
        start,
        end,
    )


def _cagr(principal: float, final_value: float, years: float) -> Optional[float]:
    if principal <= 0 or final_value <= 0 or years <= 0:
        return None
    return round(((final_value / principal) ** (1 / years) - 1) * 100, 2)


def build_historical_backtest(
    config: dict,
    start: date,
    end: date,
    frequency: BacktestFrequency = "weekly",
    force_ma: Optional[bool] = None,
    force_center_pct: Optional[float] = None,
    force_vix_enabled: Optional[bool] = None,
) -> dict:
    if start > end:
        raise ValueError("开始日期不能晚于结束日期")
    if frequency not in ("weekly", "daily"):
        raise ValueError("frequency 仅支持 weekly 或 daily")

    nasdaq_w, sp500_w = _category_weights(config)
    account_settings = resolve_all_account_settings(config)
    for name in account_settings:
        account_settings[name] = {**account_settings[name], "plan_start": start}

    ma_settings = resolve_ma_settings(config)
    if force_ma is not None:
        ma_settings["enabled"] = force_ma
    if force_center_pct is not None:
        ma_settings["center_pct"] = float(force_center_pct)

    vix_enabled = bool(force_vix_enabled) if force_vix_enabled is not None else False

    # 先按可能的月线兜底拉够前置历史，再根据实际密度决定 MA 算法
    monthly_lead = _ma_lead_start(start, "monthly", ma_settings["window_days"])
    requested_lead = _ma_lead_start(
        start,
        "weekly" if frequency == "weekly" else "daily",
        ma_settings["window_days"],
    )
    fetch_start = min(start, monthly_lead, requested_lead)
    if vix_enabled:
        fetch_start = min(fetch_start, start - timedelta(days=CRISIS_LOOKBACK_DAYS + 30))
    nasdaq_daily = fetch_daily_closes(NASDAQ_SYMBOL, fetch_start, end)
    sp500_daily = fetch_daily_closes(SP500_SYMBOL, fetch_start, end)

    price_cadence = detect_price_cadence(nasdaq_daily)
    # 两边有一边是月线兜底，就整组按月线算 MA，避免窗口口径不一致
    if detect_price_cadence(sp500_daily) == "monthly":
        price_cadence = "monthly"

    if price_cadence == "monthly":
        ma_cadence: PriceCadence = "monthly"
        nasdaq_ma_series = nasdaq_daily
        sp500_ma_series = sp500_daily
    elif frequency == "weekly":
        ma_cadence = "weekly"
        nasdaq_ma_series = to_weekly_closes(nasdaq_daily)
        sp500_ma_series = to_weekly_closes(sp500_daily)
    else:
        ma_cadence = "daily"
        nasdaq_ma_series = nasdaq_daily
        sp500_ma_series = sp500_daily

    vix_daily: Dict[date, float] = {}
    crisis_extra_total = {"CNY": 0.0, "USD": 0.0}
    if vix_enabled:
        try:
            vix_daily = fetch_daily_closes(VIX_SYMBOL, fetch_start, end, timeout=8.0)
        except Exception:
            vix_enabled = False
            vix_daily = {}

    buy_events = _build_buy_events(config, account_settings, start, end)
    annual_cap_pct = max(float(config.get("dca_boost_annual_cap_pct", 50)), 0.0)
    annual_dca_budget: Dict[Tuple[int, str], float] = {}
    annual_crisis_used: Dict[Tuple[int, str], float] = {}
    for event in buy_events:
        if event.phase != "dca":
            continue
        key = (event.plan_day.year, event.currency)
        annual_dca_budget[key] = annual_dca_budget.get(key, 0.0) + event.amount
    timeline = _timeline_dates(frequency, nasdaq_daily, sp500_daily, start, end)
    if not timeline:
        raise RuntimeError("回测区间内无可用交易日数据")

    ma_reserve: Dict[Tuple[str, str], float] = {}
    state = BacktestState()
    points: List[dict] = []
    yearly: Dict[int, Dict[str, dict]] = {}
    event_index = 0

    for point_date in timeline:
        day_contrib: Dict[str, float] = {"CNY": 0.0, "USD": 0.0}

        while event_index < len(buy_events) and buy_events[event_index].plan_day <= point_date:
            event = buy_events[event_index]
            event_index += 1

            nasdaq_px = price_on_date(nasdaq_daily, event.plan_day)
            sp500_px = price_on_date(sp500_daily, event.plan_day)
            if nasdaq_px is None or sp500_px is None:
                continue

            factor_n, factor_s = _ma_factor_for(
                ma_settings,
                ma_cadence,
                nasdaq_ma_series,
                sp500_ma_series,
                event.plan_day,
            )

            # MA 因子仍走预算守恒；危机加仓为额外本金，不进池
            ledger = state.ledger(event.currency)
            nasdaq_buy = _apply_reserve(
                ma_reserve, event.currency, "nasdaq", event.amount * nasdaq_w, factor_n
            )
            sp500_buy = _apply_reserve(
                ma_reserve, event.currency, "sp500", event.amount * sp500_w, factor_s
            )

            crisis_extra = 0.0
            if vix_enabled and vix_daily and event.phase == "dca":
                annual_key = (event.plan_day.year, event.currency)
                annual_cap = annual_dca_budget.get(annual_key, 0.0) * annual_cap_pct / 100
                vix_level = price_on_date(vix_daily, event.plan_day)
                dd_pct, _ = max_index_drawdown_pct(
                    nasdaq_daily,
                    sp500_daily,
                    event.plan_day,
                    CRISIS_LOOKBACK_DAYS,
                )
                crisis = resolve_crisis_boost(
                    vix_level=float(vix_level) if vix_level is not None else None,
                    drawdown_pct=dd_pct,
                    base_amount=event.amount,
                    lookback_days=CRISIS_LOOKBACK_DAYS,
                    annual_cap_amount=annual_cap,
                    annual_used_amount=annual_crisis_used.get(annual_key, 0.0),
                )
                crisis_extra = float(crisis["extra_amount"])
                if crisis_extra > 0:
                    nasdaq_buy += crisis_extra * nasdaq_w
                    sp500_buy += crisis_extra * sp500_w
                    crisis_extra_total[event.currency] += crisis_extra
                    annual_crisis_used[annual_key] = (
                        annual_crisis_used.get(annual_key, 0.0) + crisis_extra
                    )

            invested = event.amount + crisis_extra
            ledger.nasdaq_shares += nasdaq_buy / nasdaq_px
            ledger.sp500_shares += sp500_buy / sp500_px
            ledger.principal += invested
            day_contrib[event.currency] += invested

            year_bucket = yearly.setdefault(event.plan_day.year, {})
            entry = year_bucket.setdefault(
                event.currency,
                {"building": 0.0, "dca": 0.0, "total": 0.0},
            )
            entry["total"] += invested
            entry[event.phase] += invested

        nasdaq_px = price_on_date(nasdaq_daily, point_date)
        sp500_px = price_on_date(sp500_daily, point_date)
        if nasdaq_px is None or sp500_px is None:
            continue

        point_currencies: Dict[str, dict] = {}
        for currency in ("CNY", "USD"):
            ledger = state.ledger(currency)
            assets = ledger.value(nasdaq_px, sp500_px) + _reserve_cash(ma_reserve, currency)
            state.update_drawdown(currency, assets)
            point_currencies[currency] = {
                "principal": round(ledger.principal, 2),
                "assets": round(assets, 2),
                "profit": round(assets - ledger.principal, 2),
                "return_rate": round((assets - ledger.principal) / ledger.principal * 100, 2)
                if ledger.principal > 0
                else None,
            }

        points.append(
            {
                "year": point_date.year,
                "month": point_date.month,
                "label": point_date.isoformat(),
                "nasdaq_price": round(nasdaq_px, 2),
                "sp500_price": round(sp500_px, 2),
                "contribution_cny": round(day_contrib["CNY"], 2),
                "contribution_usd": round(day_contrib["USD"], 2),
                "currencies": point_currencies,
            }
        )

    years_span = max((end - start).days / 365.25, 1 / 365)
    summaries = []
    for currency in ("CNY", "USD"):
        ledger = state.ledger(currency)
        if points:
            last_px_n = points[-1]["nasdaq_price"]
            last_px_s = points[-1]["sp500_price"]
            final_assets = ledger.value(last_px_n, last_px_s) + _reserve_cash(ma_reserve, currency)
        else:
            final_assets = 0.0

        profit = final_assets - ledger.principal
        summaries.append(
            {
                "currency": currency,
                "account_label": "大陆" if currency == "CNY" else "香港",
                "total_contributed": round(ledger.principal, 2),
                "final_assets": round(final_assets, 2),
                "profit": round(profit, 2),
                "return_rate": round(profit / ledger.principal * 100, 2) if ledger.principal > 0 else None,
                "cagr": _cagr(ledger.principal, final_assets, years_span),
                "max_drawdown_pct": round(state.max_drawdown_by_currency.get(currency, 0.0), 2),
            }
        )

    return {
        "start_date": start,
        "end_date": end,
        "frequency": frequency,
        "price_cadence": price_cadence,
        "ma_cadence": ma_cadence,
        "nasdaq_symbol": NASDAQ_SYMBOL,
        "sp500_symbol": SP500_SYMBOL,
        "nasdaq_weight_pct": round(nasdaq_w * 100, 2),
        "sp500_weight_pct": round(sp500_w * 100, 2),
        "strategy": "building_dca",
        "dca_ma": {
            "enabled": ma_settings["enabled"],
            "window_days": ma_settings["window_days"],
            "min_factor": ma_settings["min_factor"],
            "max_factor": ma_settings["max_factor"],
            "band_pct": ma_settings["band_pct"],
            "center_pct": ma_settings["center_pct"],
        },
        "dca_vix": {
            "enabled": vix_enabled,
            "mode": "crisis_and",
            "vix_gate": 25.0,
            "drawdown_gate": 20.0,
            "lookback_days": CRISIS_LOOKBACK_DAYS,
            "extra_cny": round(crisis_extra_total["CNY"], 2),
            "extra_usd": round(crisis_extra_total["USD"], 2),
            "annual_cap_pct": annual_cap_pct,
            "single_max_multiplier": 1.0,
            "thresholds": [25.0, 35.0],
            "multipliers": [0.25, 0.4, 0.5, 0.75, 1.0],
            "total_min_factor": VIX_TOTAL_MIN_FACTOR,
            "total_max_factor": VIX_TOTAL_MAX_FACTOR,
        },
        "currency_summaries": summaries,
        "points": points,
        "yearly_contributions": [
            {
                "year": year,
                "cny_total": round(buckets.get("CNY", {}).get("total", 0.0), 2),
                "cny_building": round(buckets.get("CNY", {}).get("building", 0.0), 2),
                "cny_dca": round(buckets.get("CNY", {}).get("dca", 0.0), 2),
                "usd_total": round(buckets.get("USD", {}).get("total", 0.0), 2),
                "usd_building": round(buckets.get("USD", {}).get("building", 0.0), 2),
                "usd_dca": round(buckets.get("USD", {}).get("dca", 0.0), 2),
            }
            for year, buckets in sorted(yearly.items())
        ],
        "plan_settings": {
            name: {
                "building_first_month_amount": settings["building_first_month_amount"],
                "building_monthly_amount": settings["building_monthly_amount"],
                "building_months": settings["building_months"],
                "building_target_amount": settings.get("building_target_amount"),
                "dca_monthly_amount": settings["dca_monthly_amount"],
                "currency": ACCOUNT_CURRENCY[name],
            }
            for name, settings in account_settings.items()
        },
    }
