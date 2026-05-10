
from datetime import datetime, timezone
import hashlib
import secrets
from flask import Blueprint, request, jsonify
from sqlalchemy import func
from werkzeug.security import generate_password_hash, check_password_hash
from app.server.database import db
from app.server.models.user import Class, Message, Mission, MissionProgress, PasswordResetRequest, PlaytimeLog, Quiz, QuizResult, User
from app.server.models.announcement import Announcement
from app.auth.auth_handler import signJWT
from app.auth.auth_bearer import token_required

from app.server.services.email_service import send_otp_email

user_bp = Blueprint('user', __name__)
RESET_ALLOWED_ROLES = {'Student', 'Teacher', 'Parent'}

# In-memory storage for seen notifications to avoid showing them repeatedly
SEEN_NOTIFICATIONS = {}

# In-memory storage for 2-step registration OTP verification
PENDING_REGISTRATIONS = {}

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

    # Also check pending (not-yet-verified) registrations for conflicts
    for pending_email, pending_data in PENDING_REGISTRATIONS.items():
        if pending_email == email or pending_data['username'] == username:
            return jsonify({'error': 'A registration for this username or email is already pending verification'}), 409

    hashed_pw = generate_password_hash(password)
    
    otp = str(secrets.randbelow(1000000)).zfill(6)
    PENDING_REGISTRATIONS[email] = {
        'first_name': first_name,
        'last_name': last_name,
        'username': username,
        'email': email,
        'password_hash': hashed_pw,
        'role': role,
        'otp': otp
    }
    
    # Send actual OTP email
    if not send_otp_email(email, otp):
        PENDING_REGISTRATIONS.pop(email, None)
        return jsonify({'error': 'Unable to send OTP email. Please contact the administrator.'}), 500

    return jsonify({'message': 'OTP sent', 'email': email, 'require_otp': True}), 200

@user_bp.route('/auth/verify-otp', methods=['POST'])
def verify_otp():
    data = request.json or {}
    email = data.get('email')
    otp = data.get('otp')

    if not email or not otp:
        return jsonify({'error': 'Email and OTP are required'}), 400

    pending = PENDING_REGISTRATIONS.get(email)
    if not pending:
        return jsonify({'error': 'No pending registration found for this email'}), 404

    if pending['otp'] != str(otp).strip():
        return jsonify({'error': 'Invalid OTP'}), 400

    # Re-check the DB right before insert to guard against races
    if User.query.filter(
        (User.username == pending['username']) | (User.email == pending['email'])
    ).first():
        del PENDING_REGISTRATIONS[email]
        return jsonify({'error': 'User already exists'}), 400

    new_user = User( 
        first_name=pending['first_name'],
        last_name=pending['last_name'],
        username=pending['username'],
        email=pending['email'],
        password_hash=pending['password_hash'],
        role=pending['role'],
    )

    try:
        db.session.add(new_user)
        db.session.commit()
    except Exception:
        db.session.rollback()
        del PENDING_REGISTRATIONS[email]
        return jsonify({'error': 'Failed to create user. Please try registering again.'}), 500

    del PENDING_REGISTRATIONS[email]

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

@user_bp.route('/auth/change-password', methods=['POST'])
@token_required
def change_password():
    data = request.json or {}
    current_user_id = int(request.current_user_id)
    user = User.query.get(current_user_id)

    if not user:
        return jsonify({'error': 'User not found'}), 404

    current_password = data.get('current_password')
    new_password = data.get('new_password')

    if not current_password or not new_password:
        return jsonify({'error': 'current_password and new_password are required'}), 400

    if not check_password_hash(user.password_hash, current_password):
        return jsonify({'error': 'Incorrect current password'}), 403

    user.password_hash = generate_password_hash(new_password)
    user.must_change_password = False
    db.session.commit()

    return jsonify({
        'message': 'Password changed successfully',
        'must_change_password': False,
        'mustChangePassword': False,
    }), 200


@user_bp.route('/auth/forgot-password', methods=['POST'])
def forgot_password():
    data = request.get_json(silent=True) or {}
    email = str(data.get('email') or '').strip().lower()
    role = str(data.get('role') or '').strip()

    if not email or role not in RESET_ALLOWED_ROLES:
        return jsonify({'error': 'Email and a valid role are required'}), 400

    user = User.query.filter(func.lower(User.email) == email, User.role == role).first()
    reset_request = PasswordResetRequest(  # pyre-ignore[unexpected-keyword]
        user_id=user.id if user else None,
        email=email,
        role=role,
        status='Pending',
        activity_log=[
            {
                'event': 'requested',
                'at': datetime.utcnow().isoformat(),
                'ip': request.remote_addr,
                'matched_user': bool(user),
            }
        ],
    )
    db.session.add(reset_request)
    db.session.commit()

    return jsonify({
        'message': 'If this account exists, your reset request was sent to the administrator for review.'
    }), 202


@user_bp.route('/auth/password-reset/verify', methods=['GET'])
def verify_password_reset_token():
    token = str(request.args.get('token') or '').strip()
    if not token:
        return jsonify({'error': 'Reset token is required'}), 400

    token_hash = hashlib.sha256(token.encode('utf-8')).hexdigest()
    reset_request = PasswordResetRequest.query.filter_by(token_hash=token_hash, status='Approved').first()
    now = datetime.utcnow()
    if not reset_request or reset_request.used_at or not reset_request.token_expires_at or reset_request.token_expires_at <= now:
        if reset_request and reset_request.status == 'Approved':
            reset_request.status = 'Expired'
            reset_request.activity_log = (reset_request.activity_log or []) + [{'event': 'expired_verify', 'at': now.isoformat()}]
            db.session.commit()
        return jsonify({'error': 'This reset link is invalid or expired'}), 400

    return jsonify({'email': reset_request.email, 'role': reset_request.role, 'expires_at': reset_request.token_expires_at.isoformat()}), 200


@user_bp.route('/auth/password-reset/complete', methods=['POST'])
def complete_password_reset():
    data = request.get_json(silent=True) or {}
    token = str(data.get('token') or '').strip()
    new_password = str(data.get('new_password') or '')

    if not token or len(new_password) < 8:
        return jsonify({'error': 'A valid reset token and a password of at least 8 characters are required'}), 400

    token_hash = hashlib.sha256(token.encode('utf-8')).hexdigest()
    reset_request = PasswordResetRequest.query.filter_by(token_hash=token_hash, status='Approved').first()
    now = datetime.utcnow()

    if not reset_request or reset_request.used_at or not reset_request.token_expires_at or reset_request.token_expires_at <= now:
        if reset_request and reset_request.status == 'Approved':
            reset_request.status = 'Expired'
            reset_request.activity_log = (reset_request.activity_log or []) + [{'event': 'expired_complete', 'at': now.isoformat()}]
            db.session.commit()
        return jsonify({'error': 'This reset link is invalid or expired'}), 400

    user = User.query.get(reset_request.user_id) if reset_request.user_id else None
    if not user:
        return jsonify({'error': 'User account for this reset request no longer exists'}), 404

    user.password_hash = generate_password_hash(new_password)
    user.must_change_password = False
    reset_request.status = 'Used'
    reset_request.used_at = now
    reset_request.token_hash = None
    reset_request.activity_log = (reset_request.activity_log or []) + [{'event': 'password_reset_completed', 'at': now.isoformat()}]
    db.session.commit()

    return jsonify({'message': 'Password updated successfully. You can now log in.'}), 200

@user_bp.route('/user/profile', methods=['GET'])
@token_required
def get_own_profile():
    current_user_id = int(request.current_user_id)
    user = User.query.get(current_user_id)
    if not user:
        return jsonify({'error': 'User not found'}), 404
    return jsonify(user.to_dict()), 200


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


@user_bp.route('/ping', methods=['GET'])
def ping():
    return jsonify({"status": "ok"}), 200


@user_bp.route('/student/notifications', methods=['GET'])
@token_required
def get_student_notifications():
    student_id = int(request.current_user_id)
    student = User.query.get(student_id)
    if not student or student.role != 'Student' or not student.class_id:
        return jsonify({'notifications': []}), 200

    if student_id not in SEEN_NOTIFICATIONS:
        SEEN_NOTIFICATIONS[student_id] = {'announcements': set(), 'quizzes': set()}
    
    seen = SEEN_NOTIFICATIONS[student_id]
    notifications = []

    latest_announcement = None
    for a in Announcement.query.filter_by(class_id=student.class_id).order_by(Announcement.created_at.desc()).all():
        if not getattr(a, 'is_hidden', False):
            latest_announcement = a
            break

    if latest_announcement and latest_announcement.id not in seen['announcements']:
        notifications.append({
            'id': latest_announcement.id,
            'type': 'announcement',
            'title': f"Announcement: {latest_announcement.title}",
            'message': latest_announcement.message,
        })
        seen['announcements'].add(latest_announcement.id)

    latest_quiz = None
    for q in Quiz.query.filter_by(class_id=student.class_id).order_by(Quiz.id.desc()).all():
        if not getattr(q, 'timer_seconds', 0):
            latest_quiz = q
            break

    if latest_quiz and latest_quiz.id not in seen['quizzes']:
        notifications.append({
            'id': latest_quiz.id,
            'type': 'quiz',
            'title': "New Quiz Published",
            'message': f"{latest_quiz.title} is now available!",
        })
        seen['quizzes'].add(latest_quiz.id)

    return jsonify({'notifications': notifications}), 200

@user_bp.route('/student/quiz/<quiz_id>', methods=['GET'])
@token_required
def student_get_quiz(quiz_id):
    student_id = int(request.current_user_id)
    student = User.query.get(student_id)
    if not student or student.role != 'Student':
        return jsonify({'error': 'Student not found'}), 404

    try:
        quiz_id_int = int(float(quiz_id))
    except ValueError:
        return jsonify({'error': f'Invalid quiz ID format: {quiz_id}'}), 400

    quiz = Quiz.query.get(quiz_id_int)
    if not quiz:
        return jsonify({'error': 'Quiz not found'}), 404

    if getattr(quiz, 'timer_seconds', 0):
        return jsonify({'error': 'This quiz is currently hidden by the teacher'}), 403

    if getattr(quiz, 'status', 'published') != 'published':
        return jsonify({'error': 'This quiz is not open yet'}), 403

    status = 'Open'
    is_closed = False
    if quiz.start_date:
        now = datetime.now(timezone.utc)
        quiz_deadline = quiz.start_date.replace(tzinfo=timezone.utc) if quiz.start_date.tzinfo is None else quiz.start_date
        if now > quiz_deadline:
            is_closed = True
            status = 'Closed'

    if is_closed:
        return jsonify({'error': 'This quiz is no longer accepting submissions.'}), 403

    if quiz.class_id != student.class_id:
        return jsonify({'error': 'This quiz is not assigned to your class'}), 403

    existing = QuizResult.query.filter_by(quiz_id=quiz_id_int, student_id=student_id).first()
    if existing:
        return jsonify({'error': 'You have already submitted this quiz'}), 409

    ordered = sorted(quiz.questions or [], key=lambda q: (q.order or 0, q.id or 0))
    questions = []
    for q in ordered:
        questions.append({
            'id': q.id,
            'type': q.type,
            'text': q.text,
            'options': q.options or [],
            'points': q.points,
        })

    return jsonify({
        'id': quiz.id,
        'title': quiz.title,
        'answer_until': quiz.start_date.isoformat() if quiz.start_date else None,
        'status': status,
        'is_closed': is_closed,
        'questions': questions,
    }), 200


@user_bp.route('/user/game-history', methods=['GET'])
@token_required
def get_game_history():
    """Return a compact game history for the current user."""
    current_user_id = int(request.current_user_id)
    user = User.query.get(current_user_id)
    if not user:
        return jsonify({'error': 'User not found'}), 404

    # Playtime logs
    playtime_q = PlaytimeLog.query.filter_by(user_id=current_user_id).order_by(PlaytimeLog.date.desc()).limit(20).all()
    playtime_logs = []
    for p in playtime_q:
        playtime_logs.append({
            'date': p.date.isoformat(),
            'duration_minutes': p.duration_minutes,
        })

    # Mission progress
    missions_q = MissionProgress.query.filter_by(user_id=current_user_id).order_by(MissionProgress.created_at.desc()).limit(20).all()
    missions = []
    for m in missions_q:
        # attempt to resolve mission title
        mission_title = ''
        try:
            mission_obj = Mission.query.get(m.mission_id)
            mission_title = mission_obj.title if mission_obj else ''
        except Exception:
            mission_title = ''
        missions.append({
            'mission_id': m.mission_id,
            'title': mission_title,
            'status': m.status,
            'score': m.score,
            'updated_at': m.updated_at.isoformat() if getattr(m, 'updated_at', None) else None,
        })

    # Quiz results
    quiz_q = QuizResult.query.filter_by(student_id=current_user_id).order_by(QuizResult.created_at.desc()).limit(20).all()
    quizzes = []
    for q in quiz_q:
        quiz_obj = Quiz.query.get(q.quiz_id)
        quizzes.append({
            'quiz_id': q.quiz_id,
            'title': quiz_obj.title if quiz_obj else '',
            'score': q.score,
            'created_at': q.created_at.isoformat() if getattr(q, 'created_at', None) else None,
        })

    # Aggregate simple score: sum of mission + quiz scores
    total_score = 0
    for m in missions:
        total_score += int(m.get('score', 0) or 0)
    for q in quizzes:
        total_score += int(q.get('score', 0) or 0)

    return jsonify({
        'user': user.to_dict(),
        'role': user.role,
        'total_score': total_score,
        'playtime_logs': playtime_logs,
        'missions': missions,
        'quizzes': quizzes,
    }), 200


@user_bp.route('/user/playtime', methods=['POST'])
@token_required
def post_playtime():
    """Record a playtime session for the current user.
    Expects JSON: { "duration_minutes": <int> }
    """
    current_user_id = int(request.current_user_id)
    user = User.query.get(current_user_id)
    if not user:
        return jsonify({'error': 'User not found'}), 404

    data = request.get_json(silent=True) or {}
    try:
        duration = int(data.get('duration_minutes', 0))
    except Exception:
        return jsonify({'error': 'Invalid duration value'}), 400

    if duration <= 0:
        return jsonify({'error': 'Duration must be > 0'}), 400

    # Create log entry
    try:
        log = PlaytimeLog(user_id=current_user_id, duration_minutes=duration)  # pyre-ignore[unexpected-keyword]
        db.session.add(log)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': 'Failed to record playtime', 'details': str(e)}), 500

    return jsonify({'ok': True, 'duration_minutes': duration, 'id': log.id}), 201


@user_bp.route('/student/quiz/<quiz_id>/submit', methods=['POST'])
@token_required
def student_submit_quiz(quiz_id):
    student_id = int(request.current_user_id)
    student = User.query.get(student_id)
    if not student or student.role != 'Student':
        return jsonify({'error': 'Student not found'}), 404

    try:
        quiz_id_int = int(float(quiz_id))
    except ValueError:
        return jsonify({'error': f'Invalid quiz ID format: {quiz_id}'}), 400

    quiz = Quiz.query.get(quiz_id_int)
    if not quiz:
        return jsonify({'error': 'Quiz not found'}), 404

    if getattr(quiz, 'timer_seconds', 0):
        return jsonify({'error': 'This quiz is currently hidden and cannot be submitted'}), 403

    if getattr(quiz, 'status', 'published') != 'published':
        return jsonify({'error': 'This quiz is not open yet'}), 403

    if quiz.start_date:
        now = datetime.now(timezone.utc)
        quiz_deadline = quiz.start_date.replace(tzinfo=timezone.utc) if quiz.start_date.tzinfo is None else quiz.start_date
        if now > quiz_deadline:
            return jsonify({'error': 'This quiz is no longer accepting submissions.'}), 403

    if quiz.class_id != student.class_id:
        return jsonify({'error': 'This quiz is not assigned to your class'}), 403

    existing = QuizResult.query.filter_by(quiz_id=quiz_id_int, student_id=student_id).first()
    if existing:
        return jsonify({'error': 'Already submitted'}), 409

    data = request.get_json(silent=True) or {}
    answers: dict = data.get('answers', {})  # { str(question_id): answer_string }

    clean_answers = {}
    for k, v in answers.items():
        k_str = str(k)
        if k_str.endswith('.0'):
            k_str = k_str[:-2]
        clean_answers[k_str] = v

    ordered = sorted(quiz.questions or [], key=lambda q: (q.order or 0, q.id or 0))
    score = 0
    for q in ordered:
        given_answer = str(clean_answers.get(str(q.id), '')).strip()
        correct_answer = str(q.correct_answer or '').strip()

        if not given_answer or not correct_answer:
            continue

        is_correct = False
        if q.type == 'multiple_choice':
            # For multiple choice, it's a direct index comparison
            is_correct = (given_answer == correct_answer)
        else:  # For 'short_answer', 'identification', etc.
            given_lower = given_answer.lower()
            correct_lower = correct_answer.lower()

            # 1. Direct case-insensitive comparison
            if given_lower == correct_lower:
                is_correct = True
            # 2. Comparison ignoring all whitespace (e.g., "y=2x-3" vs "y = 2x - 3")
            elif given_lower.replace(" ", "") == correct_lower.replace(" ", ""):
                is_correct = True
            # 3. Keyword-based check for descriptive answers (e.g., "prokaryotic and eukaryotic")
            else:
                import re
                ignore_words = {'and', 'or', 'the', 'a', 'an', 'is', 'are', 'of', 'in', 'to', 'for'}
                correct_words = set(re.sub(r'[^\w\s]', '', correct_lower).split()) - ignore_words
                given_words = set(re.sub(r'[^\w\s]', '', given_lower).split()) - ignore_words
                if correct_words and correct_words.issubset(given_words):
                    is_correct = True

        if is_correct:
            score += int(q.points or 1)

    total_points = sum(int(q.points or 1) for q in ordered)
    if total_points == 0:
        total_points = max(1, len(ordered))
    
    percentage = round((score / total_points) * 100.0, 1)

    result = QuizResult(quiz_id=quiz_id_int, student_id=student_id, score=percentage)  # pyre-ignore[unexpected-keyword]
    db.session.add(result)
    db.session.commit()

    return jsonify({
        'score': f"{percentage}%",
        'raw_score': score,
        'total_points': total_points,
        'questions_count': len(ordered),
        'result_id': result.id,
    }), 201


@user_bp.route('/student/class/options', methods=['GET'])
@token_required
def student_class_options():
    student_id = int(request.current_user_id)
    student = User.query.get(student_id)
    if not student or student.role != 'Student':
        return jsonify({'error': 'Student not found'}), 404

    classes = Class.query.order_by(Class.name.asc()).all()
    sections = [str(classroom.name).strip() for classroom in classes if str(classroom.name).strip() != '']

    return jsonify({'sections': sections}), 200


@user_bp.route('/student/class/join', methods=['POST'])
@token_required
def student_class_join():
    student_id = int(request.current_user_id)
    student = User.query.get(student_id)
    if not student or student.role != 'Student':
        return jsonify({'error': 'Student not found'}), 404

    data = request.json
    section_name = (data.get('section') or '').strip() if data else ''
    if not section_name:
        return jsonify({'error': 'Section name is required'}), 400

    # Find the class by section name
    classroom = Class.query.filter_by(name=section_name).first()
    if not classroom:
        return jsonify({'error': 'Class section not found'}), 404

    # Assign student to this class
    student.class_id = classroom.id
    db.session.commit()

    return jsonify({'message': 'Successfully joined class', 'section': classroom.name}), 200


@user_bp.route('/student/class', methods=['GET'])
@token_required
def student_class_info():
    student_id = int(request.current_user_id)
    student = User.query.get(student_id)
    if not student or student.role != 'Student':
        return jsonify({'error': 'Student not found'}), 404

    if not student.class_id:
        return jsonify({'error': 'You are not assigned to a class yet'}), 404

    classroom = Class.query.get(student.class_id)
    if not classroom:
        return jsonify({'error': 'Class not found'}), 404

    teacher = User.query.get(classroom.teacher_id)
    teacher_name = ''
    if teacher:
        full = f"{(teacher.first_name or '').strip()} {(teacher.last_name or '').strip()}".strip()
        teacher_name = full if full else teacher.username

    # All quizzes assigned to this class
    all_quizzes_raw = Quiz.query.filter_by(class_id=classroom.id).order_by(Quiz.id.asc()).all()
    all_quizzes = [
        q
        for q in all_quizzes_raw
        if not getattr(q, 'timer_seconds', 0) and getattr(q, 'status', 'published') == 'published'
    ]
    
    # All announcements for this class
    all_announcements_raw = Announcement.query.filter_by(class_id=classroom.id).order_by(Announcement.created_at.desc()).all()
    all_announcements = [a for a in all_announcements_raw if not getattr(a, 'is_hidden', False)]

    announcement_payload = [{
        'id': a.id,
        'title': a.title,
        'message': a.message,
        'created_at': a.created_at.isoformat() if a.created_at else None
    } for a in all_announcements]

    # Results this student already submitted
    result_rows = QuizResult.query.filter_by(student_id=student_id).all()
    result_by_quiz = {r.quiz_id: r for r in result_rows}

    # Feedback messages keyed by quiz_result_id
    result_ids = [r.id for r in result_rows]
    feedback_by_result: dict = {}
    if result_ids:
        messages = (
            Message.query
            .filter(Message.receiver_id == student_id, Message.quiz_result_id.in_(result_ids))
            .order_by(Message.created_at.desc())
            .all()
        )
        for msg in messages:
            # Keep only the most recent message per result (already ordered desc)
            if msg.quiz_result_id not in feedback_by_result:
                feedback_by_result[msg.quiz_result_id] = msg.content

    pending = []
    completed = []
    for quiz in all_quizzes:
        status = 'Open'
        is_closed = False
        if quiz.start_date:
            now = datetime.now(timezone.utc)
            quiz_deadline = quiz.start_date.replace(tzinfo=timezone.utc) if quiz.start_date.tzinfo is None else quiz.start_date
            if now > quiz_deadline:
                is_closed = True
                status = 'Closed'
            elif (quiz_deadline - now).total_seconds() < 3600 * 24:
                status = 'Closing Soon'

        result = result_by_quiz.get(quiz.id)
        if result is None and not is_closed:
            pending.append({
                'id': quiz.id,
                'public_id': quiz.public_id,
                'title': quiz.title,
                'answer_until': quiz.start_date.isoformat() if quiz.start_date else None,
                'status': status,
                'is_closed': is_closed,
                'questions_count': len(quiz.questions or []),
                'completed': False,
            })
        elif result is not None:
            completed.append({
                'id': quiz.id,
                'public_id': quiz.public_id,
                'title': quiz.title,
                'score': f"{result.score}%",
                'feedback': feedback_by_result.get(result.id, ''),
                'answer_until': quiz.start_date.isoformat() if quiz.start_date else None,
                'status': status,
                'is_closed': is_closed,
                'completed': True,
            })

    return jsonify({
        'section': classroom.name,
        'teacher_name': teacher_name,
        'quizzes': pending + completed,
        'announcements': announcement_payload,
    }), 200


@user_bp.route('/student/location-quizzes', methods=['GET'])
@token_required
def student_location_quizzes():
    """Return teacher-authored class quiz questions usable during in-game location events."""
    student_id = int(request.current_user_id)
    student = User.query.get(student_id)
    if not student or student.role != 'Student':
        return jsonify({'error': 'Student not found'}), 404

    if not student.class_id:
        return jsonify({'error': 'You are not assigned to a class yet'}), 404

    quizzes = (
        Quiz.query
        .filter_by(class_id=student.class_id)
        .order_by(Quiz.id.asc())
        .all()
    )

    question_bank = []
    for quiz in quizzes:
        if getattr(quiz, 'timer_seconds', 0):
            continue
        if getattr(quiz, 'status', 'published') != 'published':
            continue

        ordered_questions = sorted(quiz.questions or [], key=lambda q: (q.order or 0, q.id or 0))
        for question in ordered_questions:
            q_type = (question.type or '').strip().lower()
            q_text = (question.text or '').strip()
            if not q_text:
                continue

            options = []
            correct_index = -1
            raw_correct = question.correct_answer

            if q_type == 'multiple_choice':
                options = [str(opt).strip() for opt in (question.options or []) if str(opt).strip()]
                if len(options) < 2:
                    continue

                if raw_correct is None:
                    continue

                try:
                    correct_index = int(str(raw_correct).strip())
                except ValueError:
                    normalized_answer = str(raw_correct).strip().lower()
                    for idx, opt in enumerate(options):
                        if opt.strip().lower() == normalized_answer:
                            correct_index = idx
                            break

                if correct_index < 0 or correct_index >= len(options):
                    continue

            elif q_type == 'true_false':
                options = ['True', 'False']
                normalized_answer = str(raw_correct).strip().lower()
                if normalized_answer in ('true', '1', 'yes'):
                    correct_index = 0
                elif normalized_answer in ('false', '0', 'no'):
                    correct_index = 1
                else:
                    continue
            else:
                # Skip identification/essay for round-based multiple-choice UI.
                continue

            question_bank.append({
                'quiz_id': quiz.id,
                'quiz_public_id': quiz.public_id,
                'quiz_title': quiz.title,
                'question_id': question.id,
                'question': q_text,
                'options': options,
                'correct_answer': correct_index,
                'points': int(question.points or 1),
            })

    return jsonify({
        'count': len(question_bank),
        'questions': question_bank,
    }), 200
