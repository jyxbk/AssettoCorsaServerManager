"""Content-Management Routes: ZIP/Folder-Upload, Import, Delete, Backup, Server-Profil."""
import io
import json
import re
import shutil
import time
import zipfile
from pathlib import Path

from flask import Blueprint, jsonify, render_template, request, send_file
from werkzeug.utils import secure_filename

from constants import (
    CARS_DIR, CFG_DIR, TRACKS_DIR, UPLOAD_TMP, WELCOME_FILE,
)
from helpers.auth import api_rate_limit, csrf_protect, login_required
from helpers.config_io import (
    get_extra_cfg_description, read_extra_cfg, read_server_cfg,
    set_extra_cfg_description, update_server_cfg, write_extra_cfg,
)
from helpers.content import (
    analyze_zip, auto_add_track_params, extract_from_zip,
    get_car_detail, get_car_rich, get_current_slots_per_car,
    get_folder_size_mb, get_track_detail, get_track_rich,
    list_cars, list_tracks,
    regen_entry_list, secure_filename_path,
)
from helpers.system import run_systemctl
from constants import EXTRA_CFG_KEYS

bp = Blueprint("content_mgmt", __name__)


# ── extra_cfg.yml ─────────────────────────────────────────────────────────────

@bp.route("/api/extra_cfg", methods=["GET"])
@login_required
def get_extra_cfg():
    return jsonify({"ok": True, "data": read_extra_cfg()})


@bp.route("/api/extra_cfg", methods=["POST"])
@login_required
@csrf_protect
def post_extra_cfg():
    data    = request.json or {}
    updates = {k: v for k, v in data.items() if k in EXTRA_CFG_KEYS}
    if not updates:
        return jsonify({"ok": False, "msg": "No valid keys provided"}), 400
    ok, msg = write_extra_cfg(updates)
    return jsonify({"ok": ok, "msg": msg})


# ── RCON console ──────────────────────────────────────────────────────────────

@bp.route("/api/rcon_console", methods=["POST"])
@login_required
@csrf_protect
def rcon_console():
    from helpers.system import rcon_send
    cmd = (request.json or {}).get("cmd", "").strip()
    if not cmd:
        return jsonify({"ok": False, "response": "No command"}), 400
    ok, resp = rcon_send(cmd)
    return jsonify({"ok": ok, "response": resp})


# ── Discord webhook ───────────────────────────────────────────────────────────

@bp.route("/api/discord", methods=["GET"])
@login_required
def get_discord():
    from helpers.discord import _load_discord_config
    return jsonify(_load_discord_config())


@bp.route("/api/discord", methods=["POST"])
@login_required
@csrf_protect
def set_discord():
    from helpers.discord import _load_discord_config, is_valid_webhook_url
    from constants import DISCORD_FILE
    data = request.json or {}
    url  = data.get("url", "").strip()
    if url and not is_valid_webhook_url(url):
        return jsonify({"ok": False, "msg": "Ungültige Discord-Webhook-URL"}), 400
    cfg  = _load_discord_config()
    cfg["url"]           = url
    cfg["notify_crash"]  = bool(data.get("notify_crash",  cfg.get("notify_crash",  True)))
    cfg["notify_join"]   = bool(data.get("notify_join",   cfg.get("notify_join",   False)))
    cfg["notify_record"] = bool(data.get("notify_record", cfg.get("notify_record", True)))
    cfg["notify_pb"]     = bool(data.get("notify_pb",     cfg.get("notify_pb",     False)))
    DISCORD_FILE.parent.mkdir(parents=True, exist_ok=True)
    DISCORD_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    return jsonify({"ok": True})


@bp.route("/api/discord/test", methods=["POST"])
@login_required
@csrf_protect
def test_discord():
    from helpers.discord import _load_discord_url, discord_embed, _build, _COL_BLUE, SERVICE_NAME
    url = _load_discord_url()
    if not url:
        return jsonify({"ok": False, "msg": "Keine Webhook URL konfiguriert"}), 400
    try:
        embed = _build("🔔 Test-Embed", _COL_BLUE, fields=[
            {"name": "Service",  "value": f"`{SERVICE_NAME}`", "inline": True},
            {"name": "Status",   "value": "Verbindung OK ✓",  "inline": True},
        ])
        discord_embed(url, embed, raise_on_error=True)
        return jsonify({"ok": True, "msg": "Test-Embed gesendet"})
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e)}), 500


@bp.route("/api/discord/summary", methods=["POST"])
@login_required
@csrf_protect
def discord_summary():
    from helpers.discord import _load_discord_url, discord_embed, embed_summary
    from helpers.laptimes import load_laptimes
    from helpers.config_io import read_server_cfg
    import time as _time
    url = _load_discord_url()
    if not url:
        return jsonify({"ok": False, "msg": "Keine Webhook URL konfiguriert"}), 400
    today   = _time.strftime("%Y-%m-%d")
    entries = [e for e in load_laptimes() if e.get("ts", "").startswith(today)]
    cfg     = read_server_cfg()
    track   = cfg.get("TRACK", "")
    try:
        discord_embed(url, embed_summary(entries, track), raise_on_error=True)
        return jsonify({"ok": True, "msg": f"Summary gesendet ({len(entries)} Runden heute)"})
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e)}), 500


# ── Chat broadcast ────────────────────────────────────────────────────────────

@bp.route("/api/chat", methods=["POST"])
@login_required
@csrf_protect
def api_chat_send():
    from helpers.system import rcon_send
    msg = (request.json or {}).get("message", "").strip()
    if not msg:
        return jsonify({"ok": False, "msg": "Nachricht darf nicht leer sein"}), 400
    ok, resp = rcon_send(f"/say {msg}")
    return jsonify({"ok": ok, "response": resp})


# ── Presets ───────────────────────────────────────────────────────────────────

@bp.route("/api/presets", methods=["GET"])
@login_required
def get_presets():
    from helpers.content import load_presets
    return jsonify(load_presets())


@bp.route("/api/presets", methods=["POST"])
@login_required
@csrf_protect
def save_preset():
    from helpers.content import load_presets, save_presets
    data = request.json or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"ok": False, "msg": "Name required"}), 400
    cfg     = read_server_cfg()
    presets = load_presets()
    presets[name] = {
        "track":        cfg.get("TRACK", ""),
        "layout":       cfg.get("TRACK_LAYOUT", ""),
        "config_track": cfg.get("CONFIG_TRACK", ""),
        "cars":         cfg.get("CARS", ""),
        "server_name":  cfg.get("NAME", ""),
        "saved":        time.strftime("%d.%m.%Y %H:%M"),
    }
    save_presets(presets)
    return jsonify({"ok": True, "msg": f"Preset '{name}' saved"})


@bp.route("/api/presets/<name>/load", methods=["POST"])
@login_required
@csrf_protect
def load_preset_route(name):
    from helpers.content import load_presets
    from helpers.system import load_spline_points
    presets = load_presets()
    if name not in presets:
        return jsonify({"ok": False, "msg": "Preset not found"}), 404
    p = presets[name]
    updates = {}
    if p.get("track"):       updates["TRACK"]        = p["track"]
    if "layout" in p:        updates["TRACK_LAYOUT"] = p["layout"]
    if "config_track" in p:  updates["CONFIG_TRACK"] = p["config_track"]
    if p.get("cars"):        updates["CARS"]         = p["cars"]
    ok, msg = update_server_cfg(updates)
    if ok:
        load_spline_points.cache_clear()
        run_systemctl("restart")
        return jsonify({"ok": True, "msg": f"'{name}' loaded + server restarted"})
    return jsonify({"ok": False, "msg": msg}), 500


@bp.route("/api/presets/<name>", methods=["DELETE"])
@login_required
@csrf_protect
def delete_preset(name):
    from helpers.content import load_presets, save_presets
    presets = load_presets()
    if name not in presets:
        return jsonify({"ok": False, "msg": "Not found"}), 404
    del presets[name]
    save_presets(presets)
    return jsonify({"ok": True})


# ── Events (join / leave) ─────────────────────────────────────────────────────

@bp.route("/api/events")
@login_required
def api_events():
    import subprocess, time as _time
    from helpers.laptimes import _RE_CONNECT, _RE_DISCONNECT, _RE_ISO_DATE, _RE_LOG_TIME, split_car_skin
    from constants import SERVICE_NAME
    try:
        limit = min(int(request.args.get("limit", 500)), 2000)
    except (ValueError, TypeError):
        limit = 500
    try:
        r = subprocess.run(
            ["journalctl", "-u", SERVICE_NAME, f"-n{limit * 4}", "--no-pager", "-o", "short-iso"],
            capture_output=True, text=True, timeout=10,
        )
        lines = r.stdout.splitlines()
    except Exception as e:
        return jsonify({"ok": False, "events": [], "msg": str(e)})

    events   = []
    cur_date = _time.strftime("%Y-%m-%d")
    for line in lines:
        iso_m = _RE_ISO_DATE.match(line)
        if iso_m:
            cur_date  = iso_m.group(1)
            bracket   = line.find(": [")
            if bracket != -1:
                line = line[bracket + 2:].strip()
            else:
                colon_pos = line.find(": ")
                line = line[colon_pos + 2:].strip() if colon_pos != -1 else line
        line  = line.strip()
        ts_m  = _RE_LOG_TIME.search(line)
        ts_str = ts_m.group(1) if ts_m else ""
        ts_full = f"{cur_date} {ts_str}" if ts_str else cur_date

        m = _RE_CONNECT.search(line)
        if m:
            name, guid, car_skin = m.group(1), m.group(2), m.group(3)
            car, _ = split_car_skin(car_skin)
            events.append({"type": "join", "ts": ts_full, "driver": name, "guid": guid, "car": car})
            continue
        m = _RE_DISCONNECT.search(line)
        if m:
            name = m.group(1)
            if name and not name.startswith("Server"):
                events.append({"type": "leave", "ts": ts_full, "driver": name})

    events.reverse()
    return jsonify({"ok": True, "events": events[:limit], "total": len(events), "lines_read": len(lines)})


# ── Config backup / restore ───────────────────────────────────────────────────

@bp.route("/api/backup")
@login_required
def config_backup():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for fname in ["server_cfg.ini", "entry_list.ini", "extra_cfg.yml", "welcome.txt"]:
            p = CFG_DIR / fname
            if p.exists():
                zf.write(str(p), fname)
    buf.seek(0)
    return send_file(buf, mimetype="application/zip", as_attachment=True, download_name="acserver_backup.zip")


@bp.route("/api/restore", methods=["POST"])
@login_required
@csrf_protect
def config_restore():
    f = request.files.get("backup")
    if not f:
        return jsonify({"ok": False, "msg": "No file"}), 400
    if not (f.filename or "").lower().endswith(".zip"):
        return jsonify({"ok": False, "msg": "ZIP only"}), 400
    try:
        tmp = UPLOAD_TMP / secure_filename(f.filename or "backup.zip")
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

@bp.route("/api/delete_content/<ctype>/<name>", methods=["DELETE"])
@login_required
@csrf_protect
@api_rate_limit(max_calls=10, window=60)
def delete_content(ctype, name):
    if ctype not in ("car", "track"):
        return jsonify({"ok": False, "msg": "type must be car or track"}), 400
    target = CARS_DIR / name if ctype == "car" else TRACKS_DIR / name
    if not target.exists():
        return jsonify({"ok": False, "msg": f"{name} not found"}), 404
    try:
        shutil.rmtree(str(target))
        if ctype == "car":
            cfg      = read_server_cfg()
            existing = [c for c in cfg.get("CARS", "").split(";") if c and c != name]
            update_server_cfg({"CARS": ";".join(existing)})
            # Bug fix #8: tatsächliche Slot-Anzahl aus entry_list.ini lesen
            slots = get_current_slots_per_car(existing)
            regen_entry_list(existing, slots)
        return jsonify({"ok": True, "msg": f"{name} deleted"})
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e)}), 500


@bp.route("/api/installed_content")
@login_required
def installed_content():
    from constants import CARS_DIR, TRACKS_DIR
    cfg          = read_server_cfg()
    active_cars  = [c for c in cfg.get("CARS", "").split(";") if c]
    active_track = cfg.get("TRACK", "")
    cars   = [get_car_rich(c, active_cars) for c in list_cars()]
    tracks = [get_track_rich(t, active_track) for t in list_tracks()]
    return jsonify({"cars": cars, "tracks": tracks})


@bp.route("/api/content_detail/<kind>/<name>")
@login_required
def content_detail(kind, name):
    cfg          = read_server_cfg()
    active_cars  = [c for c in cfg.get("CARS", "").split(";") if c]
    active_track = cfg.get("TRACK", "")
    if kind == "car":
        return jsonify(get_car_detail(name, active_cars))
    if kind == "track":
        return jsonify(get_track_detail(name, active_track))
    return jsonify({"ok": False, "msg": "kind must be car or track"}), 400


@bp.route("/api/disk_usage")
@login_required
def disk_usage():
    import shutil as _shutil
    from constants import CARS_DIR, TRACKS_DIR, SERVER_DIR
    cars_mb   = get_folder_size_mb(CARS_DIR)   if CARS_DIR.exists()   else 0
    tracks_mb = get_folder_size_mb(TRACKS_DIR) if TRACKS_DIR.exists() else 0
    try:
        du = _shutil.disk_usage(str(SERVER_DIR))
        free_gb  = round(du.free  / (1024**3), 1)
        total_gb = round(du.total / (1024**3), 1)
    except OSError:
        free_gb = total_gb = 0
    return jsonify({
        "cars_mb": cars_mb, "tracks_mb": tracks_mb,
        "free_gb": free_gb, "total_gb": total_gb,
    })


# ── ZIP upload ────────────────────────────────────────────────────────────────

@bp.route("/upload", methods=["POST"])
@login_required
@csrf_protect
@api_rate_limit(max_calls=10, window=60)
def upload_zip():
    if "file" not in request.files:
        return jsonify({"ok": False, "msg": "No file"}), 400
    f = request.files["file"]
    if not (f.filename or "").lower().endswith(".zip"):
        return jsonify({"ok": False, "msg": "ZIP only"}), 400
    filename  = secure_filename(f.filename or "upload.zip")
    save_path = UPLOAD_TMP / filename
    f.save(save_path)
    items, err = analyze_zip(save_path)
    if err:
        return jsonify({"ok": False, "msg": f"ZIP error: {err}"}), 400
    try:
        with zipfile.ZipFile(save_path) as zf:
            sample = zf.namelist()[:40]
    except Exception:
        sample = []
    return jsonify({"ok": True, "filename": filename,
                    "cars": items["cars"], "tracks": items["tracks"],
                    "_zip_sample": sample})


@bp.route("/import_zip", methods=["POST"])
@login_required
@csrf_protect
def import_zip():
    data       = request.json or {}
    filename   = data.get("filename")
    sel_cars   = data.get("cars", [])
    sel_tracks = data.get("tracks", [])
    if not filename:
        return jsonify({"ok": False, "msg": "No filename"}), 400
    zip_path = UPLOAD_TMP / secure_filename(filename)
    if not zip_path.exists():
        return jsonify({"ok": False, "msg": "ZIP not found – re-upload"}), 404
    try:
        imported = extract_from_zip(zip_path, sel_cars, sel_tracks)
        zip_path.unlink(missing_ok=True)
        for track in sel_tracks:
            auto_add_track_params(track)
        if sel_cars:
            cfg      = read_server_cfg()
            existing = [c for c in cfg.get("CARS", "").split(";") if c]
            new_cars = [c for c in sel_cars if c not in existing]
            if new_cars:
                all_cars = existing + new_cars
                update_server_cfg({"CARS": ";".join(all_cars)})
                regen_entry_list(all_cars, 2)
        return jsonify({"ok": True, "imported": imported})
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e)}), 500


# ── Folder upload ─────────────────────────────────────────────────────────────

@bp.route("/upload_file", methods=["POST"])
@login_required
@csrf_protect
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


@bp.route("/upload_folder_done", methods=["POST"])
@login_required
@csrf_protect
def upload_folder_done():
    content_type = request.json.get("type", "").strip() if request.json else ""
    root_name    = request.json.get("root_name", "").strip() if request.json else ""
    if content_type not in ("car", "track") or not root_name:
        return jsonify({"ok": False, "msg": "missing fields"}), 400
    if content_type == "track":
        auto_add_track_params(root_name)
    else:
        cfg      = read_server_cfg()
        existing = [c for c in cfg.get("CARS", "").split(";") if c]
        if root_name not in existing:
            all_cars = existing + [root_name]
            update_server_cfg({"CARS": ";".join(all_cars)})
            regen_entry_list(all_cars, 2)
    return jsonify({"ok": True, "name": root_name})


@bp.route("/upload_folder", methods=["POST"])
@login_required
@csrf_protect
def upload_folder():
    content_type = request.form.get("type", "").strip()
    root_name    = request.form.get("root_name", "").strip()
    files        = request.files.getlist("files")
    if content_type not in ("car", "track"):
        return jsonify({"ok": False, "msg": "type must be car or track"}), 400
    if not root_name:
        return jsonify({"ok": False, "msg": "root_name required"}), 400
    base_dir = CARS_DIR / root_name if content_type == "car" else TRACKS_DIR / root_name
    written  = 0
    for f in files:
        rel = secure_filename_path(f.filename)
        tgt = base_dir / rel
        tgt.parent.mkdir(parents=True, exist_ok=True)
        f.save(str(tgt))
        written += 1
    if content_type == "track":
        auto_add_track_params(root_name)
    else:
        cfg      = read_server_cfg()
        existing = [c for c in cfg.get("CARS", "").split(";") if c]
        if root_name not in existing:
            all_cars = existing + [root_name]
            update_server_cfg({"CARS": ";".join(all_cars)})
            regen_entry_list(all_cars, 2)
    return jsonify({"ok": True, "name": root_name, "files": written})


# ── Server profile (welcome message) ──────────────────────────────────────────

@bp.route("/api/server_profile", methods=["GET", "POST"])
@login_required
@csrf_protect
def server_profile():
    if request.method == "GET":
        msg = get_extra_cfg_description()
        if not msg and WELCOME_FILE.exists():
            msg = WELCOME_FILE.read_text(encoding="utf-8")
        return jsonify({"ok": True, "welcome": msg})
    data    = request.get_json() or {}
    welcome = data.get("welcome", "")
    WELCOME_FILE.write_text(welcome, encoding="utf-8")
    set_extra_cfg_description(welcome)
    update_server_cfg({"WELCOME_MESSAGE": "cfg/welcome.txt"})
    return jsonify({"ok": True})


# ── Public leaderboard ────────────────────────────────────────────────────────

def _fmt_ms(ms: int) -> str:
    if not ms or ms <= 0:
        return "—"
    m = ms // 60000
    s = (ms % 60000) / 1000
    return f"{m}:{s:06.3f}"


@bp.route("/leaderboard")
def public_leaderboard():
    from helpers.laptimes import load_laptimes
    entries = load_laptimes()
    safe = [
        {
            "driver":  e.get("driver", ""),
            "car":     e.get("car", ""),
            "track":   e.get("track", ""),
            "laptime": e.get("laptime", 0),
            "cuts":    e.get("cuts", 0),
            "ts":      e.get("ts", ""),
            "fmt":     _fmt_ms(e.get("laptime", 0)),
        }
        for e in entries
    ]
    best: dict = {}
    for e in safe:
        key = (e["driver"], e["track"])
        if key not in best or e["laptime"] < best[key]["laptime"]:
            best[key] = e
    best_list = sorted(best.values(), key=lambda x: (x["track"], x["laptime"]))
    all_laps  = sorted(safe, key=lambda x: x.get("laptime", 99999999))[:200]
    tracks    = sorted({e["track"] for e in safe if e["track"]})
    cars      = sorted({e["car"]   for e in safe if e["car"]})
    cfg         = read_server_cfg()
    server_name = cfg.get("NAME", "Assetto Corsa Server")
    return render_template(
        "leaderboard.html",
        server_name=server_name,
        best_list=best_list,
        all_laps=all_laps,
        tracks=tracks,
        cars=cars,
        total=len(entries),
    )


@bp.route("/leaderboard/data")
def public_leaderboard_data():
    from helpers.laptimes import load_laptimes
    track   = request.args.get("track", "").strip().lower()
    car     = request.args.get("car",   "").strip().lower()
    entries = load_laptimes()
    safe = [
        {
            "driver":  e.get("driver", ""),
            "car":     e.get("car", ""),
            "track":   e.get("track", ""),
            "laptime": e.get("laptime", 0),
            "cuts":    e.get("cuts", 0),
            "ts":      e.get("ts", ""),
            "fmt":     _fmt_ms(e.get("laptime", 0)),
        }
        for e in entries
    ]
    if track: safe = [e for e in safe if track in e["track"].lower()]
    if car:   safe = [e for e in safe if car   in e["car"].lower()]
    safe = sorted(safe, key=lambda x: x.get("laptime", 99999999))
    return jsonify({"ok": True, "entries": safe, "total": len(safe)})
