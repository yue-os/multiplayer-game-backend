from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.server.routes.admin_users import router as admin_users_router
from app.server.routes.game_sockets import router as game_sockets_router


app = FastAPI(title="BatangAware Realtime Backend", version="0.1.0")

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


@app.get("/")
def root() -> dict[str, str]:
    return {"service": "batangaware-realtime", "status": "ok"}


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "healthy"}
