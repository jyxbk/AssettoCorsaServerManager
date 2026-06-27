"""Scheduler Routes: Zeitgesteuerte Events."""
from flask import Blueprint, jsonify, request

from helpers.auth import csrf_protect, login_required
from helpers.scheduler import create_event, delete_event, get_events, reset_event

bp = Blueprint("scheduler_bp", __name__)


@bp.route("/api/scheduled_events")
@login_required
def api_get_events():
    return jsonify({"ok": True, "events": get_events()})


@bp.route("/api/scheduled_events", methods=["POST"])
@login_required
@csrf_protect
def api_create_event():
    data   = request.json or {}
    name   = str(data.get("name", "")).strip()
    dt_str = str(data.get("datetime", "")).strip()
    action = str(data.get("action", "apply_preset")).strip()
    preset = str(data.get("preset", "")).strip()
    if not name or not dt_str:
        return jsonify({"ok": False, "msg": "Name und Datum/Uhrzeit erforderlich"}), 400
    if action == "apply_preset" and not preset:
        return jsonify({"ok": False, "msg": "Preset-Name fehlt"}), 400
    evt = create_event(name, dt_str, action, preset)
    return jsonify({"ok": True, "event": evt})


@bp.route("/api/scheduled_events/<eid>", methods=["DELETE"])
@login_required
@csrf_protect
def api_delete_event(eid):
    ok = delete_event(eid)
    return jsonify({"ok": ok, "msg": "Deleted" if ok else "Not found"})


@bp.route("/api/scheduled_events/<eid>/reset", methods=["POST"])
@login_required
@csrf_protect
def api_reset_event(eid):
    ok = reset_event(eid)
    return jsonify({"ok": ok})
