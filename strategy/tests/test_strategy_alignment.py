from datetime import date, timedelta
import json

import pytest

from dividend_grid.backtest import History
from dividend_grid.coverage import CoverageMetrics, apply_coverage_review, evaluate_coverage, multi_year_ratio
from dividend_grid.datasource import AkShareDataSource
from dividend_grid.dividends import DividendUnavailable, fiscal_dividend, parse_dividend_records
from dividend_grid.grid import GridConfig, decide, evaluate_portfolio
from dividend_grid.models import Stock
from dividend_grid.portfolio import PortfolioLimits, account_etf_capacity, account_path, value_account


def record(year, ex, cash, *, split=1, state="implemented", report=None):
    return {"report_date": report or f"{year}-12-31", "announced": ex,
            "ex_date": ex if state == "implemented" else None, "cash_per_share": cash,
            "state": state, "split": split}


def test_fiscal_signal_replaces_old_interim_after_new_plan_is_confirmed():
    records = [record(2024, "2025-06-01", 0.5),
               record(2025, "2025-10-01", 0.2, report="2025-06-30"),
               record(2025, "2026-06-01", 0.6)]
    assert fiscal_dividend(records, "2026-05-30").dps == pytest.approx(0.7)
    assert fiscal_dividend(records, "2026-06-01").dps == pytest.approx(0.8)


def test_zero_cancelled_missing_and_expired_are_distinct():
    old = record(2023, "2024-06-01", 1)
    assert fiscal_dividend([old], "2025-10-01").dps is None
    zero = record(2024, "2025-04-01", 0, state="no_distribution")
    assert fiscal_dividend([old, zero], "2025-04-01").dps == 0
    cancelled = record(2024, "2025-04-01", 0, state="cancelled")
    assert fiscal_dividend([old, cancelled], "2025-04-01").dps is None
    assert fiscal_dividend([], "2025-04-01").dps is None
    # A cancelled future plan must not affect an earlier signal.
    assert fiscal_dividend([old, cancelled], "2025-03-31").dps == 1


def test_splits_only_adjust_when_effective_in_live_and_backtest():
    records = [record(2024, "2025-06-01", 1), record(2025, "2026-06-01", 1, split=2)]
    assert fiscal_dividend(records, "2026-05-31").dps == 1
    assert fiscal_dividend(records, "2026-06-01").dps == 0.5


def test_parser_retains_explicit_no_distribution_and_cancellation():
    payload = {"result": {"data": [
        {"REPORT_DATE": "2024-12-31", "NOTICE_DATE": "2025-04-01", "ASSIGN_PROGRESS": "不分配不转增"},
        {"REPORT_DATE": "2025-12-31", "NOTICE_DATE": "2026-04-01", "ASSIGN_PROGRESS": "取消分配"},
    ]}}
    parsed = parse_dividend_records(payload)
    assert [r["state"] for r in parsed] == ["no_distribution", "cancelled"]
    assert fiscal_dividend(parsed, "2025-04-01").dps == 0
    with pytest.raises(ValueError, match="分页"):
        parse_dividend_records({"result": {"data": [], "pages": 2}})


def test_live_yield_and_historical_signal_share_fiscal_calculation(monkeypatch):
    today = date.today().isoformat()
    year = date.today().year - 1
    records = [record(year, f"{year + 1}-01-02", 0.6)]
    monkeypatch.setattr("dividend_grid.datasource.fetch_dividend_records", lambda _: records)
    source = AkShareDataSource()
    source._cache["a"] = {"现价": 10, "股息率(TTM)": 20, "股息(TTM)": 2, "每股收益": 1}
    assert source.get_yield("a")[0] == pytest.approx(6)
    assert source.get_quality("a").payout_ratio == pytest.approx(60)
    bars = [{"date": today, "open": 10, "close": 10, "high": 11, "low": 9, "volume": 100}]
    asset = {"name": "A", "group": "test", "prices": bars, "actions": records, "financials": [], "weight": 0.12}
    frame = History({"a": asset}).frames["a"]
    assert frame.at[today, "yield"] == source.get_yield("a")[0]


def test_missing_dividend_holds_position_without_treating_it_as_a_sell():
    stock = Stock("a", "A", 0, dividend_available=False, dividend_note="expired")
    decision = decide(stock, 12)
    assert decision.action == "HOLD" and decision.target_shares == 12


@pytest.mark.parametrize("flag", ["risk", "unknown"])
def test_coverage_blocks_buys_but_allows_normal_sells(flag):
    cfg = GridConfig(veto_buy_on_coverage_risk=True, veto_buy_on_coverage_unknown=True)
    stock = Stock("a", "A", 6, quality_flag="ok", coverage_flag=flag, coverage_note="review")
    assert decide(stock, 0, cfg).action == "HOLD"
    stock.dividend_yield = 4
    assert decide(stock, 12, cfg).action == "SELL"


def test_insurance_never_falls_back_to_ordinary_fcf(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Insurance must not query ordinary FCF")
    monkeypatch.setattr("dividend_grid.coverage.fetch_cashflow", forbidden)
    result = evaluate_coverage("sh601318", 5, 1000000)
    assert (result.kind, result.flag, result.ratio) == ("insurance", "unknown", None)


def test_missing_year_is_not_zero_and_cashflow_window_does_not_skip_years():
    cf = {f"{y}1231": {"ocf": 100, "capex": 0} for y in (2022, 2023, 2025)}
    assert multi_year_ratio(cf, {2022: 50, 2023: 50, 2024: 50, 2025: 50})[0] is None
    cf["20241231"] = {"ocf": 100, "capex": 0}
    assert multi_year_ratio(cf, {2022: 50, 2023: 50, 2025: 50})[0] is None


def test_manual_review_expires_and_preserves_underlying_evidence():
    metrics = CoverageMetrics(kind="insurance", flag="unknown", note="unsupported")
    reviews = {"a": {"flag": "ok", "as_of": "2026-01-01", "expires_on": "2026-06-30", "reason": "verified annual report"}}
    assert apply_coverage_review("a", metrics, reviews, date(2026, 3, 1)).flag == "ok"
    assert apply_coverage_review("a", metrics, reviews, date(2026, 7, 1)).flag == "unknown"
    assert metrics.flag == "unknown"


def account_file(tmp_path, **overrides):
    raw = {"as_of": date.today().isoformat(), "cash": 20000,
           "positions": {"a": 4000, "sh512890": 20000}, "etf_layers": 8}
    raw.update(overrides)
    path = tmp_path / "account.json"
    path.write_text(json.dumps(raw))
    return path


def test_account_prices_cash_and_etf_drift_determine_the_budget(tmp_path):
    path = account_file(tmp_path)
    specs = [("a", "A", None, 0.12, "finance")]
    prices = {"a": 10, "sh512890": 2}
    account = value_account(path, specs, prices.get)
    assert account.equity == 100000
    assert account.etf_pct == 40  # Eight layers must not be confused with 16% NAV.
    assert account.holdings["a"] == pytest.approx(40)
    result = evaluate_portfolio([Stock("a", "A", 6)], account.holdings,
        GridConfig(min_cash_pct=20, external_position_pct=account.etf_pct))
    assert result.current_cash_pct == 20
    assert result.total_buy_pct == 0


def test_account_missing_prices_unknown_positions_and_placeholders_fail_closed(tmp_path):
    specs = [("a", "A", None, None, "finance")]
    with pytest.raises(ValueError, match="有效估值"):
        value_account(account_file(tmp_path), specs, lambda _: None)
    with pytest.raises(ValueError, match="未纳入清单"):
        value_account(account_file(tmp_path, positions={"unknown": 100}), specs, lambda _: 10)
    with pytest.raises(ValueError, match="真实"):
        value_account(account_file(tmp_path, cash=None), specs, lambda _: 10)
    with pytest.raises(ValueError, match="空仓状态"):
        value_account(account_file(tmp_path, etf_layers=0), specs, lambda _: 10)


def test_etf_industry_exposure_reduces_stock_buy_room():
    result = evaluate_portfolio([Stock("a", "A", 6, group="finance")], {"a": 5},
        GridConfig(max_industry_pct=15, external_position_pct=20, external_industry_pct={"finance": 8}))
    assert result.decisions[0].target_value_pct == 7


def test_missing_explicit_account_does_not_silently_fall_back(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert account_path(None) is None
    assert account_path("missing.json").name == "missing.json"
    with pytest.raises(FileNotFoundError):
        value_account(account_path("missing.json"), [], lambda _: 10)


def test_preview_before_reporting_period_is_not_an_implemented_dividend():
    payload = {"result": {"data": [{"REPORT_DATE": "2026-06-30", "NOTICE_DATE": "2026-03-28",
                                   "ASSIGN_PROGRESS": "预披露", "PRETAX_BONUS_RMB": None}]}}
    assert parse_dividend_records(payload) == []


def test_identified_special_dividend_requires_review():
    special = record(2025, "2026-06-01", 3)
    special["special"] = True
    result = fiscal_dividend([special], "2026-06-02")
    assert result.dps is None and "特别分红" in result.note


def test_good_five_year_coverage_does_not_hide_latest_shortfall(monkeypatch):
    monkeypatch.setattr("dividend_grid.coverage.fetch_bank_metrics", lambda *a, **kw: None)
    monkeypatch.setattr("dividend_grid.coverage.fetch_cashflow", lambda *a, **kw: {
        f"{y}1231": {"ocf": 200 if y < 2025 else 40, "capex": 0} for y in range(2021, 2026)})
    monkeypatch.setattr("dividend_grid.coverage.fetch_dividends", lambda *a, **kw: {y: 50 for y in range(2021, 2026)})
    result = evaluate_coverage("sh600887", None, None)
    assert result.fcf_avg_ratio > 1.5
    assert result.flag == "risk" and "最新年度" in result.note


def test_etf_buys_also_respect_combined_industry_capacity(tmp_path):
    path = account_file(tmp_path, cash=72000, positions={"a": 2800}, etf_layers=0,
                        etf_industry_weights={"finance": 0.5, "other": 0.5})
    specs = [("a", "A", None, None, "finance")]
    account = value_account(path, specs, lambda _: 10)
    assert account_etf_capacity(account, PortfolioLimits(min_cash_pct=20), specs) == 2


def test_grid_cli_uses_account_without_legacy_files(tmp_path, monkeypatch, capsys):
    from dividend_grid import cli

    monkeypatch.chdir(tmp_path)
    account_file(tmp_path)
    (tmp_path / "portfolio_rules.json").write_text('{"min_cash_pct":20,"etf_budget_pct":20}')
    (tmp_path / "watchlist.json").write_text('{"a":"A"}')

    class Source:
        def get_price(self, code):
            return {"a": 10, "sh512890": 2}[code]

        def get_yield(self, code):
            return 6, "fiscal test"

        def get_quality(self, code):
            return None

    monkeypatch.setattr(cli, "make_source", lambda *a: Source())
    assert cli.main(["--source", "akshare", "--stocks-file", "watchlist.json", "--no-coverage"]) == 0
    output = capsys.readouterr().out
    assert "当前现金 20.00%" in output and "ETF及其他持仓 40.00%" in output
    assert "按实际股数估值" in output


def test_rsi_cli_does_not_buy_when_actual_etf_already_exceeds_budget(tmp_path, monkeypatch, capsys):
    from dividend_grid import rsi6_cli
    from dividend_grid.rsi6 import WeeklyBar

    monkeypatch.chdir(tmp_path)
    account_file(tmp_path)
    (tmp_path / "portfolio_rules.json").write_text('{"min_cash_pct":20,"etf_budget_pct":20}')
    (tmp_path / "watchlist.json").write_text('{"a":"A"}')

    class Source:
        def get_price(self, code):
            return {"a": 10, "sh512890": 2}[code]

    bars = [WeeklyBar(date(2026, 1, 5) + timedelta(weeks=i), 10-i, 10-i) for i in range(8)]
    monkeypatch.setattr(rsi6_cli, "make_source", lambda *a: Source())
    monkeypatch.setattr(rsi6_cli, "fetch_weekly_bars", lambda *a: bars)
    assert rsi6_cli.main(["--mode", "layers"]) == 0
    output = capsys.readouterr().out
    assert "当前 8 层 | 目标 8 层 | 动作 维持" in output and "按实际股数估值" in output
    # Notification generation must not change the registered holdings.
    assert json.loads((tmp_path / "account.json").read_text())["etf_layers"] == 8


def test_missing_dividend_is_not_displayed_as_zero_percent():
    from dividend_grid.report import render_table
    result = evaluate_portfolio([Stock("a", "A", 0, dividend_available=False)], {"a": 12})
    text = render_table(result)
    assert "数据缺失" in text and "个股平均股息率(仅展示) 数据不足" in text


def test_account_notes_do_not_trigger_signal_only_notifications(tmp_path, monkeypatch, capsys):
    from dividend_grid import cli
    monkeypatch.chdir(tmp_path)
    (tmp_path / "portfolio_rules.json").write_text('{"min_cash_pct":20,"etf_budget_pct":20}')
    (tmp_path / "rsi6_holdings.json").write_text('{"sh512890":0}')
    sent = []
    monkeypatch.setattr(cli.ConsoleNotifier, "send", lambda *a: sent.append(a))
    assert cli.main(["--stocks", "a:A:5", "--holdings", "a:12", "--no-coverage",
                     "--notify", "console", "--notify-on", "signal"]) == 0
    assert sent == []
    assert "手工仓位估算" in capsys.readouterr().out


def test_other_assets_count_toward_cash_and_both_strategy_budgets(tmp_path):
    path = account_file(tmp_path, cash=35000, positions={"a": 1000, "sh588000": 55000},
                        etf_layers=0, external_assets={"sh588000": "Other ETF"})
    specs = [("a", "A", None, None, "finance")]
    account = value_account(path, specs, {"a": 10, "sh588000": 1}.get)
    assert account.equity == 100000
    assert account.stock_pct == 10
    assert account.etf_pct == 0
    assert account.other_pct == account.external_position_pct == pytest.approx(55)
    assert account.holdings == pytest.approx({"a": 10})
    assert account_etf_capacity(account, PortfolioLimits(min_cash_pct=20, etf_budget_pct=20), specs) == 7
    result = evaluate_portfolio([Stock("a", "A", 6)], account.holdings,
        GridConfig(min_cash_pct=20, external_position_pct=account.external_position_pct,
                   reserved_cash_pct=20))
    assert result.current_cash_pct == 35
    assert result.total_buy_pct == 0
    assert "不生成买卖信号" in " ".join(account.notes())


def test_other_asset_missing_price_stops_account_valuation(tmp_path):
    path = account_file(tmp_path, positions={"a": 4000, "sh588000": 1000}, etf_layers=0,
                        external_assets={"sh588000": "Other ETF"})
    with pytest.raises(ValueError, match="sh588000.*有效估值"):
        value_account(path, [("a", "A", None, None, "finance")], {"a": 10}.get)


@pytest.mark.parametrize("external", [
    {"sh512890": "Duplicate RSI ETF"}, {"sh600036": "Duplicate stock"},
    {"588000": "Missing exchange"}, {"sh588000": ""}, ["sh588000"],
])
def test_other_assets_require_distinct_roles_and_valid_metadata(tmp_path, external):
    path = account_file(tmp_path, external_assets=external)
    with pytest.raises(ValueError, match="其他持仓"):
        value_account(path, [("sh600036", "Bank", None, None, "finance")], lambda _: 10)


def test_grid_cli_values_other_assets_without_creating_trade_signals(tmp_path, monkeypatch, capsys):
    from dividend_grid import cli

    monkeypatch.chdir(tmp_path)
    account_file(tmp_path, cash=60000, positions={"a": 1000, "sh588000": 10000, "sz159915": 10000},
                 etf_layers=0, external_assets={"sh588000": "ETF A", "sz159915": "ETF B"})
    (tmp_path / "portfolio_rules.json").write_text('{"min_cash_pct":20,"etf_budget_pct":20}')
    (tmp_path / "watchlist.json").write_text('{"a":"A"}')
    yields_requested = []

    class Source:
        def get_price(self, code):
            return {"a": 10, "sh588000": 1, "sz159915": 2}[code]

        def get_yield(self, code):
            yields_requested.append(code)
            return 6, "test"

        def get_quality(self, code):
            return None

    monkeypatch.setattr(cli, "make_source", lambda *a: Source())
    assert cli.main(["--source", "akshare", "--stocks-file", "watchlist.json", "--no-coverage", "--notify", "none"]) == 0
    output = capsys.readouterr().out
    assert yields_requested == ["a"]
    assert "当前现金 60.00%" in output
    assert "ETF及其他持仓 30.00%" in output
    assert "512890 预留现金 20.00%" in output
    assert "512890实际仓位0.00%" in output


def test_rsi_cli_other_assets_limit_new_etf_buys(tmp_path, monkeypatch, capsys):
    from dividend_grid import rsi6_cli
    from dividend_grid.rsi6 import WeeklyBar

    monkeypatch.chdir(tmp_path)
    account_file(tmp_path, cash=25000, positions={"a": 500, "sh588000": 70000},
                 etf_layers=0, external_assets={"sh588000": "Other ETF"})
    (tmp_path / "portfolio_rules.json").write_text('{"min_cash_pct":20,"etf_budget_pct":20}')
    (tmp_path / "watchlist.json").write_text('{"a":"A"}')

    class Source:
        def get_price(self, code):
            return {"a": 10, "sh588000": 1}[code]

    bars = [WeeklyBar(date(2026, 1, 5) + timedelta(weeks=i), 10-i, 10-i) for i in range(8)]
    monkeypatch.setattr(rsi6_cli, "make_source", lambda *a: Source())
    monkeypatch.setattr(rsi6_cli, "fetch_weekly_bars", lambda *a: bars)
    assert rsi6_cli.main(["--mode", "layers", "--notify", "none"]) == 0
    output = capsys.readouterr().out
    assert "当前 0 层 | 目标 2 层 | 动作 买入" in output
    assert "4,000.00元" in output
    assert json.loads((tmp_path / "account.json").read_text())["etf_layers"] == 0
