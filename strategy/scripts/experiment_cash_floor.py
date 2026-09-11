"""实验：给「当前策略」加一个更高现金下限（=更低权益暴露上限），看收益/回撤取舍。

min_cash_pct 直接决定权益暴露上限：上限 ≈ 100% - min_cash_pct - (ETF 预留中未持有部分)。
在三个窗口上扫描 min_cash_pct = 10/20/30/40，其余参数不变。
"""

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dividend_grid.backtest import Costs, History, SCENARIOS, Simulation
from dividend_grid.grid import GroupThresholds
from dividend_grid.portfolio import PortfolioLimits


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cash-floors", nargs="+", type=float, default=[10, 20, 30, 40])
    args = parser.parse_args()

    raw_groups = json.loads(Path("strategy_groups.json").read_text(encoding="utf-8"))
    groups = {g: GroupThresholds(tuple(v["buy"]), tuple(v["sell"])) for g, v in raw_groups.items()}
    current = next(s for s in SCENARIOS if s.key == "current")

    windows = [
        ("主样本 2019-2026", "backtests/data-20260910", "2019-01-01", "2026-09-09"),
        ("2008 熊市窗口", "backtests/data-bear", "2007-01-01", "2012-12-31"),
        ("2015 熊市窗口", "backtests/data-bear", "2013-01-01", "2017-12-31"),
    ]

    for label, data, start, end in windows:
        hist = History.load(Path(data), caliber="fiscal")
        print("=" * 78)
        print(f"{label}   ETF={hist.etf_code}")
        print(f"  {'现金下限':<8}{'年化':>9}{'最大回撤':>11}{'夏普':>8}{'均暴露':>9}{'峰值暴露':>10}{'单位暴露效率':>14}")
        for mc in args.cash_floors:
            limits = PortfolioLimits(min_cash_pct=mc, max_industry_pct=30.0, etf_budget_pct=20.0)
            result = Simulation(hist, current, groups, limits, start, end).run()
            m = result["metrics"]
            daily = result["daily"]
            exp = [(d["stock_value"] + d["etf_value"]) / d["equity"] * 100 for d in daily]
            avg_exp = sum(exp) / len(exp)
            eff = m["cagr"] * 100 / avg_exp if avg_exp else float("nan")
            print(f"  {mc:>5.0f}%  {m['cagr'] * 100:>8.2f}%{m['max_drawdown'] * 100:>10.2f}%"
                  f"{m['sharpe_zero_rf']:>8.2f}{avg_exp:>8.1f}%{max(exp):>9.1f}%{eff:>13.3f}%")


if __name__ == "__main__":
    main()
