from __future__ import annotations

from dataclasses import asdict
from enum import Enum
from typing import Any

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect, status
from pydantic import BaseModel, Field, ValidationError

from app.auth.auth_handler import decodeJWT
from app.server.services.game_logic_service import GameManager


class InitLobbyRequest(BaseModel):
    lobby_id: str


class GamePhase(str, Enum):
    ERRAND = "Errand Phase"
    INTERACTION = "Interaction Phase"
    ACTIVITY_LOG = "Activity Log Phase"
    CLINIC_VOTING = "Clinic Voting Phase"


class SocketEnvelope(BaseModel):
    event: str
    data: dict[str, Any] = Field(default_factory=dict)


class TradeRequest(BaseModel):
    with_player_id: str
    item_id: str


class BuyItemRequest(BaseModel):
    item_id: str
    cost: int = Field(gt=0)


class LobbySocketHub:
    def __init__(self) -> None:
        self._connections: dict[str, dict[str, WebSocket]] = {}
        self._games: dict[str, GameManager] = {}
        self._phases: dict[str, GamePhase] = {}

    async def connect_to_lobby(self, lobby_id: str, player_token: str, websocket: WebSocket) -> str:
        player_id = self._parse_player_token(player_token)
        await websocket.accept()

        lobby_connections = self._connections.setdefault(lobby_id, {})
        lobby_connections[player_id] = websocket

        game_manager = self._games.setdefault(lobby_id, GameManager(lobby_id=lobby_id))
        self._phases.setdefault(lobby_id, GamePhase.ERRAND)

        # Auto-initialize when exactly 10 players are connected.
        if len(lobby_connections) == GameManager.REQUIRED_PLAYERS and not game_manager.players:
            game_manager.initialize_game(list(lobby_connections.keys()))

        return player_id

    def disconnect_from_lobby(self, lobby_id: str, player_id: str) -> None:
        lobby_connections = self._connections.get(lobby_id)
        if lobby_connections is None:
            return

        lobby_connections.pop(player_id, None)
        if not lobby_connections:
            self._connections.pop(lobby_id, None)
            self._games.pop(lobby_id, None)
            self._phases.pop(lobby_id, None)

    async def handle_trade(self, lobby_id: str, player_id: str, payload: dict[str, Any]) -> None:
        game_manager = self._get_lobby_game_or_raise(lobby_id)

        try:
            trade = TradeRequest.model_validate(payload)
        except ValidationError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid trade payload: {exc.errors()}",
            ) from exc

        result = game_manager.process_trade(
            player_a_id=player_id,
            player_b_id=trade.with_player_id,
            item_id=trade.item_id,
        )

        await self._send_to_player(
            lobby_id=lobby_id,
            player_id=player_id,
            message={
                "event": "trade_processed",
                "data": asdict(result),
            },
        )

        await self.broadcast_game_state(lobby_id)

    async def handle_buy_item(self, lobby_id: str, player_id: str, payload: dict[str, Any]) -> None:
        game_manager = self._get_lobby_game_or_raise(lobby_id)

        try:
            purchase = BuyItemRequest.model_validate(payload)
        except ValidationError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid buy_item payload: {exc.errors()}",
            ) from exc

        if purchase.item_id not in GameManager.ITEM_POOL:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Unknown shop item '{purchase.item_id}'.",
            )

        player_state = game_manager.players.get(player_id)
        if player_state is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Player '{player_id}' is not part of lobby '{lobby_id}'.",
            )

        if player_state.coins < purchase.cost:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Insufficient coins for this purchase.",
            )

        player_state.coins -= purchase.cost
        player_state.inventory.append(purchase.item_id)

        await self._send_to_player(
            lobby_id=lobby_id,
            player_id=player_id,
            message={
                "event": "item_purchased",
                "data": {
                    "player_id": player_id,
                    "item_id": purchase.item_id,
                    "cost": purchase.cost,
                    "remaining_coins": player_state.coins,
                },
            },
        )

        await self.broadcast_game_state(lobby_id)

    async def broadcast_game_state(self, lobby_id: str) -> None:
        game_manager = self._get_lobby_game_or_raise(lobby_id)
        lobby_connections = self._connections.get(lobby_id, {})
        if not lobby_connections:
            return

        # If game is not initialized, send a waiting state instead.
        if not game_manager.players:
            for recipient_id, recipient_socket in list(lobby_connections.items()):
                payload = {
                    "event": "game_state",
                    "data": {
                        "lobby_id": lobby_id,
                        "phase": "WAITING",
                        "activity_log": [],
                        "public": {
                            "player_count": 0,
                            "connected_count": len(lobby_connections),
                            "players": [],
                        },
                        "private": {
                            "player_id": recipient_id,
                            "role": "Unknown",
                            "coins": 0,
                            "inventory": [],
                            "checklist": [],
                        },
                    },
                }
                await recipient_socket.send_json(payload)
            return

        phase = self._phases.get(lobby_id, GamePhase.ERRAND).value
        activity_log = game_manager.generate_activity_log()
        players_public = [
            {
                "player_id": player.player_id,
                "role": player.role,
                "coins": player.coins,
                "inventory_count": len(player.inventory),
                "checklist_completed": sum(1 for item in player.checklist if item in player.inventory),
                "checklist_total": len(player.checklist),
            }
            for player in game_manager.players.values()
        ]

        for recipient_id, recipient_socket in list(lobby_connections.items()):
            recipient = game_manager.players.get(recipient_id)
            if recipient is None:
                continue

            private_view: dict[str, Any] = {
                "player_id": recipient.player_id,
                "role": recipient.role,
                "coins": recipient.coins,
                "inventory": list(recipient.inventory),
                "checklist": list(recipient.checklist),
            }

            # Infection status is only visible to the infected player themselves.
            if recipient.is_infected:
                private_view["is_infected"] = True

            payload = {
                "event": "game_state",
                "data": {
                    "lobby_id": lobby_id,
                    "phase": phase,
                    "activity_log": activity_log,
                    "public": {
                        "player_count": len(game_manager.players),
                        "connected_count": len(lobby_connections),
                        "players": players_public,
                    },
                    "private": private_view,
                },
            }

            await recipient_socket.send_json(payload)

    def _parse_player_token(self, player_token: str) -> str:
        if player_token.strip() == "":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="player_token is required.",
            )

        token = player_token.replace("Bearer ", "", 1).strip()
        decoded = decodeJWT(token)
        if not decoded or "user_id" not in decoded:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired player token.",
            )

        player_id = str(decoded["user_id"]).strip()
        if player_id == "":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token is missing a valid user_id.",
            )
        return player_id

    def _get_lobby_game_or_raise(self, lobby_id: str) -> GameManager:
        game_manager = self._games.get(lobby_id)
        if game_manager is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Lobby '{lobby_id}' does not exist.",
            )
        return game_manager

    async def _send_to_player(self, lobby_id: str, player_id: str, message: dict[str, Any]) -> None:
        lobby_connections = self._connections.get(lobby_id, {})
        websocket = lobby_connections.get(player_id)
        if websocket is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Player '{player_id}' is not currently connected.",
            )
        await websocket.send_json(message)


router = APIRouter(prefix="/ws", tags=["game-sockets"])
socket_hub = LobbySocketHub()


@router.post("/lobby/{lobby_id}/init")
async def init_lobby_manual(lobby_id: str, required_players: int | None = None) -> dict[str, object]:
    """
    Manually initialize a lobby with connected players (useful for testing).
    Optional query parameter: required_players (defaults to 10).
    """
    game_manager = socket_hub._games.get(lobby_id)
    if game_manager is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Lobby '{lobby_id}' does not exist or has no connections.",
        )

    if game_manager.players:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Lobby is already initialized.",
        )

    lobby_connections = socket_hub._connections.get(lobby_id, {})
    if not lobby_connections:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No players are connected to this lobby.",
        )

    # If required_players not specified, default to 10.
    if required_players is None:
        required_players = 10

    # Update the game manager's required player count if needed.
    if required_players != game_manager.REQUIRED_PLAYERS:
        game_manager.REQUIRED_PLAYERS = required_players

    player_ids = list(lobby_connections.keys())
    init_result = game_manager.initialize_game(player_ids)
    
    # Broadcast the initialized state to all connected players.
    await socket_hub.broadcast_game_state(lobby_id)
    
    return init_result


@router.websocket("/lobby/{lobby_id}")
async def connect_to_lobby(websocket: WebSocket, lobby_id: str, player_token: str) -> None:
    try:
        player_id = await socket_hub.connect_to_lobby(lobby_id, player_token, websocket)
        await socket_hub.broadcast_game_state(lobby_id)

        while True:
            incoming = await websocket.receive_json()
            envelope = SocketEnvelope.model_validate(incoming)

            if envelope.event == "request_trade":
                await socket_hub.handle_trade(lobby_id, player_id, envelope.data)
            elif envelope.event == "buy_item":
                await socket_hub.handle_buy_item(lobby_id, player_id, envelope.data)
            else:
                await websocket.send_json(
                    {
                        "event": "error",
                        "data": {
                            "detail": f"Unsupported event '{envelope.event}'.",
                            "supported_events": ["request_trade", "buy_item"],
                        },
                    }
                )

    except WebSocketDisconnect:
        socket_hub.disconnect_from_lobby(lobby_id, locals().get("player_id", ""))
    except HTTPException as exc:
        await websocket.send_json(
            {
                "event": "error",
                "data": {
                    "status_code": exc.status_code,
                    "detail": exc.detail,
                },
            }
        )
        socket_hub.disconnect_from_lobby(lobby_id, locals().get("player_id", ""))
        await websocket.close(code=1008)
    except ValidationError as exc:
        await websocket.send_json(
            {
                "event": "error",
                "data": {
                    "status_code": status.HTTP_400_BAD_REQUEST,
                    "detail": f"Invalid message: {exc.errors()}",
                },
            }
        )
    except Exception:
        await websocket.send_json(
            {
                "event": "error",
                "data": {
                    "status_code": status.HTTP_500_INTERNAL_SERVER_ERROR,
                    "detail": "Unexpected socket server error.",
                },
            }
        )
        socket_hub.disconnect_from_lobby(lobby_id, locals().get("player_id", ""))
        await websocket.close(code=1011)


async def broadcast_game_state(lobby_id: str) -> None:
    await socket_hub.broadcast_game_state(lobby_id)
