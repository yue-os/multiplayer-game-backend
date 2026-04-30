#!/usr/bin/env python3
"""
Call the endpoint directly to see what it returns
"""
from app.server.app import create_app
from app.server.models.user import User
from app.auth.auth_handler import signJWT
from flask import Flask, g

app = create_app()

with app.app_context():
    with app.test_request_context():
        parent = User.query.filter_by(username='ParentJane').first()
        
        # Create a mock request context
        token_dict = signJWT(parent.id, parent.role)
        token = token_dict['access_token']
        
        # Mock the current_user_id for the guard
        from app.auth.auth_handler import decodeJWT
        payload = decodeJWT(token)
        
        # Set current_user_id on request
        from flask import request
        request.current_user_id = parent.id
        request.current_user_role = parent.role
        
        # Import and call the endpoint directly
        from app.server.routes.parent import get_parent_stats
        
        result = get_parent_stats()
        
        print(f"Result type: {type(result)}")
        print(f"Result: {result}")
        
        # If it's a Response object, get the data
        if hasattr(result, 'get_json'):
            data = result[0].get_json() if isinstance(result[0], tuple) else result.get_json()
            import json
            print(json.dumps(data, indent=2))
        else:
            import json
            print(json.dumps(result, indent=2))
