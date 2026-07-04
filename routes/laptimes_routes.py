"""Lap-Times API Routes.

Bug fix #3: CSV-Export verwendet jetzt korrektes Quote-Escaping via csv-Modul.
"""
import csv
import io
import time

from flask import Blueprint, Response, jsonify, request

from helpers.auth import csrf_protect, login_required
from helpers.laptimes import clear_laptimes, load_laptimes, load_laptimes_filtered

bp = Blueprint("laptimes", __name__)


def _csv_safe(value) -> str:
    """Neutralisiert CSV-Formula-Injection (CWE-1236): Fahrername/Auto/Strecke
    kommen vom AC-Client und sind damit spielerkontrolliert. Beginnt ein Feld
    mit =, +, -, @, Tab oder CR, würde Excel/LibreOffice es beim Öffnen als
    Formel interpretieren statt als Text."""
    s = str(value)
    if s and s[0] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + s
    return s


# ── Lap times list ────────────────────────────────────────────────────────────

@bp.route("/api/laptimes")
@login_required
def api_laptimes():
    driver  = request.args.get("driver", "").strip().lower()
    track   = request.args.get("track",  "").strip().lower()
    car     = request.args.get("car",    "").strip().lower()
    q       = request.args.get("q",      "").strip().lower()
    from_dt = request.args.get("from",   "").strip()
    to_dt   = request.args.get("to",     "").strip()
    # Filterung + Sortierung vollständig in SQL (mit Indizes)
    entries = load_laptimes_filtered(driver=driver, track=track, car=car,
                                     q=q, from_dt=from_dt, to_dt=to_dt)
    return jsonify({"ok": True, "entries": entries, "total": len(entries)})


# ── Best lap per driver per track ─────────────────────────────────────────────

@bp.route("/api/laptimes/best")
@login_required
def api_laptimes_best():
    entries = load_laptimes()
    best = {}
    for e in entries:
        key = (e.get("driver", ""), e.get("track", ""))
        if key not in best or e.get("laptime", 99999999) < best[key].get("laptime", 99999999):
            best[key] = e
    result = sorted(best.values(), key=lambda e: (e.get("track", ""), e.get("laptime", 99999999)))
    return jsonify({"ok": True, "entries": result})


# ── Filter options ────────────────────────────────────────────────────────────

@bp.route("/api/laptimes/drivers")
@login_required
def api_laptimes_drivers():
    entries = load_laptimes()
    drivers = sorted({e.get("driver", "") for e in entries if e.get("driver")})
    tracks  = sorted({e.get("track",  "") for e in entries if e.get("track")})
    cars    = sorted({e.get("car",    "") for e in entries if e.get("car")})
    return jsonify({"drivers": drivers, "tracks": tracks, "cars": cars})


# ── Clear all ─────────────────────────────────────────────────────────────────

@bp.route("/api/laptimes", methods=["DELETE"])
@login_required
@csrf_protect
def api_laptimes_clear():
    clear_laptimes()
    return jsonify({"ok": True})


# ── CSV export ────────────────────────────────────────────────────────────────

@bp.route("/api/laptimes/export")
@login_required
def api_laptimes_export():
    """CSV-Export mit SQL-seitiger Filterung und sicherem Quote-Escaping (csv.writer)."""
    driver  = request.args.get("driver", "").strip().lower()
    track   = request.args.get("track",  "").strip().lower()
    car     = request.args.get("car",    "").strip().lower()
    q       = request.args.get("q",      "").strip().lower()
    from_dt = request.args.get("from",   "").strip()
    to_dt   = request.args.get("to",     "").strip()
    entries = load_laptimes_filtered(driver=driver, track=track, car=car,
                                     q=q, from_dt=from_dt, to_dt=to_dt)
    entries = sorted(entries, key=lambda x: x.get("ts", ""))

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["Datum", "Fahrer", "GUID", "Auto", "Strecke", "Rundenzeit", "Rundenzeit_ms", "Cuts"])
    for e in entries:
        ms   = e.get("laptime", 0)
        mins = ms // 60000
        secs = (ms % 60000) / 1000
        fmt  = f"{mins}:{secs:06.3f}"
        writer.writerow([
            e.get("ts", ""),
            _csv_safe(e.get("driver", "")),
            e.get("guid", ""),
            _csv_safe(e.get("car", "")),
            _csv_safe(e.get("track", "")),
            fmt,
            ms,
            e.get("cuts", 0),
        ])

    return Response(
        buf.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=laptimes.csv"},
    )


# ── Per-driver stats ──────────────────────────────────────────────────────────

@bp.route("/api/laptimes/stats")
@login_required
def api_laptimes_stats():
    entries = load_laptimes()
    drivers: dict = {}
    for e in entries:
        d = e.get("driver", "")
        if not d:
            continue
        s = drivers.setdefault(d, {
            "driver": d, "guid": e.get("guid", ""),
            "total_laps": 0, "clean_laps": 0,
            "best_overall": None, "tracks": {},
        })
        s["total_laps"] += 1
        if e.get("cuts", 0) == 0:
            s["clean_laps"] += 1
        lt = e.get("laptime", 0)
        if lt and (s["best_overall"] is None or lt < s["best_overall"]):
            s["best_overall"] = lt
        track_key = e.get("track", "unknown")
        t = s["tracks"].setdefault(track_key, {"laps": 0, "best": None, "car": ""})
        t["laps"] += 1
        if lt and (t["best"] is None or lt < t["best"]):
            t["best"] = lt
            t["car"]  = e.get("car", "")
    result = sorted(drivers.values(), key=lambda x: x["total_laps"], reverse=True)
    return jsonify({"ok": True, "stats": result})


# ── Today's quick stats ───────────────────────────────────────────────────────

@bp.route("/api/laptimes/today")
@login_required
def api_laptimes_today():
    today   = time.strftime("%Y-%m-%d")
    entries = [e for e in load_laptimes() if e.get("ts", "").startswith(today)]
    best    = min(entries, key=lambda e: e.get("laptime", 99999999), default=None)
    drivers = len({e.get("driver", "") for e in entries if e.get("driver")})
    return jsonify({
        "laps_today":    len(entries),
        "drivers_today": drivers,
        "best_today":    best,
    })
