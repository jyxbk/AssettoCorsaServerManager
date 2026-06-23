# Assetto Corsa Server Manager

A modern, browser-based dashboard for managing an [AssettoServer](https://github.com/compujuckel/AssettoServer) dedicated server. Built with Python & Flask — no external dependencies beyond the standard stack.

<img width="1892" height="939" alt="image" src="https://github.com/user-attachments/assets/ece58cd5-0c0c-4958-b144-751eb83b29fb" />

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
- **Records tab** — persistent lap time history (survives server restarts and browser logouts)
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
- **Per-slot configuration** — skin, ballast, and restrictor per car model
- **Session settings** — Practice, Qualify, Race duration, laps, wait time
- **Driving assists** — ABS, TC, ESP, auto clutch, tyre blankets, fuel/damage/tyre rates
- **Weather** — two weather slots with graphics preset and temperature settings
- **Dynamic track** — grip, randomness, session transfer, lap gain
- **Server general** — name, password, admin password, ports, sun angle, lobby registration
- **Server profile** — welcome message (shown in Content Manager) and server logo

### Content Management
- **ZIP upload** — drag-and-drop, up to 2 GB, selective import
- **Folder upload** — drag a car or track folder directly into the browser
- **Installed content** — list all cars and tracks with one-click delete
- **Auto-registration** — imported cars are automatically added to `server_cfg.ini` and `entry_list.ini`

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
- **Credentials via environment variables** — `ACWEB_USER` / `ACWEB_PASS`
- **HTTPS** via nginx reverse proxy with TLS (self-signed or custom certificate)
- No credentials stored in JavaScript

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
┌──────────────────────────────────────┐
│          Flask Web App               │  ← app.py
│                                      │
│  ┌──────────┐   ┌──────────────────┐ │
│  │ REST API │   │  Jinja2 Template │ │  ← templates/index.html
│  └──────────┘   └──────────────────┘ │
│          │                           │
│  ┌───────▼───────────────────────┐   │
│  │        Backend Logic          │   │
│  │                               │   │
│  │ • INI read/write (cfg/)       │──►│ /opt/assettoserver/cfg/
│  │ • YAML read/write (extra_cfg) │──►│ extra_cfg.yml
│  │ • systemctl start/stop        │──►│ acserver.service
│  │ • RCON TCP :9700              │──►│ AssettoServer RCON
│  │ • HTTP polling :8081          │◄──│ AssettoServer HTTP API
│  │ • UDP telemetry :12000        │◄──│ AssettoServer UDP RT
│  │ • journalctl tail             │◄──│ systemd journal
│  │ • Lap time tracker thread     │──►│ /opt/acweb/laptimes.json
│  │ • Discord monitor thread      │──►│ Discord Webhook
│  │ • ZIP / folder import         │──►│ /opt/assettoserver/content/
│  └───────────────────────────────┘   │
└──────────────────────────────────────┘
```

### Lap Time Tracking

The lap tracker runs as a background thread that tails `journalctl` for the `acserver` service in real time. It parses three types of log lines:

- **Connect** — extracts driver name, Steam GUID, car model and skin
- **Disconnect** — clears the driver from the in-memory session
- **Lap completed** — saves driver, GUID, car, track, time, cuts and timestamp to `laptimes.json`

On startup, the last 5000 journal lines are parsed in `short-iso` format (which includes the actual date) to import any laps that happened before acweb was running. Already-stored laps are deduplicated. Players already connected when acweb restarts are pre-populated from the AssettoServer HTTP API so their next laps have correct metadata.

---

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | Python 3.9+, Flask |
| Auth | Session-based login, rate limiting |
| System info | psutil |
| Live telemetry | UDP listener (AC Real-Time protocol) |
| Server control | systemd via `systemctl` |
| Reverse proxy / TLS | nginx |
| Frontend | Vanilla JS, CSS Grid, Canvas API |
| Persistent data | JSON files (`laptimes.json`, `presets.json`, `discord.json`) |

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
pip install flask werkzeug psutil
```

### 2. Run as a systemd service

```ini
# /etc/systemd/system/acweb.service
[Unit]
Description=Assetto Corsa Web Interface
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/acweb
ExecStart=/opt/acweb/venv/bin/python3 /opt/acweb/app.py
Restart=on-failure
RestartSec=5
Environment=ACWEB_USER=admin
Environment=ACWEB_PASS=your-secure-password
Environment=ACWEB_SECRET=your-random-secret-key

[Install]
WantedBy=multi-user.target
```

```bash
systemctl enable --now acweb
```

### 3. Set up HTTPS with nginx

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

### 4. Configure paths (optional)

If your AssettoServer is not at `/opt/assettoserver`, edit the constants at the top of `app.py`:

```python
SERVER_DIR   = Path("/opt/assettoserver")
SERVICE_NAME = "acserver"
```

---

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `ACWEB_USER` | `admin` | Dashboard login username |
| `ACWEB_PASS` | `acserver` | Dashboard login password |
| `ACWEB_SECRET` | *(hardcoded)* | Flask session secret — **change this** |

---

## File Layout

```
/opt/acweb/
├── app.py                  # Entire backend
├── templates/
│   ├── index.html          # Entire frontend (Jinja2 + vanilla JS)
│   └── login.html          # Login page
├── presets.json            # Saved track/car presets
├── laptimes.json           # Persistent lap time history
└── discord.json            # Discord webhook config

/opt/assettoserver/
├── cfg/
│   ├── server_cfg.ini      # Main server configuration
│   ├── entry_list.ini      # Car slot configuration
│   ├── extra_cfg.yml       # AssettoServer-specific config
│   └── welcome.txt         # Server description (shown in CM)
├── content/
│   ├── cars/
│   └── tracks/
├── blacklist.txt
├── whitelist.txt
└── admins.txt
```

---

## Security Notes

- Change `ACWEB_PASS` and `ACWEB_SECRET` before exposing to any network
- The self-signed certificate will show a browser warning on first visit — click through once and it will be remembered
- The `ADMIN_PASSWORD` in `server_cfg.ini` is used automatically for RCON (kick/ban/chat)
- Never commit `sever_infos.txt` or any file with credentials — it is listed in `.gitignore`

---

## License

MIT — free to use, modify, and distribute.
