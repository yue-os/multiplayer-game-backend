from app.server.database import db
from app.server.models.appModel import TimestampMixin
from datetime import datetime

# --- User & Relationships ---

class User(db.Model, TimestampMixin):
    __tablename__ = 'users'

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False) # Admin, Teacher, Parent, Student
    
    # Relationship: Parent -> Student (One Parent can have many Students/Children)
    parent_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    children = db.relationship('User', backref=db.backref('parent', remote_side=[id]), lazy=True)

    # Relationship: Student -> Class (Many Students in one Class)
    class_id = db.Column(db.Integer, db.ForeignKey('classes.id'), nullable=True)

    def to_dict(self):
        return {
            "id": self.id,
            "username": self.username,
            "email": self.email,
            "role": self.role,
            "parent_id": self.parent_id,
            "class_id": self.class_id
        }

class Class(db.Model, TimestampMixin):
    __tablename__ = 'classes'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    
    # Relationship: Teacher -> Class (One Teacher manages many Classes)
    teacher_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    teacher = db.relationship('User', foreign_keys=[teacher_id], backref='classes_taught')
    
    # Backref for students is defined in User.class_id

# --- Game Data ---

class Mission(db.Model):
    __tablename__ = 'missions'
    
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(100), nullable=False)
    level_req = db.Column(db.Integer, default=1)
    
    # Static data, usually no direct user relationship needed unless for tracking creation
    # But Progress links User + Mission

class MissionProgress(db.Model, TimestampMixin):
    __tablename__ = 'mission_progress'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    mission_id = db.Column(db.Integer, db.ForeignKey('missions.id'), nullable=False)
    
    status = db.Column(db.String(20), default="started") # started, completed, failed
    score = db.Column(db.Integer, default=0)

class Quiz(db.Model, TimestampMixin):
    __tablename__ = 'quizzes'
    
    id = db.Column(db.Integer, primary_key=True)
    teacher_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    title = db.Column(db.String(100), nullable=False)
    timer_seconds = db.Column(db.Integer, default=300)
    start_date = db.Column(db.DateTime, default=datetime.utcnow)

class QuizResult(db.Model, TimestampMixin):
    __tablename__ = 'quiz_results'
    
    id = db.Column(db.Integer, primary_key=True)
    quiz_id = db.Column(db.Integer, db.ForeignKey('quizzes.id'), nullable=False)
    student_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    score = db.Column(db.Integer, nullable=False)

class GameServer(db.Model):
    __tablename__ = 'game_servers'
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100))
    ip = db.Column(db.String(50), nullable=False)
    port = db.Column(db.Integer, nullable=False)
    last_heartbeat = db.Column(db.Float, default=datetime.utcnow().timestamp)
    player_count = db.Column(db.Integer, default=0)

    # Composite unique constraint to identify servers by IP:Port
    __table_args__ = (db.UniqueConstraint('ip', 'port', name='_server_ip_port_uc'),)

# --- Logs ---

class PlaytimeLog(db.Model):
    __tablename__ = 'playtime_logs'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    date = db.Column(db.Date, default=datetime.utcnow().date)
    duration_minutes = db.Column(db.Integer, default=0)
