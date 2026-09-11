"""Dated public financial records for replaying the live coverage gate."""

from datetime import date

from .backtest_data import iso_date, number, published_date
from .coverage import (BankMetrics, CoverageConfig, CoverageMetrics, assess_bank,
                       digits, evaluate_cashflow)
from .dividends import known_dividend_totals, parse_dividend_records


def parse_cashflows(payload: dict) -> list[dict]:
    rows = []
    for period, report in payload["result"]["data"]["report_list"].items():
        end, published = iso_date(period), published_date(report)
        values = {r.get("item_field"): r.get("item_value") for r in report.get("data", [])}
        ocf, capex = number(values.get("MANANETR")), number(values.get("ACQUASSETCASH"))
        if end and published and published >= end and ocf is not None and capex is not None:
            rows.append({"period": end, "published": published, "ocf": ocf, "capex": capex})
    if not rows:
        raise ValueError("No dated cash-flow reports")
    return sorted(rows, key=lambda r: (r["published"], r["period"]))


def parse_banks(payload: dict) -> list[dict]:
    result = payload.get("result")
    if not result or result.get("pages", 1) > 1:
        raise ValueError("Missing or incomplete bank history")
    rows = []
    for raw in result["data"]:
        end, published = iso_date(raw.get("REPORT_DATE")), iso_date(raw.get("NOTICE_DATE"))
        if raw.get("ORG_TYPE") == "银行" and end and published and published >= end:
            rows.append({"period": end, "published": published,
                         "car": number(raw.get("NEWCAPITALADER")),
                         "cet1": number(raw.get("HXYJBCZL")),
                         "npl": number(raw.get("NONPERLOAN")),
                         "provision": number(raw.get("BLDKBBL"))})
    if not rows:
        raise ValueError("No dated bank reports")
    return sorted(rows, key=lambda r: (r["published"], r["period"]))


def parse_cash_dividends(payload: dict) -> list[dict]:
    return parse_dividend_records(payload)


def coverage_on(code: str, data: dict, day: str,
                cfg: CoverageConfig = CoverageConfig()) -> CoverageMetrics:
    if digits(code) in cfg.insurance_codes:
        return CoverageMetrics(kind="insurance", note="保险无当时有效的专项人工复核")
    if digits(code) in cfg.bank_codes:
        known = [r for r in data["banks"] if r["published"] <= day]
        if not known:
            return CoverageMetrics(kind="bank", note="尚无已披露银行监管指标")
        last = max(known, key=lambda r: (r["period"], r["published"]))
        bank = BankMetrics(report=last["period"], announced=last["published"],
                           **{k: last[k] for k in ("car", "cet1", "npl", "provision")})
        flag, note = assess_bank(bank, cfg)
        return CoverageMetrics(kind="bank", flag=flag, note=note, bank=bank)
    cashflow = {r["period"].replace("-", ""): {"ocf": r["ocf"], "capex": r["capex"]}
                for r in data["cashflows"] if r["published"] <= day}
    return evaluate_cashflow(cashflow, known_dividend_totals(data["dividends"], day),
                             cfg, today=date.fromisoformat(day))
