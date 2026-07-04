"""Fahrer-Analytics: Ø-Geschwindigkeit, Konsistenz & Safety-Score aus den vorhandenen Lap-Daten."""
import re
import statistics
from collections import defaultdict

from helpers.content import get_track_ui
from helpers.laptimes import load_laptimes

_RE_LEN = re.compile(r'([\d.,]+)\s*(km|m)?', re.IGNORECASE)


def _parse_length_km(raw) -> float | None:
    if not raw:
        return None
    s = str(raw).strip().lower().replace(",", ".")
    m = _RE_LEN.match(s)
    if not m or not m.group(1):
        return None
    try:
        val = float(m.group(1))
    except ValueError:
        return None
    unit = m.group(2)
    if unit == "km":
        return val
    if unit == "m" or val > 50:  # ui_track.json "length" ist meist Meter ohne Einheit
        return val / 1000.0
    return val


def track_length_km(track_key: str) -> float | None:
    """Löst den gespeicherten 'track' bzw. 'track-layout' Key zur Streckenlänge (km) auf."""
    if not track_key:
        return None
    length = _parse_length_km(get_track_ui(track_key).get("length"))
    if length:
        return length
    if "-" in track_key:
        track, layout = track_key.split("-", 1)
        length = _parse_length_km(get_track_ui(track, layout).get("length"))
        if length:
            return length
    return None


def avg_speed_kmh(track_key: str, laptime_ms) -> float | None:
    if not laptime_ms or laptime_ms <= 0:
        return None
    length_km = track_length_km(track_key)
    if not length_km:
        return None
    hours = laptime_ms / 3_600_000
    return round(length_km / hours, 1)


def _consistency_pct(laptimes_ms: list) -> float | None:
    """Wie stabil die letzten sauberen Runden zueinander sind (0-100, höher = konstanter)."""
    clean = [lt for lt in laptimes_ms if lt and lt > 0]
    if len(clean) < 2:
        return None
    mean = statistics.mean(clean)
    if mean <= 0:
        return None
    cv = statistics.pstdev(clean) / mean
    return round(max(0.0, min(100.0, 100.0 * (1 - cv * 5))), 1)


def _safety_score(total_laps: int, clean_laps: int) -> float | None:
    if not total_laps:
        return None
    return round(100.0 * clean_laps / total_laps, 1)


def driver_profile(driver: str) -> dict | None:
    """Detailliertes Profil für einen einzelnen Fahrer: letzte Runden, Pro-Strecke-Stats, Scores."""
    entries = [e for e in load_laptimes() if e.get("driver") == driver]
    if not entries:
        return None
    entries.sort(key=lambda e: e.get("ts", ""))

    total_laps = len(entries)
    clean      = [e for e in entries if e.get("cuts", 0) == 0 and e.get("laptime")]
    best_overall = min((e["laptime"] for e in entries if e.get("laptime")), default=None)

    recent_laps = []
    for e in entries[-20:][::-1]:
        recent_laps.append({
            "ts":      e.get("ts", ""),
            "track":   e.get("track", ""),
            "car":     e.get("car", ""),
            "laptime": e.get("laptime", 0),
            "cuts":    e.get("cuts", 0),
            "avg_speed_kmh": avg_speed_kmh(e.get("track", ""), e.get("laptime", 0)),
        })

    consistency = _consistency_pct([e["laptime"] for e in clean[-20:]])

    tracks: dict = {}
    for e in entries:
        tk = e.get("track") or "unknown"
        t  = tracks.setdefault(tk, {"laps": 0, "clean_times": [], "best": None, "car": ""})
        t["laps"] += 1
        lt = e.get("laptime", 0)
        if e.get("cuts", 0) == 0 and lt:
            t["clean_times"].append(lt)
        if lt and (t["best"] is None or lt < t["best"]):
            t["best"] = lt
            t["car"]  = e.get("car", "")

    track_stats = []
    for tk, t in tracks.items():
        avg_clean_ms = round(statistics.mean(t["clean_times"])) if t["clean_times"] else None
        track_stats.append({
            "track":         tk,
            "laps":          t["laps"],
            "clean_laps":    len(t["clean_times"]),
            "best":          t["best"],
            "car":           t["car"],
            "avg_speed_kmh": avg_speed_kmh(tk, avg_clean_ms or t["best"]),
        })
    track_stats.sort(key=lambda x: x["laps"], reverse=True)

    return {
        "driver":       driver,
        "total_laps":   total_laps,
        "clean_laps":   len(clean),
        "safety_score": _safety_score(total_laps, len(clean)),
        "consistency":  consistency,
        "best_overall": best_overall,
        "recent_laps":  recent_laps,
        "tracks":       track_stats,
    }


def all_drivers_summary() -> list:
    """Kurzübersicht aller Fahrer für den Vergleich (Safety-Score, Konsistenz, Bestzeit)."""
    by_driver: dict = defaultdict(list)
    for e in load_laptimes():
        d = e.get("driver", "")
        if d:
            by_driver[d].append(e)

    out = []
    for driver, entries in by_driver.items():
        entries.sort(key=lambda e: e.get("ts", ""))
        total = len(entries)
        clean = [e for e in entries if e.get("cuts", 0) == 0 and e.get("laptime")]
        best  = min((e["laptime"] for e in entries if e.get("laptime")), default=None)
        out.append({
            "driver":       driver,
            "total_laps":   total,
            "safety_score": _safety_score(total, len(clean)),
            "consistency":  _consistency_pct([e["laptime"] for e in clean[-20:]]),
            "best_overall": best,
        })
    out.sort(key=lambda x: x["total_laps"], reverse=True)
    return out
