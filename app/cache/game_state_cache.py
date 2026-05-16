"""
Game state caching for lobbies and active games
"""
import json
from typing import Optional, List
from app.cache.redis_client import get_redis


class GameStateCache:
    """Cache for active lobbies and game state"""
    
    @staticmethod
    def create_lobby(lobby_id: str, lobby_data: dict, expiry: int = 3600):
        """Create a new lobby cache entry"""
        redis = get_redis()
        if not redis:
            return False
        
        try:
            key = f"lobby:{lobby_id}"
            redis.setex(key, expiry, json.dumps(lobby_data))
            # Add to active lobbies set for quick listing
            redis.sadd("active_lobbies", lobby_id)
            redis.expire(f"active_lobbies_set:{lobby_id}", expiry)
            return True
        except Exception as e:
            print(f"Error creating lobby: {e}")
            return False
    
    @staticmethod
    def get_lobby(lobby_id: str) -> Optional[dict]:
        """Get lobby data"""
        redis = get_redis()
        if not redis:
            return None
        
        try:
            key = f"lobby:{lobby_id}"
            data = redis.get(key)
            return json.loads(data) if data else None
        except Exception as e:
            print(f"Error getting lobby: {e}")
            return None
    
    @staticmethod
    def update_lobby(lobby_id: str, lobby_data: dict):
        """Update lobby data"""
        redis = get_redis()
        if not redis:
            return False
        
        try:
            key = f"lobby:{lobby_id}"
            ttl = redis.ttl(key)
            if ttl > 0:
                redis.setex(key, ttl, json.dumps(lobby_data))
                return True
            return False
        except Exception as e:
            print(f"Error updating lobby: {e}")
            return False
    
    @staticmethod
    def delete_lobby(lobby_id: str):
        """Delete lobby cache entry"""
        redis = get_redis()
        if not redis:
            return False
        
        try:
            key = f"lobby:{lobby_id}"
            redis.delete(key)
            redis.srem("active_lobbies", lobby_id)
            return True
        except Exception as e:
            print(f"Error deleting lobby: {e}")
            return False
    
    @staticmethod
    def get_active_lobbies() -> List[str]:
        """Get list of active lobby IDs"""
        redis = get_redis()
        if not redis:
            return []
        
        try:
            lobbies = redis.smembers("active_lobbies")
            return list(lobbies)
        except Exception as e:
            print(f"Error getting active lobbies: {e}")
            return []
    
    @staticmethod
    def add_player_to_lobby(lobby_id: str, player_id: str):
        """Add player to lobby participants"""
        redis = get_redis()
        if not redis:
            return False
        
        try:
            key = f"lobby:{lobby_id}:players"
            redis.sadd(key, player_id)
            return True
        except Exception as e:
            print(f"Error adding player to lobby: {e}")
            return False
    
    @staticmethod
    def remove_player_from_lobby(lobby_id: str, player_id: str):
        """Remove player from lobby participants"""
        redis = get_redis()
        if not redis:
            return False
        
        try:
            key = f"lobby:{lobby_id}:players"
            redis.srem(key, player_id)
            return True
        except Exception as e:
            print(f"Error removing player from lobby: {e}")
            return False
    
    @staticmethod
    def get_lobby_players(lobby_id: str) -> List[str]:
        """Get list of players in a lobby"""
        redis = get_redis()
        if not redis:
            return []
        
        try:
            key = f"lobby:{lobby_id}:players"
            players = redis.smembers(key)
            return list(players)
        except Exception as e:
            print(f"Error getting lobby players: {e}")
            return []

    @staticmethod
    def save_state(lobby_id: str, state_dict: dict, expiry: int = 3600):
        """Save full game state to Redis"""
        redis = get_redis()
        if not redis:
            return False
        try:
            key = f"lobby:{lobby_id}:state"
            redis.setex(key, expiry, json.dumps(state_dict))
            return True
        except Exception as e:
            print(f"Error saving game state: {e}")
            return False

    @staticmethod
    def load_state(lobby_id: str) -> Optional[dict]:
        """Load full game state from Redis"""
        redis = get_redis()
        if not redis:
            return None
        try:
            key = f"lobby:{lobby_id}:state"
            data = redis.get(key)
            return json.loads(data) if data else None
        except Exception as e:
            print(f"Error loading game state: {e}")
            return None
