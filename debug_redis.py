import os
import redis
from pathlib import Path
from dotenv import load_dotenv

# Load .env for local testing, but Railway will use its own variables
env_path = Path(__file__).resolve().parents[0] / ".env"
if env_path.exists():
    load_dotenv(env_path)

def check_redis():
    redis_url = os.getenv('REDIS_URL')
    print(f"--- Redis Connection Debug ---")
    print(f"Target URL: {redis_url}")
    
    if not redis_url:
        print("❌ ERROR: REDIS_URL is not set in environment variables.")
        return

    try:
        # Create connection
        client = redis.from_url(redis_url, socket_timeout=5)
        
        # Test 1: Ping
        print("Attempting Ping...")
        if client.ping():
            print("✅ Ping Successful!")
        
        # Test 2: Set/Get
        print("Attempting Set/Get...")
        client.set("railway_test_key", "connection_working", ex=60)
        value = client.get("railway_test_key").decode('utf-8')
        if value == "connection_working":
            print("✅ Data Write/Read Successful!")
            
        print("--- SUCCESS: Backend is fully connected to Redis ---")
        
    except Exception as e:
        print(f"❌ REDIS CONNECTION FAILED: {e}")
        print("Check if REDIS_URL is correct and Redis service is running in Railway.")

if __name__ == "__main__":
    check_redis()
