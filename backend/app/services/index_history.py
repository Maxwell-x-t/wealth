from __future__ import annotations

import csv
import io
import json
import time
from datetime import date
from pathlib import Path
from typing import Dict, List, Tuple

import httpx

from app.services.market_data import USER_AGENT

NASDAQ_SYMBOL = "^IXIC"
SP500_SYMBOL = "^GSPC"

FRED_SERIES = {
    NASDAQ_SYMBOL: "NASDAQCOM",
}
DATAHUB_SP500_URL = "https://datahub.io/core/s-and-p-500/r/data.csv"
BUNDLED_DATA_PATH = Path(__file__).resolve().parents[2] / "data" / "index_monthly.json"

_CACHE: Dict[Tuple[str, str, str], Dict[Tuple[int, int], float]] = {}
_FULL_SERIES_CACHE: Dict[str, Dict[Tuple[int, int], float]] = {}


def iter_months(start: date, end: date) -> List[Tuple[int, int]]:
    months: List[Tuple[int, int]] = []
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        months.append((year, month))
        month += 1
        if month > 12:
            month = 1
            year += 1
    return months


def price_on_month(prices: Dict[Tuple[int, int], float], year: int, month: int) -> float | None:
    if (year, month) in prices:
        return prices[(year, month)]
    candidates = [(y, m, p) for (y, m), p in prices.items() if (y, m) < (year, month)]
    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return candidates[0][2]


def _filter_range(
    prices: Dict[Tuple[int, int], float],
    start: date,
    end: date,
) -> Dict[Tuple[int, int], float]:
    return {
        key: value
        for key, value in prices.items()
        if (key[0], key[1]) >= (start.year, start.month) and (key[0], key[1]) <= (end.year, end.month)
    }


def _parse_month_key(key: str) -> Tuple[int, int]:
    year_str, month_str = key.split("-", 1)
    return int(year_str), int(month_str)


def _load_bundled_series(symbol: str) -> Dict[Tuple[int, int], float] | None:
    if not BUNDLED_DATA_PATH.exists():
        return None
    payload = json.loads(BUNDLED_DATA_PATH.read_text(encoding="utf-8"))
    raw = payload.get(symbol)
    if not raw:
        return None
    return {_parse_month_key(key): float(value) for key, value in raw.items()}


def _fred_daily_to_monthly(series_id: str, timeout: float = 180.0) -> Dict[Tuple[int, int], float]:
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"
    headers = {"User-Agent": USER_AGENT}
    with httpx.Client(timeout=timeout, headers=headers, follow_redirects=True) as client:
        last_error: Exception | None = None
        for attempt in range(4):
            try:
                response = client.get(url)
                response.raise_for_status()
                break
            except httpx.HTTPStatusError as exc:
                last_error = exc
                if exc.response.status_code == 429 and attempt < 3:
                    time.sleep(1.2 * (attempt + 1))
                    continue
                raise
        else:
            if last_error:
                raise last_error

    rows = list(csv.DictReader(io.StringIO(response.text)))
    if not rows:
        raise RuntimeError(f"FRED {series_id} 无数据")

    by_month: Dict[Tuple[int, int], float] = {}
    for row in rows:
        value = row.get(series_id, ".")
        if not value or value == ".":
            continue
        obs = date.fromisoformat(row["observation_date"])
        by_month[(obs.year, obs.month)] = float(value)
    return by_month


def _fetch_datahub_sp500_monthly(timeout: float = 180.0) -> Dict[Tuple[int, int], float]:
    headers = {"User-Agent": USER_AGENT}
    with httpx.Client(timeout=timeout, headers=headers, follow_redirects=True) as client:
        response = client.get(DATAHUB_SP500_URL)
        response.raise_for_status()

    rows = list(csv.DictReader(io.StringIO(response.text)))
    by_month: Dict[Tuple[int, int], float] = {}
    for row in rows:
        price = row.get("SP500")
        if not price:
            continue
        obs = date.fromisoformat(row["Date"][:10])
        by_month[(obs.year, obs.month)] = float(price)
    if not by_month:
        raise RuntimeError("标普 500 历史数据为空")
    return by_month


def _load_full_series(symbol: str, timeout: float = 180.0) -> Dict[Tuple[int, int], float]:
    bundled = _load_bundled_series(symbol)
    if bundled:
        return bundled

    if symbol == SP500_SYMBOL:
        return _fetch_datahub_sp500_monthly(timeout=timeout)
    if symbol == NASDAQ_SYMBOL:
        series_id = FRED_SERIES[symbol]
        return _fred_daily_to_monthly(series_id, timeout=timeout)
    raise ValueError(f"不支持的指数: {symbol}")


def _get_full_series(symbol: str, timeout: float = 180.0) -> Dict[Tuple[int, int], float]:
    if symbol in _FULL_SERIES_CACHE:
        return _FULL_SERIES_CACHE[symbol]
    full = _load_full_series(symbol, timeout=timeout)
    _FULL_SERIES_CACHE[symbol] = full
    return full


def fetch_monthly_closes(symbol: str, start: date, end: date, timeout: float = 180.0) -> Dict[Tuple[int, int], float]:
    """拉取月 K 收盘价。优先读本地 data/index_monthly.json，否则联网拉取。"""
    cache_key = (symbol, start.isoformat(), end.isoformat())
    if cache_key in _CACHE:
        return _CACHE[cache_key]

    full = _get_full_series(symbol, timeout=timeout)
    filtered = _filter_range(full, start, end)
    if not filtered:
        raise RuntimeError(f"{symbol} 在 {start} ~ {end} 区间无可用数据")

    _CACHE[cache_key] = filtered
    return filtered
