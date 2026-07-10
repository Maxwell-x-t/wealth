from __future__ import annotations

from typing import Optional


def detect_quantity_price_swap(
    quantity: float,
    price: float,
    currency: str = "CNY",
) -> Optional[dict[str, float]]:
    """检测数量与成交价是否可能填反，返回建议值。"""
    q = float(quantity)
    p = float(price)
    if q <= 0 or p <= 0:
        return None

    if currency == "USD":
        if q >= 20 and p < 20:
            swapped_qty = p
            swapped_price = q
            if 20 <= swapped_price <= 2000 and 0.0001 <= swapped_qty < 20:
                return {"quantity": swapped_qty, "price": swapped_price}
        return None

    normal_price_min = 0.1
    normal_price_max = 50.0
    suspicious = p > normal_price_max or (p > 10 and q < 100)
    if not suspicious:
        return None

    swapped_qty = p
    swapped_price = q
    if normal_price_min <= swapped_price <= normal_price_max and swapped_qty >= 50:
        return {"quantity": swapped_qty, "price": swapped_price}
    return None


def swap_validation_message(suggestion: dict[str, float], currency: str) -> str:
    unit = "USD" if currency == "USD" else "元"
    return (
        f"数量与成交价疑似填反，建议改为：数量 {suggestion['quantity']}，"
        f"成交价 {suggestion['price']} {unit}"
    )
