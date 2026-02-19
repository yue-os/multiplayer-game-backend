import time
import jwt
import os
from typing import Dict

JWT_SECRET = os.getenv("JWT_SECRET", "default_secret")
JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")

def signJWT(user_id: str, role: str) -> Dict[str, str]:
    payload = {
        "user_id": user_id,
        "role": role,
        "expiry": time.time() + 86400  # 24 hours
    }
    token = jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)
    return {"access_token": token}

def decodeJWT(token: str) -> dict:
    try:
        decoded_token = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        return decoded_token if decoded_token["expiry"] >= time.time() else None
    except:
        return None