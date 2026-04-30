from app.server.app import create_app
from app.server.database import db
from app.server.models.user import User, Class, Quiz, QuizQuestion, QuizResult
from datetime import datetime, timedelta

app = create_app()
with app.app_context():
    print('=== Creating Quizzes for teach user ===\n')
    
    # Get teach teacher and their class
    teacher = User.query.filter_by(username='teach').first()
    teach_class = Class.query.filter_by(teacher_id=teacher.id).first()
    students = User.query.filter_by(class_id=teach_class.id, role='Student').all()
    
    print(f'Teacher: {teacher.username}')
    print(f'Class: {teach_class.name}')
    print(f'Students: {len(students)}')
    for s in students:
        print(f'  - {s.username} (ID: {s.id})')
    print()
    
    # Create Quiz 1: Science - Living Things
    quiz1_exists = Quiz.query.filter_by(title='Science - Living Things', teacher_id=teacher.id).first()
    if not quiz1_exists:
        quiz1 = Quiz(
            title='Science - Living Things',
            teacher_id=teacher.id,
            class_id=teach_class.id,
            timer_seconds=1200,
            start_date=datetime.now() - timedelta(days=5)
        )
        db.session.add(quiz1)
        db.session.commit()
        
        q1 = QuizQuestion(quiz_id=quiz1.id, type='multiple_choice', text='Which of the following is a producer?',
                         options=['Lion', 'Plant', 'Deer', 'Eagle'],
                         correct_answer='1', points=5, order=0)
        q2 = QuizQuestion(quiz_id=quiz1.id, type='multiple_choice', text='What do plants need for photosynthesis?',
                         options=['Oxygen and water', 'Sunlight, water, and CO2', 'Soil and nutrients', 'Nitrogen only'],
                         correct_answer='1', points=5, order=1)
        q3 = QuizQuestion(quiz_id=quiz1.id, type='short_answer', text='Name two types of cells',
                         correct_answer='prokaryotic and eukaryotic', points=10, order=2)
        db.session.add_all([q1, q2, q3])
        db.session.commit()
        print('✓ Quiz 1 Created: Science - Living Things')
    else:
        quiz1 = quiz1_exists
        print('✓ Quiz 1 Already exists: Science - Living Things')
    
    # Create Quiz 2: English - Grammar
    quiz2_exists = Quiz.query.filter_by(title='English - Grammar Basics', teacher_id=teacher.id).first()
    if not quiz2_exists:
        quiz2 = Quiz(
            title='English - Grammar Basics',
            teacher_id=teacher.id,
            class_id=teach_class.id,
            timer_seconds=900,
            start_date=datetime.now() - timedelta(days=2)
        )
        db.session.add(quiz2)
        db.session.commit()
        
        q1 = QuizQuestion(quiz_id=quiz2.id, type='multiple_choice', text='What is the verb in "She runs quickly"?',
                         options=['She', 'runs', 'quickly', 'in'],
                         correct_answer='1', points=5, order=0)
        q2 = QuizQuestion(quiz_id=quiz2.id, type='multiple_choice', text='Identify the noun in "The blue sky"',
                         options=['The', 'blue', 'sky', 'The blue'],
                         correct_answer='2', points=5, order=1)
        db.session.add_all([q1, q2])
        db.session.commit()
        print('✓ Quiz 2 Created: English - Grammar Basics')
    else:
        quiz2 = quiz2_exists
        print('✓ Quiz 2 Already exists: English - Grammar Basics')
    
    # Create Quiz 3: Math - Fractions
    quiz3_exists = Quiz.query.filter_by(title='Math - Fractions', teacher_id=teacher.id).first()
    if not quiz3_exists:
        quiz3 = Quiz(
            title='Math - Fractions',
            teacher_id=teacher.id,
            class_id=teach_class.id,
            timer_seconds=1500,
            start_date=datetime.now() - timedelta(days=1)
        )
        db.session.add(quiz3)
        db.session.commit()
        
        q1 = QuizQuestion(quiz_id=quiz3.id, type='multiple_choice', text='What is 1/2 + 1/4?',
                         options=['1/6', '2/4', '3/4', '1/8'],
                         correct_answer='2', points=10, order=0)
        db.session.add(q1)
        db.session.commit()
        print('✓ Quiz 3 Created: Math - Fractions')
    else:
        quiz3 = quiz3_exists
        print('✓ Quiz 3 Already exists: Math - Fractions')
    
    # Create Quiz Results for each student
    print('\n=== Creating Quiz Results ===\n')
    
    for student in students:
        print(f'Creating results for {student.username}:')
        
        # Result 1: Science
        result1_exists = QuizResult.query.filter_by(quiz_id=quiz1.id, student_id=student.id).first()
        if not result1_exists:
            result1 = QuizResult(quiz_id=quiz1.id, student_id=student.id, score=92 if student.username == 'Timmy' else 78)
            db.session.add(result1)
            db.session.commit()
            print(f'  ✓ Science - {result1.score}%')
        else:
            print(f'  ✓ Science - {result1_exists.score}% (already exists)')
        
        # Result 2: English
        result2_exists = QuizResult.query.filter_by(quiz_id=quiz2.id, student_id=student.id).first()
        if not result2_exists:
            result2 = QuizResult(quiz_id=quiz2.id, student_id=student.id, score=85 if student.username == 'Timmy' else 88)
            db.session.add(result2)
            db.session.commit()
            print(f'  ✓ English - {result2.score}%')
        else:
            print(f'  ✓ English - {result2_exists.score}% (already exists)')
        
        # Result 3: Math
        result3_exists = QuizResult.query.filter_by(quiz_id=quiz3.id, student_id=student.id).first()
        if not result3_exists:
            result3 = QuizResult(quiz_id=quiz3.id, student_id=student.id, score=95 if student.username == 'Timmy' else 72)
            db.session.add(result3)
            db.session.commit()
            print(f'  ✓ Math - {result3.score}%')
        else:
            print(f'  ✓ Math - {result3_exists.score}% (already exists)')
    
    print('\n=== Summary ===')
    print(f'Teacher: {teacher.username}')
    print(f'Class: {teach_class.name}')
    print(f'\nQuizzes Created:')
    print(f'  1. {quiz1.title}')
    print(f'  2. {quiz2.title}')
    print(f'  3. {quiz3.title}')
    print(f'\nTotal Results: {len(students)} students × 3 quizzes = {len(students) * 3} results')
