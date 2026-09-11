"""输出渲染：控制台建议表 + 企业微信提醒消息。"""

from __future__ import annotations

from .models import PortfolioResult, Decision
from .grid import _fmt
from .quality import emoji as _quality_emoji
from .coverage import emoji as _coverage_emoji
from .dividends import CALIBER_NAME


def _mark(d: Decision) -> str:
    return "*" if d.stock.overridden else ""


def _coverage_cell(s) -> str:
    """分红可持续性单元格：多年 FCF 覆盖倍数（或回退单窗口）/ 银行监管指标。"""
    e = _coverage_emoji(s.coverage_flag)
    if s.coverage_kind in ("bank", "insurance"):
        return f"{e}{'银行' if s.coverage_kind == 'bank' else '保险'} {s.coverage_note}"
    if s.fcf_avg_coverage is not None:
        return f"{e}{s.coverage_note or f'FCF/分红 {s.fcf_avg_coverage:.2f}x（{s.fcf_avg_years}年）'}"
    if s.fcf_coverage is not None:
        return f"{e}FCF/分红 {s.fcf_coverage:.2f}x（TTM）"
    return f"{e}{s.coverage_note or '未评估'}"


def _yield_cell(d: Decision) -> str:
    return f"{d.stock.dividend_yield:.2f}%{_mark(d)}" if d.stock.data_available and d.stock.dividend_available else "数据缺失"


def _caliber_line(result: PortfolioResult) -> str:
    """股息率口径说明：把通用规则与本批实际使用的财年显式打出来。

    口径本身是通用规则（见 dividends.CALIBER_RULE）：不需要逐股配置、也没有有效期，
    对所有标的一致适用。这里只做展示，避免把财年口径误读成滚动 12 个月，
    进而误以为「取消中期分红」会让股息率机械跳水。
    """
    usable = [d.stock for d in result.decisions
              if d.stock.data_available and d.stock.dividend_available and not d.stock.overridden]
    by_year: dict[int, list[str]] = {}
    for s in usable:
        if s.dividend_fiscal_year is None:
            continue
        by_year.setdefault(s.dividend_fiscal_year, []).append(s.name)
    if not by_year:
        return ""
    covered = sum(len(names) for names in by_year.values())
    suffix = f"（覆盖 {covered}/{len(usable)} 只）" if covered < len(usable) else ""
    dps = [s.dividend_dps for s in usable if s.dividend_dps is not None]
    if len(by_year) == 1:
        year = next(iter(by_year))
        span = f"，每股分红 {min(dps):.4f}~{max(dps):.4f} 元" if dps else ""
        return (f"股息率口径：{CALIBER_NAME}（最近一个「年度分红已实施」的完整财年，含该财年中期分红；"
                f"非滚动 12 个月）· 本批统一 FY{year}{span}{suffix}")
    parts = " | ".join(f"FY{y}：{'、'.join(names)}"
                       for y, names in sorted(by_year.items(), reverse=True))
    return f"股息率口径：{CALIBER_NAME}（含该财年中期分红；非滚动 12 个月）· 分批财年 {parts}{suffix}"


def _average_yield(result: PortfolioResult) -> str:
    if any(d.current_shares > 0 and (not d.stock.data_available or not d.stock.dividend_available) for d in result.decisions):
        return "数据不足"
    return f"{result.avg_yield:.2f}%"


def _quality_cell(d: Decision) -> str:
    """质量列：emoji + 派息率(如有)。"""
    s = d.stock
    e = _quality_emoji(s.quality_flag)
    if s.payout_ratio is not None:
        return f"{e}{s.payout_ratio:.2f}%"
    if s.quality_flag == "risk":
        return f"{e}风险"
    return f"{e}-"


def _price_ladder_str(ladder: tuple[tuple[float, float], ...]) -> str:
    """把 ((份数, 股价), ...) 渲染成 '22.69/20.66/19.28'。"""
    if not ladder:
        return "-"
    return "/".join(f"{p:.2f}" for _, p in ladder)


def _buy_price_cell(d: Decision) -> str:
    """买入价列：各买入档（6/9/12 份）对应的股价。"""
    return _price_ladder_str(d.buy_price_ladder)


def _price_legend(decisions: list[Decision]) -> str:
    """按阈值去重生成买入价档位图例（动态取自本次计算，避免与策略配置漂移）。"""
    by_th: dict[tuple[float, ...], list[str]] = {}
    for d in decisions:
        if not d.buy_price_ladder:
            continue
        groups = by_th.setdefault(d.buy_thresholds, [])
        if d.stock.group not in groups:
            groups.append(d.stock.group)
    parts = [
        f"{'/'.join(f'{t:g}' for t in th)}%（{'/'.join(sorted(gs))}）"
        for th, gs in sorted(by_th.items())
    ]
    return "；".join(parts)


def _msg_price_txt(d: Decision) -> str:
    """提醒正文里的价格提示：买入档价（有则附卖出档价）。"""
    parts: list[str] = []
    if d.buy_price_ladder:
        parts.append("买 " + _price_ladder_str(d.buy_price_ladder))
    if d.sell_price_ladder and d.action == "SELL":
        parts.append("卖 " + _price_ladder_str(d.sell_price_ladder))
    return (" · " + " / ".join(parts)) if parts else ""


def _industry_concentration(result: PortfolioResult) -> list[tuple[str, float, float]]:
    """按行业分组汇总 (分组, 当前净值%, 目标净值%)，按目标占比从高到低。"""
    agg: dict[str, list[float]] = {}
    for d in result.decisions:
        g = d.stock.group or "default"
        cur, tgt = agg.setdefault(g, [0.0, 0.0])
        agg[g][0] = cur + d.current_value_pct
        agg[g][1] = tgt + d.target_value_pct
    for group, percentage in result.external_industry_pct.items():
        values = agg.setdefault(group, [0.0, 0.0])
        values[0] += percentage
        values[1] += percentage
    rows = [(g, v[0], v[1]) for g, v in agg.items()]
    rows.sort(key=lambda r: r[2], reverse=True)
    return rows


def render_table(result: PortfolioResult) -> str:
    """渲染纯文本建议表（按股息率从高到低）。"""
    lines: list[str] = []
    header = (
        f"{'代码':<10}{'名称':<10}{'行业':<8}{'股息率':>8}{'质量':>9}  "
        f"{'买入价(6/9/12份)':<18} {'当前':>8} | {'目标':>8} | {'动作':<18}  {'档位'}"
    )
    lines.append(header)
    lines.append("-" * 132)
    for d in result.decisions:
        s = d.stock
        yield_str = _yield_cell(d)
        if d.action == "BUY":
            act = f"买入 {_fmt(d.delta_shares)} 份"
        elif d.action == "SELL":
            act = f"卖出 {_fmt(-d.delta_shares)} 份"
        elif d.action == "HOLD_BAND":
            act = "持有带·维持"
        else:
            act = "维持"
        lines.append(
            f"{s.code:<10}{s.name:<10}{s.group:<8}{yield_str:>8}{_quality_cell(d):>9}  "
            f"{_buy_price_cell(d):<18} {_fmt(d.current_shares):>8} | {_fmt(d.target_shares):>8} | "
            f"{act:<18}  {d.band_label}"
        )
    lines.append("-" * 132)
    lines.append(
        f"目标持仓合计 {result.target_position_pct:.2f}% | "
        f"目标现金 {result.target_cash_pct:.2f}% | "
        f"当前现金 {result.current_cash_pct:.2f}%"
    )
    lines.append(
        f"汇总买入 {result.total_buy_pct:.2f}% | 汇总卖出 {result.total_sell_pct:.2f}% | "
        f"个股平均股息率(仅展示) {_average_yield(result)}"
    )
    caliber = _caliber_line(result)
    if caliber:
        lines.append(caliber)
    # 行业集中度汇总
    conc = _industry_concentration(result)
    if conc:
        parts = [f"{g} {cur:.1f}%→{tgt:.1f}%" for g, cur, tgt in conc]
        lines.append("行业集中度(当前→目标): " + " | ".join(parts))
    # 分红质量提示
    alerts = [
        f"{_quality_emoji(d.stock.quality_flag)}{d.stock.name}({d.stock.quality_note})"
        for d in result.decisions
        if d.stock.quality_flag in ("risk", "warn")
    ]
    if alerts:
        lines.append("分红质量提示: " + " | ".join(alerts))
    # 分红可持续性（FCF 覆盖倍数 / 银行监管指标）
    assessed = [d for d in result.decisions if d.stock.coverage_kind in ("fcf", "bank", "insurance")]
    if assessed:
        lines.append("分红可持续性: " + " | ".join(
            f"{_coverage_emoji(d.stock.coverage_flag)}{d.stock.name} {_coverage_cell(d.stock).split(' ', 1)[-1]}"
            for d in assessed))
    skipped = [d for d in result.decisions if d.stock.coverage_kind == "unknown"]
    if skipped:
        lines.append("覆盖度未评估: " + " | ".join(f"{d.stock.name}({d.stock.coverage_note})" for d in skipped))
    # 目标买入价 / 卖出价说明
    ladder_rows = [d for d in result.decisions if d.buy_price_ladder]
    if ladder_rows:
        lines.append(
            "买入价 = 该股达到对应档位股息率所需股价（按每股分红不变由现价推算），"
            "依次对应加仓至 6/9/12 份"
        )
        legend = _price_legend(ladder_rows)
        if legend:
            lines.append(f"买入价档位阈值（分组）：{legend}")
    sell_rows = [d for d in result.decisions if d.sell_price_ladder]
    if sell_rows:
        lines.append("卖出价(保留9/6/2份): " + " | ".join(
            f"{d.stock.name} {_price_ladder_str(d.sell_price_ladder)}" for d in sell_rows))
    no_price = [d.stock.name for d in result.decisions
                if d.stock.data_available and not d.buy_price_ladder]
    if no_price:
        lines.append("买入价不可用(缺现价或有效分红): " + ", ".join(no_price))
    if any(d.stock.overridden for d in result.decisions):
        lines.append("注：标 * 的股息率由有效期内的每股分红覆盖值与最新价格计算。")
    lines.append(f"ETF及其他持仓 {result.external_position_pct:.2f}% | 512890 预留现金 {result.reserved_cash_pct:.2f}%")
    lines.extend(f"资金/数据提示：{warning}" for warning in result.warnings)
    lines.extend(f"账户说明：{note}" for note in result.notes)
    return "\n".join(lines)


def build_dividend_message(result: PortfolioResult) -> tuple[str, str]:
    """生成企业微信提醒的 (标题, markdown 正文)。

    仅列出触发买/卖信号的个股；无信号时给出“无触发”提示。
    """
    title = f"📊 红利网格提醒 | 个股股息率 {_average_yield(result)}"

    lines: list[str] = []

    # 触发信号摘要（买/卖），便于快速扫读
    triggered = result.triggered
    if triggered:
        lines.append("**触发信号：**")
        for d in triggered:
            s = d.stock
            if d.action == "BUY":
                act = f"<font color=\"warning\">买入 {_fmt(d.delta_shares)} 份</font>"
            else:
                act = f"<font color=\"info\">卖出 {_fmt(-d.delta_shares)} 份</font>"
            lines.append(
                f"> {s.name}({s.code}) {_yield_cell(d)} → {act}"
                f"（当前 {_fmt(d.current_shares)} 份，目标 {_fmt(d.target_shares)} 份）"
                f"{_msg_price_txt(d)}"
            )
        lines.append("")
    else:
        lines.append("**本次无可执行买卖信号**。")
        lines.append("")

    # 全部标的股息率（含未触发），按股息率高→低
    lines.append("**全部标的（股息率高→低）：**")
    for d in result.decisions:
        s = d.stock
        if d.action == "BUY":
            act = f"<font color=\"warning\">买入 {_fmt(d.delta_shares)} 份</font>"
        elif d.action == "SELL":
            act = f"<font color=\"info\">卖出 {_fmt(-d.delta_shares)} 份</font>"
        elif d.action == "HOLD_BAND":
            act = "持有带"
        else:
            act = "维持"
        q = _quality_emoji(s.quality_flag)
        lines.append(
            f"> {q}{s.name} {_yield_cell(d)} · {s.group} · "
            f"当前 {_fmt(d.current_shares)} 份→目标 {_fmt(d.target_shares)} 份 → {act}"
            f"{_msg_price_txt(d)}"
        )
        if "·" in d.band_label or not s.data_available or "基本面退出" in d.band_label:
            lines.append(f"> {d.band_label}")

    # 买入价图例（阈值随分组变化，动态取自本次计算）
    legend = _price_legend(result.decisions)
    if legend:
        lines.append("")
        lines.append(
            f"> 买入价＝该股涨跌到对应档位股息率所需的股价（每股分红不变推算），"
            f"依次对应加仓至 6/9/12 份；阈值 {legend}"
        )

    # 分红质量提示（🔴/🟡）
    alerts = [
        f"> {_quality_emoji(d.stock.quality_flag)}{d.stock.name}：{d.stock.quality_note}"
        for d in result.decisions
        if d.stock.quality_flag in ("risk", "warn")
    ]
    if alerts:
        lines.append("")
        lines.append("**分红质量提示：**")
        lines.extend(alerts)

    # 分红可持续性（FCF 覆盖倍数 / 银行监管指标）
    assessed = [d for d in result.decisions if d.stock.coverage_kind in ("fcf", "bank", "insurance")]
    if assessed:
        lines.append("")
        lines.append("**分红可持续性：**")
        lines.extend(
            f"> {_coverage_emoji(d.stock.coverage_flag)}{d.stock.name}："
            f"{_coverage_cell(d.stock).split(' ', 1)[-1]}"
            for d in assessed
        )

    lines.append("")
    lines.append(
        f"目标现金 **{result.target_cash_pct:.2f}%** | "
        f"目标持仓 {result.target_position_pct:.2f}%"
    )
    lines.append(
        f"汇总买入 {result.total_buy_pct:.2f}% | 汇总卖出 {result.total_sell_pct:.2f}%"
    )
    caliber = _caliber_line(result)
    if caliber:
        lines.append("")
        lines.append(f"> {caliber}")
    lines.append(f"ETF及其他持仓 {result.external_position_pct:.2f}% | 512890 预留现金 {result.reserved_cash_pct:.2f}%（计入现金）")
    lines.extend(f"> {warning}" for warning in result.warnings)
    lines.extend(f"> {note}" for note in result.notes)
    conc = _industry_concentration(result)
    if conc:
        parts = [f"{g} {tgt:.1f}%" for g, _cur, tgt in conc if tgt > 0]
        if parts:
            lines.append("行业目标分布: " + " / ".join(parts))
    return title, "\n".join(lines)
