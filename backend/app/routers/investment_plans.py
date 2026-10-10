from datetime import date, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas.schemas import InvestmentPlanItem, InvestmentPlanOverview
from app.services.config import get_config_map
from app.services.fx_rate import get_latest_usd_cny_rate
from app.services.investment_plan import (
    build_plan_overview,
    generate_investment_plans,
    set_plan_defer,
    set_plan_skip,
)

router = APIRouter(prefix="/api/investment-plans", tags=["investment-plans"])


class PlanSkipRequest(BaseModel):
    plan_date: date
    account: str
    category: str
    phase: str
    skipped: bool = True


class PlanDeferRequest(BaseModel):
    plan_date: date
    account: str
    category: str
    phase: str
    deferred: bool = True


@router.get("", response_model=List[InvestmentPlanItem])
def list_investment_plans(
    start: Optional[date] = None,
    end: Optional[date] = None,
    phase: Optional[str] = None,
    account: Optional[str] = None,
    db: Session = Depends(get_db),
):
    display_start = start if start is not None else date.today()
    display_end = end if end is not None else display_start + timedelta(days=92)

    config = get_config_map(db)
    config["usd_cny_rate"] = get_latest_usd_cny_rate(db)
    plans = generate_investment_plans(db, config, start=display_start, end=display_end)
    if phase in ("building", "dca"):
        plans = [item for item in plans if item["phase"] == phase]
    if account in ("大陆", "香港"):
        plans = [item for item in plans if item["account"] == account]
    return plans


@router.get("/overview", response_model=InvestmentPlanOverview)
def get_investment_plan_overview(db: Session = Depends(get_db)):
    config = get_config_map(db)
    config["usd_cny_rate"] = get_latest_usd_cny_rate(db)
    return build_plan_overview(db, config)


def _reload_plan_item(db: Session, plan_date: date, account: str, category: str, phase: str, fallback_status: str):
    config = get_config_map(db)
    config["usd_cny_rate"] = get_latest_usd_cny_rate(db)
    plans = generate_investment_plans(db, config, start=plan_date, end=plan_date)
    for item in plans:
        if (
            item["plan_date"] == plan_date
            and item["account"] == account
            and item["category"] == category
            and item["phase"] == phase
        ):
            return item
    return {
        "plan_date": plan_date,
        "phase": phase,
        "phase_label": "定投" if phase == "dca" else "建仓",
        "week_index": 0,
        "account": account,
        "category": category,
        "category_label": category,
        "target_label": f"{account} · {category}",
        "amount_cny": 0,
        "base_amount_cny": 0,
        "matched_amount_cny": 0,
        "shortfall_cny": 0,
        "rolled_over_amount_cny": 0,
        "rolled_over_count": 0,
        "status": fallback_status,
    }


@router.post("/skip", response_model=InvestmentPlanItem)
def skip_investment_plan(payload: PlanSkipRequest, db: Session = Depends(get_db)):
    set_plan_skip(db, payload.plan_date, payload.account, payload.category, payload.phase, payload.skipped)
    return _reload_plan_item(
        db,
        payload.plan_date,
        payload.account,
        payload.category,
        payload.phase,
        "skipped" if payload.skipped else "pending",
    )


@router.post("/defer", response_model=InvestmentPlanItem)
def defer_investment_plan(payload: PlanDeferRequest, db: Session = Depends(get_db)):
    set_plan_defer(
        db,
        payload.plan_date,
        payload.account,
        payload.category,
        payload.phase,
        payload.deferred,
    )
    return _reload_plan_item(
        db,
        payload.plan_date,
        payload.account,
        payload.category,
        payload.phase,
        "deferred" if payload.deferred else "pending",
    )
