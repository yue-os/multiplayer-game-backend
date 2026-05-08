from flask import Blueprint, jsonify, request
from sqlalchemy import func, or_
from app.auth.auth_bearer import token_required
from app.server.database import db
from app.server.models.user import Class, User, Message, QuizResult, Quiz, MissionProgress, PlaytimeLog

parent_bp = Blueprint('parent', __name__)


def _user_display_name(user):
    return f"{(user.first_name or '').strip()} {(user.last_name or '').strip()}".strip() or user.username


def _parse_legacy_student_message(content):
    lines = str(content or '').splitlines()
    parsed = {'student_name': None, 'class_name': None, 'sender_name': None, 'content': str(content or '').strip()}

    for line in lines:
        if line.startswith('Student:'):
            parsed['student_name'] = line.replace('Student:', '', 1).strip()
        elif line.startswith('Class:'):
            parsed['class_name'] = line.replace('Class:', '', 1).strip()
        elif line.startswith('From:'):
            parsed['sender_name'] = line.replace('From:', '', 1).strip()

    try:
        blank_index = lines.index('')
    except ValueError:
        blank_index = -1

    if parsed['student_name'] and parsed['class_name'] and blank_index >= 0:
        parsed['content'] = '\n'.join(lines[blank_index + 1:]).strip()

    return parsed


def _serialize_message(message):
    legacy = _parse_legacy_student_message(message.content)
    return {
        'id': message.id,
        'public_id': message.public_id,
        'sender_id': message.sender_id,
        'sender_public_id': message.sender.public_id,
        'sender_username': message.sender.username,
        'sender_name': message.sender_name or legacy['sender_name'] or _user_display_name(message.sender),
        'sender_role': message.sender_role or message.sender.role,
        'receiver_id': message.receiver_id,
        'receiver_public_id': message.receiver.public_id,
        'receiver_name': _user_display_name(message.receiver),
        'receiver_role': message.receiver.role,
        'student_name': message.student_name or legacy['student_name'],
        'class_name': message.class_name or legacy['class_name'],
        'content': message.content if message.student_name and message.class_name else legacy['content'],
        'created_at': message.created_at.isoformat() if message.created_at else None,
        'quiz_info': None,
    }


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
            Message.sender_id == parent_id,
            Message.receiver_id == parent_id,
            Message.receiver_id.in_(child_ids) if child_ids else False
        )
    ).order_by(Message.created_at.desc())
    
    messages = messages_query.all()
    
    feedback_data = []
    for msg in messages:
        message_dict = _serialize_message(msg)
        
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
                    'class_name': Class.query.get(student.class_id).name if student and student.class_id else None,
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
    student_name = str(data.get('student_name') or '').strip()
    class_name = str(data.get('class_name') or '').strip()

    if not receiver_public_id:
        return jsonify({'error': 'receiver_public_id is required'}), 400

    if not content:
        return jsonify({'error': 'content is required'}), 400

    if not student_name or not class_name:
        return jsonify({'error': 'student_name and class_name are required'}), 400

    receiver = User.query.filter_by(public_id=receiver_public_id, role='Teacher').first()
    if not receiver:
        return jsonify({'error': 'Teacher not found'}), 404

    parent_children = User.query.filter_by(parent_id=parent_id, role='Student').all()
    matching_children = []
    for child in parent_children:
        child_class = Class.query.get(child.class_id) if child.class_id else None
        if _user_display_name(child).lower() == student_name.lower() and child_class and child_class.name == class_name:
            matching_children.append(child)

    if len(matching_children) != 1:
        return jsonify({'error': 'Message must reference exactly one linked child and class.'}), 400
    selected_child_class_ids = {child.class_id for child in matching_children if child.class_id is not None}
    if selected_child_class_ids:
        teacher_has_child_class = Quiz.query.filter(
            Quiz.teacher_id == receiver.id,
            Quiz.class_id.in_(selected_child_class_ids),
        ).first()
    else:
        teacher_has_child_class = None

    if not teacher_has_child_class:
        teacher_has_child_class = Class.query.filter(
            Class.teacher_id == receiver.id,
            Class.id.in_(selected_child_class_ids),
        ).first() if selected_child_class_ids else None

    if parent_children and not teacher_has_child_class:
        return jsonify({'error': 'You can only message teachers connected to your linked children.'}), 403

    parent = User.query.get(parent_id)
    message = Message(
        sender_id=parent_id,
        receiver_id=receiver.id,
        student_name=student_name,
        class_name=class_name,
        sender_name=_user_display_name(parent),
        sender_role=parent.role,
        content=content,
    )
    db.session.add(message)
    db.session.commit()

    return jsonify({
        'message': 'Message sent successfully',
        'data': {
            **_serialize_message(message),
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
    
    message_dict = _serialize_message(message)
    
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
                'class_name': Class.query.get(student.class_id).name if student and student.class_id else None,
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


@parent_bp.route('/parent/request-link', methods=['POST'])
def request_parent_link():
    """
    Public endpoint for students to request linking to a parent.
    Does not require authentication so students can request linking before being allowed to log in
    (since students need a parent link to log in).
    """
    data = request.get_json(silent=True) or {}
    child_username = (data.get('child_username') or '').strip()
    parent_username = (data.get('parent_username') or '').strip()
    message_content = (data.get('message') or data.get('content') or '').strip()

    if not child_username or not parent_username:
        return jsonify({'error': 'Both child_username and parent_username are required'}), 400

    # Find the child
    student = User.query.filter_by(username=child_username, role='Student').first()
    if not student:
        return jsonify({'error': 'Student account not found'}), 404

    # Find the parent
    parent = User.query.filter_by(username=parent_username, role='Parent').first()
    if not parent:
        return jsonify({'error': 'Parent account not found. Please check the username.'}), 404

    # Check if already linked
    if student.parent_id:
        if student.parent_id == parent.id:
            return jsonify({'message': 'You are already linked to this parent.'}), 200
        else:
            return jsonify({'error': 'You are already linked to another parent.'}), 400

    # Create a message/request for the parent
    sender_display_name = _user_display_name(student)
    
    # We use a special format that the parent dashboard can easily see
    # and we set student_name/class_name to ensure it shows up correctly in the feedback list
    request_msg = Message(
        sender_id=student.id,
        receiver_id=parent.id,
        sender_name=sender_display_name,
        sender_role='Student',
        content=message_content if message_content else f"I would like to link my student account ({child_username}) to your parent account.",
        student_name=sender_display_name,
        class_name="Linking Request", # Used as a label in the feedback list
    )
    
    db.session.add(request_msg)
    db.session.commit()

    return jsonify({
        'message': 'Linking request sent successfully! Please wait for your parent to approve it from their dashboard.',
        'request_id': request_msg.id
    }), 201


@parent_bp.route('/parent/message/<int:message_id>', methods=['DELETE'])
@token_required
def delete_parent_message(message_id):
    """
    Delete a message received by the parent.
    Used for dismissing notifications or denying linking requests.
    """
    guard = _parent_guard()
    if guard:
        return guard

    parent_id = int(request.current_user_id)
    message = Message.query.get(message_id)

    if not message:
        return jsonify({'error': 'Message not found'}), 404

    # Ensure the parent is the receiver of the message
    if message.receiver_id != parent_id:
        return jsonify({'error': 'Unauthorized: you can only delete messages sent to you.'}), 403

    db.session.add(message) # Not really needed before delete but standard
    db.session.delete(message)
    db.session.commit()

    return jsonify({'message': 'Message deleted successfully'}), 200
