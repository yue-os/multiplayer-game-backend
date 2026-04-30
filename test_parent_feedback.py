#!/usr/bin/env python3
"""
Test script for parent feedback endpoints
"""
import requests
import json

BASE_URL = "http://localhost:5000"

def test_parent_feedback():
    """Test the parent feedback endpoint"""
    
    # Step 1: Login as ParentJane
    print("=" * 60)
    print("TEST: Parent Feedback Endpoints")
    print("=" * 60)
    
    print("\n1. Logging in as ParentJane...")
    login_response = requests.post(
        f"{BASE_URL}/auth/login",
        json={"username": "ParentJane", "password": "123456"}
    )
    
    if login_response.status_code != 200:
        print(f"❌ Login failed: {login_response.text}")
        return
    
    token = login_response.json().get("access_token")
    print(f"✅ Login successful. Token: {token[:20]}...")
    
    headers = {"Authorization": f"Bearer {token}"}
    
    # Step 2: Get all feedback messages
    print("\n2. Getting all feedback messages...")
    feedback_response = requests.get(
        f"{BASE_URL}/parent/feedback",
        headers=headers
    )
    
    if feedback_response.status_code != 200:
        print(f"❌ Failed to get feedback: {feedback_response.text}")
        return
    
    feedback_data = feedback_response.json()
    print(f"✅ Got {feedback_data['total']} feedback messages")
    print(f"   Children count: {feedback_data['children_count']}")
    
    # Display feedback messages
    for i, msg in enumerate(feedback_data['feedback'], 1):
        print(f"\n   Message {i}:")
        print(f"   - From: {msg['sender_name']}")
        print(f"   - To: {msg['receiver_name']}")
        print(f"   - Content: {msg['content'][:60]}...")
        print(f"   - Created: {msg['created_at']}")
        
        if msg['quiz_info']:
            quiz = msg['quiz_info']
            print(f"   - Quiz: {quiz['quiz_title']}")
            print(f"   - Student: {quiz['student_name']}")
            print(f"   - Score: {quiz['score']}%")
            print(f"   - Submitted: {quiz['submitted_at']}")
    
    # Step 3: Test getting a single message detail (if messages exist)
    if feedback_data['feedback']:
        first_msg_id = feedback_data['feedback'][0]['id']
        print(f"\n3. Getting detail of message ID {first_msg_id}...")
        
        detail_response = requests.get(
            f"{BASE_URL}/parent/feedback/{first_msg_id}",
            headers=headers
        )
        
        if detail_response.status_code != 200:
            print(f"❌ Failed to get message detail: {detail_response.text}")
            return
        
        detail = detail_response.json()
        print(f"✅ Message details:")
        print(f"   - From: {detail['sender_name']}")
        print(f"   - Content: {detail['content']}")
        if detail['quiz_info']:
            print(f"   - Quiz: {detail['quiz_info']['quiz_title']}")
            print(f"   - Score: {detail['quiz_info']['score']}%")
    
    # Step 4: Test unauthorized access (try to get non-existent message)
    print(f"\n4. Testing unauthorized access...")
    invalid_response = requests.get(
        f"{BASE_URL}/parent/feedback/99999",
        headers=headers
    )
    
    if invalid_response.status_code == 404:
        print(f"✅ Correctly rejected non-existent message")
    else:
        print(f"⚠️  Unexpected status: {invalid_response.status_code}")
    
    print("\n" + "=" * 60)
    print("✅ All tests completed!")
    print("=" * 60)

if __name__ == "__main__":
    test_parent_feedback()
