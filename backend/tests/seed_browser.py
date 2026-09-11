"""Create synthetic balances for browser tests in WEALTH_DATA_DIR, never in the live database."""
import os
from pathlib import Path

assert os.environ.get("WEALTH_DEMO") == "1"
assert Path(os.environ["WEALTH_DATA_DIR"]).name.startswith("wealth-browser-")

from app.main import app
from app.database import Base, SessionLocal, engine
from app.services.config import seed_database, ensure_default_config
from app.services.strategy_account import import_opening

Base.metadata.drop_all(engine)
Base.metadata.create_all(engine)
with SessionLocal() as db:
    seed_database(db)
    ensure_default_config(db)
    import_opening(db, dict(as_of="2026-01-01", cash=10000,
        positions={"sh600036": 100, "sh512890": 0, "sh588000": 1000}, etf_layers=0,
        external_assets={"sh588000": "科创50ETF"}, etf_industry_weights={}),
        {"sh600036": 40, "sh588000": 1.5, "sh512890": 1}, name="浏览器测试账户")
    db.commit()
