#!/usr/bin/env python3
"""
Test parent stats, link_child, and unlink_child endpoints
"""
from app.server.app import create_app
from app.server.models.user import User
from app.server.database import db
from app.auth.auth_handler import signJWT
from werkzeug.security import generate_password_hash

app = create_app()

print("=" * 60)
print("TEST: Parent Stats, Link, Unlink Endpoints")
print("=" * 60)

with app.app_context():
    # Find ParentJane
    parent = User.query.filter_by(username='ParentJane').first()
    if not parent:
        print("❌ ParentJane not found")
        exit(1)
    
    print(f"\n✅ Found parent: {parent.username} (ID: {parent.id})")
    
    # Create test client
    client = app.test_client()
    
    # Create JWT token for parent
    token_dict = signJWT(parent.id, parent.role)
    token = token_dict['access_token']
    print(f"✅ Generated token for parent")
    
    headers = {"Authorization": f"Bearer {token}"}
    
    # Test 1: GET /parent/stats
    print("\n1. Testing GET /parent/stats...")
    response = client.get('/parent/stats', headers=headers)
    
    print(f"   Status: {response.status_code}")
    if response.status_code == 200:
        data = response.get_json()
        print(f"   ✅ Got {len(data)} children")
        
        for i, child in enumerate(data, 1):
            print(f"\n   Child {i}: {child['child']}")
            print(f"   - Full Name: {child['first_name']} {child['last_name']}")
            print(f"   - Playtime Logs: {len(child['playtime_logs'])}")
            print(f"   - Mission Records: {len(child['missions'])}")
            print(f"   - Quiz Scores: {len(child['scores'])}")
            print(f"   - Total Playtime: {child['total_playtime_minutes']} min")
            print(f"   - Mission Avg: {child['mission_avg_score']:.1f}%")
            print(f"   - Quiz Avg: {child['quiz_avg_score']:.1f}%")
    else:
        print(f"   ❌ Error: {response.get_json()}")
    
    # Test 2: Create unlinked student for testing link functionality
    print("\n2. Testing link_child...")
    
    # First, find or create an unlinked student
    unlinked_student = User.query.filter_by(username='TestStudent123', role='Student').first()
    if not unlinked_student:
        print("   - Creating test student for link test...")
        test_user = User(
            first_name='Test',
            last_name='Student',
            username='TestStudent123',
            email='teststudent123@test.com',
            password_hash=generate_password_hash('password123'),
            role='Student',
            class_id=None,
            parent_id=None
        )
        db.session.add(test_user)
        db.session.commit()
        unlinked_student = test_user
        print(f"   ✅ Created test student: {unlinked_student.username}")
    else:
        # Unlink if already linked
        if unlinked_student.parent_id:
            unlinked_student.parent_id = None
            db.session.commit()
            print(f"   - Cleared parent_id for {unlinked_student.username}")
    
    # Now link it
    link_response = client.post(
        '/parent/link_child',
        headers=headers,
        json={'child_username': 'TestStudent123'}
    )
    
    print(f"   Status: {link_response.status_code}")
    if link_response.status_code == 201:
        link_data = link_response.get_json()
        print(f"   ✅ Linked: {link_data['child']['username']}")
    else:
        print(f"   ❌ Error: {link_response.get_json()}")
    
    # Test 3: Verify child is now linked
    print("\n3. Verifying child is linked...")
    verify_response = client.get('/parent/stats', headers=headers)
    if verify_response.status_code == 200:
        stats = verify_response.get_json()
        linked_usernames = [s['child'] for s in stats]
        if 'TestStudent123' in linked_usernames:
            print(f"   ✅ TestStudent123 is now in parent's stats")
        else:
            print(f"   ⚠️  TestStudent123 not found in stats")
    
    # Test 4: Unlink child
    print("\n4. Testing unlink_child...")
    unlink_response = client.post(
        '/parent/unlink_child',
        headers=headers,
        json={'child_username': 'TestStudent123'}
    )
    
    print(f"   Status: {unlink_response.status_code}")
    if unlink_response.status_code == 200:
        unlink_data = unlink_response.get_json()
        print(f"   ✅ Unlinked: {unlink_data['child_username']}")
    else:
        print(f"   ❌ Error: {unlink_response.get_json()}")
    
    # Test 5: Verify child is no longer linked
    print("\n5. Verifying child is unlinked...")
    verify_response = client.get('/parent/stats', headers=headers)
    if verify_response.status_code == 200:
        stats = verify_response.get_json()
        linked_usernames = [s['child'] for s in stats]
        if 'TestStudent123' not in linked_usernames:
            print(f"   ✅ TestStudent123 is no longer in parent's stats")
        else:
            print(f"   ⚠️  TestStudent123 still found in stats")

print("\n" + "=" * 60)
print("✅ All tests completed!")
print("=" * 60)
