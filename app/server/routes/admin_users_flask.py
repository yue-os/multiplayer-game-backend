from __future__ import annotations

from flask import Blueprint, jsonify, request
from sqlalchemy.exc import IntegrityError
from werkzeug.security import generate_password_hash

from app.auth.auth_bearer import token_required
from app.server.database import db
from app.server.models.user import Class, User


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
    return {
        "id": classroom.id,
        "public_id": classroom.public_id,
        "name": classroom.name,
        "teacher_id": classroom.teacher_id,
        "teacher_username": teacher.username if teacher is not None else "",
    }


@admin_users_bp.route("/api/admin/classes", methods=["POST"])
@token_required
def create_class():
    if request.current_user_role != "Admin":
        return jsonify({"error": "Unauthorized"}), 403

    data = request.json or {}
    name = str(data.get("name", "")).strip()
    teacher_id = data.get("teacher_id")

    if not name or teacher_id is None:
        return jsonify({"error": "name and teacher_id are required"}), 400

    teacher = User.query.get(int(teacher_id))
    if teacher is None or teacher.role != "Teacher":
        return jsonify({"error": "Teacher not found"}), 404

    classroom = Class(name=name, teacher_id=teacher.id)
    db.session.add(classroom)
    db.session.commit()

    return jsonify({"message": "Class created successfully.", "class": _serialize_class(classroom)}), 201


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
