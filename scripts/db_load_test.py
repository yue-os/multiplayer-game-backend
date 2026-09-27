"""Read-only PostgreSQL load test for the game's application database.

Uses DATABASE_URL from the process environment or the backend's ignored .env.
Only SELECT statements run, each inside a read-only transaction. By default,
the target must be a local host; --allow-remote is required for a dedicated
remote test database.
"""

from __future__ import annotations

import argparse
import asyncio
import ipaddress
import math
import os
import statistics
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Sequence
from urllib.parse import urlparse

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

QUERY_MIX: tuple[tuple[str, str], ...] = (
    (
        "users_by_role",
        "SELECT role, COUNT(*) FROM users GROUP BY role",
    ),
    (
        "class_rosters",
        """SELECT c.id, COUNT(u.id) AS student_count
           FROM classes AS c
           LEFT JOIN users AS u
             ON u.class_id = c.id AND u.role = 'Student'
           GROUP BY c.id
           ORDER BY c.id
           LIMIT 100""",
    ),
    (
        "quiz_submissions",
        """SELECT q.id, COUNT(qr.id) AS submission_count
           FROM quizzes AS q
           LEFT JOIN quiz_results AS qr ON qr.quiz_id = q.id
           GROUP BY q.id
           ORDER BY q.id
           LIMIT 100""",
    ),
    (
        "recent_messages",
        """SELECT id, sender_id, receiver_id, created_at
           FROM messages
           ORDER BY created_at DESC, id DESC
           LIMIT 50""",
    ),
    (
        "password_reset_requests",
        "SELECT status, COUNT(*) FROM password_reset_requests GROUP BY status",
    ),
)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a read-only concurrent PostgreSQL workload against the app database.",
        epilog=(
            "Example: python scripts/db_load_test.py --clients 100 --duration 120 "
            "--ramp-per-second 10 --interval 5"
        ),
    )
    parser.add_argument("--clients", type=int, default=50, help="Virtual DB clients (1-1000; default: 50)")
    parser.add_argument("--duration", type=int, default=60, help="Steady time after ramp completes (1-3600s)")
    parser.add_argument("--ramp-per-second", type=float, default=10.0, help="Virtual clients started per second")
    parser.add_argument("--interval", type=float, default=5.0, help="Seconds between queries per client")
    parser.add_argument("--pool-size", type=int, default=2, help="SQLAlchemy connection pool size (default matches backend)")
    parser.add_argument("--max-overflow", type=int, default=0, help="Extra simultaneous DB connections (default matches backend)")
    parser.add_argument("--pool-timeout", type=float, default=5.0, help="Seconds to wait for a pooled connection")
    parser.add_argument("--workers", type=int, default=32, help="Maximum concurrent query worker threads (1-256)")
    parser.add_argument("--allow-remote", action="store_true", help="Allow a non-local DB host; use only a dedicated test database")
    args = parser.parse_args(argv)

    if not 1 <= args.clients <= 1000:
        parser.error("--clients must be between 1 and 1000")
    if not 1 <= args.duration <= 3600:
        parser.error("--duration must be between 1 and 3600 seconds")
    if args.ramp_per_second <= 0 or args.interval <= 0:
        parser.error("--ramp-per-second and --interval must be greater than zero")
    if not 1 <= args.pool_size <= 100:
        parser.error("--pool-size must be between 1 and 100")
    if not 0 <= args.max_overflow <= 100:
        parser.error("--max-overflow must be between 0 and 100")
    if args.pool_timeout <= 0:
        parser.error("--pool-timeout must be greater than zero")
    if not 1 <= args.workers <= 256:
        parser.error("--workers must be between 1 and 256")
    return args


def _database_url() -> str:
    value = os.getenv("DATABASE_URL", "").strip()
    if not value:
        raise ValueError("DATABASE_URL is missing; set it in the environment or backend .env")
    if value.startswith("postgres://"):
        value = value.replace("postgres://", "postgresql://", 1)
    return value


def _target_host(database_url: str) -> tuple[str, bool]:
    parsed = urlparse(database_url)
    host = parsed.hostname or "(local socket)"
    if not parsed.hostname:
        return host, True
    if host.lower() in {"localhost", "db", "game-postgres"}:
        # `db` and `game-postgres` are the local service names in this repo's
        # Docker Compose setup. They are useful when the script runs in-container.
        return host, True
    try:
        return host, ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host, False


def _make_engine(database_url: str, args: argparse.Namespace) -> Engine:
    engine = create_engine(
        database_url,
        pool_pre_ping=True,
        pool_size=args.pool_size,
        max_overflow=args.max_overflow,
        pool_timeout=args.pool_timeout,
        pool_recycle=300,
        connect_args={"application_name": "game_db_load_test"},
    )
    if engine.dialect.name != "postgresql":
        engine.dispose()
        raise ValueError(f"Expected PostgreSQL, found {engine.dialect.name!r}")
    return engine


def _run_read_query(engine: Engine, sql: str) -> None:
    with engine.connect() as connection:
        with connection.begin():
            connection.exec_driver_sql("SET TRANSACTION READ ONLY")
            connection.execute(text(sql)).fetchall()


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, math.ceil(fraction * len(ordered)) - 1)
    return ordered[max(0, index)]


async def run_load_test(args: argparse.Namespace) -> int:
    database_url = _database_url()
    host, is_local = _target_host(database_url)
    if not is_local and not args.allow_remote:
        raise ValueError(
            f"Database host {host!r} is not local. Use a dedicated test database and "
            "pass --allow-remote to confirm you intend to load it."
        )

    print(f"Target PostgreSQL host: {host}")
    engine = _make_engine(database_url, args)
    if args.clients > args.workers:
        print(
            f"Note: {args.clients} virtual clients share up to {args.workers} query workers "
            f"and {args.pool_size + args.max_overflow} DB connections."
        )

    # Check the schema and connection before starting the timed workload.
    try:
        for name, sql in QUERY_MIX:
            _run_read_query(engine, sql)
    except SQLAlchemyError as exc:
        engine.dispose()
        raise RuntimeError(
            f"Database preflight failed ({type(exc).__name__}); verify the local test DB "
            "is running and has the current application schema. No rows were changed."
        ) from None

    latencies_ms: list[float] = []
    latency_sample_limit = 100_000
    counts: Counter[str] = Counter()
    failures: Counter[str] = Counter()
    first_failure_reported = False
    stop = asyncio.Event()
    started_at = time.monotonic()
    loop = asyncio.get_running_loop()
    executor = ThreadPoolExecutor(max_workers=args.workers, thread_name_prefix="db-load")
    loop.set_default_executor(executor)

    async def client_task(client_index: int) -> None:
        nonlocal first_failure_reported
        start_delay = client_index / args.ramp_per_second
        if start_delay:
            await asyncio.sleep(start_delay)
        query_index = client_index % len(QUERY_MIX)
        while not stop.is_set():
            name, sql = QUERY_MIX[query_index % len(QUERY_MIX)]
            query_index += 1
            request_started = time.perf_counter()
            try:
                await asyncio.to_thread(_run_read_query, engine, sql)
                counts[name] += 1
                if len(latencies_ms) < latency_sample_limit:
                    latencies_ms.append((time.perf_counter() - request_started) * 1000)
            except Exception as exc:
                failures[name] += 1
                if not first_failure_reported:
                    print(f"First query failure: {type(exc).__name__} ({name})")
                    first_failure_reported = True

            try:
                await asyncio.wait_for(stop.wait(), timeout=args.interval)
            except TimeoutError:
                pass

    tasks = [asyncio.create_task(client_task(index)) for index in range(args.clients)]
    ramp_seconds = max(0.0, (args.clients - 1) / args.ramp_per_second)

    print(f"Read-only virtual clients: {args.clients}; ramp: {args.ramp_per_second:g}/s; steady duration: {args.duration}s")
    print(f"Query interval: {args.interval:g}s; connection pool: {args.pool_size} + {args.max_overflow} overflow; pool wait timeout: {args.pool_timeout:g}s")
    print("Queries: users by role, class rosters, quiz submissions, recent messages, password-reset requests")
    print("No application rows are inserted, updated, or deleted.")

    try:
        if ramp_seconds:
            await asyncio.sleep(ramp_seconds)
        print(f"Ramp complete: {args.clients}/{args.clients} virtual DB clients started; holding for {args.duration}s")
        await asyncio.sleep(args.duration)
    finally:
        stop.set()
        await asyncio.gather(*tasks, return_exceptions=True)
        elapsed = time.monotonic() - started_at
        engine.dispose()

    total_ok = sum(counts.values())
    total_failed = sum(failures.values())
    total = total_ok + total_failed
    qps = total / elapsed if elapsed else 0.0
    print("\nResults")
    print(f"Read queries: {total_ok} successful; {total_failed} failed")
    print(f"Query throughput: {qps:.1f} queries/second over {elapsed:.1f}s")
    if latencies_ms:
        print(
            f"End-to-end query latency (includes worker/pool wait): median {statistics.median(latencies_ms):.1f} ms, "
            f"p95 {_percentile(latencies_ms, 0.95):.1f} ms"
        )
        if total_ok > len(latencies_ms):
            print(f"Latency sample: first {len(latencies_ms)} successful queries")
    print("Per-query counts:")
    for name, _sql in QUERY_MIX:
        print(f"  {name}: {counts[name]} successful; {failures[name]} failed")
    return 1 if total_failed else 0


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        return asyncio.run(run_load_test(args))
    except (ValueError, RuntimeError, SQLAlchemyError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
