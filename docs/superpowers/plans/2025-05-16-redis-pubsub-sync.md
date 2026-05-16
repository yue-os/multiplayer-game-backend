# Redis Pub/Sub Synchronization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Synchronize WebSocket lobby state across multiple worker processes using Redis Pub/Sub.

**Architecture:** Each worker maintains its own WebSocket connections but treats Redis as the authoritative store for `GameState`. Workers subscribe to a Redis channel for each lobby they manage; when a state change occurs on any worker, it's saved to Redis and published to the channel, triggering all other workers to reload and broadcast the updated state.

**Tech Stack:** FastAPI, Redis, asyncio, Pydantic.

---

### Task 1: Refactor LobbyRuntime and Subscription Setup

**Files:**
- Modify: `app/server/routes/game_sockets.py`

- [ ] **Step 1: Add `subscription_task` to `LobbyRuntime`**

```python
class LobbyRuntime(BaseModel):
    model_config = {"arbitrary_types_allowed": True}

    game_state: GameState
    engine: GameEngine
    timer_task: asyncio.Task[None] | None = None
    subscription_task: asyncio.Task[None] | None = None  # Add this
    round_started_at: float = Field(default_factory=time.monotonic)
    round_duration_seconds: float = 60.0
```

- [ ] **Step 2: Update `connect_to_lobby` to load/save state and start listener**

```python
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

        # ... (player initialization logic) ...

        # Ensure the lobby starts at round 1 and reset the round timer anchor
        lobby_runtime.game_state.current_round = max(1, int(lobby_runtime.game_state.current_round))
        lobby_runtime.round_started_at = time.monotonic()

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

        # Send initial authoritative game state immediately
        await self.broadcast_game_state(lobby_id)

        return player_id
```

- [ ] **Step 3: Commit**
```bash
git add app/server/routes/game_sockets.py
git commit -m "refactor(ws): update LobbyRuntime and connect_to_lobby for Redis sync"
```

### Task 2: Implement Pub/Sub Listener and Authoritative Broadcast

**Files:**
- Modify: `app/server/routes/game_sockets.py`

- [ ] **Step 1: Implement `_listen_for_updates`**

```python
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
        except asyncio.CancelledError:
            await pubsub.unsubscribe(channel)
            raise
        except Exception as e:
            print(f"[Sync] Error in listener for {lobby_id}: {e}")
        finally:
            await pubsub.close()
```

- [ ] **Step 2: Update `broadcast_game_state` to reload from Redis**

```python
    async def broadcast_game_state(self, lobby_id: str, game_over: bool = False, scores: list[dict] | None = None) -> None:
        # Reload state from Redis to ensure we are authoritative
        cached_state = GameStateCache.load_state(lobby_id)
        if cached_state:
            runtime = self._lobbies.get(lobby_id)
            if runtime:
                runtime.game_state = GameState.model_validate(cached_state)
        
        runtime = self._get_lobby_runtime_or_raise(lobby_id)
        # ... (rest of broadcast logic) ...
```

- [ ] **Step 3: Commit**
```bash
git add app/server/routes/game_sockets.py
git commit -m "feat(ws): implement Redis Pub/Sub listener and authoritative broadcast"
```

### Task 3: Update State Modifiers (Trade and Timer)

**Files:**
- Modify: `app/server/routes/game_sockets.py`

- [ ] **Step 1: Update `handle_trade` to save and publish**

```python
    async def handle_trade(self, lobby_id: str, player_id: str, payload: dict[str, Any]) -> None:
        runtime = self._get_lobby_runtime_or_raise(lobby_id)
        # ... (trade logic) ...
        runtime.engine.process_trade(...)

        # Save state and publish update
        GameStateCache.save_state(lobby_id, runtime.game_state.model_dump())
        redis_client = get_redis()
        if redis_client:
            await redis_client.publish(f"lobby:{lobby_id}:events", "update")
            
        # ... (send individual trade results) ...
```

- [ ] **Step 2: Update `start_event_timer` to save and publish**

```python
    async def start_event_timer(self, lobby_id: str) -> None:
        # ... (loop and round rotation logic) ...
        runtime.game_state.current_round += 1
        # ...
        announcement = runtime.engine.rotate_event()
        
        # Save state and publish update after round rotation
        GameStateCache.save_state(lobby_id, runtime.game_state.model_dump())
        redis_client = get_redis()
        if redis_client:
            await redis_client.publish(f"lobby:{lobby_id}:events", "update")
        
        # ...
```

- [ ] **Step 3: Update `_cleanup_lobby` to cancel both tasks**

```python
    def _cleanup_lobby(self, lobby_id: str) -> None:
        runtime = self._lobbies.pop(lobby_id, None)
        if runtime is not None:
            if runtime.timer_task is not None:
                runtime.timer_task.cancel()
            if runtime.subscription_task is not None:
                runtime.subscription_task.cancel()
        self._connections.pop(lobby_id, None)
```

- [ ] **Step 4: Commit**
```bash
git add app/server/routes/game_sockets.py
git commit -m "feat(ws): synchronize trade and timer events via Redis"
```

### Task 4: Final Review and Manual Verification

**Files:**
- `app/server/routes/game_sockets.py`

- [ ] **Step 1: Verify all modifications match the design**
Check `connect_to_lobby`, `handle_trade`, `start_event_timer`, `_listen_for_updates`, `broadcast_game_state`, and `_cleanup_lobby`.

- [ ] **Step 2: Check for missing imports**
Ensure `GameStateCache`, `get_redis`, and `GameState` are correctly used.

- [ ] **Step 3: Run existing tests (if any)**
Check `tests/` for related WebSocket tests.

- [ ] **Step 4: Final commit and push**
```bash
git add app/server/routes/game_sockets.py
git commit -m "feat(redis): synchronize WebSocket lobbies via Pub/Sub"
```
