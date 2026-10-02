from __future__ import annotations

import time
import re
import secrets
from datetime import datetime, timedelta, timezone

from flask import Blueprint, jsonify, request
from sqlalchemy import String, case, cast, func, or_
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from werkzeug.security import generate_password_hash

from app.auth.auth_bearer import token_required
from app.server.database import db
from app.server.models.announcement import Announcement
from app.server.models.audit_log import AuditLog
from app.server.models.user import Class, GameServer, Message, MissionProgress, PasswordResetRequest, PlaytimeLog, Quiz, QuizResult, User
from app.server.password_policy import password_policy_error


admin_users_bp = Blueprint("admin_users", __name__)


def _pagination_args(default_limit: int = 15) -> tuple[int, int, int]:
    try:
        page = max(1, int(request.args.get("page", 1)))
        limit = min(20, max(1, int(request.args.get("limit", default_limit))))
    except (TypeError, ValueError):
        page, limit = 1, default_limit
    return page, limit, (page - 1) * limit


def _pagination_payload(page: int, limit: int, total: int) -> dict[str, int]:
    return {
        "page": page,
        "limit": limit,
        "total": total,
        "pages": (total + limit - 1) // limit,
    }


def _parse_audit_datetime(value: str | None, field_name: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"Invalid {field_name} date. Use an ISO 8601 date or timestamp.") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _serialize_audit_log(entry: AuditLog) -> dict[str, object]:
    timestamp = entry.timestamp
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    return {
        "id": entry.id,
        "timestamp": timestamp.isoformat().replace("+00:00", "Z"),
        "user_id": entry.user_id,
        "user_role": entry.user_role,
        "action_type": entry.action_type,
        "action": entry.action,
        "entity_type": entry.entity_type,
        "entity_id": entry.entity_id,
        "request_method": entry.request_method,
        "request_path": entry.request_path,
        "ip_address": entry.ip_address,
    }


@admin_users_bp.route("/api/admin/activity-logs", methods=["GET"])
@token_required
def list_activity_logs():
    if request.current_user_role != "Admin":
        return jsonify({"error": "Admin access is required."}), 403

    try:
        limit = min(100, max(1, int(request.args.get("limit", 50))))
        offset = max(0, int(request.args.get("offset", 0)))
        start = _parse_audit_datetime(request.args.get("from"), "from")
        end = _parse_audit_datetime(request.args.get("to"), "to")
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    query = AuditLog.query
    role = request.args.get("role", "").strip()
    action_type = request.args.get("action_type", "").strip().lower()
    search = request.args.get("search", "").strip()[:120]

    if role:
        query = query.filter(AuditLog.user_role == role)
    if action_type:
        query = query.filter(AuditLog.action_type == action_type)
    if start:
        query = query.filter(AuditLog.timestamp >= start)
    if end:
        query = query.filter(AuditLog.timestamp <= end)
    if search:
        term = f"%{search}%"
        query = query.filter(or_(
            AuditLog.action.ilike(term),
            AuditLog.entity_type.ilike(term),
            AuditLog.entity_id.ilike(term),
            AuditLog.user_role.ilike(term),
            AuditLog.request_path.ilike(term),
            cast(AuditLog.user_id, String).ilike(term),
        ))

    total = query.count()
    entries = query.order_by(AuditLog.timestamp.desc(), AuditLog.id.desc()).offset(offset).limit(limit).all()
    return jsonify({
        "logs": [_serialize_audit_log(entry) for entry in entries],
        "pagination": {"limit": limit, "offset": offset, "total": total},
    }), 200

ALLOWED_ROLES = {"Admin", "Teacher", "Parent", "Student"}
ROLE_BY_LOWER = {role.lower(): role for role in ALLOWED_ROLES}
CSV_ALLOWED_ROLES = {"Teacher", "Parent", "Student"}


def _serialize_user(user: User, class_map: dict = None, teacher_classes_map: dict = None) -> dict[str, object]:
    if class_map is not None:
        classroom = class_map.get(user.class_id)
    else:
        classroom = Class.query.get(user.class_id) if user.class_id is not None else None

    teacher_classes = []
    if user.role == "Teacher":
        if teacher_classes_map is not None:
            classes = teacher_classes_map.get(user.id, [])
        else:
            classes = Class.query.filter_by(teacher_id=user.id).order_by(Class.name.asc()).all()
            
        teacher_classes = [
            {"id": c.id, "public_id": c.public_id, "name": c.name} for c in classes
        ]

    return {
        "id": user.id,
        "public_id": user.public_id,
        "first_name": user.first_name,
        "last_name": user.last_name,
        "username": user.username,
        "email": user.email,
        "must_change_password": user.must_change_password,
        "mustChangePassword": user.must_change_password,
        "role": user.role,
        "class_id": user.class_id,
        "class_name": classroom.name if classroom is not None else None,
        "parent_id": user.parent_id,
        "classes": teacher_classes,
    }


def _serialize_class(classroom: Class, teacher_map: dict = None, student_map: dict = None) -> dict[str, object]:
    if teacher_map is not None:
        teacher = teacher_map.get(classroom.teacher_id)
    else:
        teacher = User.query.get(classroom.teacher_id)
        
    if student_map is not None:
        students = student_map.get(classroom.id, [])
    else:
        students = User.query.filter_by(class_id=classroom.id, role="Student").order_by(User.id.asc()).all()
        
    return {
        "id": classroom.id,
        "public_id": classroom.public_id,
        "name": classroom.name,
        "teacher_id": classroom.teacher_id,
        "teacher_username": teacher.username if teacher is not None else "",
        "student_count": len(students),
        "student_ids": [student.id for student in students],
    }


def _full_name(user: User) -> str:
    return f"{(user.first_name or '').strip()} {(user.last_name or '').strip()}".strip() or user.username


def _slugify_username(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "", value.lower())
    return slug or f"user{secrets.token_hex(3)}"


def _unique_username(base: str) -> str:
    username = _slugify_username(base)
    if not User.query.filter_by(username=username).first():
        return username

    for _ in range(20):
        candidate = f"{username}{secrets.randbelow(9000) + 1000}"
        if not User.query.filter_by(username=candidate).first():
            return candidate

    return f"{username}{secrets.token_hex(4)}"


def _unique_bulk_username(first_name: str, last_name: str, reserved_usernames: set[str]) -> str:
    base = _slugify_username(f"{first_name}{last_name}")
    for _ in range(100):
        candidate = f"{base}{secrets.randbelow(900) + 100}"
        normalized = candidate.lower()
        if normalized in reserved_usernames:
            continue
        if not User.query.filter_by(username=candidate).first():
            reserved_usernames.add(normalized)
            return candidate
    return _unique_username(base)


def _temporary_password() -> str:
    uppercase = "ABCDEFGHJKLMNPQRSTUVWXYZ"
    lowercase = "abcdefghijkmnopqrstuvwxyz"
    digits = "23456789"
    special = "!@#$%&*"
    alphabet = uppercase + lowercase + digits + special
    password = [
        secrets.choice(uppercase),
        secrets.choice(lowercase),
        secrets.choice(digits),
        secrets.choice(special),
        *(secrets.choice(alphabet) for _ in range(8)),
    ]
    secrets.SystemRandom().shuffle(password)
    return "".join(password)


def _valid_email(value: str) -> bool:
    return bool(re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", value))


def _serialize_password_reset_request(item: PasswordResetRequest, user_map: dict = None) -> dict[str, object]:
    if user_map is not None:
        user = user_map.get(item.user_id) if item.user_id else None
        approver = user_map.get(item.approved_by_id) if item.approved_by_id else None
    else:
        user = User.query.get(item.user_id) if item.user_id else None
        approver = User.query.get(item.approved_by_id) if item.approved_by_id else None
        
    return {
        "id": item.id,
        "public_id": item.public_id,
        "user_id": item.user_id,
        "user_email": item.email,
        "email": item.email,
        "role": item.role,
        "status": "LegacyPending" if item.status == "Pending" else item.status,
        "request_time": item.created_at.isoformat() if item.created_at else None,
        "created_at": item.created_at.isoformat() if item.created_at else None,
        "reviewed_at": item.reviewed_at.isoformat() if item.reviewed_at else None,
        "used_at": item.used_at.isoformat() if item.used_at else None,
        "expires_at": item.token_expires_at.isoformat() if item.token_expires_at else None,
        "email_queued_at": next((event.get("at") for event in (item.activity_log or []) if event.get("event") == "reset_email_queue_started"), None),
        "email_sent_at": item.email_sent_at.isoformat() if item.email_sent_at else None,
        "rejected_reason": item.rejected_reason,
        "matched_user": bool(user),
        "user_name": _full_name(user) if user else "",
        "reviewed_by": _full_name(approver) if approver else "",
        "activity_log": item.activity_log or [],
    }




def _split_name(full_name: str) -> tuple[str, str]:
    parts = [part for part in full_name.strip().split() if part]
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], parts[0]
    return parts[0], " ".join(parts[1:])


@admin_users_bp.route("/api/admin/password-reset-requests", methods=["GET"])
@token_required
def list_password_reset_requests():
    if request.current_user_role != "Admin":
        return jsonify({"error": "Unauthorized"}), 403

    page, limit, offset = _pagination_args()
    query = (
        PasswordResetRequest.query
        .order_by(PasswordResetRequest.created_at.desc(), PasswordResetRequest.id.desc())
    )
    total = query.count()
    requests = query.offset(offset).limit(limit).all()
    
    user_ids = set()
    for r in requests:
        if r.user_id: user_ids.add(r.user_id)
        if r.approved_by_id: user_ids.add(r.approved_by_id)
        
    user_map = {}
    if user_ids:
        user_map = {u.id: u for u in User.query.filter(User.id.in_(user_ids)).all()}

    return jsonify({
        "requests": [_serialize_password_reset_request(item, user_map) for item in requests],
        "pagination": _pagination_payload(page, limit, total),
    }), 200




def _normalize_import_row(row: dict[str, object]) -> dict[str, object]:
    aliases = {
        "fullname": "name",
        "full_name": "name",
        "first name": "first_name",
        "firstname": "first_name",
        "last name": "last_name",
        "lastname": "last_name",
        "e-mail": "email",
        "mail": "email",
        "user_name": "username",
        "user name": "username",
    }
    normalized = {}
    for key, value in row.items():
        clean_key = str(key).lstrip("\ufeff").strip().lower().replace(" ", "_")
        clean_key = aliases.get(clean_key, clean_key)
        normalized[clean_key] = value
    return normalized


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

    page, limit, offset = _pagination_args()
    query = Class.query.order_by(Class.name.asc(), Class.id.asc())
    total = query.count()
    classes = query.offset(offset).limit(limit).all()
    class_ids = [classroom.id for classroom in classes]
    teacher_ids = {classroom.teacher_id for classroom in classes if classroom.teacher_id}
    teacher_map = {
        teacher.id: teacher
        for teacher in User.query.filter(User.id.in_(teacher_ids), User.role == "Teacher").all()
    } if teacher_ids else {}
    student_counts = dict(
        db.session.query(User.class_id, func.count(User.id))
        .filter(User.class_id.in_(class_ids), User.role == "Student")
        .group_by(User.class_id)
        .all()
    ) if class_ids else {}
    return jsonify({
        "classes": [
            {
                "id": classroom.id,
                "public_id": classroom.public_id,
                "name": classroom.name,
                "teacher_id": classroom.teacher_id,
                "teacher_username": teacher_map.get(classroom.teacher_id).username if classroom.teacher_id in teacher_map else "",
                "teacher_name": (
                    f"{(teacher_map[classroom.teacher_id].first_name or '').strip()} { (teacher_map[classroom.teacher_id].last_name or '').strip()}".strip()
                    if classroom.teacher_id in teacher_map else ""
                ),
                "student_count": int(student_counts.get(classroom.id, 0)),
            }
            for classroom in classes
        ],
        "pagination": _pagination_payload(page, limit, total),
    }), 200


@admin_users_bp.route("/api/admin/classes", methods=["POST"])
@token_required
def create_class():
    if request.current_user_role != "Admin":
        return jsonify({"error": "Unauthorized"}), 403

    data = request.json or {}
    print("[admin/classes:create] payload=", data)
    name = str(data.get("name", "")).strip()
    teacher_id = data.get("teacher_id")
    student_ids = data.get("student_ids") or []

    if not name or teacher_id is None:
        return jsonify({"error": "name and teacher_id are required"}), 400

    if not isinstance(student_ids, list):
        return jsonify({"error": "student_ids must be a list"}), 400

    try:
        teacher = User.query.get(int(teacher_id))
    except (TypeError, ValueError):
        return jsonify({"error": "teacher_id must be a valid teacher ID"}), 400

    if teacher is None or teacher.role != "Teacher":
        return jsonify({"error": "Teacher not found"}), 404

    classroom = Class(name=name, teacher_id=teacher.id)
    db.session.add(classroom)

    db.session.flush()

    assigned_students = []
    if student_ids:
        valid_ids = [int(sid) for sid in student_ids if str(sid).isdigit()]
        if valid_ids:
            for student in User.query.filter(User.id.in_(valid_ids), User.role == "Student").all():
                student.class_id = classroom.id
                assigned_students.append(student.id)

    db.session.commit()

    payload = _serialize_class(classroom)
    payload["student_ids"] = assigned_students
    print("[admin/classes:create] created=", payload)
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

    class_quizzes = Quiz.query.filter_by(class_id=classroom.id).all()
    for quiz in class_quizzes:
        quiz.class_id = None

    GameServer.query.filter_by(class_id=classroom.id).delete(synchronize_session=False)
    Announcement.query.filter_by(class_id=classroom.id).delete(synchronize_session=False)

    try:
        db.session.delete(classroom)
        db.session.commit()
    except SQLAlchemyError as exc:
        db.session.rollback()
        return jsonify({"error": "Unable to delete class", "detail": str(exc)}), 500

    return jsonify({"message": "Class deleted successfully.", "class_id": class_id}), 200


@admin_users_bp.route("/api/admin/classes/<int:class_id>", methods=["PUT", "PATCH"])
@token_required
def update_class(class_id: int):
    if request.current_user_role != "Admin":
        return jsonify({"error": "Unauthorized"}), 403

    classroom = Class.query.get(class_id)
    if classroom is None:
        return jsonify({"error": "Class not found"}), 404

    data = request.json or {}
    name = str(data.get("name", classroom.name) or "").strip()
    teacher_id = data.get("teacher_id", classroom.teacher_id)
    student_ids = data.get("student_ids")

    if not name:
        return jsonify({"error": "name is required"}), 400

    try:
        teacher_id = int(teacher_id)
    except (TypeError, ValueError):
        return jsonify({"error": "teacher_id must be a valid teacher ID"}), 400

    teacher = User.query.get(teacher_id)
    if teacher is None or teacher.role != "Teacher":
        return jsonify({"error": "Teacher not found"}), 404

    classroom.name = name
    classroom.teacher_id = teacher.id

    assigned_students: list[int] = []
    if student_ids is not None:
        if not isinstance(student_ids, list):
            return jsonify({"error": "student_ids must be a list"}), 400

        normalized_student_ids: set[int] = set()
        for student_id in student_ids:
            try:
                normalized_student_ids.add(int(student_id))
            except (TypeError, ValueError):
                return jsonify({"error": "student_ids must contain only valid IDs"}), 400

        current_students = User.query.filter_by(class_id=classroom.id, role="Student").all()
        for student in current_students:
            if student.id not in normalized_student_ids:
                student.class_id = None

        if normalized_student_ids:
            selected_students = User.query.filter(
                User.id.in_(normalized_student_ids),
                User.role == "Student",
            ).all()
            valid_student_ids = {student.id for student in selected_students}
            missing_student_ids = normalized_student_ids - valid_student_ids
            if missing_student_ids:
                return jsonify({"error": "One or more selected students were not found"}), 404

            for student in selected_students:
                student.class_id = classroom.id
                assigned_students.append(student.id)

    db.session.commit()

    payload = _serialize_class(classroom)
    if student_ids is not None:
        payload["student_ids"] = sorted(assigned_students)

    return jsonify({"message": "Class updated successfully.", "class": payload}), 200


@admin_users_bp.route("/api/admin/users", methods=["POST"])
@token_required
def create_user():
    if request.current_user_role != "Admin":
        return jsonify({"error": "Unauthorized"}), 403

    data = request.json or {}
    print("[admin/users:create] payload=", {**data, "password": "***" if data.get("password") else ""})
    first_name = str(data.get("first_name", "")).strip()
    last_name = str(data.get("last_name", "")).strip()
    username = str(data.get("username", "")).strip()
    email = str(data.get("email", "")).strip()
    password = str(data.get("password", "")).strip()
    role = str(data.get("role", "")).strip()

    if not first_name or not last_name or not email or role not in ALLOWED_ROLES:
        return jsonify({"error": "Invalid payload"}), 400

    if not _valid_email(email):
        return jsonify({"error": "Invalid email address"}), 400

    if not username:
        username = _unique_username(f"{first_name}{last_name}")

    generated_password = False
    if not password:
        password = _temporary_password()
        generated_password = True
    else:
        password_error = password_policy_error(password)
        if password_error:
            return jsonify({"error": password_error}), 400

    if User.query.filter((User.username == username) | (User.email == email)).first():
        return jsonify({"error": "User already exists"}), 409

    user = User(
        first_name=first_name,
        last_name=last_name,
        username=username,
        email=email,
        password_hash=generate_password_hash(password),
        must_change_password=generated_password,
        role=role,
    )

    db.session.add(user)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return jsonify({"error": "Unable to create user"}), 409

    response = _serialize_user(user)
    if generated_password:
        response["credentials"] = {
            "username": username,
            "temp_password": password,
        }
    print("[admin/users:create] created=", {**response, "credentials": "***" if generated_password else None})
    return jsonify(response), 201


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
    credentials = []
    errors = []
    reserved_usernames: set[str] = set()
    seen_emails: set[str] = set()

    for idx, u in enumerate(users):
        try:
            if not isinstance(u, dict):
                errors.append({"index": idx, "error": "row must be an object"})
                continue

            u = _normalize_import_row(u)
            first_name = str(u.get("first_name", "")).strip()
            last_name = str(u.get("last_name", "")).strip()
            email = str(u.get("email", "")).strip().lower()
            raw_role = str(u.get("role", "")).strip()
            role = ROLE_BY_LOWER.get(raw_role.lower(), raw_role)

            if not first_name or not last_name or not email or not raw_role:
                errors.append({"index": idx, "error": "missing required first_name, last_name, email, or role", "email": email})
                continue

            if not _valid_email(email):
                errors.append({"index": idx, "error": "invalid email address", "email": email})
                continue

            if email in seen_emails:
                errors.append({"index": idx, "error": "duplicate email in uploaded CSV", "email": email})
                continue
            seen_emails.add(email)

            if role not in CSV_ALLOWED_ROLES:
                errors.append({"index": idx, "error": f"CSV upload only supports {', '.join(sorted(CSV_ALLOWED_ROLES))} roles", "email": email})
                continue

            if User.query.filter_by(email=email).first():
                errors.append({"index": idx, "error": "user with this email already exists", "email": email})
                continue

            username = _unique_bulk_username(first_name, last_name, reserved_usernames)
            password = _temporary_password()

            user = User(
                first_name=first_name,
                last_name=last_name,
                username=username,
                email=email,
                password_hash=generate_password_hash(password),
                must_change_password=True,
                role=role,
            )
            db.session.add(user)
            db.session.commit()
            created.append(_serialize_user(user))
            credentials.append(
                {
                    "first_name": first_name,
                    "last_name": last_name,
                    "username": username,
                    "temp_password": password,
                }
            )
        except IntegrityError as ie:
            db.session.rollback()
            errors.append({"index": idx, "error": f"Database integrity error: {str(ie)}", "email": u.get("email", "N/A")})
        except Exception as exc:
            db.session.rollback() # Rollback any partial changes for this user
            errors.append({"index": idx, "error": str(exc), "email": u.get("email", "N/A")})

    return jsonify({"created": created, "credentials": credentials, "errors": errors}), 201


@admin_users_bp.route("/api/admin/users", methods=["GET"])
@token_required
def list_users():
    if request.current_user_role != "Admin":
        return jsonify({"error": "Unauthorized"}), 403

    page, limit, offset = _pagination_args()
    query = User.query.outerjoin(Class, User.class_id == Class.id)
    role = request.args.get("role", "").strip()
    search = request.args.get("search", "").strip()[:120]
    if role and role != "All":
        if role not in ALLOWED_ROLES:
            return jsonify({"error": "Invalid role filter."}), 400
        query = query.filter(User.role == role)
    if search:
        match = f"%{search}%"
        query = query.filter(or_(
            User.first_name.ilike(match), User.last_name.ilike(match),
            User.username.ilike(match), User.email.ilike(match),
            User.role.ilike(match), Class.name.ilike(match),
        ))

    total = query.count()
    users = query.order_by(User.id.asc()).offset(offset).limit(limit).all()
    class_ids = {user.class_id for user in users if user.class_id is not None}
    class_map = {item.id: item for item in Class.query.filter(Class.id.in_(class_ids)).all()} if class_ids else {}
    teacher_ids = {user.id for user in users if user.role == "Teacher"}
    teacher_classes_map = {}
    if teacher_ids:
        for classroom in Class.query.filter(Class.teacher_id.in_(teacher_ids)).order_by(Class.name.asc()).all():
            teacher_classes_map.setdefault(classroom.teacher_id, []).append(classroom)

    return jsonify({
        "users": [_serialize_user(user, class_map, teacher_classes_map) for user in users],
        "pagination": _pagination_payload(page, limit, total),
    }), 200


@admin_users_bp.route("/api/admin/users/<int:user_id>", methods=["DELETE"])
@token_required
def delete_user(user_id: int):
    if request.current_user_role != "Admin":
        return jsonify({"error": "Unauthorized"}), 403

    user = User.query.get(user_id)
    if user is None:
        return jsonify({"error": "User not found"}), 404

    if user.role == "Admin" and User.query.filter_by(role="Admin").count() <= 1:
        return jsonify({"error": "Cannot delete the last admin account"}), 400

    if user.role == "Teacher":
        teacher_classes = Class.query.filter_by(teacher_id=user.id).all()
        teacher_quizzes = Quiz.query.filter_by(teacher_id=user.id).all()
        replacement_teacher = User.query.filter(User.role == "Teacher", User.id != user.id).order_by(User.id.asc()).first()
        if (teacher_classes or teacher_quizzes) and replacement_teacher is None:
            return jsonify({"error": "Assign this teacher's classes/quizzes to another teacher before deleting the only teacher account."}), 400

        for classroom in teacher_classes:
            classroom.teacher_id = replacement_teacher.id

        for quiz in teacher_quizzes:
            quiz.teacher_id = replacement_teacher.id

        Announcement.query.filter_by(teacher_id=user.id).delete(synchronize_session=False)
        GameServer.query.filter_by(owner_teacher_id=user.id).delete(synchronize_session=False)

    if user.role == "Parent":
        for child in User.query.filter_by(parent_id=user.id, role="Student").all():
            child.parent_id = None

    if user.role == "Student":
        QuizResult.query.filter_by(student_id=user.id).delete(synchronize_session=False)
        MissionProgress.query.filter_by(user_id=user.id).delete(synchronize_session=False)
        PlaytimeLog.query.filter_by(user_id=user.id).delete(synchronize_session=False)

    Message.query.filter(
        (Message.sender_id == user.id) | (Message.receiver_id == user.id)
    ).delete(synchronize_session=False)

    try:
        db.session.delete(user)
        db.session.commit()
    except SQLAlchemyError as exc:
        db.session.rollback()
        return jsonify({"error": "Unable to delete user", "detail": str(exc)}), 500

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

    if password:
        password_error = password_policy_error(password)
        if password_error:
            return jsonify({"error": password_error}), 400

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
        user.must_change_password = False

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


@admin_users_bp.route("/api/admin/classes/<int:class_id>/students", methods=["GET"])
@token_required
def get_class_students(class_id: int):
    if request.current_user_role != "Admin":
        return jsonify({"error": "Unauthorized"}), 403

    classroom = db.session.get(Class, class_id)
    if classroom is None:
        return jsonify({"error": "Class not found."}), 404
    page, limit, offset = _pagination_args()
    query = User.query.filter_by(class_id=class_id, role="Student").order_by(User.first_name.asc(), User.last_name.asc(), User.id.asc())
    total = query.count()
    students = query.offset(offset).limit(limit).all()
    parents = {
        parent.id: parent
        for parent in User.query.filter(User.id.in_({student.parent_id for student in students if student.parent_id}), User.role == "Parent").all()
    } if any(student.parent_id for student in students) else {}
    response = [_serialize_user(student) | {
        "parent_name": (
            f"{(parents[student.parent_id].first_name or '').strip()} {(parents[student.parent_id].last_name or '').strip()}".strip()
            or parents[student.parent_id].username
        ) if student.parent_id in parents else None,
    } for student in students]
    return jsonify({"students": response, "pagination": _pagination_payload(page, limit, total)}), 200


@admin_users_bp.route("/api/admin/classes/<int:class_id>/student-ids", methods=["GET"])
@token_required
def get_class_student_ids(class_id: int):
    if request.current_user_role != "Admin":
        return jsonify({"error": "Unauthorized"}), 403
    if db.session.get(Class, class_id) is None:
        return jsonify({"error": "Class not found."}), 404
    ids = [row[0] for row in db.session.query(User.id).filter_by(class_id=class_id, role="Student").all()]
    return jsonify({"student_ids": ids}), 200


@admin_users_bp.route("/api/admin/class-assignment/students", methods=["GET"])
@token_required
def get_class_assignment_students():
    if request.current_user_role != "Admin":
        return jsonify({"error": "Unauthorized"}), 403

    page, limit, offset = _pagination_args()
    query = User.query.outerjoin(Class, User.class_id == Class.id).filter(User.role == "Student")
    if request.args.get("unassigned", "true").lower() in {"1", "true", "yes"}:
        query = query.filter(User.class_id.is_(None))
    name = request.args.get("name", "").strip()[:100]
    grade = request.args.get("grade", "").strip()[:80]
    section = request.args.get("section", "").strip()[:80]
    if name:
        match = f"%{name}%"
        query = query.filter(or_(User.first_name.ilike(match), User.last_name.ilike(match), User.username.ilike(match)))
    if grade:
        query = query.filter(Class.name.ilike(f"%{grade}%"))
    if section:
        query = query.filter(Class.name.ilike(f"%{section}%"))
    total = query.count()
    students = query.order_by(User.first_name.asc(), User.last_name.asc(), User.id.asc()).offset(offset).limit(limit).all()
    class_ids = {student.class_id for student in students if student.class_id is not None}
    class_map = {item.id: item for item in Class.query.filter(Class.id.in_(class_ids)).all()} if class_ids else {}
    return jsonify({
        "students": [_serialize_user(student, class_map, {}) for student in students],
        "pagination": _pagination_payload(page, limit, total),
    }), 200


@admin_users_bp.route("/api/admin/class-assignment/options", methods=["GET"])
@token_required
def get_class_assignment_options():
    if request.current_user_role != "Admin":
        return jsonify({"error": "Unauthorized"}), 403

    students = User.query.filter_by(role="Student").order_by(User.username.asc()).all()
    teachers = User.query.filter_by(role="Teacher").order_by(User.username.asc()).all()
    classes = Class.query.order_by(Class.name.asc()).all()

    class_map = {c.id: c for c in classes}
    teacher_classes_map = {}
    for c in classes:
        if c.teacher_id:
            teacher_classes_map.setdefault(c.teacher_id, []).append(c)
    for t_id in teacher_classes_map:
        teacher_classes_map[t_id].sort(key=lambda x: x.name)

    teacher_map = {t.id: t for t in teachers}
    student_map = {}
    for s in students:
        if s.class_id:
            student_map.setdefault(s.class_id, []).append(s)
    for c_id in student_map:
        student_map[c_id].sort(key=lambda x: x.id)

    return jsonify(
        {
            "students": [_serialize_user(student, class_map, teacher_classes_map) for student in students],
            "teachers": [_serialize_user(teacher, class_map, teacher_classes_map) for teacher in teachers],
            "classes": [_serialize_class(classroom, teacher_map, student_map) for classroom in classes],
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
    role_counts = {
        role: int(count)
        for role, count in db.session.query(User.role, func.count(User.id)).group_by(User.role).all()
    }
    active_servers = GameServer.query.filter(GameServer.last_heartbeat > heartbeat_cutoff).count()
    total_servers = GameServer.query.count()

    if not student_ids:
        return jsonify(
            {
                "summary": {
                    "total_students": 0,
                    "role_counts": role_counts,
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
    
    mp_active = db.session.query(MissionProgress.user_id).filter(
        MissionProgress.user_id.in_(student_ids), MissionProgress.updated_at >= cutoff_dt
    ).all()
    for (uid,) in mp_active: active_player_ids.add(uid)
        
    qr_active = db.session.query(QuizResult.student_id).filter(
        QuizResult.student_id.in_(student_ids), QuizResult.updated_at >= cutoff_dt
    ).all()
    for (uid,) in qr_active: active_player_ids.add(uid)
        
    pl_active = db.session.query(PlaytimeLog.user_id).filter(
        PlaytimeLog.user_id.in_(student_ids), PlaytimeLog.date >= cutoff_date
    ).all()
    for (uid,) in pl_active: active_player_ids.add(uid)

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
                "role_counts": role_counts,
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
