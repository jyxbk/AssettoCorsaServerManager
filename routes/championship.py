"""Championship Routes: CRUD + Standings."""
from flask import Blueprint, jsonify, request

from helpers.auth import csrf_protect, login_required
from helpers.championship import (
    POINTS_PRESETS, add_round, compute_standings, create_championship,
    delete_championship, get_championship, load_championships,
    remove_round, update_championship,
)
from helpers.results import list_results

bp = Blueprint("championship_bp", __name__)


@bp.route("/api/championships")
@login_required
def api_championships():
    data = load_championships()
    out  = []
    for c in data:
        out.append({
            "id":      c["id"],
            "name":    c["name"],
            "rounds":  len(c.get("rounds", [])),
            "created": c.get("created", ""),
            "points":  c.get("points", []),
        })
    return jsonify({"ok": True, "championships": out, "points_presets": POINTS_PRESETS})


@bp.route("/api/championships/<cid>/standings")
@login_required
def api_standings(cid):
    champ = get_championship(cid)
    if not champ:
        return jsonify({"ok": False, "msg": "Not found"}), 404
    data = compute_standings(champ)
    return jsonify({"ok": True, "championship": champ, **data})


@bp.route("/api/championships", methods=["POST"])
@login_required
@csrf_protect
def api_create_championship():
    data   = request.json or {}
    name   = str(data.get("name", "")).strip()
    points = data.get("points", POINTS_PRESETS["F1"])
    if not name:
        return jsonify({"ok": False, "msg": "Name fehlt"}), 400
    if not isinstance(points, list) or not all(isinstance(p, int) for p in points):
        return jsonify({"ok": False, "msg": "Ungültiges Punkte-Schema"}), 400
    champ = create_championship(name, points)
    return jsonify({"ok": True, "championship": champ})


@bp.route("/api/championships/<cid>", methods=["DELETE"])
@login_required
@csrf_protect
def api_delete_championship(cid):
    ok = delete_championship(cid)
    return jsonify({"ok": ok, "msg": "Deleted" if ok else "Not found"})


@bp.route("/api/championships/<cid>", methods=["PATCH"])
@login_required
@csrf_protect
def api_update_championship(cid):
    data   = request.json or {}
    name   = data.get("name")
    points = data.get("points")
    ok = update_championship(cid, name=name, points=points)
    return jsonify({"ok": ok})


@bp.route("/api/championships/<cid>/rounds", methods=["POST"])
@login_required
@csrf_protect
def api_add_round(cid):
    filename = (request.json or {}).get("filename", "").strip()
    if not filename:
        return jsonify({"ok": False, "msg": "filename fehlt"}), 400
    ok = add_round(cid, filename)
    return jsonify({"ok": ok})


@bp.route("/api/championships/<cid>/rounds/<path:filename>", methods=["DELETE"])
@login_required
@csrf_protect
def api_remove_round(cid, filename):
    ok = remove_round(cid, filename)
    return jsonify({"ok": ok})


@bp.route("/api/championships/results_list")
@login_required
def api_results_for_championship():
    """Gibt alle Result-Dateien zurück die als Runde hinzugefügt werden können."""
    return jsonify({"ok": True, "results": list_results(limit=200)})
