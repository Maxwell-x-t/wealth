from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.database import Base, SessionLocal, engine
from app.routers import accounts, dashboard, exchange_rates, instruments, investment_plans, prices, transactions
from app.services.config import ensure_default_config, seed_database

Base.metadata.create_all(bind=engine)

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


@app.on_event("startup")
def on_startup():
    db = SessionLocal()
    try:
        seed_database(db)
        ensure_default_config(db)
    finally:
        db.close()


@app.get("/api/health")
def health():
    return {"status": "ok"}
