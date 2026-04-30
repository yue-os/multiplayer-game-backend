#!/usr/bin/env python3
"""
Call the endpoint directly to see what it returns - fixed version
"""
from app.server.app import create_app
from app.server.models.user import User
from app.auth.auth_handler import signJWT, decodeJWT
from flask import Flask
import json

app = create_app()

with app.app_context():
    with app.test_request_context():
        parent = User.query.filter_by(username='ParentJane').first()
        
        # Create token
        token_dict = signJWT(parent.id, parent.role)
        token = token_dict['access_token']
        
        # Verify token
        payload = decodeJWT(token)
        print(f"Token payload: {payload}")
        
        # Set current_user_id on request
        from flask import request
        request.current_user_id = payload['user_id']
        request.current_user_role = payload['role']
        
        # Import and call the endpoint directly
        from app.server.routes.parent import get_parent_stats
        
        response, status = get_parent_stats()
        
        print(f"\nStatus: {status}")
        print(f"Response data: {response.get_json()}")
