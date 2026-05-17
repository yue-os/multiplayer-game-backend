"""
Session caching layer using Redis
"""
import json
import os
from datetime import datetime, timedelta
from app.cache.redis_client import get_redis
from app.server.database import db

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


class RegistrationCache:
    """Cache for pending registrations (OTPs).

    Redis is preferred, but Railway deployments can run without it. In that case,
    pending registrations are stored in Postgres so OTP verification still works
    across gunicorn workers.
    """

    @staticmethod
    def _key(email: str) -> str:
        return f"registration:otp:{email.lower()}"

    @staticmethod
    def _set_pending_db(email: str, data: dict, expiry: int):
        from app.server.models.user import PendingRegistration

        normalized_email = email.lower()
        expires_at = datetime.utcnow() + timedelta(seconds=expiry)

        try:
            PendingRegistration.query.filter(
                PendingRegistration.expires_at < datetime.utcnow()
            ).delete(synchronize_session=False)

            pending = PendingRegistration.query.filter_by(email=normalized_email).first()
            if pending:
                pending.data = data
                pending.expires_at = expires_at
            else:
                pending = PendingRegistration(
                    email=normalized_email,
                    data=data,
                    expires_at=expires_at,
                )
                db.session.add(pending)

            db.session.commit()
            return True
        except Exception as e:
            db.session.rollback()
            print(f"Error setting pending registration in database: {e}")
            return False

    @staticmethod
    def _get_pending_db(email: str) -> dict:
        from app.server.models.user import PendingRegistration

        normalized_email = email.lower()

        try:
            pending = PendingRegistration.query.filter_by(email=normalized_email).first()
            if not pending:
                return None

            if pending.expires_at < datetime.utcnow():
                db.session.delete(pending)
                db.session.commit()
                return None

            return pending.data
        except Exception as e:
            db.session.rollback()
            print(f"Error getting pending registration from database: {e}")
            return None

    @staticmethod
    def _delete_pending_db(email: str):
        from app.server.models.user import PendingRegistration

        normalized_email = email.lower()

        try:
            pending = PendingRegistration.query.filter_by(email=normalized_email).first()
            if pending:
                db.session.delete(pending)
                db.session.commit()
            return True
        except Exception as e:
            db.session.rollback()
            print(f"Error deleting pending registration from database: {e}")
            return False
    
    @staticmethod
    def set_pending(email: str, data: dict, expiry: int = 600):
        """Store pending registration data in Redis, or Postgres as fallback."""
        redis = get_redis()
        if not redis:
            return RegistrationCache._set_pending_db(email, data, expiry)
        
        try:
            key = RegistrationCache._key(email)
            redis.setex(key, expiry, json.dumps(data))
            return True
        except Exception as e:
            print(f"Error setting pending registration: {e}")
            return RegistrationCache._set_pending_db(email, data, expiry)

    @staticmethod
    def get_pending(email: str) -> dict:
        """Retrieve pending registration data from Redis, or Postgres as fallback."""
        redis = get_redis()
        if not redis:
            return RegistrationCache._get_pending_db(email)
        
        try:
            key = RegistrationCache._key(email)
            data = redis.get(key)
            if data:
                return json.loads(data)
        except Exception as e:
            print(f"Error getting pending registration: {e}")
        return RegistrationCache._get_pending_db(email)

    @staticmethod
    def delete_pending(email: str):
        """Remove pending registration data from Redis and Postgres fallback."""
        redis = get_redis()
        db_deleted = RegistrationCache._delete_pending_db(email)
        if not redis:
            return db_deleted
        
        try:
            key = RegistrationCache._key(email)
            redis.delete(key)
            return db_deleted
        except Exception as e:
            print(f"Error deleting pending registration: {e}")
            return db_deleted
