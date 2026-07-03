from datetime import date
from typing import List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.services.fx_rate import get_latest_usd_cny_rate, list_fx_rates, save_usd_cny_rate

router = APIRouter(prefix="/api/exchange-rates", tags=["exchange-rates"])


class FxRateOut(BaseModel):
    pair: str
    rate: float
    snapshot_date: date

    model_config = {"from_attributes": True}


class FxRateCreate(BaseModel):
    rate: float = Field(gt=0)
    snapshot_date: Optional[date] = None


@router.get("/latest", response_model=FxRateOut)
def get_latest_rate(db: Session = Depends(get_db)):
    rate = get_latest_usd_cny_rate(db)
    rows = list_fx_rates(db, limit=1)
    if rows:
        return rows[0]
    return FxRateOut(pair="USD/CNY", rate=rate, snapshot_date=date.today())


@router.get("", response_model=List[FxRateOut])
def get_rate_history(db: Session = Depends(get_db)):
    return list_fx_rates(db)


@router.post("", response_model=FxRateOut)
def create_rate(payload: FxRateCreate, db: Session = Depends(get_db)):
    return save_usd_cny_rate(db, payload.rate, payload.snapshot_date)
