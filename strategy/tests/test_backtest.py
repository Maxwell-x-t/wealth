from datetime import date, timedelta

import pytest

from dividend_grid.backtest import Broker, Costs, History, Lot, Scenario, Simulation, dividend_tax_rate
from dividend_grid.grid import GroupThresholds
from dividend_grid.portfolio import PortfolioLimits


def asset(code="stock", prices=None, actions=None, financials=None):
    if prices is None:
        prices = [10] * 20
    rows = [{"date": (date(2018, 12, 24) + timedelta(days=i)).isoformat(),
             "open": price, "close": price, "high": price * 1.01,
             "low": price * 0.99, "volume": 10000}
            for i, price in enumerate(prices)]
    return {"code": code, "name": code, "group": "test", "weight": 0.12,
            "prices": rows, "actions": actions or [], "financials": financials or []}


def action(day="2019-01-01", cash=0.0, split=1.0, report="2017-12-31"):
    return {"ex_date": day, "announced": day, "cash_per_share": cash,
            "split": split, "bonus_taxable": 0.0, "pay_date": None,
            "report_date": report}


def test_dividend_and_split_do_not_change_account_wealth():
    broker = Broker(0)
    broker.lots["stock"] = [Lot(1000, date(2018, 1, 1))]
    before = broker.equity({"stock": 10})
    broker.corporate_action("stock", action(cash=1, split=2), "2019-01-07")
    assert broker.quantity("stock") == 2000
    assert broker.equity({"stock": 4.5}) == before
    assert broker.cash == 0
    broker.settle("2019-01-07")
    assert broker.cash == 1000
    assert broker.equity({"stock": 4.5}) == before


def test_fifo_dividend_tax_and_cash_are_reconciled():
    broker = Broker(0, Costs(commission=0, minimum_commission=0, slippage=0))
    broker.lots["stock"] = [Lot(100, date(2017, 1, 1), 1), Lot(100, date(2019, 1, 1), 1)]
    broker.trade("stock", "2019-01-15", "SELL", 150, 10, 0, "2019-01-14", "test")
    assert broker.taxes["stock"] == 10
    assert broker.quantity("stock") == 50
    assert broker.cash == pytest.approx(1500 - 1.5 - 0.03 - 10)


def test_tax_anniversaries_and_stamp_change():
    acquired = date(2023, 1, 15)
    assert dividend_tax_rate(acquired, date(2023, 2, 15)) == 0.2
    assert dividend_tax_rate(acquired, date(2023, 2, 16)) == 0.1
    assert dividend_tax_rate(acquired, date(2024, 1, 16)) == 0
    costs = Costs(commission=0, minimum_commission=0)
    assert costs.fee(10000, True, date(2023, 8, 25), False) == pytest.approx(10.1)
    assert costs.fee(10000, True, date(2023, 8, 28), False) == pytest.approx(5.1)
    assert costs.fee(10000, True, date(2023, 8, 28), True) == 0


def test_buy_lots_cannot_spend_cash_reserve():
    broker = Broker(1000)
    bought = broker.trade("stock", "2019-01-03", "BUY", 1000, 3, 100, "2019-01-02", "test")
    assert bought == 200
    assert broker.cash >= 100


def test_future_dividend_does_not_enter_previous_yield():
    history = History({"stock": asset(actions=[action(cash=0.6)])})
    frame = history.frames["stock"]
    assert frame.at["2018-12-31", "yield"] == 0
    assert frame.at["2019-01-01", "yield"] == pytest.approx(6)


def test_future_report_cannot_supply_earlier_eps():
    reports = [{"period": "2017-12-31", "published": "2019-01-03", "eps_ytd": 1}]
    frame = History({"stock": asset(financials=reports)}).frames["stock"]
    assert frame.at["2019-01-02", "eps"] != frame.at["2019-01-02", "eps"]
    assert frame.at["2019-01-03", "eps"] == 1


def test_indicator_is_continuous_through_split_and_dividend():
    prices = [10] * 8 + [4.5] * 12
    history = History({"stock": asset(prices=prices, actions=[action(cash=1, split=2)])})
    assert history.frames["stock"]["adjusted"].tolist() == pytest.approx([10] * 20)


def test_split_during_suspension_adjusts_stale_mark_until_trading_resumes():
    item = asset(prices=[10] * 8 + [5] * 12, actions=[action(split=2)])
    item["prices"] = [row for row in item["prices"] if row["date"] != "2019-01-01"]
    history = History({"stock": item, "other": asset("other")})
    assert history.frames["stock"].at["2019-01-01", "close"] == 5
    assert history.marks("2019-01-01", opening=True)["stock"] == 5
    assert history.frames["stock"]["adjusted"].tolist() == pytest.approx([10] * 20)


def test_signal_executes_next_open_and_profit_reconciles():
    actions = [action(day="2018-12-31", cash=0.6)]
    history = History({"stock": asset(actions=actions), "sh512890": asset("sh512890")})
    scenario = Scenario("test", "test", etf="cash", quality=False)
    result = Simulation(history, scenario, {"default": GroupThresholds()}, PortfolioLimits(),
                        "2019-01-01", "2019-01-10", capital=100000).run()
    assert result["trades"]
    assert result["trades"][0]["signal_date"] == "2018-12-31"
    assert result["trades"][0]["date"] == "2019-01-01"
    assert all(trade["signal_date"] < trade["date"] for trade in result["trades"])
    assert all(row["cash"] >= 0 for row in result["daily"])
    assert sum(row["profit"] for row in result["attribution"]) == pytest.approx(
        result["metrics"]["ending_equity"] - 100000)


def test_missing_quality_blocks_grid_buy_in_full_simulation():
    history = History({"stock": asset(actions=[action(day="2018-12-31", cash=0.6)]),
                       "sh512890": asset("sh512890")})
    result = Simulation(history, Scenario("test", "test", etf="cash"),
                        {"default": GroupThresholds()}, PortfolioLimits(),
                        "2019-01-01", "2019-01-10", capital=100000).run()
    assert result["metrics"]["ending_equity"] == 100000
    assert result["trades"] == []


def test_future_prices_and_reports_do_not_change_past_equity():
    import copy

    assets = {"stock": asset(actions=[action(day="2018-12-31", cash=0.6)]),
              "sh512890": asset("sh512890")}
    changed = copy.deepcopy(assets)
    for row in changed["stock"]["prices"]:
        if row["date"] > "2019-01-05":
            for field in ("open", "close", "high", "low"):
                row[field] *= 2
    changed["stock"]["financials"] = [
        {"period": "2018-12-31", "published": "2019-01-08", "eps_ytd": 100}]
    scenario = Scenario("test", "test", etf="cash", quality=False)
    results = [Simulation(History(data), scenario, {"default": GroupThresholds()},
                          PortfolioLimits(), "2019-01-01", "2019-01-05", capital=100000).run()
               for data in (assets, changed)]
    assert results[0]["daily"] == results[1]["daily"]
    assert results[0]["trades"] == results[1]["trades"]


def test_fiscal_caliber_does_not_double_count_drifting_ex_dates():
    """除息日逐年前移时，旧口径会把两个年度分红同时装进 365 天窗口，财年口径不会。"""
    rows = [{"date": day, "open": 10.0, "close": 10.0, "high": 10.1, "low": 9.9,
             "volume": 10000}
            for day in ("2019-07-11", "2019-07-12", "2020-07-09", "2020-07-10")]
    item = asset()
    item["prices"] = rows
    item["actions"] = [action(day="2019-07-12", cash=1.0, report="2018-12-31"),
                       action(day="2020-07-10", cash=1.2, report="2019-12-31")]
    naive = History({"stock": item}, caliber="naive").frames["stock"]
    fiscal = History({"stock": item}, caliber="fiscal").frames["stock"]
    # 旧的 365 天口径：2019-07-12 与 2020-07-10 同时落在窗口内 → 1.0+1.2=2.2 → 22%
    assert naive.at["2020-07-10", "yield"] == pytest.approx(22)
    # 财年口径：只取最近一个年度分红已实施的完整财年（2019-12-31 那笔）→ 1.2 → 12%
    assert fiscal.at["2020-07-10", "yield"] == pytest.approx(12)


def test_cash_dividend_without_report_period_is_rejected():
    item = asset(actions=[action(cash=0.6, report=None)])
    with pytest.raises(ValueError, match="report period"):
        History({"stock": item})


def test_normalized_yield_uses_three_fiscal_year_median():
    rows = [{"date": day, "open": 10.0, "close": 10.0, "high": 10.1, "low": 9.9,
             "volume": 10000}
            for day in ("2019-07-01", "2020-07-01", "2021-07-01", "2022-07-01")]
    item = asset()
    item["prices"] = rows
    item["actions"] = [
        action(day="2019-07-01", cash=1.0, report="2018-12-31"),
        action(day="2020-07-01", cash=3.0, report="2019-12-31"),
        action(day="2021-07-01", cash=2.0, report="2020-12-31"),
        action(day="2022-07-01", cash=4.0, report="2021-12-31"),
    ]
    frame = History({"stock": item}).frames["stock"]
    # 最近一笔财年分红 4.0 → 40%；最近三个完整财年中位数 median(3,2,4)=3 → 30%
    assert frame.at["2022-07-01", "yield"] == pytest.approx(40)
    assert frame.at["2022-07-01", "yield_norm"] == pytest.approx(30)


def test_broker_uses_the_dataset_etf_code_for_tax_and_fees():
    broker = Broker(0, Costs(commission=0, minimum_commission=0, slippage=0),
                    etf_code="sh510880")
    broker.lots["sh510880"] = [Lot(1000, date(2018, 1, 1))]
    broker.lots["stock"] = [Lot(1000, date(2018, 1, 1))]
    for code in ("sh510880", "stock"):
        broker.corporate_action(code, action(cash=1), "2019-01-07")
    # ETF 分红不计入应税股息（买卖 ETF 不缴股息红利税）。
    assert broker.lots["sh510880"][0].taxable_dividends_per_share == 0
    assert broker.lots["stock"][0].taxable_dividends_per_share == 1
    broker.settle("2019-01-07")
    # ETF 卖出免印花税与过户费。
    broker.trade("sh510880", "2019-06-03", "SELL", 1000, 10, 0, "2019-05-31", "test")
    assert broker.fees["sh510880"] == 0


def test_history_detects_etf_code_from_the_universe_group():
    assets = {"stock": asset("stock"),
              "sh510880": dict(asset("sh510880"), group="ETF", name="上证红利ETF")}
    assert History(assets).etf_code == "sh510880"
    assert History(assets, etf_code="sh510300").etf_code == "sh510300"
    # 没有 ETF 标的时回退到默认载体，保持旧行为。
    assert History({"stock": asset("stock")}).etf_code == "sh512890"


def test_simulation_excludes_the_dataset_etf_from_stock_codes():
    assets = {"stock": asset("stock"),
              "sh510880": dict(asset("sh510880"), group="ETF", name="上证红利ETF")}
    history = History(assets)
    simulation = Simulation(history, Scenario("grid_only", "仅网格", etf="cash"),
                            {"test": GroupThresholds((5.0, 5.5, 6.0), (4.5, 4.0, 3.5))},
                            PortfolioLimits(min_cash_pct=10, max_industry_pct=30,
                                            industry_limits={},
                                            etf_budget_pct=20),
                            "2019-01-01", "2019-01-15")
    assert simulation.stock_codes == ["stock"]
    assert simulation.etf_code == "sh510880"
    assert simulation.broker.etf_code == "sh510880"
