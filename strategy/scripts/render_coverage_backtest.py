"""Render the coverage-gated comparison, audit and sensitivity windows."""

import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from render_backtest_report import setup_fonts, plt, pd, mdates, FuncFormatter
from dividend_grid.backtest import dataset_fingerprint
from dividend_grid.backtest_coverage import coverage_on
from dividend_grid.rsi6 import DEFAULT_RSI_PARAMS


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def pct(value):
    return f"{value * 100:.2f}%"


NAMES = {"current": "新版网格+RSI", "payout_only": "仅派息率筛查对照",
         "grid_hold_etf": "新版网格+ETF持有", "hold": "同清单买入持有",
         "etf_hold": "初始80% ETF买入持有"}
COLORS = {"current": "#11775c", "payout_only": "#bb514c", "grid_hold_etf": "#397daa",
          "hold": "#777b80", "etf_hold": "#b18b2c"}


def plot(results, path):
    setup_fonts()
    fig, axes = plt.subplots(3, 1, figsize=(12.8, 10.5), sharex=True,
                             gridspec_kw={"height_ratios": [3, 1.7, 1.5]})
    for key, result in results.items():
        frame = pd.DataFrame(result["daily"])
        dates = pd.to_datetime(frame["date"])
        peak = frame["equity"].cummax().clip(lower=result["metrics"]["initial_capital"])
        width = 2.3 if key == "current" else 1.3
        axes[0].plot(dates, frame["equity"] / 10000, color=COLORS[key],
                     label=NAMES[key], linewidth=width)
        axes[1].plot(dates, (frame["equity"] / peak - 1) * 100, color=COLORS[key], linewidth=width)
        if key in ("current", "payout_only"):
            axes[2].plot(dates, frame["cash"] / frame["equity"] * 100, color=COLORS[key],
                         label=NAMES[key], linewidth=1.5)
    axes[0].set_ylabel("账户净值 / 万元")
    axes[1].set_ylabel("回撤")
    axes[2].set_ylabel("现金占总资产")
    for axis in axes[1:]:
        axis.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:.0f}%"))
    axes[2].set_ylim(0, 105)
    axes[2].axhline(20, linewidth=0.9, color="#b7bdc3", linestyle="--")
    axes[0].legend(loc="upper left", ncol=2, fontsize=9, frameon=False)
    axes[2].legend(loc="upper right", ncol=2, fontsize=9, frameon=False)
    axes[2].xaxis.set_major_locator(mdates.YearLocator())
    axes[2].xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    for axis in axes:
        axis.grid(axis="y", color="#e7ebee", linewidth=0.7)
        axis.margins(x=0.01)
    current = results["current"]["metrics"]
    fig.suptitle("新版红利策略回测", x=0.085, y=0.965, ha="left", fontsize=20, fontweight="bold")
    fig.text(0.085, 0.928, f'{current["start"]} 至 {current["end"]}  |  初始100万元  |  已计分红、税费和滑点',
             fontsize=10, color="#60666c")
    fig.text(0.085, 0.019, "现行策略：现金底线20%、ETF预算20%。被动基准仅初始配比，持有期间比例随市值漂移。",
             fontsize=9, color="#60666c")
    fig.subplots_adjust(top=0.892, bottom=0.075, left=0.085, right=0.975, hspace=0.17)
    fig.savefig(path, dpi=180, facecolor="white")
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", type=Path, default=Path("backtests/results-coverage-20260911"))
    parser.add_argument("--data", type=Path, default=Path("backtests/data-coverage-20260911"))
    args = parser.parse_args()
    summary = read(args.results / "summary.json")
    fingerprint = dataset_fingerprint(args.data)
    assert fingerprint == summary["configuration"]["dataset_sha256"], "Dataset changed after simulation"
    results = {m["key"]: read(args.results / f'{m["key"]}.json') for m in summary["results"]}
    metrics = {k: v["metrics"] for k, v in results.items()}
    current, control = metrics["current"], metrics["payout_only"]
    cfg = summary["configuration"]
    if cfg["limits"]["min_cash_pct"] != 20 or cfg["limits"]["etf_budget_pct"] != 20 or cfg["capital"] != 1_000_000:
        raise ValueError("This report's benchmark labels require 20% cash, 20% ETF and CNY 1 million")
    manifest = read(args.data / "manifest.json")
    audit = []
    for code, spec in manifest["universe"].items():
        item = read(args.data / f"{code}.json")
        if spec["group"] == "ETF":
            continue
        coverage = coverage_on(code, item["coverage"], current["end"])
        records = item["coverage"]["cashflows"] or item["coverage"]["banks"]
        audit.append({"code": code, "name": spec["name"], "reports": len(records),
                      "first_publication": min((r["published"] for r in records), default=None),
                      "end_coverage": asdict(coverage)})
    source_hash = hashlib.sha256()
    for file in sorted(Path("dividend_grid").glob("*.py")):
        source_hash.update(file.name.encode())
        source_hash.update(file.read_bytes())
    for key, result in results.items():
        assert all(t["signal_date"] < t["date"] for t in result["trades"])
        assert all(d["cash"] >= 0 for d in result["daily"])
        assert abs(sum(a["profit"] for a in result["attribution"]) -
                   (result["metrics"]["ending_equity"] - cfg["capital"])) < 0.01
    verification = {"dataset_sha256": fingerprint, "source_sha256": source_hash.hexdigest(),
                    "rsi": asdict(DEFAULT_RSI_PARAMS), "coverage_audit": audit,
                    "checks": ["all trades after signal date", "no borrowed cash", "P&L reconciles to NAV"]}
    (args.results / "verification.json").write_text(json.dumps(verification, ensure_ascii=False, indent=2))
    plot(results, args.results / "equity_drawdown.png")
    lines = ["# 新版红利策略收益回测", "",
        f'区间：{current["start"]} 至 {current["end"]}。初始全部现金100万元；当前16只个股清单及512890。', "",
        f'新版期末 **{current["ending_equity"]/10000:.2f}万元**，累计收益 **{pct(current["total_return"])}**，'
        f'年化 **{pct(current["cagr"])}**，最大回撤 **{pct(abs(current["max_drawdown"]))}**。', "",
        f'与同资金约束、仅派息率筛查对照相比，期末少{(control["ending_equity"]-current["ending_equity"])/10000:.2f}万元，'
        f'年化少{(control["cagr"]-current["cagr"])*100:.2f}个百分点，最大回撤收窄'
        f'{(abs(control["max_drawdown"])-abs(current["max_drawdown"]))*100:.2f}个百分点。'
        '这是收益与回撤的取舍，不能据此宣称新版提高了收益。', "",
        "## 同期比较", "",
        "| 方案 | 期末万元 | 累计收益 | 年化 | 最大回撤 | 平均现金 | 交易笔数 |",
        "|---|---:|---:|---:|---:|---:|---:|"]
    for key, m in metrics.items():
        lines.append(f'| {NAMES[key]} | {m["ending_equity"]/10000:.2f} | {pct(m["total_return"])} | '
                     f'{pct(m["cagr"])} | {pct(abs(m["max_drawdown"]))} | {m["average_cash_pct"]:.2f}% | {m["trades"]} |')
    lines += ["", "![净值、回撤与现金比例](equity_drawdown.png)", "",
        "基准定义：同清单持有初始分配60%个股等权、20% ETF、20%现金；ETF持有初始分配80% ETF、20%现金。"
        "新品种上市前保留相应现金，上市后有价格并满足成交条件才买。持有基准不主动再平衡，分红留现金，"
        "实际比例会随涨跌漂移，因此并不持续遵守20%现金底线。新版网格+ETF持有中的ETF同样只初始买入20%。", "",
        f'新版平均现金{current["average_cash_pct"]:.2f}%，ETF平均{current["average_etf_pct"]:.2f}%，'
        f'对照平均现金{control["average_cash_pct"]:.2f}%。更多现金和持仓变化共同影响回撤，不能单独归因于质量筛查。'
        f'零无风险利率夏普：新版{current["sharpe_zero_rf"]:.3f}、对照{control["sharpe_zero_rf"]:.3f}，本样本未显示改善。', "",
        "## 逐年收益", "", "2026年为截至9月10日的累计收益，不是全年预测。", "",
        "| 年份 | 新版 | 仅派息率筛查 | 同清单持有 | 初始80% ETF持有 |", "|---|---:|---:|---:|---:|"]
    for year in current["annual_returns"]:
        lines.append("| " + year + " | " + " | ".join(pct(metrics[k]["annual_returns"][year])
                     for k in ("current", "payout_only", "hold", "etf_hold")) + " |")
    lines += ["", "## 更换起点", "", "每个窗口均重新从100万元现金开始；它们是重叠样本检查，不是独立样本外验证。", "",
              "| 起点 | 新版年化/最大回撤 | 对照年化/最大回撤 | 同清单持有年化 | ETF持有年化 |", "|---|---:|---:|---:|---:|"]
    for window in ("from-2022", "from-2023"):
        path = args.results / window / "summary.json"
        if not path.exists():
            raise ValueError(f"Missing subperiod: {path}")
        sub = read(path)
        assert sub["configuration"]["dataset_sha256"] == fingerprint
        s = {r["key"]: r for r in sub["results"]}
        lines.append(f'| {s["current"]["start"]} | {pct(s["current"]["cagr"])}/{pct(abs(s["current"]["max_drawdown"]))} | '
                     f'{pct(s["payout_only"]["cagr"])}/{pct(abs(s["payout_only"]["max_drawdown"]))} | '
                     f'{pct(s["hold"]["cagr"])} | {pct(s["etf_hold"]["cagr"])} |')
    lines += ["", "两个较晚起点也表现为新版收益低于对照、回撤较小，但新版收益超过两个被动基准。"
              "全区间与子区间的相对排名不同，不能只选表现最好的起点。", "",
              "## 现金覆盖检查的影响", "",
              "以下为本来触发买入、通过派息率检查后，被新增覆盖规则拦截的交易日数；连续多日可以是同一个信号，不能当作独立订单数。", "",
              "| 标的 | 原因 | 信号日数 |", "|---|---|---:|"]
    for key, count in sorted(current["coverage_blocked_signal_days"].items(), key=lambda item: -item[1]):
        code, flag = key.split(":")
        lines.append(f'| {manifest["universe"][code]["name"]} {code[2:]} | '
                     f'{"数据/专项复核未知" if flag == "unknown" else "覆盖风险"} | {count} |')
    lines += ["", "保险没有当时有效的专项复核，按未知处理；没有为获得更好的回测结果补填通过记录。"
              "普通企业必须有至少3个连续、现金流与年度分红匹配的财年；多年覆盖不足1倍或最新完整年度不足1倍则暂停买入。"
              "缺失分红不补零，只有年度方案完成后才合并中期及年度分红总额。", "",
              "## 收益与费用", "",
              f'累计毛股息{current["gross_dividends"]:,.2f}元；交易费用{current["fees"]:,.2f}元；'
              f'卖出时已扣股息税{current["dividend_tax_paid"]:,.2f}元；滑点成本{current["slippage_cost"]:,.2f}元。'
              '以上均已进入期末净值，股息不可再重复加到收益上。', "",
              f'期末按市值估值，未模拟强制全部卖出；若期末卖出，估算另有股息补税'
              f'{current["estimated_exit_dividend_tax"]:,.2f}元及卖出费用/滑点。', "",
              "| 标的 | 净利润贡献/元 | 期末市值/元 |", "|---|---:|---:|"]
    for row in sorted(results["current"]["attribution"], key=lambda r: -r["profit"]):
        lines.append(f'| {row["name"]} | {row["profit"]:,.2f} | {row["ending_value"]:,.2f} |')
    lines += ["", "## 执行与数据边界", "",
        "- 日末生成信号，下一可交易日开盘模拟成交；按100股买入，卖出遵循整手及清仓零股规则。资金、行业额度在开盘重新检查；信号不保证实际成交。",
        "- 现金底线20%、ETF预留20%、个股剩余60%、单行业30%。额度约束新增买入，价格漂移不触发强制再平衡。",
        "- ETF采用周RSI(6)，将本周截至当日收盘的价格作为未完成周线；仅回放每天一次收盘检查，无法重建盘中触发。10/7/4层分别对应总资产20%/14%/8%。",
        "- 佣金万三、最低5元，单边滑点万五；股票印花税及过户费按历史费率切换，股息税按FIFO持有期在卖出时估算。",
        "- 不复权价成交，显式处理现金分红与送转；缺少真实派息到账日时，假设除息后5个交易日到账，到账前为应收股息不能买入。",
        "- 财年每股分红与TTM EPS计算派息率；这仍不等于完全匹配财年的派息率。现金覆盖检查另用匹配年度现金流和分红总额。",
        *["- " + value + "。" for value in cfg["limitations"]],
        "- 涨跌停、停牌检查为日线近似；未模拟盘口排队、市场冲击或限价单。公开数据质量及历史报表修订仍会影响结果。", "",
        "## 数据核对与复现", "",
        "现金流来自新浪公开财报，分红和银行指标来自东方财富，日线来自腾讯。原始响应已保留在数据目录raw中。"
        "按记录披露日期过滤未来信息，但现存报表及更新日期不等同于逐日归档的原始披露数据库。", "",
        "银行字段校正：招商银行2025年末核心一级资本充足率14.16%、一级资本充足率16.51%。"
        "公开接口对应字段分别为HXYJBCZL和FIRST_ADEQUACY_RATIO；实盘和回测均改用前者。"
        "来源：[招商银行2025年度报告](https://s3gw.cmbimg.com/lb5001-cmbweb-prd-1255000097/CIOAMananger/20260329/eb427a44-727f-48e8-9e21-5fb007f26495.pdf)。", "",
        f'数据SHA-256：`{fingerprint}`。', "",
        "[参数及指标](summary.json) · [覆盖数据核对](verification.json) · [新版逐笔交易和每日净值](current.json)", "",
        "```bash", "uv run python scripts/backtest.py --scenarios current payout_only grid_hold_etf hold etf_hold",
        "uv run python scripts/render_coverage_backtest.py", "```", "",
        "历史收益为模型估算，既不是未来收益承诺，也不能证明筛查阈值已经最优。"]
    (args.results / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(args.results / "REPORT.md")


if __name__ == "__main__":
    main()
