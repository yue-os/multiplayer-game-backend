import os
import uuid
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.exc import OperationalError
from sqlalchemy import inspect, text

class Base(DeclarativeBase):
    pass

db = SQLAlchemy(model_class=Base)


def _ensure_public_ids(app, inspector):
    from app.server.models import user

    model_table_pairs = [
        (user.User, 'users'),
        (user.Class, 'classes'),
        (user.Mission, 'missions'),
        (user.MissionProgress, 'mission_progress'),
        (user.Quiz, 'quizzes'),
        (user.QuizResult, 'quiz_results'),
        (user.Message, 'messages'),
        (user.GameServer, 'game_servers'),
        (user.PlaytimeLog, 'playtime_logs'),
    ]

    for model, table_name in model_table_pairs:
        if not inspector.has_table(table_name):
            continue

        columns = {col['name'] for col in inspector.get_columns(table_name)}
        modified = False
        if 'public_id' not in columns:
            db.session.execute(text(f"ALTER TABLE {table_name} ADD COLUMN public_id VARCHAR(36)"))
            modified = True

        # Only query if we might actually need to backfill
        if 'public_id' in columns:
            rows_to_fix = model.query.filter(model.public_id.is_(None)).limit(100).all()
            if rows_to_fix:
                for row in rows_to_fix:
                    row.public_id = str(uuid.uuid4())
                modified = True

        if modified:
            db.session.commit()
            db.session.execute(text(f"CREATE UNIQUE INDEX IF NOT EXISTS uq_{table_name}_public_id ON {table_name}(public_id)"))
            db.session.execute(text(f"ALTER TABLE {table_name} ALTER COLUMN public_id SET NOT NULL"))
            db.session.commit()


def _ensure_user_name_columns(app, inspector):
    if not inspector.has_table('users'):
        return

    columns = {col['name'] for col in inspector.get_columns('users')}
    modified = False

    if 'first_name' not in columns:
        db.session.execute(text("ALTER TABLE users ADD COLUMN first_name VARCHAR(80) DEFAULT ''"))
        modified = True

    if 'last_name' not in columns:
        db.session.execute(text("ALTER TABLE users ADD COLUMN last_name VARCHAR(80) DEFAULT ''"))
        modified = True

    if modified:
        db.session.execute(text("UPDATE users SET first_name = '' WHERE first_name IS NULL"))
        db.session.execute(text("UPDATE users SET last_name = '' WHERE last_name IS NULL"))
        db.session.execute(text("ALTER TABLE users ALTER COLUMN first_name SET NOT NULL"))
        db.session.execute(text("ALTER TABLE users ALTER COLUMN last_name SET NOT NULL"))
        db.session.commit()


def _ensure_game_server_columns(app, inspector):
    if not inspector.has_table('game_servers'):
        return

    columns = {col['name'] for col in inspector.get_columns('game_servers')}
    modified = False

    if 'persistent' not in columns:
        db.session.execute(text("ALTER TABLE game_servers ADD COLUMN persistent BOOLEAN DEFAULT FALSE"))
        modified = True

    if 'owner_teacher_id' not in columns:
        db.session.execute(text("ALTER TABLE game_servers ADD COLUMN owner_teacher_id INTEGER"))
        modified = True

    if 'class_id' not in columns:
        db.session.execute(text("ALTER TABLE game_servers ADD COLUMN class_id INTEGER"))
        modified = True

    if 'required_players' not in columns:
        db.session.execute(text("ALTER TABLE game_servers ADD COLUMN required_players INTEGER DEFAULT 2"))
        modified = True

    if modified:
        db.session.execute(text("UPDATE game_servers SET persistent = FALSE WHERE persistent IS NULL"))
        db.session.execute(text("UPDATE game_servers SET required_players = 2 WHERE required_players IS NULL OR required_players < 1"))
        db.session.execute(text("ALTER TABLE game_servers ALTER COLUMN persistent SET NOT NULL"))
        db.session.execute(text("ALTER TABLE game_servers ALTER COLUMN required_players SET NOT NULL"))
        db.session.commit()


def _ensure_quiz_columns(app, inspector):
    if not inspector.has_table('quizzes'):
        return

    columns = {col['name'] for col in inspector.get_columns('quizzes')}
    modified = False

    if 'class_id' not in columns:
        db.session.execute(text("ALTER TABLE quizzes ADD COLUMN class_id INTEGER"))
        modified = True

    if modified:
        db.session.commit()

def _ensure_messages_columns(app, inspector):
    if not inspector.has_table('messages'):
        return

    columns = {col['name'] for col in inspector.get_columns('messages')}
    modified = False

    if 'quiz_result_id' not in columns:
        db.session.execute(text("ALTER TABLE messages ADD COLUMN quiz_result_id INTEGER"))
        modified = True

    if modified:
        db.session.commit()

def init_db(app):
    database_url = os.getenv('DATABASE_URL')
    if not database_url:
        raise ValueError("DATABASE_URL is not set in environment variables")
    
    # SQLAlchemy requires 'postgresql://'
    if database_url.startswith("postgres://"):
        database_url = database_url.replace("postgres://", "postgresql://", 1)

    app.config['SQLALCHEMY_DATABASE_URI'] = database_url
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    
    db.init_app(app)
    
    with app.app_context():
        # Explicitly import models to ensure they are registered with SQLAlchemy 
        # before we attempt to create the tables.
        from app.server.models import user

        try:
            # Create tables if they don't exist
            db.create_all()
            
            # Reuse one inspector instance to save time
            inspector = inspect(db.engine)
            _ensure_user_name_columns(app, inspector)
            _ensure_game_server_columns(app, inspector)
            _ensure_quiz_columns(app, inspector)
            _ensure_messages_columns(app, inspector)
            _ensure_public_ids(app, inspector)

            # Import seed function inside the context/function to avoid circular imports 
            try:
                from app.server.seed import seed_database
                seed_database()
            except Exception as e:
                print(f"Error seeding database: {e}")
        except OperationalError as e:
            raise RuntimeError(
                "Database authentication failed. Check DATABASE_URL credentials."
            ) from e