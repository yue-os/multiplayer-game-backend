from __future__ import annotations

import asyncio
import time
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
from app.cache.game_state_cache import GameStateCache
from app.cache.redis_client import get_redis


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
    subscription_task: asyncio.Task[None] | None = None
    round_started_at: float = Field(default_factory=time.monotonic)
    round_duration_seconds: float = 60.0


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
            # Try to load existing state from Redis
            cached_state = GameStateCache.load_state(lobby_id)
            if cached_state:
                game_state = GameState.model_validate(cached_state)
            else:
                game_state = GameState(lobby_id=lobby_id, current_event=LocationEvent.SCHOOL, lockdown_meter=0)
            
            lobby_runtime = LobbyRuntime(game_state=game_state, engine=GameEngine(game_state))
            self._lobbies[lobby_id] = lobby_runtime

        # Ensure the lobby starts at round 1 and reset the round timer anchor
        lobby_runtime.game_state.current_round = max(1, int(lobby_runtime.game_state.current_round))
        lobby_runtime.round_started_at = time.monotonic()

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
            
            # Assign initial infected player if we have enough players and no one is infected yet
            # Never make a Doctor the infected player (doctors are immune)
            if not any(p.is_carrier for p in lobby_runtime.game_state.players):
                # Find non-doctor players to be patient zero
                non_doctor_players = [
                    p for p in lobby_runtime.game_state.players
                    if p.visible_role != VisibleRole.DOCTOR
                ]
                if non_doctor_players:
                    # Use deterministic selection based on player count for consistency
                    import random
                    patient_zero = non_doctor_players[0]  # First non-doctor becomes patient zero
                    patient_zero.is_carrier = True
                    print(f"[Relay] Assigned {patient_zero.player_id} ({patient_zero.visible_role}) as patient zero")

        # Save state to Redis and publish update
        GameStateCache.save_state(lobby_id, lobby_runtime.game_state.model_dump())
        redis_client = get_redis()
        if redis_client:
            await redis_client.publish(f"lobby:{lobby_id}:events", "update")

        # Start synchronization listener if not already running
        if lobby_runtime.subscription_task is None or lobby_runtime.subscription_task.done():
            lobby_runtime.subscription_task = asyncio.create_task(self._listen_for_updates(lobby_id))

        if lobby_runtime.timer_task is None or lobby_runtime.timer_task.done():
            lobby_runtime.timer_task = asyncio.create_task(self.start_event_timer(lobby_id))

        # Send initial authoritative game state immediately so clients see round 1
        await self.broadcast_game_state(lobby_id)

        return player_id

    def disconnect_from_lobby(self, lobby_id: str, player_id: str) -> None:
        lobby_connections = self._connections.get(lobby_id)
        if lobby_connections is None:
            return

        lobby_connections.pop(player_id, None)
        if not lobby_connections:
            self._cleanup_lobby(lobby_id)

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

    async def broadcast_game_state(self, lobby_id: str, game_over: bool = False, scores: list[dict] | None = None) -> None:
        # Reload state from Redis to ensure we are authoritative
        cached_state = GameStateCache.load_state(lobby_id)
        if cached_state:
            runtime_obj = self._lobbies.get(lobby_id)
            if runtime_obj:
                runtime_obj.game_state = GameState.model_validate(cached_state)
        
        runtime = self._get_lobby_runtime_or_raise(lobby_id)
        lobby_connections = self._connections.get(lobby_id, {})
        if not lobby_connections:
            return

        players_public = [self._public_player_payload(player) for player in runtime.game_state.players]
        now = time.monotonic()
        elapsed = max(0.0, now - runtime.round_started_at)
        timer_left = max(0.0, runtime.round_duration_seconds - elapsed)

        for recipient_id, recipient_socket in list(lobby_connections.items()):
            recipient = self._find_player(runtime.game_state, recipient_id)
            if recipient is None:
                continue

            data: dict[str, object] = {
                "lobby_id": lobby_id,
                "current_event": runtime.game_state.current_event.value,
                "lockdown_meter": runtime.game_state.lockdown_meter,
                "round": runtime.game_state.current_round,
                "max_rounds": runtime.game_state.max_rounds,
                "server_time": time.time(),
                "round_duration_seconds": runtime.round_duration_seconds,
                "round_timer_left": timer_left,
                "public_players": players_public,
                "you": self._private_player_payload(recipient),
            }

            if game_over:
                data["game_over"] = True
                data["scores"] = scores or []

            await recipient_socket.send_json({"event": "game_state", "data": data})

    async def start_event_timer(self, lobby_id: str) -> None:
        iteration_count = 0
        while True:
            try:
                await asyncio.sleep(1)

                if lobby_id not in self._lobbies:
                    print(f"[Timer] Lobby {lobby_id} no longer exists, exiting timer.")
                    return
                if not self._connections.get(lobby_id):
                    print(f"[Timer] No connections in lobby {lobby_id}, exiting timer.")
                    return

                runtime = self._lobbies[lobby_id]
                if time.monotonic() < runtime.round_started_at + runtime.round_duration_seconds:
                    await self.broadcast_game_state(lobby_id)
                    continue

                runtime.round_started_at = time.monotonic()
                runtime.game_state.current_round += 1
                current_round = runtime.game_state.current_round
                max_rounds = runtime.game_state.max_rounds
                iteration_count += 1

                print(f"[Timer] Lobby {lobby_id} - Round {current_round}/{max_rounds}")

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
                            "round": current_round,
                            "max_rounds": max_rounds,
                            "server_time": time.time(),
                            "round_duration_seconds": runtime.round_duration_seconds,
                            "round_timer_left": runtime.round_duration_seconds,
                        },
                    },
                )

                if current_round >= max_rounds:
                    print(f"[Timer] Lobby {lobby_id} game over at round {current_round}. Computing scores...")
                    scores = runtime.engine.compute_scores()
                    print(f"[Timer] Lobby {lobby_id} scores computed: {scores}")
                    await self.broadcast_game_state(lobby_id, game_over=True, scores=scores)
                    print(f"[Timer] Lobby {lobby_id} game_over broadcast sent. Cleaning up...")
                    self._cleanup_lobby(lobby_id)
                    print(f"[Timer] Lobby {lobby_id} cleaned up. Timer exiting.")
                    return

                await self.broadcast_game_state(lobby_id)
            except Exception as e:
                print(f"[Timer] ERROR in lobby {lobby_id} at iteration {iteration_count}: {e}")
                import traceback
                traceback.print_exc()
                self._cleanup_lobby(lobby_id)
                return

    async def _listen_for_updates(self, lobby_id: str) -> None:
        redis_client = get_redis()
        if not redis_client:
            return

        pubsub = redis_client.pubsub()
        channel = f"lobby:{lobby_id}:events"
        await pubsub.subscribe(channel)
        
        try:
            while True:
                # Use a small timeout to allow checking for cancellation
                message = await pubsub.get_message(ignore_subscribe_metadata=True, timeout=1.0)
                if message:
                    # State updated on another worker, reload and broadcast
                    await self.broadcast_game_state(lobby_id)
                await asyncio.sleep(0.1) # Yield control
        except asyncio.CancelledError:
            await pubsub.unsubscribe(channel)
            raise
        except Exception as e:
            print(f"[Sync] Error in listener for {lobby_id}: {e}")
        finally:
            await pubsub.close()

    def _cleanup_lobby(self, lobby_id: str) -> None:
        runtime = self._lobbies.pop(lobby_id, None)
        if runtime is not None and runtime.timer_task is not None:
            runtime.timer_task.cancel()
        self._connections.pop(lobby_id, None)

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


@router.get("/lobby/list")
async def list_ws_lobbies() -> list[dict]:
    """
    Return a list of currently active websocket lobbies managed in memory.
    Each entry mirrors the `/server/list` shape enough for the Godot client
    to display and join a relay-hosted lobby.
    """
    result: list[dict] = []
    now = time.time()

    # Build entries from in-memory hub state
    for lobby_id, runtime in socket_hub._lobbies.items():
        connections = socket_hub._connections.get(lobby_id, {})
        current_players = len(connections)
        required_players = int(runtime.round_duration_seconds) if runtime is not None else 2

        result.append(
            {
                "ip": "",
                "port": 0,
                "name": f"{lobby_id}",
                "count": current_players,
                "persistent": False,
                "teacher_lobby": False,
                "online": True,
                "joinable": True,
                "current_players": current_players,
                "required_players": required_players,
                "started": False,
                "status": "Relay",
                "websocket": True,
                "lobby_id": lobby_id,
            }
        )

    return result
