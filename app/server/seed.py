from app.server.database import db
from app.server.models.user import User, Class, Mission, MissionProgress, PlaytimeLog
from werkzeug.security import generate_password_hash
from datetime import date

def seed_database():
    """
    Populates the database with initial sample data if no users exist.
    """
    # 1. Check if data exists to prevent duplicates
    if User.query.first():
        print("Database already contains data. Skipping seed.")
        return

    print("Seeding database with sample data...")

    # 2. Create Users (Admin, Teacher, Parent, Student)
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

    # 3. Create Relationships & Classes
    # Create a Class managed by Mr.Smith
    math_class = Class(name="Algebra 101", teacher_id=teacher.id)
    db.session.add(math_class)
    db.session.commit()

    # Link Timmy to ParentJane and Math Class
    student.parent_id = parent.id
    student.class_id = math_class.id
    db.session.add(student)

    # 4. Create Game Data (Missions)
    m1 = Mission(title="Tutorial: Movement", level_req=1)
    m2 = Mission(title="Chapter 1: The Forest", level_req=2)
    m3 = Mission(title="Chapter 2: The Cave", level_req=5)
    db.session.add_all([m1, m2, m3])
    db.session.commit()

    # 5. Create Sample Progress & Logs
    # Timmy completed the Tutorial
    prog1 = MissionProgress(
        user_id=student.id, 
        mission_id=m1.id, 
        status='completed', 
        score=100
    )
    
    # Timmy played for 45 minutes today
    log1 = PlaytimeLog(
        user_id=student.id, 
        date=date.today(), 
        duration_minutes=45
    )

    db.session.add_all([prog1, log1])
    db.session.commit()

    print("Database seeded successfully!")
    print("Default Users Created:")
    print(" - Admin: admin / admin123")
    print(" - Teacher: Mr.Smith / teach123")
    print(" - Parent: ParentJane / parent123")
    print(" - Student: Timmy / timmy123")
