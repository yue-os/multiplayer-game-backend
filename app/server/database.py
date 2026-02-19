import os
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.orm import DeclarativeBase

class Base(DeclarativeBase):
    pass

db = SQLAlchemy(model_class=Base)

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
        
        # Create tables if they don't exist
        db.create_all()
        
        # Import seed function inside the context/function to avoid circular imports 
        try:
            from app.server.seed import seed_database
            seed_database()
        except Exception as e:
            print(f"Error seeding database: {e}")