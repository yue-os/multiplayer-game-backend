# Redis Setup Guide

## Quick Start with Docker

### 1. Prerequisites
- Docker and Docker Compose installed
- `.env` file configured (copy from `.env.example`)

### 2. Start services
```bash
docker-compose up -d
```

This will start:
- **Redis** on `localhost:6379`
- **PostgreSQL** on `localhost:5432`
- **Backend** on `localhost:5000`

### 3. Verify Redis is running
```bash
docker exec game-redis redis-cli -a changeme ping
# Should return: PONG
```

### 4. Check logs
```bash
docker-compose logs redis     # Redis logs
docker-compose logs backend   # Backend logs
docker-compose logs db        # Database logs
```

## Environment Variables

Update `.env` with your actual values:

```env
REDIS_URL=redis://:password@redis:6379/0
REDIS_PASSWORD=your-strong-password
SESSION_EXPIRY=86400  # 24 hours
```

## Using Redis in Your Code

### Session Management
```python
from app.cache.session_cache import SessionCache

# Store session
SessionCache.set_session(player_id="123", session_data={
    "username": "player_name",
    "token": "jwt_token",
    "level": 5
})

# Get session
session = SessionCache.get_session("123")

# Extend session
SessionCache.extend_session("123")

# Delete session
SessionCache.delete_session("123")
```

### Game State Caching
```python
from app.cache.game_state_cache import GameStateCache

# Create lobby
GameStateCache.create_lobby("lobby_1", {
    "name": "Quick Match",
    "max_players": 4,
    "created_at": "2024-01-01T00:00:00"
})

# Get active lobbies
lobbies = GameStateCache.get_active_lobbies()

# Add player to lobby
GameStateCache.add_player_to_lobby("lobby_1", "player_123")

# Get lobby players
players = GameStateCache.get_lobby_players("lobby_1")

# Update lobby
GameStateCache.update_lobby("lobby_1", updated_data)

# Delete lobby
GameStateCache.delete_lobby("lobby_1")
```

## Monitoring Redis

### Interactive CLI
```bash
docker exec -it game-redis redis-cli -a changeme

# Inside redis-cli:
> MONITOR              # Watch all commands in real-time
> INFO                 # Server info
> DBSIZE               # Number of keys
> FLUSHDB              # Clear current database (WARNING!)
> KEYS *               # List all keys
```

### Monitor specific patterns
```bash
docker exec game-redis redis-cli -a changeme KEYS "session:*"
docker exec game-redis redis-cli -a changeme KEYS "lobby:*"
```

## Stopping Services

```bash
docker-compose down        # Stop all services
docker-compose down -v     # Stop and remove volumes (CAREFUL - deletes data!)
```

## Troubleshooting

### Redis connection fails
```bash
# Check if Redis is running
docker ps | grep game-redis

# Check Redis logs
docker logs game-redis

# Test connection
docker exec game-redis redis-cli -a changeme ping
```

### Database won't connect
```bash
# Check PostgreSQL logs
docker logs game-postgres

# Connect to database
docker exec -it game-postgres psql -U postgres -d game_db
```

### Backend crashes
```bash
# Check backend logs
docker logs game-backend

# Rebuild image
docker-compose up --build
```

## Production Checklist

- [ ] Change `REDIS_PASSWORD` to strong password
- [ ] Enable Redis persistence (`--appendonly yes` - already in compose)
- [ ] Setup Redis backups
- [ ] Configure Redis memory limits
- [ ] Setup monitoring/alerting
- [ ] Use Redis cluster for high availability
- [ ] Enable TLS for Redis connections (if external)

## Next Steps

1. Integrate session caching into your login routes
2. Cache game lobbies and player state
3. Add rate limiting with Redis
4. Setup WebSocket event distribution with Redis Pub/Sub
5. Monitor and optimize based on actual usage patterns
