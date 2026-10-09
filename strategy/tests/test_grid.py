"""网格算法单元测试：覆盖各档位与边界值。"""

from dividend_grid.models import Stock
from dividend_grid.grid import decide, evaluate_portfolio, GridConfig, GroupThresholds


def _stock(y: float, weight: float = 0.12) -> Stock:
    return Stock(code="t", name="测试", dividend_yield=y, weight=weight)


def _d(y: float, current: float, cfg: GridConfig | None = None):
    return decide(_stock(y), current, cfg or GridConfig())


# ---------- 买入区 ----------

def test_buy_tier1_from_zero():
    d = _d(5.2, 0)
    assert d.action == "BUY"
    assert d.target_shares == 6
    assert d.delta_shares == 6


def test_buy_tier2():
    d = _d(5.7, 0)
    assert d.action == "BUY"
    assert d.target_shares == 9


def test_buy_tier3():
    d = _d(6.5, 3)
    assert d.action == "BUY"
    assert d.target_shares == 12
    assert d.delta_shares == 9


def test_buy_overweight_holds_no_reduce():
    # 已 12 份，股息率仍在买入区但目标只有 6 -> 维持不降
    d = _d(5.2, 12)
    assert d.action == "HOLD"
    assert d.delta_shares == 0
    assert d.target_shares == 12


# ---------- 持有带 ----------

def test_hold_band_middle():
    d = _d(5.0, 6)
    assert d.action == "HOLD_BAND"
    assert d.delta_shares == 0
    assert d.target_shares == 6


# ---------- 卖出区 ----------

def test_sell_tier1():
    d = _d(4.7, 12)
    assert d.action == "SELL"
    assert d.target_shares == 9
    assert d.delta_shares == -3


def test_sell_tier2():
    d = _d(4.3, 12)
    assert d.action == "SELL"
    assert d.target_shares == 6
    assert d.delta_shares == -6


def test_sell_tier3_floor():
    d = _d(3.5, 12)
    assert d.action == "SELL"
    assert d.target_shares == 2  # 最低保留 2 份底仓
    assert d.delta_shares == -10


def test_sell_never_below_floor():
    # 已持仓 6 份，深跌应卖到底仓 2，而非卖 10
    d = _d(3.5, 6)
    assert d.action == "SELL"
    assert d.target_shares == 2
    assert d.delta_shares == -4


def test_sell_at_or_below_target_holds():
    # 已持仓 2 份（底仓），不再卖
    d = _d(3.5, 2)
    assert d.action == "HOLD"
    assert d.delta_shares == 0


# ---------- 边界值 ----------

def test_boundary_4_9_is_hold_band():
    # 恰为 4.9% -> 持有带不卖
    d = _d(4.9, 12)
    assert d.action == "HOLD_BAND"


def test_boundary_5_1_triggers_buy():
    # 恰为 5.1% -> 触发买入，目标 6
    d = _d(5.1, 0)
    assert d.action == "BUY"
    assert d.target_shares == 6


def test_boundary_4_2_sells_six_not_ten():
    # 恰为 4.2% -> 归入卖 6 档（目标 6），而非卖 10
    d = _d(4.2, 12)
    assert d.action == "SELL"
    assert d.target_shares == 6
    assert d.delta_shares == -6


def test_boundary_4_6_sells_three():
    # 恰为 4.6% -> 归入 4.6~4.9 档，卖 3（目标 9）
    d = _d(4.6, 12)
    assert d.action == "SELL"
    assert d.target_shares == 9


def test_boundary_5_6_target_nine():
    d = _d(5.6, 0)
    assert d.target_shares == 9


def test_boundary_6_0_target_twelve():
    d = _d(6.0, 0)
    assert d.target_shares == 12


# ---------- 组合与现金 ----------

def test_portfolio_cash_and_sort():
    stocks = [
        Stock(code="a", name="A", dividend_yield=4.79, weight=0.12),  # 卖出区
        Stock(code="b", name="B", dividend_yield=5.26, weight=0.12),  # 买入区目标6
        Stock(code="c", name="C", dividend_yield=6.10, weight=0.12),  # 满仓12
    ]
    holdings = {"a": 12, "b": 6, "c": 9}
    res = evaluate_portfolio(stocks, holdings, GridConfig())

    # 排序：按股息率从高到低 c(6.10) > b(5.26) > a(4.79)
    assert [d.stock.code for d in res.decisions] == ["c", "b", "a"]

    # 当前持仓净值 = (12+6+9)*1% = 27% -> 现金 73%
    assert abs(res.current_cash_pct - 73.0) < 1e-6

    # 目标：c 12(买3)、b 6(维持)、a 9(卖3) -> 目标持仓 27% 现金 73%
    assert abs(res.target_position_pct - 27.0) < 1e-6

    # 触发：c 买入、a 卖出
    codes = {d.stock.code for d in res.triggered}
    assert codes == {"c", "a"}


def test_share_value_scales_with_pool():
    cfg = GridConfig(default_weight=0.24)
    d = decide(_stock(6.5, weight=0.24), 0, cfg)
    # 单份净值 = 0.24/12*100 = 2%
    assert abs(d.share_value_pct - 2.0) < 1e-6
    assert abs(d.target_value_pct - 24.0) < 1e-6  # 12 份 * 2%


# ---------- 监控清单加载(定时任务用) ----------

def test_load_watchlist_dict_format(tmp_path):
    from dividend_grid.cli import _load_watchlist
    import json

    p = tmp_path / "watchlist.json"
    p.write_text(json.dumps({"sh600690": "海尔智家", "sh601398": "工商银行"}), encoding="utf-8")
    items = _load_watchlist(p)
    assert [(c, n) for c, n, _, _, _ in items] == [
        ("sh600690", "海尔智家"),
        ("sh601398", "工商银行"),
    ]
    # 股息率留空(由数据源获取)，未指定分组默认 default
    assert all(dy is None for _, _, dy, _, _ in items)
    assert all(g == "default" for *_, g in items)


# ---------- 分红质量过滤 ----------

def test_quality_assess_flags():
    from dividend_grid.quality import assess, QualityConfig
    from dividend_grid.datasource import QualityMetrics

    cfg = QualityConfig()  # warn80 risk100 flag_negative_eps veto=False
    # 健康
    f, _ = assess(QualityMetrics(eps=2.0, dividend_per_share=0.9, payout_ratio=45.0), cfg)
    assert f == "ok"
    # 警示
    f, _ = assess(QualityMetrics(eps=1.0, dividend_per_share=0.85, payout_ratio=85.0), cfg)
    assert f == "warn"
    # 高风险(派息率>100)
    f, _ = assess(QualityMetrics(eps=1.0, dividend_per_share=1.2, payout_ratio=120.0), cfg)
    assert f == "risk"
    # 亏损分红
    f, _ = assess(QualityMetrics(eps=-0.5, dividend_per_share=0.3, payout_ratio=None), cfg)
    assert f == "risk"
    # 数据不足
    f, _ = assess(None, cfg)
    assert f == "unknown"


def test_quality_veto_blocks_buy_but_not_sell():
    from dividend_grid.quality import QualityConfig
    cfg = GridConfig(veto_buy_on_risk=True)

    # 高风险 + 触发买入 -> 否决为维持
    s_buy = Stock(code="a", name="风险买", dividend_yield=6.5, weight=0.12,
                  quality_flag="risk")
    d = decide(s_buy, 0, cfg)
    assert d.action == "HOLD"
    assert d.delta_shares == 0
    assert "质量否决" in d.band_label

    # 高风险 + 触发卖出 -> 卖出不受影响
    s_sell = Stock(code="b", name="风险卖", dividend_yield=3.5, weight=0.12,
                   quality_flag="risk")
    d2 = decide(s_sell, 12, cfg)
    assert d2.action == "SELL"
    assert d2.target_shares == 2


def test_quality_soft_mode_does_not_block():
    # 默认软告警(veto=False)：高风险仍按网格买入
    cfg = GridConfig(veto_buy_on_risk=False)
    s = Stock(code="a", name="风险买", dividend_yield=6.5, weight=0.12,
              quality_flag="risk")
    d = decide(s, 0, cfg)
    assert d.action == "BUY"


# ---------- 企业微信消息:全部标的股息率 ----------

def test_message_includes_non_triggered_yields():
    from dividend_grid.report import build_dividend_message

    stocks = [
        Stock(code="a", name="买入股", dividend_yield=6.10, weight=0.12),   # 触发买入
        Stock(code="b", name="维持股", dividend_yield=5.00, weight=0.12),   # 持有带,未触发
    ]
    holdings = {"a": 0, "b": 6}
    res = evaluate_portfolio(stocks, holdings, GridConfig())
    title, content = build_dividend_message(res)

    # 未触发的"维持股"及其股息率也应出现在正文中
    assert "维持股" in content
    assert "5.00%" in content
    assert "当前 6 份→目标 6 份" in content
    # 全部标的区块存在
    assert "全部标的" in content
    # 触发的买入股仍在
    assert "买入股" in content


def test_message_marks_quote_yield_above_fiscal_caliber():
    from dividend_grid.report import build_dividend_message, quote_yield_warnings

    stock = Stock(code="sz000538", name="云南白药", dividend_yield=3.07, quote_yield=5.09)
    close = Stock(code="sz000333", name="美的集团", dividend_yield=5.17, quote_yield=5.06)
    res = evaluate_portfolio([stock, close], {}, GridConfig())
    _, content = build_dividend_message(res)
    warnings = quote_yield_warnings([stock, close])

    assert "云南白药 3.07%（行情 5.09%）" in content
    assert "美的集团 5.17%（行情" not in content
    assert len(warnings) == 1
    assert "高出 2.02 个百分点" in warnings[0]
    assert "买卖仍按财年口径" in warnings[0]
    assert "东方财富分红明细还没收录的现金分红" in warnings[0]
    assert "特别分红" not in warnings[0]


# ---------- 行业分组阈值 ----------

def _fin_cfg() -> GridConfig:
    return GridConfig(
        groups={
            "default": GroupThresholds(buy=(5.1, 5.6, 6.0), sell=(4.9, 4.6, 4.2)),
            "金融": GroupThresholds(buy=(5.5, 6.0, 6.5), sell=(5.3, 5.0, 4.6)),
        }
    )


def _grouped_stock(y: float, group: str) -> Stock:
    return Stock(code="t", name="测试", dividend_yield=y, weight=0.12, group=group)


def test_group_financial_buy_threshold_raised():
    cfg = _fin_cfg()
    # 5.2% 对默认是买入区，但对金融(门槛5.5%)属于卖出区
    d_default = decide(_grouped_stock(5.2, "消费"), 12, cfg)  # 消费未配置→默认
    assert d_default.action == "HOLD"  # 默认买入区目标6，当前12超配→维持
    d_fin = decide(_grouped_stock(5.2, "金融"), 12, cfg)
    assert d_fin.action == "SELL"      # 金融：5.2<5.3 卖出档1，卖到9
    assert d_fin.target_shares == 9


def test_group_financial_buy_tiers():
    cfg = _fin_cfg()
    assert decide(_grouped_stock(5.6, "金融"), 0, cfg).target_shares == 6   # >=5.5
    assert decide(_grouped_stock(6.0, "金融"), 0, cfg).target_shares == 9   # >=6.0
    assert decide(_grouped_stock(6.5, "金融"), 0, cfg).target_shares == 12  # >=6.5


def test_group_financial_hold_band():
    cfg = _fin_cfg()
    # 金融持有带 [5.3, 5.5)
    d = decide(_grouped_stock(5.4, "金融"), 6, cfg)
    assert d.action == "HOLD_BAND"
    assert d.delta_shares == 0


def test_unconfigured_group_falls_back_to_default():
    cfg = _fin_cfg()
    # 消费未配置 → 用 default：5.0% 落默认持有带 [4.9,5.1)
    d = decide(_grouped_stock(5.0, "消费"), 6, cfg)
    assert d.action == "HOLD_BAND"


def test_load_watchlist_object_format_with_group(tmp_path):
    from dividend_grid.cli import _load_watchlist
    import json

    p = tmp_path / "watchlist.json"
    p.write_text(
        json.dumps({"sh600036": {"name": "招商银行", "group": "金融"}}),
        encoding="utf-8",
    )
    items = _load_watchlist(p)
    assert items[0][0] == "sh600036"
    assert items[0][1] == "招商银行"
    assert items[0][4] == "金融"  # group


def test_load_watchlist_list_format(tmp_path):
    from dividend_grid.cli import _load_watchlist
    import json

    p = tmp_path / "watchlist.json"
    p.write_text(
        json.dumps([{"code": "sh600690", "name": "海尔智家", "weight": 0.24}]),
        encoding="utf-8",
    )
    items = _load_watchlist(p)
    assert items[0][0] == "sh600690"
    assert items[0][1] == "海尔智家"
    assert items[0][3] == 0.24


# ---------- 腾讯行情解析 ----------

def test_parse_tencent_quote_yield_and_quality():
    from dividend_grid.datasource import parse_tencent_quote

    # 构造 65+ 个字段，64 号为股息率 5.85，39 为市盈率 4.98，46 为市净率 0.47
    parts = [""] * 70
    parts[1] = "兴业银行"
    parts[2] = "601166"
    parts[3] = "18.23"
    parts[30] = "20260824111533"
    parts[39] = "4.98"
    parts[46] = "0.47"
    parts[64] = "5.85"
    raw = 'v_sh601166="' + "~".join(parts) + '";'
    kv = parse_tencent_quote(raw)
    assert kv["名称"] == "兴业银行"
    assert kv["股息率(TTM)"] == 5.85
    assert kv["市盈率(TTM)"] == 4.98
    assert kv["市净率"] == 0.47
    # DPS = 18.23 * 5.85 / 100
    assert abs(kv["股息(TTM)"] - 18.23 * 5.85 / 100) < 1e-9
    # EPS = 18.23 / 4.98
    assert abs(kv["每股收益"] - 18.23 / 4.98) < 1e-9


def test_to_quote_symbol():
    from dividend_grid.datasource import to_quote_symbol, to_xq_symbol

    assert to_quote_symbol("sh601166") == "sh601166"
    assert to_quote_symbol("601166") == "sh601166"
    assert to_quote_symbol("000001") == "sz000001"
    assert to_xq_symbol("sz000001") == "SZ000001"


# ---------- 方案A：自身分位阈值 ----------

def _p(pct: float, y: float = 4.0, current: float = 0.0):
    stock = Stock(code="t", name="测试", dividend_yield=y, weight=0.12,
                  yield_percentile=pct)
    return decide(stock, current, GridConfig(dynamic_percentile=True))


def test_percentile_buy_tiers_use_own_distribution():
    # 绝对股息率很低（4.0%，绝对值模式下位于卖出区），但相对自身历史处于高分位
    assert _p(95).target_shares == 12 and _p(95).action == "BUY"
    assert _p(85).target_shares == 9
    assert _p(70).target_shares == 6
    assert _p(50).action == "HOLD_BAND"


def test_percentile_sell_tiers_and_floor():
    assert _p(30, current=11).target_shares == 9
    assert _p(15, current=11).target_shares == 6
    assert _p(5, current=11).target_shares == 2
    assert _p(5, current=1).action == "HOLD"  # 已低于目标，维持不加仓


def test_percentile_missing_falls_back_to_absolute_thresholds():
    stock = Stock(code="t", name="测试", dividend_yield=5.2, weight=0.12,
                  yield_percentile=None)
    d = decide(stock, 0.0, GridConfig(dynamic_percentile=True))
    assert d.action == "BUY" and d.target_shares == 6


# ---------- 目标买入价（提醒里打印） ----------

def test_price_ladder_math():
    from dividend_grid.grid import price_ladder

    # 现价 21.0、股息率 5.51% -> DPS = 1.1571；各档价格 = DPS / 档位
    ladder = price_ladder(21.0, 5.51, (5.1, 5.6, 6.0), (6.0, 9.0, 12.0))
    shares = [c for c, _ in ladder]
    prices = [p for _, p in ladder]
    assert shares == [6.0, 9.0, 12.0]
    assert abs(prices[0] - 21.0 * 5.51 / 5.1) < 1e-9
    assert abs(prices[1] - 21.0 * 5.51 / 5.6) < 1e-9
    assert abs(prices[2] - 21.0 * 5.51 / 6.0) < 1e-9
    # 档位越高（越便宜）-> 价格越低
    assert prices[0] > prices[1] > prices[2]


def test_price_ladder_missing_inputs():
    from dividend_grid.grid import price_ladder

    assert price_ladder(None, 5.5, (5.1, 5.6, 6.0), (6.0, 9.0, 12.0)) == ()
    assert price_ladder(21.0, 0.0, (5.1, 5.6, 6.0), (6.0, 9.0, 12.0)) == ()
    assert price_ladder(0.0, 5.5, (5.1, 5.6, 6.0), (6.0, 9.0, 12.0)) == ()


def test_decide_fills_default_price_ladder():
    stock = Stock(code="t", name="测试", dividend_yield=5.51, weight=0.12, price=21.0)
    d = decide(stock, 6.0, GridConfig())
    assert [c for c, _ in d.buy_price_ladder] == [6.0, 9.0, 12.0]
    assert d.buy_thresholds == (5.1, 5.6, 6.0)
    # 卖出价对应保留 9/6/2 份
    assert [c for c, _ in d.sell_price_ladder] == [9.0, 6.0, 2.0]
    assert d.sell_thresholds == (4.9, 4.6, 4.2)


def test_decide_price_ladder_uses_group_thresholds():
    cfg = _fin_cfg()
    stock = Stock(code="t", name="测试", dividend_yield=6.01, weight=0.12,
                  group="金融", price=20.0)
    d = decide(stock, 9.0, cfg)
    assert d.buy_thresholds == (5.5, 6.0, 6.5)
    # 金融第一档 5.5% -> 20*6.01/5.5
    assert abs(d.buy_price_ladder[0][1] - 20.0 * 6.01 / 5.5) < 1e-9


def test_decide_no_price_yields_empty_ladder():
    d = decide(_stock(5.5), 0.0, GridConfig())
    assert d.buy_price_ladder == ()
    assert d.sell_price_ladder == ()


def test_decide_dynamic_mode_has_no_price_ladder():
    stock = Stock(code="t", name="测试", dividend_yield=4.0, weight=0.12,
                  yield_percentile=95.0, price=20.0)
    d = decide(stock, 0.0, GridConfig(dynamic_percentile=True))
    assert d.buy_price_ladder == ()


def test_table_and_message_render_buy_price():
    from dividend_grid.report import render_table, build_dividend_message

    stocks = [
        Stock(code="a", name="买入股", dividend_yield=6.10, weight=0.12, price=20.0),
    ]
    res = evaluate_portfolio(stocks, {"a": 0}, GridConfig())
    table = render_table(res)
    assert "买入价(6/9/12份)" in table
    # 20*6.10/5.1 = 23.92, 20*6.10/5.6 = 21.79, 20*6.10/6.0 = 20.33
    assert "23.92/21.79/20.33" in table
    assert "买入价档位阈值" in table

    _title, content = build_dividend_message(res)
    assert "买 23.92/21.79/20.33" in content
    assert "买入价" in content
