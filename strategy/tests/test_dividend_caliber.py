"""股息率口径通用规则的回归测试。

核心不变量：股息率按「财年口径」计算，与分红到账节奏解耦。
典型反面场景是「某年取消中期分红」——滚动 12 个月口径会因此机械跳水（假卖出信号），
财年口径不会。这里同时锁住旧口径的跳水行为，防止未来有人改回 TTM。
"""

from datetime import date, timedelta

import pytest

from dividend_grid.datasource import AkShareDataSource
from dividend_grid.dividends import CALIBER_NAME, fiscal_dividend
from dividend_grid.models import Decision, PortfolioResult, Stock
from dividend_grid.report import _caliber_line, render_table


def rec(report, announced, ex, cash, state="implemented", special=False, split=1.0):
    return {"report_date": report, "announced": announced, "ex_date": ex, "state": state,
            "cash_per_share": cash,
            "cash_total": cash * 1e9 if cash else 0.0,
            "special": special, "split": split}


# 海尔智家真实节奏：FY2025 中期 0.2692（除息 2025-11-07）+ 年度 0.89151（除息 2026-08-21），
# FY2026 中报公告不分配。次年年度分红要到 2027 年 7~8 月才实施。
HAIER = [
    rec("2025-06-30", "2025-10-25", "2025-11-07", 0.2692),
    rec("2025-12-31", "2026-08-15", "2026-08-21", 0.89151),
    rec("2026-06-30", "2026-08-28", None, 0.0, state="no_distribution"),
]


def rolling_365(records, window_end):
    """对照用的旧口径：过去 365 天内已除息的每股分红求和。"""
    end = date.fromisoformat(window_end)
    start = end - timedelta(days=365)
    return sum(r["cash_per_share"] for r in records
               if r["ex_date"] and start < date.fromisoformat(r["ex_date"]) <= end)


def test_fiscal_caliber_survives_interim_dropping_out_of_window():
    """取消中期分红不会让财年口径股息率跳水（旧 365 天口径会）。"""
    expected = pytest.approx(0.2692 + 0.89151)
    for day in ("2026-09-11", "2026-11-07", "2026-11-08", "2026-12-01",
                "2027-01-01", "2027-06-01", "2027-09-01"):
        snapshot = fiscal_dividend(HAIER, day)
        assert snapshot.dps == expected, day
        assert snapshot.fiscal_year == 2025, day

    # 对照组：中期分红除息满一年后，滚动口径从 1.16071 掉到 0.89151（-23%）
    assert rolling_365(HAIER, "2026-09-11") == pytest.approx(0.2692 + 0.89151)
    assert rolling_365(HAIER, "2026-11-08") == pytest.approx(0.89151)


def test_fiscal_caliber_switches_only_when_new_annual_is_implemented():
    """新财年年度分红实施后才切换财年，切换时不留空档。"""
    later = HAIER + [rec("2026-12-31", "2027-03-26", "2027-07-30", 1.15)]
    assert fiscal_dividend(later, "2027-07-29").dps == pytest.approx(1.16071)
    assert fiscal_dividend(later, "2027-07-29").fiscal_year == 2025
    updated = fiscal_dividend(later, "2027-07-30")
    assert updated.dps == pytest.approx(1.15)
    assert updated.fiscal_year == 2026


def test_fiscal_caliber_expires_when_no_annual_cycle_resolves():
    """多年没有新的年度分红实施时失效，避免用陈旧分红长期充数。"""
    snapshot = fiscal_dividend(HAIER, "2029-01-01")
    assert snapshot.dps is None
    assert "过期" in snapshot.note


def test_caliber_line_reports_rule_and_fiscal_year():
    line = _caliber_line(_result(_stock("海尔智家", 2025, 1.1607),
                                 _stock("招商银行", 2025, 2.0160)))
    assert CALIBER_NAME in line
    assert "FY2025" in line
    assert "非滚动 12 个月" in line
    assert "1.1607" in line and "2.0160" in line


def test_caliber_line_groups_mixed_fiscal_years():
    line = _caliber_line(_result(_stock("海尔智家", 2025, 1.1607),
                                 _stock("某落后股", 2024, 0.5)))
    assert "FY2025" in line and "FY2024" in line
    assert "海尔智家" in line and "某落后股" in line


def test_caliber_line_flags_partial_coverage():
    """部分标的缺财年口径时不能声称「本批统一」。"""
    line = _caliber_line(_result(_stock("海尔智家", 2025, 1.1607),
                                 _stock("缺数据的股", None, None)))
    assert "本批统一 FY2025" in line
    assert "覆盖 1/2 只" in line


def test_caliber_line_is_empty_without_fiscal_data():
    """手工覆盖或数据缺失时不谎报口径。"""
    assert _caliber_line(_result(_stock("海尔智家", None, None))) == ""
    assert _caliber_line(_result(_stock("海尔智家", 2025, 1.1607, overridden=True))) == ""


def test_console_table_shows_caliber_line():
    text = render_table(_result(_stock("海尔智家", 2025, 1.1607)))
    assert "股息率口径" in text


def test_yield_and_snapshot_share_one_dividend_fetch(monkeypatch):
    """get_yield 与 get_dividend_snapshot 共用缓存，不重复拉分红明细。"""
    calls: list[str] = []
    monkeypatch.setattr("dividend_grid.datasource.fetch_dividend_records",
                        lambda code: calls.append(code) or HAIER)
    source = AkShareDataSource()
    monkeypatch.setattr(source, "_fetch", lambda code: {"现价": "21.03"})

    yield_pct, note = source.get_yield("sh600690")
    snapshot = source.get_dividend_snapshot("sh600690")

    assert calls == ["sh600690"]
    assert yield_pct == pytest.approx(1.16071 / 21.03 * 100, rel=1e-6)
    assert CALIBER_NAME in note
    assert snapshot.fiscal_year == 2025


def _stock(name, year, dps, **kw):
    return Stock(code="sh600690", name=name, dividend_yield=5.5,
                 dividend_fiscal_year=year, dividend_dps=dps, **kw)


def _result(*stocks):
    return PortfolioResult(decisions=[
        Decision(s, 1.0, 1.0, "HOLD", 0.0, "持有带·维持", 1.0) for s in stocks])
