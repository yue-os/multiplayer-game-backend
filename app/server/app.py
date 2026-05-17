import os
import re
from typing import Optional
from dotenv import load_dotenv
import logging

load_dotenv()

from flask import Flask, jsonify, request
from flask_cors import CORS
from werkzeug.exceptions import HTTPException
from app.server.database import init_db
from app.server.routes.user import user_bp
from app.server.routes.appRoutes import app_bp
from app.server.routes.teacher import teacher_bp
from app.server.routes.parent import parent_bp
from app.server.routes.docs import docs_bp
from app.server.routes.admin_users_flask import admin_users_bp

LAN_ORIGIN_REGEX = r"https?://(localhost|127\.0\.0\.1|10\.\d+\.\d+\.\d+|192\.168\.\d+\.\d+|172\.(1[6-9]|2\d|3[0-1])\.\d+\.\d+)(:\d+)?"
LAN_ORIGIN_PATTERN = re.compile(f"^{LAN_ORIGIN_REGEX}$")
VERCEL_PREVIEW_PATTERN = re.compile(r"^https://[a-zA-Z0-9-]+\.vercel\.app$")

logger = logging.getLogger("http")
logger.setLevel(logging.INFO)

_udp_discovery_started = False


def _maybe_start_udp_discovery() -> None:
    global _udp_discovery_started
    if _udp_discovery_started:
        return

    enabled = os.getenv("ENABLE_UDP_DISCOVERY", "false").strip().lower() in ("1", "true", "yes", "on")
    if not enabled:
        return

    try:
        from udp_discovery import start_background_discovery
        start_background_discovery()
        _udp_discovery_started = True
        logger.info("UDP discovery responder started")
    except Exception as exc:
        logger.warning("UDP discovery responder failed to start: %s", exc)


def _parse_csv_env(name: str) -> set[str]:
    raw = os.getenv(name, "")
    return {item.strip().rstrip("/") for item in raw.split(",") if item.strip()}


def _load_allowed_origins() -> set[str]:
    allowed = set()
    allowed.update(_parse_csv_env("FRONTEND_ORIGINS"))
    allowed.update(_parse_csv_env("CORS_ALLOWED_ORIGINS"))

    vercel_url = os.getenv("VERCEL_URL", "").strip().rstrip("/")
    if vercel_url:
        if not vercel_url.startswith("http://") and not vercel_url.startswith("https://"):
            vercel_url = f"https://{vercel_url}"
        allowed.add(vercel_url)

    return allowed


ALLOWED_ORIGINS = _load_allowed_origins()


def create_app():
    app = Flask(__name__)
    _maybe_start_udp_discovery()
    
    # Configuration
    app.config['SECRET_KEY'] = os.getenv('SECRET_KEY')
    
    # CORS Configuration
    # We use flask-cors to handle all CORS logic including preflights (OPTIONS).
    # We allow the specific origins from environment variables, plus local/preview patterns.
    CORS(
        app,
        resources={
            r"/*": {
                "origins": list(ALLOWED_ORIGINS) + [LAN_ORIGIN_PATTERN, VERCEL_PREVIEW_PATTERN],
            }
        },
        supports_credentials=True,
        allow_headers=["Content-Type", "Authorization", "X-Requested-With", "Accept", "Origin"],
        methods=["GET", "HEAD", "POST", "OPTIONS", "PUT", "PATCH", "DELETE"],
        expose_headers=["Content-Type", "Authorization"],
        vary_header=True
    )

    @app.before_request
    def log_request():
        try:
            body = request.get_data(as_text=True)[:1000]
        except Exception:
            body = "<unreadable>"
        logger.info("HTTP %s %s from=%s headers=%s body=%s",
                    request.method,
                    request.path,
                    request.remote_addr,
                    {k: v for k, v in request.headers.items() if k.lower() not in ("content-type", "user-agent", "authorization")},
                    body)

    @app.route("/health", methods=["GET"])
    def health_check():
        return jsonify({"status": "ok"}), 200

    @app.errorhandler(Exception)
    def handle_unexpected_error(error):
        if isinstance(error, HTTPException):
            response = jsonify({"error": error.description})
            response.status_code = error.code or 500
            return response

        app.logger.exception("Unhandled server error", exc_info=error)
        response = jsonify({"error": "Unexpected server error", "detail": str(error)})
        response.status_code = 500
        return response
    
    # Initialize Database
    init_db(app)
    
    # Register Blueprints
    app.register_blueprint(user_bp)
    app.register_blueprint(app_bp)
    app.register_blueprint(teacher_bp)
    app.register_blueprint(parent_bp)
    app.register_blueprint(docs_bp)
    app.register_blueprint(admin_users_bp)
    
    return app


app = create_app()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=True)
