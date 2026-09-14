from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from app.database import engine
from app.models.models import StrategyAccount, Transaction
from app.services.account_plan import month_index_for_date, phase_for_month_index, resolve_account_settings
from app.services.holdings import convert_transaction_to_cny, _to_decimal

ACCOUNT_CURRENCY = {"大陆": "CNY", "香港": "USD"}
INDEX_CATEGORIES = {"nasdaq", "sp500"}


def infer_plan_phase(trade_date: date, config: dict, account_name: str = "大陆") -> str:
    """根据账户计划开始日、建仓月数与建仓目标总额推断交易所属阶段。"""
    settings = resolve_account_settings(config, account_name)
    month_index = month_index_for_date(settings["plan_start"], trade_date)
    return phase_for_month_index(settings, month_index)


def tx_effective_phase(tx: Transaction, config: dict) -> str:
    if tx.plan_phase in ("building", "dca"):
        return tx.plan_phase
    account_name = tx.account.name if tx.account else "大陆"
    return infer_plan_phase(tx.trade_date, config, account_name)


def tx_matches_phase(tx: Transaction, phase: str, config: dict) -> bool:
    if tx.side != "buy":
        return False
    return tx_effective_phase(tx, config) == phase


def tx_amount_cny(tx: Transaction, usd_cny_rate: float) -> float:
    instrument = tx.instrument
    currency = instrument.currency if instrument else "CNY"
    rate = float(tx.exchange_rate) if currency == "USD" else 1.0
    amount = float(convert_transaction_to_cny(_to_decimal(tx.amount), currency, _to_decimal(rate)))
    fee = float(convert_transaction_to_cny(_to_decimal(tx.fee), currency, _to_decimal(rate)))
    return amount + fee


def tx_amount_native(tx: Transaction) -> float:
    amount = _to_decimal(tx.amount) if tx.amount else _to_decimal(tx.quantity) * _to_decimal(tx.price)
    fee = _to_decimal(tx.fee)
    return float(amount + fee)


def compute_phase_investment(
    db: Session,
    config: dict,
    usd_cny_rate: float,
    *,
    scope: str = "all",
) -> dict:
    building_cny = 0.0
    dca_cny = 0.0
    other_cny = 0.0

    rows = db.query(Transaction).filter(Transaction.side == "buy").all()
    managed = {row.account_id for row in db.query(StrategyAccount)} if scope == "index" else set()
    for tx in rows:
        if scope == "index" and (
            tx.account_id in managed
            or not tx.instrument
            or tx.instrument.category not in INDEX_CATEGORIES
        ):
            continue
        amount = tx_amount_cny(tx, usd_cny_rate)
        phase = tx_effective_phase(tx, config)
        if phase == "building":
            building_cny += amount
        elif phase == "dca":
            dca_cny += amount
        else:
            other_cny += amount

    return {
        "building_invested_cny": round(building_cny, 2),
        "dca_invested_cny": round(dca_cny, 2),
        "other_invested_cny": round(other_cny, 2),
    }


def compute_account_phase_investment(
    db: Session,
    config: dict,
    account_name: str,
    usd_cny_rate: float,
) -> dict:
    building_cny = 0.0
    dca_cny = 0.0
    building_native = 0.0
    dca_native = 0.0
    rows = (
        db.query(Transaction)
        .filter(Transaction.side == "buy")
        .all()
    )
    for tx in rows:
        if not tx.account or tx.account.name != account_name:
            continue
        amount_cny = tx_amount_cny(tx, usd_cny_rate)
        amount_native = tx_amount_native(tx)
        phase = tx_effective_phase(tx, config)
        if phase == "building":
            building_cny += amount_cny
            building_native += amount_native
        elif phase == "dca":
            dca_cny += amount_cny
            dca_native += amount_native
    return {
        "currency": ACCOUNT_CURRENCY.get(account_name, "CNY"),
        "building_invested": round(building_native, 2),
        "dca_invested": round(dca_native, 2),
        "building_invested_cny": round(building_cny, 2),
        "dca_invested_cny": round(dca_cny, 2),
    }


def compute_phase_plan_stats(plans: list[dict], today: date) -> dict:
    building_matched = dca_matched = 0.0
    building_planned = dca_planned = 0.0

    for item in plans:
        if item["plan_date"] > today:
            continue
        amount = float(item.get("amount_cny", 0))
        matched = float(item.get("matched_amount_cny", 0))
        if item["phase"] == "building":
            building_planned += amount
            if item["status"] in ("done", "partial"):
                building_matched += matched
        elif item["phase"] == "dca":
            dca_planned += amount
            if item["status"] in ("done", "partial"):
                dca_matched += matched

    return {
        "building_matched_cny": round(building_matched, 2),
        "dca_matched_cny": round(dca_matched, 2),
        "building_planned_cny": round(building_planned, 2),
        "dca_planned_cny": round(dca_planned, 2),
    }


def build_phase_investment_summary(
    db: Session,
    config: dict,
    plans: list[dict],
    today: date,
    usd_cny_rate: float,
    *,
    scope: str = "all",
) -> dict:
    plan_stats = compute_phase_plan_stats(plans, today)
    tx_stats = compute_phase_investment(db, config, usd_cny_rate, scope=scope)
    return {**tx_stats, **plan_stats}


def ensure_plan_phase_column() -> None:
    inspector = inspect(engine)
    if "transactions" not in inspector.get_table_names():
        return
    columns = {col["name"] for col in inspector.get_columns("transactions")}
    if "plan_phase" in columns:
        return
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE transactions ADD COLUMN plan_phase VARCHAR(20)"))
