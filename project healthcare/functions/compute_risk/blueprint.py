"""Timer: nightly Health Risk Score computation (docs/BLUEPRINT.md Part 8.1).

Also exposed over HTTP for on-demand recompute from the UI/demo. After
scoring, triggers the recommendation engine and raises risk alerts for
any area that drops below threshold.
"""
import logging

import azure.functions as func

from shared import db
from shared.alerts import rules as alert_rules
from shared.audit import audit_write
from shared.auth import (
    error_response,
    parse_int_param,
    require_own_patient,
    require_role,
)
from shared.recommend import generate_and_store
from shared.responses import success
from shared.scoring.risk_score import compute_and_store

bp = func.Blueprint()


def _run_for_patient(patient_id: int) -> dict:
    area_scores = compute_and_store(patient_id)
    for area, result in area_scores.items():
        try:
            alert_rules.check_risk_score(patient_id, area, result["score"])
        except Exception as exc:
            logging.warning("compute_risk: risk alert check failed for patient %s area %s: %s", patient_id, area, exc)
    generate_and_store(patient_id, area_scores)
    return area_scores


@bp.timer_trigger(schedule="0 0 2 * * *", arg_name="timer", run_on_startup=False)
def compute_risk_timer(timer: func.TimerRequest) -> None:
    patients = db.query("SELECT id FROM Patients", ())
    for p in patients:
        try:
            _run_for_patient(p["id"])
        except Exception as exc:
            logging.error("compute_risk: failed for patient %s: %s", p["id"], exc)
    logging.info("compute_risk: scored %d patients", len(patients))


@bp.route(route="risk-score/{patientId}/compute", methods=["POST"])
def compute_risk_http(req: func.HttpRequest) -> func.HttpResponse:
    try:
        principal = require_role(req, "patient", "provider")
        patient_id = parse_int_param(req, "patientId")
        require_own_patient(principal, patient_id)
        area_scores = _run_for_patient(patient_id)
        audit_write(req, "RiskScore", patient_id, action="compute_risk_score")
        return success(area_scores)
    except Exception as exc:
        return error_response(exc)
