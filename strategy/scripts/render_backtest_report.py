"""Render the verified offline results into charts and a Chinese report."""

import argparse
import json
import os
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


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def published_bug_before(results_dir: Path) -> dict | None:
    """读取同一批次在修复 publish_date 偏差前的归档结果，用于量化修复影响。"""
    archived = results_dir.parent / "archive-published-bug" / results_dir.name / "summary.json"
    if not archived.exists():
        return None
    return {row["key"]: row for row in read(archived)["results"]}


def pct(value):
    return f"{value * 100:.2f}%"


def setup_fonts():
    font_path = Path("/System/Library/Fonts/Supplemental/Arial Unicode.ttf")
    if font_path.exists():
        font_manager.fontManager.addfont(str(font_path))
        plt.rcParams["font.family"] = font_manager.FontProperties(fname=str(font_path)).get_name()
    plt.rcParams.update({"axes.unicode_minus": False, "font.size": 10, "axes.spines.top": False,
                         "axes.spines.right": False, "axes.edgecolor": "#c9ced3",
                         "text.color": "#24282d", "axes.labelcolor": "#41474e",
                         "xtick.color": "#626970", "ytick.color": "#626970"})


COLORS = {"current": "#13876d", "baseline_naive": "#c0392b", "dynamic_a": "#b8653c",
          "dynamic_b": "#7a5aa8", "dynamic_ab": "#9b2226", "etf_rsi_only": "#286fbd",
          "rsi_all_in": "#0f9d8f",
          "grid_hold_etf": "#7995ad", "grid_only": "#5aa39b", "hold": "#777b83",
          "etf_hold": "#a8863c", "no_quality": "#4b7a3f"}


def curves(results, keys, path):
    figure, axes = plt.subplots(3, 1, figsize=(12.8, 10.5), sharex=True,
                                gridspec_kw={"height_ratios": [3, 1.6, 1.35]})
    frames = {}
    for key in keys:
        frame = pd.DataFrame(results[key]["daily"])
        frame["date"] = pd.to_datetime(frame["date"])
        frames[key] = frame
        peaks = frame["equity"].cummax().clip(lower=results[key]["metrics"]["initial_capital"])
        axes[0].plot(frame["date"], frame["equity"] / 10000, color=COLORS[key],
                     linewidth=2.3 if key in ("current", "etf_rsi_only", "rsi_all_in") else 1.4,
                     label=results[key]["metrics"]["name"])
        axes[1].plot(frame["date"], (frame["equity"] / peaks - 1) * 100,
                     color=COLORS[key], linewidth=1.25)
    for key in ("current", "dynamic_a", "etf_rsi_only", "rsi_all_in"):
        frame = frames[key]
        axes[2].plot(frame["date"], frame["cash"] / frame["equity"] * 100,
                     color=COLORS[key], linewidth=1.6, label=results[key]["metrics"]["name"])
    axes[0].set_ylabel("账户净值 / 万元")
    axes[1].set_ylabel("距历史高点回撤")
    axes[2].set_ylabel("现金占比")
    axes[1].yaxis.set_major_formatter(FuncFormatter(lambda value, _: f"{value:.0f}%"))
    axes[2].yaxis.set_major_formatter(FuncFormatter(lambda value, _: f"{value:.0f}%"))
    axes[2].set_ylim(0, 105)
    axes[0].legend(loc="upper left", ncol=2, fontsize=8.6, frameon=True, framealpha=0.92,
                   facecolor="white", edgecolor="#e2e6e9", borderpad=0.6)
    axes[2].legend(loc="upper right", ncol=2, fontsize=8.4, frameon=True, framealpha=0.92,
                   facecolor="white", edgecolor="#e2e6e9", borderpad=0.6)
    axes[2].xaxis.set_major_locator(mdates.YearLocator())
    axes[2].xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    for axis in axes:
        axis.grid(axis="y", color="#e8ebed", linewidth=0.7)
        axis.margins(x=0.01)
    figure.suptitle("红利策略收益回测：口径修正与动态阈值对照", x=0.08, ha="left",
                    fontsize=19, fontweight="bold")
    figure.text(0.08, 0.938,
                f'{results["current"]["metrics"]["start"]} 至 {results["current"]["metrics"]["end"]}'
                "  |  初始资金 100 万元  |  计入分红、税费与滑点",
                color="#5f666e", fontsize=10)
    figure.text(0.08, 0.02, "按当前清单回溯；财报为现存历史记录，可能含重述。历史结果不代表未来收益。",
                fontsize=9, color="#697079")
    figure.subplots_adjust(top=0.9, bottom=0.075, left=0.09, right=0.975, hspace=0.17)
    figure.savefig(path, dpi=180, facecolor="white")
    plt.close(figure)


SHORT = {"current": "当前策略（财年口径）", "baseline_naive": "旧365天口径对照",
         "dynamic_a": "方案A：分位阈值", "dynamic_b": "方案B：分红归一化",
         "dynamic_ab": "方案A+B", "etf_rsi_only": "仅RSI择时ETF（分层）",
         "rsi_all_in": "仅RSI择时ETF（梭哈）",
         "grid_hold_etf": "网格+ETF持有", "grid_only": "仅股息率网格",
         "hold": "同清单买入持有", "etf_hold": "90%ETF持有", "no_quality": "关闭质量筛查"}

# 只标注关键方案，其余靠图例识别，避免标签互相遮挡。
ANNOTATE = {"dynamic_a": (8, 4), "dynamic_b": (8, -16), "current": (-6, -20),
            "etf_rsi_only": (8, 8), "rsi_all_in": (-88, 12), "hold": (8, -16), "etf_hold": (8, 6)}


def scatter(summary, path, baseline_cagr, hold_cagr):
    """年化收益 vs 平均权益暴露：识别“收益是否只是来自更高仓位”。"""
    figure, axis = plt.subplots(figsize=(11.0, 6.8))
    for row in summary["results"]:
        exposure = 100 - row["average_cash_pct"]
        axis.scatter(exposure, row["cagr"] * 100, color=COLORS.get(row["key"], "#5f666e"),
                     s=72, zorder=3, edgecolor="white", linewidth=0.8,
                     label=SHORT.get(row["key"], row["name"]))
        if row["key"] in ANNOTATE:
            axis.annotate(SHORT[row["key"]], (exposure, row["cagr"] * 100),
                          textcoords="offset points", xytext=ANNOTATE[row["key"]],
                          fontsize=9, color="#24282d", fontweight="medium", zorder=4)
    axis.axhline(baseline_cagr * 100, color="#c9ced3", linewidth=1.1, linestyle="--", zorder=1)
    axis.axhline(hold_cagr * 100, color="#d8dbe0", linewidth=1.1, linestyle="--", zorder=1)
    axis.axhline(0, color="#e8ebed", linewidth=1)
    axis.set_xlabel("平均权益暴露（100% - 平均现金占比）")
    axis.set_ylabel("年化收益")
    axis.yaxis.set_major_formatter(FuncFormatter(lambda value, _: f"{value:.0f}%"))
    axis.xaxis.set_major_formatter(FuncFormatter(lambda value, _: f"{value:.0f}%"))
    axis.set_xlim(-8, 104)
    axis.set_ylim(-1, 18.5)
    axis.grid(color="#eef1f3", linewidth=0.8)
    axis.set_axisbelow(True)
    axis.legend(loc="lower right", frameon=False, fontsize=8.4, ncol=3,
                handletextpad=0.3, columnspacing=1.1)
    figure.suptitle("收益与仓位暴露：右移不一定代表择时更准", x=0.055, ha="left",
                    fontsize=16, fontweight="bold")
    figure.text(0.055, 0.9,
                f"虚线：修正口径基线 {baseline_cagr * 100:.2f}%（灰）、同清单买入持有 {hold_cagr * 100:.2f}%（浅灰）",
                color="#5f666e", fontsize=9.5)
    figure.subplots_adjust(top=0.87, bottom=0.1, left=0.085, right=0.98)
    figure.savefig(path, dpi=180, facecolor="white")
    plt.close(figure)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", type=Path, default=Path("backtests/results-20260910"))
    parser.add_argument("--date-tag", default="20260910")
    args = parser.parse_args()
    summary = read(args.results / "summary.json")
    results = {row["key"]: read(args.results / f'{row["key"]}.json') for row in summary["results"]}
    metrics = {key: value["metrics"] for key, value in results.items()}
    rows = {row["key"]: row for row in summary["results"]}

    setup_fonts()
    curves(results, ["hold", "etf_hold", "etf_rsi_only", "rsi_all_in", "dynamic_a", "dynamic_b", "current"],
           args.results / "equity_drawdown.png")
    scatter(summary, args.results / "risk_return.png",
            metrics["current"]["cagr"], metrics["hold"]["cagr"])

    current, naive = metrics["current"], metrics["baseline_naive"]
    etf_rsi = metrics["etf_rsi_only"]
    rsi_all = metrics["rsi_all_in"]
    hold = metrics["hold"]

    def exposure(metric):
        return 100 - metric["average_cash_pct"]

    def efficiency(metric):
        value = exposure(metric)
        return metric["cagr"] * 100 / value if value else 0.0

    # 快速往返：买入后 7 个自然日内又卖出的次数（噪音交易指示）
    def round_trips(key):
        import collections
        from datetime import date as _date
        by_code = collections.defaultdict(list)
        for trade in results[key]["trades"]:
            by_code[trade["code"]].append(trade)
        count = 0
        for trades in by_code.values():
            for i, trade in enumerate(trades):
                if trade["side"] != "BUY" or i + 1 >= len(trades):
                    continue
                nxt = trades[i + 1]
                if nxt["side"] == "SELL" and (
                        _date.fromisoformat(nxt["date"]) - _date.fromisoformat(trade["date"])).days <= 7:
                    count += 1
        return count

    current_frame = pd.DataFrame(results["current"]["daily"])
    cash_pct = current_frame["cash"] / current_frame["equity"] * 100
    all_in_frame = pd.DataFrame(results["rsi_all_in"]["daily"])
    all_in_exposure_max = (1 - all_in_frame["cash"] / all_in_frame["equity"]).max() * 100
    attribution = sorted(results["current"]["attribution"], key=lambda row: row["profit"], reverse=True)
    top_share = sum(row["profit"] for row in attribution[:3]) / (
        current["ending_equity"] - current["initial_capital"])

    lines = [
        "# 红利策略收益回测：口径修正与动态阈值对照", "",
        f'区间：{current["start"]} 至 {current["end"]}。初始资金 1,000,000 元，起始全部现金。', "",
        "## 结论摘要", "",
        f'1. **修正股息率口径后，原基线结论不成立**：年化由 {pct(naive["cagr"])} 降至 '
        f'**{pct(current["cagr"])}**，减少 {(naive["cagr"] - current["cagr"]) * 100:.2f} 个百分点。'
        "旧的“过去 365 天分红求和”口径会重复计入两个年度分红，产生假信号。",
        f'2. **修正后的当前策略跑输被动基准**：年化 {pct(current["cagr"])} 低于同清单买入持有的 '
        f'{pct(hold["cagr"])}，也低于 90% 红利低波 ETF 持有的 {pct(metrics["etf_hold"]["cagr"])}。',
        f'3. **方案 A（自身分位阈值）收益最高但风险同步放大**：年化 {pct(metrics["dynamic_a"]["cagr"])}，'
        f'最大回撤 {pct(metrics["dynamic_a"]["max_drawdown"])}、夏普 {metrics["dynamic_a"]["sharpe_zero_rf"]:.2f}，'
        f'均差于修正后基线（{pct(current["max_drawdown"])}、{current["sharpe_zero_rf"]:.2f}）。'
        f'其平均权益暴露高达 {exposure(metrics["dynamic_a"]):.1f}%（基线 {exposure(current):.1f}%），'
        "收益主要来自“更早、更长期满仓”，而非择时更准。",
        f'4. **方案 B（3 年分红中位数归一化）最稳但最保守**：最大回撤仅 '
        f'{pct(metrics["dynamic_b"]["max_drawdown"])}、夏普 {metrics["dynamic_b"]["sharpe_zero_rf"]:.2f}，'
        f'但平均权益暴露只有 {exposure(metrics["dynamic_b"]):.1f}%，年化 {pct(metrics["dynamic_b"]["cagr"])}。',
        f'5. **仅按 RSI 择时买红低波 ETF 是本次效率最高的方案**：年化 {pct(etf_rsi["cagr"])}、'
        f'夏普 {etf_rsi["sharpe_zero_rf"]:.2f}（全场最高）、最大回撤 {pct(etf_rsi["max_drawdown"])}，'
        f'平均权益暴露仅 {exposure(etf_rsi):.1f}%，全程只有 {etf_rsi["trades"]} 笔交易。'
        f'单位暴露效率 {efficiency(etf_rsi):.2f}% 为全场最高，优于 16 只个股网格。'
        "**但该结论不稳健**：剔除 2019~2021 后，其效率降到 0.259%（2022 年起）、0.227%（2023 年起），"
        "被当前策略反超，绝对收益也掉到买入持有之下。详见《剔除 2019~2021 后的回测对照》。",
        f'6. **A+B 叠加不互补**：年化 {pct(metrics["dynamic_ab"]["cagr"])} 但回撤 '
        f'{pct(metrics["dynamic_ab"]["max_drawdown"])}、夏普 {metrics["dynamic_ab"]["sharpe_zero_rf"]:.2f} 为全场最低。',
        f'7. **RSI 梭哈版（31 以下满仓、70/74 减仓）年化 {pct(rsi_all["cagr"])}，高于分层版但效率更低**：'
        f'收益提升来自平均权益暴露由 {exposure(etf_rsi):.1f}% 翻倍到 {exposure(rsi_all):.1f}%，'
        f'单位暴露效率反而由 {efficiency(etf_rsi):.2f}% 降至 {efficiency(rsi_all):.2f}%，夏普也由 '
        f'{etf_rsi["sharpe_zero_rf"]:.2f} 降至 {rsi_all["sharpe_zero_rf"]:.2f}。'
        f'两者最大回撤恰好都是 {pct(rsi_all["max_drawdown"])}，因为该谷值发生在两条曲线尚未分叉的 2020 年初。', "",
        "![净值、回撤与现金占比](equity_drawdown.png)", "",
        "## 一、股息率口径修正", "",
        "修正前：每股分红为“截至信号日过去 365 天已实施现金分红之和”。除息日逐年漂移几天时，"
        "该窗口会同时装进相邻两个年度的分红；漂移出窗口时又漏计，档位随之翻转。",
        "",
        "修正后：按**财年**分组——先确定最近一个“年度（12-31 报告期）分红已实施”的完整财年，"
        "再汇总该财年全部已实施分红（含中期分红）。仅使用除息日不晚于当天的记录，不含未来信息。", "",
        "| 指标 | 旧 365 天口径 | 修正后财年口径 |",
        "|---|---:|---:|",
        f'| 年化收益 | {pct(naive["cagr"])} | {pct(current["cagr"])} |',
        f'| 最大回撤 | {pct(naive["max_drawdown"])} | {pct(current["max_drawdown"])} |',
        f'| 夏普（无风险利率 0） | {naive["sharpe_zero_rf"]:.2f} | {current["sharpe_zero_rf"]:.2f} |',
        f'| 成交笔数 | {naive["trades"]} | {current["trades"]} |',
        f'| 7 日内买入又卖出（噪音往返） | {round_trips("baseline_naive")} | {round_trips("current")} |',
        f'| 手续费合计/元 | {naive["fees"]:,.0f} | {current["fees"]:,.0f} |',
        f'| 滑点影响/元 | {naive["slippage_cost"]:,.0f} | {current["slippage_cost"]:,.0f} |',
        "",
        "典型的假信号：招商银行 2019 年除息日 07-12、2020 年 07-10。旧口径下 2020-07-10 的股息率"
        "被算成买入档，回测在 2020-07-13 买入 1,600 股、次日 07-14 又卖出 1,000 股，"
        "纯属口径噪音。修正后该往返完全消失，全样本的快速往返交易由 7 笔降为 1 笔，"
        "手续费与滑点合计下降约一半（见上表）。",
        "",
        "剩下的那 1 笔不是口径噪音：美的集团 2023-06-01 除息后股息率跳档，06-02 买入 1,400 股；"
        "股价在随后 6 个交易日内上涨约 11%，网格回落到卖出档，06-08 减仓 100 股（约 7% 仓位）。"
        "这是信号驱动的一次再平衡，而不是全面平仓，与上述「买入次日抛掉大半」的假信号性质不同。",
        "",
        "因此，**此前引用的旧口径数字已随口径与数据两次修正而作废**："
        f'按同一口径重算，旧 365 天方案为年化 {pct(naive["cagr"])}、最大回撤 {pct(naive["max_drawdown"])}。'
        "后续所有判断都以财年口径为准。**旧口径的“超额收益”有相当部分来自噪音交易与口径错觉。**", "",
        "## 二、动态阈值方案对照", "",
        "| 方案 | 年化收益 | 最大回撤 | 夏普 | 平均权益暴露 | 单位暴露效率 | 成交笔数 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for key in ("current", "dynamic_a", "dynamic_b", "dynamic_ab"):
        row, metric = rows[key], metrics[key]
        lines.append(f'| {row["name"]} | {pct(metric["cagr"])} | {pct(metric["max_drawdown"])} | '
                     f'{metric["sharpe_zero_rf"]:.2f} | {exposure(metric):.1f}% | '
                     f'{efficiency(metric):.2f}% | {metric["trades"]} |')
    lines += [
        "",
        "**单位暴露效率 = 年化收益 ÷ 平均权益暴露**，用于剔除“仓位更高”带来的影响。", "",
        "- 方案 A（≥65/80/90 分位→6/9/12 份；≤35/20/10 分位→累计卖 3/6/10）用该股自身滚动 3 年分位"
        "替代绝对股息率，可自动适应中枢漂移；上市不足约 2 年的标的自动回退绝对阈值。",
        "- 方案 B 把分子换成“最近至多 3 个完整财年的分红中位数”，把“分红增长”与“价格下跌”分离。",
        "",
        "需要注意：**方案 A 的阈值在本样本中几乎不构成约束**——其平均权益暴露"
        f'{exposure(metrics["dynamic_a"]):.1f}%（基线只有 {exposure(current):.1f}%），2019 年即达 69.4%。'
        "也就是说 A 在本段样本里退化为“尽早满仓持有清单”，"
        "直接吃到了 2019~2021 红利股行情。它的高收益与高回撤同源，**不能视为可复制的超额收益**。"
        "这正是需要用单位暴露效率而非单看收益来判断的原因。", "",
        "![收益与仓位暴露](risk_return.png)", "",
        "## 三、仅 RSI 择时红利低波 ETF", "",
        "### 3.1 分层版：RSI<31/25/20 买至 4/7/10 层", "",
        f'不使用任何个股网格：全部资金按 512890 的周线 Wilder RSI(6) 择时——'
        f'RSI<31/25/20 分别买入至 4/7/10 层，RSI>74 减 2 层、>80 清仓，'
        f'单层为可用额度的 1/10，保留 10% 现金。', "",
        "| 指标 | 仅 RSI 择时 ETF | 修正后网格+RSI |",
        "|---|---:|---:|",
        f'| 年化收益 | **{pct(etf_rsi["cagr"])}** | {pct(current["cagr"])} |',
        f'| 最大回撤 | {pct(etf_rsi["max_drawdown"])} | {pct(current["max_drawdown"])} |',
        f'| 夏普 | **{etf_rsi["sharpe_zero_rf"]:.2f}** | {current["sharpe_zero_rf"]:.2f} |',
        f'| 平均权益暴露 | {exposure(etf_rsi):.1f}% | {exposure(current):.1f}% |',
        f'| 单位暴露效率 | **{efficiency(etf_rsi):.2f}%** | {efficiency(current):.2f}% |',
        f'| 成交笔数 | **{etf_rsi["trades"]}** | {current["trades"]} |',
        f'| 手续费合计/元 | {etf_rsi["fees"]:,.0f} | {current["fees"]:,.0f} |',
        "",
        f'在相近的权益暴露（{exposure(etf_rsi):.1f}% vs {exposure(current):.1f}%）下，'
        f'单一 ETF + RSI 择时同时取得了更高的年化、更高的夏普和更少的交易。'
        f'代价是标的高度集中：组合完全依赖一只红利低波 ETF 的流动性与跟踪表现，'
        f'且 ETF 无股息税与个股分散效应。', "",
        "### 3.2 梭哈版：RSI<31 满仓，70/74 各减 10%，周首日 74 以上直接减 20%", "",
        "同一套 RSI(6)，但改用**百分比仓位**而非层数，且买点更宽松（31 就满仓，而非等到 20）：",
        "",
        "- **买入**：RSI < 31 → 一次性买入至满仓（下单权重 100%）。",
        "- **卖出**：RSI ≥ 70 卖当前持仓的 10%；RSI ≥ 74 再卖 10%；同一周累计上限 20%。",
        "- **周首日特例**：若当周第一个交易日 RSI 已 ≥ 74，则直接卖 20%，本周不再有任何操作。",
        "- 每次卖出都是**按当时剩余持仓的比例**计算，而非初始仓位。", "",
        "| 指标 | 梭哈版 | 分层版 | 差异 |",
        "|---|---:|---:|---:|",
        f'| 年化收益 | **{pct(rsi_all["cagr"])}** | {pct(etf_rsi["cagr"])} | '
        f'{(rsi_all["cagr"] - etf_rsi["cagr"]) * 100:+.2f} pt |',
        f'| 最大回撤 | {pct(rsi_all["max_drawdown"])} | {pct(etf_rsi["max_drawdown"])} | '
        f'{(rsi_all["max_drawdown"] - etf_rsi["max_drawdown"]) * 100:+.2f} pt |',
        f'| 夏普 | {rsi_all["sharpe_zero_rf"]:.2f} | **{etf_rsi["sharpe_zero_rf"]:.2f}** | '
        f'{rsi_all["sharpe_zero_rf"] - etf_rsi["sharpe_zero_rf"]:+.2f} |',
        f'| 波动率 | {pct(rsi_all["volatility"])} | {pct(etf_rsi["volatility"])} | '
        f'{(rsi_all["volatility"] - etf_rsi["volatility"]) * 100:+.2f} pt |',
        f'| 平均权益暴露 | {exposure(rsi_all):.1f}% | {exposure(etf_rsi):.1f}% | '
        f'{exposure(rsi_all) - exposure(etf_rsi):+.1f} pt |',
        f'| 单位暴露效率 | {efficiency(rsi_all):.2f}% | **{efficiency(etf_rsi):.2f}%** | '
        f'{efficiency(rsi_all) - efficiency(etf_rsi):+.3f} pt |',
        f'| 成交笔数 | {rsi_all["trades"]} | **{etf_rsi["trades"]}** | '
        f'{rsi_all["trades"] - etf_rsi["trades"]:+d} |',
        f'| 手续费合计/元 | {rsi_all["fees"]:,.0f} | {etf_rsi["fees"]:,.0f} | '
        f'{rsi_all["fees"] - etf_rsi["fees"]:+,.0f} |',
        f'| 期末资金/万元 | {rsi_all["ending_equity"] / 10000:.2f} | '
        f'{etf_rsi["ending_equity"] / 10000:.2f} | '
        f'{(rsi_all["ending_equity"] - etf_rsi["ending_equity"]) / 10000:+.2f} |', "",
        f'**结论：梭哈版多出的收益主要来自"仓位更高"，而非"择时更准"。** '
        f'年化从 {pct(etf_rsi["cagr"])} 抬到 {pct(rsi_all["cagr"])}（+{ (rsi_all["cagr"] - etf_rsi["cagr"]) * 100:.2f} pt），'
        f'但平均权益暴露同步从 {exposure(etf_rsi):.1f}% 翻倍到 {exposure(rsi_all):.1f}%；'
        f'按单位暴露效率算，梭哈版 {efficiency(rsi_all):.2f}% 反而**低于**分层版 {efficiency(etf_rsi):.2f}%。'
        f'夏普也从 {etf_rsi["sharpe_zero_rf"]:.2f} 降到 {rsi_all["sharpe_zero_rf"]:.2f}。', "",
        f'**为什么最大回撤完全相同（{pct(rsi_all["max_drawdown"])}）？** 两条曲线的回撤谷都落在 '
        f'2020-03-05 → 2020-03-23 的疫情急跌段。而在 2020-07-02 之前，梭哈版的仓位轨迹与分层版**逐日完全一致**'
        f'（因 RSI<20 时分层版同样买满 10 层），首次分叉要到 2020-07-02 才出现。'
        f'这意味着两个方案的"最大回撤"其实测量的是同一段历史，**不能据此认为梭哈版风险更小**；'
        f'分叉之后梭哈版仓位更高，其回撤敏感度必然更大，只是恰好没有超过 2020 年初那一次。', "",
        f'**周首日特例放大了减持的几何衰减。** 全样本 93 笔中，43 笔是"周首日 74 以上卖 20%"。'
        f'由于每次按**剩余持仓**计算比例，连续多周 RSI 高位时会呈 0.8ⁿ 的几何衰减：'
        f'2020-07-07 至 2020-09-01 连续 9 周各减 20%，仓位从满仓一路衰减到不足 5%。'
        f'与之对照，分层版在 2020-07-06（RSI>80）直接清仓归零。两种处理在 2021 年造成巨大差异：'
        f'梭哈版 2021 年 {pct(rsi_all["annual_returns"]["2021"])}，分层版仅 {pct(etf_rsi["annual_returns"]["2021"])}。'
        f'**这一年的差距来自"清仓 vs 留仓"的单次事件，而非策略的系统性优劣。**', "",
        f'**关于"梭哈"的口径提示**：下单权重设为 100%，但受组合"最低保留 10% 现金"规则约束，'
        f'建仓时最多买到初始资金的 90%，样本内最高权益暴露 {all_in_exposure_max:.1f}%'
        f'（为价格上涨后的被动漂移），与分层版满层 10 层（同样 90%）上限一致。'
        f'若要严格 100% 满仓，需放宽 min_cash 规则。', "",
        "## 四、全部方案结果", "",
        "| 方案 | 期末资金/万元 | 累计收益 | 年化收益 | 最大回撤 | 夏普 | 平均现金 | 成交笔数 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in summary["results"]:
        lines.append(f'| {row["name"]} | {row["ending_equity"] / 10000:.2f} | {pct(row["total_return"])} | '
                     f'{pct(row["cagr"])} | {pct(row["max_drawdown"])} | {row["sharpe_zero_rf"]:.2f} | '
                     f'{row["average_cash_pct"]:.2f}% | {row["trades"]} |')
    lines += [
        "", "## 五、年度收益", "",
        "| 年度 | 修正后当前策略 | 方案A 分位 | 方案B 归一化 | 仅RSI分层 | 仅RSI梭哈 | 同清单持有 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for year in current["annual_returns"]:
        label = year + ("（截至9月9日）" if year == "2026" else "")
        cells = [pct(metrics[key]["annual_returns"][year])
                 for key in ("current", "dynamic_a", "dynamic_b", "etf_rsi_only", "rsi_all_in", "hold")]
        lines.append("| " + " | ".join([label] + cells) + " |")
    lines += [
        "",
        "2019 年修正后当前策略未触发实际买入，收益为零；方案 A 因分位阈值在同年即有 69% 暴露而取得 22.62%。"
        "这直接说明了两者的差别来自进场时点与仓位，而非选股。2026 年一行是年内收益，并非年化预测。", "",
        f'梭哈版与分层版最大的年度分化在 2021 年（{pct(rsi_all["annual_returns"]["2021"])} vs '
        f'{pct(etf_rsi["annual_returns"]["2021"])}）：分层版在 2020 年 7 月 RSI>80 时已清仓归零，'
        f'而梭哈版仅按 20%/周 递减、仍留有仓位，吃到了 2021 年的上涨。', "",
        "## 六、资金与成本", "",
        f'- 修正后当前策略平均现金 {current["average_cash_pct"]:.2f}%，平均 ETF 仓位 {current["average_etf_pct"]:.2f}%。',
        f'- 期末现金比例 {cash_pct.iloc[-1]:.2f}%，样本最低现金比例 {cash_pct.min():.2f}%。买入限制不强制卖出存量仓位。',
        f'- 累计确认现金分红 {current["gross_dividends"]:,.2f} 元；已扣股息税 {current["dividend_tax_paid"]:,.2f} 元。',
        f'- 佣金、过户费及印花税合计 {current["fees"]:,.2f} 元；滑点影响约 {current["slippage_cost"]:,.2f} 元，已包含在成交价格中。',
        f'- 期末未平仓部分如立即卖出，估算另有股息税 {current["estimated_exit_dividend_tax"]:,.2f} 元。',
        "- 初始无持仓，无外部入金或提款，现金收益率设为 0；ETF 自身管理费等已反映在历史交易价格中。", "",
        "## 七、收益来源", "",
        f'修正后当前策略前三项贡献合计占净利润 {top_share * 100:.2f}%。'
        "收益仍高度集中于少数标的与 2024 年，不能视为稳定股息收益。", "",
        "| 标的 | 净利润贡献/元 | 其中现金分红/元 | 期末市值/元 |", "|---|---:|---:|---:|",
    ]
    for row in attribution:
        lines.append(f'| {row["name"]} | {row["profit"]:,.2f} | {row["dividends"]:,.2f} | {row["ending_value"]:,.2f} |')
    lines += ["", "利润贡献包含价格盈亏、分红、成交费用和已扣股息税，其合计与账户净利润逐分核对。", "",
              "## 八、计算方法", "",
              "1. 用 2017 年起的历史数据预热。**每股分红按财年口径**：取最近一个年度（12-31 报告期）"
              "分红已实施的完整财年，汇总该财年全部已实施分红（含中期），并按此后发生的送转调整每股口径；"
              "除以当日未复权收盘价得到股息率。未来实施的分红不提前使用。"
              "另保留旧“过去 365 天求和”口径用于对照。",
              "2. 方案 A 的分位为当前股息率在自身过去 3 年（756 个交易日）观测中的百分位，"
              "不足 480 个观测时回退绝对阈值；方案 B 的分子为最近至多 3 个完整财年分红的中位数。"
              "两者都只用历史，不含未来信息。",
              "3. 财报按公告日期才进入可用集合；季度 TTM EPS = 本期累计 EPS + 上年全年 EPS - 上年同期累计 EPS。"
              "缺少必要历史报告时质量为未知，当前策略暂停该股买入。送转后相应调整已公布 EPS 的每股口径。",
              "4. ETF 指标通过已发生的拆分重建连续价格，沿用项目的 Wilder RSI(6) 和每周卖出状态。"
              "2021-10-22 的 1 拆 2 同时调整份额与停牌期间估值，避免虚假收益。",
              "5. 收盘后生成信号，下一交易日按开盘价加减滑点成交；买入按 100 股/份向下取整，卖出允许清理全部尾仓。"
              "停牌、缺失交易、单价交易日及模型判断的开盘涨跌停受限方向不成交。",
              "6. 先卖后买，开盘再次核对现金和行业额度。当前配置为现金目标 10%、单行业新增目标上限 30%、ETF 额度 20%；"
              "仅 RSI 择时 ETF 方案把单层额度放宽为可用额度的 1/10（保留 10% 现金）。"
              "梭哈版的下单权重为 100%，但同样受最低 10% 现金约束，实际权益上限约 90%；"
              "其卖出按“当前剩余持仓的比例”执行，同一周累计上限 20%。",
              "7. 佣金假设单边 0.03%、最低 5 元；股票卖出印花税在 2023-08-28 前按 0.1%，此后 0.05%；ETF 不收该印花税。"
              "股票过户费 2022-04-29 前单边 0.002%，此后 0.001%。",
              "8. 股票股息税按 FIFO 持仓期限在卖出时估算：一个月以内 20%，一个月以上至一年 10%，超过一年 0%。",
              "9. 除息时确认应收分红计入净值；个股缺少完整派现日期，主模型假设延后 5 个交易日才能使用现金。", "",
    ]
    before = published_bug_before(args.results)
    quality = metrics["current"].get("quality_observations", {})
    observations = sum(quality.values()) or 1
    before_attr = {row["code"]: row for row in results["current"]["attribution"]}
    after_attr = {row["code"]: row for row in results["no_quality"]["attribution"]}
    gaps = sorted(((after_attr[code]["profit"] - before_attr[code]["profit"],
                    after_attr[code]["name"]) for code in before_attr), reverse=True)
    lines += [
        "## 九、数据与规则核查", "",
        "### 9.1 财报披露日期偏差（已修复）", "",
        "上游新浪财报接口对 2025-06-30 及更早的报告期，`publish_date` 字段整体多算了一年；"
        "而同一响应里的 `update_time`（Unix 时间戳）与真实披露时间吻合。按前者使用，"
        "基本面会整体滞后一年，并在相邻年度交界处造成 TTM EPS 空窗"
        "（例如华润江中在 2025-10-27 至 2026-03-19 之间缺失 96 个交易日）。",
        "",
        "修正方式：以 `update_time` 为准，仅当它缺失或晚于 `publish_date` 时才回退到后者。"
        "本批数据 1,209 条财报记录中有 485 条被修正（40.1%），修正后空窗降为 0 天。"
        "可用 `scripts/rebuild_backtest_data.py` 从已缓存的原始响应离线重算，无需重新下载。", "",
        "受影响的只有依赖质量筛查的网格类方案；纯 RSI 方案与关闭质量筛查的方案"
        "完全不读财报，数值逐项不变——这本身就是一次有效的交叉验证。", "",
    ]
    if before:
        lines += ["| 方案 | 修复前年化 | 修复后年化 | 变化 | 修复前笔数 | 修复后笔数 |",
                  "|---|---:|---:|---:|---:|---:|"]
        for key in ("current", "grid_only", "dynamic_a", "no_quality", "etf_rsi_only", "rsi_all_in"):
            if key not in before:
                continue
            old_value, new_value = before[key]["cagr"], metrics[key]["cagr"]
            lines.append(f'| {SHORT[key]} | {pct(old_value)} | {pct(new_value)} | '
                         f'{(new_value - old_value) * 100:+.2f} pt | {before[key]["trades"]} | '
                         f'{metrics[key]["trades"]} |')
        lines.append("")
    lines += [
        "**影响方向是「抬升网格类方案的收益」。** 修正后的财报更早进入可用集合，"
        "质量标记随之改变，原本被误判为「未知」或滞后一年才更新的标的更早恢复可买，"
        f'当前策略因此从 {pct(before["current"]["cagr"]) if before else "—"} 抬到 '
        f'{pct(current["cagr"])}。注意这意味着**此前基于旧数据的网格类结论整体偏低**，'
        "而 RSI 类结论不受影响。", "",
        "### 9.2 质量筛查挡掉了什么", "",
        f'当前策略逐日对每只标的评估分红质量：`ok` {quality.get("ok", 0):,} 次、'
        f'`warn` {quality.get("warn", 0):,} 次、`risk` {quality.get("risk", 0):,} 次、'
        f'`unknown` {quality.get("unknown", 0):,} 次；`risk`（派息率 >100% 或 EPS ≤0 仍分红）'
        f'占 {quality.get("risk", 0) / observations * 100:.1f}%。`risk` 与 `unknown` 都会暂停买入。', "",
        f'把质量筛查整个关掉（`no_quality`），全样本年化由 {pct(current["cagr"])} 升到 '
        f'{pct(metrics["no_quality"]["cagr"])}（{(metrics["no_quality"]["cagr"] - current["cagr"]) * 100:+.2f} pt），'
        f'成交笔数由 {current["trades"]} 增到 {metrics["no_quality"]["trades"]}。'
        "差异高度集中：", "",
        "| 标的 | 开启筛查利润/元 | 关闭筛查利润/元 | 差异/元 |", "|---|---:|---:|---:|",
    ]
    for gap, name in gaps[:3]:
        code = next(key for key in before_attr if before_attr[key]["name"] == name)
        lines.append(f'| {name} | {before_attr[code]["profit"]:,.0f} | '
                     f'{after_attr[code]["profit"]:,.0f} | {gap:+,.0f} |')
    top_name = gaps[0][1]
    lines += [
        "",
        f'**{top_name}贡献了绝大部分差异**：它因派息率长期高于 100%（例如 2022-06 前后按模型算得的'
        "派息率 103.6%）被一直判为 `risk`，网格因此从未买入，而回测区间内它恰好是大牛股。", "",
        "这一条要谨慎解读，**它不等于「质量筛查是错的」**：",
        "",
        "- 回测用的是今天的清单，等于提前知道哪些标的后来的表现好，属于**幸存者偏差**；"
        "质量筛查的作用是规避「高股息但分红不可持续」的陷阱股，其代价本来就表现为"
        "「偶尔错过一两只事后走强的标的」。",
        "- `risk` 的依据是模型回算的派息率，而 TTM EPS 与财年 DPS 口径不完全对齐时，"
        "派息率会被高估。要判断规则是否过严，应看它是否稳定误伤，而不是看单只标的的收益差。",
        "- 因此当前结论是「**值得进一步核查规则阈值**」，而不是「应当关闭质量筛查」。"
        "关闭筛查在本窗口带来更高收益，但这是否为幸存者偏差所致，需要更多证据才能判断。", "",
        "## 十、数据限制", "",
              "- 使用今天的 16 只股票清单回看 2019 年，存在选股与幸存者偏差，不能解释为当时已经可选出这些股票。",
              "- 现存财报历史表可能含后续更正或重述；公告日期过滤能限制使用时间，但无法恢复每个历史时点的原始财报版本。",
              "- 中国电信从 2021-08-20、中国移动从 2022-01-05 才有本次 A 股价格记录；512890 从 2019-01-18 才有交易记录。",
              "- 免费历史数据并非逐笔成交或券商对账数据；实际执行和税款可能有差异。",
              "- 本次为**样本内**对照，未做样本外验证。2019~2021 是红利股相对占优的区间，"
              "任何“更早满仓”的做法都会在该区间占便宜，判断方案优劣需以此为前提。"
              "**已另做剔除 2019~2021 的重测（见《剔除 2019~2021 后的回测对照》），"
              "结论发生变化：仅 RSI 择时 ETF 的效率优势在子区间消失。**",
              "- **RSI 择时对参数敏感，且不存在稳定最优。** 把 RSI 周期（4/6/9）与买卖阈值整体平移"
              "（-4~+4 点）做成网格重跑后：年化收益与平均权益暴露的相关系数在 0.80~0.96 之间，"
              "收益几乎完全由仓位解释；效率最优 4/4 落在几乎不交易的 -4 边界，收益最优的最优位置"
              "还随窗口漂移。当前配置（周期 6、不平移）两头都不是最优。详见《RSI 参数敏感性》。",
              "- 日收盘触发并非盘中触价交易的精确复制；本次结果是可复算的历史估算，不是收益承诺。", "",
              "## 十一、文件与来源", "",
              "- [逐项指标、配置和数据审计](summary.json)",
              "- [修正后当前策略每日净值、逐笔交易和股息明细](current.json)",
              "- [旧口径对照](baseline_naive.json)｜[方案A](dynamic_a.json)｜[方案B](dynamic_b.json)"
              "｜[方案A+B](dynamic_ab.json)｜[仅RSI分层版](etf_rsi_only.json)｜[仅RSI梭哈版](rsi_all_in.json)",
              "- [仅 RSI 择时 ETF 计算模型说明](rsi_etf_model.md)：信号、层仓状态机、成本与实测特征。",
              "- [净值及回撤图](equity_drawdown.png)｜[收益与暴露散点图](risk_return.png)",
              "- [剔除 2019~2021 后的回测对照](../subperiod-20260910/REPORT.md)：只改起始日的稳健性检验。",
              "- [RSI 参数敏感性（周期 × 阈值平移）](../sensitivity-rsi-20260910/REPORT.md)："
              "只改 RSI 周期与买卖阈值的参数扫描。",
              "- [财报披露日期修正脚本](../../scripts/rebuild_backtest_data.py)："
              "从已缓存原始响应离线重建财报数据。",
              "- [腾讯公开行情接口](https://web.ifzq.gtimg.cn/appstock/app/fqkline/get)：未复权日线。",
              "- [东方财富分红送配](https://data.eastmoney.com/yjfp/)：个股实施分红、送转记录（含报告期与除息日）。",
              "- [新浪财务报表](https://vip.stock.finance.sina.com.cn/corp/go.php/vFD_FinanceSummary/stockid/600690.phtml)：EPS 及公告日期。",
              "- [512890 分红与拆分历史](https://fundf10.eastmoney.com/fhsp_512890.html)。",
              "- [印花税减半公告](https://shanghai.chinatax.gov.cn/tax/zcfw/zcfgk/yhs/202308/t468451.html)。",
              "- [股息红利差别化税收政策](https://www.chinatax.gov.cn/chinatax/n810341/n810765/n1465977/n1466017/c1967339/content.html)。", "",
              f'缓存数据 SHA-256：`{summary["configuration"]["dataset_sha256"]}`。原始响应保存在相邻 `data-20260910/raw` 目录。', ""]
    (args.results / "REPORT.md").write_text("\n".join(lines), encoding="utf-8")
    print(args.results / "REPORT.md")
    print(args.results / "equity_drawdown.png")
    print(args.results / "risk_return.png")


if __name__ == "__main__":
    main()
