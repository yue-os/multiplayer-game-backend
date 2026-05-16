from app.cache.session_cache import RegistrationCache
import pytest
from unittest.mock import MagicMock, patch
import json

@patch('app.cache.session_cache.get_redis')
def test_registration_cache_flow(mock_get_redis):
    # Setup mock
    mock_redis = MagicMock()
    mock_get_redis.return_value = mock_redis
    
    email = "test@example.com"
    data = {"username": "tester", "otp": "123456"}
    
    # Verify set
    mock_redis.setex.return_value = True
    assert RegistrationCache.set_pending(email, data) is True
    mock_redis.setex.assert_called_once_with(
        f"registration:otp:{email.lower()}", 
        600, 
        json.dumps(data)
    )
    
    # Verify get
    mock_redis.get.return_value = json.dumps(data)
    assert RegistrationCache.get_pending(email) == data
    mock_redis.get.assert_called_once_with(f"registration:otp:{email.lower()}")
    
    # Verify delete
    mock_redis.delete.return_value = 1
    assert RegistrationCache.delete_pending(email) is True
    mock_redis.delete.assert_called_once_with(f"registration:otp:{email.lower()}")
    
    # Verify gone
    mock_redis.get.return_value = None
    assert RegistrationCache.get_pending(email) is None

@patch('app.cache.session_cache.get_redis')
def test_registration_cache_no_redis(mock_get_redis):
    mock_get_redis.return_value = None
    email = "test@example.com"
    
    assert RegistrationCache.set_pending(email, {}) is False
    assert RegistrationCache.get_pending(email) is None
    assert RegistrationCache.delete_pending(email) is False
