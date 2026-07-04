from __future__ import annotations

import threading
from datetime import datetime
from typing import Optional

from app.database import SessionLocal
from app.services.config import get_config_map, save_config
from app.services.fx_rate import refresh_usd_cny_rate
from app.services.price_refresh import refresh_all_prices

_lock = threading.Lock()
_stop_event = threading.Event()
_thread: Optional[threading.Thread] = None
_last_result: dict = {
    "running": False,
    "last_run_at": None,
    "last_status": None,
    "last_error": None,
    "prices_success": 0,
    "prices_fail": 0,
    "fx_rate": None,
    "fx_source": None,
}


def get_sync_status(db=None) -> dict:
    config = get_config_map(db) if db is not None else {}
    if db is None:
        session = SessionLocal()
        try:
            config = get_config_map(session)
        finally:
            session.close()

    enabled = str(config.get("sync_enabled", "0")) in ("1", "true", "True")
    interval_hours = int(float(config.get("sync_interval_hours", 24)))
    with _lock:
        result = dict(_last_result)
    result.update(
        {
            "enabled": enabled,
            "interval_hours": interval_hours,
            "scheduler_alive": _thread is not None and _thread.is_alive(),
        }
    )
    # 持久化的上次结果
    if not result.get("last_run_at") and config.get("sync_last_at"):
        result["last_run_at"] = config.get("sync_last_at")
        result["last_status"] = config.get("sync_last_status")
    return result


def run_sync_once() -> dict:
    with _lock:
        if _last_result.get("running"):
            return dict(_last_result)
        _last_result["running"] = True
        _last_result["last_error"] = None

    db = SessionLocal()
    try:
        prices = refresh_all_prices(db)
        fx = refresh_usd_cny_rate(db)
        now = datetime.now().isoformat(timespec="seconds")
        status = "ok" if prices["fail_count"] == 0 else "partial"
        payload = {
            "running": False,
            "last_run_at": now,
            "last_status": status,
            "last_error": None,
            "prices_success": prices["success_count"],
            "prices_fail": prices["fail_count"],
            "fx_rate": fx["rate"],
            "fx_source": fx["source"],
        }
        save_config(
            db,
            {
                "sync_last_at": now,
                "sync_last_status": status,
            },
        )
        with _lock:
            _last_result.update(payload)
        return dict(_last_result)
    except Exception as exc:  # noqa: BLE001
        now = datetime.now().isoformat(timespec="seconds")
        with _lock:
            _last_result.update(
                {
                    "running": False,
                    "last_run_at": now,
                    "last_status": "error",
                    "last_error": str(exc),
                }
            )
            result = dict(_last_result)
        try:
            save_config(db, {"sync_last_at": now, "sync_last_status": "error"})
        except Exception:  # noqa: BLE001
            pass
        return result
    finally:
        db.close()
        with _lock:
            _last_result["running"] = False


def _loop():
    while not _stop_event.is_set():
        session = SessionLocal()
        try:
            config = get_config_map(session)
            enabled = str(config.get("sync_enabled", "0")) in ("1", "true", "True")
            interval_hours = max(1, int(float(config.get("sync_interval_hours", 24))))
        finally:
            session.close()

        if enabled:
            run_sync_once()
            # 按分钟分段 sleep，便于及时响应 stop / 配置变更
            for _ in range(interval_hours * 60):
                if _stop_event.wait(60):
                    return
        else:
            if _stop_event.wait(60):
                return


def start_scheduler() -> None:
    global _thread
    if _thread is not None and _thread.is_alive():
        return
    _stop_event.clear()
    _thread = threading.Thread(target=_loop, name="wealth-sync", daemon=True)
    _thread.start()


def stop_scheduler() -> None:
    _stop_event.set()
