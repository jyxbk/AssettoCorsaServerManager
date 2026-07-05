"""Lap-Times API Routes.

Bug fix #3: CSV-Export verwendet jetzt korrektes Quote-Escaping via csv-Modul.
"""
import csv
import io

from flask import Blueprint, Response, jsonify, request

from helpers.auth import csrf_protect, login_required
from helpers.laptimes import (
    clear_laptimes, load_laptimes, load_laptimes_filtered,
    load_best_per_driver_track, load_distinct_filter_values,
    load_today_laptimes, load_driver_stats,
)

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
    return jsonify({"ok": True, "entries": load_best_per_driver_track()})


# ── Filter options ────────────────────────────────────────────────────────────

@bp.route("/api/laptimes/drivers")
@login_required
def api_laptimes_drivers():
    return jsonify(load_distinct_filter_values())


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
        ms      = int(e.get("laptime", 0))
        mins    = ms // 60000
        secs    = (ms % 60000) // 1000
        ms_part = ms % 1000
        fmt     = f"{mins}:{secs:02d}.{ms_part:03d}"
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
    return jsonify({"ok": True, "stats": load_driver_stats()})


# ── Today's quick stats ───────────────────────────────────────────────────────

@bp.route("/api/laptimes/today")
@login_required
def api_laptimes_today():
    entries = load_today_laptimes()
    best    = min(entries, key=lambda e: e.get("laptime", 99999999), default=None)
    drivers = len({e.get("driver", "") for e in entries if e.get("driver")})
    return jsonify({
        "laps_today":    len(entries),
        "drivers_today": drivers,
        "best_today":    best,
    })
