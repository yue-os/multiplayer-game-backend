# Design Doc: Redis Integration for Multi-Worker Scalability

**Date:** 2026-05-16
**Status:** Draft
**Topic:** Redis Integration

## 1. Problem Statement
The current backend uses in-memory dictionaries (`PENDING_REGISTRATIONS`, `SEEN_NOTIFICATIONS`, and `LobbySocketHub._lobbies`) to store transient state. When running with multiple Gunicorn workers (as configured in the `Dockerfile` and `docker-compose.yml`), state is not shared between workers. This causes:
- Registration OTP verification to fail if the request hits a different worker.
- Students to see duplicate notifications.
- WebSocket game lobbies to be isolated to a single worker, preventing players on different workers from playing together.

## 2. Goals
- Transition all transient state from in-memory storage to Redis.
- Ensure 100% consistency across multiple Gunicorn workers.
- Improve game reliability by persisting lobby state across backend restarts.

## 3. Architecture

### Phase 1: Registration & OTPs
- **Existing:** `PENDING_REGISTRATIONS` dict in `app/server/routes/user.py`.
- **New:** `RegistrationCache` utility.
- **Key Pattern:** `registration:otp:{email}` (String).
- **TTL:** 10 minutes.
- **Workflow:**
    1. `/auth/register` writes user data to Redis.
    2. `/auth/verify-otp` reads from Redis, creates user in DB, and deletes the Redis key.

### Phase 2: Seen Notifications
- **Existing:** `SEEN_NOTIFICATIONS` dict in `app/server/routes/user.py`.
- **New:** `NotificationCache` utility.
- **Key Pattern:** `seen_notifications:{user_id}` (Set).
- **TTL:** 24 hours (aligned with `SESSION_EXPIRY`).
- **Workflow:**
    1. `get_student_notifications` checks if `announcement:{id}` or `quiz:{id}` is in the Redis Set.
    2. If not seen, it's added to the list and marked as seen in Redis.

### Phase 3: WebSocket Lobbies
- **Existing:** `LobbySocketHub._lobbies` dict in `app/server/routes/game_sockets.py`.
- **New:** Integrated `GameStateCache` and Redis Pub/Sub.
- **Key Pattern:** 
    - State: `lobby:{lobby_id}:state` (Hash/String).
    - Events: `lobby:{lobby_id}:events` (Pub/Sub Channel).
- **Workflow:**
    1. **Connection:** When a player joins, the worker fetches the `GameState` from Redis.
    2. **Updates:** When an action (e.g., trade) occurs:
        - The worker updates the Redis state.
        - The worker publishes the change to the Pub/Sub channel.
    3. **Synchronization:** Every worker running that lobby subscribes to the Pub/Sub channel and broadcasts updates to its local connected clients.

## 4. Components to be Modified

### `app/cache/`
- `redis_client.py`: Ensure connection pool is robust.
- `session_cache.py`: Add `RegistrationCache`.
- `game_state_cache.py`: Extend to support full `GameState` serialization.
- `notification_cache.py`: (New file) Implement `NotificationCache`.

### `app/server/routes/`
- `user.py`: Replace `PENDING_REGISTRATIONS` and `SEEN_NOTIFICATIONS` logic.
- `game_sockets.py`: Refactor `LobbySocketHub` to use Redis as the source of truth and Pub/Sub for messaging.

## 5. Testing Strategy
- **Unit Tests:** Mock Redis to verify cache utility logic.
- **Integration Tests:** Run two instances of the backend and verify state sharing (e.g., register on instance A, verify on instance B).
- **Manual Verification:** Use `redis-cli` to monitor keys during registration and game play.

## 6. Success Criteria
- [ ] Registration works regardless of which worker handles the requests.
- [ ] Notifications only appear once per user session.
- [ ] WebSocket trades are visible to all players in a lobby across all workers.
- [ ] Redis keys are cleaned up automatically via TTL.
