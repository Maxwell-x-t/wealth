from __future__ import annotations

from datetime import date
from decimal import Decimal

import pyxirr
from sqlalchemy.orm import Session

from app.models.models import CashEvent, Instrument, PriceSnapshot, StrategyAccount, Transaction
from app.services.ledger import external_flows
from app.services.allocation import compute_rebalance_detail
from app.services.allocation_targets import (
    build_account_category_allocations,
    build_category_allocations,
)
from app.services.holdings import (
    build_historical_price_index,
    build_holdings,
    convert_transaction_to_cny,
    total_assets_cny_as_of,
    _to_decimal,
)
from app.services.fx_rate import get_usd_cny_rate_as_of
from app.services.investment_plan import generate_investment_plans
from app.services.plan_phase import build_phase_investment_summary


def compute_cashflows(db: Session, usd_cny_rate: float = 7.2) -> tuple[list[date], list[float]]:
    transactions = (
        db.query(Transaction)
        .order_by(Transaction.trade_date.asc(), Transaction.id.asc())
        .all()
    )
    flows_by_date: dict[date, Decimal] = {}
    managed = {row.account_id for row in db.query(StrategyAccount)}

    for tx in transactions:
        if tx.account_id in managed:
            continue
        amount = _to_decimal(tx.amount) if tx.amount else _to_decimal(tx.quantity) * _to_decimal(tx.price)
        fee = _to_decimal(tx.fee)
        instrument = db.query(Instrument).filter(Instrument.id == tx.instrument_id).one()
        rate = _to_decimal(tx.exchange_rate)
        cny_amount = convert_transaction_to_cny(amount, instrument.currency, rate)
        cny_fee = convert_transaction_to_cny(fee, instrument.currency, rate)

        if tx.side == "buy":
            cash = -(cny_amount + cny_fee)
        else:
            cash = cny_amount - cny_fee

        flows_by_date[tx.trade_date] = flows_by_date.get(tx.trade_date, Decimal("0")) + cash

    for flow_date, cash in external_flows(db):
        flows_by_date[flow_date] = flows_by_date.get(flow_date, Decimal("0")) + cash
    dates = sorted(flows_by_date.keys())
    amounts = [float(flows_by_date[d]) for d in dates]
    return dates, amounts


def compute_dashboard_metrics(db: Session, targets: dict) -> dict:
    usd_cny_rate = float(targets.get("usd_cny_rate", 7.2))
    holdings = build_holdings(db, usd_cny_rate=usd_cny_rate)

    transactions = db.query(Transaction).all()
    managed = {row.account_id for row in db.query(StrategyAccount)}
    buy_total = Decimal("0")
    sell_total = Decimal("0")

    for tx in transactions:
        if tx.account_id in managed:
            continue
        amount = _to_decimal(tx.amount) if tx.amount else _to_decimal(tx.quantity) * _to_decimal(tx.price)
        fee = _to_decimal(tx.fee)
        instrument = db.query(Instrument).filter(Instrument.id == tx.instrument_id).one()
        rate = _to_decimal(tx.exchange_rate)
        cny_amount = convert_transaction_to_cny(amount, instrument.currency, rate)
        cny_fee = convert_transaction_to_cny(fee, instrument.currency, rate)

        if tx.side == "buy":
            buy_total += cny_amount + cny_fee
        else:
            sell_total += cny_amount - cny_fee

    for _, cash in external_flows(db):
        if cash < 0:
            buy_total -= cash
        else:
            sell_total += cash
    total_assets_cny = Decimal(str(sum(h["market_value_cny"] for h in holdings)))
    net_investment_cny = buy_total - sell_total
    total_return_cny = total_assets_cny + sell_total - buy_total
    return_rate = (
        float((total_return_cny / net_investment_cny * 100).quantize(Decimal("0.01")))
        if net_investment_cny > 0
        else None
    )

    unrealized_pnl_cny = Decimal(str(sum(h["unrealized_pnl_cny"] for h in holdings)))
    realized_pnl_cny = Decimal(str(sum(h["realized_pnl_cny"] for h in holdings)))
    realized_pnl_cny += sum(
        _to_decimal(event.amount) * (1 if event.kind == "dividend" else -1)
        for event in db.query(CashEvent) if event.account_id in managed and event.kind in ("dividend", "fee")
    )

    mainland_assets_cny = Decimal("0")
    hk_assets_cny = Decimal("0")
    cny_assets = Decimal("0")
    usd_assets = Decimal("0")

    for holding in holdings:
        if "香港" in holding["account_name"] or holding["currency"] == "USD":
            hk_assets_cny += Decimal(str(holding["market_value_cny"]))
            if holding["currency"] == "USD":
                usd_assets += Decimal(str(holding["market_value"]))
        else:
            mainland_assets_cny += Decimal(str(holding["market_value_cny"]))
            cny_assets += Decimal(str(holding["market_value"]))

    target_map = {
        "nasdaq": float(targets.get("nasdaq", 70)),
        "sp500": float(targets.get("sp500", 30)),
        "a_share": float(targets.get("a_share", 0)),
        "gold": float(targets.get("gold", 0)),
        "cash": float(targets.get("cash", 0)),
        "qdii": float(targets.get("qdii", 0)),
    }
    category_allocations = build_category_allocations(
        holdings,
        target_map,
        float(total_assets_cny),
    )
    account_category_allocations = build_account_category_allocations(targets, holdings)

    rebalance_suggestion = None
    asset_allocs = [c for c in category_allocations if c["target_pct"] > 0 or c["current_pct"] > 0]
    if asset_allocs:
        most_under = min(asset_allocs, key=lambda x: x["gap_pct"])
        if most_under["gap_pct"] > 0.5:
            rebalance_suggestion = f"建议下一笔买入：{most_under['label']}"

    metrics = {
        "total_assets_cny": float(total_assets_cny),
        "net_investment_cny": float(net_investment_cny),
        "total_return_cny": float(total_return_cny),
        "return_rate": return_rate,
        "realized_pnl_cny": float(realized_pnl_cny),
        "unrealized_pnl_cny": float(unrealized_pnl_cny),
        "xirr": None,
        "annualized_return": None,
        "mainland_assets_cny": float(mainland_assets_cny),
        "hk_assets_cny": float(hk_assets_cny),
        "cny_assets": float(cny_assets),
        "usd_assets": float(usd_assets),
        "category_allocations": category_allocations,
        "account_category_allocations": account_category_allocations,
        "rebalance_suggestion": rebalance_suggestion,
        "holdings": holdings,
    }

    rebalance = compute_rebalance_detail(db, targets, metrics)
    metrics["rebalance_suggestion"] = rebalance.get("summary") or rebalance_suggestion
    metrics["rebalance"] = rebalance

    xirr_value = None
    annualized_return = None
    flow_dates, flow_amounts = compute_cashflows(db, usd_cny_rate)
    if flow_dates and total_assets_cny > 0:
        dates = flow_dates + [date.today()]
        amounts = flow_amounts + [float(total_assets_cny)]
        try:
            xirr_value = pyxirr.xirr(dates, amounts)
            if xirr_value is not None:
                annualized_return = round(float(xirr_value) * 100, 2)
                xirr_value = round(float(xirr_value) * 100, 2)
        except Exception:
            xirr_value = None
            annualized_return = None

    metrics["xirr"] = xirr_value
    metrics["annualized_return"] = annualized_return

    plans = generate_investment_plans(db, targets, metrics_for_rebalance=False)
    metrics["phase_investment"] = build_phase_investment_summary(
        db, targets, plans, date.today(), usd_cny_rate
    )

    return metrics


def _net_investment_cny_as_of(db: Session, as_of: date) -> Decimal:
    transactions = db.query(Transaction).filter(Transaction.trade_date <= as_of).all()
    managed = {row.account_id for row in db.query(StrategyAccount)}
    buy_total = Decimal("0")
    sell_total = Decimal("0")

    for tx in transactions:
        if tx.account_id in managed:
            continue
        amount = _to_decimal(tx.amount) if tx.amount else _to_decimal(tx.quantity) * _to_decimal(tx.price)
        fee = _to_decimal(tx.fee)
        instrument = db.query(Instrument).filter(Instrument.id == tx.instrument_id).one()
        rate = _to_decimal(tx.exchange_rate)
        cny_amount = convert_transaction_to_cny(amount, instrument.currency, rate)
        cny_fee = convert_transaction_to_cny(fee, instrument.currency, rate)
        if tx.side == "buy":
            buy_total += cny_amount + cny_fee
        else:
            sell_total += cny_amount - cny_fee

    return buy_total - sell_total - sum(cash for _, cash in external_flows(db, as_of))


def build_asset_history(db: Session, usd_cny_rate: float = 7.2) -> list[dict]:
    price_index = build_historical_price_index(db)
    if not price_index:
        return []

    snapshot_dates = sorted({snap_date for series in price_index.values() for snap_date, _ in series})
    today = date.today()
    if not snapshot_dates or snapshot_dates[-1] < today:
        snapshot_dates.append(today)

    history = []
    fallback_rate = _to_decimal(usd_cny_rate)
    for snap_date in snapshot_dates:
        fx_rate = _to_decimal(get_usd_cny_rate_as_of(db, snap_date, float(fallback_rate)))
        total_assets = total_assets_cny_as_of(db, snap_date, price_index, fx_rate)
        net_investment = _net_investment_cny_as_of(db, snap_date)
        total_return = total_assets - net_investment
        history.append(
            {
                "date": snap_date,
                "total_assets_cny": float(total_assets),
                "net_investment_cny": float(net_investment),
                "total_return_cny": float(total_return),
            }
        )

    return history
