from __future__ import annotations

import os
from enum import Enum
from functools import lru_cache
from datetime import datetime
from typing import Generator
import uuid

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, select, delete, update, func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker
from sqlalchemy import String, Integer, Boolean
from werkzeug.security import generate_password_hash
from app.auth.auth_handler import decodeJWT

from app.server.models.user import PasswordResetRequest, PlaytimeLog, MissionProgress, QuizResult, Class, Quiz

class UserRole(str, Enum):
    ADMIN = "Admin"
    TEACHER = "Teacher"
    PARENT = "Parent"
    STUDENT = "Student"


class Base(DeclarativeBase):
    pass


class UserRecord(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    public_id: Mapped[str] = mapped_column(String(36), unique=True, index=True, nullable=False, default=lambda: str(uuid.uuid4()))
    first_name: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    last_name: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    username: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    email: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    must_change_password: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    parent_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    class_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(nullable=False, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class AdminUserCreate(BaseModel):
    first_name: str = Field(min_length=1, max_length=80)
    last_name: str = Field(min_length=1, max_length=80)
    username: str = Field(min_length=1, max_length=80)
    email: str = Field(min_length=3, max_length=120)
    password: str = Field(min_length=6, max_length=255)
    role: UserRole


class AdminUserUpdate(BaseModel):
    first_name: str = Field(min_length=1, max_length=80)
    last_name: str = Field(min_length=1, max_length=80)
    username: str = Field(min_length=1, max_length=80)
    email: str = Field(min_length=3, max_length=120)
    password: str | None = Field(default=None, min_length=6, max_length=255)
    role: UserRole


class AdminUserRead(BaseModel):
    id: int
    public_id: str
    first_name: str
    last_name: str
    username: str
    email: str
    role: UserRole


def require_admin(authorization: str | None = Header(default=None)) -> None:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="A valid bearer token is required.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    payload = decodeJWT(authorization.removeprefix("Bearer ").strip())
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="The bearer token is invalid or expired.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if payload.get("role") != UserRole.ADMIN.value:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access is required.",
        )


router = APIRouter(
    prefix="/api/admin/users",
    tags=["admin-users"],
    dependencies=[Depends(require_admin)],
)


@lru_cache(maxsize=1)
def _make_engine():
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is not configured")
    if database_url.startswith("postgres://"):
        database_url = database_url.replace("postgres://", "postgresql://", 1)
    return create_engine(database_url, pool_pre_ping=True)


@lru_cache(maxsize=1)
def _make_session_factory():
    return sessionmaker(bind=_make_engine(), autoflush=False, autocommit=False)


def get_db_session() -> Generator[Session, None, None]:
    try:
        session = _make_session_factory()()
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc
    try:
        yield session
    finally:
        session.close()


def _serialize_user(user: UserRecord) -> AdminUserRead:
    return AdminUserRead(
        id=user.id,
        public_id=user.public_id,
        first_name=user.first_name,
        last_name=user.last_name,
        username=user.username,
        email=user.email,
        role=UserRole(user.role),
    )


@router.post("", response_model=AdminUserRead, status_code=status.HTTP_201_CREATED)
def create_user(payload: AdminUserCreate, db: Session = Depends(get_db_session)) -> AdminUserRead:
    existing_user = db.execute(
        select(UserRecord).where(
            (UserRecord.username == payload.username) | (UserRecord.email == payload.email)
        )
    ).scalar_one_or_none()

    if existing_user is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A user with the same username or email already exists.",
        )

    user = UserRecord(
        first_name=payload.first_name,
        last_name=payload.last_name,
        username=payload.username,
        email=payload.email,
        password_hash=generate_password_hash(payload.password),
        must_change_password=False,
        role=payload.role.value,
    )

    db.add(user)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Unable to create the user due to a database constraint.",
        ) from exc

    db.refresh(user)
    return _serialize_user(user)


@router.get("")
def list_users(
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=15, ge=1, le=20),
    role: str = Query(default=""),
    search: str = Query(default="", max_length=120),
    db: Session = Depends(get_db_session),
) -> dict[str, object]:
    query = select(UserRecord).outerjoin(Class, UserRecord.class_id == Class.id)
    count_query = select(func.count(UserRecord.id)).select_from(UserRecord).outerjoin(Class, UserRecord.class_id == Class.id)
    filters = []
    if role and role != "All":
        if role not in {item.value for item in UserRole}:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid role filter.")
        filters.append(UserRecord.role == role)
    if search.strip():
        pattern = f"%{search.strip()}%"
        filters.append(or_(
            UserRecord.first_name.ilike(pattern),
            UserRecord.last_name.ilike(pattern),
            UserRecord.username.ilike(pattern),
            UserRecord.email.ilike(pattern),
            UserRecord.role.ilike(pattern),
            Class.name.ilike(pattern),
        ))
    if filters:
        query = query.where(*filters)
        count_query = count_query.where(*filters)

    total = int(db.execute(count_query).scalar_one() or 0)
    users = db.execute(
        query.order_by(UserRecord.id.asc()).offset((page - 1) * limit).limit(limit)
    ).scalars().all()
    class_ids = {user.class_id for user in users if user.class_id is not None}
    class_map = {}
    if class_ids:
        class_map = {
            item.id: item
            for item in db.execute(select(Class).where(Class.id.in_(class_ids))).scalars().all()
        }
    teacher_ids = {user.id for user in users if user.role == UserRole.TEACHER.value}
    teacher_classes: dict[int, list[Class]] = {}
    if teacher_ids:
        for classroom in db.execute(
            select(Class).where(Class.teacher_id.in_(teacher_ids)).order_by(Class.name.asc())
        ).scalars().all():
            teacher_classes.setdefault(classroom.teacher_id, []).append(classroom)

    rows = []
    for user in users:
        rows.append({
            **_serialize_user(user).model_dump(),
            "class_id": user.class_id,
            "class_name": class_map[user.class_id].name if user.class_id in class_map else None,
            "parent_id": user.parent_id,
            "must_change_password": user.must_change_password,
            "mustChangePassword": user.must_change_password,
            "classes": [
                {"id": item.id, "public_id": item.public_id, "name": item.name}
                for item in teacher_classes.get(user.id, [])
            ],
        })
    return {
        "users": rows,
        "pagination": {
            "page": page,
            "limit": limit,
            "total": total,
            "pages": (total + limit - 1) // limit,
        },
    }


@router.delete("/{user_id}", status_code=status.HTTP_200_OK)
def delete_user(user_id: int, db: Session = Depends(get_db_session)) -> dict[str, str | int]:
    user = db.get(UserRecord, user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found.",
        )

    db.execute(delete(PasswordResetRequest).where(PasswordResetRequest.user_id == user_id))

    if user.role == "Student":
        db.execute(delete(PlaytimeLog).where(PlaytimeLog.user_id == user_id))
        db.execute(delete(MissionProgress).where(MissionProgress.user_id == user_id))
        db.execute(delete(QuizResult).where(QuizResult.student_id == user_id))
        
    elif user.role == "Parent":
        db.execute(update(UserRecord).where(UserRecord.parent_id == user_id).values(parent_id=None))

    elif user.role == "Teacher":
        # Guard check: Prevent deletion if they own classes or quizzes
        assigned_classes = db.execute(select(Class).where(Class.teacher_id == user_id)).first()
        assigned_quizzes = db.execute(select(Quiz).where(Quiz.teacher_id == user_id)).first()
        
        if assigned_classes or assigned_quizzes:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Please reassign this teacher's classes and quizzes to another teacher before deleting their account."
            )

    db.delete(user)

    try:
        db.commit()
    except Exception as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete user: {str(exc)}"
        )

    return {"message": "User deleted successfully.", "user_id": user_id}


@router.put("/{user_id}", response_model=AdminUserRead)
@router.patch("/{user_id}", response_model=AdminUserRead)
def update_user(user_id: int, payload: AdminUserUpdate, db: Session = Depends(get_db_session)) -> AdminUserRead:
    user = db.get(UserRecord, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")

    conflict = db.execute(
        select(UserRecord).where(
            ((UserRecord.username == payload.username) | (UserRecord.email == payload.email))
            & (UserRecord.id != user.id)
        )
    ).scalar_one_or_none()
    if conflict is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Another user already uses the same username or email.",
        )

    user.first_name = payload.first_name
    user.last_name = payload.last_name
    user.username = payload.username
    user.email = payload.email
    user.role = payload.role.value
    if payload.password:
        user.password_hash = generate_password_hash(payload.password)
        user.must_change_password = False

    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Unable to update the user due to a database constraint.",
        ) from exc

    db.refresh(user)
    return _serialize_user(user)
