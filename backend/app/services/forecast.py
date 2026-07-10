from __future__ import annotations

import random
from collections import defaultdict
from datetime import date
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from app.services.account_plan import resolve_all_account_settings
from app.services.dca_schedule_core import latest_dca_amount
from app.services.investment_plan import generate_investment_plans
from app.services.returns import compute_dashboard_metrics


SCENARIO_META = (
    ("pessimistic", "悲观"),
    ("neutral", "中性"),
    ("optimistic", "乐观"),
)


def _annual_contribution_fallback(config: dict) -> float:
    settings = resolve_all_account_settings(config)
    monthly = sum(latest_dca_amount(item["dca_amount_schedule"]) for item in settings.values())
    return monthly * 12


def _build_contribution_by_year(
    db: Session,
    config: dict,
    start: date,
    years: int,
) -> Dict[int, dict]:
    end = date(start.year + years - 1, 12, 31)
    plans = generate_investment_plans(db, config, start=start, end=end)
    by_year: Dict[int, dict] = {}

    for item in plans:
        if item["status"] in ("merged", "done", "partial"):
            continue
        if item["plan_date"] < start:
            continue
        year = item["plan_date"].year
        bucket = by_year.setdefault(year, {"total": 0.0, "building": 0.0, "dca": 0.0})
        amount = float(item["amount_cny"])
        bucket["total"] += amount
        if item["phase"] == "building":
            bucket["building"] += amount
        else:
            bucket["dca"] += amount

    fallback = _annual_contribution_fallback(config)
    for year in range(start.year, start.year + years):
        if year not in by_year:
            by_year[year] = {"total": fallback, "building": 0.0, "dca": fallback}

    return dict(sorted(by_year.items()))


def _contribution_year_totals(by_year: Dict[int, dict]) -> Dict[int, float]:
    return {year: values["total"] for year, values in by_year.items()}


def _deflate(nominal: float, inflation_rate: float, year_offset: int) -> float:
    if year_offset <= 0 or inflation_rate == 0:
        return nominal
    return nominal / ((1 + inflation_rate) ** year_offset)


def _percentile(sorted_values: List[float], pct: float) -> float:
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return sorted_values[0]
    rank = (len(sorted_values) - 1) * pct / 100.0
    low = int(rank)
    high = min(low + 1, len(sorted_values) - 1)
    weight = rank - low
    return sorted_values[low] * (1 - weight) + sorted_values[high] * weight


def _enrich_point_with_inflation(point: dict, inflation_rate: float) -> dict:
    offset = int(point["year_offset"])
    assets = float(point["assets_cny"])
    principal = float(point["principal_cny"])
    assets_real = _deflate(assets, inflation_rate, offset)
    principal_real = _deflate(principal, inflation_rate, offset)
    profit_real = assets_real - principal_real
    point["assets_real_cny"] = round(assets_real, 2)
    point["principal_real_cny"] = round(principal_real, 2)
    point["profit_real_cny"] = round(profit_real, 2)
    point["return_rate_real"] = (
        round(profit_real / principal_real * 100, 2) if principal_real > 0 else None
    )
    return point


def _project_scenario(
    start_assets: float,
    start_principal: float,
    start_year: int,
    years: int,
    annual_return_pct: float,
    contribution_by_year: Dict[int, float],
    inflation_rate: Optional[float] = None,
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

    if inflation_rate is not None:
        points = [_enrich_point_with_inflation(point, inflation_rate) for point in points]

    final = points[-1]
    result = {
        "annual_return_pct": annual_return_pct,
        "points": points,
        "final_assets_cny": final["assets_cny"],
        "final_principal_cny": final["principal_cny"],
        "final_profit_cny": final["profit_cny"],
        "final_return_rate": final["return_rate"],
    }
    if inflation_rate is not None:
        result["final_assets_real_cny"] = final["assets_real_cny"]
        result["final_principal_real_cny"] = final["principal_real_cny"]
        result["final_profit_real_cny"] = final["profit_real_cny"]
        result["final_return_rate_real"] = final["return_rate_real"]
    return result


def _run_monte_carlo(
    start_assets: float,
    start_principal: float,
    start_year: int,
    years: int,
    mean_return_pct: float,
    volatility_pct: float,
    contribution_by_year: Dict[int, float],
    paths: int,
    inflation_rate: Optional[float] = None,
    seed: int = 42,
) -> dict:
    paths = max(50, min(int(paths), 2000))
    mean = mean_return_pct / 100.0
    vol = max(0.0, volatility_pct / 100.0)
    rng = random.Random(seed)

    matrix: List[List[float]] = []
    final_assets_list: List[float] = []

    for _ in range(paths):
        assets = start_assets
        principal = start_principal
        series = [assets]
        for offset in range(1, years + 1):
            contribution_year = start_year + offset - 1
            contribution = float(contribution_by_year.get(contribution_year, 0.0))
            annual_return = rng.gauss(mean, vol)
            annual_return = max(annual_return, -0.95)
            assets = (assets + contribution) * (1 + annual_return)
            principal += contribution
            series.append(assets)
        matrix.append(series)
        final_assets_list.append(assets)

    points = []
    for offset in range(years + 1):
        values = sorted(row[offset] for row in matrix)
        p10 = _percentile(values, 10)
        p50 = _percentile(values, 50)
        p90 = _percentile(values, 90)
        point = {
            "year_offset": offset,
            "year": start_year if offset == 0 else start_year + offset - 1,
            "year_label": "现在" if offset == 0 else f"{start_year + offset - 1}年末",
            "p10_cny": round(p10, 2),
            "p50_cny": round(p50, 2),
            "p90_cny": round(p90, 2),
        }
        if inflation_rate is not None:
            point["p10_real_cny"] = round(_deflate(p10, inflation_rate, offset), 2)
            point["p50_real_cny"] = round(_deflate(p50, inflation_rate, offset), 2)
            point["p90_real_cny"] = round(_deflate(p90, inflation_rate, offset), 2)
        points.append(point)

    finals = sorted(final_assets_list)
    result = {
        "paths": paths,
        "mean_return_pct": mean_return_pct,
        "volatility_pct": volatility_pct,
        "points": points,
        "final_p10_cny": round(_percentile(finals, 10), 2),
        "final_p50_cny": round(_percentile(finals, 50), 2),
        "final_p90_cny": round(_percentile(finals, 90), 2),
    }
    if inflation_rate is not None:
        result["final_p10_real_cny"] = round(_deflate(result["final_p10_cny"], inflation_rate, years), 2)
        result["final_p50_real_cny"] = round(_deflate(result["final_p50_cny"], inflation_rate, years), 2)
        result["final_p90_real_cny"] = round(_deflate(result["final_p90_cny"], inflation_rate, years), 2)
    return result


def build_wealth_forecast(
    db: Session,
    config: dict,
    years: Optional[int] = None,
    pessimistic: Optional[float] = None,
    neutral: Optional[float] = None,
    optimistic: Optional[float] = None,
    use_inflation: bool = False,
    inflation_pct: Optional[float] = None,
    use_monte_carlo: bool = False,
    mc_volatility_pct: Optional[float] = None,
    mc_paths: Optional[int] = None,
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

    inflation_rate = None
    inflation_pct_value = float(
        inflation_pct if inflation_pct is not None else config.get("forecast_inflation_pct", 2)
    )
    if use_inflation:
        inflation_rate = max(0.0, inflation_pct_value) / 100.0

    metrics = compute_dashboard_metrics(db, config)
    start_assets = float(metrics["total_assets_cny"])
    start_principal = float(metrics["net_investment_cny"])
    today = date.today()

    contribution_detail = _build_contribution_by_year(db, config, today, years)
    contribution_by_year = _contribution_year_totals(contribution_detail)
    scenarios = []
    for key, label in SCENARIO_META:
        projected = _project_scenario(
            start_assets,
            start_principal,
            today.year,
            years,
            rates[key],
            contribution_by_year,
            inflation_rate=inflation_rate,
        )
        scenarios.append({"key": key, "label": label, **projected})

    result = {
        "current_assets_cny": round(start_assets, 2),
        "current_net_investment_cny": round(start_principal, 2),
        "years": years,
        "rates": rates,
        "use_inflation": use_inflation,
        "inflation_pct": inflation_pct_value if use_inflation else None,
        "use_monte_carlo": use_monte_carlo,
        "contribution_by_year": [
            {
                "year": year,
                "amount_cny": round(values["total"], 2),
                "building_cny": round(values["building"], 2),
                "dca_cny": round(values["dca"], 2),
            }
            for year, values in contribution_detail.items()
        ],
        "scenarios": scenarios,
        "monte_carlo": None,
    }

    if use_monte_carlo:
        volatility = float(
            mc_volatility_pct
            if mc_volatility_pct is not None
            else config.get("forecast_mc_volatility", 15)
        )
        paths = int(mc_paths if mc_paths is not None else config.get("forecast_mc_paths", 500))
        result["monte_carlo"] = _run_monte_carlo(
            start_assets,
            start_principal,
            today.year,
            years,
            rates["neutral"],
            volatility,
            contribution_by_year,
            paths,
            inflation_rate=inflation_rate,
        )

    return result
