"""财报披露日期解析：上游 publish_date 对较早报告期会多算一年。"""

from datetime import datetime, timezone

from dividend_grid.backtest_data import parse_financials, published_date


def stamp(year, month, day):
    return datetime(year, month, day, tzinfo=timezone.utc).timestamp()


def report(publish_date, update_time=None, eps="1.25"):
    payload = {"data": [{"item_field": "BASICEPS", "item_value": eps},
                        {"item_field": "PARENETP", "item_value": "788192557.44"}]}
    payload["publish_date"] = publish_date
    if update_time is not None:
        payload["update_time"] = update_time
    return payload


def test_update_time_wins_when_publish_date_is_a_year_late():
    # 实测案例：2024 年报被上游标成 2026-03-20，真实披露时间是 2025-03-19。
    assert published_date(report("20260320", stamp(2025, 3, 19))) == "2025-03-19"


def test_publish_date_is_used_when_update_time_missing():
    assert published_date(report("20260320")) == "2026-03-20"


def test_later_update_time_does_not_override_publish_date():
    # update_time 晚于 publish_date 时保留 publish_date，避免引入未来信息。
    assert published_date(report("20250401", stamp(2025, 6, 1))) == "2025-04-01"


def test_unparsable_update_time_falls_back_to_publish_date():
    assert published_date(report("20260320", "not-a-timestamp")) == "2026-03-20"


def test_parse_financials_uses_update_time_and_filters_future_reports():
    payload = {"result": {"data": {"report_list": {
        "20241231": report("20260320", stamp(2025, 3, 19)),
        "20250930": report("20251025", stamp(2025, 10, 24), eps="1.08"),
        "20191231": report("20200401", stamp(2019, 12, 1), eps="0.50"),
    }}}}
    rows = parse_financials("sh600750", payload)
    # 披露日早于报告期结束的记录必须丢弃，并按披露日排序。
    assert [row["period"] for row in rows] == ["2024-12-31", "2025-09-30"]
    assert rows[0]["published"] == "2025-03-19"
    assert rows[1]["published"] == "2025-10-24"
    assert rows[0]["eps_ytd"] == 1.25


# --- 日线坏点剔除 ---------------------------------------------------------

from dividend_grid.backtest_data import (drop_isolated_spikes, etf_dividend_url,
                                         parse_prices)


def bar(day, close, open_=None, high=None, low=None, volume=10000):
    open_ = close if open_ is None else open_
    return {"date": day, "open": open_, "close": close,
            "high": high if high is not None else close * 1.01,
            "low": low if low is not None else close * 0.99, "volume": volume}


def test_isolated_downward_spike_is_dropped():
    # 实测案例：sh510880 的 2008-01-02 被上游记成 1.467，前后是 4.637 / 4.794。
    rows = [bar("2007-12-28", 4.637), bar("2008-01-02", 1.467), bar("2008-01-03", 4.794)]
    kept, dropped = drop_isolated_spikes(rows)
    assert [row["date"] for row in dropped] == ["2008-01-02"]
    assert [row["date"] for row in kept] == ["2007-12-28", "2008-01-03"]


def test_isolated_upward_spike_is_dropped():
    rows = [bar("2010-01-04", 10.0), bar("2010-01-05", 40.0), bar("2010-01-06", 10.2)]
    kept, dropped = drop_isolated_spikes(rows)
    assert [row["date"] for row in dropped] == ["2010-01-05"]
    assert len(kept) == 2


def test_dividend_ex_date_gap_is_not_dropped():
    # 510880 在 2009-03-24 每份分红 1.0 元，价格从 4.5 掉到 1.5：单边偏离，不是坏点。
    rows = [bar("2009-03-23", 4.5), bar("2009-03-24", 1.5), bar("2009-03-25", 1.52)]
    kept, dropped = drop_isolated_spikes(rows)
    assert dropped == []
    assert len(kept) == 3


def test_smooth_and_falling_series_are_untouched():
    rows = [bar(f"2008-10-{day:02d}", 10.0 - day * 0.1) for day in range(1, 21)]
    kept, dropped = drop_isolated_spikes(rows)
    assert dropped == []
    assert len(kept) == 20


def test_first_and_last_bars_are_never_dropped():
    rows = [bar("2008-01-02", 1.467), bar("2008-01-03", 4.794)]
    kept, dropped = drop_isolated_spikes(rows)
    assert dropped == []
    assert len(kept) == 2


def test_parse_prices_filters_window_and_drops_spikes():
    snapshot = {"params": {"param": "sh510880,day,2007-01-01,2008-12-31,640,"},
                "response": {"data": {"sh510880": {"day": [
                    ["2006-12-29", "1.0", "1.0", "1.0", "1.0", "1"],
                    ["2007-12-28", "4.691", "4.637", "4.719", "4.620", "313962"],
                    ["2008-01-02", "1.481", "1.467", "1.500", "1.465", "335567"],
                    ["2008-01-03", "4.630", "4.794", "4.801", "4.610", "533222"],
                ]}}}}
    rows, dropped = parse_prices("sh510880", [snapshot], "2007-01-01", "2008-12-31")
    assert [row["date"] for row in rows] == ["2007-12-28", "2008-01-03"]
    assert [row["date"] for row in dropped] == ["2008-01-02"]


def test_parse_prices_rejects_nonpositive_prices():
    snapshot = {"params": {"param": "sh600036,day,2007-01-01,2008-12-31,640,"},
                "response": {"data": {"sh600036": {"day": [
                    ["2007-01-04", "0", "0", "0", "0", "100"]]}}}}
    import pytest
    with pytest.raises(ValueError, match="Invalid OHLC data"):
        parse_prices("sh600036", [snapshot], "2007-01-01", "2008-12-31")


def test_etf_dividend_url_accepts_prefixed_and_bare_codes():
    assert etf_dividend_url("sh510880").endswith("/fhsp_510880.html")
    assert etf_dividend_url("512890").endswith("/fhsp_512890.html")
