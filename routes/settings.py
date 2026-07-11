"""Settings-Routes: Konfiguration speichern (Track, Assists, Session, Weather, etc.)."""
from flask import Blueprint, jsonify, request

from helpers.auth import api_rate_limit, csrf_protect, login_required
from helpers.config_io import read_server_cfg, remove_cfg_section, update_section_cfg, update_server_cfg
from helpers.content import get_current_slots_per_car, regen_entry_list
from helpers.laptimes import _load_chat_notify_config, _load_cut_actions_config, save_chat_notify_config, save_cut_actions_config
from helpers.system import load_spline_points, maybe_restart, run_systemctl
from helpers.telegram import _load_telegram_config, save_telegram_config, telegram_notify

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
        ok2, msg2 = update_server_cfg({"MAX_CLIENTS": total_slots})
        if not ok2:
            ok, msg = ok2, msg2
    maybe_restart(data)
    return jsonify({"ok": ok, "msg": msg})


# ── Assists ───────────────────────────────────────────────────────────────────

@bp.route("/save_assists", methods=["POST"])
@login_required
@csrf_protect
@api_rate_limit(max_calls=20, window=60)
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
@api_rate_limit(max_calls=20, window=60)
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
@api_rate_limit(max_calls=20, window=60)
def save_session():
    data = request.json or {}

    # QUALIFY: Sektion entfernen wenn TIME=0 (Content Manager zeigt sonst Icon)
    qualify_time = int(data.get("qualify_time", 0) or 0)
    if "qualify_time" in data and qualify_time == 0:
        remove_cfg_section("QUALIFY")
    elif "qualify_time" in data and qualify_time > 0:
        upd = {"TIME": qualify_time, "IS_OPEN": 1 if data.get("qualify_open") else 0}
        update_section_cfg({"QUALIFY": upd})

    # RACE: Sektion entfernen wenn LAPS=0 (Content Manager zeigt sonst Icon)
    race_laps = int(data.get("race_laps", 0) or 0)
    if "race_laps" in data and race_laps == 0:
        remove_cfg_section("RACE")
    elif "race_laps" in data and race_laps > 0:
        upd = {
            "LAPS":      race_laps,
            "WAIT_TIME": int(data.get("race_wait", 60) or 60),
            "IS_OPEN":   1 if data.get("race_open") else 0,
        }
        update_section_cfg({"RACE": upd})

    # PRACTICE immer aktualisieren
    practice_upd: dict = {}
    if "practice_time" in data: practice_upd["TIME"]    = int(data.get("practice_time", 0) or 0)
    if "practice_open" in data: practice_upd["IS_OPEN"] = 1 if data["practice_open"] else 0
    if practice_upd:
        ok_p, msg_p = update_section_cfg({"PRACTICE": practice_upd})
        if not ok_p:
            maybe_restart(data)
            return jsonify({"ok": False, "msg": msg_p})

    maybe_restart(data)
    return jsonify({"ok": True, "msg": "Saved"})


# ── Weather ───────────────────────────────────────────────────────────────────

@bp.route("/save_weather", methods=["POST"])
@login_required
@csrf_protect
@api_rate_limit(max_calls=20, window=60)
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
@api_rate_limit(max_calls=20, window=60)
def save_dynamic_track():
    data    = request.json or {}
    allowed = {"SESSION_START", "RANDOMNESS", "SESSION_TRANSFER", "LAP_GAIN"}
    upd     = {k: v for k, v in data.items() if k in allowed}
    if not upd:
        return jsonify({"ok": False, "msg": "No data"})
    ok, msg = update_section_cfg({"DYNAMIC_TRACK": upd})
    maybe_restart(data)
    return jsonify({"ok": ok, "msg": msg})


# ── Chat Lap-Notifications ────────────────────────────────────────────────────

@bp.route("/api/chat_notify", methods=["GET"])
@login_required
def get_chat_notify():
    return jsonify(_load_chat_notify_config())


@bp.route("/api/chat_notify", methods=["POST"])
@login_required
@csrf_protect
def set_chat_notify():
    data = request.json or {}
    splits_raw = data.get("split_points", [])
    splits = [
        {"pos": float(s["pos"]), "name": str(s["name"])}
        for s in splits_raw
        if 0.0 < float(s.get("pos", 0)) < 1.0 and s.get("name")
    ]
    cfg = {
        "enabled":      bool(data.get("enabled", False)),
        "show_delta":   bool(data.get("show_delta", True)),
        "show_cuts":    bool(data.get("show_cuts",  True)),
        "show_splits":  bool(data.get("show_splits", False)),
        "prefix":       str(data.get("prefix",  ">> ")),
        "split_points": splits,
    }
    try:
        save_chat_notify_config(cfg)
        # Split-Config sofort in UDP-Listener übernehmen
        from helpers.system import set_split_config
        set_split_config(splits if cfg["show_splits"] else [])
        return jsonify({"ok": True, "cfg": cfg})
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e)}), 500


# ── Plugin-Status + LiveWeatherPlugin Toggle ──────────────────────────────────

import re as _re
from constants import EXTRA_CFG_FILE


def _read_yaml() -> str:
    return EXTRA_CFG_FILE.read_text(encoding="utf-8") if EXTRA_CFG_FILE.exists() else ""


def _write_yaml(content: str):
    EXTRA_CFG_FILE.write_text(content, encoding="utf-8")


def _active_plugins(content: str) -> list:
    """Gibt die Liste der aktiven Plugins aus EnablePlugins zurück."""
    m = _re.search(r'EnablePlugins:\s*\n((?:[ \t]+-[ \t]+\S+\n?)*)', content)
    if not m:
        return []
    return _re.findall(r'[ \t]+-[ \t]+(\S+)', m.group(1))


def _remove_plugin(content: str, name: str) -> str:
    return _re.sub(rf'[ \t]+-[ \t]+{name}\n?', '', content)


def _add_plugin(content: str, name: str) -> str:
    if name in _active_plugins(content):
        return content
    return _re.sub(
        r'(EnablePlugins:\s*\n)((?:[ \t]+-[ \t]+\S+\n?)*)',
        lambda m: m.group(1) + m.group(2) + f"  - {name}\n",
        content,
    )


@bp.route("/api/plugin_status", methods=["GET"])
@login_required
def get_plugin_status():
    """Gibt aktive Plugins + LiveWeatherPlugin-Details zurück."""
    content = _read_yaml()
    active  = _active_plugins(content)

    # LiveWeatherPlugin API-Key und Interval auslesen
    m_key = _re.search(r'^\s*OpenWeatherMapApiKey:\s*"?([^"\n]+)"?', content, _re.MULTILINE)
    api_key_raw = m_key.group(1).strip() if m_key else ""
    api_key_set = bool(api_key_raw)
    api_key_hint = ("•" * 8 + api_key_raw[-4:]) if len(api_key_raw) > 4 else ("" if not api_key_raw else "gesetzt")

    m_int = _re.search(r'^\s*RefreshIntervalMinutes:\s*(\d+)', content, _re.MULTILINE)
    interval = int(m_int.group(1)) if m_int else 10

    return jsonify({
        "active":       active,
        "lwp_enabled":  "LiveWeatherPlugin" in active,
        "lwp_key_set":  api_key_set,
        "lwp_key_hint": api_key_hint,
        "lwp_interval": interval,
    })


@bp.route("/api/live_weather_plugin", methods=["POST"])
@login_required
@csrf_protect
def set_live_weather_plugin():
    data    = request.json or {}
    enabled = bool(data.get("enabled", False))
    api_key = data.get("api_key", "").strip()
    interval = int(data.get("interval", 10) or 10)

    if not EXTRA_CFG_FILE.exists():
        return jsonify({"ok": False, "msg": "extra_cfg.yml nicht gefunden"}), 500

    try:
        content = _read_yaml()

        # Kein neuer Key angegeben → vorhandenen aus der Config verwenden
        if enabled and not api_key:
            m = _re.search(r'^\s*OpenWeatherMapApiKey:\s*"?([^"\n]+)"?', content, _re.MULTILINE)
            api_key = m.group(1).strip() if m else ""

        if enabled:
            if not api_key:
                return jsonify({"ok": False, "msg": "API-Key fehlt"}), 400

            # LiveWeather und VotingWeather schließen sich gegenseitig aus
            content = _remove_plugin(content, "VotingWeatherPlugin")
            content = _add_plugin(content, "LiveWeatherPlugin")

            content = _re.sub(r'^#LiveWeatherPlugin:', 'LiveWeatherPlugin:', content, flags=_re.MULTILINE)
            content = _re.sub(r'^#\s*OpenWeatherMapApiKey:.*', f'  OpenWeatherMapApiKey: "{api_key}"', content, flags=_re.MULTILINE)
            content = _re.sub(r'^#\s*RefreshIntervalMinutes:.*', f'  RefreshIntervalMinutes: {interval}', content, flags=_re.MULTILINE)

            if "OpenWeatherMapApiKey" not in content:
                content += f"\nLiveWeatherPlugin:\n  OpenWeatherMapApiKey: \"{api_key}\"\n  RefreshIntervalMinutes: {interval}\n"
            else:
                content = _re.sub(
                    r'(OpenWeatherMapApiKey:\s*)"?[^"\n]*"?',
                    lambda m: f'{m.group(1)}"{api_key}"',
                    content,
                )
                content = _re.sub(
                    r'(RefreshIntervalMinutes:\s*)\d+',
                    f'\\g<1>{interval}',
                    content,
                )
        else:
            # Nur LiveWeather deaktivieren — VotingWeather bleibt wie es ist
            content = _remove_plugin(content, "LiveWeatherPlugin")

        _write_yaml(content)
        maybe_restart({"restart": True})
        return jsonify({"ok": True, "enabled": enabled})
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e)}), 500


# ── VotingWeatherPlugin ───────────────────────────────────────────────────────

@bp.route("/api/voting_weather", methods=["GET"])
@login_required
def get_voting_weather():
    content = _read_yaml()
    m_int = _re.search(r'VotingIntervalMinutes:\s*(\d+)', content)
    m_dur = _re.search(r'VotingDurationSeconds:\s*(\d+)', content)
    m_seq = _re.search(r'SequentialWeatherReplacement:\s*(true|false)', content)
    return jsonify({
        "active":     "VotingWeatherPlugin" in _active_plugins(content),
        "interval":   int(m_int.group(1)) if m_int else 30,
        "duration":   int(m_dur.group(1)) if m_dur else 30,
        "sequential": (m_seq.group(1) == "true") if m_seq else False,
    })


@bp.route("/api/voting_weather", methods=["POST"])
@login_required
@csrf_protect
def set_voting_weather():
    data     = request.json or {}
    enabled  = data.get("enabled")          # None = nur Settings speichern, kein Toggle
    interval = max(1, int(data.get("interval", 30) or 30))
    duration = max(5, int(data.get("duration", 30) or 30))
    seq      = bool(data.get("sequential", False))

    if not EXTRA_CFG_FILE.exists():
        return jsonify({"ok": False, "msg": "extra_cfg.yml nicht gefunden"}), 500
    try:
        content = _read_yaml()

        if enabled is True:
            # VotingWeather aktivieren — LiveWeather muss raus (Konflikt)
            content = _remove_plugin(content, "LiveWeatherPlugin")
            content = _add_plugin(content, "VotingWeatherPlugin")
        elif enabled is False:
            # Nur VotingWeather deaktivieren — nichts anderes anfassen
            content = _remove_plugin(content, "VotingWeatherPlugin")

        content = _re.sub(r'(VotingIntervalMinutes:\s*)\d+',   f'\\g<1>{interval}', content)
        content = _re.sub(r'(VotingDurationSeconds:\s*)\d+',   f'\\g<1>{duration}', content)
        content = _re.sub(
            r'(SequentialWeatherReplacement:\s*)(true|false)',
            f'\\g<1>{"true" if seq else "false"}',
            content,
        )
        _write_yaml(content)
        if enabled is not None:
            maybe_restart({"restart": True})
        return jsonify({"ok": True, "enabled": enabled})
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e)}), 500


# ── Ignore Configuration Errors (z.B. fehlende data.acd-Checksums) ───────────

_IGNORE_CFG_KEYS = {
    "MissingCarChecksums", "MissingTrackParams",
    "WrongServerDetails", "UnsafeAdminWhitelist",
}


@bp.route("/api/ignore_config_errors", methods=["GET"])
@login_required
def get_ignore_config_errors():
    content = _read_yaml()
    data = {}
    for key in _IGNORE_CFG_KEYS:
        m = _re.search(rf'(?m)^[ \t]+{key}:\s*(true|false)\s*$', content)
        data[key] = m.group(1) == "true" if m else False
    return jsonify({"ok": True, "data": data})


@bp.route("/api/ignore_config_errors", methods=["POST"])
@login_required
@csrf_protect
def set_ignore_config_errors():
    data  = request.json or {}
    key   = data.get("key")
    value = bool(data.get("value"))
    if key not in _IGNORE_CFG_KEYS:
        return jsonify({"ok": False, "msg": "Ungültiger Schlüssel"}), 400
    if not EXTRA_CFG_FILE.exists():
        return jsonify({"ok": False, "msg": "extra_cfg.yml nicht gefunden"}), 500
    try:
        content  = _read_yaml()
        val_str  = "true" if value else "false"
        new_content, n = _re.subn(
            rf'(?m)^([ \t]+{key}:\s*)(true|false)\s*$',
            rf'\g<1>{val_str}',
            content,
        )
        if n == 0:
            return jsonify({"ok": False, "msg": f"{key} nicht in extra_cfg.yml gefunden"}), 500
        _write_yaml(new_content)
        if data.get("restart"):
            run_systemctl("restart")
        return jsonify({"ok": True, "key": key, "value": value})
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e)}), 500


# ── Telegram ─────────────────────────────────────────────────────────────────

@bp.route("/api/telegram", methods=["GET"])
@login_required
def get_telegram():
    return jsonify(_load_telegram_config())


@bp.route("/api/telegram", methods=["POST"])
@login_required
@csrf_protect
def set_telegram():
    data = request.json or {}
    cfg = {
        "token":       str(data.get("token", "")),
        "chat_id":     str(data.get("chat_id", "")),
        "notify_join": bool(data.get("notify_join", False)),
    }
    try:
        save_telegram_config(cfg)
        return jsonify({"ok": True, "cfg": cfg})
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e)}), 500


@bp.route("/api/telegram/test", methods=["POST"])
@login_required
@csrf_protect
def test_telegram():
    cfg     = _load_telegram_config()
    token   = cfg.get("token", "")
    chat_id = cfg.get("chat_id", "")
    if not token or not chat_id:
        return jsonify({"ok": False, "msg": "Token und Chat-ID fehlen"})
    try:
        telegram_notify(token, chat_id, "🧪 Test\\-Nachricht vom AC Server Dashboard", raise_on_error=True)
        return jsonify({"ok": True, "msg": "Test-Nachricht gesendet"})
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e)})


# ── Cut Actions ───────────────────────────────────────────────────────────────

@bp.route("/api/cut_actions", methods=["GET"])
@login_required
def get_cut_actions():
    return jsonify(_load_cut_actions_config())


@bp.route("/api/cut_actions", methods=["POST"])
@login_required
@csrf_protect
def set_cut_actions():
    data = request.json or {}
    cfg = {
        "enabled":           bool(data.get("enabled", False)),
        "warn_cuts_per_lap": int(data.get("warn_cuts_per_lap", 2) or 0),
        "warn_message":      str(data.get("warn_message", "⚠️ {driver}: {cuts} Cuts!")),
        "kick_session_cuts": int(data.get("kick_session_cuts", 0) or 0),
        "kick_message":      str(data.get("kick_message", "Kick: Zu viele Cuts ({cuts} gesamt)")),
    }
    try:
        save_cut_actions_config(cfg)
        return jsonify({"ok": True, "cfg": cfg})
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e)}), 500


# ── Track params ──────────────────────────────────────────────────────────────

@bp.route("/api/add_track_params", methods=["POST"])
@login_required
@csrf_protect
def add_track_params():
    data  = request.json or {}
    import re as _re
    track = data.get("track", "").strip()
    if not track:
        return jsonify({"ok": False, "msg": "track required"}), 400
    if not _re.match(r'^[a-zA-Z0-9_\-]+$', track):
        return jsonify({"ok": False, "msg": "Ungültiger Track-Name"}), 400
    city = data.get("city", track).strip() or track
    try:
        lat = float(data.get("lat", 0) or 0)
        lon = float(data.get("lon", 0) or 0)
        tz  = int(float(data.get("tz", 0) or 0))
    except (ValueError, TypeError):
        return jsonify({"ok": False, "msg": "Ungültige Koordinaten"}), 400
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

# ── KI-Fahrer (AI traffic) ────────────────────────────────────────────────────

@bp.route("/api/ai_config", methods=["GET"])
@login_required
def get_ai_config():
    from helpers.config_io import read_ai_params, read_extra_cfg
    from constants import AI_PARAM_KEYS, TRACKS_DIR

    cfg = read_extra_cfg()
    ai_params = read_ai_params()

    # Aktuelle Strecke aus server_cfg.ini
    from helpers.config_io import read_server_cfg
    srv = read_server_cfg()
    track = srv.get("TRACK", "")
    layout = srv.get("TRACK_LAYOUT", "")

    # AssettoServer sucht fast_lane.ai immer im Track-Root-Ordner
    spline_root = TRACKS_DIR / track / "ai" / "fast_lane.ai" if track else None
    spline_found = spline_root.exists() if spline_root else False

    return jsonify({
        "ok": True,
        "EnableAi": cfg.get("EnableAi", "false"),
        "spline_found": spline_found,
        "spline_path": str(spline_root) if spline_root else "",
        "track": track,
        "layout": layout,
        "params": {k: ai_params.get(k, "") for k in AI_PARAM_KEYS},
    })


@bp.route("/api/ai_config", methods=["POST"])
@login_required
@csrf_protect
@api_rate_limit(max_calls=10, window=60)
def save_ai_config():
    from helpers.config_io import write_ai_params, write_extra_cfg
    from constants import AI_PARAM_KEYS

    data = request.json or {}

    # EnableAi in extra_cfg.yml (Top-Level)
    enable_val = data.get("EnableAi", "false")
    ok, msg = write_extra_cfg({"EnableAi": enable_val})
    if not ok:
        return jsonify({"ok": False, "msg": msg})

    # AiParams-Block schreiben
    updates = {}
    for key in AI_PARAM_KEYS:
        if key in data:
            val = data[key]
            if key in ("HideAiCars", "TwoWayTraffic"):
                updates[key] = str(val).lower() in ("true", "1", "yes")
            else:
                try:
                    updates[key] = float(val) if "." in str(val) else int(val)
                except (ValueError, TypeError):
                    updates[key] = val

    if updates:
        ok2, msg2 = write_ai_params(updates)
        if not ok2:
            return jsonify({"ok": False, "msg": msg2})

    return jsonify({"ok": True, "msg": "KI-Konfiguration gespeichert"})
