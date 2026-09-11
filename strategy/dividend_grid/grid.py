"""核心网格算法（纯个股版）。

策略要点（严格按边界约定：买入用 >=，卖出用 <，等于档位视为持有）：

买入区（越便宜越买，累计目标）：
    股息率 >= 6.0%  -> 目标 12 份
    股息率 >= 5.6%  -> 目标 9 份
    股息率 >= 5.1%  -> 目标 6 份
    买入区内若当前 > 目标，仅维持（不主动降仓）。

持有带（4.9% ~ 5.1%）：不买不卖，维持现仓。

卖出区（越贵越卖，跌破档位逐档减仓，最低保留底仓）：
    股息率 <  4.9%  -> 累计卖 3 份（目标保留 9）
    股息率 <  4.6%  -> 累计卖 6 份（目标保留 6）
    股息率 <  4.2%  -> 累计卖 10 份（目标保留 2 底仓）
    卖出区内若当前 <= 目标，则维持（不加仓）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
import math

from .models import Stock, Decision, PortfolioResult

# 份数结构固定：买入目标 6/9/12，卖出后保留 9/6/2（底仓 2）。
# 各分组只调「股息率档位阈值」，不调份数结构。
BUY_TARGETS = (6.0, 9.0, 12.0)   # 对应 buy 阈值 [b1, b2, b3]
SELL_CUMULATIVE = (3.0, 6.0, 10.0)  # 对应 sell 阈值 [s1, s2, s3] 的累计卖出份数

# 默认分组阈值（与原纯个股版一致）
DEFAULT_BUY = (5.1, 5.6, 6.0)
DEFAULT_SELL = (4.9, 4.6, 4.2)

# 动态阈值（方案A）：用该股自身滚动分位替代绝对股息率，自动适应中枢漂移。
# 分位越高＝相对自身历史越便宜。份数结构与绝对值模式完全一致（6/9/12，保留 9/6/2）。
PERCENTILE_BUY = (65.0, 80.0, 90.0)   # ≥65/80/90 分位 -> 目标 6/9/12 份
PERCENTILE_SELL = (35.0, 20.0, 10.0)  # ≤35/20/10 分位 -> 累计卖 3/6/10 份


@dataclass
class GroupThresholds:
    """单个行业分组的股息率档位阈值。

    buy = (b1, b2, b3)，b1<b2<b3；股息率 ≥bN 时目标 6/9/12 份。
    sell = (s1, s2, s3)，s1>s2>s3；股息率 <sN 时逐档减仓（保留 9/6/2）。
    持有带 = [s1, b1)。
    """

    buy: tuple[float, float, float] = DEFAULT_BUY
    sell: tuple[float, float, float] = DEFAULT_SELL


def _default_groups() -> dict[str, GroupThresholds]:
    return {"default": GroupThresholds()}


@dataclass
class GridConfig:
    """网格参数。"""

    full_shares: float = 12.0  # 满仓份数
    floor_shares: float = 2.0  # 普通网格底仓；已确认的基本面退出可清仓
    default_weight: float = 0.12  # 单只满仓净值占总资产比例（--pool）
    groups: dict[str, GroupThresholds] = field(default_factory=_default_groups)
    dynamic_percentile: bool = False  # 方案A：改用该股自身滚动分位作为阈值
    veto_buy_on_risk: bool = False  # 分红质量高风险时否决买入（卖出不受影响）
    veto_buy_on_unknown: bool = False
    veto_buy_on_coverage_risk: bool = False
    veto_buy_on_coverage_unknown: bool = False
    exit_reasons: dict[str, str] = field(default_factory=dict)
    min_cash_pct: float = 0.0
    max_industry_pct: float = 100.0
    industry_limits: dict[str, float] = field(default_factory=dict)
    external_position_pct: float = 0.0
    external_industry_pct: dict[str, float] = field(default_factory=dict)
    reserved_cash_pct: float = 0.0

    def __post_init__(self) -> None:
        for value in (self.min_cash_pct, self.max_industry_pct, *self.industry_limits.values(),
                      self.external_position_pct, self.reserved_cash_pct):
            if not math.isfinite(value) or not 0 <= value <= 100:
                raise ValueError("仓位限制必须是 0 到 100 的有限百分数")
        if any(not math.isfinite(v) or v < 0 for v in self.external_industry_pct.values()) or sum(self.external_industry_pct.values()) > self.external_position_pct + 1e-6:
            raise ValueError("ETF 行业占用不能超过 ETF 实际仓位")
        if self.min_cash_pct + self.external_position_pct + self.reserved_cash_pct > 100:
            raise ValueError("现金保留与外部策略额度合计不能超过 100%")

    def thresholds_for(self, group: str) -> GroupThresholds:
        """按分组取阈值；未配置的分组回退到 default。"""
        if group in self.groups:
            return self.groups[group]
        return self.groups.get("default", GroupThresholds())


def _share_value_pct(weight: float, cfg: GridConfig) -> float:
    """单份净值占总资产的百分比。默认 weight=0.12, full=12 -> 1%/份。"""
    if cfg.full_shares <= 0:
        return 0.0
    return weight / cfg.full_shares * 100.0


def price_ladder(
    price: float | None,
    current_yield: float,
    thresholds: tuple[float, float, float],
    counts: tuple[float, float, float],
) -> tuple[tuple[float, float], ...]:
    """推算各档股息率对应的股价：((份数, 股价), ...)。

    假设每股分红(DPS)不变，则 ``DPS = 现价 × 当前股息率``，因此达到目标股息率
    ``t`` 所需的股价为 ``现价 × 当前股息率 ÷ t``。用于提醒里直接给出「到哪一档
    挂什么价」。现价或股息率缺失时返回空元组。
    """
    if price is None or not math.isfinite(price) or price <= 0:
        return ()
    if not math.isfinite(current_yield) or current_yield <= 0:
        return ()
    out: list[tuple[float, float]] = []
    for count, t in zip(counts, thresholds):
        if not math.isfinite(t) or t <= 0:
            continue
        out.append((count, price * current_yield / t))
    return tuple(out)


def decide(stock: Stock, current_shares: float, cfg: GridConfig | None = None) -> Decision:
    """对单只个股给出网格决策（按其行业分组阈值判断）。"""
    cfg = cfg or GridConfig()
    th = cfg.thresholds_for(stock.group)
    y = stock.dividend_yield
    full = cfg.full_shares
    floor = cfg.floor_shares
    svp = _share_value_pct(stock.weight, cfg)

    if not math.isfinite(current_shares) or current_shares < 0:
        raise ValueError(f"{stock.code} 持仓份数必须是非负有限数")
    if not math.isfinite(stock.weight) or not 0 < stock.weight <= 1:
        raise ValueError(f"{stock.code} 满仓权重必须大于 0 且不超过 1")
    if stock.code in cfg.exit_reasons:
        return Decision(stock, current_shares, 0.0, "SELL" if current_shares else "HOLD",
                        -current_shares, f"基本面退出：{cfg.exit_reasons[stock.code]}", svp)
    if not stock.data_available:
        return Decision(stock, current_shares, current_shares, "HOLD", 0.0,
                        "行情缺失，维持并保留资金占用", svp)
    if not stock.dividend_available:
        return Decision(stock, current_shares, current_shares, "HOLD", 0.0,
                        "分红数据待复核：" + stock.dividend_note, svp)
    if not math.isfinite(y) or y < 0:
        raise ValueError(f"{stock.code} 股息率必须是非负有限数")

    # 方案A：若开启动态分位且该股分位可用，则用自身滚动分位替代绝对股息率。
    # 次新股分位数据不足（yield_percentile 为 None）时自动回退到绝对阈值。
    use_dynamic = (
        cfg.dynamic_percentile
        and stock.yield_percentile is not None
        and math.isfinite(stock.yield_percentile)
    )
    if use_dynamic:
        b1, b2, b3 = PERCENTILE_BUY
        s1, s2, s3 = PERCENTILE_SELL
        value = float(stock.yield_percentile)
        prefix = f"自身{value:.0f}%分位·"
        unit = "分位"
    else:
        b1, b2, b3 = th.buy
        s1, s2, s3 = th.sell
        value = y
        prefix = ""
        unit = "%"

    # 各档买卖触发价（仅绝对值阈值模式有意义；分位模式阈值不是股息率，跳过）
    buy_ladder: tuple[tuple[float, float], ...] = ()
    sell_ladder: tuple[tuple[float, float], ...] = ()
    buy_thresholds: tuple[float, ...] = ()
    sell_thresholds: tuple[float, ...] = ()
    if not use_dynamic:
        buy_thresholds = (b1, b2, b3)
        sell_thresholds = (s1, s2, s3)
        buy_ladder = price_ladder(stock.price, y, buy_thresholds, BUY_TARGETS)
        keep_counts = tuple(max(floor, full - c) for c in SELL_CUMULATIVE)
        sell_ladder = price_ladder(stock.price, y, sell_thresholds, keep_counts)

    if value >= b1:
        # 买入区
        if value >= b3:
            target = BUY_TARGETS[2]
        elif value >= b2:
            target = BUY_TARGETS[1]
        else:
            target = BUY_TARGETS[0]
        if current_shares < target:
            action, new = "BUY", target
        else:
            # 超配：维持，不主动降仓
            action, new = "HOLD", current_shares
        band = f"{prefix}买入区(目标{_fmt(target)}份)"
    elif value >= s1:
        # 持有带 [s1, b1)
        action, new, target = "HOLD_BAND", current_shares, current_shares
        band = f"{prefix}持有带({_fmt(s1)}~{_fmt(b1)}{unit})"
    else:
        # 卖出区 value < s1，跌破档位逐档减仓
        if value < s3:
            cum_sell = SELL_CUMULATIVE[2]
        elif value < s2:
            cum_sell = SELL_CUMULATIVE[1]
        else:
            cum_sell = SELL_CUMULATIVE[0]
        keep = max(floor, full - cum_sell)
        target = keep
        if current_shares > keep:
            action, new = "SELL", keep
        else:
            # 已低于/等于目标，维持（不加仓）
            action, new = "HOLD", current_shares
        band = f"{prefix}卖出区(目标{_fmt(keep)}份/底仓{_fmt(floor)})"

    # 分红质量否决：高风险标的即使触发买入也不买（卖出不受影响）
    if action == "BUY" and cfg.veto_buy_on_risk and stock.quality_flag == "risk":
        action, new = "HOLD", current_shares
        band += "·质量否决买入"
    if action == "BUY" and cfg.veto_buy_on_unknown and stock.quality_flag == "unknown":
        action, new = "HOLD", current_shares
        band += "·质量数据不足，暂停买入"
    if action == "BUY" and (
        (cfg.veto_buy_on_coverage_risk and stock.coverage_flag == "risk") or
        (cfg.veto_buy_on_coverage_unknown and stock.coverage_flag == "unknown")
    ):
        action, new = "HOLD", current_shares
        band += "·可持续性待复核，暂停买入：" + stock.coverage_note

    delta = round(new - current_shares, 6)
    return Decision(
        stock=stock,
        current_shares=current_shares,
        target_shares=new,
        action=action,
        delta_shares=delta,
        band_label=band,
        share_value_pct=svp,
        buy_price_ladder=buy_ladder,
        sell_price_ladder=sell_ladder,
        buy_thresholds=buy_thresholds,
        sell_thresholds=sell_thresholds,
    )


def evaluate_portfolio(
    stocks: list[Stock],
    holdings: dict[str, float],
    cfg: GridConfig | None = None,
) -> PortfolioResult:
    """评估组合：逐股独立判断，并推导现金仓位。

    holdings: {股票代码: 当前份数}，缺省视为 0 份。
    """
    cfg = cfg or GridConfig()
    codes = [s.code for s in stocks]
    if len(codes) != len(set(codes)):
        raise ValueError("监控清单存在重复股票代码")
    for code, shares in holdings.items():
        if not math.isfinite(float(shares)) or float(shares) < 0:
            raise ValueError(f"{code} 持仓份数必须是非负有限数")
        if shares > 0 and code not in codes:
            raise ValueError(f"持仓 {code} 未纳入监控清单，无法准确计算可用资金")
    decisions: list[Decision] = []
    for s in stocks:
        cur = float(holdings.get(s.code, 0.0))
        decisions.append(decide(s, cur, cfg))

    # 按股息率从高到低排序
    decisions.sort(key=lambda d: d.stock.dividend_yield, reverse=True)

    current_stock_pos = sum(d.current_value_pct for d in decisions)
    current_pos = current_stock_pos + cfg.external_position_pct
    if current_pos > 100.0 + 1e-8:
        raise ValueError("当前持仓合计超过 100%，请先校准持仓份数和外部持仓")
    warnings: list[str] = []
    missing = [d.stock.code for d in decisions if not d.stock.data_available]
    if missing:
        warnings.append("行情不完整，暂停全部新增买入：" + ", ".join(missing))

    # 先计入卖出后的占用，再按行业和剩余现金等比例缩减买入。
    base_by_group: dict[str, float] = {}
    buys_by_group: dict[str, float] = {}
    for d in decisions:
        group = d.stock.group
        base_by_group[group] = base_by_group.get(group, 0.0) + min(
            d.current_value_pct, d.target_value_pct)
        buys_by_group[group] = buys_by_group.get(group, 0.0) + max(d.delta_value_pct, 0.0)
    group_factors: dict[str, float] = {}
    for group, base in base_by_group.items():
        base += cfg.external_industry_pct.get(group, 0.0)
        cap = cfg.industry_limits.get(group, cfg.max_industry_pct)
        demand = buys_by_group[group]
        group_factors[group] = min(1.0, max(cap - base, 0.0) / demand) if demand else 1.0
        if base > cap:
            warnings.append(f"{group} 已超行业上限 {cap:g}%，暂停该行业加仓")
    demand = sum(buys_by_group[g] * factor for g, factor in group_factors.items())
    base_pos = sum(base_by_group.values()) + cfg.external_position_pct
    available = max(100.0 - cfg.min_cash_pct - cfg.reserved_cash_pct - base_pos, 0.0)
    total_factor = min(1.0, available / demand) if demand else 1.0
    if missing:
        total_factor = 0.0
    if base_pos > 100.0 - cfg.min_cash_pct - cfg.reserved_cash_pct:
        warnings.append("现金低于保留及 ETF 预留额度，暂停新增买入")
    for d in decisions:
        if d.action != "BUY":
            continue
        factor = group_factors[d.stock.group] * total_factor
        if factor < 1.0:
            delta = math.floor(d.delta_shares * factor * 1_000_000) / 1_000_000
            d.delta_shares = delta
            d.target_shares = d.current_shares + delta
            d.action = "BUY" if delta > 0 else "HOLD"
            d.band_label += "·受资金/行业/数据约束"
    target_pos = sum(d.target_value_pct for d in decisions) + cfg.external_position_pct
    if any(d.action == "BUY" for d in decisions) and any(d.action == "SELL" for d in decisions):
        warnings.append("目标按先卖后买计算；卖出成交后再执行买入，并更新持仓份数")

    # 组合加权平均股息率（按当前净值权重，仅展示）
    if current_stock_pos > 0:
        avg_yield = sum(
            d.stock.dividend_yield * d.current_value_pct for d in decisions
        ) / current_stock_pos
    else:
        avg_yield = 0.0

    return PortfolioResult(
        decisions=decisions,
        avg_yield=round(avg_yield, 4),
        current_cash_pct=round(100.0 - current_pos, 4),
        target_cash_pct=round(100.0 - target_pos, 4),
        current_position_pct=round(current_pos, 4),
        target_position_pct=round(target_pos, 4),
        warnings=warnings,
        external_position_pct=cfg.external_position_pct,
        reserved_cash_pct=cfg.reserved_cash_pct,
        external_industry_pct=cfg.external_industry_pct,
    )


def _fmt(x: float) -> str:
    """去掉多余的小数 0，如 6.0 -> 6，6.4 -> 6.4。"""
    if abs(x - round(x)) < 1e-9:
        return str(int(round(x)))
    return f"{x:g}"
