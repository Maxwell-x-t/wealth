"""Dated dividend records and the fiscal-year signal shared by live and replay."""

from dataclasses import dataclass
from datetime import date
import math
from statistics import median

import requests


DIVIDEND_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"

# ── 通用股息率口径规则（唯一权威描述，输出与文档都引用这里）────────────────
# 股息率以最近一个「年度分红（12-31 报告期）已实施」的完整财年为基准；若已有
# 更新财年的同周期正分红实施公告，则用新方案替换基准财年的对应周期（如新中期
# 替换旧中期），再除以最新价。这样既及时使用已公开方案，也不会重复相加或把
# 半年分红误当全年分红。
#
# 为什么不用滚动 12 个月：滚动窗口与「钱什么时候到账」强相关，会随除息日漂移和
# 分红节奏变化（如某年取消中期分红）机械涨落，产生与估值无关的假买卖信号。
# 新方案从实施公告日起用于估值，不必等到除息；实际现金仍只在除息后确认。
# live 与回测共用本函数，回测按公告日生效，不使用未来信息。
CALIBER_NAME = "财年基准+确认方案"
CALIBER_RULE = (
    "股息率以最近一个「年度分红已实施」的完整财年为基准；更新财年已有同周期正分红"
    "实施公告时，以新方案替换旧周期后除以最新价。新旧周期不重复相加，也不把半年分红"
    "直接当作全年分红；回测按实施公告日生效。"
)


class DividendUnavailable(ValueError):
    pass


def _date(value):
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10]).isoformat()
    except ValueError:
        return None


def _number(value):
    try:
        number = float(value)
        return number if math.isfinite(number) and number >= 0 else None
    except (ValueError, TypeError):
        return None


def parse_dividend_records(payload: dict) -> list[dict]:
    result = payload.get("result")
    if not result or not isinstance(result.get("data"), list):
        raise ValueError("分红明细缺失，不能假定未分红")
    if result.get("pages", 1) > 1:
        raise ValueError("分红明细分页不完整")
    records = []
    for row in result["data"]:
        period, announced = _date(row.get("REPORT_DATE")), _date(row.get("NOTICE_DATE"))
        if not period or not announced:
            continue
        status = str(row.get("ASSIGN_PROGRESS") or "")
        explanation = str(row.get("IMPL_PLAN_PROFILE") or row.get("PLAN_EXPLAIN") or "")
        cash = _number(row.get("PRETAX_BONUS_RMB"))
        ex = _date(row.get("EX_DIVIDEND_DATE"))
        if any(word in status for word in ("取消", "终止")):
            state = "cancelled"
        elif "不分配" in status or "不分红" in status or (
            cash in (None, 0) and any(word in explanation for word in ("不分配", "不分红", "不进行利润分配"))
        ):
            state, cash = "no_distribution", 0.0
        elif status == "实施分配" and ex:
            state = "implemented"
            if cash is None:
                # A pure stock distribution has no cash; absent cash alone is not proof.
                if not (_number(row.get("BONUS_RATIO")) or _number(row.get("IT_RATIO"))):
                    raise ValueError(f"已实施分红缺少现金金额：{period}")
                cash = 0.0
        else:
            continue
        if announced < period:
            raise ValueError(f"分红公告早于报告期：{period}")
        if state == "implemented" and announced > ex:
            raise ValueError(f"分红公告日晚于除息日：{period}")
        shares = _number(row.get("TOTAL_SHARES"))
        records.append({"report_date": period, "announced": announced, "ex_date": ex,
                        "state": state, "cash_per_share": (cash or 0) / 10,
                        "cash_total": (cash * shares / 10 if cash and shares else
                                       0.0 if not cash else None),
                        "special": row.get("IS_SPECIAL_DIVIDEND") in (True, 1, "1") or "特别分红" in explanation,
                        "split": 1 + (_number(row.get("BONUS_RATIO")) or 0) / 10
                                 + (_number(row.get("IT_RATIO")) or 0) / 10})
    keys = [(r["report_date"], r["announced"], r["ex_date"], r["state"]) for r in records]
    if len(keys) != len(set(keys)):
        raise ValueError("分红明细包含重复记录，需复核")
    return records


def known_dividend_totals(records: list[dict], day: str) -> dict[int, float]:
    """Complete fiscal-year cash distributions, available at the specified date."""
    known = [r for r in records if r["announced"] <= day and
             (r["state"] != "implemented" or r["ex_date"] <= day)]
    annual = {}
    for record in sorted(known, key=lambda r: r["announced"]):
        if record["report_date"].endswith("12-31"):
            annual[int(record["report_date"][:4])] = record
    totals = {}
    for year, last in annual.items():
        if last["state"] == "cancelled":
            continue
        completed = [r for r in known if int(r["report_date"][:4]) == year
                     and r["state"] == "implemented"]
        if any(r["cash_total"] is None for r in completed):
            continue
        totals[year] = sum(r["cash_total"] for r in completed)
    return totals


def fetch_dividend_records(code: str) -> list[dict]:
    digits = code[2:] if code[:2].lower() in ("sh", "sz", "bj") else code
    response = requests.get(DIVIDEND_URL, params={
        "reportName": "RPT_SHAREBONUS_DET", "columns": "ALL", "pageSize": "500",
        "sortColumns": "REPORT_DATE", "sortTypes": "-1",
        "filter": f'(SECURITY_CODE="{digits}")'},
        headers={"User-Agent": "Mozilla/5.0"}, timeout=25)
    response.raise_for_status()
    return parse_dividend_records(response.json())


@dataclass(frozen=True)
class FiscalDividend:
    dps: float | None
    median_dps: float | None
    fiscal_year: int | None
    note: str


def fiscal_dividend(records: list[dict], as_of: str) -> FiscalDividend:
    day = date.fromisoformat(as_of)
    known = [r for r in records if r["announced"] <= as_of and (
        r.get("state", "implemented") != "implemented" or (r.get("ex_date") and r["ex_date"] <= as_of))]
    annual = [r for r in known if str(r.get("report_date") or "").endswith("12-31")]
    if not annual:
        return FiscalDividend(None, None, None, "缺少已完成财年的分红记录")
    year = max(int(r["report_date"][:4]) for r in annual)
    # An unresolved annual cycle must not keep a years-old dividend alive forever.
    if day > date(year + 2, 9, 30):
        return FiscalDividend(None, None, year, f"FY{year}分红已过期，待核实新年度方案")
    totals = {}
    for candidate in range(year - 2, year + 1):
        endings = [r for r in annual if int(r["report_date"][:4]) == candidate]
        if not endings:
            continue
        latest = max(endings, key=lambda r: r["announced"])
        if latest.get("state") == "cancelled":
            if candidate == year:
                return FiscalDividend(None, None, year, f"FY{year}分红方案取消，待复核")
            continue
        if any(r.get("special") and str(r.get("report_date") or "").startswith(str(candidate)) for r in known):
            if candidate == year:
                return FiscalDividend(None, None, year, f"FY{year}包含已标识特别分红，需核实可持续每股分红")
            continue
        total = 0.0
        for record in known:
            if not record.get("report_date") or int(record["report_date"][:4]) != candidate or record.get("state", "implemented") != "implemented":
                continue
            factor = math.prod(r.get("split", 1) for r in known
                               if r.get("state", "implemented") == "implemented"
                               and r["ex_date"] >= record["ex_date"])
            total += record["cash_per_share"] / factor
        totals[candidate] = total
    components: dict[str, float] = {}

    def adjusted_dps(record: dict) -> float:
        factor = math.prod(
            r.get("split", 1)
            for r in known
            if r.get("state", "implemented") == "implemented"
            and r.get("ex_date")
            and record.get("ex_date")
            and r["ex_date"] >= record["ex_date"]
        )
        return record["cash_per_share"] / factor

    for record in known:
        if (
            int(record["report_date"][:4]) == year
            and record.get("state", "implemented") == "implemented"
        ):
            slot = record["report_date"][5:]
            components[slot] = components.get(slot, 0.0) + adjusted_dps(record)

    # 新财年的年度方案一旦发布实施公告，该财年的分红周期已经完整，直接整体
    # 切换到新财年（其中明确不分配的中期按 0 处理），避免混用旧中期和新年度。
    newer_annual = [
        record
        for record in records
        if str(record.get("report_date") or "").endswith("12-31")
        and int(record["report_date"][:4]) > year
        and record.get("state", "implemented") == "implemented"
        and record.get("announced", "") <= as_of
        and record.get("ex_date")
    ]
    if newer_annual:
        latest_year = max(int(record["report_date"][:4]) for record in newer_annual)
        cycle = [
            record
            for record in records
            if str(record.get("report_date") or "").startswith(f"{latest_year}-")
            and record.get("announced", "") <= as_of
        ]
        if any(record.get("special") for record in cycle):
            return FiscalDividend(
                None,
                median(totals.values()),
                latest_year,
                f"FY{latest_year}包含已标识特别分红，需核实可持续每股分红",
            )
        amount = sum(
            adjusted_dps(record)
            for record in cycle
            if record.get("state", "implemented") == "implemented"
            and record.get("cash_per_share", 0) > 0
            and record.get("ex_date")
        )
        return FiscalDividend(
            amount,
            median(totals.values()),
            latest_year,
            f"FY{latest_year}已确认完整方案每股分红{amount:.4f}元",
        )

    # 已公开、已有明确除息安排的更新正分红可立即参与估值。按报告期槽位替换，
    # 例如 FY2026 中期替换 FY2025 中期，而不是叠加成两次中期分红。
    confirmed: dict[tuple[int, str], list[dict]] = {}
    for record in records:
        report_date = str(record.get("report_date") or "")
        if (
            len(report_date) < 10
            or int(report_date[:4]) <= year
            or record.get("state", "implemented") != "implemented"
            or record.get("announced", "") > as_of
            or not record.get("ex_date")
            or record.get("cash_per_share", 0) <= 0
        ):
            continue
        confirmed.setdefault((int(report_date[:4]), report_date[5:]), []).append(record)

    replaced = []
    for slot in {key[1] for key in confirmed}:
        latest_year = max(candidate_year for candidate_year, candidate_slot in confirmed
                          if candidate_slot == slot)
        replacements = confirmed[(latest_year, slot)]
        if any(record.get("special") for record in replacements):
            return FiscalDividend(
                None,
                median(totals.values()),
                year,
                f"FY{latest_year}-{slot}包含已标识特别分红，需核实可持续每股分红",
            )
        components[slot] = sum(adjusted_dps(record) for record in replacements)
        replaced.append(f"FY{latest_year}-{slot}")

    amount = sum(components.values())
    replacement_note = f"，已按{'、'.join(sorted(replaced))}新方案替换同周期" if replaced else ""
    return FiscalDividend(
        amount,
        median(totals.values()),
        year,
        f"FY{year}基准每股分红{amount:.4f}元{replacement_note}",
    )
