from datetime import datetime, timezone

from app.server.database import db


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class AuditLog(db.Model):
    """Append-only record of authenticated platform activity."""

    __tablename__ = "audit_logs"

    id = db.Column(db.Integer, primary_key=True)
    timestamp = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now, index=True)
    user_id = db.Column(db.Integer, nullable=True, index=True)
    user_role = db.Column(db.String(20), nullable=False, default="System", index=True)
    action_type = db.Column(db.String(20), nullable=False, index=True)
    action = db.Column(db.String(255), nullable=False)
    entity_type = db.Column(db.String(80), nullable=True)
    entity_id = db.Column(db.String(80), nullable=True)
    request_method = db.Column(db.String(10), nullable=True)
    request_path = db.Column(db.String(255), nullable=True)
    ip_address = db.Column(db.String(64), nullable=True)
