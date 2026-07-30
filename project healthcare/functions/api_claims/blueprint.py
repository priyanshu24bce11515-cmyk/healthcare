"""HTTP: clinical note -> insurance claim assistant (docs/BLUEPRINT.md Part
6.G, Part 8.5). Full workflow: draft -> ready -> submitted -> processing ->
approved | denied, with every transition recorded in ClaimStatusHistory.
"""
import json

import azure.functions as func

from shared import db
from shared.audit import audit_read, audit_write
from shared.audit import record as audit_record
from shared.auth import (
    BadRequest,
    error_response,
    parse_int_param,
    require_own_patient,
    require_role,
)
from shared.nlp.claim_extract import create_claim_from_note
from shared.responses import not_found, success
from shared.validation import require_string

bp = func.Blueprint()

_STATUSES = ("draft", "ready", "submitted", "processing", "approved", "denied")

# A denied claim's guidance is looked up by keyword found in the denial
# reason text — real adjudication systems return structured reason codes;
# this is a pragmatic keyword match for a synthetic-data demo.
_DENIAL_GUIDANCE = {
    "missing": "Missing information denials are usually the fastest to appeal — resubmit with the specific field the payer flagged (see denialReason) attached.",
    "not covered": "A 'not covered' denial often means the wrong diagnosis/procedure code was used, or the service needs prior authorization — check the code against the payer's policy before appealing.",
    "duplicate": "Duplicate-claim denials mean this service was already billed — confirm no earlier submission exists before resubmitting.",
    "authorization": "This service required prior authorization that wasn't on file — obtain and attach the authorization number, then resubmit.",
    "timely filing": "The claim was submitted after the payer's filing deadline — an appeal must include documentation of a valid exception (e.g. delayed notification of coverage).",
}


def _denial_guidance(reason: str | None) -> str | None:
    if not reason:
        return None
    lowered = reason.lower()
    for keyword, guidance in _DENIAL_GUIDANCE.items():
        if keyword in lowered:
            return guidance
    return "Review the denial reason against the payer's policy and resubmit with supporting documentation, or contact the payer for clarification."


def _record_history(claim_id: int, status: str, changed_by: str, note: str | None = None) -> None:
    db.execute(
        "INSERT INTO ClaimStatusHistory (claimId, status, changedBy, note) VALUES (?, ?, ?, ?)",
        (claim_id, status, changed_by, note),
    )


@bp.route(route="claims", methods=["POST"])
def create_claim(req: func.HttpRequest) -> func.HttpResponse:
    try:
        principal = require_role(req, "provider")
        try:
            body = req.get_json()
        except ValueError:
            raise BadRequest("Invalid JSON body")

        patient_id = body.get("patientId")
        provider = require_string(body.get("provider"), "provider")
        note_text = require_string(body.get("noteText"), "noteText", max_length=5000)
        amount = body.get("amount")

        if not patient_id:
            raise BadRequest("patientId is required")

        claim = create_claim_from_note(patient_id, provider, note_text, amount)
        _record_history(claim["id"], claim["status"], principal.user_id, note="Created from clinical note")
        audit_record(principal, action="create_claim", target_type="Claim", target_id=claim["id"])
        return success(claim, status_code=201)
    except Exception as exc:
        return error_response(exc)


@bp.route(route="claims/{patientId}", methods=["GET"])
def list_claims(req: func.HttpRequest) -> func.HttpResponse:
    try:
        principal = require_role(req, "patient", "provider")
        patient_id = parse_int_param(req, "patientId")
        require_own_patient(principal, patient_id)
        audit_read(req, "Claim", patient_id, action="list_claims")
        rows = db.query(
            "SELECT id, provider, amount, diagnosisCodes, status, denialReason, extractedFields, missingFields FROM Claims WHERE patientId = ? ORDER BY id DESC",
            (patient_id,),
        )
        for r in rows:
            r["extractedFields"] = json.loads(r["extractedFields"]) if r["extractedFields"] else {}
            r["missingFields"] = json.loads(r["missingFields"]) if r["missingFields"] else []
            if r["status"] == "denied":
                r["appealGuidance"] = _denial_guidance(r["denialReason"])
        return success(rows)
    except Exception as exc:
        return error_response(exc)


@bp.route(route="claims/{claimId}/history", methods=["GET"])
def get_claim_history(req: func.HttpRequest) -> func.HttpResponse:
    try:
        principal = require_role(req, "patient", "provider")
        claim_id = parse_int_param(req, "claimId")
        claim = db.query_one("SELECT patientId FROM Claims WHERE id = ?", (claim_id,))
        if not claim:
            return not_found("Claim")
        require_own_patient(principal, claim["patientId"])
        rows = db.query(
            "SELECT status, changedBy, changedAt, note FROM ClaimStatusHistory WHERE claimId = ? ORDER BY changedAt ASC",
            (claim_id,),
        )
        return success(rows)
    except Exception as exc:
        return error_response(exc)


@bp.route(route="claims/{claimId}/status", methods=["PATCH"])
def update_claim_status(req: func.HttpRequest) -> func.HttpResponse:
    try:
        principal = require_role(req, "provider")
        claim_id = parse_int_param(req, "claimId")
        claim = db.query_one("SELECT id FROM Claims WHERE id = ?", (claim_id,))
        if not claim:
            return not_found("Claim")
        try:
            body = req.get_json()
        except ValueError:
            body = {}
        status = body.get("status")
        if status not in _STATUSES:
            raise BadRequest(f"status must be one of: {', '.join(_STATUSES)}")
        denial_reason = body.get("denialReason")
        if status == "denied" and not denial_reason:
            raise BadRequest("denialReason is required when status is 'denied'")

        if denial_reason is not None:
            denial_reason = require_string(denial_reason, "denialReason", max_length=500, required=False)
            db.execute("UPDATE Claims SET status = ?, denialReason = ? WHERE id = ?", (status, denial_reason, claim_id))
        else:
            db.execute("UPDATE Claims SET status = ? WHERE id = ?", (status, claim_id))

        _record_history(claim_id, status, principal.user_id, note=denial_reason)
        audit_write(req, "Claim", claim_id, action=f"claim_status:{status}")

        result = {"id": claim_id, "status": status}
        if status == "denied":
            result["appealGuidance"] = _denial_guidance(denial_reason)
        return success(result)
    except Exception as exc:
        return error_response(exc)
