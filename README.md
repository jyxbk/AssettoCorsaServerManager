# Assetto Corsa Server Manager

A modern, browser-based dashboard for managing an [AssettoServer](https://github.com/compujuckel/AssettoServer) dedicated server. Built with Python & Flask — no external dependencies beyond the standard stack.

<img width="1838" height="1017" alt="image" src="https://github.com/user-attachments/assets/297b6db8-92c4-4ceb-ac8b-e615fb15d55c" />

---

## Features

### Dashboard
- **Live server status** with uptime display
- **Quick stats bar** — laps today, active drivers today, best lap of the day
- **Real-time system stats** — CPU and RAM with progress bars
- **Live driver cards** — lap times, best/last/current lap, spline position
- **Live minimap** — driver positions on the track via UDP telemetry
- **Server chat mirror** — in-game chat visible in the browser
- **Chat broadcast** — send messages to all players directly from the dashboard
- **Track presets** — save and restore track/car configurations in one click

### Drivers & Lap Times
- **Live Drivers tab** — real-time positions on track map, kick/ban buttons
- **Live Times tab** — live leaderboard for the current session
- **Records tab** — persistent lap time history (survives server restarts)
  - Best laps leaderboard per driver & track
  - Full history with filter by driver, track, car
  - Pagination (50 entries per page)
  - CSV export with active filters applied
  - Driver statistics — total laps, clean laps %, best time, best car per track

### Server Control
- Start / Stop / Restart with one click
- Auto-restart option after saving any setting

### Settings
- **Track & Car selection** — browse with preview images, track length and pit box info
- **Session settings** — Practice, Qualify, Race duration, laps, wait time
- **Driving assists** — ABS, TC, ESP, auto clutch, tyre blankets, fuel/damage/tyre rates
- **Weather** — two weather slots with graphics preset and temperature settings
- **Dynamic track** — grip, randomness, session transfer, lap gain
- **Server general** — name, password, admin password, ports, sun angle, lobby registration
- **Server profile** — welcome message (shown in Content Manager) and server logo

### Content Manager
- **Card grid library** — all installed cars and tracks as visual cards with thumbnails
- **Active content marking** — currently configured car/track highlighted with green border
- **Detail modal** — per-car: skin gallery (lazy-loaded thumbnails), spec table, validation issues; per-track: layout list, validation issues
- **Disk usage bar** — cars / tracks size in MB, free disk space
- **Batch operations** — select multiple items and delete in one click
- **ZIP upload** — drag-and-drop, up to 2 GB, selective import
- **Folder upload** — drag a car or track folder directly into the browser
- **Auto-registration** — imported cars are automatically added to `server_cfg.ini` and `entry_list.ini`
- **Validation** — flags missing `ui_car.json`, missing `collider.kn5`, missing `surfaces.ini` etc.

### Entry List Editor
- **Per-slot editor** — every slot is its own card with individual model, skin, ballast, restrictor, driver name, and Steam GUID
- **Drag & drop** — reorder slots by dragging (native HTML5, no libraries)
- **Skin thumbnails** — live thumbnail preview next to every skin dropdown (lazy-loaded)
- **Quick-Add** — add N slots of a chosen car/skin combination in one step
- **Multi-edit** — select multiple slots via checkboxes and apply ballast/restrictor to all at once
- **Live validation** — duplicate GUID detection, missing car model warning, MAX_CLIENTS mismatch notice
- **INI import** — upload an existing `entry_list.ini`, parsed client-side, no page reload
- **INI export** — download the current server `entry_list.ini` directly
- **Presets** — save and load named entry list configurations (stored in `entry_list_presets.json`)

### Player Management
- **Whitelist** — add/remove Steam GUIDs
- **Admins** — manage admin GUID list
- **Ban list** — view and unban previously banned players
- **Kick / Ban** live players from the Drivers tab via RCON

### Advanced
- **extra_cfg.yml editor** — configure AssettoServer-specific settings directly:
  - Server details, AFK kick, max ping, forced lights
  - WeatherFX (CSP rain), Client Messages, Real Time
  - Minimum CSP version, security level, RCON port
  - Loading image URL
- **Discord Webhook** — crash/restart notifications, optional join/leave alerts, test button
- **Config backup** — download all config files as ZIP
- **Config restore** — upload a backup ZIP, applies config and restarts the server
- **Track Parameters editor** — set latitude, longitude and UTC timezone offset for Real Time mode

### Logs & RCON
- **Server log viewer** — live journal output with color-coded log levels
- **RCON console** — send commands with history of recent commands and responses

### Security
- **Session-based login** with rate limiting (max 10 attempts / IP / 5 min)
- **CSRF protection** on all state-changing endpoints (custom token, no external library)
- **API rate limiting** on all write-endpoints (per-IP, in-memory)
- **All credentials via environment variables** — nothing hardcoded, startup fails loudly if vars are missing
- **HTTPS** via nginx reverse proxy with TLS (self-signed or custom certificate)

### UX
- Dark theme with Assetto Corsa red accent
- EN / DE language toggle (persisted in localStorage)
- Mobile-responsive layout (breakpoints at 900px and 600px)

---

## Architecture

```
Browser
    │
    │  HTTPS :443  (nginx reverse proxy)
    ▼
┌────────────────────────────────────────────────┐
│              Flask Web App (app.py)             │
│                                                 │
│  routes/                   helpers/             │
│  ├── main.py               ├── auth.py          │
│  ├── settings.py           ├── config_io.py     │
│  ├── content_mgmt.py       ├── content.py       │
│  ├── entry_list.py         ├── system.py        │
│  ├── players.py            ├── laptimes.py      │
│  └── laptimes_routes.py    └── discord.py       │
│                                                 │
│  constants.py  ← all shared paths & env vars   │
│  templates/index.html  ← Jinja2 + vanilla JS   │
└────────────────────────────────────────────────┘
         │
         ├──► /opt/assettoserver/cfg/     (INI / YAML read-write)
         ├──► acserver.service            (systemctl)
         ├──► AssettoServer RCON :9700   (TCP)
         ├──► AssettoServer HTTP :8081   (status polling)
         ├──► AssettoServer UDP :12000   (Real-Time telemetry)
         ├──► systemd journal            (log tailing)
         ├──► /opt/acweb/laptimes.json   (lap time persistence)
         ├──► /opt/acweb/presets.json    (track/car presets)
         └──► Discord Webhook            (notifications)
```

### Lap Time Tracking

The lap tracker runs as a background thread that tails `journalctl` for the `acserver` service in real time. It parses three types of log lines:

- **Connect** — extracts driver name, Steam GUID, car model and skin
- **Disconnect** — clears the driver from the in-memory session
- **Lap completed** — saves driver, GUID, car, track, time, cuts and timestamp to `laptimes.json`

On startup, the last 5000 journal lines are parsed in `short-iso` format to import any laps that happened before acweb was running. Already-stored laps are deduplicated.

---

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | Python 3.9+, Flask (Blueprint architecture) |
| Auth | Session-based login, CSRF tokens, rate limiting |
| System info | psutil |
| Live telemetry | UDP listener (AC Real-Time protocol) |
| Server control | systemd via `systemctl` |
| Reverse proxy / TLS | nginx |
| Frontend | Vanilla JS, CSS Grid, Canvas API |
| Persistent data | JSON files (`laptimes.json`, `presets.json`, `discord.json`, `entry_list_presets.json`) |

---

## Requirements

- Linux server with `systemd`
- Python 3.9+
- AssettoServer installed at `/opt/assettoserver`
- nginx (for HTTPS)

---

## Installation

### 1. Clone and install

```bash
git clone https://github.com/jyxbk/AssettoCorsaServerManager.git /opt/acweb
cd /opt/acweb
python3 -m venv venv
source venv/bin/activate
pip install flask werkzeug psutil paramiko
```

### 2. Create environment file

```bash
cp .env.example .env
nano .env   # fill in your values
```

All three `ACWEB_*` variables are **required** — the app exits on startup if any are missing.

### 3. Run as a systemd service

```ini
# /etc/systemd/system/acweb.service
[Unit]
Description=Assetto Corsa Web Interface
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/acweb
EnvironmentFile=/opt/acweb/.env
ExecStart=/opt/acweb/venv/bin/python3 /opt/acweb/app.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
systemctl enable --now acweb
```

### 4. Set up HTTPS with nginx

```bash
apt install nginx openssl

# Generate self-signed certificate (valid 10 years)
mkdir -p /etc/nginx/ssl
openssl req -x509 -nodes -days 3650 -newkey rsa:2048 \
  -keyout /etc/nginx/ssl/acweb.key \
  -out /etc/nginx/ssl/acweb.crt \
  -subj '/CN=acweb/O=AC Server/C=DE'
```

```nginx
# /etc/nginx/sites-available/acweb
server {
    listen 80;
    return 301 https://$host$request_uri;
}
server {
    listen 443 ssl;
    ssl_certificate     /etc/nginx/ssl/acweb.crt;
    ssl_certificate_key /etc/nginx/ssl/acweb.key;
    ssl_protocols       TLSv1.2 TLSv1.3;
    client_max_body_size 2g;
    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_read_timeout 300;
    }
}
```

```bash
ln -s /etc/nginx/sites-available/acweb /etc/nginx/sites-enabled/acweb
systemctl enable --now nginx
```

The dashboard is now accessible at `https://<server-ip>`.

### 5. Configure paths (optional)

If your AssettoServer is not at `/opt/assettoserver`, edit the paths in `constants.py`:

```python
SERVER_DIR   = Path("/opt/assettoserver")
SERVICE_NAME = "acserver"
```

---

## Environment Variables

### Required (app won't start without these)

| Variable | Description |
|---|---|
| `ACWEB_SECRET` | Flask session secret key — generate with `python3 -c "import secrets; print(secrets.token_hex(32))"` |
| `ACWEB_USER` | Dashboard login username |
| `ACWEB_PASS` | Dashboard login password |

### Optional (for `deploy.py` only)

| Variable | Default | Description |
|---|---|---|
| `ACWEB_HOST` | — | SSH host to deploy to |
| `ACWEB_SSH_USER` | — | SSH username |
| `ACWEB_SSH_PASS` | — | SSH password |
| `ACWEB_SSH_PORT` | `22` | SSH port |
| `ACWEB_REMOTE_PATH` | `/opt/acweb` | Remote deploy path |

---

## File Layout

```
/opt/acweb/
├── app.py                      # Flask app, blueprint registration
├── constants.py                # Shared paths & env var validation
├── helpers/
│   ├── auth.py                 # login_required, csrf_protect, rate limiting
│   ├── config_io.py            # INI / YAML read-write helpers
│   ├── content.py              # Cars, tracks, ZIP, entry list helpers
│   ├── system.py               # systemctl, psutil, UDP, RCON, chat
│   ├── laptimes.py             # Lap tracker background thread
│   └── discord.py              # Discord webhook monitor thread
├── routes/
│   ├── main.py                 # Index, live API, images, login, logs
│   ├── settings.py             # /save_* endpoints
│   ├── content_mgmt.py         # Upload, import, delete, backup
│   ├── entry_list.py           # Entry List Editor API
│   ├── players.py              # Whitelist, admins, blacklist, kick/ban
│   └── laptimes_routes.py      # /api/laptimes/* endpoints
├── static/
│   ├── css/dashboard.css
│   └── js/
│       ├── dashboard.js
│       └── lang.js
├── templates/
│   ├── index.html              # Main dashboard (Jinja2 + vanilla JS)
│   ├── leaderboard.html        # Public leaderboard page
│   └── login.html
├── .env.example                # Template for environment variables
├── presets.json                # Saved track/car presets (auto-created)
├── laptimes.json               # Persistent lap time history (auto-created)
├── discord.json                # Discord webhook config (auto-created)
└── entry_list_presets.json     # Entry list presets (auto-created)

/opt/assettoserver/
├── cfg/
│   ├── server_cfg.ini
│   ├── entry_list.ini
│   ├── extra_cfg.yml
│   └── welcome.txt
├── content/
│   ├── cars/
│   └── tracks/
├── blacklist.txt
├── whitelist.txt
└── admins.txt
```

---

## Security Notes

- Generate a strong `ACWEB_SECRET`: `python3 -c "import secrets; print(secrets.token_hex(32))"`
- All state-changing endpoints are protected by CSRF tokens
- Write endpoints are rate-limited per IP
- The self-signed certificate will show a browser warning on first visit — click through once and it will be remembered
- Never commit `.env` or any file with credentials — it is listed in `.gitignore`

---

## License

MIT — free to use, modify, and distribute.
