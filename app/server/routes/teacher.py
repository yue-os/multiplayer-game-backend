from datetime import datetime
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
    QuizResult,
    User,
)

teacher_bp = Blueprint('teacher', __name__)


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
    classes = Class.query.filter_by(teacher_id=teacher_id).all()

    if not classes:
        return jsonify({'classes': [], 'students': []}), 200

    class_ids = [classroom.id for classroom in classes]

    students = (
        User.query.filter(User.class_id.in_(class_ids), User.role == 'Student')
        .order_by(User.class_id.asc(), User.username.asc())
        .all()
    )

    if not students:
        class_payload = [
            {'id': classroom.id, 'public_id': classroom.public_id, 'name': classroom.name}
            for classroom in classes
        ]
        return jsonify({'classes': class_payload, 'students': []}), 200

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

    class_name_by_id = {classroom.id: classroom.name for classroom in classes}
    class_payload = [
        {'id': classroom.id, 'public_id': classroom.public_id, 'name': classroom.name}
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
                'student_id': student.id,
                'student_public_id': student.public_id,
                'username': student.username,
                'class_id': student.class_id,
                'class_name': class_name_by_id.get(student.class_id),
                'missions': mission_summary,
                'quizzes': quiz_summary,
            }
        )

    return jsonify({'classes': class_payload, 'students': student_payload}), 200


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
    title = (data.get('title') or '').strip()
    timer_seconds = data.get('timer_seconds', 300)
    start_date_raw = data.get('start_date')

    if not title:
        return jsonify({'error': 'title is required'}), 400

    try:
        timer_seconds = int(timer_seconds)
        if timer_seconds <= 0:
            raise ValueError
    except (TypeError, ValueError):
        return jsonify({'error': 'timer_seconds must be a positive integer'}), 400

    try:
        if start_date_raw:
            start_date = datetime.fromisoformat(str(start_date_raw).replace('Z', '+00:00'))
        else:
            start_date = datetime.utcnow()
    except ValueError:
        return jsonify({'error': 'start_date must be a valid ISO-8601 datetime'}), 400

    teacher_id = int(request.current_user_id)

    quiz = Quiz(
        teacher_id=teacher_id,
        title=title,
        timer_seconds=timer_seconds,
        start_date=start_date,
    )

    db.session.add(quiz)
    db.session.commit()

    return jsonify(
        {
            'message': 'Quiz created successfully',
            'quiz': {
                'id': quiz.id,
                'public_id': quiz.public_id,
                'teacher_id': quiz.teacher_id,
                'title': quiz.title,
                'timer_seconds': quiz.timer_seconds,
                'start_date': quiz.start_date.isoformat() if quiz.start_date else None,
            },
        }
    ), 201


@teacher_bp.route('/teacher/message', methods=['POST'])
@token_required
def send_message():
    guard = _teacher_guard()
    if guard:
        return guard

    data = request.get_json(silent=True) or {}
    receiver_public_id = (data.get('receiver_public_id') or '').strip()
    content = (data.get('content') or '').strip()

    if not receiver_public_id:
        return jsonify({'error': 'receiver_public_id is required'}), 400

    if not content:
        return jsonify({'error': 'content is required'}), 400

    teacher_id = int(request.current_user_id)

    receiver = User.query.filter_by(public_id=receiver_public_id).first()
    if not receiver:
        return jsonify({'error': 'Receiver not found'}), 404

    if receiver.role not in ('Student', 'Parent'):
        return jsonify({'error': 'Receiver must be a Student or Parent'}), 400

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

    message = Message(sender_id=teacher_id, receiver_id=receiver.id, content=content)
    db.session.add(message)
    db.session.commit()

    return jsonify(
        {
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
        }
    ), 201


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
    ip = (data.get('ip') or '').strip()
    port = data.get('port')
    player_count = data.get('player_count', 0)

    if not class_public_id:
        return jsonify({'error': 'class_public_id is required'}), 400

    classroom = Class.query.filter_by(public_id=class_public_id, teacher_id=teacher_id).first()
    if not classroom:
        return jsonify({'error': 'Class not found or not owned by teacher'}), 403

    if not ip or port is None:
        return jsonify(
            {
                'error': 'ip and port are required to create a lobby record',
                'integration': {
                    'option': 'Use existing /server/register heartbeat flow',
                    'instruction': 'Start your game server and POST {name, port, count} to /server/register from the game host.',
                    'tip': f'Include class metadata in name, for example: {classroom.name} Lobby',
                },
            }
        ), 400

    try:
        port = int(port)
        player_count = int(player_count)
        if port <= 0:
            raise ValueError
        if player_count < 0:
            raise ValueError
    except (TypeError, ValueError):
        return jsonify({'error': 'port must be positive and player_count must be >= 0'}), 400

    server_name = name if name else f'{classroom.name} Lobby'

    existing = GameServer.query.filter_by(ip=ip, port=port).first()
    if existing:
        existing.name = server_name
        existing.player_count = player_count
        existing.last_heartbeat = time.time()
        db.session.commit()
        return jsonify(
            {
                'message': 'Lobby updated successfully',
                'lobby': {
                    'id': existing.id,
                    'public_id': existing.public_id,
                    'name': existing.name,
                    'ip': existing.ip,
                    'port': existing.port,
                    'player_count': existing.player_count,
                    'class_id': classroom.id,
                    'class_public_id': classroom.public_id,
                    'class_name': classroom.name,
                    'teacher_id': teacher_id,
                },
                'note': 'GameServer has no class_id column; class linkage is enforced at creation time and returned in response.',
            }
        ), 200

    lobby = GameServer(
        name=server_name,
        ip=ip,
        port=port,
        player_count=player_count,
        last_heartbeat=time.time()
    )
    db.session.add(lobby)
    db.session.commit()

    return jsonify(
        {
            'message': 'Lobby created successfully',
            'lobby': {
                'id': lobby.id,
                'public_id': lobby.public_id,
                'name': lobby.name,
                'ip': lobby.ip,
                'port': lobby.port,
                'player_count': lobby.player_count,
                'class_id': classroom.id,
                'class_public_id': classroom.public_id,
                'class_name': classroom.name,
                'teacher_id': teacher_id,
            },
            'note': 'GameServer has no class_id column; class linkage is enforced at creation time and returned in response.',
        }
    ), 201
