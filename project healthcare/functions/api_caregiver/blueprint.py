"""HTTP: caregiver/family linking, scoped view, and alert acknowledgment
(docs/BLUEPRINT.md Part 6.E, Part 8.3).

A patient links a caregiver by contact (creates an unclaimed, invited row);
the caregiver "accepts" the invitation by calling POST /caregivers/claim with
that same contact, binding their verified identity to it — claiming IS the
accept step. accessScope controls what the caregiver can SEE (vitals /
adherence / alerts); accessLevel controls whether they can additionally
WRITE (view_only vs full) — see require_caregiver_write_access.
"""
from datetime import datetime, timedelta, timezone

import azure.functions as func

from shared import db
from shared.alerts.rules import acknowledge
from shared.audit import audit_read, audit_write
from shared.audit import record as audit_record
from shared.auth import (
    BadRequest,
    error_response,
    get_principal,
    parse_int_param,
    require_caregiver_write_access,
    require_own_patient,
    require_role,
)
from shared.claims import claim_by_contact
from shared.ratelimit import enforce_claim_attempts
from shared.responses import error, not_found, success
from shared.validation import require_choice, require_string

bp = func.Blueprint()

_ACCESS_LEVELS = ("view_only", "full")


@bp.route(route="caregivers", methods=["POST"])
def link_caregiver(req: func.HttpRequest) -> func.HttpResponse:
    try:
        principal = require_role(req, "patient")
        try:
            body = req.get_json()
        except ValueError:
            raise BadRequest("Invalid JSON body")

        name = require_string(body.get("name"), "name")
        contact = require_string(body.get("contact"), "contact")
        relationship = require_string(body.get("relationship", "family"), "relationship", max_length=50)
        access_scope = require_string(body.get("accessScope", "vitals,adherence,alerts"), "accessScope", max_length=200)
        access_level = body.get("accessLevel", "view_only")
        require_choice(access_level, "accessLevel", _ACCESS_LEVELS)

        caregiver_id = db.execute_returning_id(
            """
            INSERT INTO Caregivers (name, contact, patientId, relationship, accessScope, accessLevel)
            OUTPUT INSERTED.id
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (name, contact, principal.patient_id, relationship, access_scope, access_level),
        )
        audit_record(principal, action="link_caregiver", target_type="Caregiver", target_id=caregiver_id)
        return success({"id": caregiver_id}, status_code=201)
    except Exception as exc:
        return error_response(exc)


@bp.route(route="caregivers/claim", methods=["POST"])
def claim_caregiver(req: func.HttpRequest) -> func.HttpResponse:
    """A freshly-signed-in principal (not yet onboarded — role may be None)
    accepts an invited-but-unclaimed Caregivers row by the contact info on
    file for it, binding it to their identity. Required before that
    principal can use caregiver_view for the linked patient. Deliberately
    calls get_principal directly (not require_role): a not-yet-onboarded
    principal has no role yet, so require_role would 403 them before they
    could ever accept the invitation."""
    try:
        principal = get_principal(req)
        enforce_claim_attempts(principal.user_id)
        try:
            body = req.get_json()
        except ValueError:
            raise BadRequest("Invalid JSON body")

        contact = body.get("contact")
        if not contact:
            raise BadRequest("contact is required")

        try:
            result = claim_by_contact(principal, table="Caregivers", owner_col="principalUserId", contact=contact)
        except Exception:
            audit_record(principal, action="claim_caregiver", target_type="Caregiver", target_id="unknown", outcome="denied", phi_accessed=False)
            raise
        audit_record(principal, action="claim_caregiver", target_type="Caregiver", target_id=result["id"])
        return success(result)
    except Exception as exc:
        return error_response(exc)


@bp.route(route="caregivers/me/patients", methods=["GET"])
def list_my_linked_patients(req: func.HttpRequest) -> func.HttpResponse:
    """The signed-in caregiver's own linked patients — the real-auth
    counterpart to a patient's principal.patientId. A caregiver's identity
    carries no single patientId (they may be linked to several), so the
    frontend calls this to find out which patient(s) they can view, instead
    of assuming one."""
    try:
        principal = require_role(req, "caregiver")
        rows = db.query(
            """
            SELECT p.id, p.name, p.dob, p.sex, c.accessScope, c.accessLevel, latest.score AS overallScore
            FROM Caregivers c
            JOIN Patients p ON p.id = c.patientId
            OUTER APPLY (
                SELECT TOP 1 score FROM RiskScores
                WHERE patientId = p.id AND area = 'overall'
                ORDER BY computedAt DESC
            ) latest
            WHERE c.principalUserId = ?
            ORDER BY p.name
            """,
            (principal.user_id,),
        )
        return success(rows)
    except Exception as exc:
        return error_response(exc)


@bp.route(route="caregivers/{patientId}", methods=["GET"])
def list_caregivers(req: func.HttpRequest) -> func.HttpResponse:
    try:
        principal = require_role(req, "patient", "provider", "caregiver")
        patient_id = parse_int_param(req, "patientId")
        require_own_patient(principal, patient_id)
        audit_read(req, "Caregiver", patient_id, action="list_caregivers")
        rows = db.query(
            """
            SELECT id, name, contact, relationship, accessScope, accessLevel,
                   CASE WHEN principalUserId IS NOT NULL THEN 1 ELSE 0 END AS accepted
            FROM Caregivers WHERE patientId = ?
            """,
            (patient_id,),
        )
        return success(rows)
    except Exception as exc:
        return error_response(exc)


@bp.route(route="caregiver-view/{patientId}", methods=["GET"])
def caregiver_view(req: func.HttpRequest) -> func.HttpResponse:
    try:
        principal = require_role(req, "caregiver")
        patient_id = parse_int_param(req, "patientId")
        caregiver = db.query_one(
            "SELECT accessScope, accessLevel FROM Caregivers WHERE patientId = ? AND principalUserId = ?",
            (patient_id, principal.user_id),
        )
        if not caregiver:
            return error("FORBIDDEN", "No claimed caregiver link for this patient", 403)

        scope = set((caregiver["accessScope"] or "").split(","))
        payload: dict = {"patientId": patient_id, "accessScope": sorted(scope), "accessLevel": caregiver["accessLevel"]}

        if "vitals" in scope:
            since = datetime.now(timezone.utc) - timedelta(days=7)
            payload["recentVitals"] = db.query(
                "SELECT type, value, unit, recordedAt FROM Vitals WHERE patientId = ? AND recordedAt >= ? ORDER BY recordedAt DESC",
                (patient_id, since),
            )
        if "adherence" in scope:
            payload["adherence"] = db.query(
                """
                SELECT m.name, m.schedule,
                  SUM(CASE WHEN a.status = 'taken' THEN 1 ELSE 0 END) AS taken,
                  COUNT(*) AS total
                FROM Medications m JOIN AdherenceLog a ON a.medicationId = m.id
                WHERE m.patientId = ? GROUP BY m.name, m.schedule
                """,
                (patient_id,),
            )
        if "alerts" in scope:
            payload["alerts"] = db.query(
                "SELECT id, kind, detail, value, raisedAt, acknowledgedBy FROM Alerts WHERE patientId = ? ORDER BY raisedAt DESC",
                (patient_id,),
            )

        audit_record(principal, action="caregiver_view", target_type="Patient", target_id=patient_id)
        return success(payload)
    except Exception as exc:
        return error_response(exc)


@bp.route(route="alerts/{alertId}/acknowledge", methods=["POST"])
def acknowledge_alert(req: func.HttpRequest) -> func.HttpResponse:
    try:
        principal = require_role(req, "patient", "caregiver", "provider")
        alert_id = parse_int_param(req, "alertId")
        alert = db.query_one("SELECT patientId FROM Alerts WHERE id = ?", (alert_id,))
        if not alert:
            return not_found("Alert")
        require_own_patient(principal, alert["patientId"])
        require_caregiver_write_access(principal, alert["patientId"])
        acknowledge(alert_id, principal.user_id)
        audit_write(req, "Alert", alert_id, action="acknowledge_alert")
        return success({"id": alert_id, "acknowledgedBy": principal.user_id})
    except Exception as exc:
        return error_response(exc)
