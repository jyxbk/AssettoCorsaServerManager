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
import functools
from pathlib import Path
from flask import Flask, render_template, request, jsonify, send_file
from flask_httpauth import HTTPBasicAuth
from werkzeug.security import generate_password_hash, check_password_hash
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
PRESETS_FILE = Path("/opt/acweb/presets.json")
SERVICE_NAME = "acserver"
SECRET_KEY   = "ac_dashboard_secret_42x"
RCON_PORT    = 9700

USERS = {"admin": generate_password_hash("acserver")}

UPLOAD_TMP = Path("/tmp/acweb_uploads")
UPLOAD_TMP.mkdir(exist_ok=True)

app = Flask(__name__)
app.secret_key = SECRET_KEY
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024 * 1024
auth = HTTPBasicAuth()

# ── Weather presets available in AC ──────────────────────────────────────────
WEATHER_PRESETS = [
    "1_heavy_clouds","2_light_clouds","3_clear","4_mid_clear",
    "5_light_clouds","6_light_clouds","7_heavy_clouds","8_drizzle",
    "9_light_drizzle","10_drizzle_race","11_practice_storm",
]

# ── UDP live position listener ────────────────────────────────────────────────
_car_data   = {}   # car_id -> {spLine, lapTimeMs, lastLapMs, bestLapMs, lapCount}
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
            if pkt in (2, 53) and size >= 2:   # RT_CAR_UPDATE
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
            elif pkt == 4 and size >= 6:        # RT_LAP_COMPLETED
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

# ── Auth ──────────────────────────────────────────────────────────────────────
@auth.verify_password
def verify_password(username, password):
    if username in USERS and check_password_hash(USERS[username], password):
        return username

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
    import urllib.request
    for url in ["http://127.0.0.1:8081/api/details",
                "http://127.0.0.1:8081/INFO"]:
        try:
            with urllib.request.urlopen(url, timeout=2) as r:
                return json.loads(r.read())
        except Exception:
            pass
    return None

def server_json():
    import urllib.request
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
    """Return display name + badge path for a car."""
    ui_path = CARS_DIR / car / "ui" / "ui_car.json"
    name = car
    brand = ""
    if ui_path.exists():
        try:
            d = json.loads(ui_path.read_text(encoding="utf-8", errors="replace"))
            name  = d.get("name",  car)
            brand = d.get("brand", "")
        except Exception:
            pass
    return {"id": car, "name": name, "brand": brand}

def get_track_ui(track, layout=""):
    """Return display info for a track/layout."""
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
    """Read [SERVER] section only."""
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
    """Read all sections of server_cfg.ini."""
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
    """Update key=value pairs in server_cfg.ini (in any section)."""
    cfg_path = CFG_DIR / "server_cfg.ini"
    if not cfg_path.exists(): return False, "server_cfg.ini not found"
    lines = cfg_path.read_text().splitlines()
    new_lines = []
    for line in lines:
        replaced = False
        for key, value in updates.items():
            if line.strip().startswith(key + "="):
                new_lines.append(f"{key}={value}")
                replaced = True
                break
        if not replaced:
            new_lines.append(line)
    cfg_path.write_text("\n".join(new_lines) + "\n")
    return True, "Saved"

def update_section_cfg(section_updates):
    """Update multiple keys inside specific INI sections.
    section_updates = {"PRACTICE": {"TIME": 60, "IS_OPEN": 1}, ...}
    """
    cfg_path = CFG_DIR / "server_cfg.ini"
    if not cfg_path.exists(): return False, "server_cfg.ini not found"
    lines = cfg_path.read_text().splitlines()
    new_lines, current_section = [], None
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            current_section = stripped[1:-1]
            new_lines.append(line)
            continue
        replaced = False
        if current_section in section_updates and "=" in stripped:
            k = stripped.split("=", 1)[0].strip()
            if k in section_updates[current_section]:
                new_lines.append(f"{k}={section_updates[current_section][k]}")
                replaced = True
        if not replaced:
            new_lines.append(line)
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
        s = _sock.socket(_sock.AF_INET, _sock.SOCK_STREAM)
        s.settimeout(3)
        s.connect(("127.0.0.1", RCON_PORT))
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
    PRESETS_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False))

# ── Entry-list generator ──────────────────────────────────────────────────────
def _regen_entry_list(car_models, slots_per_car=2):
    try:
        lines, i = [], 0
        for model in car_models:
            skin = ""
            skins_dir = CARS_DIR / model / "skins"
            if skins_dir.exists():
                sk = sorted(s.name for s in skins_dir.iterdir() if s.is_dir())
                if sk: skin = sk[0]
            for _ in range(slots_per_car):
                lines += [f"[CAR_{i}]", f"MODEL={model}", f"SKIN={skin}",
                          "SPECTATOR_MODE=0","DRIVERNAME=","TEAM=","GUID=",
                          "BALLAST=0","RESTRICTOR=0",""]
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
            # Flat ZIPs: root folder is the car/track itself (no cars/ or tracks/ prefix)
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
    """Return the relative path after 'prefix/name/' in a zip entry, or None."""
    m = re.search(rf"(?:^|/){re.escape(prefix)}/(.+)", name)
    if m:
        return m.group(1)
    return None

def extract_from_zip(zip_path, sel_cars, sel_tracks):
    imported = []
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
        # Detect if this is a flat ZIP (no cars/ or tracks/ prefix)
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


# ═══════════════════════════════════════════════════════════════════════════════
#  ROUTES
# ═══════════════════════════════════════════════════════════════════════════════

@app.route("/")
@auth.login_required
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
@auth.login_required
def api_live():
    ensure_udp()
    cfg    = read_server_cfg()
    track  = cfg.get("TRACK", "")
    layout = cfg.get("TRACK_LAYOUT", "")
    raw    = load_spline_points(track, layout)
    spline_pts = [[x,y] for x,y in raw if not (math.isnan(x) or math.isnan(y))]

    drivers = []
    js = server_json()
    if js:
        for i, car in enumerate(js.get("Cars", js.get("cars", []))):
            if not car.get("IsConnected", car.get("connected", False)):
                continue
            name = car.get("DriverName") or car.get("driver", {}).get("name", "")
            if not name: continue
            sp = _car_data.get(i, {}).get("spLine", 0)
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
        "status":        server_status(),
        "system":        get_system_stats(),
        "info":          server_info(),
        "drivers":       drivers,
        "spline_points": spline_pts,
        "chat":          get_recent_chat(30),
    })


# ── Image endpoints ───────────────────────────────────────────────────────────
@app.route("/car_img/<car>")
@auth.login_required
def car_img(car):
    base = CARS_DIR / car
    for rel in ["ui/badge.png", "ui/car_small.png", "ui/car.png"]:
        p = base / rel
        if p.exists():
            return send_file(p, mimetype="image/png")
    return "", 404

@app.route("/skin_img/<car>/<path:skin>")
@auth.login_required
def skin_img(car, skin):
    base = CARS_DIR / car / "skins" / skin
    for fn in ["livery.png", "preview.png", "Skin.png"]:
        p = base / fn
        if p.exists():
            return send_file(p, mimetype="image/png")
    return "", 404

@app.route("/track_img/<track>")
@app.route("/track_img/<track>/<layout>")
@auth.login_required
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
@auth.login_required
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
@auth.login_required
def api_car_info(car):
    ui   = get_car_ui(car)
    skins = get_car_skins(car)
    return jsonify({**ui, "skins": skins})

@app.route("/api/track_info/<track>")
@app.route("/api/track_info/<track>/<layout>")
@auth.login_required
def api_track_info(track, layout=""):
    return jsonify(get_track_ui(track, layout))


# ── Settings saves ────────────────────────────────────────────────────────────
@app.route("/save_config", methods=["POST"])
@auth.login_required
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
    if data.get("cars"):
        _regen_entry_list(data["cars"], max(1, min(5, spc)))
    return jsonify({"ok": ok, "msg": msg})

@app.route("/save_assists", methods=["POST"])
@auth.login_required
def save_assists():
    data = request.json or {}
    allowed = {
        "ABS_ALLOWED","TC_ALLOWED","STABILITY_ALLOWED","AUTOCLUTCH_ALLOWED",
        "TYRE_BLANKETS_ALLOWED","FORCE_VIRTUAL_MIRROR","FUEL_RATE",
        "DAMAGE_MULTIPLIER","TYRE_WEAR_RATE","ALLOWED_TYRES_OUT","MAX_CLIENTS",
    }
    updates = {k: v for k,v in data.items() if k in allowed}
    ok, msg = update_server_cfg(updates)
    return jsonify({"ok": ok, "msg": msg})

@app.route("/save_server_settings", methods=["POST"])
@auth.login_required
def save_server_settings():
    data = request.json or {}
    allowed = {
        "NAME","PASSWORD","ADMIN_PASSWORD","REGISTER_TO_LOBBY",
        "MAX_CLIENTS","UDP_PORT","TCP_PORT","HTTP_PORT","SUN_ANGLE",
    }
    updates = {k: v for k,v in data.items() if k in allowed}
    ok, msg = update_server_cfg(updates)
    return jsonify({"ok": ok, "msg": msg})

@app.route("/save_session", methods=["POST"])
@auth.login_required
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
    return jsonify({"ok": ok, "msg": msg})

@app.route("/save_weather", methods=["POST"])
@auth.login_required
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
    return jsonify({"ok": ok, "msg": msg})

@app.route("/save_dynamic_track", methods=["POST"])
@auth.login_required
def save_dynamic_track():
    data = request.json or {}
    allowed = {"SESSION_START","RANDOMNESS","SESSION_TRANSFER","LAP_GAIN"}
    upd = {k: v for k,v in data.items() if k in allowed}
    if not upd: return jsonify({"ok": False, "msg": "No data"})
    ok, msg = update_section_cfg({"DYNAMIC_TRACK": upd})
    return jsonify({"ok": ok, "msg": msg})


# ── Track params ─────────────────────────────────────────────────────────────
TRACK_PARAMS_FILE = SERVER_DIR / "data" / "data_track_params.ini"

@app.route("/api/add_track_params", methods=["POST"])
@auth.login_required
def add_track_params():
    data = request.json or {}
    track = data.get("track", "").strip()
    city  = data.get("city", "Unknown").strip()
    lat   = data.get("lat", 0)
    lon   = data.get("lon", 0)
    tz    = data.get("tz", 0)
    if not track:
        return jsonify({"ok": False, "msg": "track required"}), 400
    section = f"[{track.lower()}]"
    entry = f"\n{section}\nCITY={city}\nLATITUDE={lat}\nLONGITUDE={lon}\nTIMEZONE={tz}\n"
    existing = TRACK_PARAMS_FILE.read_text() if TRACK_PARAMS_FILE.exists() else ""
    if section in existing:
        return jsonify({"ok": True, "msg": "already exists"})
    with open(TRACK_PARAMS_FILE, "a") as f:
        f.write(entry)
    return jsonify({"ok": True, "msg": f"Added params for {track}"})

# ── Server control ────────────────────────────────────────────────────────────
@app.route("/control/<action>", methods=["POST"])
@auth.login_required
def control(action):
    if action not in ("start","stop","restart"):
        return jsonify({"ok": False, "msg": "Invalid action"}), 400
    ok, msg = run_systemctl(action)
    return jsonify({"ok": ok, "msg": msg, "status": server_status()})

@app.route("/logs")
@auth.login_required
def logs():
    try:
        r = subprocess.run(
            ["journalctl","-u",SERVICE_NAME,"-n","200","--no-pager","-o","cat"],
            capture_output=True, text=True, timeout=10)
        return jsonify({"logs": r.stdout})
    except Exception as e:
        return jsonify({"logs": f"Error: {e}"})


# ── Presets ───────────────────────────────────────────────────────────────────
@app.route("/api/presets", methods=["GET"])
@auth.login_required
def get_presets():
    return jsonify(load_presets())

@app.route("/api/presets", methods=["POST"])
@auth.login_required
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
@auth.login_required
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
@auth.login_required
def delete_preset(name):
    presets = load_presets()
    if name not in presets:
        return jsonify({"ok": False, "msg": "Not found"}), 404
    del presets[name]
    save_presets(presets)
    return jsonify({"ok": True})


# ── Player management ────────────────────────────────────────────────────────
@app.route("/api/kick", methods=["POST"])
@auth.login_required
def kick_player():
    car_id = (request.json or {}).get("car_id")
    if car_id is None: return jsonify({"ok": False, "msg": "car_id missing"}), 400
    ok, msg = rcon_send(f"/kick_id {car_id}")
    return jsonify({"ok": ok, "msg": msg})

@app.route("/api/ban", methods=["POST"])
@auth.login_required
def ban_player():
    data  = request.json or {}
    guid  = data.get("guid","")
    name  = data.get("name","unknown")
    car_id = data.get("car_id")
    if not guid: return jsonify({"ok": False, "msg": "GUID missing"}), 400
    bl = SERVER_DIR / "blacklist.txt"
    existing = bl.read_text() if bl.exists() else ""
    if guid not in existing:
        with open(bl, "a") as f: f.write(f"{guid}\n")
    if car_id is not None: rcon_send(f"/kick_id {car_id}")
    return jsonify({"ok": True, "msg": f"{name} banned"})


# ── Content upload ────────────────────────────────────────────────────────────
@app.route("/upload", methods=["POST"])
@auth.login_required
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
@auth.login_required
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
        return jsonify({"ok": True, "imported": imported})
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e)}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=False)
