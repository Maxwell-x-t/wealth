from datetime import date, timedelta

import pytest

from dividend_grid.rsi6 import (
    AllInState,
    DEFAULT_RSI_PARAMS,
    Rsi6State,
    RsiParams,
    WeeklyBar,
    build_market_message,
    build_market_snapshot,
    decide,
    decide_all_in,
    rsi_wilder,
    week_id,
    weekly_rsi,
)
from dividend_grid.rsi6_data import aggregate_daily_bars, parse_weekly_bars


def _bars(closes: list[float], opening: float | None = None) -> list[WeeklyBar]:
    result = []
    for index, close in enumerate(closes):
        value = close if index < len(closes) - 1 or opening is None else opening
        result.append(
            WeeklyBar(
                date=date(2026, 1, 5) + timedelta(days=index * 7),
                open=value,
                close=close,
            )
        )
    return result


def test_wilder_rsi_known_values():
    # 6-period Wilder example: six initial changes, then one smoothed change.
    closes = [100, 102, 101, 103, 102, 104, 103, 105]
    expected = 73.6842105263
    assert rsi_wilder(closes) == pytest.approx(expected)


def test_weekly_rsi_uses_current_open_for_opening_rsi():
    bars = _bars([100, 102, 101, 103, 102, 104, 103, 105], opening=100)
    current, opening = weekly_rsi(bars)
    assert current > opening


def test_buy_thresholds_are_strict_and_cumulative():
    state = Rsi6State()
    decision, state = decide(30.99, 30.99, 0, state, "2026-09-07")
    assert (decision.action, decision.target_layers, decision.delta_layers) == ("BUY", 4, 4)

    decision, _ = decide(25.0, 25.0, 0, state, "2026-09-07")
    assert (decision.action, decision.target_layers) == ("BUY", 4)
    decision, _ = decide(31.0, 31.0, 0, state, "2026-09-07")
    assert decision.action == "HOLD"
    decision, _ = decide(24.99, 24.99, 4, state, "2026-09-07")
    assert (decision.action, decision.target_layers, decision.delta_layers) == ("BUY", 7, 3)
    decision, _ = decide(19.99, 19.99, 7, state, "2026-09-07")
    assert (decision.action, decision.target_layers, decision.delta_layers) == ("BUY", 10, 3)


def test_buy_does_not_reduce_over_target():
    decision, _ = decide(30.0, 30.0, 8, Rsi6State(), "2026-09-07")
    assert decision.action == "HOLD"
    assert decision.delta_layers == 0
    assert decision.target_layers == 8


def test_sell_base_prevents_repeated_daily_sale():
    decision, state = decide(75.0, 70.0, 8, Rsi6State(), "2026-09-07")
    assert (decision.action, decision.pending_sell_layers) == ("SELL", 2)
    assert state.sell_base == 8

    decision, state = decide(76.0, 71.0, 8, state, "2026-09-07")
    assert (decision.action, decision.pending_sell_layers) == ("SELL", 2)
    assert state.sell_base == 8

    # Partial and complete fills must not create a new sale.
    decision, _ = decide(71.0, 71.0, 7, state, "2026-09-07")
    assert decision.pending_sell_layers == 1
    decision, _ = decide(76.0, 71.0, 6, state, "2026-09-07")
    assert decision.pending_sell_layers == 0


def test_sell_uses_strict_thresholds_and_can_upgrade_to_full_exit():
    decision, state = decide(74.0, 80.0, 8, Rsi6State(), "2026-09-07")
    assert decision.action == "HOLD"
    decision, state = decide(80.0, 73.0, 8, state, "2026-09-07")
    assert decision.pending_sell_layers == 2
    assert state.frozen is True
    decision, _ = decide(19.0, 60.0, 6, state, "2026-09-07")
    assert decision.action == "FROZEN"
    decision, state = decide(80.01, 60.0, 6, state, "2026-09-07")
    assert decision.target_layers == 0
    assert decision.pending_sell_layers == 6


def test_sell_floor_is_zero_and_week_resets():
    decision, state = decide(80.0, 80.0, 1, Rsi6State(), "2026-09-07")
    assert decision.target_layers == 0
    assert decision.pending_sell_layers == 1
    decision, state = decide(80.0, 80.0, 0, state, "2026-09-14")
    assert state.sell_base == 0
    assert decision.pending_sell_layers == 0


def test_week_id_and_data_parser():
    assert week_id(date(2026, 9, 10)) == "2026-09-07"
    bars = parse_weekly_bars([{"日期": "2026-09-07", "开盘": 1, "收盘": 2}])
    assert bars[0].open == 1
    assert bars[0].close == 2
    aggregated = aggregate_daily_bars(
        [
            {"日期": "2026-09-07", "开盘": 1, "收盘": 2},
            {"日期": "2026-09-08", "开盘": 2, "收盘": 3},
            {"日期": "2026-09-14", "开盘": 4, "收盘": 5},
        ]
    )
    assert [(bar.date.isoformat(), bar.open, bar.close) for bar in aggregated] == [
        ("2026-09-07", 1, 3),
        ("2026-09-14", 4, 5),
    ]


def test_market_snapshot_projects_four_price_triggers():
    bars = _bars([100, 102, 101, 103, 102, 104, 103, 105])
    snapshot = build_market_snapshot(bars, label="上周")
    assert [trigger.direction for trigger in snapshot.triggers] == ["涨", "涨", "跌", "跌", "跌"]
    assert snapshot.triggers[0].price > snapshot.close
    assert snapshot.triggers[1].price > snapshot.close
    assert snapshot.triggers[2].price < snapshot.close
    assert snapshot.triggers[3].price < snapshot.close
    _title, message = build_market_message(snapshot, morning=True)
    assert "上周收盘:105.000" in message
    assert "RSI>80" in message
    assert "RSI<25" in message
    assert "RSI<31" in message
    _title, midday_message = build_market_message(snapshot, midday=True)
    assert "盘中提醒" in midday_message
    assert "当天行情已结束" not in midday_message
    for trigger in snapshot.triggers:
        projected = rsi_wilder([bar.close for bar in bars] + [trigger.price])
        assert projected == pytest.approx(trigger.target_rsi)


# ---------- 梭哈版（31 以下满仓，70/74 各减 10%） ----------

W = "2026-01-05"


def _allin(rsi: float, state: AllInState, week: str = W, current: float = 90.0):
    return decide_all_in(rsi, current, state, week)


def test_all_in_buys_below_31_only():
    d, _ = _allin(30.99, AllInState(week=W))
    assert d.action == "BUY"
    d, _ = _allin(31.0, AllInState(week=W))
    assert d.action == "HOLD"


def test_all_in_sells_ten_percent_at_seventy_then_seventy_four():
    # 新周首个交易日先落在中间带，避免触发"首日 >74"特例
    d, state = _allin(50.0, AllInState())
    assert d.action == "HOLD"
    d, state = _allin(70.0, state)
    assert d.action == "SELL" and d.sell_fraction == pytest.approx(0.10)
    d, state = _allin(71.0, state)
    assert d.action == "HOLD"  # 同一级不重复卖
    d, state = _allin(74.0, state)
    assert d.action == "SELL" and d.sell_fraction == pytest.approx(0.10)
    d, state = _allin(80.0, state)
    assert d.action == "HOLD"  # 周内 20% 已用完


def test_all_in_week_cap_is_twenty_percent():
    _, state = _allin(50.0, AllInState())
    _, state = _allin(70.0, state)
    _, state = _allin(75.0, state)
    assert state.sold_steps == 2
    d, _ = _allin(88.0, state)
    assert d.action == "HOLD" and d.sell_fraction == 0.0


def test_all_in_first_day_above_74_sells_twenty_and_freezes():
    d, state = _allin(74.01, AllInState())
    assert d.action == "SELL" and d.sell_fraction == pytest.approx(0.20)
    assert state.frozen is True
    d, _ = _allin(20.0, state)
    assert d.action == "FROZEN"  # 冻结优先级高于梭哈


def test_all_in_exactly_74_on_first_day_counts_as_the_special_case():
    # 中文"74 以上"含 74 本身：首日即触发"直接卖 20% 并冻结"
    d, state = _allin(74.0, AllInState())
    assert d.action == "SELL" and d.sell_fraction == pytest.approx(0.20)
    assert state.frozen is True


def test_all_in_week_resets():
    _, state = _allin(50.0, AllInState())
    _, state = _allin(70.0, state)
    assert state.sold_steps == 1
    d, state = _allin(70.0, state, week="2026-01-12")
    assert d.action == "SELL" and state.sold_steps == 1


def test_all_in_simultaneous_breakout_sells_both_steps():
    _, state = _allin(50.0, AllInState())
    d, state = _allin(75.0, state)
    assert d.action == "SELL" and d.sell_fraction == pytest.approx(0.20)
    assert state.sold_steps == 2


# ---------- RsiParams：默认值等价生产配置、平移与校验 ----------


def test_default_params_match_production_thresholds():
    assert DEFAULT_RSI_PARAMS.period == 6
    assert DEFAULT_RSI_PARAMS.buy_levels == (20.0, 25.0, 31.0)
    assert DEFAULT_RSI_PARAMS.sell_two_rsi == 74.0
    assert DEFAULT_RSI_PARAMS.sell_all_rsi == 80.0
    assert DEFAULT_RSI_PARAMS.allin_buy_rsi == 31.0
    assert DEFAULT_RSI_PARAMS.allin_step_rsi == (70.0, 74.0)


def test_shift_moves_every_threshold_and_keeps_layers():
    shifted = RsiParams().shifted(2.0)
    assert shifted.buy_levels == (22.0, 27.0, 33.0)
    assert shifted.sell_two_rsi == 76.0
    assert shifted.sell_all_rsi == 82.0
    assert shifted.allin_buy_rsi == 33.0
    assert shifted.allin_step_rsi == (72.0, 76.0)
    assert shifted.buy_layers == DEFAULT_RSI_PARAMS.buy_layers
    assert shifted.period == DEFAULT_RSI_PARAMS.period


def test_shifted_buy_threshold_takes_effect():
    params = RsiParams().shifted(2.0)  # 最浅买入档由 31 抬到 33，触发更容易
    decision, _ = decide(33.0, 33.0, 0, Rsi6State(), "2026-09-07", params=params)
    assert decision.action == "HOLD"
    decision, _ = decide(32.0, 32.0, 0, Rsi6State(), "2026-09-07", params=params)
    assert (decision.action, decision.target_layers) == ("BUY", 4)


def test_shifted_sell_threshold_takes_effect():
    params = RsiParams().shifted(-2.0)  # 卖 2 层档由 >74 降到 >72
    decision, _ = decide(73.0, 73.0, 8, Rsi6State(), "2026-09-07", params=params)
    assert decision.action == "SELL"
    assert decision.pending_sell_layers == 2


def test_shifted_all_in_thresholds_take_effect():
    params = RsiParams().shifted(2.0)
    # 买点由 31 抬到 33：33 不再满仓，32 才触发
    d, _ = decide_all_in(33.0, 90.0, AllInState(week=W), W, params=params)
    assert d.action == "HOLD"
    d, _ = decide_all_in(32.0, 90.0, AllInState(week=W), W, params=params)
    assert d.action == "BUY"
    # 首日高卖档由 74 抬到 76：75 不再触发「直接卖 20%」特例，只走 72 档的 10%
    d, _ = decide_all_in(75.0, 90.0, AllInState(), W, params=params)
    assert d.action == "SELL" and d.sell_fraction == pytest.approx(0.10)
    d, _ = decide_all_in(76.0, 90.0, AllInState(), W, params=params)
    assert d.action == "SELL" and d.sell_fraction == pytest.approx(0.20)


@pytest.mark.parametrize("kwargs", [
    {"period": 0},
    {"buy_levels": (20.0, 25.0), "buy_layers": (10, 7, 4)},
    {"buy_levels": (25.0, 20.0, 31.0)},
    {"sell_two_rsi": 80.0, "sell_all_rsi": 74.0},
    {"allin_step_rsi": (70.0,)},
])
def test_params_reject_invalid_configurations(kwargs):
    with pytest.raises(ValueError):
        RsiParams(**kwargs)
