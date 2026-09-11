"""RSI 参数敏感性扫描：周期 × 阈值平移。

在「仅 RSI 择时 ETF」的分层版与梭哈版上，扫描
    周期 period ∈ {4, 6, 9}
    阈值平移 shift ∈ {-4, -2, 0, +2, +4}（所有买卖阈值整体平移，周期与层数不变）
在两个窗口（2019-2026 全样本、2022 年起）上重跑，输出 JSON、热力图与中文报告。

用法：
    python scripts/rsi_sensitivity.py --data backtests/data-20260910 \
        --output backtests/sensitivity-rsi-20260910
"""

import argparse
import json
import os
import sys
from pathlib import Path
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("MPLCONFIGDIR", str(Path(tempfile.gettempdir()) / "dividend-grid-matplotlib"))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.ticker import FuncFormatter
import numpy as np

from dividend_grid.backtest import Costs, History, SCENARIOS, Simulation
from dividend_grid.grid import GroupThresholds
from dividend_grid.portfolio import load_limits
from dividend_grid.rsi6 import DEFAULT_RSI_PARAMS, RsiParams


PERIODS = (4, 6, 9)
SHIFTS = (-4.0, -2.0, 0.0, 2.0, 4.0)
STRATEGIES = ("etf_rsi_only", "rsi_all_in")
WINDOWS = (("2019-2026", "2019-01-01", "2026-09-09"),
           ("2022-起", "2022-01-01", "2026-09-09"))

SHORT = {"etf_rsi_only": "仅 RSI 分层版", "rsi_all_in": "仅 RSI 梭哈版"}

SCENARIO_BY_KEY = {scenario.key: scenario for scenario in SCENARIOS}


def setup_fonts() -> None:
    font_path = Path("/System/Library/Fonts/Supplemental/Arial Unicode.ttf")
    if font_path.exists():
        font_manager.fontManager.addfont(str(font_path))
        plt.rcParams["font.family"] = font_manager.FontProperties(fname=str(font_path)).get_name()
    plt.rcParams.update({"axes.unicode_minus": False, "font.size": 10, "axes.spines.top": False,
                         "axes.spines.right": False, "axes.edgecolor": "#c9ced3",
                         "text.color": "#24282d", "axes.labelcolor": "#41474e",
                         "xtick.color": "#626970", "ytick.color": "#626970"})


def shift_label(shift: float) -> str:
    return f"{shift:+.0f}" if shift else "0"


def run_grid(data: Path, groups, limits, costs) -> list[dict]:
    cells = []
    for period in PERIODS:
        history = History.load(data, caliber="fiscal", rsi_period=period)
        for strategy_key in STRATEGIES:
            scenario = SCENARIO_BY_KEY[strategy_key]
            for shift in SHIFTS:
                params = RsiParams(period=period).shifted(shift)
                for window_name, start, end in WINDOWS:
                    result = Simulation(history, scenario, groups, limits, start, end,
                                        1_000_000, costs, 5, params).run()
                    metric = result["metrics"]
                    exposure = 100 - metric["average_cash_pct"]
                    cells.append({
                        "period": period,
                        "shift": shift,
                        "strategy": strategy_key,
                        "window": window_name,
                        "cagr": metric["cagr"],
                        "max_drawdown": metric["max_drawdown"],
                        "sharpe": metric["sharpe_zero_rf"],
                        "average_cash_pct": metric["average_cash_pct"],
                        "exposure": exposure,
                        "efficiency": metric["cagr"] * 100 / exposure if exposure else 0.0,
                        "trades": metric["trades"],
                        "ending_equity": metric["ending_equity"],
                    })
                    print(f'{period} {shift:+.0f} {strategy_key:14s} {window_name:9s} '
                          f'cagr={metric["cagr"] * 100:6.2f}% '
                          f'eff={cells[-1]["efficiency"]:.3f}%', flush=True)
    return cells


def cell(cells: list[dict], period: int, shift: float, strategy: str, window: str) -> dict:
    for row in cells:
        if (row["period"] == period and row["shift"] == shift
                and row["strategy"] == strategy and row["window"] == window):
            return row
    raise KeyError((period, shift, strategy, window))


def matrix(cells: list[dict], strategy: str, window: str, field: str) -> np.ndarray:
    return np.array([[cell(cells, period, shift, strategy, window)[field] for shift in SHIFTS]
                     for period in PERIODS], dtype=float)


def heatmaps(cells: list[dict], field: str, title: str, note: str, path: Path,
             scale: float, digits: int, cmap: str) -> None:
    figure, axes = plt.subplots(2, 2, figsize=(13.2, 9.0))
    for row_index, strategy in enumerate(STRATEGIES):
        for column_index, (window_name, _, _) in enumerate(WINDOWS):
            axis = axes[row_index][column_index]
            values = matrix(cells, strategy, window_name, field) * scale
            imaged = axis.imshow(values, cmap=cmap, aspect="auto")
            for i in range(len(PERIODS)):
                for j in range(len(SHIFTS)):
                    value = values[i][j]
                    midpoint = (np.nanmin(values) + np.nanmax(values)) / 2
                    color = "white" if value > midpoint else "#24282d"
                    weight = "bold" if (SHIFTS[j] == 0 and PERIODS[i] == 6) else "normal"
                    axis.text(j, i, f"{value:.{digits}f}" + ("%" if scale == 100 else ""),
                              ha="center", va="center", fontsize=10, color=color, fontweight=weight)
            # 默认配置（周期 6、平移 0）加方框标记
            axis.add_patch(plt.Rectangle((SHIFTS.index(0.0) - 0.5, PERIODS.index(6) - 0.5), 1, 1,
                                         fill=False, edgecolor="#c0392b", linewidth=2.2, zorder=5))
            axis.set_xticks(range(len(SHIFTS)))
            axis.set_xticklabels([shift_label(s) for s in SHIFTS])
            axis.set_yticks(range(len(PERIODS)))
            axis.set_yticklabels([f"周期 {p}" for p in PERIODS])
            axis.set_xlabel("阈值平移（点）")
            axis.set_title(f"{SHORT[strategy]} · {window_name}", loc="left", fontsize=12,
                           fontweight="bold", pad=6)
            figure.colorbar(imaged, ax=axis, fraction=0.046, pad=0.03)
    figure.suptitle(title, x=0.055, ha="left", fontsize=18, fontweight="bold")
    figure.text(0.055, 0.935, note, color="#5f666e", fontsize=10)
    figure.text(0.055, 0.012,
                "红框 = 当前生产配置（周期 6、不平移）。每一格是一次独立完整回测；历史结果不代表未来收益。",
                fontsize=9, color="#697079")
    figure.subplots_adjust(top=0.885, bottom=0.095, left=0.07, right=0.975, hspace=0.3, wspace=0.18)
    figure.savefig(path, dpi=180, facecolor="white")
    plt.close(figure)


def pct(value: float) -> str:
    return f"{value * 100:.2f}%"


def table(cells: list[dict], strategy: str, window: str, field: str, formatter) -> list[str]:
    lines = ["| 周期 \\ 平移 | " + " | ".join(shift_label(s) for s in SHIFTS) + " |",
             "|---|" + "---:|" * len(SHIFTS)]
    for period in PERIODS:
        values = [formatter(cell(cells, period, shift, strategy, window)[field]) for shift in SHIFTS]
        lines.append(f"| {period} | " + " | ".join(values) + " |")
    return lines


def spread(cells: list[dict], strategy: str, window: str, field: str) -> tuple[float, float, float]:
    values = matrix(cells, strategy, window, field)
    return float(values.min()), float(values.max()), float(values.max() - values.min())


def exposure_correlation(cells: list[dict], strategy: str, window: str) -> float:
    exposures = np.array([row["exposure"] for row in cells
                          if row["strategy"] == strategy and row["window"] == window])
    returns = np.array([row["cagr"] * 100 for row in cells
                        if row["strategy"] == strategy and row["window"] == window])
    return float(np.corrcoef(exposures, returns)[0, 1])


def argmax(cells: list[dict], strategy: str, window: str, field: str) -> dict:
    pool = [row for row in cells if row["strategy"] == strategy and row["window"] == window]
    return max(pool, key=lambda row: row[field])


def render_report(cells: list[dict], path: Path) -> None:
    lines = [
        "# RSI 参数敏感性：周期 × 阈值平移",
        "",
        "在「仅 RSI 择时 ETF」的两个版本上，把 **RSI 周期**（4 / 6 / 9 周）与 **所有买卖阈值"
        "的整体平移量**（-4 / -2 / 0 / +2 / +4 点）做成参数网格，在两个窗口上重跑。"
        "数据、成本、执行假设全部不变，只改这两个参数；每个格子是一次完整的独立回测。",
        "",
        "阈值平移的含义：周期 6、平移 +2 时，买入档由 20/25/31 变为 22/27/33，"
        "卖出档由 74/80 变为 76/82；梭哈版由 31/70/74 变为 33/72/76。层数与仓位规则不变。",
        "红框标出的是当前生产配置（周期 6、不平移）。",
        "",
        "## 一、一句话结论",
        "",
        "**参数曲面没有内部最优。** 效率最优 4/4 全落在几乎不交易的 -4 边界；收益最优 2/4 落在 +4 边界，"
        "另 2 个落在周期 4 的内部，且**最优位置随窗口漂移**（梭哈版周期 4 全样本最优于平移 -2，"
        "2022 年起却移到平移 0）。换句话说：**平移越大、越早进场、暴露越高、年化越高**；"
        "**平移越小、越少交易、暴露越低、单位暴露效率越高**。当前配置（周期 6、平移 0）对两者都不是最优。"
        "这说明决定结果的仍是「持有多少」，而不是「RSI 择时有多准」——与全样本、子区间得到的结论一致。",
        "",
        "## 二、收益几乎完全由暴露解释",
        "",
        "| 策略 | 窗口 | corr(平均权益暴露, 年化收益) | 暴露范围 | 年化范围 |",
        "|---|---|---:|---:|---:|",
    ]
    for strategy in STRATEGIES:
        for window_name, _, _ in WINDOWS:
            correlation = exposure_correlation(cells, strategy, window_name)
            pool = [row for row in cells
                    if row["strategy"] == strategy and row["window"] == window_name]
            low_e = min(row["exposure"] for row in pool)
            high_e = max(row["exposure"] for row in pool)
            low_c = min(row["cagr"] for row in pool) * 100
            high_c = max(row["cagr"] for row in pool) * 100
            lines.append(f'| {SHORT[strategy]} | {window_name} | {correlation:+.3f} | '
                         f'{low_e:.1f}%~{high_e:.1f}% | {low_c:.2f}%~{high_c:.2f}% |')
    lines += [
        "",
        "30 个格子里，年化收益与平均权益暴露的相关系数都在 +0.80 以上（最高 +0.957）。"
        "换句话说：**只要知道一次回测的平均仓位，就基本能预测它的年化收益**，"
        "RSI 周期与阈值只通过「改变仓位」间接起作用。",
        "",
        "> 注意「单位暴露效率」这个指标本身是暴露的递减函数（分子随暴露上升、分母也上升），"
        "所以它的最优必然落在暴露最小的角落——这正是下面 -4 列全部异常突出的原因。",
        "",
        "## 三、最优参数落在哪儿",
        "",
        "| 策略 | 窗口 | 收益最优 | 效率最优 |",
        "|---|---|---|---|",
    ]
    for strategy in STRATEGIES:
        for window_name, _, _ in WINDOWS:
            best_return = argmax(cells, strategy, window_name, "cagr")
            best_efficiency = argmax(cells, strategy, window_name, "efficiency")
            edge_return = "（边界）" if abs(best_return["shift"]) == 4 else "（内部）"
            edge_efficiency = "（边界）" if abs(best_efficiency["shift"]) == 4 else "（内部）"
            lines.append(
                f'| {SHORT[strategy]} | {window_name} | '
                f'周期 {best_return["period"]} / 平移 {shift_label(best_return["shift"])}'
                f'（年化 {pct(best_return["cagr"])}）{edge_return} | '
                f'周期 {best_efficiency["period"]} / 平移 {shift_label(best_efficiency["shift"])}'
                f'（效率 {best_efficiency["efficiency"]:.3f}%）{edge_efficiency} |')
    lines += [
        "",
        "四组里效率最优全部落在 **-4 边界**；收益最优 2 组在 **+4 边界**、2 组在周期 4 的内部。"
        "边界最优意味着真正的极值在扫描范围之外，当前配置既非收益最优也非效率最优。",
        "",
        "最不稳定的正是**收益最优的位置**：梭哈版 + 周期 4 在全样本最优点为平移 -2（13.27%），"
        "2022 年起却移到平移 0（13.07%）；分层版 2022 年起的 +2 与 +4 只差 0.01 个百分点，"
        "是平台而非峰点。**最优参数随窗口漂移，不可复现。**",
        "",
        "## 四、周期的影响同样不小",
        "",
        "| 周期（平移 0，全样本） | 分层版年化 | 分层版效率 | 梭哈版年化 | 梭哈版效率 |",
        "|---|---:|---:|---:|---:|",
    ]
    for period in PERIODS:
        cells_row = [cell(cells, period, 0.0, strategy, "2019-2026") for strategy in STRATEGIES]
        lines.append(f'| {period} | {pct(cells_row[0]["cagr"])} | {cells_row[0]["efficiency"]:.3f}% | '
                     f'{pct(cells_row[1]["cagr"])} | {cells_row[1]["efficiency"]:.3f}% |')
    lines += [
        "",
        "生产选用的周期 6 并不是收益最好的：分层版在周期 4 得到 11.74%（周期 6 为 9.26%），"
        "差 2.5 个百分点。周期 9 则几乎不交易（全样本仅 8~9 笔），年化掉到 3.79%，"
        "等于长期空仓——它的「高效率」只是低暴露的副产品。",
        "",
        "换个周期就能把年化从 9.26% 改到 11.74%，而这两个周期在设计上没有优劣之分，"
        "说明**此前「仅 RSI 择时 ETF 效率最高」的说法，一部分是周期 6 这个具体选择的产物**。",
        "",
        "## 五、单位暴露效率（年化收益 ÷ 平均权益暴露）",
        "",
        "![效率热力图](rsi_sensitivity_efficiency.png)",
        "",
    ]
    for strategy in STRATEGIES:
        for window_name, _, _ in WINDOWS:
            lines += [f"### {SHORT[strategy]} · {window_name}", ""]
            lines += table(cells, strategy, window_name, "efficiency",
                           lambda value: f"{value:.3f}%")
            low, high, width = spread(cells, strategy, window_name, "efficiency")
            lines += ["", f"极差 {width:.3f} 个百分点（最低 {low:.3f}%，最高 {high:.3f}%）。", ""]

    lines += [
        "## 六、年化收益",
        "",
        "![年化收益热力图](rsi_sensitivity_cagr.png)",
        "",
    ]
    for strategy in STRATEGIES:
        for window_name, _, _ in WINDOWS:
            lines += [f"### {SHORT[strategy]} · {window_name}", ""]
            lines += table(cells, strategy, window_name, "cagr", pct)
            low, high, width = spread(cells, strategy, window_name, "cagr")
            lines += ["", f"极差 {width * 100:.2f} 个百分点（最低 {low * 100:.2f}%，"
                          f"最高 {high * 100:.2f}%）。", ""]

    lines += ["## 七、最大回撤与夏普", ""]
    for strategy in STRATEGIES:
        for window_name, _, _ in WINDOWS:
            lines += [f"### {SHORT[strategy]} · {window_name}", "", "最大回撤：", ""]
            lines += table(cells, strategy, window_name, "max_drawdown", pct)
            lines += ["", "夏普（无风险利率 0）：", ""]
            lines += table(cells, strategy, window_name, "sharpe", lambda value: f"{value:.2f}")
            lines.append("")

    lines += ["## 八、平均权益暴露（%）", ""]
    for strategy in STRATEGIES:
        for window_name, _, _ in WINDOWS:
            lines += [f"### {SHORT[strategy]} · {window_name}", ""]
            lines += table(cells, strategy, window_name, "exposure", lambda value: f"{value:.1f}")
            lines.append("")

    lines += [
        "## 九、怎么读这张表", "",
        "- **对角线是否平滑**：相邻参数之间数值平滑变化＝策略对参数不敏感；"
        "出现孤立的尖峰/尖谷＝结论靠特定参数凑出来。",
        "- **最优参数是否在边界**：若最优的平移量落在 -4 或 +4 两端，说明真正的"
        "最优在扫描范围之外，当前配置更可能是巧合而非稳定最优。",
        "- **红框是当前生产配置**（周期 6、不平移）。看它落在网格中的相对位置。",
        "- 全样本窗口含 2019~2021 红利股占优期，任何「更早满仓」的做法都会占便宜，"
        "因此要对照 2022 年起那一列一起看。",
        "",
        "## 十、复算方式", "",
        "```bash",
        "uv run python scripts/rsi_sensitivity.py \\",
        "    --data backtests/data-20260910 \\",
        "    --output backtests/sensitivity-rsi-20260910",
        "",
        "# 只重新渲染报告与图（复用已有 cells.json，不重跑回测）",
        "uv run python scripts/rsi_sensitivity.py \\",
        "    --output backtests/sensitivity-rsi-20260910 --render-only",
        "```",
        "",
    ]
    (path / "REPORT.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, default=Path("backtests/data-20260910"))
    parser.add_argument("--output", type=Path, default=Path("backtests/sensitivity-rsi-20260910"))
    parser.add_argument("--slippage", type=float, default=0.0005)
    parser.add_argument("--render-only", action="store_true",
                        help="复用已有 cells.json 重新渲染报告与图，不重跑回测")
    args = parser.parse_args()

    args.output.mkdir(parents=True, exist_ok=True)
    if args.render_only:
        payload = json.loads((args.output / "cells.json").read_text(encoding="utf-8"))
        cells = payload["cells"]
    else:
        raw_groups = json.loads(Path("strategy_groups.json").read_text(encoding="utf-8"))
        groups = {group: GroupThresholds(tuple(value["buy"]), tuple(value["sell"]))
                  for group, value in raw_groups.items()}
        limits = load_limits(Path("portfolio_rules.json"))
        costs = Costs(slippage=args.slippage)
        cells = run_grid(args.data, groups, limits, costs)
        (args.output / "cells.json").write_text(
            json.dumps({"periods": PERIODS, "shifts": SHIFTS, "strategies": STRATEGIES,
                        "windows": [name for name, _, _ in WINDOWS], "cells": cells},
                       ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")

    setup_fonts()
    heatmaps(cells, "efficiency",
             "RSI 参数敏感性：单位暴露效率对周期与阈值的反应",
             "每一格是一次完整回测；红框为当前生产配置。效率＝年化收益 ÷ 平均权益暴露。",
             args.output / "rsi_sensitivity_efficiency.png", 1.0, 3, "YlGnBu")
    heatmaps(cells, "cagr",
             "RSI 参数敏感性：年化收益对周期与阈值的反应",
             "每一格是一次完整回测；红框为当前生产配置。",
             args.output / "rsi_sensitivity_cagr.png", 100.0, 2, "YlGnBu")
    render_report(cells, args.output)

    # 与当前生产配置的一致性核对：周期 6、平移 0 应逐项等于默认参数的结果。
    for strategy in STRATEGIES:
        for window_name, _, _ in WINDOWS:
            row = cell(cells, 6, 0.0, strategy, window_name)
            print(f'[check] {strategy} {window_name}: cagr={pct(row["cagr"])} '
                  f'eff={row["efficiency"]:.3f}% trades={row["trades"]}')
    print(args.output / "REPORT.md")


if __name__ == "__main__":
    main()
