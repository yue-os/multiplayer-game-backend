"""Generate synthetic WebSocket clients and optional dashboard traffic.

Use only against a local or dedicated test deployment. Optional teacher lobbies
are created through the teacher API and their database records are deleted when
the run ends. Relay state uses unique IDs and expires from Redis by its TTL.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import random
import secrets
import statistics
import sys
import time
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from urllib.parse import urlencode

import aiohttp
import websockets
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from app.auth.auth_handler import signJWT  # noqa: E402

LATENCY_SAMPLE_LIMIT = 100_000

DASHBOARD_REQUESTS = {
    "admin": {
        "initial": [
            "/api/admin/dashboard/analytics",
            "/api/admin/users",
            "/api/admin/classes",
            "/api/admin/password-reset-requests",
        ],
        "poll": "/api/admin/dashboard/analytics",
        "interval": 10.0,
    },
    "teacher": {
        "initial": [
            "/user/profile",
            "/teacher/class/overview",
            "/teacher/quizzes",
            "/teacher/announcements",
        ],
        # Models a teacher with the messages tab open (the UI polls every 5s).
        "poll": "/teacher/messages",
        "interval": 5.0,
    },
    "parent": {
        "initial": ["/user/profile", "/parent/stats", "/api/messages"],
        "poll": "/api/messages",
        "interval": 5.0,
    },
}


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Load test the game's WebSocket relay, teacher lobby creation, and dashboard APIs.",
        epilog=(
            "Example: python scripts/load_test.py --users 100 --duration 60 "
            "--ramp-per-second 10 --start-games --admin-users 5 "
            "--teacher-users 10 --parent-users 20\n"
            "Parent-only staging test: python scripts/load_test.py --users 0 --parent-users 100 "
            "--parent-token-file .\\loadtest-parent-tokens.txt --duration 120\n"
            "Teacher lobbies: python scripts/load_test.py --users 800 --teacher-lobbies 100 "
            "--duration 120 --ramp-per-second 10"
        ),
    )
    parser.add_argument("--base-ws-url", default="ws://127.0.0.1:8000", help="WebSocket service base URL")
    parser.add_argument("--users", type=int, default=100, help="Number of synthetic game clients (0-10000; use 0 for dashboard-only tests)")
    parser.add_argument("--duration", type=int, default=60, help="Steady-state duration in seconds")
    parser.add_argument("--ramp-per-second", type=float, default=10.0, help="Connection attempts per second")
    parser.add_argument("--start-games", action="store_true", help="Start each lobby after all clients connect, exercising one-second state broadcasts")
    parser.add_argument("--http-users", type=int, default=0, help="Additional clients polling the HTTP endpoint (0 disables HTTP traffic)")
    parser.add_argument("--http-path", default="/health", help="GET path used by HTTP poll clients")
    parser.add_argument("--http-interval", type=float, default=5.0, help="Seconds between HTTP polls per client")
    parser.add_argument("--http-token", default="", help="Optional bearer token for the HTTP endpoint")
    parser.add_argument("--admin-users", type=int, default=0, help="Synthetic read-only admin dashboard clients")
    parser.add_argument("--teacher-users", type=int, default=0, help="Synthetic read-only teacher dashboard clients")
    parser.add_argument("--parent-users", type=int, default=0, help="Synthetic read-only parent dashboard clients")
    parser.add_argument("--teacher-lobbies", type=int, default=0, help="Create this many teacher-owned lobbies; --users must be 8 per lobby")
    parser.add_argument("--teacher-class-public-id", default="", help="Class owned by the teacher token (defaults to the teacher's first class)")
    parser.add_argument("--lobby-create-ramp-per-second", type=float, default=10.0, help="Teacher lobby API creation attempts per second")
    parser.add_argument("--dashboard-ramp-per-second", type=float, default=10.0, help="Dashboard clients started per second")
    parser.add_argument("--admin-token", default=os.getenv("LOADTEST_ADMIN_TOKEN", ""), help="Admin test JWT (or set LOADTEST_ADMIN_TOKEN)")
    parser.add_argument("--teacher-token", default=os.getenv("LOADTEST_TEACHER_TOKEN", ""), help="Teacher test JWT (or set LOADTEST_TEACHER_TOKEN)")
    parser.add_argument("--parent-token", default=os.getenv("LOADTEST_PARENT_TOKEN", ""), help="Single parent test JWT fallback (or set LOADTEST_PARENT_TOKEN)")
    parser.add_argument("--parent-token-file", default="", help="Local text file with one parent test JWT per line; clients rotate through these accounts")
    parser.add_argument("--parent-poll-interval", type=float, default=5.0, help="Parent inbox polling interval in seconds (default matches the dashboard)")
    parser.add_argument("--allow-remote", action="store_true", help="Allow a dedicated staging/test host (never live production)")
    args = parser.parse_args(argv)

    user_counts = [args.users, args.http_users, args.admin_users, args.teacher_users, args.parent_users]
    if any(count < 0 or count > 10_000 for count in user_counts):
        parser.error("user counts must be between 0 and 10000")
    if sum(user_counts) < 1:
        parser.error("configure at least one game, dashboard, or HTTP client")
    if args.duration < 1 or args.duration > 3600:
        parser.error("--duration must be between 1 and 3600 seconds")
    if args.ramp_per_second <= 0 or args.dashboard_ramp_per_second <= 0 or args.http_interval <= 0 or args.parent_poll_interval <= 0:
        parser.error("ramp rates and polling intervals must be greater than zero")
    if args.lobby_create_ramp_per_second <= 0:
        parser.error("--lobby-create-ramp-per-second must be greater than zero")
    if not 0 <= args.teacher_lobbies <= 1000:
        parser.error("--teacher-lobbies must be between 0 and 1000")
    if args.teacher_lobbies:
        if args.users != args.teacher_lobbies * 8:
            parser.error("--users must equal --teacher-lobbies times 8 so every teacher lobby is full")
        if not args.teacher_token.strip():
            parser.error("--teacher-lobbies requires a real teacher JWT via LOADTEST_TEACHER_TOKEN or --teacher-token")
    if not args.http_path.startswith("/") or args.http_path.startswith("//"):
        parser.error("--http-path must be a path beginning with one slash")
    return args


def _load_parent_tokens(args: argparse.Namespace) -> list[str]:
    raw_tokens: list[str] = []
    if args.parent_token_file:
        token_path = Path(args.parent_token_file).expanduser()
        try:
            raw_tokens = token_path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            raise ValueError(f"Could not read parent token file {token_path}: {exc}") from None
    elif args.parent_token:
        raw_tokens = [args.parent_token]

    tokens = [
        token.strip().removeprefix("Bearer ").strip()
        for token in raw_tokens
        if token.strip() and not token.lstrip().startswith("#")
    ]
    return list(dict.fromkeys(token for token in tokens if token))


def _parent_response_error(path: str, payload: object) -> str | None:
    if path == "/user/profile":
        if not isinstance(payload, dict) or payload.get("role") != "Parent":
            return "expected a parent profile object"
    elif path == "/parent/stats":
        if not isinstance(payload, list) or any(not isinstance(child, dict) for child in payload):
            return "expected a list of child statistics"
        if any(not {"child", "playtime_logs", "missions", "scores"}.issubset(child) for child in payload):
            return "child statistics are missing expected fields"
    elif path == "/api/messages":
        if not isinstance(payload, list) or any(not isinstance(message, dict) for message in payload):
            return "expected a list of message objects"
        if any(not {"public_id", "sender_role", "receiver_role", "content"}.issubset(message) for message in payload):
            return "message objects are missing expected fields"
    return None


def _http_url(ws_url: str, path: str) -> str:
    base = ws_url.rstrip("/")
    if base.startswith("wss://"):
        base = "https://" + base[6:]
    elif base.startswith("ws://"):
        base = "http://" + base[5:]
    else:
        raise ValueError("--base-ws-url must begin with ws:// or wss://")
    return base + path


def _require_local_target(ws_url: str, allow_remote: bool) -> None:
    from urllib.parse import urlparse

    hostname = urlparse(ws_url).hostname
    if not hostname:
        raise ValueError("Could not read a hostname from --base-ws-url")
    try:
        is_local = ipaddress_is_loopback(hostname)
    except OSError:
        is_local = hostname.lower() == "localhost"
    if not is_local and not allow_remote:
        raise ValueError(
            f"Target {hostname!r} is not local. Use a dedicated test server, "
            "then pass --allow-remote to confirm you intend to load it."
        )


def ipaddress_is_loopback(hostname: str) -> bool:
    import ipaddress

    return ipaddress.ip_address(hostname).is_loopback


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, math.ceil(fraction * len(ordered)) - 1)
    return ordered[max(0, index)]


def _reservoir_add(samples: list[float], seen_count: int, value: float) -> None:
    if len(samples) < LATENCY_SAMPLE_LIMIT:
        samples.append(value)
        return
    slot = random.randrange(seen_count)
    if slot < LATENCY_SAMPLE_LIMIT:
        samples[slot] = value


async def run_load_test(args: argparse.Namespace) -> int:
    _require_local_target(args.base_ws_url, args.allow_remote)
    parent_tokens = _load_parent_tokens(args)
    if args.parent_users and not parent_tokens:
        raise ValueError(
            "Parent dashboard clients need a JWT. Set LOADTEST_PARENT_TOKEN, "
            "pass --parent-token, or provide --parent-token-file."
        )
    dashboard_counts = {
        "admin": args.admin_users,
        "teacher": args.teacher_users,
        "parent": args.parent_users,
    }
    dashboard_tokens = {
        "admin": args.admin_token.removeprefix("Bearer ").strip(),
        "teacher": args.teacher_token.removeprefix("Bearer ").strip(),
        "parent": parent_tokens[0] if parent_tokens else "",
    }
    for role, count in dashboard_counts.items():
        if (count or (role == "teacher" and args.teacher_lobbies)) and not dashboard_tokens[role]:
            raise ValueError(
                f"{role.title()} dashboard clients/lobby creation need a real {role} JWT. "
                f"Set LOADTEST_{role.upper()}_TOKEN in the environment or pass --{role}-token."
            )
    dashboard_total = sum(dashboard_counts.values())
    base_ws_url = args.base_ws_url.rstrip("/")

    # Check one protected read endpoint per requested role before creating load.
    # This avoids spending a full run sending requests with missing/expired tokens.
    preflight_paths = {
        "admin": "/api/admin/dashboard/analytics",
        "teacher": "/teacher/class/overview",
        "parent": "/parent/stats",
    }
    teacher_classes: list[dict] = []
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as preflight_session:
        for role, count in dashboard_counts.items():
            if not count and not (role == "teacher" and args.teacher_lobbies):
                continue
            tokens_to_check = (
                parent_tokens[: min(count, len(parent_tokens))]
                if role == "parent"
                else [dashboard_tokens[role]]
            )
            for token_index, token in enumerate(tokens_to_check, start=1):
                async with preflight_session.get(
                    _http_url(base_ws_url, preflight_paths[role]),
                    headers={"Authorization": f"Bearer {token}"},
                ) as response:
                    try:
                        payload = await response.json(content_type=None)
                    except (aiohttp.ContentTypeError, json.JSONDecodeError):
                        payload = {}
                    if response.status >= 400:
                        token_label = (
                            f" token {token_index}"
                            if role == "parent" and len(tokens_to_check) > 1
                            else " token"
                        )
                        if response.status >= 500:
                            raise ValueError(
                                f"{role.title()}{token_label} preflight reached {preflight_paths[role]} "
                                f"but the backend returned HTTP {response.status}. Check the backend "
                                "exception logs; this response does not indicate a JWT role or expiry failure."
                            )
                        raise ValueError(
                            f"{role.title()}{token_label} preflight failed: HTTP {response.status} "
                            f"from {preflight_paths[role]}. Check the token's role, expiry, "
                            "and that it matches the backend JWT_SECRET."
                        )
                    if role == "teacher" and isinstance(payload, dict):
                        teacher_classes = payload.get("classes", [])

    teacher_class_public_id = args.teacher_class_public_id.strip()
    if args.teacher_lobbies:
        if not teacher_classes:
            raise ValueError("Teacher lobby creation needs a class owned by this teacher account")
        owned_class_ids = [
            str(classroom.get("public_id", ""))
            for classroom in teacher_classes
            if isinstance(classroom, dict) and classroom.get("public_id")
        ]
        if teacher_class_public_id:
            if teacher_class_public_id not in owned_class_ids:
                raise ValueError("--teacher-class-public-id is not owned by the supplied teacher token")
        else:
            teacher_class_public_id = owned_class_ids[0] if owned_class_ids else ""
            if not teacher_class_public_id:
                raise ValueError("Teacher class overview returned no class public IDs")

    run_id = f"{int(time.time())}-{secrets.token_hex(3)}"
    lobby_prefix = f"loadtest-{run_id}"
    clients_per_lobby = 8
    lobby_ids = [] if args.teacher_lobbies else [
        f"{lobby_prefix}-lobby-{index + 1:03d}"
        for index in range(math.ceil(args.users / clients_per_lobby))
    ]
    stop = asyncio.Event()
    all_attempts_finished = asyncio.Event()
    start_games = asyncio.Event()
    dashboards_ready = asyncio.Event()
    if dashboard_total == 0:
        dashboards_ready.set()
    outcomes = {"attempted": 0, "connected": 0, "failed": 0, "messages": 0, "http_ok": 0, "http_failed": 0}
    first_state_latencies: list[float] = []
    http_latencies_ms: list[float] = []
    lobby_create_latencies_ms: list[float] = []
    endpoint_stats: dict[str, dict[str, int]] = {}
    endpoint_latencies_ms: dict[str, list[float]] = {}
    endpoint_status_codes: dict[str, Counter[int]] = {}
    endpoint_sample_counts: Counter[str] = Counter()
    http_latency_seen = [0]
    errors: list[str] = []
    active_sockets: set[object] = set()
    teacher_lobby_public_ids: list[str] = []
    teacher_lobby_cleanup_failed = 0
    start_time = time.monotonic()

    def finish_attempt() -> None:
        outcomes["attempted"] += 1
        if outcomes["attempted"] >= args.users:
            all_attempts_finished.set()

    async def player_client(index: int) -> None:
        delay = index / args.ramp_per_second
        if delay:
            await asyncio.sleep(delay)

        if stop.is_set():
            return

        lobby_index = index // clients_per_lobby
        lobby_id = lobby_ids[lobby_index]
        player_id = f"{lobby_prefix}-player-{index + 1:05d}"
        token = signJWT(user_id=player_id, role="Student")["access_token"]
        query = urlencode({"player_token": f"Bearer {token}"})
        url = f"{base_ws_url}/ws/lobby/{lobby_id}?{query}"
        connected_at = time.monotonic()
        attempt_counted = False

        def mark_attempt_finished() -> None:
            nonlocal attempt_counted
            if not attempt_counted:
                attempt_counted = True
                finish_attempt()

        try:
            async with websockets.connect(
                url,
                open_timeout=15,
                close_timeout=2,
                ping_interval=20,
                ping_timeout=20,
                max_queue=64,
            ) as websocket:
                active_sockets.add(websocket)
                outcomes["connected"] += 1
                mark_attempt_finished()

                async def start_lobby_when_ready() -> None:
                    await start_games.wait()
                    if not stop.is_set():
                        await websocket.send(json.dumps({"event": "start_game", "data": {}}))

                host_task = None
                if args.start_games and index % clients_per_lobby == 0:
                    host_task = asyncio.create_task(start_lobby_when_ready())

                first_state_seen = False
                try:
                    while not stop.is_set():
                        try:
                            raw = await asyncio.wait_for(websocket.recv(), timeout=1.0)
                        except TimeoutError:
                            continue
                        outcomes["messages"] += 1
                        try:
                            message = json.loads(raw)
                        except (TypeError, json.JSONDecodeError):
                            continue
                        if not first_state_seen and message.get("event") == "game_state":
                            first_state_seen = True
                            first_state_latencies.append(time.monotonic() - connected_at)
                finally:
                    if host_task is not None:
                        host_task.cancel()
                        await asyncio.gather(host_task, return_exceptions=True)
        except Exception as exc:
            if not stop.is_set():
                outcomes["failed"] += 1
                errors.append(f"player {index + 1}: {type(exc).__name__}: {exc}")
            mark_attempt_finished()
        finally:
            if "websocket" in locals():
                active_sockets.discard(websocket)

    def record_endpoint_result(path: str, status: int | None, latency_ms: float) -> None:
        http_latency_seen[0] += 1
        _reservoir_add(http_latencies_ms, http_latency_seen[0], latency_ms)
        endpoint_sample_counts[path] += 1
        _reservoir_add(
            endpoint_latencies_ms.setdefault(path, []),
            endpoint_sample_counts[path],
            latency_ms,
        )
        if status is not None:
            endpoint_status_codes.setdefault(path, Counter())[status] += 1

    async def perform_http_get(
        session: aiohttp.ClientSession,
        path: str,
        token: str = "",
        validate_parent: bool = False,
    ) -> None:
        url = _http_url(base_ws_url, path)
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        stats = endpoint_stats.setdefault(path, {"ok": 0, "failed": 0})
        began = time.monotonic()
        try:
            async with session.get(url, headers=headers) as response:
                payload_error = None
                if validate_parent and response.status < 400:
                    try:
                        payload = await response.json(content_type=None)
                    except (aiohttp.ContentTypeError, json.JSONDecodeError):
                        payload_error = "expected a JSON response"
                    else:
                        payload_error = _parent_response_error(path, payload)
                else:
                    await response.read()
                latency_ms = (time.monotonic() - began) * 1000
                record_endpoint_result(path, response.status, latency_ms)
                if response.status < 400 and payload_error is None:
                    outcomes["http_ok"] += 1
                    stats["ok"] += 1
                else:
                    outcomes["http_failed"] += 1
                    stats["failed"] += 1
                    if len(errors) < 20:
                        if payload_error:
                            errors.append(f"Unexpected response at {path}: {payload_error}")
                        else:
                            errors.append(f"HTTP {response.status} at {path}")
        except Exception as exc:
            record_endpoint_result(path, None, (time.monotonic() - began) * 1000)
            outcomes["http_failed"] += 1
            stats["failed"] += 1
            if len(errors) < 20:
                errors.append(f"HTTP {path}: {type(exc).__name__}: {exc}")

    async def perform_teacher_lobby_request(
        session: aiohttp.ClientSession,
        method: str,
        path: str,
        stats_path: str,
        payload: dict | None = None,
    ) -> tuple[int, dict]:
        url = _http_url(base_ws_url, path)
        headers = {"Authorization": f"Bearer {dashboard_tokens['teacher']}"}
        stats = endpoint_stats.setdefault(stats_path, {"ok": 0, "failed": 0})
        began = time.monotonic()
        try:
            async with session.request(method, url, headers=headers, json=payload) as response:
                try:
                    body = await response.json(content_type=None)
                except (aiohttp.ContentTypeError, json.JSONDecodeError):
                    await response.read()
                    body = {}
                latency_ms = (time.monotonic() - began) * 1000
                record_endpoint_result(stats_path, response.status, latency_ms)
                if response.status < 400:
                    outcomes["http_ok"] += 1
                    stats["ok"] += 1
                    return response.status, body if isinstance(body, dict) else {}

                outcomes["http_failed"] += 1
                stats["failed"] += 1
                if len(errors) < 20:
                    errors.append(f"HTTP {response.status} at {stats_path}")
                return response.status, {}
        except Exception as exc:
            record_endpoint_result(stats_path, None, (time.monotonic() - began) * 1000)
            outcomes["http_failed"] += 1
            stats["failed"] += 1
            if len(errors) < 20:
                errors.append(f"HTTP {stats_path}: {type(exc).__name__}: {exc}")
            return 0, {}

    async def create_teacher_lobby(index: int, session: aiohttp.ClientSession) -> str | None:
        delay = index / args.lobby_create_ramp_per_second
        if delay:
            await asyncio.sleep(delay)
        name = f"{lobby_prefix}-teacher-lobby-{index + 1:03d}"
        began = time.monotonic()
        status, body = await perform_teacher_lobby_request(
            session,
            "POST",
            "/teacher/lobby/create",
            "/teacher/lobby/create",
            {
                "class_public_id": teacher_class_public_id,
                "name": name,
                "required_players": clients_per_lobby,
            },
        )
        lobby_create_latencies_ms.append((time.monotonic() - began) * 1000)
        lobby = body.get("lobby") if isinstance(body.get("lobby"), dict) else {}
        public_id = str(lobby.get("public_id", "")).strip()
        return public_id if status == 201 and public_id else None

    async def cleanup_teacher_lobbies(session: aiohttp.ClientSession) -> int:
        if not args.teacher_lobbies:
            return 0

        cleanup_ids = set(teacher_lobby_public_ids)
        _status, body = await perform_teacher_lobby_request(
            session,
            "GET",
            "/teacher/lobby/list",
            "/teacher/lobby/list",
        )
        listed_lobbies = body.get("lobbies", [])
        if not isinstance(listed_lobbies, list):
            listed_lobbies = []
        for lobby in listed_lobbies:
            if not isinstance(lobby, dict):
                continue
            if str(lobby.get("name", "")).startswith(f"{lobby_prefix}-teacher-lobby-"):
                public_id = str(lobby.get("public_id", "")).strip()
                if public_id:
                    cleanup_ids.add(public_id)

        if not cleanup_ids:
            print("Teacher lobby cleanup: no created records found")
            return 0
        semaphore = asyncio.Semaphore(16)

        async def delete_one(public_id: str) -> bool:
            async with semaphore:
                status, _body = await perform_teacher_lobby_request(
                    session,
                    "DELETE",
                    f"/teacher/lobby/{public_id}",
                    "/teacher/lobby/{public_id}",
                )
                return 200 <= status < 300

        results = await asyncio.gather(*(delete_one(public_id) for public_id in cleanup_ids))
        failures = sum(not result for result in results)
        print(f"Teacher lobby cleanup: {len(results) - failures}/{len(results)} database records deleted")
        return failures

    async def http_client(client_index: int, session: aiohttp.ClientSession) -> None:
        await asyncio.sleep(client_index * args.http_interval / max(args.http_users, 1))
        while not stop.is_set():
            await perform_http_get(session, args.http_path, args.http_token)
            try:
                await asyncio.wait_for(stop.wait(), timeout=args.http_interval)
            except TimeoutError:
                pass

    async def dashboard_client(
        role: str,
        global_index: int,
        role_index: int,
        session: aiohttp.ClientSession,
    ) -> None:
        delay = global_index / args.dashboard_ramp_per_second
        if delay:
            await asyncio.sleep(delay)
        if stop.is_set():
            return

        spec = DASHBOARD_REQUESTS[role]
        if role == "parent":
            token = parent_tokens[role_index % len(parent_tokens)]
        else:
            token = dashboard_tokens[role]
        await asyncio.gather(
            *(perform_http_get(session, path, token, validate_parent=(role == "parent")) for path in spec["initial"])
        )
        ready_counter[0] += 1
        if ready_counter[0] >= dashboard_total:
            dashboards_ready.set()

        poll_interval = args.parent_poll_interval if role == "parent" else float(spec["interval"])
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=poll_interval)
            except TimeoutError:
                if not stop.is_set():
                    await perform_http_get(session, str(spec["poll"]), token, validate_parent=(role == "parent"))

    print(f"Target: {base_ws_url}")
    if args.teacher_lobbies:
        print(f"Teacher lobby load: create {args.teacher_lobbies}; fill with {args.users} players (8 per lobby)")
        print(f"Lobby creation ramp: {args.lobby_create_ramp_per_second:g} lobby requests/second")
    else:
        print(f"Synthetic game clients: {args.users} across {len(lobby_ids)} ephemeral lobbies (max 8 per lobby)")
    print(f"Ramp: {args.ramp_per_second:g} connection attempts/second; steady duration: {args.duration}s")
    print(f"Game timer broadcasts: {'enabled' if args.start_games else 'disabled (pass --start-games to enable)'}")
    print(f"Additional HTTP poll clients: {args.http_users} at {args.http_path} every {args.http_interval:g}s")
    print(f"Dashboard clients: admin={args.admin_users}, teacher={args.teacher_users}, parent={args.parent_users}")
    if args.parent_users:
        print(f"Parent accounts: {len(parent_tokens)} token(s); inbox poll interval: {args.parent_poll_interval:g}s")
        if len(parent_tokens) < args.parent_users:
            print(
                f"Note: parent tokens will be reused across {args.parent_users} clients; "
                "use one distinct test account per client for representative account-level data."
            )
    print("Use isolated staging/test data only; never target live production. Lobbies use unique IDs and Redis state expires by TTL.")

    total_http_users = args.http_users + dashboard_total
    http_connector = aiohttp.TCPConnector(limit=max(100, total_http_users * 4))
    timeout = aiohttp.ClientTimeout(total=10)
    async with aiohttp.ClientSession(connector=http_connector, timeout=timeout) as http_session:
        ready_counter = [0]
        tasks: list[asyncio.Task] = []
        try:
            if args.teacher_lobbies:
                created_ids = await asyncio.gather(
                    *(create_teacher_lobby(index, http_session) for index in range(args.teacher_lobbies))
                )
                teacher_lobby_public_ids = [public_id for public_id in created_ids if public_id]
                print(f"Teacher lobbies created: {len(teacher_lobby_public_ids)}/{args.teacher_lobbies}")
                if len(teacher_lobby_public_ids) != args.teacher_lobbies:
                    raise RuntimeError(
                        f"Only {len(teacher_lobby_public_ids)} of {args.teacher_lobbies} teacher lobbies were created; "
                        "the test will stop and attempt to delete the ones that succeeded."
                    )
                lobby_ids = list(teacher_lobby_public_ids)
                print(f"Synthetic game clients: {args.users} across {len(lobby_ids)} teacher lobbies (8 per lobby)")

            tasks = [asyncio.create_task(player_client(i)) for i in range(args.users)]
            tasks.extend(asyncio.create_task(http_client(i, http_session)) for i in range(args.http_users))
            dashboard_index = 0
            for role, count in dashboard_counts.items():
                for role_index in range(count):
                    tasks.append(
                        asyncio.create_task(
                            dashboard_client(role, dashboard_index, role_index, http_session)
                        )
                    )
                    dashboard_index += 1

            ramp_seconds = max(args.users - 1, 0) / args.ramp_per_second
            if args.users:
                try:
                    await asyncio.wait_for(all_attempts_finished.wait(), timeout=ramp_seconds + 20)
                except TimeoutError:
                    errors.append("Timed out waiting for all WebSocket connection attempts to finish")

            start_games.set()
            connected_now = outcomes["connected"]
            print(f"Connection ramp complete: {connected_now}/{args.users} WebSockets connected; holding for {args.duration}s")
            dashboard_ramp_seconds = max(dashboard_total - 1, 0) / args.dashboard_ramp_per_second
            try:
                await asyncio.wait_for(dashboards_ready.wait(), timeout=dashboard_ramp_seconds + 20)
            except TimeoutError:
                errors.append("Timed out waiting for dashboard clients to finish their initial requests")
            await asyncio.sleep(args.duration)
        finally:
            stop.set()
            start_games.set()
            # Close sockets first so their receive loops can exit normally, then
            # cancel ramp/poll tasks that have not connected yet.
            if active_sockets:
                await asyncio.gather(
                    *(websocket.close(code=1000, reason="Load test finished") for websocket in list(active_sockets)),
                    return_exceptions=True,
                )
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            teacher_lobby_cleanup_failed = await cleanup_teacher_lobbies(http_session)

    elapsed = time.monotonic() - start_time
    print("\nResults")
    print(f"  WebSocket connections: {outcomes['connected']}/{args.users} connected; {outcomes['failed']} failed")
    print(f"  Game-state/event messages received: {outcomes['messages']}")
    if first_state_latencies:
        latencies_ms = [value * 1000 for value in first_state_latencies]
        print(
            "  First game-state latency: "
            f"median {statistics.median(latencies_ms):.1f} ms, "
            f"p95 {_percentile(latencies_ms, 0.95):.1f} ms"
        )
    if total_http_users or args.teacher_lobbies:
        http_total = outcomes["http_ok"] + outcomes["http_failed"]
        http_rps = http_total / elapsed if elapsed else 0.0
        print(
            f"  Dashboard/HTTP responses: {outcomes['http_ok']} successful; "
            f"{outcomes['http_failed']} failed; {http_rps:.1f} requests/second"
        )
        if http_latencies_ms:
            print(
                "  HTTP latency: "
                f"median {statistics.median(http_latencies_ms):.1f} ms, "
                f"p95 {_percentile(http_latencies_ms, 0.95):.1f} ms, "
                f"p99 {_percentile(http_latencies_ms, 0.99):.1f} ms"
            )
            if http_latency_seen[0] > len(http_latencies_ms):
                print(f"  Latency percentile sample: {len(http_latencies_ms)} of {http_latency_seen[0]} responses")
        print("  HTTP endpoint counts:")
        for path, counts in sorted(endpoint_stats.items()):
            endpoint_latencies = endpoint_latencies_ms.get(path, [])
            codes = endpoint_status_codes.get(path, Counter())
            code_summary = ", ".join(f"{code}={count}" for code, count in sorted(codes.items()))
            endpoint_total = counts["ok"] + counts["failed"]
            endpoint_rps = endpoint_total / elapsed if elapsed else 0.0
            line = f"    {path}: {counts['ok']} successful; {counts['failed']} failed"
            line += f"; {endpoint_rps:.1f} requests/second"
            if endpoint_latencies:
                line += (
                    f"; p50 {_percentile(endpoint_latencies, 0.50):.1f} ms"
                    f"; p95 {_percentile(endpoint_latencies, 0.95):.1f} ms"
                    f"; p99 {_percentile(endpoint_latencies, 0.99):.1f} ms"
                )
            if code_summary:
                line += f"; status {code_summary}"
            print(line)
    if args.teacher_lobbies:
        successful_creates = endpoint_stats.get("/teacher/lobby/create", {}).get("ok", 0)
        failed_creates = endpoint_stats.get("/teacher/lobby/create", {}).get("failed", 0)
        print(f"  Teacher lobby creation: {successful_creates}/{args.teacher_lobbies} succeeded; {failed_creates} failed")
        if lobby_create_latencies_ms:
            print(
                "  Lobby creation latency: "
                f"median {statistics.median(lobby_create_latencies_ms):.1f} ms, "
                f"p95 {_percentile(lobby_create_latencies_ms, 0.95):.1f} ms"
            )
        print(f"  Teacher lobby cleanup failures: {teacher_lobby_cleanup_failed}")
    print(f"  Elapsed: {elapsed:.1f}s")
    if errors:
        print("  Sample errors:")
        for error in errors[:10]:
            print(f"    - {error}")
    print("\nThis reports client-side results only; also monitor backend, Redis, database, CPU, and memory.")
    return 0 if outcomes["failed"] == 0 and outcomes["http_failed"] == 0 else 1


def main() -> None:
    args = parse_args()
    try:
        exit_code = asyncio.run(run_load_test(args))
    except KeyboardInterrupt:
        print("\nInterrupted")
        exit_code = 130
    except (ValueError, OSError, RuntimeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        exit_code = 2
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
