from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.server.routes.admin_users import router as admin_users_router
from app.server.routes.game_sockets import router as game_sockets_router


app = FastAPI(title="BatangAware Realtime Backend", version="0.1.0")

origins = [
    "http://192.168.1.7:5173",
    "http://192.168.1.16:5173",
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://192.168.1.16:3000",
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://192.168.1.16:8080",
    "http://localhost:8080",
    "http://127.0.0.1:8080",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
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
