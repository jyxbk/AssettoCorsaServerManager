#!/usr/bin/env python3
"""Flask-App-Factory — neue modulare Version des AC Server Dashboards."""
from flask import Flask
from constants import SECRET_KEY


def create_app():
    app = Flask(__name__, template_folder="templates")
    app.secret_key = SECRET_KEY
    app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024 * 1024

    from routes.main import bp as main_bp
    from routes.settings import bp as settings_bp
    from routes.analytics import bp as analytics_bp
    from routes.championship import bp as championship_bp
    from routes.content_mgmt import bp as content_mgmt_bp
    from routes.entry_list import bp as entry_list_bp
    from routes.laptimes_routes import bp as laptimes_bp
    from routes.players import bp as players_bp
    from routes.results import bp as results_bp
    from routes.scheduler import bp as scheduler_bp

    app.register_blueprint(main_bp)
    app.register_blueprint(settings_bp)
    app.register_blueprint(analytics_bp)
    app.register_blueprint(championship_bp)
    app.register_blueprint(content_mgmt_bp)
    app.register_blueprint(entry_list_bp)
    app.register_blueprint(laptimes_bp)
    app.register_blueprint(players_bp)
    app.register_blueprint(results_bp)
    app.register_blueprint(scheduler_bp)

    return app


if __name__ == "__main__":
    application = create_app()
    application.run(host="0.0.0.0", port=5000, debug=False)
