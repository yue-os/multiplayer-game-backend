from flask import Blueprint, request, jsonify
from app.server.database import db
from app.server.models.user import GameServer, MissionProgress, Mission
from app.auth.auth_bearer import token_required
from sqlalchemy import or_
import time
from datetime import datetime

app_bp = Blueprint('app_routes', __name__)

# --- Game Server Registry ---

@app_bp.route('/server/register', methods=['POST'])
def register_server():
    """
    Called by Godot Server to register itself.
    No JWT auth required for servers typically, or use a shared API key.
    """
    data = request.json
    client_ip = request.remote_addr
    advertised_ip = str(data.get("ip", client_ip)).strip()
    port = data.get("port")
    name = data.get("name", "Unknown Server")
    count = data.get("count", 0)
    required_players = data.get("required_players", 2)

    try:
        count = max(0, int(count))
    except (TypeError, ValueError):
        count = 0

    try:
        required_players = max(1, int(required_players))
    except (TypeError, ValueError):
        required_players = 2

    if advertised_ip == "":
        advertised_ip = client_ip

    # Check if server exists
    server = GameServer.query.filter_by(ip=advertised_ip, port=port).first()
    
    if server:
        server.last_heartbeat = time.time()
        server.player_count = count
        server.required_players = required_players
        server.name = name # Update name if changed
    else:
        server = GameServer(
            ip=advertised_ip,
            port=port, 
            name=name, 
            player_count=count,
            required_players=required_players,
            last_heartbeat=time.time()
        )
        db.session.add(server)
    
    db.session.commit()
    return "OK", 200

@app_bp.route('/server/list', methods=['GET'])
def list_servers():
    """
    Returns list of active game servers (heartbeat within last 15s)
    plus persistent teacher-created lobbies.
    """
    now = time.time()
    cutoff = now - 15 # 15 seconds timeout
    
    # Query DB for active heartbeat servers OR persistent lobbies.
    active_servers = GameServer.query.filter(
        or_(GameServer.last_heartbeat > cutoff, GameServer.persistent.is_(True))
    ).all()
    
    server_list = []
    for s in active_servers:
        is_teacher_lobby = bool(s.persistent and s.owner_teacher_id is not None)
        is_recently_active = bool(s.last_heartbeat and s.last_heartbeat > cutoff)
        is_online = is_recently_active or is_teacher_lobby

        # Teacher lobbies are always listed online; if no active heartbeat yet,
        # treat current players as 0 and keep room in "Not yet started" state.
        current_players = int(s.player_count or 0) if is_recently_active else 0
        required_players = max(1, int(s.required_players or 2))
        if is_teacher_lobby and required_players < 2:
            required_players = 2

        # Started means the room is actively running and has reached a playable threshold.
        is_started = is_recently_active and current_players >= required_players

        if is_started:
            status = 'Started'
        elif is_online:
            status = 'Not yet started'
        else:
            status = 'Offline'

        server_list.append({
            "ip": s.ip,
            "port": s.port,
            "name": s.name,
            "count": current_players,
            "persistent": bool(s.persistent),
            "online": is_online,
            "joinable": is_online,
            "current_players": current_players,
            "required_players": required_players,
            "started": is_started,
            "status": status
        })
        
    return jsonify(server_list), 200

# --- Gameplay Progress ---

@app_bp.route('/mission/update', methods=['POST'])
@token_required
def update_mission():
    user_id = request.current_user_id
    data = request.json
    mission_public_id = (data.get('mission_public_id') or '').strip()
    score = data.get('score')
    status = data.get('status', 'completed')

    if not mission_public_id:
        return jsonify({'error': 'mission_public_id is required'}), 400

    mission = Mission.query.filter_by(public_id=mission_public_id).first()
    if not mission:
        return jsonify({'error': 'Invalid mission public ID'}), 400
    
    # Check if mission exists (optional validation)
    mission_id = mission.id

    progress = MissionProgress.query.filter_by(user_id=user_id, mission_id=mission_id).first()
    
    if progress:
        progress.score = max(progress.score, score) # Keep high score
        progress.status = status
    else:
        progress = MissionProgress(
            user_id=user_id,
            mission_id=mission_id,
            score=score,
            status=status
        )
        db.session.add(progress)
        
    db.session.commit()
    return jsonify({'message': 'Progress saved'}), 200
