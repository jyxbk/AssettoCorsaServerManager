"""Player-Management: Whitelist, Admins, Blacklist, Kick/Ban."""
from flask import Blueprint, jsonify, request

from constants import ADMINS_FILE, BLACKLIST_FILE, WHITELIST_FILE
from helpers.auth import api_rate_limit, csrf_protect, login_required
from helpers.content import add_guid, is_valid_guid, read_guid_list, remove_guid
from helpers.system import rcon_send

bp = Blueprint("players", __name__)


# ── Whitelist ─────────────────────────────────────────────────────────────────

@bp.route("/api/whitelist", methods=["GET"])
@login_required
def get_whitelist():
    return jsonify({"guids": read_guid_list(WHITELIST_FILE)})


@bp.route("/api/whitelist", methods=["POST"])
@login_required
@csrf_protect
def add_whitelist():
    guid = (request.json or {}).get("guid", "").strip()
    if not guid:
        return jsonify({"ok": False, "msg": "GUID required"}), 400
    if not is_valid_guid(guid):
        return jsonify({"ok": False, "msg": "Invalid GUID"}), 400
    added = add_guid(WHITELIST_FILE, guid)
    return jsonify({"ok": True, "added": added})


@bp.route("/api/whitelist/<guid>", methods=["DELETE"])
@login_required
@csrf_protect
def del_whitelist(guid):
    removed = remove_guid(WHITELIST_FILE, guid)
    return jsonify({"ok": removed, "msg": "Removed" if removed else "Not found"})


# ── Admins ────────────────────────────────────────────────────────────────────

@bp.route("/api/admins", methods=["GET"])
@login_required
def get_admins():
    return jsonify({"guids": read_guid_list(ADMINS_FILE)})


@bp.route("/api/admins", methods=["POST"])
@login_required
@csrf_protect
def add_admin():
    guid = (request.json or {}).get("guid", "").strip()
    if not guid:
        return jsonify({"ok": False, "msg": "GUID required"}), 400
    if not is_valid_guid(guid):
        return jsonify({"ok": False, "msg": "Invalid GUID"}), 400
    added = add_guid(ADMINS_FILE, guid)
    return jsonify({"ok": True, "added": added})


@bp.route("/api/admins/<guid>", methods=["DELETE"])
@login_required
@csrf_protect
def del_admin(guid):
    removed = remove_guid(ADMINS_FILE, guid)
    return jsonify({"ok": removed, "msg": "Removed" if removed else "Not found"})


# ── Blacklist ─────────────────────────────────────────────────────────────────

@bp.route("/api/blacklist", methods=["GET"])
@login_required
def get_blacklist():
    return jsonify({"guids": read_guid_list(BLACKLIST_FILE)})


@bp.route("/api/blacklist/<guid>", methods=["DELETE"])
@login_required
@csrf_protect
def del_blacklist(guid):
    removed = remove_guid(BLACKLIST_FILE, guid)
    return jsonify({"ok": removed, "msg": "Removed" if removed else "Not found"})


# ── Kick / Ban ────────────────────────────────────────────────────────────────

@bp.route("/api/kick", methods=["POST"])
@login_required
@csrf_protect
@api_rate_limit(max_calls=20, window=60)
def kick_player():
    car_id = (request.json or {}).get("car_id")
    if car_id is None:
        return jsonify({"ok": False, "msg": "car_id missing"}), 400
    if not isinstance(car_id, int) or car_id < 0:
        return jsonify({"ok": False, "msg": "car_id must be a non-negative integer"}), 400
    ok, msg = rcon_send(f"/kick_id {car_id}")
    return jsonify({"ok": ok, "msg": msg})


@bp.route("/api/ban", methods=["POST"])
@login_required
@csrf_protect
@api_rate_limit(max_calls=20, window=60)
def ban_player():
    data   = request.json or {}
    guid   = data.get("guid", "")
    name   = data.get("name", "unknown")
    car_id = data.get("car_id")
    if not guid:
        return jsonify({"ok": False, "msg": "GUID missing"}), 400
    if not is_valid_guid(guid):
        return jsonify({"ok": False, "msg": "Invalid GUID"}), 400
    add_guid(BLACKLIST_FILE, guid)
    if car_id is not None and isinstance(car_id, int) and car_id >= 0:
        rcon_send(f"/kick_id {car_id}")
    return jsonify({"ok": True, "msg": f"{name} banned"})
