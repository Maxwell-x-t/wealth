from __future__ import annotations

from datetime import date, datetime
from typing import Dict, List, Optional

from app.services.calendar import scheduled_weekly_days_in_month
from app.services.dca_schedule_core import default_schedule_entry, parse_schedule

ACCOUNT_SPECS: List[dict] = [
    {"name": "大陆", "key": "mainland", "market": "cn"},
    {"name": "香港", "key": "hk", "market": "us"},
]


def _parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def _config_float(config: dict, key: str, default: float = 0.0) -> float:
    raw = config.get(key)
    if raw is None or raw == "":
        return default
    return float(raw)


def _config_int(config: dict, key: str, default: int) -> int:
    raw = config.get(key)
    if raw is None or raw == "":
        return default
    return int(float(raw))


def _optional_global_target(config: dict) -> Optional[float]:
    raw = config.get("building_target_amount")
    if raw not in (None, ""):
        value = float(raw)
        if value > 0:
            return value
    # 兼容旧版仅配置分账户建仓目标
    mainland_raw = config.get("mainland_building_target_amount")
    if mainland_raw not in (None, ""):
        mainland_target = float(mainland_raw)
        ratio = account_ratio(config, "mainland")
        if mainland_target > 0 and ratio > 0:
            return round(mainland_target / ratio, 2)
    return None


def account_ratio(config: dict, account_key: str) -> float:
    mainland_pct = _config_float(config, "mainland", 60)
    hk_pct = _config_float(config, "hk", 40)
    total_pct = mainland_pct + hk_pct
    if total_pct <= 0:
        return 0.5
    return (mainland_pct if account_key == "mainland" else hk_pct) / total_pct


def resolve_plan_dca_schedule(config: dict, plan_start: date) -> list[dict]:
    """统一定投档位；兼容旧版分账户 schedule。"""
    schedule = parse_schedule(config.get("dca_amount_schedule"))
    if schedule:
        return schedule

    global_amount = _config_float(config, "dca_monthly_amount", 10000)
    mainland_schedule = parse_schedule(config.get("mainland_dca_amount_schedule"))
    hk_schedule = parse_schedule(config.get("hk_dca_amount_schedule"))
    if mainland_schedule or hk_schedule:
        return [default_schedule_entry(global_amount, plan_start)]

    return [default_schedule_entry(global_amount, plan_start)]


def resolve_unified_plan_settings(config: dict) -> dict:
    """全局建仓/定投计划（人民币口径月额）。"""
    plan_start = _parse_date(str(config.get("plan_start_date") or date.today().isoformat()))
    dca_amount = _config_float(config, "dca_monthly_amount", 10000)
    dca_schedule = resolve_plan_dca_schedule(config, plan_start)

    return {
        "plan_start": plan_start,
        "building_months": _config_int(config, "building_months", 8),
        "building_first_month_amount": _config_float(config, "building_first_month_amount", 100000),
        "building_monthly_amount": _config_float(config, "building_monthly_amount", 50000),
        "building_target_amount": _optional_global_target(config),
        "dca_monthly_amount": dca_amount,
        "weeks_per_month": max(1, _config_int(config, "weeks_per_month", 4)),
        "dca_amount_schedule": dca_schedule,
    }


def _slice_plan_amount(config: dict, account_key: str, cny_amount: float) -> float:
    """按账户比例拆分统一计划金额；香港返回 USD。"""
    ratio = account_ratio(config, account_key)
    sliced = cny_amount * ratio
    if account_key == "hk":
        rate = _config_float(config, "usd_cny_rate", 7.2)
        return round(sliced / rate, 2) if rate > 0 else 0.0
    return round(sliced, 2)


def resolve_account_settings(config: dict, account_name: str) -> dict:
    """从统一计划按账户比例派生执行参数（同一起点、同一节奏）。"""
    spec = next(item for item in ACCOUNT_SPECS if item["name"] == account_name)
    key = spec["key"]
    unified = resolve_unified_plan_settings(config)
    plan_start = unified["plan_start"]

    building_target_cny = unified.get("building_target_amount")
    building_target = (
        _slice_plan_amount(config, key, building_target_cny)
        if building_target_cny
        else None
    )

    dca_schedule = unified["dca_amount_schedule"]

    return {
        "account": account_name,
        "key": key,
        "market": spec["market"],
        "plan_start": plan_start,
        "building_months": unified["building_months"],
        "building_first_month_amount": _slice_plan_amount(
            config, key, unified["building_first_month_amount"]
        ),
        "building_monthly_amount": _slice_plan_amount(
            config, key, unified["building_monthly_amount"]
        ),
        "dca_monthly_amount": _slice_plan_amount(config, key, unified["dca_monthly_amount"]),
        "weeks_per_month": unified["weeks_per_month"],
        "building_target_amount": building_target,
        "dca_amount_schedule": dca_schedule,
        "plan_amount_currency": "CNY" if key == "mainland" else "USD",
        "unified_dca_schedule": True,
    }


def dca_amount_for_account_month(
    config: dict,
    account_key: str,
    schedule: list[dict],
    year: int,
    month: int,
) -> float:
    """统一 schedule 为人民币月额，按账户比例换算为执行币种。"""
    from app.services.dca_schedule_core import dca_amount_for_month

    unified_cny = dca_amount_for_month(schedule, year, month)
    return _slice_plan_amount(config, account_key, unified_cny)


def resolve_all_account_settings(config: dict) -> Dict[str, dict]:
    return {spec["name"]: resolve_account_settings(config, spec["name"]) for spec in ACCOUNT_SPECS}


def week_days_for_month(year: int, month: int, market: str, weeks_per_month: int) -> List[date]:
    days = scheduled_weekly_days_in_month(year, month, market)
    if not days:
        return []
    limit = max(1, weeks_per_month)
    if limit >= len(days):
        return days
    if limit == 1:
        return [days[-1]]
    picked: List[date] = []
    for index in range(limit):
        pos = round(index * (len(days) - 1) / (limit - 1))
        picked.append(days[pos])
    return picked


def month_index_for_date(plan_start: date, target: date) -> int:
    return (target.year - plan_start.year) * 12 + (target.month - plan_start.month)


def building_last_month_index(settings: dict) -> int:
    """建仓阶段最后一个 month_index（含）；无建仓月时返回 -1。"""
    building_months = int(settings["building_months"])
    target = settings.get("building_target_amount")
    if not target or target <= 0:
        return building_months

    cumulative = 0.0
    first = float(settings["building_first_month_amount"])
    monthly = float(settings["building_monthly_amount"])

    for month_index in range(building_months + 1):
        amount = first if month_index <= 0 else monthly
        if cumulative >= target:
            return max(month_index - 1, -1)
        if cumulative + amount >= target:
            return month_index
        cumulative += amount
    return building_months


def phase_for_month_index(settings: dict, month_index: int) -> str:
    if month_index < 0:
        return "dca"
    if month_index <= building_last_month_index(settings):
        return "building"
    return "dca"


def building_amount_for_month(settings: dict, month_index: int) -> float:
    if phase_for_month_index(settings, month_index) != "building":
        return 0.0

    first = float(settings["building_first_month_amount"])
    monthly = float(settings["building_monthly_amount"])
    raw = first if month_index <= 0 else monthly
    target = settings.get("building_target_amount")
    if not target or target <= 0:
        return raw

    cumulative = 0.0
    for prior_index in range(month_index):
        prior_raw = first if prior_index <= 0 else monthly
        remaining = target - cumulative
        if remaining <= 0:
            break
        cumulative += min(prior_raw, remaining)

    remaining = target - cumulative
    if remaining <= 0:
        return 0.0
    return min(raw, remaining)


def phase_for_account_date(settings: dict, target: date) -> str:
    month_index = month_index_for_date(settings["plan_start"], target)
    return phase_for_month_index(settings, month_index)
