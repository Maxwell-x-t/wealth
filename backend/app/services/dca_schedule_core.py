from __future__ import annotations

import json
from datetime import date
from typing import Optional


def _month_key(year: int, month: int) -> tuple[int, int]:
    return year, month


def _entry_month(entry: dict) -> tuple[int, int]:
    eff = date.fromisoformat(str(entry["effective_from"]))
    return eff.year, eff.month


def parse_schedule(raw: Optional[str]) -> list[dict]:
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    schedule: list[dict] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        amount = item.get("amount")
        effective_from = item.get("effective_from")
        if amount is None or not effective_from:
            continue
        schedule.append(
            {
                "amount": round(float(amount), 4),
                "effective_from": str(effective_from),
            }
        )
    schedule.sort(key=lambda row: row["effective_from"])
    return schedule


def serialize_schedule(schedule: list[dict]) -> str:
    return json.dumps(schedule, ensure_ascii=False)


def default_schedule_entry(amount: float, effective_from: date) -> dict:
    return {"amount": round(float(amount), 4), "effective_from": effective_from.isoformat()}


def dca_amount_for_month(schedule: list[dict], year: int, month: int) -> float:
    if not schedule:
        return 0.0
    target = _month_key(year, month)
    amount = float(schedule[0]["amount"])
    for entry in schedule:
        if _entry_month(entry) <= target:
            amount = float(entry["amount"])
        else:
            break
    return amount


def latest_dca_amount(schedule: list[dict]) -> float:
    if not schedule:
        return 0.0
    return float(schedule[-1]["amount"])


def append_schedule_entry(
    schedule: list[dict],
    amount: float,
    effective_from: date,
) -> list[dict]:
    eff_month = _month_key(effective_from.year, effective_from.month)
    kept = [entry for entry in schedule if _entry_month(entry) < eff_month]
    same_month = [entry for entry in schedule if _entry_month(entry) == eff_month]
    if same_month:
        kept.extend(same_month[:-1])
    kept.append(default_schedule_entry(amount, effective_from))
    kept.sort(key=lambda row: row["effective_from"])
    return kept
