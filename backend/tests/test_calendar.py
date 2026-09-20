from datetime import date

from app.services.calendar import is_trading_day


def test_cn_weekends_are_not_trading_days_even_on_makeup_workdays():
    assert is_trading_day(date(2026, 9, 19), "cn") is False
    assert is_trading_day(date(2026, 9, 20), "cn") is False
    assert is_trading_day(date(2026, 10, 10), "cn") is False


def test_cn_2026_national_day_holidays_are_skipped():
    assert is_trading_day(date(2026, 9, 30), "cn") is True
    assert is_trading_day(date(2026, 10, 1), "cn") is False
    assert is_trading_day(date(2026, 10, 7), "cn") is False
    assert is_trading_day(date(2026, 10, 8), "cn") is True
    assert is_trading_day(date(2026, 10, 9), "cn") is True
