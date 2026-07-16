from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas.schemas import AllocationTarget, AssetSnapshotPoint, DashboardSummary
from app.services.fx_rate import get_latest_usd_cny_rate
from app.services.config import get_config_map, save_config
from app.services.dca_amount_schedule import ensure_plan_schedule_updates, merge_dca_schedules_on_save
from app.services.allocation_targets import validate_account_index_targets
from app.services.returns import build_asset_history, compute_dashboard_metrics

router = APIRouter(tags=["dashboard"])


def _optional_date(config: dict, key: str):
    value = config.get(key)
    if not value:
        return None
    return date.fromisoformat(str(value))


def _optional_float(config: dict, key: str):
    value = config.get(key)
    if value in (None, ""):
        return None
    return float(value)


def _optional_int(config: dict, key: str):
    value = config.get(key)
    if value in (None, ""):
        return None
    return int(float(value))


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
        building_target_amount=_optional_float(config, "building_target_amount"),
        dca_monthly_amount=float(config.get("dca_monthly_amount", 10000)),
        weeks_per_month=int(config.get("weeks_per_month", 4)),
        plan_horizon_years=int(config.get("plan_horizon_years", 20)),
        mainland_nasdaq_code=config.get("mainland_nasdaq_code", "513100"),
        mainland_sp500_code=config.get("mainland_sp500_code", "513500"),
        hk_nasdaq_code=config.get("hk_nasdaq_code", "QQQM"),
        hk_sp500_code=config.get("hk_sp500_code", "VOO"),
        forecast_years=int(config.get("forecast_years", 20)),
        forecast_return_pessimistic=float(config.get("forecast_return_pessimistic", 4)),
        forecast_return_neutral=float(config.get("forecast_return_neutral", 8)),
        forecast_return_optimistic=float(config.get("forecast_return_optimistic", 12)),
        forecast_inflation_pct=float(config.get("forecast_inflation_pct", 2)),
        forecast_mc_volatility=float(config.get("forecast_mc_volatility", 15)),
        forecast_mc_paths=int(float(config.get("forecast_mc_paths", 500))),
        sync_enabled=str(config.get("sync_enabled", "0")) in ("1", "true", "True"),
        sync_interval_hours=int(float(config.get("sync_interval_hours", 24))),
        plan_rebalance_enabled=str(config.get("plan_rebalance_enabled", "1")) in ("1", "true", "True"),
        plan_rebalance_threshold=float(config.get("plan_rebalance_threshold", 5)),
        dca_boost_enabled=str(config.get("dca_boost_enabled", "1")) in ("1", "true", "True"),
        dca_boost_20_pct_amount=float(config.get("dca_boost_20_pct_amount", 10000)),
        dca_boost_30_pct_amount=float(config.get("dca_boost_30_pct_amount", 20000)),
        dca_boost_40_pct_amount=float(config.get("dca_boost_40_pct_amount", 30000)),
        dca_boost_monthly_cap=float(config.get("dca_boost_monthly_cap", 30000)),
        dca_boost_cash_available=float(config.get("dca_boost_cash_available", 0)),
        dca_boost_lookback_days=int(float(config.get("dca_boost_lookback_days", 365))),
        dca_ma_enabled=str(config.get("dca_ma_enabled", "0")) in ("1", "true", "True"),
        dca_ma_window_days=int(float(config.get("dca_ma_window_days", 200))),
        dca_ma_min_factor=float(config.get("dca_ma_min_factor", 0.7)),
        dca_ma_max_factor=float(config.get("dca_ma_max_factor", 1.3)),
        dca_ma_band_pct=float(config.get("dca_ma_band_pct", 20)),
        dca_ma_center_pct=float(config.get("dca_ma_center_pct", 8)),
        hk_whole_share_only=str(config.get("hk_whole_share_only", "1")) in ("1", "true", "True"),
        hk_share_price_buffer_pct=float(config.get("hk_share_price_buffer_pct", 2)),
        mainland_nasdaq=_optional_float(config, "mainland_nasdaq"),
        mainland_sp500=_optional_float(config, "mainland_sp500"),
        hk_nasdaq=_optional_float(config, "hk_nasdaq"),
        hk_sp500=_optional_float(config, "hk_sp500"),
    )


def _prepare_config(config: dict, db: Session) -> dict:
    updates = ensure_plan_schedule_updates(config)
    if updates:
        save_config(db, updates)
        config = get_config_map(db)
    return config


DEPRECATED_ACCOUNT_PLAN_KEYS = (
    "mainland_plan_start_date",
    "hk_plan_start_date",
    "mainland_building_first_month_amount",
    "mainland_building_monthly_amount",
    "mainland_building_months",
    "mainland_building_target_amount",
    "mainland_dca_monthly_amount",
    "mainland_weeks_per_month",
    "hk_building_first_month_amount",
    "hk_building_monthly_amount",
    "hk_building_months",
    "hk_building_target_amount",
    "hk_dca_monthly_amount",
    "hk_weeks_per_month",
)


def _schema_to_config(payload: AllocationTarget) -> dict:
    data = payload.model_dump()
    date_fields = ("plan_start_date",)
    write_only_dates = ("dca_effective_from",)
    for key in date_fields:
        value = data.get(key)
        if value:
            data[key] = value.isoformat()
        elif key in data:
            data[key] = ""
    for key in write_only_dates:
        data.pop(key, None)
    optional_fields = (
        "building_target_amount",
        "mainland_nasdaq",
        "mainland_sp500",
        "hk_nasdaq",
        "hk_sp500",
    )
    for key in optional_fields:
        if data.get(key) is None:
            data[key] = ""
    for key in DEPRECATED_ACCOUNT_PLAN_KEYS:
        data[key] = ""
    data["sync_enabled"] = "1" if data.get("sync_enabled") else "0"
    data["plan_rebalance_enabled"] = "1" if data.get("plan_rebalance_enabled") else "0"
    data["dca_boost_enabled"] = "1" if data.get("dca_boost_enabled") else "0"
    data["dca_ma_enabled"] = "1" if data.get("dca_ma_enabled") else "0"
    data["hk_whole_share_only"] = "1" if data.get("hk_whole_share_only") else "0"
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
    config = _prepare_config(config, db)
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

    validate_account_index_targets(payload.model_dump())

    old_config = get_config_map(db)
    old_config = _prepare_config(old_config, db)
    effective_from = payload.dca_effective_from or date.today()
    data = _schema_to_config(payload)
    schedule_updates = merge_dca_schedules_on_save(old_config, data, effective_from)
    data.update(schedule_updates)
    save_config(db, data)
    return _config_to_schema(get_config_map(db))
