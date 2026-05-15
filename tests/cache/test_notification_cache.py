from app.cache.notification_cache import NotificationCache
import pytest
from unittest.mock import MagicMock, patch

@patch('app.cache.notification_cache.get_redis')
def test_notification_cache(mock_get_redis):
    # Setup mock
    mock_redis = MagicMock()
    mock_get_redis.return_value = mock_redis
    
    uid, ntype, nid = "user123", "announcement", "456"
    key = f"seen_notifications:{uid}"
    value = f"{ntype}:{nid}"
    
    # Verify is_seen False
    mock_redis.sismember.return_value = False
    assert NotificationCache.is_seen(uid, ntype, nid) is False
    
    # Verify mark_as_seen
    mock_redis.sadd.return_value = 1
    assert NotificationCache.mark_as_seen(uid, ntype, nid) is True
    mock_redis.sadd.assert_called_once_with(key, value)
    mock_redis.expire.assert_called_once()
    
    # Verify is_seen True
    mock_redis.sismember.return_value = True
    assert NotificationCache.is_seen(uid, ntype, nid) is True
