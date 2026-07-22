"""Role-based access helpers.

Reads the client principal that Azure Static Web Apps / API Management inject
as the `x-ms-client-principal` header (base64 JSON) after AD B2C sign-in.
For local dev without B2C wired up yet, falls back to `x-demo-principal`
(same shape) so Functions can be exercised directly.
"""
import base64
import json
import logging
from dataclasses import dataclass

import azure.functions as func

from . import config, db


@dataclass
class Principal:
    user_id: str
    role: str | None  # patient | provider | caregiver | None (verified identity, not yet onboarded)
    patient_id: int | None  # for patients: their own id; for caregivers: linked patient id


class Unauthorized(Exception):
    pass


class Forbidden(Exception):
    pass


class NotOnboarded(Forbidden):
    """Identity is verified, but not yet linked to a Patient/Provider/Caregiver
    row — distinct from Forbidden (valid role, wrong resource) so the frontend
    can show an onboarding/claim screen instead of a generic access-denied."""


class RateLimited(Forbidden):
    """Caller exceeded an attempt budget on a sensitive, pre-authorization
    endpoint (see shared/ratelimit.py) — distinct from Forbidden so it maps
    to 429, not 403."""


class BadRequest(Exception):
    pass


def parse_int_param(req: func.HttpRequest, name: str) -> int:
    raw = req.route_params.get(name)
    try:
        return int(raw)
    except (TypeError, ValueError):
        raise BadRequest(f"'{name}' must be an integer, got: {raw!r}")


_ROLE_PRECEDENCE = ("provider", "caregiver", "patient")


def _resolve_role(payload: dict, claims: dict) -> str | None:
    """Picks the caller's role deterministically instead of trusting array
    order. Rejects principals carrying more than one recognized role rather
    than silently guessing which one applies."""
    user_roles = payload.get("userRoles") or []
    recognized = [r for r in user_roles if r in _ROLE_PRECEDENCE]
    if len(recognized) > 1:
        raise Unauthorized("Client principal has more than one recognized role")
    if recognized:
        return recognized[0]
    return claims.get("role")


def resolve_role_from_db(user_id: str) -> tuple[str | None, int | None]:
    """Resolves role (+ patient_id, for patients) purely from the database.
    Needed for identities that don't carry an app-specific role claim at all
    — e.g. a real Entra External ID / AD B2C token, which proves *who* someone
    is but knows nothing about this app's patient/provider/caregiver concept.
    Checked in this order because it mirrors the exclusivity rule enforced by
    the claim endpoints: a person is a patient OR a provider OR a caregiver,
    never more than one (see shared/claims.py)."""
    patient = db.query_one("SELECT id FROM Patients WHERE b2cObjectId = ?", (user_id,))
    if patient:
        return "patient", patient["id"]
    provider = db.query_one("SELECT id FROM Providers WHERE principalUserId = ?", (user_id,))
    if provider:
        return "provider", None
    caregiver = db.query_one("SELECT id FROM Caregivers WHERE principalUserId = ?", (user_id,))
    if caregiver:
        return "caregiver", None
    return None, None


def _decode_bearer_payload_unverified(authorization: str) -> dict | None:
    """DEV ONLY (gated behind ALLOW_DEMO_PRINCIPAL): extracts the claims from
    a raw `Authorization: Bearer` JWT WITHOUT verifying its signature. Locally
    there is no EasyAuth in front of the Functions host to validate the token
    and inject x-ms-client-principal, so this lets the real MSAL sign-in flow
    be exercised end-to-end in local dev. Adds no new local risk — the same
    flag already accepts a fully forgeable x-demo-principal header. In every
    deployed environment the flag is off and only the EasyAuth-injected
    x-ms-client-principal (signature-verified upstream) is trusted."""
    if not authorization.lower().startswith("bearer "):
        return None
    token = authorization[7:].strip()
    parts = token.split(".")
    if len(parts) != 3:
        return None
    try:
        segment = parts[1] + "=" * (-len(parts[1]) % 4)
        claims = json.loads(base64.urlsafe_b64decode(segment))
    except Exception:
        return None
    user_id = claims.get("oid") or claims.get("sub")
    if not user_id:
        return None
    return {"userId": user_id}


def _safe_int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def get_principal(req: func.HttpRequest) -> Principal:
    header = req.headers.get("x-ms-client-principal")
    payload: dict | None = None
    # Dev-only escape hatches (unsigned demo header / unverified bearer) are
    # hard-blocked in production regardless of ALLOW_DEMO_PRINCIPAL — belt and
    # suspenders on top of the flag defaulting off in deployed environments.
    if not header and config.ALLOW_DEMO_PRINCIPAL and not config.IS_PRODUCTION:
        header = req.headers.get("x-demo-principal")
        if not header:
            payload = _decode_bearer_payload_unverified(req.headers.get("authorization", ""))
    if not header and payload is None:
        raise Unauthorized("Missing client principal")

    if payload is None:
        try:
            payload = json.loads(base64.b64decode(header))
        except Exception as exc:
            raise Unauthorized("Invalid client principal") from exc

    claims = {c.get("typ"): c.get("val") for c in payload.get("claims", [])} if "claims" in payload else payload

    user_id = payload.get("userId") or claims.get("sub") or claims.get("userId")
    if not user_id:
        raise Unauthorized("Client principal missing user_id")

    role = _resolve_role(payload, claims)
    patient_id = _safe_int(payload.get("patientId") or claims.get("patientId"))

    if not role:
        # No app-specific role claim present (the normal case for a real
        # Entra External ID token) — fall back to the database. role may
        # still come back None: a verified identity with no linked record
        # yet ("not onboarded"), which is not an authentication failure.
        role, patient_id = resolve_role_from_db(user_id)
    elif role != "patient":
        # A provider/caregiver has no single "own" patient — never carry a
        # stray patientId for them (it must not be usable as an ownership key).
        patient_id = None

    return Principal(user_id=user_id, role=role, patient_id=patient_id)


def get_caller_info(req: func.HttpRequest) -> dict:
    """Everything the audit trail needs about the caller, resolved once so
    routes don't each reconstruct it. Never raises — audit must work even for
    a request that is about to be denied (that denial is itself auditable)."""
    try:
        principal = get_principal(req)
        user_id, role, patient_id = principal.user_id, principal.role, principal.patient_id
    except Exception:
        user_id, role, patient_id = "anonymous", None, None
    # X-Forwarded-For is set by APIM / Static Web Apps; first hop is the client.
    fwd = req.headers.get("x-forwarded-for", "")
    ip = fwd.split(",")[0].strip() if fwd else req.headers.get("x-client-ip", "unknown")
    return {
        "user_id": user_id,
        "role": role,
        "patient_id": patient_id,
        "ip": ip,
        "user_agent": req.headers.get("user-agent", "unknown"),
    }


def require_role(req: func.HttpRequest, *allowed_roles: str) -> Principal:
    principal = get_principal(req)
    if principal.role is None:
        raise NotOnboarded("Account not yet linked to a patient, provider, or caregiver record")
    if principal.role not in allowed_roles:
        raise Forbidden(f"Role '{principal.role}' not permitted, requires one of {allowed_roles}")
    return principal


def caregiver_linked(principal: Principal, patient_id: int) -> bool:
    """True only if this exact caregiver principal has claimed a Caregivers
    link to this patient (see api_caregiver.claim_caregiver) — a caregiver
    role alone is not enough."""
    row = db.query_one(
        "SELECT id FROM Caregivers WHERE patientId = ? AND principalUserId = ?",
        (patient_id, principal.user_id),
    )
    return row is not None


def require_own_patient(principal: Principal, patient_id: int) -> None:
    """Enforces that a request scoped to `patient_id` is actually allowed for
    this principal: patients may only touch their own record, caregivers only
    a patient they've claimed a link to. Providers are unrestricted — the
    schema has no provider-patient assignment table (an accepted, documented
    scope limit, not an oversight)."""
    if principal.role == "patient" and principal.patient_id != patient_id:
        raise Forbidden("Patients may only access their own records")
    if principal.role == "caregiver" and not caregiver_linked(principal, patient_id):
        raise Forbidden("Caregiver is not linked to this patient")


def require_caregiver_write_access(principal: Principal, patient_id: int) -> None:
    """For write operations (acknowledge an alert, book/cancel appointments,
    manage medications) a caregiver additionally needs accessLevel='full' —
    a 'view_only' caregiver can see the scoped dashboard but cannot act on
    the patient's behalf. No-op for patient/provider roles (this call is
    meant to sit alongside require_own_patient, only adding a stricter bar
    for the caregiver case)."""
    if principal.role != "caregiver":
        return
    row = db.query_one(
        "SELECT accessLevel FROM Caregivers WHERE patientId = ? AND principalUserId = ?",
        (patient_id, principal.user_id),
    )
    if not row or row["accessLevel"] != "full":
        raise Forbidden("This caregiver has view-only access and cannot perform this action")


def error_response(exc: Exception) -> func.HttpResponse:
    # Local import: responses.py has no dependency on auth.py, but auth.py is
    # imported very early (by config-adjacent modules); keeping the import
    # here avoids any import-order fragility.
    from .responses import error

    if isinstance(exc, Unauthorized):
        return error("UNAUTHORIZED", str(exc), 401)
    if isinstance(exc, NotOnboarded):
        return error("NOT_ONBOARDED", str(exc), 403)
    if isinstance(exc, RateLimited):
        return error("RATE_LIMITED", str(exc), 429)
    if isinstance(exc, Forbidden):
        return error("FORBIDDEN", str(exc), 403)
    if isinstance(exc, BadRequest):
        return error("BAD_REQUEST", str(exc), 400)
    # Never leak the raw exception (stack trace, SQL error text, Azure
    # resource names, or any PHI it might reference) to the client.
    logging.exception("Unhandled error in request")
    return error("INTERNAL_ERROR", "Internal server error", 500)
