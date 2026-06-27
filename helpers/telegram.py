"""Telegram-Bot-Notifications: Crash/Restart + Join/Leave."""
import json
import threading
import time
import urllib.request

from constants import SERVICE_NAME, TELEGRAM_FILE
from helpers.system import server_status

_tg_last_status = [None]


def _load_telegram_config() -> dict:
    if TELEGRAM_FILE.exists():
        try:
            return json.loads(TELEGRAM_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_telegram_config(cfg: dict):
    TELEGRAM_FILE.parent.mkdir(parents=True, exist_ok=True)
    TELEGRAM_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def telegram_notify(token: str, chat_id: str, message: str, raise_on_error: bool = False):
    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = json.dumps({
            "chat_id":    chat_id,
            "text":       message,
            "parse_mode": "MarkdownV2",
        }).encode("utf-8")
        req = urllib.request.Request(
            url, data=payload,
            headers={
                "Content-Type": "application/json",
                "User-Agent":   "AC-Server-Dashboard/1.0",
            },
            method="POST",
        )
        urllib.request.urlopen(req, timeout=5)
    except Exception:
        if raise_on_error:
            raise


def _telegram_monitor():
    while True:
        time.sleep(30)
        try:
            cfg     = _load_telegram_config()
            token   = cfg.get("token", "")
            chat_id = cfg.get("chat_id", "")
            if not token or not chat_id:
                continue
            current = server_status()
            prev    = _tg_last_status[0]
            if prev is not None and prev == "active" and current in ("failed", "inactive"):
                telegram_notify(token, chat_id,
                    f"🔴 Server `{SERVICE_NAME}` ist *offline* (Status: {current})")
            elif prev is not None and prev in ("failed", "inactive") and current == "active":
                telegram_notify(token, chat_id,
                    f"🟢 Server `{SERVICE_NAME}` ist wieder *online*")
            _tg_last_status[0] = current
        except Exception:
            pass


def start_telegram_monitor():
    threading.Thread(target=_telegram_monitor, daemon=True).start()
