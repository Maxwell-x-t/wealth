"""行情数据源：以当前价格、完整财年基准和已确认新方案计算股息率。

- ManualDataSource：股息率来自命令行 / 调用方直接提供。
- AkShareDataSource：默认走腾讯财经行情（免登录）。若设置环境变量
  ``XQ_A_TOKEN`` / ``XUEQIU_TOKEN``，优先尝试雪球个股接口。

两者都会应用 `dividend_overrides.json` 覆盖（命中代码以覆盖值为准，标注来源）。
"""

from __future__ import annotations

import datetime as _dt
import math
import os
from dataclasses import dataclass
from typing import Optional, Protocol
from urllib.request import Request, urlopen

from .dividends import (
    CALIBER_NAME,
    DividendUnavailable,
    fetch_annual_eps_map,
    fetch_dividend_records,
    fiscal_dividend,
    payout_percent,
)


@dataclass
class QualityMetrics:
    """分红质量相关指标（用于价值陷阱过滤）。"""

    eps: Optional[float] = None  # 优先该财年年报 EPS，否则 TTM
    dividend_per_share: Optional[float] = None  # 优先财年口径 DPS
    payout_ratio: Optional[float] = None  # 派息率% = 同财年 DPS/EPS*100
    pe_ttm: Optional[float] = None
    pb: Optional[float] = None
    market_cap: Optional[float] = None  # 总市值（元），用于推算分红总额
    source: str = ""


# 腾讯 qt.gtimg.cn 字段（~ 分隔）。64 = 股息率%，39 = 市盈率，46 = 市净率，
# 45 = 总市值（亿元），44 = 流通市值（亿元）。
TENCENT_IDX_NAME = 1
TENCENT_IDX_CODE = 2
TENCENT_IDX_PRICE = 3
TENCENT_IDX_PE = 39
TENCENT_IDX_MARKET_CAP = 45
TENCENT_IDX_PB = 46
TENCENT_IDX_YIELD = 64


def normalize_code(code: str) -> str:
    """去掉交易所前缀，返回 6 位数字代码。sh600690 -> 600690, sz000538 -> 000538。"""
    c = code.strip().lower()
    for prefix in ("sh", "sz", "bj"):
        if c.startswith(prefix):
            return c[len(prefix):]
    return c


def to_quote_symbol(code: str) -> str:
    """转换为带小写交易所前缀的代码，如 sh600690 / sz000538。"""
    c = code.strip().lower()
    for prefix in ("sh", "sz", "bj"):
        if c.startswith(prefix):
            return prefix + c[len(prefix):]
    digits = c
    if digits.startswith("6"):
        return "sh" + digits
    if digits.startswith(("0", "3")):
        return "sz" + digits
    if digits.startswith(("4", "8")):
        return "bj" + digits
    return "sh" + digits


def to_xq_symbol(code: str) -> str:
    """转换为雪球代码格式（大写交易所前缀 + 6 位代码），如 SH600690 / SZ000538。"""
    return to_quote_symbol(code).upper()


def _to_float(v) -> Optional[float]:
    try:
        if v in (None, ""):
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def parse_tencent_quote(raw: str) -> dict:
    """解析腾讯 qt.gtimg.cn 单行行情。返回中文字段字典。"""
    line = raw.strip()
    if '="' in line:
        line = line.split('="', 1)[1]
    line = line.rstrip('";')
    parts = line.split("~")
    if len(parts) <= TENCENT_IDX_YIELD:
        raise RuntimeError(f"腾讯行情字段不足: n={len(parts)}")
    price = _to_float(parts[TENCENT_IDX_PRICE])
    pe = _to_float(parts[TENCENT_IDX_PE]) if len(parts) > TENCENT_IDX_PE else None
    pb = _to_float(parts[TENCENT_IDX_PB]) if len(parts) > TENCENT_IDX_PB else None
    dy = _to_float(parts[TENCENT_IDX_YIELD])
    dps = None
    eps = None
    if price is not None and dy is not None:
        dps = price * dy / 100.0
    if price is not None and pe is not None and pe != 0:
        eps = price / pe
    return {
        "名称": parts[TENCENT_IDX_NAME],
        "代码": parts[TENCENT_IDX_CODE],
        "现价": price,
        "市盈率(TTM)": pe,
        "市净率": pb,
        "总市值(亿)": _to_float(parts[TENCENT_IDX_MARKET_CAP]) if len(parts) > TENCENT_IDX_MARKET_CAP else None,
        "股息率(TTM)": dy,
        "股息(TTM)": dps,
        "每股收益": eps,
        "时间": parts[30] if len(parts) > 30 else "",
    }


class DataSource(Protocol):
    def get_price(self, code: str) -> Optional[float]: ...

    def get_yield(self, code: str) -> tuple[float, str]:
        """返回 (股息率%, 来源标注)。"""
        ...

    def get_quality(self, code: str) -> Optional[QualityMetrics]:
        """返回分红质量指标；数据源不支持时返回 None。"""
        ...

    def get_dividend_snapshot(self, code: str) -> Optional[object]:
        """返回财年口径分红快照（FiscalDividend）；仅供输出展示口径，不参与判定。"""
        ...


class ManualDataSource:
    """股息率由调用方直接给定（无质量指标）。"""

    def __init__(self, yields: dict[str, float]):
        self._yields = dict(yields)

    def get_yield(self, code: str) -> tuple[float, str]:
        if code not in self._yields:
            raise KeyError(f"手动数据源缺少 {code} 的股息率")
        return self._yields[code], "manual"

    def get_quality(self, code: str) -> Optional[QualityMetrics]:
        return None

    def get_dividend_snapshot(self, code: str) -> Optional[object]:
        return None

    def get_price(self, code: str) -> Optional[float]:
        return None


class AkShareDataSource:
    """获取个股股息率与分红质量指标。

    行情默认：腾讯财经 ``qt.gtimg.cn``（免登录，字段 64 = 股息率 TTM）。
    可选：环境变量 ``XQ_A_TOKEN`` / ``XUEQIU_TOKEN`` 时优先走雪球。
    股息率不取行情里的 TTM 字段，改由东方财富分红明细按统一方案口径重算（见
    ``dividends.CALIBER_RULE``），避免除息日漂移与分红节奏变化造成假信号。
    派息率用同一财年的 DPS ÷ 年报基本 EPS，不用腾讯 TTM 市盈率反推。
    按代码缓存，get_yield / get_quality / get_dividend_snapshot 共用同一次请求。
    """

    def __init__(self, token: Optional[str] = None):
        self._cache: dict[str, dict] = {}
        self._dividend_cache: dict[str, object] = {}
        self._eps_cache: dict[str, dict[int, float]] = {}
        self._token = (
            token
            or os.environ.get("XQ_A_TOKEN", "")
            or os.environ.get("XUEQIU_TOKEN", "")
        ).strip()

    def _fetch(self, code: str) -> dict:
        if code in self._cache:
            return self._cache[code]
        errors: list[str] = []
        if self._token:
            try:
                kv = self._fetch_xueqiu(code)
                self._cache[code] = kv
                return kv
            except Exception as e:  # noqa: BLE001
                errors.append(f"xueqiu: {e}")
        try:
            kv = self._fetch_tencent(code)
            self._cache[code] = kv
            return kv
        except Exception as e:  # noqa: BLE001
            errors.append(f"tencent: {e}")
        raise RuntimeError("；".join(errors) or f"无法获取 {code} 行情")

    def _fetch_xueqiu(self, code: str) -> dict:
        import akshare as ak

        symbol = to_xq_symbol(code)
        df = ak.stock_individual_spot_xq(symbol=symbol, token=self._token)
        if df is None or df.empty:
            raise RuntimeError(f"雪球未返回 {code} 的行情数据")
        kv = dict(zip(df["item"], df["value"]))
        kv["_source"] = "xueqiu"
        return kv

    def _fetch_tencent(self, code: str) -> dict:
        symbol = to_quote_symbol(code)
        url = f"https://qt.gtimg.cn/q={symbol}"
        req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urlopen(req, timeout=15) as resp:
            raw = resp.read().decode("gbk", "replace")
        if "v_" not in raw or '=""' in raw.replace(" ", ""):
            raise RuntimeError(f"腾讯未返回 {code} 的行情")
        kv = parse_tencent_quote(raw)
        kv["_source"] = "tencent"
        return kv

    def _snapshot(self, code: str):
        """统一口径分红快照；分红明细不可用时抛 DividendUnavailable。"""
        if code not in self._dividend_cache:
            try:
                self._dividend_cache[code] = fiscal_dividend(
                    fetch_dividend_records(code), _dt.date.today().isoformat())
            except Exception as exc:
                raise DividendUnavailable(f"{code} 分红明细不可用：{exc}") from exc
        return self._dividend_cache[code]

    def _annual_eps(self, code: str, year: Optional[int]) -> Optional[float]:
        if year is None:
            return None
        if code not in self._eps_cache:
            try:
                self._eps_cache[code] = fetch_annual_eps_map(code)
            except Exception:  # noqa: BLE001
                self._eps_cache[code] = {}
        return self._eps_cache[code].get(year)

    def get_yield(self, code: str) -> tuple[float, str]:
        kv = self._fetch(code)
        price = _to_float(kv.get("现价"))
        if price is None or not math.isfinite(price) or price <= 0:
            raise ValueError(f"{code} 无有效现价")
        snapshot = self._snapshot(code)
        if snapshot.dps is None:
            raise DividendUnavailable(f"{code} {snapshot.note}")
        return snapshot.dps / price * 100, f"东方财富{CALIBER_NAME} {snapshot.note}"

    def get_quote_yield(self, code: str) -> Optional[float]:
        """行情源展示的股息率，不参与买卖。腾讯字段为股息率 TTM。"""
        try:
            return _to_float(self._fetch(code).get("股息率(TTM)"))
        except Exception:  # noqa: BLE001
            return None

    def get_dividend_snapshot(self, code: str) -> Optional[object]:
        """返回财年口径快照供输出展示口径与财年；取数失败返回 None，不影响判定。"""
        try:
            return self._snapshot(code)
        except Exception:  # noqa: BLE001
            return None

    def get_price(self, code: str) -> Optional[float]:
        return _to_float(self._fetch(code).get("现价"))

    def get_quality(self, code: str) -> Optional[QualityMetrics]:
        try:
            kv = self._fetch(code)
        except Exception:  # noqa: BLE001
            return None
        ttm_eps = _to_float(kv.get("每股收益"))
        ttm_dps = _to_float(kv.get("股息(TTM)"))
        pe = _to_float(kv.get("市盈率(TTM)"))
        pb = _to_float(kv.get("市净率"))
        cap_yi = _to_float(kv.get("总市值(亿)"))
        snapshot = None
        try:
            snapshot = self._snapshot(code)
        except Exception:  # noqa: BLE001
            snapshot = None
        fy_eps = None
        if snapshot is not None and snapshot.dps is not None:
            dps = snapshot.dps
            fy_eps = self._annual_eps(code, snapshot.fiscal_year)
            eps = fy_eps if fy_eps is not None else ttm_eps
            payout = payout_percent(dps, fy_eps)
        else:
            dps = ttm_dps
            eps = ttm_eps
            payout = payout_percent(dps, ttm_eps)
            if payout is None and pe is not None and pe > 0 and kv.get("股息率(TTM)") not in (None, ""):
                payout = float(kv["股息率(TTM)"]) * pe
        src = str(kv.get("_source", "tencent"))
        return QualityMetrics(
            eps=eps,
            dividend_per_share=dps,
            payout_ratio=payout,
            pe_ttm=pe,
            pb=pb,
            market_cap=cap_yi * 1e8 if cap_yi else None,
            source=src,
        )


def apply_overrides(
    code: str,
    base_yield: float,
    base_source: str,
    overrides: dict[str, dict],
    *,
    price: Optional[float] = None,
    today: Optional[_dt.date] = None,
) -> tuple[float, str, bool]:
    """用有有效期的每股分红金额和最新价格计算覆盖股息率。"""
    if code in overrides:
        entry = overrides[code]
        if not isinstance(entry, dict):
            raise ValueError(f"{code} 固定股息率覆盖已停用，请提供每股分红及有效期")
        declared = _dt.date.fromisoformat(entry["as_of"])
        expires = _dt.date.fromisoformat(entry["expires_on"])
        now = today or _dt.date.today()
        if not declared <= now <= expires:
            raise ValueError(f"{code} 分红覆盖未生效或已过期")
        dps = float(entry["dividend_per_share"])
        if not math.isfinite(dps) or dps < 0:
            raise ValueError(f"{code} 每股分红金额无效")
        if price is None or not math.isfinite(price) or price <= 0:
            raise ValueError(f"{code} 无最新价格，不能计算分红覆盖")
        return dps / price * 100.0, f"override DPS {declared} 至 {expires}", True
    return base_yield, base_source, False


def make_source(name: str, manual_yields: Optional[dict[str, float]] = None) -> DataSource:
    if name == "manual":
        return ManualDataSource(manual_yields or {})
    if name == "akshare":
        return AkShareDataSource()
    raise ValueError(f"未知数据源: {name}")
