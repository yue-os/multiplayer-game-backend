#!/usr/bin/env python3
"""
Debug parent stats response
"""
from app.server.app import create_app
from app.server.models.user import User
from app.auth.auth_handler import signJWT

app = create_app()

with app.app_context():
    parent = User.query.filter_by(username='ParentJane').first()
    
    client = app.test_client()
    token_dict = signJWT(parent.id, parent.role)
    token = token_dict['access_token']
    headers = {"Authorization": f"Bearer {token}"}
    
    response = client.get('/parent/stats', headers=headers)
    print(f"Status: {response.status_code}")
    
    import json
    data = response.get_json()
    print(json.dumps(data, indent=2))
