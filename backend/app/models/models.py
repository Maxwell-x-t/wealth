from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Optional

from sqlalchemy import Date, DateTime, ForeignKey, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class InstrumentCategory(str, Enum):
    NASDAQ = "nasdaq"
    SP500 = "sp500"
    A_SHARE = "a_share"
    GOLD = "gold"
    CASH = "cash"
    QDII = "qdii"
    OTHER = "other"


class TransactionSide(str, Enum):
    BUY = "buy"
    SELL = "sell"


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    currency: Mapped[str] = mapped_column(String(10), nullable=False, default="CNY")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    instruments: Mapped[list["Instrument"]] = relationship(back_populates="account")
    transactions: Mapped[list["Transaction"]] = relationship(back_populates="account")


class Instrument(Base):
    __tablename__ = "instruments"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(20), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    category: Mapped[str] = mapped_column(String(20), nullable=False)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), nullable=False)
    currency: Mapped[str] = mapped_column(String(10), nullable=False, default="CNY")
    is_active: Mapped[bool] = mapped_column(default=True)

    account: Mapped["Account"] = relationship(back_populates="instruments")
    transactions: Mapped[list["Transaction"]] = relationship(back_populates="instrument")
    prices: Mapped[list["PriceSnapshot"]] = relationship(back_populates="instrument")

    __table_args__ = (UniqueConstraint("account_id", "code", name="uq_account_code"),)


class Transaction(Base):
    __tablename__ = "transactions"

    id: Mapped[int] = mapped_column(primary_key=True)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), nullable=False)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id"), nullable=False)
    side: Mapped[str] = mapped_column(String(10), nullable=False)
    quantity: Mapped[float] = mapped_column(Numeric(18, 6), nullable=False)
    price: Mapped[float] = mapped_column(Numeric(18, 6), nullable=False)
    amount: Mapped[float] = mapped_column(Numeric(18, 4), nullable=False)
    fee: Mapped[float] = mapped_column(Numeric(18, 4), nullable=False, default=0)
    exchange_rate: Mapped[float] = mapped_column(Numeric(18, 6), nullable=False, default=1)
    note: Mapped[Optional[str]] = mapped_column(Text)
    plan_phase: Mapped[Optional[str]] = mapped_column(String(20))
    etf_layers_after: Mapped[Optional[int]] = mapped_column()
    request_id: Mapped[Optional[str]] = mapped_column(String(36), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    account: Mapped["Account"] = relationship(back_populates="transactions")
    instrument: Mapped["Instrument"] = relationship(back_populates="transactions")


class PriceSnapshot(Base):
    __tablename__ = "price_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id"), nullable=False)
    price: Mapped[float] = mapped_column(Numeric(18, 6), nullable=False)
    iopv: Mapped[Optional[float]] = mapped_column(Numeric(18, 6))
    premium_rate: Mapped[Optional[float]] = mapped_column(Numeric(18, 6))
    snapshot_date: Mapped[date] = mapped_column(Date, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    instrument: Mapped["Instrument"] = relationship(back_populates="prices")


class AppConfig(Base):
    __tablename__ = "app_config"

    key: Mapped[str] = mapped_column(String(50), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)


class FxRateSnapshot(Base):
    __tablename__ = "fx_rate_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    pair: Mapped[str] = mapped_column(String(20), nullable=False, default="USD/CNY")
    rate: Mapped[float] = mapped_column(Numeric(18, 6), nullable=False)
    snapshot_date: Mapped[date] = mapped_column(Date, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class StrategyAccount(Base):
    __tablename__ = "strategy_accounts"

    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), primary_key=True)
    opening_json: Mapped[str] = mapped_column(Text)
    current_json: Mapped[str] = mapped_column(Text)
    revision: Mapped[int] = mapped_column(default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class CashEvent(Base):
    __tablename__ = "cash_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"))
    event_date: Mapped[date] = mapped_column(Date)
    kind: Mapped[str] = mapped_column(String(20))
    amount: Mapped[float] = mapped_column(Numeric(18, 4), default=0)
    instrument_id: Mapped[Optional[int]] = mapped_column(ForeignKey("instruments.id"))
    quantity: Mapped[float] = mapped_column(Numeric(18, 6), default=0)
    note: Mapped[Optional[str]] = mapped_column(Text)
    request_id: Mapped[str] = mapped_column(String(36), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
