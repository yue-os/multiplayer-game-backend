"""
Redis client initialization and connection management
"""
import os
import redis
from dotenv import load_dotenv

load_dotenv()

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

try:
    redis_client = redis.from_url(REDIS_URL, decode_responses=True)
    redis_client.ping()
    print("✓ Redis connection established")
except redis.ConnectionError as e:
    print(f"✗ Redis connection failed: {e}")
    redis_client = None


def get_redis():
    """Get Redis client instance"""
    return redis_client


def is_redis_available():
    """Check if Redis is available"""
    try:
        if redis_client:
            redis_client.ping()
            return True
    except Exception:
        pass
    return False
