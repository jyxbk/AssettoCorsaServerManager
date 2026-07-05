"""Lap-Time-Tracker: journalctl-Parsing, persistente Speicherung, Discord-Notifications."""
import re
import subprocess
import threading
import time
import urllib.request

from constants import CHAT_NOTIFY_FILE, CUT_ACTIONS_FILE, SERVICE_NAME
from helpers.config_io import read_server_cfg
from helpers.discord import (
    _load_discord_config, discord_embed, discord_notify,
    embed_join, embed_leave, embed_pb, embed_record,
)

# ── Shared state ──────────────────────────────────────────────────────────────
_lt_lock        = threading.Lock()
_lt_session: dict   = {}   # driver_name → {guid, car, skin, track}
_personal_bests: dict = {}   # (driver, track) → best laptime_ms
_track_records: dict  = {}   # track → {laptime_ms, driver, car}
_cut_sessions: dict   = {}   # driver_name → total cuts in current session

# ── Regex patterns ────────────────────────────────────────────────────────────
_RE_CONNECT    = re.compile(
    r'\[[\d:]+ INF\] (.+?) \((\d{17}),\s*\d+ \(([^)]+)\)\) has connected'
)
_RE_LAP        = re.compile(
    r'\[(\d{2}:\d{2}:\d{2}) INF\] Lap completed by (.+?), (\d+) cuts?, laptime (\d+)'
)
_RE_DISCONNECT = re.compile(r'\[[\d:]+ INF\] (.+?) has disconnected')
_RE_ISO_DATE   = re.compile(r'^(\d{4}-\d{2}-\d{2})')
_RE_LOG_TIME   = re.compile(r'\[(\d{2}:\d{2}:\d{2})\s+INF\]')


# ── Persistent storage (SQLite) ───────────────────────────────────────────────

def load_laptimes() -> list:
    """Lädt alle Rundenzeiten aus SQLite, neueste zuerst."""
    from helpers.db import db_conn as _db
    with _db() as conn:
        rows = conn.execute(
            "SELECT ts, driver, guid, car, skin, track, laptime, cuts"
            " FROM laptimes ORDER BY ts DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def load_laptimes_filtered(
    driver: str = "", track: str = "", car: str = "",
    q: str = "", from_dt: str = "", to_dt: str = "",
) -> list:
    """SQL-seitige Filterung mit COLLATE NOCASE (kein LOWER() — behält Index-Kompatibilität)."""
    from helpers.db import db_conn as _db
    conds, params = [], []
    if driver:
        # LIKE … COLLATE NOCASE statt LOWER(col) LIKE → Index-freundlich
        conds.append("driver LIKE ? COLLATE NOCASE"); params.append(f"%{driver}%")
    if track:
        conds.append("track LIKE ? COLLATE NOCASE");  params.append(f"%{track}%")
    if car:
        conds.append("car LIKE ? COLLATE NOCASE");    params.append(f"%{car}%")
    if q:
        conds.append(
            "(driver LIKE ? COLLATE NOCASE"
            " OR car    LIKE ? COLLATE NOCASE"
            " OR track  LIKE ? COLLATE NOCASE)"
        )
        params.extend([f"%{q}%", f"%{q}%", f"%{q}%"])
    if from_dt:
        conds.append("SUBSTR(ts,1,10) >= ?"); params.append(from_dt)
    if to_dt:
        conds.append("SUBSTR(ts,1,10) <= ?"); params.append(to_dt)
    where = ("WHERE " + " AND ".join(conds)) if conds else ""
    sql = (
        f"SELECT ts, driver, guid, car, skin, track, laptime, cuts"
        f" FROM laptimes {where} ORDER BY laptime ASC"
    )
    with _db() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def append_laptime(entry: dict):
    """Fügt eine Rundenzeit ein; UNIQUE-Index auf (driver, laptime, track) verhindert Duplikate."""
    from helpers.db import db_conn as _db
    with _db() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO laptimes"
            " (ts, driver, guid, car, skin, track, laptime, cuts)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (
                entry.get("ts", ""), entry.get("driver", ""), entry.get("guid", ""),
                entry.get("car", ""), entry.get("skin", ""), entry.get("track", ""),
                entry.get("laptime", 0), entry.get("cuts", 0),
            ),
        )


def clear_laptimes():
    from helpers.db import db_conn as _db
    with _db() as conn:
        conn.execute("DELETE FROM laptimes")


def load_best_per_driver_track() -> list:
    """Beste Rundenzeit je (Fahrer, Strecke) — vollständig in SQL aggregiert."""
    from helpers.db import db_conn as _db
    sql = """
        SELECT l.ts, l.driver, l.guid, l.car, l.skin, l.track, l.laptime, l.cuts
        FROM laptimes l
        INNER JOIN (
            SELECT driver, track, MIN(laptime) AS min_lt
            FROM laptimes
            GROUP BY driver, track
        ) m ON l.driver = m.driver AND l.track = m.track AND l.laptime = m.min_lt
        ORDER BY l.track, l.laptime
    """
    with _db() as conn:
        rows = conn.execute(sql).fetchall()
    return [dict(r) for r in rows]


def load_distinct_filter_values() -> dict:
    """Gibt DISTINCT-Werte für driver/track/car aus SQL zurück — kein Python-Scan."""
    from helpers.db import db_conn as _db
    with _db() as conn:
        drivers = [r[0] for r in conn.execute(
            "SELECT DISTINCT driver FROM laptimes WHERE driver != '' ORDER BY driver COLLATE NOCASE"
        ).fetchall()]
        tracks = [r[0] for r in conn.execute(
            "SELECT DISTINCT track FROM laptimes WHERE track != '' ORDER BY track COLLATE NOCASE"
        ).fetchall()]
        cars = [r[0] for r in conn.execute(
            "SELECT DISTINCT car FROM laptimes WHERE car != '' ORDER BY car COLLATE NOCASE"
        ).fetchall()]
    return {"drivers": drivers, "tracks": tracks, "cars": cars}


def load_today_laptimes() -> list:
    """Heutige Rundenzeiten — date-Filterung in SQL via SUBSTR(ts,1,10)."""
    import time
    from helpers.db import db_conn as _db
    today = time.strftime("%Y-%m-%d")
    with _db() as conn:
        rows = conn.execute(
            "SELECT ts, driver, guid, car, skin, track, laptime, cuts"
            " FROM laptimes WHERE SUBSTR(ts,1,10) = ? ORDER BY laptime",
            (today,),
        ).fetchall()
    return [dict(r) for r in rows]


def load_driver_stats() -> list:
    """Fahrer-Statistiken vollständig in SQL aggregiert — kein Python-O(n)-Scan."""
    from helpers.db import db_conn as _db
    with _db() as conn:
        summary_rows = conn.execute("""
            SELECT driver, guid,
                   COUNT(*) AS total_laps,
                   SUM(CASE WHEN cuts = 0 THEN 1 ELSE 0 END) AS clean_laps,
                   MIN(laptime) AS best_overall
            FROM laptimes
            WHERE driver != ''
            GROUP BY driver
            ORDER BY total_laps DESC
        """).fetchall()
        track_rows = conn.execute("""
            SELECT driver, track, COUNT(*) AS laps,
                   MIN(laptime) AS best,
                   (SELECT car FROM laptimes i
                    WHERE i.driver = o.driver AND i.track = o.track
                      AND i.laptime = MIN(o.laptime) LIMIT 1) AS car
            FROM laptimes o
            WHERE driver != ''
            GROUP BY driver, track
        """).fetchall()

    by_driver: dict = {}
    for r in summary_rows:
        by_driver[r["driver"]] = {
            "driver": r["driver"], "guid": r["guid"],
            "total_laps": r["total_laps"], "clean_laps": r["clean_laps"],
            "best_overall": r["best_overall"], "tracks": {},
        }
    for r in track_rows:
        d = r["driver"]
        if d in by_driver:
            by_driver[d]["tracks"][r["track"]] = {
                "laps": r["laps"], "best": r["best"], "car": r["car"] or "",
            }
    return list(by_driver.values())


# ── Helpers ───────────────────────────────────────────────────────────────────

def split_car_skin(car_skin: str) -> tuple[str, str]:
    """Trennt 'car_model-skin_name' am ersten Bindestrich."""
    if "-" in car_skin:
        return car_skin.split("-", 1)
    return car_skin, ""


def _fmt_ms(ms: int) -> str:
    """Formatiert Millisekunden als m:ss.mmm."""
    ms = int(ms)
    minutes = ms // 60000
    seconds = (ms % 60000) // 1000
    millis  = ms % 1000
    return f"{minutes}:{seconds:02d}.{millis:03d}"


def _fmt_delta_ms(ms: int) -> str:
    """Formatiert Delta-Millisekunden als +/-s.mmm."""
    sign   = "+" if ms >= 0 else "-"
    total  = abs(ms)
    secs   = total // 1000
    millis = total % 1000
    return f"{sign}{secs}.{millis:03d}s"


def _load_chat_notify_config() -> dict:
    """Laedt die Chat-Notification-Konfiguration."""
    if CHAT_NOTIFY_FILE.exists():
        try:
            return json.loads(CHAT_NOTIFY_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_chat_notify_config(cfg: dict):
    """Speichert die Chat-Notification-Konfiguration."""
    CHAT_NOTIFY_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_cut_actions_config() -> dict:
    if CUT_ACTIONS_FILE.exists():
        try:
            return json.loads(CUT_ACTIONS_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_cut_actions_config(cfg: dict):
    CUT_ACTIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
    CUT_ACTIONS_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


# ── Journal-Parsing ───────────────────────────────────────────────────────────

def _parse_journal_block(lines: list, known_ts_set: set) -> list:
    """Parst eine Liste von Journal-Zeilen (short-iso Format) und gibt neue Lap-Einträge zurück."""
    driver_session: dict = {}   # lokale Session – umbenannt von 'session' (vermeidet Shadowing)
    new_entries = []
    cur_date = time.strftime("%Y-%m-%d")

    for line in lines:
        iso_m = _RE_ISO_DATE.match(line)
        if iso_m:
            cur_date = iso_m.group(1)
            colon_pos = line.find(": ")
            line = line[colon_pos + 2:].strip() if colon_pos != -1 else line

        line = line.strip()
        m = _RE_CONNECT.search(line)
        if m:
            name, guid, car_skin = m.group(1), m.group(2), m.group(3)
            car, skin = split_car_skin(car_skin)
            driver_session[name] = {"guid": guid, "car": car, "skin": skin}
            continue
        m = _RE_DISCONNECT.search(line)
        if m:
            driver_session.pop(m.group(1), None)
            continue
        m = _RE_LAP.search(line)
        if m:
            ts_str, name, cuts, laptime_ms = m.group(1), m.group(2), int(m.group(3)), int(m.group(4))
            ts_full = f"{cur_date} {ts_str}"
            key = f"{name}|{laptime_ms}|{ts_full}"
            if key in known_ts_set:
                continue
            info  = driver_session.get(name, {})
            cfg   = read_server_cfg()
            track = cfg.get("TRACK", "")
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


# ── Startup preload ───────────────────────────────────────────────────────────

def _preload_session_from_http():
    """Füllt _lt_session aus der AS HTTP API, falls acweb während einer Session neugestartet wurde."""
    try:
        with urllib.request.urlopen("http://127.0.0.1:8081/api/details", timeout=3) as r:
            import json as _json
            data = _json.loads(r.read())
        cfg     = read_server_cfg()
        track   = cfg.get("TRACK", "")
        layout  = cfg.get("TRACK_LAYOUT", "")
        track_str = f"{track}-{layout}" if layout else track
        for car in data.get("players", {}).get("Cars", []):
            if not car.get("IsConnected"):
                continue
            name  = car.get("DriverName", "")
            guid  = car.get("ID", "")
            model = car.get("Model", "")
            skin  = car.get("Skin", "")
            if name:
                with _lt_lock:
                    _lt_session[name] = {"guid": guid, "car": model, "skin": skin, "track": track_str}
    except Exception:
        pass


def _preload_personal_bests():
    """Fuellt _personal_bests und _track_records aus gespeicherten Lap-Eintraegen beim Start."""
    global _personal_bests, _track_records
    entries = load_laptimes()
    bests: dict   = {}
    records: dict = {}
    for e in entries:
        lt    = e.get("laptime", 0)
        if not lt or lt <= 0:
            continue
        pb_key = (e.get("driver", ""), e.get("track", ""))
        if pb_key not in bests or lt < bests[pb_key]:
            bests[pb_key] = lt
        track = e.get("track", "")
        if track and (track not in records or lt < records[track]["laptime_ms"]):
            records[track] = {"laptime_ms": lt, "driver": e.get("driver", ""), "car": e.get("car", "")}
    with _lt_lock:
        _personal_bests = bests
        _track_records  = records


def _preload_journal_history():
    """Importiert fehlende Runden aus den letzten 5000 Journal-Zeilen beim Start."""
    _preload_session_from_http()
    try:
        r = subprocess.run(
            ["journalctl", "-u", SERVICE_NAME, "-n", "5000", "--no-pager", "-o", "short-iso"],
            capture_output=True, text=True, timeout=15,
        )
        lines = r.stdout.splitlines()
    except Exception:
        return
    # Leeres known-Set: DB-UNIQUE-Index (driver, laptime, track) verhindert Duplikate via INSERT OR IGNORE
    new_entries = _parse_journal_block(lines, set())
    for entry in new_entries:
        append_laptime(entry)
    _preload_personal_bests()


# ── Live monitor ──────────────────────────────────────────────────────────────

def _split_sender():
    """Hintergrund-Thread: liest Split-Events aus der Queue und sendet sie per RCON."""
    from helpers.system import get_split_events, server_json as _sjson, rcon_send as _rcon
    while True:
        time.sleep(0.5)
        # Config ZUERST prüfen — get_split_events() leert die Queue sofort,
        # Ereignisse wären bei deaktivierter Config unwiederbringlich verloren.
        chat_cfg = _load_chat_notify_config()
        if not (chat_cfg.get("enabled") and chat_cfg.get("show_splits")):
            continue
        events = get_split_events()
        if not events:
            continue
        # Car-ID → Fahrername
        js = _sjson()
        car_names: dict = {}
        if js:
            for _i, _c in enumerate(js.get("Cars", [])):
                if _c.get("IsConnected") and _c.get("DriverName"):
                    car_names[_i] = _c["DriverName"]
        prefix = chat_cfg.get("prefix", ">> ")
        for evt in events:
            driver = car_names.get(evt["car_id"], "")
            if not driver:
                continue
            _rcon(f"/say {prefix}{driver} | {evt['name']}: {_fmt_ms(evt['ms'])}")


def _laptime_monitor():
    """Verfolgt journalctl live und persistiert neue Runden + Discord-Meldungen."""
    try:
        proc = subprocess.Popen(
            ["journalctl", "-u", SERVICE_NAME, "-f", "-n", "0", "--no-pager", "-o", "cat"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
        )
    except Exception:
        return

    for line in proc.stdout:
        line = line.strip()

        m = _RE_CONNECT.search(line)
        if m:
            name, guid, car_skin = m.group(1), m.group(2), m.group(3)
            car, skin = split_car_skin(car_skin)
            cfg   = read_server_cfg()
            track = cfg.get("TRACK", ""); layout = cfg.get("TRACK_LAYOUT", "")
            with _lt_lock:
                _lt_session[name] = {
                    "guid": guid, "car": car, "skin": skin,
                    "track": f"{track}-{layout}" if layout else track,
                }
                _cut_sessions[name] = 0  # Session-Cuts zurücksetzen
            dcfg = _load_discord_config()
            if dcfg.get("url") and dcfg.get("notify_join"):
                discord_embed(dcfg["url"], embed_join(name, car))
            try:
                from helpers.telegram import _load_telegram_config, escape_markdown_v2, telegram_notify as _tg_notify
                tcfg = _load_telegram_config()
                if tcfg.get("token") and tcfg.get("chat_id") and tcfg.get("notify_join"):
                    _tg_notify(tcfg["token"], tcfg["chat_id"],
                        f"🟢 *{escape_markdown_v2(name)}* connected \\({escape_markdown_v2(car)}\\)")
            except Exception:
                pass
            continue

        m = _RE_DISCONNECT.search(line)
        if m:
            left = m.group(1)
            with _lt_lock:
                _lt_session.pop(left, None)
                _cut_sessions.pop(left, None)
            dcfg = _load_discord_config()
            if dcfg.get("url") and dcfg.get("notify_join"):
                discord_embed(dcfg["url"], embed_leave(left))
            try:
                from helpers.telegram import _load_telegram_config, escape_markdown_v2, telegram_notify as _tg_notify
                tcfg = _load_telegram_config()
                if tcfg.get("token") and tcfg.get("chat_id") and tcfg.get("notify_join"):
                    _tg_notify(tcfg["token"], tcfg["chat_id"], f"🔴 *{escape_markdown_v2(left)}* disconnected")
            except Exception:
                pass
            continue

        m = _RE_LAP.search(line)
        if m:
            ts_str, name, cuts, laptime_ms = m.group(1), m.group(2), int(m.group(3)), int(m.group(4))
            with _lt_lock:
                info = dict(_lt_session.get(name, {}))
            cfg   = read_server_cfg()
            track = cfg.get("TRACK", ""); layout = cfg.get("TRACK_LAYOUT", "")
            track_str = f"{track}-{layout}" if layout else track
            entry = {
                "ts":      time.strftime("%Y-%m-%d ") + ts_str,
                "driver":  name,
                "guid":    info.get("guid", ""),
                "car":     info.get("car", ""),
                "skin":    info.get("skin", ""),
                "track":   track_str,
                "laptime": laptime_ms,
                "cuts":    cuts,
            }
            append_laptime(entry)

            # ── Discord: PB & Streckenrekord ─────────────────────────────────
            if laptime_ms > 10000:
                dcfg    = _load_discord_config()
                url     = dcfg.get("url", "")
                pb_key  = (name, track_str)
                with _lt_lock:
                    prev_pb = _personal_bests.get(pb_key)
                is_pb   = prev_pb is None or laptime_ms < prev_pb
                car_    = info.get("car", "")

                sent_record = False
                # Streckenrekord: NUR saubere Runden (cuts == 0)
                if cuts == 0:
                    with _lt_lock:
                        prev_rec = _track_records.get(track_str)
                    is_record = prev_rec is None or laptime_ms < prev_rec["laptime_ms"]
                    if is_record:
                        with _lt_lock:
                            _track_records[track_str] = {
                                "laptime_ms": laptime_ms, "driver": name, "car": car_,
                            }
                        if url and dcfg.get("notify_record", True):
                            prev_ms = prev_rec["laptime_ms"] if prev_rec else None
                            discord_embed(url, embed_record(name, car_, track_str, laptime_ms, prev_ms))
                            sent_record = True

                # PB: auch mit Cuts melden (aber nicht doppelt wenn Rekord gesendet)
                if is_pb and not sent_record:
                    if url and dcfg.get("notify_pb", False):
                        discord_embed(url, embed_pb(name, car_, track_str, laptime_ms, prev_pb, cuts))

                if is_pb:
                    with _lt_lock:
                        _personal_bests[pb_key] = laptime_ms

            # ── Cut-Actions ──────────────────────────────────────────────────
            if cuts > 0:
                cut_cfg = _load_cut_actions_config()
                if cut_cfg.get("enabled"):
                    with _lt_lock:
                        _cut_sessions[name] = _cut_sessions.get(name, 0) + cuts
                        session_cuts = _cut_sessions[name]

                    warn_per_lap = int(cut_cfg.get("warn_cuts_per_lap", 2) or 0)
                    kick_total   = int(cut_cfg.get("kick_session_cuts", 0) or 0)

                    if warn_per_lap > 0 and cuts >= warn_per_lap:
                        from helpers.system import rcon_send as _rcon
                        tmpl = cut_cfg.get("warn_message", "⚠️ {driver}: {cuts} Cuts!")
                        _rcon(f"/say {tmpl.replace('{driver}', name).replace('{cuts}', str(cuts))}")

                    if kick_total > 0 and session_cuts >= kick_total:
                        from helpers.system import rcon_send as _rcon, server_json as _sjson
                        js = _sjson()
                        car_id = None
                        if js:
                            for _i, _c in enumerate(js.get("Cars", [])):
                                if _c.get("DriverName") == name and _c.get("IsConnected"):
                                    car_id = _i
                                    break
                        if car_id is not None:
                            tmpl = cut_cfg.get("kick_message", "Kick: Zu viele Cuts ({cuts} gesamt)")
                            _rcon(f"/say {tmpl.replace('{cuts}', str(session_cuts))}")
                            _rcon(f"/kick_id {car_id}")

            # ── Chat-Notification ────────────────────────────────────────────
            chat_cfg = _load_chat_notify_config()
            if chat_cfg.get("enabled"):
                from helpers.system import rcon_send
                pb_key   = (name, track_str)
                with _lt_lock:
                    prev_best = _personal_bests.get(pb_key)
                is_pb    = (prev_best is None or laptime_ms < prev_best)

                prefix = chat_cfg.get("prefix", ">> ")
                msg    = f"{prefix}{name} | {_fmt_ms(laptime_ms)}"

                if is_pb:
                    msg += " | NEW PB!"
                    with _lt_lock:
                        _personal_bests[pb_key] = laptime_ms
                elif prev_best is not None and chat_cfg.get("show_delta", True):
                    msg += f" | {_fmt_delta_ms(laptime_ms - prev_best)}"

                if cuts > 0 and chat_cfg.get("show_cuts", True):
                    msg += f" | {cuts} cut{'s' if cuts != 1 else ''}"

                rcon_send(f"/say {msg}")


def start_lap_tracker():
    """Startet Preload + Live-Monitor + Split-Sender als Background-Threads."""
    threading.Thread(target=_preload_journal_history, daemon=True).start()
    threading.Thread(target=_laptime_monitor, daemon=True).start()
    threading.Thread(target=_split_sender, daemon=True).start()
