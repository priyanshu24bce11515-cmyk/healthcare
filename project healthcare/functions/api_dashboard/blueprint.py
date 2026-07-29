"""HTTP: patient dashboard, trends & analytics, provider patient roster
(docs/BLUEPRINT.md Part 6.H)."""
from datetime import datetime, timedelta, timezone

import azure.functions as func

from shared import config, db
from shared.audit import audit_read, audit_write
from shared.audit import record as audit_record
from shared.auth import (
    BadRequest,
    error_response,
    get_principal,
    parse_int_param,
    require_own_patient,
    require_role,
)
from shared.claims import claim_by_contact
from shared.ratelimit import enforce_claim_attempts
from shared.responses import not_found, paginate_params, paginated, success
from shared.scoring.risk_score import band as risk_band
from shared.validation import require_number, require_string

bp = func.Blueprint()

_VITAL_TYPES = ["heartRate", "steps", "sleep", "bp_systolic", "bp_diastolic", "glucose"]
_GOAL_KINDS = {"fitness", "nutrition"}
_RISK_BANDS = {"good", "moderate", "high", "critical"}
# Mirrors shared.scoring.risk_score.band() — kept here as SQL so a provider's
# roster can be filtered/paginated in the database instead of fetching every
# patient's scores into Python first. NULL (no score computed yet) must NOT
# fall into the ELSE branch — that would misclassify an un-scored patient as
# 'critical', which is both wrong and needlessly alarming.
_BAND_CASE_SQL = """
    CASE
        WHEN latest.score IS NULL THEN NULL
        WHEN latest.score >= 76 THEN 'good'
        WHEN latest.score >= 51 THEN 'moderate'
        WHEN latest.score >= 26 THEN 'high'
        ELSE 'critical'
    END
"""


def _latest_vitals(patient_id: int) -> dict:
    latest = {}
    for vtype in _VITAL_TYPES:
        if vtype in config.CUMULATIVE_VITAL_TYPES:
            # Steps arrive as many small per-tick readings from a live feed —
            # show today's running total, not the single latest raw reading.
            row = db.query_one(
                """
                SELECT SUM(value) AS value, MAX(unit) AS unit, MAX(recordedAt) AS recordedAt, MAX(source) AS source
                FROM Vitals
                WHERE patientId = ? AND type = ? AND CAST(recordedAt AS DATE) = CAST(SYSUTCDATETIME() AS DATE)
                """,
                (patient_id, vtype),
            )
            if row and row["value"] is not None:
                latest[vtype] = row
            continue

        row = db.query_one(
            "SELECT TOP 1 value, unit, recordedAt, source FROM Vitals WHERE patientId = ? AND type = ? ORDER BY recordedAt DESC",
            (patient_id, vtype),
        )
        if row:
            latest[vtype] = row
    return latest


def _trend(patient_id: int, since: datetime, until: datetime | None) -> dict:
    if until:
        rows = db.query(
            "SELECT type, value, recordedAt FROM Vitals WHERE patientId = ? AND recordedAt >= ? AND recordedAt <= ? ORDER BY recordedAt ASC",
            (patient_id, since, until),
        )
    else:
        rows = db.query(
            "SELECT type, value, recordedAt FROM Vitals WHERE patientId = ? AND recordedAt >= ? ORDER BY recordedAt ASC",
            (patient_id, since),
        )
    trend: dict[str, list[dict]] = {}
    for r in rows:
        trend.setdefault(r["type"], []).append({"value": r["value"], "recordedAt": r["recordedAt"]})
    return trend


def _latest_risk_scores(patient_id: int) -> dict:
    rows = db.query(
        """
        SELECT area, score, reason, computedAt FROM RiskScores r
        WHERE patientId = ? AND computedAt = (
          SELECT MAX(computedAt) FROM RiskScores WHERE patientId = r.patientId AND area = r.area
        )
        """,
        (patient_id,),
    )
    result = {}
    for r in rows:
        r["band"] = risk_band(r["score"])
        result[r["area"]] = r
    return result


def _goal_progress(patient_id: int, goal: dict) -> float:
    """Fitness goal progress is computed from actual recorded steps for the
    goal's period — not trusted from a manually-bumped counter. Nutrition has
    no automatic data source in this schema (no nutrition-log table), so it
    stays a patient-reported value."""
    if goal["kind"] != "fitness":
        return goal["progress"]
    period_days = {"daily": 1, "weekly": 7, "monthly": 30}.get(goal["period"], 7)
    since = datetime.now(timezone.utc) - timedelta(days=period_days)
    row = db.query_one(
        "SELECT SUM(value) AS total FROM Vitals WHERE patientId = ? AND type = 'steps' AND recordedAt >= ?",
        (patient_id, since),
    )
    return float(row["total"]) if row and row["total"] is not None else 0.0


@bp.route(route="dashboard/{patientId}", methods=["GET"])
def get_dashboard(req: func.HttpRequest) -> func.HttpResponse:
    try:
        principal = require_role(req, "patient", "provider", "caregiver")
        patient_id = parse_int_param(req, "patientId")
        require_own_patient(principal, patient_id)

        patient = db.query_one("SELECT id, name, dob, sex FROM Patients WHERE id = ?", (patient_id,))
        if not patient:
            return not_found("Patient")
        audit_read(req, "Patient", patient_id, action="view_dashboard")

        active_med_count = db.query_one(
            "SELECT COUNT(*) AS n FROM Medications WHERE patientId = ? AND (endDate IS NULL OR endDate >= CAST(SYSUTCDATETIME() AS DATE))",
            (patient_id,),
        )["n"]
        active_alert_count = db.query_one(
            "SELECT COUNT(*) AS n FROM Alerts WHERE patientId = ? AND acknowledgedAt IS NULL", (patient_id,)
        )["n"]

        payload = {
            "patient": patient,
            "latestVitals": _latest_vitals(patient_id),
            "riskScores": _latest_risk_scores(patient_id),
            "activeMedicationCount": active_med_count,
            "activeAlertCount": active_alert_count,
            "recommendations": db.query(
                """
                SELECT TOP 3 id, text, reason, category, priority, priorityScore, generatedAt, actedOn, dismissedAt
                FROM Recommendations WHERE patientId = ? AND dismissedAt IS NULL ORDER BY generatedAt DESC
                """,
                (patient_id,),
            ),
            "unacknowledgedAlerts": db.query(
                "SELECT id, kind, severity, detail, value, raisedAt FROM Alerts WHERE patientId = ? AND acknowledgedAt IS NULL ORDER BY raisedAt DESC",
                (patient_id,),
            ),
            "upcomingAppointments": db.query(
                """
                SELECT a.id, a.startsAt, a.type, a.status, p.name AS providerName, p.specialty
                FROM Appointments a JOIN Providers p ON p.id = a.providerId
                WHERE a.patientId = ? AND a.startsAt >= ? ORDER BY a.startsAt ASC
                """,
                (patient_id, datetime.now(timezone.utc)),
            ),
            "disclaimer": config.WELLNESS_DISCLAIMER,
        }
        return success(payload)
    except Exception as exc:
        return error_response(exc)


@bp.route(route="analytics/{patientId}", methods=["GET"])
def get_analytics(req: func.HttpRequest) -> func.HttpResponse:
    try:
        principal = require_role(req, "patient", "provider", "caregiver")
        patient_id = parse_int_param(req, "patientId")
        require_own_patient(principal, patient_id)
        audit_read(req, "Patient", patient_id, action="view_analytics")

        # Explicit ?from/?to date range takes precedence over ?days.
        from_param, to_param = req.params.get("from"), req.params.get("to")
        if from_param:
            try:
                since = datetime.fromisoformat(from_param)
            except ValueError:
                raise BadRequest("from must be an ISO-8601 date/datetime")
            until = datetime.fromisoformat(to_param) if to_param else datetime.now(timezone.utc)
        else:
            days = int(req.params.get("days", 30))
            since = datetime.now(timezone.utc) - timedelta(days=days)
            until = None

        goals = db.query("SELECT id, kind, target, progress, period FROM Goals WHERE patientId = ?", (patient_id,))
        for g in goals:
            g["progress"] = _goal_progress(patient_id, g)

        payload = {
            "vitalsTrend": _trend(patient_id, since, until),
            "riskScoreHistory": db.query(
                "SELECT area, score, computedAt FROM RiskScores WHERE patientId = ? AND computedAt >= ? ORDER BY computedAt ASC",
                (patient_id, since),
            ),
            "goals": goals,
        }
        return success(payload)
    except Exception as exc:
        return error_response(exc)


@bp.route(route="patients", methods=["GET"])
def list_patients(req: func.HttpRequest) -> func.HttpResponse:
    """Provider's patient roster — paginated, optionally filtered by the
    patient's current overall wellness band."""
    try:
        require_role(req, "provider")
        page, page_size = paginate_params(req)
        risk_band_filter = req.params.get("riskBand")
        if risk_band_filter and risk_band_filter not in _RISK_BANDS:
            raise BadRequest(f"riskBand must be one of {sorted(_RISK_BANDS)}")

        base_from = """
            FROM Patients p
            OUTER APPLY (
                SELECT TOP 1 score FROM RiskScores
                WHERE patientId = p.id AND area = 'overall'
                ORDER BY computedAt DESC
            ) latest
        """
        where = f"WHERE {_BAND_CASE_SQL} = ?" if risk_band_filter else ""
        params = [risk_band_filter] if risk_band_filter else []

        total = db.query_one(f"SELECT COUNT(*) AS n {base_from} {where}", params)["n"]
        offset = (page - 1) * page_size
        rows = db.query(
            f"""
            SELECT p.id, p.name, p.dob, p.sex, latest.score AS overallScore
            {base_from} {where}
            ORDER BY p.id
            OFFSET ? ROWS FETCH NEXT ? ROWS ONLY
            """,
            [*params, offset, page_size],
        )
        for r in rows:
            r["riskBand"] = risk_band(r["overallScore"]) if r["overallScore"] is not None else None
        return success(paginated(rows, total, page, page_size))
    except Exception as exc:
        return error_response(exc)


@bp.route(route="patients", methods=["POST"])
def create_patient(req: func.HttpRequest) -> func.HttpResponse:
    try:
        principal = require_role(req, "provider")
        try:
            body = req.get_json()
        except ValueError:
            raise BadRequest("Invalid JSON body")

        name = require_string(body.get("name"), "name")
        contact = require_string(body.get("contact"), "contact")
        sex = require_string(body.get("sex"), "sex", max_length=20, required=False)

        dob_raw = body.get("dob")
        if not dob_raw:
            raise BadRequest("dob is required")
        try:
            dob = datetime.fromisoformat(dob_raw).date() if isinstance(dob_raw, str) else None
        except ValueError:
            dob = None
        if dob is None:
            raise BadRequest("dob must be an ISO-8601 date (YYYY-MM-DD)")
        if dob > datetime.now(timezone.utc).date():
            raise BadRequest("dob cannot be in the future")

        patient_id = db.execute_returning_id(
            "INSERT INTO Patients (name, dob, sex, contact) OUTPUT INSERTED.id VALUES (?, ?, ?, ?)",
            (name, dob, sex, contact),
        )
        audit_record(principal, action="register_patient", target_type="Patient", target_id=patient_id)
        return success({"id": patient_id}, status_code=201)
    except Exception as exc:
        return error_response(exc)


@bp.route(route="patients/claim", methods=["POST"])
def claim_patient(req: func.HttpRequest) -> func.HttpResponse:
    """A freshly-signed-in principal (not yet onboarded) claims a
    provider-registered-but-unclaimed Patients row by its contact email,
    binding it to their identity — the patient-side half of the onboarding
    flow (a provider registers the patient first via POST /patients)."""
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
            result = claim_by_contact(principal, table="Patients", owner_col="b2cObjectId", contact=contact)
        except Exception:
            audit_record(principal, action="claim_patient", target_type="Patient", target_id="unknown", outcome="denied", phi_accessed=False)
            raise
        audit_record(principal, action="claim_patient", target_type="Patient", target_id=result["id"])
        return success(result)
    except Exception as exc:
        return error_response(exc)


_NAME_LOOKUP = {
    "patient": ("Patients", "b2cObjectId"),
    "provider": ("Providers", "principalUserId"),
    "caregiver": ("Caregivers", "principalUserId"),
}


@bp.route(route="me", methods=["GET"])
def get_me(req: func.HttpRequest) -> func.HttpResponse:
    """Returns the caller's resolved identity — {userId, role, patientId,
    name}, role possibly null for a verified-but-not-yet-onboarded principal.
    Called by the frontend right after sign-in to decide "show the app" vs
    "show onboarding," and to show the signed-in person's own name (not just
    whichever patient a provider/caregiver happens to be viewing) — e.g. in
    the navbar. Deliberately calls get_principal directly (not require_role)
    since role=null is an expected, valid response here, not an error."""
    try:
        principal = get_principal(req)
        name = None
        if principal.role in _NAME_LOOKUP:
            table, owner_col = _NAME_LOOKUP[principal.role]
            row = db.query_one(f"SELECT TOP 1 name FROM {table} WHERE {owner_col} = ?", (principal.user_id,))
            name = row["name"] if row else None
        return success(
            {"userId": principal.user_id, "role": principal.role, "patientId": principal.patient_id, "name": name}
        )
    except Exception as exc:
        return error_response(exc)


@bp.route(route="goals", methods=["POST"])
def create_goal(req: func.HttpRequest) -> func.HttpResponse:
    try:
        principal = require_role(req, "patient")
        try:
            body = req.get_json()
        except ValueError:
            raise BadRequest("Invalid JSON body")

        kind = body.get("kind")
        period = body.get("period", "weekly")
        if kind not in _GOAL_KINDS or body.get("target") is None:
            raise BadRequest(f"kind (one of {sorted(_GOAL_KINDS)}) and target are required")
        if period not in ("daily", "weekly", "monthly"):
            raise BadRequest("period must be one of: daily, weekly, monthly")
        target = require_number(body.get("target"), "target", min_value=0.01, max_value=1_000_000)

        goal_id = db.execute_returning_id(
            "INSERT INTO Goals (patientId, kind, target, progress, period) OUTPUT INSERTED.id VALUES (?, ?, ?, 0, ?)",
            (principal.patient_id, kind, target, period),
        )
        audit_write(req, "Goal", goal_id, action="create_goal")
        return success({"id": goal_id}, status_code=201)
    except Exception as exc:
        return error_response(exc)


@bp.route(route="goals/{goalId}", methods=["PATCH"])
def update_goal(req: func.HttpRequest) -> func.HttpResponse:
    try:
        principal = require_role(req, "patient")
        goal_id = parse_int_param(req, "goalId")
        goal = db.query_one("SELECT patientId FROM Goals WHERE id = ?", (goal_id,))
        if not goal:
            return not_found("Goal")
        require_own_patient(principal, goal["patientId"])
        try:
            body = req.get_json()
        except ValueError:
            raise BadRequest("Invalid JSON body")

        if body.get("target") is None and body.get("progress") is None:
            raise BadRequest("target and/or progress must be provided")
        target = require_number(body.get("target"), "target", min_value=0.01, max_value=1_000_000) if body.get("target") is not None else None
        progress = require_number(body.get("progress"), "progress", min_value=0, max_value=1_000_000) if body.get("progress") is not None else None

        if target is not None:
            db.execute("UPDATE Goals SET target = ? WHERE id = ?", (target, goal_id))
        if progress is not None:
            db.execute("UPDATE Goals SET progress = ? WHERE id = ?", (progress, goal_id))
        audit_write(req, "Goal", goal_id, action="update_goal")

        return success({"id": goal_id})
    except Exception as exc:
        return error_response(exc)
