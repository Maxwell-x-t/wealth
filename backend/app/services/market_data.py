from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional, Tuple

import httpx

from app.models.models import Instrument

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


@dataclass
class QuoteResult:
    price: float
    snapshot_date: date
    iopv: Optional[float] = None
    premium_rate: Optional[float] = None


def _cn_market_prefix(code: str) -> str:
    if code.startswith(("5", "6", "9")):
        return "sh"
    return "sz"


def _cn_secid(code: str) -> str:
    if code.startswith(("5", "6", "9")):
        return f"1.{code}"
    return f"0.{code}"


def _optional_float(value) -> Optional[float]:
    if value is None or value in ("-", ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:  # NaN
        return None
    return number


def _premium_from_price_iopv(price: float, iopv: Optional[float]) -> Optional[float]:
    if iopv is None or iopv <= 0:
        return None
    return round((price - iopv) / iopv * 100, 2)


def fetch_cn_price(code: str, timeout: float = 10.0) -> QuoteResult:
    """拉取大陆 ETF/股票最新价；ETF 优先带 IOPV/溢价率。"""
    errors = []
    try:
        return _fetch_cn_eastmoney_ulist(code, timeout)
    except Exception as exc:  # noqa: BLE001
        errors.append(f"eastmoney-ulist: {exc}")

    try:
        return _fetch_cn_eastmoney(code, timeout)
    except Exception as exc:  # noqa: BLE001
        errors.append(f"eastmoney: {exc}")

    try:
        return _fetch_cn_sina(code, timeout)
    except Exception as exc:  # noqa: BLE001
        errors.append(f"sina: {exc}")

    raise RuntimeError("；".join(errors))


def _fetch_cn_eastmoney_ulist(code: str, timeout: float) -> QuoteResult:
    """东财 ulist：现价 + IOPV(f441)，溢价率 = (现价-IOPV)/IOPV。"""
    url = "https://push2.eastmoney.com/api/qt/ulist.np/get"
    params = {
        "secids": _cn_secid(code),
        "fields": "f2,f12,f14,f441,f402",
        "invt": "2",
        "fltt": "2",
    }
    headers = {"User-Agent": USER_AGENT, "Referer": "https://quote.eastmoney.com/"}
    with httpx.Client(timeout=timeout, headers=headers) as client:
        response = client.get(url, params=params)
        response.raise_for_status()
        payload = response.json()

    rows = ((payload.get("data") or {}).get("diff")) or []
    if not rows:
        raise RuntimeError("无行情数据")
    row = rows[0]
    price = _optional_float(row.get("f2"))
    if price is None or price <= 0:
        raise RuntimeError("无最新价")

    iopv = _optional_float(row.get("f441"))
    if iopv is not None and iopv <= 0:
        iopv = None
    premium = _premium_from_price_iopv(price, iopv)
    if premium is None:
        # f402 为折价率（正=折价），取负得到溢价率
        discount = _optional_float(row.get("f402"))
        if discount is not None:
            premium = round(-discount, 2)

    return QuoteResult(price=price, snapshot_date=date.today(), iopv=iopv, premium_rate=premium)


def _fetch_cn_eastmoney(code: str, timeout: float) -> QuoteResult:
    url = "https://push2.eastmoney.com/api/qt/stock/get"
    params = {
        "secid": _cn_secid(code),
        "fields": "f43,f57,f58,f86",
        "invt": "2",
        "fltt": "2",
    }
    headers = {"User-Agent": USER_AGENT, "Referer": "https://quote.eastmoney.com/"}
    with httpx.Client(timeout=timeout, headers=headers) as client:
        response = client.get(url, params=params)
        response.raise_for_status()
        payload = response.json()

    data = payload.get("data") or {}
    price = data.get("f43")
    if price is None or price in ("-", ""):
        raise RuntimeError("无最新价")

    price_value = float(price)
    if price_value > 10000:
        price_value = price_value / 100.0

    ts = data.get("f86")
    snapshot = datetime.fromtimestamp(int(ts)).date() if ts else date.today()
    return QuoteResult(price=price_value, snapshot_date=snapshot)


def _fetch_cn_sina(code: str, timeout: float) -> QuoteResult:
    symbol = f"{_cn_market_prefix(code)}{code}"
    url = f"https://hq.sinajs.cn/list={symbol}"
    headers = {"User-Agent": USER_AGENT, "Referer": "https://finance.sina.com.cn"}
    with httpx.Client(timeout=timeout, headers=headers) as client:
        response = client.get(url)
        response.raise_for_status()
        text = response.text

    if '="' not in text:
        raise RuntimeError("响应格式异常")
    body = text.split('="', 1)[1].rsplit('"', 1)[0]
    if not body:
        raise RuntimeError("无行情数据")
    parts = body.split(",")
    if len(parts) < 4:
        raise RuntimeError("字段不足")

    price_value = float(parts[3])
    if price_value <= 0:
        price_value = float(parts[2])
    if price_value <= 0:
        raise RuntimeError("价格无效")

    snapshot = date.today()
    if len(parts) >= 31 and parts[30]:
        try:
            snapshot = datetime.strptime(parts[30], "%Y-%m-%d").date()
        except ValueError:
            pass
    return QuoteResult(price=price_value, snapshot_date=snapshot)


def fetch_us_price(code: str, timeout: float = 10.0) -> QuoteResult:
    """拉取美股 ETF 最新价，优先新浪美股，失败则 Yahoo。"""
    errors = []
    try:
        return _fetch_us_sina(code, timeout)
    except Exception as exc:  # noqa: BLE001
        errors.append(f"sina: {exc}")

    try:
        return _fetch_us_yahoo(code, timeout)
    except Exception as exc:  # noqa: BLE001
        errors.append(f"yahoo: {exc}")

    raise RuntimeError("；".join(errors))


def _fetch_us_sina(code: str, timeout: float) -> QuoteResult:
    symbol = f"gb_{code.lower()}"
    url = f"https://hq.sinajs.cn/list={symbol}"
    headers = {"User-Agent": USER_AGENT, "Referer": "https://finance.sina.com.cn"}
    with httpx.Client(timeout=timeout, headers=headers) as client:
        response = client.get(url)
        response.raise_for_status()
        text = response.text

    if '="' not in text:
        raise RuntimeError("响应格式异常")
    body = text.split('="', 1)[1].rsplit('"', 1)[0]
    if not body:
        raise RuntimeError("无行情数据")
    parts = body.split(",")
    if len(parts) < 2:
        raise RuntimeError("字段不足")

    price_value = float(parts[1])
    if price_value <= 0:
        raise RuntimeError("价格无效")

    snapshot = date.today()
    if len(parts) >= 4 and parts[3]:
        try:
            snapshot = datetime.strptime(parts[3][:10], "%Y-%m-%d").date()
        except ValueError:
            pass
    return QuoteResult(price=price_value, snapshot_date=snapshot)


def _fetch_us_yahoo(code: str, timeout: float) -> QuoteResult:
    url = f"https://query2.finance.yahoo.com/v8/finance/chart/{code}"
    params = {"interval": "1d", "range": "5d"}
    headers = {"User-Agent": USER_AGENT}
    with httpx.Client(timeout=timeout, headers=headers) as client:
        response = client.get(url, params=params)
        response.raise_for_status()
        payload = response.json()

    result = (payload.get("chart") or {}).get("result") or []
    if not result:
        error = (payload.get("chart") or {}).get("error")
        raise RuntimeError(error.get("description") if error else "无行情数据")

    meta = result[0].get("meta") or {}
    price = meta.get("regularMarketPrice") or meta.get("previousClose")
    if price is None:
        quotes = (result[0].get("indicators") or {}).get("quote") or []
        closes = (quotes[0] or {}).get("close") if quotes else None
        if closes:
            for value in reversed(closes):
                if value is not None:
                    price = value
                    break
    if price is None:
        raise RuntimeError("无最新价")

    ts = meta.get("regularMarketTime")
    snapshot = datetime.utcfromtimestamp(int(ts)).date() if ts else date.today()
    return QuoteResult(price=float(price), snapshot_date=snapshot)


def fetch_instrument_price(instrument: Instrument) -> QuoteResult:
    code = instrument.code.strip()
    currency = (instrument.currency or "CNY").upper()
    upper = code.upper()

    if currency == "USD" or upper.isalpha():
        return fetch_us_price(upper)

    if code.isdigit():
        return fetch_cn_price(code)

    if "." in code:
        symbol, market = code.split(".", 1)
        market = market.upper()
        if market in ("SS", "SH", "SZ"):
            return fetch_cn_price(symbol)
        return fetch_us_price(upper)

    return fetch_us_price(upper)


def resolve_source_label(instrument: Instrument) -> str:
    currency = (instrument.currency or "CNY").upper()
    code = instrument.code.strip()
    if currency == "USD" or code.isalpha():
        return "新浪美股/Yahoo"
    return "东方财富/新浪"


def fetch_usd_cny_rate(timeout: float = 10.0) -> Tuple[float, date, str]:
    """拉取美元兑人民币汇率，优先在岸，失败回退离岸。"""
    errors = []
    for symbol, label in (("fx_susdcny", "新浪在岸"), ("fx_susdcnh", "新浪离岸")):
        try:
            rate, snapshot = _fetch_sina_fx(symbol, timeout)
            return rate, snapshot, label
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{label}: {exc}")
    raise RuntimeError("；".join(errors))


def _fetch_sina_fx(symbol: str, timeout: float) -> Tuple[float, date]:
    url = f"https://hq.sinajs.cn/list={symbol}"
    headers = {"User-Agent": USER_AGENT, "Referer": "https://finance.sina.com.cn"}
    with httpx.Client(timeout=timeout, headers=headers) as client:
        response = client.get(url)
        response.raise_for_status()
        text = response.text

    if '="' not in text:
        raise RuntimeError("响应格式异常")
    body = text.split('="', 1)[1].rsplit('"', 1)[0]
    if not body:
        raise RuntimeError("无汇率数据")
    parts = body.split(",")
    if len(parts) < 2:
        raise RuntimeError("字段不足")

    # 新浪外汇：常见现价在 index 8，否则取买价 index 1
    candidates = []
    if len(parts) > 8 and parts[8]:
        candidates.append(parts[8])
    candidates.extend([parts[1], parts[2], parts[3]])
    rate = None
    for item in candidates:
        try:
            value = float(item)
        except (TypeError, ValueError):
            continue
        if 1 < value < 20:
            rate = value
            break
    if rate is None:
        raise RuntimeError("汇率无效")

    snapshot = date.today()
    for part in reversed(parts):
        part = (part or "").strip()
        if len(part) >= 10 and part[4] == "-" and part[7] == "-":
            try:
                snapshot = datetime.strptime(part[:10], "%Y-%m-%d").date()
                break
            except ValueError:
                continue
    return rate, snapshot
