"""Event-Scheduler: zeitgesteuerte Konfigurationswechsel."""
import json
import logging
import threading
import time
import uuid
from datetime import datetime

from constants import PRESETS_FILE, SCHEDULED_EVENTS_FILE

logger = logging.getLogger(__name__)

# Schützt SCHEDULED_EVENTS_FILE vor gleichzeitigen Lese-/Schreibzugriffen aus
# dem Scheduler-Thread und aus Web-Request-Threads (create/delete/reset_event).
_events_lock = threading.Lock()


def _load_events() -> list:
    if SCHEDULED_EVENTS_FILE.exists():
        try:
            return json.loads(SCHEDULED_EVENTS_FILE.read_text(encoding="utf-8"))
        except Exception:
            logger.exception("_load_events: konnte %s nicht parsen", SCHEDULED_EVENTS_FILE)
    return []


def _save_events(events: list):
    SCHEDULED_EVENTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    SCHEDULED_EVENTS_FILE.write_text(
        json.dumps(events, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def get_events() -> list:
    with _events_lock:
        return _load_events()


def create_event(name: str, dt_str: str, action: str, preset: str = "") -> dict:
    evt = {
        "id":       str(uuid.uuid4())[:8],
        "name":     name,
        "datetime": dt_str,       # "YYYY-MM-DD HH:MM"
        "action":   action,       # "apply_preset" | "restart"
        "preset":   preset,
        "executed": False,
        "created":  datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
    with _events_lock:
        events = _load_events()
        events.append(evt)
        _save_events(events)
    return evt


def delete_event(eid: str) -> bool:
    with _events_lock:
        events = _load_events()
        new    = [e for e in events if e.get("id") != eid]
        if len(new) == len(events):
            return False
        _save_events(new)
        return True


def reset_event(eid: str) -> bool:
    """Markiert ein ausgeführtes Event als 'noch nicht ausgeführt' (für Re-Scheduling)."""
    with _events_lock:
        events = _load_events()
        for e in events:
            if e.get("id") == eid:
                e["executed"]    = False
                e.pop("executed_at", None)
                _save_events(events)
                return True
        return False


def _execute_event(evt: dict):
    action = evt.get("action", "")

    if action == "apply_preset":
        preset_name = evt.get("preset", "")
        if not preset_name or not PRESETS_FILE.exists():
            return
        try:
            presets = json.loads(PRESETS_FILE.read_text(encoding="utf-8"))
            p = presets.get(preset_name)
            if not isinstance(p, dict):
                logger.warning("_execute_event: Preset '%s' fehlt oder ist ungültig", preset_name)
                return
            from helpers.config_io import update_server_cfg
            updates = {}
            if isinstance(p.get("track"), str) and p["track"]:
                updates["TRACK"] = p["track"]
            if isinstance(p.get("layout"), str) and p["layout"]:
                updates["TRACK_LAYOUT"] = p["layout"]
                updates["CONFIG_TRACK"] = p["layout"]
            cars = p.get("cars")
            if isinstance(cars, list) and cars:
                updates["CARS"] = ";".join(str(c) for c in cars if isinstance(c, str))
            if updates:
                update_server_cfg(updates)
            from helpers.system import run_systemctl, load_spline_points
            load_spline_points.cache_clear()
            run_systemctl("restart")
        except Exception:
            logger.exception("_execute_event: apply_preset '%s' fehlgeschlagen", preset_name)

    elif action == "restart":
        from helpers.system import run_systemctl
        run_systemctl("restart")

    # Discord / Telegram-Notification
    try:
        from helpers.discord import _load_discord_config, discord_notify
        dcfg = _load_discord_config()
        if dcfg.get("url"):
            discord_notify(dcfg["url"], f"📅 Scheduled event ausgeführt: **{evt.get('name','')}** ({action})")
    except Exception:
        logger.exception("_execute_event: Discord-Benachrichtigung fehlgeschlagen")
    try:
        from helpers.telegram import _load_telegram_config, escape_markdown_v2, telegram_notify
        tcfg = _load_telegram_config()
        if tcfg.get("token") and tcfg.get("chat_id"):
            telegram_notify(tcfg["token"], tcfg["chat_id"],
                f"📅 Scheduled Event: *{escape_markdown_v2(evt.get('name',''))}* \\({action}\\)")
    except Exception:
        logger.exception("_execute_event: Telegram-Benachrichtigung fehlgeschlagen")


def _scheduler_loop():
    while True:
        time.sleep(30)
        try:
            now = datetime.now().strftime("%Y-%m-%d %H:%M")
            with _events_lock:
                events = _load_events()
                due = [e for e in events if not e.get("executed") and e.get("datetime", "") <= now]
                for evt in due:
                    evt["executed"]    = True
                    evt["executed_at"] = now
                if due:
                    _save_events(events)
            for evt in due:
                _execute_event(evt)
        except Exception:
            logger.exception("_scheduler_loop: Fehler im Scheduler-Loop")


def start_scheduler():
    threading.Thread(target=_scheduler_loop, daemon=True).start()
