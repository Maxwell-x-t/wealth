"""Download and cache public data for reproducible daily backtests."""

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timezone
from io import StringIO
import json
import math
from pathlib import Path
import re
import time

import pandas as pd
import requests

from .dividends import parse_dividend_records


PRICE_URL = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
DIVIDEND_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
FINANCE_URL = "https://quotes.sina.cn/cn/api/openapi.php/CompanyFinanceService.getFinanceReport2022"
ETF_DIVIDEND_URL = "https://fundf10.eastmoney.com/fhsp_512890.html"


def etf_dividend_url(code: str) -> str:
    """基金分红页地址。code 为带市场前缀的代码（如 sh512890），页面只用纯数字部分。"""
    return f"https://fundf10.eastmoney.com/fhsp_{code[2:] if code[:2].isalpha() else code}.html"


def request(url: str, params: dict | None = None) -> requests.Response:
    error = None
    for attempt in range(3):
        try:
            response = requests.get(url, params=params, timeout=25,
                                    headers={"User-Agent": "Mozilla/5.0"})
            response.raise_for_status()
            return response
        except requests.RequestException as exc:
            error = exc
            if attempt < 2:
                time.sleep(attempt + 1)
    raise RuntimeError(f"Public data request failed: {url}: {error}") from error


def number(value, default=None):
    try:
        result = float(value)
        return result if math.isfinite(result) else default
    except (ValueError, TypeError):
        return default


def iso_date(value) -> str | None:
    if value is None or str(value) in {"", "--", "NaT", "nan"}:
        return None
    text = str(value)
    if re.fullmatch(r"\d{8}", text):
        text = f"{text[:4]}-{text[4:6]}-{text[6:]}"
    try:
        return date.fromisoformat(text[:10]).isoformat()
    except ValueError:
        return None


def drop_isolated_spikes(rows: list[dict], ratio: float = 0.5) -> tuple[list[dict], list[dict]]:
    """剔除「孤立跳变」坏点：单日收盘相对**前后两个交易日都**偏离 50% 以上。

    腾讯行情接口偶发单日坏数据（实测 sh510880 的 2008-01-02 被记成 1.467，
    而前后两个交易日分别是 4.637 与 4.794），伪造出 -68% / +227% 的两日往返。

    判定必须要求「相对前后两天都偏离」，因为价格是不复权序列：真实的除权跳空
    只会单边偏离（例如 510880 在 2009-03-24 每份分红 1.0 元、价格从 4.5 掉到
    1.5），第二天不会回跳，因此不会被误删。涨跌停制度下 A 股也不可能出现
    50% 的反向回归。
    """
    kept, dropped = [], []
    for index, row in enumerate(rows):
        if 0 < index < len(rows) - 1:
            before, after, close = rows[index - 1]["close"], rows[index + 1]["close"], row["close"]
            if (close < before * ratio and close < after * ratio) or \
                    (close > before / ratio and close > after / ratio):
                dropped.append(dict(row))
                continue
        kept.append(row)
    return kept, dropped


def parse_prices(code: str, snapshots: list[dict], start: str, end: str) -> tuple[list[dict], list[dict]]:
    """把缓存的原始响应解析成日线；在线抓取与离线重建共用这一条路径。"""
    rows = {}
    for snapshot in snapshots:
        parts = snapshot["params"]["param"].split(",")
        first, last = parts[2], parts[3]
        data = snapshot["response"].get("data", {}).get(code, {})
        for row in data.get("day", []):
            if not first <= row[0] <= last or not start <= row[0] <= end:
                continue
            values = list(map(float, row[1:6]))
            if any(not math.isfinite(v) for v in values) or min(values[:4]) <= 0:
                raise ValueError(f"Invalid OHLC data: {code} {row[0]}")
            rows[row[0]] = dict(zip(("date", "open", "close", "high", "low", "volume"),
                                    [row[0], *values]))
    if not rows:
        raise ValueError(f"No raw price history: {code}")
    return drop_isolated_spikes([rows[day] for day in sorted(rows)])


def fetch_prices(code: str, start: str, end: str) -> tuple[list[dict], list[dict], list[dict]]:
    snapshots = []
    for year in range(int(start[:4]), int(end[:4]) + 1, 2):
        first, last = f"{year}-01-01", min(f"{year + 1}-12-31", end)
        params = {"param": f"{code},day,{first},{last},640,"}
        snapshots.append({"params": params, "response": request(PRICE_URL, params).json()})
    rows, dropped = parse_prices(code, snapshots, start, end)
    return rows, snapshots, dropped


def fetch_dividends(code: str) -> tuple[list[dict], dict]:
    params = {"reportName": "RPT_SHAREBONUS_DET", "columns": "ALL", "pageSize": "500",
              "sortColumns": "EX_DIVIDEND_DATE", "sortTypes": "1",
              "filter": f'(SECURITY_CODE="{code[2:]}")'}
    payload = request(DIVIDEND_URL, params).json()
    result = payload.get("result")
    if result is None:
        raise ValueError(f"No dividend response: {code}")
    if result.get("pages", 1) > 1:
        raise ValueError(f"Dividend response unexpectedly paginated: {code}")
    actions = []
    for row in result["data"]:
        ex = iso_date(row.get("EX_DIVIDEND_DATE"))
        notice = iso_date(row.get("NOTICE_DATE"))
        if not ex or row.get("ASSIGN_PROGRESS") != "实施分配":
            continue
        if not notice or notice > ex:
            raise ValueError(f"Invalid dividend announcement date: {code} {ex}")
        cash = number(row.get("PRETAX_BONUS_RMB"), 0.0) / 10.0
        bonus = number(row.get("BONUS_RATIO"), 0.0) / 10.0
        transfer = number(row.get("IT_RATIO"), 0.0) / 10.0
        actions.append({"ex_date": ex, "announced": notice, "cash_per_share": cash,
                        "split": 1.0 + bonus + transfer, "bonus_taxable": bonus,
                        "pay_date": None, "pay_date_assumed": True,
                        "report_date": iso_date(row.get("REPORT_DATE"))})
    return actions, payload


def published_date(report: dict) -> str | None:
    """取财报的披露日期。

    上游 `publish_date` 对较早的报告期会**多算一年**（实测 40% 的记录偏移 12 个月，
    例如报告期 2024-12-31 被标成 2026-03-20），而 `update_time`（Unix 秒）与真实
    披露节奏逐一吻合。因此以 `update_time` 为准，仅在其缺失时回退到 `publish_date`。
    """
    stated = iso_date(report.get("publish_date"))
    stamped = report.get("update_time")
    if stamped:
        try:
            moment = datetime.fromtimestamp(float(stamped), tz=timezone.utc).date().isoformat()
        except (TypeError, ValueError, OSError):
            moment = None
        if moment and (stated is None or moment < stated):
            return moment
    return stated


def parse_financials(code: str, payload: dict) -> list[dict]:
    """把新浪财报响应解析成带披露日期的 EPS 记录。"""
    reports = payload["result"]["data"]["report_list"]
    rows = []
    for period, report in reports.items():
        end = iso_date(period)
        publication = published_date(report)
        values = {row["item_field"]: row.get("item_value") for row in report["data"]}
        eps = number(values.get("BASICEPS"))
        if publication and end and eps is not None and publication >= end:
            rows.append({"period": end, "published": publication, "eps_ytd": eps,
                         "net_profit_ytd": number(values.get("PARENETP")),
                         "updated_at": report.get("update_time")})
    if not rows:
        raise ValueError(f"No dated EPS reports: {code}")
    return sorted(rows, key=lambda row: (row["published"], row["period"]))


def fetch_financials(code: str) -> tuple[list[dict], dict]:
    payload = request(FINANCE_URL, {"paperCode": code, "source": "lrb", "type": "0",
                                    "page": "1", "num": "1000"}).json()
    return parse_financials(code, payload), payload


def rebuild_prices(directory: Path) -> list[dict]:
    """用已缓存的 raw 响应重新解析日线，用于修复历史坏点，无需联网。"""
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    results = []
    for code in manifest["universe"]:
        raw_path, item_path = directory / "raw" / f"{code}.json", directory / f"{code}.json"
        if not raw_path.exists() or not item_path.exists():
            continue
        item = json.loads(item_path.read_text(encoding="utf-8"))
        snapshots = json.loads(raw_path.read_text(encoding="utf-8")).get("prices")
        if not snapshots:
            continue
        rows, dropped = parse_prices(code, snapshots, item["requested_start"], item["requested_end"])
        item["prices"] = rows
        item["dropped_price_bars"] = dropped
        item_path.write_text(json.dumps(item, ensure_ascii=False, indent=2), encoding="utf-8")
        results.append({"code": code, "bars": len(rows), "dropped": len(dropped)})
    return results


def rebuild_financials(directory: Path) -> list[dict]:
    """用已缓存的 raw 响应重新解析财报，用于修正历史披露日期，无需联网。"""
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    results = []
    for code in manifest["universe"]:
        raw_path, item_path = directory / "raw" / f"{code}.json", directory / f"{code}.json"
        if not raw_path.exists() or not item_path.exists():
            continue
        payload = json.loads(raw_path.read_text(encoding="utf-8")).get("financials")
        if not payload:
            continue
        item = json.loads(item_path.read_text(encoding="utf-8"))
        before = {row["period"]: row["published"] for row in item.get("financials", [])}
        item["financials"] = parse_financials(code, payload)
        item_path.write_text(json.dumps(item, ensure_ascii=False, indent=2), encoding="utf-8")
        corrected = sum(1 for row in item["financials"]
                        if before.get(row["period"]) not in (None, row["published"]))
        results.append({"code": code, "reports": len(item["financials"]), "corrected": corrected})
    return results


def fetch_etf_dividends(code: str = "sh512890") -> tuple[list[dict], str]:
    html = request(etf_dividend_url(code)).text
    tables = pd.read_html(StringIO(html))
    actions = []
    dividend_table_seen = False
    for table in tables:
        names = [str(column) for column in table.columns]
        if any("拆分折算日" in column for column in names):
            for _, row in table.iterrows():
                ex = iso_date(row.get("拆分折算日"))
                if not ex:
                    continue
                ratio_text = str(row["拆分折算比例"])
                parts = ratio_text.split(":")
                if len(parts) != 2:
                    raise ValueError(f"Unrecognized ETF split: {ratio_text}")
                ratio = float(parts[1]) / float(parts[0])
                actions.append({"ex_date": ex, "announced": ex,
                                "cash_per_share": 0.0, "split": ratio,
                                "bonus_taxable": 0.0, "pay_date": ex,
                                "pay_date_assumed": False, "report_date": None})
        if not any("除息" in column for column in names):
            continue
        dividend_table_seen = True
        for _, row in table.iterrows():
            fields = {str(key): value for key, value in row.items()}
            ex_value = next(value for key, value in fields.items() if "除息" in key)
            ex = iso_date(ex_value)
            if not ex:
                continue
            amount_key = next(key for key in fields if "分红" in key and "每" in key)
            amount = fields[amount_key]
            match = re.search(r"([\d.]+)", str(amount))
            if not match:
                raise ValueError(f"Unrecognized ETF dividend: {amount}")
            payment = next((iso_date(value) for key, value in fields.items() if "发放" in key), None)
            actions.append({"ex_date": ex, "announced": ex,
                            "cash_per_share": float(match.group(1)) / (10 if "10" in amount_key else 1), "split": 1.0,
                            "bonus_taxable": 0.0, "pay_date": payment,
                            "pay_date_assumed": payment is None, "report_date": None})
    if not dividend_table_seen:
        raise ValueError("ETF dividend history missing")
    return sorted(actions, key=lambda row: row["ex_date"]), html


def download_dataset(watchlist: Path, directory: Path, start: str, end: str,
                     etf_code: str = "sh512890", etf_name: str = "红利低波ETF") -> None:
    directory.mkdir(parents=True, exist_ok=True)
    raw_dir = directory / "raw"
    raw_dir.mkdir(exist_ok=True)
    universe = json.loads(watchlist.read_text(encoding="utf-8"))
    universe[etf_code] = {"name": etf_name, "group": "ETF"}

    def download(code: str, spec: dict) -> dict:
        destination = directory / f"{code}.json"
        if destination.exists():
            cached = json.loads(destination.read_text(encoding="utf-8"))
            if cached["requested_start"] == start and cached["requested_end"] == end:
                return {"code": code, "cached": True, "bars": len(cached["prices"])}
        prices, price_raw, dropped = fetch_prices(code, start, end)
        if code == etf_code:
            actions, dividend_raw = fetch_etf_dividends(etf_code)
            financials, finance_raw = [], {}
        else:
            actions, dividend_raw = fetch_dividends(code)
            financials, finance_raw = fetch_financials(code)
        item = {"code": code, "name": spec["name"], "group": spec["group"],
                "weight": spec.get("weight", 0.12), "requested_start": start,
                "requested_end": end, "retrieved_at": datetime.now().astimezone().isoformat(),
                "prices": prices, "actions": actions, "financials": financials,
                "dividend_records": parse_dividend_records(dividend_raw) if code != etf_code else [],
                "dropped_price_bars": dropped}
        (raw_dir / f"{code}.json").write_text(json.dumps({"prices": price_raw,
             "dividends": dividend_raw, "financials": finance_raw}, ensure_ascii=False), encoding="utf-8")
        destination.write_text(json.dumps(item, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"code": code, "bars": len(prices), "actions": len(actions),
                "reports": len(financials), "dropped_bars": len(dropped)}

    failures = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        jobs = {pool.submit(download, code, spec): code for code, spec in universe.items()}
        for job in as_completed(jobs):
            try:
                print(json.dumps(job.result(), ensure_ascii=False), flush=True)
            except Exception as exc:
                failures.append(f"{jobs[job]}: {exc}")
                print(f"ERROR {failures[-1]}", flush=True)
    if failures:
        raise RuntimeError("Incomplete dataset: " + "; ".join(failures))
    manifest = {"start": start, "end": end, "universe": universe, "etf_code": etf_code,
                "sources": [PRICE_URL, DIVIDEND_URL, FINANCE_URL, etf_dividend_url(etf_code)]}
    (directory / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
