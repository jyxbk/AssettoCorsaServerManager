"""Supervised Thread-Management mit automatischem Neustart und Backoff-Logging."""
import logging
import threading
import time

_logger = logging.getLogger(__name__)

# Alle supervisierten Threads für Status-Endpunkt
_registry: list[dict] = []
_registry_lock = threading.Lock()


def supervised(
    target,
    name: str,
    restart_delay: float = 5.0,
    max_delay: float = 300.0,
    one_shot: bool = False,
) -> threading.Thread:
    """Startet *target* in einem supervisierten Daemon-Thread.

    Bei Exception:  logger.exception → Neustart nach *restart_delay* Sekunden.
    Bei normalem Return (kein Fehler):
        one_shot=False → Warning + Neustart (unerwartet, Loop hätte nicht enden sollen)
        one_shot=True  → Info, kein Neustart (Preload-Funktionen laufen einmalig)
    Backoff: delay verdoppelt sich pro Crash bis max_delay.
    """
    def _runner():
        delay = restart_delay
        while True:
            try:
                _logger.info("Thread [%s] gestartet", name)
                target()
                if one_shot:
                    _logger.info("Thread [%s] abgeschlossen (one-shot)", name)
                    _update_status(name, alive=False)
                    return
                _logger.warning(
                    "Thread [%s] unerwartet beendet — Neustart in %.0fs", name, delay
                )
            except Exception:
                _logger.exception(
                    "Thread [%s] abgestürzt — Neustart in %.0fs", name, delay
                )
            _update_status(name, alive=False, restarting=True)
            time.sleep(delay)
            delay = min(delay * 2, max_delay)
            _update_status(name, alive=True, restarting=False)

    t = threading.Thread(target=_runner, daemon=True, name=name)
    t.start()

    with _registry_lock:
        _registry.append({"name": name, "thread": t, "one_shot": one_shot})

    _logger.debug("Thread [%s] registriert (one_shot=%s)", name, one_shot)
    return t


def _update_status(name: str, *, alive: bool, restarting: bool = False):
    with _registry_lock:
        for entry in _registry:
            if entry["name"] == name:
                entry["_alive_override"] = alive
                entry["_restarting"] = restarting
                break


def thread_status() -> list[dict]:
    """Gibt den Status aller supervisierten Threads zurück (für /api/system oder Health-Check)."""
    with _registry_lock:
        return [
            {
                "name":       e["name"],
                "alive":      e["thread"].is_alive(),
                "one_shot":   e["one_shot"],
                "restarting": e.get("_restarting", False),
            }
            for e in _registry
        ]
