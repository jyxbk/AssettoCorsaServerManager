"""Settings-Routes: Konfiguration speichern (Track, Assists, Session, Weather, etc.)."""
from flask import Blueprint, jsonify, request

from helpers.auth import api_rate_limit, csrf_protect, login_required
from helpers.config_io import read_server_cfg, update_section_cfg, update_server_cfg
from helpers.content import get_current_slots_per_car, regen_entry_list
from helpers.system import load_spline_points, maybe_restart

bp = Blueprint("settings", __name__)


# ── Track & Car config ────────────────────────────────────────────────────────

@bp.route("/save_config", methods=["POST"])
@login_required
@csrf_protect
@api_rate_limit(max_calls=20, window=60)
def save_config():
    data = request.json or {}
    updates = {}
    if data.get("track")  is not None: updates["TRACK"]        = data["track"]
    if data.get("layout") is not None:
        updates["TRACK_LAYOUT"] = data.get("layout", "")
        updates["CONFIG_TRACK"] = data.get("layout", "")
    if data.get("cars"):
        updates["CARS"] = ";".join(data["cars"])
    ok, msg = update_server_cfg(updates)
    if updates.get("TRACK") or updates.get("TRACK_LAYOUT"):
        load_spline_points.cache_clear()
    # Bug fix #10: int(... or 2) verhindert TypeError wenn None übergeben wird
    spc = int(data.get("slots_per_car") or 2)
    car_config = data.get("car_config")
    if data.get("cars"):
        spc_clamped = max(1, min(5, spc))
        regen_entry_list(data["cars"], spc_clamped, car_config)
        total_slots = len(data["cars"]) * spc_clamped
        update_server_cfg({"MAX_CLIENTS": total_slots})
    maybe_restart(data)
    return jsonify({"ok": ok, "msg": msg})


# ── Assists ───────────────────────────────────────────────────────────────────

@bp.route("/save_assists", methods=["POST"])
@login_required
@csrf_protect
def save_assists():
    data = request.json or {}
    allowed = {
        "ABS_ALLOWED", "TC_ALLOWED", "STABILITY_ALLOWED", "AUTOCLUTCH_ALLOWED",
        "TYRE_BLANKETS_ALLOWED", "FORCE_VIRTUAL_MIRROR", "FUEL_RATE",
        "DAMAGE_MULTIPLIER", "TYRE_WEAR_RATE", "ALLOWED_TYRES_OUT", "MAX_CLIENTS",
    }
    updates = {k: v for k, v in data.items() if k in allowed}
    ok, msg = update_server_cfg(updates)
    maybe_restart(data)
    return jsonify({"ok": ok, "msg": msg})


# ── Server general settings ───────────────────────────────────────────────────

@bp.route("/save_server_settings", methods=["POST"])
@login_required
@csrf_protect
def save_server_settings():
    data = request.json or {}
    allowed = {
        "NAME", "PASSWORD", "ADMIN_PASSWORD", "REGISTER_TO_LOBBY",
        "MAX_CLIENTS", "UDP_PORT", "TCP_PORT", "HTTP_PORT", "SUN_ANGLE",
        "PICKUP_MODE_ENABLED", "LOOP_MODE", "SLEEP_TIME", "CLIENT_SEND_INTERVAL_HZ",
        "RACE_OVER_TIME", "KICK_QUORUM", "VOTING_QUORUM", "VOTE_DURATION",
        "BLACKLIST_MODE", "LEGAL_TYRES", "ALLOWED_TYRES_OUT",
    }
    updates = {k: v for k, v in data.items() if k in allowed}
    ok, msg = update_server_cfg(updates)
    maybe_restart(data)
    return jsonify({"ok": ok, "msg": msg})


# ── Session settings ──────────────────────────────────────────────────────────

@bp.route("/save_session", methods=["POST"])
@login_required
@csrf_protect
def save_session():
    data = request.json or {}
    section_updates = {}
    for sess in ("PRACTICE", "QUALIFY", "RACE"):
        key = sess.lower()
        upd = {}
        if f"{key}_time" in data: upd["TIME"]      = data[f"{key}_time"]
        if f"{key}_laps" in data: upd["LAPS"]      = data[f"{key}_laps"]
        if f"{key}_open" in data: upd["IS_OPEN"]   = 1 if data[f"{key}_open"] else 0
        if f"{key}_wait" in data: upd["WAIT_TIME"] = data[f"{key}_wait"]
        if upd:
            section_updates[sess] = upd
    if not section_updates:
        return jsonify({"ok": False, "msg": "No data"})
    ok, msg = update_section_cfg(section_updates)
    maybe_restart(data)
    return jsonify({"ok": ok, "msg": msg})


# ── Weather ───────────────────────────────────────────────────────────────────

@bp.route("/save_weather", methods=["POST"])
@login_required
@csrf_protect
def save_weather():
    data = request.json or {}
    section_updates = {}
    for i in range(2):
        key  = f"weather_{i}"
        sect = f"WEATHER_{i}"
        upd  = {}
        if f"{key}_graphics" in data: upd["GRAPHICS"]                 = data[f"{key}_graphics"]
        if f"{key}_ambient"  in data: upd["BASE_TEMPERATURE_AMBIENT"] = data[f"{key}_ambient"]
        if f"{key}_road"     in data: upd["BASE_TEMPERATURE_ROAD"]    = data[f"{key}_road"]
        if f"{key}_var_amb"  in data: upd["VARIATION_AMBIENT"]        = data[f"{key}_var_amb"]
        if f"{key}_var_road" in data: upd["VARIATION_ROAD"]           = data[f"{key}_var_road"]
        if upd:
            section_updates[sect] = upd
    if not section_updates:
        return jsonify({"ok": False, "msg": "No data"})
    ok, msg = update_section_cfg(section_updates)
    maybe_restart(data)
    return jsonify({"ok": ok, "msg": msg})


# ── Dynamic track ─────────────────────────────────────────────────────────────

@bp.route("/save_dynamic_track", methods=["POST"])
@login_required
@csrf_protect
def save_dynamic_track():
    data    = request.json or {}
    allowed = {"SESSION_START", "RANDOMNESS", "SESSION_TRANSFER", "LAP_GAIN"}
    upd     = {k: v for k, v in data.items() if k in allowed}
    if not upd:
        return jsonify({"ok": False, "msg": "No data"})
    ok, msg = update_section_cfg({"DYNAMIC_TRACK": upd})
    maybe_restart(data)
    return jsonify({"ok": ok, "msg": msg})


# ── Track params ──────────────────────────────────────────────────────────────

@bp.route("/api/add_track_params", methods=["POST"])
@login_required
@csrf_protect
def add_track_params():
    data  = request.json or {}
    track = data.get("track", "").strip()
    city  = data.get("city", track).strip() or track
    lat   = float(data.get("lat", 0) or 0)
    lon   = float(data.get("lon", 0) or 0)
    tz    = int(float(data.get("tz", 0) or 0))
    if not track:
        return jsonify({"ok": False, "msg": "track required"}), 400
    from constants import TRACK_PARAMS_FILE
    section = f"[{track.lower()}]"
    TRACK_PARAMS_FILE.parent.mkdir(parents=True, exist_ok=True)
    existing = TRACK_PARAMS_FILE.read_text(encoding="utf-8") if TRACK_PARAMS_FILE.exists() else ""
    if section in existing:
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
                if k == "CITY":      new_lines.append(f"CITY={city}"); continue
                if k == "LATITUDE":  new_lines.append(f"LATITUDE={lat}"); continue
                if k == "LONGITUDE": new_lines.append(f"LONGITUDE={lon}"); continue
                if k == "TIMEZONE":  new_lines.append(f"TIMEZONE={tz}"); continue
            new_lines.append(line)
        TRACK_PARAMS_FILE.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
        return jsonify({"ok": True, "msg": f"Updated params for {track}"})
    entry = f"\n{section}\nCITY={city}\nLATITUDE={lat}\nLONGITUDE={lon}\nTIMEZONE={tz}\n"
    with open(TRACK_PARAMS_FILE, "a", encoding="utf-8") as f:
        f.write(entry)
    return jsonify({"ok": True, "msg": f"Added params for {track}"})
