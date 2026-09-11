from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.responses import JSONResponse
from urllib.parse import urlsplit

from app.database import Base, SessionLocal, engine
from app.routers import (
    accounts,
    annual_review,
    backtest,
    dashboard,
    dca_signals,
    exchange_rates,
    forecast,
    index_data,
    instruments,
    investment_plans,
    prices,
    sync,
    transactions,
    strategy,
)
from app.services.config import ensure_default_config, seed_database
from app.services.plan_phase import ensure_plan_phase_column
from app.services.price_schema import ensure_price_premium_columns
from app.services.sync_job import start_scheduler, stop_scheduler
from app.services.ledger import ensure_ledger_columns

Base.metadata.create_all(bind=engine)
ensure_plan_phase_column()
ensure_price_premium_columns()
ensure_ledger_columns(engine)

app = FastAPI(title="Wealth Investment OS", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"https?://(localhost|127\.0\.0\.1|\[::1\])(:[0-9]+)?",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "[::1]", "testserver"])


@app.middleware("http")
async def check_write_origin(request, call_next):
    origin = request.headers.get("origin")
    if request.method not in ("GET", "HEAD", "OPTIONS") and origin:
        parsed = urlsplit(origin)
        if parsed.scheme not in ("http", "https") or parsed.hostname not in ("localhost", "127.0.0.1", "::1"):
            return JSONResponse({"detail": "仅接受本地页面的记账请求"}, status_code=403)
    return await call_next(request)

app.include_router(accounts.router)
app.include_router(instruments.router)
app.include_router(transactions.router)
app.include_router(strategy.router)
app.include_router(exchange_rates.router)
app.include_router(investment_plans.router)
app.include_router(prices.router)
app.include_router(dashboard.router)
app.include_router(forecast.router)
app.include_router(backtest.router)
app.include_router(annual_review.router)
app.include_router(sync.router)
app.include_router(index_data.router)
app.include_router(dca_signals.router)


@app.on_event("startup")
def on_startup():
    db = SessionLocal()
    try:
        seed_database(db)
        ensure_default_config(db)
    finally:
        db.close()
    start_scheduler()


@app.on_event("shutdown")
def on_shutdown():
    stop_scheduler()


@app.get("/api/health")
def health():
    return {"status": "ok"}
