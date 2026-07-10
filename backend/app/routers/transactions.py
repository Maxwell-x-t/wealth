from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.models import Account, Instrument, Transaction
from app.schemas.schemas import TransactionCreate, TransactionOut, TransactionUpdate
from app.services.config import get_config_map
from app.services.holdings import compute_instrument_states, convert_transaction_to_cny, _to_decimal
from app.services.transaction_validation import detect_quantity_price_swap, swap_validation_message

router = APIRouter(prefix="/api/transactions", tags=["transactions"])


def _calc_amount(quantity: float, price: float) -> float:
    return round(quantity * price, 4)


def _normalize_exchange_rate(db: Session, instrument: Instrument, exchange_rate: float) -> float:
    if instrument.currency == "CNY":
        return 1.0
    if exchange_rate > 1:
        return exchange_rate
    config = get_config_map(db)
    return float(config.get("usd_cny_rate", 7.2))


def _normalize_fee(fee: float) -> float:
    return max(0.0, float(fee))


def _enrich(tx: Transaction) -> TransactionOut:
    instrument = tx.instrument
    currency = instrument.currency if instrument else "CNY"
    rate = _to_decimal(tx.exchange_rate)
    amount_cny = float(convert_transaction_to_cny(_to_decimal(tx.amount), currency, rate))

    return TransactionOut(
        id=tx.id,
        trade_date=tx.trade_date,
        account_id=tx.account_id,
        instrument_id=tx.instrument_id,
        side=tx.side,
        quantity=float(tx.quantity),
        price=float(tx.price),
        amount=float(tx.amount),
        fee=float(tx.fee),
        exchange_rate=float(tx.exchange_rate),
        note=tx.note,
        plan_phase=tx.plan_phase,
        amount_cny=amount_cny,
        currency=currency,
        created_at=tx.created_at,
        account_name=tx.account.name if tx.account else None,
        instrument_name=instrument.name if instrument else None,
        instrument_code=instrument.code if instrument else None,
    )


def _validate_sell(db: Session, instrument_id: int, quantity: float, exclude_id: Optional[int] = None) -> None:
    states = compute_instrument_states(db)
    state = states.get(instrument_id)
    held = float(state.quantity) if state else 0.0

    if exclude_id:
        existing = db.query(Transaction).filter(Transaction.id == exclude_id).first()
        if existing and existing.side == "sell" and existing.instrument_id == instrument_id:
            held += float(existing.quantity)

    if quantity > held:
        raise HTTPException(status_code=400, detail=f"卖出数量超过持仓（当前可卖 {held}）")


@router.get("", response_model=list[TransactionOut])
def list_transactions(
    plan_phase: Optional[str] = Query(default=None),
    db: Session = Depends(get_db),
):
    query = db.query(Transaction).order_by(Transaction.trade_date.desc(), Transaction.id.desc())
    if plan_phase in ("building", "dca"):
        query = query.filter(Transaction.plan_phase == plan_phase)
    rows = query.all()
    return [_enrich(row) for row in rows]


def _validate_qty_price(quantity: float, price: float, currency: str) -> None:
    suggestion = detect_quantity_price_swap(quantity, price, currency)
    if suggestion:
        raise HTTPException(status_code=400, detail=swap_validation_message(suggestion, currency))


@router.post("", response_model=TransactionOut)
def create_transaction(payload: TransactionCreate, db: Session = Depends(get_db)):
    account = db.query(Account).filter(Account.id == payload.account_id).first()
    instrument = db.query(Instrument).filter(Instrument.id == payload.instrument_id).first()
    if not account or not instrument:
        raise HTTPException(status_code=404, detail="账户或品种不存在")

    if payload.side == "sell":
        _validate_sell(db, payload.instrument_id, payload.quantity)
    else:
        _validate_qty_price(payload.quantity, payload.price, instrument.currency)

    exchange_rate = _normalize_exchange_rate(db, instrument, payload.exchange_rate)

    tx = Transaction(
        trade_date=payload.trade_date,
        account_id=payload.account_id,
        instrument_id=payload.instrument_id,
        side=payload.side,
        quantity=payload.quantity,
        price=payload.price,
        amount=_calc_amount(payload.quantity, payload.price),
        fee=_normalize_fee(payload.fee),
        exchange_rate=exchange_rate,
        note=payload.note,
        plan_phase=payload.plan_phase,
    )
    db.add(tx)
    db.commit()
    db.refresh(tx)
    return _enrich(tx)


@router.put("/{transaction_id}", response_model=TransactionOut)
def update_transaction(transaction_id: int, payload: TransactionUpdate, db: Session = Depends(get_db)):
    tx = db.query(Transaction).filter(Transaction.id == transaction_id).first()
    if not tx:
        raise HTTPException(status_code=404, detail="交易记录不存在")

    data = payload.model_dump(exclude_unset=True)
    side = data.get("side", tx.side)
    quantity = data.get("quantity", float(tx.quantity))
    price = data.get("price", float(tx.price))
    instrument_id = data.get("instrument_id", tx.instrument_id)

    if side == "sell":
        _validate_sell(db, instrument_id, quantity, exclude_id=transaction_id)
    else:
        instrument = db.query(Instrument).filter(Instrument.id == instrument_id).first()
        if instrument:
            _validate_qty_price(quantity, price, instrument.currency)

    for key, value in data.items():
        setattr(tx, key, value)

    instrument = db.query(Instrument).filter(Instrument.id == tx.instrument_id).one()
    tx.exchange_rate = _normalize_exchange_rate(db, instrument, float(tx.exchange_rate))
    tx.amount = _calc_amount(float(tx.quantity), float(tx.price))
    tx.fee = _normalize_fee(float(tx.fee))
    db.commit()
    db.refresh(tx)
    return _enrich(tx)


@router.delete("/{transaction_id}")
def delete_transaction(transaction_id: int, db: Session = Depends(get_db)):
    tx = db.query(Transaction).filter(Transaction.id == transaction_id).first()
    if not tx:
        raise HTTPException(status_code=404, detail="交易记录不存在")
    db.delete(tx)
    db.commit()
    return {"ok": True}
