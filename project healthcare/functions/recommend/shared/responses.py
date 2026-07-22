"""Consistent API response envelope for every route.

Success: {"data": ..., "meta": {"timestamp": <UTC ISO>, "requestId": <uuid>, ...}}
Error:   {"error": {"code": "SHORT_CODE", "message": "human readable"},
          "meta": {"timestamp": <UTC ISO>, "requestId": <uuid>}}

No route should ever return HTTP 200 with an error in the body, and no route
should ever leak a raw exception/stack trace to the client — error() and the
exception-driven helper in auth.error_response both go through here.
"""
import json
import uuid
from datetime import datetime, timezone
from math import ceil
from typing import Any

import azure.functions as func


def _meta(**extra) -> dict:
    meta = {"timestamp": datetime.now(timezone.utc).isoformat(), "requestId": str(uuid.uuid4())}
    meta.update(extra)
    return meta


# Applied to every response this API returns. Cache-Control: no-store is the
# one that matters most here — every route in this app returns PHI (even a
# 403 body can echo back an identifier), so nothing should ever be cached by
# a shared proxy or the browser's back/forward cache. The rest is
# defense-in-depth for the case where this Function App is reached directly
# rather than through Static Web Apps/APIM's own header layer.
_SECURITY_HEADERS = {
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
}


def _json_response(body: dict, status_code: int) -> func.HttpResponse:
    return func.HttpResponse(
        json.dumps(body, default=str),
        status_code=status_code,
        mimetype="application/json",
        headers=_SECURITY_HEADERS,
    )


def success(data: Any, status_code: int = 200, **meta_extra) -> func.HttpResponse:
    body = {"data": data, "meta": _meta(**meta_extra)}
    return _json_response(body, status_code)


def error(code: str, message: str, status_code: int) -> func.HttpResponse:
    body = {"error": {"code": code, "message": message}, "meta": _meta()}
    return _json_response(body, status_code)


def not_found(resource: str) -> func.HttpResponse:
    return error("NOT_FOUND", f"{resource} not found", 404)


def paginate_params(req: func.HttpRequest, default_page_size: int = 20, max_page_size: int = 100) -> tuple[int, int]:
    """Reads ?page & ?pageSize (1-indexed), clamped to sane bounds."""
    try:
        page = max(1, int(req.params.get("page", 1)))
    except (TypeError, ValueError):
        page = 1
    try:
        page_size = int(req.params.get("pageSize", default_page_size))
    except (TypeError, ValueError):
        page_size = default_page_size
    page_size = max(1, min(max_page_size, page_size))
    return page, page_size


def paginated(items: list, total_count: int, page: int, page_size: int) -> dict:
    return {
        "items": items,
        "meta": {
            "page": page,
            "pageSize": page_size,
            "totalCount": total_count,
            "totalPages": ceil(total_count / page_size) if page_size else 0,
            "hasMore": page * page_size < total_count,
        },
    }
