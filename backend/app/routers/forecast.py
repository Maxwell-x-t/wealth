from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas.schemas import RiskSimulation, WealthForecast
from app.services.config import get_config_map
from app.services.forecast import build_wealth_forecast
from app.services.fx_rate import get_latest_usd_cny_rate
from app.services.risk import build_risk_simulation

router = APIRouter(prefix="/api/forecast", tags=["forecast"])


@router.get("", response_model=WealthForecast)
def get_wealth_forecast(
    years: Optional[int] = Query(None, ge=1, le=40),
    pessimistic: Optional[float] = Query(None, ge=-50, le=50),
    neutral: Optional[float] = Query(None, ge=-50, le=50),
    optimistic: Optional[float] = Query(None, ge=-50, le=50),
    use_inflation: bool = Query(False),
    inflation_pct: Optional[float] = Query(None, ge=0, le=20),
    use_monte_carlo: bool = Query(False),
    mc_volatility: Optional[float] = Query(None, ge=0, le=80),
    mc_paths: Optional[int] = Query(None, ge=50, le=2000),
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
        use_inflation=use_inflation,
        inflation_pct=inflation_pct,
        use_monte_carlo=use_monte_carlo,
        mc_volatility_pct=mc_volatility,
        mc_paths=mc_paths,
    )


@router.get("/risk", response_model=RiskSimulation)
def get_risk_simulation(
    years: Optional[int] = Query(None, ge=1, le=40),
    recovery_return: Optional[float] = Query(None, ge=-50, le=50),
    drawdowns: Optional[str] = Query(None, description="逗号分隔跌幅，如 30,40,50"),
    db: Session = Depends(get_db),
):
    config = get_config_map(db)
    config["usd_cny_rate"] = get_latest_usd_cny_rate(db)
    drawdown_list: Optional[List[float]] = None
    if drawdowns:
        drawdown_list = [float(item.strip()) for item in drawdowns.split(",") if item.strip()]
    return build_risk_simulation(
        db,
        config,
        drawdowns=drawdown_list,
        recovery_return_pct=recovery_return,
        horizon_years=years,
    )
