from __future__ import annotations

import json
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import List, Optional, Set, Tuple, Union

from sqlalchemy.orm import Session

from app.models.models import AppConfig, Instrument, Transaction
from app.services.allocation import find_instruments_by_account_category
from app.services.calendar import scheduled_weekly_days_in_month
from app.services.config import save_config
from app.services.holdings import CATEGORY_LABELS

MATCH_WINDOW_DAYS = 5
AMOUNT_TOLERANCE = 0.2
PLAN_SKIPS_KEY = "plan_skips"


def plan_skip_key(
    plan_date: Union[date, str],
    account: str,
    category: str,
) -> str:
    if isinstance(plan_date, date):
        plan_date = plan_date.isoformat()
    return f"{plan_date}|{account}|{category}"


def load_plan_skips(db: Session) -> Set[str]:
    row = db.query(AppConfig).filter(AppConfig.key == PLAN_SKIPS_KEY).first()
    if not row or not row.value:
        return set()
    try:
        data = json.loads(row.value)
    except json.JSONDecodeError:
        return set()
    if not isinstance(data, list):
        return set()
    return {str(item) for item in data}


def set_plan_skip(
    db: Session,
    plan_date: date,
    account: str,
    category: str,
    skipped: bool,
) -> Set[str]:
    skips = load_plan_skips(db)
    key = plan_skip_key(plan_date, account, category)
    if skipped:
        skips.add(key)
    else:
        skips.discard(key)
    save_config(db, {PLAN_SKIPS_KEY: json.dumps(sorted(skips), ensure_ascii=False)})
    return skips


def _parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def _month_offset(base: date, offset: int) -> tuple[int, int]:
    month = base.month - 1 + offset
    year = base.year + month // 12
    month = month % 12 + 1
    return year, month


def _phase_for_month(month_index: int, building_months: int) -> str:
    if month_index <= 0:
        return "building"
    if month_index <= building_months:
        return "building"
    return "dca"


def _monthly_amount(phase: str, month_index: int, config: dict) -> float:
    if phase == "building":
        if month_index <= 0:
            return float(config.get("building_first_month_amount", 100000))
        return float(config.get("building_monthly_amount", 50000))
    return float(config.get("dca_monthly_amount", 10000))


def _group_key(item: dict) -> Tuple[str, str]:
    return item["account"], item["category"]


def _category_label(category: str) -> str:
    return CATEGORY_LABELS.get(category, category)


def _target_label(account_name: str, category: str) -> str:
    return f"{account_name} · {_category_label(category)}"


def _tx_amount_cny(tx: Transaction, instrument: Instrument, usd_cny_rate: float) -> float:
    amount = float(tx.amount) + float(tx.fee)
    if instrument.currency == "USD":
        amount *= usd_cny_rate
    return amount


def _amounts_match(tx_amount: float, plan_amount: float) -> bool:
    if plan_amount <= 0:
        return True
    return abs(tx_amount - plan_amount) / max(plan_amount, 1) <= AMOUNT_TOLERANCE


def _window_bounds(plan_date: date) -> tuple[date, date]:
    return plan_date - timedelta(days=MATCH_WINDOW_DAYS), plan_date + timedelta(days=MATCH_WINDOW_DAYS)


def _window_closed(plan_date: date) -> bool:
    return date.today() > _window_bounds(plan_date)[1]


def _date_status(plan_date: date) -> str:
    today = date.today()
    if plan_date < today:
        return "overdue"
    if plan_date == today:
        return "today"
    return "pending"


def _init_plan_fields(item: dict) -> None:
    item["base_amount_cny"] = round(float(item["amount_cny"]), 2)
    item["rolled_over_amount_cny"] = 0.0
    item["rolled_over_count"] = 0
    item["matched_amount_cny"] = 0.0
    item["shortfall_cny"] = 0.0


def _sum_available_in_window(
    txs: list[Transaction],
    tx_remaining: dict[int, float],
    window_start: date,
    window_end: date,
) -> float:
    return sum(
        tx_remaining.get(tx.id, 0.0)
        for tx in txs
        if window_start <= tx.trade_date <= window_end and tx_remaining.get(tx.id, 0.0) > 0
    )


def _consume_for_plan(
    txs: list[Transaction],
    tx_remaining: dict[int, float],
    window_start: date,
    window_end: date,
    target_amount: float,
) -> float:
    if target_amount <= 0:
        return 0.0

    consumed = 0.0
    for tx in txs:
        if consumed >= target_amount:
            break
        if not (window_start <= tx.trade_date <= window_end):
            continue
        remaining = tx_remaining.get(tx.id, 0.0)
        if remaining <= 0:
            continue
        take = min(remaining, target_amount - consumed)
        consumed += take
        tx_remaining[tx.id] = remaining - take
    return round(consumed, 2)


def _apply_match_result(plan: dict, needed: float, available: float, window_closed: bool) -> float:
    """根据窗口内可用金额更新计划状态，返回应从交易池扣减的金额。"""
    min_needed = needed * (1 - AMOUNT_TOLERANCE)

    if available >= min_needed or _amounts_match(available, needed):
        consumed = min(available, needed)
        plan["status"] = "done"
        plan["matched_amount_cny"] = round(consumed, 2)
        plan["shortfall_cny"] = 0.0
        return consumed

    if available > 0 and window_closed:
        plan["status"] = "partial"
        plan["matched_amount_cny"] = round(available, 2)
        plan["shortfall_cny"] = round(max(0.0, needed - available), 2)
        return available

    if available > 0:
        plan["status"] = _date_status(plan["plan_date"])
        plan["matched_amount_cny"] = round(available, 2)
        plan["shortfall_cny"] = 0.0
        return available

    plan["status"] = _date_status(plan["plan_date"])
    plan["matched_amount_cny"] = 0.0
    plan["shortfall_cny"] = 0.0
    return 0.0


def _load_group_transactions(
    db: Session,
    account_name: str,
    category: str,
    min_date: date,
    max_date: date,
    usd_cny_rate: float,
) -> tuple[list[Transaction], dict[int, Instrument], dict[int, float]]:
    instruments = find_instruments_by_account_category(db, account_name, category)
    if not instruments:
        return [], {}, {}

    instrument_by_id = {instrument.id: instrument for instrument in instruments}
    instrument_ids = list(instrument_by_id.keys())
    txs = (
        db.query(Transaction)
        .filter(
            Transaction.instrument_id.in_(instrument_ids),
            Transaction.side == "buy",
            Transaction.trade_date >= min_date,
            Transaction.trade_date <= max_date,
        )
        .order_by(Transaction.trade_date, Transaction.id)
        .all()
    )
    tx_remaining = {
        tx.id: _tx_amount_cny(tx, instrument_by_id[tx.instrument_id], usd_cny_rate) for tx in txs
    }
    return txs, instrument_by_id, tx_remaining


def _group_match_transactions(items: List[dict], db: Session, usd_cny_rate: float) -> None:
    """按账户+标的大类，从旧到新核销计划（支持部分完成与单笔覆盖多周）。"""
    by_group: dict[Tuple[str, str], List[dict]] = defaultdict(list)
    for item in items:
        by_group[_group_key(item)].append(item)

    for group in by_group.values():
        group.sort(key=lambda row: row["plan_date"])
        min_date = group[0]["plan_date"] - timedelta(days=MATCH_WINDOW_DAYS)
        max_date = group[-1]["plan_date"] + timedelta(days=MATCH_WINDOW_DAYS)
        txs, _, tx_remaining = _load_group_transactions(
            db,
            group[0]["account"],
            group[0]["category"],
            min_date,
            max_date,
            usd_cny_rate,
        )
        if not txs:
            for row in group:
                if row["status"] != "done":
                    row["status"] = _date_status(row["plan_date"])
            continue

        for plan in group:
            if plan["status"] == "done":
                continue

            needed = float(plan["base_amount_cny"])
            window_start, window_end = _window_bounds(plan["plan_date"])
            available = _sum_available_in_window(txs, tx_remaining, window_start, window_end)
            consume_amount = _apply_match_result(
                plan,
                needed,
                available,
                _window_closed(plan["plan_date"]),
            )
            if consume_amount > 0:
                _consume_for_plan(txs, tx_remaining, window_start, window_end, consume_amount)


def _apply_skips(items: List[dict], skips: Set[str]) -> None:
    """手动跳过：不要求补投，差额/全额均不顺延到下一笔。"""
    for item in items:
        key = plan_skip_key(item["plan_date"], item["account"], item["category"])
        if key not in skips:
            continue
        if item["status"] == "done":
            continue
        item["status"] = "skipped"
        item["shortfall_cny"] = 0.0


def _apply_rollover_merges(items: List[dict]) -> None:
    """窗口关闭后：逾期全额顺延，部分完成仅顺延差额。窗口未关闭不合并。"""
    by_group: dict[Tuple[str, str], List[dict]] = defaultdict(list)
    for item in items:
        by_group[_group_key(item)].append(item)

    for group in by_group.values():
        group.sort(key=lambda row: row["plan_date"])
        pending_rollover = 0.0
        pending_count = 0

        for item in group:
            if item["status"] in ("done", "skipped"):
                continue

            if item["status"] == "overdue":
                # 匹配窗口仍开放时保留逾期，等待补录，不提前整笔合并
                if not _window_closed(item["plan_date"]):
                    continue
                pending_rollover += float(item["base_amount_cny"])
                pending_count += 1
                item["status"] = "merged"
                continue

            if item["status"] == "partial":
                shortfall = float(item.get("shortfall_cny", 0))
                if shortfall > 0:
                    pending_rollover += shortfall
                    pending_count += 1
                continue

            if item["status"] in ("pending", "today") and pending_rollover > 0:
                item["rolled_over_amount_cny"] = round(pending_rollover, 2)
                item["rolled_over_count"] = pending_count
                item["amount_cny"] = round(item["base_amount_cny"] + pending_rollover, 2)
                pending_rollover = 0.0
                pending_count = 0


def _rematch_rolled_targets(items: List[dict], db: Session, usd_cny_rate: float) -> None:
    """对含补投金额的计划，用合并后金额重新匹配交易。"""
    targets = [
        item
        for item in items
        if item["status"] in ("pending", "today", "overdue")
        and float(item.get("rolled_over_amount_cny", 0)) > 0
    ]
    if not targets:
        return

    by_group: dict[Tuple[str, str], List[dict]] = defaultdict(list)
    for item in targets:
        by_group[_group_key(item)].append(item)

    for group in by_group.values():
        group.sort(key=lambda row: row["plan_date"])
        min_date = min(row["plan_date"] for row in group) - timedelta(days=MATCH_WINDOW_DAYS)
        max_date = max(row["plan_date"] for row in group) + timedelta(days=MATCH_WINDOW_DAYS)
        txs, _, tx_remaining = _load_group_transactions(
            db,
            group[0]["account"],
            group[0]["category"],
            min_date,
            max_date,
            usd_cny_rate,
        )
        if not txs:
            continue

        for plan in group:
            needed = float(plan["amount_cny"])
            window_start, window_end = _window_bounds(plan["plan_date"])
            available = _sum_available_in_window(txs, tx_remaining, window_start, window_end)
            consume_amount = _apply_match_result(
                plan,
                needed,
                available,
                _window_closed(plan["plan_date"]),
            )
            if consume_amount > 0:
                _consume_for_plan(txs, tx_remaining, window_start, window_end, consume_amount)


def _finalize_plan_items(items: List[dict], db: Session, usd_cny_rate: float) -> List[dict]:
    for item in items:
        _init_plan_fields(item)
        item["status"] = _date_status(item["plan_date"])

    _group_match_transactions(items, db, usd_cny_rate)
    _apply_skips(items, load_plan_skips(db))
    _apply_rollover_merges(items)
    _rematch_rolled_targets(items, db, usd_cny_rate)
    return items


def _append_plan_item(
    items: List[dict],
    plan_date: date,
    phase: str,
    account_name: str,
    category: str,
    amount_cny: float,
    week_index: int,
) -> None:
    if amount_cny <= 0:
        return

    items.append(
        {
            "plan_date": plan_date,
            "phase": phase,
            "phase_label": "建仓" if phase == "building" else "定投",
            "week_index": week_index,
            "account": account_name,
            "category": category,
            "category_label": _category_label(category),
            "target_label": _target_label(account_name, category),
            "amount_cny": round(amount_cny, 2),
            "status": "pending",
        }
    )


def generate_investment_plans(
    db: Session,
    config: dict,
    start: Optional[date] = None,
    end: Optional[date] = None,
) -> List[dict]:
    plan_start = _parse_date(config.get("plan_start_date", date.today().isoformat()))
    horizon_years = int(config.get("plan_horizon_years", 20))
    building_months = int(config.get("building_months", 8))
    nasdaq_pct = float(config.get("nasdaq", 70))
    sp500_pct = float(config.get("sp500", 30))
    mainland_pct = float(config.get("mainland", 60))
    hk_pct = float(config.get("hk", 40))
    usd_cny_rate = float(config.get("usd_cny_rate", 7.2))

    if end is None:
        end_year, end_month = _month_offset(plan_start, horizon_years * 12)
        gen_end = date(end_year, end_month, 1)
    else:
        gen_end = end

    gen_start = plan_start
    display_start = start
    display_end = end

    total_months = horizon_years * 12 + building_months + 1
    items: List[dict] = []

    for month_index in range(total_months):
        year, month = _month_offset(plan_start, month_index)
        phase = _phase_for_month(month_index, building_months)
        monthly_amount = _monthly_amount(phase, month_index, config)

        cn_days = scheduled_weekly_days_in_month(year, month, "cn")
        us_days = scheduled_weekly_days_in_month(year, month, "us")
        if not cn_days:
            continue

        cn_weekly = monthly_amount / len(cn_days)

        for week_index, cn_day in enumerate(cn_days, start=1):
            if cn_day < gen_start or cn_day > gen_end:
                continue

            if phase == "dca":
                category = "nasdaq" if week_index % 2 == 1 else "sp500"
                weekly_amount = monthly_amount / max(len(cn_days), 4)
                for account_name, account_pct, market_days in (
                    ("大陆", mainland_pct, cn_days),
                    ("香港", hk_pct, us_days),
                ):
                    if cn_day not in market_days and account_name == "香港":
                        us_day = us_days[min(week_index - 1, len(us_days) - 1)] if us_days else cn_day
                        plan_day = us_day
                    else:
                        plan_day = cn_day if account_name == "大陆" else (
                            us_days[min(week_index - 1, len(us_days) - 1)] if us_days else cn_day
                        )

                    if plan_day < gen_start or plan_day > gen_end:
                        continue

                    account_amount = weekly_amount * account_pct / 100
                    _append_plan_item(
                        items,
                        plan_day,
                        phase,
                        account_name,
                        category,
                        account_amount,
                        week_index,
                    )
            else:
                for account_name, account_pct, market_days in (
                    ("大陆", mainland_pct, cn_days),
                    ("香港", hk_pct, us_days),
                ):
                    plan_day = cn_day if account_name == "大陆" else (
                        us_days[min(week_index - 1, len(us_days) - 1)] if us_days else cn_day
                    )
                    if plan_day < gen_start or plan_day > gen_end:
                        continue

                    account_weekly = cn_weekly * account_pct / 100
                    for category, cat_pct in (("nasdaq", nasdaq_pct), ("sp500", sp500_pct)):
                        amount = account_weekly * cat_pct / 100
                        _append_plan_item(
                            items,
                            plan_day,
                            phase,
                            account_name,
                            category,
                            amount,
                            week_index,
                        )

    items.sort(key=lambda item: (item["plan_date"], item["account"], item["category"]))
    items = _finalize_plan_items(items, db, usd_cny_rate)

    if display_start is not None:
        items = [item for item in items if item["plan_date"] >= display_start]
    if display_end is not None:
        items = [item for item in items if item["plan_date"] <= display_end]

    return items


def build_plan_overview(db: Session, config: dict) -> dict:
    plans = generate_investment_plans(db, config)
    today = date.today()
    upcoming = [
        item
        for item in plans
        if item["plan_date"] >= today
        and item["status"] not in ("done", "merged", "partial", "skipped")
    ][:8]
    overdue = [item for item in plans if item["status"] == "overdue"]

    building_plans = [item for item in plans if item["phase"] == "building"]
    dca_plans = [item for item in plans if item["phase"] == "dca"]
    dca_elapsed = [item for item in dca_plans if item["plan_date"] <= today]
    building_done = sum(1 for item in building_plans if item["status"] == "done")
    dca_done = sum(1 for item in dca_elapsed if item["status"] == "done")
    dca_partial = sum(1 for item in dca_elapsed if item["status"] == "partial")
    dca_skipped = sum(1 for item in dca_elapsed if item["status"] == "skipped")
    merged_count = sum(1 for item in plans if item["status"] == "merged" and item["plan_date"] <= today)
    # 跳过不计入未完成，执行率 = 完成 / (到期 − 跳过)
    dca_denominator = max(len(dca_elapsed) - dca_skipped, 0)
    dca_execution_rate = (
        round(dca_done / dca_denominator * 100, 1) if dca_denominator else 0.0
    )

    history = [
        item
        for item in plans
        if item["plan_date"] <= today
        and item["status"] in ("done", "partial", "merged", "overdue", "skipped")
    ]
    history.sort(key=lambda item: (item["plan_date"], item["account"], item["category"]), reverse=True)
    history = history[:100]

    return {
        "upcoming": upcoming,
        "overdue_count": len(overdue),
        "overdue": overdue[:10],
        "merged_count": merged_count,
        "building_total": len(building_plans),
        "building_done": building_done,
        "dca_done": dca_done,
        "dca_partial": dca_partial,
        "dca_elapsed": len(dca_elapsed),
        "dca_execution_rate": dca_execution_rate,
        "history": history,
        "next_item": upcoming[0] if upcoming else None,
    }
