from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from app.models.models import AppConfig, FxRateSnapshot
from app.services.config import get_config_map

PAIR_USD_CNY = "USD/CNY"


def get_latest_usd_cny_rate(db: Session) -> float:
    latest = (
        db.query(FxRateSnapshot)
        .filter(FxRateSnapshot.pair == PAIR_USD_CNY)
        .order_by(FxRateSnapshot.snapshot_date.desc(), FxRateSnapshot.id.desc())
        .first()
    )
    if latest:
        return float(latest.rate)
    config = get_config_map(db)
    return float(config.get("usd_cny_rate", 7.2))


def list_fx_rates(db: Session, limit: int = 30) -> list[FxRateSnapshot]:
    return (
        db.query(FxRateSnapshot)
        .filter(FxRateSnapshot.pair == PAIR_USD_CNY)
        .order_by(FxRateSnapshot.snapshot_date.desc(), FxRateSnapshot.id.desc())
        .limit(limit)
        .all()
    )


def save_usd_cny_rate(db: Session, rate: float, snapshot_date: date | None = None) -> FxRateSnapshot:
    snap_date = snapshot_date or date.today()
    row = FxRateSnapshot(pair=PAIR_USD_CNY, rate=rate, snapshot_date=snap_date)
    db.add(row)
    config_row = db.query(AppConfig).filter(AppConfig.key == "usd_cny_rate").first()
    if config_row:
        config_row.value = str(rate)
    else:
        db.add(AppConfig(key="usd_cny_rate", value=str(rate)))
    db.commit()
    db.refresh(row)
    return row
