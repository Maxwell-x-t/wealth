from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.models import Instrument, PriceSnapshot
from app.schemas.schemas import PriceOut, PriceUpdate

router = APIRouter(prefix="/api/prices", tags=["prices"])


@router.get("", response_model=list[PriceOut])
def list_latest_prices(db: Session = Depends(get_db)):
    instruments = db.query(Instrument).filter(Instrument.is_active.is_(True)).all()
    result: list[PriceOut] = []

    for instrument in instruments:
        latest = (
            db.query(PriceSnapshot)
            .filter(PriceSnapshot.instrument_id == instrument.id)
            .order_by(PriceSnapshot.snapshot_date.desc(), PriceSnapshot.id.desc())
            .first()
        )
        if latest:
            result.append(
                PriceOut(
                    instrument_id=instrument.id,
                    instrument_code=instrument.code,
                    instrument_name=instrument.name,
                    price=float(latest.price),
                    snapshot_date=latest.snapshot_date,
                    currency=instrument.currency,
                )
            )

    return result


@router.post("", response_model=PriceOut)
def upsert_price(payload: PriceUpdate, db: Session = Depends(get_db)):
    instrument = db.query(Instrument).filter(Instrument.id == payload.instrument_id).first()
    if not instrument:
        raise HTTPException(status_code=404, detail="品种不存在")

    snapshot_date = payload.snapshot_date or date.today()
    row = PriceSnapshot(
        instrument_id=payload.instrument_id,
        price=payload.price,
        snapshot_date=snapshot_date,
    )
    db.add(row)
    db.commit()

    return PriceOut(
        instrument_id=instrument.id,
        instrument_code=instrument.code,
        instrument_name=instrument.name,
        price=payload.price,
        snapshot_date=snapshot_date,
        currency=instrument.currency,
    )
