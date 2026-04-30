#!/usr/bin/env python3
"""
Direct test of parent feedback endpoints using Flask app context
"""
from app.server.app import create_app
from app.server.models.user import User
from app.auth.auth_handler import signJWT
import json

app = create_app()

print("=" * 60)
print("TEST: Parent Feedback Endpoints (Direct)")
print("=" * 60)

with app.app_context():
    # Find ParentJane
    parent = User.query.filter_by(username='ParentJane').first()
    if not parent:
        print("❌ ParentJane not found")
        exit(1)
    
    print(f"\n✅ Found parent: {parent.username} (ID: {parent.id})")
    
    # Get children
    children = User.query.filter_by(parent_id=parent.id, role='Student').all()
    print(f"✅ Children: {[c.username for c in children]}")
    
    # Create test client
    client = app.test_client()
    
    # Create JWT token for parent
    token_dict = signJWT(parent.id, parent.role)
    token = token_dict['access_token']
    print(f"✅ Generated token for parent")
    
    # Test 1: GET /parent/feedback
    print("\n1. Testing GET /parent/feedback...")
    headers = {"Authorization": f"Bearer {token}"}
    response = client.get('/parent/feedback', headers=headers)
    
    print(f"   Status: {response.status_code}")
    if response.status_code == 200:
        data = response.get_json()
        print(f"   ✅ Got {data['total']} feedback messages")
        print(f"   ✅ Children count: {data['children_count']}")
        
        for i, msg in enumerate(data['feedback'], 1):
            print(f"\n   Message {i}:")
            print(f"   - From: {msg['sender_name']}")
            print(f"   - To: {msg['receiver_name']}")
            print(f"   - Content: {msg['content'][:60]}...")
            
            if msg['quiz_info']:
                quiz = msg['quiz_info']
                print(f"   - Quiz: {quiz['quiz_title']}")
                print(f"   - Student: {quiz['student_name']}")
                print(f"   - Score: {quiz['score']}%")
    else:
        print(f"   ❌ Error: {response.get_json()}")
    
    # Test 2: GET /parent/feedback/<id> for first message
    if response.status_code == 200:
        data = response.get_json()
        if data['feedback']:
            msg_id = data['feedback'][0]['id']
            print(f"\n2. Testing GET /parent/feedback/{msg_id}...")
            detail_response = client.get(f'/parent/feedback/{msg_id}', headers=headers)
            
            print(f"   Status: {detail_response.status_code}")
            if detail_response.status_code == 200:
                detail = detail_response.get_json()
                print(f"   ✅ Message ID: {detail['id']}")
                print(f"   ✅ From: {detail['sender_name']}")
                print(f"   ✅ Content: {detail['content'][:60]}...")
                if detail['quiz_info']:
                    print(f"   ✅ Quiz: {detail['quiz_info']['quiz_title']}")
            else:
                print(f"   ❌ Error: {detail_response.get_json()}")
    
    # Test 3: Unauthorized access test
    print(f"\n3. Testing unauthorized access...")
    student = User.query.filter_by(username='Timmy').first()
    if student:
        student_token_dict = signJWT(student.id, student.role)
        student_token = student_token_dict['access_token']
        student_headers = {"Authorization": f"Bearer {student_token}"}
        auth_response = client.get('/parent/feedback', headers=student_headers)
        
        if auth_response.status_code == 403:
            print(f"   ✅ Correctly rejected non-parent user (status 403)")
        else:
            print(f"   ⚠️  Unexpected status: {auth_response.status_code}")

print("\n" + "=" * 60)
print("✅ All tests completed!")
print("=" * 60)
