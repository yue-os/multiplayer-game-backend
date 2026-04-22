from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect, status
from pydantic import BaseModel, Field, ValidationError

from app.auth.auth_handler import decodeJWT
from app.server.models.game_models import (
    GameState,
    HealthStatus,
    ItemType,
    LocationEvent,
    PlayerState,
    VisibleRole,
)
from app.server.services.game_logic import GameEngine


class SocketEnvelope(BaseModel):
    event: str
    data: dict[str, Any] = Field(default_factory=dict)


class TradeRequest(BaseModel):
    with_player_id: str = Field(min_length=1)
    items_offered_a: dict[str, int] = Field(default_factory=dict)
    items_offered_b: dict[str, int] = Field(default_factory=dict)


class LobbyRuntime(BaseModel):
    model_config = {"arbitrary_types_allowed": True}

    game_state: GameState
    engine: GameEngine
    timer_task: asyncio.Task[None] | None = None


class LobbySocketHub:
    def __init__(self) -> None:
        self._connections: dict[str, dict[str, WebSocket]] = {}
        self._lobbies: dict[str, LobbyRuntime] = {}

    async def connect_to_lobby(self, lobby_id: str, player_token: str, websocket: WebSocket) -> str:
        auth_payload = self._parse_player_token(player_token)
        player_id = auth_payload["player_id"]
        visible_role = self._map_claim_role(auth_payload["role"])

        await websocket.accept()

        lobby_connections = self._connections.setdefault(lobby_id, {})
        lobby_connections[player_id] = websocket

        lobby_runtime = self._lobbies.get(lobby_id)
        if lobby_runtime is None:
            game_state = GameState(lobby_id=lobby_id, current_event=LocationEvent.SCHOOL, lockdown_meter=0)
            lobby_runtime = LobbyRuntime(game_state=game_state, engine=GameEngine(game_state))
            self._lobbies[lobby_id] = lobby_runtime

        player_state = self._find_player(lobby_runtime.game_state, player_id)
        if player_state is None:
            player_state = PlayerState(
                player_id=player_id,
                visible_role=visible_role,
                inventory={
                    ItemType.SNACKS: 1,
                    ItemType.MASKS: 1,
                },
                health_status=HealthStatus.HEALTHY,
            )
            lobby_runtime.game_state.players.append(player_state)

        if lobby_runtime.timer_task is None or lobby_runtime.timer_task.done():
            lobby_runtime.timer_task = asyncio.create_task(self.start_event_timer(lobby_id))

        return player_id

    def disconnect_from_lobby(self, lobby_id: str, player_id: str) -> None:
        lobby_connections = self._connections.get(lobby_id)
        if lobby_connections is None:
            return

        lobby_connections.pop(player_id, None)
        if not lobby_connections:
            self._connections.pop(lobby_id, None)
            runtime = self._lobbies.pop(lobby_id, None)
            if runtime is not None and runtime.timer_task is not None:
                runtime.timer_task.cancel()

    async def handle_trade(self, lobby_id: str, player_id: str, payload: dict[str, Any]) -> None:
        runtime = self._get_lobby_runtime_or_raise(lobby_id)

        try:
            trade = TradeRequest.model_validate(payload)
        except ValidationError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid trade payload: {exc.errors()}",
            ) from exc

        player_a = self._find_player(runtime.game_state, player_id)
        if player_a is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Player '{player_id}' not found in lobby '{lobby_id}'.",
            )

        player_b = self._find_player(runtime.game_state, trade.with_player_id)
        if player_b is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Player '{trade.with_player_id}' not found in lobby '{lobby_id}'.",
            )

        items_offered_a = self._parse_trade_items(trade.items_offered_a)
        items_offered_b = self._parse_trade_items(trade.items_offered_b)

        runtime.engine.process_trade(
            player_a=player_a,
            player_b=player_b,
            items_offered_a=items_offered_a,
            items_offered_b=items_offered_b,
        )

        await self._send_to_player(
            lobby_id=lobby_id,
            player_id=player_id,
            message={
                "event": "trade_result",
                "data": {
                    "you": self._private_player_payload(player_a),
                    "other_player": self._public_player_payload(player_b),
                },
            },
        )

        await self._send_to_player(
            lobby_id=lobby_id,
            player_id=player_b.player_id,
            message={
                "event": "trade_result",
                "data": {
                    "you": self._private_player_payload(player_b),
                    "other_player": self._public_player_payload(player_a),
                },
            },
        )

    async def broadcast_game_state(self, lobby_id: str) -> None:
        runtime = self._get_lobby_runtime_or_raise(lobby_id)
        lobby_connections = self._connections.get(lobby_id, {})
        if not lobby_connections:
            return

        players_public = [self._public_player_payload(player) for player in runtime.game_state.players]

        for recipient_id, recipient_socket in list(lobby_connections.items()):
            recipient = self._find_player(runtime.game_state, recipient_id)
            if recipient is None:
                continue

            payload = {
                "event": "game_state",
                "data": {
                    "lobby_id": lobby_id,
                    "current_event": runtime.game_state.current_event.value,
                    "lockdown_meter": runtime.game_state.lockdown_meter,
                    "public_players": players_public,
                    "you": self._private_player_payload(recipient),
                },
            }

            await recipient_socket.send_json(payload)

    async def start_event_timer(self, lobby_id: str) -> None:
        while True:
            await asyncio.sleep(60)
            if lobby_id not in self._lobbies:
                return
            if not self._connections.get(lobby_id):
                return

            runtime = self._lobbies[lobby_id]
            announcement = runtime.engine.rotate_event()
            hints = self._build_event_hints(runtime.game_state.current_event)

            await self._broadcast_to_lobby(
                lobby_id,
                {
                    "event": "location_event",
                    "data": {
                        "current_event": runtime.game_state.current_event.value,
                        "announcement": announcement,
                        "hints": hints,
                    },
                },
            )

            await self.broadcast_game_state(lobby_id)

    def _parse_player_token(self, player_token: str) -> dict[str, str]:
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
        role = str(decoded.get("role", "Student")).strip()
        if player_id == "":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token is missing a valid user_id.",
            )
        return {"player_id": player_id, "role": role}

    def _get_lobby_runtime_or_raise(self, lobby_id: str) -> LobbyRuntime:
        runtime = self._lobbies.get(lobby_id)
        if runtime is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Lobby '{lobby_id}' does not exist.",
            )
        return runtime

    async def _send_to_player(self, lobby_id: str, player_id: str, message: dict[str, Any]) -> None:
        lobby_connections = self._connections.get(lobby_id, {})
        websocket = lobby_connections.get(player_id)
        if websocket is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Player '{player_id}' is not currently connected.",
            )
        await websocket.send_json(message)

    async def _broadcast_to_lobby(self, lobby_id: str, message: dict[str, Any]) -> None:
        lobby_connections = self._connections.get(lobby_id, {})
        for socket in list(lobby_connections.values()):
            await socket.send_json(message)

    def _find_player(self, game_state: GameState, player_id: str) -> PlayerState | None:
        for player in game_state.players:
            if player.player_id == player_id:
                return player
        return None

    def _parse_trade_items(self, offered_items: dict[str, int]) -> dict[ItemType, int]:
        parsed: dict[ItemType, int] = {}
        for item_name, count in offered_items.items():
            try:
                item_type = ItemType(item_name)
            except ValueError as exc:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Invalid trade item '{item_name}'.",
                ) from exc

            parsed[item_type] = int(count)
        return parsed

    def _map_claim_role(self, token_role: str) -> VisibleRole:
        role_map: dict[str, VisibleRole] = {
            "Student": VisibleRole.STUDENT,
            "Teacher": VisibleRole.CARETAKER,
            "Parent": VisibleRole.CARETAKER,
            "Admin": VisibleRole.GUARD,
        }
        return role_map.get(token_role, VisibleRole.STUDENT)

    def _public_player_payload(self, player: PlayerState) -> dict[str, Any]:
        return {
            "player_id": player.player_id,
            "visible_role": player.visible_role.value,
            "inventory": {item.value: count for item, count in player.inventory.items()},
            "mission_completed": player.mission_completed,
        }

    def _private_player_payload(self, player: PlayerState) -> dict[str, Any]:
        return {
            "player_id": player.player_id,
            "visible_role": player.visible_role.value,
            "is_carrier": player.is_carrier,
            "health_status": player.health_status.value,
            "inventory": {item.value: count for item, count in player.inventory.items()},
            "mission_completed": player.mission_completed,
        }

    def _build_event_hints(self, current_event: LocationEvent) -> list[str]:
        hints_map: dict[LocationEvent, list[str]] = {
            LocationEvent.SCHOOL: [
                "School protocol active: verify item counts before trading.",
                "Crowd movement is moderate this round.",
            ],
            LocationEvent.PARK: [
                "Open-air advantage: exposure pressure is lower.",
                "Spacing trades out can reduce cumulative risk.",
            ],
            LocationEvent.CANTEEN: [
                "Canteen crowding alert: infection checks are stricter.",
                "Masks have higher tactical value in this event.",
            ],
            LocationEvent.CLINIC: [
                "Clinic event: coordinate medicine exchanges efficiently.",
                "Observe behavior cues before voting phases.",
            ],
            LocationEvent.MARKET: [
                "Market surge: expect more frequent trade opportunities.",
                "Track your mission items to avoid unnecessary risk.",
            ],
        }
        return hints_map[current_event]


router = APIRouter(prefix="/ws", tags=["game-sockets"])
socket_hub = LobbySocketHub()


@router.post("/lobby/{lobby_id}/start_event_timer")
async def start_event_timer(lobby_id: str) -> dict[str, str]:
    runtime = socket_hub._lobbies.get(lobby_id)
    if runtime is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Lobby '{lobby_id}' does not exist.",
        )

    if runtime.timer_task is None or runtime.timer_task.done():
        runtime.timer_task = asyncio.create_task(socket_hub.start_event_timer(lobby_id))

    return {"message": "Event timer started."}


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
            else:
                await websocket.send_json(
                    {
                        "event": "error",
                        "data": {
                            "detail": f"Unsupported event '{envelope.event}'.",
                            "supported_events": ["request_trade"],
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
