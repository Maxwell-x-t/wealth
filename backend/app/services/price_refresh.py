from __future__ import annotations

from typing import List

from sqlalchemy.orm import Session

from app.models.models import Instrument, PriceSnapshot
from app.services.market_data import fetch_instrument_price, resolve_source_label


def refresh_all_prices(db: Session) -> dict:
    instruments = (
        db.query(Instrument)
        .filter(Instrument.is_active.is_(True))
        .order_by(Instrument.id)
        .all()
    )
    items: List[dict] = []
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
                {
                    "instrument_id": instrument.id,
                    "instrument_code": instrument.code,
                    "instrument_name": instrument.name,
                    "success": True,
                    "price": round(price, 6),
                    "snapshot_date": snapshot_date,
                    "currency": instrument.currency,
                    "source": source,
                    "error": None,
                }
            )
            success_count += 1
        except Exception as exc:  # noqa: BLE001
            items.append(
                {
                    "instrument_id": instrument.id,
                    "instrument_code": instrument.code,
                    "instrument_name": instrument.name,
                    "success": False,
                    "price": None,
                    "snapshot_date": None,
                    "currency": instrument.currency,
                    "source": source,
                    "error": str(exc),
                }
            )

    if success_count:
        db.commit()
    else:
        db.rollback()

    return {
        "success_count": success_count,
        "fail_count": len(items) - success_count,
        "items": items,
    }
