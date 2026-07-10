from __future__ import annotations

from typing import List, Optional

from sqlalchemy.orm import Session

from app.services.forecast import _annual_contribution_fallback, _build_contribution_by_year
from app.services.returns import compute_dashboard_metrics

# 历史熊市参考跌幅（示意，非精确回测）
HISTORICAL_CRASHES = (
    {"key": "2008", "label": "2008 金融危机", "drawdown_pct": 50},
    {"key": "2020", "label": "2020 疫情冲击", "drawdown_pct": 30},
    {"key": "2022", "label": "2022 熊市", "drawdown_pct": 25},
)

DEFAULT_DRAWDOWNS = (30, 40, 50)


def _estimate_annual_contribution(db: Session, config: dict) -> float:
    from datetime import date

    today = date.today()
    by_year = _build_contribution_by_year(db, config, today, years=1)
    if by_year:
        first = next(iter(by_year.values()))
        if isinstance(first, dict):
            return float(first.get("total", 0.0))
        return float(first)
    return _annual_contribution_fallback(config)


def _project_after_crash(
    start_assets: float,
    annual_contribution: float,
    annual_return_pct: float,
    years: int,
) -> List[dict]:
    rate = annual_return_pct / 100.0
    assets = start_assets
    points = [{"year_offset": 0, "assets_cny": round(assets, 2)}]
    for offset in range(1, years + 1):
        assets = (assets + annual_contribution) * (1 + rate)
        points.append({"year_offset": offset, "assets_cny": round(assets, 2)})
    return points


def _recovery_years(
    after_crash: float,
    target_assets: float,
    annual_contribution: float,
    annual_return_pct: float,
    max_years: int = 40,
) -> Optional[int]:
    if after_crash >= target_assets:
        return 0
    rate = annual_return_pct / 100.0
    assets = after_crash
    for year in range(1, max_years + 1):
        assets = (assets + annual_contribution) * (1 + rate)
        if assets >= target_assets:
            return year
    return None


def _build_scenario(
    label: str,
    key: str,
    drawdown_pct: float,
    current_assets: float,
    current_principal: float,
    annual_contribution: float,
    recovery_return_pct: float,
    horizon_years: int,
) -> dict:
    drawdown = max(0.0, min(float(drawdown_pct), 99.0)) / 100.0
    after_crash = current_assets * (1 - drawdown)
    loss = current_assets - after_crash
    recovery = _recovery_years(after_crash, current_assets, annual_contribution, recovery_return_pct)
    points = _project_after_crash(after_crash, annual_contribution, recovery_return_pct, horizon_years)
    final_assets = points[-1]["assets_cny"]
    # 假设崩盘后本金不变（未卖出），继续定投累加本金
    final_principal = current_principal + annual_contribution * horizon_years
    final_profit = final_assets - final_principal

    return {
        "key": key,
        "label": label,
        "drawdown_pct": round(drawdown * 100, 2),
        "assets_after_crash_cny": round(after_crash, 2),
        "loss_cny": round(loss, 2),
        "recovery_years": recovery,
        "recovery_return_pct": recovery_return_pct,
        "horizon_years": horizon_years,
        "final_assets_cny": final_assets,
        "final_principal_cny": round(final_principal, 2),
        "final_profit_cny": round(final_profit, 2),
        "final_return_rate": round(final_profit / final_principal * 100, 2) if final_principal > 0 else None,
        "points": points,
    }


def build_risk_simulation(
    db: Session,
    config: dict,
    drawdowns: Optional[List[float]] = None,
    recovery_return_pct: Optional[float] = None,
    horizon_years: Optional[int] = None,
) -> dict:
    metrics = compute_dashboard_metrics(db, config)
    current_assets = float(metrics["total_assets_cny"])
    current_principal = float(metrics["net_investment_cny"])
    annual_contribution = _estimate_annual_contribution(db, config)

    recovery_return_pct = float(
        recovery_return_pct
        if recovery_return_pct is not None
        else config.get("forecast_return_neutral", 8)
    )
    horizon_years = int(horizon_years if horizon_years is not None else config.get("forecast_years", 20))
    horizon_years = max(1, min(horizon_years, 40))

    drawdown_list = drawdowns if drawdowns else list(DEFAULT_DRAWDOWNS)
    custom_scenarios = [
        _build_scenario(
            label=f"跌幅 {int(d)}%",
            key=f"dd_{int(d)}",
            drawdown_pct=d,
            current_assets=current_assets,
            current_principal=current_principal,
            annual_contribution=annual_contribution,
            recovery_return_pct=recovery_return_pct,
            horizon_years=horizon_years,
        )
        for d in drawdown_list
    ]

    historical = [
        _build_scenario(
            label=item["label"],
            key=item["key"],
            drawdown_pct=item["drawdown_pct"],
            current_assets=current_assets,
            current_principal=current_principal,
            annual_contribution=annual_contribution,
            recovery_return_pct=recovery_return_pct,
            horizon_years=horizon_years,
        )
        for item in HISTORICAL_CRASHES
    ]

    return {
        "current_assets_cny": round(current_assets, 2),
        "current_net_investment_cny": round(current_principal, 2),
        "annual_contribution_cny": round(annual_contribution, 2),
        "recovery_return_pct": recovery_return_pct,
        "horizon_years": horizon_years,
        "custom_scenarios": custom_scenarios,
        "historical_scenarios": historical,
    }
