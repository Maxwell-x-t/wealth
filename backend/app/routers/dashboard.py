from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas.schemas import AllocationTarget, AssetSnapshotPoint, DashboardSummary
from app.services.fx_rate import get_latest_usd_cny_rate
from app.services.config import get_config_map, save_config
from app.services.returns import build_asset_history, compute_dashboard_metrics

router = APIRouter(tags=["dashboard"])


def _config_to_schema(config: dict) -> AllocationTarget:
    plan_start = config.get("plan_start_date")
    return AllocationTarget(
        nasdaq=float(config.get("nasdaq", 70)),
        sp500=float(config.get("sp500", 30)),
        a_share=float(config.get("a_share", 0)),
        gold=float(config.get("gold", 0)),
        cash=float(config.get("cash", 0)),
        qdii=float(config.get("qdii", 0)),
        mainland=float(config.get("mainland", 60)),
        hk=float(config.get("hk", 40)),
        usd_cny_rate=float(config.get("usd_cny_rate", 7.2)),
        plan_start_date=date.fromisoformat(plan_start) if plan_start else None,
        building_first_month_amount=float(config.get("building_first_month_amount", 100000)),
        building_monthly_amount=float(config.get("building_monthly_amount", 50000)),
        building_months=int(config.get("building_months", 8)),
        dca_monthly_amount=float(config.get("dca_monthly_amount", 10000)),
        plan_horizon_years=int(config.get("plan_horizon_years", 20)),
        mainland_nasdaq_code=config.get("mainland_nasdaq_code", "513100"),
        mainland_sp500_code=config.get("mainland_sp500_code", "513500"),
        hk_nasdaq_code=config.get("hk_nasdaq_code", "QQQM"),
        hk_sp500_code=config.get("hk_sp500_code", "VOO"),
        forecast_years=int(config.get("forecast_years", 20)),
        forecast_return_pessimistic=float(config.get("forecast_return_pessimistic", 4)),
        forecast_return_neutral=float(config.get("forecast_return_neutral", 8)),
        forecast_return_optimistic=float(config.get("forecast_return_optimistic", 12)),
    )


def _schema_to_config(payload: AllocationTarget) -> dict:
    data = payload.model_dump()
    if data.get("plan_start_date"):
        data["plan_start_date"] = data["plan_start_date"].isoformat()
    return data


@router.get("/api/dashboard", response_model=DashboardSummary)
def get_dashboard(db: Session = Depends(get_db)):
    config = get_config_map(db)
    config["usd_cny_rate"] = get_latest_usd_cny_rate(db)
    return compute_dashboard_metrics(db, config)


@router.get("/api/dashboard/history", response_model=list[AssetSnapshotPoint])
def get_dashboard_history(db: Session = Depends(get_db)):
    usd_cny_rate = get_latest_usd_cny_rate(db)
    return build_asset_history(db, usd_cny_rate=usd_cny_rate)


@router.get("/api/config", response_model=AllocationTarget)
def get_config(db: Session = Depends(get_db)):
    config = get_config_map(db)
    config["usd_cny_rate"] = get_latest_usd_cny_rate(db)
    return _config_to_schema(config)


@router.put("/api/config", response_model=AllocationTarget)
def update_config(payload: AllocationTarget, db: Session = Depends(get_db)):
    asset_total = round(
        payload.nasdaq + payload.sp500 + payload.a_share + payload.gold + payload.cash + payload.qdii,
        2,
    )
    if asset_total != 100:
        raise HTTPException(
            status_code=400,
            detail="纳指、标普、A股、黄金、现金、QDII 比例之和必须为 100",
        )
    if round(payload.mainland + payload.hk, 2) != 100:
        raise HTTPException(status_code=400, detail="大陆与香港比例之和必须为 100")

    save_config(db, _schema_to_config(payload))
    return _config_to_schema(get_config_map(db))
