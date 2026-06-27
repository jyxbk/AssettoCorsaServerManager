"""Race Results Routes."""
from flask import Blueprint, jsonify

from helpers.auth import login_required
from helpers.results import get_result, list_results

bp = Blueprint("results_bp", __name__)


@bp.route("/api/results")
@login_required
def api_results():
    return jsonify({"ok": True, "results": list_results()})


@bp.route("/api/results/<filename>")
@login_required
def api_result_detail(filename):
    data = get_result(filename)
    if data is None:
        return jsonify({"ok": False, "msg": "Not found"}), 404
    return jsonify({"ok": True, "result": data})
