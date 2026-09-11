from datetime import date, timedelta
import json
from types import SimpleNamespace

import pytest

from dividend_grid.datasource import AkShareDataSource, apply_overrides, parse_tencent_quote
from dividend_grid.grid import GridConfig, decide, evaluate_portfolio
from dividend_grid.models import Stock
from dividend_grid.portfolio import PortfolioLimits, load_etf_layers
from dividend_grid.quality import QualityConfig, assess
from dividend_grid.rsi6 import build_market_snapshot, rsi_wilder
from dividend_grid.rsi6_data import adjusted_weekly_bars, fetch_weekly_bars


def test_sixteen_full_buy_signals_fit_shared_budget():
    stocks = [Stock(str(i), str(i), 7, group=str(i % 4)) for i in range(16)]
    result = evaluate_portfolio(stocks, {}, GridConfig(
        min_cash_pct=10, max_industry_pct=30, external_position_pct=8, reserved_cash_pct=12))
    assert sum(d.target_value_pct for d in result.decisions) == pytest.approx(70)
    assert result.target_position_pct == pytest.approx(78)
    assert result.target_cash_pct == pytest.approx(22)
    assert all(d.target_shares == pytest.approx(70 / 16) for d in result.decisions)


def test_industry_cap_and_sales_are_applied_before_cash_allocation():
    stocks = [Stock("sell", "Sell", 4.7, group="a"),
              Stock("buy", "Buy", 7, group="a"),
              Stock("other", "Other", 7, group="b")]
    result = evaluate_portfolio(stocks, {"sell": 12}, GridConfig(max_industry_pct=15))
    targets = {d.stock.code: d.target_shares for d in result.decisions}
    assert targets == {"sell": 9, "buy": 6, "other": 12}
    assert any("先卖后买" in warning for warning in result.warnings)


def test_existing_over_limit_position_is_held_and_new_buys_blocked():
    stocks = [Stock("held", "Held", 5, group="a"), Stock("new", "New", 7, group="a")]
    result = evaluate_portfolio(stocks, {"held": 40}, GridConfig(max_industry_pct=30))
    assert result.total_buy_pct == 0
    assert result.total_sell_pct == 0
    assert result.target_position_pct == 40
    assert result.warnings


def test_missing_quote_keeps_position_and_blocks_buys():
    stocks = [Stock("missing", "Missing", 0, data_available=False), Stock("buy", "Buy", 7)]
    result = evaluate_portfolio(stocks, {"missing": 50})
    assert result.current_cash_pct == 50
    assert result.target_cash_pct == 50
    assert result.total_buy_pct == 0
    assert result.warnings


def test_missing_holding_in_watchlist_is_not_treated_as_cash():
    with pytest.raises(ValueError, match="未纳入"):
        evaluate_portfolio([Stock("a", "A", 7)], {"unlisted": 50})


@pytest.mark.parametrize("shares", [-1, float("nan"), float("inf")])
def test_invalid_holdings_are_rejected(shares):
    with pytest.raises(ValueError):
        evaluate_portfolio([Stock("a", "A", 7)], {"a": shares})


def test_total_current_position_above_one_hundred_is_rejected():
    with pytest.raises(ValueError, match="超过 100"):
        evaluate_portfolio([Stock("a", "A", 5)], {"a": 95}, GridConfig(external_position_pct=10))


def test_unknown_quality_blocks_buy_and_confirmed_exit_overrides_floor():
    stock = Stock("a", "A", 7)
    assert decide(stock, 0, GridConfig(veto_buy_on_unknown=True)).action == "HOLD"
    decision = decide(stock, 2, GridConfig(exit_reasons={"a": "confirmed dividend impairment"}))
    assert decision.action == "SELL"
    assert decision.target_shares == 0


def test_negative_pe_is_recognized_as_loss_in_quality_filter():
    parts = [""] * 70
    parts[3], parts[39], parts[64] = "10", "-5", "6"
    quote = parse_tencent_quote('v_sh600011="' + "~".join(parts) + '";')
    source = AkShareDataSource()
    source._cache["a"] = quote
    metrics = source.get_quality("a")
    assert metrics.eps == -2
    assert assess(metrics, QualityConfig())[0] == "risk"
    quote["每股收益"] = None
    quote["市盈率(TTM)"] = 0
    assert assess(source.get_quality("a"), QualityConfig())[0] == "unknown"


def test_dividend_override_tracks_price_and_preserves_as_of_date():
    overrides = {"a": {"dividend_per_share": 1.2, "as_of": "2026-09-01", "expires_on": "2026-12-31"}}
    args = ("a", 5, "live", overrides)
    low, source, marked = apply_overrides(*args, price=20, today=date(2026, 9, 10))
    high, _, _ = apply_overrides(*args, price=30, today=date(2026, 9, 11))
    assert (low, high) == pytest.approx((6, 4))
    assert "2026-09-01" in source
    assert marked
    with pytest.raises(ValueError, match="过期"):
        apply_overrides(*args, price=20, today=date(2027, 1, 1))
    with pytest.raises(ValueError, match="最新价格"):
        apply_overrides(*args, today=date(2026, 9, 10))
    with pytest.raises(ValueError, match="固定股息率"):
        apply_overrides("a", 5, "live", {"a": 5.14}, price=20)


def test_etf_capacity_uses_actual_stock_position():
    limits = PortfolioLimits()
    assert limits.layer_pct == 2
    assert limits.etf_capacity(70) == 10
    assert limits.etf_capacity(80) == 5
    assert limits.etf_capacity(91) == 0


def test_missing_etf_holdings_cannot_be_assumed_zero(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_etf_layers(tmp_path / "missing.json")
    path = tmp_path / "layers.json"
    path.write_text('{"sh512890": 1.5}')
    with pytest.raises(ValueError):
        load_etf_layers(path)


def _rows(values):
    return [{"date": (date(2026, 1, 5) + timedelta(days=i * 7)).isoformat(),
             "open": value, "close": value} for i, value in enumerate(values)]


def test_cash_distribution_does_not_create_false_rsi_drop():
    bars = adjusted_weekly_bars(_rows([90] * 8), _rows([100] * 7 + [90]))
    assert rsi_wilder([bar.close for bar in bars]) == 50
    assert rsi_wilder([bar.raw_close for bar in bars]) == 0
    assert bars[-1].close == bars[-1].raw_close == 90


def test_adjusted_trigger_prices_match_trade_price_scale():
    values = [100, 102, 101, 103, 102, 104, 103, 105]
    bars = adjusted_weekly_bars(_rows(values), _rows([v * 2 for v in values]))
    snapshot = build_market_snapshot(bars, label="Latest", current_layers=2, max_buy_layers=5)
    assert snapshot.close == 210
    for trigger in snapshot.triggers:
        assert rsi_wilder([bar.close for bar in bars] + [trigger.price]) == pytest.approx(trigger.target_rsi)
    assert "买入至5层" in snapshot.triggers[-1].action
    assert "买入至10层" not in snapshot.triggers[-1].action


def test_adjusted_raw_date_mismatch_is_rejected():
    with pytest.raises(ValueError, match="日期不一致"):
        adjusted_weekly_bars(_rows([1] * 8), _rows([1] * 7))


@pytest.mark.parametrize("extra_adjusted", [True, False])
def test_adjusted_raw_history_prefix_mismatch_is_aligned(extra_adjusted):
    values = [100, 102, 101, 103, 102, 104, 103, 105]
    adjusted, raw = _rows(values), _rows([value * 2 for value in values])
    if extra_adjusted:
        raw = raw[1:]
    else:
        adjusted = adjusted[1:]
    bars = adjusted_weekly_bars(adjusted, raw)
    assert len(bars) == 7
    assert bars[0].date == date(2026, 1, 12)
    assert [bar.close for bar in bars] == pytest.approx([value * 2 for value in values[1:]])
    assert [bar.raw_close for bar in bars] == [value * 2 for value in values[1:]]


def test_adjusted_raw_interior_gap_is_rejected():
    adjusted = _rows([1] * 9)
    raw = adjusted[1:4] + adjusted[5:]
    with pytest.raises(ValueError, match="日期不一致"):
        adjusted_weekly_bars(adjusted, raw)


@pytest.mark.parametrize("adjusted,raw", [([], []), (_rows([1] * 8), []), ([], _rows([1] * 8))])
def test_adjusted_raw_empty_history_is_rejected(adjusted, raw):
    with pytest.raises(ValueError, match="日期不一致"):
        adjusted_weekly_bars(adjusted, raw)


def test_fetch_requests_adjusted_and_raw_without_silent_unadjusted_fallback(monkeypatch):
    import akshare
    calls = []

    def fetch(**kwargs):
        calls.append(kwargs["adjust"])
        rows = _rows([90] * 8 if kwargs["adjust"] == "qfq" else [100] * 7 + [90])
        return SimpleNamespace(to_dict=lambda _: rows)

    monkeypatch.setattr(akshare, "fund_etf_hist_em", fetch)
    assert len(fetch_weekly_bars()) == 8
    assert calls == ["qfq", ""]


def test_fetch_falls_back_to_explicit_adjusted_tencent_data(monkeypatch):
    import akshare
    from dividend_grid import rsi6_data

    def unavailable(**kwargs):
        raise RuntimeError("primary unavailable")

    monkeypatch.setattr(akshare, "fund_etf_hist_em", unavailable)
    calls = []

    def fallback(code, adjust):
        calls.append((code, adjust))
        return _rows([90] * 8 if adjust else [100] * 7 + [90])

    monkeypatch.setattr(rsi6_data, "_tencent_daily_rows", fallback)
    bars = fetch_weekly_bars()
    assert calls == [("sh512890", "qfq"), ("sh512890", "")]
    assert rsi_wilder([bar.close for bar in bars]) == 50


def test_tencent_raw_only_response_cannot_masquerade_as_adjusted(monkeypatch):
    from dividend_grid import rsi6_data
    response = SimpleNamespace(raise_for_status=lambda: None,
                               json=lambda: {"data": {"sh512890": {"day": [["2026-09-10", "1", "1"]]}}})
    monkeypatch.setattr(rsi6_data.requests, "get", lambda *args, **kwargs: response)
    with pytest.raises(ValueError, match="qfqday"):
        rsi6_data._tencent_daily_rows("sh512890", "qfq")


@pytest.mark.parametrize("adjust,key", [("qfq", "qfqday"), ("", "day")])
def test_tencent_uses_precise_daily_endpoint(monkeypatch, adjust, key):
    from dividend_grid import rsi6_data
    calls = []

    def fetch(url, *, params, timeout):
        calls.append((url, params, timeout))
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {
            "data": {"sh512890": {key: [
                ["2026-09-11", "1.214", "1.203", "1.216", "1.202", "4883984.00", {}]
            ]}}
        })

    monkeypatch.setattr(rsi6_data.requests, "get", fetch)
    rows = rsi6_data._tencent_daily_rows("sh512890", adjust)
    assert calls == [("https://proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get",
                      {"param": f"sh512890,day,,,640,{adjust}"}, 15)]
    assert rows == [{"date": "2026-09-11", "open": "1.214", "close": "1.203"}]


def test_market_cli_records_one_sell_target_across_repeated_runs(tmp_path, monkeypatch, capsys):
    from dividend_grid import rsi6_cli

    files = {"portfolio_rules.json": {"min_cash_pct": 10, "max_industry_pct": 30, "etf_budget_pct": 20},
             "rsi6_holdings.json": {"sh512890": 8}, "holdings.json": {}, "watchlist.json": {}}
    for name, data in files.items():
        (tmp_path / name).write_text(json.dumps(data))
    monkeypatch.chdir(tmp_path)
    values = [100, 103, 102, 105, 104, 107, 106, 108]
    bars = adjusted_weekly_bars(_rows(values), _rows(values))
    assert 74 < rsi_wilder(values) < 80
    monkeypatch.setattr(rsi6_cli, "fetch_weekly_bars", lambda _: bars)
    assert rsi6_cli.main([]) == 0
    assert "卖出至6层" in capsys.readouterr().out
    (tmp_path / "rsi6_holdings.json").write_text('{"sh512890": 6}')
    assert rsi6_cli.main([]) == 0
    output = capsys.readouterr().out
    assert "本周该档卖出已完成" in output
    assert "卖出至4层" not in output


def test_cli_quote_failure_reports_position_and_returns_failure(tmp_path, monkeypatch, capsys):
    from dividend_grid import cli

    files = {"portfolio_rules.json": {"min_cash_pct": 10, "max_industry_pct": 30, "etf_budget_pct": 20},
             "rsi6_holdings.json": {"sh512890": 0}, "holdings.json": {"a": 12},
             "watchlist.json": {"a": "A", "b": "B"}}
    for name, data in files.items():
        (tmp_path / name).write_text(json.dumps(data))
    monkeypatch.chdir(tmp_path)

    class Source:
        def get_yield(self, code):
            if code == "a":
                raise RuntimeError("quote unavailable")
            return 7, "test"

        def get_quality(self, code):
            return None

    monkeypatch.setattr(cli, "make_source", lambda *_: Source())
    assert cli.main(["--stocks-file", "watchlist.json", "--source", "akshare"]) == 1
    output = capsys.readouterr().out
    assert "当前现金 88.00%" in output
    assert "暂停全部新增买入" in output


def test_rsi_cli_caps_layers_using_shared_holdings(tmp_path, monkeypatch, capsys):
    from dividend_grid import rsi6_cli
    files = {"portfolio_rules.json": {"min_cash_pct": 10, "max_industry_pct": 30, "etf_budget_pct": 20},
             "rsi6_holdings.json": {"sh512890": 0}, "holdings.json": {"a": 80},
             "watchlist.json": {"a": "A"}}
    for name, data in files.items():
        (tmp_path / name).write_text(json.dumps(data))
    monkeypatch.chdir(tmp_path)
    bars = adjusted_weekly_bars(_rows([110, 109, 108, 107, 106, 105, 104, 103]),
                                _rows([110, 109, 108, 107, 106, 105, 104, 103]))
    monkeypatch.setattr(rsi6_cli, "fetch_weekly_bars", lambda _: bars)
    assert rsi6_cli.main(["--mode", "layers"]) == 0
    assert "目标 5 层" in capsys.readouterr().out


def test_shipped_portfolio_rules_allocate_exactly_100pct():
    """仓库配置的三分类必须凑满 100%：个股上限 + 现金下限 + ETF 预留。

    个股上限不是独立字段，而是 100% − min_cash_pct − etf_budget_pct 推导出来的，
    所以改现金下限会直接改变个股上限。此测试锁定当前决策：60 / 20 / 20。
    """
    from pathlib import Path

    from dividend_grid.portfolio import load_limits

    limits = load_limits(Path(__file__).resolve().parents[1] / "portfolio_rules.json")
    stock_cap = 100.0 - limits.min_cash_pct - limits.etf_budget_pct

    assert limits.min_cash_pct == 20.0
    assert limits.etf_budget_pct == 20.0
    assert stock_cap == 60.0
    assert limits.min_cash_pct + limits.etf_budget_pct + stock_cap == pytest.approx(100.0)


def test_stock_cap_tracks_cash_floor():
    """降低现金下限应当等量抬高个股上限（ETF 预留不变）。"""
    generous = PortfolioLimits(min_cash_pct=20, max_industry_pct=30, etf_budget_pct=20)
    strict = PortfolioLimits(min_cash_pct=30, max_industry_pct=30, etf_budget_pct=20)
    assert 100 - generous.min_cash_pct - generous.etf_budget_pct == 60
    assert 100 - strict.min_cash_pct - strict.etf_budget_pct == 50
