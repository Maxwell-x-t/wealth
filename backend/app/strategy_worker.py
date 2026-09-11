"""Bounded subprocess entry point; never sends notifications or mutates the journal."""

from contextlib import redirect_stdout
from dataclasses import asdict, replace
from datetime import date
import io
import json
from pathlib import Path
import sys
import tempfile

from dividend_grid.cli import main, _load_watchlist
from dividend_grid.datasource import make_source
from dividend_grid.portfolio import value_account_data, load_limits, account_etf_capacity
from dividend_grid.rsi6 import Rsi6State, decide, week_id, weekly_rsi
from dividend_grid.rsi6_data import fetch_weekly_bars


def run(payload):
    raw = payload["account"]
    output = {"grid": None, "rsi": None, "errors": []}
    with tempfile.TemporaryDirectory() as directory:
        account_file = Path(directory) / "account.json"
        account_file.write_text(json.dumps(raw), encoding="utf-8")
        captured = io.StringIO()
        with redirect_stdout(captured):
            main(["--account-file", str(account_file), "--stocks-file", "watchlist.json",
                  "--source", "akshare", "--notify", "none", "--json"])
        try:
            output["grid"] = json.loads(captured.getvalue())
        except ValueError:
            print(captured.getvalue(), file=sys.stderr)
            output["errors"].append("个股策略缺少完整数据，暂停生成信号；请稍后重试")
    try:
        limits = load_limits(Path("portfolio_rules.json"))
        source = make_source("akshare")
        account = value_account_data(raw, _load_watchlist(Path("watchlist.json")), source.get_price)
        bars = fetch_weekly_bars("sh512890")
        if week_id(bars[-1].date) != week_id(date.today()):
            raise ValueError("周线行情未更新到本周")
        rsi, open_rsi = weekly_rsi(bars)
        decision, _state = decide(rsi, open_rsi, account.etf_layers,
            Rsi6State.from_dict(payload["rsi_state"]), week_id(bars[-1].date))
        capacity = account_etf_capacity(account, limits, _load_watchlist(Path("watchlist.json")))
        if account.etf_pct >= limits.etf_budget_pct:
            capacity = account.etf_layers
        if decision.action == "BUY":
            target = max(account.etf_layers, min(capacity, decision.target_layers))
            decision = replace(decision, target_layers=target, delta_layers=target-account.etf_layers,
                               action="BUY" if target > account.etf_layers else "HOLD")
        output["rsi"] = dict(asdict(decision), price_date=bars[-1].date.isoformat())
    except Exception as exc:
        print(f"RSI: {exc}", file=sys.stderr)
        output["errors"].append("512890 复权行情不可用，暂停 RSI 信号；请稍后重试")
    return output


if __name__ == "__main__":
    print(json.dumps(run(json.load(sys.stdin)), ensure_ascii=False))
