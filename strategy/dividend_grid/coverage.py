"""分红可持续性检验：FCF 覆盖倍数 与 银行监管指标。

两条互补判据，按标的类型自动选择：

- **一般企业**：FCF 覆盖倍数 = 自由现金流 ÷ 现金分红。主判据取**近若干年
  （默认 5 年）合计 FCF ÷ 合计分红**，以平滑单年资本开支波动；不足 3 年时
  回退单窗口。现金流量表带同业往来科目，对银行无经济含义，故不用。
- **银行**：改看监管指标——核心一级资本充足率 / 不良贷款率 / 拨备覆盖率。

数据源：
- 现金流：新浪财报接口 `source=llb`（现金流量表），与回测取数同源。
- TTM 现金分红：股息率(TTM) × 总市值（分别来自腾讯行情字段 64 与 45）。
- 历年现金分红总额：东方财富分红明细 `RPT_SHAREBONUS_DET`
  （每股派息 `PRETAX_BONUS_RMB` ÷ 10 × 总股本 `TOTAL_SHARES`）。
- 银行指标：东方财富 F10 主要指标 `RPT_F10_FINANCE_MAINFINADATA`。

口径提醒：单年 FCF 波动极大（资本开支年际分布不均），故以多年平均为主判据；
重资产行业（电力、乳业）的扩张性资本开支会系统性压低该指标，需人工区分。
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, timedelta
import math
from typing import Optional

import requests
from .dividends import known_dividend_totals, parse_dividend_records

CASHFLOW_URL = "https://quotes.sina.cn/cn/api/openapi.php/CompanyFinanceService.getFinanceReport2022"
BANK_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
DIVIDEND_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"

FLAG_EMOJI = {"ok": "✅", "warn": "🟡", "risk": "🔴", "unknown": "⚪"}


@dataclass(frozen=True)
class CoverageConfig:
    """判据阈值。银行指标方向不同，单独一组。"""

    fcf_pass: float = 1.5       # FCF/分红 >= 此值 -> 达标
    fcf_floor: float = 1.0      # < 此值 -> 盖不住分红
    fcf_years: int = 5          # 多年平均窗口（完整财年数）
    fcf_min_years: int = 3      # 可用财年少于此值则回退单窗口 TTM
    cet1_warn: float = 10.0     # 核心一级资本充足率 < 此值 -> 警示
    cet1_risk: float = 8.5      # < 此值 -> 高风险（监管底线约 7.5%）
    npl_warn: float = 1.5       # 不良率 > 此值 -> 警示
    npl_risk: float = 2.0       # > 此值 -> 高风险
    provision_warn: float = 200.0   # 拨备覆盖率 < 此值 -> 警示
    provision_risk: float = 150.0   # < 此值 -> 高风险（监管要求约 120~150%）
    insurance_codes: tuple[str, ...] = ("601318", "601601", "601628", "601336", "601319")
    bank_codes: tuple[str, ...] = ("600036", "000001", "601166")


@dataclass
class BankMetrics:
    report: str = ""
    announced: str = ""
    car: Optional[float] = None        # 资本充足率 %
    cet1: Optional[float] = None       # 核心一级资本充足率 %
    npl: Optional[float] = None        # 不良贷款率 %
    provision: Optional[float] = None  # 拨备覆盖率 %
    source: str = "eastmoney F10"


@dataclass
class CoverageMetrics:
    kind: str = "unknown"              # fcf / bank / insurance / unknown
    flag: str = "unknown"              # ok / warn / risk / unknown
    note: str = ""
    fcf_ttm: Optional[float] = None        # 元
    dividend_ttm: Optional[float] = None   # 元
    ratio: Optional[float] = None          # 单窗口（TTM）fcf_ttm / dividend_ttm
    fcf_avg_ratio: Optional[float] = None  # 多年合计 FCF / 合计分红（主判据）
    fcf_avg_years: int = 0                 # 参与多年平均的完整财年数
    bank: Optional[BankMetrics] = None


def _number(value) -> Optional[float]:
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def digits(code: str) -> str:
    """sh601166 -> 601166。"""
    c = code.strip().lower()
    return c[2:] if c[:2] in ("sh", "sz", "bj") else c


def secucode(code: str) -> str:
    """sh601166 -> 601166.SH（东方财富口径）。"""
    c = code.strip().lower()
    prefix = c[:2] if c[:2] in ("sh", "sz", "bj") else "sh"
    return f"{digits(code)}.{'SH' if prefix == 'sh' else 'SZ' if prefix == 'sz' else 'BJ'}"


def ttm_value(records: dict[str, float], latest: str) -> Optional[float]:
    """由累计口径（YTD）季度数据推算 TTM。

    年报期直接取值；其余用「上年全年 + 本期累计 − 上年同期累计」。
    """
    if latest not in records or records[latest] is None:
        return None
    if latest.endswith("1231"):
        return records[latest]
    year, month_day = int(latest[:4]), latest[4:]
    prev_fy = records.get(f"{year - 1}1231")
    prev_same = records.get(f"{year - 1}{month_day}")
    if prev_fy is None or prev_same is None:
        return None
    return records[latest] + prev_fy - prev_same


def fetch_cashflow(code: str, *, timeout: float = 25.0) -> dict[str, dict[str, float]]:
    """抓取现金流量表，返回 {报告期 YYYYMMDD: {'ocf':…, 'capex':…}}（单位：元）。

    `num=40` 覆盖约 10 年（每年 4 期），以保证多年平均所需的历史年报期。
    """
    response = requests.get(
        CASHFLOW_URL,
        params={"paperCode": code, "source": "llb", "type": "0", "page": "1", "num": "40"},
        headers={"User-Agent": "Mozilla/5.0"}, timeout=timeout,
    )
    response.raise_for_status()
    reports = response.json()["result"]["data"]["report_list"]
    out: dict[str, dict[str, float]] = {}
    for period, report in reports.items():
        values = {row.get("item_field"): row.get("item_value") for row in report.get("data", [])}
        ocf = _number(values.get("MANANETR"))          # 经营活动产生的现金流量净额
        capex = _number(values.get("ACQUASSETCASH"))   # 购建固定资产、无形资产和其他长期资产所支付的现金
        if ocf is None or capex is None:
            continue
        out[period] = {"ocf": ocf, "capex": capex}
    if not out:
        raise RuntimeError(f"无现金流量表数据: {code}")
    return out


def fetch_dividends(code: str, *, timeout: float = 25.0) -> dict[int, float]:
    """抓取历年现金分红总额，返回 {财年: 分红总额(元)}。

    来源东方财富分红明细 `RPT_SHAREBONUS_DET`；年度方案已完成后，
    按 `REPORT_DATE` 年份归集已实施分红（含中期），明确不分配可记零。
    总额 = 每 10 股派息 ÷ 10 × 总股本。
    """
    response = requests.get(
        DIVIDEND_URL,
        params={"reportName": "RPT_SHAREBONUS_DET", "columns": "ALL", "pageSize": "500",
                "sortColumns": "REPORT_DATE", "sortTypes": "-1",
                "filter": f'(SECURITY_CODE="{digits(code)}")'},
        headers={"User-Agent": "Mozilla/5.0"}, timeout=timeout,
    )
    response.raise_for_status()
    result = response.json().get("result")
    rows = result.get("data") if result else None
    if not rows:
        raise RuntimeError(f"无分红记录: {code}")
    out = known_dividend_totals(parse_dividend_records({"result": result}), date.today().isoformat())
    if not out:
        raise RuntimeError(f"无已实施分红记录: {code}")
    return out


def fiscal_year_fcf(cashflow: dict[str, dict[str, float]], year: int) -> Optional[float]:
    """单个完整财年的自由现金流 = 该年经营现金流 − 资本开支（元）。"""
    record = cashflow.get(f"{year}1231")
    return None if record is None else record["ocf"] - record["capex"]


def _dividend_pending(year: int, dividends: dict[int, float], today: date | None = None) -> bool:
    """最新财年是否「分红尚未可知」（无记录且离财年结束还不到 9 个月）。

    暂未完成年度分红的最新财年可先剔除；过了有效期仍然缺失则中断连续窗口。
    任何年份的缺失都不等同于零分红。
    """
    if year in dividends:
        return False
    fye = date(year, 12, 31)
    return (today or date.today()) < fye + timedelta(days=274)


def multi_year_ratio(cashflow: dict[str, dict[str, float]], dividends: dict[int, float],
                     years: int = 5, min_years: int = 3, today: date | None = None
                     ) -> tuple[Optional[float], int, dict[int, tuple[float, float]]]:
    """近 N 个完整财年的合计 FCF ÷ 合计分红（主判据）。

    返回 (覆盖倍数, 参与年数, {财年: (FCF, 分红)})。

    - 窗口 = 现金流量表里最近的 N 个完整财年（连续，不跳年，避免挑年份）；
    - 窗口内缺分红记录即中断，只有明确不分配才计为 0；
    - 最新财年若分红尚未可知则先剔除（见 `_dividend_pending`）；
    - 用**合计**而非年度比值的均值：单年 FCF 可能为负，均值会被极端年份主导，
      合计口径等价于「窗口内总现金流能否覆盖总分红」，更稳健。
    - 有效分红年数 < `min_years` 时返回 (None, n, {})，由调用方回退单窗口。
    """
    annual_fcf = {int(p[:4]): r["ocf"] - r["capex"]
                  for p, r in cashflow.items() if p.endswith("1231")}
    if not annual_fcf:
        return None, 0, {}
    latest = max(annual_fcf)
    window = list(range(latest, latest - years, -1))
    if window and _dividend_pending(window[0], dividends, today):
        window = window[1:]
    # Stop at a missing year; absence is not evidence of a zero dividend.
    complete = []
    for year in window:
        if year not in annual_fcf or year not in dividends:
            break
        complete.append(year)
    window = complete
    if len(window) < min_years:
        return None, len(window), {}
    total_div = sum(dividends.get(y, 0.0) for y in window)
    if total_div <= 0:
        return None, len(window), {}
    total_fcf = sum(annual_fcf[y] for y in window)
    detail = {y: (annual_fcf[y], dividends.get(y, 0.0)) for y in window}
    return total_fcf / total_div, len(window), detail


def fetch_bank_metrics(code: str, *, timeout: float = 25.0) -> Optional[BankMetrics]:
    """抓取银行监管指标；非银行（如保险）返回 None。"""
    response = requests.get(
        BANK_URL,
        params={"reportName": "RPT_F10_FINANCE_MAINFINADATA", "columns": "ALL", "pageSize": "1",
                "sortColumns": "REPORT_DATE", "sortTypes": "-1",
                "filter": f'(SECUCODE="{secucode(code)}")'},
        headers={"User-Agent": "Mozilla/5.0"}, timeout=timeout,
    )
    response.raise_for_status()
    result = response.json().get("result")
    if not result or not result.get("data"):
        return None
    row = result["data"][0]
    if row.get("ORG_TYPE") != "银行":
        return None
    return BankMetrics(
        report=str(row.get("REPORT_DATE_NAME") or "")[:16],
        announced=str(row.get("NOTICE_DATE") or "")[:10],
        car=_number(row.get("NEWCAPITALADER")),
        cet1=_number(row.get("HXYJBCZL")),
        npl=_number(row.get("NONPERLOAN")),
        provision=_number(row.get("BLDKBBL")),
    )


def grade_ratio(ratio: float, cfg: CoverageConfig = CoverageConfig(),
                label: str = "FCF/分红") -> tuple[str, str]:
    """按覆盖倍数给出 (flag, note)。"""
    if ratio >= cfg.fcf_pass:
        return "ok", f"{label} {ratio:.2f}x ≥ {cfg.fcf_pass:g}x"
    if ratio >= cfg.fcf_floor:
        return "warn", f"{label} {ratio:.2f}x（覆盖分红但不足 {cfg.fcf_pass:g}x 裕度）"
    return "risk", f"{label} {ratio:.2f}x（盖不住分红）"


def assess_fcf(fcf_ttm: Optional[float], dividend_ttm: Optional[float],
               cfg: CoverageConfig = CoverageConfig()) -> tuple[float | None, str, str]:
    """返回 (覆盖倍数, flag, note)。"""
    if fcf_ttm is None or dividend_ttm is None or dividend_ttm <= 0:
        return None, "unknown", "FCF 或分红数据不足"
    ratio = fcf_ttm / dividend_ttm
    flag, note = grade_ratio(ratio, cfg)
    return ratio, flag, note


def assess_bank(bank: BankMetrics, cfg: CoverageConfig = CoverageConfig()) -> tuple[str, str]:
    """返回 (flag, note)。三项指标取最差者。"""
    flags: list[str] = []
    parts: list[str] = []

    def grade(value: Optional[float], warn: float, risk: float, higher_is_better: bool) -> str:
        if value is None:
            return "unknown"
        if higher_is_better:
            return "risk" if value < risk else "warn" if value < warn else "ok"
        return "risk" if value > risk else "warn" if value > warn else "ok"

    specs = (
        ("核心一级", bank.cet1, cfg.cet1_warn, cfg.cet1_risk, True),
        ("不良率", bank.npl, cfg.npl_warn, cfg.npl_risk, False),
        ("拨备覆盖", bank.provision, cfg.provision_warn, cfg.provision_risk, True),
    )
    for label, value, warn, risk, higher in specs:
        flags.append(grade(value, warn, risk, higher))
        if value is not None:
            parts.append(f"{label} {value:.2f}%" if label != "拨备覆盖" else f"{label} {value:.1f}%")
    order = {"ok": 0, "warn": 1, "unknown": 2, "risk": 3}
    flag = max(flags, key=lambda f: order[f]) if flags else "unknown"
    return flag, " | ".join(parts) or "无银行指标"


def emoji(flag: str) -> str:
    return FLAG_EMOJI.get(flag, "")


def apply_coverage_review(code: str, metrics: CoverageMetrics, reviews: dict,
                          today: date | None = None) -> CoverageMetrics:
    review = reviews.get(code)
    if review is None:
        return metrics
    day = today or date.today()
    start, stop = date.fromisoformat(review["as_of"]), date.fromisoformat(review["expires_on"])
    flag, reason = review["flag"], review["reason"]
    if flag not in ("ok", "warn", "risk") or not isinstance(reason, str) or not reason.strip() or start > stop:
        raise ValueError(f"{code} 人工复核需填写结论、依据和有效日期")
    if not start <= day <= stop:
        return replace(metrics, note=metrics.note + "；人工复核未生效或已过期")
    return replace(metrics, flag=flag, note=f"人工复核至{stop}：{reason}；原始指标：{metrics.note}")


def evaluate_coverage(code: str, dividend_yield_pct: Optional[float],
                      market_cap: Optional[float],
                      cfg: CoverageConfig = CoverageConfig()) -> CoverageMetrics:
    """按标的类型自动选择判据并抓数。网络/解析失败时抛异常，由调用方降级处理。

    market_cap 单位为元；TTM 分红总额 = 股息率% × 总市值。
    非银行的主判据为**多年合计 FCF ÷ 合计分红**；多年分红取数失败或财年不足时
    回退单窗口 TTM（这两种情形不视为整体失败）。
    """
    if digits(code) in cfg.insurance_codes:
        return CoverageMetrics(kind="insurance", flag="unknown",
                               note="保险待专项复核：偿付能力、可分配利润及上游分红；不使用普通企业FCF")
    bank = fetch_bank_metrics(code)
    if bank is not None:
        flag, note = assess_bank(bank, cfg)
        return CoverageMetrics(kind="bank", flag=flag, note=f"{bank.report} {note}", bank=bank)
    if digits(code) in cfg.bank_codes:
        return CoverageMetrics(kind="bank", flag="unknown", note="银行监管指标缺失，暂停新增买入待复核")

    cashflow = fetch_cashflow(code)
    fallback = ""
    try:
        dividends = fetch_dividends(code)
    except Exception as e:  # Public provider failure leaves the independent TTM estimate available.
        dividends = {}
        fallback = f"；历年分红取数失败（{e}），回退单窗口"
    dividend_ttm = (dividend_yield_pct / 100.0 * market_cap
                    if dividend_yield_pct and market_cap else None)
    return evaluate_cashflow(cashflow, dividends, cfg, dividend_ttm=dividend_ttm,
                             fallback=fallback)


def evaluate_cashflow(cashflow: dict, dividends: dict[int, float],
                      cfg: CoverageConfig = CoverageConfig(), *, today: date | None = None,
                      dividend_ttm: float | None = None, fallback: str = "") -> CoverageMetrics:
    """Shared live/replay grading; callers supply only records known at the valuation date."""
    if not cashflow:
        return CoverageMetrics(kind="fcf", note="尚无已披露现金流量表")
    latest = max(cashflow)
    ocf = ttm_value({p: r["ocf"] for p, r in cashflow.items()}, latest)
    capex = ttm_value({p: r["capex"] for p, r in cashflow.items()}, latest)
    fcf = ocf - capex if ocf is not None and capex is not None else None
    ttm_ratio, ttm_flag, ttm_note = assess_fcf(fcf, dividend_ttm, cfg)
    avg_ratio, avg_years, detail = multi_year_ratio(
        cashflow, dividends, years=cfg.fcf_years, min_years=cfg.fcf_min_years, today=today)
    if avg_ratio is None and not fallback:
        fallback = f"；完整匹配财年 {avg_years} 年，无法计算多年覆盖"

    if avg_ratio is not None:
        flag, note = grade_ratio(avg_ratio, cfg, f"{avg_years}年均FCF/分红")
        suffix = f"，单窗口(TTM) {ttm_ratio:.2f}x" if ttm_ratio is not None else ""
        note = note + suffix
        latest_year = max(detail)
        latest_fcf, latest_dividend = detail[latest_year]
        if latest_dividend > 0:
            recent_ratio = latest_fcf / latest_dividend
            note += f"；FY{latest_year}覆盖{recent_ratio:.2f}x"
            if recent_ratio < cfg.fcf_floor:
                flag = "risk"
                note += "，最新年度覆盖不足，需复核趋势"
    else:
        flag, note = ttm_flag, ttm_note + fallback

    return CoverageMetrics(kind="fcf", flag=flag, note=note, fcf_ttm=fcf,
                           dividend_ttm=dividend_ttm, ratio=ttm_ratio,
                           fcf_avg_ratio=avg_ratio, fcf_avg_years=avg_years)
