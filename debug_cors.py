
import re
from flask import Flask, request
from flask_cors import CORS

app = Flask(__name__)

ALLOWED_ORIGINS = set()
LAN_ORIGIN_REGEX = r"https?://(localhost|127\.0\.0\.1|10\.\d+\.\d+\.\d+|192\.168\.\d+\.\d+|172\.(1[6-9]|2\d|3[0-1])\.\d+\.\d+)(:\d+)?"

CORS(
    app,
    resources={
        r"/*": {
            "origins": list(ALLOWED_ORIGINS) + [LAN_ORIGIN_REGEX, r"https://[a-zA-Z0-9-]+\.vercel\.app"],
        }
    },
    supports_credentials=True,
    allow_headers=["Content-Type", "Authorization", "X-Requested-With", "Accept", "Origin"],
    methods=["GET", "HEAD", "POST", "OPTIONS", "PUT, PATCH, DELETE"],
)


def _is_allowed_origin(origin):
    if not origin:
        return False
    normalized = origin.rstrip("/")
    LAN_ORIGIN_PATTERN = re.compile(f"^{LAN_ORIGIN_REGEX}$")
    VERCEL_PREVIEW_PATTERN = re.compile(r"^https://[a-zA-Z0-9-]+\.vercel\.app$")
    return bool(
        normalized in set()
        or LAN_ORIGIN_PATTERN.match(normalized)
        or VERCEL_PREVIEW_PATTERN.match(normalized)
    )

def _add_cors_headers(response):
    origin = request.headers.get("Origin")
    if _is_allowed_origin(origin):
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Vary"] = "Origin"
        response.headers["Access-Control-Allow-Credentials"] = "true"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization, X-Requested-With, Accept, Origin"
        response.headers["Access-Control-Allow-Methods"] = "GET, HEAD, POST, OPTIONS, PUT, PATCH, DELETE"
    return response

# @app.after_request
# def add_cors_headers(response):
#     return _add_cors_headers(response)

@app.route("/test")

def test():
    return "ok"

if __name__ == "__main__":
    client = app.test_client()
    
    # Test with a Vercel origin
    origin = "https://batangaware-dashboard.vercel.app"
    print(f"Testing origin: {origin}")
    
    # Preflight request
    resp = client.options("/test", headers={"Origin": origin, "Access-Control-Request-Method": "POST"})
    print(f"OPTIONS status: {resp.status_code}")
    print(f"OPTIONS headers: {dict(resp.headers)}")
    
    # Main request
    resp = client.get("/test", headers={"Origin": origin})
    print(f"GET status: {resp.status_code}")
    print(f"GET headers: {dict(resp.headers)}")
