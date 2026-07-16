from __future__ import annotations

from sqlalchemy import inspect, text

from app.database import engine


def ensure_price_premium_columns() -> None:
    """兼容旧库：为 price_snapshots 增加 IOPV / 溢价率列。"""
    inspector = inspect(engine)
    if "price_snapshots" not in inspector.get_table_names():
        return
    columns = {col["name"] for col in inspector.get_columns("price_snapshots")}
    statements = []
    if "iopv" not in columns:
        statements.append("ALTER TABLE price_snapshots ADD COLUMN iopv NUMERIC(18, 6)")
    if "premium_rate" not in columns:
        statements.append("ALTER TABLE price_snapshots ADD COLUMN premium_rate NUMERIC(18, 6)")
    if not statements:
        return
    with engine.begin() as conn:
        for sql in statements:
            conn.execute(text(sql))
