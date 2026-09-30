from __future__ import annotations

import os

from a2wsgi import WSGIMiddleware
from fastapi import FastAPI
from fastapi import Request
from fastapi.middleware.cors import CORSMiddleware

from app.auth.auth_handler import decodeJWT
from app.server.audit import audit_context, classify_read, classify_request_action, record_activity
from app.server.app import app as flask_app
from app.server.routes.admin_users import router as admin_users_router
from app.server.routes.game_sockets import router as game_sockets_router


app = FastAPI(title="BatangAware Realtime Backend", version="0.1.0")


@app.middleware("http")
async def audit_fastapi_activity(request: Request, call_next):
    path = request.url.path
    # Flask installs its own hooks for its mounted routes. These FastAPI paths
    # handle their own audit context here, including ORM changes in the admin API.
    if not (path.startswith("/api/admin/users") or path == "/ws/lobby/list"):
        return await call_next(request)

    payload = None
    authorization = request.headers.get("Authorization", "")
    if authorization.startswith("Bearer "):
        payload = decodeJWT(authorization.removeprefix("Bearer ").strip())

    actor_id = None
    actor_role = "Anonymous"
    if payload:
        try:
            actor_id = int(payload.get("user_id"))
        except (TypeError, ValueError):
            actor_id = None
        actor_role = payload.get("role") or "Anonymous"

    context = {
        "user_id": actor_id,
        "user_role": actor_role,
        "request_method": request.method,
        "request_path": path,
        "ip_address": request.client.host if request.client else None,
        "tracked_changes": 0,
    }
    context_token = audit_context.set(context)
    try:
        response = await call_next(request)
        if response.status_code < 400:
            event_data = None
            if request.method == "GET" and actor_id is not None:
                read_data = classify_read(path)
                if read_data:
                    entity_type, action = read_data
                    event_data = ("read", action, entity_type, None)
            elif context.get("tracked_changes", 0) == 0:
                event_data = classify_request_action(request.method, path)

            if event_data:
                action_type, action, entity_type, entity_id = event_data
                try:
                    with flask_app.app_context():
                        record_activity(
                            user_id=actor_id,
                            user_role=actor_role,
                            action_type=action_type,
                            action=action,
                            entity_type=entity_type,
                            entity_id=entity_id,
                            request_method=request.method,
                            request_path=path,
                            ip_address=context.get("ip_address"),
                        )
                        from app.server.database import db

                        db.session.commit()
                        db.session.remove()
                except Exception:
                    from app.server.database import db

                    db.session.rollback()
                    db.session.remove()
                    flask_app.logger.exception("Unable to persist FastAPI activity audit record")
        return response
    finally:
        audit_context.reset(context_token)

lan_origin_regex = r"https?://(localhost|127\.0\.0\.1|10\.\d+\.\d+\.\d+|192\.168\.\d+\.\d+|172\.(1[6-9]|2\d|3[0-1])\.\d+\.\d+)(:\d+)?"


def _parse_csv_env(name: str) -> list[str]:
    raw = os.getenv(name, "")
    return [item.strip().rstrip("/") for item in raw.split(",") if item.strip()]


allowed_origins = [
    "http://localhost:5173",
    "http://localhost:3000",
    "http://localhost:8080",
    "http://127.0.0.1:5173",
    "http://127.0.0.1:3000",
    "http://127.0.0.1:8080",
]
allowed_origins.extend(_parse_csv_env("FRONTEND_ORIGINS"))
allowed_origins.extend(_parse_csv_env("CORS_ALLOWED_ORIGINS"))
for env_name in ("FRONTEND_BASE_URL", "PASSWORD_RESET_BASE_URL", "RESET_LINK_BASE_URL"):
    env_url = os.getenv(env_name, "").strip().rstrip("/")
    if env_url:
        allowed_origins.append(env_url)

app.add_middleware(
    CORSMiddleware,
    allow_origins=sorted(set(allowed_origins)),
    allow_origin_regex=lan_origin_regex,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(game_sockets_router)
app.include_router(admin_users_router)

# Keep the existing Flask REST API available from the same process. The
# WebSocket routes above must remain registered before this catch-all mount.
app.mount("/", WSGIMiddleware(flask_app))


@app.get("/")
def root() -> dict[str, str]:
    return {"service": "batangaware-realtime", "status": "ok"}


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "healthy"}
