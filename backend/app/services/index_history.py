from __future__ import annotations

import calendar
import csv
import io
import json
import time
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Tuple

import httpx

from app.services.market_data import USER_AGENT

NASDAQ_SYMBOL = "^IXIC"
SP500_SYMBOL = "^GSPC"
VIX_SYMBOL = "^VIX"

FRED_SERIES = {
    NASDAQ_SYMBOL: "NASDAQCOM",
    VIX_SYMBOL: "VIXCLS",
}
DATAHUB_SP500_URL = "https://datahub.io/core/s-and-p-500/r/data.csv"
CBOE_VIX_HISTORY_URL = "https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv"
EASTMONEY_KLINE_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
# 东财无 ^IXIC 综合指数长历史，用纳斯达克100(NDX)对齐大陆纳指 ETF
EASTMONEY_DAILY_SECID = {
    NASDAQ_SYMBOL: "100.NDX",
    SP500_SYMBOL: "100.SPX",
}
BUNDLED_DATA_PATH = Path(__file__).resolve().parents[2] / "data" / "index_monthly.json"
BUNDLED_DAILY_PATH = Path(__file__).resolve().parents[2] / "data" / "index_daily.json"

_CACHE: Dict[Tuple[str, str, str], Dict[Tuple[int, int], float]] = {}
_FULL_SERIES_CACHE: Dict[str, Dict[Tuple[int, int], float]] = {}
_DAILY_CACHE: Dict[Tuple[str, str, str], Dict[date, float]] = {}
_DAILY_FULL_CACHE: Dict[str, Dict[date, float]] = {}


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


def _median_gap_days(series: Dict[date, float]) -> float:
    dates = sorted(series)
    if len(dates) < 3:
        return 30.0
    sample = dates[: min(len(dates), 120)]
    gaps = sorted((sample[i] - sample[i - 1]).days for i in range(1, len(sample)))
    return float(gaps[len(gaps) // 2])


def _is_dense_daily(series: Dict[date, float], min_points: int = 200) -> bool:
    return len(series) >= min_points and _median_gap_days(series) <= 3


def _load_bundled_daily_series(symbol: str) -> Dict[date, float] | None:
    if not BUNDLED_DAILY_PATH.exists():
        return None
    payload = json.loads(BUNDLED_DAILY_PATH.read_text(encoding="utf-8"))
    raw = payload.get(symbol)
    if not raw:
        return None
    return {date.fromisoformat(str(k)[:10]): float(v) for k, v in raw.items()}


def _save_bundled_daily_series(symbol: str, series: Dict[date, float]) -> None:
    payload: Dict[str, Dict[str, float]] = {}
    if BUNDLED_DAILY_PATH.exists():
        try:
            payload = json.loads(BUNDLED_DAILY_PATH.read_text(encoding="utf-8"))
        except Exception:
            payload = {}
    payload[symbol] = {
        day.isoformat(): round(float(price), 6)
        for day, price in sorted(series.items())
    }
    BUNDLED_DAILY_PATH.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    tmp = BUNDLED_DAILY_PATH.with_suffix(".json.tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(BUNDLED_DAILY_PATH)


def _fetch_fred_daily_pandas(series_id: str) -> Dict[date, float]:
    """经 pandas_datareader 拉 FRED 日频（比直接 CSV 更稳定）。"""
    import pandas_datareader.data as web

    frame = web.DataReader(series_id, "fred", start="1970-01-01")
    if frame is None or frame.empty:
        raise RuntimeError(f"FRED {series_id} 无数据")
    column = frame.columns[0]
    series = frame[column].dropna()
    by_day = {idx.date(): float(value) for idx, value in series.items()}
    if not _is_dense_daily(by_day):
        raise RuntimeError(f"FRED {series_id} 日频密度不足")
    return by_day


def _backfill_daily_from_monthly(
    daily: Dict[date, float],
    monthly: Dict[Tuple[int, int], float],
) -> Dict[date, float]:
    """用月线把日频向前补齐（FRED 标普仅约 10 年时使用）。"""
    if not monthly:
        return daily
    out = dict(daily)
    first_daily = min(daily) if daily else date.max
    for (year, month), price in monthly.items():
        last_day = calendar.monthrange(year, month)[1]
        day = date(year, month, 1)
        month_end = date(year, month, last_day)
        while day <= month_end:
            if day < first_daily and day.weekday() < 5:
                out.setdefault(day, float(price))
            day += timedelta(days=1)
    return out


def _fetch_eastmoney_daily(secid: str, timeout: float = 20.0) -> Dict[date, float]:
    headers = {
        "User-Agent": USER_AGENT,
        "Referer": "https://quote.eastmoney.com/",
    }
    params = {
        "secid": secid,
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
        "klt": "101",
        "fqt": "0",
        "beg": "19900101",
        "end": "20500101",
        "lmt": "1000000",
    }
    last_error: Exception | None = None
    payload = None
    with httpx.Client(timeout=timeout, headers=headers, follow_redirects=True) as client:
        for attempt in range(3):
            try:
                response = client.get(EASTMONEY_KLINE_URL, params=params)
                response.raise_for_status()
                payload = response.json()
                break
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                time.sleep(0.6 * (attempt + 1))
        else:
            raise RuntimeError(f"东财 {secid} 拉取失败: {last_error}") from last_error

    klines = ((payload.get("data") or {}).get("klines")) or []
    by_day: Dict[date, float] = {}
    for row in klines:
        parts = str(row).split(",")
        if len(parts) < 3:
            continue
        by_day[date.fromisoformat(parts[0][:10])] = float(parts[2])
    if not _is_dense_daily(by_day):
        raise RuntimeError(f"东财 {secid} 日频数据不足或不连续")
    return by_day


def _monthly_to_pseudo_daily(monthly: Dict[Tuple[int, int], float]) -> Dict[date, float]:
    """将月线序列展开为每月最后一个自然日，供日/周频回测离线兜底。"""
    by_day: Dict[date, float] = {}
    for (year, month), price in monthly.items():
        last_day = calendar.monthrange(year, month)[1]
        by_day[date(year, month, last_day)] = price
    return by_day


def _parse_cboe_vix_date(raw: str) -> date:
    raw = raw.strip()
    try:
        return datetime.strptime(raw, "%m/%d/%Y").date()
    except ValueError:
        return date.fromisoformat(raw[:10])


def _fetch_cboe_vix_daily(timeout: float = 20.0) -> Dict[date, float]:
    headers = {"User-Agent": USER_AGENT}
    with httpx.Client(timeout=timeout, headers=headers, follow_redirects=True) as client:
        response = client.get(CBOE_VIX_HISTORY_URL)
        response.raise_for_status()

    rows = list(csv.DictReader(io.StringIO(response.text)))
    by_day: Dict[date, float] = {}
    for row in rows:
        raw = row.get("DATE") or row.get("Date")
        close = row.get("CLOSE") or row.get("Close")
        if not raw or not close:
            continue
        by_day[_parse_cboe_vix_date(raw)] = float(close)
    if not by_day:
        raise RuntimeError("CBOE VIX 历史数据为空")
    return by_day


def _fetch_cboe_vix_monthly(timeout: float = 20.0) -> Dict[Tuple[int, int], float]:
    by_day = _fetch_cboe_vix_daily(timeout=timeout)
    by_month: Dict[Tuple[int, int], float] = {}
    for obs, price in sorted(by_day.items()):
        by_month[(obs.year, obs.month)] = price
    return by_month


def _fred_daily_series(series_id: str, timeout: float = 180.0) -> Dict[date, float]:
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

    by_day: Dict[date, float] = {}
    for row in rows:
        value = row.get(series_id, ".")
        if not value or value == ".":
            continue
        obs = date.fromisoformat(row["observation_date"])
        by_day[obs] = float(value)
    return by_day


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


def _fetch_datahub_sp500_daily(timeout: float = 180.0) -> Dict[date, float]:
    headers = {"User-Agent": USER_AGENT}
    with httpx.Client(timeout=timeout, headers=headers, follow_redirects=True) as client:
        response = client.get(DATAHUB_SP500_URL)
        response.raise_for_status()

    rows = list(csv.DictReader(io.StringIO(response.text)))
    by_day: Dict[date, float] = {}
    for row in rows:
        price = row.get("SP500")
        if not price:
            continue
        obs = date.fromisoformat(row["Date"][:10])
        by_day[obs] = float(price)
    if not by_day:
        raise RuntimeError("标普 500 历史数据为空")
    return by_day


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


def _load_daily_full_series(symbol: str, timeout: float = 12.0) -> Dict[date, float]:
    """加载真日频：本地日线缓存 → 东财/CBOE/FRED → 月线兜底。"""
    local_daily = _load_bundled_daily_series(symbol)
    if local_daily and _is_dense_daily(local_daily):
        return local_daily

    errors: List[str] = []

    if symbol in EASTMONEY_DAILY_SECID:
        try:
            series = _fetch_eastmoney_daily(EASTMONEY_DAILY_SECID[symbol], timeout=min(timeout, 12.0))
            _save_bundled_daily_series(symbol, series)
            return series
        except Exception as exc:  # noqa: BLE001
            errors.append(f"eastmoney: {exc}")

    if symbol == VIX_SYMBOL:
        try:
            series = _fetch_cboe_vix_daily(timeout=timeout)
            _save_bundled_daily_series(symbol, series)
            return series
        except Exception as exc:  # noqa: BLE001
            errors.append(f"cboe: {exc}")

    fred_id = FRED_SERIES.get(symbol)
    if symbol == SP500_SYMBOL:
        fred_id = "SP500"
    if fred_id:
        try:
            series = _fetch_fred_daily_pandas(fred_id)
            if symbol == SP500_SYMBOL:
                bundled = _load_bundled_series(symbol)
                if bundled:
                    series = _backfill_daily_from_monthly(series, bundled)
            if not _is_dense_daily(series):
                raise RuntimeError("FRED 日频密度不足")
            _save_bundled_daily_series(symbol, series)
            return series
        except Exception as exc:  # noqa: BLE001
            errors.append(f"fred: {exc}")

    bundled = _load_bundled_series(symbol)
    if bundled:
        return _monthly_to_pseudo_daily(bundled)
    raise RuntimeError(f"{symbol} 日频拉取失败且无本地月线兜底: {' | '.join(errors)}")


def _get_daily_full_series(symbol: str, timeout: float = 12.0) -> Dict[date, float]:
    if symbol in _DAILY_FULL_CACHE:
        return _DAILY_FULL_CACHE[symbol]
    full = _load_daily_full_series(symbol, timeout=timeout)
    _DAILY_FULL_CACHE[symbol] = full
    return full


def clear_daily_series_cache() -> None:
    _DAILY_FULL_CACHE.clear()
    _DAILY_CACHE.clear()


def fetch_remote_daily_series(symbol: str, timeout: float = 20.0) -> Dict[date, float]:
    """仅远端日频（用于手动刷新本地日线缓存）。"""
    errors: List[str] = []

    if symbol in EASTMONEY_DAILY_SECID:
        try:
            series = _fetch_eastmoney_daily(EASTMONEY_DAILY_SECID[symbol], timeout=timeout)
            _save_bundled_daily_series(symbol, series)
            return series
        except Exception as exc:  # noqa: BLE001
            errors.append(f"eastmoney: {exc}")

    if symbol == VIX_SYMBOL:
        try:
            series = _fetch_cboe_vix_daily(timeout=timeout)
            _save_bundled_daily_series(symbol, series)
            return series
        except Exception as exc:  # noqa: BLE001
            errors.append(f"cboe: {exc}")

    fred_id = FRED_SERIES.get(symbol)
    if symbol == SP500_SYMBOL:
        fred_id = "SP500"
    if fred_id:
        try:
            series = _fetch_fred_daily_pandas(fred_id)
            if symbol == SP500_SYMBOL:
                bundled = _load_bundled_series(symbol)
                if bundled:
                    series = _backfill_daily_from_monthly(series, bundled)
            if not _is_dense_daily(series):
                raise RuntimeError("FRED 日频密度不足")
            _save_bundled_daily_series(symbol, series)
            return series
        except Exception as exc:  # noqa: BLE001
            errors.append(f"fred: {exc}")

    raise RuntimeError(f"{symbol} 远端日频失败: {' | '.join(errors) or '无可用数据源'}")


def _filter_daily_range(series: Dict[date, float], start: date, end: date) -> Dict[date, float]:
    return {d: p for d, p in series.items() if start <= d <= end}


def price_on_date(prices: Dict[date, float], as_of: date) -> float | None:
    if as_of in prices:
        return prices[as_of]
    candidates = [(d, p) for d, p in prices.items() if d <= as_of]
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1]


def weekly_end_dates(daily: Dict[date, float], start: date, end: date) -> List[date]:
    """按 ISO 周取每周最后一个有数据的交易日。"""
    by_week: Dict[Tuple[int, int], List[date]] = defaultdict(list)
    for d in daily:
        if start <= d <= end:
            by_week[d.isocalendar()[:2]].append(d)
    return sorted(max(days) for days in by_week.values() if days)


def to_weekly_closes(daily: Dict[date, float]) -> Dict[date, float]:
    by_week: Dict[Tuple[int, int], List[Tuple[date, float]]] = defaultdict(list)
    for d, price in daily.items():
        by_week[d.isocalendar()[:2]].append((d, price))
    return {max(items, key=lambda x: x[0])[0]: max(items, key=lambda x: x[0])[1] for items in by_week.values()}


def fetch_daily_closes(symbol: str, start: date, end: date, timeout: float = 12.0) -> Dict[date, float]:
    cache_key = (symbol, start.isoformat(), end.isoformat())
    if cache_key in _DAILY_CACHE:
        return _DAILY_CACHE[cache_key]
    full = _get_daily_full_series(symbol, timeout=timeout)
    filtered = _filter_daily_range(full, start, end)
    if not filtered:
        raise RuntimeError(f"{symbol} 在 {start} ~ {end} 区间无可用日频数据")
    _DAILY_CACHE[cache_key] = filtered
    return filtered


def get_daily_cache_status() -> Dict[str, dict]:
    status: Dict[str, dict] = {}
    for symbol in (NASDAQ_SYMBOL, SP500_SYMBOL, VIX_SYMBOL):
        series = _load_bundled_daily_series(symbol) or {}
        latest = max(series).isoformat() if series else None
        status[symbol] = {
            "points": len(series),
            "latest_day": latest,
            "dense_daily": _is_dense_daily(series) if series else False,
        }
    return status


def fetch_remote_full_series(symbol: str, timeout: float = 30.0) -> Dict[Tuple[int, int], float]:
    """仅走远端数据源（不读本地 bundled）。用于手动更新本地快照。"""
    if symbol == VIX_SYMBOL:
        try:
            return _fetch_cboe_vix_monthly(timeout=timeout)
        except Exception:
            return _fred_daily_to_monthly(FRED_SERIES[VIX_SYMBOL], timeout=timeout)
    if symbol == SP500_SYMBOL:
        return _fetch_datahub_sp500_monthly(timeout=timeout)
    if symbol in FRED_SERIES:
        return _fred_daily_to_monthly(FRED_SERIES[symbol], timeout=timeout)
    raise ValueError(f"不支持的指数: {symbol}")


def latest_bundled_price(symbol: str) -> float | None:
    """读取本地月线快照的最新收盘价，供盘中信号兜底。"""
    series = _load_bundled_series(symbol)
    if not series:
        return None
    latest_key = max(series)
    return float(series[latest_key])


def _load_full_series(symbol: str, timeout: float = 180.0) -> Dict[Tuple[int, int], float]:
    bundled = _load_bundled_series(symbol)
    if bundled:
        return bundled

    if symbol == SP500_SYMBOL:
        return _fetch_datahub_sp500_monthly(timeout=timeout)
    if symbol in FRED_SERIES:
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
