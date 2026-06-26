"""Lap-Time-Tracker: journalctl-Parsing, persistente Speicherung, Discord-Notifications."""
import json
import re
import subprocess
import threading
import time
import urllib.request

from constants import LAPTIMES_FILE, SERVICE_NAME
from helpers.config_io import read_server_cfg
from helpers.discord import _load_discord_config, discord_notify

# ── Shared state ──────────────────────────────────────────────────────────────
_lt_lock    = threading.Lock()
_lt_session: dict = {}   # driver_name → {guid, car, skin, track}

# ── Regex patterns ────────────────────────────────────────────────────────────
_RE_CONNECT    = re.compile(
    r'\[[\d:]+ INF\] (.+?) \((\d{17}),\s*\d+ \(([^)]+)\)\) has connected'
)
_RE_LAP        = re.compile(
    r'\[(\d{2}:\d{2}:\d{2}) INF\] Lap completed by (.+?), (\d+) cuts?, laptime (\d+)'
)
_RE_DISCONNECT = re.compile(r'\[[\d:]+ INF\] (.+?) has disconnected')
_RE_ISO_DATE   = re.compile(r'^(\d{4}-\d{2}-\d{2})')
_RE_LOG_TIME   = re.compile(r'\[(\d{2}:\d{2}:\d{2})\s+INF\]')


# ── Persistent storage ────────────────────────────────────────────────────────

def load_laptimes() -> list:
    if LAPTIMES_FILE.exists():
        try:
            return json.loads(LAPTIMES_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return []


def _save_laptimes(entries: list):
    LAPTIMES_FILE.parent.mkdir(parents=True, exist_ok=True)
    LAPTIMES_FILE.write_text(json.dumps(entries, ensure_ascii=False), encoding="utf-8")


def append_laptime(entry: dict):
    with _lt_lock:
        entries = load_laptimes()
        for e in entries:
            if (e.get("driver") == entry["driver"]
                    and e.get("laptime") == entry["laptime"]
                    and e.get("track")  == entry["track"]):
                return  # Duplikat
        entries.append(entry)
        _save_laptimes(entries)


def clear_laptimes():
    with _lt_lock:
        _save_laptimes([])


# ── Helpers ───────────────────────────────────────────────────────────────────

def split_car_skin(car_skin: str) -> tuple[str, str]:
    """Trennt 'car_model-skin_name' am ersten Bindestrich."""
    if "-" in car_skin:
        return car_skin.split("-", 1)
    return car_skin, ""


# ── Journal-Parsing ───────────────────────────────────────────────────────────

def _parse_journal_block(lines: list, known_ts_set: set) -> list:
    """Parst eine Liste von Journal-Zeilen (short-iso Format) und gibt neue Lap-Einträge zurück."""
    driver_session: dict = {}   # lokale Session – umbenannt von 'session' (vermeidet Shadowing)
    new_entries = []
    cur_date = time.strftime("%Y-%m-%d")

    for line in lines:
        iso_m = _RE_ISO_DATE.match(line)
        if iso_m:
            cur_date = iso_m.group(1)
            colon_pos = line.find(": ")
            line = line[colon_pos + 2:].strip() if colon_pos != -1 else line

        line = line.strip()
        m = _RE_CONNECT.search(line)
        if m:
            name, guid, car_skin = m.group(1), m.group(2), m.group(3)
            car, skin = split_car_skin(car_skin)
            driver_session[name] = {"guid": guid, "car": car, "skin": skin}
            continue
        m = _RE_DISCONNECT.search(line)
        if m:
            driver_session.pop(m.group(1), None)
            continue
        m = _RE_LAP.search(line)
        if m:
            ts_str, name, cuts, laptime_ms = m.group(1), m.group(2), int(m.group(3)), int(m.group(4))
            ts_full = f"{cur_date} {ts_str}"
            key = f"{name}|{laptime_ms}|{ts_full}"
            if key in known_ts_set:
                continue
            info  = driver_session.get(name, {})
            cfg   = read_server_cfg()
            track = cfg.get("TRACK", "")
            layout = cfg.get("TRACK_LAYOUT", "")
            new_entries.append({
                "ts":      ts_full,
                "driver":  name,
                "guid":    info.get("guid", ""),
                "car":     info.get("car", ""),
                "skin":    info.get("skin", ""),
                "track":   f"{track}-{layout}" if layout else track,
                "laptime": laptime_ms,
                "cuts":    cuts,
            })
    return new_entries


# ── Startup preload ───────────────────────────────────────────────────────────

def _preload_session_from_http():
    """Füllt _lt_session aus der AS HTTP API, falls acweb während einer Session neugestartet wurde."""
    try:
        with urllib.request.urlopen("http://127.0.0.1:8081/api/details", timeout=3) as r:
            import json as _json
            data = _json.loads(r.read())
        cfg     = read_server_cfg()
        track   = cfg.get("TRACK", "")
        layout  = cfg.get("TRACK_LAYOUT", "")
        track_str = f"{track}-{layout}" if layout else track
        for car in data.get("players", {}).get("Cars", []):
            if not car.get("IsConnected"):
                continue
            name  = car.get("DriverName", "")
            guid  = car.get("ID", "")
            model = car.get("Model", "")
            skin  = car.get("Skin", "")
            if name:
                with _lt_lock:
                    _lt_session[name] = {"guid": guid, "car": model, "skin": skin, "track": track_str}
    except Exception:
        pass


def _preload_journal_history():
    """Importiert fehlende Runden aus den letzten 5000 Journal-Zeilen beim Start."""
    _preload_session_from_http()
    try:
        r = subprocess.run(
            ["journalctl", "-u", SERVICE_NAME, "-n", "5000", "--no-pager", "-o", "short-iso"],
            capture_output=True, text=True, timeout=15,
        )
        lines = r.stdout.splitlines()
    except Exception:
        return
    with _lt_lock:
        existing = load_laptimes()
        known = {f"{e['driver']}|{e['laptime']}|{e['ts']}" for e in existing}
        new_entries = _parse_journal_block(lines, known)
        if new_entries:
            _save_laptimes(existing + new_entries)


# ── Live monitor ──────────────────────────────────────────────────────────────

def _laptime_monitor():
    """Verfolgt journalctl live und persistiert neue Runden + Discord-Meldungen."""
    try:
        proc = subprocess.Popen(
            ["journalctl", "-u", SERVICE_NAME, "-f", "-n", "0", "--no-pager", "-o", "cat"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
        )
    except Exception:
        return

    for line in proc.stdout:
        line = line.strip()

        m = _RE_CONNECT.search(line)
        if m:
            name, guid, car_skin = m.group(1), m.group(2), m.group(3)
            car, skin = split_car_skin(car_skin)
            cfg   = read_server_cfg()
            track = cfg.get("TRACK", ""); layout = cfg.get("TRACK_LAYOUT", "")
            with _lt_lock:
                _lt_session[name] = {
                    "guid": guid, "car": car, "skin": skin,
                    "track": f"{track}-{layout}" if layout else track,
                }
            dcfg = _load_discord_config()
            if dcfg.get("url") and dcfg.get("notify_join"):
                discord_notify(dcfg["url"], f"🟢 **{name}** connected ({car})")
            continue

        m = _RE_DISCONNECT.search(line)
        if m:
            left = m.group(1)
            with _lt_lock:
                _lt_session.pop(left, None)
            dcfg = _load_discord_config()
            if dcfg.get("url") and dcfg.get("notify_join"):
                discord_notify(dcfg["url"], f"🔴 **{left}** disconnected")
            continue

        m = _RE_LAP.search(line)
        if m:
            ts_str, name, cuts, laptime_ms = m.group(1), m.group(2), int(m.group(3)), int(m.group(4))
            with _lt_lock:
                info = dict(_lt_session.get(name, {}))
            cfg   = read_server_cfg()
            track = cfg.get("TRACK", ""); layout = cfg.get("TRACK_LAYOUT", "")
            entry = {
                "ts":      time.strftime("%Y-%m-%d ") + ts_str,
                "driver":  name,
                "guid":    info.get("guid", ""),
                "car":     info.get("car", ""),
                "skin":    info.get("skin", ""),
                "track":   f"{track}-{layout}" if layout else track,
                "laptime": laptime_ms,
                "cuts":    cuts,
            }
            append_laptime(entry)


def start_lap_tracker():
    """Startet Preload + Live-Monitor als Background-Threads."""
    threading.Thread(target=_preload_journal_history, daemon=True).start()
    threading.Thread(target=_laptime_monitor, daemon=True).start()
