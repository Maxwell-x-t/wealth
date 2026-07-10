from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Dict, List, Optional, Tuple

from app.services.account_plan import (
    ACCOUNT_SPECS,
    building_amount_for_month,
    phase_for_month_index,
    resolve_all_account_settings,
)

from app.services.index_history import (
    NASDAQ_SYMBOL,
    SP500_SYMBOL,
    fetch_monthly_closes,
    iter_months,
    price_on_month,
)

ACCOUNT_CURRENCY = {"大陆": "CNY", "香港": "USD"}


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


def _cagr(principal: float, final_value: float, years: float) -> Optional[float]:
    if principal <= 0 or final_value <= 0 or years <= 0:
        return None
    return round(((final_value / principal) ** (1 / years) - 1) * 100, 2)


def build_historical_backtest(
    config: dict,
    start: date,
    end: date,
) -> dict:
    if start > end:
        raise ValueError("开始日期不能晚于结束日期")

    nasdaq_w, sp500_w = _category_weights(config)
    account_settings = resolve_all_account_settings(config)

    # 回测区间以用户选定起点作为计划开始，金额仍读配置
    for name in account_settings:
        account_settings[name] = {**account_settings[name], "plan_start": start}

    nasdaq_prices = fetch_monthly_closes(NASDAQ_SYMBOL, start, end)
    sp500_prices = fetch_monthly_closes(SP500_SYMBOL, start, end)

    state = BacktestState()
    points: List[dict] = []
    yearly: Dict[int, Dict[str, dict]] = {}

    months = iter_months(start, end)
    for year, month in months:
        nasdaq_px = price_on_month(nasdaq_prices, year, month)
        sp500_px = price_on_month(sp500_prices, year, month)
        if nasdaq_px is None or sp500_px is None:
            continue

        month_contrib: Dict[str, float] = {"CNY": 0.0, "USD": 0.0}

        for spec in ACCOUNT_SPECS:
            name = spec["name"]
            settings = account_settings[name]
            currency = ACCOUNT_CURRENCY[name]
            month_index = _month_index_from_start(start, year, month)
            if month_index < 0:
                continue

            phase = phase_for_month_index(settings, month_index)
            if phase == "building":
                amount = building_amount_for_month(settings, month_index)
            else:
                amount = float(settings["dca_monthly_amount"])
            if amount <= 0:
                continue

            month_contrib[currency] += amount

            ledger = state.ledger(currency)
            nasdaq_buy = amount * nasdaq_w
            sp500_buy = amount * sp500_w
            ledger.nasdaq_shares += nasdaq_buy / nasdaq_px
            ledger.sp500_shares += sp500_buy / sp500_px
            ledger.principal += amount

            year_bucket = yearly.setdefault(year, {})
            entry = year_bucket.setdefault(
                currency,
                {"building": 0.0, "dca": 0.0, "total": 0.0},
            )
            entry["total"] += amount
            entry[phase] += amount

        point_currencies: Dict[str, dict] = {}
        for currency in ("CNY", "USD"):
            ledger = state.ledger(currency)
            assets = ledger.value(nasdaq_px, sp500_px)
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
                "year": year,
                "month": month,
                "label": f"{year}-{month:02d}",
                "nasdaq_price": round(nasdaq_px, 2),
                "sp500_price": round(sp500_px, 2),
                "contribution_cny": round(month_contrib["CNY"], 2),
                "contribution_usd": round(month_contrib["USD"], 2),
                "currencies": point_currencies,
            }
        )

    years_span = max((end.year - start.year) + (end.month - start.month) / 12.0, 1 / 12)
    summaries = []
    for currency in ("CNY", "USD"):
        ledger = state.ledger(currency)
        if ledger.principal <= 0 and points:
            # 无投入则仍返回空摘要
            last_px_n = points[-1]["nasdaq_price"]
            last_px_s = points[-1]["sp500_price"]
            final_assets = ledger.value(last_px_n, last_px_s)
        elif points:
            last_px_n = points[-1]["nasdaq_price"]
            last_px_s = points[-1]["sp500_price"]
            final_assets = ledger.value(last_px_n, last_px_s)
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
        "nasdaq_symbol": NASDAQ_SYMBOL,
        "sp500_symbol": SP500_SYMBOL,
        "nasdaq_weight_pct": round(nasdaq_w * 100, 2),
        "sp500_weight_pct": round(sp500_w * 100, 2),
        "strategy": "building_dca",
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
