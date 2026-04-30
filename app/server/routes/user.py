from flask import Blueprint, request, jsonify
from werkzeug.security import generate_password_hash, check_password_hash
from app.server.database import db
from app.server.models.user import User, PlaytimeLog, MissionProgress
from app.auth.auth_handler import signJWT
from app.auth.auth_bearer import token_required

user_bp = Blueprint('user', __name__)

@user_bp.route('/auth/register', methods=['POST'])
def register():
    data = request.json
    first_name = (data.get('first_name') or '').strip()
    last_name = (data.get('last_name') or '').strip()
    username = data.get('username')
    email = data.get('email', 'test@gmail.com') # Default email for testing
    password = data.get('password')
    role = data.get('role', 'Student') # Default to Student

    if not first_name or not last_name or not username or not email or not password:
        return jsonify({'error': 'Missing required fields'}), 400

    if User.query.filter((User.username == username) | (User.email == email)).first():
        return jsonify({'error': 'User already exists'}), 400

    hashed_pw = generate_password_hash(password)
    new_user = User(
        first_name=first_name,
        last_name=last_name,
        username=username,
        email=email,
        password_hash=hashed_pw,
        role=role,
    )
    
    db.session.add(new_user)
    db.session.commit()

    return jsonify({'message': 'User registered successfully'}), 201

@user_bp.route('/auth/login', methods=['POST'])
def login():
    data = request.json
    username = (data.get('username') or '').strip()
    password = data.get('password') or ''
    print('[auth/login] username=', username)

    user = User.query.filter_by(username=username).first()

    password_matches = False
    if user:
        try:
            password_matches = check_password_hash(user.password_hash, password)
        except ValueError:
            password_matches = False

    if user and password_matches:
        # Check if student is connected to a parent
        if user.role == 'Student' and user.parent_id is None:
            return jsonify({'error': 'Student account must be linked to a parent to play. Please ask your parent to link your account first.'}), 403
        
        token = signJWT(str(user.id), user.role)
        payload = dict(token)
        payload['must_change_password'] = bool(getattr(user, 'must_change_password', False))
        payload['mustChangePassword'] = payload['must_change_password']
        payload['user'] = user.to_dict()
        return jsonify(payload), 200

    return jsonify({'error': 'Invalid credentials'}), 401


@user_bp.route('/user/profile', methods=['GET'])
@token_required
def get_own_profile():
    user = User.query.get(int(request.current_user_id))
    if not user:
        return jsonify({'error': 'User not found'}), 404

    payload = user.to_dict()
    print('[user/profile:get] user_id=', user.id, 'role=', user.role)
    return jsonify(payload), 200


@user_bp.route('/auth/change-password', methods=['POST'])
@token_required
def change_password():
    data = request.json or {}
    current_password = data.get('current_password') or ''
    new_password = data.get('new_password') or ''

    if len(new_password) < 8:
        return jsonify({'error': 'New password must be at least 8 characters'}), 400

    user = User.query.get(int(request.current_user_id))
    if not user:
        return jsonify({'error': 'User not found'}), 404

    if not check_password_hash(user.password_hash, current_password):
        return jsonify({'error': 'Current password is incorrect'}), 401

    user.password_hash = generate_password_hash(new_password)
    user.must_change_password = False
    db.session.commit()

    return jsonify({'message': 'Password changed successfully'}), 200


@user_bp.route('/user/profile', methods=['PATCH'])
@token_required
def update_own_profile():
    data = request.json or {}
    current_user_id = int(request.current_user_id)

    user = User.query.get(current_user_id)
    if not user:
        return jsonify({'error': 'User not found'}), 404

    first_name = str(data.get('first_name', user.first_name)).strip()
    last_name = str(data.get('last_name', user.last_name)).strip()
    username = str(data.get('username', user.username)).strip()
    email = str(data.get('email', user.email)).strip()

    if first_name == '' or last_name == '' or username == '' or email == '':
        return jsonify({'error': 'first_name, last_name, username, and email are required'}), 400

    conflict = User.query.filter(
        ((User.username == username) | (User.email == email)) & (User.id != user.id)
    ).first()
    if conflict:
        return jsonify({'error': 'Another user already uses the same username or email'}), 409

    user.first_name = first_name
    user.last_name = last_name
    user.username = username
    user.email = email

    db.session.commit()
    return jsonify({'message': 'Profile updated successfully', 'user': user.to_dict()}), 200

@user_bp.route('/parent/link_child', methods=['POST'])
@token_required
def link_child():
    if request.current_user_role != 'Parent':
        return jsonify({'error': 'Unauthorized'}), 403
    
    data = request.json
    child_username = data.get('child_username')
    
    child = User.query.filter_by(username=child_username).first()
    if not child:
        return jsonify({'error': 'Child user not found'}), 404
        
    # Link child to parent (current user)
    child.parent_id = int(request.current_user_id)
    db.session.commit()
    
    return jsonify({'message': f'Linked {child_username} to account'}), 200


@user_bp.route('/parent/unlink_child', methods=['POST'])
@token_required
def unlink_child():
    if request.current_user_role != 'Parent':
        return jsonify({'error': 'Unauthorized'}), 403

    data = request.json or {}
    child_username = (data.get('child_username') or '').strip()
    if child_username == '':
        return jsonify({'error': 'child_username is required'}), 400

    parent_id = int(request.current_user_id)
    child = User.query.filter_by(username=child_username, parent_id=parent_id, role='Student').first()
    if not child:
        return jsonify({'error': 'Linked child not found'}), 404

    child.parent_id = None
    db.session.commit()

    return jsonify({'message': f'Unlinked {child_username} successfully'}), 200

@user_bp.route('/parent/stats', methods=['GET'])
@token_required
def get_children_stats():
    if request.current_user_role != 'Parent':
        return jsonify({'error': 'Unauthorized'}), 403
        
    parent_id = int(request.current_user_id)
    children = User.query.filter_by(parent_id=parent_id).all()
    
    stats = []
    for child in children:
        # Get Playtime
        logs = PlaytimeLog.query.filter_by(user_id=child.id).order_by(PlaytimeLog.date.desc()).limit(7).all()
        playtime = [{"date": str(l.date), "minutes": l.duration_minutes} for l in logs]
        
        # Get Mission Progress (Grades/Scores)
        progress = MissionProgress.query.filter_by(user_id=child.id).all()
        scores = [{"mission_id": p.mission_id, "score": p.score, "status": p.status} for p in progress]
        
        stats.append({
            "child": child.username,
            "child_public_id": child.public_id,
            "playtime_logs": playtime,
            "scores": scores
        })
        
    return jsonify(stats), 200
