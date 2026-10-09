from datetime import date, datetime
from decimal import Decimal
import json
from typing import Literal
from uuid import UUID
import os
import subprocess
import sys
import threading

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.models import Account, AppConfig, CashEvent, Instrument, PriceSnapshot, StrategyAccount, Transaction
from app.services.ledger import begin_write, canonical_code, ledger_rows, rebuild_accounts
from app.services.strategy_account import STRATEGY_DIR, specs
from dividend_grid.portfolio import account_etf_capacity, load_limits, save_limits, value_account_data

router = APIRouter(prefix="/api/strategy", tags=["strategy"])
analysis_lock = threading.Lock()
PORTFOLIO_RULES = STRATEGY_DIR / "portfolio_rules.json"


class CashEntry(BaseModel):
    model_config = {"allow_inf_nan": False}
    event_date: date
    kind: Literal["deposit", "withdrawal", "dividend", "fee", "shares"]
    amount: float = Field(default=0, ge=0, le=1e12)
    instrument_id: int | None = None
    quantity: int = Field(default=0, ge=-1000000000, le=1000000000, strict=True)
    note: str | None = Field(default=None, max_length=1000)
    request_id: UUID

    @model_validator(mode="after")
    def valid_event(self):
        if self.kind == "shares":
            if not self.instrument_id or self.quantity == 0 or self.amount:
                raise ValueError("送转调整须填写证券和非零股数变动，金额为零")
        elif self.amount <= 0 or self.quantity != 0:
            raise ValueError("资金记录须填写正数金额，股数变动为零")
        if self.kind == "dividend" and not self.instrument_id:
            raise ValueError("分红须选择证券")
        return self


class PortfolioRulesUpdate(BaseModel):
    model_config = {"allow_inf_nan": False}
    min_cash_pct: float = Field(ge=0, le=100)
    etf_budget_pct: float = Field(ge=0, le=100)

    @model_validator(mode="after")
    def budget_fits(self):
        cash = round(self.min_cash_pct, 2)
        etf = round(self.etf_budget_pct, 2)
        if etf <= 0 or cash + etf > 100:
            raise ValueError("现金底线与 512890 资金上限须在 0 到 100 之间，资金上限须大于 0，且两者相加不能超过 100%")
        self.min_cash_pct = cash
        self.etf_budget_pct = etf
        return self


def require_account(db, account_id):
    ledger = db.get(StrategyAccount, account_id)
    if not ledger:
        raise HTTPException(404, "策略账户尚未接入")
    return ledger


@router.get("/accounts")
def list_accounts(db: Session = Depends(get_db)):
    return [{"id": l.account_id, "name": db.get(Account, l.account_id).name}
            for l in db.query(StrategyAccount)]


@router.get("/accounts/{account_id}")
def account_summary(account_id: int, db: Session = Depends(get_db)):
    ledger = require_account(db, account_id)
    raw, opening = json.loads(ledger.current_json), json.loads(ledger.opening_json)
    prices, holdings = {}, []
    for instrument in db.query(Instrument).filter_by(account_id=account_id):
        code = canonical_code(instrument.code)
        latest = db.query(PriceSnapshot).filter_by(instrument_id=instrument.id).order_by(
            PriceSnapshot.snapshot_date.desc(), PriceSnapshot.id.desc()).first()
        price = float(latest.price) if latest else None
        prices[code] = price
        quantity = raw["positions"].get(code, 0)
        holdings.append(dict(instrument_id=instrument.id, code=code, name=instrument.name, quantity=quantity,
            price=price, price_date=latest.snapshot_date.isoformat() if latest else None,
            market_value=round(quantity * price, 2) if price is not None else (0 if quantity == 0 else None),
            sleeve="rsi" if code == "sh512890" else "other" if code in raw.get("external_assets", {}) else "grid"))
    limits = load_limits(PORTFOLIO_RULES)
    valuation, error = None, None
    try:
        valuation = value_account_data(raw, specs(), prices.get)
    except ValueError as exc:
        error = str(exc)
    equity = valuation.equity if valuation else None
    opening_equity = float(Decimal(str(opening["cash"])) + sum(
        Decimal(str(q)) * Decimal(str(opening["reference_prices"].get(c, 0))) for c, q in opening["positions"].items()))
    net_flow = sum(float(e.amount) * (1 if e.kind == "deposit" else -1)
                   for e in db.query(CashEvent).filter_by(account_id=account_id) if e.kind in ("deposit", "withdrawal"))
    for holding in holdings:
        holding["weight"] = holding["market_value"] / equity * 100 if equity and holding["market_value"] is not None else None
    return dict(account_id=account_id, name=db.get(Account, account_id).name, revision=ledger.revision,
        demo=os.environ.get("WEALTH_DEMO") == "1",
        as_of=raw["as_of"], opening_date=opening["as_of"], cash=raw["cash"], equity=equity,
        pnl_since_opening=round(equity - opening_equity - net_flow, 2) if equity else None,
        etf_layers=raw["etf_layers"], holdings=holdings, valuation_error=error,
        min_cash_pct=limits.min_cash_pct, etf_budget_pct=limits.etf_budget_pct,
        etf_capacity=account_etf_capacity(valuation, limits, specs()) if valuation else None,
        stock_cash_available=round(max(0, raw["cash"] - equity * limits.min_cash_pct / 100
            - max(0, equity * limits.etf_budget_pct / 100 - valuation.values.get("sh512890", 0))), 2) if valuation else None)


@router.put("/portfolio-rules")
def update_portfolio_rules(payload: PortfolioRulesUpdate):
    try:
        limits = save_limits(PORTFOLIO_RULES, payload.min_cash_pct, payload.etf_budget_pct)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {
        "min_cash_pct": limits.min_cash_pct,
        "etf_budget_pct": limits.etf_budget_pct,
        "stock_cap_pct": round(100 - limits.min_cash_pct - limits.etf_budget_pct, 2),
    }


@router.get("/accounts/{account_id}/cash-events")
def list_events(account_id: int, db: Session = Depends(get_db)):
    require_account(db, account_id)
    return db.query(CashEvent).filter_by(account_id=account_id).order_by(CashEvent.event_date.desc(), CashEvent.id.desc()).all()


@router.post("/accounts/{account_id}/cash-events")
def create_event(account_id: int, payload: CashEntry, db: Session = Depends(get_db)):
    begin_write(db)
    require_account(db, account_id)
    data = payload.model_dump(exclude={"request_id"})
    existing = db.query(CashEvent).filter_by(request_id=str(payload.request_id)).first()
    if existing:
        if existing.account_id != account_id or any(
            (float(getattr(existing, k)) != float(v) if k == "amount" else getattr(existing, k) != v)
            for k, v in data.items()):
            raise HTTPException(409, "重复请求编号对应不同资金记录")
        return {"id": existing.id}
    event = CashEvent(account_id=account_id, request_id=str(payload.request_id), **data)
    db.add(event)
    rebuild_accounts(db, [account_id])
    db.commit()
    return {"id": event.id}


@router.delete("/accounts/{account_id}/cash-events/{event_id}")
def delete_event(account_id: int, event_id: int, db: Session = Depends(get_db)):
    begin_write(db)
    require_account(db, account_id)
    event = db.get(CashEvent, event_id)
    if not event or event.account_id != account_id:
        raise HTTPException(404, "资金记录不存在")
    db.delete(event)
    rebuild_accounts(db, [account_id])
    db.commit()
    return {"ok": True}


@router.get("/accounts/{account_id}/signals")
def get_signals(account_id: int, db: Session = Depends(get_db)):
    ledger = require_account(db, account_id)
    saved = db.get(AppConfig, f"strategy_signals_{account_id}")
    result = json.loads(saved.value) if saved else None
    if result:
        result["stale"] = result["revision"] != ledger.revision or result["as_of"][:10] != date.today().isoformat()
    return result


@router.post("/accounts/{account_id}/signals")
def refresh_signals(account_id: int, db: Session = Depends(get_db)):
    from dividend_grid.rsi6 import week_id
    ledger = require_account(db, account_id)
    raw, opening = json.loads(ledger.current_json), json.loads(ledger.opening_json)
    revision = ledger.revision
    current_week = week_id(date.today())
    state = dict(week=current_week, sell_base=None, planned_sell_layers=0, frozen=False)
    layers = opening["etf_layers"]
    for row in ledger_rows(db, account_id):
        if isinstance(row, Transaction) and canonical_code(row.instrument.code) == "sh512890":
            if row.side == "sell" and week_id(row.trade_date) == current_week:
                if state["sell_base"] is None:
                    state["sell_base"] = layers
                state["frozen"] = True
                state["planned_sell_layers"] = max(state["planned_sell_layers"], state["sell_base"] - row.etf_layers_after)
            layers = row.etf_layers_after
    previous = db.get(AppConfig, f"strategy_signals_{account_id}")
    previous_rsi = (json.loads(previous.value).get("rsi") or {}) if previous else {}
    if previous_rsi.get("week") == current_week:
        if previous_rsi.get("sell_base") is not None:
            state["sell_base"] = previous_rsi["sell_base"]
        state["planned_sell_layers"] = max(state["planned_sell_layers"], previous_rsi.get("planned_sell_layers", 0))
        state["frozen"] = state["frozen"] or previous_rsi.get("frozen", False)
    db.rollback()
    if not analysis_lock.acquire(blocking=False):
        raise HTTPException(409, "策略正在刷新，请稍后重试")
    try:
        result = subprocess.run([sys.executable, "-m", "app.strategy_worker"],
            cwd=STRATEGY_DIR, env=dict(os.environ, PYTHONPATH=str(STRATEGY_DIR.parent / "backend")),
            input=json.dumps(dict(account=raw, rsi_state=state)), capture_output=True, text=True, timeout=240)
        if result.returncode != 0:
            raise HTTPException(502, "策略数据暂不可用，请稍后重试")
        output = json.loads(result.stdout)
    except subprocess.TimeoutExpired as exc:
        raise HTTPException(504, "行情或财报服务超时，未更新策略结果") from exc
    except ValueError as exc:
        raise HTTPException(502, "策略服务未返回有效结果") from exc
    finally:
        analysis_lock.release()
    begin_write(db)
    ledger = require_account(db, account_id)
    if ledger.revision != revision:
        raise HTTPException(409, "计算期间账本发生变化，请重新刷新策略")
    output.update(revision=revision, as_of=datetime.now().isoformat(timespec="seconds"), stale=False)
    key = f"strategy_signals_{account_id}"
    saved = db.get(AppConfig, key)
    if saved:
        saved.value = json.dumps(output, ensure_ascii=False)
    else:
        db.add(AppConfig(key=key, value=json.dumps(output, ensure_ascii=False)))
    db.commit()
    return output
