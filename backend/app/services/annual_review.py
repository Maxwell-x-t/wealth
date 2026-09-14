from __future__ import annotations

import json
from datetime import date
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.models import AppConfig
from app.services.config import get_config_map, save_config
from app.services.dca_drawdown import compute_drawdown_boost
from app.services.fx_rate import get_latest_usd_cny_rate
from app.services.investment_plan import _compute_dca_category_weights, _is_truthy_config, build_plan_overview
from app.services.returns import compute_dashboard_metrics

CHECKLIST_TEMPLATE = (
    {"key": "dca_done", "label": "完成定投计划"},
    {"key": "raise_dca", "label": "随收入增加定投金额"},
    {"key": "rebalance", "label": "检查并调整资产比例"},
    {"key": "bonds", "label": "评估是否增加债券/稳健资产"},
    {"key": "emergency", "label": "保留应急资金"},
)


def _config_key(year: int) -> str:
    return f"annual_review_{year}"


def _load_checks(db: Session, year: int) -> Dict[str, bool]:
    row = db.query(AppConfig).filter(AppConfig.key == _config_key(year)).first()
    if not row or not row.value:
        return {item["key"]: False for item in CHECKLIST_TEMPLATE}
    try:
        data = json.loads(row.value)
    except json.JSONDecodeError:
        return {item["key"]: False for item in CHECKLIST_TEMPLATE}
    return {item["key"]: bool(data.get(item["key"], False)) for item in CHECKLIST_TEMPLATE}


def _build_hints(db: Session, config: dict) -> List[dict]:
    overview = build_plan_overview(db, config)
    metrics = compute_dashboard_metrics(db, config, scope="index")
    hints = []

    rate = overview.get("dca_execution_rate", 0)
    if overview.get("dca_elapsed", 0) > 0:
        hints.append(
            {
                "key": "dca_done",
                "level": "success" if rate >= 80 else "warning",
                "text": f"定投执行率 {rate}%（完成 {overview.get('dca_done', 0)} / {overview.get('dca_elapsed', 0)}）",
            }
        )
    if overview.get("overdue_count", 0) > 0:
        hints.append(
            {
                "key": "dca_done",
                "level": "warning",
                "text": f"仍有 {overview['overdue_count']} 笔计划逾期未执行",
            }
        )
    if overview.get("merged_count", 0) > 0:
        hints.append(
            {
                "key": "dca_done",
                "level": "info",
                "text": f"已有 {overview['merged_count']} 笔逾期合并到后续应投",
            }
        )

    account_index_gaps = metrics.get("account_category_allocations") or {}
    for account_name, allocations in account_index_gaps.items():
        for item in allocations:
            if item.get("category") not in ("nasdaq", "sp500"):
                continue
            gap = abs(float(item.get("gap_pct") or 0))
            if gap >= 5:
                hints.append(
                    {
                        "key": "rebalance",
                        "level": "warning",
                        "text": f"{account_name} {item.get('label')} 偏离目标 {item.get('gap_pct')} 个百分点",
                    }
                )

    for item in metrics.get("category_allocations") or []:
        if item.get("category") in ("nasdaq", "sp500"):
            continue
        gap = abs(float(item.get("gap_pct") or 0))
        if gap >= 5:
            hints.append(
                {
                    "key": "rebalance",
                    "level": "warning",
                    "text": f"{item.get('label')} 偏离目标 {item.get('gap_pct')} 个百分点",
                }
            )

    suggestion = metrics.get("rebalance_suggestion")
    if suggestion:
        hints.append({"key": "rebalance", "level": "info", "text": suggestion})

    if _is_truthy_config(config.get("plan_rebalance_enabled", "1")):
        _, _, tilt_active, tilt_note = _compute_dca_category_weights(
            config,
            metrics.get("category_allocations") or [],
        )
        if tilt_active and tilt_note:
            hints.append({"key": "rebalance", "level": "warning", "text": tilt_note})
        else:
            hints.append(
                {
                    "key": "rebalance",
                    "level": "success",
                    "text": "指数仓位偏离均在阈值内，定投按目标比例分配",
                }
            )

    boost = compute_drawdown_boost(db, config)
    if boost.get("enabled"):
        if boost.get("applied_amount", 0) > 0 and boost.get("note"):
            hints.append({"key": "dca_done", "level": "warning", "text": boost["note"]})
        elif boost.get("tier") and boost.get("note"):
            hints.append({"key": "dca_done", "level": "info", "text": boost["note"]})
        elif boost.get("max_drawdown_pct", 0) > 0:
            hints.append(
                {
                    "key": "dca_done",
                    "level": "info",
                    "text": f"当前最大指数回撤 {boost['max_drawdown_pct']:.1f}%，未触发危机加仓",
                }
            )

    return hints


def get_annual_review(db: Session, year: Optional[int] = None) -> dict:
    year = year or date.today().year
    config = get_config_map(db)
    config["usd_cny_rate"] = get_latest_usd_cny_rate(db)
    checks = _load_checks(db, year)
    items = [
        {
            "key": item["key"],
            "label": item["label"],
            "checked": checks.get(item["key"], False),
        }
        for item in CHECKLIST_TEMPLATE
    ]
    done = sum(1 for item in items if item["checked"])
    return {
        "year": year,
        "items": items,
        "done_count": done,
        "total_count": len(items),
        "completion_rate": round(done / len(items) * 100, 1) if items else 0,
        "hints": _build_hints(db, config),
    }


def save_annual_review(db: Session, year: int, checks: Dict[str, bool]) -> dict:
    payload = {item["key"]: bool(checks.get(item["key"], False)) for item in CHECKLIST_TEMPLATE}
    save_config(db, {_config_key(year): json.dumps(payload, ensure_ascii=False)})
    return get_annual_review(db, year)
