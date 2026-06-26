"""Entry List Editor — API Routes."""
import json
from pathlib import Path

from flask import Blueprint, jsonify, request, send_file

from constants import CFG_DIR, PRESETS_FILE
from helpers.auth import api_rate_limit, csrf_protect, login_required
from helpers.config_io import read_server_cfg, update_server_cfg
from helpers.content import (
    get_car_skins, list_cars, read_entry_list, write_entry_list_slots,
)

bp = Blueprint("entry_list", __name__)

_EL_PRESETS_FILE = Path(str(PRESETS_FILE).replace("presets.json", "entry_list_presets.json"))


# ── Helpers ───────────────────────────────────────────────────────────────────

def _load_el_presets() -> dict:
    if _EL_PRESETS_FILE.exists():
        try:
            return json.loads(_EL_PRESETS_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save_el_presets(data: dict) -> None:
    _EL_PRESETS_FILE.parent.mkdir(parents=True, exist_ok=True)
    _EL_PRESETS_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _clamp_slot(s: dict) -> dict:
    return {
        "model":      str(s.get("model", ""))[:64],
        "skin":       str(s.get("skin",  ""))[:64],
        "ballast":    max(0, min(150, int(s.get("ballast",    0) or 0))),
        "restrictor": max(0, min(400, int(s.get("restrictor", 0) or 0))),
        "drivername": str(s.get("drivername", ""))[:64],
        "team":       str(s.get("team",       ""))[:64],
        "guid":       str(s.get("guid",       ""))[:64],
        "spectator":  int(bool(s.get("spectator", 0))),
    }


# ── GET /api/entry_list ───────────────────────────────────────────────────────

@bp.route("/api/entry_list", methods=["GET"])
@login_required
def get_entry_list():
    slots      = read_entry_list()
    cfg        = read_server_cfg()
    max_clients = int(cfg.get("MAX_CLIENTS", 0) or 0)
    cars        = list_cars()
    return jsonify({
        "ok":          True,
        "slots":       slots,
        "max_clients": max_clients,
        "cars":        cars,
    })


# ── POST /api/entry_list ──────────────────────────────────────────────────────

@bp.route("/api/entry_list", methods=["POST"])
@login_required
@csrf_protect
@api_rate_limit(max_calls=30, window=60)
def save_entry_list():
    data  = request.json or {}
    raw   = data.get("slots", [])
    if not isinstance(raw, list):
        return jsonify({"ok": False, "msg": "slots must be a list"}), 400
    if len(raw) > 200:
        return jsonify({"ok": False, "msg": "Maximal 200 Slots erlaubt"}), 400

    slots = [_clamp_slot(s) for s in raw]
    write_entry_list_slots(slots)

    # MAX_CLIENTS synchron halten
    total = len([s for s in slots if not s["spectator"]])
    if total:
        update_server_cfg({"MAX_CLIENTS": total})

    # CARS in server_cfg aktualisieren (alle einzigartigen Modelle)
    models = list(dict.fromkeys(s["model"] for s in slots if s["model"]))
    if models:
        update_server_cfg({"CARS": ";".join(models)})

    return jsonify({"ok": True, "msg": f"{len(slots)} Slots gespeichert", "total": len(slots)})


# ── GET /api/entry_list/export ────────────────────────────────────────────────

@bp.route("/api/entry_list/export")
@login_required
def export_entry_list():
    entry_path = CFG_DIR / "entry_list.ini"
    if not entry_path.exists():
        return jsonify({"ok": False, "msg": "entry_list.ini nicht gefunden"}), 404
    return send_file(
        str(entry_path), mimetype="text/plain",
        as_attachment=True, download_name="entry_list.ini",
    )


# ── GET /api/car_skins_detail/<car> ──────────────────────────────────────────

@bp.route("/api/car_skins_detail/<car>")
@login_required
def car_skins_detail(car):
    """Gibt Skins mit Thumbnail-Info zurück."""
    from constants import CARS_DIR
    skins = get_car_skins(car)
    result = []
    for s in skins:
        base = CARS_DIR / car / "skins" / s
        result.append({
            "name":        s,
            "has_livery":  (base / "livery.png").exists(),
            "has_preview": (base / "preview.png").exists(),
        })
    return jsonify({"ok": True, "skins": result})


# ── GET /api/entry_list_presets ───────────────────────────────────────────────

@bp.route("/api/entry_list_presets", methods=["GET"])
@login_required
def get_el_presets():
    return jsonify({"ok": True, "presets": _load_el_presets()})


# ── POST /api/entry_list_presets ──────────────────────────────────────────────

@bp.route("/api/entry_list_presets", methods=["POST"])
@login_required
@csrf_protect
@api_rate_limit(max_calls=20, window=60)
def save_el_preset():
    import time
    data    = request.json or {}
    name    = str(data.get("name", "")).strip()[:80]
    slots   = data.get("slots", [])
    if not name:
        return jsonify({"ok": False, "msg": "Name erforderlich"}), 400
    presets = _load_el_presets()
    presets[name] = {
        "slots":   [_clamp_slot(s) for s in slots],
        "saved":   time.strftime("%d.%m.%Y %H:%M"),
        "count":   len(slots),
    }
    _save_el_presets(presets)
    return jsonify({"ok": True, "msg": f"Preset \"{name}\" gespeichert"})


# ── DELETE /api/entry_list_presets/<name> ─────────────────────────────────────

@bp.route("/api/entry_list_presets/<name>", methods=["DELETE"])
@login_required
@csrf_protect
def delete_el_preset(name):
    presets = _load_el_presets()
    if name not in presets:
        return jsonify({"ok": False, "msg": "Preset nicht gefunden"}), 404
    del presets[name]
    _save_el_presets(presets)
    return jsonify({"ok": True})
