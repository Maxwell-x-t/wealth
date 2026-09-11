from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.models import Account, Instrument, StrategyAccount, Transaction
from app.schemas.schemas import InstrumentCreate, InstrumentOut, InstrumentUpdate
from app.services.ledger import begin_write, register_instrument

router = APIRouter(prefix="/api/instruments", tags=["instruments"])


def _enrich(instrument: Instrument) -> InstrumentOut:
    return InstrumentOut(
        id=instrument.id,
        code=instrument.code,
        name=instrument.name,
        category=instrument.category,
        account_id=instrument.account_id,
        currency=instrument.currency,
        is_active=instrument.is_active,
        account_name=instrument.account.name if instrument.account else None,
    )


def _check_duplicate_code(
    db: Session,
    account_id: int,
    code: str,
    exclude_id: Optional[int] = None,
) -> None:
    query = db.query(Instrument).filter(
        Instrument.account_id == account_id,
        Instrument.code == code,
    )
    if exclude_id:
        query = query.filter(Instrument.id != exclude_id)
    if query.first():
        raise HTTPException(status_code=400, detail=f"该账户下已存在代码 {code}")


@router.get("", response_model=list[InstrumentOut])
def list_instruments(active_only: bool = False, db: Session = Depends(get_db)):
    query = db.query(Instrument).order_by(Instrument.id)
    if active_only:
        query = query.filter(Instrument.is_active.is_(True))
    return [_enrich(item) for item in query.all()]


@router.post("", response_model=InstrumentOut)
def create_instrument(payload: InstrumentCreate, db: Session = Depends(get_db)):
    begin_write(db)
    account = db.query(Account).filter(Account.id == payload.account_id).first()
    if not account:
        raise HTTPException(status_code=404, detail="账户不存在")

    _check_duplicate_code(db, payload.account_id, payload.code)

    instrument = Instrument(**payload.model_dump())
    register_instrument(db, instrument)
    db.add(instrument)
    db.commit()
    db.refresh(instrument)
    return _enrich(instrument)


@router.put("/{instrument_id}", response_model=InstrumentOut)
def update_instrument(instrument_id: int, payload: InstrumentUpdate, db: Session = Depends(get_db)):
    instrument = db.query(Instrument).filter(Instrument.id == instrument_id).first()
    if not instrument:
        raise HTTPException(status_code=404, detail="品种不存在")

    data = payload.model_dump(exclude_unset=True)
    if db.get(StrategyAccount, instrument.account_id) and any(
        key != "name" and value != getattr(instrument, key) for key, value in data.items()
    ):
        raise HTTPException(400, "已接入策略账本的证券仅能修改名称")
    account_id = data.get("account_id", instrument.account_id)
    code = data.get("code", instrument.code)

    if "account_id" in data:
        account = db.query(Account).filter(Account.id == account_id).first()
        if not account:
            raise HTTPException(status_code=404, detail="账户不存在")

    if "account_id" in data or "code" in data:
        _check_duplicate_code(db, account_id, code, exclude_id=instrument_id)

    for key, value in data.items():
        setattr(instrument, key, value)

    db.commit()
    db.refresh(instrument)
    return _enrich(instrument)


@router.delete("/{instrument_id}")
def delete_instrument(instrument_id: int, db: Session = Depends(get_db)):
    instrument = db.query(Instrument).filter(Instrument.id == instrument_id).first()
    if not instrument:
        raise HTTPException(status_code=404, detail="品种不存在")

    tx_count = db.query(Transaction).filter(Transaction.instrument_id == instrument_id).count()
    if db.get(StrategyAccount, instrument.account_id):
        raise HTTPException(400, "策略账本的期初证券不能删除")
    if tx_count > 0:
        raise HTTPException(status_code=400, detail="该品种已有交易记录，请改为停用而非删除")

    db.delete(instrument)
    db.commit()
    return {"ok": True}
