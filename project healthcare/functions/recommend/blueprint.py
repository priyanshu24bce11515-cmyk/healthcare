"""HTTP: fetch, regenerate, and act on explainable recommendations
(docs/BLUEPRINT.md Part 8.2)."""
from datetime import datetime, timezone

import azure.functions as func

from shared import db
from shared.audit import audit_read, audit_write
from shared.auth import (
    BadRequest,
    error_response,
    parse_int_param,
    require_own_patient,
    require_role,
)
from shared.recommend import generate_and_store
from shared.responses import not_found, success

bp = func.Blueprint()


@bp.route(route="recommendations/{patientId}", methods=["GET"])
def list_recommendations(req: func.HttpRequest) -> func.HttpResponse:
    try:
        principal = require_role(req, "patient", "provider")
        patient_id = parse_int_param(req, "patientId")
        require_own_patient(principal, patient_id)
        audit_read(req, "Recommendation", patient_id, action="list_recommendations")
        rows = db.query(
            """
            SELECT TOP 10 id, text, reason, category, priority, priorityScore, source, generatedAt, actedOn, dismissedAt
            FROM Recommendations WHERE patientId = ? ORDER BY generatedAt DESC
            """,
            (patient_id,),
        )
        return success(rows)
    except Exception as exc:
        return error_response(exc)


@bp.route(route="recommendations/{patientId}/generate", methods=["POST"])
def regenerate_recommendations(req: func.HttpRequest) -> func.HttpResponse:
    try:
        principal = require_role(req, "patient", "provider")
        patient_id = parse_int_param(req, "patientId")
        require_own_patient(principal, patient_id)
        latest = db.query(
            """
            SELECT area, score, reason FROM RiskScores r
            WHERE patientId = ? AND computedAt = (
              SELECT MAX(computedAt) FROM RiskScores WHERE patientId = r.patientId AND area = r.area
            )
            """,
            (patient_id,),
        )
        area_scores = {r["area"]: {"score": r["score"], "reason": r["reason"]} for r in latest}
        recs = generate_and_store(patient_id, area_scores)
        audit_write(req, "Recommendation", patient_id, action="generate_recommendations")
        return success(recs, status_code=201)
    except Exception as exc:
        return error_response(exc)


@bp.route(route="recommendations/{recommendationId}/action", methods=["PATCH"])
def act_on_recommendation(req: func.HttpRequest) -> func.HttpResponse:
    """Records the patient's response to a recommendation — feeds the
    recommendation engine's feedback loop (see shared/recommend.py
    _recent_feedback), which damps a dismissed category and boosts one the
    patient acted on for that patient's next set of recommendations."""
    try:
        principal = require_role(req, "patient")
        rec_id = parse_int_param(req, "recommendationId")
        rec = db.query_one("SELECT patientId FROM Recommendations WHERE id = ?", (rec_id,))
        if not rec:
            return not_found("Recommendation")
        require_own_patient(principal, rec["patientId"])

        try:
            body = req.get_json()
        except ValueError:
            raise BadRequest("Invalid JSON body")
        action = body.get("action")
        if action not in ("acted", "dismissed"):
            raise BadRequest("action must be 'acted' or 'dismissed'")

        if action == "acted":
            db.execute("UPDATE Recommendations SET actedOn = 1 WHERE id = ?", (rec_id,))
        else:
            db.execute("UPDATE Recommendations SET dismissedAt = ? WHERE id = ?", (datetime.now(timezone.utc), rec_id))
        audit_write(req, "Recommendation", rec_id, action=f"recommendation_{action}")

        return success({"id": rec_id, "action": action})
    except Exception as exc:
        return error_response(exc)
