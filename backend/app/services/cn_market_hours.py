from __future__ import annotations

from datetime import date, datetime, time
from typing import Optional
from zoneinfo import ZoneInfo

from app.services.calendar import is_trading_day

CN_TZ = ZoneInfo("Asia/Shanghai")

MORNING_OPEN = time(9, 30)
MORNING_CLOSE = time(11, 30)
AFTERNOON_OPEN = time(13, 0)
AFTERNOON_CLOSE = time(15, 0)


def cn_market_now(now: Optional[datetime] = None) -> datetime:
    if now is None:
        return datetime.now(CN_TZ)
    if now.tzinfo is None:
        return now.replace(tzinfo=CN_TZ)
    return now.astimezone(CN_TZ)


def cn_etf_session(now: Optional[datetime] = None) -> dict:
    """大陆 ETF / A 股交易时段状态（北京时间）。"""
    now = cn_market_now(now)
    today = now.date()
    clock = now.time().replace(microsecond=0)

    if not is_trading_day(today, "cn"):
        return {
            "open": False,
            "session": "closed",
            "reason": "non_trading_day",
            "local_time": now.strftime("%Y-%m-%d %H:%M:%S"),
            "timezone": "Asia/Shanghai",
        }

    if MORNING_OPEN <= clock <= MORNING_CLOSE:
        session = "morning"
        open_flag = True
        reason = None
    elif AFTERNOON_OPEN <= clock <= AFTERNOON_CLOSE:
        session = "afternoon"
        open_flag = True
        reason = None
    elif clock < MORNING_OPEN:
        session = "pre_market"
        open_flag = False
        reason = "before_open"
    elif MORNING_CLOSE < clock < AFTERNOON_OPEN:
        session = "lunch_break"
        open_flag = False
        reason = "lunch_break"
    else:
        session = "after_hours"
        open_flag = False
        reason = "after_close"

    return {
        "open": open_flag,
        "session": session,
        "reason": reason,
        "local_time": now.strftime("%Y-%m-%d %H:%M:%S"),
        "timezone": "Asia/Shanghai",
        "trading_day": today.isoformat(),
    }
