# Assetto Corsa Server Manager

A modern, browser-based dashboard for managing an [AssettoServer](https://github.com/compujuckel/AssettoServer) dedicated server. Built with Python & Flask — no external tools or extra software required.

<img width="1892" height="939" alt="image" src="https://github.com/user-attachments/assets/ece58cd5-0c0c-4958-b144-751eb83b29fb" />

---

## Features

### Dashboard & Monitoring
- **Live server status** — running / stopped indicator with auto-refresh
- **Real-time system stats** — CPU and RAM usage with visual progress bars
- **Live driver list** — connected players with current lap time, best lap, last lap, and lap count
- **Live minimap** — driver positions rendered on the track spline in real time (via UDP telemetry)
- **Server chat mirror** — full in-game chat streamed to the web interface
- **Server log viewer** — live journal output from the `acserver` systemd service

### Server Control
- Start / Stop / Restart the server with one click
- Displays current server name, track, connected players, and HTTP port

### Configuration
- **Track & Car selection** — browse all installed tracks (including layouts) and cars with preview images and badges
- **Session settings** — configure Practice, Qualifying, and Race duration, laps, wait time, and open/closed lobby
- **Driving assists** — ABS, TC, stability control, auto-clutch, tyre blankets, fuel rate, damage, tyre wear, and more
- **Weather** — two weather slots with graphics preset, ambient/road temperature, and variation
- **Dynamic track** — grip level, randomness, session transfer, and lap gain
- **Server settings** — server name, password, admin password, ports, sun angle, and lobby registration

### Content Management
- **ZIP upload** — drag-and-drop upload of car/track ZIP packages (up to 2 GB)
- **Selective import** — preview contents of a ZIP and choose which cars/tracks to extract
- **Preset system** — save current track/car configuration as a named preset and restore it in one click (also restarts the server automatically)

### Player Management
- **Kick** players by car ID via RCON
- **Ban** players by GUID — adds to `blacklist.txt` and kicks immediately

### UX
- Dark theme with Assetto Corsa red accent
- Tab-based layout: Dashboard · Drivers · Track/Cars · Sessions · Weather · Assists · Server · Upload · Presets
- Responsive grid — works on tablets and wide monitors
- Language toggle (EN / DE)

---

## How It Works

This project is a self-hosted web application that acts as a full control panel for an Assetto Corsa dedicated server running on Linux. There are no cloud services, no third-party dashboards — everything runs locally on the same machine as the game server.

### Architecture Overview

```
Browser (you)
    │
    │  HTTP on port 8080
    ▼
┌─────────────────────────────────┐
│         Flask Web App           │  ← app.py
│                                 │
│  ┌──────────┐  ┌─────────────┐  │
│  │ REST API │  │  HTML/JS UI │  │  ← index.html (served as template)
│  └──────────┘  └─────────────┘  │
│         │                       │
│  ┌──────▼──────────────────┐    │
│  │    Backend Logic        │    │
│  │                         │    │
│  │ • systemctl commands    │────┼──► acserver (systemd service)
│  │ • INI file read/write   │────┼──► /opt/assettoserver/cfg/
│  │ • RCON TCP connection   │────┼──► AssettoServer :9700
│  │ • HTTP polling          │────┼──► AssettoServer :8081/JSON|
│  │ • journalctl log reader │────┼──► systemd journal
│  │ • UDP telemetry thread  │◄───┼─── AssettoServer :12000 (UDP)
│  │ • ZIP import engine     │────┼──► /opt/assettoserver/content/
│  │ • psutil system stats   │    │
│  └─────────────────────────┘    │
└─────────────────────────────────┘
```

### Component Breakdown

**`app.py` — the entire backend**

All server logic lives in a single Python file to keep deployment simple. It is structured in layers:

1. **Configuration helpers** — reads and writes `server_cfg.ini` and `entry_list.ini` directly on disk. The INI parser preserves all comments and sections and only modifies the specific key-value pairs that were changed.

2. **systemd integration** — server start / stop / restart are executed via `subprocess` calls to `systemctl`. Status is polled via `systemctl is-active`.

3. **RCON client** — kick and ban commands are sent over a raw TCP socket using the Minecraft/Source-style RCON protocol that AssettoServer exposes on port 9700. The admin password from `server_cfg.ini` is used automatically.

4. **HTTP polling** — connected driver list and session info are fetched from AssettoServer's built-in HTTP API (`/JSON|` endpoint) on port 8081.

5. **UDP telemetry thread** — a background daemon thread listens on UDP port 12000 for AssettoServer's Real-Time telemetry packets. Packet types 2/53 (car update) carry spline position, lap time, best lap, and last lap. Packet type 4 (lap completed) updates lap counts. This data is merged with the HTTP driver list to produce the live driver cards and minimap positions.

6. **Spline / minimap** — when a track is selected, the `fast_lane.ai` binary spline file is parsed from disk. The 3D points are projected to 2D (X/Z), normalized to a 0–1 range, and cached. Each driver's spline position (0.0–1.0, received via UDP) is mapped to a pixel coordinate on a Canvas element in the browser.

7. **Content import** — ZIP files are uploaded to a temporary directory, analyzed to find which cars and tracks they contain, and then selectively extracted into `/opt/assettoserver/content/`.

8. **Preset system** — current track/car/server name settings are serialized to JSON and stored in `/opt/acweb/presets.json`. Loading a preset writes back to `server_cfg.ini` and immediately restarts the server.

**`index.html` — the entire frontend**

A single-page application rendered as a Jinja2 template. All JavaScript is inline. The UI polls `/api/live` every 3 seconds to refresh driver cards, system stats, chat, and the minimap without reloading the page.

---

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | Python 3, Flask |
| Auth | Flask-HTTPAuth (HTTP Basic) |
| System info | psutil |
| Live telemetry | UDP listener (AC Real-Time protocol) |
| Server control | systemd via `systemctl` |
| Frontend | Vanilla JS, CSS Grid, Canvas API |

---

## Requirements

- Linux server with `systemd`
- Python 3.9+
- AssettoServer installed at `/opt/assettoserver`
- The web app itself deployed at `/opt/acweb`

```
pip install flask flask-httpauth werkzeug psutil
```

---

## Installation

1. **Clone the repository**
   ```bash
   git clone https://github.com/jyxbk/AssettoCorsaServerManager.git /opt/acweb
   cd /opt/acweb
   ```

2. **Install dependencies**
   ```bash
   pip install flask flask-httpauth werkzeug psutil
   ```

3. **Configure paths** (if your AssettoServer is not at `/opt/assettoserver`)
   Edit the constants at the top of `app.py`:
   ```python
   SERVER_DIR = Path("/opt/assettoserver")
   SERVICE_NAME = "acserver"
   RCON_PORT = 9700
   ```

4. **Change the default credentials**
   In `app.py`, update the admin password:
   ```python
   USERS = {"admin": generate_password_hash("your-secure-password")}
   ```

5. **Run the dashboard**
   ```bash
   python app.py
   ```
   The interface is available at `http://<server-ip>:8080`

6. **(Optional) Run as a systemd service**
   ```ini
   [Unit]
   Description=AC Web Dashboard
   After=network.target

   [Service]
   ExecStart=/usr/bin/python3 /opt/acweb/app.py
   WorkingDirectory=/opt/acweb
   Restart=always
   User=root

   [Install]
   WantedBy=multi-user.target
   ```
   ```bash
   systemctl enable --now acweb
   ```

---

## UDP Telemetry

Live driver positions and lap times are received via the Assetto Corsa Real-Time UDP protocol on port `12000` (localhost). Make sure your server config has UDP output enabled and points to `127.0.0.1:12000`.

---

## Security Notes

- The dashboard uses HTTP Basic Auth. It is strongly recommended to run it behind a reverse proxy (e.g. nginx) with HTTPS in any public-facing setup.
- Never commit credentials or server info files to version control — use `.gitignore`.
- The `ADMIN_PASSWORD` in `server_cfg.ini` is used for RCON commands (kick/ban).

---

## License

MIT — free to use, modify, and distribute.
