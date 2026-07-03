from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models.models import Instrument, PriceSnapshot, Transaction


D = Decimal
TWO = D("0.01")
SIX = D("0.000001")


CATEGORY_LABELS = {
    "nasdaq": "纳指",
    "sp500": "标普",
    "a_share": "A股",
    "gold": "黄金",
    "cash": "现金",
    "qdii": "QDII",
    "other": "其他",
}


@dataclass
class InstrumentState:
    instrument_id: int
    quantity: D
    total_cost: D
    realized_pnl: D

    @property
    def avg_cost(self) -> D:
        if self.quantity <= 0:
            return D("0")
        return (self.total_cost / self.quantity).quantize(SIX)


def _to_decimal(value: float | Decimal | str) -> D:
    return D(str(value))


def compute_instrument_states(db: Session) -> dict[int, InstrumentState]:
    transactions = (
        db.query(Transaction)
        .order_by(Transaction.trade_date.asc(), Transaction.id.asc())
        .all()
    )
    states: dict[int, InstrumentState] = {}

    for tx in transactions:
        qty = _to_decimal(tx.quantity)
        price = _to_decimal(tx.price)
        fee = _to_decimal(tx.fee)
        amount = _to_decimal(tx.amount)

        state = states.get(
            tx.instrument_id,
            InstrumentState(tx.instrument_id, D("0"), D("0"), D("0")),
        )

        if tx.side == "buy":
            buy_amount = amount if amount > 0 else qty * price
            state.total_cost += buy_amount + fee
            state.quantity += qty
        else:
            if state.quantity <= 0:
                raise ValueError(f"品种 {tx.instrument_id} 卖出数量超过持仓")
            if qty > state.quantity:
                raise ValueError(f"品种 {tx.instrument_id} 卖出数量超过持仓")

            avg_cost = state.avg_cost
            sell_cost = (qty * avg_cost).quantize(TWO)
            sell_amount = amount if amount > 0 else qty * price
            state.realized_pnl += sell_amount - sell_cost - fee
            state.total_cost -= sell_cost
            state.quantity -= qty

            if state.quantity == 0:
                state.total_cost = D("0")

        states[tx.instrument_id] = state

    return states


def get_latest_prices(db: Session) -> dict[int, D]:
    instruments = db.query(Instrument).all()
    prices: dict[int, D] = {}

    for instrument in instruments:
        latest = (
            db.query(PriceSnapshot)
            .filter(PriceSnapshot.instrument_id == instrument.id)
            .order_by(PriceSnapshot.snapshot_date.desc(), PriceSnapshot.id.desc())
            .first()
        )
        if latest:
            prices[instrument.id] = _to_decimal(latest.price)

    return prices


def convert_transaction_to_cny(amount: D, currency: str, exchange_rate: D) -> D:
    """成交金额折人民币：使用交易当日汇率。"""
    if currency == "CNY":
        return amount
    return (amount * exchange_rate).quantize(TWO)


def convert_market_to_cny(amount: D, currency: str, usd_cny_rate: D) -> D:
    """市值折人民币：使用当前配置汇率（后续可接行情）。"""
    if currency == "CNY":
        return amount
    return (amount * usd_cny_rate).quantize(TWO)


# 兼容旧调用：默认按市值汇率
def convert_to_cny(amount: D, currency: str, exchange_rate: D, usd_cny_rate: D) -> D:
    if currency == "CNY":
        return amount
    if exchange_rate != D("1"):
        return convert_transaction_to_cny(amount, currency, exchange_rate)
    return convert_market_to_cny(amount, currency, usd_cny_rate)


def build_holdings(
    db: Session,
    usd_cny_rate: float = 7.2,
) -> list[dict]:
    instruments = (
        db.query(Instrument)
        .filter(Instrument.is_active.is_(True))
        .all()
    )
    states = compute_instrument_states(db)
    latest_prices = get_latest_prices(db)
    usd_rate = _to_decimal(usd_cny_rate)

    holdings: list[dict] = []
    for instrument in instruments:
        state = states.get(
            instrument.id,
            InstrumentState(instrument.id, D("0"), D("0"), D("0")),
        )
        if state.quantity <= 0 and state.realized_pnl == 0:
            continue

        current_price = latest_prices.get(instrument.id, D("0"))
        market_value = (state.quantity * current_price).quantize(TWO)
        unrealized = (market_value - state.total_cost).quantize(TWO)
        unrealized_rate = (
            float((unrealized / state.total_cost * 100).quantize(TWO))
            if state.total_cost > 0
            else 0.0
        )
        market_value_cny = convert_market_to_cny(market_value, instrument.currency, usd_rate)
        unrealized_cny = convert_market_to_cny(unrealized, instrument.currency, usd_rate)
        realized_cny = convert_market_to_cny(state.realized_pnl, instrument.currency, usd_rate)

        holdings.append(
            {
                "instrument_id": instrument.id,
                "code": instrument.code,
                "name": instrument.name,
                "category": instrument.category,
                "account_id": instrument.account_id,
                "account_name": instrument.account.name,
                "currency": instrument.currency,
                "quantity": float(state.quantity),
                "avg_cost": float(state.avg_cost),
                "total_cost": float(state.total_cost),
                "current_price": float(current_price),
                "market_value": float(market_value),
                "market_value_cny": float(market_value_cny),
                "unrealized_pnl": float(unrealized),
                "unrealized_pnl_cny": float(unrealized_cny),
                "unrealized_pnl_rate": unrealized_rate,
                "realized_pnl": float(state.realized_pnl),
                "realized_pnl_cny": float(realized_cny),
            }
        )

    total_assets_cny = sum(h["market_value_cny"] for h in holdings) or 0.0
    for holding in holdings:
        holding["weight"] = (
            holding["market_value_cny"] / total_assets_cny * 100
            if total_assets_cny > 0
            else 0.0
        )

    return holdings
