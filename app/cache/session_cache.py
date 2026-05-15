"""
Session caching layer using Redis
"""
import json
import os
from datetime import timedelta
from app.cache.redis_client import get_redis

SESSION_EXPIRY = int(os.getenv("SESSION_EXPIRY", 86400))  # 24 hours default


class SessionCache:
    """Cache for player sessions"""
    
    @staticmethod
    def set_session(player_id: str, session_data: dict, expiry: int = SESSION_EXPIRY):
        """Store player session in Redis"""
        redis = get_redis()
        if not redis:
            return False
        
        try:
            key = f"session:{player_id}"
            redis.setex(key, expiry, json.dumps(session_data))
            return True
        except Exception as e:
            print(f"Error setting session: {e}")
            return False
    
    @staticmethod
    def get_session(player_id: str) -> dict:
        """Retrieve player session from Redis"""
        redis = get_redis()
        if not redis:
            return None
        
        try:
            key = f"session:{player_id}"
            data = redis.get(key)
            return json.loads(data) if data else None
        except Exception as e:
            print(f"Error getting session: {e}")
            return None
    
    @staticmethod
    def delete_session(player_id: str):
        """Remove player session from Redis"""
        redis = get_redis()
        if not redis:
            return False
        
        try:
            key = f"session:{player_id}"
            redis.delete(key)
            return True
        except Exception as e:
            print(f"Error deleting session: {e}")
            return False
    
    @staticmethod
    def extend_session(player_id: str, expiry: int = SESSION_EXPIRY):
        """Extend session expiry"""
        redis = get_redis()
        if not redis:
            return False
        
        try:
            key = f"session:{player_id}"
            session_data = redis.get(key)
            if session_data:
                redis.expire(key, expiry)
                return True
            return False
        except Exception as e:
            print(f"Error extending session: {e}")
            return False
