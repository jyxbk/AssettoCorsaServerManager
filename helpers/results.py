"""Race Results Parser: liest AC-Server Results-JSON-Dateien aus SERVER_DIR/results/."""
import json
import re

from constants import RESULTS_DIR


def _fmt_ms(ms: int) -> str:
    if not ms:
        return "—"
    mins = ms // 60000
    secs = (ms % 60000) / 1000
    return f"{mins}:{secs:06.3f}"


def _fmt_total(ms: int) -> str:
    if not ms:
        return "—"
    h = ms // 3600000
    m = (ms % 3600000) // 60000
    s = (ms % 60000) / 1000
    return f"{h}:{m:02d}:{s:06.3f}" if h else f"{m}:{s:06.3f}"


def list_results(limit: int = 100) -> list:
    """Gibt die neuesten Result-Dateien sortiert (neueste zuerst) zurück."""
    if not RESULTS_DIR.exists():
        return []
    files = sorted(RESULTS_DIR.glob("*.json"), key=lambda f: f.stat().st_mtime, reverse=True)
    out = []
    for f in files[:limit]:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            result_list = data.get("Result", [])
            laps        = data.get("Laps", [])
            winner      = result_list[0].get("DriverName", "?") if result_list else "?"
            driver_count = len({l.get("DriverName", "") for l in laps} | {r.get("DriverName", "") for r in result_list})
            out.append({
                "filename":     f.name,
                "type":         data.get("Type", "PRACTICE"),
                "track":        data.get("TrackName", ""),
                "config":       data.get("TrackConfig", ""),
                "date":         data.get("Date", ""),
                "lap_count":    len(laps),
                "driver_count": driver_count,
                "winner":       winner,
            })
        except Exception:
            pass
    return out


def get_result(filename: str) -> dict | None:
    """Parst eine einzelne Result-Datei vollständig."""
    if not re.match(r'^[\w\-\.]+\.json$', filename):
        return None
    path = RESULTS_DIR / filename
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None

    # Ergebnis-Einträge anreichern
    result_list = data.get("Result", [])
    for i, r in enumerate(result_list):
        r["position"]  = i + 1
        r["best_fmt"]  = _fmt_ms(r.get("BestLap", 0))
        r["total_fmt"] = _fmt_total(r.get("TotalTime", 0))

    # Gap zum Leader berechnen
    if len(result_list) > 1:
        leader_total = result_list[0].get("TotalTime", 0)
        for r in result_list:
            if r["position"] == 1:
                r["gap_fmt"] = "—"
            elif leader_total and r.get("TotalTime"):
                diff = r["TotalTime"] - leader_total
                r["gap_fmt"] = f"+{_fmt_total(diff)}"
            else:
                r["gap_fmt"] = "—"
    else:
        for r in result_list:
            r["gap_fmt"] = "—"

    # Lap-Zeiten pro Fahrer für Mini-Chart (schnellste 20 Runden je Fahrer)
    laps = data.get("Laps", [])
    driver_bests: dict = {}
    for lap in laps:
        name = lap.get("DriverName", "")
        lt   = lap.get("LapTime", 0)
        if lt and lt > 10000:
            if name not in driver_bests or lt < driver_bests[name]:
                driver_bests[name] = lt

    # Sektorzeiten nur wenn vorhanden
    has_sectors = any(lap.get("Sectors") for lap in laps)

    return {
        "filename":     filename,
        "type":         data.get("Type", "PRACTICE"),
        "track":        data.get("TrackName", ""),
        "config":       data.get("TrackConfig", ""),
        "date":         data.get("Date", ""),
        "duration":     data.get("DurationSecs", 0),
        "race_laps":    data.get("RaceLaps", 0),
        "result":       result_list,
        "laps":         laps,
        "driver_bests": driver_bests,
        "has_sectors":  has_sectors,
        "collision_count": len([e for e in data.get("Events", []) if "COLLISION" in e.get("Type", "")]),
    }
