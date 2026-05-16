"""
Redis client initialization and connection management
"""
import os
import redis
import redis.asyncio as async_redis
from dotenv import load_dotenv

load_dotenv()

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# Synchronous client for Flask routes
redis_client = None
try:
    if REDIS_URL:
        # Basic validation to avoid ValueError from redis-py
        if any(REDIS_URL.startswith(s) for s in ["redis://", "rediss://", "unix://"]):
            redis_client = redis.from_url(REDIS_URL, decode_responses=True)
            redis_client.ping()
            print("✓ Redis connection established")
        else:
            print(f"✗ Invalid REDIS_URL scheme. Must start with redis://, rediss://, or unix://. Got: {REDIS_URL.split(':')[0]}...")
    else:
        print("✗ REDIS_URL not set, Redis client will be unavailable")
except redis.ConnectionError as e:
    print(f"✗ Redis connection failed: {e}")
    redis_client = None
except Exception as e:
    print(f"✗ Redis initialization error: {e}")
    redis_client = None

# Asynchronous client for FastAPI/WebSockets
async_redis_client = None
try:
    if REDIS_URL and any(REDIS_URL.startswith(s) for s in ["redis://", "rediss://", "unix://"]):
        async_redis_client = async_redis.from_url(REDIS_URL, decode_responses=True)
    else:
        async_redis_client = None
except Exception as e:
    print(f"✗ Async Redis initialization error: {e}")
    async_redis_client = None


def get_redis():
    """Get Redis client instance (Synchronous)"""
    return redis_client


def get_async_redis():
    """Get Async Redis client instance"""
    return async_redis_client


def is_redis_available():
    """Check if Redis is available"""
    try:
        if redis_client:
            redis_client.ping()
            return True
    except Exception:
        pass
    return False
