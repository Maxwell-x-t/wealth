"""512890 周 RSI6 独立策略命令行入口。"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Optional

from .notifiers import ConsoleNotifier, WeComWebhookNotifier
from .rsi6 import (
    CODE,
    NAME,
    Rsi6State,
    build_market_message,
    build_market_snapshot,
    build_message,
    decide,
    render_table,
    weekly_rsi,
    week_id,
)
from .rsi6_data import fetch_weekly_bars
from .portfolio import account_etf_capacity, account_path, load_limits, load_etf_layers, stock_position_pct, value_account, read_account
from .datasource import make_source


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"配置必须是 JSON 对象: {path}")
    return data


def _load_layers(path: Path, code: str) -> int:
    return load_etf_layers(path)


def _load_state(path: Path) -> Rsi6State:
    return Rsi6State.from_dict(_load_json(path))


def _account_state(path: Path, actual_path: Path | None) -> Rsi6State:
    state = _load_state(path)
    recorded = read_account(actual_path).get("rsi_state") if actual_path else None
    if not recorded or not recorded.get("frozen"):
        return state
    actual = Rsi6State.from_dict(recorded)
    if actual.week != week_id(date.today()):
        return state
    if state.week != actual.week:
        return actual
    if state.sell_base is None:
        state.sell_base = actual.sell_base
    state.planned_sell_layers = max(state.planned_sell_layers, actual.planned_sell_layers)
    state.frozen = True
    return state


def _write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="512890 周线 RSI(6) 层仓提醒")
    parser.add_argument(
        "--mode",
        choices=["market", "layers"],
        default="market",
        help="market=价格触发提醒（默认），layers=层仓决策",
    )
    parser.add_argument(
        "--session",
        choices=["morning", "midday", "close"],
        default="close",
        help="market 模式：morning=开盘前，midday=盘中，close=收盘后",
    )
    parser.add_argument("--layers-file", default="rsi6_holdings.json", help="512890 层数配置")
    parser.add_argument("--state-file", default="rsi6_state.json", help="本周卖出状态文件")
    parser.add_argument("--portfolio-file", default="portfolio_rules.json", help="统一资金限制")
    parser.add_argument("--stocks-file", default="watchlist.json", help="个股监控清单")
    parser.add_argument("--holdings-file", default="holdings.json", help="个股当前份数")
    parser.add_argument("--account-file", default=None, help="实际股数和现金账户；默认自动检测 account.json")
    parser.add_argument("--pool", type=float, default=0.12, help="个股默认满仓权重")
    parser.add_argument("--notify", choices=["none", "console", "wecom"], default="none")
    parser.add_argument("--notify-on", choices=["signal", "always"], default="signal")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    layers_path = Path(args.layers_file)
    state_path = Path(args.state_file)
    try:
        from .cli import _load_watchlist

        limits = load_limits(Path(args.portfolio_file))
        specs = _load_watchlist(Path(args.stocks_file))
        actual_path = account_path(args.account_file, legacy_override=args.holdings_file != "holdings.json")
        account = value_account(actual_path, specs, make_source("akshare").get_price, args.pool) if actual_path else None
        if account:
            current_layers, stock_pct, etf_pct = account.etf_layers, account.stock_pct, account.etf_pct
            account_notes = account.notes()
        else:
            current_layers = _load_layers(layers_path, CODE)
            with Path(args.holdings_file).open(encoding="utf-8") as handle:
                holdings = json.load(handle)
            if any(code == CODE for code, *_ in specs) or CODE in holdings:
                raise ValueError("ETF 不能重复计入个股清单或持仓")
            stock_pct = stock_position_pct(holdings, specs, args.pool)
            etf_pct = current_layers * limits.layer_pct
            account_notes = ["手工仓位估算：ETF 层数未按市值重估，实际资金需核对"]
        if stock_pct + etf_pct + (account.other_pct if account else 0) > 100.0 + 1e-8:
            raise ValueError("个股与 ETF 当前总仓位超过 100%，请校准持仓")
        capacity = account_etf_capacity(account, limits, specs) if account else limits.etf_capacity(stock_pct)
        if account and etf_pct >= limits.etf_budget_pct:
            capacity = current_layers
        bars = fetch_weekly_bars(CODE)
        if args.mode == "market":
            current_week = week_id(date.today())
            has_current_week = week_id(bars[-1].date) == current_week
            completed_bars = bars[:-1] if has_current_week else bars
            reference_bars = bars
            if len(reference_bars) < 7:
                raise ValueError("完整周线数据不足，无法计算 RSI(6)")
            trigger_bars = completed_bars if len(completed_bars) >= 7 else bars
            rsi, open_rsi = weekly_rsi(bars)
            _decision, state = decide(rsi, open_rsi, current_layers, _account_state(state_path, actual_path),
                                      week_id(bars[-1].date))
            sell_base = state.sell_base if state.sell_base is not None else current_layers
            snapshot = build_market_snapshot(
                reference_bars,
                label="最近交易日" if args.session == "morning" else "今日",
                trigger_bars=trigger_bars,
                current_layers=current_layers,
                max_buy_layers=min(capacity, current_layers) if state.frozen else capacity,
                layer_pct=limits.layer_pct,
                sell_target_74=max(sell_base - 2, 0),
                current_value_pct=etf_pct if account else None,
            )
            if _decision.pending_sell_layers:
                snapshot = replace(snapshot, notes=(*snapshot.notes,
                    f"本周已记录卖出目标{_decision.target_layers}层，待卖出{_decision.pending_sell_layers}层。"))
            snapshot = replace(snapshot, notes=(*snapshot.notes, *account_notes))
            _write_json(state_path, state.to_dict())
        else:
            rsi, open_rsi = weekly_rsi(bars)
            current_week = week_id(bars[-1].date)
            decision, state = decide(
                rsi,
                open_rsi,
                current_layers,
                _account_state(state_path, actual_path),
                current_week,
                code=CODE,
                name=NAME,
            )
            if decision.action == "BUY" and decision.target_layers > capacity:
                target = max(current_layers, capacity)
                decision = replace(decision, target_layers=target,
                                   delta_layers=target - current_layers,
                                   action="BUY" if target > current_layers else "HOLD",
                                   note="买入目标受实际仓位、现金或行业限额约束")
            if account and decision.action == "BUY":
                amount = max(0, min(decision.target_layers * limits.layer_pct / 100 * account.equity
                                    - account.values.get(CODE, 0),
                                    account.cash - limits.min_cash_pct / 100 * account.equity))
                if amount == 0:
                    decision = replace(decision, action="HOLD", target_layers=current_layers,
                                       delta_layers=0, note="实际市值已达目标或可用现金不足")
                else:
                    decision = replace(decision, note=f"目标增配预算（含费用）{amount:,.2f}元")
            if account and decision.action == "SELL" and current_layers:
                quantity = account.quantities.get(CODE, 0) * decision.pending_sell_layers / current_layers
                decision = replace(decision, note=f"按实际份额比例减仓约{quantity:.0f}份，成交数量需按交易单位取整")
            _write_json(state_path, state.to_dict())
    except Exception as exc:  # noqa: BLE001
        print(f"[错误] RSI6 策略执行失败: {exc}", file=sys.stderr)
        return 1

    if args.mode == "market":
        title, content = build_market_message(
            snapshot,
            morning=args.session == "morning",
            midday=args.session == "midday",
        )
        print(content)
    else:
        title, content = build_message(decision)
        content += "\n" + "\n".join(account_notes)
        print(render_table(decision))
        print("\n".join(account_notes))
    if args.notify != "none":
        should_notify = args.notify_on == "always" or (
            (args.mode == "layers" and decision.triggered)
            or (args.mode == "market" and any(t.active for t in snapshot.triggers))
        )
        if not should_notify:
            print("\n[提醒] RSI6 无触发信号，按 --notify-on=signal 未推送。")
            return 0
        try:
            if args.notify == "console":
                ConsoleNotifier().send(title, content)
            else:
                WeComWebhookNotifier().send(title, content)
            print("\n[提醒] RSI6 已推送到企业微信。" if args.notify == "wecom" else "")
        except Exception as exc:  # noqa: BLE001
            print(f"\n[提醒] RSI6 企业微信推送失败: {exc}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
