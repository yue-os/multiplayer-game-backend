from __future__ import annotations

import os
from enum import Enum
from functools import lru_cache
from datetime import datetime
from typing import Generator
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker
from sqlalchemy import String, Integer
from werkzeug.security import generate_password_hash


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


router = APIRouter(prefix="/api/admin/users", tags=["admin-users"])


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


@router.get("", response_model=list[AdminUserRead])
def list_users(db: Session = Depends(get_db_session)) -> list[AdminUserRead]:
    users = db.execute(select(UserRecord).order_by(UserRecord.id.asc())).scalars().all()
    return [_serialize_user(user) for user in users]


@router.delete("/{user_id}", status_code=status.HTTP_200_OK)
def delete_user(user_id: int, db: Session = Depends(get_db_session)) -> dict[str, str | int]:
    user = db.get(UserRecord, user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found.",
        )

    db.delete(user)
    db.commit()
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
