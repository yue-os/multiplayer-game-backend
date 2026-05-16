# Design Doc: Redis Pub/Sub Synchronization for WebSocket Lobbies

## Status
Proposed

## Context
Currently, WebSocket lobbies are managed in-memory within each worker process. In a multi-worker environment, state changes (like trades or timer updates) on one worker are not reflected for players connected to other workers. We need a way to synchronize game state across all workers using Redis as the authoritative store and Pub/Sub for real-time updates.

## Goals
- Ensure all players in a lobby see the same game state regardless of which worker they are connected to.
- Use Redis as the single source of truth for `GameState`.
- Minimize latency for state updates across workers.

## Architecture

### 1. Redis State Persistence
The `GameStateCache` class provides `save_state` and `load_state` methods. These will be used to persist the `GameState` model.
- **Serialization:** `game_state.model_dump()`
- **Deserialization:** `GameState.model_validate(data)`

### 2. Pub/Sub Synchronization
Each lobby will have a dedicated Redis channel: `lobby:{lobby_id}:events`.

- **Event Producer:** Whenever a worker modifies the game state (player joins, trade occurs, round rotates), it must:
    1. Call `GameStateCache.save_state(lobby_id, state_dict)`.
    2. Call `redis_client.publish(f"lobby:{lobby_id}:events", "update")`.

- **Event Consumer:** Each worker managing connections for a lobby will run a background task:
    1. Subscribe to `lobby:{lobby_id}:events`.
    2. On message:
        - Load authoritative state from Redis.
        - Update local `LobbyRuntime`.
        - Broadcast the new state to all locally connected WebSockets.

### 3. Updated Components in `LobbySocketHub`

#### LobbyRuntime
- New field: `subscription_task: asyncio.Task[None] | None`.

#### connect_to_lobby
- Check Redis for existing state.
- If missing, initialize and save.
- If present, load and validate.
- Start `_listen_for_updates(lobby_id)` if it doesn't exist.
- Save state and publish "join" event to Redis.

#### handle_trade
- Reload state from Redis (or rely on Pub/Sub).
- Process trade via `GameEngine`.
- Save state to Redis.
- Publish "trade" event to Redis.

#### _listen_for_updates(lobby_id)
- Async loop using `redis_client.pubsub()`.
- Await `pubsub.get_message()`.
- Refresh state and broadcast on update.

#### broadcast_game_state
- Load state from Redis before broadcasting to ensures it is authoritative.

#### _cleanup_lobby
- Cancel `timer_task`.
- Cancel `subscription_task`.

## Alternatives Considered

### Approach 1: Polling Redis
Workers could poll Redis every second for state changes.
- **Pros:** Simple to implement.
- **Cons:** High latency (up to 1s) and high Redis load.

### Approach 2: Sticky Sessions
Force all players for a lobby to the same worker.
- **Pros:** No synchronization needed.
- **Cons:** Complex load balancer configuration; doesn't scale well for very large lobbies; harder to maintain high availability.

## Testing Strategy
- **Unit Tests:** Mock Redis Pub/Sub and verify that publishing an event triggers a broadcast on the "subscribing" hub.
- **Integration Tests:** Run two instances of the `LobbySocketHub` (simulating two workers) and verify that a trade on one is reflected in the broadcast of the other.
