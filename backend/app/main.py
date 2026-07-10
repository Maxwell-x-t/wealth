from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.database import Base, SessionLocal, engine
from app.routers import (
    accounts,
    annual_review,
    backtest,
    dashboard,
    exchange_rates,
    forecast,
    instruments,
    investment_plans,
    prices,
    sync,
    transactions,
)
from app.services.config import ensure_default_config, seed_database
from app.services.plan_phase import ensure_plan_phase_column
from app.services.sync_job import start_scheduler, stop_scheduler

Base.metadata.create_all(bind=engine)
ensure_plan_phase_column()

app = FastAPI(title="Wealth Investment OS", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(accounts.router)
app.include_router(instruments.router)
app.include_router(transactions.router)
app.include_router(exchange_rates.router)
app.include_router(investment_plans.router)
app.include_router(prices.router)
app.include_router(dashboard.router)
app.include_router(forecast.router)
app.include_router(backtest.router)
app.include_router(annual_review.router)
app.include_router(sync.router)


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
