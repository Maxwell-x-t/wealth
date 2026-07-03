from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import List, Optional, Tuple

from sqlalchemy.orm import Session

from app.models.models import Instrument, Transaction
from app.services.allocation import find_instrument, resolve_instrument_codes
from app.services.calendar import scheduled_weekly_days_in_month

MATCH_WINDOW_DAYS = 5
AMOUNT_TOLERANCE = 0.2


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


def _tx_amount_cny(tx: Transaction, instrument: Instrument, usd_cny_rate: float) -> float:
    amount = float(tx.amount) + float(tx.fee)
    if instrument.currency == "USD":
        amount *= usd_cny_rate
    return amount


def _amounts_match(tx_amount: float, plan_amount: float) -> bool:
    if plan_amount <= 0:
        return True
    return abs(tx_amount - plan_amount) / max(plan_amount, 1) <= AMOUNT_TOLERANCE


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


def _group_match_transactions(items: List[dict], db: Session, usd_cny_rate: float) -> None:
    """按账户+分类，从旧到新用买入交易核销计划（支持单笔覆盖多周）。"""
    by_group: dict[Tuple[str, str], List[dict]] = defaultdict(list)
    for item in items:
        by_group[_group_key(item)].append(item)

    for group in by_group.values():
        group.sort(key=lambda row: row["plan_date"])
        instrument_id = next((row["instrument_id"] for row in group if row["instrument_id"]), None)
        if not instrument_id:
            for row in group:
                if row["status"] != "done":
                    row["status"] = _date_status(row["plan_date"])
            continue

        instrument = db.query(Instrument).filter(Instrument.id == instrument_id).one()
        min_date = group[0]["plan_date"] - timedelta(days=MATCH_WINDOW_DAYS)
        max_date = group[-1]["plan_date"] + timedelta(days=MATCH_WINDOW_DAYS)
        txs = (
            db.query(Transaction)
            .filter(
                Transaction.instrument_id == instrument_id,
                Transaction.side == "buy",
                Transaction.trade_date >= min_date,
                Transaction.trade_date <= max_date,
            )
            .order_by(Transaction.trade_date, Transaction.id)
            .all()
        )
        tx_remaining = {tx.id: _tx_amount_cny(tx, instrument, usd_cny_rate) for tx in txs}

        for plan in group:
            if plan["status"] == "done":
                continue

            needed = float(plan["base_amount_cny"])
            window_start = plan["plan_date"] - timedelta(days=MATCH_WINDOW_DAYS)
            window_end = plan["plan_date"] + timedelta(days=MATCH_WINDOW_DAYS)

            for tx in txs:
                remaining = tx_remaining.get(tx.id, 0.0)
                if remaining <= 0:
                    continue
                if not (window_start <= tx.trade_date <= window_end):
                    continue

                min_needed = needed * (1 - AMOUNT_TOLERANCE)
                if _amounts_match(remaining, needed) or remaining >= min_needed:
                    plan["status"] = "done"
                    tx_remaining[tx.id] = max(0.0, remaining - needed)
                    break

            if plan["status"] != "done":
                plan["status"] = _date_status(plan["plan_date"])


def _apply_rollover_merges(items: List[dict]) -> None:
    """将逾期未执行金额合并到同组下一笔待执行/今日计划。"""
    by_group: dict[Tuple[str, str], List[dict]] = defaultdict(list)
    for item in items:
        by_group[_group_key(item)].append(item)

    for group in by_group.values():
        group.sort(key=lambda row: row["plan_date"])
        pending_rollover = 0.0
        pending_count = 0

        for item in group:
            _init_plan_fields(item)

            if item["status"] == "done":
                continue

            if item["status"] == "overdue":
                pending_rollover += float(item["base_amount_cny"])
                pending_count += 1
                item["status"] = "merged"
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
        and item.get("instrument_id")
    ]
    if not targets:
        return

    by_group: dict[Tuple[str, str], List[dict]] = defaultdict(list)
    for item in targets:
        by_group[_group_key(item)].append(item)

    for group in by_group.values():
        group.sort(key=lambda row: row["plan_date"])
        instrument_id = group[0]["instrument_id"]
        instrument = db.query(Instrument).filter(Instrument.id == instrument_id).one()

        min_date = min(row["plan_date"] for row in group) - timedelta(days=MATCH_WINDOW_DAYS)
        max_date = max(row["plan_date"] for row in group) + timedelta(days=MATCH_WINDOW_DAYS)
        txs = (
            db.query(Transaction)
            .filter(
                Transaction.instrument_id == instrument_id,
                Transaction.side == "buy",
                Transaction.trade_date >= min_date,
                Transaction.trade_date <= max_date,
            )
            .order_by(Transaction.trade_date, Transaction.id)
            .all()
        )
        tx_remaining = {tx.id: _tx_amount_cny(tx, instrument, usd_cny_rate) for tx in txs}

        for plan in group:
            needed = float(plan["amount_cny"])
            window_start = plan["plan_date"] - timedelta(days=MATCH_WINDOW_DAYS)
            window_end = plan["plan_date"] + timedelta(days=MATCH_WINDOW_DAYS)
            min_needed = needed * (1 - AMOUNT_TOLERANCE)

            for tx in txs:
                remaining = tx_remaining.get(tx.id, 0.0)
                if remaining <= 0:
                    continue
                if not (window_start <= tx.trade_date <= window_end):
                    continue
                if _amounts_match(remaining, needed) or remaining >= min_needed:
                    plan["status"] = "done"
                    tx_remaining[tx.id] = max(0.0, remaining - needed)
                    break


def _finalize_plan_items(items: List[dict], db: Session, usd_cny_rate: float) -> List[dict]:
    for item in items:
        _init_plan_fields(item)
        item["status"] = _date_status(item["plan_date"])

    _group_match_transactions(items, db, usd_cny_rate)
    _apply_rollover_merges(items)
    _rematch_rolled_targets(items, db, usd_cny_rate)
    return items


def _append_plan_item(
    items: List[dict],
    plan_date: date,
    phase: str,
    account_name: str,
    category: str,
    code: str,
    amount_cny: float,
    week_index: int,
    db: Session,
) -> None:
    if amount_cny <= 0:
        return

    instrument = find_instrument(db, account_name, code)
    items.append(
        {
            "plan_date": plan_date,
            "phase": phase,
            "phase_label": "建仓" if phase == "building" else "定投",
            "week_index": week_index,
            "account": account_name,
            "category": category,
            "category_label": "纳指" if category == "nasdaq" else "标普",
            "instrument_code": code,
            "instrument_name": instrument.name if instrument else code,
            "instrument_id": instrument.id if instrument else None,
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
    codes = resolve_instrument_codes(config)

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
                    code = codes["mainland" if account_name == "大陆" else "hk"][category]
                    _append_plan_item(
                        items,
                        plan_day,
                        phase,
                        account_name,
                        category,
                        code,
                        account_amount,
                        week_index,
                        db,
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
                        code = codes["mainland" if account_name == "大陆" else "hk"][category]
                        amount = account_weekly * cat_pct / 100
                        _append_plan_item(
                            items,
                            plan_day,
                            phase,
                            account_name,
                            category,
                            code,
                            amount,
                            week_index,
                            db,
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
        if item["plan_date"] >= today and item["status"] not in ("done", "merged")
    ][:8]
    overdue = [item for item in plans if item["status"] == "overdue"]

    building_plans = [item for item in plans if item["phase"] == "building"]
    dca_plans = [item for item in plans if item["phase"] == "dca"]
    building_done = sum(1 for item in building_plans if item["status"] == "done")
    dca_done = sum(1 for item in dca_plans if item["status"] == "done" and item["plan_date"] <= today)
    merged_count = sum(1 for item in plans if item["status"] == "merged" and item["plan_date"] <= today)

    return {
        "upcoming": upcoming,
        "overdue_count": len(overdue),
        "overdue": overdue[:10],
        "merged_count": merged_count,
        "building_total": len(building_plans),
        "building_done": building_done,
        "dca_done": dca_done,
        "next_item": upcoming[0] if upcoming else None,
    }
