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


def is_cn_etf(code: str) -> bool:
    """大陆 ETF/LOF。个股的东财字段不是 IOPV，不计算溢价。"""
    normalized = code.strip()
    if len(normalized) > 2 and normalized[:2].lower() in {"sh", "sz"} and normalized[2:].isdigit():
        normalized = normalized[2:]
    return normalized.startswith(("15", "16", "50", "51", "56", "58"))


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


def _positive_float(value) -> Optional[float]:
    number = _optional_float(value)
    if number is None or number <= 0:
        return None
    return number


def _nonzero_float(value) -> Optional[float]:
    """东财未启用字段常填 0，0 视为缺失。"""
    number = _optional_float(value)
    if number is None or number == 0:
        return None
    return number


def _premium_from_price_iopv(price: float, iopv: Optional[float]) -> Optional[float]:
    if iopv is None or iopv <= 0:
        return None
    return round((price - iopv) / iopv * 100, 2)


def _etf_valuation(row: dict, price: float) -> tuple[Optional[float], Optional[float]]:
    """从东财行情行解析 IOPV / 溢价率。优先 IOPV 现算，其次 f191、f402。"""
    iopv = _positive_float(row.get("f441")) or _positive_float(row.get("f186"))
    premium = _premium_from_price_iopv(price, iopv)
    if premium is None:
        quoted = _nonzero_float(row.get("f191"))
        if quoted is not None:
            premium = round(quoted, 2)
        else:
            discount = _nonzero_float(row.get("f402"))
            if discount is not None:
                premium = round(-discount, 2)
        if iopv is None and premium is not None and premium > -100:
            iopv = round(price / (1 + premium / 100), 6)
    return iopv, premium


def apply_previous_iopv(quote: QuoteResult, previous_iopv: Optional[float]) -> QuoteResult:
    """新行情缺 IOPV 时沿用最近 IOPV，并用最新现价重算溢价。"""
    iopv = quote.iopv if quote.iopv is not None and quote.iopv > 0 else None
    if iopv is not None:
        return QuoteResult(
            price=quote.price,
            snapshot_date=quote.snapshot_date,
            iopv=iopv,
            premium_rate=_premium_from_price_iopv(quote.price, iopv),
        )
    if quote.premium_rate is not None:
        return quote
    if previous_iopv is None or previous_iopv <= 0:
        return quote
    return QuoteResult(
        price=quote.price,
        snapshot_date=quote.snapshot_date,
        iopv=previous_iopv,
        premium_rate=_premium_from_price_iopv(quote.price, previous_iopv),
    )


def _merge_cn_quotes(base: Optional[QuoteResult], extra: QuoteResult) -> QuoteResult:
    if base is None:
        return extra
    iopv = base.iopv if base.iopv is not None else extra.iopv
    premium = base.premium_rate if base.premium_rate is not None else extra.premium_rate
    if premium is None:
        premium = _premium_from_price_iopv(base.price, iopv)
    return QuoteResult(
        price=base.price,
        snapshot_date=base.snapshot_date,
        iopv=iopv,
        premium_rate=premium,
    )


def fetch_cn_price(code: str, timeout: float = 10.0) -> QuoteResult:
    """拉取大陆 ETF/股票最新价；ETF 优先带 IOPV/溢价率。"""
    errors = []
    quote = None
    try:
        quote = _fetch_cn_eastmoney_ulist(code, timeout)
    except Exception as exc:  # noqa: BLE001
        errors.append(f"eastmoney-ulist: {exc}")

    needs_valuation = is_cn_etf(code) and (
        quote is None or quote.iopv is None or quote.premium_rate is None
    )
    if quote is None or needs_valuation:
        try:
            extra = _fetch_cn_eastmoney(code, timeout)
            quote = _merge_cn_quotes(quote, extra)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"eastmoney: {exc}")

    if quote is not None:
        if not is_cn_etf(code):
            return QuoteResult(price=quote.price, snapshot_date=quote.snapshot_date)
        return quote

    try:
        return _fetch_cn_sina(code, timeout)
    except Exception as exc:  # noqa: BLE001
        errors.append(f"sina: {exc}")

    raise RuntimeError("；".join(errors))


def _fetch_cn_eastmoney_ulist(code: str, timeout: float) -> QuoteResult:
    """东财 ulist：现价 + IOPV(f441)/净值(f186)/溢价(f191)。"""
    url = "https://push2.eastmoney.com/api/qt/ulist.np/get"
    params = {
        "secids": _cn_secid(code),
        "fields": "f2,f12,f14,f186,f191,f402,f441",
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
    price = _positive_float(row.get("f2"))
    if price is None:
        raise RuntimeError("无最新价")

    iopv, premium = _etf_valuation(row, price) if is_cn_etf(code) else (None, None)
    return QuoteResult(price=price, snapshot_date=date.today(), iopv=iopv, premium_rate=premium)


def _fetch_cn_eastmoney(code: str, timeout: float) -> QuoteResult:
    url = "https://push2.eastmoney.com/api/qt/stock/get"
    params = {
        "secid": _cn_secid(code),
        "fields": "f43,f57,f58,f86,f186,f191,f402,f441",
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
    if price_value <= 0:
        raise RuntimeError("无最新价")

    ts = data.get("f86")
    snapshot = datetime.fromtimestamp(int(ts)).date() if ts else date.today()
    iopv, premium = _etf_valuation(data, price_value) if is_cn_etf(code) else (None, None)
    return QuoteResult(price=price_value, snapshot_date=snapshot, iopv=iopv, premium_rate=premium)


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


def fetch_yahoo_index_quote(symbol: str, timeout: float = 10.0) -> QuoteResult:
    """拉取 Yahoo 指数最新价（如 ^IXIC、^GSPC、^VIX）。"""
    encoded = symbol.replace("^", "%5E")
    url = f"https://query2.finance.yahoo.com/v8/finance/chart/{encoded}"
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
