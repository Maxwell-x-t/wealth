from __future__ import annotations

from datetime import date, timedelta
from typing import List

import chinese_calendar
import holidays

US_HOLIDAYS = holidays.US()


def is_trading_day(day: date, market: str) -> bool:
    if day.weekday() >= 5:
        return False
    if market == "cn":
        try:
            return chinese_calendar.is_workday(day)
        except NotImplementedError:
            return day.weekday() < 5
    if market == "us":
        return day not in US_HOLIDAYS
    return True


def adjust_to_trading_day(day: date, market: str) -> date:
    current = day
    while not is_trading_day(current, market):
        current -= timedelta(days=1)
    return current


def scheduled_weekly_days_in_month(year: int, month: int, market: str) -> List[date]:
    import calendar as cal

    first_day = date(year, month, 1)
    last_day = date(year, month, cal.monthrange(year, month)[1])
    weeks: dict[tuple[int, int], List[date]] = {}

    current = first_day
    while current <= last_day:
        key = current.isocalendar()[:2]
        weeks.setdefault(key, []).append(current)
        current += timedelta(days=1)

    scheduled: List[date] = []
    for days in weeks.values():
        candidate = max(days)
        while candidate >= min(days):
            if is_trading_day(candidate, market):
                scheduled.append(candidate)
                break
            candidate -= timedelta(days=1)

    return sorted(scheduled)
