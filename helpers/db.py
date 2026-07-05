"""SQLite-Datenbank-Layer: Schema, Verbindung, Migration von JSON."""
import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

from constants import LAPTIMES_FILE

DB_PATH  = Path(str(LAPTIMES_FILE.parent / "data.db"))
_db_lock = threading.Lock()


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


@contextmanager
def db_conn():
    """Kontext-Manager: serialisiert Schreibzugriffe, committet oder rollbackt."""
    with _db_lock:
        conn = _get_conn()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


def init_db():
    """Erstellt Schema, führt Schema-Upgrades durch und migriert vorhandene laptimes.json."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _db_lock:
        conn = _get_conn()
        try:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS laptimes (
                    id      INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts      TEXT    NOT NULL DEFAULT '',
                    driver  TEXT    NOT NULL DEFAULT '',
                    guid    TEXT             DEFAULT '',
                    car     TEXT             DEFAULT '',
                    skin    TEXT             DEFAULT '',
                    track   TEXT             DEFAULT '',
                    laptime INTEGER NOT NULL DEFAULT 0,
                    cuts    INTEGER          DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS idx_lt_driver  ON laptimes(driver COLLATE NOCASE);
                CREATE INDEX IF NOT EXISTS idx_lt_track   ON laptimes(track  COLLATE NOCASE);
                CREATE INDEX IF NOT EXISTS idx_lt_ts      ON laptimes(ts);
                CREATE INDEX IF NOT EXISTS idx_lt_laptime ON laptimes(laptime);
                CREATE INDEX IF NOT EXISTS idx_lt_ts_date ON laptimes(SUBSTR(ts,1,10));
            """)
            conn.commit()
            _upgrade_schema(conn)
            _migrate_json(conn)
        finally:
            conn.close()


def _upgrade_schema(conn: sqlite3.Connection):
    """Versionierte Schema-Upgrades — läuft jede einmalig via PRAGMA user_version."""
    version = conn.execute("PRAGMA user_version").fetchone()[0]

    if version < 1:
        # Sprint 8: UNIQUE INDEX korrigiert — (driver, laptime, track) erlaubte keine
        # identischen Zeiten zu unterschiedlichen Zeitstempeln (z.B. Gleichstände).
        # Neuer Index: (driver, track, laptime, ts) — ts unterscheidet legitime Wiederholungen.
        conn.execute("DROP INDEX IF EXISTS idx_lt_dedup")
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_lt_dedup"
            " ON laptimes(driver, track, laptime, ts)"
        )
        conn.execute("PRAGMA user_version = 1")
        conn.commit()


def _migrate_json(conn: sqlite3.Connection):
    """Einmalige Migration: laptimes.json → SQLite (nur wenn Tabelle leer)."""
    if not LAPTIMES_FILE.exists():
        return
    count = conn.execute("SELECT COUNT(*) FROM laptimes").fetchone()[0]
    if count > 0:
        return
    try:
        entries = json.loads(LAPTIMES_FILE.read_text(encoding="utf-8"))
        conn.executemany(
            "INSERT OR IGNORE INTO laptimes"
            " (ts, driver, guid, car, skin, track, laptime, cuts)"
            " VALUES (?,?,?,?,?,?,?,?)",
            [
                (
                    e.get("ts", ""), e.get("driver", ""), e.get("guid", ""),
                    e.get("car", ""), e.get("skin", ""), e.get("track", ""),
                    e.get("laptime", 0), e.get("cuts", 0),
                )
                for e in entries
                if e.get("driver") and e.get("laptime", 0) > 0
            ],
        )
        conn.commit()
        LAPTIMES_FILE.rename(str(LAPTIMES_FILE) + ".migrated")
        print(f"[db] Migration abgeschlossen: {len(entries)} Einträge importiert.")
    except Exception as exc:
        print(f"[db] JSON-Migration fehlgeschlagen: {exc}")
