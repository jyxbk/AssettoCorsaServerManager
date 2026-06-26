"""Discord-Webhook-Integration: Crash/Restart-Benachrichtigungen + Join/Leave."""
import json
import threading
import time
import urllib.request

from constants import DISCORD_FILE, SERVICE_NAME
from helpers.system import server_status

_discord_last_status = [None]


def _load_discord_config() -> dict:
    if DISCORD_FILE.exists():
        try:
            return json.loads(DISCORD_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _load_discord_url() -> str:
    return _load_discord_config().get("url", "")


def discord_notify(webhook_url: str, message: str):
    try:
        payload = json.dumps({"content": message}).encode("utf-8")
        req = urllib.request.Request(
            webhook_url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=5)
    except Exception:
        pass


def _discord_monitor():
    while True:
        time.sleep(30)
        try:
            dcfg = _load_discord_config()
            url  = dcfg.get("url", "")
            if not url:
                continue
            current = server_status()
            prev    = _discord_last_status[0]
            if prev is not None and prev == "active" and current in ("failed", "inactive"):
                discord_notify(url, f"🔴 Server `{SERVICE_NAME}` went **offline** (status: {current})")
            elif prev is not None and prev in ("failed", "inactive") and current == "active":
                discord_notify(url, f"🟢 Server `{SERVICE_NAME}` is back **online**")
            _discord_last_status[0] = current
        except Exception:
            pass


def start_discord_monitor():
    threading.Thread(target=_discord_monitor, daemon=True).start()
