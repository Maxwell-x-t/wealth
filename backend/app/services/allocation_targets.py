from __future__ import annotations

from decimal import Decimal
from typing import Optional

from app.services.account_plan import ACCOUNT_SPECS
from app.services.holdings import CATEGORY_LABELS

INDEX_CATEGORIES = ("nasdaq", "sp500")
ALL_CATEGORIES = ("nasdaq", "sp500", "a_share", "gold", "cash", "qdii")


def _config_float(config: dict, key: str, default: float = 0.0) -> float:
    raw = config.get(key)
    if raw in (None, ""):
        return default
    return float(raw)


def _optional_float(config: dict, key: str) -> Optional[float]:
    raw = config.get(key)
    if raw in (None, ""):
        return None
    return float(raw)


def resolve_account_index_targets(config: dict, account_key: str) -> tuple[float, float]:
    """账户内纳指/标普目标比例（%）；未配置时回退全局。"""
    nasdaq = _optional_float(config, f"{account_key}_nasdaq")
    sp500 = _optional_float(config, f"{account_key}_sp500")
    if nasdaq is not None and sp500 is not None:
        return nasdaq, sp500
    return _config_float(config, "nasdaq", 70), _config_float(config, "sp500", 30)


def resolve_account_category_target_map(config: dict, account_key: str) -> dict[str, float]:
    """账户内各大类目标比例；指数类可单独覆盖，其余沿用全局。"""
    nasdaq_pct, sp500_pct = resolve_account_index_targets(config, account_key)
    return {
        "nasdaq": nasdaq_pct,
        "sp500": sp500_pct,
        "a_share": _config_float(config, "a_share", 0),
        "gold": _config_float(config, "gold", 0),
        "cash": _config_float(config, "cash", 0),
        "qdii": _config_float(config, "qdii", 0),
    }


def build_category_allocations(
    holdings: list[dict],
    target_map: dict[str, float],
    total_assets_cny: Optional[float] = None,
) -> list[dict]:
    if total_assets_cny is None:
        total_assets_cny = sum(float(h["market_value_cny"]) for h in holdings)
    total = Decimal(str(total_assets_cny)) if total_assets_cny else Decimal("0")

    category_values = {category: Decimal("0") for category in ALL_CATEGORIES}
    for holding in holdings:
        cat = holding["category"]
        if cat in category_values:
            category_values[cat] += Decimal(str(holding["market_value_cny"]))

    allocations: list[dict] = []
    for category in ALL_CATEGORIES:
        value = category_values[category]
        current_pct = (
            float((value / total * 100).quantize(Decimal("0.01")))
            if total > 0
            else 0.0
        )
        target_pct = float(target_map.get(category, 0.0))
        allocations.append(
            {
                "category": category,
                "label": CATEGORY_LABELS.get(category, category),
                "current_pct": current_pct,
                "target_pct": target_pct,
                "gap_pct": round(target_pct - current_pct, 2),
            }
        )
    return allocations


def build_account_category_allocations(
    config: dict,
    holdings: list[dict],
) -> dict[str, list[dict]]:
    result: dict[str, list[dict]] = {}
    for spec in ACCOUNT_SPECS:
        account_name = spec["name"]
        account_key = spec["key"]
        account_holdings = [h for h in holdings if h.get("account_name") == account_name]
        account_total = sum(float(h["market_value_cny"]) for h in account_holdings)
        target_map = resolve_account_category_target_map(config, account_key)
        result[account_name] = build_category_allocations(
            account_holdings,
            target_map,
            account_total,
        )
    return result


def validate_account_index_targets(payload: dict) -> None:
    from fastapi import HTTPException

    for account_key, label in (("mainland", "大陆"), ("hk", "香港")):
        nasdaq = payload.get(f"{account_key}_nasdaq")
        sp500 = payload.get(f"{account_key}_sp500")
        if nasdaq in (None, "") and sp500 in (None, ""):
            continue
        if nasdaq == 0 and sp500 == 0:
            continue
        if nasdaq is None or sp500 is None:
            raise HTTPException(
                status_code=400,
                detail=f"{label}账户纳指与标普比例需同时填写，或均留空以使用全局配置",
            )
        total = round(float(nasdaq) + float(sp500), 2)
        if total != 100:
            raise HTTPException(
                status_code=400,
                detail=f"{label}账户纳指与标普比例之和必须为 100",
            )
