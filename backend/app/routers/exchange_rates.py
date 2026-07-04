from datetime import date
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.services.fx_rate import get_latest_usd_cny_rate, list_fx_rates, refresh_usd_cny_rate, save_usd_cny_rate

router = APIRouter(prefix="/api/exchange-rates", tags=["exchange-rates"])


class FxRateOut(BaseModel):
    pair: str
    rate: float
    snapshot_date: date
    source: Optional[str] = None

    model_config = {"from_attributes": True}


class FxRateCreate(BaseModel):
    rate: float = Field(gt=0)
    snapshot_date: Optional[date] = None


@router.get("/latest", response_model=FxRateOut)
def get_latest_rate(db: Session = Depends(get_db)):
    rate = get_latest_usd_cny_rate(db)
    rows = list_fx_rates(db, limit=1)
    if rows:
        return FxRateOut(pair=rows[0].pair, rate=float(rows[0].rate), snapshot_date=rows[0].snapshot_date)
    return FxRateOut(pair="USD/CNY", rate=rate, snapshot_date=date.today())


@router.get("", response_model=List[FxRateOut])
def get_rate_history(db: Session = Depends(get_db)):
    return [
        FxRateOut(pair=row.pair, rate=float(row.rate), snapshot_date=row.snapshot_date)
        for row in list_fx_rates(db)
    ]


@router.post("", response_model=FxRateOut)
def create_rate(payload: FxRateCreate, db: Session = Depends(get_db)):
    row = save_usd_cny_rate(db, payload.rate, payload.snapshot_date)
    return FxRateOut(pair=row.pair, rate=float(row.rate), snapshot_date=row.snapshot_date)


@router.post("/refresh", response_model=FxRateOut)
def refresh_rate(db: Session = Depends(get_db)):
    try:
        result = refresh_usd_cny_rate(db)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"拉取汇率失败：{exc}") from exc
    return FxRateOut(**result)
