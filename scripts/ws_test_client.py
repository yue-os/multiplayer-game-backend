from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Sequence
from pathlib import Path

import websockets

# Add project root to path so we can import app
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.auth.auth_handler import signJWT


def build_url(base_ws_url: str, lobby_id: str, user_id: str, role: str) -> str:
    token = signJWT(user_id=user_id, role=role)["access_token"]
    return f"{base_ws_url}/ws/lobby/{lobby_id}?player_token=Bearer%20{token}"


async def init_lobby(base_http_url: str, lobby_id: str, required_players: int = 2) -> None:
    """Call the manual init endpoint to initialize the lobby."""
    import aiohttp
    
    async with aiohttp.ClientSession() as session:
        url = f"{base_http_url}/ws/lobby/{lobby_id}/init?required_players={required_players}"
        async with session.post(url) as resp:
            if resp.status == 200:
                result = await resp.json()
                print(f"[init] Lobby initialized: {result}")
            else:
                text = await resp.text()
                print(f"[init] Init failed ({resp.status}): {text}")


async def read_messages(name: str, socket: websockets.WebSocketClientProtocol) -> None:
    try:
        while True:
            raw = await socket.recv()
            payload = json.loads(raw)
            print(f"[{name}] <- {json.dumps(payload, ensure_ascii=False)}")
    except websockets.ConnectionClosed:
        return


async def run_smoke_test(base_ws_url: str, base_http_url: str, lobby_id: str) -> None:
    url_player_1 = build_url(base_ws_url, lobby_id, user_id="1", role="Student")
    url_player_2 = build_url(base_ws_url, lobby_id, user_id="2", role="Student")

    async with websockets.connect(url_player_1) as ws1, websockets.connect(url_player_2) as ws2:
        reader_1 = asyncio.create_task(read_messages("P1", ws1))
        reader_2 = asyncio.create_task(read_messages("P2", ws2))

        # Give the server a short moment to send initial state.
        await asyncio.sleep(0.5)

        # Manually initialize the lobby with the connected players.
        await init_lobby(base_http_url, lobby_id)
        await asyncio.sleep(0.5)

        buy_item_event = {
            "event": "buy_item",
            "data": {
                "item_id": "Mask",
                "cost": 10,
            },
        }
        await ws1.send(json.dumps(buy_item_event))

        await asyncio.sleep(0.5)

        request_trade_event = {
            "event": "request_trade",
            "data": {
                "with_player_id": "2",
                "item_id": "Mask",
            },
        }
        await ws1.send(json.dumps(request_trade_event))

        await asyncio.sleep(2.0)

        reader_1.cancel()
        reader_2.cancel()


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="BatangAware websocket smoke test client")
    parser.add_argument("--base-ws-url", default="ws://127.0.0.1:8000", help="Base websocket URL")
    parser.add_argument("--lobby-id", default="lobby-1", help="Lobby identifier")
    return parser.parse_args(argv)


def main() -> None:
    args = parse_args()
    # Derive HTTP URL from WS URL (e.g., ws://127.0.0.1:8000 -> http://127.0.0.1:8000)
    base_http_url = args.base_ws_url.replace("ws://", "http://").replace("wss://", "https://")
    asyncio.run(run_smoke_test(args.base_ws_url, base_http_url, args.lobby_id))


if __name__ == "__main__":
    main()
