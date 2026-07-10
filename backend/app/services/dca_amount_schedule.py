from __future__ import annotations

from datetime import date
from typing import Optional

from app.services.account_plan import resolve_unified_plan_settings
from app.services.dca_schedule_core import (
    append_schedule_entry,
    default_schedule_entry,
    parse_schedule,
    serialize_schedule,
)


def ensure_plan_schedule_updates(config: dict) -> dict[str, str]:
    """确保统一定投档位存在。"""
    if config.get("dca_amount_schedule"):
        return {}
    unified = resolve_unified_plan_settings(config)
    entry = default_schedule_entry(
        unified["dca_monthly_amount"],
        unified["plan_start"],
    )
    return {"dca_amount_schedule": serialize_schedule([entry])}


def ensure_account_schedule_updates(config: dict) -> dict[str, str]:
    """兼容旧调用名。"""
    return ensure_plan_schedule_updates(config)


def schedule_for_plan(config: dict) -> list[dict]:
    unified = resolve_unified_plan_settings(config)
    return unified["dca_amount_schedule"]


def _amount_changed(old: Optional[float], new: Optional[float], tol: float = 0.01) -> bool:
    if old is None and new is None:
        return False
    if old is None or new is None:
        return True
    return abs(float(old) - float(new)) > tol


def merge_dca_schedules_on_save(
    old_config: dict,
    new_data: dict,
    effective_from: Optional[date],
) -> dict[str, str]:
    """定投月额变更时追加统一定投档位。"""
    updates: dict[str, str] = {}
    base_config = {**old_config, **new_data}
    bootstrap = ensure_plan_schedule_updates(base_config)
    base_config.update(bootstrap)

    schedule = parse_schedule(base_config.get("dca_amount_schedule"))
    if not schedule:
        schedule = parse_schedule(bootstrap.get("dca_amount_schedule"))
    if not schedule:
        unified = resolve_unified_plan_settings(base_config)
        schedule = [default_schedule_entry(unified["dca_monthly_amount"], unified["plan_start"])]

    old_amount = float(old_config.get("dca_monthly_amount") or resolve_unified_plan_settings(old_config)["dca_monthly_amount"])
    new_amount = float(new_data.get("dca_monthly_amount") or resolve_unified_plan_settings(base_config)["dca_monthly_amount"])

    if not _amount_changed(old_amount, new_amount):
        updates["dca_amount_schedule"] = serialize_schedule(schedule)
    else:
        eff = effective_from or date.today()
        schedule = append_schedule_entry(schedule, new_amount, eff)
        updates["dca_amount_schedule"] = serialize_schedule(schedule)

    # 清除旧版分账户 schedule，避免混淆
    updates["mainland_dca_amount_schedule"] = ""
    updates["hk_dca_amount_schedule"] = ""
    return {**bootstrap, **updates}
