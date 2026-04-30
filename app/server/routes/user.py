from flask import Blueprint, request, jsonify
from werkzeug.security import generate_password_hash, check_password_hash
from app.server.database import db
from app.server.models.user import Class, Message, Quiz, QuizResult, User
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
    db.session.commit()

    return jsonify({'message': 'Password changed successfully'}), 200

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
        'timer_seconds': quiz.timer_seconds,
        'questions': questions,
    }), 200


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

    result = QuizResult(quiz_id=quiz_id_int, student_id=student_id, score=score)
    db.session.add(result)
    db.session.commit()

    return jsonify({
        'score': score,
        'total_points': sum(int(q.points or 1) for q in ordered),
        'questions_count': len(ordered),
        'result_id': result.id,
    }), 201


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
    all_quizzes = Quiz.query.filter_by(class_id=classroom.id).order_by(Quiz.start_date.asc()).all()

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
        result = result_by_quiz.get(quiz.id)
        if result is None:
            pending.append({
                'id': quiz.id,
                'public_id': quiz.public_id,
                'title': quiz.title,
                'due_date': quiz.start_date.strftime('%b %d, %Y') if quiz.start_date else '—',
                'questions_count': len(quiz.questions or []),
                'completed': False,
            })
        else:
            total_points = sum(q.points or 1 for q in (quiz.questions or []))
            if total_points == 0:
                total_points = len(quiz.questions or [])

            completed.append({
                'id': quiz.id,
                'public_id': quiz.public_id,
                'title': quiz.title,
                'score': f"{result.score} / {total_points}",
                'feedback': feedback_by_result.get(result.id, ''),
                'completed': True,
            })

    return jsonify({
        'section': classroom.name,
        'teacher_name': teacher_name,
        'quizzes': pending + completed,
    }), 200