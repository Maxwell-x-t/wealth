from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas.schemas import HistoricalBacktest
from app.services.backtest import build_historical_backtest
from app.services.config import get_config_map

router = APIRouter(prefix="/api/backtest", tags=["backtest"])

# 回测页中性区并排对比预设
MA_CENTER_OPTIONS = (0.0, 5.0, 8.0, 10.0)


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
        baseline = build_historical_backtest(config, start, end_date, force_ma=False)
        center_variants = []
        for center in MA_CENTER_OPTIONS:
            run = build_historical_backtest(
                config,
                start,
                end_date,
                force_ma=True,
                force_center_pct=center,
            )
            center_variants.append(
                {
                    "center_pct": center,
                    "currency_summaries": run["currency_summaries"],
                    "points": run["points"],
                }
            )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    default_center = 8.0
    primary = next(
        (item for item in center_variants if item["center_pct"] == default_center),
        center_variants[0] if center_variants else None,
    )
    if primary is not None:
        baseline["ma_comparison"] = {
            "settings": {
                "enabled": True,
                "window_days": int(float(config.get("dca_ma_window_days", 200))),
                "min_factor": float(config.get("dca_ma_min_factor", 0.7)),
                "max_factor": float(config.get("dca_ma_max_factor", 1.3)),
                "band_pct": float(config.get("dca_ma_band_pct", 20)),
                "center_pct": default_center,
            },
            "currency_summaries": primary["currency_summaries"],
            "points": primary["points"],
        }
    baseline["ma_center_comparisons"] = center_variants
    return baseline
