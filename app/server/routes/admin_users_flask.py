from __future__ import annotations

import time
from datetime import datetime, timedelta

from flask import Blueprint, jsonify, request
from sqlalchemy import case, func
from sqlalchemy.exc import IntegrityError
from werkzeug.security import generate_password_hash

from app.auth.auth_bearer import token_required
from app.server.database import db
from app.server.models.user import Class, GameServer, MissionProgress, PlaytimeLog, QuizResult, User


admin_users_bp = Blueprint("admin_users", __name__)

ALLOWED_ROLES = {"Admin", "Teacher", "Parent", "Student"}


def _serialize_user(user: User) -> dict[str, object]:
    classroom = Class.query.get(user.class_id) if user.class_id is not None else None
    return {
        "id": user.id,
        "public_id": user.public_id,
        "first_name": user.first_name,
        "last_name": user.last_name,
        "username": user.username,
        "email": user.email,
        "role": user.role,
        "class_id": user.class_id,
        "class_name": classroom.name if classroom is not None else None,
        "parent_id": user.parent_id,
    }


def _serialize_class(classroom: Class) -> dict[str, object]:
    teacher = User.query.get(classroom.teacher_id)
    student_count = User.query.filter_by(class_id=classroom.id, role="Student").count()
    return {
        "id": classroom.id,
        "public_id": classroom.public_id,
        "name": classroom.name,
        "teacher_id": classroom.teacher_id,
        "teacher_username": teacher.username if teacher is not None else "",
        "student_count": student_count,
    }


def _full_name(user: User) -> str:
    return f"{(user.first_name or '').strip()} {(user.last_name or '').strip()}".strip() or user.username


def _completion_rate(missions_total: int, missions_completed: int) -> float:
    if missions_total <= 0:
        return 0.0
    return round((missions_completed / missions_total) * 100.0, 1)


def _badge_labels(completion_rate: float, avg_quiz_score: float, total_playtime_minutes: int, missions_completed: int) -> list[str]:
    badges: list[str] = []
    if completion_rate >= 90:
        badges.append("Completion Champion")
    if avg_quiz_score >= 85:
        badges.append("Mastery Star")
    if total_playtime_minutes >= 180:
        badges.append("Consistent Player")
    if missions_completed >= 10:
        badges.append("Milestone Achiever")
    return badges or ["Rising Learner"]


@admin_users_bp.route("/api/admin/classes", methods=["GET"])
@token_required
def get_classes():
    if request.current_user_role != "Admin":
        return jsonify({"error": "Unauthorized"}), 403

    classes = Class.query.order_by(Class.name.asc()).all()
    return jsonify([_serialize_class(classroom) for classroom in classes]), 200


@admin_users_bp.route("/api/admin/classes", methods=["POST"])
@token_required
def create_class():
    if request.current_user_role != "Admin":
        return jsonify({"error": "Unauthorized"}), 403

    data = request.json or {}
    name = str(data.get("name", "")).strip()
    teacher_id = data.get("teacher_id")
    student_ids = data.get("student_ids") or []

    if not name or teacher_id is None:
        return jsonify({"error": "name and teacher_id are required"}), 400

    if not isinstance(student_ids, list):
        return jsonify({"error": "student_ids must be a list"}), 400

    teacher = User.query.get(int(teacher_id))
    if teacher is None or teacher.role != "Teacher":
        return jsonify({"error": "Teacher not found"}), 404

    classroom = Class(name=name, teacher_id=teacher.id)
    db.session.add(classroom)

    db.session.flush()

    assigned_students = []
    for student_id in student_ids:
        student = User.query.get(int(student_id))
        if student is None or student.role != "Student":
            continue
        student.class_id = classroom.id
        assigned_students.append(student.id)

    db.session.commit()

    payload = _serialize_class(classroom)
    payload["student_ids"] = assigned_students
    return jsonify({"message": "Class created successfully.", "class": payload}), 201


@admin_users_bp.route("/api/admin/classes/<int:class_id>", methods=["DELETE"])
@token_required
def delete_class(class_id: int):
    if request.current_user_role != "Admin":
        return jsonify({"error": "Unauthorized"}), 403

    classroom = Class.query.get(class_id)
    if classroom is None:
        return jsonify({"error": "Class not found"}), 404

    students = User.query.filter_by(class_id=classroom.id, role="Student").all()
    for student in students:
        student.class_id = None

    db.session.delete(classroom)
    db.session.commit()

    return jsonify({"message": "Class deleted successfully.", "class_id": class_id}), 200


@admin_users_bp.route("/api/admin/users", methods=["POST"])
@token_required
def create_user():
    if request.current_user_role != "Admin":
        return jsonify({"error": "Unauthorized"}), 403

    data = request.json or {}
    first_name = str(data.get("first_name", "")).strip()
    last_name = str(data.get("last_name", "")).strip()
    username = str(data.get("username", "")).strip()
    email = str(data.get("email", "")).strip()
    password = str(data.get("password", "")).strip()
    role = str(data.get("role", "")).strip()

    if not first_name or not last_name or not username or not email or not password or role not in ALLOWED_ROLES:
        return jsonify({"error": "Invalid payload"}), 400

    if User.query.filter((User.username == username) | (User.email == email)).first():
        return jsonify({"error": "User already exists"}), 409

    user = User(
        first_name=first_name,
        last_name=last_name,
        username=username,
        email=email,
        password_hash=generate_password_hash(password),
        role=role,
    )

    db.session.add(user)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return jsonify({"error": "Unable to create user"}), 409

    return jsonify(_serialize_user(user)), 201


@admin_users_bp.route("/api/admin/users/bulk-create", methods=["POST"])
@token_required
def bulk_create_users():
    if request.current_user_role != "Admin":
        return jsonify({"error": "Unauthorized"}), 403

    data = request.json or {}
    users = data.get("users") or []
    if not isinstance(users, list) or not users:
        return jsonify({"error": "No users provided"}), 400

    created = []
    errors = []

    for idx, u in enumerate(users):
        try:
            first_name = str(u.get("first_name", "")).strip()
            last_name = str(u.get("last_name", "")).strip()
            username = str(u.get("username", "")).strip()
            email = str(u.get("email", "")).strip()
            password = str(u.get("password", "")).strip()
            role = str(u.get("role", "Student")).strip()

            if not first_name or not last_name or not username or not email or not password or role not in ALLOWED_ROLES:
                errors.append({"index": idx, "error": "invalid payload"})
                continue

            if User.query.filter((User.username == username) | (User.email == email)).first():
                errors.append({"index": idx, "error": "user exists", "username": username})
                continue

            user = User(
                first_name=first_name,
                last_name=last_name,
                username=username,
                email=email,
                password_hash=generate_password_hash(password),
                role=role,
            )
            db.session.add(user)
            db.session.flush()
            created.append(_serialize_user(user))
        except Exception as exc:
            db.session.rollback()
            errors.append({"index": idx, "error": str(exc)})

    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return jsonify({"error": "Unable to create users due to integrity error"}), 409

    return jsonify({"created": created, "errors": errors}), 201


@admin_users_bp.route("/api/admin/users", methods=["GET"])
@token_required
def list_users():
    if request.current_user_role != "Admin":
        return jsonify({"error": "Unauthorized"}), 403

    users = User.query.order_by(User.id.asc()).all()
    return jsonify([_serialize_user(user) for user in users]), 200


@admin_users_bp.route("/api/admin/users/<int:user_id>", methods=["DELETE"])
@token_required
def delete_user(user_id: int):
    if request.current_user_role != "Admin":
        return jsonify({"error": "Unauthorized"}), 403

    user = User.query.get(user_id)
    if user is None:
        return jsonify({"error": "User not found"}), 404

    db.session.delete(user)
    db.session.commit()
    return jsonify({"message": "User deleted successfully.", "user_id": user_id}), 200


@admin_users_bp.route("/api/admin/users/<int:user_id>", methods=["PUT", "PATCH"])
@token_required
def update_user(user_id: int):
    if request.current_user_role != "Admin":
        return jsonify({"error": "Unauthorized"}), 403

    user = User.query.get(user_id)
    if user is None:
        return jsonify({"error": "User not found"}), 404

    data = request.json or {}

    first_name = str(data.get("first_name", user.first_name)).strip()
    last_name = str(data.get("last_name", user.last_name)).strip()
    username = str(data.get("username", user.username)).strip()
    email = str(data.get("email", user.email)).strip()
    role = str(data.get("role", user.role)).strip()
    password = str(data.get("password", "")).strip()

    if not first_name or not last_name or not username or not email or role not in ALLOWED_ROLES:
        return jsonify({"error": "Invalid payload"}), 400

    conflict = User.query.filter(
        ((User.username == username) | (User.email == email)) & (User.id != user.id)
    ).first()
    if conflict:
        return jsonify({"error": "Another user already uses the same username or email"}), 409

    user.first_name = first_name
    user.last_name = last_name
    user.username = username
    user.email = email
    user.role = role
    if password:
        user.password_hash = generate_password_hash(password)

    db.session.commit()
    return jsonify(_serialize_user(user)), 200


@admin_users_bp.route("/api/admin/classes/<string:grade>/<string:section>/students", methods=["GET"])
@token_required
def get_class_students_by_grade_section(grade: str, section: str):
    if request.current_user_role != "Admin":
        return jsonify({"error": "Unauthorized"}), 403

    # Backend stores class name as "Grade X - Section" in many places
    class_name = f"{grade} - {section}"
    classroom = Class.query.filter_by(name=class_name).first()
    if classroom is None:
        return jsonify([]), 200

    students = User.query.filter_by(class_id=classroom.id, role="Student").order_by(User.username.asc()).all()
    return jsonify([_serialize_user(student) for student in students]), 200


@admin_users_bp.route("/api/admin/class-assignment/options", methods=["GET"])
@token_required
def get_class_assignment_options():
    if request.current_user_role != "Admin":
        return jsonify({"error": "Unauthorized"}), 403

    students = User.query.filter_by(role="Student").order_by(User.username.asc()).all()
    teachers = User.query.filter_by(role="Teacher").order_by(User.username.asc()).all()
    classes = Class.query.order_by(Class.name.asc()).all()

    return jsonify(
        {
            "students": [_serialize_user(student) for student in students],
            "teachers": [_serialize_user(teacher) for teacher in teachers],
            "classes": [_serialize_class(classroom) for classroom in classes],
        }
    ), 200


@admin_users_bp.route("/api/admin/class-assignment", methods=["POST"])
@token_required
def assign_student_class_and_teacher():
    if request.current_user_role != "Admin":
        return jsonify({"error": "Unauthorized"}), 403

    data = request.json or {}
    student_id = data.get("student_id")
    class_id = data.get("class_id")
    teacher_id = data.get("teacher_id")

    if student_id is None or class_id is None or teacher_id is None:
        return jsonify({"error": "student_id, class_id, and teacher_id are required"}), 400

    student = User.query.get(int(student_id))
    teacher = User.query.get(int(teacher_id))
    classroom = Class.query.get(int(class_id))

    if student is None or student.role != "Student":
        return jsonify({"error": "Student not found"}), 404
    if teacher is None or teacher.role != "Teacher":
        return jsonify({"error": "Teacher not found"}), 404
    if classroom is None:
        return jsonify({"error": "Class not found"}), 404

    classroom.teacher_id = teacher.id
    student.class_id = classroom.id

    db.session.commit()

    return jsonify(
        {
            "message": "Student class and teacher assignment updated.",
            "student": _serialize_user(student),
            "class": _serialize_class(classroom),
        }
    ), 200


@admin_users_bp.route("/api/admin/dashboard/analytics", methods=["GET"])
@token_required
def dashboard_analytics():
    if request.current_user_role != "Admin":
        return jsonify({"error": "Unauthorized"}), 403

    students = User.query.filter_by(role="Student").order_by(User.username.asc()).all()
    student_ids = [student.id for student in students]
    now = datetime.utcnow()
    cutoff_dt = now - timedelta(days=7)
    cutoff_date = (now - timedelta(days=6)).date()
    heartbeat_cutoff = time.time() - 15

    total_students = len(students)
    active_servers = GameServer.query.filter(GameServer.last_heartbeat > heartbeat_cutoff).count()
    total_servers = GameServer.query.count()

    if not student_ids:
        return jsonify(
            {
                "summary": {
                    "total_students": 0,
                    "active_players": 0,
                    "average_completion_rate": 0.0,
                    "average_quiz_score": 0.0,
                    "total_missions_completed": 0,
                    "total_playtime_minutes": 0,
                    "active_game_servers": active_servers,
                    "total_game_servers": total_servers,
                    "backend_status": "online",
                },
                "leaderboard": [],
                "recent_activity": [],
                "usage_frequency": [],
                "key_achievements": [],
                "modules": [
                    {"name": "Progress Dashboard", "status": "Enabled"},
                    {"name": "Performance Metrics", "status": "Enabled"},
                    {"name": "Leaderboards", "status": "Enabled"},
                    {"name": "Usage Frequency", "status": "Enabled"},
                    {"name": "Game-Based Content Controls", "status": "Available"},
                    {"name": "Behavioral Data", "status": "Available"},
                    {"name": "System Health", "status": "Online"},
                    {"name": "Parent/Teacher Reporting", "status": "Available"},
                    {"name": "Content Moderation", "status": "Available"},
                ],
            }
        ), 200

    mission_rows = (
        db.session.query(
            MissionProgress.user_id,
            func.count(MissionProgress.id).label("missions_total"),
            func.sum(case((MissionProgress.status == "completed", 1), else_=0)).label("missions_completed"),
            func.coalesce(func.avg(MissionProgress.score), 0).label("mission_avg_score"),
            func.coalesce(func.max(MissionProgress.score), 0).label("mission_best_score"),
            func.max(MissionProgress.updated_at).label("mission_last_update"),
        )
        .filter(MissionProgress.user_id.in_(student_ids))
        .group_by(MissionProgress.user_id)
        .all()
    )

    quiz_rows = (
        db.session.query(
            QuizResult.student_id,
            func.count(QuizResult.id).label("quizzes_taken"),
            func.coalesce(func.avg(QuizResult.score), 0).label("quiz_avg_score"),
            func.coalesce(func.max(QuizResult.score), 0).label("quiz_best_score"),
            func.max(QuizResult.updated_at).label("quiz_last_update"),
        )
        .filter(QuizResult.student_id.in_(student_ids))
        .group_by(QuizResult.student_id)
        .all()
    )

    playtime_rows = (
        db.session.query(
            PlaytimeLog.user_id,
            func.coalesce(func.sum(PlaytimeLog.duration_minutes), 0).label("total_playtime_minutes"),
            func.count(PlaytimeLog.id).label("session_count"),
            func.count(func.distinct(PlaytimeLog.date)).label("active_days"),
            func.max(PlaytimeLog.date).label("last_played_on"),
        )
        .filter(PlaytimeLog.user_id.in_(student_ids))
        .group_by(PlaytimeLog.user_id)
        .all()
    )

    daily_usage_rows = (
        db.session.query(
            PlaytimeLog.date,
            func.count(func.distinct(PlaytimeLog.user_id)).label("active_users"),
            func.coalesce(func.sum(PlaytimeLog.duration_minutes), 0).label("minutes"),
        )
        .filter(PlaytimeLog.date >= cutoff_date)
        .group_by(PlaytimeLog.date)
        .order_by(PlaytimeLog.date.asc())
        .all()
    )

    mission_by_user = {
        row.user_id: {
            "missions_total": int(row.missions_total or 0),
            "missions_completed": int(row.missions_completed or 0),
            "mission_avg_score": float(row.mission_avg_score or 0),
            "mission_best_score": int(row.mission_best_score or 0),
            "mission_last_update": row.mission_last_update,
        }
        for row in mission_rows
    }

    quiz_by_user = {
        row.student_id: {
            "quizzes_taken": int(row.quizzes_taken or 0),
            "quiz_avg_score": float(row.quiz_avg_score or 0),
            "quiz_best_score": int(row.quiz_best_score or 0),
            "quiz_last_update": row.quiz_last_update,
        }
        for row in quiz_rows
    }

    playtime_by_user = {
        row.user_id: {
            "total_playtime_minutes": int(row.total_playtime_minutes or 0),
            "session_count": int(row.session_count or 0),
            "active_days": int(row.active_days or 0),
            "last_played_on": row.last_played_on,
        }
        for row in playtime_rows
    }

    class_ids = {student.class_id for student in students if student.class_id is not None}
    class_name_by_id = {
        classroom.id: classroom.name
        for classroom in Class.query.filter(Class.id.in_(class_ids)).all()
    }

    leaderboard: list[dict[str, object]] = []
    total_completion_rates: list[float] = []
    total_quiz_scores: list[float] = []
    total_playtime_minutes = 0
    total_missions_completed = 0

    for student in students:
        mission = mission_by_user.get(
            student.id,
            {
                "missions_total": 0,
                "missions_completed": 0,
                "mission_avg_score": 0.0,
                "mission_best_score": 0,
                "mission_last_update": None,
            },
        )
        quiz = quiz_by_user.get(
            student.id,
            {
                "quizzes_taken": 0,
                "quiz_avg_score": 0.0,
                "quiz_best_score": 0,
                "quiz_last_update": None,
            },
        )
        playtime = playtime_by_user.get(
            student.id,
            {
                "total_playtime_minutes": 0,
                "session_count": 0,
                "active_days": 0,
                "last_played_on": None,
            },
        )

        completion_rate = _completion_rate(mission["missions_total"], mission["missions_completed"])
        badges = _badge_labels(
            completion_rate,
            quiz["quiz_avg_score"],
            playtime["total_playtime_minutes"],
            mission["missions_completed"],
        )

        total_completion_rates.append(completion_rate)
        total_quiz_scores.append(quiz["quiz_avg_score"])
        total_playtime_minutes += playtime["total_playtime_minutes"]
        total_missions_completed += mission["missions_completed"]

        last_activity_candidates = [value for value in [mission["mission_last_update"], quiz["quiz_last_update"]] if value is not None]
        if playtime["last_played_on"] is not None:
            last_activity_candidates.append(datetime.combine(playtime["last_played_on"], datetime.min.time()))

        leaderboard.append(
            {
                "student_id": student.id,
                "public_id": student.public_id,
                "username": student.username,
                "full_name": _full_name(student),
                "class_id": student.class_id,
                "class_name": class_name_by_id.get(student.class_id),
                "missions_total": mission["missions_total"],
                "missions_completed": mission["missions_completed"],
                "completion_rate": completion_rate,
                "quiz_avg_score": round(quiz["quiz_avg_score"], 1),
                "quiz_best_score": quiz["quiz_best_score"],
                "playtime_minutes": playtime["total_playtime_minutes"],
                "active_days": playtime["active_days"],
                "session_count": playtime["session_count"],
                "badges": badges,
                "last_activity": max(last_activity_candidates) if last_activity_candidates else None,
            }
        )

    leaderboard.sort(
        key=lambda item: (
            float(item["completion_rate"]),
            float(item["quiz_avg_score"]),
            int(item["playtime_minutes"]),
        ),
        reverse=True,
    )

    for index, row in enumerate(leaderboard, start=1):
        row["rank"] = index

    recent_events: list[dict[str, object]] = []
    for student in students:
        mission = mission_by_user.get(student.id)
        if mission and mission["mission_last_update"] is not None:
            recent_events.append(
                {
                    "timestamp": mission["mission_last_update"],
                    "kind": "Progress",
                    "student": student.username,
                    "detail": f"{mission['missions_completed']} completed / {mission['missions_total']} total missions",
                }
            )
        quiz = quiz_by_user.get(student.id)
        if quiz and quiz["quiz_last_update"] is not None:
            recent_events.append(
                {
                    "timestamp": quiz["quiz_last_update"],
                    "kind": "Quiz",
                    "student": student.username,
                    "detail": f"Average score {round(quiz['quiz_avg_score'], 1)}",
                }
            )
        playtime = playtime_by_user.get(student.id)
        if playtime and playtime["last_played_on"] is not None:
            recent_events.append(
                {
                    "timestamp": datetime.combine(playtime["last_played_on"], datetime.min.time()),
                    "kind": "Playtime",
                    "student": student.username,
                    "detail": f"{playtime['session_count']} session(s), {playtime['total_playtime_minutes']} min total",
                }
            )

    recent_events.sort(key=lambda item: item["timestamp"], reverse=True)
    recent_events = recent_events[:12]

    active_player_ids = set()
    for row in MissionProgress.query.filter(MissionProgress.user_id.in_(student_ids), MissionProgress.updated_at >= cutoff_dt).all():
        active_player_ids.add(row.user_id)
    for row in QuizResult.query.filter(QuizResult.student_id.in_(student_ids), QuizResult.updated_at >= cutoff_dt).all():
        active_player_ids.add(row.student_id)
    for row in PlaytimeLog.query.filter(PlaytimeLog.user_id.in_(student_ids), PlaytimeLog.date >= cutoff_date).all():
        active_player_ids.add(row.user_id)

    total_active_players = len(active_player_ids)
    average_completion_rate = round(sum(total_completion_rates) / len(total_completion_rates), 1) if total_completion_rates else 0.0
    average_quiz_score = round(sum(total_quiz_scores) / len(total_quiz_scores), 1) if total_quiz_scores else 0.0

    top_completion = leaderboard[0] if leaderboard else None
    top_quiz = max(leaderboard, key=lambda row: float(row["quiz_avg_score"])) if leaderboard else None
    top_activity = max(leaderboard, key=lambda row: int(row["playtime_minutes"])) if leaderboard else None

    key_achievements = [
        {
            "label": "Top completion",
            "student": top_completion["full_name"] if top_completion else "N/A",
            "value": f"{top_completion['completion_rate']:.1f}%" if top_completion else "0.0%",
        },
        {
            "label": "Top quiz score",
            "student": top_quiz["full_name"] if top_quiz else "N/A",
            "value": f"{float(top_quiz['quiz_avg_score']):.1f}" if top_quiz else "0.0",
        },
        {
            "label": "Most active",
            "student": top_activity["full_name"] if top_activity else "N/A",
            "value": f"{int(top_activity['playtime_minutes'])} min" if top_activity else "0 min",
        },
    ]

    return jsonify(
        {
            "summary": {
                "total_students": total_students,
                "active_players": total_active_players,
                "average_completion_rate": average_completion_rate,
                "average_quiz_score": average_quiz_score,
                "total_missions_completed": total_missions_completed,
                "total_playtime_minutes": total_playtime_minutes,
                "active_game_servers": active_servers,
                "total_game_servers": total_servers,
                "backend_status": "online",
                "last_checked": now.isoformat(),
            },
            "leaderboard": leaderboard,
            "recent_activity": [
                {
                    "timestamp": event["timestamp"].isoformat() if event.get("timestamp") is not None else None,
                    "kind": event["kind"],
                    "student": event["student"],
                    "detail": event["detail"],
                }
                for event in recent_events
            ],
            "usage_frequency": [
                {
                    "date": row.date.isoformat(),
                    "active_users": int(row.active_users or 0),
                    "minutes": int(row.minutes or 0),
                }
                for row in daily_usage_rows
            ],
            "key_achievements": key_achievements,
            "modules": [
                {"name": "Progress Dashboard", "status": "Enabled", "description": "Completed levels, activities, and curriculum modules."},
                {"name": "Performance Metrics", "status": "Enabled", "description": "Correct vs wrong answers, quiz scores, and mastery."},
                {"name": "Leaderboards", "status": "Enabled", "description": "Student rankings, badges earned, and achievements."},
                {"name": "Usage Frequency", "status": "Enabled", "description": "Login frequency and daily/active user metrics."},
                {"name": "Game-Based Content Controls", "status": "Available", "description": "Add, edit, or reorganize educational content."},
                {"name": "Behavioral Data", "status": "Enabled", "description": "Insights on click-paths and in-app navigation."},
                {"name": "System Health", "status": "Enabled", "description": "Server uptime and app performance signals."},
                {"name": "Parent/Teacher Reporting", "status": "Available", "description": "Generate communication-friendly reports."},
                {"name": "Content Moderation", "status": "Available", "description": "Manage user-generated content safely."},
            ],
        }
    ), 200
