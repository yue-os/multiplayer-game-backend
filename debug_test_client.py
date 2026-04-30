#!/usr/bin/env python3
"""
Call the endpoint via the test client to simulate full HTTP flow
"""
from app.server.app import create_app
from app.server.models.user import User
from app.auth.auth_handler import signJWT
import json

app = create_app()

with app.app_context():
    parent = User.query.filter_by(username='ParentJane').first()
    
    # Create token
    token_dict = signJWT(parent.id, parent.role)
    token = token_dict['access_token']
    
    # Use test client
    client = app.test_client()
    headers = {"Authorization": f"Bearer {token}"}
    
    # Make the request
    response = client.get('/parent/stats', headers=headers)
    
    print(f"Status: {response.status_code}")
    data = response.get_json()
    
    # Check field count
    if isinstance(data, list) and len(data) > 0:
        first_child = data[0]
        print(f"\nFirst child fields: {list(first_child.keys())}")
        print(f"Total fields: {len(first_child)}")
        
        print(f"\nExpected to have:")
        expected = ['child', 'child_id', 'child_public_id', 'first_name', 'last_name', 'class_id', 
                   'playtime_logs', 'missions', 'scores', 'mission_avg_score', 'quiz_avg_score', 
                   'total_playtime_minutes']
        print(f"  {expected}")
        
        print(f"\nMissing fields:")
        missing = [f for f in expected if f not in first_child]
        print(f"  {missing}")
        
        print(f"\nFull response:")
        print(json.dumps(data, indent=2))
