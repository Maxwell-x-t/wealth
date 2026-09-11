from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.models import Account, Instrument, StrategyAccount, Transaction
from app.schemas.schemas import TransactionCreate, TransactionOut, TransactionUpdate
from app.services.config import get_config_map
from app.services.holdings import convert_transaction_to_cny, _to_decimal
from app.services.ledger import begin_write, rebuild_accounts
from app.services.transaction_validation import detect_quantity_price_swap, swap_validation_message

router = APIRouter(prefix="/api/transactions", tags=["transactions"])


def _enrich(tx):
    instrument = tx.instrument
    currency = instrument.currency
    return TransactionOut(
        **{key: getattr(tx, key) for key in TransactionOut.model_fields if hasattr(Transaction, key)},
        amount_cny=float(convert_transaction_to_cny(_to_decimal(tx.amount), currency, _to_decimal(tx.exchange_rate))),
        currency=currency, account_name=tx.account.name,
        instrument_name=instrument.name, instrument_code=instrument.code,
    )


def _validate(db, tx):
    account, instrument = db.get(Account, tx.account_id), db.get(Instrument, tx.instrument_id)
    if not account or not instrument:
        raise HTTPException(404, "账户或品种不存在")
    if instrument.account_id != account.id:
        raise HTTPException(400, "证券不属于所选账户")
    if tx.side == "buy" and not instrument.is_active:
        raise HTTPException(400, "已停用证券不能买入")
    if tx.side == "buy":
        suggestion = detect_quantity_price_swap(tx.quantity, tx.price, instrument.currency)
        if suggestion:
            raise HTTPException(400, swap_validation_message(suggestion, instrument.currency))
    if instrument.currency == "CNY":
        tx.exchange_rate = 1
    elif tx.exchange_rate <= 1:
        tx.exchange_rate = float(get_config_map(db).get("usd_cny_rate", 7.2))
    if not db.get(StrategyAccount, tx.account_id) and tx.etf_layers_after is not None:
        raise HTTPException(400, "策略层数仅适用于已接入的策略账户")
    tx.amount = (Decimal(str(tx.quantity)) * Decimal(str(tx.price))).quantize(Decimal("0.0001"))


@router.get("", response_model=list[TransactionOut])
def list_transactions(plan_phase: Optional[str] = Query(default=None), account_id: Optional[int] = None,
                      db: Session = Depends(get_db)):
    query = db.query(Transaction).order_by(Transaction.trade_date.desc(), Transaction.id.desc())
    if plan_phase in ("building", "dca"):
        query = query.filter(Transaction.plan_phase == plan_phase)
    if account_id is not None:
        query = query.filter(Transaction.account_id == account_id)
    return [_enrich(row) for row in query.all()]


@router.post("", response_model=TransactionOut)
def create_transaction(payload: TransactionCreate, db: Session = Depends(get_db)):
    begin_write(db)
    data = payload.model_dump(exclude={"request_id"})
    request_id = str(payload.request_id) if payload.request_id else None
    tx = Transaction(**data, request_id=request_id)
    _validate(db, tx)
    if request_id:
        existing = db.query(Transaction).filter_by(request_id=request_id).first()
        if existing:
            for key in data:
                a, b = getattr(existing, key), getattr(tx, key)
                equal = float(a) == float(b) if key in ("quantity", "price", "fee", "exchange_rate") else a == b
                if not equal:
                    raise HTTPException(409, "重复请求编号对应不同成交")
            return _enrich(existing)
    db.add(tx)
    rebuild_accounts(db, [tx.account_id])
    db.commit()
    db.refresh(tx)
    return _enrich(tx)


@router.put("/{transaction_id}", response_model=TransactionOut)
def update_transaction(transaction_id: int, payload: TransactionUpdate, db: Session = Depends(get_db)):
    begin_write(db)
    tx = db.get(Transaction, transaction_id)
    if not tx:
        raise HTTPException(404, "交易记录不存在")
    old_account = tx.account_id
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(tx, key, value)
    _validate(db, tx)
    rebuild_accounts(db, [old_account, tx.account_id])
    db.commit()
    db.refresh(tx)
    return _enrich(tx)


@router.delete("/{transaction_id}")
def delete_transaction(transaction_id: int, db: Session = Depends(get_db)):
    begin_write(db)
    tx = db.get(Transaction, transaction_id)
    if not tx:
        raise HTTPException(404, "交易记录不存在")
    account_id = tx.account_id
    db.delete(tx)
    rebuild_accounts(db, [account_id])
    db.commit()
    return {"ok": True}
