import os
import uuid
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.exc import OperationalError
from sqlalchemy import inspect, text

class Base(DeclarativeBase):
    pass

db = SQLAlchemy(model_class=Base)


def _ensure_public_ids(app):
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

    inspector = inspect(db.engine)

    for model, table_name in model_table_pairs:
        if not inspector.has_table(table_name):
            continue

        columns = {col['name'] for col in inspector.get_columns(table_name)}
        if 'public_id' not in columns:
            db.session.execute(text(f"ALTER TABLE {table_name} ADD COLUMN public_id VARCHAR(36)"))
            db.session.commit()

        rows = model.query.filter(model.public_id.is_(None)).all()
        if rows:
            for row in rows:
                row.public_id = str(uuid.uuid4())
            db.session.commit()

        db.session.execute(text(f"CREATE UNIQUE INDEX IF NOT EXISTS uq_{table_name}_public_id ON {table_name}(public_id)"))
        db.session.commit()

        db.session.execute(text(f"ALTER TABLE {table_name} ALTER COLUMN public_id SET NOT NULL"))
        db.session.commit()

def init_db(app):
    database_url = os.getenv('SUPABASE_DB_URL')
    if not database_url:
        raise ValueError("SUPABASE_DB_URL is not set in environment variables")
    
    # SQLAlchemy requires 'postgresql://', Supabase might provide 'postgres://'
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
            _ensure_public_ids(app)

            # Import seed function inside the context/function to avoid circular imports 
            try:
                from app.server.seed import seed_database
                seed_database()
            except Exception as e:
                print(f"Error seeding database: {e}")
        except OperationalError as e:
            raise RuntimeError(
                "Database authentication failed. Check SUPABASE_DB_URL credentials. "
                "If using Supabase pooler (:6543), use username format "
                "'postgres.<project-ref>' with your database password."
            ) from e