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
import os
import re
from typing import Optional
from dotenv import load_dotenv

load_dotenv(override=True)

LAN_ORIGIN_REGEX = r"https?://(localhost|127\.0\.0\.1|10\.\d+\.\d+\.\d+|192\.168\.\d+\.\d+|172\.(1[6-9]|2\d|3[0-1])\.\d+\.\d+)(:\d+)?"
LAN_ORIGIN_PATTERN = re.compile(f"^{LAN_ORIGIN_REGEX}$")
VERCEL_PREVIEW_PATTERN = re.compile(r"^https://[a-zA-Z0-9-]+\.vercel\.app$")


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


def _is_allowed_origin(origin: Optional[str]) -> bool:
    if not origin:
        return False
    normalized = origin.rstrip("/")
    return bool(
        normalized in ALLOWED_ORIGINS
        or LAN_ORIGIN_PATTERN.match(normalized)
        or VERCEL_PREVIEW_PATTERN.match(normalized)
    )


def _add_cors_headers(response):
    origin = request.headers.get("Origin")
    if _is_allowed_origin(origin):
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Vary"] = "Origin"
        response.headers["Access-Control-Allow-Credentials"] = "true"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization, X-Requested-With, Accept, Origin"
        response.headers["Access-Control-Allow-Methods"] = "GET, HEAD, POST, OPTIONS, PUT, PATCH, DELETE"
    return response

def create_app():
    app = Flask(__name__)
    
    # Configuration
    app.config['SECRET_KEY'] = os.getenv('SECRET_KEY')
    
    # CORS Configuration for LAN and local dev
    # Allow specified origins and common request headers (includes Content-Type)
    CORS(
        app,
        resources={
            r"/*": {
                "origins": list(ALLOWED_ORIGINS) + [LAN_ORIGIN_REGEX, r"https://[a-zA-Z0-9-]+\.vercel\.app"],
            }
        },
        supports_credentials=True,
        allow_headers=["Content-Type", "Authorization", "X-Requested-With", "Accept", "Origin"],
        methods=["GET", "HEAD", "POST", "OPTIONS", "PUT", "PATCH", "DELETE"],
    )

    @app.after_request
    def add_cors_headers(response):
        return _add_cors_headers(response)

    @app.errorhandler(Exception)
    def handle_unexpected_error(error):
        if isinstance(error, HTTPException):
            response = jsonify({"error": error.description})
            response.status_code = error.code or 500
            return _add_cors_headers(response)

        app.logger.exception("Unhandled server error", exc_info=error)
        response = jsonify({"error": "Unexpected server error", "detail": str(error)})
        response.status_code = 500
        return _add_cors_headers(response)
    
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
