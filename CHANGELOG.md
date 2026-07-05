# Changelog

Alle relevanten Änderungen werden in dieser Datei dokumentiert.  
Format basiert auf [Keep a Changelog](https://keepachangelog.com/de/1.0.0/).  
Dieses Projekt folgt [Semantic Versioning](https://semver.org/lang/de/).

---

## [1.0.0] — 2026-07-05

Erste stabile Release-Version. Das Projekt wurde über 8 Entwicklungs-Sprints
aufgebaut und ist auf produktionsbereitem Stand.

### Hinzugefügt

#### Dashboard & Live-Ansicht
- Live-Dashboard mit Echtzeit-Fahrerinformationen (Rundenzeit, Bestzeit, aktuelle Runde)
- Live-Minimap: Fahrerposition auf der Strecke via UDP-Telemetrie und `fast_lane.ai`-Spline
- System-Monitor-Tab: CPU, RAM, Netzwerk (TX/RX) mit Echtzeit-Polling
- Nation-Flags aus AC-Client-Daten (`DriverNation`) in der Fahrerliste
- Server-Chat-Mirror: In-Game-Chat im Browser lesbar
- Error-Banner nach 4 aufeinanderfolgenden API-Fehlern

#### Konfiguration
- Server-Einstellungen (Name, Passwort, Fahrzeuge, Strecke, Sessions, Wetter)
- Dynamischer Track-Grip mit Preset-System
- Extra-Config-Editor (`extra_cfg.yml`)
- Sun-Angle zu Uhrzeit-Mapping via Look-Up-Table
- Entry-List-Editor mit GUID-Verwaltung

#### Rundenzeiten & Analytics
- SQLite-Backend für persistente Rundenzeiten-Speicherung (mit WAL-Modus)
- Live-Journal-Parsing via journalctl für Echtzeit-Lap-Erkennung
- Rundenzeiten-Filter (Fahrer, Strecke, Auto, Datum, Freitext)
- CSV-Export mit Formula-Injection-Schutz (CWE-1236)
- Fahrer-Analytik: Ø-Geschwindigkeit, Konsistenz-Score, Safety-Score
- Leaderboard: Bestzeiten aller Fahrer

#### Notifications
- Discord-Webhook-Integration: Server-Crash, Neustart, Fahrer-Join/Leave, PB, Streckenrekord
- Telegram-Bot-Integration: Server-Status-Benachrichtigungen
- In-Game-Chat-Benachrichtigungen bei neuen Bestzeiten und Streckenrekorden
- Splitzzeiten im In-Game-Chat (konfigurierbare Messpunkte)

#### Content-Management
- Car/Track-Browser mit UI-Daten aus den AC-Verzeichnissen
- ZIP-Upload und automatische Installation von Cars/Tracks
- Konfigurations-Backup als ZIP-Download
- Entry-List-Generierung aus installierten Fahrzeugen

#### Meisterschaften & Ergebnisse
- Meisterschafts-CRUD mit konfigurierbaren Punkteschemata (F1, IndyCar, custom)
- Automatische Standings-Berechnung aus Rennergebnis-JSONs
- Ergebnis-Viewer für alle Session-Typen (Practice, Qualifying, Race)

#### Spielerverwaltung
- Whitelist, Admins, Blacklist — Bearbeitung im Browser
- Kick/Ban direkt aus dem Dashboard

#### Scheduler
- Zeitgesteuerte Events: Server-Neustart oder Preset-Wechsel zu definiertem Zeitpunkt

#### Sicherheit & Infrastruktur
- Session-basierte Authentifizierung mit Rate-Limiting (10 Login-Versuche / 5 min)
- CSRF-Schutz auf allen mutierenden Endpunkten (POST/PUT/DELETE)
- HTTP-Security-Header: X-Frame-Options, X-Content-Type-Options, CSP, Referrer-Policy
- API-Rate-Limiting auf schreibenden Endpunkten (30 req/60s)
- Path-Traversal-Schutz für Datei-Endpunkte

#### Theme-System
- 4 Themes: Dark (Standard), Dark Blue, Racing Green, Hell
- Theme-Persistenz via `localStorage`
- `:focus-visible` für WCAG 2.1 AA Keyboard-Navigation

#### Logging & Supervision
- Strukturiertes Logging via Python `logging`-Modul (alle Module)
- Supervisierte Background-Threads mit automatischem Neustart und exponentiellem Backoff
- Thread-Status-Endpunkt: `GET /api/system/threads`

### Geändert

- Rundenzeiten-Speicherung von JSON-Datei auf SQLite migriert
- Schema-Versionierung via `PRAGMA user_version` für One-Time-Migrations
- UNIQUE-Index auf `(driver, track, laptime, ts)` — verhindert legitime Gleichstände nicht mehr
- SQL-Filterung mit `COLLATE NOCASE` statt `LOWER()` für Index-Kompatibilität
- Aggregierende Endpunkte nutzen jetzt SQL statt Python-O(n)-Schleifen

### Behoben

- Split-Events wurden bei deaktiviertem Chat-Notification unwiederbringlich verworfen
- `SUN_ANGLE` wurde als String in der INI-Datei gespeichert (AC erwartet Integer)
- CSV-Export hatte Float-Arithmetik-Fehler bei der Zeitformatierung
- `autoRestart()` gab keinen strict Boolean zurück

### Bekannte Einschränkungen

- Keine HTTPS-Unterstützung — empfohlene Lösung: nginx-Reverse-Proxy mit TLS
- `unsafe-inline` in CSP notwendig durch Jinja2-Inline-Scripts
- Kein Multi-Server-Support
- Keine automatischen DB-Backups

---

## [Unreleased]

Zukünftige Änderungen werden hier vor dem nächsten Release gesammelt.
