from __future__ import annotations

from datetime import date
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.models import Account, Instrument
from app.services.holdings import CATEGORY_LABELS, build_holdings


def _pct_gap(current: float, target: float) -> float:
    return round(target - current, 2)


def compute_account_gaps(
    mainland_assets_cny: float,
    hk_assets_cny: float,
    total_assets_cny: float,
    mainland_target: float,
    hk_target: float,
) -> List[dict]:
    if total_assets_cny <= 0:
        mainland_pct = hk_pct = 0.0
    else:
        mainland_pct = mainland_assets_cny / total_assets_cny * 100
        hk_pct = hk_assets_cny / total_assets_cny * 100

    return [
        {
            "key": "mainland",
            "label": "大陆",
            "current_pct": round(mainland_pct, 2),
            "target_pct": mainland_target,
            "gap_pct": _pct_gap(mainland_pct, mainland_target),
        },
        {
            "key": "hk",
            "label": "香港",
            "current_pct": round(hk_pct, 2),
            "target_pct": hk_target,
            "gap_pct": _pct_gap(hk_pct, hk_target),
        },
    ]


def resolve_instrument_codes(config: dict) -> Dict[str, Dict[str, str]]:
    return {
        "mainland": {
            "nasdaq": config.get("mainland_nasdaq_code", "513100"),
            "sp500": config.get("mainland_sp500_code", "513500"),
        },
        "hk": {
            "nasdaq": config.get("hk_nasdaq_code", "QQQM"),
            "sp500": config.get("hk_sp500_code", "VOO"),
        },
    }


def find_instrument(db: Session, account_name: str, code: str) -> Optional[Instrument]:
    return (
        db.query(Instrument)
        .join(Account)
        .filter(Instrument.code == code, Account.name == account_name)
        .first()
    )


def compute_rebalance_detail(db: Session, config: dict, metrics: dict) -> dict:
    total_assets = metrics["total_assets_cny"]
    account_gaps = compute_account_gaps(
        metrics["mainland_assets_cny"],
        metrics["hk_assets_cny"],
        total_assets,
        float(config.get("mainland", 60)),
        float(config.get("hk", 40)),
    )

    index_gaps = metrics["category_allocations"]
    index_candidates = [
        item for item in index_gaps
        if item["target_pct"] > 0 or item["current_pct"] > 0
    ]

    most_under_index = None
    if index_candidates:
        most_under_index = min(index_candidates, key=lambda item: item["gap_pct"])

    most_under_account = None
    if account_gaps:
        most_under_account = min(account_gaps, key=lambda item: item["gap_pct"])

    codes = resolve_instrument_codes(config)
    recommendations: List[dict] = []

    if most_under_index and most_under_index["gap_pct"] > 0.5:
        category = most_under_index["category"]
        account_key = most_under_account["key"] if most_under_account and most_under_account["gap_pct"] > 0.5 else "mainland"
        account_name = "大陆" if account_key == "mainland" else "香港"

        instrument = None
        code = None
        if category in ("nasdaq", "sp500"):
            code = codes[account_key][category]
            instrument = find_instrument(db, account_name, code)
        else:
            instrument = (
                db.query(Instrument)
                .join(Account)
                .filter(
                    Instrument.category == category,
                    Instrument.is_active.is_(True),
                    Account.name == account_name,
                )
                .first()
            )
            if not instrument:
                instrument = (
                    db.query(Instrument)
                    .filter(Instrument.category == category, Instrument.is_active.is_(True))
                    .first()
                )
            code = instrument.code if instrument else category

        recommendations.append(
            {
                "account": account_name,
                "category": category,
                "label": CATEGORY_LABELS.get(category, category),
                "code": code,
                "name": instrument.name if instrument else CATEGORY_LABELS.get(category, category),
                "reason": f"{most_under_index['label']}低于目标 {most_under_index['gap_pct']:.2f}%",
            }
        )

    summary_parts = []
    if most_under_index and most_under_index["gap_pct"] > 0.5:
        summary_parts.append(f"建议下一笔买入：{most_under_index['label']}")
    if most_under_account and most_under_account["gap_pct"] > 0.5:
        summary_parts.append(f"{most_under_account['label']}账户偏低 {most_under_account['gap_pct']:.2f}%")

    if recommendations:
        rec = recommendations[0]
        summary_parts.append(f"优先品种：{rec['account']} {rec['code']} {rec['name']}")

    return {
        "summary": "；".join(summary_parts) if summary_parts else None,
        "index_gaps": index_gaps,
        "account_gaps": account_gaps,
        "recommendations": recommendations,
    }
