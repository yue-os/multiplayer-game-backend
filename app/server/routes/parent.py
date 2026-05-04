from flask import Blueprint, jsonify, request
from sqlalchemy import func, or_
from app.auth.auth_bearer import token_required
from app.server.database import db
from app.server.models.user import Class, User, Message, QuizResult, Quiz, MissionProgress, PlaytimeLog

parent_bp = Blueprint('parent', __name__)


def _parent_guard():
    """Check if current user is a Parent"""
    user_id = int(request.current_user_id)
    user = User.query.get(user_id)
    if not user or user.role != 'Parent':
        return jsonify({'error': 'Unauthorized: must be a Parent'}), 403
    return None


@parent_bp.route('/parent/feedback', methods=['GET'])
@token_required
def get_parent_feedback():
    """
    Get all feedback messages for parent and their children.
    Returns messages sent to parent and to parent's students (children).
    Includes quiz information for messages linked to quiz results.
    """
    guard = _parent_guard()
    if guard:
        return guard

    parent_id = int(request.current_user_id)
    
    # Get parent's children (students)
    children = User.query.filter_by(parent_id=parent_id, role='Student').all()
    child_ids = [child.id for child in children]
    
    # Get all messages sent to parent or to parent's children
    messages_query = Message.query.filter(
        db.or_(
            Message.receiver_id == parent_id,
            Message.receiver_id.in_(child_ids) if child_ids else False
        )
    ).order_by(Message.created_at.desc())
    
    messages = messages_query.all()
    
    feedback_data = []
    for msg in messages:
        message_dict = {
            'id': msg.id,
            'public_id': msg.public_id,
            'sender_id': msg.sender_id,
            'sender_public_id': msg.sender.public_id,
            'sender_name': f"{msg.sender.first_name} {msg.sender.last_name}".strip() or msg.sender.username,
            'receiver_id': msg.receiver_id,
            'receiver_public_id': msg.receiver.public_id,
            'receiver_name': f"{msg.receiver.first_name} {msg.receiver.last_name}".strip() or msg.receiver.username,
            'content': msg.content,
            'created_at': msg.created_at.isoformat() if msg.created_at else None,
            'quiz_info': None
        }
        
        # If message is linked to a quiz result, include quiz details
        if msg.quiz_result_id:
            quiz_result = QuizResult.query.get(msg.quiz_result_id)
            if quiz_result:
                quiz = Quiz.query.get(quiz_result.quiz_id)
                student = User.query.get(quiz_result.student_id)
                message_dict['quiz_info'] = {
                    'quiz_result_id': quiz_result.id,
                    'quiz_title': quiz.title if quiz else None,
                    'student_name': f"{student.first_name} {student.last_name}".strip() or student.username if student else None,
                    'score': quiz_result.score,
                    'submitted_at': quiz_result.created_at.isoformat() if quiz_result.created_at else None
                }
        
        feedback_data.append(message_dict)
    
    return jsonify({
        'feedback': feedback_data,
        'total': len(feedback_data),
        'children_count': len(child_ids)
    }), 200


@parent_bp.route('/api/messages', methods=['GET'])
@token_required
def list_parent_messages():
    response, status_code = get_parent_feedback()
    if status_code != 200:
        return response, status_code

    data = response.get_json(silent=True) or {}
    return jsonify(data.get('feedback') or []), 200


@parent_bp.route('/api/messages', methods=['POST'])
@token_required
def send_parent_message():
    guard = _parent_guard()
    if guard:
        return guard

    parent_id = int(request.current_user_id)
    data = request.get_json(silent=True) or {}
    receiver_public_id = str(data.get('receiver_public_id') or '').strip()
    content = str(data.get('content') or data.get('message') or '').strip()

    if not receiver_public_id:
        return jsonify({'error': 'receiver_public_id is required'}), 400

    if not content:
        return jsonify({'error': 'content is required'}), 400

    receiver = User.query.filter_by(public_id=receiver_public_id, role='Teacher').first()
    if not receiver:
        return jsonify({'error': 'Teacher not found'}), 404

    parent_children = User.query.filter_by(parent_id=parent_id, role='Student').all()
    child_class_ids = {child.class_id for child in parent_children if child.class_id is not None}
    if child_class_ids:
        teacher_has_child_class = Quiz.query.filter(
            Quiz.teacher_id == receiver.id,
            Quiz.class_id.in_(child_class_ids),
        ).first()
    else:
        teacher_has_child_class = None

    if not teacher_has_child_class:
        teacher_has_child_class = Class.query.filter(
            Class.teacher_id == receiver.id,
            Class.id.in_(child_class_ids),
        ).first() if child_class_ids else None

    if parent_children and not teacher_has_child_class:
        return jsonify({'error': 'You can only message teachers connected to your linked children.'}), 403

    message = Message(sender_id=parent_id, receiver_id=receiver.id, content=content)
    db.session.add(message)
    db.session.commit()

    return jsonify({
        'message': 'Message sent successfully',
        'data': {
            'id': message.id,
            'public_id': message.public_id,
            'sender_id': message.sender_id,
            'receiver_id': message.receiver_id,
            'receiver_public_id': receiver.public_id,
            'content': message.content,
            'created_at': message.created_at.isoformat() if message.created_at else None,
        },
    }), 201


@parent_bp.route('/parent/feedback/<int:message_id>', methods=['GET'])
@token_required
def get_feedback_detail(message_id):
    """Get detailed view of a single feedback message"""
    guard = _parent_guard()
    if guard:
        return guard

    parent_id = int(request.current_user_id)
    message = Message.query.get(message_id)
    
    if not message:
        return jsonify({'error': 'Message not found'}), 404
    
    # Check if parent has access to this message
    # Parent can view if they're the receiver or if their child is the receiver
    children = User.query.filter_by(parent_id=parent_id, role='Student').all()
    child_ids = [child.id for child in children]
    
    if message.receiver_id != parent_id and message.receiver_id not in child_ids:
        return jsonify({'error': 'Unauthorized: message not for you or your children'}), 403
    
    message_dict = {
        'id': message.id,
        'public_id': message.public_id,
        'sender_id': message.sender_id,
        'sender_public_id': message.sender.public_id,
        'sender_name': f"{message.sender.first_name} {message.sender.last_name}".strip() or message.sender.username,
        'receiver_id': message.receiver_id,
        'receiver_public_id': message.receiver.public_id,
        'receiver_name': f"{message.receiver.first_name} {message.receiver.last_name}".strip() or message.receiver.username,
        'content': message.content,
        'created_at': message.created_at.isoformat() if message.created_at else None,
        'quiz_info': None
    }
    
    # Include quiz details if available
    if message.quiz_result_id:
        quiz_result = QuizResult.query.get(message.quiz_result_id)
        if quiz_result:
            quiz = Quiz.query.get(quiz_result.quiz_id)
            student = User.query.get(quiz_result.student_id)
            message_dict['quiz_info'] = {
                'quiz_result_id': quiz_result.id,
                'quiz_title': quiz.title if quiz else None,
                'student_name': f"{student.first_name} {student.last_name}".strip() or student.username if student else None,
                'score': quiz_result.score,
                'submitted_at': quiz_result.created_at.isoformat() if quiz_result.created_at else None
            }
    
    return jsonify(message_dict), 200


@parent_bp.route('/parent/stats', methods=['GET'])
@token_required
def get_parent_stats():
    """
    Get stats for all children linked to parent.
    Returns list of children with their playtime and mission data.
    """
    guard = _parent_guard()
    if guard:
        return guard

    parent_id = int(request.current_user_id)
    
    # Get all children (students linked to this parent)
    children = User.query.filter_by(parent_id=parent_id, role='Student').all()
    
    stats_list = []
    for child in children:
        # Get playtime logs
        playtime_logs = PlaytimeLog.query.filter_by(user_id=child.id).order_by(PlaytimeLog.date.desc()).all()
        playtime_data = [
            {
                'date': str(log.date),
                'minutes': log.duration_minutes,
                'id': log.id,
                'public_id': log.public_id
            }
            for log in playtime_logs
        ]
        
        # Get mission progress
        mission_progress = MissionProgress.query.filter_by(user_id=child.id).all()
        mission_data = [
            {
                'mission_id': mp.mission_id,
                'status': mp.status,
                'score': mp.score,
                'updated_at': mp.updated_at.isoformat() if mp.updated_at else None,
                'public_id': mp.public_id
            }
            for mp in mission_progress
        ]
        
        # Get quiz results
        quiz_results = QuizResult.query.filter_by(student_id=child.id).all()
        quiz_data = [
            {
                'quiz_id': qr.quiz_id,
                'score': qr.score,
                'updated_at': qr.created_at.isoformat() if qr.created_at else None,
                'public_id': qr.public_id
            }
            for qr in quiz_results
        ]
        
        # Calculate average scores
        mission_avg = float(sum(m['score'] for m in mission_data) / len(mission_data)) if mission_data else 0.0
        quiz_avg = float(sum(q['score'] for q in quiz_data) / len(quiz_data)) if quiz_data else 0.0
        total_playtime = sum(log.duration_minutes or 0 for log in playtime_logs)
        
        stats_list.append({
            'child': child.username,
            'child_id': child.id,
            'child_public_id': child.public_id,
            'first_name': child.first_name,
            'last_name': child.last_name,
            'class_id': child.class_id,
            'playtime_logs': playtime_data,
            'missions': mission_data,
            'scores': quiz_data,
            'mission_avg_score': mission_avg,
            'quiz_avg_score': quiz_avg,
            'total_playtime_minutes': total_playtime,
        })
    
    return jsonify(stats_list), 200


@parent_bp.route('/parent/link_child', methods=['POST'])
@token_required
def link_child():
    """
    Link a student to parent by student username.
    Parent claims a child account.
    """
    guard = _parent_guard()
    if guard:
        return guard

    parent_id = int(request.current_user_id)
    data = request.get_json(silent=True) or {}
    child_username = (data.get('child_username') or data.get('child_identifier') or '').strip()
    
    if not child_username:
        return jsonify({'error': 'child_username is required'}), 400
    
    lowered_identifier = child_username.lower()
    full_name = func.lower(func.trim(User.first_name + ' ' + User.last_name))
    student = User.query.filter(
        User.role == 'Student',
        or_(
            func.lower(User.username) == lowered_identifier,
            func.lower(User.email) == lowered_identifier,
            func.lower(User.public_id) == lowered_identifier,
            full_name == lowered_identifier,
        ),
    ).first()
    if not student:
        return jsonify({'error': 'Student not found. Enter the student username, email, public ID, or full name.'}), 404
    
    # Check if already linked to another parent
    if student.parent_id and student.parent_id != parent_id:
        return jsonify({'error': 'This student is already linked to another parent'}), 400
    
    # Link the student to parent
    student.parent_id = parent_id
    db.session.commit()
    
    return jsonify({
        'message': f'Successfully linked {child_username}',
        'child': {
            'username': student.username,
            'first_name': student.first_name,
            'last_name': student.last_name,
            'id': student.id,
            'public_id': student.public_id,
            'class_id': student.class_id
        }
    }), 201


@parent_bp.route('/parent/unlink_child', methods=['POST'])
@token_required
def unlink_child():
    """
    Unlink a student from parent by student username.
    Parent removes a child account.
    """
    guard = _parent_guard()
    if guard:
        return guard

    parent_id = int(request.current_user_id)
    data = request.get_json(silent=True) or {}
    child_username = (data.get('child_username') or '').strip()
    
    if not child_username:
        return jsonify({'error': 'child_username is required'}), 400
    
    # Find student by username
    student = User.query.filter_by(username=child_username, role='Student').first()
    if not student:
        return jsonify({'error': 'Student not found'}), 404
    
    # Check if linked to this parent
    if student.parent_id != parent_id:
        return jsonify({'error': 'This student is not linked to your account'}), 403
    
    # Unlink the student from parent
    student.parent_id = None
    db.session.commit()
    
    return jsonify({
        'message': f'Successfully unlinked {child_username}',
        'child_username': child_username
    }), 200
