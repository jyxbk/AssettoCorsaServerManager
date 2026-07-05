# AC Server Dashboard

Ein modernes, browserbasiertes Dashboard zum Verwalten eines [AssettoServer](https://github.com/compujuckel/AssettoServer)-Dedicated-Servers. Gebaut mit Python & Flask — ohne externe Abhängigkeiten außer dem Standard-Stack.

[![Release](https://img.shields.io/github/v/release/jyxbk/AssettoCorsaServerManager)](https://github.com/jyxbk/AssettoCorsaServerManager/releases)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue)](https://python.org)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

<img width="1838" height="1017" alt="Dashboard Screenshot" src="https://github.com/user-attachments/assets/297b6db8-92c4-4ceb-ac8b-e615fb15d55c" />

---

## Features

### Live-Dashboard
- **Echtzeit-Fahrerinformationen** — Rundenzeit, Bestzeit, aktuelle Runde, Fahrzeug
- **Live-Minimap** — Fahrerposition auf der Strecke via UDP-Telemetrie und Strecken-Spline-Daten
- **System-Monitor** — CPU, RAM, Netzwerk (TX/RX KB/s), Server-Uptime
- **Server-Chat-Mirror** — In-Game-Chat im Browser sichtbar und mit RCON beantwortbar
- **Nation-Flags** — Herkunftsland der verbundenen Fahrer
- **Error-Banner** — Automatische Warnung bei Server-Ausfällen

### Konfiguration
- **Server-Einstellungen** — Name, Passwort, Fahrzeuge, Strecke, Slots, Admin-Passwort
- **Session-Konfiguration** — Practice, Qualifying, Race mit individuellen Zeiten
- **Wetter-Editor** — Preset-Wechsel, Temperaturen, dynamischer Track-Grip
- **Extra-Config-Editor** — `extra_cfg.yml` direkt im Browser bearbeiten
- **Entry-List-Editor** — Fahrzeugslots mit GUID-Verwaltung

### Rundenzeiten & Analytics
- **Persistente Speicherung** in SQLite (WAL-Modus, automatische JSON-Migration)
- **Live-Erkennung** via journalctl-Parsing — kein Neustart erforderlich
- **Filter & Suche** — nach Fahrer, Strecke, Auto, Datum, Freitext
- **CSV-Export** mit Formula-Injection-Schutz
- **Fahrer-Analytik** — Ø-Geschwindigkeit, Konsistenz-Score, Safety-Score
- **Leaderboard** — Bestzeiten aller Fahrer pro Strecke

### Benachrichtigungen
- **Discord-Webhooks** — Server-Crash/Restart, Fahrer Join/Leave, neuer PB, Streckenrekord
- **Telegram-Bot** — Server-Status-Benachrichtigungen
- **In-Game-Chat** — Benachrichtigung bei neuen Bestzeiten direkt im Spiel
- **Splitzzeiten** — Konfigurierbare Messpunkte mit Chat-Ausgabe

### Weitere Features
- **Content-Management** — Car/Track-Browser, ZIP-Upload, Installations-Assistent
- **Spielerverwaltung** — Whitelist, Admins, Blacklist, Kick/Ban
- **Meisterschaften** — CRUD mit konfigurierbaren Punkteschemata, automatische Standings
- **Ergebnis-Viewer** — Alle Session-Typen (Practice, Qualifying, Race)
- **Scheduler** — Zeitgesteuerte Server-Neustarts und Preset-Wechsel
- **Theme-System** — 4 Themes: Dark, Dark Blue, Racing Green, Hell

---

## Voraussetzungen

| Anforderung | Mindestversion |
|-------------|---------------|
| Python | 3.11 |
| AssettoServer | aktuell |
| Betriebssystem | Linux (Debian/Ubuntu empfohlen) |
| systemd | für Service-Management |

**Python-Pakete** (siehe `requirements.txt`):
- Flask 3.1.3
- psutil 7.2.2
- Werkzeug, Jinja2 (Flask-Abhängigkeiten)

---

## Installation

### 1. Repository klonen

```bash
git clone https://github.com/jyxbk/AssettoCorsaServerManager.git /opt/acweb
cd /opt/acweb
```

### 2. Python-Virtualenv erstellen und Abhängigkeiten installieren

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 3. Umgebungsvariablen konfigurieren

```bash
cp .env.example .env
nano .env          # Pflichtfelder befüllen (ACWEB_SECRET, ACWEB_USER, ACWEB_PASS)
```

> **Wichtig:** `ACWEB_SECRET` muss ein zufälliger String mit mindestens 32 Zeichen sein.
> Generieren: `python3 -c "import secrets; print(secrets.token_hex(32))"`

### 4. systemd-Service einrichten

```ini
# /etc/systemd/system/acweb.service
[Unit]
Description=Assetto Corsa Web Interface
After=network.target acserver.service

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
systemctl daemon-reload
systemctl enable acweb
systemctl start acweb
```

### 5. Dashboard öffnen

```
http://<server-ip>:8080
```

Anmelden mit den in `.env` konfigurierten Zugangsdaten.

---

## Schnellstart (Kurzversion)

```bash
git clone https://github.com/jyxbk/AssettoCorsaServerManager.git /opt/acweb
cd /opt/acweb
python3 -m venv venv && source venv/bin/activate && pip install -r requirements.txt
cp .env.example .env
# .env editieren: ACWEB_SECRET, ACWEB_USER, ACWEB_PASS setzen
python3 app.py    # Entwicklungsmodus, für Produktion: systemd-Service nutzen
```

---

## Konfiguration

### Pflicht-Umgebungsvariablen

| Variable | Beschreibung |
|----------|-------------|
| `ACWEB_SECRET` | Flask-Session-Secret (mind. 32 zufällige Zeichen) |
| `ACWEB_USER` | Login-Benutzername für das Dashboard |
| `ACWEB_PASS` | Login-Passwort für das Dashboard |

### Optionale Umgebungsvariablen

| Variable | Standard | Beschreibung |
|----------|---------|-------------|
| `AC_SERVICE` | `acserver` | systemd-Service-Name des Assetto-Servers |
| `AC_RCON_PORT` | `9700` | RCON-Port |
| `ACWEB_LOG_LEVEL` | `INFO` | Log-Level: `DEBUG`, `INFO`, `WARNING`, `ERROR` |

Alle Variablen sind in `.env.example` dokumentiert.

### Dashboard-Konfiguration

Folgende Einstellungen werden **im Dashboard** konfiguriert (nicht via Env-Var):
- Discord-Webhook-URL und Benachrichtigungstypen
- Telegram-Bot-Token und Chat-ID
- Splitzzeiten-Konfiguration
- Scheduled Events (geplante Neustarts/Preset-Wechsel)

---

## Projektstruktur

```
/opt/acweb/
├── app.py                  # Flask-Einstiegspunkt
├── constants.py            # Pfade und Konfigurationskonstanten
├── requirements.txt        # Python-Abhängigkeiten (gepinnt)
├── .env                    # Lokale Konfiguration (nicht committen!)
├── .env.example            # Vorlage mit Beschreibungen
├── helpers/
│   ├── auth.py             # Authentifizierung, Rate-Limiting, CSRF
│   ├── config_io.py        # INI/YAML lesen und schreiben (atomar)
│   ├── db.py               # SQLite-Layer, Schema-Migrations
│   ├── laptimes.py         # Lap-Tracker, Journal-Parsing, Notifications
│   ├── system.py           # systemctl, psutil, UDP-Telemetrie, RCON
│   ├── threads.py          # Supervised Thread-Management
│   ├── discord.py          # Discord-Webhook-Integration
│   ├── telegram.py         # Telegram-Bot-Integration
│   ├── scheduler.py        # Zeitgesteuerter Event-Scheduler
│   ├── analytics.py        # Fahrer-Analytics (Speed, Konsistenz, Safety)
│   ├── championship.py     # Meisterschafts-Logik
│   ├── content.py          # Cars/Tracks/ZIP/Content-Management
│   └── results.py          # Rennergebnis-Parser
├── routes/                 # Flask-Blueprints (je ein File pro Domain)
├── static/
│   ├── css/dashboard.css   # Alle Styles inkl. 4 Themes
│   └── js/dashboard.js     # Frontend-Logik
└── templates/
    └── index.html          # Haupt-Template (SPA-ähnlich)
```

---

## Screenshots

| Dashboard | Server Monitor | Rundenzeiten |
|-----------|---------------|--------------|
| *(Screenshot folgt)* | *(Screenshot folgt)* | *(Screenshot folgt)* |

| Konfiguration | Meisterschaften | Fahrer-Analytics |
|---------------|----------------|-----------------|
| *(Screenshot folgt)* | *(Screenshot folgt)* | *(Screenshot folgt)* |

---

## FAQ

**Q: Funktioniert das auch mit dem Original Assetto Corsa Server (nicht AssettoServer)?**  
A: Teilweise. Die Basis-Konfiguration (INI-Dateien, Entry-List) funktioniert. Features wie Live-Weather-Plugin und AssettoServer-spezifische `extra_cfg.yml`-Optionen benötigen AssettoServer.

**Q: Wie werden Rundenzeiten erfasst?**  
A: Das Dashboard liest den systemd-Journal-Log des AC-Servers via `journalctl` aus und parst Lap-Completed-Events in Echtzeit. Kein Plugin oder Servermodifikation erforderlich.

**Q: Warum HTTP statt HTTPS?**  
A: Das Dashboard läuft auf Port 8080 ohne TLS. Für den produktiven Einsatz wird ein nginx-Reverse-Proxy mit Let's Encrypt empfohlen. Im lokalen LAN-Betrieb ist HTTP akzeptabel.

**Q: Was passiert wenn ein Background-Thread abstürzt?**  
A: Alle Background-Threads (Discord-Monitor, Telegram-Monitor, Lap-Tracker, Scheduler) werden automatisch neu gestartet — mit exponentiellem Backoff (5s → 10s → ... → max. 300s). Der Thread-Status ist unter `GET /api/system/threads` abrufbar.

**Q: Wie wird die Datenbank gesichert?**  
A: Derzeit keine automatischen Backups. Empfehlung: `data.db` täglich via cron sichern: `cp /opt/acweb/data.db /backup/data-$(date +%Y%m%d).db`

**Q: Kann ich mehrere Server verwalten?**  
A: Nein. Das Dashboard ist für einen einzelnen AssettoServer ausgelegt. Multi-Server-Support ist nicht geplant.

**Q: Wie setze ich das Passwort zurück?**  
A: `ACWEB_PASS` in `.env` ändern und den Service neu starten: `systemctl restart acweb`

---

## Bekannte Einschränkungen

- **Kein HTTPS** — nginx-Reverse-Proxy erforderlich für sichere Verbindungen
- **Kein Multi-Server-Support** — ein Dashboard, ein Server
- **`unsafe-inline` in CSP** — notwendig durch Jinja2-Inline-Scripts im Template
- **Kein automatisches DB-Backup** — manuelle Sicherung empfohlen
- **Rundenzeit-Erkennung via journalctl** — bei Journal-Rotation können sehr alte Einträge nicht nachgeladen werden
- **Nur Linux** — Windows und macOS werden nicht unterstützt (systemd-Abhängigkeit)

---

## Lizenz

[MIT](LICENSE) — Copyright (c) 2026 jyxbk
