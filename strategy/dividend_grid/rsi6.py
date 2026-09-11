"""512890 周线 RSI(6) 层仓策略。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import math
from datetime import date, timedelta
from typing import Optional, Sequence


CODE = "sh512890"
NAME = "华泰柏瑞中证红利低波ETF"
FULL_LAYERS = 10

# 梭哈版（百分比）参数：RSI < 31 满仓；70/74 两级各减当前持仓的 10%，周内累计上限 20%。
BUY_ALL_RSI = 31.0
SELL_STEP_RSI = (70.0, 74.0)
SELL_STEP_FRACTION = 0.10
SELL_WEEK_CAP_FRACTION = 0.20


@dataclass(frozen=True)
class RsiParams:
    """RSI 择时参数集。默认值等于当前生产配置（周期 6、买卖阈值 20/25/31 与 74/80）。

    分层版（``decide``）用 ``buy_levels`` / ``sell_two_rsi`` / ``sell_all_rsi``；
    梭哈版（``decide_all_in``）用 ``allin_buy_rsi`` / ``allin_step_rsi``。
    ``period`` 只用于生成 History 指标，不参与单次决策，放在这里是为了让一次参数扫描
    的周期与阈值能一起传递。
    """

    period: int = 6
    # 分层版买入：RSI 严格小于 buy_levels[i] 时买入至 buy_layers[i] 层（由深到浅升序）。
    buy_levels: tuple[float, ...] = (20.0, 25.0, 31.0)
    buy_layers: tuple[int, ...] = (10, 7, 4)
    # 分层版卖出：RSI > sell_two_rsi 卖 2 层；RSI > sell_all_rsi 清仓。
    sell_two_rsi: float = 74.0
    sell_all_rsi: float = 80.0
    # 梭哈版：RSI < allin_buy_rsi 满仓；allin_step_rsi 两档各卖当前持仓 10%。
    allin_buy_rsi: float = 31.0
    allin_step_rsi: tuple[float, ...] = (70.0, 74.0)

    def __post_init__(self) -> None:
        if self.period <= 0:
            raise ValueError("RSI 周期必须为正数")
        if len(self.buy_levels) != len(self.buy_layers):
            raise ValueError("buy_levels 与 buy_layers 长度必须一致")
        if tuple(sorted(self.buy_levels)) != tuple(self.buy_levels):
            raise ValueError("buy_levels 必须按由深到浅升序排列")
        if self.sell_all_rsi <= self.sell_two_rsi:
            raise ValueError("清仓阈值必须高于卖 2 层阈值")
        if len(self.allin_step_rsi) != 2:
            raise ValueError("梭哈版卖出档位必须恰好两级")

    def shifted(self, delta: float) -> "RsiParams":
        """所有 RSI 阈值整体平移 delta 点，周期与层数保持不变。"""
        return replace(
            self,
            buy_levels=tuple(value + delta for value in self.buy_levels),
            sell_two_rsi=self.sell_two_rsi + delta,
            sell_all_rsi=self.sell_all_rsi + delta,
            allin_buy_rsi=self.allin_buy_rsi + delta,
            allin_step_rsi=tuple(value + delta for value in self.allin_step_rsi),
        )


DEFAULT_RSI_PARAMS = RsiParams()


@dataclass(frozen=True)
class WeeklyBar:
    date: date
    open: float
    close: float
    raw_close: float | None = None


@dataclass
class Rsi6State:
    """本周卖出状态；买入不需要跨日记账。"""

    week: str = ""
    sell_base: Optional[int] = None
    planned_sell_layers: int = 0
    frozen: bool = False

    @classmethod
    def from_dict(cls, raw: dict) -> "Rsi6State":
        base = raw.get("sell_base")
        return cls(
            week=str(raw.get("week", "")),
            sell_base=None if base is None else int(base),
            planned_sell_layers=int(raw.get("planned_sell_layers", 0)),
            frozen=bool(raw.get("frozen", False)),
        )

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class AllInState:
    """梭哈版本周状态。卖出按当前持仓的比例记账，不涉及层数。"""

    week: str = ""
    sold_steps: int = 0  # 本周已执行的卖出级数（0/1/2）
    frozen: bool = False  # 本周首个交易日即 >74 时置位，本周不再操作

    @classmethod
    def from_dict(cls, raw: dict) -> "AllInState":
        return cls(
            week=str(raw.get("week", "")),
            sold_steps=int(raw.get("sold_steps", 0)),
            frozen=bool(raw.get("frozen", False)),
        )

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class Rsi6Decision:
    code: str
    name: str
    week: str
    rsi: float
    open_rsi: float
    current_layers: int
    target_layers: int
    delta_layers: int
    action: str
    sell_base: Optional[int]
    planned_sell_layers: int
    pending_sell_layers: int
    frozen: bool
    note: str = ""

    @property
    def triggered(self) -> bool:
        return self.action in {"BUY", "SELL"}


@dataclass(frozen=True)
class PriceTrigger:
    price: float
    target_rsi: float
    direction: str
    action: str
    active: bool


@dataclass(frozen=True)
class MarketSnapshot:
    label: str
    close: float
    rsi: float
    triggers: tuple[PriceTrigger, ...]
    notes: tuple[str, ...] = ()


def week_id(value: date) -> str:
    """返回 ISO 周的周一日期，作为状态文件中的稳定周标识。"""

    monday = value - timedelta(days=value.weekday())
    return monday.isoformat()


def rsi_wilder(closes: Sequence[float], period: int = 6) -> float:
    """按通达信/Wilder 平滑算法计算序列最后一个 RSI。"""

    if period <= 0:
        raise ValueError("RSI 周期必须为正数")
    if len(closes) < period + 1:
        raise ValueError(f"RSI({period}) 至少需要 {period + 1} 根收盘价")
    if any(not math.isfinite(value) or value <= 0 for value in closes):
        raise ValueError("RSI 收盘价必须是正的有限数")

    deltas = [float(closes[i]) - float(closes[i - 1]) for i in range(1, len(closes))]
    gains = [max(delta, 0.0) for delta in deltas]
    losses = [max(-delta, 0.0) for delta in deltas]
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for gain, loss in zip(gains[period:], losses[period:]):
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period

    if avg_gain == 0 and avg_loss == 0:
        return 50.0
    if avg_loss == 0:
        return 100.0
    if avg_gain == 0:
        return 0.0
    relative_strength = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + relative_strength)


def wilder_averages(closes: Sequence[float], period: int = 6) -> tuple[float, float]:
    """返回最后一根 K 线后的 Wilder 平均上涨/下跌幅度。"""

    if len(closes) < period + 1:
        raise ValueError(f"RSI({period}) 至少需要 {period + 1} 根收盘价")
    deltas = [float(closes[i]) - float(closes[i - 1]) for i in range(1, len(closes))]
    avg_gain = sum(max(delta, 0.0) for delta in deltas[:period]) / period
    avg_loss = sum(max(-delta, 0.0) for delta in deltas[:period]) / period
    for delta in deltas[period:]:
        gain = max(delta, 0.0)
        loss = max(-delta, 0.0)
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
    return avg_gain, avg_loss


def price_for_rsi(closes: Sequence[float], target_rsi: float, period: int = 6) -> float:
    """按下一根 K 线的收盘价反推达到目标 RSI 所需的价格。"""

    if not 0 < target_rsi < 100:
        raise ValueError("目标 RSI 必须在 0 到 100 之间")
    last_close = float(closes[-1])
    avg_gain, avg_loss = wilder_averages(closes, period)
    target_rs = target_rsi / (100.0 - target_rsi)

    current_rsi = rsi_wilder(closes, period)
    if target_rsi > current_rsi:
        if avg_loss == 0:
            return last_close
        change = (period - 1) * (target_rs * avg_loss - avg_gain)
        return last_close + max(change, 0.0)
    if target_rsi < current_rsi:
        if target_rs == 0:
            return 0.0
        change = (period - 1) * (avg_gain / target_rs - avg_loss)
        return max(last_close - max(change, 0.0), 0.0)
    return last_close


def build_market_snapshot(
    bars: Sequence[WeeklyBar],
    *,
    label: str,
    trigger_bars: Sequence[WeeklyBar] | None = None,
    period: int = 6,
    current_layers: int | None = None,
    max_buy_layers: int = 10,
    layer_pct: float = 2.0,
    sell_target_74: int | None = None,
    current_value_pct: float | None = None,
) -> MarketSnapshot:
    rsi = rsi_wilder([bar.close for bar in bars], period)
    trigger_closes = [bar.close for bar in (trigger_bars or bars)]
    specs = (
        (80.0, "涨", "全仓卖出", rsi > 80.0),
        (74.0, "涨", "卖出2层（ETF满仓额度20%）", rsi > 74.0),
        (31.0, "跌", "买入至4层", rsi < 31.0),
        (25.0, "跌", "买入至7层", rsi < 25.0),
        (20.0, "跌", "买入至10层", rsi < 20.0),
    )
    triggers = []
    for target, direction, action, active in specs:
        if current_layers is not None:
            if target in (31.0, 25.0, 20.0):
                desired = {31.0: 4, 25.0: 7, 20.0: 10}[target]
                allowed = max(current_layers, min(desired, max_buy_layers))
                if current_value_pct is not None and allowed * layer_pct <= current_value_pct:
                    allowed = current_layers
                action = (f"买入至{allowed}层（总资产{allowed * layer_pct:g}%）"
                          if allowed > current_layers else "维持当前层数，不新增买入")
            elif target == 74.0:
                sell_target = max(current_layers - 2, 0) if sell_target_74 is None else sell_target_74
                reduction = min(2, max(current_layers - sell_target, 0))
                sell_pct = (current_value_pct * reduction / current_layers
                            if current_value_pct is not None and current_layers else reduction * layer_pct)
                action = (f"卖出至{sell_target}层（本周最多减2层，按当前估值约占总资产{sell_pct:g}%）"
                          if current_layers > sell_target else "本周该档卖出已完成，维持")
                if current_layers == 0:
                    action = "当前空仓，无需卖出"
            else:
                action = "全仓卖出" if current_layers else "当前空仓，无需卖出"
        triggers.append(PriceTrigger(
            price=price_for_rsi(trigger_closes, target, period),
            target_rsi=target,
            direction=direction,
            action=action,
            active=active,
        ))
    notes = ["周内触发口径；RSI 使用前复权连续价格，触发价已对齐最新交易价格。",
             "同侧多档同时触发时，只执行最深档目标，不叠加。"]
    if current_layers is not None:
        notes.append(f"ETF 当前{current_layers}层；资金允许买入至{max_buy_layers}层；同一档按目标层数执行。")
    if current_value_pct is not None:
        notes.append(f"ETF 实际市值占比{current_value_pct:.2f}%；买入目标按总资产比例，卖出按实际份额比例。")
    return MarketSnapshot(
        label=label,
        close=float(bars[-1].raw_close if bars[-1].raw_close is not None else bars[-1].close),
        rsi=rsi,
        triggers=tuple(triggers),
        notes=tuple(notes),
    )


def build_market_message(
    snapshot: MarketSnapshot,
    *,
    morning: bool = False,
    midday: bool = False,
) -> tuple[str, str]:
    """生成本地双时点使用的红利低波 ETF 提醒。"""

    if morning:
        title = "红利低波ETF今日行情即将开始"
        intro = f"红利低波ETF今日行情即将开始，{snapshot.label}数据"
        stats = f"{snapshot.label}收盘:{snapshot.close:.3f}\n{snapshot.label}RSI(6)值:{snapshot.rsi:.2f}"
    elif midday:
        title = "红利低波ETF盘中提醒"
        intro = (
            f"红利低波ETF盘中提醒，今日最新价:{snapshot.close:.3f},"
            f"今日RSI(6)值:{snapshot.rsi:.2f}"
        )
        stats = ""
    else:
        title = "红利低波ETF当天行情已结束"
        intro = (
            f"红利低波ETF当天行情已结束收盘价:{snapshot.close:.3f},"
            f"今日RSI(6)值:{snapshot.rsi:.2f}"
        )
        stats = ""

    lines = [intro]
    if stats:
        lines.append(stats)
    for trigger in snapshot.triggers:
        status = "当前已触发" if trigger.active else "当前未触发"
        comparison = ">" if trigger.direction == "涨" else "<"
        lines.append(
            f"价格{trigger.direction}至{trigger.price:.3f}触发{trigger.action}"
            f"(RSI{comparison}{trigger.target_rsi:g}，{status})"
        )
    lines.extend(snapshot.notes)
    return title, "\n".join(lines)


def weekly_rsi(bars: Sequence[WeeklyBar], period: int = 6) -> tuple[float, float]:
    """返回 (当前周收盘 RSI, 当前周开盘代入 RSI)。"""

    if not bars:
        raise ValueError("周线数据为空")
    closes = [bar.close for bar in bars]
    current = rsi_wilder(closes, period)
    open_closes = [*closes[:-1], bars[-1].open]
    opening = rsi_wilder(open_closes, period)
    return current, opening


def _buy_target(rsi: float, params: RsiParams = DEFAULT_RSI_PARAMS) -> Optional[int]:
    for level, layers in zip(params.buy_levels, params.buy_layers):
        if rsi < level:
            return layers
    return None


def decide(
    rsi: float,
    open_rsi: float,
    current_layers: int,
    state: Rsi6State,
    week: str,
    *,
    code: str = CODE,
    name: str = NAME,
    params: RsiParams = DEFAULT_RSI_PARAMS,
) -> tuple[Rsi6Decision, Rsi6State]:
    """根据 RSI 和已持仓层数决策，并返回更新后的本周状态。

    `params` 默认等于生产配置；传 ``RsiParams(...).shifted(delta)`` 可整体平移阈值。
    """

    if not 0 <= current_layers <= FULL_LAYERS:
        raise ValueError(f"当前层数必须在 0 到 {FULL_LAYERS} 层之间")

    if state.week != week:
        state = Rsi6State(week=week)
    else:
        state = Rsi6State(**state.__dict__)

    # 首次进入卖区记录当时的持仓；之后只提高本周累计计划，不重复开档。
    in_sell_zone = rsi > params.sell_two_rsi
    if in_sell_zone and state.sell_base is None:
        state.sell_base = current_layers

    requested_sell = 0
    if rsi > params.sell_all_rsi:
        requested_sell = state.sell_base or 0
    elif rsi > params.sell_two_rsi:
        requested_sell = min(2, state.sell_base or 0)
    state.planned_sell_layers = max(state.planned_sell_layers, requested_sell)
    state.frozen = state.frozen or state.planned_sell_layers > 0

    pending_sell = 0
    if state.sell_base is not None:
        executed = max(state.sell_base - current_layers, 0)
        pending_sell = min(max(state.planned_sell_layers - executed, 0), current_layers)

    if pending_sell:
        decision = Rsi6Decision(
            code=code,
            name=name,
            week=week,
            rsi=rsi,
            open_rsi=open_rsi,
            current_layers=current_layers,
            target_layers=max(current_layers - pending_sell, 0),
            delta_layers=-pending_sell,
            action="SELL",
            sell_base=state.sell_base,
            planned_sell_layers=state.planned_sell_layers,
            pending_sell_layers=pending_sell,
            frozen=state.frozen,
            note="本周待执行卖出",
        )
        return decision, state

    if state.frozen:
        decision = Rsi6Decision(
            code=code,
            name=name,
            week=week,
            rsi=rsi,
            open_rsi=open_rsi,
            current_layers=current_layers,
            target_layers=current_layers,
            delta_layers=0,
            action="FROZEN",
            sell_base=state.sell_base,
            planned_sell_layers=state.planned_sell_layers,
            pending_sell_layers=0,
            frozen=True,
            note="本周卖出已完成，暂停买入；超过80仍可升级清仓",
        )
        return decision, state

    target = _buy_target(rsi, params)
    if target is None:
        action = "HOLD"
        target = current_layers
        note = "中间带，不买不卖"
    elif current_layers < target:
        action = "BUY"
        note = "买入目标为累计层数"
    else:
        action = "HOLD"
        target = current_layers
        note = "当前层数高于买入目标，维持"

    decision = Rsi6Decision(
        code=code,
        name=name,
        week=week,
        rsi=rsi,
        open_rsi=open_rsi,
        current_layers=current_layers,
        target_layers=target,
        delta_layers=target - current_layers,
        action=action,
        sell_base=state.sell_base,
        planned_sell_layers=state.planned_sell_layers,
        pending_sell_layers=0,
        frozen=False,
        note=note,
    )
    return decision, state


@dataclass(frozen=True)
class AllInDecision:
    """31 以下梭哈版本的决策结果（按比例，不涉及层数）。"""

    week: str
    rsi: float
    current_pct: float
    action: str  # BUY / SELL / FROZEN / HOLD
    sell_fraction: float  # 本次卖出占当前持仓的比例
    sold_steps: int
    frozen: bool
    note: str = ""

    @property
    def triggered(self) -> bool:
        return self.action in {"BUY", "SELL"}


def decide_all_in(
    rsi: float,
    current_pct: float,
    state: AllInState,
    week: str,
    *,
    step_fraction: float = SELL_STEP_FRACTION,
    params: RsiParams = DEFAULT_RSI_PARAMS,
) -> tuple[AllInDecision, AllInState]:
    """31 以下梭哈版本的每日决策。

    规则（全部为严格不等式）：
    - RSI < allin_buy_rsi（默认 31）：买入至满仓（梭哈）。
    - RSI ≥ allin_step_rsi[0]（默认 70）：卖出当前持仓的 10%；
      继续升到 ≥ allin_step_rsi[1]（默认 74）再卖 10%（周内累计上限 20%）。
    - 本周第一个交易日 RSI 即 ≥ allin_step_rsi[1]（"74 以上"含 74）：一次性卖出 20%，本周不再操作。
    - 周内正常卖出后不冻结，RSI 回落到买入阈值以下仍会梭哈。

    `params` 默认等于生产配置，可用 ``RsiParams(...).shifted(delta)`` 整体平移阈值。
    """

    if not 0 <= current_pct <= 100 + 1e-9:
        raise ValueError("当前持仓比例必须在 0 到 100 之间")

    if state.week != week:
        # 新的一周：首个交易日即 ≥ 高卖档走"直接卖 20%"特例，否则重置累计步数。
        if rsi >= params.allin_step_rsi[1]:
            state = AllInState(week=week, sold_steps=2, frozen=True)
            return AllInDecision(
                week=week, rsi=rsi, current_pct=current_pct, action="SELL",
                sell_fraction=SELL_WEEK_CAP_FRACTION, sold_steps=2, frozen=True,
                note="本周首日 RSI 已达高卖档以上，直接卖出 20%，本周不再操作",
            ), state
        state = AllInState(week=week)
    elif state.frozen:
        return AllInDecision(
            week=week, rsi=rsi, current_pct=current_pct, action="FROZEN",
            sell_fraction=0.0, sold_steps=state.sold_steps, frozen=True,
            note="本周已按首日高卖档清掉 20%，不再操作",
        ), state

    if rsi < params.allin_buy_rsi:
        return AllInDecision(
            week=week, rsi=rsi, current_pct=current_pct, action="BUY",
            sell_fraction=0.0, sold_steps=state.sold_steps, frozen=False,
            note=f"RSI<{params.allin_buy_rsi:g}，买入至满仓",
        ), state

    target_steps = (2 if rsi >= params.allin_step_rsi[1]
                    else (1 if rsi >= params.allin_step_rsi[0] else 0))
    pending = max(target_steps - state.sold_steps, 0)
    if pending:
        state.sold_steps = target_steps
        return AllInDecision(
            week=week, rsi=rsi, current_pct=current_pct, action="SELL",
            sell_fraction=pending * step_fraction, sold_steps=target_steps,
            frozen=state.frozen,
            note=f"RSI≥{params.allin_step_rsi[target_steps - 1]:g}，卖出当前持仓的 {pending * step_fraction:.0%}",
        ), state

    return AllInDecision(
        week=week, rsi=rsi, current_pct=current_pct, action="HOLD",
        sell_fraction=0.0, sold_steps=state.sold_steps, frozen=state.frozen,
        note="中间带，不动",
    ), state


def render_table(decision: Rsi6Decision) -> str:
    action = {
        "BUY": f"买入 {decision.delta_layers} 层",
        "SELL": f"卖出 {decision.pending_sell_layers} 层",
        "FROZEN": "本周冻结",
        "HOLD": "维持",
    }.get(decision.action, decision.action)
    lines = [
        f"{decision.code} {decision.name}",
        f"周 RSI6 {decision.rsi:.2f} | 开盘代入 RSI6 {decision.open_rsi:.2f}",
        f"当前 {decision.current_layers} 层 | 目标 {decision.target_layers} 层 | 动作 {action}",
        decision.note,
    ]
    if decision.sell_base is not None:
        lines.append(
            f"sell_base {decision.sell_base} 层 | 本周累计计划 {decision.planned_sell_layers} 层"
        )
    return "\n".join(lines)


def build_message(decision: Rsi6Decision) -> tuple[str, str]:
    title = f"📈 {decision.code} 周 RSI6 层仓提醒"
    action = {
        "BUY": f"<font color=\"warning\">买入 {decision.delta_layers} 层，目标 {decision.target_layers} 层</font>",
        "SELL": f"<font color=\"info\">卖出 {decision.pending_sell_layers} 层（待执行）</font>",
        "FROZEN": "本周卖出已完成，暂停买入",
        "HOLD": "维持",
    }.get(decision.action, decision.action)
    lines = [
        f"**{decision.name}（{decision.code}）**",
        f"> 周 RSI6：{decision.rsi:.2f} · 开盘代入：{decision.open_rsi:.2f}",
        f"> 当前：{decision.current_layers} 层 · 动作：{action}",
        f"> {decision.note}",
    ]
    if decision.sell_base is not None:
        lines.append(
            f"> 本周 sell_base：{decision.sell_base} 层 · 累计计划：{decision.planned_sell_layers} 层"
        )
    return title, "\n".join(lines)
