"""极端熊市检验报告：用 2007-2017 真实历史回测，检验"抗跌"是否成立。

用法：
    python scripts/render_bear_report.py \
        --data backtests/data-bear \
        --window 2008=backtests/bear-2008 \
        --window 2015=backtests/bear-2015 \
        --output backtests/bear-20260910
"""

import argparse
import json
import os
from pathlib import Path
import tempfile

os.environ.setdefault("MPLCONFIGDIR", str(Path(tempfile.gettempdir()) / "dividend-grid-matplotlib"))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.ticker import FuncFormatter


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def pct(value):
    return f"{value * 100:.2f}%"


def signed_pct(value):
    return f"{value * 100:+.2f}%"


SHORT = {
    "current": "当前策略（网格+RSI）",
    "etf_rsi_only": "仅 RSI 分层（全仓 ETF）",
    "rsi_all_in": "仅 RSI 梭哈版",
    "grid_hold_etf": "网格+ETF持有",
    "grid_only": "仅股息率网格",
    "hold": "同清单买入持有",
    "etf_hold": "90% 红利ETF持有",
    "no_quality": "当前策略（关闭质量筛查）",
}

# 报告正文的固定顺序
ORDER = ["current", "grid_only", "grid_hold_etf", "etf_rsi_only", "rsi_all_in", "hold", "etf_hold"]
CHART_KEYS = ["current", "grid_only", "etf_rsi_only", "hold"]
CHART_COLORS = {"current": "#c0392b", "grid_only": "#2e86c1", "grid_hold_etf": "#7d3c98",
                "etf_rsi_only": "#e67e22", "rsi_all_in": "#16a085", "hold": "#7f8c8d",
                "etf_hold": "#b7950b", "no_quality": "#95a5a6"}

# 极端行情片段（泡沫顶 → 熊市底），用 510880 的收盘价定出
EPISODES = {
    "2008": ("2007-10-15", "2008-11-04", "上证红利ETF 见顶→见底（-75.00%）"),
    "2015": ("2015-06-12", "2016-01-28", "股灾顶→底（-47.64%）"),
}


def setup_fonts():
    font_path = Path("/System/Library/Fonts/Supplemental/Arial Unicode.ttf")
    if font_path.exists():
        font_manager.fontManager.addfont(str(font_path))
        plt.rcParams["font.family"] = font_manager.FontProperties(fname=str(font_path)).get_name()
    plt.rcParams.update({"axes.unicode_minus": False, "font.size": 10,
                         "axes.spines.top": False, "axes.spines.right": False,
                         "figure.facecolor": "white", "axes.facecolor": "white"})
    return FuncFormatter(lambda value, _: f"{value:.0f}%")


def nav_frame(result):
    """返回 {date: equity}，以及按日期升序的列表。"""
    series = {row["date"]: row["equity"] for row in result["daily"]}
    return series, [row["date"] for row in result["daily"]]


def value_at(series, days, day, strict=False):
    """取 <= day（strict=False）或 < day（strict=True）的最后一个交易日净值。"""
    keys = [key for key in days if (key < day if strict else key <= day)]
    return series[max(keys)] if keys else None


def episode_return(series, days, start, end):
    before = value_at(series, days, start, strict=True)
    after = value_at(series, days, end)
    if before is None or after is None:
        return None
    return after / before - 1


def yearly_drawdown(result):
    """逐年的年内最大回撤（以年初净值为基准）。"""
    buckets = {}
    for row in result["daily"]:
        buckets.setdefault(row["date"][:4], []).append(row["equity"])
    out = {}
    for year, values in buckets.items():
        peak = values[0]
        worst = 0.0
        for value in values:
            peak = max(peak, value)
            worst = min(worst, value / peak - 1)
        out[year] = worst
    return out


def yearly_exposure(result):
    """逐年的平均股票/ETF/现金占比。"""
    buckets = {}
    for row in result["daily"]:
        nav = row["equity"]
        if not nav:
            continue
        buckets.setdefault(row["date"][:4], []).append(
            (row["stock_value"] / nav, row["etf_value"] / nav, row["cash"] / nav))
    out = {}
    for year, rows in buckets.items():
        count = len(rows)
        out[year] = tuple(sum(row[i] for row in rows) / count for i in range(3))
    return out


def load_window(label, directory):
    summaries = read(Path(directory) / "summary.json")
    results = {}
    for path in sorted(Path(directory).glob("*.json")):
        if path.name == "summary.json":
            continue
        payload = read(path)
        results[payload["metrics"]["key"]] = payload
    return {"label": label, "directory": directory, "summary": summaries, "results": results}


def chart_netvalue(windows, output, percent):
    """两个窗口的累计净值（对数纵轴），并标出极端行情片段。"""
    figure, axes = plt.subplots(1, len(windows), figsize=(6.2 * len(windows), 4.4))
    if len(windows) == 1:
        axes = [axes]
    for axis, window in zip(axes, windows):
        for key in CHART_KEYS:
            payload = window["results"].get(key)
            if not payload:
                continue
            days = [row["date"] for row in payload["daily"]]
            equit = [row["equity"] for row in payload["daily"]]
            base = equit[0]
            axis.plot([int(day[:4]) + (int(day[5:7]) - 1) / 12 for day in days],
                      [value / base for value in equit],
                      color=CHART_COLORS.get(key, "#333"), linewidth=1.6,
                      label=SHORT.get(key, key))
        start, end, note = EPISODES[window["label"]]
        shade_start = window["results"]["current"]["daily"][0]["date"]
        if shade_start <= start:
            axis.axvspan(int(start[:4]) + (int(start[5:7]) - 1) / 12,
                         int(end[:4]) + (int(end[5:7]) - 1) / 12,
                         color="#f1c40f", alpha=0.16, zorder=0)
            axis.annotate(note, xy=(0.5, 0.04), xycoords="axes fraction", ha="center",
                          fontsize=8.5, color="#7d6608")
        axis.set_yscale("log")
        axis.set_title(f"{window['label']} 窗口（{window['summary']['configuration']['start']} ~ "
                       f"{window['summary']['configuration']['end']}）")
        axis.set_xlabel("年份")
        axis.set_ylabel("累计净值（对数，起点=1）")
        axis.grid(alpha=0.25, linewidth=0.6)
        axis.legend(frameon=False, fontsize=8.5, loc="upper left")
        axis.yaxis.set_major_formatter(FuncFormatter(lambda value, _: f"{value:g}"))
    figure.tight_layout()
    figure.savefig(output, dpi=170)
    plt.close(figure)


def chart_exposure(window, output, etf_prices):
    """2008 窗口：上panel 为上证红利ETF 价格，下panel 为各策略 ETF 仓位。"""
    days = [row["date"] for row in window["results"]["current"]["daily"]]
    first, last = days[0], days[-1]
    trimmed = {day: value for day, value in etf_prices.items() if first <= day <= last}
    peak = max(trimmed.values())
    figure, (top, bottom) = plt.subplots(2, 1, figsize=(9.6, 6.4), sharex=True,
                                         gridspec_kw={"height_ratios": [1, 1.15], "hspace": 0.12})
    top.plot([int(day[:4]) + (int(day[5:7]) - 1) / 12 for day in trimmed],
             [value / peak * 100 for value in trimmed.values()],
             color="#34495e", linewidth=1.6)
    trough_day = min(trimmed, key=trimmed.get)
    trough = trimmed[trough_day] / peak * 100
    top.axhline(trough, color="#34495e", linewidth=0.8, linestyle=":")
    top.annotate(f"谷底 {trough_day}（相对于峰值 {trough - 100:.1f}%）",
                 xy=(0.03, 0.08), xycoords="axes fraction", fontsize=8.5, color="#34495e")
    top.set_ylabel("上证红利ETF\n价格指数（峰值=100）")
    top.set_ylim(0, 108)
    top.grid(alpha=0.25, linewidth=0.6)
    top.set_title("2008 窗口：泡沫破裂期的价格与各策略 ETF 仓位")

    for key in ("current", "etf_rsi_only", "rsi_all_in", "grid_only"):
        payload = window["results"].get(key)
        if not payload:
            continue
        bottom.plot([int(row["date"][:4]) + (int(row["date"][5:7]) - 1) / 12 for row in payload["daily"]],
                    [row["etf_value"] / row["equity"] * 100 if row["equity"] else 0 for row in payload["daily"]],
                    color=CHART_COLORS.get(key, "#333"), linewidth=1.5, alpha=0.95,
                    label=SHORT.get(key, key))
    bottom.set_ylabel("ETF 仓位占总资产（%）")
    bottom.set_xlabel("年份")
    bottom.set_ylim(-2, 100)
    bottom.grid(alpha=0.25, linewidth=0.6)
    bottom.legend(frameon=False, fontsize=8.5, ncol=2, loc="upper left")
    figure.savefig(output, dpi=170, bbox_inches="tight")
    plt.close(figure)


def window_table(window):
    lines = ["| 策略 | 总收益 | 年化 | 最大回撤 | 年化波动 | 平均股票 | 平均ETF | 平均现金 | 交易笔数 |",
             "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for key in ORDER:
        payload = window["results"].get(key)
        if not payload:
            continue
        m = payload["metrics"]
        lines.append("| {} | {} | {} | {} | {} | {:.1f}% | {:.1f}% | {:.1f}% | {} |".format(
            SHORT.get(key, key), signed_pct(m["total_return"]), pct(m["cagr"]),
            signed_pct(m["max_drawdown"]), pct(m["volatility"]),
            100 - m["average_cash_pct"] - m["average_etf_pct"], m["average_etf_pct"],
            m["average_cash_pct"], m["trades"]))
    return "\n".join(lines)


def yearly_table(window):
    keys = [key for key in ORDER if key in window["results"]]
    years = sorted(window["results"]["current"]["metrics"]["annual_returns"])
    header = "| 策略 | " + " | ".join(years) + " |"
    lines = [header, "| --- | " + " | ".join("---:" for _ in years) + " |"]
    for key in keys:
        annual = window["results"][key]["metrics"]["annual_returns"]
        cells = [signed_pct(annual.get(year, 0.0)) for year in years]
        lines.append(f"| {SHORT.get(key, key)} | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def drawdown_table(window):
    keys = [key for key in ORDER if key in window["results"]]
    years = sorted(window["results"]["current"]["metrics"]["annual_returns"])
    header = "| 策略 | " + " | ".join(years) + " |"
    lines = [header, "| --- | " + " | ".join("---:" for _ in years) + " |"]
    for key in keys:
        series = yearly_drawdown(window["results"][key])
        cells = [signed_pct(series.get(year, 0.0)) for year in years]
        lines.append(f"| {SHORT.get(key, key)} | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def exposure_table(window):
    keys = [key for key in ORDER if key in window["results"]]
    years = sorted(window["results"]["current"]["metrics"]["annual_returns"])
    lines = ["| 策略 | " + " | ".join(f"{year} 股/ETF/现金" for year in years) + " |",
             "| --- | " + " | ".join("---:" for _ in years) + " |"]
    for key in keys:
        series = yearly_exposure(window["results"][key])
        cells = []
        for year in years:
            stock, etf, cash = series.get(year, (0.0, 0.0, 0.0))
            cells.append(f"{stock * 100:.0f}% / {etf * 100:.0f}% / {cash * 100:.0f}%")
        lines.append(f"| {SHORT.get(key, key)} | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def episode_table(windows):
    lines = ["| 策略 | 2007-10-15 → 2008-11-04 | 2015-06-12 → 2016-01-28 |",
             "| --- | ---: | ---: |"]
    for key in ORDER:
        cells = []
        for window in windows:
            payload = window["results"].get(key)
            start, end, _ = EPISODES[window["label"]]
            if not payload:
                cells.append("—")
                continue
            series, days = nav_frame(payload)
            value = episode_return(series, days, start, end)
            cells.append(signed_pct(value) if value is not None else "—")
        lines.append(f"| {SHORT.get(key, key)} | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, default=Path("backtests/data-bear"))
    parser.add_argument("--window", action="append", required=True, help="标签=结果目录")
    parser.add_argument("--output", type=Path, default=Path("backtests/bear-20260910"))
    args = parser.parse_args()

    windows = []
    for spec in args.window:
        label, directory = spec.split("=", 1)
        windows.append(load_window(label, directory))

    args.output.mkdir(parents=True, exist_ok=True)
    percent = setup_fonts()

    manifest = read(args.data / "manifest.json")
    etf_code = manifest["etf_code"]
    etf_prices = {row["date"]: row["close"] for row in read(args.data / f"{etf_code}.json")["prices"]}
    etf_first = min(etf_prices)
    etf_peak_day = max(etf_prices, key=etf_prices.get)
    etf_trough_day = min(etf_prices, key=etf_prices.get)
    etf_min = etf_prices[etf_trough_day]

    chart_netvalue(windows, args.output / "bear_netvalue.png", percent)
    chart_exposure(windows[0], args.output / "bear_exposure.png", etf_prices)

    # 逐年 510880 自身收益，作为"不择时"的收益锚
    anchors = {}
    for year in sorted({day[:4] for day in etf_prices}):
        days = sorted(day for day in etf_prices if day.startswith(year))
        if len(days) < 2:
            continue
        anchors[year] = etf_prices[days[-1]] / etf_prices[days[0]] - 1

    lines = []

    def point(result, day):
        """<= day 的最后一个交易日：日期、净值、ETF/股票/现金占比。"""
        row = [item for item in result["daily"] if item["date"] <= day][-1]
        nav = row["equity"]
        return (row["date"], nav, row["etf_value"] / nav * 100,
                row["stock_value"] / nav * 100, row["cash"] / nav * 100)

    a, b = windows[0]["results"], windows[1]["results"]
    cur_a, cur_b = a["current"]["metrics"], b["current"]["metrics"]
    rsi_a, rsi_b = a["etf_rsi_only"]["metrics"], b["etf_rsi_only"]["metrics"]
    allin_a = a["rsi_all_in"]["metrics"]
    hold_a, hold_b = a["hold"]["metrics"], b["hold"]["metrics"]
    ratio_a = hold_a["max_drawdown"] / cur_a["max_drawdown"]
    ratio_b = hold_b["max_drawdown"] / cur_b["max_drawdown"]
    rsi_first = next(t for t in a["etf_rsi_only"]["trades"] if t["code"] == etf_code)
    rsi_trough = point(a["etf_rsi_only"], "2008-10-28")
    cur_before_trough = point(a["current"], "2008-10-28")
    cur_early = point(a["current"], "2008-01-31")

    lines.append("# 极端熊市检验：用 2007–2017 真实历史回测「抗跌」")
    lines.append("")
    lines.append(f"> **一句话结论**：抗跌是真的——当前策略在 2008 年最大回撤 {cur_a['max_drawdown']:.1%}、"
                 f"2015 年 {cur_b['max_drawdown']:.1%}，分别只有买入持有（{hold_a['max_drawdown']:.1%} / "
                 f"{hold_b['max_drawdown']:.1%}）的 1/{ratio_a:.0f} 和 1/{ratio_b:.0f}。"
                 f"**但抗跌的来源不是 RSI 择时，而是股息率网格的估值闸门**："
                 f"2007 年泡沫期所有标的股息率都被压到阈值之下，网格自动零仓位。"
                 f"把 RSI 择时单独拿出来用，在 2008 年会亏掉 {rsi_a['annual_returns']['2008']:.1%}"
                 f"——它是加仓工具，不是保护工具。")
    lines.append("")

    lines.append("## 一、为什么之前没检验到")
    lines.append("")
    lines.append("`results-20260910` 的样本是 2019-01 ~ 2026-09。这 7 年红利低波整体走强"
                 "（买入持有年化 9.96%），最大回撤只有 -18.59%。在这种样本里，"
                 "任何「少亏」都只能靠「仓位低」，检验不出策略在系统性崩盘下会不会失灵。")
    lines.append("")
    lines.append("本报告把样本往前推到 2007 年，套住两段真正的极端行情：")
    lines.append("")
    lines.append(f"- **2008**：上证红利ETF 不复权价格从 {etf_peak_day} 的 {etf_prices[etf_peak_day]:.3f} 元"
                 f"跌到 {etf_trough_day} 的 {etf_min:.3f} 元，**{etf_min / etf_prices[etf_peak_day] - 1:.2%}**"
                 f"（含分红的总收益口径跌幅略小）。")
    lines.append("- **2015**：2015-06-12 见顶 4.154 元 → 2016-01-28 的 2.175 元，**-47.64%**（杠杆牛破裂）。")
    lines.append("")

    lines.append("## 二、数据与设定")
    lines.append("")
    lines.append(f"- 数据源与主回测完全一致（腾讯前复权日线、东方财富分红、新浪财报），"
                 f"落在 `{args.data}/`，共 {len(manifest['universe'])} 个标的。")
    lines.append(f"- **ETF 载体换成 `{etf_code}` 上证红利ETF**（2007-01-18 上市）："
                 f"`sh512890` 红利低波ETF 2019 年才上市，无法回看 2008。两者都是"
                 f"上证红利类指数，是同一投资思路的早期载体。")
    lines.append("- **清单剔除中国移动（2021-01 上市）与中国电信（2021-03 上市）**，"
                 "其余 14 只原样保留；美的集团 2013-09 上市，2007-2012 段自然无数据、不参与交易。"
                 "清单见 `watchlist-bear.json`。")
    lines.append("- 其余口径不变：收盘出信号、次日开盘成交；佣金万三、印花税单边、"
                 "过户费、滑点 5bp、股息税按持有期；现金底线 10%、行业上限 30%、ETF 额度 20%。")
    lines.append("- **注意**：RSI 阈值（31/70/74）是在 512890 上校准的，"
                 "移用到 510880 属于跨标的近似，结论按「机制是否成立」解读，不按小数点解读。")
    lines.append("")
    lines.append("**先说一个「没差别」的结果**：两个窗口里 `no_quality`（关闭质量筛查）"
                 "与 `current` 的每一个数字都完全相同。因为这两段时期策略的成交极少、"
                 "且触发买入的标的（如 2008 年的华能国际）财报质量都过关，"
                 "质量闸门没有挡掉任何一笔交易——它在极端熊市里既不是保护、也不是拖累。")
    lines.append("")

    for index, window in enumerate(windows, start=3):
        configuration = window["summary"]["configuration"]
        numeral = "三四五六七八九"[index - 3]
        lines.append(f"## {numeral}、{window['label']} 窗口"
                     f"（{configuration['start']} ~ {configuration['end']}）")
        lines.append("")
        lines.append(window_table(window))
        lines.append("")
        lines.append("**分年度收益**")
        lines.append("")
        lines.append(yearly_table(window))
        lines.append("")
        lines.append("**分年度年内最大回撤**")
        lines.append("")
        lines.append(drawdown_table(window))
        lines.append("")
        if window["label"] == "2008":
            lines.append(f"参照：上证红利ETF 自身分年度收益 "
                         + "、".join(f"{year} {signed_pct(anchors[year])}"
                                     for year in sorted(anchors) if year <= "2012"))
            lines.append("")
            lines.append("![2008 窗口仓位轨迹](bear_exposure.png)")
            lines.append("")
        else:
            lines.append("参照：上证红利ETF 自身分年度收益 "
                         + "、".join(f"{year} {signed_pct(anchors[year])}"
                                     for year in sorted(anchors) if year >= "2013"))
            lines.append("")

    lines.append("## 五、极端行情片段：泡沫顶到熊底")
    lines.append("")
    lines.append(episode_table(windows))
    lines.append("")
    lines.append("![净值对比](bear_netvalue.png)")
    lines.append("")

    lines.append("## 六、分年度的股/ETF/现金占比")
    lines.append("")
    for window in windows:
        lines.append(f"**{window['label']} 窗口**")
        lines.append("")
        lines.append(exposure_table(window))
        lines.append("")

    lines.append("## 七、关键机制拆解")
    lines.append("")
    lines.append("### 7.1 股息率网格：泡沫期会自动空仓")
    lines.append("")
    lines.append("2007 年 1 月到 10 月，上证红利ETF 从 1.963 涨到 5.188（+164%），"
                 "所有个股的股息率被股价压到阈值之下。网格的判定是「太贵」→ 只减不加 → "
                 "**`grid_only` 在整个 2007 年零成交、零仓位**。")
    lines.append("")
    lines.append(f"结果：{windows[0]['label']} 窗口里 `grid_only` 的总收益 "
                 f"{a['grid_only']['metrics']['total_return']:+.2%}、"
                 f"最大回撤 {a['grid_only']['metrics']['max_drawdown']:.2%}，"
                 f"2008 年当年 {a['grid_only']['metrics']['annual_returns']['2008']:+.2%}。"
                 "它躲过 2008 不是靠预判，而是靠「贵到不该买」这一条机械规则。"
                 "同一年它只在 9 月买到一只够便宜的标的（华能国际，股息率触及 5.1% 买入区），"
                 "用约 6% 的仓位做了两轮「买恐慌—卖反弹」。")
    lines.append("")
    lines.append("**这是策略抗跌能力真正的来源。**")
    lines.append("")
    lines.append("### 7.2 RSI 择时：2008 失效，2015 有效——它依赖行情的形状")
    lines.append("")
    lines.append("把 RSI 择时单独拿出来（`etf_rsi_only`，全仓 ETF、按 RSI 分层），"
                 "比较两个极端年份：")
    lines.append("")
    lines.append("| 策略 | 2008 年 | 2015 年 | 2008 窗口最大回撤 | 2015 窗口最大回撤 |")
    lines.append("| --- | ---: | ---: | ---: | ---: |")
    for key in ("etf_rsi_only", "rsi_all_in"):
        lines.append(f"| {SHORT[key]} | "
                     + " | ".join([signed_pct(a[key]['metrics']['annual_returns']['2008']),
                                   signed_pct(b[key]['metrics']['annual_returns']['2015']),
                                   signed_pct(a[key]['metrics']['max_drawdown']),
                                   signed_pct(b[key]['metrics']['max_drawdown'])]) + " |")
    lines.append("")
    lines.append(f"2008 的失败过程（见上图）：{rsi_first['date']} 上证红利ETF 跌破时 "
                 f"RSI6 读到 {float(rsi_first['reason'].split('=')[-1]):.1f}，策略判定「超卖」开始建仓；"
                 f"2008 年 3 月 RSI 进一步走低（22.4、18.7），策略继续加仓；"
                 f"到 {rsi_trough[0]} 已经持有 {rsi_trough[2]:.1f}% 仓位的 ETF，"
                 f"净值从 100 万掉到 {rsi_trough[1] / 10000:.1f} 万"
                 f"（{rsi_a['annual_returns']['2008']:.1%}）。"
                 "对单边慢熊，RSI 只会不断给出「更超卖」的买入信号，越跌越买。")
    lines.append("")
    lines.append(f"2015 则相反：2015-06-12 见顶时 RSI 处在高位，策略已经清仓（0% 仓位）→ "
                 f"躲过股灾 → 2015 年 8 月崩盘后 RSI 转低再买回。2015 年 "
                 f"{rsi_b['annual_returns']['2015']:+.2%}、年内最大回撤 "
                 f"{yearly_drawdown(b['etf_rsi_only'])['2015']:.1%}，"
                 f"而买入持有当年盘中回撤 {yearly_drawdown(b['hold'])['2015']:.1%}。"
                 "**同一个 RSI 逻辑，在「急跌急反弹」里有效，在「慢性单边熊市」里是陷阱。**")
    lines.append("")
    lines.append("### 7.3 当前策略的抗跌，是网格给的，不是 RSI 给的")
    lines.append("")
    lines.append(f"`current` = 股息率网格（估值闸门） + RSI 分层加仓（小仓位）。"
                 f"{cur_early[0]} 时它的 ETF 仓位只有 {cur_early[2]:.1f}%，"
                 f"{cur_before_trough[0]}（熊市底附近）是 {cur_before_trough[3]:.1f}% 股票 + "
                 f"{cur_before_trough[2]:.1f}% ETF、{cur_before_trough[4]:.1f}% 现金。"
                 f"抗跌来自「没仓位」，而「没仓位」来自网格在 2007 年的估值否决；"
                 f"RSI 在同一时期只贡献了不到 10% 的 ETF 仓位，其中大部分还是浮亏的。")
    lines.append("")

    lines.append("## 八、结论")
    lines.append("")
    lines.append(f"1. **抗跌成立，但不是靠择时逃顶。** 2008 年 {cur_a['max_drawdown']:.1%}、"
                 f"2015 年 {cur_b['max_drawdown']:.1%} 的回撤，对应买入持有的 "
                 f"{hold_a['max_drawdown']:.1%} 与 {hold_b['max_drawdown']:.1%}。检验通过。")
    lines.append("2. **抗跌的来源是股息率网格的估值闸门。** 泡沫期股息率被压低 → 机械空仓 → "
                 "自然规避。这是策略里唯一在极端行情中经得起检验的部分。")
    lines.append(f"3. **RSI 择时在长期单边熊市里是风险来源。** 单独使用 2008 年 "
                 f"{rsi_a['annual_returns']['2008']:.1%}，梭哈版 "
                 f"{allin_a['annual_returns']['2008']:.1%}，与满仓持有（"
                 f"{hold_a['annual_returns']['2008']:.1%}）几乎一样惨。"
                 "它在策略里的作用是「跌得够深时加仓」，而不是「跌之前撤退」——不要把它当成风控。")
    lines.append(f"4. **抗跌的代价是长期收益。** {windows[0]['label']} 窗口年化 "
                 f"{cur_a['cagr']:.2%}（买入持有 {hold_a['cagr']:.2%}）；"
                 f"{windows[1]['label']} 窗口年化 {cur_b['cagr']:.2%}"
                 f"（买入持有 {hold_b['cagr']:.2%}）。策略在泡沫期缺席，"
                 "而这正是长期收益最集中的时段。")
    lines.append("5. **这解释了主回测的结论**：策略的「单位暴露效率高、但绝对收益低」，"
                 "不是参数没调好，而是这类策略的固有取舍——它买的是回撤保护，"
                 "把回报让给了愿意承受回撤的人。")
    lines.append("")

    lines.append("## 九、限制")
    lines.append("")
    lines.append("- **清单是「今天的清单回看历史」**，存在幸存者偏差："
                 "2007 年真实可投的高股息标的里，退市/重组/业绩崩塌的那些没有进来。"
                 f"所以 2008 的 {cur_a['max_drawdown']:.1%} 是乐观估计。")
    lines.append("- **ETF 载体换成了 510880**，与生产用的 512890 不是同一只基金；"
                 "510880 跟踪上证红利、512890 跟踪红利低波，波动与股息率水平有差异。")
    lines.append("- **RSI 阈值未针对 510880 重新校准**，跨标的移用属于近似。")
    lines.append("- 2007-2012 段策略从零冷启动，前几个月 RSI 未成形（周线需要 7 周），"
                 "这段的「空仓」部分是预热而非判断。")
    lines.append("- 未建模个股停牌复牌、配股、退市整理期；2008 年这类事件在 A 股并不罕见。")
    lines.append("- 数据本身有坏点：腾讯接口在 `sh510880` 的 2008-01-02 返回过一个孤立跳变"
                 "（1.467 元夹在 4.637 / 4.794 之间），已由 "
                 "`scripts/repair_backtest_prices.py` 剔除（全库仅此 1 条）。"
                 "未清洗前它会伪造一次 -68%/+227% 往返，把 `etf_rsi_only` 的 2008 年亏损放大到 -61.5%。")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append(f"复算：`python scripts/fetch_backtest_data.py --start 2007-01-01 --end 2017-12-31 "
                 f"--watchlist watchlist-bear.json --output {args.data} --etf-code {etf_code} "
                 f"--etf-name 上证红利ETF` → `python scripts/repair_backtest_prices.py "
                 f"--data {args.data}`（剔除坏点），随后对每个窗口执行 "
                 f"`python scripts/backtest.py --data {args.data} --output <窗口目录> "
                 f"--start <起> --end <止> --scenarios ...`，最后 "
                 f"`python scripts/render_bear_report.py --window 2008=<目录> --window 2015=<目录> "
                 f"--output {args.output}`。")
    lines.append("")
    lines.append("⚠️ 以上内容由 AI 基于公开数据整理生成，仅供参考，不构成任何投资建议或个股推荐。"
                 "投资有风险，决策需谨慎。")

    (args.output / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"report": str(args.output / "REPORT.md"),
                      "charts": [str(args.output / "bear_netvalue.png"),
                                 str(args.output / "bear_exposure.png")]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
