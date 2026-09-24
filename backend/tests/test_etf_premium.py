from datetime import date
from unittest.mock import patch

import pytest
from app.database import Base, SessionLocal, engine
from app.models.models import Account, Instrument, PriceSnapshot
from app.services.config import seed_database
from app.services.holdings import get_latest_quotes
from app.services.market_data import QuoteResult, _etf_valuation, apply_previous_iopv, is_cn_etf
from app.services.price_refresh import refresh_all_prices


@pytest.fixture
def db():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with SessionLocal() as session:
        seed_database(session)
        session.commit()
        yield session


def test_is_cn_etf_matches_fund_prefixes_only():
    assert is_cn_etf("159915") is True
    assert is_cn_etf("512890") is True
    assert is_cn_etf("518880") is True
    assert is_cn_etf("563360") is True
    assert is_cn_etf("588000") is True
    assert is_cn_etf("sh513100") is True
    assert is_cn_etf("000001") is False
    assert is_cn_etf("600036") is False
    assert is_cn_etf("601318") is False
    assert is_cn_etf("QQQM") is False


def test_stock_latest_quotes_drop_stored_premium(db):
    account = db.query(Account).filter_by(name="大陆").one()
    stock = Instrument(
        code="600036",
        name="招商银行",
        category="a_share",
        account_id=account.id,
        currency="CNY",
    )
    db.add(stock)
    db.flush()
    db.add(
        PriceSnapshot(
            instrument_id=stock.id,
            price=41,
            iopv=51.45,
            premium_rate=-20.31,
            snapshot_date=date(2026, 9, 22),
        )
    )
    db.commit()

    quote = get_latest_quotes(db)[stock.id]
    assert float(quote["price"]) == 41
    assert quote["iopv"] is None
    assert quote["premium_rate"] is None


def test_refresh_stock_clears_carried_premium(db):
    account = db.query(Account).filter_by(name="大陆").one()
    stock = Instrument(
        code="000001",
        name="平安银行",
        category="a_share",
        account_id=account.id,
        currency="CNY",
    )
    db.add(stock)
    db.flush()
    db.add(
        PriceSnapshot(
            instrument_id=stock.id,
            price=11.5,
            iopv=665515275,
            premium_rate=-100,
            snapshot_date=date.today(),
        )
    )
    db.commit()

    def fake_quote(item):
        if item.code == "000001":
            return QuoteResult(
                price=11.72,
                snapshot_date=date.today(),
                iopv=665515275,
                premium_rate=-100,
            )
        return QuoteResult(price=1.0, snapshot_date=date.today())

    with patch("app.services.price_refresh.fetch_instrument_price", side_effect=fake_quote):
        result = refresh_all_prices(db)

    item = next(row for row in result["items"] if row["instrument_code"] == "000001")
    assert item["success"] is True
    assert item["iopv"] is None
    assert item["premium_rate"] is None
    latest = (
        db.query(PriceSnapshot)
        .filter_by(instrument_id=stock.id)
        .order_by(PriceSnapshot.id.desc())
        .first()
    )
    assert float(latest.price) == 11.72
    assert latest.iopv is None
    assert latest.premium_rate is None


def test_etf_valuation_from_iopv():
    iopv, premium = _etf_valuation({"f441": 1.817, "f191": 0, "f402": 0}, 2.007)
    assert iopv == 1.817
    assert premium == 10.46


def test_etf_valuation_from_premium_field():
    iopv, premium = _etf_valuation({"f441": 0, "f186": 0, "f191": 10.46, "f402": 0}, 2.007)
    assert premium == 10.46
    assert iopv == round(2.007 / 1.1046, 6)


def test_etf_valuation_ignores_zero_placeholders():
    iopv, premium = _etf_valuation({"f441": 0, "f186": "-", "f191": 0, "f402": 0}, 2.012)
    assert iopv is None
    assert premium is None


def test_apply_previous_iopv_recomputes_with_new_price():
    quote = QuoteResult(price=2.012, snapshot_date=date(2026, 9, 17))
    filled = apply_previous_iopv(quote, 1.817)
    assert filled.iopv == 1.817
    assert filled.premium_rate == round((2.012 - 1.817) / 1.817 * 100, 2)


def test_apply_previous_iopv_keeps_live_premium():
    quote = QuoteResult(price=2.012, snapshot_date=date(2026, 9, 17), premium_rate=10.5)
    filled = apply_previous_iopv(quote, 1.817)
    assert filled.iopv is None
    assert filled.premium_rate == 10.5


def test_latest_quotes_reuse_same_day_iopv(db):
    instrument = db.query(Instrument).filter_by(code="513100").one()
    db.add(
        PriceSnapshot(
            instrument_id=instrument.id,
            price=2.007,
            iopv=1.817,
            premium_rate=10.46,
            snapshot_date=date(2026, 9, 17),
        )
    )
    db.add(
        PriceSnapshot(
            instrument_id=instrument.id,
            price=2.012,
            snapshot_date=date(2026, 9, 17),
        )
    )
    db.commit()

    quote = get_latest_quotes(db)[instrument.id]
    assert float(quote["price"]) == 2.012
    assert quote["iopv"] == 1.817
    assert quote["premium_rate"] == round((2.012 - 1.817) / 1.817 * 100, 2)


def test_refresh_carries_forward_missing_iopv(db):
    instrument = db.query(Instrument).filter_by(code="513100").one()
    db.add(
        PriceSnapshot(
            instrument_id=instrument.id,
            price=2.007,
            iopv=1.817,
            premium_rate=10.46,
            snapshot_date=date.today(),
        )
    )
    db.commit()

    def fake_quote(item):
        if item.code == "513100":
            return QuoteResult(price=2.012, snapshot_date=date.today())
        return QuoteResult(price=1.0, snapshot_date=date.today())

    with patch("app.services.price_refresh.fetch_instrument_price", side_effect=fake_quote):
        result = refresh_all_prices(db)

    item = next(row for row in result["items"] if row["instrument_code"] == "513100")
    assert item["success"] is True
    assert item["iopv"] == 1.817
    assert item["premium_rate"] == round((2.012 - 1.817) / 1.817 * 100, 2)

    latest = (
        db.query(PriceSnapshot)
        .filter_by(instrument_id=instrument.id)
        .order_by(PriceSnapshot.id.desc())
        .first()
    )
    assert float(latest.price) == 2.012
    assert float(latest.iopv) == 1.817
    assert float(latest.premium_rate) == item["premium_rate"]
