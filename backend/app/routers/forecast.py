from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas.schemas import WealthForecast
from app.services.config import get_config_map
from app.services.forecast import build_wealth_forecast
from app.services.fx_rate import get_latest_usd_cny_rate

router = APIRouter(prefix="/api/forecast", tags=["forecast"])


@router.get("", response_model=WealthForecast)
def get_wealth_forecast(
    years: Optional[int] = Query(None, ge=1, le=40),
    pessimistic: Optional[float] = Query(None, ge=-50, le=50),
    neutral: Optional[float] = Query(None, ge=-50, le=50),
    optimistic: Optional[float] = Query(None, ge=-50, le=50),
    db: Session = Depends(get_db),
):
    config = get_config_map(db)
    config["usd_cny_rate"] = get_latest_usd_cny_rate(db)
    return build_wealth_forecast(
        db,
        config,
        years=years,
        pessimistic=pessimistic,
        neutral=neutral,
        optimistic=optimistic,
    )
