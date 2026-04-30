#!/usr/bin/env python3
"""
Debug parent stats endpoint
"""
from app.server.app import create_app
from app.server.models.user import User, MissionProgress, QuizResult, PlaytimeLog
from app.auth.auth_handler import signJWT

app = create_app()

with app.app_context():
    parent = User.query.filter_by(username='ParentJane').first()
    children = User.query.filter_by(parent_id=parent.id, role='Student').all()
    
    print(f'Manually building stats_list...\n')
    
    stats_list = []
    for child in children:
        # Get playtime logs
        playtime_logs = PlaytimeLog.query.filter_by(user_id=child.id).order_by(PlaytimeLog.date.desc()).all()
        playtime_data = [
            {
                'date': str(log.date),
                'minutes': log.duration_minutes,
                'id': log.id,
                'public_id': log.public_id
            }
            for log in playtime_logs
        ]
        
        # Get mission progress
        mission_progress = MissionProgress.query.filter_by(user_id=child.id).all()
        mission_data = [
            {
                'mission_id': mp.mission_id,
                'status': mp.status,
                'score': mp.score,
                'updated_at': mp.updated_at.isoformat() if mp.updated_at else None,
                'public_id': mp.public_id
            }
            for mp in mission_progress
        ]
        
        # Get quiz results
        quiz_results = QuizResult.query.filter_by(student_id=child.id).all()
        quiz_data = [
            {
                'quiz_id': qr.quiz_id,
                'score': qr.score,
                'updated_at': qr.created_at.isoformat() if qr.created_at else None,
                'public_id': qr.public_id
            }
            for qr in quiz_results
        ]
        
        print(f'{child.username}:')
        print(f'  Missions: {mission_data}')
        print(f'  Quizzes: {quiz_data}')
        print()
        
        # Calculate average scores
        mission_avg = float(sum(m['score'] for m in mission_data) / len(mission_data)) if mission_data else 0.0
        quiz_avg = float(sum(q['score'] for q in quiz_data) / len(quiz_data)) if quiz_data else 0.0
        total_playtime = sum(log.duration_minutes or 0 for log in playtime_logs)
        
        stats_list.append({
            'child': child.username,
            'child_id': child.id,
            'child_public_id': child.public_id,
            'first_name': child.first_name,
            'last_name': child.last_name,
            'class_id': child.class_id,
            'playtime_logs': playtime_data,
            'missions': mission_data,
            'scores': quiz_data,
            'mission_avg_score': mission_avg,
            'quiz_avg_score': quiz_avg,
            'total_playtime_minutes': total_playtime,
        })
    
    print('\nFinal stats_list:')
    import json
    print(json.dumps(stats_list, indent=2))
