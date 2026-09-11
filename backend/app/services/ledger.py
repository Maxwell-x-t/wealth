"""Rebuild a reconciled account after every journal mutation, in one SQLite transaction."""

from datetime import date, datetime
from decimal import Decimal
import json

from fastapi import HTTPException
from sqlalchemy import inspect, text

from app.models.models import Account, CashEvent, Instrument, StrategyAccount, Transaction

D = Decimal


def ensure_ledger_columns(engine):
    columns = {c["name"] for c in inspect(engine).get_columns("transactions")}
    with engine.begin() as connection:
        if "etf_layers_after" not in columns:
            connection.execute(text("ALTER TABLE transactions ADD COLUMN etf_layers_after INTEGER"))
        if "request_id" not in columns:
            connection.execute(text("ALTER TABLE transactions ADD COLUMN request_id VARCHAR(36)"))
        connection.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_transactions_request_id ON transactions(request_id)"))


def begin_write(db):
    # Acquire the SQLite write reservation before reading balances, including across workers.
    db.execute(text("BEGIN IMMEDIATE"))


def canonical_code(code):
    if len(code) == 8 and code[:2] in ("sh", "sz", "bj") and code[2:].isdigit():
        return code
    if len(code) == 6 and code.isdigit():
        return ("sh" if code[0] in "569" else "bj" if code[0] in "48" else "sz") + code
    raise ValueError(f"策略账户不支持证券代码 {code}")


def register_instrument(db, instrument):
    ledger = db.get(StrategyAccount, instrument.account_id)
    if not ledger:
        return
    try:
        code = canonical_code(instrument.code)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if instrument.currency != "CNY":
        raise HTTPException(400, "策略账户仅支持人民币证券")
    instrument.code = code[2:]
    for field in ("opening_json", "current_json"):
        raw = json.loads(getattr(ledger, field))
        if code in raw["positions"]:
            raise HTTPException(400, "证券已存在于策略账本")
        raw["positions"][code] = 0
        raw.setdefault("external_assets", {})[code] = instrument.name
        setattr(ledger, field, json.dumps(raw, ensure_ascii=False))
    ledger.revision += 1


def ledger_rows(db, account_id, as_of=None):
    transactions = db.query(Transaction).filter_by(account_id=account_id).all()
    events = db.query(CashEvent).filter_by(account_id=account_id).all()
    rows = [(t.trade_date, t.created_at, 1, t.id, t) for t in transactions]
    rows += [(e.event_date, e.created_at, 0, e.id, e) for e in events]
    return [row[-1] for row in sorted(rows, key=lambda r: r[:4]) if as_of is None or row[0] <= as_of]


def replay_account(db, ledger, as_of=None):
    from dividend_grid.rsi6 import week_id
    raw = json.loads(ledger.opening_json)
    if as_of is not None and as_of < date.fromisoformat(raw["as_of"]):
        return None
    raw = dict(raw, positions=dict(raw["positions"]))
    cash = D(str(raw["cash"]))
    quantities = {code: D(str(qty)) for code, qty in raw["positions"].items()}
    layers = raw["etf_layers"]
    rsi_state = dict(week=week_id(as_of or date.today()), sell_base=None, planned_sell_layers=0, frozen=False)
    instruments = {i.id: i for i in db.query(Instrument).filter_by(account_id=ledger.account_id)}
    opening_date = date.fromisoformat(raw["as_of"])
    for row in ledger_rows(db, ledger.account_id, as_of):
        event_date = row.trade_date if isinstance(row, Transaction) else row.event_date
        if event_date < opening_date or event_date > date.today():
            raise ValueError(f"记录日期须在期初核对日 {opening_date} 与今天之间")
        code = None
        if row.instrument_id is not None:
            instrument = instruments.get(row.instrument_id)
            if not instrument or instrument.currency != "CNY":
                raise ValueError("证券必须属于当前人民币账户")
            code = canonical_code(instrument.code)
            if code not in quantities:
                raise ValueError(f"{code} 未纳入期初清单，须先登记为策略或其他持仓")
        if isinstance(row, Transaction):
            qty, amount, fee = D(str(row.quantity)), D(str(row.amount)), D(str(row.fee))
            if qty != qty.to_integral_value() or row.price <= 0 or row.exchange_rate != 1:
                raise ValueError("人民币实际成交须填写整数股数、正数价格和汇率 1")
            if row.side == "buy":
                cash -= amount + fee
                quantities[code] += qty
            else:
                cash += amount - fee
                quantities[code] -= qty
            if code == "sh512890":
                if row.etf_layers_after is None:
                    raise ValueError("512890 成交须填写成交后的策略层数")
                if row.side == "sell" and week_id(event_date) == rsi_state["week"]:
                    if rsi_state["sell_base"] is None:
                        rsi_state["sell_base"] = layers
                    rsi_state["planned_sell_layers"] = max(rsi_state["planned_sell_layers"], rsi_state["sell_base"] - row.etf_layers_after)
                    rsi_state["frozen"] = True
                layers = row.etf_layers_after
            elif row.etf_layers_after is not None:
                raise ValueError("只有 512890 需要策略层数")
        elif row.kind == "shares":
            if not code or D(str(row.quantity)) != D(str(row.quantity)).to_integral_value():
                raise ValueError("送转调整须选择证券并填写整数股数变动")
            quantities[code] += D(str(row.quantity))
        else:
            amount = D(str(row.amount))
            cash += amount if row.kind in ("deposit", "dividend") else -amount
        if cash < 0 or any(q < 0 for q in quantities.values()):
            raise ValueError(f"{event_date} 记录导致现金不足或卖出超过持仓，未保存")
        if not 0 <= layers <= 10 or bool(quantities.get("sh512890")) != bool(layers):
            raise ValueError("512890 实际份额与策略层数不一致")
        raw["as_of"] = event_date.isoformat()
    raw.update(cash=float(cash), positions={code: float(q) for code, q in quantities.items()}, etf_layers=layers, rsi_state=rsi_state)
    return raw


def rebuild_accounts(db, account_ids):
    db.flush()
    try:
        for account_id in set(account_ids):
            ledger = db.get(StrategyAccount, account_id)
            if ledger:
                ledger.current_json = json.dumps(replay_account(db, ledger), ensure_ascii=False)
                ledger.revision += 1
                ledger.updated_at = datetime.utcnow()
        # Legacy accounts also need chronological oversell checks after edits/deletions.
        from app.services.holdings import compute_instrument_states
        compute_instrument_states(db)
    except ValueError as exc:
        db.rollback()
        raise HTTPException(400, str(exc)) from exc


def external_flows(db, as_of=None):
    flows = []
    for ledger in db.query(StrategyAccount):
        opening = json.loads(ledger.opening_json)
        opened = date.fromisoformat(opening["as_of"])
        if as_of is None or opened <= as_of:
            capital = D(str(opening["cash"])) + sum(
                D(str(q)) * D(str(opening["reference_prices"].get(code, 0)))
                for code, q in opening["positions"].items())
            flows.append((opened, -capital))
        for event in db.query(CashEvent).filter_by(account_id=ledger.account_id):
            if (as_of is None or event.event_date <= as_of) and event.kind in ("deposit", "withdrawal"):
                flows.append((event.event_date, D(str(event.amount)) * (-1 if event.kind == "deposit" else 1)))
    return flows


def managed_cash(db, as_of=None):
    result = []
    for ledger in db.query(StrategyAccount):
        raw = replay_account(db, ledger, as_of) if as_of else json.loads(ledger.current_json)
        if raw:
            result.append((db.get(Account, ledger.account_id), D(str(raw["cash"]))))
    return result
