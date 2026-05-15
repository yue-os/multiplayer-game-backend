from app.cache.redis_client import get_redis
import os

SESSION_EXPIRY = int(os.getenv("SESSION_EXPIRY", 86400))

class NotificationCache:
    """Tracks seen notifications per user using Redis Sets"""
    
    @staticmethod
    def mark_as_seen(user_id: str, notification_type: str, target_id: str):
        """Mark a notification as seen by adding it to a Redis Set"""
        redis = get_redis()
        if not redis:
            return False
        try:
            key = f"seen_notifications:{user_id}"
            value = f"{notification_type}:{target_id}"
            redis.sadd(key, value)
            redis.expire(key, SESSION_EXPIRY)
            return True
        except Exception as e:
            print(f"Error marking notification as seen: {e}")
            return False

    @staticmethod
    def is_seen(user_id: str, notification_type: str, target_id: str) -> bool:
        """Check if a notification has been seen by the user"""
        redis = get_redis()
        if not redis:
            return False
        try:
            key = f"seen_notifications:{user_id}"
            value = f"{notification_type}:{target_id}"
            return redis.sismember(key, value)
        except Exception as e:
            print(f"Error checking if notification is seen: {e}")
            return False
