from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Tuple

from app.services.index_history import (
    NASDAQ_SYMBOL,
    SP500_SYMBOL,
    VIX_SYMBOL,
    clear_daily_series_cache,
    fetch_remote_daily_series,
    fetch_remote_full_series,
    get_daily_cache_status,
)

DATA_PATH = Path(__file__).resolve().parents[2] / "data" / "index_monthly.json"
SUPPORTED_SYMBOLS = (NASDAQ_SYMBOL, SP500_SYMBOL, VIX_SYMBOL)


def _month_key(item: Tuple[int, int]) -> str:
    return f"{item[0]}-{item[1]:02d}"


def _read_payload() -> Dict[str, Dict[str, float]]:
    if not DATA_PATH.exists():
        return {}
    raw = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    out: Dict[str, Dict[str, float]] = {}
    for symbol, series in raw.items():
        if not isinstance(series, dict):
            continue
        out[symbol] = {str(k): float(v) for k, v in series.items()}
    return out


def _write_payload(payload: Dict[str, Dict[str, float]]) -> None:
    DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    normalized: Dict[str, Dict[str, float]] = {}
    for symbol, series in payload.items():
        ordered = dict(sorted(series.items(), key=lambda kv: kv[0]))
        normalized[symbol] = {k: round(float(v), 6) for k, v in ordered.items()}

    text = json.dumps(normalized, ensure_ascii=False, indent=2) + "\n"
    tmp = DATA_PATH.with_suffix(".json.tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(DATA_PATH)


def get_index_data_status() -> dict:
    payload = _read_payload()
    daily_status = get_daily_cache_status()
    symbols: List[dict] = []
    for symbol in SUPPORTED_SYMBOLS:
        series = payload.get(symbol, {})
        latest_month = max(series.keys()) if series else None
        daily = daily_status.get(symbol, {})
        symbols.append(
            {
                "symbol": symbol,
                "points": len(series),
                "latest_month": latest_month,
                "daily_points": int(daily.get("points") or 0),
                "latest_day": daily.get("latest_day"),
                "dense_daily": bool(daily.get("dense_daily")),
            }
        )
    updated_at = None
    if DATA_PATH.exists():
        updated_at = (
            datetime.fromtimestamp(DATA_PATH.stat().st_mtime, tz=timezone.utc)
            .astimezone()
            .isoformat(timespec="seconds")
        )
    return {
        "updated_at": updated_at,
        "path": str(DATA_PATH),
        "symbols": symbols,
    }


def refresh_index_data(symbols: List[str], timeout: float = 20.0) -> dict:
    payload = _read_payload()
    results: List[dict] = []

    for symbol in symbols:
        if symbol not in SUPPORTED_SYMBOLS:
            results.append(
                {
                    "symbol": symbol,
                    "status": "error",
                    "added": 0,
                    "updated": 0,
                    "latest_month": None,
                    "daily_points": 0,
                    "error": "不支持的指数",
                }
            )
            continue

        monthly_error = None
        daily_error = None
        added = updated = 0
        latest_month = None
        daily_points = 0

        try:
            full = fetch_remote_full_series(symbol, timeout=timeout)
            incoming = {_month_key(k): float(v) for k, v in full.items()}
            existing = payload.get(symbol, {})
            added = sum(1 for key in incoming if key not in existing)
            updated = sum(
                1 for key, value in incoming.items() if key in existing and abs(existing[key] - value) > 1e-9
            )
            merged = dict(existing)
            merged.update(incoming)
            payload[symbol] = merged
            latest_month = max(merged.keys()) if merged else None
        except Exception as exc:  # noqa: BLE001
            monthly_error = str(exc)
            existing = payload.get(symbol, {})
            latest_month = max(existing.keys()) if existing else None

        try:
            daily = fetch_remote_daily_series(symbol, timeout=timeout)
            daily_points = len(daily)
        except Exception as exc:  # noqa: BLE001
            daily_error = str(exc)

        if monthly_error and daily_error:
            results.append(
                {
                    "symbol": symbol,
                    "status": "error",
                    "added": 0,
                    "updated": 0,
                    "latest_month": latest_month,
                    "daily_points": 0,
                    "error": f"月线: {monthly_error}；日线: {daily_error}",
                }
            )
        else:
            note_parts = []
            if monthly_error:
                note_parts.append(f"月线失败: {monthly_error}")
            if daily_error:
                note_parts.append(f"日线失败: {daily_error}")
            results.append(
                {
                    "symbol": symbol,
                    "status": "ok",
                    "added": added,
                    "updated": updated,
                    "latest_month": latest_month,
                    "daily_points": daily_points,
                    "error": "；".join(note_parts) if note_parts else None,
                }
            )

    _write_payload(payload)
    clear_daily_series_cache()
    success = sum(1 for item in results if item["status"] == "ok")
    return {
        "success": success,
        "failed": len(results) - success,
        "results": results,
        "status": get_index_data_status(),
    }
