"""Generate synthetic WebSocket clients and optional HTTP polling traffic.

Use only against a local or dedicated test deployment. The script creates
ephemeral in-memory/Redis lobby state and does not create database accounts.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import secrets
import statistics
import sys
import time
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
        # Models a teacher with the parent messages tab open (the UI polls every 5s).
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
        description="Load test the game's WebSocket relay and dashboard read APIs.",
        epilog=(
            "Example: python scripts/load_test.py --users 100 --duration 60 "
            "--ramp-per-second 10 --start-games --admin-users 5 "
            "--teacher-users 10 --parent-users 20"
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
    parser.add_argument("--dashboard-ramp-per-second", type=float, default=10.0, help="Dashboard clients started per second")
    parser.add_argument("--admin-token", default=os.getenv("LOADTEST_ADMIN_TOKEN", ""), help="Admin test JWT (or set LOADTEST_ADMIN_TOKEN)")
    parser.add_argument("--teacher-token", default=os.getenv("LOADTEST_TEACHER_TOKEN", ""), help="Teacher test JWT (or set LOADTEST_TEACHER_TOKEN)")
    parser.add_argument("--parent-token", default=os.getenv("LOADTEST_PARENT_TOKEN", ""), help="Parent test JWT (or set LOADTEST_PARENT_TOKEN)")
    parser.add_argument("--allow-remote", action="store_true", help="Allow sending load to a non-local host")
    args = parser.parse_args(argv)

    user_counts = [args.users, args.http_users, args.admin_users, args.teacher_users, args.parent_users]
    if any(count < 0 or count > 10_000 for count in user_counts):
        parser.error("user counts must be between 0 and 10000")
    if sum(user_counts) < 1:
        parser.error("configure at least one game, dashboard, or HTTP client")
    if args.duration < 1 or args.duration > 3600:
        parser.error("--duration must be between 1 and 3600 seconds")
    if args.ramp_per_second <= 0 or args.dashboard_ramp_per_second <= 0 or args.http_interval <= 0:
        parser.error("ramp rates and --http-interval must be greater than zero")
    if not args.http_path.startswith("/") or args.http_path.startswith("//"):
        parser.error("--http-path must be a path beginning with one slash")
    return args


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


async def run_load_test(args: argparse.Namespace) -> int:
    _require_local_target(args.base_ws_url, args.allow_remote)
    dashboard_counts = {
        "admin": args.admin_users,
        "teacher": args.teacher_users,
        "parent": args.parent_users,
    }
    dashboard_tokens = {
        "admin": args.admin_token.removeprefix("Bearer ").strip(),
        "teacher": args.teacher_token.removeprefix("Bearer ").strip(),
        "parent": args.parent_token.removeprefix("Bearer ").strip(),
    }
    for role, count in dashboard_counts.items():
        if count and not dashboard_tokens[role]:
            raise ValueError(
                f"{role.title()} dashboard clients need a real {role} JWT. "
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
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as preflight_session:
        for role, count in dashboard_counts.items():
            if not count:
                continue
            async with preflight_session.get(
                _http_url(base_ws_url, preflight_paths[role]),
                headers={"Authorization": f"Bearer {dashboard_tokens[role]}"},
            ) as response:
                await response.read()
                if response.status >= 400:
                    raise ValueError(
                        f"{role.title()} token preflight failed: HTTP {response.status} "
                        f"from {preflight_paths[role]}. Check the token's role, expiry, "
                        "and that it matches the backend JWT_SECRET."
                    )

    run_id = f"{int(time.time())}-{secrets.token_hex(3)}"
    lobby_prefix = f"loadtest-{run_id}"
    clients_per_lobby = 8
    lobby_ids = [f"{lobby_prefix}-lobby-{index + 1:03d}" for index in range(math.ceil(args.users / clients_per_lobby))]
    stop = asyncio.Event()
    all_attempts_finished = asyncio.Event()
    start_games = asyncio.Event()
    dashboards_ready = asyncio.Event()
    if dashboard_total == 0:
        dashboards_ready.set()
    outcomes = {"attempted": 0, "connected": 0, "failed": 0, "messages": 0, "http_ok": 0, "http_failed": 0}
    first_state_latencies: list[float] = []
    http_latencies_ms: list[float] = []
    endpoint_stats: dict[str, dict[str, int]] = {}
    errors: list[str] = []
    active_sockets: set[object] = set()
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

    async def perform_http_get(session: aiohttp.ClientSession, path: str, token: str = "") -> None:
        url = _http_url(base_ws_url, path)
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        stats = endpoint_stats.setdefault(path, {"ok": 0, "failed": 0})
        began = time.monotonic()
        try:
            async with session.get(url, headers=headers) as response:
                await response.read()
                http_latencies_ms.append((time.monotonic() - began) * 1000)
                if response.status < 400:
                    outcomes["http_ok"] += 1
                    stats["ok"] += 1
                else:
                    outcomes["http_failed"] += 1
                    stats["failed"] += 1
                    if len(errors) < 20:
                        errors.append(f"HTTP {response.status} at {path}")
        except Exception as exc:
            outcomes["http_failed"] += 1
            stats["failed"] += 1
            if len(errors) < 20:
                errors.append(f"HTTP {path}: {type(exc).__name__}: {exc}")

    async def http_client(client_index: int, session: aiohttp.ClientSession) -> None:
        await asyncio.sleep(client_index * args.http_interval / max(args.http_users, 1))
        while not stop.is_set():
            await perform_http_get(session, args.http_path, args.http_token)
            try:
                await asyncio.wait_for(stop.wait(), timeout=args.http_interval)
            except TimeoutError:
                pass

    async def dashboard_client(role: str, global_index: int, session: aiohttp.ClientSession) -> None:
        delay = global_index / args.dashboard_ramp_per_second
        if delay:
            await asyncio.sleep(delay)
        if stop.is_set():
            return

        spec = DASHBOARD_REQUESTS[role]
        token = dashboard_tokens[role]
        await asyncio.gather(*(perform_http_get(session, path, token) for path in spec["initial"]))
        ready_counter[0] += 1
        if ready_counter[0] >= dashboard_total:
            dashboards_ready.set()

        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=float(spec["interval"]))
            except TimeoutError:
                if not stop.is_set():
                    await perform_http_get(session, str(spec["poll"]), token)

    print(f"Target: {base_ws_url}")
    print(f"Synthetic game clients: {args.users} across {len(lobby_ids)} lobbies (max 8 per lobby)")
    print(f"Ramp: {args.ramp_per_second:g} connection attempts/second; steady duration: {args.duration}s")
    print(f"Game timer broadcasts: {'enabled' if args.start_games else 'disabled (pass --start-games to enable)'}")
    print(f"Additional HTTP poll clients: {args.http_users} at {args.http_path} every {args.http_interval:g}s")
    print(f"Dashboard clients: admin={args.admin_users}, teacher={args.teacher_users}, parent={args.parent_users}")
    print("Use a test deployment only. Lobbies use unique IDs; leftover Redis state expires after its configured TTL.")

    total_http_users = args.http_users + dashboard_total
    http_connector = aiohttp.TCPConnector(limit=max(100, total_http_users * 4))
    timeout = aiohttp.ClientTimeout(total=10)
    async with aiohttp.ClientSession(connector=http_connector, timeout=timeout) as http_session:
        tasks = [asyncio.create_task(player_client(i)) for i in range(args.users)]
        tasks.extend(asyncio.create_task(http_client(i, http_session)) for i in range(args.http_users))
        dashboard_index = 0
        ready_counter = [0]
        for role, count in dashboard_counts.items():
            for _ in range(count):
                tasks.append(asyncio.create_task(dashboard_client(role, dashboard_index, http_session)))
                dashboard_index += 1

        ramp_seconds = max(args.users - 1, 0) / args.ramp_per_second
        try:
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
    if total_http_users:
        print(f"  Dashboard/HTTP responses: {outcomes['http_ok']} successful; {outcomes['http_failed']} failed")
        if http_latencies_ms:
            print(
                "  HTTP latency: "
                f"median {statistics.median(http_latencies_ms):.1f} ms, "
                f"p95 {_percentile(http_latencies_ms, 0.95):.1f} ms"
            )
        print("  HTTP endpoint counts:")
        for path, counts in sorted(endpoint_stats.items()):
            print(f"    {path}: {counts['ok']} successful; {counts['failed']} failed")
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
    except (ValueError, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        exit_code = 2
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
