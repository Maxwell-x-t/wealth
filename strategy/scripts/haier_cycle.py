#!/usr/bin/env python3
"""海尔智家：单股网格「完整周期」收益测算。

背景：默认口径（fiscal 财年）下，海尔智家 2017-2025 的股息率长期只有 1.6%~3.9%，
直到 2026-08-21（FY2025 年度分红 0.8915 元除息）才首次突破 5.1% 进入买入区。
也就是说 **历史上并不存在一个已走完的完整周期** —— 当前只是刚建仓。

本脚本做两件事：
1. `real`：真实历史跑单股隔离回测，列出全部成交，证明「没有完整周期」。
2. `cycle`：用真实 DPS（1.1607 元）构造走完 4.6% -> 6.6% -> 3.8% 的合成价格路径，
   让引擎完整跑一轮「空仓 -> 6/9/12 份 -> 9/6/2 份」，并给出收益分解与周期长短敏感性。

口径：单股隔离，总资产 100 万元，单只满仓权重 0.12 -> 1 份 = 1% = 1 万元。
其余为现金（不计息）；保留现金上限 0，ETF 额度 1%（引擎要求非零）。
"""

from __future__ import annotations

import argparse
import json
from datetime import date, timedelta
from pathlib import Path
import sys

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dividend_grid.backtest import Costs, History, Scenario, Simulation, month_after  # noqa: E402
from dividend_grid.grid import GroupThresholds  # noqa: E402
from dividend_grid.portfolio import PortfolioLimits  # noqa: E402

CODE = "sh600690"
NAME = "海尔智家"
GROUP = "消费"
WEIGHT = 0.12
CAPITAL = 1_000_000.0
DATA = Path("backtests/data-20260910")

YIELD_START = 4.60   # 周期起点：低于买入区，空仓
YIELD_BOTTOM = 6.60  # 周期底部：超过 6.0% 满仓档
YIELD_TOP = 3.80     # 周期顶部：跌破 4.2% 减到底仓


def _limits() -> PortfolioLimits:
    """单股隔离：不留现金、不限行业、几乎不留 ETF 额度。"""
    return PortfolioLimits(min_cash_pct=0.0, max_industry_pct=100.0,
                           industry_limits={}, etf_budget_pct=1.0)


def _scenario() -> Scenario:
    return Scenario("haier", f"{NAME}单股网格", stocks="grid", etf="cash", quality=False)


def _daily_yield_path(anchors: list[tuple[str, float]], end: str) -> pd.Series:
    """按锚点线性插值出每个交易日的目标股息率。"""
    idx = pd.bdate_range(anchors[0][0], end)
    series = pd.Series(index=idx, dtype=float)
    dates = [pd.Timestamp(day) for day, _ in anchors]
    values = [value for _, value in anchors]
    for i in range(len(dates) - 1):
        left, right = dates[i], dates[i + 1]
        mask = (idx >= left) & (idx <= right)
        span = (right - left).days or 1
        series[mask] = [values[i] + (values[i + 1] - values[i])
                        * ((day - left).days / span) for day in idx[mask]]
    return series


def build_anchors(start: str, buy_months: int, sell_months: int) -> list[tuple[str, float]]:
    first = date.fromisoformat(start)
    bottom = month_after(first, buy_months)
    top = month_after(bottom, sell_months)
    return [(first.isoformat(), YIELD_START),
            (bottom.isoformat(), YIELD_BOTTOM),
            (top.isoformat(), YIELD_TOP)]


def cycle_asset(dps: float, anchors: list[tuple[str, float]]) -> dict:
    """按「股息率路径 + 固定 DPS」生成合成行情，并挂上真实金额的年度分红。"""
    end = anchors[-1][0]
    yields = _daily_yield_path(anchors, end)
    prices, previous = [], None
    for day, y in yields.items():
        close = dps / (y / 100)
        opening = close if previous is None else previous
        prices.append({"date": day.date().isoformat(), "open": round(opening, 3),
                       "close": round(close, 3),
                       "high": round(max(opening, close) * 1.006, 3),
                       "low": round(min(opening, close) * 0.994, 3),
                       "volume": 120000.0})
        previous = close
    # 年度分红：播种日必须在回测区间内，否则引擎不会登记该财年 DPS；
    # 除息日必须落在交易日历上（周末会被整段跳过）。
    actions, ex = [], date.fromisoformat(anchors[0][0]) + timedelta(days=9)
    final = date.fromisoformat(end)
    while ex <= final:
        ex = pd.bdate_range(ex, ex + timedelta(days=3))[0].date()
        if ex > final:
            break
        actions.append({"ex_date": ex.isoformat(),
                        "announced": (ex - timedelta(days=6)).isoformat(),
                        "cash_per_share": dps, "split": 1.0, "bonus_taxable": 0.0,
                        "pay_date": None, "pay_date_assumed": True,
                        "report_date": f"{ex.year - 1}-12-31"})
        ex = month_after(ex, 12)
    return {"code": CODE, "name": NAME, "group": GROUP, "weight": WEIGHT,
            "requested_start": anchors[0][0], "requested_end": end,
            "retrieved_at": "synthetic", "prices": prices, "actions": actions,
            "financials": []}


def _run(asset: dict, caliber: str, start: str, end: str) -> dict:
    history = History({CODE: asset}, caliber=caliber, etf_code="sh512890")
    sim = Simulation(history, _scenario(), {"default": GroupThresholds()},
                     _limits(), start, end, CAPITAL, Costs())
    return sim.run()


def _summarize(result: dict, start_equity: float) -> dict:
    """收益分解：价格收益 = 卖出所得 + 期末市值 − 买入成本；再加分红、减费用。"""
    trades = result["trades"]
    bought = sum(t["notional"] for t in trades if t["side"] == "BUY")
    sold = sum(t["notional"] for t in trades if t["side"] == "SELL")
    fees = sum(t["fees"] for t in trades) + sum(t["dividend_tax"] for t in trades)
    dividends = sum(event["gross"] for event in result["dividend_events"])
    attribution = result["attribution"][0]
    ending_position = attribution["ending_value"]
    ending = result["metrics"]["ending_equity"]
    price_gain = sold + ending_position - bought
    return {"trades": len(trades),
            "shares_bought": sum(t["quantity"] for t in trades if t["side"] == "BUY"),
            "shares_sold": sum(t["quantity"] for t in trades if t["side"] == "SELL"),
            "bought": bought, "sold": sold, "fees": fees, "gross_dividends": dividends,
            "price_gain": price_gain, "ending_position_value": ending_position,
            "ending_equity": ending, "profit": ending - start_equity,
            "return_pct": (ending / start_equity - 1) * 100,
            "return_on_sleeve_pct": (ending - start_equity) / (CAPITAL * WEIGHT) * 100}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("backtests/haier_cycle_20260911.json"))
    parser.add_argument("--detail", action="store_true", help="打印逐笔成交")
    args = parser.parse_args()

    real_asset = json.loads((DATA / f"{CODE}.json").read_text(encoding="utf-8"))
    real = History({CODE: real_asset}, caliber="fiscal", etf_code="sh512890")
    frame = real.frames[CODE]
    tail = frame[frame.index >= "2026-08-01"]
    dps = float((tail["yield"] * tail["close"] / 100).iloc[-1])

    # ---------- 1) 真实历史：有没有完整周期 ----------
    real_run = _run(real_asset, "fiscal", "2017-01-01", "2026-09-09")
    real_summary = _summarize(real_run, CAPITAL)
    first_buy = next((t["date"] for t in real_run["trades"] if t["side"] == "BUY"), None)
    max_yield = float(frame[frame.index >= "2017-01-01"]["yield"].max())
    first_zone = frame[(frame.index >= "2017-01-01") & (frame["yield"] >= 5.1)].index.min()

    # ---------- 2) 合成完整周期 + 敏感性 ----------
    sensitivities = []
    for total_months in (18, 24, 32, 48):
        buy_months = round(total_months * 0.35)
        anchors = build_anchors("2026-07-06", buy_months, total_months - buy_months)
        end = anchors[-1][0]
        run = _run(cycle_asset(dps, anchors), "fiscal", anchors[0][0], end)
        summary = _summarize(run, CAPITAL)
        sensitivities.append({"total_months": total_months, "anchors": anchors,
                              "summary": summary, "trades": run["trades"]})

    base = next(row for row in sensitivities if row["total_months"] == 32)
    payload = {
        "code": CODE, "name": NAME, "capital": CAPITAL, "dps": dps,
        "share_value_yuan": CAPITAL * WEIGHT / 12.0,
        "real_history": {
            "window": "2017-01-01 ~ 2026-09-09", "caliber": "fiscal",
            "max_yield_pct": round(max_yield, 4), "first_buy_date": first_buy,
            "first_time_yield_ge_5_1pct": first_zone, "complete_cycle": False,
            "summary": real_summary, "trades": real_run["trades"],
        },
        "synthetic_cycle": {
            "note": f"固定 DPS，股息率 {YIELD_START}% -> {YIELD_BOTTOM}% -> {YIELD_TOP}%",
            "base_anchors": base["anchors"], "base_summary": base["summary"],
            "base_trades": base["trades"], "base_dividend_events": base["dividend_events"]
            if "dividend_events" in base else [],
            "sensitivity": [{k: v for k, v in row.items() if k != "trades"}
                            for row in sensitivities],
        },
    }
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False),
                           encoding="utf-8")

    print(f"DPS = {dps:.4f} 元/股   1 份 = {CAPITAL * WEIGHT / 12.0:,.0f} 元 "
          f"（12 份 = {CAPITAL * WEIGHT:,.0f} 元）")
    print(f"\n[1] 真实历史 2017-01-01 ~ 2026-09-09（fiscal 口径）")
    print(f"    股息率历史最高 {max_yield:.2f}% ；首次 >=5.1% 在 {first_zone}")
    print(f"    成交 {real_summary['trades']} 笔，首次买入 {first_buy}")
    print(f"    期末资产 {real_summary['ending_equity']:,.0f} 元，盈亏 {real_summary['profit']:+,.0f} 元")
    print(f"    → 未走完任何完整周期（甚至还没买满 6 份）")

    print(f"\n[2] 合成完整周期（基准 32 个月，{YIELD_START}% -> {YIELD_BOTTOM}% -> {YIELD_TOP}%）")
    s = base["summary"]
    print(f"    买入 {s['shares_bought']:,.0f} 股 / {s['bought']:,.0f} 元；"
          f"卖出 {s['shares_sold']:,.0f} 股 / {s['sold']:,.0f} 元")
    print(f"    价格收益 {s['price_gain']:+,.0f}（含期末底仓 {s['ending_position_value']:,.0f}）"
          f" | 分红 {s['gross_dividends']:+,.0f} | 费用 {s['fees']:,.0f}")
    print(f"    完整周期盈亏 {s['profit']:+,.0f} 元"
          f"（对 100 万账户 {s['return_pct']:+.2f}%，对 12 万仓位 {s['return_on_sleeve_pct']:+.2f}%）")

    print(f"\n[3] 周期长短敏感性")
    print(f"    {'周期':>6}{'买入':>12}{'卖出':>12}{'分红':>10}{'盈亏':>12}{'对12万仓位':>12}")
    for row in sensitivities:
        r = row["summary"]
        print(f"    {row['total_months']:>4}月{r['bought']:>12,.0f}{r['sold']:>12,.0f}"
              f"{r['gross_dividends']:>10,.0f}{r['profit']:>+12,.0f}"
              f"{r['return_on_sleeve_pct']:>+11.1f}%")

    if args.detail:
        print("\n    逐笔成交（基准 32 个月）：")
        for t in base["trades"]:
            print(f"      {t['date']} {t['side']:<4} {t['quantity']:>7.0f} 股 "
                  f"@{t['price']:.3f}  {t['reason']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
