from flask import Blueprint, request, jsonify
from app.server.database import db
from app.server.models.user import GameServer, MissionProgress, Mission
from app.auth.auth_bearer import token_required
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

    if advertised_ip == "":
        advertised_ip = client_ip

    # Check if server exists
    server = GameServer.query.filter_by(ip=advertised_ip, port=port).first()
    
    if server:
        server.last_heartbeat = time.time()
        server.player_count = count
        server.name = name # Update name if changed
    else:
        server = GameServer(
            ip=advertised_ip,
            port=port, 
            name=name, 
            player_count=count,
            last_heartbeat=time.time()
        )
        db.session.add(server)
    
    db.session.commit()
    return "OK", 200

@app_bp.route('/server/list', methods=['GET'])
def list_servers():
    """
    Returns list of active game servers (heartbeat within last 15s).
    """
    now = time.time()
    cutoff = now - 15 # 15 seconds timeout
    
    # Query DB for active servers
    active_servers = GameServer.query.filter(GameServer.last_heartbeat > cutoff).all()
    
    server_list = []
    for s in active_servers:
        server_list.append({
            "ip": s.ip,
            "port": s.port,
            "name": s.name,
            "count": s.player_count
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
