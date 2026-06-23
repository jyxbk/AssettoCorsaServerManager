#!/usr/bin/env python3
import os
import re
import json
import math
import shutil
import struct
import socket as _sock
import threading
import time
import zipfile
import subprocess
import io
import functools
import urllib.request
from pathlib import Path
from datetime import datetime, timezone
from flask import (Flask, render_template, request, jsonify,
                   send_file, session, redirect, url_for)
from werkzeug.utils import secure_filename
try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False

SERVER_DIR   = Path("/opt/assettoserver")
CONTENT_DIR  = SERVER_DIR / "content"
CARS_DIR     = CONTENT_DIR / "cars"
TRACKS_DIR   = CONTENT_DIR / "tracks"
CFG_DIR      = SERVER_DIR / "cfg"
PRESETS_FILE  = Path("/opt/acweb/presets.json")
DISCORD_FILE  = Path("/opt/acweb/discord.json")
LAPTIMES_FILE = Path("/opt/acweb/laptimes.json")
WELCOME_FILE    = CFG_DIR / "welcome.txt"
EXTRA_CFG_FILE  = CFG_DIR / "extra_cfg.yml"
LOGO_FILE       = SERVER_DIR / "logo.png"
WHITELIST_FILE  = SERVER_DIR / "whitelist.txt"
ADMINS_FILE     = SERVER_DIR / "admins.txt"
BLACKLIST_FILE  = SERVER_DIR / "blacklist.txt"
SERVICE_NAME = "acserver"
SECRET_KEY   = os.environ.get("ACWEB_SECRET", "ac_dashboard_secret_42x")
RCON_PORT    = 9700

ACWEB_USER = os.environ.get("ACWEB_USER", "admin")
ACWEB_PASS = os.environ.get("ACWEB_PASS", "acserver")

UPLOAD_TMP = Path("/tmp/acweb_uploads")
UPLOAD_TMP.mkdir(exist_ok=True)

app = Flask(__name__)
app.secret_key = SECRET_KEY
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024 * 1024

# ── Weather presets available in AC ──────────────────────────────────────────
WEATHER_PRESETS = [
    "1_heavy_clouds","2_light_clouds","3_clear","4_mid_clear",
    "5_light_clouds","6_light_clouds","7_heavy_clouds","8_drizzle",
    "9_light_drizzle","10_drizzle_race","11_practice_storm",
]

# ── Rate limiting for login ───────────────────────────────────────────────────
_rate_limit = {}  # ip -> [timestamps]
_rate_lock  = threading.Lock()

def _check_rate_limit(ip):
    now = time.time()
    with _rate_lock:
        times = [t for t in _rate_limit.get(ip, []) if now - t < 300]
        if len(times) >= 10:
            return False
        times.append(now)
        _rate_limit[ip] = times
        return True

# ── Auth decorator ────────────────────────────────────────────────────────────
def login_required(f):
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("logged_in"):
            if request.path.startswith("/api/") or request.path.startswith("/control/") \
               or request.path in ("/logs", "/upload", "/import_zip",
                                    "/upload_file", "/upload_folder",
                                    "/upload_folder_done", "/save_config",
                                    "/save_assists", "/save_server_settings",
                                    "/save_session", "/save_weather",
                                    "/save_dynamic_track"):
                return jsonify({"ok": False, "msg": "Unauthorized"}), 401
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated

# ── Login / Logout routes ─────────────────────────────────────────────────────
@app.route("/login", methods=["GET", "POST"])
def login():
    if session.get("logged_in"):
        return redirect(url_for("index"))
    error = None
    if request.method == "POST":
        ip = request.remote_addr
        if not _check_rate_limit(ip):
            error = "Too many attempts. Try again in 5 minutes."
        else:
            username = request.form.get("username", "")
            password = request.form.get("password", "")
            if username == ACWEB_USER and password == ACWEB_PASS:
                session["logged_in"] = True
                session.permanent = False
                return redirect(url_for("index"))
            else:
                error = "Invalid username or password."
    return render_template("login.html", error=error)

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

# ── UDP live position listener ────────────────────────────────────────────────
_car_data   = {}
_udp_pkt    = [0]
_udp_err    = ["none"]
_udp_ready  = False

def _udp_listener():
    s = _sock.socket(_sock.AF_INET, _sock.SOCK_DGRAM)
    s.setsockopt(_sock.SOL_SOCKET, _sock.SO_REUSEADDR, 1)
    s.settimeout(1.0)
    try:
        s.bind(("127.0.0.1", 12000))
    except Exception as e:
        _udp_err[0] = f"bind: {e}"; return
    while True:
        try:
            data, _ = s.recvfrom(512)
            if not data: continue
            _udp_pkt[0] += 1
            pkt, size = data[0], len(data)
            if pkt in (2, 53) and size >= 2:
                cid = data[1]
                entry = _car_data.get(cid, {})
                if size >= 33:
                    try:
                        sp = struct.unpack_from("<f", data, 29)[0]
                        if 0.0 <= sp <= 1.0:
                            entry["spLine"] = round(sp, 4)
                    except Exception: pass
                if size >= 45:
                    try:
                        entry["lapTimeMs"] = struct.unpack_from("<I", data, 33)[0]
                        entry["lastLapMs"] = struct.unpack_from("<I", data, 37)[0]
                        entry["bestLapMs"] = struct.unpack_from("<I", data, 41)[0]
                    except Exception: pass
                if size >= 47:
                    try:
                        entry["lapCount"] = struct.unpack_from("<H", data, 45)[0]
                    except Exception: pass
                _car_data[cid] = entry
            elif pkt == 4 and size >= 6:
                cid = data[1]
                lap_ms = struct.unpack_from("<I", data, 2)[0]
                entry = _car_data.get(cid, {})
                if 10000 < lap_ms < 7200000:
                    entry["lastLapMs"] = lap_ms
                    if lap_ms < entry.get("bestLapMs", 99999999):
                        entry["bestLapMs"] = lap_ms
                entry["lapCount"] = entry.get("lapCount", 0) + 1
                _car_data[cid] = entry
        except _sock.timeout: continue
        except Exception as e: _udp_err[0] = str(e); continue

def ensure_udp():
    global _udp_ready
    if not _udp_ready:
        _udp_ready = True
        threading.Thread(target=_udp_listener, daemon=True).start()

# ── Discord webhook background thread ────────────────────────────────────────
_discord_last_status = [None]

def _discord_monitor():
    while True:
        time.sleep(30)
        try:
            dcfg = _load_discord_config()
            url = dcfg.get("url", "")
            if not url:
                continue
            current = server_status()
            prev    = _discord_last_status[0]
            if prev is not None and prev == "active" and current in ("failed", "inactive"):
                _discord_notify(url, f"🔴 Server `{SERVICE_NAME}` went **offline** (status: {current})")
            elif prev is not None and prev in ("failed", "inactive") and current == "active":
                _discord_notify(url, f"🟢 Server `{SERVICE_NAME}` is back **online**")
            _discord_last_status[0] = current
        except Exception:
            pass

def _discord_notify(webhook_url, message):
    try:
        payload = json.dumps({"content": message}).encode("utf-8")
        req = urllib.request.Request(
            webhook_url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        urllib.request.urlopen(req, timeout=5)
    except Exception:
        pass

def _load_discord_config():
    if DISCORD_FILE.exists():
        try:
            return json.loads(DISCORD_FILE.read_text())
        except Exception:
            pass
    return {}

def _load_discord_url():
    return _load_discord_config().get("url", "")

threading.Thread(target=_discord_monitor, daemon=True).start()

# ── Lap time tracker ──────────────────────────────────────────────────────────
_lt_lock    = threading.Lock()
_lt_session = {}  # driver_name -> {guid, car, skin, track}

# AS log format: [HH:MM:SS INF] message  (time + level inside ONE bracket)
# Car IDs use underscores only; split car-skin on the FIRST hyphen.
_RE_CONNECT    = re.compile(
    r'\[[\d:]+ INF\] (.+?) \((\d{17}),\s*\d+ \(([^)]+)\)\) has connected'
)
_RE_LAP        = re.compile(
    r'\[(\d{2}:\d{2}:\d{2}) INF\] Lap completed by (.+?), (\d+) cuts?, laptime (\d+)'
)
_RE_DISCONNECT = re.compile(r'\[[\d:]+ INF\] (.+?) has disconnected')
_RE_ISO_DATE   = re.compile(r'^(\d{4}-\d{2}-\d{2})')

def _load_laptimes():
    if LAPTIMES_FILE.exists():
        try:
            return json.loads(LAPTIMES_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return []

def _save_laptimes(entries):
    LAPTIMES_FILE.parent.mkdir(parents=True, exist_ok=True)
    LAPTIMES_FILE.write_text(json.dumps(entries, ensure_ascii=False), encoding="utf-8")

def _append_laptime(entry):
    with _lt_lock:
        entries = _load_laptimes()
        # Deduplicate: skip if same driver+track+laptime already stored
        for e in entries:
            if (e.get("driver") == entry["driver"] and
                    e.get("laptime") == entry["laptime"] and
                    e.get("track") == entry["track"]):
                return
        entries.append(entry)
        _save_laptimes(entries)

def _split_car_skin(car_skin):
    """Split 'car_model-skin_name' on the first hyphen (AC car IDs never contain hyphens)."""
    if '-' in car_skin:
        return car_skin.split('-', 1)
    return car_skin, ''

def _parse_journal_block(lines, known_ts_set):
    """Parse a list of journal lines (short-iso format) and return new lap entries."""
    session = {}
    new_entries = []
    cur_date = time.strftime("%Y-%m-%d")  # fallback for lines without ISO prefix

    for line in lines:
        # short-iso lines start with: 2026-06-22T22:37:18+0200 hostname svc[pid]: <message>
        iso_m = _RE_ISO_DATE.match(line)
        if iso_m:
            cur_date = iso_m.group(1)
            # Strip the syslog prefix to get the AC log message
            colon_pos = line.find(': ')
            line = line[colon_pos + 2:].strip() if colon_pos != -1 else line

        line = line.strip()
        m = _RE_CONNECT.search(line)
        if m:
            name, guid, car_skin = m.group(1), m.group(2), m.group(3)
            car, skin = _split_car_skin(car_skin)
            session[name] = {"guid": guid, "car": car, "skin": skin}
            continue
        m = _RE_DISCONNECT.search(line)
        if m:
            session.pop(m.group(1), None)
            continue
        m = _RE_LAP.search(line)
        if m:
            ts_str, name, cuts, laptime_ms = m.group(1), m.group(2), int(m.group(3)), int(m.group(4))
            ts_full = f"{cur_date} {ts_str}"
            key = f"{name}|{laptime_ms}|{ts_full}"
            if key in known_ts_set:
                continue
            info = session.get(name, {})
            cfg  = read_server_cfg()
            track  = cfg.get("TRACK", "")
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

def _preload_session_from_http():
    """Pre-populate _lt_session from AS HTTP API so already-connected players
    get correct guid/car even if acweb was restarted while they were online."""
    try:
        with urllib.request.urlopen("http://127.0.0.1:8081/api/details", timeout=3) as r:
            data = json.loads(r.read())
        cfg    = read_server_cfg()
        track  = cfg.get("TRACK", "")
        layout = cfg.get("TRACK_LAYOUT", "")
        track_str = f"{track}-{layout}" if layout else track
        for car in data.get("players", {}).get("Cars", []):
            if not car.get("IsConnected"):
                continue
            name = car.get("DriverName", "")
            guid = car.get("ID", "")
            model = car.get("Model", "")
            skin  = car.get("Skin", "")
            if name:
                _lt_session[name] = {
                    "guid": guid, "car": model, "skin": skin, "track": track_str
                }
    except Exception:
        pass

def _preload_journal_history():
    """On startup: parse recent journal and import any laps not yet in laptimes.json."""
    # First try to populate current session from HTTP (handles restarts mid-session)
    _preload_session_from_http()
    try:
        r = subprocess.run(
            ["journalctl", "-u", SERVICE_NAME, "-n", "5000", "--no-pager", "-o", "short-iso"],
            capture_output=True, text=True, timeout=15)
        lines = r.stdout.splitlines()
    except Exception:
        return
    with _lt_lock:
        existing = _load_laptimes()
        known = {f"{e['driver']}|{e['laptime']}|{e['ts']}" for e in existing}
        new_entries = _parse_journal_block(lines, known)
        if new_entries:
            _save_laptimes(existing + new_entries)

def _laptime_monitor():
    """Tail journalctl live and persist new lap completions + Discord notifications."""
    discord_cfg = {}  # cache
    try:
        proc = subprocess.Popen(
            ["journalctl", "-u", SERVICE_NAME, "-f", "-n", "0", "--no-pager", "-o", "cat"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True
        )
    except Exception:
        return

    for line in proc.stdout:
        line = line.strip()
        m = _RE_CONNECT.search(line)
        if m:
            name, guid, car_skin = m.group(1), m.group(2), m.group(3)
            car, skin = _split_car_skin(car_skin)
            cfg = read_server_cfg()
            track = cfg.get("TRACK", ""); layout = cfg.get("TRACK_LAYOUT", "")
            _lt_session[name] = {
                "guid": guid, "car": car, "skin": skin,
                "track": f"{track}-{layout}" if layout else track,
            }
            # Discord: player joined (if enabled)
            dcfg = _load_discord_config()
            if dcfg.get("url") and dcfg.get("notify_join"):
                _discord_notify(dcfg["url"], f"🟢 **{name}** connected ({car})")
            continue

        m = _RE_DISCONNECT.search(line)
        if m:
            left = m.group(1)
            info = _lt_session.pop(left, {})
            dcfg = _load_discord_config()
            if dcfg.get("url") and dcfg.get("notify_join"):
                _discord_notify(dcfg["url"], f"🔴 **{left}** disconnected")
            continue

        m = _RE_LAP.search(line)
        if m:
            ts_str, name, cuts, laptime_ms = m.group(1), m.group(2), int(m.group(3)), int(m.group(4))
            info = _lt_session.get(name, {})
            cfg  = read_server_cfg()
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
            _append_laptime(entry)

# Run startup history import, then start live monitor
threading.Thread(target=_preload_journal_history, daemon=True).start()
threading.Thread(target=_laptime_monitor, daemon=True).start()

# ── System helpers ────────────────────────────────────────────────────────────
def run_systemctl(action):
    try:
        r = subprocess.run(["systemctl", action, SERVICE_NAME],
                           capture_output=True, text=True, timeout=15)
        return r.returncode == 0, r.stdout + r.stderr
    except Exception as e:
        return False, str(e)

def server_status():
    r = subprocess.run(["systemctl", "is-active", SERVICE_NAME],
                       capture_output=True, text=True)
    return r.stdout.strip()

def server_info():
    for url in ["http://127.0.0.1:8081/api/details",
                "http://127.0.0.1:8081/INFO"]:
        try:
            with urllib.request.urlopen(url, timeout=2) as r:
                return json.loads(r.read())
        except Exception:
            pass
    return None

def server_json():
    try:
        with urllib.request.urlopen("http://127.0.0.1:8081/JSON|", timeout=2) as r:
            return json.loads(r.read())
    except Exception:
        return None

def get_system_stats():
    if not HAS_PSUTIL:
        return {"cpu": 0, "mem_percent": 0, "mem_used_mb": 0, "mem_total_mb": 0}
    cpu = psutil.cpu_percent(interval=0.1)
    mem = psutil.virtual_memory()
    return {
        "cpu": round(cpu, 1),
        "mem_percent": round(mem.percent, 1),
        "mem_used_mb": mem.used // (1024*1024),
        "mem_total_mb": mem.total // (1024*1024),
    }

def get_local_ip():
    try:
        r = subprocess.run(["hostname", "-I"], capture_output=True, text=True, timeout=3)
        ips = r.stdout.strip().split()
        return ips[0] if ips else "unknown"
    except Exception:
        return "unknown"

def get_uptime_string():
    try:
        r = subprocess.run(
            ["systemctl", "show", SERVICE_NAME, "--property=ActiveEnterTimestamp"],
            capture_output=True, text=True, timeout=5)
        line = r.stdout.strip()
        m = re.search(r"=(.+)", line)
        if not m or not m.group(1).strip() or m.group(1).strip() == "n/a":
            return "unknown"
        ts_str = m.group(1).strip()
        # strptime with %Z returns a naive datetime in LOCAL time (ignores tz offset).
        # Compare against datetime.now() (also local) — not utcnow() — to avoid
        # a false negative difference of -2h in CEST timezone.
        for fmt in ["%a %Y-%m-%d %H:%M:%S %Z", "%a %Y-%m-%d %H:%M:%S"]:
            try:
                dt  = datetime.strptime(ts_str, fmt)
                now = datetime.now()
                total_seconds = max(0, int((now - dt).total_seconds()))
                days    = total_seconds // 86400
                hours   = (total_seconds % 86400) // 3600
                minutes = (total_seconds % 3600) // 60
                parts = []
                if days:    parts.append(f"{days}d")
                if hours:   parts.append(f"{hours}h")
                if minutes: parts.append(f"{minutes}m")
                return " ".join(parts) if parts else "< 1m"
            except Exception:
                continue
        return "unknown"
    except Exception:
        return "unknown"

# ── Content helpers ───────────────────────────────────────────────────────────
def list_cars():
    if not CARS_DIR.exists(): return []
    return sorted(d.name for d in CARS_DIR.iterdir() if d.is_dir())

def list_tracks():
    if not TRACKS_DIR.exists(): return []
    result = []
    for d in sorted(TRACKS_DIR.iterdir()):
        if not d.is_dir(): continue
        layouts = sorted(s.name for s in d.iterdir()
                         if s.is_dir() and (s / "data").exists())
        result.append({"name": d.name, "layouts": layouts})
    return result

def get_car_ui(car):
    ui_path = CARS_DIR / car / "ui" / "ui_car.json"
    name = car; brand = ""
    if ui_path.exists():
        try:
            d = json.loads(ui_path.read_text(encoding="utf-8", errors="replace"))
            name  = d.get("name",  car)
            brand = d.get("brand", "")
        except Exception:
            pass
    return {"id": car, "name": name, "brand": brand}

def get_track_ui(track, layout=""):
    candidates = []
    if layout:
        candidates.append(TRACKS_DIR / track / "ui" / layout / "ui_track.json")
    candidates.append(TRACKS_DIR / track / "ui" / "ui_track.json")
    for p in candidates:
        if p.exists():
            try:
                d = json.loads(p.read_text(encoding="utf-8", errors="replace"))
                return {
                    "name":     d.get("name", track),
                    "length":   d.get("length", ""),
                    "pitboxes": d.get("pitboxes", ""),
                }
            except Exception:
                pass
    return {"name": track, "length": "", "pitboxes": ""}

def get_car_skins(car):
    skins_dir = CARS_DIR / car / "skins"
    if not skins_dir.exists(): return []
    return sorted(s.name for s in skins_dir.iterdir() if s.is_dir())

# ── Config helpers ────────────────────────────────────────────────────────────
def read_server_cfg():
    cfg_path = CFG_DIR / "server_cfg.ini"
    if not cfg_path.exists(): return {}
    data, section = {}, None
    with open(cfg_path) as f:
        for line in f:
            line = line.strip()
            if line.startswith("[") and line.endswith("]"):
                section = line[1:-1]
            elif "=" in line and section == "SERVER":
                k, v = line.split("=", 1)
                data[k.strip()] = v.strip()
    return data

def read_full_server_cfg():
    cfg_path = CFG_DIR / "server_cfg.ini"
    if not cfg_path.exists(): return {}
    result, section = {}, None
    with open(cfg_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith(";") or line.startswith("#"):
                continue
            if line.startswith("[") and line.endswith("]"):
                section = line[1:-1]
                result.setdefault(section, {})
            elif "=" in line and section:
                k, v = line.split("=", 1)
                result[section][k.strip()] = v.strip()
    return result

def update_server_cfg(updates):
    cfg_path = CFG_DIR / "server_cfg.ini"
    if not cfg_path.exists(): return False, "server_cfg.ini not found"
    lines = cfg_path.read_text().splitlines()
    new_lines = []
    found_keys = set()

    for line in lines:
        replaced = False
        for key, value in updates.items():
            if line.strip().startswith(key + "="):
                new_lines.append(f"{key}={value}")
                found_keys.add(key)
                replaced = True
                break
        if not replaced:
            new_lines.append(line)

    missing = {k: v for k, v in updates.items() if k not in found_keys}
    if missing:
        server_start = None
        next_section = None
        for i, line in enumerate(new_lines):
            s = line.strip()
            if s == "[SERVER]":
                server_start = i
            elif s.startswith("[") and s.endswith("]") and server_start is not None:
                next_section = i
                break
        insert_at = next_section if next_section is not None else len(new_lines)
        if server_start is None:
            insert_at = len(new_lines)
        for k, v in missing.items():
            new_lines.insert(insert_at, f"{k}={v}")
            insert_at += 1

    cfg_path.write_text("\n".join(new_lines) + "\n")
    return True, "Saved"

def update_section_cfg(section_updates):
    cfg_path = CFG_DIR / "server_cfg.ini"
    if not cfg_path.exists(): return False, "server_cfg.ini not found"
    lines = cfg_path.read_text().splitlines()
    new_lines, current_section = [], None
    found = {sec: set() for sec in section_updates}

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            if current_section in section_updates:
                for k, v in section_updates[current_section].items():
                    if k not in found[current_section]:
                        new_lines.append(f"{k}={v}")
                        found[current_section].add(k)
            current_section = stripped[1:-1]
            new_lines.append(line)
            continue
        replaced = False
        if current_section in section_updates and "=" in stripped:
            k = stripped.split("=", 1)[0].strip()
            if k in section_updates[current_section]:
                new_lines.append(f"{k}={section_updates[current_section][k]}")
                found[current_section].add(k)
                replaced = True
        if not replaced:
            new_lines.append(line)

    if current_section in section_updates:
        for k, v in section_updates[current_section].items():
            if k not in found[current_section]:
                new_lines.append(f"{k}={v}")

    cfg_path.write_text("\n".join(new_lines) + "\n")
    return True, "Saved"

# ── Spline / map helpers ───────────────────────────────────────────────────────
@functools.lru_cache(maxsize=8)
def load_spline_points(track, layout):
    ai_path = (TRACKS_DIR / track / layout / "ai" / "fast_lane.ai"
               if layout else TRACKS_DIR / track / "ai" / "fast_lane.ai")
    if not ai_path.exists(): return ()
    try:
        with open(ai_path, "rb") as f:
            data = f.read()
        if len(data) < 8: return ()
        count = struct.unpack_from("<i", data, 4)[0]
        if not (0 < count < 300000):
            count = struct.unpack_from("<i", data, 0)[0]
        if not (0 < count < 300000): return ()
        rec = (len(data) - 8) // count
        if rec < 12: return ()
        pts = []
        for i in range(count):
            off = 8 + i * rec
            if off + 12 > len(data): break
            x, _y, z = struct.unpack_from("<fff", data, off)
            pts.append((x, z))
        if len(pts) < 10: return ()
        mn_x = min(p[0] for p in pts); mx_x = max(p[0] for p in pts)
        mn_z = min(p[1] for p in pts); mx_z = max(p[1] for p in pts)
        w = mx_x - mn_x or 1; h = mx_z - mn_z or 1
        step = max(1, len(pts) // 2000)
        return tuple((round((p[0]-mn_x)/w,4), round((p[1]-mn_z)/h,4))
                     for p in pts[::step])
    except Exception:
        return ()

# ── RCON helper ───────────────────────────────────────────────────────────────
def rcon_send(cmd):
    admin_pw = read_server_cfg().get("ADMIN_PASSWORD", "")
    try:
        port = int(read_extra_cfg().get("RconPort", RCON_PORT))
    except (ValueError, TypeError):
        port = RCON_PORT
    try:
        s = _sock.socket(_sock.AF_INET, _sock.SOCK_STREAM)
        s.settimeout(3)
        s.connect(("127.0.0.1", port))
        def _pack(rid, rtype, body):
            b = body.encode("utf-8") + b"\x00\x00"
            return struct.pack("<iii", 4+4+len(b), rid, rtype) + b
        def _recv():
            raw = s.recv(4)
            if len(raw) < 4: return ""
            sz = struct.unpack("<i", raw)[0]
            d = b""
            while len(d) < sz:
                chunk = s.recv(sz - len(d))
                if not chunk: break
                d += chunk
            return d[8:].rstrip(b"\x00").decode("utf-8", errors="replace")
        s.sendall(_pack(1, 3, admin_pw)); _recv()
        s.sendall(_pack(2, 2, cmd)); resp = _recv()
        s.close()
        return True, resp or "OK"
    except Exception as e:
        return False, str(e)

# ── Chat from logs ────────────────────────────────────────────────────────────
def get_recent_chat(n=40):
    try:
        r = subprocess.run(
            ["journalctl", "-u", SERVICE_NAME, "-n", "1000",
             "--no-pager", "-o", "cat"],
            capture_output=True, text=True, timeout=5)
        msgs = []
        for line in r.stdout.split("\n"):
            if "CHAT:" in line and "$CSP" not in line:
                try:
                    ts   = line[1:9] if line.startswith("[") else ""
                    text = line.split("CHAT: ", 1)[1].strip()
                    msgs.append({"time": ts, "text": text})
                except Exception:
                    pass
        return msgs[-n:]
    except Exception:
        return []

# ── Preset helpers ────────────────────────────────────────────────────────────
def load_presets():
    if PRESETS_FILE.exists():
        try: return json.loads(PRESETS_FILE.read_text())
        except Exception: return {}
    return {}

def save_presets(data):
    PRESETS_FILE.parent.mkdir(parents=True, exist_ok=True)
    PRESETS_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False))

# ── Entry-list generator ──────────────────────────────────────────────────────
def _regen_entry_list(car_models, slots_per_car=2, car_config=None):
    """Generate entry_list.ini.
    car_config: {car_id: {skin, ballast, restrictor}} — optional per-car overrides.
    """
    try:
        lines, i = [], 0
        for model in car_models:
            cfg_entry = (car_config or {}).get(model, {})
            skin = cfg_entry.get("skin", "")
            if not skin:
                skins_dir = CARS_DIR / model / "skins"
                if skins_dir.exists():
                    sk = sorted(s.name for s in skins_dir.iterdir() if s.is_dir())
                    if sk: skin = sk[0]
            ballast    = cfg_entry.get("ballast",    0)
            restrictor = cfg_entry.get("restrictor", 0)
            for _ in range(slots_per_car):
                lines += [f"[CAR_{i}]", f"MODEL={model}", f"SKIN={skin}",
                          "SPECTATOR_MODE=0","DRIVERNAME=","TEAM=","GUID=",
                          f"BALLAST={ballast}",f"RESTRICTOR={restrictor}",""]
                i += 1
        (CFG_DIR / "entry_list.ini").write_text("\n".join(lines))
    except Exception:
        pass

# ── ZIP helpers ───────────────────────────────────────────────────────────────
def analyze_zip(zip_path):
    items = {"cars": [], "tracks": []}
    try:
        with zipfile.ZipFile(zip_path) as zf:
            names = zf.namelist()
            for n in names:
                m = re.search(r"(?:^|/)cars/([^/]+)/", n)
                if m and m.group(1) not in items["cars"]:
                    items["cars"].append(m.group(1))
                m = re.search(r"(?:^|/)tracks/([^/]+)/", n)
                if m and m.group(1) not in items["tracks"]:
                    items["tracks"].append(m.group(1))
            if not items["cars"] and not items["tracks"]:
                roots = set()
                for n in names:
                    parts = n.split("/")
                    if len(parts) > 1 and parts[0]:
                        roots.add(parts[0])
                for r in roots:
                    has_data  = any(n.startswith(f"{r}/data/") for n in names)
                    has_kn5   = any(n.startswith(f"{r}/") and n.endswith(".kn5") for n in names)
                    has_skins = any(n.startswith(f"{r}/skins/") for n in names)
                    if has_skins or (has_kn5 and not has_data):
                        items["cars"].append(r)
                    elif has_data or has_kn5:
                        items["tracks"].append(r)
    except Exception as e:
        return None, str(e)
    return items, None

def _zip_rel(name, prefix):
    m = re.search(rf"(?:^|/){re.escape(prefix)}/(.+)", name)
    if m:
        return m.group(1)
    return None

def extract_from_zip(zip_path, sel_cars, sel_tracks):
    imported = []
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
        has_prefix_cars   = any(re.search(r"(?:^|/)cars/",   n) for n in names)
        has_prefix_tracks = any(re.search(r"(?:^|/)tracks/", n) for n in names)

        for name in names:
            for car in sel_cars:
                if has_prefix_cars:
                    rel = _zip_rel(name, f"cars/{car}")
                else:
                    rel = _zip_rel(name, car)
                if rel and not rel.endswith("/"):
                    tgt = CARS_DIR / car / rel
                    tgt.parent.mkdir(parents=True, exist_ok=True)
                    with zf.open(name) as src, open(tgt, "wb") as dst:
                        shutil.copyfileobj(src, dst)
                    key = f"cars/{car}"
                    if key not in imported: imported.append(key)

            for track in sel_tracks:
                if has_prefix_tracks:
                    rel = _zip_rel(name, f"tracks/{track}")
                else:
                    rel = _zip_rel(name, track)
                if rel and not rel.endswith("/"):
                    tgt = TRACKS_DIR / track / rel
                    tgt.parent.mkdir(parents=True, exist_ok=True)
                    with zf.open(name) as src, open(tgt, "wb") as dst:
                        shutil.copyfileobj(src, dst)
                    key = f"tracks/{track}"
                    if key not in imported: imported.append(key)
    return imported

# ── extra_cfg.yml helpers ─────────────────────────────────────────────────────
EXTRA_CFG_KEYS = [
    "EnableServerDetails", "ServerDescription", "LoadingImageUrl",
    "EnableAntiAfk", "MaxAfkTimeMinutes", "MaxPing", "ForceLights",
    "EnableWeatherFx", "MinimumCSPVersion", "EnableClientMessages",
    "EnableRealTime", "MandatoryClientSecurityLevel", "RconPort",
]

def _yaml_quote(s):
    """Always produce a double-quoted, single-line YAML string with \\n for newlines."""
    s = str(s)
    s = s.replace("\\", "\\\\").replace('"', '\\"')
    s = s.replace("\n", "\\n").replace("\r", "")
    return '"' + s + '"'

def _yaml_unquote(s):
    s = s.strip()
    if len(s) >= 2 and s[0] == '"' and s[-1] == '"':
        inner = s[1:-1]
        inner = inner.replace('\\"', '"').replace('\\n', '\n').replace('\\\\', '\\')
        return inner
    if len(s) >= 2 and s[0] == "'" and s[-1] == "'":
        return s[1:-1].replace("''", "'")
    return s

def read_extra_cfg():
    result = {}
    if not EXTRA_CFG_FILE.exists():
        return result
    for line in EXTRA_CFG_FILE.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        for key in EXTRA_CFG_KEYS:
            if stripped.startswith(key + ":"):
                val = stripped[len(key)+1:].strip()
                result[key] = _yaml_unquote(val)
                break
    return result

def _yaml_format_value(val):
    """Format a Python value as a YAML scalar (always single-line)."""
    if isinstance(val, bool):
        return "true" if val else "false"
    s = str(val)
    if s.lower() in ("true", "false"):
        return s.lower()
    try:
        float(s)
        return s
    except ValueError:
        return _yaml_quote(s)

def write_extra_cfg(updates):
    if not EXTRA_CFG_FILE.exists():
        return False, "extra_cfg.yml not found"
    lines = EXTRA_CFG_FILE.read_text(encoding="utf-8").splitlines()
    found_keys = set()
    new_lines = []
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        matched_key = None
        for key in updates:
            if stripped.startswith(key + ":"):
                matched_key = key
                break
        if matched_key is not None:
            new_lines.append(f"{matched_key}: {_yaml_format_value(updates[matched_key])}")
            found_keys.add(matched_key)
            # Skip any continuation lines (indented or non-key lines) that belonged
            # to a multi-line value of this key
            i += 1
            while i < len(lines):
                nxt = lines[i].strip()
                if re.match(r'^[A-Za-z#]', nxt):
                    break
                i += 1
            continue
        new_lines.append(line)
        i += 1

    for key, val in updates.items():
        if key not in found_keys:
            new_lines.append(f"{key}: {_yaml_format_value(val)}")

    EXTRA_CFG_FILE.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    return True, "Saved"

def get_extra_cfg_description():
    return read_extra_cfg().get("ServerDescription", "")

def set_extra_cfg_description(description):
    return write_extra_cfg({"ServerDescription": description})

# ── Player list helpers ───────────────────────────────────────────────────────
def _read_guid_list(path: Path):
    if not path.exists(): return []
    return [l.strip() for l in path.read_text().splitlines() if l.strip()]

def _write_guid_list(path: Path, guids):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(guids) + ("\n" if guids else ""))

def _add_guid(path: Path, guid: str):
    guids = _read_guid_list(path)
    if guid not in guids:
        guids.append(guid)
        _write_guid_list(path, guids)
        return True
    return False

def _remove_guid(path: Path, guid: str):
    guids = _read_guid_list(path)
    if guid in guids:
        guids.remove(guid)
        _write_guid_list(path, guids)
        return True
    return False

# ── Track params ──────────────────────────────────────────────────────────────
TRACK_PARAMS_FILE = SERVER_DIR / "data" / "data_track_params.ini"

def _auto_add_track_params(track):
    section = f"[{track.lower()}]"
    TRACK_PARAMS_FILE.parent.mkdir(parents=True, exist_ok=True)
    existing = TRACK_PARAMS_FILE.read_text() if TRACK_PARAMS_FILE.exists() else ""
    if section in existing:
        return
    lat, lon, tz = 0.0, 0.0, 0
    ui_path = TRACKS_DIR / track / "ui" / "ui_track.json"
    if not ui_path.exists():
        for d in ((TRACKS_DIR / track).iterdir() if (TRACKS_DIR / track).exists() else []):
            candidate = TRACKS_DIR / track / d.name / "ui" / "ui_track.json"
            if candidate.exists():
                ui_path = candidate
                break
    if ui_path.exists():
        try:
            d = json.loads(ui_path.read_text(encoding="utf-8", errors="replace"))
            city = d.get("city", track)
        except Exception:
            city = track
    else:
        city = track
    entry = f"\n{section}\nCITY={city}\nLATITUDE={lat}\nLONGITUDE={lon}\nTIMEZONE={tz}\n"
    with open(TRACK_PARAMS_FILE, "a") as f:
        f.write(entry)

# ── Folder upload helper ──────────────────────────────────────────────────────
def secure_filename_path(rel):
    parts = Path(rel.replace("\\", "/")).parts
    safe  = [secure_filename(p) for p in parts if p and p not in (".", "..")]
    return Path(*safe) if safe else Path("file")


# ═══════════════════════════════════════════════════════════════════════════════
#  ROUTES
# ═══════════════════════════════════════════════════════════════════════════════

@app.route("/")
@login_required
def index():
    cfg    = read_server_cfg()
    full   = read_full_server_cfg()
    tracks = list_tracks()
    cars   = list_cars()
    car_ui = {c: get_car_ui(c) for c in cars}
    track_ui = {}
    for t in tracks:
        layouts = t["layouts"]
        layout  = layouts[0] if layouts else ""
        track_ui[t["name"]] = get_track_ui(t["name"], layout)
    return render_template(
        "index.html",
        status=server_status(),
        info=server_info(),
        cfg=cfg,
        full_cfg=full,
        cars=cars,
        car_ui=car_ui,
        tracks=tracks,
        track_ui=track_ui,
        selected_cars=cfg.get("CARS","").split(";") if cfg.get("CARS") else [],
        local_ip=get_local_ip(),
        weather_presets=WEATHER_PRESETS,
    )


# ── Live data ────────────────────────────────────────────────────────────────
@app.route("/api/live")
@login_required
def api_live():
    ensure_udp()
    status = server_status()
    active = status == "active"

    cfg    = read_server_cfg()
    track  = cfg.get("TRACK", "")
    layout = cfg.get("TRACK_LAYOUT", "")
    raw    = load_spline_points(track, layout)
    spline_pts = [[x,y] for x,y in raw if not (math.isnan(x) or math.isnan(y))]

    # Only hit the AS HTTP API when the server is actually running
    drivers  = []
    info     = None
    if active:
        info = server_info()
        js   = server_json()
        if js:
            for i, car in enumerate(js.get("Cars", js.get("cars", []))):
                if not car.get("IsConnected", car.get("connected", False)):
                    continue
                name = car.get("DriverName") or car.get("driver", {}).get("name", "")
                if not name: continue
                udp = _car_data.get(i, {})
                drv = {
                    "id":       i,
                    "name":     name,
                    "guid":     car.get("ID", ""),
                    "model":    car.get("Model", car.get("model", "")),
                    "skin":     car.get("Skin",  car.get("skin",  "")),
                    "team":     car.get("DriverTeam", ""),
                    "nation":   car.get("DriverNation", ""),
                    "spLine":   udp.get("spLine", 0),
                    "lapCount": udp.get("lapCount", 0),
                    "lapTime":  udp.get("lapTimeMs", 0),
                    "lastLap":  udp.get("lastLapMs", 0),
                    "bestLap":  udp.get("bestLapMs", 0),
                    "mapX":     None,
                    "mapY":     None,
                }
                if spline_pts and drv["spLine"] > 0:
                    idx = int(drv["spLine"] * len(spline_pts)) % len(spline_pts)
                    drv["mapX"] = spline_pts[idx][0]
                    drv["mapY"] = spline_pts[idx][1]
                drivers.append(drv)

    return jsonify({
        "status":        status,
        "system":        get_system_stats(),
        "info":          info,
        "drivers":       drivers,
        "spline_points": spline_pts,
        "chat":          get_recent_chat(30) if active else [],
    })


# ── Uptime ───────────────────────────────────────────────────────────────────
@app.route("/api/uptime")
@login_required
def api_uptime():
    return jsonify({"uptime": get_uptime_string()})


# ── Image endpoints ───────────────────────────────────────────────────────────
@app.route("/car_img/<car>")
@login_required
def car_img(car):
    base = CARS_DIR / car
    for rel in ["ui/badge.png", "ui/car_small.png", "ui/car.png"]:
        p = base / rel
        if p.exists():
            return send_file(p, mimetype="image/png")
    return "", 404

@app.route("/skin_img/<car>/<path:skin>")
@login_required
def skin_img(car, skin):
    base = CARS_DIR / car / "skins" / skin
    for fn in ["livery.png", "preview.png", "Skin.png"]:
        p = base / fn
        if p.exists():
            return send_file(p, mimetype="image/png")
    return "", 404

@app.route("/track_img/<track>")
@app.route("/track_img/<track>/<layout>")
@login_required
def track_img(track, layout=""):
    base = TRACKS_DIR / track
    candidates = []
    if layout:
        candidates += [
            base / layout / "map.png",
            base / "ui" / layout / "preview.png",
            base / "ui" / layout / "outline.png",
        ]
    candidates += [
        base / "map.png",
        base / "ui" / "preview.png",
        base / "ui" / "outline.png",
    ]
    for p in candidates:
        if p.exists():
            return send_file(p, mimetype="image/png")
    return "", 404

@app.route("/map")
@login_required
def track_map():
    cfg    = read_server_cfg()
    track  = cfg.get("TRACK", "")
    layout = cfg.get("TRACK_LAYOUT", "")
    candidates = []
    if layout:
        candidates += [
            TRACKS_DIR / track / layout / "map.png",
            TRACKS_DIR / track / "ui" / layout / "outline.png",
            TRACKS_DIR / track / "ui" / layout / "preview.png",
        ]
    candidates += [
        TRACKS_DIR / track / "map.png",
        TRACKS_DIR / track / "ui" / "outline.png",
        TRACKS_DIR / track / "ui" / "preview.png",
    ]
    for p in candidates:
        if p.exists():
            return send_file(p, mimetype="image/png")
    return "", 404


# ── API: car/track UI info ────────────────────────────────────────────────────
@app.route("/api/car_info/<car>")
@login_required
def api_car_info(car):
    ui   = get_car_ui(car)
    skins = get_car_skins(car)
    return jsonify({**ui, "skins": skins})

@app.route("/api/car_skins/<car>")
@login_required
def api_car_skins(car):
    return jsonify({"skins": get_car_skins(car)})

@app.route("/api/content_check/<kind>/<name>")
@login_required
def content_check(kind, name):
    if kind == "car":
        base = CARS_DIR / name
        checks = {
            "collider.kn5": (base / "collider.kn5").exists(),
            "data":         (base / "data").is_dir() or (base / "data.acd").exists(),
            "ui":           (base / "ui" / "ui_car.json").exists(),
        }
    elif kind == "track":
        base = TRACKS_DIR / name
        checks = {
            "ui":           (base / "ui" / "ui_track.json").exists(),
            "data/surfaces":(base / "data" / "surfaces.ini").exists(),
        }
    else:
        return jsonify({"ok": False, "msg": "kind must be car or track"}), 400
    ok = all(checks.values())
    return jsonify({"ok": ok, "name": name, "checks": checks})

@app.route("/api/track_info/<track>")
@app.route("/api/track_info/<track>/<layout>")
@login_required
def api_track_info(track, layout=""):
    return jsonify(get_track_ui(track, layout))


# ── Settings saves ────────────────────────────────────────────────────────────
def _maybe_restart(data):
    if data.get("restart"):
        run_systemctl("restart")

@app.route("/save_config", methods=["POST"])
@login_required
def save_config():
    data = request.json or {}
    updates = {}
    if data.get("track")  is not None: updates["TRACK"]        = data["track"]
    if data.get("layout") is not None:
        updates["TRACK_LAYOUT"]  = data.get("layout","")
        updates["CONFIG_TRACK"]  = data.get("layout","")
    if data.get("cars"):
        updates["CARS"] = ";".join(data["cars"])
    ok, msg = update_server_cfg(updates)
    if updates.get("TRACK") or updates.get("TRACK_LAYOUT"):
        load_spline_points.cache_clear()
    spc = int(data.get("slots_per_car", 2))
    car_config = data.get("car_config")
    if data.get("cars"):
        spc_clamped = max(1, min(5, spc))
        _regen_entry_list(data["cars"], spc_clamped, car_config)
        total_slots = len(data["cars"]) * spc_clamped
        update_server_cfg({"MAX_CLIENTS": total_slots})
    _maybe_restart(data)
    return jsonify({"ok": ok, "msg": msg})

@app.route("/save_assists", methods=["POST"])
@login_required
def save_assists():
    data = request.json or {}
    allowed = {
        "ABS_ALLOWED","TC_ALLOWED","STABILITY_ALLOWED","AUTOCLUTCH_ALLOWED",
        "TYRE_BLANKETS_ALLOWED","FORCE_VIRTUAL_MIRROR","FUEL_RATE",
        "DAMAGE_MULTIPLIER","TYRE_WEAR_RATE","ALLOWED_TYRES_OUT","MAX_CLIENTS",
    }
    updates = {k: v for k,v in data.items() if k in allowed}
    ok, msg = update_server_cfg(updates)
    _maybe_restart(data)
    return jsonify({"ok": ok, "msg": msg})

@app.route("/save_server_settings", methods=["POST"])
@login_required
def save_server_settings():
    data = request.json or {}
    allowed = {
        "NAME","PASSWORD","ADMIN_PASSWORD","REGISTER_TO_LOBBY",
        "MAX_CLIENTS","UDP_PORT","TCP_PORT","HTTP_PORT","SUN_ANGLE",
    }
    updates = {k: v for k,v in data.items() if k in allowed}
    ok, msg = update_server_cfg(updates)
    _maybe_restart(data)
    return jsonify({"ok": ok, "msg": msg})

@app.route("/save_session", methods=["POST"])
@login_required
def save_session():
    data = request.json or {}
    section_updates = {}
    for sess in ("PRACTICE", "QUALIFY", "RACE"):
        key = sess.lower()
        upd = {}
        if f"{key}_time"    in data: upd["TIME"]     = data[f"{key}_time"]
        if f"{key}_laps"    in data: upd["LAPS"]     = data[f"{key}_laps"]
        if f"{key}_open"    in data: upd["IS_OPEN"]  = 1 if data[f"{key}_open"] else 0
        if f"{key}_wait"    in data: upd["WAIT_TIME"]= data[f"{key}_wait"]
        if upd: section_updates[sess] = upd
    if not section_updates:
        return jsonify({"ok": False, "msg": "No data"})
    ok, msg = update_section_cfg(section_updates)
    _maybe_restart(data)
    return jsonify({"ok": ok, "msg": msg})

@app.route("/save_weather", methods=["POST"])
@login_required
def save_weather():
    data = request.json or {}
    section_updates = {}
    for i in range(2):
        key = f"weather_{i}"
        sect = f"WEATHER_{i}"
        upd = {}
        if f"{key}_graphics" in data:       upd["GRAPHICS"]                  = data[f"{key}_graphics"]
        if f"{key}_ambient"  in data:       upd["BASE_TEMPERATURE_AMBIENT"]  = data[f"{key}_ambient"]
        if f"{key}_road"     in data:       upd["BASE_TEMPERATURE_ROAD"]     = data[f"{key}_road"]
        if f"{key}_var_amb"  in data:       upd["VARIATION_AMBIENT"]         = data[f"{key}_var_amb"]
        if f"{key}_var_road" in data:       upd["VARIATION_ROAD"]            = data[f"{key}_var_road"]
        if upd: section_updates[sect] = upd
    if not section_updates:
        return jsonify({"ok": False, "msg": "No data"})
    ok, msg = update_section_cfg(section_updates)
    _maybe_restart(data)
    return jsonify({"ok": ok, "msg": msg})

@app.route("/save_dynamic_track", methods=["POST"])
@login_required
def save_dynamic_track():
    data = request.json or {}
    allowed = {"SESSION_START","RANDOMNESS","SESSION_TRANSFER","LAP_GAIN"}
    upd = {k: v for k,v in data.items() if k in allowed}
    if not upd: return jsonify({"ok": False, "msg": "No data"})
    ok, msg = update_section_cfg({"DYNAMIC_TRACK": upd})
    _maybe_restart(data)
    return jsonify({"ok": ok, "msg": msg})


# ── Track params ─────────────────────────────────────────────────────────────
@app.route("/api/add_track_params", methods=["POST"])
@login_required
def add_track_params():
    data  = request.json or {}
    track = data.get("track", "").strip()
    city  = data.get("city", track).strip() or track
    lat   = float(data.get("lat", 0) or 0)
    lon   = float(data.get("lon", 0) or 0)
    tz    = int(float(data.get("tz", 0) or 0))   # UTC offset as integer, e.g. 1 for CET
    if not track:
        return jsonify({"ok": False, "msg": "track required"}), 400
    section = f"[{track.lower()}]"
    TRACK_PARAMS_FILE.parent.mkdir(parents=True, exist_ok=True)
    existing = TRACK_PARAMS_FILE.read_text() if TRACK_PARAMS_FILE.exists() else ""
    if section in existing:
        # Update existing entry
        lines = existing.splitlines()
        new_lines, in_sect = [], False
        for line in lines:
            if line.strip().lower() == section.lower():
                in_sect = True
                new_lines.append(line)
                continue
            if in_sect and line.strip().startswith("[") and line.strip().endswith("]"):
                in_sect = False
            if in_sect:
                k = line.split("=", 1)[0].strip().upper()
                if k == "CITY":       new_lines.append(f"CITY={city}"); continue
                if k == "LATITUDE":   new_lines.append(f"LATITUDE={lat}"); continue
                if k == "LONGITUDE":  new_lines.append(f"LONGITUDE={lon}"); continue
                if k == "TIMEZONE":   new_lines.append(f"TIMEZONE={tz}"); continue
            new_lines.append(line)
        TRACK_PARAMS_FILE.write_text("\n".join(new_lines) + "\n")
        return jsonify({"ok": True, "msg": f"Updated params for {track}"})
    entry = f"\n{section}\nCITY={city}\nLATITUDE={lat}\nLONGITUDE={lon}\nTIMEZONE={tz}\n"
    with open(TRACK_PARAMS_FILE, "a") as f:
        f.write(entry)
    return jsonify({"ok": True, "msg": f"Added params for {track}"})

# ── Server control ────────────────────────────────────────────────────────────
@app.route("/control/<action>", methods=["POST"])
@login_required
def control(action):
    if action not in ("start","stop","restart"):
        return jsonify({"ok": False, "msg": "Invalid action"}), 400
    ok, msg = run_systemctl(action)
    return jsonify({"ok": ok, "msg": msg, "status": server_status()})

@app.route("/logs")
@login_required
def logs():
    try:
        r = subprocess.run(
            ["journalctl","-u",SERVICE_NAME,"-n","200","--no-pager","-o","cat"],
            capture_output=True, text=True, timeout=10)
        return jsonify({"logs": r.stdout})
    except Exception as e:
        return jsonify({"logs": f"Error: {e}"})


# ── RCON console ─────────────────────────────────────────────────────────────
@app.route("/api/rcon_console", methods=["POST"])
@login_required
def rcon_console():
    cmd = (request.json or {}).get("cmd", "").strip()
    if not cmd:
        return jsonify({"ok": False, "response": "No command"}), 400
    ok, resp = rcon_send(cmd)
    return jsonify({"ok": ok, "response": resp})


# ── Extra CFG ────────────────────────────────────────────────────────────────
@app.route("/api/extra_cfg", methods=["GET"])
@login_required
def get_extra_cfg():
    return jsonify({"ok": True, "data": read_extra_cfg()})

@app.route("/api/extra_cfg", methods=["POST"])
@login_required
def post_extra_cfg():
    data = request.json or {}
    updates = {k: v for k, v in data.items() if k in EXTRA_CFG_KEYS}
    if not updates:
        return jsonify({"ok": False, "msg": "No valid keys provided"}), 400
    ok, msg = write_extra_cfg(updates)
    return jsonify({"ok": ok, "msg": msg})


# ── Player management (whitelist/admins/blacklist) ────────────────────────────
@app.route("/api/whitelist", methods=["GET"])
@login_required
def get_whitelist():
    return jsonify({"guids": _read_guid_list(WHITELIST_FILE)})

@app.route("/api/whitelist", methods=["POST"])
@login_required
def add_whitelist():
    guid = (request.json or {}).get("guid", "").strip()
    if not guid: return jsonify({"ok": False, "msg": "GUID required"}), 400
    added = _add_guid(WHITELIST_FILE, guid)
    return jsonify({"ok": True, "added": added})

@app.route("/api/whitelist/<guid>", methods=["DELETE"])
@login_required
def del_whitelist(guid):
    removed = _remove_guid(WHITELIST_FILE, guid)
    return jsonify({"ok": removed, "msg": "Removed" if removed else "Not found"})

@app.route("/api/admins", methods=["GET"])
@login_required
def get_admins():
    return jsonify({"guids": _read_guid_list(ADMINS_FILE)})

@app.route("/api/admins", methods=["POST"])
@login_required
def add_admin():
    guid = (request.json or {}).get("guid", "").strip()
    if not guid: return jsonify({"ok": False, "msg": "GUID required"}), 400
    added = _add_guid(ADMINS_FILE, guid)
    return jsonify({"ok": True, "added": added})

@app.route("/api/admins/<guid>", methods=["DELETE"])
@login_required
def del_admin(guid):
    removed = _remove_guid(ADMINS_FILE, guid)
    return jsonify({"ok": removed, "msg": "Removed" if removed else "Not found"})

@app.route("/api/blacklist", methods=["GET"])
@login_required
def get_blacklist():
    return jsonify({"guids": _read_guid_list(BLACKLIST_FILE)})

@app.route("/api/blacklist/<guid>", methods=["DELETE"])
@login_required
def del_blacklist(guid):
    removed = _remove_guid(BLACKLIST_FILE, guid)
    return jsonify({"ok": removed, "msg": "Removed" if removed else "Not found"})


# ── Lap times API ────────────────────────────────────────────────────────────
@app.route("/api/laptimes")
@login_required
def api_laptimes():
    entries = _load_laptimes()
    driver  = request.args.get("driver", "").strip().lower()
    track   = request.args.get("track",  "").strip().lower()
    car     = request.args.get("car",    "").strip().lower()
    if driver: entries = [e for e in entries if driver in e.get("driver","").lower()]
    if track:  entries = [e for e in entries if track  in e.get("track", "").lower()]
    if car:    entries = [e for e in entries if car    in e.get("car",   "").lower()]
    # Sort fastest first
    entries_sorted = sorted(entries, key=lambda e: e.get("laptime", 99999999))
    return jsonify({"ok": True, "entries": entries_sorted, "total": len(entries_sorted)})

@app.route("/api/laptimes/best")
@login_required
def api_laptimes_best():
    """Best lap per driver per track."""
    entries = _load_laptimes()
    best = {}
    for e in entries:
        key = (e.get("driver",""), e.get("track",""))
        if key not in best or e.get("laptime", 99999999) < best[key].get("laptime", 99999999):
            best[key] = e
    result = sorted(best.values(), key=lambda e: (e.get("track",""), e.get("laptime", 99999999)))
    return jsonify({"ok": True, "entries": result})

@app.route("/api/laptimes/drivers")
@login_required
def api_laptimes_drivers():
    entries = _load_laptimes()
    drivers = sorted({e.get("driver","") for e in entries if e.get("driver")})
    tracks  = sorted({e.get("track","")  for e in entries if e.get("track")})
    cars    = sorted({e.get("car","")    for e in entries if e.get("car")})
    return jsonify({"drivers": drivers, "tracks": tracks, "cars": cars})

@app.route("/api/laptimes", methods=["DELETE"])
@login_required
def api_laptimes_clear():
    with _lt_lock:
        _save_laptimes([])
    return jsonify({"ok": True})

@app.route("/api/laptimes/export")
@login_required
def api_laptimes_export():
    entries = _load_laptimes()
    driver = request.args.get("driver", "").strip().lower()
    track  = request.args.get("track",  "").strip().lower()
    car    = request.args.get("car",    "").strip().lower()
    if driver: entries = [e for e in entries if driver in e.get("driver","").lower()]
    if track:  entries = [e for e in entries if track  in e.get("track", "").lower()]
    if car:    entries = [e for e in entries if car    in e.get("car",   "").lower()]
    entries = sorted(entries, key=lambda x: x.get("ts",""))
    buf = io.StringIO()
    buf.write("Datum,Fahrer,GUID,Auto,Strecke,Rundenzeit,Rundenzeit_ms,Cuts\n")
    for e in entries:
        ms   = e.get("laptime", 0)
        mins = ms // 60000
        secs = (ms % 60000) / 1000
        fmt  = f"{mins}:{secs:06.3f}"
        # Escape commas in fields
        def csv_field(v): return f'"{v}"' if ',' in str(v) else str(v)
        buf.write(f"{csv_field(e.get('ts',''))},{csv_field(e.get('driver',''))},"
                  f"{csv_field(e.get('guid',''))},{csv_field(e.get('car',''))},"
                  f"{csv_field(e.get('track',''))},{fmt},{ms},{e.get('cuts',0)}\n")
    from flask import Response
    return Response(
        buf.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=laptimes.csv"}
    )

@app.route("/api/laptimes/stats")
@login_required
def api_laptimes_stats():
    """Per-driver aggregated statistics."""
    entries = _load_laptimes()
    drivers = {}
    for e in entries:
        d = e.get("driver", "")
        if not d:
            continue
        s = drivers.setdefault(d, {
            "driver": d, "guid": e.get("guid",""),
            "total_laps": 0, "clean_laps": 0,
            "best_overall": None, "tracks": {}
        })
        s["total_laps"] += 1
        if e.get("cuts", 0) == 0:
            s["clean_laps"] += 1
        lt = e.get("laptime", 0)
        if lt and (s["best_overall"] is None or lt < s["best_overall"]):
            s["best_overall"] = lt
        track = e.get("track", "unknown")
        t = s["tracks"].setdefault(track, {"laps": 0, "best": None, "car": ""})
        t["laps"] += 1
        if lt and (t["best"] is None or lt < t["best"]):
            t["best"] = lt
            t["car"]  = e.get("car", "")
    result = sorted(drivers.values(), key=lambda x: x["total_laps"], reverse=True)
    return jsonify({"ok": True, "stats": result})

@app.route("/api/laptimes/today")
@login_required
def api_laptimes_today():
    """Quick stats for today's dashboard summary."""
    today   = time.strftime("%Y-%m-%d")
    entries = [e for e in _load_laptimes() if e.get("ts","").startswith(today)]
    best    = min(entries, key=lambda e: e.get("laptime", 99999999), default=None)
    drivers = len({e.get("driver","") for e in entries if e.get("driver")})
    return jsonify({
        "laps_today":   len(entries),
        "drivers_today": drivers,
        "best_today":   best,
    })


# ── Config backup/restore ─────────────────────────────────────────────────────
@app.route("/api/backup")
@login_required
def config_backup():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for fname in ["server_cfg.ini", "entry_list.ini", "extra_cfg.yml", "welcome.txt"]:
            p = CFG_DIR / fname
            if p.exists():
                zf.write(str(p), fname)
    buf.seek(0)
    return send_file(
        buf,
        mimetype="application/zip",
        as_attachment=True,
        download_name="acserver_backup.zip"
    )

@app.route("/api/restore", methods=["POST"])
@login_required
def config_restore():
    f = request.files.get("backup")
    if not f:
        return jsonify({"ok": False, "msg": "No file"}), 400
    if not f.filename.lower().endswith(".zip"):
        return jsonify({"ok": False, "msg": "ZIP only"}), 400
    try:
        tmp = UPLOAD_TMP / secure_filename(f.filename)
        f.save(str(tmp))
        allowed = {"server_cfg.ini", "entry_list.ini", "extra_cfg.yml", "welcome.txt"}
        with zipfile.ZipFile(tmp) as zf:
            for name in zf.namelist():
                bname = Path(name).name
                if bname in allowed:
                    dest = CFG_DIR / bname
                    dest.write_bytes(zf.read(name))
        tmp.unlink(missing_ok=True)
        run_systemctl("restart")
        return jsonify({"ok": True, "msg": "Config restored and server restarted"})
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e)}), 500


# ── Car/track deletion ────────────────────────────────────────────────────────
@app.route("/api/delete_content/<ctype>/<name>", methods=["DELETE"])
@login_required
def delete_content(ctype, name):
    if ctype not in ("car", "track"):
        return jsonify({"ok": False, "msg": "type must be car or track"}), 400
    if ctype == "car":
        target = CARS_DIR / name
    else:
        target = TRACKS_DIR / name
    if not target.exists():
        return jsonify({"ok": False, "msg": f"{name} not found"}), 404
    try:
        shutil.rmtree(str(target))
        if ctype == "car":
            cfg = read_server_cfg()
            existing = [c for c in cfg.get("CARS", "").split(";") if c and c != name]
            update_server_cfg({"CARS": ";".join(existing)})
            _regen_entry_list(existing, 2)
        return jsonify({"ok": True, "msg": f"{name} deleted"})
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e)}), 500

@app.route("/api/installed_content")
@login_required
def installed_content():
    cars   = list_cars()
    tracks = [t["name"] for t in list_tracks()]
    return jsonify({"cars": cars, "tracks": tracks})


# ── Chat (broadcast via RCON) ─────────────────────────────────────────────────
@app.route("/api/chat", methods=["POST"])
@login_required
def api_chat_send():
    msg = (request.json or {}).get("message", "").strip()
    if not msg:
        return jsonify({"ok": False, "msg": "Nachricht darf nicht leer sein"}), 400
    ok, resp = rcon_send(f"/say {msg}")
    return jsonify({"ok": ok, "response": resp})


# ── Discord webhook ───────────────────────────────────────────────────────────
@app.route("/api/discord", methods=["GET"])
@login_required
def get_discord():
    return jsonify(_load_discord_config())

@app.route("/api/discord", methods=["POST"])
@login_required
def set_discord():
    data = request.json or {}
    url  = data.get("url", "").strip()
    cfg  = _load_discord_config()
    cfg["url"]          = url
    cfg["notify_join"]  = bool(data.get("notify_join", cfg.get("notify_join", False)))
    DISCORD_FILE.parent.mkdir(parents=True, exist_ok=True)
    DISCORD_FILE.write_text(json.dumps(cfg))
    return jsonify({"ok": True})

@app.route("/api/discord/test", methods=["POST"])
@login_required
def test_discord():
    url = _load_discord_url()
    if not url:
        return jsonify({"ok": False, "msg": "Keine Webhook URL konfiguriert"}), 400
    try:
        _discord_notify(url, f"🔔 Test-Nachricht vom AC Server Dashboard (`{SERVICE_NAME}`)")
        return jsonify({"ok": True, "msg": "Test-Nachricht gesendet"})
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e)}), 500


# ── Presets ───────────────────────────────────────────────────────────────────
@app.route("/api/presets", methods=["GET"])
@login_required
def get_presets():
    return jsonify(load_presets())

@app.route("/api/presets", methods=["POST"])
@login_required
def save_preset():
    data = request.json or {}
    name = (data.get("name") or "").strip()
    if not name: return jsonify({"ok": False, "msg": "Name required"}), 400
    cfg = read_server_cfg()
    presets = load_presets()
    presets[name] = {
        "track":       cfg.get("TRACK",""),
        "layout":      cfg.get("TRACK_LAYOUT",""),
        "config_track":cfg.get("CONFIG_TRACK",""),
        "cars":        cfg.get("CARS",""),
        "server_name": cfg.get("NAME",""),
        "saved":       time.strftime("%d.%m.%Y %H:%M"),
    }
    save_presets(presets)
    return jsonify({"ok": True, "msg": f"Preset '{name}' saved"})

@app.route("/api/presets/<name>/load", methods=["POST"])
@login_required
def load_preset_route(name):
    presets = load_presets()
    if name not in presets:
        return jsonify({"ok": False, "msg": "Preset not found"}), 404
    p = presets[name]
    updates = {}
    if p.get("track"):        updates["TRACK"]        = p["track"]
    if "layout" in p:         updates["TRACK_LAYOUT"] = p["layout"]
    if "config_track" in p:   updates["CONFIG_TRACK"] = p["config_track"]
    if p.get("cars"):         updates["CARS"]         = p["cars"]
    ok, msg = update_server_cfg(updates)
    if ok:
        load_spline_points.cache_clear()
        run_systemctl("restart")
        return jsonify({"ok": True, "msg": f"'{name}' loaded + server restarted"})
    return jsonify({"ok": False, "msg": msg}), 500

@app.route("/api/presets/<name>", methods=["DELETE"])
@login_required
def delete_preset(name):
    presets = load_presets()
    if name not in presets:
        return jsonify({"ok": False, "msg": "Not found"}), 404
    del presets[name]
    save_presets(presets)
    return jsonify({"ok": True})


# ── Player management (kick/ban) ─────────────────────────────────────────────
@app.route("/api/kick", methods=["POST"])
@login_required
def kick_player():
    car_id = (request.json or {}).get("car_id")
    if car_id is None: return jsonify({"ok": False, "msg": "car_id missing"}), 400
    ok, msg = rcon_send(f"/kick_id {car_id}")
    return jsonify({"ok": ok, "msg": msg})

@app.route("/api/ban", methods=["POST"])
@login_required
def ban_player():
    data  = request.json or {}
    guid  = data.get("guid","")
    name  = data.get("name","unknown")
    car_id = data.get("car_id")
    if not guid: return jsonify({"ok": False, "msg": "GUID missing"}), 400
    _add_guid(BLACKLIST_FILE, guid)
    if car_id is not None: rcon_send(f"/kick_id {car_id}")
    return jsonify({"ok": True, "msg": f"{name} banned"})


# ── Folder upload ─────────────────────────────────────────────────────────────
@app.route("/upload_file", methods=["POST"])
@login_required
def upload_file():
    content_type = request.form.get("type", "").strip()
    root_name    = request.form.get("root_name", "").strip()
    rel_path     = request.form.get("rel_path", "").strip()
    f            = request.files.get("file")
    if content_type not in ("car", "track"):
        return jsonify({"ok": False, "msg": "type must be car or track"}), 400
    if not root_name or not rel_path or not f:
        return jsonify({"ok": False, "msg": "missing fields"}), 400
    base_dir = CARS_DIR / root_name if content_type == "car" else TRACKS_DIR / root_name
    rel      = secure_filename_path(rel_path)
    tgt      = base_dir / rel
    tgt.parent.mkdir(parents=True, exist_ok=True)
    f.save(str(tgt))
    return jsonify({"ok": True, "path": str(rel)})

@app.route("/upload_folder_done", methods=["POST"])
@login_required
def upload_folder_done():
    content_type = request.json.get("type", "").strip() if request.json else ""
    root_name    = request.json.get("root_name", "").strip() if request.json else ""
    if content_type not in ("car", "track") or not root_name:
        return jsonify({"ok": False, "msg": "missing fields"}), 400
    if content_type == "track":
        _auto_add_track_params(root_name)
    else:
        cfg = read_server_cfg()
        existing = [c for c in cfg.get("CARS", "").split(";") if c]
        if root_name not in existing:
            all_cars = existing + [root_name]
            update_server_cfg({"CARS": ";".join(all_cars)})
            _regen_entry_list(all_cars, 2)
    return jsonify({"ok": True, "name": root_name})

@app.route("/upload_folder", methods=["POST"])
@login_required
def upload_folder():
    content_type = request.form.get("type", "").strip()
    root_name    = request.form.get("root_name", "").strip()
    files        = request.files.getlist("files")
    if content_type not in ("car", "track"):
        return jsonify({"ok": False, "msg": "type must be car or track"}), 400
    if not root_name:
        return jsonify({"ok": False, "msg": "root_name required"}), 400
    base_dir = CARS_DIR / root_name if content_type == "car" else TRACKS_DIR / root_name
    written = 0
    for f in files:
        rel = secure_filename_path(f.filename)
        tgt = base_dir / rel
        tgt.parent.mkdir(parents=True, exist_ok=True)
        f.save(str(tgt))
        written += 1
    if content_type == "track":
        _auto_add_track_params(root_name)
    else:
        cfg = read_server_cfg()
        existing = [c for c in cfg.get("CARS", "").split(";") if c]
        if root_name not in existing:
            all_cars = existing + [root_name]
            update_server_cfg({"CARS": ";".join(all_cars)})
            _regen_entry_list(all_cars, 2)
    return jsonify({"ok": True, "name": root_name, "files": written})

# ── Content upload (ZIP) ──────────────────────────────────────────────────────
@app.route("/upload", methods=["POST"])
@login_required
def upload_zip():
    if "file" not in request.files:
        return jsonify({"ok": False, "msg": "No file"}), 400
    f = request.files["file"]
    if not f.filename.lower().endswith(".zip"):
        return jsonify({"ok": False, "msg": "ZIP only"}), 400
    filename = secure_filename(f.filename)
    save_path = UPLOAD_TMP / filename
    f.save(save_path)
    items, err = analyze_zip(save_path)
    if err: return jsonify({"ok": False, "msg": f"ZIP error: {err}"}), 400
    try:
        with zipfile.ZipFile(save_path) as zf:
            sample = zf.namelist()[:40]
    except Exception:
        sample = []
    return jsonify({"ok": True, "filename": filename,
                    "cars": items["cars"], "tracks": items["tracks"],
                    "_zip_sample": sample})

@app.route("/import_zip", methods=["POST"])
@login_required
def import_zip():
    data = request.json or {}
    filename     = data.get("filename")
    sel_cars     = data.get("cars", [])
    sel_tracks   = data.get("tracks", [])
    if not filename: return jsonify({"ok": False, "msg": "No filename"}), 400
    zip_path = UPLOAD_TMP / secure_filename(filename)
    if not zip_path.exists():
        return jsonify({"ok": False, "msg": "ZIP not found – re-upload"}), 404
    try:
        imported = extract_from_zip(zip_path, sel_cars, sel_tracks)
        zip_path.unlink(missing_ok=True)

        for track in sel_tracks:
            _auto_add_track_params(track)

        if sel_cars:
            cfg = read_server_cfg()
            existing = [c for c in cfg.get("CARS", "").split(";") if c]
            new_cars = [c for c in sel_cars if c not in existing]
            if new_cars:
                all_cars = existing + new_cars
                update_server_cfg({"CARS": ";".join(all_cars)})
                _regen_entry_list(all_cars, 2)

        return jsonify({"ok": True, "imported": imported})
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e)}), 500


# ── Server profile (welcome message + logo) ──────────────────────────────────
@app.route("/api/server_profile", methods=["GET", "POST"])
@login_required
def server_profile():
    if request.method == "GET":
        msg = get_extra_cfg_description()
        if not msg and WELCOME_FILE.exists():
            msg = WELCOME_FILE.read_text(encoding="utf-8")
        return jsonify({"ok": True, "welcome": msg, "has_logo": LOGO_FILE.exists()})
    data    = request.get_json() or {}
    welcome = data.get("welcome", "")
    WELCOME_FILE.write_text(welcome, encoding="utf-8")
    set_extra_cfg_description(welcome)
    update_server_cfg({"WELCOME_MESSAGE": "cfg/welcome.txt"})
    return jsonify({"ok": True})

@app.route("/api/server_logo", methods=["GET"])
@login_required
def get_server_logo():
    if not LOGO_FILE.exists():
        return ("", 404)
    return send_file(str(LOGO_FILE), mimetype="image/png")

@app.route("/api/server_logo", methods=["POST"])
@login_required
def upload_server_logo():
    f = request.files.get("logo")
    if not f:
        return jsonify({"ok": False, "msg": "no file"}), 400
    f.save(str(LOGO_FILE))
    return jsonify({"ok": True})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=False)
