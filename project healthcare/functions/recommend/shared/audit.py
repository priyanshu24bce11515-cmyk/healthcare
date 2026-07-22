"""Writes sensitive actions to AuditLog (docs/BLUEPRINT.md Part 6, Part 10).

HIPAA requires an audit trail of every PHI access: who, what, when, from
where, and the outcome. Writes happen on a background worker thread so they
never add latency to the request path — if the queue is full or the worker
is unavailable, the write falls back to synchronous so an audit event is
never silently dropped.

AuditLog is append-only (enforced by a DB trigger — see infra/sql/schema.sql):
there is intentionally no update or delete helper here.
"""
import logging
import queue
import threading
from datetime import datetime, timezone

import azure.functions as func

from . import db
from .auth import Principal, get_caller_info

_QUEUE_MAX = 1000
_audit_queue: "queue.Queue[tuple]" = queue.Queue(maxsize=_QUEUE_MAX)
_worker_started = False
_worker_lock = threading.Lock()


def _write_row(row: tuple) -> None:
    db.execute(
        """
        INSERT INTO AuditLog (actorId, role, action, targetType, targetId, ipAddress, userAgent, phiAccessed, outcome, at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        row,
    )


def _worker() -> None:
    while True:
        row = _audit_queue.get()
        try:
            _write_row(row)
        except Exception as exc:  # never let the audit worker die
            logging.error("audit: background write failed: %s", exc)
        finally:
            _audit_queue.task_done()


def _ensure_worker() -> None:
    global _worker_started
    if _worker_started:
        return
    with _worker_lock:
        if not _worker_started:
            threading.Thread(target=_worker, name="audit-writer", daemon=True).start()
            _worker_started = True


def record(
    actor: Principal,
    action: str,
    target_type: str,
    target_id,
    *,
    phi_accessed: bool = True,
    outcome: str = "success",
    ip: str | None = None,
    user_agent: str | None = None,
) -> None:
    """Queue an audit event (non-blocking). Kept backward-compatible with the
    original (actor, action, target_type, target_id) signature; the context
    kwargs default sensibly for the write-path callers that don't pass them."""
    # role can be None for a not-yet-onboarded principal claiming its first
    # role — AuditLog.role is NOT NULL, so substitute a sentinel.
    role = (actor.role if actor else None) or "unonboarded"
    actor_id = actor.user_id if actor else "anonymous"
    row = (
        actor_id,
        role,
        action,
        target_type,
        str(target_id),
        ip,
        user_agent,
        1 if phi_accessed else 0,
        outcome,
        datetime.now(timezone.utc),
    )
    _ensure_worker()
    try:
        _audit_queue.put_nowait(row)
    except queue.Full:
        # Never drop an audit event — block the request rather than lose it.
        logging.warning("audit: queue full, writing synchronously")
        try:
            _write_row(row)
        except Exception as exc:
            logging.error("audit: synchronous write failed: %s", exc)


# --- Convenience helpers that resolve caller context straight from the request ---


def _record_from_req(
    req: func.HttpRequest, action: str, target_type: str, target_id, *, phi_accessed: bool, outcome: str
) -> None:
    info = get_caller_info(req)
    principal = Principal(user_id=info["user_id"], role=info["role"], patient_id=info["patient_id"])
    record(
        principal,
        action,
        target_type,
        target_id,
        phi_accessed=phi_accessed,
        outcome=outcome,
        ip=info["ip"],
        user_agent=info["user_agent"],
    )


def audit_read(req: func.HttpRequest, target_type: str, target_id, action: str | None = None) -> None:
    """Record a PHI read. Call in every route that returns patient data."""
    _record_from_req(req, action or f"read_{target_type}", target_type, target_id, phi_accessed=True, outcome="success")


def audit_write(req: func.HttpRequest, target_type: str, target_id, action: str | None = None) -> None:
    """Record a PHI create/update."""
    _record_from_req(req, action or f"write_{target_type}", target_type, target_id, phi_accessed=True, outcome="success")


def audit_denied(req: func.HttpRequest, target_type: str, target_id, action: str | None = None) -> None:
    """Record a denied access attempt (403/401) — HIPAA wants failed access logged too."""
    _record_from_req(req, action or f"denied_{target_type}", target_type, target_id, phi_accessed=False, outcome="denied")
