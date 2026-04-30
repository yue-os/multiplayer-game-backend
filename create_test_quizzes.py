from app.server.app import create_app
from app.server.database import db
from app.server.models.user import User, Class, Quiz, QuizQuestion, QuizResult
from datetime import datetime, timedelta

app = create_app()
with app.app_context():
    print('=== Creating Real Quiz Data ===\n')
    
    # Get Mr.Smith and his class
    teacher = User.query.filter_by(username='Mr.Smith').first()
    test_class = Class.query.filter_by(name='Test Algebra Class', teacher_id=teacher.id).first()
    student = User.query.filter_by(class_id=test_class.id, role='Student').first()
    
    print(f'Teacher: {teacher.username}')
    print(f'Class: {test_class.name}')
    print(f'Student: {student.username}\n')
    
    # Create Quiz 1: Linear Equations
    quiz1_exists = Quiz.query.filter_by(title='Linear Equations - Chapter 2', teacher_id=teacher.id).first()
    if not quiz1_exists:
        quiz1 = Quiz(
            title='Linear Equations - Chapter 2',
            teacher_id=teacher.id,
            class_id=test_class.id,
            timer_seconds=900,
            start_date=datetime.now() - timedelta(days=3)
        )
        db.session.add(quiz1)
        db.session.commit()
        
        # Add questions
        q1 = QuizQuestion(quiz_id=quiz1.id, type='multiple_choice', text='Solve: 2x + 3 = 7',
                         options=['x = 1', 'x = 2', 'x = 3', 'x = 4'],
                         correct_answer='1', points=5, order=0)
        q2 = QuizQuestion(quiz_id=quiz1.id, type='multiple_choice', text='Find x: 3x - 5 = 10',
                         options=['x = 3', 'x = 4', 'x = 5', 'x = 6'],
                         correct_answer='2', points=5, order=1)
        q3 = QuizQuestion(quiz_id=quiz1.id, type='short_answer', text='Write the equation of a line with slope 2 and y-intercept -3',
                         correct_answer='y = 2x - 3', points=10, order=2)
        db.session.add_all([q1, q2, q3])
        db.session.commit()
        print('✓ Quiz 1 Created: Linear Equations - Chapter 2')
    else:
        quiz1 = quiz1_exists
        print('✓ Quiz 1 Already exists: Linear Equations - Chapter 2')
    
    # Create Quiz 2: Quadratic Functions
    quiz2_exists = Quiz.query.filter_by(title='Quadratic Functions - Chapter 3', teacher_id=teacher.id).first()
    if not quiz2_exists:
        quiz2 = Quiz(
            title='Quadratic Functions - Chapter 3',
            teacher_id=teacher.id,
            class_id=test_class.id,
            timer_seconds=1200,
            start_date=datetime.now() - timedelta(days=1)
        )
        db.session.add(quiz2)
        db.session.commit()
        
        q1 = QuizQuestion(quiz_id=quiz2.id, type='multiple_choice', text='What is the vertex form of a quadratic?',
                         options=['y = ax^2 + bx + c', 'y = a(x-h)^2 + k', 'y = mx + b', 'y = |x| + c'],
                         correct_answer='1', points=5, order=0)
        q2 = QuizQuestion(quiz_id=quiz2.id, type='multiple_choice', text='Factor: x^2 + 5x + 6',
                         options=['(x+1)(x+6)', '(x+2)(x+3)', '(x+5)(x+1)', '(x+3)(x+2)'],
                         correct_answer='1', points=5, order=1)
        db.session.add_all([q1, q2])
        db.session.commit()
        print('✓ Quiz 2 Created: Quadratic Functions - Chapter 3')
    else:
        quiz2 = quiz2_exists
        print('✓ Quiz 2 Already exists: Quadratic Functions - Chapter 3')
    
    # Create Quiz 3: Systems of Equations
    quiz3_exists = Quiz.query.filter_by(title='Systems of Equations - Chapter 4', teacher_id=teacher.id).first()
    if not quiz3_exists:
        quiz3 = Quiz(
            title='Systems of Equations - Chapter 4',
            teacher_id=teacher.id,
            class_id=test_class.id,
            timer_seconds=1500,
            start_date=datetime.now()
        )
        db.session.add(quiz3)
        db.session.commit()
        
        q1 = QuizQuestion(quiz_id=quiz3.id, type='multiple_choice', text='Solve the system: x + y = 5, x - y = 1',
                         options=['x=3, y=2', 'x=2, y=3', 'x=4, y=1', 'x=1, y=4'],
                         correct_answer='0', points=10, order=0)
        db.session.add(q1)
        db.session.commit()
        print('✓ Quiz 3 Created: Systems of Equations - Chapter 4')
    else:
        quiz3 = quiz3_exists
        print('✓ Quiz 3 Already exists: Systems of Equations - Chapter 4')
    
    # Create Quiz Results
    print('\n=== Creating Quiz Results ===\n')
    
    # Result 1: Student got 90% on Linear Equations
    result1_exists = QuizResult.query.filter_by(quiz_id=quiz1.id, student_id=student.id).first()
    if not result1_exists:
        result1 = QuizResult(quiz_id=quiz1.id, student_id=student.id, score=90)
        db.session.add(result1)
        db.session.commit()
        print(f'✓ Result 1: {student.username} scored 90% on "{quiz1.title}"')
    else:
        result1 = result1_exists
        print(f'✓ Result 1 Already exists: {student.username} scored {result1.score}% on "{quiz1.title}"')
    
    # Result 2: Student got 75% on Quadratic Functions
    result2_exists = QuizResult.query.filter_by(quiz_id=quiz2.id, student_id=student.id).first()
    if not result2_exists:
        result2 = QuizResult(quiz_id=quiz2.id, student_id=student.id, score=75)
        db.session.add(result2)
        db.session.commit()
        print(f'✓ Result 2: {student.username} scored 75% on "{quiz2.title}"')
    else:
        result2 = result2_exists
        print(f'✓ Result 2 Already exists: {student.username} scored {result2.score}% on "{quiz2.title}"')
    
    # Result 3: Student got 88% on Systems of Equations
    result3_exists = QuizResult.query.filter_by(quiz_id=quiz3.id, student_id=student.id).first()
    if not result3_exists:
        result3 = QuizResult(quiz_id=quiz3.id, student_id=student.id, score=88)
        db.session.add(result3)
        db.session.commit()
        print(f'✓ Result 3: {student.username} scored 88% on "{quiz3.title}"')
    else:
        result3 = result3_exists
        print(f'✓ Result 3 Already exists: {student.username} scored {result3.score}% on "{quiz3.title}"')
    
    print('\n=== Summary ===')
    print(f'Teacher: {teacher.username} ({teacher.public_id})')
    print(f'Class: {test_class.name}')
    print(f'Student: {student.username} ({student.public_id})')
    print(f'\nQuizzes Ready for Testing:')
    print(f'  1. {quiz1.title} - 3 questions')
    print(f'  2. {quiz2.title} - 2 questions')
    print(f'  3. {quiz3.title} - 1 question')
    print(f'\nResults Ready:')
    print(f'  • {result1.score}% on Quiz 1')
    print(f'  • {result2.score}% on Quiz 2')
    print(f'  • {result3.score}% on Quiz 3')
