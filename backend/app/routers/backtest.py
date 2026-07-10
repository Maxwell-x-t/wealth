from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas.schemas import HistoricalBacktest
from app.services.backtest import build_historical_backtest
from app.services.config import get_config_map

router = APIRouter(prefix="/api/backtest", tags=["backtest"])


@router.get("", response_model=HistoricalBacktest)
def get_historical_backtest(
    start: date = Query(..., description="回测开始日期（计划起点）"),
    end: Optional[date] = Query(None, description="回测结束日期，默认今天"),
    db: Session = Depends(get_db),
):
    end_date = end or date.today()
    if start > end_date:
        raise HTTPException(status_code=400, detail="开始日期不能晚于结束日期")

    config = get_config_map(db)
    try:
        return build_historical_backtest(config, start, end_date)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
