"""股息率口径通用规则的回归测试。

核心不变量：股息率以完整财年为基准，确认的新方案按同周期替换，与到账节奏解耦。
典型反面场景是「某年取消中期分红」——滚动 12 个月口径会因此机械跳水（假卖出信号），
财年口径不会。这里同时锁住旧口径的跳水行为，防止未来有人改回 TTM。
"""

from datetime import date, timedelta

import pytest

from dividend_grid.datasource import AkShareDataSource
from dividend_grid.dividends import CALIBER_NAME, fiscal_dividend, parse_annual_eps
from dividend_grid.quality import QualityConfig, assess
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


PING_AN_BANK = [
    rec("2025-06-30", "2025-09-30", "2025-10-15", 0.236),
    rec("2025-12-31", "2026-06-05", "2026-06-12", 0.360),
    rec("2026-06-30", "2026-09-17", "2026-09-24", 0.249),
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


def test_fiscal_caliber_switches_when_new_annual_plan_is_confirmed():
    """新财年年度实施方案公告后切换，除息时不应再次变化。"""
    later = HAIER + [rec("2026-12-31", "2027-03-26", "2027-07-30", 1.15)]
    assert fiscal_dividend(later, "2027-03-25").dps == pytest.approx(1.16071)
    confirmed = fiscal_dividend(later, "2027-03-26")
    assert confirmed.dps == pytest.approx(1.15)
    assert confirmed.fiscal_year == 2026
    updated = fiscal_dividend(later, "2027-07-30")
    assert updated.dps == pytest.approx(1.15)
    assert updated.fiscal_year == 2026


def test_confirmed_interim_plan_replaces_previous_interim_from_announcement():
    before = fiscal_dividend(PING_AN_BANK, "2026-09-16")
    assert before.dps == pytest.approx(0.596)

    announced = fiscal_dividend(PING_AN_BANK, "2026-09-17")
    assert announced.dps == pytest.approx(0.609)
    assert announced.fiscal_year == 2025
    assert "FY2026-06-30" in announced.note

    # 估值从实施公告日起更新，不必等待除息；除息日不应再重复计入。
    assert fiscal_dividend(PING_AN_BANK, "2026-09-23").dps == pytest.approx(0.609)
    assert fiscal_dividend(PING_AN_BANK, "2026-09-24").dps == pytest.approx(0.609)


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
    assert "本批统一基准 FY2025" in line
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


def test_parse_annual_eps_keeps_year_end_basic_eps():
    years = parse_annual_eps({"result": {"data": [
        {"REPORT_DATE": "2026-06-30", "EPSJB": 0.78},
        {"REPORT_DATE": "2025-12-31", "EPSJB": 1.43},
        {"REPORT_DATE": "2024-12-31", "EPSJB": 1.25},
    ]}})
    assert years == {2025: 1.43, 2024: 1.25}


def test_jiangzhong_payout_uses_fiscal_eps_not_ttm(monkeypatch):
    """华润江中：同花顺约 96% 来自 FY2025 DPS 1.38 / 年报 EPS 1.43，不能用 TTM EPS 抬过 100%。"""
    records = [
        rec("2025-06-30", "2025-09-27", "2025-10-13", 0.50),
        rec("2025-12-31", "2026-06-13", "2026-06-22", 0.88),
        rec("2026-06-30", "2026-09-03", "2026-09-11", 0.50),
    ]
    monkeypatch.setattr("dividend_grid.datasource.fetch_dividend_records", lambda _: records)
    monkeypatch.setattr("dividend_grid.datasource.fetch_annual_eps_map", lambda _: {2025: 1.43})
    source = AkShareDataSource()
    source._cache["sh600750"] = {
        "现价": 22.26, "市盈率(TTM)": 16.16, "每股收益": 1.3774752475247525,
        "股息率(TTM)": 6.2, "股息(TTM)": 1.38012, "_source": "tencent",
    }
    metrics = source.get_quality("sh600750")
    assert metrics.dividend_per_share == pytest.approx(1.38)
    assert metrics.eps == pytest.approx(1.43)
    assert metrics.payout_ratio == pytest.approx(1.38 / 1.43 * 100)
    assert assess(metrics, QualityConfig()) == ("warn", "派息率96.50%偏高")


def _stock(name, year, dps, **kw):
    return Stock(code="sh600690", name=name, dividend_yield=5.5,
                 dividend_fiscal_year=year, dividend_dps=dps, **kw)


def _result(*stocks):
    return PortfolioResult(decisions=[
        Decision(s, 1.0, 1.0, "HOLD", 0.0, "持有带·维持", 1.0) for s in stocks])
