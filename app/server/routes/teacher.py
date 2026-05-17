from datetime import datetime, timezone
import os
import secrets
import time

from flask import Blueprint, jsonify, request
from sqlalchemy import case, func

from app.auth.auth_bearer import token_required
from app.server.database import db
from app.server.models.user import (
    Class,
    GameServer,
    Message,
    MissionProgress,
    PlaytimeLog,
    Quiz,
    QuizQuestion,
    QuizResult,
    User,
)
from app.server.models.announcement import Announcement

teacher_bp = Blueprint('teacher', __name__)


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
    }


def _serialize_quiz_question(question: QuizQuestion):
    return {
        'id': question.id,
        'public_id': question.public_id,
        'type': question.type,
        'text': question.text,
        'description': getattr(question, 'description', None),
        'options': question.options,
        'correct_answer': question.correct_answer,
        'points': question.points,
        'order': question.order,
        'required': bool(getattr(question, 'required', True)),
    }


def _serialize_quiz(quiz: Quiz):
    ordered_questions = sorted(quiz.questions or [], key=lambda question: (question.order or 0, question.id or 0))
    deadline = quiz.start_date
    now = datetime.now(timezone.utc)
    if deadline and deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=timezone.utc)
    saved_status = getattr(quiz, 'status', None) or 'published'
    status = 'closed' if saved_status == 'published' and deadline and deadline <= now else saved_status
    return {
        'id': quiz.id,
        'public_id': quiz.public_id,
        'teacher_id': quiz.teacher_id,
        'class_id': quiz.class_id,
        'title': quiz.title,
        'description': getattr(quiz, 'description', None),
        'status': status,
        'is_hidden': bool(getattr(quiz, 'timer_seconds', 0)), # Renamed, repurposed for is_hidden
        'answer_until': quiz.start_date.isoformat() if quiz.start_date else None,
        'allow_retakes': bool(getattr(quiz, 'allow_retakes', True)),
        'shuffle_questions': bool(getattr(quiz, 'shuffle_questions', False)),
        'shuffle_choices': bool(getattr(quiz, 'shuffle_choices', False)),
        'auto_grade': bool(getattr(quiz, 'auto_grade', True)),
        'show_correct_answers': bool(getattr(quiz, 'show_correct_answers', False)),
        'require_all_questions': bool(getattr(quiz, 'require_all_questions', True)),
        'instant_feedback': bool(getattr(quiz, 'instant_feedback', False)),
        'passing_score': getattr(quiz, 'passing_score', 70),
        'questions_count': len(ordered_questions),
        'questions': [_serialize_quiz_question(question) for question in ordered_questions],
        'created_at': quiz.created_at.isoformat() if quiz.created_at else None,
        'submission_count': getattr(quiz, 'submission_count', 0), # Placeholder for now, requires a query
    }


def _parse_quiz_datetime(value):
    if not value:
        return None, None

    try:
        parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except ValueError:
        return None, 'Answer Until must be a valid date and time.'

    return parsed, None


def _normalize_quiz_payload(data, teacher_id, existing_quiz=None):
    title = (data.get('title') or '').strip()
    status = (data.get('status') or getattr(existing_quiz, 'status', None) or 'published').strip().lower()
    class_id = data.get('class_id', getattr(existing_quiz, 'class_id', None))
    questions = data.get('questions', [])

    if status not in {'draft', 'published', 'closed'}:
        return None, ('Quiz status must be Draft, Published, or Closed.', 400)

    if not title:
        return None, ('Quiz title is required.', 400)

    if class_id:
        classroom = Class.query.filter_by(id=class_id, teacher_id=teacher_id).first()
        if not classroom:
            return None, ('Class not found or not owned by teacher.', 404)
        class_id = classroom.id

    if not isinstance(questions, list):
        return None, ('Quiz questions must be a list.', 400)

    valid_questions = []
    for idx, question in enumerate(questions):
        if not isinstance(question, dict):
            continue
        question_text = (question.get('text') or '').strip()
        if not question_text:
            continue
        question_type = str(question.get('type') or 'multiple_choice').strip()
        if question_type not in {'multiple_choice', 'true_false', 'identification'}:
            return None, (f'Question {idx + 1} has an unsupported type.', 400)
        options = question.get('options') if isinstance(question.get('options'), list) else []
        if question_type == 'multiple_choice' and status == 'published' and len([opt for opt in options if str(opt).strip()]) < 2:
            return None, (f'Question {idx + 1} needs at least two options.', 400)
        valid_questions.append((idx, question_text, question_type, options, question))

    if status == 'published' and not valid_questions:
        return None, ('Add at least one question before publishing.', 400)

    answer_until, datetime_error = _parse_quiz_datetime(data.get('answer_until'))
    if datetime_error:
        return None, (datetime_error, 400)

    if status == 'published' and answer_until:
        comparable_deadline = answer_until if answer_until.tzinfo else answer_until.replace(tzinfo=timezone.utc)
        if comparable_deadline <= datetime.now(timezone.utc):
            return None, ('Answer Until must be in the future before publishing.', 400)

    return {
        'title': title,
        'class_id': class_id,
        'questions': valid_questions,
        'answer_until': answer_until,
        'description': data.get('description') or '',
        'status': status,
        'is_hidden': bool(data.get('is_hidden', getattr(existing_quiz, 'timer_seconds', 0) if existing_quiz else False)),
        'allow_retakes': bool(data.get('allow_retakes', getattr(existing_quiz, 'allow_retakes', True))),
        'shuffle_questions': bool(data.get('shuffle_questions', getattr(existing_quiz, 'shuffle_questions', False))),
        'shuffle_choices': bool(data.get('shuffle_choices', getattr(existing_quiz, 'shuffle_choices', False))),
        'auto_grade': bool(data.get('auto_grade', getattr(existing_quiz, 'auto_grade', True))),
        'show_correct_answers': bool(data.get('show_correct_answers', getattr(existing_quiz, 'show_correct_answers', False))),
        'require_all_questions': bool(data.get('require_all_questions', getattr(existing_quiz, 'require_all_questions', True))),
        'instant_feedback': bool(data.get('instant_feedback', getattr(existing_quiz, 'instant_feedback', False))),
        'passing_score': int(data.get('passing_score') or getattr(existing_quiz, 'passing_score', 70) or 70),
    }, None


def _apply_quiz_payload(quiz, payload):
    quiz.title = payload['title']
    quiz.description = payload['description']
    quiz.class_id = payload['class_id']
    quiz.start_date = payload['answer_until']
    quiz.status = payload['status']
    quiz.timer_seconds = 1 if payload['is_hidden'] or payload['status'] == 'draft' else 0
    quiz.allow_retakes = payload['allow_retakes']
    quiz.shuffle_questions = payload['shuffle_questions']
    quiz.shuffle_choices = payload['shuffle_choices']
    quiz.auto_grade = payload['auto_grade']
    quiz.show_correct_answers = payload['show_correct_answers']
    quiz.require_all_questions = payload['require_all_questions']
    quiz.instant_feedback = payload['instant_feedback']
    quiz.passing_score = payload['passing_score']


def _replace_quiz_questions(quiz, valid_questions):
    QuizQuestion.query.filter_by(quiz_id=quiz.id).delete()

    for order, (_, question_text, question_type, options, source) in enumerate(valid_questions):
        db.session.add(
            QuizQuestion(
                quiz_id=quiz.id,
                type=question_type,
                text=question_text,
                description=(source.get('description') or '').strip() or None,
                options=options if question_type == 'multiple_choice' else [],
                correct_answer=str(source.get('correct_answer', '')).strip() or None,
                points=int(source.get('points', 1)) if source.get('points') else 1,
                order=order,
                required=bool(source.get('required', True)),
            )
        )

def _serialize_lobby(lobby: GameServer, classroom, teacher_id: int):
    return {
        'id': lobby.id,
        'public_id': lobby.public_id,
        'name': lobby.name,
        'ip': lobby.ip,
        'port': lobby.port,
        'player_count': lobby.player_count,
        'required_players': lobby.required_players,
        'persistent': lobby.persistent,
        'owner_teacher_id': lobby.owner_teacher_id,
        'teacher_lobby': bool(lobby.persistent and lobby.owner_teacher_id is not None),
        'class_id': lobby.class_id,
        'class_public_id': classroom.public_id if classroom else None,
        'class_name': classroom.name if classroom else None,
        'teacher_id': teacher_id,
    }


def _generated_lobby_host():
    configured_host = os.getenv('LOBBY_HOST_IP', '').strip()
    if configured_host:
        return configured_host

    host = (request.host or '').split(':', 1)[0].strip()
    if host:
        return host

    return (request.remote_addr or '127.0.0.1').strip()


def _port_range():
    try:
        port_min = int(os.getenv('LOBBY_PORT_MIN', '50000'))
        port_max = int(os.getenv('LOBBY_PORT_MAX', '59999'))
    except ValueError:
        return 50000, 59999

    if port_min <= 0 or port_max > 65535 or port_min > port_max:
        return 50000, 59999

    return port_min, port_max


def _allocate_lobby_endpoint():
    host = _generated_lobby_host()
    port_min, port_max = _port_range()
    span = port_max - port_min + 1

    for _ in range(min(span, 100)):
        port = port_min + secrets.randbelow(span)
        if not GameServer.query.filter_by(ip=host, port=port).first():
            return host, port

    for port in range(port_min, port_max + 1):
        if not GameServer.query.filter_by(ip=host, port=port).first():
            return host, port

    raise RuntimeError('No available lobby endpoints')


def _teacher_guard():
    if request.current_user_role != 'Teacher':
        return jsonify({'error': 'Unauthorized'}), 403
    return None


@teacher_bp.route('/teacher/class/overview', methods=['GET'])
@token_required
def class_overview():
    guard = _teacher_guard()
    if guard:
        return guard

    teacher_id = int(request.current_user_id)
    teacher = User.query.get(teacher_id)
    teacher_profile = {
        'id': teacher.id,
        'public_id': teacher.public_id,
        'username': teacher.username,
        'first_name': teacher.first_name,
        'last_name': teacher.last_name,
        'email': teacher.email,
    } if teacher else None
    classes = Class.query.filter_by(teacher_id=teacher_id).all()

    if not classes:
        return jsonify({'profile': teacher_profile, 'classes': [], 'students': [], 'parents': []}), 200

    class_ids = [classroom.id for classroom in classes]

    students = (
        User.query.filter(User.class_id.in_(class_ids), User.role == 'Student')
        .order_by(User.class_id.asc(), User.username.asc())
        .all()
    )

    if not students:
        class_payload = [
            {
                'id': classroom.id,
                'public_id': classroom.public_id,
                'name': classroom.name,
                'teacher_id': classroom.teacher_id,
            }
            for classroom in classes
        ]
        return jsonify({'profile': teacher_profile, 'classes': class_payload, 'students': [], 'parents': []}), 200

    student_ids = [student.id for student in students]

    mission_rows = (
        db.session.query(
            MissionProgress.user_id,
            func.count(MissionProgress.id).label('missions_total'),
            func.sum(case((MissionProgress.status == 'completed', 1), else_=0)).label('missions_completed'),
            func.coalesce(func.avg(MissionProgress.score), 0).label('mission_avg_score'),
            func.coalesce(func.max(MissionProgress.score), 0).label('mission_best_score'),
        )
        .filter(MissionProgress.user_id.in_(student_ids))
        .group_by(MissionProgress.user_id)
        .all()
    )

    quiz_rows = (
        db.session.query(
            QuizResult.student_id,
            func.count(QuizResult.id).label('quizzes_taken'),
            func.coalesce(func.avg(QuizResult.score), 0).label('quiz_avg_score'),
            func.coalesce(func.max(QuizResult.score), 0).label('quiz_best_score'),
        )
        .filter(QuizResult.student_id.in_(student_ids))
        .group_by(QuizResult.student_id)
        .all()
    )

    mission_by_user = {
        row.user_id: {
            'missions_total': int(row.missions_total or 0),
            'missions_completed': int(row.missions_completed or 0),
            'mission_avg_score': float(row.mission_avg_score or 0),
            'mission_best_score': int(row.mission_best_score or 0),
        }
        for row in mission_rows
    }

    quiz_by_user = {
        row.student_id: {
            'quizzes_taken': int(row.quizzes_taken or 0),
            'quiz_avg_score': float(row.quiz_avg_score or 0),
            'quiz_best_score': int(row.quiz_best_score or 0),
        }
        for row in quiz_rows
    }

    parent_ids = sorted({student.parent_id for student in students if student.parent_id is not None})
    parent_rows = []
    if parent_ids:
        parent_rows = (
            User.query.filter(User.id.in_(parent_ids), User.role == 'Parent')
            .order_by(User.first_name.asc(), User.last_name.asc(), User.username.asc())
            .all()
        )
    parent_map = {parent.id: parent for parent in parent_rows}

    parent_child_counts = {parent_id: 0 for parent_id in parent_ids}
    for student in students:
        if student.parent_id in parent_child_counts:
            parent_child_counts[student.parent_id] += 1

    parent_payload = []
    for parent in parent_rows:
        full_name = f"{(parent.first_name or '').strip()} {(parent.last_name or '').strip()}".strip()
        if not full_name:
            full_name = parent.username
        parent_payload.append(
            {
                'parent_id': parent.id,
                'parent_public_id': parent.public_id,
                'parent_name': full_name,
                'username': parent.username,
                'children_count': int(parent_child_counts.get(parent.id, 0)),
            }
        )

    class_name_by_id = {classroom.id: classroom.name for classroom in classes}
    class_payload = [
        {
            'id': classroom.id,
            'public_id': classroom.public_id,
            'name': classroom.name,
            'teacher_id': classroom.teacher_id,
        }
        for classroom in classes
    ]

    student_payload = []
    for student in students:
        mission_summary = mission_by_user.get(
            student.id,
            {
                'missions_total': 0,
                'missions_completed': 0,
                'mission_avg_score': 0.0,
                'mission_best_score': 0,
            },
        )
        quiz_summary = quiz_by_user.get(
            student.id,
            {
                'quizzes_taken': 0,
                'quiz_avg_score': 0.0,
                'quiz_best_score': 0,
            },
        )

        student_payload.append(
                {
                    'id': student.id,
                    'student_id': student.id,
                    'student_public_id': student.public_id,
                    'username': student.username,
                    'first_name': student.first_name,
                    'last_name': student.last_name,
                    'class_id': student.class_id,
                    'class_name': class_name_by_id.get(student.class_id),
                    'parent_id': student.parent_id,
                    'parent_public_id': parent_map.get(student.parent_id).public_id if parent_map.get(student.parent_id) else None,
                    'parent_name': (
                        (
                            f"{(parent_map.get(student.parent_id).first_name or '').strip()} {(parent_map.get(student.parent_id).last_name or '').strip()}"
                        ).strip()
                        if parent_map.get(student.parent_id)
                        else None
                    )
                    or (parent_map.get(student.parent_id).username if parent_map.get(student.parent_id) else None),
                    'missions': mission_summary,
                    'quizzes': quiz_summary,
                }
        )

    return jsonify({'profile': teacher_profile, 'classes': class_payload, 'students': student_payload, 'parents': parent_payload}), 200


@teacher_bp.route('/teacher/student/<string:student_public_id>', methods=['GET'])
@token_required
def student_summary(student_public_id: str):
    guard = _teacher_guard()
    if guard:
        return guard

    teacher_id = int(request.current_user_id)

    student = User.query.filter_by(public_id=student_public_id, role='Student').first()
    if not student:
        return jsonify({'error': 'Student not found'}), 404

    classroom = Class.query.filter_by(id=student.class_id, teacher_id=teacher_id).first()
    if not classroom:
        return jsonify({'error': 'Student is not in your class'}), 403

    progress_rows = (
        MissionProgress.query.filter_by(user_id=student.id)
        .order_by(MissionProgress.updated_at.desc())
        .all()
    )

    quiz_rows = QuizResult.query.filter_by(student_id=student.id).all()
    playtime_rows = (
        PlaytimeLog.query.filter_by(user_id=student.id)
        .order_by(PlaytimeLog.date.desc())
        .all()
    )

    mission_progress = [
        {
            'mission_id': row.mission_id,
            'status': row.status,
            'score': row.score,
            'updated_at': row.updated_at.isoformat() if row.updated_at else None,
        }
        for row in progress_rows
    ]

    quiz_results = [
        {'quiz_id': row.quiz_id, 'score': row.score, 'updated_at': row.updated_at.isoformat() if row.updated_at else None}
        for row in quiz_rows
    ]

    playtime_logs = [{'date': str(row.date), 'minutes': row.duration_minutes} for row in playtime_rows]
    total_playtime_minutes = sum(row.duration_minutes or 0 for row in playtime_rows)

    return jsonify(
        {
            'student': {
                'id': student.id,
                'public_id': student.public_id,
                'username': student.username,
                'class_id': student.class_id,
                'class_public_id': classroom.public_id,
                'class_name': classroom.name,
            },
            'summary': {
                'mission_count': len(mission_progress),
                'quiz_count': len(quiz_results),
                'mission_average_score': float(
                    sum(item['score'] or 0 for item in mission_progress) / len(mission_progress)
                )
                if mission_progress
                else 0.0,
                'quiz_average_score': float(sum(item['score'] for item in quiz_results) / len(quiz_results))
                if quiz_results
                else 0.0,
                'total_playtime_minutes': total_playtime_minutes,
            },
            'missions': mission_progress,
            'quiz_results': quiz_results,
            'playtime_logs': playtime_logs,
        }
    ), 200


@teacher_bp.route('/teacher/quiz', methods=['POST'])
@token_required
def create_quiz():
    guard = _teacher_guard()
    if guard:
        return guard

    data = request.get_json(silent=True) or {}
    teacher_id = int(request.current_user_id)

    payload, error = _normalize_quiz_payload(data, teacher_id)
    if error:
        message, status_code = error
        return jsonify({'error': message}), status_code

    quiz = Quiz(
        teacher_id=teacher_id,
        class_id=payload['class_id'],
        title=payload['title'],
    )

    db.session.add(quiz)
    db.session.flush()

    _apply_quiz_payload(quiz, payload)
    _replace_quiz_questions(quiz, payload['questions'])

    db.session.commit()

    return jsonify(
        {
            'message': 'Draft saved successfully' if payload['status'] == 'draft' else 'Quiz published successfully',
            'quiz': _serialize_quiz(quiz),
        }
    ), 201


@teacher_bp.route('/teacher/quizzes', methods=['GET'])
@token_required
def list_teacher_quizzes():
    guard = _teacher_guard()
    if guard:
        return guard
    
    # Add student submission count to each quiz
    quiz_submissions = db.session.query(QuizResult.quiz_id, func.count(QuizResult.id).label('submission_count')) \
        .group_by(QuizResult.quiz_id).all()
    submission_counts = {item.quiz_id: item.submission_count for item in quiz_submissions}
    
    teacher_id = int(request.current_user_id)
    quizzes = (
        Quiz.query.filter_by(teacher_id=teacher_id)
        .order_by(Quiz.id.desc())
        .all()
    )

    serialized_quizzes = []
    for quiz in quizzes:
        quiz.submission_count = submission_counts.get(quiz.id, 0)
        serialized_quizzes.append(_serialize_quiz(quiz))
    return jsonify({'quizzes': serialized_quizzes}), 200


@teacher_bp.route('/teacher/quiz/results', methods=['GET'])
@token_required
def list_teacher_quiz_results():
    guard = _teacher_guard()
    if guard:
        return guard

    teacher_id = int(request.current_user_id)
    class_id = request.args.get('class_id', type=int)
    quiz_id = request.args.get('quiz_id', type=int)

    teacher_classes = Class.query.filter_by(teacher_id=teacher_id).all()
    teacher_class_ids = [classroom.id for classroom in teacher_classes]
    if not teacher_class_ids:
        return jsonify({'results': []}), 200

    if class_id is not None and class_id not in teacher_class_ids:
        return jsonify({'error': 'Class not found or not owned by teacher'}), 404

    selected_quiz = None
    if quiz_id is not None:
        selected_quiz = Quiz.query.filter_by(id=quiz_id, teacher_id=teacher_id).first()
        if not selected_quiz:
            return jsonify({'error': 'Quiz not found or not owned by teacher'}), 404

    active_class_ids = [class_id] if class_id is not None else teacher_class_ids
    query = (
        db.session.query(QuizResult, Quiz, User)
        .join(Quiz, Quiz.id == QuizResult.quiz_id)
        .join(User, User.id == QuizResult.student_id)
        .filter(Quiz.teacher_id == teacher_id, User.class_id.in_(active_class_ids))
    )
    if quiz_id is not None:
        query = query.filter(Quiz.id == quiz_id)
    rows = query.order_by(QuizResult.updated_at.desc(), QuizResult.id.desc()).all()

    parent_ids = sorted({student.parent_id for _, _, student in rows if student.parent_id is not None})
    parent_map = {}
    if parent_ids:
        parent_rows = User.query.filter(User.id.in_(parent_ids), User.role == 'Parent').all()
        parent_map = {parent.id: parent for parent in parent_rows}

    class_map = {classroom.id: classroom for classroom in teacher_classes}
    payload = []
    for result, quiz, student in rows:
        parent = parent_map.get(student.parent_id)
        classroom = class_map.get(student.class_id)
        submitted_at = result.updated_at or result.created_at
        student_name = f"{(student.first_name or '').strip()} {(student.last_name or '').strip()}".strip() or student.username
        parent_name = None
        if parent:
            parent_name = f"{(parent.first_name or '').strip()} {(parent.last_name or '').strip()}".strip() or parent.username

        payload.append(
            {
                'id': result.id,
                'public_id': result.public_id,
                'quiz_id': quiz.id,
                'quiz_public_id': quiz.public_id,
                'quiz_title': quiz.title,
                'quiz_class_id': quiz.class_id,
                'student_id': student.id,
                'student_public_id': student.public_id,
                'student_name': student_name,
                'student_username': student.username,
                'class_id': student.class_id,
                'class_name': classroom.name if classroom else None,
                'parent_id': parent.id if parent else None,
                'parent_public_id': parent.public_id if parent else None,
                'parent_name': parent_name,
                'score': result.score,
                'submitted_at': submitted_at.isoformat() if submitted_at else None,
                'questions_count': len(quiz.questions or []),
            }
        )

    return jsonify({'quiz': _serialize_quiz(selected_quiz) if selected_quiz else None, 'results': payload}), 200


@teacher_bp.route('/teacher/quiz/result/<int:result_id>', methods=['DELETE'])
@token_required
def delete_quiz_result(result_id: int):
    """Allows a teacher to delete a student's quiz result, enabling a retake."""
    guard = _teacher_guard()
    if guard:
        return guard

    teacher_id = int(request.current_user_id)

    result = QuizResult.query.get(result_id)
    if not result:
        return jsonify({'error': 'Quiz result not found'}), 404

    # Verify the teacher owns the quiz associated with this result
    quiz = Quiz.query.filter_by(id=result.quiz_id, teacher_id=teacher_id).first()
    if not quiz:
        return jsonify({'error': 'Quiz result does not belong to a quiz you own'}), 403

    db.session.delete(result)
    db.session.commit()

    return jsonify({'message': 'Quiz result deleted successfully. The student can now retake the quiz.', 'result_id': result_id}), 200


@teacher_bp.route('/teacher/quiz/<int:quiz_id>/retake', methods=['POST'])
@token_required
def allow_quiz_retake(quiz_id: int):
    """Allows a student to retake a quiz by deleting their existing result for that specific quiz."""
    guard = _teacher_guard()
    if guard:
        return guard

    teacher_id = int(request.current_user_id)

    data = request.get_json(silent=True) or {}
    student_id = data.get('student_id')
    if not student_id:
        return jsonify({'error': 'student_id is required in the request body'}), 400

    # Verify the teacher owns the quiz
    quiz = Quiz.query.filter_by(id=quiz_id, teacher_id=teacher_id).first()
    if not quiz:
        return jsonify({'error': 'Quiz not found or not owned by you'}), 404

    result = QuizResult.query.filter_by(quiz_id=quiz_id, student_id=student_id).first()
    if not result:
        return jsonify({'error': 'No existing quiz result found for this student to allow a retake'}), 404

    db.session.delete(result)
    db.session.commit()

    return jsonify({'message': 'Quiz result deleted. The student can now retake the quiz.', 'quiz_id': quiz_id, 'student_id': student_id}), 200

@teacher_bp.route('/teacher/quiz/<int:quiz_id>', methods=['GET', 'PATCH', 'DELETE'])
@token_required
def manage_teacher_quiz(quiz_id: int):
    guard = _teacher_guard()
    if guard:
        return guard

    teacher_id = int(request.current_user_id)
    quiz = Quiz.query.filter_by(id=quiz_id, teacher_id=teacher_id).first()
    if not quiz:
        return jsonify({'error': 'Quiz not found or not owned by teacher'}), 404

    if request.method == 'GET':
        return jsonify({'quiz': _serialize_quiz(quiz)}), 200

    if request.method == 'DELETE':
        # Manually delete associated results as there's no cascade from Quiz -> QuizResult
        QuizResult.query.filter_by(quiz_id=quiz.id).delete()

        # Questions are deleted via cascade='all, delete-orphan' on the Quiz.questions relationship
        db.session.delete(quiz)
        db.session.commit()
        return jsonify({'message': 'Quiz and all its results deleted successfully', 'quiz_id': quiz_id}), 200

    data = request.get_json(silent=True) or {}
    payload, error = _normalize_quiz_payload(data, teacher_id, quiz)
    if error:
        message, status_code = error
        return jsonify({'error': message}), status_code

    _apply_quiz_payload(quiz, payload)
    _replace_quiz_questions(quiz, payload['questions'])

    db.session.commit()
    db.session.refresh(quiz)

    return jsonify({'message': 'Draft saved successfully' if payload['status'] == 'draft' else 'Quiz updated successfully', 'quiz': _serialize_quiz(quiz)}), 200


@teacher_bp.route('/teacher/quiz/<int:quiz_id>/toggle_visibility', methods=['PATCH'])
@token_required
def toggle_quiz_visibility(quiz_id: int):
    guard = _teacher_guard()
    if guard:
        return guard
    teacher_id = int(request.current_user_id)
    quiz = Quiz.query.filter_by(id=quiz_id, teacher_id=teacher_id).first()
    if not quiz:
        return jsonify({'error': 'Quiz not found or not owned by teacher'}), 404
    current_hidden = bool(getattr(quiz, 'timer_seconds', 0))
    quiz.timer_seconds = 0 if current_hidden else 1
    db.session.commit()
    return jsonify({'message': 'Visibility toggled', 'is_hidden': not current_hidden}), 200


@teacher_bp.route('/teacher/message', methods=['POST'])
@token_required
def send_message():
    guard = _teacher_guard()
    if guard:
        return guard

    data = request.get_json(silent=True) or {}
    # Accept both receiver_public_id and student_id (for parent communication)
    receiver_public_id = (data.get('receiver_public_id') or data.get('student_id') or '').strip()
    content = (data.get('content') or data.get('message') or '').strip()
    student_name = str(data.get('student_name') or '').strip()
    class_name = str(data.get('class_name') or '').strip()
    quiz_result_id = data.get('quiz_result_id')

    if not receiver_public_id:
        return jsonify({'error': 'receiver_public_id or student_id is required'}), 400

    if not content:
        return jsonify({'error': 'content or message is required'}), 400

    teacher_id = int(request.current_user_id)

    # Try to find receiver by public_id first, then by ID
    receiver = User.query.filter_by(public_id=receiver_public_id).first()
    if not receiver:
        # Try to find by ID (for when frontend sends numeric student_id)
        try:
            receiver_id = int(receiver_public_id)
            receiver = User.query.get(receiver_id)
        except (ValueError, TypeError):
            pass
    
    if not receiver:
        return jsonify({'error': 'Receiver not found'}), 404

    if receiver.role not in ('Student', 'Parent'):
        return jsonify({'error': 'Receiver must be a Student or Parent'}), 400

    if receiver.role == 'Parent' and (not student_name or not class_name):
        return jsonify({'error': 'student_name and class_name are required for parent messages'}), 400

    if receiver.role == 'Student':
        valid_student = (
            db.session.query(User.id)
            .join(Class, Class.id == User.class_id)
            .filter(User.id == receiver.id, User.role == 'Student', Class.teacher_id == teacher_id)
            .first()
        )
        if not valid_student:
            return jsonify({'error': 'Student is not in your class'}), 403
    else:
        teacher_class_ids = [c.id for c in Class.query.filter_by(teacher_id=teacher_id).all()]
        if not teacher_class_ids:
            return jsonify({'error': 'You have no assigned classes'}), 403

        student_exists = (
            User.query.filter(
                User.parent_id == receiver.id,
                User.role == 'Student',
                User.class_id.in_(teacher_class_ids),
            ).first()
        )
        if not student_exists:
            return jsonify({'error': 'Parent is not linked to your students'}), 403

        matching_student = (
            db.session.query(User)
            .join(Class, Class.id == User.class_id)
            .filter(
                User.parent_id == receiver.id,
                User.role == 'Student',
                User.class_id.in_(teacher_class_ids),
                Class.name == class_name,
            )
            .all()
        )
        matching_student_context = [student for student in matching_student if _user_display_name(student).lower() == student_name.lower()]
        if len(matching_student_context) != 1:
            return jsonify({'error': 'Message must reference one student in this parent-teacher class context.'}), 400

    # Validate quiz_result_id if provided
    if quiz_result_id:
        try:
            quiz_result_id = int(quiz_result_id)
            quiz_result = QuizResult.query.get(quiz_result_id)
            if not quiz_result:
                return jsonify({'error': 'Quiz result not found'}), 404
        except (ValueError, TypeError):
            return jsonify({'error': 'Invalid quiz_result_id'}), 400

    sender = User.query.get(teacher_id)
    message = Message(
        sender_id=teacher_id,
        receiver_id=receiver.id,
        student_name=student_name or None,
        class_name=class_name or None,
        sender_name=_user_display_name(sender),
        sender_role=sender.role,
        content=content,
        quiz_result_id=quiz_result_id,
    )
    db.session.add(message)
    db.session.commit()

    return jsonify(
        {
            'message': 'Message sent successfully',
            'data': {
                **_serialize_message(message),
            },
        }
    ), 201


@teacher_bp.route('/teacher/messages', methods=['GET'])
@token_required
def list_teacher_messages():
    guard = _teacher_guard()
    if guard:
        return guard

    teacher_id = int(request.current_user_id)
    messages = (
        Message.query
        .filter((Message.sender_id == teacher_id) | (Message.receiver_id == teacher_id))
        .order_by(Message.created_at.desc())
        .all()
    )

    payload = [_serialize_message(message) for message in messages]

    return jsonify(payload), 200


@teacher_bp.route('/teacher/announcement', methods=['POST'])
@token_required
def create_announcement():
    guard = _teacher_guard()
    if guard:
        return guard

    data = request.get_json(silent=True) or {}
    class_id = data.get('class_id')
    title = (data.get('title') or '').strip()
    message = (data.get('message') or '').strip()

    if not class_id or not title or not message:
        return jsonify({'error': 'class_id, title, and message are required'}), 400

    teacher_id = int(request.current_user_id)
    classroom = Class.query.filter_by(id=class_id, teacher_id=teacher_id).first()
    if not classroom:
        return jsonify({'error': 'Class not found or not owned by teacher'}), 403

    announcement = Announcement(class_id=class_id, teacher_id=teacher_id, title=title, message=message, is_hidden=False)
    db.session.add(announcement)
    db.session.commit()

    return jsonify({'message': 'Announcement posted successfully', 'announcement': announcement.to_dict()}), 201


@teacher_bp.route('/teacher/announcements', methods=['GET'])
@token_required
def list_announcements():
    guard = _teacher_guard()
    if guard:
        return guard

    teacher_id = int(request.current_user_id)
    
    # Fetch all announcements for the teacher's classes
    announcements = Announcement.query.filter_by(teacher_id=teacher_id).order_by(Announcement.created_at.desc()).all()

    return jsonify({'announcements': [a.to_dict() for a in announcements]}), 200


@teacher_bp.route('/teacher/announcement/<int:announcement_id>', methods=['DELETE'])
@token_required
def delete_announcement(announcement_id: int):
    guard = _teacher_guard()
    if guard:
        return guard

    teacher_id = int(request.current_user_id)
    
    announcement = Announcement.query.filter_by(id=announcement_id, teacher_id=teacher_id).first()
    if not announcement:
        return jsonify({'error': 'Announcement not found or not owned by teacher'}), 404

    db.session.delete(announcement)
    db.session.commit()

    return jsonify({'message': 'Announcement deleted successfully', 'id': announcement_id}), 200


@teacher_bp.route('/teacher/announcement/<int:announcement_id>/toggle_visibility', methods=['PATCH'])
@token_required
def toggle_announcement_visibility(announcement_id: int):
    guard = _teacher_guard()
    if guard:
        return guard
    teacher_id = int(request.current_user_id)
    announcement = Announcement.query.filter_by(id=announcement_id, teacher_id=teacher_id).first()
    if not announcement:
        return jsonify({'error': 'Announcement not found or not owned by teacher'}), 404
    current_hidden = bool(announcement.is_hidden)
    announcement.is_hidden = not current_hidden
    db.session.commit()
    return jsonify({'message': 'Visibility toggled', 'is_hidden': not current_hidden}), 200


@teacher_bp.route('/teacher/lobby/create', methods=['POST'])
@token_required
def create_lobby():
    guard = _teacher_guard()
    if guard:
        return guard

    teacher_id = int(request.current_user_id)
    data = request.get_json(silent=True) or {}

    class_public_id = (data.get('class_public_id') or '').strip()
    name = (data.get('name') or '').strip()
    requested_ip = (data.get('ip') or '').strip()
    requested_port = data.get('port')
    required_players = data.get('required_players', data.get('player_count', 5))

    if not class_public_id:
        return jsonify({'error': 'class_public_id is required'}), 400

    classroom = Class.query.filter_by(public_id=class_public_id, teacher_id=teacher_id).first()
    if not classroom:
        return jsonify({'error': 'Class not found or not owned by teacher'}), 403

    try:
        required_players = int(required_players)
        if required_players not in range(4, 10):
            raise ValueError
    except (TypeError, ValueError):
        return jsonify({'error': 'required_players must be one of 4, 5, 6, 7, 8, or 9'}), 400

    server_name = name if name else f'{classroom.name} Lobby'

    if requested_ip and requested_port is not None:
        try:
            port = int(requested_port)
            if port <= 0:
                raise ValueError
        except (TypeError, ValueError):
            return jsonify({'error': 'port must be a positive integer'}), 400

        ip = requested_ip
        existing = GameServer.query.filter_by(ip=ip, port=port).first()
        if existing:
            return jsonify({'error': 'Generated lobby endpoint is already in use. Refresh lobbies and try again.'}), 409
    else:
        try:
            ip, port = _allocate_lobby_endpoint()
        except RuntimeError:
            return jsonify({'error': 'No available lobby endpoints'}), 503

    lobby = GameServer(
        name=server_name,
        ip=ip,
        port=port,
        player_count=0,  # No players yet; teacher doesn't join the game
        required_players=required_players,
        last_heartbeat=time.time(),
        persistent=True,
        owner_teacher_id=teacher_id,
        class_id=classroom.id,
    )
    db.session.add(lobby)
    db.session.commit()

    return jsonify(
        {
            'message': 'Lobby created successfully',
            'lobby': _serialize_lobby(lobby, classroom, teacher_id),
        }
    ), 201


@teacher_bp.route('/teacher/lobby/list', methods=['GET'])
@token_required
def list_teacher_lobbies():
    guard = _teacher_guard()
    if guard:
        return guard

    teacher_id = int(request.current_user_id)
    lobbies = GameServer.query.filter_by(owner_teacher_id=teacher_id).order_by(GameServer.id.desc()).all()

    class_map = {c.id: c for c in Class.query.filter_by(teacher_id=teacher_id).all()}
    payload = []
    for lobby in lobbies:
        classroom = class_map.get(lobby.class_id)
        payload.append(_serialize_lobby(lobby, classroom, teacher_id))

    return jsonify({'lobbies': payload}), 200


@teacher_bp.route('/teacher/lobby/<string:lobby_public_id>', methods=['DELETE'])
@token_required
def delete_teacher_lobby(lobby_public_id: str):
    guard = _teacher_guard()
    if guard:
        return guard

    teacher_id = int(request.current_user_id)
    lobby = GameServer.query.filter_by(public_id=lobby_public_id, owner_teacher_id=teacher_id).first()
    if not lobby:
        return jsonify({'error': 'Lobby not found or not owned by teacher'}), 404

    db.session.delete(lobby)
    db.session.commit()
    return jsonify({'message': 'Lobby removed successfully', 'public_id': lobby_public_id}), 200


@teacher_bp.route('/teacher/class', methods=['POST'])
@token_required
def create_class():
    guard = _teacher_guard()
    if guard:
        return guard

    data = request.get_json(silent=True) or {}
    name = (data.get('name') or '').strip()

    if not name:
        return jsonify({'error': 'Class name is required'}), 400

    teacher_id = int(request.current_user_id)

    # Create the new class in the database
    new_class = Class(name=name, teacher_id=teacher_id)
    db.session.add(new_class)
    db.session.commit()

    return jsonify({
        'message': 'Class created successfully!',
        'class': {
            'id': new_class.id,
            'name': new_class.name,
            'public_id': new_class.public_id
        }
    }), 201
