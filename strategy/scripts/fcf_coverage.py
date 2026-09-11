"""检验「自由现金流 ÷ 现金分红」覆盖倍数（含近 3~5 年平均）。

**单一口径**：FCF = 经营现金流 − 购建固定资产、无形资产和其他长期资产支付的现金
（= 标准「资本开支」）。数据全部来自新浪财报接口 `source=llb`，与实盘提醒
（`dividend_grid/coverage.py`）同源同定义，避免不同数据源给出不同答案。

四个窗口同时给出，因为单年 FCF 波动极大、不同窗口会背离：

- **TTM**（最近 4 个季度滚动）——反映最新造血能力；
- **FY**（最近完整财年）——分红实际对应的年度；
- **近 3 年 / 近 5 年合计**——`ΣFCF ÷ Σ分红`，**主判据**（实盘提醒即用 5 年口径）。
  窗口取最近的完整财年（连续不跳年）；窗口内无已实施分红记录的财年计为 0；
  最新财年分红尚未可知时先剔除（避免把「未公布」误当 0 分红而高估倍数）。

分红总额：
- TTM 用「股息率(TTM) × 总市值」；
- 单年与多年用东方财富分红明细 `RPT_SHAREBONUS_DET`：
  `Σ(每 10 股派息 ÷ 10 × 总股本)`，按 `REPORT_DATE` 年份归集（含中期分红）。

⚠️ 银行现金流含同业往来，FCF 无经济含义，请改用监管指标
（`dividend_grid/coverage.py::fetch_bank_metrics`）。

用法：
    uv run python scripts/fcf_coverage.py
    uv run python scripts/fcf_coverage.py --codes sz000423 sh600887 --years 5
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dividend_grid.coverage import (
    assess_bank, assess_fcf, emoji as _cov_emoji, fetch_bank_metrics, fetch_cashflow,
    fetch_dividends, fiscal_year_fcf, multi_year_ratio, ttm_value,
)
from dividend_grid.datasource import AkShareDataSource

DEFAULT_CODES = [
    "sz000423", "sh600750", "sh600887", "sh600011", "sz000538",
    "sh600690", "sh601166", "sz000001", "sh601318",
]


def _tag(ratio: float | None, threshold: float, years: int = 0) -> str:
    if ratio is None:
        return "数据不足"
    head = f"{ratio:.2f}x"
    if ratio >= threshold:
        return f"✅ {head}"
    if ratio >= 1.0:
        return f"🟡 {head}"
    return f"🔴 {head}"


def _load_names(path: Path) -> dict[str, str]:
    """从监控清单取代码→名称；缺失时回退代码本身。"""
    if not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    out: dict[str, str] = {}
    if isinstance(raw, dict):
        for code, val in raw.items():
            out[str(code)] = str(val.get("name")) if isinstance(val, dict) else str(val)
    return out


def _mean_annual(detail: dict[int, tuple[float, float]]) -> float | None:
    """年度比值的简单平均（只计有分红的年份），回答「典型年份覆盖几倍」。"""
    ratios = [fcf / div for fcf, div in detail.values() if div > 0]
    return sum(ratios) / len(ratios) if ratios else None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--threshold", type=float, default=1.5)
    parser.add_argument("--stocks-file", type=Path, default=Path("watchlist.json"))
    parser.add_argument("--detail", action="store_true", help="逐财年明细")
    parser.add_argument("--codes", nargs="*", default=DEFAULT_CODES)
    args = parser.parse_args()

    names = _load_names(args.stocks_file)
    source = AkShareDataSource()
    print(f"阈值 {args.threshold}x | FCF = 经营现金流 − 资本开支（新浪财报口径）")
    print("「近N年」= 窗口内合计 FCF ÷ 合计分红（主判据）；「年均」= 各年比值的简单平均")
    print("实盘提醒采用近 5 年合计口径。\n")
    print(f"{'标的':<9}{'股息率':>7}{'TTM':>9}{'FY':>9}{'近3年':>10}{'近5年':>10}{'年均':>9}{'年数':>5}")
    print("-" * 70)

    rows = []
    for code in args.codes:
        name = names.get(code, code)
        try:
            yield_pct, _ = source.get_yield(code)
            metrics = source.get_quality(code)
        except Exception as exc:  # noqa: BLE001
            print(f"{name:<9} 行情获取失败: {exc}")
            continue

        # 银行：现金流含同业往来，FCF 无经济含义 → 改报监管指标
        try:
            bank = fetch_bank_metrics(code)
        except Exception:  # noqa: BLE001
            bank = None
        if bank is not None:
            flag, _ = assess_bank(bank)
            print(f"{name:<9}{yield_pct:>6.2f}%   银行·FCF 不适用 → {_cov_emoji(flag)} "
                  f"{bank.report} 核心一级 {bank.cet1}% / 不良 {bank.npl}% / 拨备 {bank.provision}%")
            continue

        try:
            cashflow = fetch_cashflow(code)
        except Exception as exc:  # noqa: BLE001
            print(f"{name:<9} 现金流获取失败: {exc}")
            continue

        latest = max(cashflow)
        annual_periods = [p for p in cashflow if p.endswith("1231")]
        latest_year = int(max(annual_periods)[:4])
        ocf = ttm_value({p: r["ocf"] for p, r in cashflow.items()}, latest)
        capex = ttm_value({p: r["capex"] for p, r in cashflow.items()}, latest)
        ttm_fcf = None if ocf is None or capex is None else ocf - capex
        ttm_div = yield_pct / 100.0 * metrics.market_cap if metrics and metrics.market_cap else None
        ttm_ratio, _, _ = assess_fcf(ttm_fcf, ttm_div)

        fy_fcf = fiscal_year_fcf(cashflow, latest_year)
        dividends: dict[int, float] = {}
        try:
            dividends = fetch_dividends(code)
        except Exception as exc:  # noqa: BLE001
            print(f"{name:<9} 分红明细获取失败: {exc}")
        fy_div = dividends.get(latest_year)
        fy_ratio = fy_fcf / fy_div if (fy_fcf is not None and fy_div) else None

        r3, n3, _ = multi_year_ratio(cashflow, dividends, years=3, min_years=3)
        r5, n5, detail = multi_year_ratio(cashflow, dividends, years=5, min_years=3)
        mean5 = _mean_annual(detail)

        def cell(r):
            return f"{r:8.2f}x" if r is not None else "    n/a"

        print(f"{name:<9}{yield_pct:>6.2f}%{cell(ttm_ratio)}{cell(fy_ratio)}"
              f"{cell(r3):>10}{cell(r5):>10}{cell(mean5):>9}{n5:>5}")
        rows.append((name, ttm_ratio, fy_ratio, r3, r5, n5, detail))

    print(f"\n【判定：> {args.threshold}x】")
    for name, ttm, fy, r3, r5, n5, _ in rows:
        print(f"  {name:<9} TTM {_tag(ttm, args.threshold):<10}"
              f" FY {_tag(fy, args.threshold):<10}"
              f" 近3年 {_tag(r3, args.threshold):<10} 近5年 {_tag(r5, args.threshold)}")

    if args.detail:
        print("\n【逐财年明细】")
        for name, _, _, _, r5, _, detail in rows:
            if not detail:
                continue
            print(f"  {name}（近 5 年合计 {r5:.2f}x）" if r5 is not None else f"  {name}")
            for year in sorted(detail, reverse=True):
                fcf, div = detail[year]
                if div > 0:
                    print(f"      {year}: FCF {fcf / 1e8:>8.2f}亿  分红 {div / 1e8:>7.2f}亿  "
                          f"{fcf / div:>6.2f}x")
                else:
                    print(f"      {year}: FCF {fcf / 1e8:>8.2f}亿  分红      —   （当年未分红）")

    print("\n⚠️ 口径说明：新浪「资本开支」仅含购建固定资产/无形资产等长期资产支出，")
    print("   不含理财、股权投资等投资性流出，因此比部分行情软件的 FCF 宽。")
    print("   多年合计口径中，某财年未分红计为 0（不摊薄分母），重资产扩张期会显得很重。")
    print("   银行不适用（现金流含同业往来），应改看核心一级资本充足率 / 不良率 / 拨备覆盖率。")
    print("   以上基于公开财报，不构成投资建议。")


if __name__ == "__main__":
    main()
