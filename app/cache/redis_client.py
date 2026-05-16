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
try:
    redis_client = redis.from_url(REDIS_URL, decode_responses=True)
    redis_client.ping()
    print("✓ Redis connection established")
except redis.ConnectionError as e:
    print(f"✗ Redis connection failed: {e}")
    redis_client = None

# Asynchronous client for FastAPI/WebSockets
async_redis_client = async_redis.from_url(REDIS_URL, decode_responses=True)


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
