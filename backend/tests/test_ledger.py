import json
import os
import tempfile
from datetime import date
from pathlib import Path
from uuid import uuid4

os.environ["WEALTH_DATA_DIR"] = tempfile.mkdtemp(prefix="wealth-ledger-tests-")
os.environ["WEALTH_DISABLE_SYNC"] = "1"

import pytest
from fastapi.testclient import TestClient
from app.database import Base, SessionLocal, engine
from app.main import app
from app.models.models import Account, Instrument, StrategyAccount, Transaction
from app.services.strategy_account import import_opening
from app.services.config import seed_database
from app.services.holdings import build_holdings
from app.services.returns import compute_cashflows, _net_investment_cny_as_of
from dividend_grid.portfolio import read_account

OPENING = dict(as_of="2026-01-01", cash=10000, positions={"sh600036": 100, "sh512890": 0},
               etf_layers=0, external_assets={}, etf_industry_weights={})


@pytest.fixture
def client():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        seed_database(db)
        account_id = import_opening(db, OPENING, {"sh600036": 40})
        db.commit()
    with TestClient(app) as api:
        api.account_id = account_id
        holdings = api.get(f"/api/strategy/accounts/{account_id}").json()["holdings"]
        api.ids = {row["code"]: row["instrument_id"] for row in holdings}
        yield api


def buy(api, **changes):
    payload = dict(trade_date="2026-01-02", account_id=api.account_id,
                   instrument_id=api.ids["sh600036"], side="buy", quantity=100, price=40,
                   fee=5, exchange_rate=1, request_id=str(uuid4()))
    payload.update(changes)
    return api.post("/api/transactions", json=payload)


def summary(api):
    response = api.get(f"/api/strategy/accounts/{api.account_id}")
    assert response.status_code == 200, response.text
    return response.json()


def event(api, **changes):
    payload = dict(event_date="2026-01-02", kind="deposit", amount=1000, request_id=str(uuid4()))
    payload.update(changes)
    return api.post(f"/api/strategy/accounts/{api.account_id}/cash-events", json=payload)


def test_opening_is_not_a_fake_trade(client):
    s = summary(client)
    assert s["cash"] == 10000 and s["equity"] == 14000 and s["pnl_since_opening"] == 0
    assert client.get("/api/transactions").json() == []
    with SessionLocal() as db:
        assert sum(h["market_value_cny"] for h in build_holdings(db)) == 14000
        assert compute_cashflows(db) == ([date(2026, 1, 1)], [-14000])
        assert _net_investment_cny_as_of(db, date(2026, 1, 1)) == 14000


def test_buy_sell_and_edit_rebuild_cash(client):
    tx = buy(client)
    assert tx.status_code == 200, tx.text
    s = summary(client)
    assert s["cash"] == 5995 and s["equity"] == 13995 and s["pnl_since_opening"] == -5
    assert buy(client, side="sell", quantity=50, fee=3, trade_date="2026-01-03").status_code == 200
    assert summary(client)["cash"] == 7992
    assert client.put(f'/api/transactions/{tx.json()["id"]}', json={"fee": 6}).status_code == 200
    assert summary(client)["cash"] == 7991
    with SessionLocal() as db:
        assert compute_cashflows(db)[1] == [-14000]


@pytest.mark.parametrize("changes", [dict(quantity=100000), dict(side="sell", quantity=101),
    dict(trade_date="2025-12-31"), dict(trade_date="2099-01-01"), dict(quantity=1.5), dict(price=0)])
def test_invalid_trade_is_atomic(client, changes):
    assert buy(client, **changes).status_code == 400
    assert summary(client)["cash"] == 10000
    assert client.get("/api/transactions").json() == []


def test_retroactive_sell_and_delete_dependency(client):
    tx = buy(client, trade_date="2026-01-03").json()
    assert buy(client, trade_date="2026-01-02", side="sell", quantity=200).status_code == 400
    assert buy(client, trade_date="2026-01-04", side="sell", quantity=200).status_code == 200
    assert client.delete(f'/api/transactions/{tx["id"]}').status_code == 400
    assert len(client.get("/api/transactions").json()) == 2


def test_idempotency_and_null_fields(client):
    request_id = str(uuid4())
    tx = buy(client, request_id=request_id)
    assert buy(client, request_id=request_id).json()["id"] == tx.json()["id"]
    assert buy(client, request_id=request_id, price=41).status_code == 409
    assert client.put(f'/api/transactions/{tx.json()["id"]}', json={"quantity": None}).status_code == 422
    assert len(client.get("/api/transactions").json()) == 1


def test_account_instrument_mismatch(client):
    accounts = client.get("/api/accounts").json()
    another = next(a["id"] for a in accounts if a["id"] != client.account_id)
    assert buy(client, account_id=another).status_code == 400


def test_etf_layers(client):
    instrument_id = client.ids["sh512890"]
    assert buy(client, instrument_id=instrument_id, price=1).status_code == 400
    assert buy(client, instrument_id=instrument_id, price=1, etf_layers_after=4).status_code == 200
    assert summary(client)["etf_layers"] == 4
    assert buy(client, instrument_id=instrument_id, price=1, side="sell", etf_layers_after=2).status_code == 400
    assert buy(client, instrument_id=instrument_id, price=1, side="sell", etf_layers_after=0).status_code == 200
    assert summary(client)["etf_layers"] == 0


def test_cash_events_and_return_basis(client):
    assert event(client).status_code == 200
    assert summary(client)["pnl_since_opening"] == 0
    assert event(client, kind="dividend", amount=100, instrument_id=client.ids["sh600036"]).status_code == 200
    assert summary(client)["pnl_since_opening"] == 100
    assert event(client, kind="fee", amount=20).status_code == 200
    assert event(client, kind="withdrawal", amount=20000).status_code == 400
    assert summary(client)["cash"] == 11080
    with SessionLocal() as db:
        assert sum(compute_cashflows(db)[1]) == -15000
        from app.services.returns import compute_dashboard_metrics
        from app.services.config import get_config_map
        assert compute_dashboard_metrics(db, get_config_map(db))['realized_pnl_cny'] == 80


def test_cash_dependency_and_shares(client):
    deposit = event(client, amount=20000).json()
    assert buy(client, quantity=500, trade_date="2026-01-03").status_code == 200
    assert client.delete(f'/api/strategy/accounts/{client.account_id}/cash-events/{deposit["id"]}').status_code == 400
    assert event(client, kind="shares", amount=0, quantity=100, instrument_id=client.ids["sh600036"], event_date="2026-01-04").status_code == 200
    assert next(h for h in summary(client)["holdings"] if h["code"] == "sh600036")["quantity"] == 700


def test_protect_opening_instruments(client):
    instrument_id = client.ids["sh600036"]
    assert client.delete(f"/api/instruments/{instrument_id}").status_code == 400
    assert client.put(f"/api/instruments/{instrument_id}", json={"is_active": False}).status_code == 400


def test_live_sqlite_adapter(client, tmp_path):
    pointer = tmp_path / "account.json"
    pointer.write_text(json.dumps({"wealth_ledger": {"database": str(Path(os.environ["WEALTH_DATA_DIR"]) / "wealth.db"), "account_id": client.account_id}}))
    assert read_account(pointer)["cash"] == 10000
    buy(client)
    assert read_account(pointer)["cash"] == 5995


def test_dashboard_and_legacy_cashflows(client):
    from app.models.models import PriceSnapshot
    with SessionLocal() as db:
        account = db.query(Account).filter_by(name="香港").one()
        instrument = db.query(Instrument).filter_by(account_id=account.id).first()
        db.add(Transaction(account_id=account.id, instrument_id=instrument.id, trade_date=date(2026, 1, 2),
            side="buy", quantity=2, price=100, amount=200, fee=1, exchange_rate=7))
        db.add(PriceSnapshot(instrument_id=instrument.id, price=100, snapshot_date=date(2026, 1, 2)))
        db.commit()
        assert sum(compute_cashflows(db)[1]) == -15407
    response = client.get("/api/dashboard")
    assert response.status_code == 200, response.text
    dashboard = response.json()
    assert dashboard["total_assets_cny"] == 1440
    assert all(item["category"] in {"nasdaq", "sp500"} for item in dashboard["holdings"])
    assert all(item["account_name"] != "红利策略" for item in dashboard["holdings"])
    assert client.get("/api/dashboard?scope=all").json()["total_assets_cny"] > 14000


def test_concurrent_buys_cannot_spend_same_cash(client):
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: buy(client, quantity=150).status_code, range(2)))
    assert sorted(results) == [200, 400]
    assert summary(client)["cash"] == 3995


def test_new_passive_instrument_is_included_in_ledger(client):
    response = client.post('/api/instruments', json=dict(account_id=client.account_id, code='159915',
        name='创业板ETF', category='a_share', currency='CNY'))
    assert response.status_code == 200, response.text
    assert buy(client, instrument_id=response.json()['id'], price=3).status_code == 200
    s = summary(client)
    assert s['cash'] == 9695 and s['equity'] is None
    assert '缺少有效估值' in s['valuation_error']


def test_external_web_pages_cannot_write(client):
    response = client.post('/api/transactions', headers={'Origin': 'https://other.example'}, json={})
    assert response.status_code == 403


def test_recorded_etf_sale_freezes_buying_for_week(client):
    from dividend_grid.rsi6 import Rsi6State, decide, week_id
    instrument_id = client.ids['sh512890']
    assert buy(client, instrument_id=instrument_id, price=1, etf_layers_after=4, trade_date=date.today().isoformat()).status_code == 200
    assert buy(client, instrument_id=instrument_id, price=1, side='sell', quantity=10, etf_layers_after=4, trade_date=date.today().isoformat()).status_code == 200
    with SessionLocal() as db:
        raw = json.loads(db.get(StrategyAccount, client.account_id).current_json)
    decision, _ = decide(19, 19, 4, Rsi6State.from_dict(raw['rsi_state']), week_id(date.today()))
    assert decision.action == 'FROZEN' and decision.frozen
