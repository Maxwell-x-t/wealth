from datetime import date, timedelta

from app.services.investment_plan import (
    _apply_defers,
    _init_plan_fields,
    _match_hk_whole_share_group,
    _upcoming_plans,
    plan_skip_key,
)


def _plan(plan_date, amount, account="香港", category="nasdaq", phase="dca", status="pending"):
    item = {
        "plan_date": plan_date,
        "account": account,
        "category": category,
        "phase": phase,
        "amount_cny": amount,
        "base_amount_cny": amount,
        "status": status,
    }
    _init_plan_fields(item)
    item["status"] = status
    return item


def test_defer_moves_amount_to_next_open_plan():
    today = date.today()
    old = _plan(today - timedelta(days=40), 30, status="accumulating")
    older = _plan(today - timedelta(days=20), 20, status="accumulating")
    upcoming = _plan(today + timedelta(days=3), 50, status="pending")
    key = plan_skip_key(old["plan_date"], old["account"], old["category"], old["phase"])

    _apply_defers([old, older, upcoming], {key}, set(), today)

    assert old["status"] == "deferred"
    assert old["base_amount_cny"] == 30
    assert upcoming["rolled_over_amount_cny"] == 30
    assert upcoming["rolled_over_count"] == 1
    assert upcoming["amount_cny"] == 80
    assert older["rolled_over_amount_cny"] == 0


def test_upcoming_hides_old_hk_accumulating_and_keeps_latest_ready():
    today = date.today()
    old_pool = _plan(today - timedelta(days=60), 10, status="accumulating")
    old_pool["whole_share_mode"] = True
    latest_ready = _plan(today - timedelta(days=7), 10, status="ready")
    latest_ready["whole_share_mode"] = True
    earlier_ready = _plan(today - timedelta(days=14), 10, status="ready")
    earlier_ready["whole_share_mode"] = True
    future = _plan(today + timedelta(days=7), 10, status="pending")
    mainland = _plan(today + timedelta(days=1), 100, account="大陆", category="sp500", status="pending")

    upcoming = _upcoming_plans([old_pool, earlier_ready, latest_ready, future, mainland], today)

    assert old_pool not in upcoming
    assert earlier_ready not in upcoming
    assert latest_ready in upcoming
    assert future in upcoming
    assert mainland in upcoming


def _patch_hk_pool(monkeypatch, price):
    monkeypatch.setattr("app.services.investment_plan._reference_share_price_usd", lambda db, category: price)
    monkeypatch.setattr("app.services.investment_plan._share_price_buffer_pct", lambda config: 0)
    monkeypatch.setattr(
        "app.services.investment_plan._load_hk_group_transactions_usd",
        lambda *args, **kwargs: ([], {}, {}),
    )


def test_deferred_hk_amount_stays_in_current_pool(monkeypatch):
    today = date.today()
    earlier = _plan(today - timedelta(days=14), 180)
    deferred = _plan(today - timedelta(days=7), 30, status="deferred")
    future = _plan(today + timedelta(days=7), 30)
    future["rolled_over_amount_cny"] = 30
    future["rolled_over_count"] = 1
    future["amount_cny"] = 60
    _patch_hk_pool(monkeypatch, 200)

    _match_hk_whole_share_group([earlier, deferred, future], None, {}, 7.2)

    assert deferred["status"] == "deferred"
    assert earlier["status"] == "ready"
    assert earlier["execution_pool_usd"] == 210
    assert future["status"] == "pending"
    assert future["execution_pool_usd"] == 210
    assert future["rolled_over_amount_cny"] == 30
    assert future["amount_cny"] == 60


def test_due_hk_plan_does_not_add_deferred_amount_again(monkeypatch):
    today = date.today()
    deferred = _plan(today - timedelta(days=30), 40, status="deferred")
    target = _plan(today, 60, status="pending")
    target["rolled_over_amount_cny"] = 40
    target["rolled_over_count"] = 1
    target["amount_cny"] = 100
    _patch_hk_pool(monkeypatch, 200)

    _match_hk_whole_share_group([deferred, target], None, {}, 7.2)

    assert deferred["status"] == "deferred"
    assert target["execution_pool_usd"] == 100
    assert target["status"] == "accumulating"
    assert target["rolled_over_amount_cny"] == 40
