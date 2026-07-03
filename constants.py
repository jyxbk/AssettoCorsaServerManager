#!/usr/bin/env python3
"""Shared constants and path configuration for AC Server Dashboard."""
import os
import sys
from pathlib import Path

# ── Service ───────────────────────────────────────────────────────────────────
SERVICE_NAME = "acserver"
RCON_PORT    = 9700

# ── Auth ──────────────────────────────────────────────────────────────────────
def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        print(f"[FATAL] Umgebungsvariable {name} ist nicht gesetzt. "
              f"Bitte .env befüllen und neu starten.", file=sys.stderr)
        sys.exit(1)
    return value

SECRET_KEY = _require_env("ACWEB_SECRET")
ACWEB_USER = _require_env("ACWEB_USER")
ACWEB_PASS = _require_env("ACWEB_PASS")

ACWEB_PORT = int(os.environ.get("ACWEB_PORT", "8080"))

# ── Paths ─────────────────────────────────────────────────────────────────────
SERVER_DIR    = Path("/opt/assettoserver")
CONTENT_DIR   = SERVER_DIR / "content"
CARS_DIR      = CONTENT_DIR / "cars"
TRACKS_DIR    = CONTENT_DIR / "tracks"
CFG_DIR       = SERVER_DIR / "cfg"

PRESETS_FILE      = Path("/opt/acweb/presets.json")
DISCORD_FILE      = Path("/opt/acweb/discord.json")
LAPTIMES_FILE     = Path("/opt/acweb/laptimes.json")
CHAT_NOTIFY_FILE  = Path("/opt/acweb/chat_notify.json")
TELEGRAM_FILE        = Path("/opt/acweb/telegram.json")
CUT_ACTIONS_FILE     = Path("/opt/acweb/cut_actions.json")
CHAMPIONSHIPS_FILE   = Path("/opt/acweb/championships.json")
SCHEDULED_EVENTS_FILE = Path("/opt/acweb/scheduled_events.json")
WELCOME_FILE   = CFG_DIR / "welcome.txt"
EXTRA_CFG_FILE = CFG_DIR / "extra_cfg.yml"
LOGO_FILE      = SERVER_DIR / "logo.png"
WHITELIST_FILE = SERVER_DIR / "whitelist.txt"
ADMINS_FILE    = SERVER_DIR / "admins.txt"
BLACKLIST_FILE = SERVER_DIR / "blacklist.txt"
TRACK_PARAMS_FILE = SERVER_DIR / "data" / "data_track_params.ini"

RESULTS_DIR = SERVER_DIR / "results"
UPLOAD_TMP  = Path("/tmp/acweb_uploads")
UPLOAD_TMP.mkdir(exist_ok=True)

# ── Weather presets ───────────────────────────────────────────────────────────
WEATHER_PRESETS = [
    "1_heavy_clouds", "2_light_clouds", "3_clear", "4_mid_clear",
    "5_light_clouds", "6_light_clouds", "7_heavy_clouds", "8_drizzle",
    "9_light_drizzle", "10_drizzle_race", "11_practice_storm",
]

# ── extra_cfg.yml supported keys ──────────────────────────────────────────────
EXTRA_CFG_KEYS = [
    "EnableServerDetails", "ServerDescription", "LoadingImageUrl",
    "EnableAntiAfk", "MaxAfkTimeMinutes", "MaxPing", "ForceLights",
    "EnableWeatherFx", "MinimumCSPVersion", "EnableClientMessages",
    "EnableRealTime", "MandatoryClientSecurityLevel", "RconPort",
    "UDPPluginAddress", "UDPPluginLocalPort",
]
