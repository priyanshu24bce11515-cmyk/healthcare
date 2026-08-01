"""HTTP: audit log read access, provider only (docs/BLUEPRINT.md Part 10).

Filterable by targetType, targetId, action (substring), and date range;
paginated (never returns the whole table). Querying the audit log is itself
an auditable action — HIPAA compliance reporting needs to know who looked at
the audit trail, too.
"""
from datetime import datetime

import azure.functions as func

from shared import db
from shared.audit import audit_read
from shared.auth import BadRequest, error_response, require_role
from shared.responses import paginate_params, paginated, success

bp = func.Blueprint()


def _parse_date(value: str | None, field: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        raise BadRequest(f"{field} must be an ISO-8601 date/datetime")


@bp.route(route="audit", methods=["GET"])
def list_audit_log(req: func.HttpRequest) -> func.HttpResponse:
    try:
        require_role(req, "provider")
        page, page_size = paginate_params(req, default_page_size=50, max_page_size=500)

        target_type = req.params.get("targetType")
        target_id = req.params.get("targetId")
        action = req.params.get("action")
        date_from = _parse_date(req.params.get("from"), "from")
        date_to = _parse_date(req.params.get("to"), "to")

        clauses = []
        params: list = []
        if target_type:
            clauses.append("targetType = ?")
            params.append(target_type)
        if target_id:
            clauses.append("targetId = ?")
            params.append(target_id)
        if action:
            clauses.append("action LIKE ?")
            params.append(f"%{action}%")
        if date_from:
            clauses.append("at >= ?")
            params.append(date_from)
        if date_to:
            clauses.append("at <= ?")
            params.append(date_to)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""

        total = db.query_one(f"SELECT COUNT(*) AS n FROM AuditLog {where}", params)["n"]
        offset = (page - 1) * page_size
        rows = db.query(
            f"""
            SELECT id, actorId, role, action, targetType, targetId, ipAddress, phiAccessed, outcome, at
            FROM AuditLog {where}
            ORDER BY at DESC
            OFFSET ? ROWS FETCH NEXT ? ROWS ONLY
            """,
            [*params, offset, page_size],
        )

        # Querying the audit trail is itself audited (HIPAA compliance
        # reporting needs to know who reviewed access history).
        audit_read(req, "AuditLog", target_id or "all", action="query_audit_log")

        return success(paginated(rows, total, page, page_size))
    except Exception as exc:
        return error_response(exc)
