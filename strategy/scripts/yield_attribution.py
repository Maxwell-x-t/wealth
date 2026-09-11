"""按"价格"与"分红"两个来源分解股息率变化，并对比两种 TTM 口径。

回答两个问题：
1. 某只股票股息率上升，是因为股价跌了（真的便宜），还是因为每股分红涨了？
2. 现行"过去 365 天已除息分红求和"的口径，在除息日逐年漂移时是否会算错？

用法：
    .venv/bin/python scripts/yield_attribution.py
    .venv/bin/python scripts/yield_attribution.py --code sh600036 --chart
"""

from __future__ import annotations

import argparse
import json
import math
import os
from datetime import date, timedelta
from pathlib import Path
import tempfile

os.environ.setdefault("MPLCONFIGDIR", str(Path(tempfile.gettempdir()) / "dividend-grid-matplotlib"))

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates
from matplotlib import font_manager
from matplotlib.ticker import FuncFormatter
import matplotlib.pyplot as plt
import pandas as pd

ETF = "sh512890"


def _scale(actions_ex: str, day: str, splits: list[tuple[str, float]]) -> float:
    factor = 1.0
    for split_date, ratio in splits:
        if actions_ex < split_date <= day:
            factor *= ratio
    return factor


def ttm_dps_naive(actions: list[dict], day: str, splits: list[tuple[str, float]]) -> float:
    """现行口径：过去 365 天内已除息的分红直接求和（回测引擎现行实现）。"""
    start = (date.fromisoformat(day) - timedelta(days=365)).isoformat()
    return sum(action["cash_per_share"] * _scale(action["ex_date"], day, splits)
               for action in actions if start < action["ex_date"] <= day)


def ttm_dps_fiscal(actions: list[dict], day: str, splits: list[tuple[str, float]]) -> float:
    """修正口径：取最近一个"年度分红已实施"的完整财年，汇总该财年全部已实施分红。"""
    done = [a for a in actions if a["ex_date"] <= day and a.get("report_date")]
    fiscal_years = sorted({a["report_date"][:4] for a in done
                           if a["report_date"].endswith("12-31")})
    for year in reversed(fiscal_years):
        annual = [a for a in done if a["report_date"] == f"{year}-12-31"]
        if not annual:
            continue
        return sum(a["cash_per_share"] * _scale(a["ex_date"], day, splits)
                   for a in done if a["report_date"][:4] == year)
    return 0.0


def series(asset: dict, method) -> pd.DataFrame:
    splits = [(a["ex_date"], a["split"]) for a in asset["actions"] if a["split"] != 1]
    rows = []
    for row in asset["prices"]:
        day = row["date"]
        price = float(row["close"])
        dps = method(asset["actions"], day, splits)
        rows.append({"date": day, "price": price, "dps": dps,
                     "yield": dps / price * 100 if price > 0 else 0.0})
    return pd.DataFrame(rows).set_index("date")


def band(value: float, buy, sell) -> int:
    """把股息率映射到网格档位：3/2/1=买入档，0=持有带，-1/-2/-3=卖出档。"""
    b1, b2, b3 = buy
    s1, s2, s3 = sell
    if value >= b3:
        return 3
    if value >= b2:
        return 2
    if value >= b1:
        return 1
    if value >= s1:
        return 0
    if value >= s2:
        return -1
    if value >= s3:
        return -2
    return -3


def decompose(frame: pd.DataFrame, day0: str, day1: str) -> dict:
    """ln(y1/y0) = ln(dps1/dps0) - ln(p1/p0)。"""
    y0, y1 = frame.at[day0, "yield"], frame.at[day1, "yield"]
    p0, p1 = frame.at[day0, "price"], frame.at[day1, "price"]
    d0, d1 = frame.at[day0, "dps"], frame.at[day1, "dps"]
    dividend = math.log(d1 / d0) if d0 > 0 and d1 > 0 else 0.0
    price = -math.log(p1 / p0)
    total = dividend + price
    return {"yield0": y0, "yield1": y1, "price0": p0, "price1": p1, "dps0": d0, "dps1": d1,
            "dividend": dividend, "price": price, "total": total,
            "dividend_share": dividend / total if abs(total) > 1e-9 else 0.0}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, default=Path("backtests/data-20260909"))
    parser.add_argument("--code", default="sh600036")
    parser.add_argument("--chart", action="store_true")
    parser.add_argument("--output", type=Path, default=Path("backtests"))
    args = parser.parse_args()

    manifest = json.loads((args.data / "manifest.json").read_text(encoding="utf-8"))
    assets = {code: json.loads((args.data / f"{code}.json").read_text(encoding="utf-8"))
              for code in manifest["universe"]}
    groups = json.loads(Path("strategy_groups.json").read_text(encoding="utf-8"))
    naive = {c: series(a, ttm_dps_naive) for c, a in assets.items()}
    fiscal = {c: series(a, ttm_dps_fiscal) for c, a in assets.items()}
    for frame in list(naive.values()) + list(fiscal.values()):
        frame.drop(frame.index[frame.index < "2019-01-02"], inplace=True)

    codes = [c for c in assets if c != ETF]
    print("一、股息率变化的来源分解（2019 首个交易日 → 2026-09-09，按财年口径）")
    print(f"{'代码':<10}{'名称':<10}{'起股息':>8}{'终股息':>8}{'股价':>16}{'每股分红':>17}"
          f"{'分红贡献':>10}{'价格贡献':>10}")
    rows = []
    for code in codes:
        asset, frame = assets[code], fiscal[code]
        d = decompose(frame, frame.index[0], frame.index[-1])
        rows.append((code, asset["name"], d))
        print(f"{code:<10}{asset['name']:<10}{d['yield0']:>7.2f}%{d['yield1']:>7.2f}%"
              f"{d['price0']:>8.2f}→{d['price1']:<7.2f}{d['dps0']:>8.3f}→{d['dps1']:<8.3f}"
              f"{d['dividend_share'] * 100:>9.1f}%{(1 - d['dividend_share']) * 100:>9.1f}%")

    print("\n二、现行 TTM 口径 vs 按财年口径：最新股息率差异")
    print(f"{'代码':<10}{'名称':<10}{'现行口径':>10}{'财年口径':>10}{'档位翻转天数':>14}{'占交易天数':>11}")
    total_flips = 0
    total_days = 0
    for code in codes:
        asset = assets[code]
        buy = groups.get(asset["group"], groups["default"])["buy"]
        sell = groups.get(asset["group"], groups["default"])["sell"]
        joined = naive[code][["yield"]].rename(columns={"yield": "naive"}).join(
            fiscal[code][["yield"]].rename(columns={"yield": "fiscal"}), how="inner")
        flipped = (joined["naive"].apply(lambda v: band(v, buy, sell))
                   != joined["fiscal"].apply(lambda v: band(v, buy, sell)))
        count, days = int(flipped.sum()), len(joined)
        total_flips += count
        total_days += days
        print(f"{code:<10}{asset['name']:<10}{joined['naive'].iloc[-1]:>9.2f}%"
              f"{joined['fiscal'].iloc[-1]:>9.2f}%{count:>14}{count / days * 100:>10.1f}%")
    print(f"{'合计':<20}{'':>10}{'':>10}{total_flips:>14}{total_flips / total_days * 100:>10.1f}%")

    print("\n三、各股股息率极值（按财年口径，2019 起）")
    for code in codes:
        frame, asset = fiscal[code], assets[code]
        hi, lo = frame["yield"].idxmax(), frame["yield"].idxmin()
        print(f"  {asset['name']:<8} 最高 {frame.at[hi, 'yield']:>6.2f}% @{hi}  "
              f"最低 {frame.at[lo, 'yield']:>6.2f}% @{lo}  最新 {frame['yield'].iloc[-1]:>5.2f}%")

    if args.chart:
        render(naive, fiscal, assets, args.code, args.output)


def render(naive, fiscal, assets, focus, output):
    font_path = Path("/System/Library/Fonts/Supplemental/Arial Unicode.ttf")
    if font_path.exists():
        font_manager.fontManager.addfont(str(font_path))
        plt.rcParams["font.family"] = font_manager.FontProperties(fname=str(font_path)).get_name()
    plt.rcParams.update({"axes.unicode_minus": False, "font.size": 10,
                         "axes.spines.top": False, "axes.spines.right": False,
                         "axes.edgecolor": "#c9ced3", "text.color": "#24282d",
                         "axes.labelcolor": "#41474e", "xtick.color": "#626970",
                         "ytick.color": "#626970"})
    frame = fiscal[focus]
    dates = pd.to_datetime(frame.index)
    figure, axes = plt.subplots(3, 1, figsize=(12.4, 11.0), sharex=True,
                                gridspec_kw={"height_ratios": [1.2, 1, 1]})
    base_dps, base_price = frame["dps"].iloc[0], frame["price"].iloc[0]
    axes[0].plot(dates, frame["yield"], color="#13876d", linewidth=2.4, label="实际股息率")
    axes[0].plot(dates, base_dps / frame["price"] * 100, color="#286fbd", linewidth=1.5,
                 linestyle="--", label="若每股分红不变（纯价格驱动）")
    axes[0].plot(dates, frame["dps"] / base_price * 100, color="#b8653c", linewidth=1.5,
                 linestyle=":", label="若股价不变（纯分红驱动）")
    axes[0].fill_between(dates, frame["yield"], base_dps / frame["price"] * 100,
                         color="#b8653c", alpha=0.10)
    axes[0].axhspan(4.9, 5.5, color="#888780", alpha=0.10)
    axes[0].set_ylabel("股息率")
    axes[0].yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:.0f}%"))
    axes[0].legend(loc="upper left", frameon=False, fontsize=9)
    axes[0].grid(axis="y", color="#e8ebed", linewidth=0.7)

    axes[1].plot(dates, naive[focus]["yield"], color="#E24B4A", linewidth=1.4,
                 label="现行口径（过去 365 天求和）")
    axes[1].plot(dates, frame["yield"], color="#13876d", linewidth=1.8, label="按财年口径")
    axes[1].set_ylabel("股息率")
    axes[1].yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:.0f}%"))
    axes[1].legend(loc="upper left", frameon=False, fontsize=9)
    axes[1].grid(axis="y", color="#e8ebed", linewidth=0.7)

    axes[2].plot(dates, frame["dps"], color="#b8653c", linewidth=2.0)
    axes[2].set_ylabel("每股分红(元)", color="#b8653c")
    axes[2].tick_params(axis="y", colors="#b8653c")
    axes[2].grid(axis="y", color="#e8ebed", linewidth=0.7)
    other = axes[2].twinx()
    other.plot(dates, frame["price"], color="#5f666e", linewidth=1.4)
    other.set_ylabel("收盘价(元)", color="#5f666e")
    other.tick_params(axis="y", colors="#5f666e")
    other.spines["top"].set_visible(False)

    axes[2].xaxis.set_major_locator(mdates.YearLocator())
    axes[2].xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    figure.suptitle(f"{assets[focus]['name']} 股息率拆解：分子、分母与口径误差", x=0.07, ha="left",
                    fontsize=17, fontweight="bold")
    figure.text(0.07, 0.945,
                "灰带＝该股当前持有带 4.9%~5.5%；第一格阴影＝完全由分红增长贡献的部分",
                color="#5f666e", fontsize=10)
    figure.subplots_adjust(top=0.89, bottom=0.06, left=0.075, right=0.92, hspace=0.14)
    path = output / "yield_attribution.png"
    figure.savefig(path, dpi=180, facecolor="white")
    plt.close(figure)
    print(f"\n图已保存: {path}")


if __name__ == "__main__":
    main()
