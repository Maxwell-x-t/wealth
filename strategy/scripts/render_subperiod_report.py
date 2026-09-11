"""对照多个回测窗口，检验结论是否只是某段行情的产物。

用法：
    python scripts/render_subperiod_report.py \
        --window 2019-2026=backtests/results-20260910 \
        --window 2022-起=backtests/results-2022plus-20260910 \
        --window 2023-起=backtests/results-2023plus-20260910 \
        --output backtests/subperiod-20260910
"""

import argparse
import json
import os
from datetime import date as _date
from pathlib import Path
import tempfile

os.environ.setdefault("MPLCONFIGDIR", str(Path(tempfile.gettempdir()) / "dividend-grid-matplotlib"))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.ticker import FuncFormatter


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def pct(value):
    return f"{value * 100:.2f}%"


SHORT = {
    "current": "当前策略（网格+RSI）",
    "baseline_naive": "旧 365 天口径",
    "dynamic_a": "方案A 分位阈值",
    "dynamic_b": "方案B 归一化",
    "dynamic_ab": "方案A+B",
    "etf_rsi_only": "仅 RSI 分层版",
    "rsi_all_in": "仅 RSI 梭哈版",
    "grid_hold_etf": "网格+ETF持有",
    "grid_only": "仅股息率网格",
    "hold": "同清单买入持有",
    "etf_hold": "90% ETF 持有",
    "no_quality": "关闭质量筛查",
}

# 图里只画最关键的几组，避免 12×3 根柱子糊成一片。
CHART_KEYS = ["current", "grid_only", "no_quality", "etf_rsi_only", "rsi_all_in", "hold", "etf_hold"]


def setup_fonts():
    font_path = Path("/System/Library/Fonts/Supplemental/Arial Unicode.ttf")
    if font_path.exists():
        font_manager.fontManager.addfont(str(font_path))
        plt.rcParams["font.family"] = font_manager.FontProperties(fname=str(font_path)).get_name()
    plt.rcParams.update({"axes.unicode_minus": False, "font.size": 10, "axes.spines.top": False,
                         "axes.spines.right": False, "axes.edgecolor": "#c9ced3",
                         "text.color": "#24282d", "axes.labelcolor": "#41474e",
                         "xtick.color": "#626970", "ytick.color": "#626970"})


WINDOW_COLORS = ["#aeb9c2", "#378ADD", "#0F6E56"]


def load(directory, keys):
    summary = read(directory / "summary.json")
    rows = {}
    for row in summary["results"]:
        if row["key"] not in keys:
            continue
        entry = dict(row)
        exposure = 100 - row["average_cash_pct"]
        entry["exposure"] = exposure
        entry["efficiency"] = row["cagr"] * 100 / exposure if exposure else 0.0
        entry["round_trips"] = round_trips(read(directory / f'{row["key"]}.json')["trades"])
        rows[row["key"]] = entry
    return rows


def round_trips(trades):
    """买入后 7 个自然日内又卖出的次数，用作噪音交易指示。"""
    import collections
    by_code = collections.defaultdict(list)
    for trade in trades:
        by_code[trade["code"]].append(trade)
    count = 0
    for series in by_code.values():
        for i, trade in enumerate(series):
            if trade["side"] != "BUY" or i + 1 >= len(series):
                continue
            nxt = series[i + 1]
            if nxt["side"] == "SELL" and (
                    _date.fromisoformat(nxt["date"]) - _date.fromisoformat(trade["date"])).days <= 7:
                count += 1
    return count


def comparison_chart(windows, path):
    labels = [name for name, _ in windows]
    figure, axes = plt.subplots(2, 1, figsize=(12.8, 9.4))
    group_width = 0.82
    bar_width = group_width / len(windows)

    panels = [
        {"field": "cagr", "title": "年化收益", "scale": 100.0, "label": "{:.2f}%", "tick": "{:.0f}%"},
        {"field": "efficiency", "title": "单位暴露效率（年化收益 / 平均权益暴露）",
         "scale": 1.0, "label": "{:.3f}%", "tick": "{:.1f}%"},
    ]
    for panel, (axis, spec) in enumerate(zip(axes, panels)):
        positions = range(len(CHART_KEYS))
        for index, (name, rows) in enumerate(windows):
            offset = (index - (len(windows) - 1) / 2) * bar_width
            values = [rows[key][spec["field"]] * spec["scale"] for key in CHART_KEYS]
            bars = axis.bar([p + offset for p in positions], values, bar_width * 0.92,
                            color=WINDOW_COLORS[index % len(WINDOW_COLORS)], label=name)
            axis.bar_label(bars, labels=[spec["label"].format(value) for value in values],
                           padding=2, fontsize=7.6, color="#41474e")
        axis.set_title(spec["title"], loc="left", fontsize=13, fontweight="bold", pad=8)
        axis.set_xticks(list(positions))
        axis.set_xticklabels([SHORT[key] for key in CHART_KEYS], fontsize=9.5)
        axis.yaxis.set_major_formatter(FuncFormatter(
            lambda value, _, f=spec["tick"]: f.format(value)))
        axis.grid(axis="y", color="#e8ebed", linewidth=0.7)
        axis.set_axisbelow(True)
        axis.margins(y=0.18)
        if panel == 1:
            axis.set_ylim(bottom=0)

    axes[0].legend(loc="upper right", frameon=False, ncol=3, fontsize=9.5)
    figure.suptitle("剔除 2019~2021 后，「仅 RSI 择时 ETF 效率最高」的结论不再成立",
                    x=0.055, ha="left", fontsize=18, fontweight="bold")
    figure.text(0.055, 0.932,
                "同一份缓存数据、同一套成本假设，只改模拟起始日；初始资金 100 万元",
                color="#5f666e", fontsize=10)
    figure.text(0.055, 0.015,
                "单位暴露效率剔除「仓位更高」的影响：数值越高，说明收益越不依赖满仓。历史结果不代表未来收益。",
                fontsize=9, color="#697079")
    figure.subplots_adjust(top=0.9, bottom=0.075, left=0.075, right=0.975, hspace=0.24)
    figure.savefig(path, dpi=180, facecolor="white")
    plt.close(figure)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--window", action="append", required=True,
                        help="窗口，格式 名称=结果目录，可重复")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    windows = []
    for spec in args.window:
        name, directory = spec.split("=", 1)
        windows.append((name, load(Path(directory), set(SHORT))))

    args.output.mkdir(parents=True, exist_ok=True)
    setup_fonts()
    comparison_chart(windows, args.output / "cagr_efficiency.png")

    names = [name for name, _ in windows]
    lines = [
        "# 剔除 2019~2021 后的回测对照",
        "",
        "同一份缓存数据、同一套成本与执行假设，**只改模拟起始日**。初始资金 100 万元，起始全部现金，"
        "行情仍从 2017 年起预热，因此 RSI、分位和股息率在窗口第一天就已有完整历史，不存在冷启动偏差。",
        "",
        "## 为什么要切这一段",
        "",
        "2019~2021 是红利股相对占优的区间。任何「更早满仓」的做法都会在那段时间占便宜，"
        "所以「哪个方案更好」的判断必须看它离开这段行情后是否还成立。",
        "",
        "![年化收益与单位暴露效率对照](cagr_efficiency.png)",
        "",
        "## 一、关键结论",
        "",
    ]

    rows = [window for _, window in windows]

    def get(window_index, key, field):
        return windows[window_index][1][key][field]

    def series(key, field):
        """按字段量纲格式化三个窗口的取值：收益用百分数，效率保留原始百分比点。"""
        scale, digits = (100.0, 2) if field == "cagr" else (1.0, 3)
        return " → ".join(f"{get(i, key, field) * scale:.{digits}f}%"
                          for i in range(len(windows)))

    lines += [
        "1. **「仅 RSI 择时 ETF 效率最高」的原结论不成立。**",
        f'   单位暴露效率由 {series("etf_rsi_only", "efficiency")}，'
        f'而当前策略（网格+RSI）为 {series("current", "efficiency")}。'
        "全样本里 RSI 分层版效率明显领先，切掉 2019~2021 后被反超，且差距随窗口后移而扩大。",
        "",
        "2. **RSI 分层版的收益优势在最短的窗口里消失。**",
        f'   年化 {series("etf_rsi_only", "cagr")}；同期同清单买入持有为 {series("hold", "cagr")}，'
        f'90% ETF 持有为 {series("etf_hold", "cagr")}。'
        "全样本与 2022 年起窗口它仍领先被动基准，但到 2023 年起（约 3.7 年）已落到两者之下。",
        "",
        "3. **梭哈版从来就不是高效方案，切段后更差。**",
        f'   效率 {series("rsi_all_in", "efficiency")}，收益 {series("rsi_all_in", "cagr")}。'
        "它在两个子区间都低于当前策略，2023 年起还低于买入持有。"
        "此前的 13.01% 主要靠 2019~2021 的高仓位，不是择时能力。",
        "",
        "4. **当前策略（网格+RSI）在子区间反而变成赢家。**",
        f'   年化 {series("current", "cagr")}，'
        f'跑赢买入持有（{series("hold", "cagr")}）、纯网格（{series("grid_only", "cagr")}）'
        f'与 90% ETF 持有（{series("etf_hold", "cagr")}）。'
        "它的弱势期恰好就是 2019~2021——那时它常年空仓等待股息率达标。",
        "",
        "5. **方案 A 被打回原形。**",
        f'   年化 {series("dynamic_a", "cagr")}，效率 {series("dynamic_a", "efficiency")}。'
        "与当前策略的差距由全样本的大幅领先，变成子区间的全面落后，证实了它此前只是「蹭满仓」。",
        "",
        "## 二、年度收益（当前策略）",
        "",
        "| 窗口 | " + " | ".join(names) + " |",
        "|---|" + "---:|" * len(names),
    ]
    for key, label in (("current", "当前策略"), ("etf_rsi_only", "仅 RSI 分层版"), ("hold", "买入持有")):
        lines.append(f'| {label} 年化 | ' + " | ".join(
            f'{get(i, key, "cagr") * 100:.2f}%' for i in range(len(windows))) + " |")
    lines += [
        "",
        "## 三、全方案对照",
        "",
        "| 方案 | " + " | ".join(f"{name} 年化" for name in names) + " | "
        + " | ".join(f"{name} 效率" for name in names) + " |",
        "|---|" + "---:|" * (2 * len(names)),
    ]
    for key in SHORT:
        if key not in rows[0]:
            continue
        cells = [f'{get(i, key, "cagr") * 100:.2f}%' for i in range(len(windows))]
        effs = [f'{get(i, key, "efficiency"):.3f}%' for i in range(len(windows))]
        lines.append(f'| {SHORT[key]} | ' + " | ".join(cells) + " | " + " | ".join(effs) + " |")
    lines += [
        "",
        "「效率」＝ 年化收益 / 平均权益暴露，用于剔除「仓位更高」带来的收益。",
        "",
        "## 四、需要注意的口径问题",
        "",
    ]
    naive_note = "、".join(
        f'{names[i]} {get(i, "baseline_naive", "round_trips")} 笔' for i in range(len(windows)))
    fiscal_note = "、".join(
        f'{get(i, "current", "round_trips")} 笔' for i in range(len(windows)))
    lines += [
        "**旧 365 天口径在子区间仍然收益偏高，但那是口径噪音。**",
        f'它的 7 日内「买入又卖出」往返次数为 {naive_note}，而修正后的财年口径只有 {fiscal_note}。'
        "旧口径的往返多落在 6~8 月分红季，形态是「大笔买入后数日内抛掉大半」"
        "（如中国石油 2022-06-29 买 16,900 股、06-30 卖 5,400 股），"
        "是除息日漂移把相邻两年分红装进同一窗口造成的假信号。"
        "财年口径剩下的那一对出现在美的集团：2023-06-01 除息后股息率跳档触发买入 1,400 股，"
        "股价 6 日内上涨约 11%、网格回落到卖出档，减仓 100 股（约 7% 仓位）——"
        "属于信号驱动的一次再平衡，不是全面平仓。因此旧口径的高收益不可复制。",
        "",
        "> 口径说明：这里的「往返」是启发式统计——按标的把成交排序，只要一笔买入之后紧邻的成交是"
        "7 日内的卖出就算一次，因此「小额加仓后紧接着一笔不相关的大额减持」也会被计入。"
        "它用来对比口径噪音的量级，不等于精确的往返交易识别。",
        "",
        "**幸存者偏差在子区间更突出。** 用的是今天的 16 只股票清单回看 2022 年，"
        "等于提前知道了哪些标的后来的表现；512890 本身也是事后选中的。切段不能消除这一偏差。",
        "",
        "**子区间样本更短。** 2022 年起约 4.7 年、2023 年起约 3.7 年，只覆盖一轮半完整周期，"
        "统计意义弱于全样本。结论应当读作「原结论不稳健」，而不是「新排名已被证实」。",
        "",
        "## 五、复算方式",
        "",
        "```bash",
        "uv run python scripts/backtest.py --data backtests/data-20260910 \\",
        "    --output backtests/results-2022plus-20260910 --start 2022-01-01 --end 2026-09-09",
        "",
        "uv run python scripts/render_subperiod_report.py \\",
        "    --window 2019-2026=backtests/results-20260910 \\",
        "    --window 2022-起=backtests/results-2022plus-20260910 \\",
        "    --window 2023-起=backtests/results-2023plus-20260910 \\",
        "    --output backtests/subperiod-20260910",
        "```",
        "",
        "各窗口的逐笔交易、每日净值和数据指纹见各自结果目录下的 JSON 与 summary.json。",
        "",
    ]
    (args.output / "REPORT.md").write_text("\n".join(lines), encoding="utf-8")
    print(args.output / "REPORT.md")
    print(args.output / "cagr_efficiency.png")


if __name__ == "__main__":
    main()
