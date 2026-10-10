from __future__ import annotations

import json
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import List, Optional, Set, Tuple, Union

from sqlalchemy.orm import Session

from app.models.models import AppConfig, Instrument, Transaction
from app.services.account_plan import (
    building_amount_for_month,
    dca_amount_for_account_month,
    month_index_for_date,
    phase_for_month_index,
    resolve_all_account_settings,
    resolve_unified_plan_settings,
    week_days_for_month,
)
from app.services.dca_schedule_core import dca_amount_for_month
from app.services.allocation import find_instruments_by_account_category
from app.services.allocation_targets import INDEX_CATEGORIES, resolve_account_index_targets
from app.services.config import save_config
from app.services.holdings import CATEGORY_LABELS, get_latest_prices
from app.services.plan_phase import build_phase_investment_summary, compute_account_phase_investment, tx_matches_phase

MATCH_WINDOW_DAYS = 5
AMOUNT_TOLERANCE = 0.2
PLAN_SKIPS_KEY = "plan_skips"
PLAN_DEFERS_KEY = "plan_defers"


def _account_key(account_name: str) -> str:
    return "mainland" if account_name == "大陆" else "hk"


def plan_skip_key(
    plan_date: Union[date, str],
    account: str,
    category: str,
    phase: str,
) -> str:
    if isinstance(plan_date, date):
        plan_date = plan_date.isoformat()
    return f"{plan_date}|{account}|{category}|{phase}"


def _plan_skip_keys_for_item(item: dict) -> List[str]:
    keys = [
        plan_skip_key(item["plan_date"], item["account"], item["category"], item["phase"]),
    ]
    # 兼容旧版跳过记录（无阶段）
    if isinstance(item["plan_date"], date):
        legacy_date = item["plan_date"].isoformat()
    else:
        legacy_date = str(item["plan_date"])
    keys.append(f"{legacy_date}|{item['account']}|{item['category']}")
    return keys


def _save_plan_markers(db: Session, key: str, values: Set[str]) -> None:
    save_config(db, {key: json.dumps(sorted(values), ensure_ascii=False)})


def _load_plan_marker_set(db: Session, key: str) -> Set[str]:
    row = db.query(AppConfig).filter(AppConfig.key == key).first()
    if not row or not row.value:
        return set()
    try:
        data = json.loads(row.value)
    except json.JSONDecodeError:
        return set()
    if not isinstance(data, list):
        return set()
    return {str(item) for item in data}


def load_plan_skips(db: Session) -> Set[str]:
    return _load_plan_marker_set(db, PLAN_SKIPS_KEY)


def load_plan_defers(db: Session) -> Set[str]:
    return _load_plan_marker_set(db, PLAN_DEFERS_KEY)


def _marker_keys(plan_date: date, account: str, category: str, phase: str) -> tuple[str, str]:
    key = plan_skip_key(plan_date, account, category, phase)
    legacy_key = f"{plan_date.isoformat()}|{account}|{category}"
    return key, legacy_key


def set_plan_skip(
    db: Session,
    plan_date: date,
    account: str,
    category: str,
    phase: str,
    skipped: bool,
) -> Set[str]:
    skips = load_plan_skips(db)
    defers = load_plan_defers(db)
    key, legacy_key = _marker_keys(plan_date, account, category, phase)
    if skipped:
        skips.add(key)
        skips.discard(legacy_key)
        defers.discard(key)
        defers.discard(legacy_key)
    else:
        skips.discard(key)
        skips.discard(legacy_key)
    _save_plan_markers(db, PLAN_SKIPS_KEY, skips)
    _save_plan_markers(db, PLAN_DEFERS_KEY, defers)
    return skips


def set_plan_defer(
    db: Session,
    plan_date: date,
    account: str,
    category: str,
    phase: str,
    deferred: bool,
) -> Set[str]:
    skips = load_plan_skips(db)
    defers = load_plan_defers(db)
    key, legacy_key = _marker_keys(plan_date, account, category, phase)
    if deferred:
        defers.add(key)
        defers.discard(legacy_key)
        skips.discard(key)
        skips.discard(legacy_key)
    else:
        defers.discard(key)
        defers.discard(legacy_key)
    _save_plan_markers(db, PLAN_SKIPS_KEY, skips)
    _save_plan_markers(db, PLAN_DEFERS_KEY, defers)
    return defers


def _parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def _month_offset(base: date, offset: int) -> tuple[int, int]:
    month = base.month - 1 + offset
    year = base.year + month // 12
    month = month % 12 + 1
    return year, month


def _group_key(item: dict) -> Tuple[str, str, str]:
    return item["account"], item["category"], item["phase"]


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


def _is_fulfilled(available: float, needed: float) -> bool:
    """容差内视为完成：尾差不再顺延到后续计划。"""
    if needed <= 0:
        return True
    min_needed = needed * (1 - AMOUNT_TOLERANCE)
    return available >= min_needed or _amounts_match(available, needed)


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


def _tx_amount_usd(tx: Transaction, instrument: Instrument) -> float:
    return round(float(tx.amount) + float(tx.fee), 2)


def _hk_whole_share_enabled(config: dict) -> bool:
    raw = config.get("hk_whole_share_only", "1")
    return str(raw) not in ("0", "false", "False", "")


def _share_price_buffer_pct(config: dict) -> float:
    try:
        return float(config.get("hk_share_price_buffer_pct", 2))
    except (TypeError, ValueError):
        return 2.0


def _is_whole_share_tx(tx: Transaction) -> bool:
    qty = float(tx.quantity)
    return qty >= 1 and abs(qty - round(qty)) < 0.001


def _reference_share_price_usd(db: Session, category: str) -> float:
    instruments = find_instruments_by_account_category(db, "香港", category)
    if not instruments:
        return 0.0
    prices = get_latest_prices(db)
    for instrument in instruments:
        price = prices.get(instrument.id)
        if price and float(price) > 0:
            return float(price)
    return 0.0


def _plan_currency(item: dict) -> str:
    return "USD" if item.get("account") == "香港" else "CNY"


def _is_upcoming_plan(item: dict, today: date) -> bool:
    status = item["status"]
    if status in ("done", "merged", "skipped", "deferred", "partial", "overdue"):
        return False
    if status == "ready":
        return True
    if status in ("pending", "today", "accumulating"):
        return item["plan_date"] >= today
    return item["plan_date"] >= today


def _upcoming_plans(plans: List[dict], today: date, limit: Optional[int] = None) -> List[dict]:
    """即将执行：未到期计划、今天的计划，以及每个香港整股池最新的一笔可买入。"""
    ready_latest: dict[Tuple[str, str], dict] = {}
    selected: List[dict] = []
    for item in plans:
        if item["status"] == "ready" and item.get("whole_share_mode"):
            key = (item["account"], item["category"])
            current = ready_latest.get(key)
            if current is None or item["plan_date"] >= current["plan_date"]:
                ready_latest[key] = item
            continue
        if _is_upcoming_plan(item, today):
            selected.append(item)
    selected.extend(ready_latest.values())
    selected.sort(key=lambda row: (
        0 if row["status"] == "ready" else 1,
        row["plan_date"],
        row["account"],
        row["category"],
    ))
    if limit is not None:
        return selected[:limit]
    return selected


def _init_plan_fields(item: dict) -> None:
    if "base_amount_cny" not in item:
        item["base_amount_cny"] = round(float(item["amount_cny"]), 2)
    item["rolled_over_amount_cny"] = 0.0
    item["rolled_over_count"] = 0
    item["matched_amount_cny"] = 0.0
    item["shortfall_cny"] = 0.0
    item["credit_offset_cny"] = 0.0
    item["month_base_cny"] = 0.0
    item["month_net_cny"] = 0.0
    item["month_matched_cny"] = 0.0
    item["month_remaining_cny"] = 0.0
    item["is_month_anchor"] = False
    if "currency" not in item:
        item["currency"] = _plan_currency(item)
    item["whole_share_mode"] = False
    item["execution_pool_usd"] = 0.0
    item["share_reference_price_usd"] = 0.0
    item["share_threshold_usd"] = 0.0
    item["executable_shares"] = 0


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
    """根据窗口内可用金额更新计划状态，返回记入本计划的匹配金额。"""
    if needed <= 0 or _is_fulfilled(available, needed):
        plan["status"] = "done"
        plan["matched_amount_cny"] = round(available, 2)
        plan["shortfall_cny"] = 0.0
        return round(available, 2)

    if available > 0 and window_closed:
        plan["status"] = "partial"
        plan["matched_amount_cny"] = round(available, 2)
        plan["shortfall_cny"] = round(max(0.0, needed - available), 2)
        return available

    if available > 0:
        today = date.today()
        if not window_closed:
            if plan["plan_date"] < today:
                plan["status"] = "partial"
            elif plan["plan_date"] == today:
                plan["status"] = "today"
            else:
                plan["status"] = "pending"
        else:
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
    phase: str,
    config: dict,
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
    txs = [tx for tx in txs if tx_matches_phase(tx, phase, config)]
    tx_remaining = {
        tx.id: _tx_amount_cny(tx, instrument_by_id[tx.instrument_id], usd_cny_rate) for tx in txs
    }
    return txs, instrument_by_id, tx_remaining


def _consume_in_range(
    txs: list[Transaction],
    tx_remaining: dict[int, float],
    range_start: date,
    range_end: date,
    cap: Optional[float] = None,
) -> float:
    if cap is not None and cap <= 0:
        return 0.0

    consumed = 0.0
    for tx in txs:
        if cap is not None and consumed >= cap:
            break
        if not (range_start <= tx.trade_date <= range_end):
            continue
        remaining = tx_remaining.get(tx.id, 0.0)
        if remaining <= 0:
            continue
        take = remaining if cap is None else min(remaining, cap - consumed)
        consumed += take
        tx_remaining[tx.id] = remaining - take
    return round(consumed, 2)


def _match_dca_group_by_week(
    group: List[dict],
    db: Session,
    config: dict,
    usd_cny_rate: float,
) -> None:
    min_date = group[0]["plan_date"] - timedelta(days=MATCH_WINDOW_DAYS)
    max_date = group[-1]["plan_date"] + timedelta(days=MATCH_WINDOW_DAYS)
    txs, _, tx_remaining = _load_group_transactions(
        db,
        group[0]["account"],
        group[0]["category"],
        group[0]["phase"],
        config,
        min_date,
        max_date,
        usd_cny_rate,
    )
    if not txs:
        for row in group:
            if row["status"] in ("done", "deferred"):
                continue
            row["status"] = _date_status(row["plan_date"])
        return

    pending_debit = 0.0
    pending_credit = 0.0

    for plan in group:
        if plan["status"] in ("done", "skipped", "deferred"):
            continue

        base_needed = float(plan["base_amount_cny"])
        defer_in = float(plan.get("rolled_over_amount_cny") or 0)
        defer_count = int(plan.get("rolled_over_count") or 0)
        rollover_in = pending_debit
        gross_needed = base_needed + rollover_in + defer_in
        credit_applied = min(pending_credit, gross_needed)
        net_needed = round(gross_needed - credit_applied, 2)
        pending_credit = round(pending_credit - credit_applied, 2)
        pending_debit = 0.0

        if rollover_in > 0 or defer_in > 0:
            plan["rolled_over_amount_cny"] = round(defer_in + rollover_in, 2)
            plan["rolled_over_count"] = defer_count + (1 if rollover_in > 0 else 0)
        if credit_applied > 0:
            plan["credit_offset_cny"] = round(credit_applied, 2)
            plan["adjustment_note"] = f"含上期结余抵扣 {round(credit_applied, 2)} 元"
        if defer_in > 0 and rollover_in == 0 and credit_applied == 0:
            pass
        elif rollover_in > 0 or credit_applied > 0 or defer_in > 0:
            plan["amount_cny"] = round(max(0.0, net_needed), 2)

        if net_needed <= 0:
            _apply_match_result(plan, 0.0, 0.0, True)
            continue

        window_start, window_end = _window_bounds(plan["plan_date"])
        consumed = _consume_in_range(txs, tx_remaining, window_start, window_end)
        fulfilled = _is_fulfilled(consumed, net_needed)
        _apply_match_result(
            plan,
            net_needed,
            consumed,
            _window_closed(plan["plan_date"]),
        )
        if consumed > net_needed:
            pending_credit = round(pending_credit + consumed - net_needed, 2)
        elif (
            _window_closed(plan["plan_date"])
            and consumed < net_needed
            and not fulfilled
        ):
            # 仅明显不足才顺延；容差内完成的尾差不再滚到下一笔
            pending_debit = round(net_needed - consumed, 2)


def _load_hk_group_transactions_usd(
    db: Session,
    account_name: str,
    category: str,
    config: dict,
    min_date: date,
    max_date: date,
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
    txs = [
        tx
        for tx in txs
        if tx_matches_phase(tx, "building", config) or tx_matches_phase(tx, "dca", config)
    ]
    tx_remaining = {
        tx.id: _tx_amount_usd(tx, instrument_by_id[tx.instrument_id]) for tx in txs
    }
    return txs, instrument_by_id, tx_remaining


def _append_pool_note(plan: dict, note: str) -> None:
    existing = plan.get("adjustment_note")
    if existing:
        if note in existing:
            return
        plan["adjustment_note"] = f"{existing}；{note}"
    else:
        plan["adjustment_note"] = note


def _assign_hk_whole_share_status(
    plan: dict,
    pool: float,
    threshold: float,
    ref_price: float,
    consumed_usd: float,
    today: date,
    elapsed: bool,
) -> None:
    plan["execution_pool_usd"] = round(pool, 2)
    plan["share_reference_price_usd"] = round(ref_price, 3)
    plan["share_threshold_usd"] = round(threshold, 2)
    plan["executable_shares"] = int(pool // ref_price) if ref_price > 0 else 0
    plan["whole_share_mode"] = True
    plan["currency"] = "USD"

    if not elapsed:
        plan["status"] = "pending"
        plan["shortfall_cny"] = 0.0
        due = float(plan.get("base_amount_cny") or 0)
        if pool > 0 and ref_price > 0:
            _append_pool_note(
                plan,
                f"当前池 ${round(pool, 2)}（到期后 +${round(due, 2)}）",
            )
        return

    if consumed_usd > 0:
        plan["matched_amount_cny"] = round(consumed_usd, 2)
        plan["shortfall_cny"] = 0.0
        if pool < threshold:
            plan["status"] = "done"
            return
        plan["status"] = "ready"
        if plan["executable_shares"] > 0:
            _append_pool_note(
                plan,
                f"可买 {plan['executable_shares']} 股（约 ${round(ref_price, 3)}/股，手动执行）",
            )
        return

    if ref_price <= 0:
        plan["status"] = "accumulating"
        _append_pool_note(plan, "请先更新香港品种行情")
        return

    if pool >= threshold:
        plan["status"] = "ready" if plan["plan_date"] <= today else "pending"
        plan["shortfall_cny"] = 0.0
        if plan["executable_shares"] > 0:
            _append_pool_note(
                plan,
                f"可买 {plan['executable_shares']} 股（约 ${round(ref_price, 3)}/股，手动执行）",
            )
        return

    if pool > 0:
        plan["status"] = "accumulating"
        plan["shortfall_cny"] = 0.0
        _append_pool_note(
            plan,
            f"池 ${round(pool, 2)} / ${round(threshold, 2)}，还差 ${round(max(0.0, threshold - pool), 2)}",
        )
        return

    if plan["plan_date"] > today:
        plan["status"] = "pending"
    elif plan["plan_date"] == today:
        plan["status"] = "today"
    else:
        plan["status"] = "accumulating"


def _publish_current_hk_pool(group: List[dict], pool: float, ref_price: float, threshold: float, today: date) -> None:
    """可买入显示的是含已延期金额的当前执行池，而不是上一期快照。"""
    shown = None
    for plan in group:
        if plan.get("status") in ("skipped", "deferred", "done", "partial"):
            continue
        if plan["plan_date"] > today or not plan.get("whole_share_mode"):
            continue
        if shown is None or plan["plan_date"] >= shown["plan_date"]:
            shown = plan
    if shown is None:
        return

    shown["execution_pool_usd"] = round(pool, 2)
    shown["share_reference_price_usd"] = round(ref_price, 3)
    shown["share_threshold_usd"] = round(threshold, 2)
    shown["executable_shares"] = int(pool // ref_price) if ref_price > 0 else 0
    if ref_price <= 0:
        return
    if pool >= threshold and shown["executable_shares"] > 0:
        shown["status"] = "ready"
        fresh = f"可买 {shown['executable_shares']} 股（约 ${round(ref_price, 3)}/股，手动执行）"
    elif pool > 0:
        shown["status"] = "accumulating"
        fresh = f"池 ${round(pool, 2)} / ${round(threshold, 2)}，还差 ${round(max(0.0, threshold - pool), 2)}"
    else:
        return
    existing = shown.get("adjustment_note") or ""
    parts = [
        part for part in existing.split("；")
        if part and not part.startswith("可买 ") and not part.startswith("池 $")
    ]
    parts.append(fresh)
    shown["adjustment_note"] = "；".join(parts)


def _match_hk_whole_share_group(
    group: List[dict],
    db: Session,
    config: dict,
    usd_cny_rate: float,
) -> None:
    """香港整股模式：金额计划逐期注入执行池，达门槛后可买 floor(pool/price) 股。"""
    del usd_cny_rate  # 香港整股池按美元记账

    category = group[0]["category"]
    ref_price = _reference_share_price_usd(db, category)
    buffer_pct = _share_price_buffer_pct(config)
    threshold = ref_price * (1 + buffer_pct / 100) if ref_price > 0 else 0.0

    min_date = group[0]["plan_date"] - timedelta(days=MATCH_WINDOW_DAYS)
    max_date = group[-1]["plan_date"] + timedelta(days=MATCH_WINDOW_DAYS)
    txs, _, tx_remaining = _load_hk_group_transactions_usd(
        db,
        group[0]["account"],
        category,
        config,
        min_date,
        max_date,
    )

    pool = 0.0
    credit = 0.0
    today = date.today()

    for plan in group:
        if plan.get("status") == "skipped":
            continue
        deferred = plan.get("status") == "deferred"

        # 延期金额已在原计划到期时进入执行池，这里只加本期自身金额，避免加第二次。
        base = float(plan["base_amount_cny"])
        elapsed = plan["plan_date"] <= today
        credit_applied = min(credit, base) if elapsed else 0.0
        net_contribution = base - credit_applied if elapsed else 0.0
        if elapsed:
            credit -= credit_applied

        if credit_applied > 0 and not deferred:
            _append_pool_note(plan, f"含上期结余抵扣 ${round(credit_applied, 2)}")

        if elapsed:
            pool += net_contribution

        consumed_usd = 0.0
        if elapsed:
            window_end = plan["plan_date"] + timedelta(days=MATCH_WINDOW_DAYS)
            for tx in txs:
                if tx.trade_date > window_end:
                    break
                remaining = tx_remaining.get(tx.id, 0.0)
                if remaining <= 0:
                    continue
                if not _is_whole_share_tx(tx):
                    continue
                consumed_usd += remaining
                tx_remaining[tx.id] = 0.0
                pool -= remaining
                if pool < 0:
                    credit += -pool
                    pool = 0.0

        if deferred:
            plan["execution_pool_usd"] = round(pool, 2)
            plan["whole_share_mode"] = True
            plan["currency"] = "USD"
            plan["status"] = "deferred"
            continue

        _assign_hk_whole_share_status(plan, pool, threshold, ref_price, consumed_usd, today, elapsed)

    _publish_current_hk_pool(group, pool, ref_price, threshold, today)


def _group_match_transactions(items: List[dict], db: Session, config: dict, usd_cny_rate: float) -> None:
    """建仓/定投均按周 ±5 天核销；香港整股模式按账户+大类共用执行池。"""
    by_group: dict[Tuple[str, str, str], List[dict]] = defaultdict(list)
    hk_groups: dict[Tuple[str, str], List[dict]] = defaultdict(list)
    hk_enabled = _hk_whole_share_enabled(config)

    for item in items:
        if item["account"] == "香港" and hk_enabled:
            hk_groups[(item["account"], item["category"])].append(item)
        else:
            by_group[_group_key(item)].append(item)

    for group in hk_groups.values():
        group.sort(key=lambda row: (row["plan_date"], 0 if row["phase"] == "building" else 1))
        _match_hk_whole_share_group(group, db, config, usd_cny_rate)

    for group in by_group.values():
        if group[0]["account"] == "香港" and hk_enabled:
            continue
        group.sort(key=lambda row: row["plan_date"])
        _match_dca_group_by_week(group, db, config, usd_cny_rate)


def _apply_defers(items: List[dict], defers: Set[str], skips: Set[str], today: date) -> None:
    """手动延期：金额加到同一账户、标的、阶段的下一笔未到期计划。"""
    groups: dict[Tuple[str, str, str], List[dict]] = defaultdict(list)
    for item in items:
        groups[_group_key(item)].append(item)

    for group in groups.values():
        group.sort(key=lambda row: row["plan_date"])
        carry = 0.0
        carry_count = 0
        for plan in group:
            keys = _plan_skip_keys_for_item(plan)
            if any(key in skips for key in keys):
                continue
            if plan.get("status") == "done" or not any(key in defers for key in keys):
                if carry > 0 and plan["plan_date"] >= today:
                    plan["rolled_over_amount_cny"] = round(
                        float(plan.get("rolled_over_amount_cny") or 0) + carry,
                        2,
                    )
                    plan["rolled_over_count"] = int(plan.get("rolled_over_count") or 0) + carry_count
                    plan["amount_cny"] = round(float(plan["amount_cny"]) + carry, 2)
                    carry = 0.0
                    carry_count = 0
                continue
            carry += float(plan["amount_cny"])
            carry_count += 1
            plan["status"] = "deferred"
            plan["shortfall_cny"] = 0.0
            plan["matched_amount_cny"] = 0.0
            plan["rolled_over_amount_cny"] = 0.0
            plan["rolled_over_count"] = 0
            plan["adjustment_note"] = "已延期，金额顺延到后续未到期计划"
        if carry > 0:
            for plan in group:
                if plan.get("status") == "deferred":
                    plan["adjustment_note"] = "已延期，没有更晚的计划可顺延"


def _apply_skips(items: List[dict], skips: Set[str]) -> None:
    """手动跳过：不要求补投，差额/全额均不顺延到下一笔。"""
    for item in items:
        if not any(key in skips for key in _plan_skip_keys_for_item(item)):
            continue
        if item["status"] == "done":
            continue
        item["status"] = "skipped"
        item["shortfall_cny"] = 0.0


def _apply_rollover_merges(items: List[dict]) -> None:
    """顺延已在按周匹配中处理，保留函数以兼容调用。"""
    return


def _rematch_rolled_targets(items: List[dict], db: Session, config: dict, usd_cny_rate: float) -> None:
    """顺延已在按周匹配中处理，保留函数以兼容调用。"""
    return


def _finalize_plan_items(items: List[dict], db: Session, config: dict, usd_cny_rate: float) -> List[dict]:
    for item in items:
        _init_plan_fields(item)
        item["status"] = _date_status(item["plan_date"])

    today = date.today()
    skips = load_plan_skips(db)
    _apply_defers(items, load_plan_defers(db), skips, today)
    _group_match_transactions(items, db, config, usd_cny_rate)
    _apply_skips(items, skips)
    _apply_rollover_merges(items)
    _rematch_rolled_targets(items, db, config, usd_cny_rate)
    return items


def _is_truthy_config(value) -> bool:
    return str(value) in ("1", "true", "True")


def _default_dca_category_weights(config: dict, account_key: str) -> dict[str, float]:
    nasdaq_pct, sp500_pct = resolve_account_index_targets(config, account_key)
    total = nasdaq_pct + sp500_pct
    if total <= 0:
        return {"nasdaq": 0.5, "sp500": 0.5}
    return {
        "nasdaq": nasdaq_pct / total,
        "sp500": sp500_pct / total,
    }


def _resolve_account_dca_weights(
    config: dict,
    account_key: str,
    global_weights: dict[str, float],
    using_rebalance: bool,
    tilt_note: Optional[str],
) -> tuple[dict[str, float], bool, Optional[str], Optional[str]]:
    """定投权重：常态按账户内纳指/标普；倾斜按全仓低配方向，账户不支持的大类保持原比例。"""
    default_weights = _default_dca_category_weights(config, account_key)
    if not using_rebalance:
        return default_weights, False, None, None

    tilt_target = next((cat for cat, weight in global_weights.items() if weight >= 0.999), None)
    if not tilt_target:
        return default_weights, False, None, None

    nasdaq_pct, sp500_pct = resolve_account_index_targets(config, account_key)
    if tilt_target == "nasdaq" and nasdaq_pct <= 0:
        return default_weights, False, None, None
    if tilt_target == "sp500" and sp500_pct <= 0:
        return default_weights, False, None, None

    active_weights = {
        category: (1.0 if category == tilt_target else 0.0)
        for category in INDEX_CATEGORIES
    }
    return active_weights, True, tilt_note, tilt_target


def _compute_dca_category_weights(
    config: dict,
    category_allocations: list[dict],
) -> tuple[dict[str, float], dict[str, float], bool, Optional[str]]:
    """按全仓偏离度计算定投大类权重；超阈值时 100% 投偏离最大的一类。"""
    threshold = float(config.get("plan_rebalance_threshold", 5))
    default_weights = _default_dca_category_weights(config, "mainland")
    gap_by_category = {item["category"]: float(item["gap_pct"]) for item in category_allocations}

    candidates = [
        (category, gap_by_category.get(category, 0.0))
        for category in INDEX_CATEGORIES
        if gap_by_category.get(category, 0.0) > threshold
    ]
    if not candidates:
        return _default_dca_category_weights(config, "mainland"), gap_by_category, False, None

    most_under_category, most_under_gap = max(candidates, key=lambda item: item[1])
    weights = {
        category: (1.0 if category == most_under_category else 0.0)
        for category in INDEX_CATEGORIES
    }
    label = _category_label(most_under_category)
    tilt_note = f"全仓{label}偏低 {most_under_gap:.2f}%，本月定投 100% 投{label}"
    return weights, gap_by_category, True, tilt_note


def _dca_adjustment_note(
    category: str,
    gap: float,
    threshold: float,
    tilt_note: Optional[str] = None,
    is_tilt_target: bool = False,
) -> Optional[str]:
    if tilt_note and is_tilt_target:
        return tilt_note
    if gap <= threshold:
        return None
    return f"{_category_label(category)}偏低 {gap:.2f}%"


def _append_plan_item(
    items: List[dict],
    plan_date: date,
    phase: str,
    account_name: str,
    category: str,
    amount_cny: float,
    week_index: int,
    base_amount_cny: Optional[float] = None,
    adjustment_note: Optional[str] = None,
    dca_boost_cny: Optional[float] = None,
    month_boost_cny: Optional[float] = None,
    boost_note: Optional[str] = None,
) -> None:
    if amount_cny <= 0:
        return

    item = {
        "plan_date": plan_date,
        "phase": phase,
        "phase_label": "建仓" if phase == "building" else "定投",
        "week_index": week_index,
        "account": account_name,
        "category": category,
        "category_label": _category_label(category),
        "target_label": _target_label(account_name, category),
        "amount_cny": round(amount_cny, 2),
        "currency": "USD" if account_name == "香港" else "CNY",
        "status": "pending",
        "dca_boost_cny": 0.0,
        "month_boost_cny": 0.0,
    }
    if base_amount_cny is not None:
        item["base_amount_cny"] = round(base_amount_cny, 2)
    notes = []
    if adjustment_note:
        notes.append(adjustment_note)
    if boost_note:
        notes.append(boost_note)
    if notes:
        item["adjustment_note"] = "；".join(notes)
    if dca_boost_cny is not None and dca_boost_cny > 0:
        item["dca_boost_cny"] = round(dca_boost_cny, 2)
    if month_boost_cny is not None and month_boost_cny > 0:
        item["month_boost_cny"] = round(month_boost_cny, 2)
    items.append(item)


def _generate_account_plans(
    db: Session,
    config: dict,
    settings: dict,
    horizon_years: int,
    gen_start: date,
    gen_end: date,
    nasdaq_pct: float,
    sp500_pct: float,
    rebalance_enabled: bool,
    using_rebalance: bool,
    category_weights: dict[str, float],
    default_weights: dict[str, float],
    gap_by_category: dict[str, float],
    rebalance_threshold: float,
    account_monthly_boost: float = 0.0,
    boost_note: Optional[str] = None,
    tilt_note: Optional[str] = None,
    tilt_target_category: Optional[str] = None,
    ma_factors: Optional[dict] = None,
) -> List[dict]:
    account_name = settings["account"]
    plan_start = settings["plan_start"]
    building_months = settings["building_months"]
    market = settings["market"]
    weeks_per_month = settings["weeks_per_month"]
    total_months = horizon_years * 12 + building_months + 1
    items: List[dict] = []

    for month_index in range(total_months):
        year, month = _month_offset(plan_start, month_index)
        phase = phase_for_month_index(settings, month_index)
        if phase == "building":
            monthly_amount = building_amount_for_month(settings, month_index)
            if monthly_amount <= 0:
                continue
        else:
            monthly_amount = dca_amount_for_account_month(
                config,
                settings["key"],
                settings["dca_amount_schedule"],
                year,
                month,
            )

        today = date.today()
        is_current_month = year == today.year and month == today.month
        month_boost = account_monthly_boost if is_current_month else 0.0
        base_monthly_amount = monthly_amount
        if phase == "dca" and month_boost > 0:
            monthly_amount += month_boost

        week_days = week_days_for_month(year, month, market, weeks_per_month)
        if not week_days:
            continue

        active_week_days = week_days
        if month_index == 0:
            active_week_days = [day for day in week_days if day >= plan_start]
        if not active_week_days:
            continue

        weekly_divisor = max(len(active_week_days), weeks_per_month)
        weekly_pool = monthly_amount / len(active_week_days)

        for week_index, plan_day in enumerate(active_week_days, start=1):
            if plan_day < gen_start or plan_day > gen_end:
                continue

            if phase == "dca":
                weekly_amount = monthly_amount / weekly_divisor
                weekly_base = base_monthly_amount / weekly_divisor
                weekly_boost = month_boost / weekly_divisor if month_boost > 0 else 0.0
                show_boost_note = bool(boost_note and week_index == 1 and month_boost > 0)
                month_ma = ma_factors if (ma_factors and is_current_month) else None

                def _ma_for(category: str) -> Tuple[float, Optional[str]]:
                    if not month_ma:
                        return 1.0, None
                    info = month_ma.get(category) or {}
                    factor = info.get("factor", 1.0) or 1.0
                    return factor, info.get("note")

                if rebalance_enabled:
                    active_weights = category_weights if using_rebalance else default_weights
                    for category in INDEX_CATEGORIES:
                        cat_fraction = active_weights.get(category, 0)
                        if cat_fraction <= 0:
                            continue
                        base_portion = weekly_base * cat_fraction
                        boost_portion = weekly_boost * cat_fraction
                        ma_factor, ma_note = _ma_for(category)
                        ma_active = abs(ma_factor - 1.0) > 1e-6
                        amount = base_portion * ma_factor + boost_portion

                        note_parts: List[str] = []
                        if using_rebalance:
                            gap_note = _dca_adjustment_note(
                                category,
                                gap_by_category.get(category, 0),
                                rebalance_threshold,
                                tilt_note=tilt_note,
                                is_tilt_target=category == tilt_target_category,
                            )
                            if gap_note:
                                note_parts.append(gap_note)
                        if ma_active and ma_note and week_index == 1:
                            note_parts.append(ma_note)
                        note = "；".join(note_parts) if note_parts else None

                        if using_rebalance:
                            base_display = weekly_base * default_weights.get(category, 0)
                        elif ma_active:
                            base_display = base_portion
                        else:
                            base_display = None

                        _append_plan_item(
                            items,
                            plan_day,
                            phase,
                            account_name,
                            category,
                            amount,
                            week_index,
                            base_amount_cny=base_display,
                            adjustment_note=note,
                            dca_boost_cny=boost_portion if boost_portion > 0 else None,
                            month_boost_cny=month_boost if show_boost_note else None,
                            boost_note=boost_note if show_boost_note else None,
                        )
                else:
                    for category, cat_fraction in default_weights.items():
                        if cat_fraction <= 0:
                            continue
                        base_portion = weekly_base * cat_fraction
                        boost_portion = weekly_boost * cat_fraction
                        ma_factor, ma_note = _ma_for(category)
                        ma_active = abs(ma_factor - 1.0) > 1e-6
                        amount = base_portion * ma_factor + boost_portion
                        note = ma_note if (ma_active and week_index == 1) else None
                        _append_plan_item(
                            items,
                            plan_day,
                            phase,
                            account_name,
                            category,
                            amount,
                            week_index,
                            base_amount_cny=base_portion if ma_active else None,
                            adjustment_note=note,
                        )
            else:
                for category, cat_pct in (("nasdaq", nasdaq_pct), ("sp500", sp500_pct)):
                    if cat_pct <= 0:
                        continue
                    amount = weekly_pool * cat_pct / 100
                    _append_plan_item(
                        items,
                        plan_day,
                        phase,
                        account_name,
                        category,
                        amount,
                        week_index,
                    )

    return items


def generate_investment_plans(
    db: Session,
    config: dict,
    start: Optional[date] = None,
    end: Optional[date] = None,
    metrics_for_rebalance: bool = True,
) -> List[dict]:
    from app.services.config import save_config
    from app.services.dca_amount_schedule import ensure_plan_schedule_updates

    schedule_updates = ensure_plan_schedule_updates(config)
    if schedule_updates:
        save_config(db, schedule_updates)
        config = {**config, **schedule_updates}

    horizon_years = int(config.get("plan_horizon_years", 20))
    usd_cny_rate = float(config.get("usd_cny_rate", 7.2))

    account_settings = resolve_all_account_settings(config)
    unified = resolve_unified_plan_settings(config)
    if end is None:
        gen_start = unified["plan_start"]
        gen_end = gen_start
        end_year, end_month = _month_offset(
            unified["plan_start"],
            horizon_years * 12 + unified["building_months"],
        )
        gen_end = date(end_year, end_month, 1)
    else:
        gen_end = end
        gen_start = unified["plan_start"]

    display_start = start
    display_end = end
    items: List[dict] = []

    rebalance_enabled = _is_truthy_config(config.get("plan_rebalance_enabled", "1"))
    rebalance_threshold = float(config.get("plan_rebalance_threshold", 5))
    metrics = None
    if rebalance_enabled and metrics_for_rebalance:
        from app.services.returns import compute_dashboard_metrics

        metrics = compute_dashboard_metrics(db, config, scope="index")

    global_category_allocations = metrics.get("category_allocations") or [] if metrics else []
    category_weights = _default_dca_category_weights(config, "mainland")
    gap_by_category: dict[str, float] = {}
    using_rebalance = False
    tilt_note: Optional[str] = None
    tilt_target_category: Optional[str] = None
    if metrics is not None:
        category_weights, gap_by_category, using_rebalance, tilt_note = _compute_dca_category_weights(
            config,
            global_category_allocations,
        )
        if using_rebalance:
            for category, weight in category_weights.items():
                if weight >= 0.999:
                    tilt_target_category = category
                    break

    from app.services.dca_drawdown import compute_drawdown_boost
    from app.services.dca_ma_factor import resolve_current_month_factors

    today = date.today()
    ma_factors = resolve_current_month_factors(config, today)
    account_boost_map: dict[str, float] = {}
    unified_settings = account_settings["大陆"]
    month_index = month_index_for_date(unified_settings["plan_start"], today)
    total_dca_current_month = 0.0
    if phase_for_month_index(unified_settings, month_index) == "dca":
        total_dca_current_month = dca_amount_for_month(
            unified["dca_amount_schedule"],
            today.year,
            today.month,
        )

    boost_info = compute_drawdown_boost(
        db, config, today, base_amount_cny=total_dca_current_month
    )

    for account_name, settings in account_settings.items():
        if phase_for_month_index(settings, month_index) != "dca":
            account_boost_map[account_name] = 0.0
            continue
        monthly_cny = dca_amount_for_account_month(
            config,
            settings["key"],
            settings["dca_amount_schedule"],
            today.year,
            today.month,
        )
        if settings["key"] == "hk":
            rate = float(config.get("usd_cny_rate", 7.2))
            monthly_cny_equiv = monthly_cny * rate
        else:
            monthly_cny_equiv = monthly_cny
        share = monthly_cny_equiv / total_dca_current_month if total_dca_current_month > 0 else 0.0
        account_boost_map[account_name] = round(boost_info["applied_amount"] * share, 2)

    for settings in account_settings.values():
        account_key = settings["key"]
        nasdaq_pct, sp500_pct = resolve_account_index_targets(config, account_key)
        default_weights = _default_dca_category_weights(config, account_key)
        active_weights, account_using_rebalance, account_tilt_note, account_tilt_target = (
            _resolve_account_dca_weights(
                config,
                account_key,
                category_weights,
                using_rebalance,
                tilt_note,
            )
        )
        items.extend(
            _generate_account_plans(
                db,
                config,
                settings,
                horizon_years,
                gen_start,
                gen_end,
                nasdaq_pct,
                sp500_pct,
                rebalance_enabled,
                account_using_rebalance,
                active_weights,
                default_weights,
                gap_by_category,
                rebalance_threshold,
                account_monthly_boost=account_boost_map.get(settings["account"], 0.0),
                boost_note=boost_info.get("note"),
                tilt_note=account_tilt_note,
                tilt_target_category=account_tilt_target,
                ma_factors=ma_factors,
            )
        )

    items.sort(key=lambda item: (item["plan_date"], item["account"], item["category"]))
    items = _finalize_plan_items(items, db, config, usd_cny_rate)

    if display_start is not None:
        items = [item for item in items if item["plan_date"] >= display_start]
    if display_end is not None:
        items = [item for item in items if item["plan_date"] <= display_end]

    return items


def _account_plan_stats(
    plans: List[dict],
    account: str,
    today: date,
    settings: Optional[dict] = None,
) -> dict:
    account_plans = [item for item in plans if item["account"] == account]
    building_plans = [item for item in account_plans if item["phase"] == "building"]
    dca_plans = [item for item in account_plans if item["phase"] == "dca"]
    dca_elapsed = [item for item in dca_plans if item["plan_date"] <= today]
    dca_skipped = sum(1 for item in dca_elapsed if item["status"] in ("skipped", "deferred"))
    dca_done = sum(1 for item in dca_elapsed if item["status"] == "done")
    dca_partial = sum(1 for item in dca_elapsed if item["status"] == "partial")
    dca_denominator = max(len(dca_elapsed) - dca_skipped, 0)
    overdue = [item for item in account_plans if item["status"] == "overdue"]
    upcoming = _upcoming_plans(account_plans, today)

    building_matched = dca_matched = 0.0
    for item in account_plans:
        if item["plan_date"] > today:
            continue
        matched = float(item.get("matched_amount_cny", 0))
        if item["phase"] == "building" and item["status"] in ("done", "partial"):
            building_matched += matched
        elif item["phase"] == "dca" and item["status"] in ("done", "partial"):
            dca_matched += matched

    building_planned_amount = sum(
        float(item.get("base_amount_cny", item.get("amount_cny", 0)))
        for item in building_plans
    )

    return {
        "account": account,
        "building_total": len(building_plans),
        "building_done": sum(1 for item in building_plans if item["status"] == "done"),
        "dca_elapsed": len(dca_elapsed),
        "dca_done": dca_done,
        "dca_partial": dca_partial,
        "dca_execution_rate": round(dca_done / dca_denominator * 100, 1) if dca_denominator else 0.0,
        "overdue_count": len(overdue),
        "building_matched_cny": round(building_matched, 2),
        "dca_matched_cny": round(dca_matched, 2),
        "building_target_amount": settings.get("building_target_amount") if settings else None,
        "building_planned_amount": round(building_planned_amount, 2),
        "next_item": upcoming[0] if upcoming else None,
    }


def build_plan_overview(db: Session, config: dict) -> dict:
    plans = generate_investment_plans(db, config)
    today = date.today()
    usd_cny_rate = float(config.get("usd_cny_rate", 7.2))
    upcoming = _upcoming_plans(plans, today, limit=8)
    overdue = [item for item in plans if item["status"] == "overdue"]

    building_plans = [item for item in plans if item["phase"] == "building"]
    dca_plans = [item for item in plans if item["phase"] == "dca"]
    dca_elapsed = [item for item in dca_plans if item["plan_date"] <= today]
    building_done = sum(1 for item in building_plans if item["status"] == "done")
    dca_done = sum(1 for item in dca_elapsed if item["status"] == "done")
    dca_partial = sum(1 for item in dca_elapsed if item["status"] == "partial")
    dca_skipped = sum(1 for item in dca_elapsed if item["status"] in ("skipped", "deferred"))
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
        and item["status"] in ("done", "partial", "merged", "overdue", "skipped", "deferred", "accumulating", "ready")
    ]
    history.sort(key=lambda item: (item["plan_date"], item["account"], item["category"]), reverse=True)
    history = history[:100]

    plan_stats = build_phase_investment_summary(db, config, plans, today, usd_cny_rate, scope="index")
    phase_investment = plan_stats

    account_summaries = []
    account_settings = resolve_all_account_settings(config)
    for account_name in ("大陆", "香港"):
        summary = _account_plan_stats(
            plans,
            account_name,
            today,
            account_settings.get(account_name),
        )
        account_tx = compute_account_phase_investment(db, config, account_name, usd_cny_rate)
        summary.update(account_tx)
        account_summaries.append(summary)

    dca_tilt_active = False
    if _is_truthy_config(config.get("plan_rebalance_enabled", "1")):
        from app.services.returns import compute_dashboard_metrics

        metrics = compute_dashboard_metrics(db, config, scope="index")
        _, _, dca_tilt_active, _ = _compute_dca_category_weights(
            config,
            metrics.get("category_allocations") or [],
        )

    from app.services.dca_drawdown import compute_drawdown_boost
    from app.services.dca_ma_factor import resolve_current_month_factors

    ma_factors = resolve_current_month_factors(config, today)
    dca_ma = [
        {
            "category": category,
            "category_label": _category_label(category),
            "factor": info.get("factor", 1.0),
            "deviation_pct": info.get("deviation_pct"),
            "note": info.get("note"),
        }
        for category, info in ma_factors.items()
    ]

    overview_base = 0.0
    try:
        unified = account_settings["大陆"]
        overview_base = dca_amount_for_month(
            unified["dca_amount_schedule"], today.year, today.month
        )
    except Exception:
        overview_base = 0.0

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
        "phase_investment": phase_investment,
        "account_summaries": account_summaries,
        "dca_boost": compute_drawdown_boost(db, config, today, base_amount_cny=overview_base),
        "dca_ma": dca_ma,
        "dca_tilt_active": dca_tilt_active,
    }
