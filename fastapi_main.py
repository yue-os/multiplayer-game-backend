from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.server.routes.game_sockets import router as game_sockets_router


app = FastAPI(title="BatangAware Realtime Backend", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(game_sockets_router)


@app.get("/")
def root() -> dict[str, str]:
    return {"service": "batangaware-realtime", "status": "ok"}


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "healthy"}
