from datetime import datetime
from app.server.database import db

class Announcement(db.Model):
    __tablename__ = 'announcements'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    class_id = db.Column(db.Integer, nullable=False)
    teacher_id = db.Column(db.Integer, nullable=False)
    title = db.Column(db.String(255), nullable=False)
    message = db.Column(db.Text, nullable=False)
    is_hidden = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'class_id': self.class_id,
            'teacher_id': self.teacher_id,
            'title': self.title,
            'message': self.message,
            'is_hidden': bool(self.is_hidden),
            'created_at': self.created_at.isoformat() + 'Z' if self.created_at else None
        }
