"""Create reusable parent accounts for the read-only parent API load test.

The command is a dry run unless --apply is supplied. It uses the configured
database directly and writes short-lived JWTs to the ignored token file consumed
by scripts/load_test.py. Use only with an isolated test database.
"""

from __future__ import annotations

import argparse
import ipaddress
import os
import re
import secrets
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
TOKEN_FILE = ROOT / "loadtest-parent-tokens.txt"
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create synthetic Parent accounts and JWTs for the parent endpoint load test.",
        epilog=(
            "Dry run: python scripts/create_loadtest_parents.py --count 100\n"
            "Create accounts: python scripts/create_loadtest_parents.py --count 100 --apply"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--count", type=int, default=100, help="Number of accounts to ensure (1-1000; default: 100)")
    parser.add_argument("--prefix", default="loadtest-parent", help="Username/email prefix used to identify these test accounts")
    parser.add_argument("--apply", action="store_true", help="Write accounts to the database; without this flag, only show the plan")
    parser.add_argument("--allow-remote", action="store_true", help="Allow a dedicated remote test database; never use a live production database")
    args = parser.parse_args()

    if not 1 <= args.count <= 1000:
        parser.error("--count must be between 1 and 1000")
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,35}", args.prefix):
        parser.error("--prefix must start with a lowercase letter and contain only lowercase letters, digits, or hyphens (max 36 characters)")
    return args


def database_target(allow_remote: bool) -> tuple[str, str]:
    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        raise ValueError("DATABASE_URL is missing; set it in the environment or backend .env")
    if database_url.startswith("postgres://"):
        database_url = database_url.replace("postgres://", "postgresql://", 1)

    parsed = urlparse(database_url)
    if parsed.scheme not in {"postgresql", "postgresql+psycopg", "postgresql+psycopg2"}:
        raise ValueError("This script requires a PostgreSQL DATABASE_URL")

    host = parsed.hostname or "(local socket)"
    if not parsed.hostname or host.lower() in {"localhost", "db", "game-postgres"}:
        is_local = True
    else:
        try:
            is_local = ipaddress.ip_address(host).is_loopback
        except ValueError:
            is_local = False

    if not is_local and not allow_remote:
        raise ValueError(
            f"Database host {host!r} is not local. Use a dedicated test database and "
            "pass --allow-remote to confirm you intend to use it."
        )
    return database_url, host


def _write_tokens(tokens: list[str]) -> None:
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            prefix=".loadtest-parent-tokens-",
            suffix=".tmp",
            dir=ROOT,
            delete=False,
        ) as token_file:
            temp_path = Path(token_file.name)
            token_file.write("\n".join(tokens) + "\n")
            token_file.flush()
            os.fsync(token_file.fileno())
        if os.name != "nt":
            os.chmod(temp_path, 0o600)
        os.replace(temp_path, TOKEN_FILE)
        temp_path = None
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def run(args: argparse.Namespace) -> int:
    database_url, host = database_target(args.allow_remote)

    usernames = [f"{args.prefix}-{index:05d}" for index in range(1, args.count + 1)]
    emails = [f"{username}@example.test" for username in usernames]
    print(f"PostgreSQL host: {host}")
    print(f"Parent accounts: {args.count} ({usernames[0]} through {usernames[-1]})")
    print(f"JWT output: {TOKEN_FILE}")
    print("Use only an isolated test database; these accounts have no linked children or messages by default.")

    if not args.apply:
        print("Dry run only. Add --apply to create missing accounts and write JWTs.")
        return 0

    from flask import Flask
    from sqlalchemy import inspect, or_
    from sqlalchemy.exc import IntegrityError, SQLAlchemyError
    from werkzeug.security import generate_password_hash

    from app.auth.auth_handler import signJWT
    from app.server.database import db
    from app.server.models.user import User

    app = Flask("create-loadtest-parent-accounts")
    app.config.update(
        SQLALCHEMY_DATABASE_URI=database_url,
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        SQLALCHEMY_ENGINE_OPTIONS={"pool_pre_ping": True, "pool_recycle": 300, "pool_size": 2, "max_overflow": 0},
    )
    db.init_app(app)

    created_count = 0
    try:
        with app.app_context():
            if not inspect(db.engine).has_table("users"):
                raise RuntimeError("The users table does not exist. Initialize the test backend database first.")

            matches = User.query.filter(or_(User.username.in_(usernames), User.email.in_(emails))).all()
            by_username = {user.username: user for user in matches}
            by_email = {user.email: user for user in matches}
            accounts: list[User] = []
            new_accounts: list[User] = []

            for index, (username, email) in enumerate(zip(usernames, emails), start=1):
                username_match = by_username.get(username)
                email_match = by_email.get(email)
                if username_match or email_match:
                    if (
                        username_match is None
                        or email_match is None
                        or username_match.id != email_match.id
                        or username_match.role != "Parent"
                    ):
                        raise ValueError(
                            f"Account name collision for {username!r}; no accounts were changed. "
                            "Choose a different --prefix."
                        )
                    accounts.append(username_match)
                    continue

                user = User(
                    first_name="Load Test",
                    last_name=f"Parent {index:05d}",
                    username=username,
                    email=email,
                    password_hash=generate_password_hash(secrets.token_urlsafe(32)),
                    must_change_password=True,
                    role="Parent",
                )
                accounts.append(user)
                new_accounts.append(user)

            db.session.add_all(new_accounts)
            db.session.flush()
            tokens = [signJWT(str(user.id), "Parent")["access_token"] for user in accounts]
            try:
                db.session.commit()
            except IntegrityError:
                db.session.rollback()
                raise RuntimeError(
                    "A database uniqueness conflict occurred; no accounts from this batch were committed. "
                    "Retry the command or choose a different --prefix."
                ) from None
            created_count = len(new_accounts)
            db.session.remove()

        _write_tokens(tokens)
    except SQLAlchemyError:
        raise RuntimeError("Database operation failed; check the test database connection and schema.") from None
    finally:
        with app.app_context():
            db.engine.dispose()

    print(f"Created: {created_count}; reused: {args.count - created_count}")
    print(f"Wrote {len(tokens)} fresh 24-hour parent JWTs to {TOKEN_FILE}")
    print("The JWT file is ignored by Git. Do not share or commit it.")
    return 0


def main() -> None:
    try:
        raise SystemExit(run(parse_args()))
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()
