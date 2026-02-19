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
    username = data.get('username')
    email = data.get('email')
    password = data.get('password')
    role = data.get('role', 'Student') # Default to Student

    if not username or not email or not password:
        return jsonify({'error': 'Missing required fields'}), 400

    if User.query.filter((User.username == username) | (User.email == email)).first():
        return jsonify({'error': 'User already exists'}), 400

    hashed_pw = generate_password_hash(password)
    new_user = User(username=username, email=email, password_hash=hashed_pw, role=role)
    
    db.session.add(new_user)
    db.session.commit()

    return jsonify({'message': 'User registered successfully'}), 201

@user_bp.route('/auth/login', methods=['POST'])
def login():
    data = request.json
    username = data.get('username')
    password = data.get('password')

    user = User.query.filter_by(username=username).first()

    if user and check_password_hash(user.password_hash, password):
        token = signJWT(str(user.id), user.role)
        return jsonify(token), 200

    return jsonify({'error': 'Invalid credentials'}), 401

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
            "playtime_logs": playtime,
            "scores": scores
        })
        
    return jsonify(stats), 200