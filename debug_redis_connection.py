import os
import redis
from dotenv import load_dotenv

# Load environment variables if running locally
load_dotenv()

def test_redis():
    redis_url = os.getenv("REDIS_URL")
    
    if not redis_url:
        print("❌ Error: REDIS_URL environment variable not found.")
        return

    print(f"Connecting to: {redis_url.split('@')[-1]} (password hidden)")
    
    try:
        # Initialize client
        r = redis.from_url(redis_url, decode_responses=True)
        
        # Test 1: PING
        response = r.ping()
        if response:
            print("✅ PING: Success! Redis is alive.")
        
        # Test 2: Write/Read
        test_key = "railway_test_key"
        test_value = "hello_railway"
        r.set(test_key, test_value, ex=60)
        value = r.get(test_key)
        
        if value == test_value:
            print(f"✅ Write/Read: Success! (Key: {test_key}, Value: {value})")
        else:
            print(f"❌ Write/Read: Failed. Expected {test_value}, got {value}")
            
    except redis.AuthenticationError:
        print("❌ Error: Authentication failed. Check your REDIS_PASSWORD.")
    except redis.ConnectionError as e:
        print(f"❌ Error: Could not connect to Redis. {e}")
    except Exception as e:
        print(f"❌ Unexpected Error: {e}")

if __name__ == "__main__":
    test_redis()
