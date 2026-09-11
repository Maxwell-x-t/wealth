from datetime import date, timedelta

import pytest

from dividend_grid.backtest import History, Scenario, Simulation
from dividend_grid.backtest_coverage import coverage_on, parse_banks
from dividend_grid.coverage import apply_coverage_review, fetch_bank_metrics
from dividend_grid.dividends import known_dividend_totals
from dividend_grid.grid import GroupThresholds
from dividend_grid.portfolio import PortfolioLimits


def dividend(year, total=60, **kwargs):
    return dict(report_date=f"{year}-12-31", announced=f"{year+1}-05-01",
                ex_date=f"{year+1}-06-01", state="implemented", cash_total=total,
                cash_per_share=0.6, split=1, **kwargs)


def coverage_data():
    return {"cashflows": [{"period": f"{year}-12-31", "published": f"{year+1}-04-01",
                           "ocf": 200, "capex": 50} for year in (2015, 2016, 2017)],
            "dividends": [dividend(year) for year in (2015, 2016, 2017)], "banks": []}


def test_dividend_total_waits_for_completed_annual_distribution():
    interim = dividend(2018, 40)
    interim.update(report_date="2018-06-30", announced="2018-08-01", ex_date="2018-09-01")
    annual = dividend(2018, 60)
    records = [interim, annual]
    assert known_dividend_totals(records, "2019-05-31") == {}
    assert known_dividend_totals(records, "2019-06-01") == {2018: 100}
    annual["cash_total"] = None
    assert known_dividend_totals(records, "2019-06-01") == {}
    annual.update(state="no_distribution", cash_total=0, cash_per_share=0, ex_date=None)
    assert known_dividend_totals(records, "2019-05-01") == {2018: 40}


def test_cashflow_uses_historical_clock_and_latest_year_risk_gate():
    data = coverage_data()
    data["cashflows"].append({"period": "2018-12-31", "published": "2019-04-01", "ocf": 10, "capex": 50})
    data["dividends"].append(dividend(2018))
    assert coverage_on("sz000423", data, "2019-05-31").flag == "ok"
    current = coverage_on("sz000423", data, "2019-06-01")
    assert current.fcf_avg_ratio > 1.5
    assert current.flag == "risk"
    assert "FY2018" in current.note


def test_future_financial_publication_cannot_change_earlier_coverage():
    data = coverage_data()
    earlier = coverage_on("sz000423", data, "2019-01-01")
    data["cashflows"].append({"period": "2017-12-31", "published": "2019-02-01", "ocf": -10000, "capex": 50})
    assert coverage_on("sz000423", data, "2019-01-01") == earlier
    assert coverage_on("sz000423", data, "2019-02-01").flag == "risk"


def test_insurance_stays_unknown_until_a_dated_review_is_effective():
    metrics = coverage_on("sh601318", {}, "2019-01-01")
    assert metrics.kind == "insurance" and metrics.flag == "unknown"
    reviews = {"sh601318": {"flag": "ok", "as_of": "2020-01-01", "expires_on": "2020-12-31", "reason": "test"}}
    assert apply_coverage_review("sh601318", metrics, reviews, date(2019, 12, 31)).flag == "unknown"
    assert apply_coverage_review("sh601318", metrics, reviews, date(2020, 1, 1)).flag == "ok"
    assert apply_coverage_review("sh601318", metrics, reviews, date(2021, 1, 1)).flag == "unknown"


def test_bank_core_tier_one_field_is_shared_by_live_and_replay(monkeypatch):
    raw = {"ORG_TYPE": "银行", "REPORT_DATE": "2025-12-31", "NOTICE_DATE": "2026-03-28",
           "HXYJBCZL": 14.16, "FIRST_ADEQUACY_RATIO": 16.51, "NEWCAPITALADER": 18.24,
           "NONPERLOAN": 0.94, "BLDKBBL": 391.79}
    payload = {"result": {"pages": 1, "data": [raw]}}
    class Response:
        def raise_for_status(self):
            pass
        def json(self):
            return payload
    monkeypatch.setattr("dividend_grid.coverage.requests.get", lambda *a, **k: Response())
    assert fetch_bank_metrics("sh600036").cet1 == 14.16
    data = {"banks": parse_banks(payload)}
    assert coverage_on("sh600036", data, "2026-03-27").flag == "unknown"
    assert coverage_on("sh600036", data, "2026-03-28").bank.cet1 == 14.16


def test_missing_core_tier_one_is_not_filled_from_tier_one():
    row = {"ORG_TYPE": "银行", "REPORT_DATE": "2025-12-31", "NOTICE_DATE": "2026-03-28",
           "FIRST_ADEQUACY_RATIO": 16.51, "NONPERLOAN": 0.94, "BLDKBBL": 391.79}
    data = {"banks": parse_banks({"result": {"data": [row]}})}
    assert coverage_on("sh600036", data, "2026-03-28").flag == "unknown"


def stock_asset():
    return {"code": "stock", "name": "stock", "group": "test", "weight": 0.12,
            "actions": [dividend(2017)], "financials": [
                {"period": "2017-12-31", "published": "2018-04-01", "eps_ytd": 1}],
            "dividend_records": [dividend(2017)], "coverage": coverage_data(),
            "prices": [{"date": (date(2018, 12, 24) + timedelta(days=i)).isoformat(),
                        "open": 10, "close": 10, "high": 10.1, "low": 9.9, "volume": 10000}
                       for i in range(20)]}


def run(item, coverage=True):
    return Simulation(History({"stock": item}), Scenario("test", "test", etf="cash", coverage=coverage),
                      {"default": GroupThresholds()}, PortfolioLimits(), "2019-01-01", "2019-01-10").run()


def test_coverage_gate_blocks_unknown_and_risk_but_not_the_control():
    item = stock_asset()
    assert run(item)["trades"]
    item["coverage"]["cashflows"] = []
    assert not run(item)["trades"]
    assert run(item, coverage=False)["trades"]
    item["coverage"] = coverage_data()
    item["coverage"]["cashflows"][-1]["ocf"] = -1000
    assert not run(item)["trades"]
    assert run(item, coverage=False)["trades"]


def test_new_strategy_rejects_a_dataset_missing_coverage_history():
    item = stock_asset()
    del item["coverage"]
    with pytest.raises(ValueError, match="dated coverage"):
        run(item)


def test_future_coverage_cannot_change_past_equity():
    item = stock_asset()
    expected = run(item)
    item["coverage"]["cashflows"].append({"period": "2017-12-31", "published": "2019-02-01",
                                         "ocf": -10000, "capex": 50})
    actual = run(item)
    assert expected["trades"] == actual["trades"]
    assert expected["daily"] == actual["daily"]
