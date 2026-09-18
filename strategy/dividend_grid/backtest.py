"""Daily, cash-funded simulation using dated public dividend and EPS records."""

from calendar import monthrange
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
import hashlib
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from .datasource import QualityMetrics
from .grid import GridConfig, GroupThresholds, evaluate_portfolio
from .models import Stock
from .portfolio import PortfolioLimits
from .quality import QualityConfig, assess
from .dividends import fiscal_dividend, parse_dividend_records
from .backtest_coverage import coverage_on
from .coverage import CoverageMetrics, apply_coverage_review
from .rsi6 import (AllInState, DEFAULT_RSI_PARAMS, Rsi6State, RsiParams, decide as decide_rsi,
                   decide_all_in, rsi_wilder, week_id)


DEFAULT_ETF = "sh512890"
# 兼容旧引用：历史数据集的默认 ETF 载体。
ETF = DEFAULT_ETF

# 分位窗口：滚动 3 年（756 个交易日）；不足 min_obs 个观测时该股回退绝对阈值。
PERCENTILE_WINDOW = 756
PERCENTILE_MIN_OBS = 480


def _percentile_rank(series: pd.Series, window: int = PERCENTILE_WINDOW,
                     min_obs: int = PERCENTILE_MIN_OBS) -> pd.Series:
    """当前值在自身过去 window 个观测中的百分位（0~100）。观测不足返回 NaN。"""
    return series.rolling(window, min_periods=min_obs).apply(
        lambda values: float((values <= values[-1]).mean() * 100.0), raw=True)


def month_after(day: date, months: int) -> date:
    year, month = divmod(day.year * 12 + day.month - 1 + months, 12)
    return date(year, month + 1, min(day.day, monthrange(year, month + 1)[1]))


def dividend_tax_rate(acquired: date, sold: date) -> float:
    if sold <= month_after(acquired, 1):
        return 0.20
    if sold <= month_after(acquired, 12):
        return 0.10
    return 0.0


@dataclass(frozen=True)
class Costs:
    commission: float = 0.0003
    minimum_commission: float = 5.0
    slippage: float = 0.0005
    dividend_tax: bool = True

    def fee(self, amount: float, sell: bool, day: date, etf: bool) -> float:
        if amount <= 0:
            return 0.0
        commission = max(self.minimum_commission, self.commission * amount)
        transfer = 0 if etf else amount * (0.00002 if day < date(2022, 4, 29) else 0.00001)
        stamp = 0 if etf or not sell else amount * (0.001 if day < date(2023, 8, 28) else 0.0005)
        return commission + transfer + stamp


@dataclass
class Lot:
    quantity: float
    acquired: date
    taxable_dividends_per_share: float = 0.0


@dataclass
class Broker:
    cash: float
    costs: Costs = field(default_factory=Costs)
    etf_code: str = DEFAULT_ETF
    lots: dict[str, list[Lot]] = field(default_factory=lambda: defaultdict(list))
    receivables: list[tuple[str, float]] = field(default_factory=list)
    trades: list[dict] = field(default_factory=list)
    income: dict[str, float] = field(default_factory=lambda: defaultdict(float))
    income_events: list[dict] = field(default_factory=list)
    fees: dict[str, float] = field(default_factory=lambda: defaultdict(float))
    taxes: dict[str, float] = field(default_factory=lambda: defaultdict(float))
    flows: dict[str, float] = field(default_factory=lambda: defaultdict(float))

    def quantity(self, code: str) -> float:
        return sum(lot.quantity for lot in self.lots[code])

    def equity(self, prices: dict[str, float]) -> float:
        return self.cash + sum(value for _, value in self.receivables) + sum(
            self.quantity(code) * prices.get(code, 0.0) for code in self.lots)

    def settle(self, day: str) -> None:
        pending = []
        for due, amount in self.receivables:
            if due <= day:
                self.cash += amount
            else:
                pending.append((due, amount))
        self.receivables = pending

    def corporate_action(self, code: str, action: dict, pay_date: str) -> None:
        quantity = self.quantity(code)
        gross = quantity * action["cash_per_share"]
        if gross:
            self.receivables.append((pay_date, gross))
            self.income[code] += gross
            self.income_events.append({"code": code, "ex_date": action["ex_date"],
                                       "pay_date": pay_date, "gross": gross})
        for lot in self.lots[code]:
            if code != self.etf_code:
                lot.taxable_dividends_per_share += action["cash_per_share"] + action.get("bonus_taxable", 0.0)
            lot.quantity *= action["split"]
            lot.taxable_dividends_per_share /= action["split"]

    def trade(self, code: str, day: str, side: str, quantity: float, opening: float,
              reserve: float, signal_date: str, reason: str) -> float:
        if side == "BUY":
            quantity = math.floor(quantity / 100 + 1e-9) * 100
            price = opening * (1 + self.costs.slippage)
            while quantity > 0 and quantity * price + self.costs.fee(
                quantity * price, False, date.fromisoformat(day), code == self.etf_code
            ) > self.cash - reserve + 1e-8:
                quantity -= 100
        else:
            quantity = min(quantity, self.quantity(code))
            price = opening * (1 - self.costs.slippage)
        if quantity <= 1e-8:
            return 0.0
        value = quantity * price
        fee = self.costs.fee(value, side == "SELL", date.fromisoformat(day), code == self.etf_code)
        tax = 0.0
        if side == "BUY":
            self.cash -= value + fee
            self.lots[code].append(Lot(quantity, date.fromisoformat(day)))
            self.flows[code] -= value
        else:
            remaining = quantity
            for lot in self.lots[code]:
                taken = min(lot.quantity, remaining)
                if self.costs.dividend_tax and code != self.etf_code:
                    tax += taken * lot.taxable_dividends_per_share * dividend_tax_rate(
                        lot.acquired, date.fromisoformat(day))
                lot.quantity -= taken
                remaining -= taken
                if remaining <= 1e-8:
                    break
            self.lots[code] = [lot for lot in self.lots[code] if lot.quantity > 1e-8]
            self.cash += value - fee - tax
            self.flows[code] += value
        if self.cash < -1e-6:
            raise AssertionError("Backtest borrowed cash")
        self.fees[code] += fee
        self.taxes[code] += tax
        self.trades.append({"date": day, "signal_date": signal_date, "code": code,
                            "side": side, "quantity": quantity, "price": price,
                            "notional": value, "fees": fee, "dividend_tax": tax,
                            "slippage_cost": quantity * abs(price - opening), "reason": reason})
        return quantity


class History:
    def __init__(self, assets: dict[str, dict], caliber: str = "fiscal", rsi_period: int = 6,
                 etf_code: str | None = None):
        if caliber not in ("fiscal", "naive"):
            raise ValueError("caliber 必须是 fiscal 或 naive")
        if rsi_period <= 0:
            raise ValueError("rsi_period 必须是正整数")
        detected = next((code for code, asset in assets.items()
                         if asset.get("group") == "ETF"), DEFAULT_ETF)
        self.etf_code = etf_code or detected
        self.caliber = caliber
        self.rsi_period = rsi_period
        self.assets = assets
        self.calendar = sorted({row["date"] for asset in assets.values() for row in asset["prices"]})
        self.frames = {}
        self.actions = {}
        self.audit = []
        self.coverage_cache = {}
        for code, asset in assets.items():
            frame = pd.DataFrame(asset["prices"]).set_index("date").reindex(self.calendar)
            frame["traded"] = frame["open"].notna() & (frame["volume"] > 0)
            frame["close"] = frame["close"].ffill()
            self.frames[code] = frame
            events = defaultdict(list)
            seen = set()
            for action in asset["actions"]:
                key = (action["ex_date"], action["cash_per_share"], action["split"])
                if key in seen:
                    raise ValueError(f"Duplicate corporate action: {code} {key}")
                seen.add(key)
                events[action["ex_date"]].append(action)
            self.actions[code] = events
            self._prepare_signals(code)

    @classmethod
    def load(cls, directory: Path, caliber: str = "fiscal", rsi_period: int = 6) -> "History":
        manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        assets = {code: json.loads((directory / f"{code}.json").read_text(encoding="utf-8"))
                  for code in manifest["universe"]}
        for code, asset in assets.items():
            raw = directory / "raw" / f"{code}.json"
            if asset.get("group") != "ETF" and raw.exists():
                payload = json.loads(raw.read_text(encoding="utf-8")).get("dividends")
                if isinstance(payload, dict):
                    asset["dividend_records"] = parse_dividend_records(payload)
        stated = manifest.get("etf_code")
        if not stated:
            stated = next((code for code, spec in manifest["universe"].items()
                           if spec.get("group") == "ETF"), None)
        return cls(assets, caliber=caliber, rsi_period=rsi_period, etf_code=stated)

    def _prepare_signals(self, code: str) -> None:
        asset, frame = self.assets[code], self.frames[code]
        dividend_records = asset.get("dividend_records", asset["actions"])
        dividend_available, dividend_notes = [], []
        reports = sorted(asset["financials"], key=lambda row: row["published"])
        announced = {}
        report_index = 0
        dividends = []  # 每条：[财年, 除息日, 每股分红(已按后续拆股调整), 报告期]
        known_splits = []
        yields, norms, earnings, fiscal_earnings, adjusted, rsis = [], [], [], [], [], []
        factor, previous = 1.0, None
        weekly_closes, last_week = [], None
        for day in self.calendar:
            current = frame.at[day, "close"]
            if not frame.at[day, "traded"] and previous is not None:
                current = previous
            while report_index < len(reports) and reports[report_index]["published"] <= day:
                row = reports[report_index]
                announced[row["period"]] = row
                report_index += 1
            for action in self.actions[code].get(day, []):
                if action["announced"] > day:
                    raise ValueError(f"Action unavailable at ex-date: {code} {day}")
                report = action.get("report_date")
                if report is None and code != self.etf_code and action["cash_per_share"]:
                    raise ValueError(f"Cash dividend missing report period: {code} {day}")
                fiscal = int(report[:4]) if report else None
                dividends.append([fiscal, day, action["cash_per_share"], report])
                ratio = action["split"]
                if not frame.at[day, "traded"] and pd.notna(current):
                    current = (current - action["cash_per_share"]) / ratio
                if ratio != 1:
                    known_splits.append((day, ratio))
                    for entry in dividends:
                        entry[2] /= ratio
                if previous is not None:
                    denominator = previous - action["cash_per_share"]
                    if denominator <= 0:
                        raise ValueError(f"Dividend exceeds reference price: {code} {day}")
                    factor *= ratio * previous / denominator
            frame.at[day, "close"] = current
            snapshot = fiscal_dividend(dividend_records, day)
            fiscal_total, fiscal_median = snapshot.dps or 0.0, snapshot.median_dps or 0.0
            dividend_available.append(snapshot.dps is not None or self.caliber == "naive")
            dividend_notes.append(snapshot.note)
            cutoff = (date.fromisoformat(day) - timedelta(days=365)).isoformat()
            naive_total = sum(entry[2] for entry in dividends if entry[1] > cutoff)
            total = fiscal_total if self.caliber == "fiscal" else naive_total
            if pd.notna(current):
                yields.append(total / current * 100)
                norms.append(fiscal_median / current * 100)
            else:
                yields.append(0.0)
                norms.append(0.0)

            def eps_for(period: str):
                report = announced.get(period)
                if report is None:
                    return None
                split_factor = math.prod(ratio for ex, ratio in known_splits if ex > report["published"])
                return report["eps_ytd"] / split_factor

            eps = None
            if announced:
                latest = max(announced)
                if latest.endswith("12-31"):
                    eps = eps_for(latest)
                else:
                    year = int(latest[:4]) - 1
                    values = (eps_for(latest), eps_for(f"{year}-12-31"), eps_for(f"{year}{latest[4:]}"))
                    if all(value is not None for value in values):
                        eps = values[0] + values[1] - values[2]
            earnings.append(eps if eps is not None else float("nan"))
            fy_eps = None
            if snapshot.fiscal_year is not None:
                fy_eps = eps_for(f"{snapshot.fiscal_year}-12-31")
            fiscal_earnings.append(fy_eps if fy_eps is not None else float("nan"))
            indicator_price = current * factor if pd.notna(current) else float("nan")
            adjusted.append(indicator_price)
            rsi = float("nan")
            if code == self.etf_code and frame.at[day, "traded"]:
                week = week_id(date.fromisoformat(day))
                if week != last_week:
                    weekly_closes.append(indicator_price)
                    last_week = week
                else:
                    weekly_closes[-1] = indicator_price
                if len(weekly_closes) >= self.rsi_period + 1:
                    rsi = rsi_wilder(weekly_closes, self.rsi_period)
            rsis.append(rsi)
            if pd.notna(current):
                previous = current
        frame["yield"] = yields
        frame["dividend_available"] = dividend_available
        frame["dividend_note"] = dividend_notes
        frame["yield_norm"] = norms
        frame["eps"] = earnings
        frame["fiscal_eps"] = fiscal_earnings
        frame["adjusted"] = adjusted
        frame["rsi"] = rsis
        frame["rsi"] = frame["rsi"].ffill()
        frame["yield_pct"] = _percentile_rank(frame["yield"])
        frame["yield_norm_pct"] = _percentile_rank(frame["yield_norm"])
        changes = frame["adjusted"].pct_change(fill_method=None)
        unusual = changes[(changes.abs() > 0.25) & (changes.index >= "2019-01-01")]
        self.audit.append({"code": code, "name": asset["name"],
                           "first_price": asset["prices"][0]["date"],
                           "last_price": asset["prices"][-1]["date"],
                           "price_rows": len(asset["prices"]),
                           "eps_report_count": len(reports),
                           "corporate_actions_since_2019": [a for a in asset["actions"] if a["ex_date"] >= "2019-01-01"],
                           "large_adjusted_moves": {day: float(value) for day, value in unusual.items()}})

    def marks(self, day: str, opening: bool = False) -> dict[str, float]:
        marks = {}
        for code, frame in self.frames.items():
            value = frame.at[day, "open"] if opening and frame.at[day, "traded"] else frame.at[day, "close"]
            if pd.notna(value):
                marks[code] = float(value)
        return marks


@dataclass(frozen=True)
class Scenario:
    key: str
    name: str
    stocks: str = "grid"
    etf: str = "rsi"
    quality: bool = True
    etf_only: bool = False
    percentile: bool = False  # 方案A：自身分位阈值
    normalize: bool = False   # 方案B：3 年分红中位数归一化
    caliber: str = "fiscal"   # DPS 口径：fiscal=财年（修正），naive=过去365天（旧）
    coverage: bool = False


SCENARIOS = (
    Scenario("current", "新版策略：网格+RSI+现金覆盖检查", coverage=True),
    Scenario("payout_only", "对照：网格+RSI，仅派息率筛查"),
    Scenario("baseline_naive", "当前策略（旧365天口径，对照）", caliber="naive"),
    Scenario("dynamic_a", "方案A：自身分位阈值", percentile=True),
    Scenario("dynamic_b", "方案B：3年分红中位数归一化", normalize=True),
    Scenario("dynamic_ab", "方案A+B：分位阈值+归一化", percentile=True, normalize=True),
    Scenario("etf_rsi_only", "仅RSI择时：全仓红利低波ETF", stocks="cash", etf="rsi", etf_only=True),
    Scenario("rsi_all_in", "仅RSI梭哈版：31以下满仓，70/74各减10%", stocks="cash", etf="allin", etf_only=True),
    Scenario("grid_hold_etf", "新版网格+ETF持有", etf="hold", coverage=True),
    Scenario("grid_only", "仅股息率网格", etf="cash"),
    Scenario("hold", "同清单买入持有", stocks="hold", etf="hold"),
    Scenario("etf_hold", "可投资资金买入持有红利低波ETF", stocks="cash", etf="hold", etf_only=True),
    Scenario("no_quality", "当前策略（关闭质量筛查）", quality=False),
)


class Simulation:
    def __init__(self, history: History, scenario: Scenario, groups: dict[str, GroupThresholds],
                 limits: PortfolioLimits, start: str, end: str, capital: float = 1_000_000,
                 costs: Costs | None = None, dividend_delay: int = 5,
                 rsi_params: RsiParams | None = None, quality_config: QualityConfig | None = None):
        self.history, self.scenario = history, scenario
        self.groups, self.limits = groups, limits
        self.start, self.end, self.capital = start, end, capital
        self.etf_code = history.etf_code
        self.broker = Broker(capital, costs or Costs(), etf_code=history.etf_code)
        self.dividend_delay = dividend_delay
        self.rsi_params = rsi_params or DEFAULT_RSI_PARAMS
        self.state, self.etf_layers = Rsi6State(), 0
        self.allin_state = AllInState()
        self.pending = []
        self.baseline_bought = set()
        self.daily = []
        self.blocked = defaultdict(int)
        self.quality_observations = defaultdict(int)
        self.stock_codes = [code for code in history.assets if code != self.etf_code]
        self.quality_config = quality_config or QualityConfig()
        if scenario.coverage and any("coverage" not in history.assets[c] for c in self.stock_codes):
            raise ValueError("Coverage-enabled replay requires dated coverage history for every stock")
        if self.quality_config.exit_reasons:
            raise ValueError("Undated exit_reasons cannot be applied retrospectively")
        self.coverage_observations = defaultdict(int)
        self.coverage_blocks = defaultdict(int)

    @property
    def etf_layer_pct(self) -> float:
        """单层 ETF 占总资产比例。仅 ETF 策略时用满可用额度（100% - 现金保留）。"""
        if self.scenario.etf_only:
            return (100.0 - self.limits.min_cash_pct) / 10.0
        return self.limits.layer_pct

    def etf_capacity(self, stock_position_pct: float) -> int:
        available = max(100.0 - self.limits.min_cash_pct - stock_position_pct, 0.0)
        return min(10, math.floor((available + 1e-9) / self.etf_layer_pct))

    def plan(self, day: str) -> list[dict]:
        marks = self.history.marks(day)
        nav = self.broker.equity(marks)
        orders = []
        if self.scenario.stocks == "grid":
            stocks, holdings = [], {}
            for code in self.stock_codes:
                if code not in marks:
                    continue
                frame, asset = self.history.frames[code], self.history.assets[code]
                ttm_eps, fy_eps = float(frame.at[day, "eps"]), float(frame.at[day, "fiscal_eps"])
                raw_y = float(frame.at[day, "yield"])
                dps = raw_y * marks[code] / 100.0
                payout = dps / fy_eps * 100 if math.isfinite(fy_eps) and fy_eps > 0 else None
                eps = fy_eps if math.isfinite(fy_eps) else ttm_eps
                metrics = None if math.isnan(eps) else QualityMetrics(
                    eps=eps, dividend_per_share=dps, payout_ratio=payout)
                quality, note = assess(metrics, self.quality_config)
                self.quality_observations[quality] += 1
                coverage = CoverageMetrics()
                if self.scenario.coverage:
                    key = (code, day)
                    if key not in self.history.coverage_cache:
                        self.history.coverage_cache[key] = coverage_on(code, asset["coverage"], day)
                    coverage = apply_coverage_review(code, self.history.coverage_cache[key],
                        self.quality_config.coverage_reviews, today=date.fromisoformat(day))
                    self.coverage_observations[f"{code}:{coverage.flag}"] += 1
                if self.scenario.normalize:
                    y = float(frame.at[day, "yield_norm"])
                    percentile = float(frame.at[day, "yield_norm_pct"])
                else:
                    y = raw_y
                    percentile = float(frame.at[day, "yield_pct"])
                if not math.isfinite(y):
                    y = 0.0
                stocks.append(Stock(code, asset["name"], y, weight=asset["weight"],
                                    group=asset["group"], quality_flag=quality, quality_note=note,
                                    dividend_available=bool(frame.at[day, "dividend_available"]),
                                    dividend_note=frame.at[day, "dividend_note"],
                                    coverage_kind=coverage.kind, coverage_flag=coverage.flag,
                                    coverage_note=coverage.note,
                                    yield_percentile=percentile if math.isfinite(percentile) else None))
                holdings[code] = self.broker.quantity(code) * marks[code] / nav * 12.0 / asset["weight"]
            etf_pct = (self.broker.quantity(self.etf_code)
                       * marks.get(self.etf_code, 0.0) / nav * 100)
            config = GridConfig(groups=self.groups, dynamic_percentile=self.scenario.percentile,
                                veto_buy_on_risk=self.scenario.quality and self.quality_config.veto_buy_on_risk,
                                veto_buy_on_unknown=self.scenario.quality and self.quality_config.veto_buy_on_unknown,
                                veto_buy_on_coverage_risk=self.scenario.coverage and self.quality_config.veto_buy_on_coverage_risk,
                                veto_buy_on_coverage_unknown=self.scenario.coverage and self.quality_config.veto_buy_on_coverage_unknown,
                                min_cash_pct=self.limits.min_cash_pct,
                                max_industry_pct=self.limits.max_industry_pct,
                                industry_limits=self.limits.industry_limits,
                                external_position_pct=etf_pct,
                                reserved_cash_pct=max(self.limits.etf_budget_pct - etf_pct, 0.0))
            result = evaluate_portfolio(stocks, holdings, config)
            for decision in result.decisions:
                if "可持续性待复核" in decision.band_label:
                    self.coverage_blocks[f"{decision.stock.code}:{decision.stock.coverage_flag}"] += 1
            for decision in result.triggered:
                orders.append({"code": decision.stock.code, "side": decision.action,
                               "weight": decision.target_value_pct / 100,
                               "signal_date": day, "reason": decision.band_label})
        if self.scenario.etf == "rsi" and self.etf_code in marks:
            rsi = self.history.frames[self.etf_code].at[day, "rsi"]
            if pd.notna(rsi):
                decision, self.state = decide_rsi(float(rsi), float(rsi), self.etf_layers,
                                                  self.state, week_id(date.fromisoformat(day)),
                                                  params=self.rsi_params)
                if decision.triggered:
                    stock_pct = sum(self.broker.quantity(code) * marks.get(code, 0) for code in self.stock_codes) / nav * 100
                    target = decision.target_layers
                    if decision.action == "BUY":
                        target = max(self.etf_layers, min(target, self.etf_capacity(stock_pct)))
                    orders.append({"code": self.etf_code, "side": decision.action,
                                   "weight": target * self.etf_layer_pct / 100,
                                   "layers": target, "signal_date": day, "reason": f"RSI6={rsi:.4f}"})
        if self.scenario.etf == "allin" and self.etf_code in marks:
            rsi = self.history.frames[self.etf_code].at[day, "rsi"]
            if pd.notna(rsi):
                held_value = self.broker.quantity(self.etf_code) * marks.get(self.etf_code, 0.0)
                current_pct = held_value / nav * 100 if nav else 0.0
                decision, self.allin_state = decide_all_in(
                    float(rsi), current_pct, self.allin_state, week_id(date.fromisoformat(day)),
                    params=self.rsi_params)
                if decision.action == "BUY":
                    orders.append({"code": self.etf_code, "side": "BUY", "weight": 1.0, "signal_date": day,
                                   "reason": f"RSI6={rsi:.4f}<{self.rsi_params.allin_buy_rsi:g} 梭哈"})
                elif decision.action == "SELL" and decision.sell_fraction > 0:
                    orders.append({"code": self.etf_code, "side": "SELL",
                                   "sell_fraction": decision.sell_fraction, "signal_date": day,
                                   "reason": f"RSI6={rsi:.4f} {decision.note}"})
        return orders

    def tradable(self, code: str, day: str, side: str) -> bool:
        frame = self.history.frames[code]
        row = frame.loc[day]
        if not row["traded"]:
            self.blocked["suspended_or_missing"] += 1
            return False
        if abs(row["high"] - row["low"]) < 1e-9:
            self.blocked["single_price_session"] += 1
            return False
        i = self.history.calendar.index(day)
        prev = frame.at[self.history.calendar[max(0, i - 1)], "close"]
        if code != self.etf_code and pd.notna(prev):
            for action in self.history.actions[code].get(day, []):
                prev = (prev - action["cash_per_share"]) / action["split"]
            change = row["open"] / prev - 1
            if (side == "BUY" and change >= 0.099) or (side == "SELL" and change <= -0.099):
                self.blocked["opening_at_price_limit"] += 1
                return False
        return True

    def baseline_orders(self, day: str) -> list[dict]:
        orders = []
        allocations = {}
        if self.scenario.stocks == "hold":
            allocation = (100 - self.limits.min_cash_pct - self.limits.etf_budget_pct) / 100 / len(self.stock_codes)
            allocations.update({code: allocation for code in self.stock_codes})
        if self.scenario.etf == "hold":
            allocations[self.etf_code] = (100 - self.limits.min_cash_pct if self.scenario.etf_only else self.limits.etf_budget_pct) / 100
        for code, fraction in allocations.items():
            if code not in self.baseline_bought and self.history.assets[code]["prices"][0]["date"] < day:
                orders.append({"code": code, "side": "BUY", "amount": self.capital * fraction,
                               "signal_date": self.history.calendar[self.history.calendar.index(day) - 1],
                               "reason": "initial buy-and-hold allocation"})
        return orders

    def execute(self, day: str) -> None:
        marks = self.history.marks(day, opening=True)
        nav = self.broker.equity(marks)
        candidates = [order for order in self.pending + self.baseline_orders(day)
                      if self.tradable(order["code"], day, order["side"])]
        for order in [o for o in candidates if o["side"] == "SELL"]:
            code, opening = order["code"], marks[order["code"]]
            held = self.broker.quantity(code)
            if "sell_fraction" in order:
                quantity = held * order["sell_fraction"]
            elif "layers" in order and self.etf_layers:
                quantity = held * (self.etf_layers - order["layers"]) / self.etf_layers
            else:
                quantity = max(held - order["weight"] * nav / opening, 0)
            if quantity < held - 1e-7:
                quantity = math.floor(quantity / 100) * 100
            sold = self.broker.trade(code, day, "SELL", quantity, opening, 0,
                                     order["signal_date"], order["reason"])
            if sold and "layers" in order:
                self.etf_layers = order["layers"]
        buys = []
        for order in [o for o in candidates if o["side"] == "BUY"]:
            code, opening = order["code"], marks[order["code"]]
            held = self.broker.quantity(code)
            amount = order.get("amount", max(order.get("weight", 0) * nav - held * opening, 0))
            if amount > 0:
                buys.append((order, amount))
        # Recheck industry room at the actual opening marks, before buying.
        for group in {self.history.assets[o["code"]]["group"] for o, _ in buys
                      if o["code"] != self.etf_code}:
            if self.scenario.stocks != "grid":
                continue
            held_value = sum(self.broker.quantity(code) * marks.get(code, 0) for code in self.stock_codes
                             if self.history.assets[code]["group"] == group)
            cap = self.limits.industry_limits.get(group, self.limits.max_industry_pct) / 100 * nav
            demand = sum(amount for order, amount in buys if self.history.assets[order["code"]]["group"] == group)
            ratio = min(1.0, max(cap - held_value, 0) / demand) if demand else 1
            buys = [(order, amount * ratio if self.history.assets[order["code"]]["group"] == group else amount)
                    for order, amount in buys]
        for etf_pass in (False, True):
            part = [(order, amount) for order, amount in buys
                    if (order["code"] == self.etf_code) == etf_pass]
            if not part:
                continue
            reserve = nav * self.limits.min_cash_pct / 100
            if self.scenario.stocks == "hold" or self.scenario.etf_only:
                reserve = self.capital * self.limits.min_cash_pct / 100
            elif not etf_pass:
                reserve += max(self.limits.etf_budget_pct / 100 * nav
                               - self.broker.quantity(self.etf_code) * marks.get(self.etf_code, 0), 0)
            demand = sum(amount for _, amount in part)
            ratio = min(1.0, max(self.broker.cash - reserve, 0) / demand) if demand else 0
            for order, amount in part:
                code, opening = order["code"], marks[order["code"]]
                bought = self.broker.trade(code, day, "BUY", amount * ratio / opening,
                                           opening, reserve, order["signal_date"], order["reason"])
                if bought:
                    if "amount" in order:
                        self.baseline_bought.add(code)
                    if "layers" in order:
                        self.etf_layers = order["layers"]

    def run(self) -> dict:
        days = [day for day in self.history.calendar if self.start <= day <= self.end]
        if len(days) < 2:
            raise ValueError("Backtest needs at least two trading days")
        first_index = self.history.calendar.index(days[0])
        if first_index:
            self.pending = self.plan(self.history.calendar[first_index - 1])
        for day in days:
            i = self.history.calendar.index(day)
            for code in self.history.assets:
                for action in self.history.actions[code].get(day, []):
                    due = action.get("pay_date")
                    if due is None:
                        due = (self.history.calendar[i + self.dividend_delay]
                               if i + self.dividend_delay < len(self.history.calendar)
                               else (date.fromisoformat(day) + timedelta(days=self.dividend_delay * 2)).isoformat())
                    self.broker.corporate_action(code, action, due)
            self.broker.settle(day)
            self.execute(day)
            marks = self.history.marks(day)
            nav = self.broker.equity(marks)
            etf_value = self.broker.quantity(self.etf_code) * marks.get(self.etf_code, 0)
            if self.scenario.etf == "allin":
                # 梭哈版按比例持仓，此处只为日报表折算等效层数。
                self.etf_layers = (int(round(etf_value / nav * 100 / self.etf_layer_pct))
                                   if nav and self.etf_layer_pct else 0)
            self.daily.append({"date": day, "equity": nav, "cash": self.broker.cash,
                               "receivables": sum(value for _, value in self.broker.receivables),
                               "etf_value": etf_value, "etf_layers": self.etf_layers,
                               "stock_value": sum(self.broker.quantity(code) * marks.get(code, 0) for code in self.stock_codes)})
            self.pending = self.plan(day)
        daily = pd.DataFrame(self.daily).set_index("date")
        returns = daily["equity"].pct_change()
        returns.iloc[0] = daily["equity"].iloc[0] / self.capital - 1
        peaks = daily["equity"].cummax().clip(lower=self.capital)
        drawdown = daily["equity"] / peaks - 1
        years = (date.fromisoformat(days[-1]) - date.fromisoformat(days[0])).days / 365.25
        last = float(daily["equity"].iloc[-1])
        annual = {}
        previous = self.capital
        for year, group in daily.groupby(daily.index.str[:4]):
            ending = float(group["equity"].iloc[-1])
            annual[year] = ending / previous - 1
            previous = ending
        closing = self.history.marks(days[-1])
        attribution = []
        deferred_tax = 0.0
        for code, asset in self.history.assets.items():
            value = self.broker.quantity(code) * closing.get(code, 0)
            pnl = value + self.broker.flows[code] + self.broker.income[code] - self.broker.fees[code] - self.broker.taxes[code]
            attribution.append({"code": code, "name": asset["name"], "profit": pnl,
                                "ending_value": value, "dividends": self.broker.income[code],
                                "fees": self.broker.fees[code], "dividend_tax": self.broker.taxes[code]})
            if code != self.etf_code and self.broker.costs.dividend_tax:
                deferred_tax += sum(lot.quantity * lot.taxable_dividends_per_share * dividend_tax_rate(
                    lot.acquired, date.fromisoformat(days[-1])) for lot in self.broker.lots[code])
        if not math.isclose(sum(row["profit"] for row in attribution), last - self.capital, abs_tol=0.01):
            raise AssertionError("P&L attribution does not reconcile with NAV")
        metrics = {"key": self.scenario.key, "name": self.scenario.name, "start": days[0], "end": days[-1],
                   "quality_scope": ("派息率+逐期现金覆盖/银行指标；保险按有效期人工复核" if self.scenario.coverage
                                     else "仅派息率筛查" if self.scenario.quality and self.scenario.stocks == "grid"
                                     else "无质量筛查"),
                   "initial_capital": self.capital, "ending_equity": last,
                   "total_return": last / self.capital - 1, "cagr": (last / self.capital) ** (1 / years) - 1,
                   "max_drawdown": float(drawdown.min()), "volatility": float(returns.std() * np.sqrt(252)),
                   "sharpe_zero_rf": float(returns.mean() / returns.std() * np.sqrt(252)) if returns.std() else 0,
                   "average_cash_pct": float((daily["cash"] / daily["equity"]).mean() * 100),
                   "average_etf_pct": float((daily["etf_value"] / daily["equity"]).mean() * 100),
                   "gross_dividends": sum(self.broker.income.values()), "fees": sum(self.broker.fees.values()),
                   "dividend_tax_paid": sum(self.broker.taxes.values()), "estimated_exit_dividend_tax": deferred_tax,
                   "slippage_cost": sum(t["slippage_cost"] for t in self.broker.trades),
                   "trades": len(self.broker.trades),
                   "annual_turnover": sum(t["notional"] for t in self.broker.trades) / daily["equity"].mean() / years,
                   "annual_returns": annual, "blocked_orders": dict(self.blocked),
                   "quality_observations": dict(self.quality_observations),
                   "coverage_observations": dict(self.coverage_observations),
                   "coverage_blocked_signal_days": dict(self.coverage_blocks)}
        return {"metrics": metrics, "daily": self.daily, "trades": self.broker.trades,
                "dividend_events": self.broker.income_events, "attribution": attribution}


def dataset_fingerprint(directory: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(directory.glob("*.json")) + sorted((directory / "raw").glob("*.json")):
        digest.update(str(path.relative_to(directory)).encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()
