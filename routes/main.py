"""Haupt-Routes: Index, Live-API, Uptime, Bilder, Server-Control, Logs, Login/Logout."""
import math

from flask import Blueprint, jsonify, redirect, render_template, request, send_file, session, url_for

from constants import (
    ACWEB_PASS, ACWEB_USER, CARS_DIR, TRACKS_DIR, WEATHER_PRESETS,
)
from helpers.auth import api_rate_limit, check_rate_limit, csrf_protect, login_required, _get_client_ip
from helpers.config_io import read_full_server_cfg, read_server_cfg
from helpers.content import get_car_skins, get_car_ui, get_track_ui, list_cars, list_tracks
from helpers.system import (
    ensure_udp, get_car_data, get_recent_chat, get_system_stats,
    get_uptime_string, load_spline_points, run_systemctl, server_info,
    server_json, server_status, get_local_ip,
)
import subprocess
from constants import SERVICE_NAME

bp = Blueprint("main", __name__)


# ── Auth ──────────────────────────────────────────────────────────────────────

@bp.route("/login", methods=["GET", "POST"])
def login():
    if session.get("logged_in"):
        return redirect(url_for("main.index"))
    error = None
    if request.method == "POST":
        ip = _get_client_ip()
        if not check_rate_limit(ip):
            error = "Too many attempts. Try again in 5 minutes."
        else:
            username = request.form.get("username", "")
            password = request.form.get("password", "")
            if username == ACWEB_USER and password == ACWEB_PASS:
                session["logged_in"] = True
                session.permanent = False
                return redirect(url_for("main.index"))
            else:
                error = "Invalid username or password."
    return render_template("login.html", error=error)


@bp.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("main.login"))


# ── Index ─────────────────────────────────────────────────────────────────────

@bp.route("/")
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
        selected_cars=cfg.get("CARS", "").split(";") if cfg.get("CARS") else [],
        local_ip=get_local_ip(),
        weather_presets=WEATHER_PRESETS,
    )


# ── Live API ──────────────────────────────────────────────────────────────────

@bp.route("/api/live")
@login_required
def api_live():
    ensure_udp()
    status = server_status()
    active = status == "active"

    cfg    = read_server_cfg()
    track  = cfg.get("TRACK", "")
    layout = cfg.get("TRACK_LAYOUT", "")
    raw    = load_spline_points(track, layout)
    spline_pts = [[x, y] for x, y in raw if not (math.isnan(x) or math.isnan(y))]

    drivers = []
    info    = None
    if active:
        info = server_info()
        js   = server_json()
        if js:
            for i, car in enumerate(js.get("Cars", js.get("cars", []))):
                if not car.get("IsConnected", car.get("connected", False)):
                    continue
                name = car.get("DriverName") or car.get("driver", {}).get("name", "")
                if not name:
                    continue
                udp = get_car_data(i)
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


@bp.route("/api/uptime")
@login_required
def api_uptime():
    return jsonify({"uptime": get_uptime_string()})


# ── Image endpoints ───────────────────────────────────────────────────────────

@bp.route("/car_img/<car>")
@login_required
def car_img(car):
    base = CARS_DIR / car
    for rel in ["ui/badge.png", "ui/car_small.png", "ui/car.png"]:
        p = base / rel
        if p.exists():
            return send_file(p, mimetype="image/png")
    return "", 404


@bp.route("/skin_img/<car>/<path:skin>")
@login_required
def skin_img(car, skin):
    base = CARS_DIR / car / "skins" / skin
    for fn in ["livery.png", "preview.png", "Skin.png"]:
        p = base / fn
        if p.exists():
            return send_file(p, mimetype="image/png")
    return "", 404


@bp.route("/track_img/<track>")
@bp.route("/track_img/<track>/<layout>")
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


@bp.route("/map")
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


# ── Car/Track info API ────────────────────────────────────────────────────────

@bp.route("/api/car_info/<car>")
@login_required
def api_car_info(car):
    ui    = get_car_ui(car)
    skins = get_car_skins(car)
    return jsonify({**ui, "skins": skins})


@bp.route("/api/car_skins/<car>")
@login_required
def api_car_skins(car):
    return jsonify({"skins": get_car_skins(car)})


@bp.route("/api/content_check/<kind>/<name>")
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
            "ui":            (base / "ui" / "ui_track.json").exists(),
            "data/surfaces": (base / "data" / "surfaces.ini").exists(),
        }
    else:
        return jsonify({"ok": False, "msg": "kind must be car or track"}), 400
    ok = all(checks.values())
    return jsonify({"ok": ok, "name": name, "checks": checks})


@bp.route("/api/track_info/<track>")
@bp.route("/api/track_info/<track>/<layout>")
@login_required
def api_track_info(track, layout=""):
    return jsonify(get_track_ui(track, layout))


# ── Server control ────────────────────────────────────────────────────────────

@bp.route("/control/<action>", methods=["POST"])
@login_required
@csrf_protect
@api_rate_limit(max_calls=10, window=60)
def control(action):
    if action not in ("start", "stop", "restart"):
        return jsonify({"ok": False, "msg": "Invalid action"}), 400
    ok, msg = run_systemctl(action)
    return jsonify({"ok": ok, "msg": msg, "status": server_status()})


@bp.route("/logs")
@login_required
def logs():
    try:
        r = subprocess.run(
            ["journalctl", "-u", SERVICE_NAME, "-n", "200", "--no-pager", "-o", "cat"],
            capture_output=True, text=True, timeout=10,
        )
        return jsonify({"logs": r.stdout})
    except Exception as e:
        return jsonify({"logs": f"Error: {e}"})
