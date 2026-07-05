#!/usr/bin/env python3
"""AC Server Dashboard — Einstiegspunkt.

Struktur:
  constants.py          ← gemeinsame Pfade & Konstanten
  helpers/
    auth.py             ← Rate-Limiting, login_required
    config_io.py        ← INI/YAML lesen & schreiben
    system.py           ← systemctl, psutil, UDP, RCON, Chat
    discord.py          ← Discord-Webhook-Monitor
    laptimes.py         ← Lap-Tracker, Journal-Parsing
    content.py          ← Cars/Tracks/ZIP/Presets/GUIDs
    threads.py          ← Supervised Thread-Management
  routes/
    main.py             ← Index, Live-API, Bilder, Login, Logs
    settings.py         ← /save_* Endpunkte
    content_mgmt.py     ← Upload, Import, Delete, Backup, Leaderboard
    players.py          ← Whitelist, Admins, Blacklist, Kick/Ban
    laptimes_routes.py  ← /api/laptimes/* Endpunkte
    entry_list.py       ← Entry List Editor API
"""
import logging
import logging.config
import os

from flask import Flask, jsonify

from constants import SECRET_KEY
from helpers.auth import get_csrf_token

# ── Logging-Konfiguration ─────────────────────────────────────────────────────
_LOG_LEVEL = os.environ.get("ACWEB_LOG_LEVEL", "INFO").upper()

logging.config.dictConfig({
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "standard": {
            "format": "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            "datefmt": "%Y-%m-%d %H:%M:%S",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "standard",
            "level": _LOG_LEVEL,
        },
    },
    "root": {
        "handlers": ["console"],
        "level": _LOG_LEVEL,
    },
    # Werkzeug-Access-Log auf WARNING reduzieren — Polling-Spam unterdrücken
    "loggers": {
        "werkzeug": {"level": "WARNING", "propagate": True},
    },
})

_logger = logging.getLogger(__name__)

# ── Blueprints ────────────────────────────────────────────────────────────────
from routes.main            import bp as main_bp
from routes.settings        import bp as settings_bp
from routes.content_mgmt    import bp as content_mgmt_bp
from routes.players         import bp as players_bp
from routes.laptimes_routes import bp as laptimes_bp
from routes.entry_list      import bp as entry_list_bp
from routes.results         import bp as results_bp
from routes.championship    import bp as championship_bp
from routes.scheduler       import bp as scheduler_bp
from routes.analytics       import bp as analytics_bp

# ── App ───────────────────────────────────────────────────────────────────────
app = Flask(__name__)
app.secret_key = SECRET_KEY
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024 * 1024  # 2 GB

@app.context_processor
def inject_csrf_token():
    return {"csrf_token": get_csrf_token}

app.register_blueprint(main_bp)
app.register_blueprint(settings_bp)
app.register_blueprint(content_mgmt_bp)
app.register_blueprint(players_bp)
app.register_blueprint(laptimes_bp)
app.register_blueprint(entry_list_bp)
app.register_blueprint(results_bp)
app.register_blueprint(championship_bp)
app.register_blueprint(scheduler_bp)
app.register_blueprint(analytics_bp)

# ── Security Headers ──────────────────────────────────────────────────────────
@app.after_request
def _security_headers(resp):
    resp.headers["X-Frame-Options"]        = "DENY"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-XSS-Protection"]       = "1; mode=block"
    resp.headers["Referrer-Policy"]        = "strict-origin-when-cross-origin"
    resp.headers["Permissions-Policy"]     = "geolocation=(), microphone=(), camera=()"
    resp.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; "
        "connect-src 'self'; "
        "font-src 'self'; "
        "frame-ancestors 'none';"
    )
    return resp

# ── Thread-Status-Endpunkt ────────────────────────────────────────────────────
from helpers.auth import login_required

@app.route("/api/system/threads")
@login_required
def api_thread_status():
    """Gibt den Live-Status aller supervisierten Background-Threads zurück."""
    from helpers.threads import thread_status
    return jsonify({"ok": True, "threads": thread_status()})

# ── Datenbank-Initialisierung (Schema + JSON-Migration) ──────────────────────
from helpers.db import init_db
_logger.info("Initialisiere Datenbank...")
init_db()

# ── Background-Threads (supervisiert) ────────────────────────────────────────
from helpers.discord   import start_discord_monitor
from helpers.laptimes  import start_lap_tracker
from helpers.telegram  import start_telegram_monitor
from helpers.scheduler import start_scheduler

_logger.info("Starte Background-Threads...")
start_discord_monitor()
start_telegram_monitor()
start_scheduler()
start_lap_tracker()

# Split-Config aus chat_notify.json beim Start laden
from helpers.laptimes import _load_chat_notify_config
from helpers.system   import set_split_config
_cn = _load_chat_notify_config()
if _cn.get("show_splits") and _cn.get("split_points"):
    set_split_config(_cn["split_points"])
    _logger.info("Split-Konfiguration geladen: %d Punkte", len(_cn["split_points"]))

_logger.info("AC Server Dashboard bereit.")

# ── Run ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=False)
