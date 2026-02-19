from functools import wraps
from flask import request, jsonify
from app.auth.auth_handler import decodeJWT

def token_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = None
        
        if 'Authorization' in request.headers:
            auth_header = request.headers['Authorization']
            if auth_header.startswith("Bearer "):
                token = auth_header.split(" ")[1]
        
        if not token:
            return jsonify({'message': 'Token is missing!'}), 401

        payload = decodeJWT(token)
        if not payload:
            return jsonify({'message': 'Token is invalid or expired!'}), 401

        # Inject current_user_id and role into kwargs or request context
        # Here we attach it to the request object for easy access in routes
        request.current_user_id = payload['user_id']
        request.current_user_role = payload['role']

        return f(*args, **kwargs)

    return decorated