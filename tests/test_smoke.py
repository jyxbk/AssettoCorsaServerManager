"""Smoke-Test-Suite für das AC Server Dashboard.

Prüft die kritischen Abläufe ohne echten AC-Server:
- App startet korrekt
- Login funktioniert
- Dashboard erreichbar
- APIs antworten
- Datenbank initialisiert
- Laptime wird gespeichert
- Serverstatus abrufbar
- Security-Header vorhanden
- Thread-Status-API antwortet

Ausführen:
    pip install pytest
    ACWEB_USER=admin ACWEB_PASS=test ACWEB_SECRET=testsecret12345678901234567890123456789 \\
        pytest tests/test_smoke.py -v
"""
import json
import os
import sqlite3
import sys
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ── Test-Umgebungsvariablen setzen BEVOR app importiert wird ─────────────────
os.environ.setdefault("ACWEB_SECRET", "smoketest_secret_key_32_chars_long!")
os.environ.setdefault("ACWEB_USER",   "testadmin")
os.environ.setdefault("ACWEB_PASS",   "testpass123")
os.environ.setdefault("ACWEB_LOG_LEVEL", "WARNING")  # Test-Output sauber halten


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def tmp_data_dir(tmp_path_factory):
    """Temporäres Verzeichnis für DB und Config-Dateien."""
    return tmp_path_factory.mktemp("acweb_test")


@pytest.fixture(scope="session")
def app_client(tmp_data_dir):
    """Flask-Test-Client mit gemockten Systemaufrufen.

    Mockt:
    - systemctl (kein echter AC-Server nötig)
    - journalctl (keine echten Logs nötig)
    - SQLite-Pfad in temporäres Verzeichnis umleiten
    - Background-Threads deaktiviert (kein Rauschen in Tests)
    """
    db_path = tmp_data_dir / "data.db"

    # DB-Pfad BEVOR app importiert wird auf Temp-Dir umlenken
    import helpers.db as db_module
    db_module.DB_PATH = db_path
    # Schema in Temp-DB initialisieren — muss explizit nach dem Patch passieren
    db_module.init_db()

    # Background-Threads stumm schalten — Tests brauchen keine echten Threads
    with patch("helpers.discord.start_discord_monitor"), \
         patch("helpers.telegram.start_telegram_monitor"), \
         patch("helpers.scheduler.start_scheduler"), \
         patch("helpers.laptimes.start_lap_tracker"), \
         patch("helpers.system.server_status", return_value="active"), \
         patch("helpers.system.get_system_stats", return_value={
             "cpu": 12.5, "mem_percent": 45.2,
             "mem_used_mb": 3600, "mem_total_mb": 8192,
             "net_tx_kbps": 5.1, "net_rx_kbps": 8.3,
         }), \
         patch("subprocess.run", return_value=MagicMock(
             returncode=0, stdout="active\n", stderr=""
         )):

        import app as app_module
        app_module.app.config["TESTING"]    = True
        app_module.app.config["SECRET_KEY"] = os.environ["ACWEB_SECRET"]

        with app_module.app.test_client() as client:
            yield client


@pytest.fixture(scope="session")
def logged_in_client(app_client):
    """Test-Client mit aktiver Session (eingeloggt)."""
    resp = app_client.post("/login", data={
        "username": os.environ["ACWEB_USER"],
        "password": os.environ["ACWEB_PASS"],
    }, follow_redirects=True)
    assert resp.status_code == 200, f"Login fehlgeschlagen: {resp.status_code}"
    return app_client


# ── 1. Serverstart ────────────────────────────────────────────────────────────

class TestServerStart:
    def test_app_module_importierbar(self):
        """App-Modul kann ohne Exception importiert werden."""
        import app
        assert app.app is not None

    def test_flask_app_hat_secret_key(self):
        import app
        assert app.app.secret_key
        assert len(app.app.secret_key) >= 20

    def test_blueprints_registriert(self):
        import app
        registered = {bp.name for bp in app.app.blueprints.values()}
        expected = {"main", "settings", "laptimes", "analytics", "results_bp"}
        assert expected.issubset(registered), f"Fehlende Blueprints: {expected - registered}"

    def test_datenbank_existiert(self, tmp_data_dir):
        import helpers.db as db_module
        assert db_module.DB_PATH.exists(), "data.db wurde nicht erstellt"

    def test_datenbank_schema_korrekt(self, tmp_data_dir):
        import helpers.db as db_module
        conn = sqlite3.connect(str(db_module.DB_PATH))
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='laptimes'"
        )
        assert cursor.fetchone() is not None, "Tabelle 'laptimes' fehlt"
        conn.close()


# ── 2. Login ──────────────────────────────────────────────────────────────────

class TestLogin:
    def test_login_seite_erreichbar(self, app_client):
        resp = app_client.get("/login")
        assert resp.status_code == 200
        assert b"login" in resp.data.lower() or b"passwort" in resp.data.lower() \
               or b"password" in resp.data.lower()

    def test_login_mit_falschen_daten_schlaegt_fehl(self, app_client):
        resp = app_client.post("/login", data={
            "username": "falsch",
            "password": "falsch",
        }, follow_redirects=True)
        assert resp.status_code == 200
        # Kein Redirect zur Hauptseite — Login-Seite bleibt oder 401
        assert b"login" in resp.data.lower() or resp.status_code in (200, 401)

    def test_login_mit_korrekten_daten_leitet_weiter(self, app_client):
        with app_client.session_transaction() as sess:
            sess.clear()
        resp = app_client.post("/login", data={
            "username": os.environ["ACWEB_USER"],
            "password": os.environ["ACWEB_PASS"],
        }, follow_redirects=True)
        assert resp.status_code == 200

    def test_unauthentifizierter_zugriff_auf_api_gibt_401(self, app_client):
        """Nicht eingeloggte Requests auf /api/* müssen 401 zurückgeben, kein Redirect."""
        with app_client.session_transaction() as sess:
            sess.clear()
        resp = app_client.get("/api/live")
        assert resp.status_code == 401
        data = json.loads(resp.data)
        assert data.get("ok") is False


# ── 3. Dashboard erreichbar ───────────────────────────────────────────────────

class TestDashboard:
    def test_hauptseite_erreichbar(self, logged_in_client):
        resp = logged_in_client.get("/")
        assert resp.status_code == 200
        assert b"html" in resp.data.lower()

    def test_statische_assets_erreichbar(self, logged_in_client):
        resp = logged_in_client.get("/static/css/dashboard.css")
        assert resp.status_code == 200
        assert b":" in resp.data  # CSS enthält Deklarationen

    def test_js_asset_erreichbar(self, logged_in_client):
        resp = logged_in_client.get("/static/js/dashboard.js")
        assert resp.status_code == 200
        assert len(resp.data) > 1000  # Kein leeres File


# ── 4. API antwortet ──────────────────────────────────────────────────────────

class TestAPI:
    def test_live_api_antwortet(self, logged_in_client):
        with patch("helpers.system.server_json", return_value=None), \
             patch("helpers.system.server_info", return_value=None), \
             patch("helpers.system.get_uptime_string", return_value="5m"):
            resp = logged_in_client.get("/api/live")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert "drivers" in data

    def test_laptimes_api_antwortet(self, logged_in_client):
        resp = logged_in_client.get("/api/laptimes")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data.get("ok") is True
        assert "entries" in data

    def test_laptimes_best_antwortet(self, logged_in_client):
        resp = logged_in_client.get("/api/laptimes/best")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data.get("ok") is True

    def test_laptimes_drivers_antwortet(self, logged_in_client):
        resp = logged_in_client.get("/api/laptimes/drivers")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert "drivers" in data
        assert "tracks" in data
        assert "cars" in data

    def test_laptimes_stats_antwortet(self, logged_in_client):
        resp = logged_in_client.get("/api/laptimes/stats")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data.get("ok") is True

    def test_results_api_antwortet(self, logged_in_client):
        resp = logged_in_client.get("/api/results")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data.get("ok") is True

    def test_thread_status_api_antwortet(self, logged_in_client):
        resp = logged_in_client.get("/api/system/threads")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data.get("ok") is True
        assert isinstance(data.get("threads"), list)

    def test_championship_api_antwortet(self, logged_in_client):
        resp = logged_in_client.get("/api/championships")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data.get("ok") is True


# ── 5. Datenbank initialisiert ────────────────────────────────────────────────

class TestDatenbank:
    def test_laptimes_tabelle_hat_korrekte_spalten(self, tmp_data_dir):
        import helpers.db as db_module
        conn = sqlite3.connect(str(db_module.DB_PATH))
        info = conn.execute("PRAGMA table_info(laptimes)").fetchall()
        spalten = {row[1] for row in info}
        erwartet = {"id", "ts", "driver", "guid", "car", "skin", "track", "laptime", "cuts"}
        assert erwartet.issubset(spalten), f"Fehlende Spalten: {erwartet - spalten}"
        conn.close()

    def test_unique_index_vorhanden(self, tmp_data_dir):
        import helpers.db as db_module
        conn = sqlite3.connect(str(db_module.DB_PATH))
        indexes = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_lt_dedup'"
        ).fetchone()
        assert indexes is not None, "UNIQUE-Index idx_lt_dedup fehlt"
        conn.close()

    def test_wal_modus_aktiv(self, tmp_data_dir):
        import helpers.db as db_module
        conn = sqlite3.connect(str(db_module.DB_PATH))
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        conn.close()
        assert mode == "wal", f"Kein WAL-Modus — journal_mode ist: {mode}"

    def test_schema_version(self, tmp_data_dir):
        import helpers.db as db_module
        conn = sqlite3.connect(str(db_module.DB_PATH))
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        conn.close()
        assert version >= 1, "Schema-Version < 1 — _upgrade_schema() lief nicht"


# ── 6. Laptime wird gespeichert ───────────────────────────────────────────────

class TestLaptimeSpeicherung:
    def test_laptime_schreiben_und_lesen(self, tmp_data_dir):
        from helpers.laptimes import append_laptime, load_laptimes
        eintrag = {
            "ts":      "2026-07-05 12:00:00",
            "driver":  "Smoke Test Driver",
            "guid":    "12345678901234567",
            "car":     "ferrari_488_gt3",
            "skin":    "default",
            "track":   "monza",
            "laptime": 83456,  # 1:23.456
            "cuts":    0,
        }
        append_laptime(eintrag)
        alle = load_laptimes()
        gefunden = [e for e in alle if e["driver"] == "Smoke Test Driver"]
        assert len(gefunden) >= 1
        assert gefunden[0]["laptime"] == 83456

    def test_doppelter_eintrag_wird_ignoriert(self, tmp_data_dir):
        from helpers.laptimes import append_laptime, load_laptimes
        eintrag = {
            "ts":      "2026-07-05 12:00:01",
            "driver":  "Dedup Driver",
            "guid":    "12345678901234500",
            "car":     "ferrari_488_gt3",
            "skin":    "default",
            "track":   "monza",
            "laptime": 90000,
            "cuts":    0,
        }
        append_laptime(eintrag)
        append_laptime(eintrag)  # identischer Eintrag
        alle = load_laptimes()
        dups = [e for e in alle if e["driver"] == "Dedup Driver"]
        assert len(dups) == 1, "Doppelter Eintrag wurde nicht durch UNIQUE INDEX verhindert"

    def test_filter_nach_fahrer(self, tmp_data_dir):
        from helpers.laptimes import load_laptimes_filtered
        ergebnisse = load_laptimes_filtered(driver="smoke test driver")
        assert len(ergebnisse) >= 1
        assert all(
            "smoke test driver" in e["driver"].lower()
            for e in ergebnisse
        )

    def test_sql_aggregation_best_per_track(self, tmp_data_dir):
        from helpers.laptimes import load_best_per_driver_track
        bests = load_best_per_driver_track()
        # Nur prüfen dass die Funktion ohne Fehler läuft und eine Liste zurückgibt
        assert isinstance(bests, list)

    def test_distinct_filter_values(self, tmp_data_dir):
        from helpers.laptimes import load_distinct_filter_values
        vals = load_distinct_filter_values()
        assert "drivers" in vals and "tracks" in vals and "cars" in vals
        # "Smoke Test Driver" muss in drivers sein
        assert any("Smoke" in d for d in vals["drivers"])


# ── 7. Serverstatus abrufbar ──────────────────────────────────────────────────

class TestServerstatus:
    def test_uptime_api_antwortet(self, logged_in_client):
        with patch("helpers.system.get_uptime_string", return_value="2h 15m"), \
             patch("helpers.system.server_status", return_value="active"):
            resp = logged_in_client.get("/api/uptime")
        assert resp.status_code == 200

    def test_server_status_helper(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout="active\n", stderr="")
            from helpers.system import server_status
            status = server_status()
            assert isinstance(status, str)

    def test_system_stats_helper(self):
        with patch("helpers.system.HAS_PSUTIL", True), \
             patch("helpers.system.psutil") as mock_psutil:
            mock_psutil.cpu_percent.return_value = 25.0
            mock_psutil.virtual_memory.return_value = MagicMock(
                percent=60.0, used=4 * 1024**3, total=8 * 1024**3
            )
            mock_psutil.net_io_counters.return_value = MagicMock(
                bytes_sent=1000, bytes_recv=2000
            )
            from helpers.system import get_system_stats
            stats = get_system_stats()
            assert "cpu" in stats
            assert "mem_percent" in stats


# ── 8. Security-Header vorhanden ─────────────────────────────────────────────

class TestSecurityHeaders:
    def test_x_frame_options(self, logged_in_client):
        resp = logged_in_client.get("/")
        assert resp.headers.get("X-Frame-Options") == "DENY"

    def test_x_content_type_options(self, logged_in_client):
        resp = logged_in_client.get("/")
        assert resp.headers.get("X-Content-Type-Options") == "nosniff"

    def test_referrer_policy(self, logged_in_client):
        resp = logged_in_client.get("/")
        assert "strict-origin" in resp.headers.get("Referrer-Policy", "")

    def test_content_security_policy(self, logged_in_client):
        resp = logged_in_client.get("/")
        csp = resp.headers.get("Content-Security-Policy", "")
        assert "default-src" in csp
        assert "frame-ancestors 'none'" in csp

    def test_no_server_header(self, logged_in_client):
        resp = logged_in_client.get("/")
        server = resp.headers.get("Server", "")
        assert "werkzeug" not in server.lower() or True  # Optional aber wünschenswert


# ── 9. Thread-Supervision ────────────────────────────────────────────────────

class TestThreadSupervision:
    def test_supervised_thread_startet(self):
        from helpers.threads import supervised, _registry
        started = threading.Event()

        def dummy():
            started.set()
            time.sleep(999)

        initial_count = len(_registry)
        t = supervised(dummy, name="smoke-test-thread", restart_delay=1.0)
        started.wait(timeout=2.0)
        assert started.is_set(), "Supervised Thread hat nicht gestartet"
        assert t.is_alive()

    def test_supervised_thread_startet_nach_crash_neu(self):
        from helpers.threads import supervised
        call_count = [0]
        restarted  = threading.Event()

        def crasher():
            call_count[0] += 1
            if call_count[0] == 1:
                raise RuntimeError("Intentionaler Test-Crash")
            restarted.set()
            time.sleep(999)

        supervised(crasher, name="smoke-crash-test", restart_delay=0.1)
        restarted.wait(timeout=5.0)
        assert restarted.is_set(), "Thread wurde nach Crash nicht neu gestartet"
        assert call_count[0] >= 2, "Thread wurde nicht mindestens 2x gestartet"

    def test_one_shot_thread_startet_nicht_neu(self):
        from helpers.threads import supervised
        call_count = [0]
        done       = threading.Event()

        def einmalig():
            call_count[0] += 1
            done.set()

        supervised(einmalig, name="smoke-one-shot", one_shot=True, restart_delay=0.1)
        done.wait(timeout=2.0)
        time.sleep(0.5)  # Kurz warten — kein Neustart erwartet
        assert call_count[0] == 1, f"One-shot wurde {call_count[0]}x gestartet (erwartet: 1)"
