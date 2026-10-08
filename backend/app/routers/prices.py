from datetime import date
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.models import Instrument, PriceSnapshot
from app.schemas.schemas import PriceOut, PriceRefreshItem, PriceRefreshResult, PriceUpdate
from app.services.holdings import get_latest_quotes
from app.services.market_data import is_cn_etf
from app.services.price_refresh import refresh_all_prices

router = APIRouter(prefix="/api/prices", tags=["prices"])


def _snapshot_optional_float(latest: Optional[PriceSnapshot], attr: str) -> Optional[float]:
    if latest is None:
        return None
    value = getattr(latest, attr, None)
    if value is None:
        return None
    return float(value)


def _to_price_out(
    instrument: Instrument,
    latest: Optional[PriceSnapshot],
    quote: Optional[dict] = None,
) -> PriceOut:
    quote = quote or {}
    if is_cn_etf(instrument.code):
        iopv = quote.get("iopv")
        premium_rate = quote.get("premium_rate")
        if iopv is None:
            iopv = _snapshot_optional_float(latest, "iopv")
        if premium_rate is None:
            premium_rate = _snapshot_optional_float(latest, "premium_rate")
    else:
        iopv = None
        premium_rate = None
    return PriceOut(
        instrument_id=instrument.id,
        instrument_code=instrument.code,
        instrument_name=instrument.name,
        price=float(latest.price) if latest else None,
        iopv=iopv,
        premium_rate=premium_rate,
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
    quotes = get_latest_quotes(db)
    result: List[PriceOut] = []

    for instrument in instruments:
        latest = (
            db.query(PriceSnapshot)
            .filter(PriceSnapshot.instrument_id == instrument.id)
            .order_by(PriceSnapshot.snapshot_date.desc(), PriceSnapshot.id.desc())
            .first()
        )
        result.append(_to_price_out(instrument, latest, quotes.get(instrument.id)))

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
        iopv=None,
        premium_rate=None,
        snapshot_date=snapshot_date,
        currency=instrument.currency,
    )


@router.post("/refresh", response_model=PriceRefreshResult)
def refresh_prices(db: Session = Depends(get_db)):
    result = refresh_all_prices(db)
    return PriceRefreshResult(
        success_count=result["success_count"],
        fail_count=result["fail_count"],
        items=[PriceRefreshItem(**item) for item in result["items"]],
    )
