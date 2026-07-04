from datetime import date
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.models import Instrument, PriceSnapshot
from app.schemas.schemas import PriceOut, PriceRefreshItem, PriceRefreshResult, PriceUpdate
from app.services.market_data import fetch_instrument_price, resolve_source_label

router = APIRouter(prefix="/api/prices", tags=["prices"])


def _to_price_out(instrument: Instrument, latest: Optional[PriceSnapshot]) -> PriceOut:
    return PriceOut(
        instrument_id=instrument.id,
        instrument_code=instrument.code,
        instrument_name=instrument.name,
        price=float(latest.price) if latest else None,
        snapshot_date=latest.snapshot_date if latest else None,
        currency=instrument.currency,
    )


@router.get("", response_model=List[PriceOut])
def list_latest_prices(db: Session = Depends(get_db)):
    instruments = (
        db.query(Instrument)
        .filter(Instrument.is_active.is_(True))
        .order_by(Instrument.id)
        .all()
    )
    result: List[PriceOut] = []

    for instrument in instruments:
        latest = (
            db.query(PriceSnapshot)
            .filter(PriceSnapshot.instrument_id == instrument.id)
            .order_by(PriceSnapshot.snapshot_date.desc(), PriceSnapshot.id.desc())
            .first()
        )
        result.append(_to_price_out(instrument, latest))

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


@router.post("/refresh", response_model=PriceRefreshResult)
def refresh_prices(db: Session = Depends(get_db)):
    instruments = (
        db.query(Instrument)
        .filter(Instrument.is_active.is_(True))
        .order_by(Instrument.id)
        .all()
    )
    items: List[PriceRefreshItem] = []
    success_count = 0

    for instrument in instruments:
        source = resolve_source_label(instrument)
        try:
            price, snapshot_date = fetch_instrument_price(instrument)
            db.add(
                PriceSnapshot(
                    instrument_id=instrument.id,
                    price=price,
                    snapshot_date=snapshot_date,
                )
            )
            items.append(
                PriceRefreshItem(
                    instrument_id=instrument.id,
                    instrument_code=instrument.code,
                    instrument_name=instrument.name,
                    success=True,
                    price=round(price, 6),
                    snapshot_date=snapshot_date,
                    currency=instrument.currency,
                    source=source,
                )
            )
            success_count += 1
        except Exception as exc:  # noqa: BLE001
            items.append(
                PriceRefreshItem(
                    instrument_id=instrument.id,
                    instrument_code=instrument.code,
                    instrument_name=instrument.name,
                    success=False,
                    currency=instrument.currency,
                    source=source,
                    error=str(exc),
                )
            )

    if success_count:
        db.commit()
    else:
        db.rollback()

    return PriceRefreshResult(
        success_count=success_count,
        fail_count=len(items) - success_count,
        items=items,
    )
