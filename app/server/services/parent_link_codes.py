import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta


PARENT_LINK_CODE_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
PARENT_LINK_CODE_LENGTH = 10
PARENT_LINK_CODE_LIFETIME = timedelta(hours=24)
PARENT_LINK_ATTEMPT_LIMIT = 10
PARENT_LINK_ATTEMPT_WINDOW = timedelta(minutes=15)


def _parent_link_code_pepper() -> bytes:
    secret = (
        os.getenv("PARENT_LINK_CODE_PEPPER")
        or os.getenv("JWT_SECRET")
        or os.getenv("SECRET_KEY")
        or ""
    ).strip()
    if len(secret) < 32 or secret in {"default_secret", "your-secret-key-here"}:
        raise RuntimeError(
            "Set PARENT_LINK_CODE_PEPPER to a unique random secret of at least 32 characters."
        )
    return secret.encode("utf-8")


def hash_parent_link_code(code: str) -> str:
    return hmac.new(_parent_link_code_pepper(), code.encode("ascii"), hashlib.sha256).hexdigest()


def consume_parent_link_attempt(parent_id: int, now: datetime | None = None) -> bool:
    """Atomically count a parent's redemption attempt in a rolling fixed window."""
    from sqlalchemy import case, or_
    from app.server.database import db
    from app.server.models.user import User

    attempted_at = now or datetime.utcnow()
    window_expired = or_(
        User.parent_link_attempt_window_started_at.is_(None),
        User.parent_link_attempt_window_started_at <= attempted_at - PARENT_LINK_ATTEMPT_WINDOW,
    )
    updated = (
        User.query.filter_by(id=parent_id, role="Parent")
        .filter(or_(window_expired, User.parent_link_attempt_count < PARENT_LINK_ATTEMPT_LIMIT))
        .update(
            {
                User.parent_link_attempt_count: case(
                    (window_expired, 1), else_=User.parent_link_attempt_count + 1
                ),
                User.parent_link_attempt_window_started_at: case(
                    (window_expired, attempted_at),
                    else_=User.parent_link_attempt_window_started_at,
                ),
            },
            synchronize_session=False,
        )
    )
    if updated != 1:
        db.session.rollback()
        return False

    # Commit the attempt before validating the submitted code so failures count too.
    db.session.commit()
    return True


def clear_parent_link_attempts(parent_id: int) -> None:
    from app.server.database import db
    from app.server.models.user import User

    User.query.filter_by(id=parent_id, role="Parent").update(
        {
            User.parent_link_attempt_count: 0,
            User.parent_link_attempt_window_started_at: None,
        },
        synchronize_session=False,
    )


def issue_parent_link_code(student, now: datetime | None = None) -> dict[str, str]:
    """Rotate the student's active parent-link code and return its one-time secret."""
    issued_at = now or datetime.utcnow()
    code = "".join(secrets.choice(PARENT_LINK_CODE_ALPHABET) for _ in range(PARENT_LINK_CODE_LENGTH))
    expires_at = issued_at + PARENT_LINK_CODE_LIFETIME

    student.parent_link_code_hash = hash_parent_link_code(code)
    student.parent_link_code_expires_at = expires_at

    return {
        "connection_code": code,
        "expires_at": expires_at.isoformat(timespec="seconds") + "Z",
    }
