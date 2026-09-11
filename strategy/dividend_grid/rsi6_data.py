"""512890 周线数据读取。"""

from __future__ import annotations

from datetime import date, timedelta
import math
from typing import Any, Iterable
import requests

from .rsi6 import WeeklyBar


def _value(row: Any, *names: str) -> Any:
    for name in names:
        if isinstance(row, dict) and name in row:
            return row[name]
        try:
            return row[name]
        except (KeyError, IndexError, TypeError):
            continue
    raise KeyError(f"行情数据缺少字段: {names}")


def parse_weekly_bars(rows: Iterable[Any]) -> list[WeeklyBar]:
    bars: list[WeeklyBar] = []
    for row in rows:
        raw_date = _value(row, "日期", "date", "Date")
        if hasattr(raw_date, "date"):
            raw_date = raw_date.date()
        if isinstance(raw_date, date):
            bar_date = raw_date
        else:
            bar_date = date.fromisoformat(str(raw_date)[:10])
        bars.append(
            WeeklyBar(
                date=bar_date,
                open=float(_value(row, "开盘", "open", "Open")),
                close=float(_value(row, "收盘", "close", "Close")),
            )
        )
    bars.sort(key=lambda item: item.date)
    if len({bar.date for bar in bars}) != len(bars):
        raise ValueError("行情日期重复")
    if any(not math.isfinite(value) or value <= 0
           for bar in bars for value in (bar.open, bar.close)):
        raise ValueError("行情价格必须是正的有限数")
    return bars


def aggregate_daily_bars(rows: Iterable[Any]) -> list[WeeklyBar]:
    """将日 K 聚合成周 K，保留当前尚未结束的周。"""

    daily = parse_weekly_bars(rows)
    grouped: dict[date, list[WeeklyBar]] = {}
    for bar in daily:
        monday = bar.date - timedelta(days=bar.date.weekday())
        grouped.setdefault(monday, []).append(bar)
    return [
        WeeklyBar(date=monday, open=items[0].open, close=items[-1].close)
        for monday, items in sorted(grouped.items())
    ]


def adjusted_weekly_bars(adjusted_rows: Iterable[Any], raw_rows: Iterable[Any]) -> list[WeeklyBar]:
    """对齐复权序列与最新交易价格，并保留原始周收盘展示值。"""
    adjusted = parse_weekly_bars(adjusted_rows)
    raw = parse_weekly_bars(raw_rows)
    if not adjusted or not raw or adjusted[-1].date != raw[-1].date:
        raise ValueError("复权与原始行情日期不一致，停止计算触发价")
    # Providers can return different history lengths for the same requested limit.
    # Trim only the older prefix; missing recent sessions or interior gaps are errors.
    start = max(adjusted[0].date, raw[0].date)
    adjusted = [bar for bar in adjusted if bar.date >= start]
    raw = [bar for bar in raw if bar.date >= start]
    if [bar.date for bar in adjusted] != [bar.date for bar in raw]:
        raise ValueError("复权与原始行情日期不一致，停止计算触发价")
    scale = raw[-1].close / adjusted[-1].close
    grouped: dict[date, list[WeeklyBar]] = {}
    for bar, original in zip(adjusted, raw):
        monday = bar.date - timedelta(days=bar.date.weekday())
        grouped.setdefault(monday, []).append(WeeklyBar(
            date=bar.date, open=bar.open * scale, close=bar.close * scale,
            raw_close=original.close))
    return [WeeklyBar(date=monday, open=items[0].open, close=items[-1].close,
                      raw_close=items[-1].raw_close)
            for monday, items in sorted(grouped.items())]


def _tencent_daily_rows(code: str, adjust: str) -> list[dict]:
    response = requests.get(
        "https://proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get",
        params={"param": f"{code},day,,,640,{adjust}"}, timeout=15)
    response.raise_for_status()
    payload = response.json()["data"][code]
    key = "qfqday" if adjust == "qfq" else "day"
    if not payload.get(key):
        raise ValueError(f"腾讯未返回明确的 {key} 序列")
    return [{"date": row[0], "open": row[1], "close": row[2]} for row in payload[key]]


def fetch_weekly_bars(
    code: str = "512890",
    *,
    start_date: str = "20180101",
    end_date: str | None = None,
) -> list[WeeklyBar]:
    """用复权日 K 聚合周 K；不退回未复权序列。"""

    import akshare as ak

    end = end_date or (date.today() + timedelta(days=1)).strftime("%Y%m%d")
    symbol = code.removeprefix("sh").removeprefix("sz")
    try:
        adjusted = ak.fund_etf_hist_em(
            symbol=symbol,
            period="daily",
            start_date=start_date,
            end_date=end,
            adjust="qfq",
        )
        raw = ak.fund_etf_hist_em(symbol=symbol, period="daily", start_date=start_date,
                                  end_date=end, adjust="")
        bars = adjusted_weekly_bars(adjusted.to_dict("records"), raw.to_dict("records"))
    except Exception as eastmoney_error:  # noqa: BLE001
        try:
            symbol_with_market = "sh" + symbol
            adjusted_rows = _tencent_daily_rows(symbol_with_market, "qfq")
            raw_rows = _tencent_daily_rows(symbol_with_market, "")
            start = date.fromisoformat(f"{start_date[:4]}-{start_date[4:6]}-{start_date[6:8]}")
            stop = date.fromisoformat(f"{end[:4]}-{end[4:6]}-{end[6:8]}")
            adjusted_rows = [r for r in adjusted_rows if start <= date.fromisoformat(r["date"]) <= stop]
            raw_rows = [r for r in raw_rows if start <= date.fromisoformat(r["date"]) <= stop]
            bars = adjusted_weekly_bars(adjusted_rows, raw_rows)
        except Exception as tencent_error:  # noqa: BLE001
            raise RuntimeError(
                f"{code} 复权行情不可用，暂停 RSI 提醒："
                f"Eastmoney: {eastmoney_error}; Tencent: {tencent_error}") from tencent_error
    if len(bars) < 7:
        raise RuntimeError(f"{code} 周线数据不足，无法计算 RSI(6)")
    return bars
