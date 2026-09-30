from __future__ import annotations

from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import event, inspect
from sqlalchemy.orm import Session

from app.server.database import db
from app.server.models.audit_log import AuditLog


audit_context: ContextVar[dict[str, Any] | None] = ContextVar("audit_context", default=None)

ENTITY_LABELS = {
    "users": "user account",
    "classes": "class",
    "missions": "mission",
    "mission_progress": "mission progress",
    "quizzes": "quiz",
    "quiz_questions": "quiz question",
    "quiz_results": "quiz result",
    "announcements": "announcement",
    "messages": "message",
    "game_servers": "game lobby",
    "playtime_logs": "playtime record",
    "password_reset_requests": "password reset request",
    "pending_registrations": "pending registration",
    "student_refresh_sessions": "student session",
}


def _entity_name(instance: Any) -> tuple[str, str]:
    table = getattr(instance, "__tablename__", None)
    if table is None and getattr(instance, "__table__", None) is not None:
        table = instance.__table__.name
    table = table or instance.__class__.__name__.lower()
    return table, ENTITY_LABELS.get(table, table.replace("_", " ").rstrip("s"))


def _entity_id(instance: Any) -> str | None:
    value = getattr(instance, "public_id", None) or getattr(instance, "id", None)
    return str(value) if value is not None else None


def _action_for(instance: Any, operation: str) -> str:
    table, label = _entity_name(instance)
    if table == "users" and operation == "update":
        try:
            state = inspect(instance)
            history = state.attrs.parent_id.history
            if history.has_changes():
                return "Linked student account" if getattr(instance, "parent_id", None) else "Unlinked student account"
            class_history = state.attrs.class_id.history
            if class_history.has_changes():
                return "Assigned student to class" if getattr(instance, "class_id", None) else "Removed student from class"
        except (AttributeError, KeyError):
            pass
    if table == "game_servers" and operation == "create":
        return "Created game lobby"
    if table == "users":
        role = getattr(instance, "role", None)
        if operation == "create" and role:
            return f"Created {role} account"
        if operation == "delete" and role:
            return f"Deleted {role} account"
    return {
        "create": f"Created {label}",
        "update": f"Updated {label}",
        "delete": f"Deleted {label}",
    }[operation]


def _request_metadata() -> dict[str, Any]:
    context = audit_context.get()
    if context is not None:
        return context

    # Flask REST API fallback when it is run without the FastAPI ASGI wrapper.
    try:
        from flask import g, has_request_context, request

        if has_request_context():
            return {
                "user_id": getattr(g, "audit_user_id", None),
                "user_role": getattr(g, "audit_user_role", None) or "Anonymous",
                "request_method": request.method,
                "request_path": request.path,
                "ip_address": request.remote_addr,
                "tracked_changes": 0,
            }
    except (ImportError, RuntimeError):
        pass
    return {
        "user_id": None,
        "user_role": "System",
        "request_method": None,
        "request_path": None,
        "ip_address": None,
        "tracked_changes": 0,
    }


@event.listens_for(Session, "before_flush")
def collect_audit_changes(session: Session, _flush_context: Any, _instances: Any) -> None:
    pending: list[tuple[Any, str]] = []
    seen: set[int] = set()
    context = _request_metadata()

    for operation, objects in (("create", session.new), ("update", session.dirty), ("delete", session.deleted)):
        for instance in list(objects):
            if isinstance(instance, AuditLog) or id(instance) in seen:
                continue
            seen.add(id(instance))
            if operation == "update" and not session.is_modified(instance, include_collections=False):
                continue
            table, _label = _entity_name(instance)
            if table == "game_servers" and operation == "create" and not getattr(instance, "persistent", False) and context.get("user_id") is None:
                continue
            if table == "game_servers" and operation == "update":
                changed_fields = {
                    attribute.key
                    for attribute in inspect(instance).attrs
                    if attribute.history.has_changes()
                }
                if changed_fields and changed_fields.issubset({"last_heartbeat", "player_count"}):
                    continue
            pending.append((instance, operation))

    if pending:
        session.info.setdefault("_audit_pending", []).extend(pending)


@event.listens_for(Session, "after_flush")
def persist_audit_changes(session: Session, _flush_context: Any) -> None:
    pending = session.info.pop("_audit_pending", [])
    if not pending:
        return

    context = _request_metadata()
    for instance, operation in pending:
        table, label = _entity_name(instance)
        session.add(AuditLog(
            timestamp=datetime.now(timezone.utc),
            user_id=context.get("user_id"),
            user_role=context.get("user_role") or "System",
            action_type=operation,
            action=_action_for(instance, operation),
            entity_type=label,
            entity_id=_entity_id(instance),
            request_method=context.get("request_method"),
            request_path=context.get("request_path"),
            ip_address=context.get("ip_address"),
        ))
        context["tracked_changes"] = context.get("tracked_changes", 0) + 1


@event.listens_for(Session, "do_orm_execute")
def audit_bulk_mutations(execute_state: Any) -> None:
    """Cover ORM bulk UPDATE/DELETE statements that bypass normal flush tracking."""
    if not (execute_state.is_update or execute_state.is_delete):
        return

    table = getattr(execute_state.statement, "table", None)
    table_name = getattr(table, "name", None)
    if not table_name or table_name == AuditLog.__tablename__:
        return

    operation = "update" if execute_state.is_update else "delete"
    if table_name == "game_servers" and operation == "update":
        values = getattr(execute_state.statement, "_values", None) or {}
        changed_fields = {getattr(key, "key", str(key)) for key in values}
        if changed_fields and changed_fields.issubset({"last_heartbeat", "player_count"}):
            return

    label = ENTITY_LABELS.get(table_name, table_name.replace("_", " ").rstrip("s"))
    context = _request_metadata()
    execute_state.session.add(AuditLog(
        timestamp=datetime.now(timezone.utc),
        user_id=context.get("user_id"),
        user_role=context.get("user_role") or "System",
        action_type=operation,
        action=f"{'Updated' if operation == 'update' else 'Deleted'} {label}",
        entity_type=label,
        request_method=context.get("request_method"),
        request_path=context.get("request_path"),
        ip_address=context.get("ip_address"),
    ))
    context["tracked_changes"] = context.get("tracked_changes", 0) + 1


def record_activity(
    *,
    user_id: int | None,
    user_role: str,
    action_type: str,
    action: str,
    entity_type: str | None = None,
    entity_id: str | None = None,
    request_method: str | None = None,
    request_path: str | None = None,
    ip_address: str | None = None,
) -> None:
    """Persist a request-level event that does not necessarily touch an ORM model."""
    db.session.add(AuditLog(
        timestamp=datetime.now(timezone.utc),
        user_id=user_id,
        user_role=user_role or "System",
        action_type=action_type,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        request_method=request_method,
        request_path=request_path,
        ip_address=ip_address,
    ))


def classify_read(path: str) -> tuple[str, str] | None:
    normalized = path.strip("/").split("?")[0]
    if normalized == "api/admin/activity-logs":
        return None
    if normalized.startswith((
        "health", "auth/", "docs", "openapi", "client/", "updates/", "ping",
        "api/admin/dashboard/analytics",
    )):
        return None
    if normalized.startswith("api/admin/users"):
        return "user account", "Viewed user accounts"
    if normalized.startswith("api/admin/password-reset-requests"):
        return "password reset request", "Viewed password reset requests"
    if normalized.startswith(("api/admin/classes", "api/admin/class-assignment", "teacher/class", "student/class")):
        return "class", "Viewed class information"
    if normalized.startswith(("teacher/quiz", "student/quiz", "student/location-quizzes")):
        return "quiz", "Viewed quiz information"
    if normalized.startswith(("teacher/announcement",)):
        return "announcement", "Viewed announcements"
    if normalized.startswith("teacher/lobby"):
        return "game lobby", "Viewed game lobbies"
    if normalized.startswith("user/game-history"):
        return "game activity", "Viewed game history"
    if normalized.startswith(("parent/", "student/parent-link-code")):
        return "family account", "Viewed family account information"
    if normalized.startswith(("teacher/student", "user/profile", "user/", "student/notifications")):
        return "user account", "Viewed account information"
    if normalized.startswith(("teacher/message", "api/messages", "parent/message")):
        return "message", "Viewed messages"
    if normalized.startswith("ws/lobby"):
        return "game lobby", "Viewed game lobbies"
    if normalized.startswith(("api/admin/", "teacher/", "parent/", "student/", "user/")):
        return "platform resource", "Viewed platform information"
    return None


def classify_request_action(method: str, path: str) -> tuple[str, str, str | None, str | None] | None:
    """Describe successful requests whose effects may live outside the ORM."""
    normalized = path.strip("/").split("?")[0]
    parts = normalized.split("/")
    method = method.upper()
    if method in {"GET", "HEAD", "OPTIONS"}:
        return None
    if normalized.startswith("auth/") and normalized != "auth/change-password":
        return None

    entity_type = None
    if "lobby" in parts:
        entity_type = "game lobby"
    elif "quiz" in parts:
        entity_type = "quiz"
    elif "announcement" in parts:
        entity_type = "announcement"
    elif "message" in parts or "messages" in parts:
        entity_type = "message"
    elif "class" in parts:
        entity_type = "class"
    elif "user" in parts or "profile" in parts or "parent-link" in normalized:
        entity_type = "user account"
    elif "mission" in parts:
        entity_type = "mission progress"

    if normalized == "auth/change-password":
        return "update", "Changed account password", "user account", None
    if entity_type is None:
        return None

    if method == "POST" and entity_type == "game lobby" and "start" in normalized:
        action_type, action = "update", "Started game lobby"
    else:
        action_type = {"POST": "create", "PUT": "update", "PATCH": "update", "DELETE": "delete"}.get(method)
        if action_type is None:
            return None
        verb = {"create": "Created", "update": "Updated", "delete": "Deleted"}[action_type]
        action = f"{verb} {entity_type}" if entity_type else f"Completed {method} action"

    entity_id = next((part for part in reversed(parts) if part.isdigit()), None)
    return action_type, action, entity_type, entity_id
