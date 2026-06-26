"""Session-based auth + rate limiting + CSRF protection.

Bug fix #1: _get_client_ip() liest X-Real-IP / X-Forwarded-For, damit das
Rate-Limiting hinter einem nginx-Reverse-Proxy mit echten Client-IPs arbeitet
statt immer 127.0.0.1 zu verwenden.
"""
import functools
import secrets
import threading
import time

from flask import jsonify, redirect, request, session, url_for


_rate_limit: dict = {}
_rate_lock = threading.Lock()

# Separater Bucket für API-Rate-Limiting (schreibende Endpunkte)
_api_rate_limit: dict = {}
_api_rate_lock = threading.Lock()


# ── CSRF ──────────────────────────────────────────────────────────────────────

def get_csrf_token() -> str:
    """Gibt den CSRF-Token der aktuellen Session zurück, erzeugt ihn bei Bedarf."""
    if "_csrf_token" not in session:
        session["_csrf_token"] = secrets.token_hex(32)
    return session["_csrf_token"]


def csrf_protect(f):
    """Decorator: validiert den CSRF-Token bei allen POST/PUT/DELETE Requests.

    JSON-Requests senden den Token im X-CSRF-Token Header.
    Formular-Requests senden ihn als Hidden-Field '_csrf_token'.
    GET-Requests werden nicht geprüft.
    """
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        if request.method in ("POST", "PUT", "DELETE", "PATCH"):
            token = (
                request.headers.get("X-CSRF-Token")
                or request.form.get("_csrf_token")
            )
            expected = session.get("_csrf_token")
            if not token or not expected or not secrets.compare_digest(token, expected):
                if request.is_json or request.path.startswith("/api/"):
                    return jsonify({"ok": False, "msg": "CSRF token ungültig"}), 403
                return redirect(url_for("main.login"))
        return f(*args, **kwargs)
    return decorated


def _get_client_ip() -> str:
    """Echter Client-IP, auch hinter nginx.

    nginx muss 'proxy_set_header X-Real-IP $remote_addr;' gesetzt haben.
    """
    real_ip = request.headers.get("X-Real-IP")
    if real_ip:
        return real_ip.strip()
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.remote_addr or "unknown"


def check_rate_limit(ip: str) -> bool:
    """Maximal 10 Login-Versuche pro IP innerhalb von 5 Minuten."""
    now = time.time()
    with _rate_lock:
        times = [t for t in _rate_limit.get(ip, []) if now - t < 300]
        if len(times) >= 10:
            return False
        times.append(now)
        _rate_limit[ip] = times
        return True


def api_rate_limit(max_calls: int = 30, window: int = 60):
    """Decorator-Factory: begrenzt schreibende API-Calls pro IP.

    Standard: max. 30 Requests pro 60 Sekunden.
    Bei Überschreitung: HTTP 429 mit Retry-After Header.
    """
    def decorator(f):
        @functools.wraps(f)
        def decorated(*args, **kwargs):
            ip  = _get_client_ip()
            now = time.time()
            key = f"{f.__name__}:{ip}"
            with _api_rate_lock:
                times = [t for t in _api_rate_limit.get(key, []) if now - t < window]
                if len(times) >= max_calls:
                    resp = jsonify({"ok": False, "msg": f"Rate limit: max {max_calls} Requests/{window}s"})
                    resp.headers["Retry-After"] = str(window)
                    return resp, 429
                times.append(now)
                _api_rate_limit[key] = times
            return f(*args, **kwargs)
        return decorated
    return decorator


def login_required(f):
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("logged_in"):
            if request.path.startswith("/api/") or request.path.startswith("/control/") \
                    or request.path in (
                        "/logs", "/upload", "/import_zip",
                        "/upload_file", "/upload_folder",
                        "/upload_folder_done", "/save_config",
                        "/save_assists", "/save_server_settings",
                        "/save_session", "/save_weather",
                        "/save_dynamic_track",
                    ):
                return jsonify({"ok": False, "msg": "Unauthorized"}), 401
            return redirect(url_for("main.login"))
        return f(*args, **kwargs)
    return decorated
