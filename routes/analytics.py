"""Analytics API: Fahrerprofile mit Ø-Geschwindigkeit, Konsistenz & Safety-Score."""
from flask import Blueprint, jsonify, request

from helpers.analytics import all_drivers_summary, driver_profile
from helpers.auth import login_required

bp = Blueprint("analytics", __name__)


@bp.route("/api/analytics/drivers")
@login_required
def api_analytics_drivers():
    return jsonify({"ok": True, "drivers": all_drivers_summary()})


@bp.route("/api/analytics/driver")
@login_required
def api_analytics_driver():
    driver = request.args.get("name", "").strip()
    profile = driver_profile(driver) if driver else None
    if profile is None:
        return jsonify({"ok": False, "msg": "Not found"}), 404
    return jsonify({"ok": True, "profile": profile})
