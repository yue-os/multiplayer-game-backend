from app.server.database import db
from app.server.models.user import User, Class, Mission, MissionProgress, PlaytimeLog
from werkzeug.security import generate_password_hash
from sqlalchemy.exc import IntegrityError
from datetime import date

def seed_database():
    """
    Populates the database with initial sample data if no users exist.
    """
    try:
        if User.query.first():
            print("Database already contains data. Skipping seed.")
            return

        print("Seeding database with sample data...")

        admin = User(
            username='admin', 
            email='admin@game.com', 
            password_hash=generate_password_hash('admin123'), 
            role='Admin'
        )
        
        teacher = User(
            username='Mr.Smith', 
            email='smith@school.com', 
            password_hash=generate_password_hash('teach123'), 
            role='Teacher'
        )
        
        parent = User(
            username='ParentJane', 
            email='jane@home.com', 
            password_hash=generate_password_hash('parent123'), 
            role='Parent'
        )
        
        student = User(
            username='Timmy', 
            email='timmy@home.com', 
            password_hash=generate_password_hash('timmy123'), 
            role='Student'
        )

        db.session.add_all([admin, teacher, parent, student])
        db.session.commit() # Commit now to generate IDs for relationships

        math_class = Class(name="Algebra 101", teacher_id=teacher.id)
        db.session.add(math_class)
        db.session.commit()

        student.parent_id = parent.id
        student.class_id = math_class.id
        db.session.add(student)

        m1 = Mission(title="Tutorial: Movement", level_req=1)
        m2 = Mission(title="Chapter 1: The Forest", level_req=2)
        m3 = Mission(title="Chapter 2: The Cave", level_req=5)
        db.session.add_all([m1, m2, m3])
        db.session.commit()

        prog1 = MissionProgress(
            user_id=student.id, 
            mission_id=m1.id, 
            status='completed', 
            score=100
        )
        
        log1 = PlaytimeLog(
            user_id=student.id, 
            date=date.today(), 
            duration_minutes=45
        )

        db.session.add_all([prog1, log1])
        db.session.commit()

        print("Database seeded successfully!")
    except IntegrityError:
        db.session.rollback()
        print("Database already seeded by another worker. Skipping seed.")
    except Exception as e:
        db.session.rollback()
        print(f"Error seeding database: {e}")


if __name__ == "__main__":
    # This allows running the script manually from the project root
    from app.server.app import create_app
    app = create_app()
    with app.app_context():
        seed_database()
