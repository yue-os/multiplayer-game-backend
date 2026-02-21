from app.server.database import db
from datetime import datetime
import uuid

# Common mixins or base classes can go here
class TimestampMixin:
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class PublicIdMixin:
    public_id = db.Column(db.String(36), unique=True, index=True, nullable=False, default=lambda: str(uuid.uuid4()))
