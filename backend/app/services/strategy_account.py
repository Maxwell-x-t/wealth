from datetime import date
import json
from pathlib import Path

from app.models.models import Account, Instrument, PriceSnapshot, StrategyAccount, Transaction
from app.services.ledger import canonical_code
from dividend_grid.cli import _load_watchlist
from dividend_grid.portfolio import value_account_data

STRATEGY_DIR = Path(__file__).resolve().parents[3] / "strategy"


def specs():
    return _load_watchlist(STRATEGY_DIR / "watchlist.json")


def import_opening(db, raw, reference_prices, name="红利策略"):
    """Import only into a new account, rejecting overlap with existing live holdings."""
    from app.services.holdings import compute_instrument_states
    watchlist = specs()
    value_account_data(raw, watchlist, lambda code: reference_prices.get(code))
    if db.query(Account).filter_by(name=name).first():
        raise ValueError(f"账户 {name} 已存在，不重复导入")
    states = compute_instrument_states(db)
    for instrument in db.query(Instrument):
        state = states.get(instrument.id)
        if state and state.quantity > 0:
            try:
                code = canonical_code(instrument.code)
            except ValueError:
                continue
            if raw["positions"].get(code, 0) > 0:
                raise ValueError(f"现有账户 {instrument.account.name} 已持有 {code}，须先核对是否重复")
    raw = dict(raw, positions=dict(raw["positions"]), reference_prices=reference_prices)
    names = {code: stock_name for code, stock_name, *_ in watchlist}
    names.update(raw.get("external_assets", {}))
    names["sh512890"] = "红利低波ETF"
    for code in names:
        raw["positions"].setdefault(code, 0)
    account = Account(name=name, currency="CNY")
    db.add(account)
    db.flush()
    for code, stock_name in names.items():
        instrument = Instrument(code=code[2:], name=stock_name, category="a_share",
                                account_id=account.id, currency="CNY")
        db.add(instrument)
        db.flush()
        if reference_prices.get(code):
            db.add(PriceSnapshot(instrument_id=instrument.id, price=reference_prices[code],
                                 snapshot_date=date.fromisoformat(raw["as_of"])))
    encoded = json.dumps(raw, ensure_ascii=False)
    db.add(StrategyAccount(account_id=account.id, opening_json=encoded, current_json=encoded))
    db.flush()
    return account.id
