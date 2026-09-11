"""Extend a cached price dataset and add dated coverage history from public providers."""

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dividend_grid.backtest_coverage import parse_banks, parse_cashflows, parse_cash_dividends
from dividend_grid.backtest_data import (DIVIDEND_URL, FINANCE_URL, PRICE_URL, fetch_dividends,
                                         fetch_etf_dividends, fetch_financials, parse_prices, request)
from dividend_grid.coverage import CoverageConfig, digits, secucode


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--end", required=True)
    args = parser.parse_args()
    if args.base.resolve() == args.output.resolve():
        parser.error("Use a new directory to preserve the original data")
    manifest = json.loads((args.base / "manifest.json").read_text())
    if args.end < manifest["end"]:
        parser.error("End date precedes the base dataset")
    (args.output / "raw").mkdir(parents=True, exist_ok=True)
    cfg = CoverageConfig()

    def download(code, spec):
        output = args.output / f"{code}.json"
        if output.exists():
            item = json.loads(output.read_text())
            if item.get("requested_end") == args.end and (spec["group"] == "ETF" or "coverage" in item):
                if spec["group"] != "ETF":
                    raw = json.loads((args.output / "raw" / f"{code}.json").read_text())
                    item["coverage"]["dividends"] = parse_cash_dividends(raw["dividends"])
                    if "bank_history" in raw:
                        item["coverage"]["banks"] = parse_banks(raw["bank_history"])
                    output.write_text(json.dumps(item, ensure_ascii=False, indent=2))
                return {"code": code, "cached": True}
        item = json.loads((args.base / f"{code}.json").read_text())
        raw = json.loads((args.base / "raw" / f"{code}.json").read_text())
        params = {"param": f"{code},day,{manifest['end']},{args.end},640,"}
        raw["prices"].append({"params": params, "response": request(PRICE_URL, params).json()})
        item["prices"], item["dropped_price_bars"] = parse_prices(
            code, raw["prices"], item["requested_start"], args.end)
        if item["prices"][-1]["date"] != args.end:
            raise ValueError(f"No closing price at {args.end}: {code}")
        if spec["group"] == "ETF":
            item["actions"], raw["dividends"] = fetch_etf_dividends(code)
        else:
            item["actions"], raw["dividends"] = fetch_dividends(code)
            item["financials"], raw["financials"] = fetch_financials(code)
            coverage = {"dividends": parse_cash_dividends(raw["dividends"]), "banks": [], "cashflows": []}
            if digits(code) in cfg.bank_codes:
                raw["bank_history"] = request(DIVIDEND_URL, {
                    "reportName": "RPT_F10_FINANCE_MAINFINADATA", "columns": "ALL", "pageSize": "500",
                    "sortColumns": "REPORT_DATE", "sortTypes": "-1",
                    "filter": f'(SECUCODE="{secucode(code)}")'}).json()
                coverage["banks"] = parse_banks(raw["bank_history"])
            elif digits(code) not in cfg.insurance_codes:
                raw["cashflow_history"] = request(FINANCE_URL, {
                    "paperCode": code, "source": "llb", "type": "0", "page": "1", "num": "1000"}).json()
                coverage["cashflows"] = parse_cashflows(raw["cashflow_history"])
            item["coverage"] = coverage
        item["requested_end"] = args.end
        item["retrieved_at"] = datetime.now().astimezone().isoformat()
        (args.output / "raw" / f"{code}.json").write_text(json.dumps(raw, ensure_ascii=False))
        output.write_text(json.dumps(item, ensure_ascii=False, indent=2))
        return {"code": code, "end": item["prices"][-1]["date"],
                "coverage_reports": len(item.get("coverage", {}).get("cashflows", [])) +
                                    len(item.get("coverage", {}).get("banks", []))}

    failures = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        jobs = {pool.submit(download, code, spec): code for code, spec in manifest["universe"].items()}
        for job in as_completed(jobs):
            try:
                print(json.dumps(job.result(), ensure_ascii=False), flush=True)
            except Exception as exc:
                failures.append(f"{jobs[job]}: {exc}")
                print(f"ERROR {failures[-1]}", flush=True)
    if failures:
        raise RuntimeError("Incomplete dataset: " + "; ".join(failures))
    manifest.update(end=args.end, coverage_history=True, base_dataset=str(args.base),
                    retrieved_at=datetime.now().astimezone().isoformat())
    (args.output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
