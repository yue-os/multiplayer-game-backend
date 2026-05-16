# Redis Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move all transient state (OTPs, Notifications, Lobby State) from in-memory to Redis for multi-worker consistency.

**Architecture:** Approach 1 - Update existing cache utilities to handle complex state and use Redis Pub/Sub for WebSocket synchronization.

**Tech Stack:** Python, Flask, Redis, Pytest.

---

### Task 1: Registration OTPs Utility

**Files:**
- Modify: `app/cache/session_cache.py`
- Test: `tests/cache/test_registration_cache.py`

- [ ] **Step 1: Add RegistrationCache to session_cache.py**
```python
class RegistrationCache:
    """Cache for pending registrations (OTPs)"""
    @staticmethod
    def set_pending(email: str, data: dict, expiry: int = 600):
        redis = get_redis()
        if not redis: return False
        try:
            key = f"registration:otp:{email.lower()}"
            redis.setex(key, expiry, json.dumps(data))
            return True
        except Exception: return False

    @staticmethod
    def get_pending(email: str) -> dict:
        redis = get_redis()
        if not redis: return None
        try:
            key = f"registration:otp:{email.lower()}"
            data = redis.get(key)
            return json.loads(data) if data else None
        except Exception: return None

    @staticmethod
    def delete_pending(email: str):
        redis = get_redis()
        if not redis: return False
        try:
            key = f"registration:otp:{email.lower()}"
            redis.delete(key)
            return True
        except Exception: return False
```

- [ ] **Step 2: Create unit test**
```python
from app.cache.session_cache import RegistrationCache
import time

def test_registration_cache_flow():
    email = "test@example.com"
    data = {"username": "tester", "otp": "123456"}
    assert RegistrationCache.set_pending(email, data) is True
    assert RegistrationCache.get_pending(email) == data
    assert RegistrationCache.delete_pending(email) is True
    assert RegistrationCache.get_pending(email) is None
```

- [ ] **Step 3: Run test**
Run: `pytest tests/cache/test_registration_cache.py`

- [ ] **Step 4: Commit**
```bash
git add app/cache/session_cache.py tests/cache/test_registration_cache.py
git commit -m "feat(redis): add RegistrationCache utility"
```

---

### Task 2: Integrate RegistrationCache into User Routes

**Files:**
- Modify: `app/server/routes/user.py`

- [ ] **Step 1: Replace PENDING_REGISTRATIONS with RegistrationCache**
Modify `/auth/register` and `/auth/verify-otp` to use `RegistrationCache`.

- [ ] **Step 2: Manual verification**
Use `curl` or a test script to trigger registration and check Redis via `redis-cli KEYS "registration:otp:*"`

- [ ] **Step 3: Commit**
```bash
git add app/server/routes/user.py
git commit -m "feat(redis): move PENDING_REGISTRATIONS to Redis"
```

---

### Task 3: Seen Notifications Utility

**Files:**
- Create: `app/cache/notification_cache.py`
- Test: `tests/cache/test_notification_cache.py`

- [ ] **Step 1: Create NotificationCache**
```python
from app.cache.redis_client import get_redis
import os

SESSION_EXPIRY = int(os.getenv("SESSION_EXPIRY", 86400))

class NotificationCache:
    """Tracks seen notifications per user"""
    @staticmethod
    def mark_as_seen(user_id: str, notification_type: str, target_id: str):
        redis = get_redis()
        if not redis: return False
        try:
            key = f"seen_notifications:{user_id}"
            value = f"{notification_type}:{target_id}"
            redis.sadd(key, value)
            redis.expire(key, SESSION_EXPIRY)
            return True
        except Exception: return False

    @staticmethod
    def is_seen(user_id: str, notification_type: str, target_id: str) -> bool:
        redis = get_redis()
        if not redis: return False
        try:
            key = f"seen_notifications:{user_id}"
            value = f"{notification_type}:{target_id}"
            return redis.sismember(key, value)
        except Exception: return False
```

- [ ] **Step 2: Create unit test**
```python
from app.cache.notification_cache import NotificationCache

def test_notification_cache():
    uid, ntype, nid = "user123", "announcement", "456"
    assert NotificationCache.is_seen(uid, ntype, nid) is False
    assert NotificationCache.mark_as_seen(uid, ntype, nid) is True
    assert NotificationCache.is_seen(uid, ntype, nid) is True
```

- [ ] **Step 3: Run test**
Run: `pytest tests/cache/test_notification_cache.py`

- [ ] **Step 4: Commit**
```bash
git add app/cache/notification_cache.py tests/cache/test_notification_cache.py
git commit -m "feat(redis): add NotificationCache utility"
```

---

### Task 4: Integrate NotificationCache into User Routes

**Files:**
- Modify: `app/server/routes/user.py`

- [ ] **Step 1: Replace SEEN_NOTIFICATIONS with NotificationCache**
Modify `get_student_notifications` route.

- [ ] **Step 2: Commit**
```bash
git add app/server/routes/user.py
git commit -m "feat(redis): move SEEN_NOTIFICATIONS to Redis"
```

---

### Task 5: WebSocket Lobby Pub/Sub Synchronization

**Files:**
- Modify: `app/cache/game_state_cache.py`
- Modify: `app/server/routes/game_sockets.py`

- [ ] **Step 1: Update GameStateCache to support full state**
Add `save_state(lobby_id, state_dict)` and `load_state(lobby_id)`.

- [ ] **Step 2: Implement Pub/Sub in LobbySocketHub**
1. When `connect_to_lobby` occurs, start a background task that `SUBSCRIBE` to `lobby:{lobby_id}:events`.
2. When a local worker processes a trade, `PUBLISH` the event data to Redis.
3. The background task receives the event and calls `broadcast_game_state` locally.

- [ ] **Step 3: Commit**
```bash
git add app/cache/game_state_cache.py app/server/routes/game_sockets.py
git commit -m "feat(redis): implement WebSocket cross-worker sync via Pub/Sub"
```
