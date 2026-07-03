from datetime import date, timedelta
from typing import Optional

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas.schemas import InvestmentPlanItem, InvestmentPlanOverview
from app.services.config import get_config_map
from app.services.fx_rate import get_latest_usd_cny_rate
from app.services.investment_plan import build_plan_overview, generate_investment_plans

router = APIRouter(prefix="/api/investment-plans", tags=["investment-plans"])


@router.get("", response_model=list[InvestmentPlanItem])
def list_investment_plans(
    start: Optional[date] = None,
    end: Optional[date] = None,
    phase: Optional[str] = None,
    db: Session = Depends(get_db),
):
    display_start = start if start is not None else date.today()
    display_end = end if end is not None else display_start + timedelta(days=92)

    config = get_config_map(db)
    config["usd_cny_rate"] = get_latest_usd_cny_rate(db)
    # 始终从计划起点生成并合并，再按展示区间过滤
    plans = generate_investment_plans(db, config, start=display_start, end=display_end)
    if phase in ("building", "dca"):
        plans = [item for item in plans if item["phase"] == phase]
    return plans


@router.get("/overview", response_model=InvestmentPlanOverview)
def get_investment_plan_overview(db: Session = Depends(get_db)):
    config = get_config_map(db)
    config["usd_cny_rate"] = get_latest_usd_cny_rate(db)
    return build_plan_overview(db, config)
