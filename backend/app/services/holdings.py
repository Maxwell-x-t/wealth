from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
import json
from typing import Optional

from sqlalchemy.orm import Session

from app.models.models import Instrument, PriceSnapshot, StrategyAccount, Transaction


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


def compute_instrument_states(db: Session, as_of: Optional[date] = None) -> dict[int, InstrumentState]:
    query = db.query(Transaction).order_by(Transaction.trade_date.asc(), Transaction.id.asc())
    if as_of is not None:
        query = query.filter(Transaction.trade_date <= as_of)
    transactions = query.all()
    states = {}
    managed = {ledger.account_id: ledger for ledger in db.query(StrategyAccount)}
    from app.services.ledger import canonical_code, ledger_rows
    for ledger in managed.values():
        raw = json.loads(ledger.opening_json)
        if as_of and as_of < date.fromisoformat(raw["as_of"]):
            continue
        for instrument in db.query(Instrument).filter_by(account_id=ledger.account_id):
            code = canonical_code(instrument.code)
            qty = D(str(raw["positions"].get(code, 0)))
            # Opening market values are the performance baseline, not acquisition costs.
            cost = qty * D(str(raw["reference_prices"].get(code, 0)))
            states[instrument.id] = InstrumentState(instrument.id, qty, cost, D("0"))
        for row in ledger_rows(db, ledger.account_id, as_of):
            if isinstance(row, Transaction):
                _instrument_states_from_transactions([row], states)
            elif row.kind == "shares":
                state = states[row.instrument_id]
                state.quantity += D(str(row.quantity))
                if state.quantity < 0:
                    raise ValueError("送转调整后持仓不能为负")
    return _instrument_states_from_transactions([tx for tx in transactions if tx.account_id not in managed], states)


def _instrument_states_from_transactions(transactions: list[Transaction], states=None) -> dict[int, InstrumentState]:
    states = {} if states is None else states

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


def build_historical_price_index(db: Session) -> dict[int, list[tuple[date, D]]]:
    rows = (
        db.query(PriceSnapshot)
        .order_by(PriceSnapshot.snapshot_date.asc(), PriceSnapshot.id.asc())
        .all()
    )
    index: dict[int, list[tuple[date, D]]] = {}
    for row in rows:
        index.setdefault(row.instrument_id, []).append((row.snapshot_date, _to_decimal(row.price)))
    return index


def price_on_date(price_index: dict[int, list[tuple[date, D]]], instrument_id: int, as_of: date) -> Optional[D]:
    series = price_index.get(instrument_id, [])
    chosen: Optional[D] = None
    for snap_date, price in series:
        if snap_date <= as_of:
            chosen = price
        else:
            break
    return chosen


def total_assets_cny_as_of(
    db: Session,
    as_of: date,
    price_index: dict[int, list[tuple[date, D]]],
    usd_cny_rate: D,
) -> D:
    instruments = db.query(Instrument).filter(Instrument.is_active.is_(True)).all()
    states = compute_instrument_states(db, as_of=as_of)
    total = D("0")

    for instrument in instruments:
        state = states.get(instrument.id)
        if not state or state.quantity <= 0:
            continue
        price = price_on_date(price_index, instrument.id, as_of)
        if price is None or price <= 0:
            continue
        market_value = (state.quantity * price).quantize(TWO)
        total += convert_market_to_cny(market_value, instrument.currency, usd_cny_rate)

    from app.services.ledger import managed_cash
    return (total + sum(cash for _, cash in managed_cash(db, as_of))).quantize(TWO)


def get_latest_prices(db: Session) -> dict[int, D]:
    return {
        instrument_id: quote["price"]
        for instrument_id, quote in get_latest_quotes(db).items()
    }


def get_latest_quotes(db: Session) -> dict[int, dict]:
    """最新行情：price 必有；大陆 ETF 可能带 iopv / premium_rate。"""
    instruments = db.query(Instrument).all()
    quotes: dict[int, dict] = {}

    for instrument in instruments:
        latest = (
            db.query(PriceSnapshot)
            .filter(PriceSnapshot.instrument_id == instrument.id)
            .order_by(PriceSnapshot.snapshot_date.desc(), PriceSnapshot.id.desc())
            .first()
        )
        if not latest:
            continue
        quotes[instrument.id] = {
            "price": _to_decimal(latest.price),
            "iopv": float(latest.iopv) if getattr(latest, "iopv", None) is not None else None,
            "premium_rate": (
                float(latest.premium_rate)
                if getattr(latest, "premium_rate", None) is not None
                else None
            ),
        }

    return quotes


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
    latest_quotes = get_latest_quotes(db)
    usd_rate = _to_decimal(usd_cny_rate)

    holdings: list[dict] = []
    for instrument in instruments:
        state = states.get(
            instrument.id,
            InstrumentState(instrument.id, D("0"), D("0"), D("0")),
        )
        if state.quantity <= 0 and state.realized_pnl == 0:
            continue

        quote = latest_quotes.get(instrument.id) or {}
        current_price = quote.get("price", D("0"))
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
                "iopv": quote.get("iopv"),
                "premium_rate": quote.get("premium_rate"),
                "market_value": float(market_value),
                "market_value_cny": float(market_value_cny),
                "unrealized_pnl": float(unrealized),
                "unrealized_pnl_cny": float(unrealized_cny),
                "unrealized_pnl_rate": unrealized_rate,
                "realized_pnl": float(state.realized_pnl),
                "realized_pnl_cny": float(realized_cny),
                "basis_label": "期初市值" if db.get(StrategyAccount, instrument.account_id) else "买入成本",
            }
        )

    from app.services.ledger import managed_cash
    for account, cash in managed_cash(db):
        holdings.append(dict(instrument_id=-account.id, code="CASH", name="可用现金", category="cash",
            account_id=account.id, account_name=account.name, currency="CNY", quantity=float(cash),
            avg_cost=1, total_cost=float(cash), current_price=1, market_value=float(cash),
            market_value_cny=float(cash), unrealized_pnl=0, unrealized_pnl_cny=0,
            unrealized_pnl_rate=0, realized_pnl=0, realized_pnl_cny=0, basis_label="账户余额"))
    total_assets_cny = sum(h["market_value_cny"] for h in holdings) or 0.0
    for holding in holdings:
        holding["weight"] = (
            holding["market_value_cny"] / total_assets_cny * 100
            if total_assets_cny > 0
            else 0.0
        )

    return holdings
