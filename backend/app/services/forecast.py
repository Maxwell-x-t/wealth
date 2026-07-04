from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Dict, Optional

from sqlalchemy.orm import Session

from app.services.investment_plan import generate_investment_plans
from app.services.returns import compute_dashboard_metrics


SCENARIO_META = (
    ("pessimistic", "悲观"),
    ("neutral", "中性"),
    ("optimistic", "乐观"),
)


def _annual_contribution_fallback(config: dict) -> float:
    return float(config.get("dca_monthly_amount", 10000)) * 12


def _build_contribution_by_year(
    db: Session,
    config: dict,
    start: date,
    years: int,
) -> Dict[int, float]:
    end = date(start.year + years - 1, 12, 31)
    plans = generate_investment_plans(db, config, start=start, end=end)
    by_year: Dict[int, float] = defaultdict(float)

    for item in plans:
        if item["status"] in ("merged", "done", "partial"):
            continue
        if item["plan_date"] < start:
            continue
        by_year[item["plan_date"].year] += float(item["amount_cny"])

    fallback = _annual_contribution_fallback(config)
    for year in range(start.year, start.year + years):
        if year not in by_year:
            by_year[year] = fallback

    return dict(sorted(by_year.items()))


def _project_scenario(
    start_assets: float,
    start_principal: float,
    start_year: int,
    years: int,
    annual_return_pct: float,
    contribution_by_year: Dict[int, float],
) -> dict:
    rate = annual_return_pct / 100.0
    assets = start_assets
    principal = start_principal
    points = [
        {
            "year_offset": 0,
            "year": start_year,
            "year_label": "现在",
            "assets_cny": round(assets, 2),
            "principal_cny": round(principal, 2),
            "profit_cny": round(assets - principal, 2),
            "return_rate": round((assets - principal) / principal * 100, 2) if principal > 0 else None,
        }
    ]

    for offset in range(1, years + 1):
        contribution_year = start_year + offset - 1
        contribution = float(contribution_by_year.get(contribution_year, 0.0))
        assets = (assets + contribution) * (1 + rate)
        principal = principal + contribution
        profit = assets - principal
        points.append(
            {
                "year_offset": offset,
                "year": contribution_year,
                "year_label": f"{contribution_year}年末",
                "assets_cny": round(assets, 2),
                "principal_cny": round(principal, 2),
                "profit_cny": round(profit, 2),
                "return_rate": round(profit / principal * 100, 2) if principal > 0 else None,
            }
        )

    final = points[-1]
    return {
        "annual_return_pct": annual_return_pct,
        "points": points,
        "final_assets_cny": final["assets_cny"],
        "final_principal_cny": final["principal_cny"],
        "final_profit_cny": final["profit_cny"],
        "final_return_rate": final["return_rate"],
    }


def build_wealth_forecast(
    db: Session,
    config: dict,
    years: Optional[int] = None,
    pessimistic: Optional[float] = None,
    neutral: Optional[float] = None,
    optimistic: Optional[float] = None,
) -> dict:
    years = int(years if years is not None else config.get("forecast_years", 20))
    years = max(1, min(years, 40))

    rates = {
        "pessimistic": float(
            pessimistic if pessimistic is not None else config.get("forecast_return_pessimistic", 4)
        ),
        "neutral": float(neutral if neutral is not None else config.get("forecast_return_neutral", 8)),
        "optimistic": float(
            optimistic if optimistic is not None else config.get("forecast_return_optimistic", 12)
        ),
    }

    metrics = compute_dashboard_metrics(db, config)
    start_assets = float(metrics["total_assets_cny"])
    start_principal = float(metrics["net_investment_cny"])
    today = date.today()

    contribution_by_year = _build_contribution_by_year(db, config, today, years)
    scenarios = []
    for key, label in SCENARIO_META:
        projected = _project_scenario(
            start_assets,
            start_principal,
            today.year,
            years,
            rates[key],
            contribution_by_year,
        )
        scenarios.append({"key": key, "label": label, **projected})

    return {
        "current_assets_cny": round(start_assets, 2),
        "current_net_investment_cny": round(start_principal, 2),
        "years": years,
        "rates": rates,
        "contribution_by_year": [
            {"year": year, "amount_cny": round(amount, 2)}
            for year, amount in contribution_by_year.items()
        ],
        "scenarios": scenarios,
    }
