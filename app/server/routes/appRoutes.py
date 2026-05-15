from flask import Blueprint, request, jsonify, Response
from app.server.database import db
from app.server.models.user import GameServer, MissionProgress, Mission
from app.auth.auth_bearer import token_required
from sqlalchemy import or_
import time
from datetime import datetime
from app.server.routes.game_sockets import socket_hub
from pathlib import Path
import json
import os
import re
import threading
import traceback
from urllib.parse import urlencode
from urllib.request import urlopen

app_bp = Blueprint('app_routes', __name__)

_LATEST_GAME_VERSION_FILE = Path(__file__).resolve().parents[1] / "data" / "latest_game_version.txt"
_ITCH_LATEST_API = "https://itch.io/api/1/x/wharf/latest"
_ITCH_SYNC_TARGET = os.getenv("ITCH_SYNC_TARGET", "grahambel/batangaware").strip()
_ITCH_SYNC_CHANNEL = os.getenv("ITCH_SYNC_CHANNEL", "android").strip()
_ITCH_SYNC_INTERVAL_SECONDS = max(10, int(os.getenv("ITCH_SYNC_INTERVAL_SECONDS", "300")))

_version_sync_lock = threading.Lock()
_last_sync_attempt_at = 0.0


def _read_latest_game_version() -> str:
    if not _LATEST_GAME_VERSION_FILE.exists():
        return ""
    return _LATEST_GAME_VERSION_FILE.read_text(encoding="utf-8").strip()


def _write_latest_game_version(version_text: str) -> None:
    _LATEST_GAME_VERSION_FILE.parent.mkdir(parents=True, exist_ok=True)
    _LATEST_GAME_VERSION_FILE.write_text(version_text.strip() + "\n", encoding="utf-8")
    try:
        print(f"[latest_game_version] wrote: {version_text.strip()}")
    except Exception:
        pass


def _normalize_version_text(raw: str) -> str:
    text = str(raw or "").strip()
    if text == "":
        return ""
    # Normalize labels like "Version 4" to "4"
    text = re.sub(r"^version\s+", "", text, flags=re.IGNORECASE).strip()
    number_match = re.search(r"\d+(?:\.\d+)*", text)
    if number_match:
        return number_match.group(0).strip()
    return text


def _target_to_store_page_url(target: str) -> str:
    t = str(target or "").strip()
    if t == "":
        return ""
    # If it's already a full URL, return as-is
    if t.startswith("http://") or t.startswith("https://"):
        return t

    parts = t.split("/", 1)
    if len(parts) != 2:
        return ""
    user = parts[0].strip()
    game = parts[1].strip()
    if user == "" or game == "":
        return ""
    return f"https://{user}.itch.io/{game}"


def _fetch_itch_latest_version() -> str:
    if _ITCH_SYNC_TARGET == "" or _ITCH_SYNC_CHANNEL == "":
        return ""

    query = urlencode({
        "target": _ITCH_SYNC_TARGET,
        "channel_name": _ITCH_SYNC_CHANNEL,
    })
    api_url = f"{_ITCH_LATEST_API}?{query}"

    try:
        with urlopen(api_url, timeout=8) as response:
            body = response.read().decode("utf-8", errors="replace")
            try:
                payload = json.loads(body)
                try:
                    print(f"[latest_game_version] api payload: {payload}")
                except Exception:
                    pass
            except Exception:
                payload = None

        latest = _normalize_version_text(payload.get("latest", "")) if isinstance(payload, dict) else ""
        try:
            print(f"[latest_game_version] api latest: {latest}")
        except Exception:
            pass
        if latest != "":
            return latest
    except Exception:
        try:
            print(f"[latest_game_version] api fetch error:\n{traceback.format_exc()}")
        except Exception:
            pass

    # Fallback: scrape public store page label like <span class="version_name">Version 4</span>
    page_url = _target_to_store_page_url(_ITCH_SYNC_TARGET)
    if page_url == "":
        return ""

    try:
        with urlopen(page_url, timeout=8) as response:
            html = response.read().decode("utf-8", errors="replace")

        # Common itch page pattern
        match = re.search(r'<span\s+class="version_name"[^>]*>(.*?)</span>', html, flags=re.IGNORECASE | re.DOTALL)
        if match:
            return _normalize_version_text(match.group(1))

        # Fallback: search for plain "Version 4" or similar anywhere in the page
        match2 = re.search(r'Version\s*[:#-]?\s*(\d+(?:\.\d+)*)', html, flags=re.IGNORECASE)
        if match2:
            return _normalize_version_text(match2.group(1))

        # Last-resort: look for any numeric-looking label near the word "version"
        match3 = re.search(r'([Vv]ersion)\D{0,20}(\d+(?:\.\d+)*)', html, flags=re.IGNORECASE)
        if match3:
            return _normalize_version_text(match3.group(2))

    except Exception:
        try:
            print(f"[latest_game_version] page fetch error:\n{traceback.format_exc()}")
        except Exception:
            pass
        return ""

    return ""


def _background_sync_loop():
    """Background thread loop to periodically sync the latest version from itch."""
    try:
        while True:
            try:
                latest = _auto_sync_latest_game_version(force=True)
                try:
                    print(f"[latest_game_version] background sync attempt: {latest}")
                except Exception:
                    pass
            except Exception:
                try:
                    print("[latest_game_version] background sync encountered an error")
                except Exception:
                    pass
            time.sleep(max(5, _ITCH_SYNC_INTERVAL_SECONDS))
    except Exception:
        return


def _start_background_sync():
    try:
        thread = threading.Thread(target=_background_sync_loop, daemon=True, name="itch-version-sync")
        thread.start()
    except Exception:
        pass


# Start background sync on module import so the file stays up-to-date.
try:
    _start_background_sync()
except Exception:
    pass


try:
    try:
        print(f"[latest_game_version] initial: {_read_latest_game_version()}")
    except Exception:
        pass
except Exception:
    pass


def _auto_sync_latest_game_version(force: bool = False) -> str:
    global _last_sync_attempt_at

    now = time.time()
    if not force and (now - _last_sync_attempt_at) < _ITCH_SYNC_INTERVAL_SECONDS:
        return _read_latest_game_version()

    with _version_sync_lock:
        now = time.time()
        if not force and (now - _last_sync_attempt_at) < _ITCH_SYNC_INTERVAL_SECONDS:
            return _read_latest_game_version()

        _last_sync_attempt_at = now
        fetched = _fetch_itch_latest_version()
        if fetched != "":
            try:
                _write_latest_game_version(fetched)
            except Exception:
                try:
                    print(f"[latest_game_version] write error:\n{traceback.format_exc()}")
                except Exception:
                    pass
                # Return current on-disk value if write fails
                return _read_latest_game_version()

            try:
                print(f"[latest_game_version] synced from itch: {fetched}")
            except Exception:
                pass
            return fetched

        return _read_latest_game_version()

# --- Game Server Registry ---

@app_bp.route('/client/latest-version.txt', methods=['GET'])
def latest_version_text():
    """Public plain-text version feed for the game client update checker."""
    latest = _auto_sync_latest_game_version()
    try:
        print(f"[latest_game_version] served: {latest}")
    except Exception:
        pass
    return Response(latest + "\n", mimetype='text/plain')


@app_bp.route('/client/latest-version', methods=['GET'])
def latest_version_json():
    """Public JSON version feed for debugging/tooling."""
    latest = _auto_sync_latest_game_version()
    return jsonify({"latest": latest}), 200


@app_bp.route('/client/latest-version', methods=['POST'])
@token_required
def update_latest_version_json():
    """Admin-only endpoint to update the latest game version text file."""
    role = str(getattr(request, 'current_user_role', '')).strip().lower()
    if role != 'admin':
        return jsonify({'error': 'Admin role required'}), 403

    data = request.json or {}
    latest = str(data.get('latest', '')).strip()
    if latest == '':
        return jsonify({'error': 'latest is required'}), 400

    _write_latest_game_version(latest)
    return jsonify({'message': 'Latest version updated', 'latest': latest}), 200


@app_bp.route('/client/latest-version/sync', methods=['POST'])
@token_required
def sync_latest_version_from_itch():
    """Admin-only endpoint to force refresh from itch and persist to latest_game_version.txt."""
    role = str(getattr(request, 'current_user_role', '')).strip().lower()
    if role != 'admin':
        return jsonify({'error': 'Admin role required'}), 403

    latest = _auto_sync_latest_game_version(force=True)
    if latest == '':
        return jsonify({'error': 'Unable to sync version from itch target/channel'}), 502

    return jsonify({
        'message': 'Synced latest version from itch',
        'latest': latest,
        'target': _ITCH_SYNC_TARGET,
        'channel': _ITCH_SYNC_CHANNEL,
    }), 200


@app_bp.route('/client/latest-version/debug-api', methods=['GET'])
def debug_fetch_api():
    """Debug: fetch the itch API directly and return raw body for troubleshooting."""
    query = urlencode({
        "target": _ITCH_SYNC_TARGET,
        "channel_name": _ITCH_SYNC_CHANNEL,
    })
    api_url = f"{_ITCH_LATEST_API}?{query}"
    try:
        with urlopen(api_url, timeout=10) as response:
            body = response.read().decode('utf-8', errors='replace')
        return Response(body, mimetype='application/json')
    except Exception as e:
        try:
            return jsonify({'error': 'fetch failed', 'detail': str(e), 'trace': traceback.format_exc()}), 502
        except Exception:
            return jsonify({'error': 'fetch failed'}), 502


@app_bp.route('/client/latest-version/debug-sync', methods=['GET'])
def debug_force_sync():
    """Debug: force a sync and return what was written and read from disk."""
    latest = _auto_sync_latest_game_version(force=True)
    on_disk = _read_latest_game_version()
    return jsonify({'latest': latest, 'on_disk': on_disk, 'target': _ITCH_SYNC_TARGET, 'channel': _ITCH_SYNC_CHANNEL}), 200

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
            "teacher_lobby": is_teacher_lobby,
            "online": is_online,
            "joinable": is_online,
            "current_players": current_players,
            "required_players": required_players,
            "started": is_started,
            "status": status
        })

    # Also include any in-memory websocket relay lobbies from socket_hub.
    try:
        for lobby_id, runtime in socket_hub._lobbies.items():
            connections = socket_hub._connections.get(lobby_id, {})
            current_players = len(connections)
            server_list.append({
                "ip": "",
                "port": 0,
                "name": f"{lobby_id}",
                "count": current_players,
                "persistent": False,
                "teacher_lobby": False,
                "online": True,
                "joinable": True,
                "current_players": current_players,
                "required_players": 2,
                "started": False,
                "status": "Relay",
                "websocket": True,
                "lobby_id": lobby_id,
            })
    except Exception:
        pass

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
